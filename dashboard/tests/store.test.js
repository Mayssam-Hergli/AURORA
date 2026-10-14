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

// ------------------------------------------------------------------ redesign (12 Oct): plain language, orbit, focus
const { narrate, modelLabel } = require("../store.js");
const ev = (t, sat, kind, data, level = "info") =>
  [`${P}/core/event`, JSON.stringify({ v: 1, t, sat, level, kind, text: "raw", data })];

test("events become plain sentences: risk, cause, containment with the limit avoided, heal", () => {
  const store = createStore();
  store.handle(...ev(100, "B", "decision", { event: "state", from: "nominal", to: "flagged", trigger: "forecast", p: 0.62, horizon_s: 60, eta_s: 25 }));
  store.handle(...ev(104, "B", "decision", {
    event: "decision", action: "isolate_subsystem", eta_s: 23, p: 0.67, channel: "temp_c",
    cause: { cmd: "HEATER", args: { on: true }, origin: "ground", age_s: 6 },
  }, "alarm"));
  store.handle(...ev(130, "B", "heal", { event: "heal", temp_c: 27.3, restored_links: 0 }));
  assert.deepEqual(store.timeline.map((l) => l.text), [
    "B healed: back to normal at 27.3 °C",
    "B contained: heater cut. Temperature limit avoided by 23 s.",
    "B: failure risk 62% within 60 s",
    "B: heater switched on by a ground command",
  ]);
  assert.equal(store.timeline[1].tone, "warning");
  assert.equal(store.timeline[3].t, 98); // the command, placed at its own time
});

test("relayed causes and monitor hits read as sentences; no raw jargon", () => {
  const lines = [
    ...narrate({ t: 1, sat: "H", kind: "decision", data: { event: "decision", action: "safe_mode", cause: { cmd: "HEATER", args: { on: true }, origin: "isl:A", age_s: 4 } } }),
    ...narrate({ t: 2, sat: "E", kind: "monitor", data: { rule: "frozen" } }),
    ...narrate({ t: 3, sat: "A", kind: "decision", data: { event: "state", from: "nominal", to: "flagged", trigger: "relay", relaying: "HEATER on to I" } }),
  ];
  const text = lines.map((l) => l.text).join(" | ");
  assert.match(text, /heater switched on by a command relayed by A/);
  assert.match(text, /temperature report stopped changing/);
  assert.match(text, /forwarding “HEATER on” to neighbours/);
  assert.doesNotMatch(text, /P\(F|tgn|J=|isolate_subsystem/);
  assert.equal(modelLabel("tgn-v1"), "learned model (ensemble)");
  assert.equal(modelLabel("trajectory-v0+est"), "trajectory forecaster");
});

test("positions: the twin's plane and phase; unpublished satellites (the board) placed from a plane-mate", () => {
  const store = createStore();
  store.handle(...tm("A", "comms", 50, { ground_contact: 0, isl_peers: [], rssi_dbm: -90, plane: 1, phase_deg: 10 }));
  const pos = store.positions();
  assert.deepEqual([pos.A.plane, pos.A.phase_deg], [1, 10]);
  assert.deepEqual([pos.B.plane, pos.B.phase_deg], [1, 100]); // 90 deg ahead in plane 1
  assert.deepEqual([pos.E.plane, pos.E.phase_deg], [2, 40]); // 30 deg phasing between planes
  assert.ok(pos.L.known);
});

test("colour roles: nominal has none; at risk, contained, healing, safe hold, quarantined", () => {
  const store = createStore();
  const st = (sat, state) => store.handle(`${P}/sat/${sat}/state`, JSON.stringify({ v: 1, sat, t: 1, state }));
  store.handle(...tm("B", "thermal", 1, { temp_c: 40, heater_on: 1 }));
  st("B", "nominal");
  assert.equal(store.toneOf("B"), "");
  st("B", "flagged");
  assert.equal(store.toneOf("B"), "caution");
  store.handle(...ev(2, "B", "decision", { event: "decision", action: "isolate_subsystem" }));
  assert.equal(store.toneOf("B"), "warning");
  store.handle(...tm("B", "thermal", 3, { temp_c: 29, heater_on: 0 }));
  assert.equal(store.toneOf("B"), "warning"); // just contained: red, even below 30 C
  for (let t = 4; t <= 16; t++) store.handle(...tm("B", "thermal", t, { temp_c: 29 - 0.3 * (t - 3), heater_on: 0 }));
  assert.equal(store.toneOf("B"), "safe"); // contained 10 s ago and cooling: healing
  assert.equal(store.stateWord("B"), "healing");
  st("B", "nominal");
  assert.equal(store.sats.B.contained, null);
  st("E", "safe_hold");
  st("A", "quarantined");
  assert.deepEqual([store.toneOf("E"), store.toneOf("A")], ["safe", "warning"]);
});

test("the strip follows the satellite at risk, else B; the countdown runs on message time", () => {
  const store = createStore();
  const fc = (sat, t, p60) => store.handle(`${P}/core/forecast/${sat}`, JSON.stringify({
    v: 1, sat, t, model: "trajectory-v0", p_fail: { 10: 0, 30: 0, 60: p60, 120: p60 }, p_std: null, q: null,
  }));
  fc("E", 10, 0.02);
  assert.equal(store.atRisk("B"), "B");
  fc("G", 10, 0.4);
  assert.equal(store.atRisk("B"), "G");
  store.handle(`${P}/sat/E/state`, JSON.stringify({ v: 1, sat: "E", t: 10, state: "flagged" }));
  assert.equal(store.atRisk("B"), "E"); // an abnormal state outranks a higher P
  store.handle(...ev(20, "E", "decision", { event: "state", from: "nominal", to: "flagged", trigger: "forecast", p: 0.3, eta_s: 40 }));
  fc("E", 30, 0.3);
  assert.equal(Math.round(store.etaLeft("E")), 30);
  store.handle(...ev(31, "E", "decision", { event: "decision", action: "isolate_subsystem", eta_s: 29 }));
  assert.equal(store.etaLeft("E"), null); // contained: no countdown
});

test("a link enabled again while feasible is a reroute (drawn once)", () => {
  const store = createStore();
  const link = (enabled, t) =>
    store.handle(`${P}/link/C-D`, JSON.stringify({ v: 1, t, a: "C", b: "D", feasible: true, enabled, range_km: 900 }));
  link(true, 1);
  link(false, 2);
  link(true, 3);
  assert.deepEqual(store.reroutes, [{ key: "C-D", t: 3 }]);
});

test("a repeated sentence within 60 s is shown once; an estimated ETA says so", () => {
  const store = createStore();
  for (const t of [10, 25, 40]) store.handle(...ev(t, "B", "monitor", { rule: "heater_inconsistent" }));
  assert.equal(store.timeline.length, 1);
  const [line] = narrate({ t: 1, sat: "E", kind: "decision", data: { event: "decision", action: "isolate_subsystem", eta_s: 29, estimated: true, channel: "temp_c" } });
  assert.equal(line.text, "E contained: heater cut. Temperature limit avoided by about 29 s (temperature estimated).");
  const [bat] = narrate({ t: 1, sat: "B", kind: "decision", data: { event: "decision", action: "isolate_subsystem", eta_s: 26, channel: "soc_pct" } });
  assert.equal(bat.text, "B contained: heater cut. Battery limit avoided by 26 s.");
});

test("returning to nominal: 'no action needed' only when calm, never right after a heal", () => {
  const calm = narrate({ t: 1, sat: "E", kind: "decision", data: { event: "state", from: "flagged", to: "nominal", reason: "calm" } });
  const healed = narrate({ t: 2, sat: "E", kind: "heal", data: { event: "state", from: "flagged", to: "nominal", reason: "healed" } });
  assert.deepEqual(calm.map((l) => l.text), ["E: risk back to normal, no action needed"]);
  assert.deepEqual(healed, []);
});

test("every action has a plain phrase (no raw action names on the main screen)", () => {
  const { ACTION_LABEL } = require("../store.js");
  assert.deepEqual(ACTION_LABEL, {
    none: "No action",
    isolate_subsystem: "Heater cut",
    safe_mode: "Safe mode",
    block_source: "Source blocked",
    quarantine: "Quarantined, links cut",
  });
  for (const v of Object.values(ACTION_LABEL)) assert.doesNotMatch(v, /_/);
});

test("the chain shown is the open incident first, then contained, then a recently closed one", () => {
  const store = createStore();
  const inc = (t, id, status) => store.handle(`${P}/core/event`, JSON.stringify({
    v: 1, t, sat: "B", level: "alarm", kind: "incident", text: "x",
    data: { event: "incident", incident_id: id, status, sats: ["B"], victims: [], source: null, steps: [] },
  }));
  assert.equal(store.activeIncident(), null);
  inc(100, "INC-001-B", "closed");
  assert.equal(store.activeIncident().incident_id, "INC-001-B");
  inc(110, "INC-002-E", "contained");
  inc(120, "INC-003-A", "open");
  assert.equal(store.activeIncident().incident_id, "INC-003-A");
  inc(130, "INC-003-A", "closed");
  assert.equal(store.activeIncident().incident_id, "INC-002-E");
  store.latestT = 2000; // long after: closed incidents drop out
  inc(2000, "INC-002-E", "closed");
  assert.equal(store.activeIncident().incident_id, "INC-002-E");
  store.latestT = 3000;
  assert.equal(store.activeIncident(), null);
  assert.equal(store.timeline.length, 0); // incidents are not narrated twice
});

test("a confirmed chain outranks a newer suspected one; dismissed incidents are never shown", () => {
  const store = createStore();
  const inc = (t, id, status, extra) => store.handle(`${P}/core/event`, JSON.stringify({
    v: 1, t, sat: "E", level: "alarm", kind: "incident", text: "x",
    data: Object.assign({ event: "incident", incident_id: id, status, sats: ["E"], victims: [], source: null, steps: [] }, extra),
  }));
  inc(100, "INC-001-E", "contained", { confirmed: true });
  inc(120, "INC-002-H", "open", { confirmed: false });
  assert.equal(store.activeIncident().incident_id, "INC-001-E");
  inc(130, "INC-003-J", "closed", { confirmed: false, dismissed: true });
  assert.equal(store.activeIncident().incident_id, "INC-001-E");
});
