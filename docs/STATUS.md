# AURORA status. Updated 2026-10-12 by Youssef Mhamdi (with Claude Code); Youssef's rows only

Plan: [docs/MASTER_PLAN.md](MASTER_PLAN.md). Context: [docs/CONTEXT.md](CONTEXT.md).
Ownership (since 11 Oct): **Mayssam** owns the contract, bus, core, attacks, board and demo; **Youssef** owns `ml/`, `eval/`, `twin/`, `dashboard/` and `docs/paper/`. Neither edits the other's folders.

| Component | Path | Owner | State | Evidence |
|---|---|---|---|---|
| Repo health (Python 3.11 venv, pinned deps, LF endings) | `Makefile`, `mk/`, `requirements.lock`, `.gitattributes` | Mayssam | done (G0) | `make setup && make test` |
| Interface contract | `docs/interfaces.md`, `aurora_common/` | Mayssam | done (G1), frozen v1.2 (SPARTA mapping per P4; SAFE semantics) | `pytest tests/test_schemas.py tests/test_topics.py` |
| MQTT broker | `ground/`, `mk/core.mk` | Mayssam | done (G1): Mosquitto 1883 + 9001, reachable from Windows | `make up && make bus-check` |
| CASSANDRA core | `cassandra/` | Mayssam | in progress (Phase 4): ingest, forecast (trajectory + ensemble adapter), monitor done; sandbox, act, explain, main loop next | `pytest tests/cassandra` |
| Replay publisher (plant until the live twin exists) | `scripts/replay_twin.py` | Mayssam | done: clean scenario, bus commands applied, SAFE semantics | `pytest tests/test_replay_twin.py` |
| Live attack scripts | `attacks/` | Mayssam | missing | — |
| ESP8266 board as satellite B | `hardware/`, `prototype/phase2-demo/` | Mayssam | Phase 2 code in `phase2_kit.zip`, not yet imported | — |
| Demo script, preflight, backup recording | `docs/DEMO_SCRIPT.md`, `scripts/preflight.sh` | Mayssam | missing | — |
| Learned forecaster, baselines, calibration | `ml/` | Youssef | fixes done (branch `p2-ml-fixes`): M1–M5, S1–S4, S6, S7, S9, S10, each with a regression test; forecasts follow interfaces.md 3.5 (nulls per D4, `q`, `warming_up`). **Pipeline re-run pending** (C: drive full): models and every number are still v1 | `pytest tests/test_ml_fixes.py tests/test_online_live.py` |
| Evaluation harness | `eval/` | Youssef | fixed (M2, M3, S1, S9; writes `data/models/calibration.json`, M5); not re-run yet | `pytest tests/test_ml_fixes.py` |
| Synthetic episode generator | `ml/synthetic/` | Youssef | done; SAFE rule of interfaces.md 3.2 in the engine (M1), offline episodes `src: "synthetic"` (S10), `test_real` skipped without OPS-SAT data (S7) | `tests/test_synthetic_and_schema.py`, `tests/test_ml_fixes.py` |
| Engine hooks for the core's sandbox: `snapshot(include_queue=False)`, `seed_from_observation(obs)` | `ml/synthetic/engine.py` | Youssef | done (branch `p2-ml-fixes`); `obs = {"tick", "values": WorldTick.values, "links": WorldTick.links}` | `tests/test_ml_fixes.py::test_seed_from_observation_shadows_the_world` |
| Live twin on MQTT, recorder, `send_tc`, `make demo-sw` launcher | `twin/`, `mk/twin.mk` | Youssef | done (G3, branch `p3-twin`); `demo-sw` starts the core once `cassandra/__main__.py` exists | `pytest tests/test_twin.py`; G3 run below |
| Dashboard | `dashboard/` | Youssef | missing (Phase 7) | — |
| Paper source and numbers | `docs/paper/` | Youssef | missing in repo (the `.tex` is in Youssef's old ML repo; Phase 9) | — |
| Trained models, results | `data/models/`, `results/` | Youssef | `models-v1` bundle built (v1 ensemble + `calibration.json` rebuilt from the v1 harness output; SHA-256 pinned in `eval/datasets/download.py`); **[HUMAN] upload to the GitHub release `models-v1`**, then `make models`. Retrain after the re-run | `python -m ml.export --bundle v1` |

## Gates
G0 ✅ 2026-10-10 (`make test`: 18 passed on Python 3.11.17, pinned set in `requirements.lock`; `make lint` clean) · G1 ✅ 2026-10-11 (`make test`: 60 passed incl. broker round trip; `make bus-check`: PASS; `make lint` clean) · G2 🟡 2026-10-12 code fixes done (`make test`: 116 passed on Python 3.14, not the pinned 3.11), pipeline re-run and `results/manifest.json` pending (disk full) · G3 ✅ 2026-10-12 (see Numbers) · G4 ⏳ (Mayssam) · G5 ⏳ (both) · G6 ⏳ (Mayssam) · G7 ⏳ (both)

## Numbers (each with source file + command)
| Metric | Value | Source |
|---|---|---|
| Ported trajectory forecaster on the Phase 2 heater latch (board profile, T_max 60 C, +1.5 C/s, 1 Hz ticks) | P(F<30 s) >= 0.85 at t = 19 s, 53.5 C (paper Table II: 18.8 s, 53.3 C); flag (P >= 0.30) at t = 7 s, 35.5 C (paper: 2.3 s, 28.5 C; slower because the slope window is 8 ticks at 1 Hz instead of 8 samples at 2 Hz) | `tests/cassandra/test_forecast.py::test_phase2_heater_latch_on_the_board_profile` (simulated input) |
| Monitor false alarms, clean replay, 12 twin satellites x 600 s | 0 | `tests/cassandra/test_monitor.py::test_clean_replay_run_raises_no_alarm` (simulated) |
| G3 live twin: telemetry rate per (satellite, subsystem), 12 satellites, 60 s recording | 1.00 Hz (61 messages each in 60 s), seq +1 throughout, 0 invalid messages | `python -m twin.service` + `python -m twin.recorder --duration 60`, read with `ml.schema.read_episode` (simulated, 2026-10-12) |
| G3: `twin.send_tc E HEATER on`, temperature change over the next 40 s | E +5.66 °C; the other 11 satellites −3.89 to +1.91 °C | same recording, `data/episodes/live/g3-gate` (gitignored; simulated) |
| — | ML numbers: still the v1 results of PR #1 (`results/v1-old/` on Youssef's machine); regenerated by the Phase 2 re-run | — |

## Known limitations (said out loud in the demo and paper)
- Closed-loop results are an upper bound (sandbox uses the simulator as its model); PR #1's "61 % averted" is also inflated by an oracle in simulator safe mode (fix M1).
- Benchmark saturates: every ablation and the no-telecommand stress test reach recall 1.00, so the benefit of the graph structure and cyber features is not yet shown.
- Board temperature is emulated by firmware (no sensor); the ISL cut on the board is logical, not a radio cut.
- Training and evaluation data are synthetic (`ml/synthetic/`), not twin v1 or hardware.

## Notes for Mayssam (from Youssef, 2026-10-12)
- **Ensemble in the core:** `make models` (after the release upload) gives `data/models/{full_s0..4.pt, understanding.*, calibration.json}`; `EnsembleAdapter.load` accepts it (checked: 5 models, `tgn-v1`). `calibration.json` = `{"temperature", "tau", "horizon_s": 60, "fpr_target": 0.001, "models", "git", "source"}`.
- **τ is low:** `tau` = 0.018 (P(F<60 s) at 0.1 % FPR on validation, v1). With the state machine "flagged → quarantined when P ≥ τ", any flagged twin satellite meets it; the 0.85 act threshold may be the safer quarantine gate. Your call.
- **Warm-up:** ignore forecasts with `warming_up: true` (first 30 s). At startup the left-padded window gave one satellite P60 = 0.43 with p_std 0.46 in a test run.
- **Engine SAFE rule:** the engine now refuses in SAFE by itself (`ml.synthetic.engine.refused_in_safe`, same answers as `ReplayTwin.refused_in_safe`, test `test_m1_engine_and_replay_plant_refuse_the_same_commands`); your replay check is now redundant but harmless.
- **Twin topics:** the twin publishes `gt/event` `critical` (true state) for the recorder; red-team effects change reported values only. G3 used `python -m twin.send_tc E HEATER on` (the plan's `scripts/send_tc.py` is in your folder, so the tool lives in `twin/`).
- **The paper `.tex`** is in Youssef's old ML repo (`docs/paper/CASSANDRA_paper_updated.tex`); it moves to `docs/paper/` in Phase 9.

## Notes for Youssef (from Mayssam, 2026-10-11; done 2026-10-12)
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
- 2026-10-12 (Youssef): pipeline re-run postponed because the C: drive is full (60 MB free); `models-v1` ships the v1 ensemble meanwhile, labelled as such in `calibration.json`.
- 2026-10-12 (Youssef): `calibration.json` is written by `eval.harness` (where temperature and τ are fitted), not `make train` as the plan said.

## Changelog
- 2026-10-10: Phase 0 done (branch `p0-health`, commit a9e107a).
- 2026-10-11: Phase 1 done (branch `p1-contract`): interfaces.md v1, aurora_common, Mosquitto, Makefile split into mk/*.mk, paho-mqtt 2.1.0 added.
- 2026-10-12: Phase 2 code fixes (branch `p2-ml-fixes`, on top of `p4-core`): M1–M5, S1–S4, S6, S7, S9, S10, engine hooks, `make models`; re-run pending.
- 2026-10-12: Phase 3 done (branch `p3-twin`): `twin/service.py`, `twin/recorder.py`, `twin/send_tc.py`, `twin/launch.py`, `mk/twin.mk`.
