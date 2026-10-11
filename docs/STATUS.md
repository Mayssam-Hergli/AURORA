# AURORA status. Updated 2026-10-11 by Mayssam Hergli (with Claude Code)

Plan: [docs/MASTER_PLAN.md](MASTER_PLAN.md). Context: [docs/CONTEXT.md](CONTEXT.md).
Ownership (since 11 Oct): **Mayssam** owns the contract, bus, core, attacks, board and demo; **Youssef** owns `ml/`, `eval/`, `twin/`, `dashboard/` and `docs/paper/`. Neither edits the other's folders.

| Component | Path | Owner | State | Evidence |
|---|---|---|---|---|
| Repo health (Python 3.11 venv, pinned deps, LF endings) | `Makefile`, `mk/`, `requirements.lock`, `.gitattributes` | Mayssam | done (G0) | `make setup && make test` |
| Interface contract | `docs/interfaces.md`, `aurora_common/` | Mayssam | done (G1), frozen v1; SPARTA table pending P4 decision | `pytest tests/test_schemas.py tests/test_topics.py` |
| MQTT broker | `ground/`, `mk/core.mk` | Mayssam | done (G1): Mosquitto 1883 + 9001, reachable from Windows | `make up && make bus-check` |
| CASSANDRA core | `cassandra/` | Mayssam | missing (reference loop in `eval/closed_loop.py`) | — |
| Replay publisher (plant until the live twin exists) | `scripts/replay_twin.py` | Mayssam | missing | — |
| Live attack scripts | `attacks/` | Mayssam | missing | — |
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
G0 ✅ 2026-10-10 (`make test`: 18 passed on Python 3.11.17, pinned set in `requirements.lock`; `make lint` clean) · G1 ✅ 2026-10-11 (`make test`: 60 passed incl. broker round trip; `make bus-check`: PASS; `make lint` clean) · G2 ⏳ (Youssef) · G3 ⏳ (Youssef) · G4 ⏳ (Mayssam) · G5 ⏳ (both) · G6 ⏳ (Mayssam) · G7 ⏳ (both)

## Numbers (each with source file + command)
| Metric | Value | Source |
|---|---|---|
| — | none yet: every number in the technical report comes from PR #1's results, which are not in this repo; re-run in Phase 2 | — |

## Known limitations (said out loud in the demo and paper)
- Closed-loop results are an upper bound (sandbox uses the simulator as its model); PR #1's "61 % averted" is also inflated by an oracle in simulator safe mode (fix M1).
- Benchmark saturates: every ablation and the no-telecommand stress test reach recall 1.00, so the benefit of the graph structure and cyber features is not yet shown.
- Board temperature is emulated by firmware (no sensor); the ISL cut on the board is logical, not a radio cut.
- Training and evaluation data are synthetic (`ml/synthetic/`), not twin v1 or hardware.

## Decisions log
- 2026-10-10: T_max per satellite: 60 °C for the ESP8266 board (emulated temperature, matches the submitted paper), 45 °C default for twin and future ESP32 hardware.
- 2026-10-10: core ticks on message timestamps with a wall-clock watchdog; warn when the hw-vs-twin clock offset exceeds 0.5 s.
- 2026-10-10: Mosquitto on 1883/9001; port 6379 stays with the Phase 2 Redis fallback (`sat_b_redis`).
- 2026-10-11: scope split (see top). Makefile split into `mk/*.mk`; until `twin/service.py` exists, the core's plant is `scripts/replay_twin.py`.

## Changelog
- 2026-10-10: Phase 0 done (branch `p0-health`, commit a9e107a).
- 2026-10-11: Phase 1 done (branch `p1-contract`): interfaces.md v1, aurora_common, Mosquitto, Makefile split into mk/*.mk, paho-mqtt 2.1.0 added.
