/* AURORA dashboard data layer: bus messages (docs/interfaces.md) -> per-satellite state, history,
   orbit positions and a plain-language timeline. No DOM, no network: app.js feeds it, and
   tests/store.test.js runs it in Node. Ground truth (gt/) and red-team topics are ignored: the operator
   view shows only what the defence and the satellites say. */
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
  // Walker layout flown by the twin (ml/configs/synthetic.yaml orbit: 3 planes x 4, phasing 30 deg)
  const ORBIT = { planes: 3, perPlane: 4, phasingDeg: 30 };

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
      orbit: null, // {plane, phase_deg, t} from the twin's comms telemetry
      eta: null, // {eta_s, t, limit, channel}: the forecaster's time to the limit, from core/event data
      contained: null, // action the core took on this satellite, until it is nominal again
      series,
    };
  }

  // ------------------------------------------------------------------ plain language
  const ACTION_LABEL = {
    none: "No action",
    isolate_subsystem: "Heater cut",
    safe_mode: "Safe mode",
    block_source: "Source blocked",
    quarantine: "Quarantined, links cut",
  };
  const CHANNEL = { temp_c: "temperature", soc_pct: "battery charge", att_err_deg: "pointing error" };
  const LIMIT = { temp_c: "temperature limit", soc_pct: "battery limit", att_err_deg: "pointing limit" };
  const limitName = (channel) => LIMIT[channel] || "limit";
  const cap = (x) => x.charAt(0).toUpperCase() + x.slice(1);

  function modelLabel(model) {
    if (!model) return "";
    if (model.startsWith("trajectory")) return "trajectory forecaster";
    if (model.startsWith("tgn")) return model.includes("uncalibrated") ? "learned model (uncalibrated)" : "learned model (ensemble)";
    return "forecaster";
  }

  function origin(o) {
    if (!o) return "an unknown source";
    if (o === "ground") return "a ground command";
    if (o === "core") return "CASSANDRA";
    if (o.startsWith("isl:")) return `a command relayed by ${o.slice(4)}`;
    return "a command of unknown origin";
  }

  function causeSentence(sat, c) {
    if (c.cmd === "HEATER") return `${sat}: heater switched ${c.args && c.args.on ? "on" : "off"} by ${origin(c.origin)}`;
    if (c.cmd === "SET_MODE") return `${sat}: mode set to ${String((c.args || {}).mode || "").toLowerCase().replace("_", " ")} by ${origin(c.origin)}`;
    return `${sat}: ${String(c.cmd).toLowerCase()} received from ${origin(c.origin)}`;
  }

  const pct = (p) => `${Math.round(100 * p)}%`;
  const secs = (s) => `${Math.max(0, Math.round(s))} s`;

  /** core/event -> zero or more timeline lines {t, sat, text, tone}. tone: "" | "caution" | "warning" | "safe". */
  function narrate(ev) {
    const d = ev.data || {};
    const sat = ev.sat;
    const out = [];
    const add = (t, text, tone = "") => out.push({ t, sat, text, tone });
    const what = d.event;
    if (what === "state" && d.to === "flagged") {
      if (d.trigger === "forecast") {
        const level = typeof d.threshold === "number" ? ` (alert level ${Math.round(1000 * d.threshold) / 10}%)` : "";
        add(ev.t, `${sat}: failure risk ${pct(d.p)} within ${d.horizon_s || 60} s${level}`, "caution");
      }
      else if (d.trigger === "relay") add(ev.t, `${sat}: forwarding “${String(d.relaying || "").replace(/ to .*/, "")}” to neighbours over inter-satellite links (suspected compromise)`, "caution");
      // trigger "monitor": the monitor event already said it
    } else if (what === "state" && d.to === "nominal" && d.from === "flagged" && d.reason === "calm") {
      add(ev.t, `${sat}: risk back to normal, no action needed`);
    } else if (what === "decision") {
      if (d.cause && typeof d.cause.age_s === "number") add(ev.t - d.cause.age_s, causeSentence(sat, d.cause));
      const lead = d.escalation ? "first containment did not hold. " : "";
      const lim = cap(limitName(d.channel));
      const avoided = typeof d.eta_s === "number"
        ? d.estimated
          ? ` ${lim} avoided by about ${secs(d.eta_s)} (${CHANNEL[d.channel] || "value"} estimated).`
          : ` ${lim} avoided by ${secs(d.eta_s)}.`
        : "";
      if (ev.kind === "gated") add(ev.t, `${sat}: the model is too uncertain to act alone. Safe hold, ground alerted.`, "safe");
      else if (d.action === "isolate_subsystem") add(ev.t, `${sat} contained: ${lead}heater cut.${avoided}`, "warning");
      else if (d.action === "safe_mode") add(ev.t, `${sat} contained: ${lead}safe mode, heater and non-essential loads off.${avoided}`, "warning");
      else if (d.action === "quarantine") add(ev.t, `${sat} quarantined: ${lead}links cut, traffic rerouted.${avoided}`, "warning");
      else if (d.action === "block_source") add(ev.t, `${sat} contained: ${lead}commands from that source blocked.${avoided}`, "warning");
      else if (d.action === "none") add(ev.t, `${sat}: watching. Acting now would cost more than it saves.`);
    } else if (what === "holding") {
      add(ev.t, `${sat}: ${String(d.cmd || "commands").toLowerCase()} commands still arriving every ${secs(d.period_s || 0)}. Containment held.`, "warning");
    } else if (what === "heal") {
      const temp = typeof d.temp_c === "number" ? ` at ${d.temp_c.toFixed(1)} °C` : "";
      const links = d.restored_links ? `, ${d.restored_links} link${d.restored_links > 1 ? "s" : ""} restored` : "";
      add(ev.t, `${sat} healed: back to normal${temp}${links}`, "safe");
    } else if (what === "ground_hold") {
      add(ev.t, `${sat}: held in quarantine until ground clears it`, "warning");
    } else if (what === "ground_cleared") {
      add(ev.t, `${sat}: cleared by ground, back in service`, "safe");
    } else if (what === "exhausted") {
      add(ev.t, `${sat}: no stronger action left. Ground action required.`, "warning");
    } else if (ev.kind === "monitor" && d.rule) {
      if (d.rule === "frozen") add(ev.t, `${sat}: temperature report stopped changing. CASSANDRA now estimates it.`, "caution");
      else if (d.rule === "heater_inconsistent") add(ev.t, `${sat}: heater on but the reported temperature is not rising. CASSANDRA now estimates it.`, "caution");
      else if (d.rule === "recovered") add(ev.t, `${sat}: temperature report consistent again`);
    } else if (d.note === "stale" && sat) {
      add(ev.t, `${sat}: no ${ev.text.includes("thermal") ? "temperature " : ""}telemetry`, "caution");
    } else if (d.note === "fresh" && sat) {
      add(ev.t, `${sat}: telemetry back`);
    }
    return out;
  }

  function createStore(opts = {}) {
    const keepS = opts.historyS || 900;
    const maxEvents = opts.maxEvents || 200;
    const nowMs = opts.nowMs || (() => Date.now());
    const store = {
      sats: Object.fromEntries(SATS.map((s) => [s, emptySat(s, keepS)])),
      links: {}, // "A-B" -> {a, b, feasible, enabled, range_km, t}
      reroutes: [], // [{key, t}] links that went from disabled to enabled while feasible
      events: [], // newest first
      timeline: [], // plain-language lines, newest first
      tcs: [], // newest first, last 50 telecommands (origin is evidence)
      action: null,
      counts: { messages: 0, invalid: 0, ignored: 0 },
      changed: [], // sats whose state changed since the caller last cleared it
      latestT: null, // newest message time seen
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
      if (isNum(msg.t) && (store.latestT === null || msg.t > store.latestT)) store.latestT = msg.t;
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
        if (sub === "comms" && isNum(f.plane) && isNum(f.phase_deg)) s.orbit = { plane: f.plane, phase_deg: f.phase_deg, t: msg.t };
      } else if (kind === "state") {
        const s = store.sats[m[1]];
        if (s.state !== msg.state) store.changed.push(m[1]);
        s.state = msg.state;
        s.reason = msg.reason || null;
        if (msg.state === "nominal") s.eta = s.contained = null;
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
        const key = `${msg.a}-${msg.b}`;
        const prev = store.links[key];
        if (prev && prev.feasible && !prev.enabled && msg.feasible && msg.enabled) store.reroutes.push({ key, t: msg.t });
        if (store.reroutes.length > 50) store.reroutes.splice(0, store.reroutes.length - 50);
        store.links[key] = msg;
      } else if (kind === "tc") {
        store.tcs.unshift(msg);
        if (store.tcs.length > 50) store.tcs.length = 50;
      } else if (kind === "event") {
        store.events.unshift(msg);
        if (store.events.length > maxEvents) store.events.length = maxEvents;
        const d = msg.data || {};
        if (msg.sat && store.sats[msg.sat]) {
          const s = store.sats[msg.sat];
          if (isNum(d.eta_s) && (d.event === "state" || d.event === "decision")) {
            s.eta = { eta_s: d.eta_s, t: msg.t, limit: d.limit, channel: d.channel };
          }
          if (d.event === "decision" && d.action && d.action !== "none") {
            s.eta = null; // contained: no countdown
            s.contained = d.action;
            s.containedT = msg.t;
          }
          if (d.event === "heal") s.eta = null;
        }
        const lines = narrate(msg).filter(
          (l) => !store.timeline.some((x) => x.sat === l.sat && x.text === l.text && Math.abs(x.t - l.t) < 60),
        );
        if (lines.length) {
          store.timeline.push(...lines);
          store.timeline.sort((a, b) => b.t - a.t);
          if (store.timeline.length > maxEvents) store.timeline.length = maxEvents;
        }
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

    /** Link status: "up" (feasible and enabled), "cut" (feasible, disabled), "none". */
    store.linkStatus = (a, b) => {
      const [x, y] = a < b ? [a, b] : [b, a];
      const l = store.links[`${x}-${y}`];
      if (!l || !l.feasible) return "none";
      return l.enabled ? "up" : "cut";
    };

    /** Plane (1..3) and phase (deg) of every satellite. The twin publishes them for its satellites;
        all share one orbital rate, so one published satellite places the others (e.g. the board B). */
    store.positions = () => {
      let ref = null;
      for (const sat of SATS) {
        const o = store.sats[sat].orbit;
        if (o && (!ref || o.t > ref.o.t)) ref = { sat, o };
      }
      const out = {};
      for (const [i, sat] of SATS.entries()) {
        const plane = Math.floor(i / ORBIT.perPlane);
        const u0 = (360 / ORBIT.perPlane) * (i % ORBIT.perPlane) + ORBIT.phasingDeg * plane;
        let phase = u0;
        if (ref) {
          const j = SATS.indexOf(ref.sat);
          const ru0 = (360 / ORBIT.perPlane) * (j % ORBIT.perPlane) + ORBIT.phasingDeg * Math.floor(j / ORBIT.perPlane);
          phase = (((ref.o.phase_deg - ru0 + u0) % 360) + 360) % 360;
        }
        out[sat] = { plane: plane + 1, phase_deg: phase, known: !!ref };
      }
      return out;
    };

    /** The satellite the bottom strip follows: abnormal states first, then the highest P(F<60 s);
        when everything is nominal and calm, `fallback` (B). */
    store.atRisk = (fallback = "B", calmP = 0.1) => {
      const rank = { flagged: 3, safe_hold: 2, quarantined: 2 };
      let best = null;
      for (const sat of SATS) {
        const s = store.sats[sat];
        const p = s.forecast && !s.forecast.warming_up && isNum(s.forecast.p_fail["60"]) ? s.forecast.p_fail["60"] : 0;
        const r = rank[s.state] || 0;
        const score = r * 10 + p;
        if ((r > 0 || p >= calmP) && (!best || score > best.score)) best = { sat, score };
      }
      return best ? best.sat : fallback;
    };

    /** Colour role of a satellite: "" (nominal: no colour), "caution" (at risk), "warning" (contained or
        quarantined), "safe" (safe hold, or contained and cooling below healBelowC). */
    store.toneOf = (sat, healBelowC = 30) => {
      const s = store.sats[sat];
      const temp = s.fields.thermal && isNum(s.fields.thermal.temp_c) ? s.fields.thermal.temp_c : null;
      if (s.state === "safe_hold") return "safe";
      if (s.state === "quarantined") return "warning";
      if (s.state === "flagged") {
        if (!s.contained) return "caution";
        return store.cooling(sat) ? "safe" : "warning";
      }
      return "";
    };

    /** Contained for at least 10 s and the temperature has fallen over the last 10 s: healing. */
    store.cooling = (sat) => {
      const s = store.sats[sat];
      const r = s.series.temp_c;
      if (!s.contained || s.containedT == null || store.latestT - s.containedT < 10 || r.t.length < 2) return false;
      const tEnd = r.t[r.t.length - 1];
      let i = r.t.length - 1;
      while (i > 0 && tEnd - r.t[i - 1] <= 10) i--;
      return tEnd - r.t[i] >= 5 && r.v[r.v.length - 1] < r.v[i] - 0.2;
    };

    /** Plain words for the state, matching toneOf. */
    store.stateWord = (sat, healBelowC = 30) => {
      const s = store.sats[sat];
      const tone = store.toneOf(sat, healBelowC);
      if (s.state === "quarantined") return "quarantined";
      if (s.state === "safe_hold") return "safe hold";
      if (tone === "caution") return "at risk";
      if (tone === "warning") return "contained";
      if (tone === "safe") return "healing";
      if (s.state === "nominal") return "nominal";
      return "no data yet";
    };

    /** Seconds left to the limit, counted down from the forecaster's last ETA (message time); null if none. */
    store.etaLeft = (sat) => {
      const e = store.sats[sat].eta;
      if (!e || store.latestT === null) return null;
      const left = e.eta_s - (store.latestT - e.t);
      return left > 0 ? left : null;
    };

    return store;
  }

  function isNum(x) {
    return typeof x === "number" && Number.isFinite(x);
  }

  return { createStore, kindOf, narrate, modelLabel, limitName, SATS, SUBSCRIPTIONS, PREFIX, Ring, ACTION_LABEL, CHANNEL, ORBIT };
});
