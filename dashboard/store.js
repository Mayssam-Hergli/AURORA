/* AURORA dashboard data layer: bus messages (docs/interfaces.md) -> per-satellite state + history.
   No DOM, no network: app.js feeds it, tests/store.test.js runs it in Node. Ground truth (gt/) and
   red-team topics are ignored: the operator view shows only what the defence and the satellites say. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.AuroraStore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const PREFIX = "aurora/v1";
  const SATS = "ABCDEFGHIJKL".split("");
  const SERIES = ["temp_c", "soc_pct", "att_err_deg", "p30", "p60", "p60_lo", "p60_hi"];
  const PATTERNS = [
    ["tm", new RegExp(`^${PREFIX}/sat/([A-L])/tm/(thermal|eps|adcs|obc|comms|sun)$`)],
    ["tc", new RegExp(`^${PREFIX}/sat/([A-L])/tc$`)],
    ["state", new RegExp(`^${PREFIX}/sat/([A-L])/state$`)],
    ["link", new RegExp(`^${PREFIX}/link/([A-L])-([A-L])$`)],
    ["forecast", new RegExp(`^${PREFIX}/core/forecast/([A-L])$`)],
    ["action", new RegExp(`^${PREFIX}/core/action$`)],
    ["event", new RegExp(`^${PREFIX}/core/event$`)],
  ];
  // what the dashboard subscribes to: never gt/ or redteam/ (interfaces.md Section 2)
  const SUBSCRIPTIONS = [
    `${PREFIX}/sat/+/tm/+`,
    `${PREFIX}/sat/+/tc`,
    `${PREFIX}/sat/+/state`,
    `${PREFIX}/link/+`,
    `${PREFIX}/core/forecast/+`,
    `${PREFIX}/core/action`,
    `${PREFIX}/core/event`,
  ];

  function kindOf(topic) {
    for (const [kind, re] of PATTERNS) {
      const m = re.exec(topic);
      if (m) return { kind, m };
    }
    return null;
  }

  /** History of [t, v] points, trimmed to the last `keepS` seconds of message time. */
  class Ring {
    constructor(keepS) {
      this.keepS = keepS;
      this.t = [];
      this.v = [];
    }
    push(t, v) {
      if (this.t.length && t < this.t[this.t.length - 1]) return; // out of order: ignore
      this.t.push(t);
      this.v.push(v);
      const cut = t - this.keepS;
      let i = 0;
      while (i < this.t.length && this.t[i] < cut) i++;
      if (i) {
        this.t.splice(0, i);
        this.v.splice(0, i);
      }
    }
    get last() {
      return this.v.length ? this.v[this.v.length - 1] : null;
    }
  }

  function emptySat(sat, keepS) {
    const series = {};
    for (const s of SERIES) series[s] = new Ring(keepS);
    return {
      sat,
      src: null, // "twin" | "hw" from telemetry
      emulated: [], // fields the publisher marks as emulated
      state: null,
      reason: null,
      fields: {}, // sub -> latest fields
      lastT: null, // message time of the latest telemetry
      lastRx: null, // receive time (ms) of the latest telemetry
      forecast: null,
      series,
    };
  }

  function createStore(opts = {}) {
    const keepS = opts.historyS || 900;
    const maxEvents = opts.maxEvents || 200;
    const nowMs = opts.nowMs || (() => Date.now());
    const store = {
      sats: Object.fromEntries(SATS.map((s) => [s, emptySat(s, keepS)])),
      links: {}, // "A-B" -> {a, b, feasible, enabled, range_km, t}
      events: [], // newest first
      tcs: [], // newest first, last 50 telecommands (origin is evidence)
      action: null,
      counts: { messages: 0, invalid: 0, ignored: 0 },
      changed: [], // sats whose state changed since the caller last cleared it
    };

    store.handle = function (topic, payload) {
      const k = kindOf(topic);
      if (!k) {
        store.counts.ignored++;
        return null;
      }
      let msg;
      try {
        msg = typeof payload === "string" ? JSON.parse(payload) : JSON.parse(new TextDecoder().decode(payload));
      } catch (e) {
        store.counts.invalid++;
        return null;
      }
      store.counts.messages++;
      const { kind, m } = k;
      if (kind === "tm") {
        const s = store.sats[m[1]];
        const sub = m[2];
        s.src = msg.src || s.src;
        if (Array.isArray(msg.emulated)) s.emulated = Array.from(new Set([...s.emulated, ...msg.emulated]));
        s.fields[sub] = msg.fields || {};
        s.lastT = msg.t;
        s.lastRx = nowMs();
        const f = msg.fields || {};
        if (sub === "thermal" && isNum(f.temp_c)) s.series.temp_c.push(msg.t, f.temp_c);
        if (sub === "eps" && isNum(f.soc_pct)) s.series.soc_pct.push(msg.t, f.soc_pct);
        if (sub === "adcs" && isNum(f.att_err_deg)) s.series.att_err_deg.push(msg.t, Math.abs(f.att_err_deg));
      } else if (kind === "state") {
        const s = store.sats[m[1]];
        if (s.state !== msg.state) store.changed.push(m[1]);
        s.state = msg.state;
        s.reason = msg.reason || null;
      } else if (kind === "forecast") {
        const s = store.sats[m[1]];
        s.forecast = msg;
        const p = msg.p_fail || {};
        const sd = msg.p_std && isNum(msg.p_std["60"]) ? msg.p_std["60"] : null;
        if (isNum(p["30"])) s.series.p30.push(msg.t, p["30"]);
        if (isNum(p["60"])) {
          s.series.p60.push(msg.t, p["60"]);
          s.series.p60_lo.push(msg.t, sd === null ? null : Math.max(0, p["60"] - sd));
          s.series.p60_hi.push(msg.t, sd === null ? null : Math.min(1, p["60"] + sd));
        }
      } else if (kind === "link") {
        store.links[`${msg.a}-${msg.b}`] = msg;
      } else if (kind === "tc") {
        store.tcs.unshift(msg);
        if (store.tcs.length > 50) store.tcs.length = 50;
      } else if (kind === "event") {
        store.events.unshift(msg);
        if (store.events.length > maxEvents) store.events.length = maxEvents;
      } else if (kind === "action") {
        store.action = msg;
      }
      return kind;
    };

    /** "nominal" | "flagged" | "quarantined" | "safe_hold" | "unknown" (no state published yet). */
    store.stateOf = (sat) => store.sats[sat].state || "unknown";

    /** "EMULATED" for the twin, "HW" for a board, null before the first telemetry. */
    store.badgeOf = (sat) => {
      const s = store.sats[sat];
      if (s.src === "twin" || s.src === "synthetic") return "EMULATED";
      if (s.src === "hw") return "HW";
      return null;
    };

    /** No telemetry received for `staleS` seconds of wall-clock time. */
    store.isStale = (sat, staleS = 5) => {
      const s = store.sats[sat];
      return s.lastRx === null || nowMs() - s.lastRx > staleS * 1000;
    };

    /** Link cell for the matrix: "up" (feasible and enabled), "cut" (feasible, disabled), "none". */
    store.linkStatus = (a, b) => {
      const [x, y] = a < b ? [a, b] : [b, a];
      const l = store.links[`${x}-${y}`];
      if (!l || !l.feasible) return "none";
      return l.enabled ? "up" : "cut";
    };

    return store;
  }

  function isNum(x) {
    return typeof x === "number" && Number.isFinite(x);
  }

  return { createStore, kindOf, SATS, SUBSCRIPTIONS, PREFIX, Ring };
});
