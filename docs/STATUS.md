# AURORA status. Updated 2026-10-12 (overnight run by Claude Code for Mayssam Hergli)

> **2026-10-11 check (models):** no trained models or results on this machine (no `data/models/`, no `results/`, none in Downloads/Desktop/Documents); Youssef has pushed no branches; `main` unchanged at 2cd9cfa. The core runs the trajectory forecaster for all satellites until models + `calibration.json` exist. Phase 2 taken over by Mayssam on 11 Oct.

## Decisions taken overnight
- Step 3 (G4) had not been committed by the morning block; committed locally as `503d9e7` before starting (tests had passed, tree unchanged).
- G4 reads "flagged, **contained (action chosen by J)**, healed" (Mayssam, 11 Oct): J keeps choosing `isolate_subsystem` for a single latched A1; quarantine is not forced.
- Escalation (task 2): R in J(a) is now the **expected number of satellites reaching F** (sum of risks), not the max, so actions that spare more satellites score lower. A hazardous command (HEATER on, SET_MODE SCIENCE/WHEEL_TEST, not from the core) seen ≥ 2 times from one origin is a continuing threat in the sandbox (relays from isl:X only over feasible, enabled links; refused in SAFE). Ladder isolate_subsystem/block_source → safe_mode → quarantine; trigger: heater ON again > 2 s after the action, or P(F<60s) ≥ 0.50 for 20 s; 2 consecutive failing ticks. Heal waits until the threat is quiet (2 × period + 5 s). Values in `cassandra/config.yaml` `act`.
- Observed: for A1 with re-assertion, safe_mode holds (SAFE refuses HEATER on), so A1 never needs quarantine; quarantine is reached when safe_mode itself fails (relays from a compromised neighbour, a stuck heater).
- Phase 8: `.gitignore` gets one exception, `!prototype/phase2-demo/firmware/sat_thermal.bin` (273 KB < 1 MB rule), so the "flash this if the IDE fails" image is versioned. `attack_thermal.py` was missing from the kit: reconstructed from the demonstrator note and labelled as such. Predictor's printed "+/-0.05" footnoted as not computed (comment + README).
- Phase 8: the bridge is self-contained (pyserial + paho-mqtt only, no pydantic) so it runs from a bare Windows Python; tests validate its output with the contract schemas. Windows venv `.venv-win` created (Python 3.12.10 — CONTEXT says 3.11, but the bridge needs nothing 3.11-specific and 3.12 is what Windows has; gitignored).
- Phase 8: core flag `--twin-sats A,C-L` gives every satellite not listed `satellites.board_profile` (hw, 60 C, 4-tick window, subs thermal + obc); `make demo-sw SATS=A,C-L` passes it to the plant and the core.
- Found tonight: **WSL2 shuts its VM down when no WSL process runs, and the broker stops with it**; Windows then gets "connection refused". Documented in `hardware/BRINGUP.md` (keep WSL terminal 1 running).
- Phase 2 (ML fixes) is on branch `p2-ml-fixes`, created from `p8-board` (Youssef pushed nothing, and the fixes switch the core's sandbox adapter, which lives on our branches). Branch order for pushing: p4-core → p8-board → p2-ml-fixes.
- M1: engine safe mode no longer deletes queued commands flagged `_malicious`; `_execute` applies the interfaces.md 3.2 SAFE rule to every command. The engine still uses the hidden flag only to choose an attack's *physical effect* in offline generation (e.g. A3 drain); it never decides acceptance.
- M2: `SyntheticTwin.snapshot(include_queue=False)` drops queued commands **and** the planned C1 propagation; `seed_from_observation(obs)` moved from the core's stand-in into the engine; the core adapter now uses the engine's hooks (stand-ins deleted). `eval/closed_loop.py` plans on the stripped snapshot, quarantines with SAFE_MODE + ISL cuts (D3: QUARANTINED is a state), no longer pre-deletes blocked commands, and uses R = sum of risks like the core (Q term dropped when q is null).
- M3: T_alert = first alert anywhere in the episode; alerts before the onset of attack episodes are reported as false alarms (`pre_onset` in metrics.json).
- M5: `make train` writes `data/models/calibration.json` (temperature); `make evaluate` refits it and adds tau; `OnlineForecaster.from_models` raises without it unless `allow_uncalibrated=True` (model name "tgn-v1-uncalibrated").
- S1: every method is scored on the same truth-independent schedule (every `stride_s` = 2 s, forward-filled).
- Honesty fix found while doing S4: the old trajectory fallback in `ml/online.py` published `p_std` = 0, `next_sub` = "thermal", `p_malicious` = 0 (made-up values); it now publishes null, and `forecast_message` accepts null.
- Phase 5 (`p5-scenarios`, on top of `p2-ml-fixes`): replay plant gets red-team effects (freeze_tm, bias_tm, relay_tc, stop) and fault injection (`--fault`); `attacks/run.py` B1, C1, F1, N; `scripts/send_tc.py`.
- Phase 5, core: a satellite relaying hazardous commands over ISL is flagged as a suspected source and planned with J (candidates none / safe_mode / quarantine); if J quarantines it, it is held until ground sends SET_MODE NOMINAL (no autonomous heal of a compromised source). Escalation never reaches quarantine without an observed cyber cause (F5 stuck heater ends in safe mode + "ground action required"). Cause attribution uses hazardous commands only (it had blamed ground's own SET_MODE NOMINAL). Repeated `none` decisions published at most every 30 s.
- Phase 5, core: `SET_MODE SCIENCE` is no longer treated as hazardous (`act.hazard_modes: [WHEEL_TEST]`): legitimate ground science sessions repeat every orbit and were being modelled as "continuing threats". A3 (drain by SCIENCE toggling) is therefore not modelled as a threat; it is not one of the live scenarios.
- Phase 5, forecast: trajectory trend is Theil–Sen and the current level is its robust line at the last tick, so a single-tick SEU spike (F2) changes nothing (tested: F2 triggers no action).
- Phase 5, sandbox: rollout 60 s (CONTEXT 5.4 "simulate horizon (default 60 s)"; the 30 s came from eval.yaml), risk horizon 120 s; the shadow matches the observed ISL feasibility to an orbit phase of its own constellation model and propagates geometry from there (ephemeris knowledge, not ground truth); links the core cut stay cut in the shadow.
- Bug found and fixed in PR #1 code: `eval/datasets/opssat.py` built the OPS-SAT noise bank in every generator worker at once; one read a half-written file (`BadZipFile`) and `test_real` generation failed. Now built once, written atomically.
- Known limitation: near thermal equilibrium a weak or stuck heater warms slower than `heater_min_rise_c_s`, so the masking monitor flags it (seen with F5). Effect is conservative (temperature is then estimated, not trusted).
- Tests: the in-process loop pins the trajectory forecaster (results must not depend on whether trained models are on disk); integration tests (subprocess core) run the real configuration, i.e. the ensemble now that models exist.
- Retraining: required, because **no trained models exist on this machine**. Neither M4 nor S10 forces it on its own: the generator writes spaced JSON, so the old string match was right for synthetic training data (M4 only matters for recorder/hardware episodes), and `src` is not a model input.

## Open questions
- **C1 does not end in quarantine of the source, and I did not force it.** The core flags the relaying satellite (A) with an explanation ("relaying HEATER on to H over ISL (suspected compromised neighbour, IA-0003)") and contains every victim before its limit (`tests/cassandra/test_scenarios.py`), but J never picks quarantine for A. Debugged in detail: by the time A is flagged, every neighbour its relays reach is already latched, so cutting A changes the expected number of failures within the horizon by ~0, and quarantine costs 0.16 (cost + lost service). What J does not count is the *future* availability cost: each newly reached satellite later needs its own containment. Options: (a) add the expected containment cost of satellites a threat will newly reach to J; (b) count residual risk over a longer horizon (e.g. 300 s); (c) keep it, and let ground quarantine the source after the core flags it. Not changed overnight: it is a change of the objective, not a fix.
- **Contract v1.3 (message format, not changed):** fault injections (F2 SEU spike, F3 battery ageing, F5 stuck heater) have no `redteam/{id}` effect, so they are injected in the replay plant with `--fault TYPE:SAT@T`. Adding `spike_tm`, `age_battery`, `stuck_heater` effects would let `attacks/run.py` drive them over the bus.
- **Board demo visibility.** With the 4-tick window and decisions on P(F<60s), the core contains the board's A1 about **2 s** after the heater latches, at about **28.6 °C** (dry run). Phase 2 showed a climb to 53 °C before quarantine. Safer, but the audience barely sees B heat up. Options: (a) keep it, and show the climb with `--reassert` or the heating-curve video; (b) give the board profile its own `warn_p`/`plan_p` (e.g. plan at P ≥ 0.85) for a longer visible climb. Not changed overnight: it is a demo-narrative choice.

Plan: [docs/MASTER_PLAN.md](MASTER_PLAN.md). Context: [docs/CONTEXT.md](CONTEXT.md).
Ownership: since the night of 11 Oct, **Mayssam owns every lane** again (Youssef has been told); the 11 Oct split below is kept for history.

| Component | Path | Owner | State | Evidence |
|---|---|---|---|---|
| Repo health (Python 3.11 venv, pinned deps, LF endings) | `Makefile`, `mk/`, `requirements.lock`, `.gitattributes` | Mayssam | done (G0) | `make setup && make test` |
| Interface contract | `docs/interfaces.md`, `aurora_common/` | Mayssam | done (G1), frozen v1.2 (SPARTA mapping per P4; SAFE semantics) | `pytest tests/test_schemas.py tests/test_topics.py` |
| MQTT broker | `ground/`, `mk/core.mk` | Mayssam | done (G1): Mosquitto 1883 + 9001, reachable from Windows | `make up && make bus-check` |
| CASSANDRA core | `cassandra/` | Mayssam | done for G4 (software, A1): ingest, forecast, monitor, sandbox (stand-in engine hooks), act, explain, main loop; trajectory forecaster everywhere until trained models exist | `pytest tests/cassandra tests/integration`; `make demo-sw` + `make attack A1 TARGET=E` |
| Replay publisher (plant until the live twin exists) | `scripts/replay_twin.py` | Mayssam | done: clean scenario, bus commands applied, SAFE semantics | `pytest tests/test_replay_twin.py` |
| Live attack scripts | `attacks/` | Mayssam | partial: A1 (one latched HEATER on, `--reassert` optional, ground truth on gt/ only) and `--stop`; B1, C1, faults, N in Phase 5 | `make attack A1 TARGET=E` |
| ESP8266 board as satellite B | `hardware/`, `prototype/phase2-demo/` | Mayssam | software done (branch `p8-board`): bridge with bridge-enforced SAFE, LEDs 1/2/3, red-team freeze, `--log`, `--dry-run`; board profile 60 C, 4-tick window; Phase 2 imported; `.venv-win` ready; **real-board bring-up pending (morning)** | `tests/hardware/`, `tests/integration/test_board_dry_run.py`; `hardware/BRINGUP.md` |
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
| A1 with re-assertion every 150 s (in process, seed 3) | isolate_subsystem at t = 187 s; heater ON again 24 s later → escalation to safe_mode at t = 211 s (alternative: quarantine); heal held while commands arrive; healed at t = 511 s; max true temp 41.6 C; no other satellite touched | `tests/cassandra/test_escalation.py` (simulated) |
| A1 with re-assertion every 5 s (in process, seed 3) | first plan already rates isolate_subsystem at J = 0.95 (heater re-latched in the shadow) → safe_mode at t = 187 s; healed after stop at 28.6 C; max 41.6 C | `tests/cassandra/test_escalation.py` (simulated) |
| A1 `--reassert` through Mosquitto (20x) then `--stop` | contained beyond isolation (safe_mode), "holding containment" while commands arrive, healed after stop, max < 45 C | `tests/integration/test_a1_software.py::test_a1_reassert_escalates_beyond_isolation_and_heals_after_stop` |
| Board dry run, A1 on B (`make demo-board-dry` + `make attack A1 TARGET=B`, fake board = firmware model, real time, 2026-10-12) | flagged and `isolate_subsystem` ~2 s after the latch at 28.6 C (P(F<60s)=0.58, ETA to 60 C ~25 s, cause "heater latched ON (origin=ground, 2 s ago)", IA-0009); LED B red; healed at 26.2 C; B telemetry src `hw` | core/bridge console; `tests/integration/test_board_dry_run.py` (simulated board) |
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

## For the paper, slides, demo script and voiceover
- **A1 wording: say "contains", not "quarantines".** For a single latched heater command, J chooses `isolate_subsystem` (cut the heater, satellite stays in service). The demo script and voiceover must say "CASSANDRA contains B/E" and name the action J chose on screen; quarantine appears only when J escalates (repeated commands) or for C1.
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
