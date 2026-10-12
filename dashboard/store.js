// Dashboard state: every bus message the dashboard shows goes through handle(topic, msg).
// Pure logic (no DOM), so it runs in a browser and under Node for tests.
(function (root) {
  "use strict";

  function Store(cfg) {
    this.cfg = cfg;
    this.sats = {};
    cfg.sats.forEach((s) => {
      this.sats[s] = {
        id: s, state: "nominal", src: null, emulated: [], temp: null, soc: null, heater: null,
        mode: null, pf: null, pstd: null, model: null, nextSub: null, pMal: null, warming: false,
        series: { pf: [], pfLo: [], pfHi: [], temp: [], soc: [] },
      };
    });
    this.links = {};
    this.events = [];
    this.lastAction = null;
    this.latestT = 0;
  }

  Store.prototype.push = function (arr, t, v) {
    arr.push([t, v]);
    const cut = t - this.cfg.historySeconds;
    while (arr.length && arr[0][0] < cut) arr.shift();
  };

  Store.prototype.handle = function (topic, m) {
    const p = topic.split("/");
    if (p[0] + "/" + p[1] !== this.cfg.prefix || !m || typeof m !== "object") return null;
    if (typeof m.t === "number" && m.t > this.latestT) this.latestT = m.t;
    if (p[2] === "sat" && this.sats[p[3]]) {
      const s = this.sats[p[3]];
      if (p[4] === "tm") {
        s.src = m.src || s.src;
        if (Array.isArray(m.emulated)) s.emulated = m.emulated;
        const f = m.fields || {};
        if (p[5] === "thermal") {
          s.temp = f.temp_c; s.heater = f.heater_on;
          this.push(s.series.temp, m.t, f.temp_c);
        } else if (p[5] === "eps") {
          s.soc = f.soc_pct; this.push(s.series.soc, m.t, f.soc_pct);
        } else if (p[5] === "obc") {
          s.mode = f.mode;
        }
        return "tm";
      }
      if (p[4] === "state") { s.state = m.state; return "state"; }
      return null;
    }
    if (p[2] === "core" && p[3] === "forecast" && this.sats[p[4]]) {
      const s = this.sats[p[4]];
      const pf = m.p_fail ? m.p_fail["60"] : null;
      const sd = m.p_std ? m.p_std["60"] : null;
      s.pf = pf; s.pstd = sd; s.model = m.model; s.nextSub = m.next_sub; s.pMal = m.p_malicious;
      s.warming = !!m.warming_up;
      if (pf !== null && pf !== undefined) {
        this.push(s.series.pf, m.t, pf);
        if (sd !== null && sd !== undefined) {
          this.push(s.series.pfLo, m.t, Math.max(0, pf - sd));
          this.push(s.series.pfHi, m.t, Math.min(1, pf + sd));
        }
      }
      return "forecast";
    }
    if (p[2] === "core" && p[3] === "action") { this.lastAction = m; return "action"; }
    if (p[2] === "core" && p[3] === "event") {
      this.events.unshift(m);
      if (this.events.length > this.cfg.maxEvents) this.events.length = this.cfg.maxEvents;
      return "event";
    }
    if (p[2] === "link") { this.links[p[3]] = m; return "link"; }
    return null; // gt/ and redteam/ are never shown (the dashboard does not subscribe to them)
  };

  Store.prototype.badge = function (s) {
    if (s.src === "hw") return { cls: "hw", text: s.emulated.indexOf("temp_c") >= 0 ? "HW · temp emulated" : "HW" };
    if (s.src === "twin" || s.src === "synthetic") return { cls: "emu", text: "EMULATED" };
    return { cls: "emu", text: "no data" };
  };

  Store.prototype.linkSummary = function () {
    const all = Object.values(this.links);
    const feasible = all.filter((l) => l.feasible);
    const up = feasible.filter((l) => l.enabled);
    const cut = feasible.filter((l) => !l.enabled).map((l) => l.a + "-" + l.b);
    return { total: all.length, feasible: feasible.length, up: up.length, cut: cut };
  };

  root.AuroraStore = Store;
  if (typeof module !== "undefined") module.exports = Store;
})(typeof window !== "undefined" ? window : globalThis);
