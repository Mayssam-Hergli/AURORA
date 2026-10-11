# AURORA status. Updated 2026-10-11 by Mayssam Hergli (with Claude Code)

Plan: [docs/MASTER_PLAN.md](MASTER_PLAN.md). Context: [docs/CONTEXT.md](CONTEXT.md).
Ownership (since 11 Oct): **Mayssam** owns the contract, bus, core, attacks, board and demo; **Youssef** owns `ml/`, `eval/`, `twin/`, `dashboard/` and `docs/paper/`. Neither edits the other's folders.

| Component | Path | Owner | State | Evidence |
|---|---|---|---|---|
| Repo health (Python 3.11 venv, pinned deps, LF endings) | `Makefile`, `mk/`, `requirements.lock`, `.gitattributes` | Mayssam | done (G0) | `make setup && make test` |
| Interface contract | `docs/interfaces.md`, `aurora_common/` | Mayssam | done (G1), frozen v1.2 (SPARTA mapping per P4; SAFE semantics) | `pytest tests/test_schemas.py tests/test_topics.py` |
| MQTT broker | `ground/`, `mk/core.mk` | Mayssam | done (G1): Mosquitto 1883 + 9001, reachable from Windows | `make up && make bus-check` |
| CASSANDRA core | `cassandra/` | Mayssam | done for G4 (software, A1): ingest, forecast, monitor, sandbox (stand-in engine hooks), act, explain, main loop; trajectory forecaster everywhere until trained models exist | `pytest tests/cassandra tests/integration`; `make demo-sw` + `make attack A1 TARGET=E` |
| Replay publisher (plant until the live twin exists) | `scripts/replay_twin.py` | Mayssam | done: clean scenario, bus commands applied, SAFE semantics | `pytest tests/test_replay_twin.py` |
| Live attack scripts | `attacks/` | Mayssam | partial: A1 (one latched HEATER on, `--reassert` optional, ground truth on gt/ only) and `--stop`; B1, C1, faults, N in Phase 5 | `make attack A1 TARGET=E` |
| ESP8266 board as satellite B | `hardware/`, `prototype/phase2-demo/` | Mayssam | Phase 2 code in `phase2_kit.zip`, not yet imported | — |
| Demo script, preflight, backup recording | `docs/DEMO_SCRIPT.md`, `scripts/preflight.sh` | Mayssam | missing | — |
| Learned forecaster, baselines, calibration | `ml/` | Youssef | partial: works, known bugs M1–M5, S1–S10 (Phase 2) | PR #1 review; `make test` |
| Evaluation harness | `eval/` | Youssef | partial: oracle in closed loop (M1, M2), T_alert definition (M3) | PR #1 review |
| Synthetic episode generator (twin stand-in) | `ml/synthetic/` | Youssef | done, offline only | `tests/test_synthetic_and_schema.py` |
| Engine hooks for the core's sandbox: `snapshot(include_queue=False)`, `seed_from_observation(obs)` | `ml/synthetic/engine.py` | Youssef | missing; stubbed behind an adapter in `cassandra/sandbox.py` | — |
| Live twin on MQTT, recorder | `twin/`, `mk/twin.mk` | Youssef | missing (Phase 3) | — |
| Dashboard | `dashboard/` | Youssef | missing (Phase 7) | — |
| Paper source and numbers | `docs/paper/` | Youssef | missing in repo | — |
| Trained models, results | `data/models/`, `results/` | Youssef | missing locally (only on Youssef's machine); retrain in Phase 2 | — |

## Gates
G0 ✅ 2026-10-10 (`make test`: 18 passed on Python 3.11.17, pinned set in `requirements.lock`; `make lint` clean) · G1 ✅ 2026-10-11 (`make test`: 60 passed incl. broker round trip; `make bus-check`: PASS; `make lint` clean) · G2 ⏳ (Youssef) · G3 ⏳ (Youssef) · G4 🔎 ready for review 2026-10-11 (`tests/integration/test_a1_software.py` green through Mosquitto; live `make demo-sw` + `make attack A1 TARGET=E`: flagged, contained, healed; 117 passed; lint clean) · G5 ⏳ (both) · G6 ⏳ (Mayssam) · G7 ⏳ (both)

## Numbers (each with source file + command)
| Metric | Value | Source |
|---|---|---|
| Ported trajectory forecaster on the Phase 2 heater latch (board profile, T_max 60 C, +1.5 C/s, 1 Hz ticks) | P(F<30 s) >= 0.85 at t = 19 s, 53.5 C (paper Table II: 18.8 s, 53.3 C); flag (P >= 0.30) at t = 7 s, 35.5 C (paper: 2.3 s, 28.5 C; slower because the slope window is 8 ticks at 1 Hz instead of 8 samples at 2 Hz) | `tests/cassandra/test_forecast.py::test_phase2_heater_latch_on_the_board_profile` (simulated input) |
| Monitor false alarms, clean replay, 12 twin satellites x 600 s | 0 | `tests/cassandra/test_monitor.py::test_clean_replay_run_raises_no_alarm` (simulated) |
| A1 closed loop, replay plant, core in the loop, no broker (seed 3): heater latched on E at t = 60 s | flagged t = 166 s; isolate_subsystem at t = 187 s, 42.0 C (forecast ETA to T_max 45 C: 28 s); max true temp 41.7 C; healed t = 368 s at 29.9 C; no other satellite changed state; planning 23 ms | `tests/cassandra/test_act.py` (simulated; sandbox = simulator, so an upper bound) |
| A1 live through Mosquitto, `make demo-sw SPEED=20 SEED=3` + `make attack A1 TARGET=E` (2026-10-11) | flagged at P(F<60s)=0.31; isolate_subsystem at 41.2 C (ETA to 45 C: 29 s), cause "heater latched ON (origin=ground, 116 s ago)", SPARTA IA-0009; healed at 29.4 C; tick latency median 1.8 ms, p95 3.1 ms; planning 41 ms (laptop, WSL) | core console output; `tests/integration/test_a1_software.py` (simulated plant) |
| — | none yet: every number in the technical report comes from PR #1's results, which are not in this repo; re-run in Phase 2 | — |

## Known limitations (said out loud in the demo and paper)
- Closed-loop results are an upper bound (sandbox uses the simulator as its model); PR #1's "61 % averted" is also inflated by an oracle in simulator safe mode (fix M1).
- Benchmark saturates: every ablation and the no-telecommand stress test reach recall 1.00, so the benefit of the graph structure and cyber features is not yet shown.
- Board temperature is emulated by firmware (no sensor); the ISL cut on the board is logical, not a radio cut.
- Training and evaluation data are synthetic (`ml/synthetic/`), not twin v1 or hardware.

## Notes for Youssef
- **M1 fix in `ml/synthetic/engine.py` must follow the SAFE rule of `docs/interfaces.md` Section 3.2 (v1.2):** in SAFE the satellite refuses `HEATER on` from any origin (load shedding) and `SET_MODE` other than `NOMINAL`/`SAFE`; only `SET_MODE NOMINAL` exits SAFE; safe mode never inspects `_malicious`. `scripts/replay_twin.py` and the board bridge apply the same rule, so all plants behave alike.
- The core's sandbox needs `SyntheticTwin.snapshot(include_queue=False)` and `SyntheticTwin.seed_from_observation(obs: dict)`; until they exist they are stubbed behind an adapter in `cassandra/sandbox.py`.
- S10: offline synthetic episodes should say `src: "synthetic"` (the contract already allows it).

## For the paper and slides (Youssef: paper; Mayssam: slides)
- SPARTA ids changed from the submitted paper (P4 adopted 2026-10-11): masking is now DE-0002 / DE-0003 (EX-0001 for the replay variant), propagation IA-0003; Scenario A stays IA-0009. Wherever the new ids appear, add the footnote **"Technique mapping refined in Phase 3."** Table: `docs/interfaces.md` Section 7.

## Decisions log
- 2026-10-10: T_max per satellite: 60 °C for the ESP8266 board (emulated temperature, matches the submitted paper), 45 °C default for twin and future ESP32 hardware.
- 2026-10-10: core ticks on message timestamps with a wall-clock watchdog; warn when the hw-vs-twin clock offset exceeds 0.5 s.
- 2026-10-10: Mosquitto on 1883/9001; port 6379 stays with the Phase 2 Redis fallback (`sat_b_redis`).
- 2026-10-11: SPARTA proposal P4 adopted (interfaces.md v1.1, Section 7).
- 2026-10-11: SAFE semantics (interfaces.md v1.2): HEATER on refused in SAFE from any origin; only SET_MODE NOMINAL exits SAFE. A1 attack = one latched HEATER on (`--reassert` optional).
- 2026-10-11: scope split (see top). Makefile split into `mk/*.mk`; until `twin/service.py` exists, the core's plant is `scripts/replay_twin.py`.

## Changelog
- 2026-10-10: Phase 0 done (branch `p0-health`, commit a9e107a).
- 2026-10-11: Phase 1 done (branch `p1-contract`): interfaces.md v1, aurora_common, Mosquitto, Makefile split into mk/*.mk, paho-mqtt 2.1.0 added.
