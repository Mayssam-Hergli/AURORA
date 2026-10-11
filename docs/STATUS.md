# AURORA status. Updated 2026-10-10 by Mayssam Hergli (with Claude Code)

Plan: [docs/MASTER_PLAN.md](MASTER_PLAN.md). Context: [docs/CONTEXT.md](CONTEXT.md).

| Component | Path | Owner | State | Evidence |
|---|---|---|---|---|
| Repo health (Python 3.11 venv, pinned deps, LF endings) | `Makefile`, `requirements.lock`, `.gitattributes` | Mayssam | done (G0) | `make setup && make test` |
| Interface contract | `docs/interfaces.md`, `aurora_common/` | Mayssam | missing | — |
| MQTT broker | `ground/` | Mayssam | missing | — |
| Learned forecaster, baselines, calibration | `ml/` | Mayssam (from Youssef Mhamdi, PR #1) | partial: works, known bugs M1–M5, S1–S10 | PR #1 review; `make test` |
| Evaluation harness | `eval/` | Mayssam (from PR #1) | partial: oracle in closed loop (M1, M2), T_alert definition (M3) | PR #1 review |
| Synthetic episode generator (twin stand-in) | `ml/synthetic/` | Mayssam (from PR #1) | done, offline only | `tests/test_synthetic_and_schema.py` |
| Live twin on MQTT, recorder | `twin/` | Mayssam | missing | — |
| CASSANDRA core | `cassandra/` | Mayssam | missing (reference loop in `eval/closed_loop.py`) | — |
| Live attack scripts | `attacks/` | Mayssam | missing | — |
| Dashboard | `dashboard/` | Mayssam | missing | — |
| ESP8266 board as satellite B | `hardware/`, `prototype/phase2-demo/` | Mayssam | Phase 2 code in `phase2_kit.zip`, not yet imported | — |
| Paper source | `docs/paper/` | Mayssam | missing in repo | — |
| Trained models, results | `data/models/`, `results/` | Mayssam | missing locally (only on Youssef's machine); retrain in Phase 2 | — |

## Gates
G0 ✅ 2026-10-10 (`make test`: 18 passed on Python 3.11.17, pinned set in `requirements.lock`; `make lint` clean) · G1 ⏳ · G2 ⏳ · G3 ⏳ · G4 ⏳ · G5 ⏳ · G6 ⏳ · G7 ⏳

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

## Changelog
- 2026-10-10: Phase 0 started (branch `p0-health`).
