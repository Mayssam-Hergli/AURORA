// node --test "dashboard/tests/*.test.js"   (Node 20+, no npm install)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { createStore, kindOf, SUBSCRIPTIONS, Ring } = require("../store.js");

const P = "aurora/v1";
const tm = (sat, sub, t, fields, extra = {}) => [
  `${P}/sat/${sat}/tm/${sub}`,
  JSON.stringify({ v: 1, sat, src: "twin", t, seq: t, sub, fields, ...extra }),
];

test("never subscribes to ground truth or red-team topics", () => {
  for (const f of SUBSCRIPTIONS) assert.ok(!f.includes("/gt/") && !f.includes("/redteam/") && !f.endsWith("#"), f);
  const store = createStore();
  assert.equal(store.handle(`${P}/gt/event`, '{"v":1}'), null);
  assert.equal(store.handle(`${P}/redteam/B`, '{"v":1}'), null);
  assert.equal(store.counts.ignored, 2);
});

test("telemetry fills tiles, badges and history; emulated fields are remembered", () => {
  const store = createStore();
  store.handle(...tm("E", "thermal", 100, { temp_c: 21.5, heater_on: 0 }));
  store.handle(...tm("E", "eps", 100, { soc_pct: 80, bus_v: 28, load_w: 40, solar_w: 60, current_ma: 1400 }));
  const [topic, body] = tm("B", "thermal", 100, { temp_c: 30, heater_on: 1 }, { src: "hw", emulated: ["temp_c"] });
  store.handle(topic, body);
  assert.equal(store.badgeOf("E"), "EMULATED");
  assert.equal(store.badgeOf("B"), "HW");
  assert.equal(store.badgeOf("A"), null);
  assert.deepEqual(store.sats.B.emulated, ["temp_c"]);
  assert.equal(store.sats.E.series.temp_c.last, 21.5);
  assert.equal(store.sats.E.fields.eps.soc_pct, 80);
  assert.equal(store.isStale("E"), false);
  assert.equal(store.isStale("A"), true);
});

test("state changes are tracked for auto-focus; retained state on reconnect does not re-trigger", () => {
  const store = createStore();
  const st = (state) => store.handle(`${P}/sat/E/state`, JSON.stringify({ v: 1, sat: "E", t: 1, state }));
  st("nominal");
  st("flagged");
  st("flagged");
  assert.deepEqual(store.changed, ["E", "E"]);
  assert.equal(store.stateOf("E"), "flagged");
  assert.equal(store.stateOf("A"), "unknown");
});

test("forecast: P(F) series with the ±σ band, nulls stay null (D4)", () => {
  const store = createStore();
  const fc = (t, p60, std) =>
    store.handle(`${P}/core/forecast/E`, JSON.stringify({
      v: 1, sat: "E", t, model: "tgn-v1",
      p_fail: { 10: 0.01, 30: p60 / 2, 60: p60, 120: Math.min(1, p60 * 1.5) },
      p_std: std === null ? null : { 60: std }, q: std, next_sub: "eps", p_malicious: 0.9,
    }));
  fc(10, 0.4, 0.1);
  fc(11, 0.95, 0.2);
  fc(12, 0.5, null);
  const s = store.sats.E.series;
  assert.deepEqual(s.p60.v, [0.4, 0.95, 0.5]);
  assert.deepEqual(s.p60_lo.v.map((x) => x && +x.toFixed(2)), [0.3, 0.75, null]);
  assert.deepEqual(s.p60_hi.v.map((x) => x && +x.toFixed(2)), [0.5, 1, null]); // clipped at 1
  assert.equal(store.sats.E.forecast.model, "tgn-v1");
});

test("links matrix: up, cut (feasible but disabled), none", () => {
  const store = createStore();
  const link = (a, b, feasible, enabled) =>
    store.handle(`${P}/link/${a}-${b}`, JSON.stringify({ v: 1, t: 1, a, b, feasible, enabled, range_km: 1000 }));
  link("A", "B", true, true);
  link("B", "C", true, false);
  link("A", "C", false, false);
  assert.equal(store.linkStatus("B", "A"), "up");
  assert.equal(store.linkStatus("C", "B"), "cut");
  assert.equal(store.linkStatus("A", "C"), "none");
  assert.equal(store.linkStatus("A", "L"), "none");
});

test("events newest first, last action kept, invalid JSON counted", () => {
  const store = createStore({ maxEvents: 2 });
  for (const i of [1, 2, 3]) {
    store.handle(`${P}/core/event`, JSON.stringify({ v: 1, t: i, sat: "E", level: "info", kind: "forecast", text: `e${i}` }));
  }
  assert.deepEqual(store.events.map((e) => e.text), ["e3", "e2"]);
  store.handle(`${P}/core/action`, JSON.stringify({ v: 1, t: 4, action: "quarantine", target: "E", J: 0.2, alternatives: [], gated: null, reason: "x" }));
  assert.equal(store.action.action, "quarantine");
  assert.equal(store.handle(`${P}/core/event`, "{not json"), null);
  assert.equal(store.counts.invalid, 1);
});

test("history keeps the last historyS seconds of message time", () => {
  const r = new Ring(10);
  for (let t = 0; t <= 30; t++) r.push(t, t);
  assert.equal(r.t[0], 20);
  assert.equal(r.last, 30);
  r.push(5, 99); // out of order: ignored
  assert.equal(r.last, 30);
});

test("topic parser matches the contract's topic tree", () => {
  assert.equal(kindOf(`${P}/sat/L/tm/sun`).kind, "tm");
  assert.equal(kindOf(`${P}/sat/M/tm/sun`), null);
  assert.equal(kindOf(`${P}/link/A-B`).kind, "link");
  assert.equal(kindOf(`${P}/core/forecast/B`).kind, "forecast");
});
