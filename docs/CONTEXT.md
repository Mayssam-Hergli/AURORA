# AURORA / CASSANDRA: Full Project Context for Claude Code

- **Repository:** github.com/Mayssam-Hergli/AURORA
- **Competition:** IASTAM 6.0 Technical Challenge, Track 5 (Cybersecurity), Problem 10: "Detecting an Attack Before Mission Failure"
- **Team (INSAT, Tunis):** Mayssam Hergli (lead: architecture, defence core, red team), Youssef Mhamdi, Rami Troudi, Eya Jendoubi, Youssef Atig
- **Document date:** 6 October 2026. **Phase 3 build deadline:** 14 October 2026. **Final:** live demo at the Congress (with a recorded backup).

> **Naming.** The repository is called **AURORA**. The defence system described in our Phase 2 paper is called **CASSANDRA**. Unless the team says otherwise: AURORA = the whole project/testbed (hardware, twin, ground station, dashboard); CASSANDRA = the predictive defence inside it. Software namespace and MQTT prefix: `aurora/`. Python package for the defence core: `cassandra/`. If the team renames the system, rename globally in one commit.

## 0. How to use this document (instructions for Claude Code)

You are joining a 5-person student team that has 8 days left to build the final version of this system. This document is the single source of truth for context. Read it fully before writing code.

### Rules you must follow

1. **Never fabricate results.** No invented metrics, no "achieves 99%". Numbers come only from code that ran. Anything not built is labelled *planned*.
2. **Be honest about what is emulated.** If a sensor, satellite or model is simulated, say so in docs, logs and the dashboard.
3. **`docs/interfaces.md` is the contract.** Topics, message schemas, episode format and command format live there (Section 6). Code must match it. If an interface must change, update the doc in the same commit and say so in the commit message.
4. **Small, reviewable steps.** One feature per branch, pull requests into `main`. Do not commit until the user approves. Never force-push.
5. **Never commit data, secrets or large binaries.** Datasets are downloaded by scripts into `data/` (gitignored).
6. **Stack:** Python 3.11, type hints, `ruff` + `black`, `pytest`, configs in YAML, deterministic seeds. Firmware: Arduino framework (PlatformIO preferred, Arduino IDE acceptable). Dashboard: NASA Open MCT (Node.js).
7. **Ground truth must never leak into the defence.** Attack labels travel on a separate topic (`aurora/v1/gt/...`) that only the evaluation recorder reads. CASSANDRA code must not subscribe to it.
8. **Ask before** deleting files, changing interfaces, adding heavy dependencies, or touching hardware pin maps.
9. **Developer environment is Windows + WSL (Ubuntu).** See the gotchas in Section 10 before giving setup instructions.

Your first tasks are in Section 12. Do them in order.

## 1. The competition

**Problem 10 statement (summary):** develop a mechanism that detects malicious or abnormal behaviour on a satellite across commands, telemetry, network activity and software behaviour, **without interrupting the mission**. Evaluated on detection rate, false positives, detection time, robustness and availability impact.

**What judges scored at mid-review (out of 100):** technical architecture and design 20, actual prototype progress 20, understanding of the problem 15, feasibility and realism 15, relevance and originality 15, methodology and planning 10, presentation 5. Expect similar weighting at the final: **a working system with real numbers beats slides.**

**Timeline:**
- Phase 2 (done, passed): interim paper + 2-minute video with a hardware demo.
- **Phase 3 (now, until 14 Oct):** full build.
- **Final:** live demo + pitch at the Congress, recorded backup.

## 2. The idea

**The problem.** Attacks on space systems rarely begin with an obvious failure. The 2022 Viasat KA-SAT attack used a compromised management segment to push a wiper to tens of thousands of terminals; the chain unfolded in stages. Early signals are weak and spread across subsystems (an odd command, a small thermal drift, a slight attitude perturbation). Threshold alarms on single parameters fire late, when the physical damage is already under way.

**The idea.** Existing detectors ask "is this value abnormal right now?". CASSANDRA asks "where is this attack going, and how long until the mission fails?", and acts before the critical point with the least disruptive response.

**Six-stage loop:** Observe → Understand → Forecast → Simulate → Act → Verify.

1. **Observe:** ingest, time-align and check provenance of telemetry, commands and link status.
2. **Understand:** build a typed cyber-physical graph G_t for the current second.
3. **Forecast:** a temporal graph model outputs, per satellite, P(F < t + Δ): the probability of reaching a critical state within horizon Δ, plus the next affected subsystem.
4. **Simulate:** when risk is high, clone the digital twin and play candidate responses forward; score each with J(a).
5. **Act:** execute the best response, subject to safety gates.
6. **Verify:** check the residual risk dropped; heal and reintegrate when stable.

### The graph G_t = (V, E_t, X_t)

**Nodes V:** each satellite's subsystems (THERMAL, EPS, ADCS, OBC, COMMS, SUN sensor) and each satellite as a whole.

**Edge families:**
- **E_eng:** engineering dependencies from secure-by-component decomposition (heater → temperature → battery; wheels → attitude → solar power). These bound which propagation paths are physically possible.
- **E_SPARTA:** attack relations from the SPARTA framework.
- **E_learned:** temporal couplings (strength w, delay τ), learned only where E_eng ∪ E_SPARTA already permit an edge.
- **E_ISL(t):** inter-satellite links that physically exist at time t (line of sight and range).

**Features X_t:** time-aligned telemetry, command statistics, physical invariants.

**Physical residual** ΔΦ_ij = |observed change at j − change predicted by the engineering model along (i, j)|. This separates benign operations from induced effects. The digital twin provides the engineering model.

### Response selection

J(a) = w_r·R(a) + w_m·[1 − U(a)] + w_c·C(a) + w_q·Q(a)

- R = residual risk after action a (forecast P(F) on the twin after applying a)
- U = preserved mission capability in [0, 1]
- C = operational cost
- Q = model uncertainty (variance across an ensemble of forecaster heads)

Weights set per mission phase by operators (config file).

### Safety gates (anti-self-DoS)

An attacker could fake precursors to make the defence isolate healthy satellites. Two rules:

1. If Q(a) > θ_uncert (default 0.35), do not act autonomously; enter safe hold and alert ground.
2. Autonomous isolation is limited to 25% of constellation capacity per orbital period.

**SPARTA mapping.** Responses operationalise SPARTA countermeasures: CM0032 (onboard intrusion detection), CM0042 (robust fault management: contain → observe → heal), CM0070 (alternate communication paths: reroute only over feasible links).

**Claims we do NOT make.** Never say "the mission never stops" or "zero packet loss". Say "minimises mission impact".

## 3. Threat model and scenarios

| ID | Scenario | SPARTA | Mechanism | Critical state F |
|---|---|---|---|---|
| A | Cyber-to-physical cascade | IA-0009 (trusted relationship) | Malicious telecommand or schedule via abused ground trust drives thermal, power or attitude toward failure | T_bus > T_max, or SOC < SOC_min, or attitude error > θ_max |
| B | Telemetry masking | IA-0006 (compromised hosted payload) | Forged "healthy" telemetry hides real degradation | Same as A, but hidden; detected by cross-domain inconsistency |
| C | Cross-satellite propagation | IA-0009 | A compromised node pushes malicious commands to neighbours over inter-satellite links | Critical state reached on ≥ 1 neighbour |

**Formal critical state (paper Eq. 1):** F = (|θ_err| > θ_max) ∨ (SOC < SOC_min) ∨ (T_bus > T_max).

**Assumption:** the adversary cannot falsify every independent measurement channel of a subsystem at once.

**Natural faults** (must not be confused with attacks): sensor drift, radiation bit-flip (SEU), battery ageing, normal eclipse entry, a genuinely stuck heater.

**Known limits (state them honestly):** CASSANDRA would not have prevented KA-SAT (a ground-segment wiper); the loop runs at ms–s cadence, so no µs-scale reaction (fault injection, RF onset); no lead time against a single destructive command with no precursor; cross-checks fail if every channel is compromised; labelled real attack data is scarce.

## 4. Phase 2: what exists and what we learned

### 4.1 What was built and demonstrated (Scenario A)

**Hardware:** NodeMCU Amica (ESP8266, CP2102 USB chip) acting as satellite B, with a green and a red LED for each of satellites A, B, C on a half-size breadboard (each satellite's two LEDs share one 150 Ω resistor to GND). Pins: SatA D1/D2, SatB D5/D6, SatC D7/D0 (green/red). D3, D4, D8 left free (boot pins).

**Firmware** (`sat8266_main.cpp`): emulated thermal model: 25 °C ambient ±0.1 °C noise; heater latched → +1.5 °C/s, saturating at 95 °C; release → first-order cooling (τ ≈ 3 s). Serial 115200: sends `TELEM sim=<°C> heater=<0|1> heap=<n>` at 2 Hz; accepts `<idx><G|R|O>` LED commands and `H` / `C` (heater on/off). The FLASH button toggles the heater; the blue on-board LED shows the heater state.

**Bridge** (`esp_link_thermal.ps1`): pure PowerShell (no installs). Owns COM9, talks to Redis via raw RESP over TCP `localhost:6379`. Writes `telemetry:temp`, `truth:temp`, `hw:heater`; forwards `attack:thermal` → H / C; drives LEDs from `node:sat_a|b|c` (green / red / off).

**Bus:** Redis 7 in Docker inside WSL (`sat_b_redis`, `reset.sh` = `docker compose down -v && up -d`).

**Services (Python, WSL):**
- `predictor.py`: trajectory forecaster over the last 8 samples. t_c = (T_max − T)/Ṫ; P̂ = clip(1 − t_c/H, 0, 1), H = 30 s; flags (red) at P̂ ≥ 0.30, quarantines (off) at P̂ ≥ 0.85, cuts links A–B and B–C, opens A–C, sends safe mode (releases heater), observes, heals when T < 30 °C for 3 s. **Heuristic, not calibrated.**
- `monitor.py`: Scenario B cross-domain check (telemetry vs truth, tolerance 3 °C, two consecutive checks) → contain (docker pause) → observe → heal → verify.
- `attacks/attack_thermal.py` (heater latch), `attack_masked_thermal.py` (masking + heater), `attack_redis_inject.py` (old software-only masking).

**Measured run (paper Table II):** flagged 2.3 s after onset (28.5 °C), quarantined at 18.8 s (53.3 °C), the threshold alarm would have fired at 23.3 s (60 °C, never reached), healed at 27 s. **Margin before the critical limit: 4.5 s.** Neighbours stayed in service.

### 4.2 Phase 2 deliverables (in `docs/`)

- Interim paper (IEEE, 5 pages): honest prototype results, evaluation plan, limitations, Phase 3 milestones M1–M6.
- Demonstrator technical note (4 terminals, G/R/O code, wiring).
- 2-minute video with voiceover.

### 4.3 Why Phase 3 changes the architecture

The Phase 2 demo depended on Windows ↔ WSL ↔ USB serial bridging, which was fragile. **Phase 3 removes it:** all satellite nodes talk over WiFi/MQTT to a Raspberry Pi ground station (native Linux). No COM ports in the critical path.

## 5. Phase 3 target architecture

```
+--------------------------------------------------+
3 PHYSICAL NODES        |  RASPBERRY PI (ground station)                  |
ESP32 + real sensors     |                                                 |
A ──ESP-NOW── B ──ESP-NOW── C    Mosquitto MQTT broker (aurora/v1/...)     |
│ WiFi/MQTT │            │     | ▲         ▲            ▲                 |
└────────────┴────────┴──────────────►│         │            │             |
                          | DIGITAL TWIN │ CASSANDRA CORE │ RECORDER     |
                          | 9 simulated  │ ingest → graph → │ (runs,      |
                          | satellites + │ forecaster → sandbox│ labels,  |
                          | orbit engine │ → safety → executor │ metrics) |
+--------------------------------------------------+
                          │ WebSocket
         LAPTOP: Open MCT dashboard (projector)
```

### 5.1 Physical satellite nodes (3 × ESP32)

Each node is a full satellite: its own sensors, heater, status LEDs, radio links, and power bank.

**Bill of materials (per node; buy 1 spare of everything):**

| Item | Purpose |
|---|---|
| ESP32 DevKit (ESP-WROOM-32) | On-board computer |
| DS18B20 temperature sensor + 4.7 kΩ pull-up | Bus temperature (THERMAL) |
| INA219 current/voltage module | Heater/load current (EPS) |
| LDR + 10 kΩ divider (or BH1750 I²C light sensor) | Sun sensor (independent reference for Scenario B) |
| 100 Ω 1 W resistor | Heater, taped to the DS18B20 |
| Logic-level N-MOSFET (AO3400 / IRLZ44N) + 220 Ω gate resistor + 100 kΩ gate pull-down | Heater switch (low side, 5 V supply ≈ 50 mA ≈ 0.25 W) |
| Green + red LED, shared 150 Ω resistor | Node state |
| USB power bank | Untethered operation |

**Ground-station items:** Raspberry Pi 4 with SD card and power supply; a dedicated WiFi router or phone hotspot on a fixed channel (never depend on venue WiFi).

**ESP32 pin map (proposal; confirm on the actual board):**

| Function | GPIO | Notes |
|---|---|---|
| DS18B20 data | 4 | 4.7 kΩ to 3V3 |
| I²C SDA / SCL (INA219, BH1750) | 21 / 22 | |
| LDR analog | 34 | ADC1 (works with WiFi on), input-only |
| Heater MOSFET gate | 26 | 220 Ω series, 100 kΩ to GND |
| Status LED green / red | 25 / 27 | |
| On-board LED (heater indicator) | 2 | |
| Avoid | 0, 2 (as input), 12, 15, 6–11 | Boot strapping and flash pins |

**Physical safety:** critical limit T_max = **45 °C** on hardware (the heater can reach ~55–60 °C). Firmware enforces a hard cut-off at 55 °C regardless of commands.

**Firmware responsibilities:**

1. Read sensors at 2 Hz; publish telemetry (Section 6.2) with a sequence number.
2. Accept telecommands from MQTT (ground link) and ESP-NOW (inter-satellite link); log the source of every command.
3. Maintain an emulated battery: SOC decreases with measured load current (INA219) and increases with "solar" input from the light sensor.
4. ESP-NOW inter-satellite links: neighbour table, enable/disable per peer on command (this is how quarantine and rerouting become physical), forward relayed traffic.
5. Drive the status LEDs from the node-state topic; the on-board LED shows the heater.
6. Safe mode: heater off, minimal loads, ignore non-ground commands.

**WiFi + ESP-NOW constraint:** ESP-NOW must use the same channel as the WiFi access point. Fix the router's channel (e.g. channel 1) and set it in firmware config.

### 5.2 Ground station (Raspberry Pi, native Linux)

Runs Mosquitto, the digital twin, the CASSANDRA core, the recorder and the WebSocket bridge for the dashboard. Managed with `docker compose` or systemd. One command starts everything; one command resets.

### 5.3 Digital twin

**Purpose (three jobs):**

1. **Scale:** 9 simulated satellites join the 3 physical ones in the same message format, giving a 12-satellite constellation.
2. **Data factory:** runs at ~100× real time to generate thousands of labelled attack, fault and clean episodes.
3. **Counterfactual sandbox:** clone the current state, play each candidate response forward, return outcomes for J(a).

**Constellation:** 12 satellites, IDs A–L. Physical: A, B, C. Circular orbits, 550 km altitude (period ≈ 95.6 min), 3 planes × 4 satellites. Ground station at Tunis (36.8° N, 10.2° E), minimum elevation 10°.

**Geometry (Skyfield or analytic):** eclipse by cylindrical Earth shadow. Link (i, j) exists iff the segment between them clears Earth radius + 100 km **and** distance ≤ R_comm (default 5 000 km). Ground contact iff elevation > 10°. For the live demo, orbits may run time-compressed (config `orbit_speedup`); subsystem physics always runs at 1 s steps.

**Subsystem models (1 s step; first-order, parameters in `twin/config.yaml`):**

- **THERMAL:** C·dT/dt = P_heater + α·P_sun·(1 − eclipse) + P_int − k·(T − T_env)
- **EPS:** dSOC/dt = (η·P_solar·cos(sun angle)·(1 − eclipse) − P_load) / E_cap; heater power adds to P_load
- **ADCS:** attitude error with a first-order controller; reaction-wheel momentum accumulates; disturbance torques; saturation → loss of pointing → reduced solar power. Use SCAD's FireSat behaviour as a qualitative reference.
- **OBC:** mode machine (NOMINAL / SAFE / QUARANTINED), command queue, accepted/rejected counters, command-source log.
- **COMMS:** ISL table from geometry × enable flags; packet counters; ground contact windows.
- **SUN sensor:** derived from true sun vector and eclipse (independent of the EPS reading, which is what makes Scenario B detectable).

**Interfaces:** same MQTT topics and schemas as hardware (`src: "twin"`). Python API for the sandbox: `twin.snapshot()`, `twin.restore(s)`, `twin.apply(command)`, `twin.step(n)`, `twin.run_episode(spec) -> Episode`.

### 5.4 CASSANDRA core (`cassandra/` package)

| Module | Responsibility |
|---|---|
| `ingest` | Subscribe to telemetry/commands/links; time-align to 1 s ticks; check provenance and sequence numbers |
| `graph` | Build G_t per tick (node/edge types from 2 and 5.3); expose tensors for the model |
| `monitor` | Cross-domain consistency (e.g. sun sensor vs EPS solar input; reported vs independently measured temperature); tolerances in config |
| `forecast` | Load model, run inference each tick, publish forecasts; fallback to the trajectory forecaster if no model is loaded |
| `sandbox` | For candidate actions {do nothing, safe mode, block command source, isolate subsystem, quarantine satellite + reroute}, clone twin, simulate horizon (default 60 s), compute R, U, C, Q → J(a) |
| `safety` | Uncertainty gate (θ = 0.35), isolation rate limit (25% per orbit), hold-and-alert path |
| `executor` | Publish commands and state changes; enforce feasible-link rerouting only |
| `verify` | Track residual risk after action; heal and reintegrate when stable (e.g. back under limits for N s) |
| `explain` | Human-readable event log: cause → effect → projected failure, SPARTA IDs, chosen action and alternatives |

### 5.5 Learned forecaster (`ml/`)

- **Model:** temporal graph network: relation-aware message passing over the edge types (e.g. R-GCN or GAT) + GRU memory per node. Input window 30 s.
- **Heads:** P(F < t + Δ) for Δ ∈ {10, 30, 60, 120} s (sigmoid); next affected subsystem (softmax); malicious-vs-fault probability.
- **Training data:** twin episodes; optional pre-training/transfer on SCAD.
- **Splits:** by whole episode; hold out generator families (attack types/parameter ranges) to avoid learning the generator.
- **Calibration:** temperature scaling on validation; report ECE.
- **Uncertainty:** small ensemble (e.g. 5 heads or seeds); Q = variance of P(F).
- **Baselines (same evaluation):** static thresholds; LSTM autoencoder; trajectory forecaster (Phase 2); GDN (Graph Deviation Network, AAAI 2021); ablation without E_eng.
- **Deployment:** export TorchScript/ONNX; measure inference latency on the Raspberry Pi against the 150 ms budget.

### 5.6 External datasets (download scripts in `data/`, never committed)

| Dataset | Content | Use | Link / licence |
|---|---|---|---|
| SCAD | Industry FireSat digital twin (BigBear.ai, Redwire, Proof Labs). 120 baseline runs + 24 h long baseline + 1 800 attack runs (reaction wheel A/B/C override, 5 intensities, 12 months, several times of day). 10-min runs at 1 Hz, ~140 channels, per-second label `result_label`; attack starts at ~302 s. ~1.4 GB | External benchmark for single-satellite forecasting and propagation order | github.com/jwd164/space-cyber-anomaly-detection-dataset, CC BY 4.0 |
| ESA Anomaly Dataset / ESA-ADB | Real telemetry from 3 ESA missions, curated anomalies | False alarms on real operational noise | github.com/esa/anomaly-dataset, github.com/kplabs-pl/ESA-ADB (data on Zenodo) |
| CuCD-ID | Labelled NOS3/cFS telemetry with cyber attacks + COSMOS scripts | Command/flight-software layer (optional) | data.mendeley.com/datasets/7n2d42pm3n/3 |

**What we already measured on SCAD** (225 attack runs vs. their matched baseline runs; pre-onset difference is exactly 0, so every attack run has a perfect "no-attack" twin):

| Subsystem | Runs affected | Median delay after onset |
|---|---|---|
| Reaction wheels, torque rods | ~100% | 0 s |
| Body rates, wheel commands, attitude error, sun direction | ~45–50% | ~20–30 s |
| Star tracker | 22% | ~2 min |
| Power (charging, SOC) | 8% | ~2 min |

This is a measured cyber → actuator → attitude → sensor → power cascade, and ground truth for the "next affected subsystem" head. SCAD has no "mission failure" label: we define F on it ourselves (e.g. torque-rod saturation or pointing loss beyond θ_max) and document the choice.

### 5.7 Red-team and fault library (`attacks/`)

Each item is parameterised (onset, intensity, target, duration) and seeded.

| ID | Type | Description |
|---|---|---|
| A1 | Attack | Heater latch on a target satellite (thermal → power) |
| A2 | Attack | Attitude command override / wheel torque bias (SCAD-like; attitude → power) |
| A3 | Attack | Malicious schedule: repeated mode changes draining power |
| B1 | Attack | Telemetry freeze/replay on one channel while a real effect runs (masking) |
| B2 | Attack | Slow bias injected into one reported channel |
| C1 | Attack | Compromised satellite forwards malicious telecommands to neighbours over feasible ISLs (with relay delay) |
| F1–F5 | Fault | Sensor drift, SEU bit-flip, battery ageing, eclipse entry, hardware-stuck heater |
| N | Clean | Normal operations with noise (needed to calibrate the false-positive rate) |

On hardware, A1/B1/C1 are executed as real commands to the ESP32 nodes. Ground truth is published to `aurora/v1/gt/...` only.

### 5.8 Evaluation harness (`eval/`)

One command runs N episodes (twin or dataset) through the same CASSANDRA pipeline and writes a results folder with tables and plots for the paper.

**Definitions (match the paper):**

- **Alert time T_alert:** first tick where P(F < t + Δ) ≥ τ.
- **τ** is chosen on clean + fault episodes so that the **false-positive rate = 0.1%** of benign timesteps.
- **Warning lead time T_lead** = T_critical − T_alert (for episodes that reach F without intervention; computed with responses disabled). Report median and IQR; alerts after T_critical count as misses.
- Detection delay from onset; precision/recall at episode level.
- Next-affected-subsystem accuracy (top-1, top-3).
- Expected calibration error (10 bins).
- Response effectiveness on the twin (responses enabled): % of episodes where F is prevented; mission capability preserved (mean U); false isolations per orbit.
- Latency per tick (inference + planning) on the Raspberry Pi vs the 150 ms budget.

### 5.9 Dashboard (`dashboard/`, NASA Open MCT)

- MQTT → WebSocket bridge feeding an Open MCT telemetry plugin.
- Views: constellation map (orbits, live links, node colours G/R/O); per-satellite telemetry plots; P(F) curves with confidence band and threshold line; event log from `explain`; link status table; "emulated" badge on simulated satellites.
- Must run full-screen on a projector and survive reconnects.

## 6. Interfaces (copy into `docs/interfaces.md`)

### 6.1 MQTT topic tree (prefix `aurora/v1/`)

| Topic | Publisher | Content |
|---|---|---|
| `sat/{id}/tm/{sub}` | node or twin | Telemetry for one subsystem (`thermal`, `eps`, `adcs`, `obc`, `comms`, `sun`) |
| `sat/{id}/tc` | ground, core, attacker, ISL relay | Telecommand to satellite {id} |
| `sat/{id}/state` | core | Node state |
| `link/{a}-{b}` | twin geometry / core | Link status (a < b alphabetically) |
| `core/forecast/{id}` | core | Forecast outputs |
| `core/action` | core | Chosen action and alternatives |
| `core/event` | core | Explainable event log |
| `gt/episode`, `gt/event` | red team / twin | **Ground truth, recorder only** |

QoS 1 for commands and state, QoS 0 for telemetry. Timestamps: Unix seconds (float, UTC). Units in field names.

### 6.2 Message schemas (JSON)

```jsonc
// telemetry aurora/v1/sat/B/tm/thermal
{"v":1,"sat":"B","src":"hw","t":1760000000.5,"seq":1234,
 "sub":"thermal","fields":{"temp_c":26.4,"heater_on":0}}

// telecommand aurora/v1/sat/B/tc
{"v":1,"id":"7f3c...","t":1760000001.0,"sat":"B","origin":"ground",
 "cmd":"HEATER","args":{"on":true}}
// cmd ∈ HEATER{on}, SAFE_MODE{}, SET_MODE{mode}, ISL_ENABLE{peer,on}, PING{}
// origin ∈ ground | core | isl:<peer_id> | unknown

// node state aurora/v1/sat/B/state
{"v":1,"sat":"B","t":1760000002.0,"state":"flagged"}
// state ∈ nominal (G) | flagged (R) | quarantined (O) | safe_hold

// link aurora/v1/link/A-B
{"v":1,"t":1760000002.0,"a":"A","b":"B","feasible":true,"enabled":false,"range_km":1830}

// forecast aurora/v1/core/forecast/B
{"v":1,"sat":"B","t":1760000002.0,"model":"tgn-v1",
 "p_fail":{"10":0.08,"30":0.41,"60":0.77,"120":0.93},"p_std":{"30":0.05},
 "next_sub":"eps","p_malicious":0.86}

// action aurora/v1/core/action
{"v":1,"t":1760000003.0,"action":"quarantine","target":"B","J":0.21,
 "alternatives":[{"action":"safe_mode","J":0.34},{"action":"none","J":0.92}],
 "gated":false,"reason":"P(F<30s)=0.88 on THERMAL->EPS path (IA-0009)"}
```

Per-subsystem fields (minimum): `thermal{temp_c, heater_on}`, `eps{soc_pct, bus_v, load_w, solar_w, current_ma}`, `adcs{att_err_deg, wheel_rpm[3], rate_dps}`, `obc{mode, cmd_ok, cmd_rej, last_origin}`, `comms{ground_contact, isl_peers[], rssi_dbm}`, `sun{level, eclipse}`.

### 6.3 Episode format (twin and recorder output)

- `episodes/{episode_id}/telemetry.parquet`: one row per (t, sat, sub), columns `t, sat, src, sub, <field>...`
- `episodes/{episode_id}/commands.parquet` and `links.parquet`
- `episodes/{episode_id}/labels.json`:

```json
{"episode_id":"tw-000123","generator":"twin-v1","seed":123,
 "scenario":"A","kind":"attack","type":"A1","target":"E",
 "onset_t":300.0,"critical_t":412.0,"critical_rule":"T_bus>T_max",
 "params":{"intensity":0.8},"satellites":["A","B","C","D","E","F","G","H","I","J","K","L"]}
```

`critical_t` is null if F is never reached (clean, fault, or prevented).

## 7. Repository layout

```
AURORA/
├── README.md
├── docs/                     paper (Phase 2), demo note, this context file, interfaces.md, architecture notes
├── firmware/                 ESP32 satellite node (PlatformIO project)
├── ground/                   Raspberry Pi setup: mosquitto config, docker-compose, systemd, MQTT↔WS bridge
├── twin/                     digital twin: subsystem models, orbit/geometry engine, episode generator, sandbox API
├── cassandra/                defence core: ingest, graph, monitor, forecast, sandbox client, safety, executor, verify, explain
├── ml/                       forecaster model, training, calibration, baselines, export
├── eval/                     evaluation harness, metrics, dataset loaders (SCAD, ESA-ADB, CuCD-ID), plots
├── attacks/                  red-team and fault library (hardware + twin)
├── dashboard/                Open MCT app and plugins
├── data/                     gitignored; download scripts + README
├── prototype/phase2-demo/    the working Phase 2 demo (ESP8266 firmware, PowerShell bridge, Redis services)
└── tests/
```

## 8. Team ownership (fill in names)

| Area | Owner |
|---|---|
| Architecture, CASSANDRA core, red team, integration | Mayssam Hergli |
| Learned forecaster, calibration, baselines | TBD |
| Digital twin, episode generator, evaluation harness | TBD |
| Hardware nodes, firmware, ESP-NOW links, Raspberry Pi | TBD |
| Pitch, poster, video, paper updates | TBD |

## 9. Plan to the final (6 → 14 October)

| Day | Goals | Exit criterion |
|---|---|---|
| Tue 6 | Repo, README, skeleton, `docs/interfaces.md` frozen, parts ordered | Everyone can clone; interfaces reviewed by all owners |
| Wed 7 | Twin v0 (1 satellite, thermal + EPS) publishing to MQTT; firmware v0 (sensors → MQTT); core ingest + graph skeleton; SCAD loader + threshold baseline | One physical node and one twin satellite visible on the bus |
| Thu 8 | Twin v1 (12 satellites, orbits, ISL geometry, ADCS); episode generator + labels; forecaster v0 trained on early episodes | 1 000+ labelled episodes generated |
| Fri 9 | Sandbox + J(a) + safety gates; ESP-NOW links with enable/disable; dashboard v0 (Open MCT live telemetry) | Quarantine physically cuts a radio link |
| Sat 10 | Forecaster v1 + calibration + ensemble; baselines (LSTM-AE, GDN, ablation); attack library on hardware | Model beats thresholds on lead time on held-out episodes |
| Sun 11 | Full integration: 3 physical + 9 twin, end-to-end Scenarios A, B, C | Full loop runs live for 30 min without crash |
| Mon 12 | Evaluation runs (twin, SCAD, ESA-ADB), Raspberry Pi latency, results into paper | Results folder + plots committed |
| Tue 13 | Rehearsal, record backup demo, freeze `main` | Two clean rehearsals in a row |
| Wed 14 | Buffer, submission | Submitted |

**Paper milestones:** M1 generator (Oct 1–3, slipped), M2 forecaster + calibration, M3 Scenario B on hardware, M4 evaluation + ablation + robustness, M5 multi-node constellation (Scenario C), M6 final paper, poster, pitch.

## 10. Environment gotchas (learned the hard way)

1. **WSL cannot see USB serial devices.** COM ports exist only on Windows. Phase 3 avoids serial entirely (WiFi/MQTT).
2. **CP2102 boards** need the Silicon Labs CP210x driver on Windows.
3. **The university/home network blocks or throttles GitHub and some downloads** (winget, `Invoke-WebRequest`, Arduino board packages). PowerShell sometimes has no internet at all. Fallbacks: phone hotspot; download in the browser and place files manually; fix WSL DNS with `echo "nameserver 8.8.8.8" | sudo tee /etc/resolv.conf`.
4. **Only one program can hold a serial port.** Close the Arduino IDE Serial Monitor before flashing or bridging.
5. **ESP8266 boot pins D3, D4, D8** must not be loaded at boot. **ESP32 strapping pins 0, 2, 12, 15** likewise.
6. **ESP-NOW + WiFi** must share one channel; fix the router channel.
7. **Python venv:** the user runs scripts with the alias `pyc` (`~/cassandra-demo/venv/bin/python3`). In this repo, provide a Makefile or `scripts/` with explicit interpreter paths so teammates do not hit `ModuleNotFoundError`.
8. **VS Code terminal** defaults to PowerShell; switch to "Ubuntu (WSL)" for repo work.
9. **Locale is French on Windows** (decimal comma in some tools): parse/format numbers with an invariant culture.
10. A live demo needs a **dedicated router**, spare boards and cables, charged power banks, and a recorded backup.

## 11. Final demo script (target, ~3 minutes)

1. **Setup on the table:** 3 untethered ESP32 satellites; the dashboard on the projector shows 12 satellites in orbit (3 physical, marked; 9 emulated, marked).
2. **Scenario A (live, physical):** attacker sends a malicious heater command to B. The real heater warms the real sensor. P(F) rises on the dashboard while temperature is far from 45 °C; B turns red; the sandbox compares responses (shown with J values); CASSANDRA quarantines B: B's LED goes off, its radio links are actually disabled, and traffic reroutes over feasible links. Safe mode cuts the heater; B cools; B heals and turns green.
3. **Scenario B (masking):** the attacker freezes B's reported temperature while heating; the monitor flags the inconsistency against the independent sun/temperature channels; telemetry restored; the predictor then handles the real trend.
4. **Scenario C (propagation):** A tries to push malicious commands to its neighbours over ESP-NOW; CASSANDRA contains A before the neighbours reach a critical state.
5. **Evidence slide:** lead time at 0.1% false-positive rate vs baselines, calibration, latency on the Raspberry Pi, measured on twin episodes, SCAD and ESA-ADB.

Fallback: the recorded run of the same script; the software-only mode (twin replacing all 12 satellites) must also work with one command.

## 12. Tasks for Claude Code (do in order, ask for approval between them)

**Task 1: README and skeleton.**
- Write `README.md`: title and tagline; the problem (Viasat KA-SAT); the idea and the six-stage loop; architecture (Mermaid diagram of Section 5); scenarios table; repository layout; status table (component | owner | done / in progress / planned); getting started (real instructions only for `prototype/phase2-demo/`, "coming soon" elsewhere); data sources with links and licences; team; roadmap (Section 9); contributing conventions; honesty note (emulated vs real); references; licence "TBD".
- Create the folders of Section 7, each with a short `README.md` (purpose, owner, interfaces it uses).
- `.gitignore` for Python, Node, PlatformIO/Arduino builds, venvs, `.env`, `data/*` (keep `data/README.md`), large binaries.
- Copy this file to `docs/CONTEXT.md`.
- **Acceptance:** `git status` shows only intended files; no commit until approved.

**Task 2: Interfaces.** Write `docs/interfaces.md` from Section 6, plus a Python module `aurora_common/schemas.py` (pydantic models for every message) and `aurora_common/topics.py`. Add unit tests that validate example messages. **Acceptance:** pytest passes.

**Task 3: Ground station bring-up.** `ground/docker-compose.yml` with Mosquitto (+ config), a `make up` / `make down` / `make reset`, and a tiny MQTT echo/monitor tool (`ground/tools/mqtt_tail.py`). **Acceptance:** runs on Linux/WSL; a test publisher's messages are visible.

**Task 4: Twin v0.** `twin/` with thermal + EPS models for one satellite, publishing valid telemetry at 1 Hz (and fast-forward mode), accepting HEATER/SAFE_MODE commands. **Acceptance:** heater command visibly raises temperature and drains SOC; tests on model equations.

**Task 5: Episode generator.** Scenario/fault/clean episode specs → Parquet + labels.json (Section 6.3), deterministic by seed; attack A1 and fault F1 first. **Acceptance:** 100 episodes generated in under a few minutes; labels validated.

**Task 6: Core skeleton.** `cassandra/ingest`, `graph`, `forecast` (with the Phase 2 trajectory forecaster as the default model), `safety`, `executor`, `explain`, wired to MQTT. **Acceptance:** with twin v0 and attack A1, the core flags, quarantines and heals the satellite end-to-end.

**Task 7: Evaluation harness v0 + SCAD loader.** Metrics of Section 5.8, threshold and trajectory baselines, a SCAD download script and loader. **Acceptance:** a results table for baselines on twin episodes and on SCAD.

**Task 8: Firmware v1 (with the hardware owner).** PlatformIO ESP32 project: sensors → MQTT telemetry, telecommands, emulated battery, LEDs, safe mode, 55 °C hard cut-off; ESP-NOW links with ISL_ENABLE. **Acceptance:** a node appears on the bus as `src: "hw"` and obeys commands.

**Later tasks** (assign as owners are ready): twin v1 (12 satellites, orbits, ADCS, ISL); sandbox + J(a); forecaster (GNN + GRU), calibration, ensemble, GDN/LSTM-AE baselines; Scenarios B and C; Open MCT dashboard; Raspberry Pi deployment and latency; final evaluation runs and paper figures.

## 13. References

- SPARTA: Space Attack Research and Tactic Analysis, The Aerospace Corporation, sparta.aerospace.org
- O. Ben Yahia et al., "Securing the satellite link segment: a secure-by-component design," arXiv:2411.12632, 2024
- Viasat, "KA-SAT network cyber attack overview," 2022; J. A. Guerrero-Saade and M. van Amerongen, "AcidRain: a modem wiper rains down on Europe," SentinelLABS, 2022
- E. Rossi et al., "Temporal graph networks for deep learning on dynamic graphs," arXiv:2006.10637, 2020
- A. Deng and B. Hooi, "Graph neural network-based anomaly detection in multivariate time series" (GDN), AAAI 2021
- C. Guo et al., "On calibration of modern neural networks," ICML 2017; B. Lakshminarayanan et al., "Deep ensembles," NeurIPS 2017
- SCAD dataset, github.com/jwd164/space-cyber-anomaly-detection-dataset (CC BY 4.0)
- ESA Anomaly Dataset / ESA-ADB, github.com/esa/anomaly-dataset, github.com/kplabs-pl/ESA-ADB
- CuCD-ID, Y. Fayyaz et al., *Data in Brief* 2026, data.mendeley.com/datasets/7n2d42pm3n/3
- AegisSat satellite cybersecurity testbed (NDSS SpaceSec 2025), github.com/texydo/satellite_security_testbed
- MimicSat cyber-physical testbed, arXiv:2609.28228
- NASA Open MCT, github.com/nasa/openmct
