# AURORA interfaces (the contract)

**Version v1.2. Frozen 2026-10-11** (v1.1: SPARTA table, Section 7; v1.2: SAFE semantics, Section 3.2; no message format changed since v1). Source: `docs/CONTEXT.md` Section 6, plus decisions D2–D6, D8 and D9 of `docs/MASTER_PLAN.md`.
Code that publishes or consumes these messages must match this file. To change an interface, change this file **in the same commit** and say so in the commit message.
The Python implementation is `aurora_common/` (`schemas.py`, `topics.py`, `bus.py`); `tests/test_schemas.py` validates every example below.

## 1. Conventions

- **Transport:** MQTT 3.1.1/5 on Mosquitto (`ground/`): port **1883** (MQTT) and **9001** (MQTT over WebSockets, for the dashboard). Port 6379 belongs to the Phase 2 Redis fallback and is not used.
- **Topic prefix:** `aurora/v1/`. All topics below are relative to it.
- **Payload:** UTF-8 JSON object. Publishers we write send compact JSON (`{"a":1}`); every receiver must also accept spaced JSON (`{"a": 1}`).
- **Version:** every payload carries `"v": 1`.
- **Time:** `t` is Unix seconds, float, UTC, stamped by the publisher from its own clock. Decimal separator is always `.` (never the French-locale `,`).
- **Units** are in field names (`temp_c`, `soc_pct`, `range_km`, …). A field without a unit suffix is dimensionless or an enum.
- **Satellite ids:** `A` … `L` (12). Physical slots: `A`, `B`, `C`. In the final demo, `B` is the ESP8266 board (`src: "hw"`); every other satellite is simulated (`src: "twin"`).
- **Unknown extra fields** are allowed and ignored by receivers (forward compatibility). Missing required fields make a message invalid.

## 2. Topic tree

| Topic | Publisher | Who may subscribe | QoS | Retained |
|---|---|---|---|---|
| `sat/{id}/tm/{sub}` | hardware bridge, twin | anyone | 0 | no |
| `sat/{id}/tc` | ground, core, attacker, ISL relay (twin or bridge) | anyone | 1 | no |
| `sat/{id}/state` | core | anyone | 1 | **yes** |
| `link/{a}-{b}` | twin (geometry + enable flags) | anyone | 0 | no |
| `core/forecast/{id}` | core | anyone | 0 | no |
| `core/action` | core | anyone | 1 | no |
| `core/event` | core | anyone | 1 | no |
| `redteam/{id}` | attacker (`attacks/`) | **twin service and hardware bridge only** | 1 | no |
| `gt/episode`, `gt/event` | attacker (`attacks/`), twin | **recorder only** (and `mqtt_tail --gt` for humans) | 1 | no |

`{sub}` ∈ `thermal`, `eps`, `adcs`, `obc`, `comms`, `sun`. In `link/{a}-{b}`, `a < b` alphabetically (`link/A-B`, never `link/B-A`).

**Ground-truth isolation (CONTEXT rule 7, plan rule 0.5).** The CASSANDRA core subscribes to exactly these filters and nothing else:

```
aurora/v1/sat/+/tm/+
aurora/v1/sat/+/tc
aurora/v1/link/+
```

It must never subscribe to `aurora/v1/gt/#`, `aurora/v1/redteam/#`, `aurora/v1/#` or `#`, and never read `labels.json`, `ml_labels.json` or any `_malicious` / `_auth_ok` flag. `aurora_common.topics.CORE_ALLOWED_SUBSCRIPTIONS` and `FORBIDDEN_FOR_CORE` encode this; a test enforces it.

## 3. Message schemas

### 3.1 Telemetry: `sat/{id}/tm/{sub}`

```json
{"v":1,"sat":"B","src":"hw","t":1760000000.5,"seq":1234,"sub":"thermal","fields":{"temp_c":26.4,"heater_on":0},"emulated":["temp_c"]}
```

| Field | Type | Meaning |
|---|---|---|
| `sat` | id | satellite |
| `src` | `hw` \| `twin` \| `synthetic` | who produced it: real board, live twin, or offline synthetic data (D9) |
| `t` | float | sample time |
| `seq` | int ≥ 0 | increases by 1 per message of the same (`sat`, `sub`); gaps and repeats are evidence for the core |
| `sub` | enum | subsystem; must equal the `{sub}` in the topic |
| `fields` | object | per-subsystem fields below; all listed fields are required, extra fields are allowed |
| `emulated` | list of field names, optional | fields computed by firmware or software instead of measured (the ESP8266 sends `["temp_c"]`) |

A satellite may publish only some subsystems (the ESP8266 board publishes `thermal` and `obc`).

| `sub` | Required fields (units) |
|---|---|
| `thermal` | `temp_c` (°C), `heater_on` (0 \| 1) |
| `eps` | `soc_pct` (%), `bus_v` (V), `load_w` (W), `solar_w` (W), `current_ma` (mA) |
| `adcs` | `att_err_deg` (°), `wheel_rpm` (list of 3, rpm), `rate_dps` (°/s) |
| `obc` | `mode` (`NOMINAL` \| `SAFE` \| `QUARANTINED`), `cmd_ok` (cumulative count), `cmd_rej` (cumulative count), `last_origin` (origin string, may be `""` before the first command) |
| `comms` | `ground_contact` (0 \| 1), `isl_peers` (list of ids with an enabled link), `rssi_dbm` (dBm) |
| `sun` | `level` (relative, ≥ 0, 1 = full sun), `eclipse` (0 \| 1) |

Receivers also accept `isl_peers` as a comma-separated string (`"A,C"`), which the offline generator writes; publishers send a list.

### 3.2 Telecommand: `sat/{id}/tc`

```json
{"v":1,"id":"7f3c9a1e2b4d4c6f8a0b1c2d3e4f5061","t":1760000001.0,"sat":"B","origin":"ground","cmd":"HEATER","args":{"on":true}}
```

| Field | Type | Meaning |
|---|---|---|
| `id` | string | unique per command; new commands use a uuid4 hex. Relays keep the original `id`, so a receiver can drop a command it has already executed |
| `sat` | id | destination satellite; must equal `{id}` in the topic |
| `origin` | `ground` \| `core` \| `isl:<id>` \| `unknown` | where the command came from (D6). `core` marks the defence's own commands |
| `cmd` | enum | see below |
| `args` | object | per command |

| `cmd` | `args` |
|---|---|
| `HEATER` | `{"on": bool}` |
| `SAFE_MODE` | `{}` |
| `SET_MODE` | `{"mode": "NOMINAL" \| "SAFE" \| "SCIENCE" \| "IDLE" \| "WHEEL_TEST"}` (D3). `QUARANTINED` is a core **state** (3.3), not a mode that can be commanded |
| `ISL_ENABLE` | `{"peer": id, "on": bool}` |
| `PING` | `{}` |

The envelope (`id`, `sat`, `origin`, `cmd`) is validated strictly. **`args` that do not match the table are still delivered**: attackers send malformed commands, and the satellite rejects them (its `cmd_rej` counter rises). Publishers we write (core, attack scripts) must send valid `args`; `aurora_common.schemas.args_problems()` checks them.

**Satellite behaviour in SAFE (semantics, v1.2).** Every plant follows the same rule: the twin, `scripts/replay_twin.py`, the ESP8266 bridge, and `ml/synthetic/engine.py`.
- `SAFE_MODE` (or `SET_MODE {"mode":"SAFE"}`) puts the satellite in SAFE: heater off, non-essential loads off.
- While in SAFE, the satellite **refuses `HEATER {"on": true}` from any origin**, including `ground` and `core` (load shedding). It never asks whether a command is malicious; it cannot know.
- While in SAFE, it refuses commands whose origin is not `ground` or `core` (CONTEXT §5.1), and refuses `SET_MODE` to any mode other than `NOMINAL` or `SAFE`.
- **Only `SET_MODE {"mode":"NOMINAL"}` exits SAFE.**
- Still accepted in SAFE from `ground` or `core`: `HEATER {"on": false}`, `SAFE_MODE`, `ISL_ENABLE`, `PING`.
- A refused command increments `obc.cmd_rej`; an accepted one increments `obc.cmd_ok`.

### 3.3 Node state: `sat/{id}/state` (retained)

```json
{"v":1,"sat":"B","t":1760000002.0,"state":"flagged","reason":"P(F<30s)=0.41 (trajectory-v0)"}
```

`state` ∈ `nominal` (green, G) \| `flagged` (red, R) \| `quarantined` (off, O) \| `safe_hold` (off, O). `reason` is optional. Retained, so a dashboard or bridge that reconnects gets the current state at once.

### 3.4 Link: `link/{a}-{b}`

```json
{"v":1,"t":1760000002.0,"a":"A","b":"B","feasible":true,"enabled":false,"range_km":1830.0}
```

`feasible`: line of sight and range allow the link (geometry). `enabled`: both ends have it enabled (`ISL_ENABLE`). A message crosses the link only if both are true. With one ESP8266 board, enabling or disabling a link of `B` is logical (no radio).

### 3.5 Forecast: `core/forecast/{id}`

```json
{"v":1,"sat":"B","t":1760000002.0,"model":"tgn-v1","p_fail":{"10":0.08,"30":0.41,"60":0.77,"120":0.93},"p_std":{"10":0.01,"30":0.05,"60":0.06,"120":0.05},"q":0.06,"next_sub":"eps","p_malicious":0.86}
```

| Field | Type | Meaning |
|---|---|---|
| `model` | string | which forecaster produced it, e.g. `tgn-v1` (ensemble), `trajectory-v0` (Phase 2 logic), `tgn-v1-uncalibrated` |
| `p_fail` | object, keys `"10"`, `"30"`, `"60"`, `"120"` | P(F < t + Δ), each in [0, 1], non-decreasing in Δ |
| `p_std` | object with the same keys, or `null` | ensemble **standard deviation** of P(F < t + Δ) (D4). `null` with fewer than 2 models |
| `q` | float or `null` | the uncertainty used by the gate: `p_std` at the decision horizon (60 s by default). `null` when unavailable (D4); never `0` as a stand-in |
| `next_sub` | subsystem or `null` | next affected subsystem; `null` if the model does not predict it (e.g. trajectory) |
| `p_malicious` | float or `null` | P(malicious \| anomaly); `null` if the model does not predict it |
| `warming_up` | bool, optional | `true` until the input window is full |

### 3.6 Action: `core/action`

```json
{"v":1,"t":1760000003.0,"action":"quarantine","target":"B","J":0.21,"alternatives":[{"action":"safe_mode","J":0.34},{"action":"none","J":0.92}],"gated":false,"q":0.06,"reason":"P(F<30s)=0.88 on THERMAL->EPS path (IA-0009)"}
```

| Field | Type | Meaning |
|---|---|---|
| `action` | `none` \| `safe_mode` \| `block_source` \| `cut_heater` \| `quarantine` | chosen action (`quarantine` includes the reroute over feasible links) |
| `target` | id | satellite acted on |
| `args` | object, optional | e.g. `{"origin":"isl:A"}` for `block_source`, `{"subsystem":"thermal","effect":"heater_off"}` for `cut_heater` |
| `J` | float or `null` | cost of the chosen action, J(a) = w_r·R + w_m·(1−U) + w_c·C + w_q·Q |
| `alternatives` | list of `{action, J}` | the other candidates, best first |
| `gated` | bool or `null` | `true`: Q unknown/invalid or above θ_uncert (0.35); link isolation/quarantine withheld, supported local protection allowed. Q remains `null` when unavailable; event data reports `UNKNOWN`, `KNOWN_LOW` or `KNOWN_HIGH` |
| `q` | float or `null` | the Q used by the gate |
| `reason` | string | one-line explanation |

### 3.7 Event: `core/event`

```json
{"v":1,"t":1760000003.0,"sat":"B","level":"alarm","kind":"decision","text":"[B] P(F<30s)=0.88 (trajectory-v0) · cause: heater latched ON (origin=ground) · action: quarantine (J=0.21)","sparta":["IA-0009"]}
```

| Field | Type | Meaning |
|---|---|---|
| `sat` | id or `null` | `null` for system-wide events |
| `level` | `info` \| `warning` \| `alarm` | severity |
| `kind` | `startup` \| `forecast` \| `monitor` \| `decision` \| `gated` \| `budget` \| `heal` \| `ingest` \| `clock` | what produced it |
| `text` | string | the human-readable line (dashboard event log) |
| `sparta` | list of strings, optional | SPARTA technique / countermeasure ids |
| `data` | object, optional | machine-readable details |

### 3.8 Red team: `redteam/{id}` (D8)

Effects that need a compromised on-board computer. Only the twin service and the hardware bridge subscribe. **The core never does.**

```json
{"v":1,"t":1760000010.0,"sat":"B","effect":"freeze_tm","args":{"sub":"thermal","field":"temp_c"}}
```

| `effect` | `args` | Meaning |
|---|---|---|
| `freeze_tm` | `{"sub", "field"}` | hold the last reported value of that field while the physics continues (B1) |
| `bias_tm` | `{"sub", "field", "rate_per_s"}` | add a slowly growing offset to the reported value (B2) |
| `relay_tc` | `{"cmd", "args", "delay_s"?}` | the satellite forwards this command to every neighbour over feasible, enabled links, published on `sat/{nbr}/tc` with `origin: "isl:<id>"` (C1) |
| `stop` | `{}` | clear all red-team effects on that satellite |

### 3.9 Ground truth: `gt/episode`, `gt/event` (recorder only)

```json
{"v":1,"t":1760000000.0,"phase":"start","episode_id":"live-20261011-153000","generator":"attacks-v1","seed":0,"scenario":"A","kind":"attack","type":"A1","target":"E","onset_t":1760000010.0,"critical_t":null,"critical_rule":null,"params":{"intensity":1.0},"satellites":["A","B","C","D","E","F","G","H","I","J","K","L"]}
{"v":1,"t":1760000010.0,"episode_id":"live-20261011-153000","event":"onset","sat":"E","data":{"cmd":"HEATER"}}
```

- `gt/episode`: the `labels.json` keys of Section 4, plus `phase` ∈ `start` \| `end`. The `end` message carries the final `critical_t` and `critical_rule`.
- `gt/event`: `event` ∈ `onset` \| `stop` \| `critical`, with `sat` and optional `data`.

## 4. Episode format (twin, recorder and offline generator)

```
episodes/{episode_id}/telemetry.parquet   one row per (t, sat, sub): t, sat, src, seq, sub, <fields>
episodes/{episode_id}/commands.parquet    one row per telecommand: v, id, t, sat, origin, cmd, args (JSON string), accepted, src
episodes/{episode_id}/links.parquet       one row per (t, a, b): t, a, b, feasible, enabled, range_km
episodes/{episode_id}/labels.json         episode ground truth (below)
episodes/{episode_id}/ml_labels.json      per-satellite ground truth (D2)
```

In Parquet, `wheel_rpm` is flattened to `wheel_rpm_0`, `wheel_rpm_1`, `wheel_rpm_2`, and `isl_peers` is a comma-separated string.

`labels.json`:

```json
{"episode_id":"tw-000123","generator":"twin-v1","seed":123,"scenario":"A","kind":"attack","type":"A1","target":"E","onset_t":1760000300.0,"critical_t":1760000412.0,"critical_rule":"T_bus>T_max","params":{"intensity":0.8},"satellites":["A","B","C","D","E","F","G","H","I","J","K","L"]}
```

`scenario` ∈ `A` \| `B` \| `C` \| `F` (fault) \| `N` (clean); `kind` ∈ `attack` \| `fault` \| `clean`; `type` ∈ `A1`–`A3`, `B1`, `B2`, `C1`, `F1`–`F5`, `N`. Times are Unix seconds. `critical_t` is `null` if F is never reached (clean, fault, or prevented). `critical_rule` ∈ `T_bus>T_max` \| `SOC<SOC_min` \| `att_err>theta_max`.

`ml_labels.json` (D2): per-satellite truth, times in **seconds since `t0`**, with `t0` (Unix seconds) stored in the file.

```json
{"episode_id":"tw-000123","t0":1760000000.0,"dt_s":1.0,"n_ticks":600,"family":{"drift":"ramp","noise":"gauss","precursor":"burst","intensity":0.8,"realnoise":false},"satellites":{"E":{"onset_t":300.0,"critical_t":412.0,"critical_rule":"T_bus>T_max","malicious":true,"affected_t":{"obc":300.0,"thermal":303.0,"eps":340.0},"reported_masked":[]}}}
```

Only satellites with an anomaly need to be listed.

## 5. Critical state and limits

F = (|att_err_deg| > θ_max) ∨ (soc_pct < SOC_min) ∨ (temp_c > T_max), **per satellite, configurable**:

| Satellite kind | T_max | SOC_min | θ_max |
|---|---|---|---|
| twin (default) | 45 °C | 30 % | 10° |
| ESP8266 board (`B`, emulated temperature) | 60 °C | n/a | n/a |
| future ESP32 hardware | 45 °C | 30 % | n/a |

## 6. ESP8266 serial protocol (bridge ↔ firmware, `prototype/phase2-demo/`)

Not MQTT, but fixed by the firmware already flashed, so recorded here. 115200 baud, lines end with `\n`.
- Board → bridge, every 500 ms: `TELEM sim=<°C> heater=<0|1> heap=<bytes>` (temperature emulated by the firmware: 25 °C ambient, +1.5 °C/s while the heater is latched, ceiling 95 °C).
- Bridge → board: `H` heater on, `C` heater off, `<idx><G|R|O>` LED state with idx **1 = A, 2 = B, 3 = C**.
- Node state → LED: `nominal` → `G`, `flagged` → `R`, `quarantined` / `safe_hold` → `O` (both off).

## 7. SPARTA mapping

Used in `core/event.sparta` and `core/action.reason`. Proposal P4 (`docs/INTERFACE_PROPOSALS.md`) adopted on 2026-10-11. The submitted paper used IA-0006 for masking and IA-0009 for propagation; wherever the paper or slides show the new ids, add the footnote *"Technique mapping refined in Phase 3."*

| Scenario | Technique |
|---|---|
| A: cyber-to-physical cascade (A1–A3) | IA-0009 Trusted Relationship |
| B: telemetry masking, freeze (B1) | DE-0002 Disrupt or Deceive Downlink |
| B: telemetry masking, replay (B1 replay variant) | EX-0001 Replay |
| B: slow bias (B2) | DE-0003 On-Board Values Obfuscation |
| C: cross-satellite propagation (C1) | IA-0003 Crosslink via Compromised Neighbor |

| Response | Countermeasure |
|---|---|
| detection (forecast, monitor) | CM0032 On-Board Intrusion Detection & Prevention |
| safe mode, isolate subsystem, quarantine (contain → observe → heal) | CM0042 Robust Fault Management (and CM0032) |
| reroute over feasible links | CM0070 Alternate Communications Paths |

## Changelog

- **v1.2, 2026-10-11:** SAFE semantics (Section 3.2): in SAFE, `HEATER on` is refused from any origin, `SET_MODE` other than `NOMINAL`/`SAFE` is refused, and only `SET_MODE NOMINAL` exits SAFE. Semantics only; no message format changed.

- **v1.1, 2026-10-11:** SPARTA mapping (Section 7) adopts proposal P4: masking DE-0002 / DE-0003 (EX-0001 for replay), propagation IA-0003, reroute CM0070, isolation CM0032 / CM0042. No message format changed.

- **v1, 2026-10-11:** frozen. From CONTEXT §6 plus: `redteam/{id}` topic (D8); `src: synthetic` (D9); `SET_MODE` enum (D3); `p_std` is a standard deviation and `q`, `p_std` may be `null` (D4); `origin` enum (D6); `ml_labels.json` with `t0` (D2); `emulated`, `warming_up`, `reason`, `args` optional fields; `core/event` schema; per-satellite limits; QoS, retained and subscription rules.
