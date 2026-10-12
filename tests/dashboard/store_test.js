// Node test of dashboard/store.js (no browser): run with `node tests/dashboard/store_test.js`.
"use strict";
const assert = require("assert");
const path = require("path");
const Store = require(path.join(__dirname, "..", "..", "dashboard", "store.js"));

const cfg = { prefix: "aurora/v1", sats: "ABCDEFGHIJKL".split(""), historySeconds: 900, thresholds: [], maxEvents: 3 };
const s = new Store(cfg);
const T0 = 1800000000;

// telemetry from the board (hw, emulated temperature) and from the twin
s.handle("aurora/v1/sat/B/tm/thermal", { v: 1, sat: "B", src: "hw", t: T0, seq: 1, sub: "thermal", fields: { temp_c: 31.5, heater_on: 1 }, emulated: ["temp_c"] });
s.handle("aurora/v1/sat/E/tm/eps", { v: 1, sat: "E", src: "twin", t: T0 + 1, seq: 1, sub: "eps", fields: { soc_pct: 70, bus_v: 7, load_w: 1, solar_w: 1, current_ma: 1 } });
assert.strictEqual(s.sats.B.temp, 31.5);
assert.deepStrictEqual(s.badge(s.sats.B), { cls: "hw", text: "HW · temp emulated" });
assert.deepStrictEqual(s.badge(s.sats.E), { cls: "emu", text: "EMULATED" });
assert.strictEqual(s.sats.E.series.soc.length, 1);

// forecasts: band only when p_std exists (D4: null with a single model)
s.handle("aurora/v1/core/forecast/E", { v: 1, sat: "E", t: T0 + 2, model: "tgn-v1", p_fail: { 10: 0.1, 30: 0.2, 60: 0.4, 120: 0.6 }, p_std: { 60: 0.05 }, q: 0.05, next_sub: "eps", p_malicious: 0.9 });
s.handle("aurora/v1/core/forecast/B", { v: 1, sat: "B", t: T0 + 2, model: "trajectory-v0", p_fail: { 10: 0, 30: 0.2, 60: 0.5, 120: 0.7 }, p_std: null, q: null, next_sub: null, p_malicious: null });
assert.strictEqual(s.sats.E.series.pfHi.length, 1);
assert.strictEqual(s.sats.B.series.pfHi.length, 0);
assert.strictEqual(s.sats.B.pf, 0.5);

// node state, action, events (bounded), links
s.handle("aurora/v1/sat/B/state", { v: 1, sat: "B", t: T0 + 3, state: "flagged" });
assert.strictEqual(s.sats.B.state, "flagged");
s.handle("aurora/v1/core/action", { v: 1, t: T0 + 4, action: "isolate_subsystem", target: "B", J: 0.07, alternatives: [], gated: null, q: null, reason: "x" });
assert.strictEqual(s.lastAction.target, "B");
for (let i = 0; i < 5; i++) s.handle("aurora/v1/core/event", { v: 1, t: T0 + i, level: "info", kind: "decision", text: "e" + i });
assert.strictEqual(s.events.length, 3);
assert.strictEqual(s.events[0].text, "e4");
s.handle("aurora/v1/link/A-B", { v: 1, t: T0, a: "A", b: "B", feasible: true, enabled: false, range_km: 1 });
s.handle("aurora/v1/link/A-C", { v: 1, t: T0, a: "A", b: "C", feasible: true, enabled: true, range_km: 1 });
s.handle("aurora/v1/link/B-C", { v: 1, t: T0, a: "B", b: "C", feasible: false, enabled: false, range_km: 1 });
assert.deepStrictEqual(s.linkSummary(), { total: 3, feasible: 2, up: 1, cut: ["A-B"] });

// ground truth is never shown, even if a message arrived on such a topic
assert.strictEqual(s.handle("aurora/v1/gt/event", { v: 1, t: T0, episode_id: "x", event: "onset", sat: "B" }), null);

// ring buffer drops points older than historySeconds
s.handle("aurora/v1/sat/E/tm/eps", { v: 1, sat: "E", src: "twin", t: T0 + 2000, seq: 2, sub: "eps", fields: { soc_pct: 69 } });
assert.strictEqual(s.sats.E.series.soc.length, 1);
console.log("dashboard store: all checks passed");
