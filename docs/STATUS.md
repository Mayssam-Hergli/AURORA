> Historical integration/planning record. The hardened `rami` behavior, current checks and fresh result provenance are documented in [HARDENING_REPORT.md](HARDENING_REPORT.md). Earlier action names, uncertainty rules and numbers below describe earlier revisions.

# AURORA status. Updated 2026-10-12 midday (Claude Code for Mayssam Hergli)

# Midday update (12 Oct): Youssef's branches integrated

Youssef pushed his own `p2-ml-fixes`, `p3-twin` and `p7-dashboard` (base `c66f4c4`). They cover the same three phases we built overnight, done in parallel, and 24 files are touched by both. **Decision (Mayssam): our chain is the base, his work is merged onto it, and his dashboard replaces ours.** Everything is on the new local branch **`p9-integration`** (on top of `p7-dashboard`). Nothing has been pushed; `main` and his remote branches are untouched.

Checked first: his branch alone passes all its non-broker tests on Python 3.11 (he could only run 3.14). Our core on his `ml/` as-is failed (4 failures, 11 errors), because our shadow geometry needs `seed_from_observation(..., hold_geometry, t_index)`. So this needed a real merge, not a swap.

**Taken from Youssef**
- `make models` (SHA-256-checked download) and `ml.export --bundle`. The pin now points to a **models-v2** release, which is our 12 Oct retrain; v1 (the PR #1 models) stays on its own release. The bundle is built: `dist/aurora-models-v2.zip`, sha256 `87253c74…bff7`. It is reproducible: the same files give the same zip. **[HUMAN] Upload it** (command below), otherwise `make models` returns 404.
- `ml/online.py` and `ml/schema.py` as-is:
  - The no-model fallback ignores channels not yet received. Our fallback gave P = 1 on a thermal-only board, because SOC held at 0 read as below SOC_min.
  - Forecast messages carry `q` and `warming_up`.
  - `from_models()` without models falls back instead of raising.
- His regression tests, `tests/test_ml_review_fixes.py` (M1–M5, S1–S4, S7, S9, S10) and `tests/test_online_live.py`, all pass on the merged code. Our four tests that duplicated his (M3, M5, S1, S7) were removed.
- 3-way merges (base `c66f4c4`) of `features.py`, `calibration.py`, `train.py`, `generate.py`, `metrics.py`, `harness.py` and `closed_loop.py`. In `closed_loop.py` ours was kept: R = sum of risks, as in the core.
- **One `seed_from_observation`.** We had both written one, and a plain merge silently kept two, with his overriding ours. The merged hook:
  - accepts his WorldTick shape and our flat shape;
  - keeps our `hold_geometry`, `t_index` and `disabled`;
  - adopts his fault/block/anomaly reset, ADCS and OBC counters, thermostat rule and `QUARANTINED` mode.
- **One SAFE rule:** `ml.synthetic.engine.refused_in_safe(cmd, args, origin)`, called by the engine, the twin service and the core sandbox. The board bridge keeps its own copy because it runs without the repo's dependencies.
- His **dashboard** (uPlot, 3 views):
  - Its `act: 0.85 "flagged -> quarantined"` line is corrected to a **plan line at 0.50 ("J decides")**.
  - `make dashboard` binds to 127.0.0.1.
  - New targets: `make dashboard-test` and `make tc`. `tests/test_dashboard.py` runs his Node tests from pytest.
- From his twin: **`gt/event critical`**, which ours lacked; it is now in `twin/service.py`.

**Bugs found and fixed while integrating**
1. **M3 (honesty; numbers changed).** Our overnight M3 change counted a pre-onset false alarm as a detection *with lead time*. His definition is the one merged: an alert before onset is a false alarm, and detection and lead start at onset. I re-ran evaluate through figures with the same models and predictions:
   - GDN: 0.80 / 222 s → **0.69 / 167 s**.
   - LSTM-AE: 0.38 / 205 s → **0.35 / 197 s**.
   - Ensemble OOD lead: 139 s → **135 s**. Ensemble ID and Real are unchanged.

   The corrected values equal PR #1's original report for those baselines, so they did not "improve" overnight. The Numbers section is updated.
2. **Inverted flag hysteresis (core).** Ensemble satellites flag at tau ≈ 0.015 but un-flagged only below calm_p = 0.10, so P = 0.03 was both a flag and calm. In the browser, E flapped between flagged and nominal 3 times before the action. A satellite now un-flags only below min(calm_p, half its flag threshold); trajectory behaviour is unchanged. Test added.
3. **Spurious "frozen" masking alarm on every satellite (core).** When the core caught up on several ticks at once (20x plant, core near its 50 ms budget), each tick re-read the same message. The monitor saw 10 identical readings, which is its masking signature, and flagged all 12 satellites. It now counts distinct messages (message time from the tick's age), not ticks. Test added.
4. **The recorder labelled critical times from reported telemetry**, which a B1 freeze hides. It now uses the twin's `gt/event critical` (the true crossing) and falls back to telemetry only where there is no truth (the board). Test with a B1 freeze added.
5. Core flag messages printed tau unrounded (`>= 0.015277743339538574`); they now show 3 significant digits.
6. The board dry-run integration test had the retained-state problem fixed this morning in the A1 test; same fix applied.

**Evidence**
- **Tests:** 181 pass (WSL, Python 3.11.17, broker up, models present, integration tests included); `make lint` is clean.
- **Pipeline:** re-run from evaluate, every step exit 0 (`pipeline_times.tsv`, 12:20–12:50).
- **Phase 6:** re-run on the final code (`results/live_manifest.json`). The outcomes are identical:
  - 12/12 runs within limits;
  - C1 source quarantined in 2 of 3 seeds;
  - the brief false flag in C1 seed 1 is now one flag instead of two flaps.
- **Dashboard, checked in Chrome** (headless, real bus, live core, 1,500 msg/s): A1 on E shows flagged, then `ISOLATE_SUBSYSTEM E` with J 0.13 and alternatives safe_mode 0.16 / quarantine 0.22 / none 0.62, with the cause and IA-0009, then healed.

**Reply to Youssef:** drafted for Mayssam (not sent).


# Morning report (12 Oct)

Nothing was pushed and `main` was not touched. Every branch below is local, and each one sits on top of the previous one (a linear chain off `main` 2cd9cfa).

## What's done, with evidence
| # | Task | Branch / commit | Evidence |
|---|---|---|---|
| 1 | Models check (top of the 11 Oct notes) | `p4-core` 7b9d981 | this file, "2026-10-11 check" below |
| 2 | Real escalation (isolate → safe_mode → quarantine) + A1 `--reassert` integration test | `p4-core` 0c380f1 | `tests/cassandra/test_escalation.py`; `tests/integration/test_a1_software.py::test_a1_reassert_escalates_beyond_isolation_and_heals_after_stop` passes through Mosquitto |
| 3 | Phase 8 software: ESP8266 bridge, board profile, Phase 2 import, bring-up guide | `p8-board` 5504b5c | `tests/hardware/`, `tests/integration/test_board_dry_run.py` passes; `hardware/BRINGUP.md` |
| 4 | ML fixes M1–M5, S1–S4, S6, S7, S10; real engine hooks for the sandbox | `p2-ml-fixes` 1b8847a | `tests/test_ml_fixes.py` |
| 4b | Full pipeline: generate → cache → train → evaluate → closed loop → robustness → stress → export → figures | (outputs in `data/`, `results/`; not in git except the manifests) | `results/logs/pipeline_times.tsv`: second pass, every step exit 0, finished 03:25; `results/manifest.json` |
| 5a | Phase 5: B1, C1, faults, N; replay red-team effects; source handling; robust trend; shadow geometry | `p5-scenarios` fd08b4a | `tests/cassandra/test_scenarios.py` |
| 5b | Phase 3: live twin service (real engine) + recorder | `p3-twin` 43c167e | `tests/test_twin_service.py` |
| 5c | Phase 7: single-page dashboard (Open MCT fallback) | `p7-dashboard` f03130b | `tests/dashboard/`; live data path verified over WebSocket 9001 (~67k messages in 45 s) |
| 6 | Ensemble wired into the core | (no code change needed: it loads `data/models/full_s*.pt` + `calibration.json`) | core prints `forecaster: tgn-v1 (5 models)`; every Phase 6 run and both A1 integration tests ran with it |
| 6b | Phase 6: 12 scenario runs (A1, A1-reassert, B1, C1 × 3 seeds) | `p7-dashboard` (this commit) | `results/live_manifest.json`; table under Numbers |
| 6c | `results/manifest.json` (git hash, versions, seeds, commands, wall time per step, headline metrics) | `p7-dashboard` (this commit) | `python scripts/write_manifest.py` |
| 6d | Numbers with sources + old-vs-new table | this file | Numbers section |
| 7 | `scripts/preflight.sh`, draft `docs/DEMO_SCRIPT.md` | `p7-dashboard` (this commit) | `bash scripts/preflight.sh` |

Final test state (WSL, `~/aurora-venv`, Python 3.11.17, models present): all non-broker suites pass (exit 0); broker suites `tests/integration` + `tests/test_bus_integration.py`: **5 passed**; `make lint` clean.

## What failed or was skipped, and why
- **B1, seed 1 reached the limit (fixed, both results kept).** In the first Phase 6 pass E crossed 45 °C (action at 45.17 °C, max 45.32 °C). The cause: while `temp_c` is untrusted (frozen) the core estimates temperature with `heating_rate_c_s` 0.2 °C/s, but the twin heater warms up to ~0.3 °C/s near room temperature, so the estimate lagged. Fix: `heating_rate_c_s: 0.3` (the fastest plausible rate, so the estimate errs early). The re-run had all 12 runs within limits: B1 actions at 75 / 55 / 75 s after the attack, max 36.5 / 32.1 / 25.1 °C. Old run: `results/live_manifest_heat0.2.json`; new: `results/live_manifest.json`.
- **Pipeline first pass:** `generate` failed on `test_real` (`BadZipFile`, a race in the OPS-SAT noise bank, bug from PR #1); evaluate / robustness / stress / figures failed after it. The race was fixed, `test_real` regenerated with the same seeds, and the pipeline re-run from `evaluate`; every step of the second pass exited 0 (`pipeline_times.tsv` keeps both passes).
- **Integration tests failed once after Phase 6 (test bug, fixed).** The plant at 20x stamps simulated time, which runs ahead of the wall clock, so retained node states from earlier runs passed the test's time filter and ended the wait early. The test now discards states received before the core is up. Not a core bug: in process, the ensemble core raises no flag on a clean replay (checked).
- **Open MCT skipped:** the single-page dashboard (MASTER_PLAN fallback) was built instead. *(Midday: replaced by Youssef's single-page dashboard, checked in Chrome against the live core.)*
- **Closed-loop planning is over budget offline:** the `eval/closed_loop.py` sandbox planning median is 461 ms (p95 488 ms) against the 150 ms budget (midday re-run, under less load: 254 ms / 402 ms). The live core plans in 63–105 ms (Phase 6 medians), so this concerns the offline evaluator's planner.
- **Not done (needs you or a screen):** real-board bring-up, the backup demo recording, browser check of the dashboard, and the paper text (`docs/paper/` does not exist in the repo; every number is in this file and in `results/manifest.json`).
- **Not done (out of time and scope):** Open MCT, contract v1.3 fault effects, the C1 objective change (see Open questions).

## Push commands (Git Bash; nothing is pushed yet; replaced at midday)
**Do not push our local `p2-ml-fixes`, `p3-twin` or `p7-dashboard` under those names.** Youssef's branches with the same names are on GitHub with a different history: the push would be rejected, and it must never be forced. `p9-integration` contains all of our commits plus the merge, so it is the one to push:
```bash
cd /c/Users/USER/AURORA
git push origin p4-core              # fast-forward of origin/p4-core: the core main loop Youssef asked for
git push -u origin p8-board          # new on GitHub
git push -u origin p5-scenarios      # new on GitHub: attacks A1/B1/C1/F1/N
git push -u origin p9-integration    # everything, merged with Youssef's work
# [HUMAN] the models-v2 release that `make models` downloads (GitHub CLI):
gh release create models-v2 dist/aurora-models-v2.zip --title "models-v2 (12 Oct retrain)" --notes "5-seed ensemble + calibration.json (T 0.730, tau 0.0153). sha256 87253c7408976cf9b40100997aa84ee75d9b3783cfef16e75292c560e547bff7. Numbers: results/manifest.json"
```
No PRs, no merge into `main`.

## 5 commands to start hardware bring-up (details: `hardware/BRINGUP.md`)
```bash
# 1. WSL terminal 1 (keep it open: WSL stops the broker when no WSL process runs)
cd /mnt/c/Users/USER/AURORA && make up
# 2. WSL terminal 1: twin for A, C-L and the core (B gets the board profile); fast venv
make demo-sw SATS=A,C-L VENV=$HOME/aurora-venv
# 3. Windows PowerShell: the bridge for the board on COM9 as satellite B
cd C:\Users\USER\AURORA; .\.venv-win\Scripts\python.exe hardware\esp8266_bridge\bridge.py --port COM9 --sat B --log curve.csv
# 4. WSL terminal 2: the attack
cd /mnt/c/Users/USER/AURORA && make attack A1 TARGET=B VENV=$HOME/aurora-venv
# 5. WSL terminal 2: end it
make attack-stop TARGET=B VENV=$HOME/aurora-venv
```

---

> **2026-10-11 check (models):** no trained models or results on this machine (no `data/models/`, no `results/`, none in Downloads/Desktop/Documents); Youssef has pushed no branches; `main` unchanged at 2cd9cfa. The core runs the trajectory forecaster for all satellites until models + `calibration.json` exist. Phase 2 taken over by Mayssam on 11 Oct. **Update 12 Oct:** retrained overnight; `data/models/` holds the 5-seed ensemble + 3 ablations and `calibration.json` (temperature 0.730, tau 0.0153 at 0.1 % FPR); the core uses it.

## Decisions taken overnight
- Step 3 (G4) had not been committed by the morning block; committed locally as `503d9e7` before starting (tests had passed, tree unchanged).
- G4 reads "flagged, **contained (action chosen by J)**, healed" (Mayssam, 11 Oct): J keeps choosing `isolate_subsystem` for a single latched A1; quarantine is not forced.
- Escalation (task 2): R in J(a) is now the **expected number of satellites reaching F** (sum of risks), not the max, so actions that spare more satellites score lower. A hazardous command (HEATER on, SET_MODE WHEEL_TEST, not from the core) seen ≥ 2 times from one origin is a continuing threat in the sandbox (relays from isl:X only over feasible, enabled links; refused in SAFE). Ladder isolate_subsystem/block_source → safe_mode → quarantine; trigger: heater ON again > 2 s after the action, or P(F<60s) ≥ 0.50 for 20 s; 2 consecutive failing ticks. Heal waits until the threat is quiet (2 × period + 5 s). Values in `cassandra/config.yaml` `act`.
- Observed: for A1 with re-assertion, safe_mode holds (SAFE refuses HEATER on), so A1 never needs quarantine; quarantine is reached when safe_mode itself fails (relays from a compromised neighbour, a stuck heater).
- Phase 8: `.gitignore` gets one exception, `!prototype/phase2-demo/firmware/sat_thermal.bin` (273 KB < 1 MB rule), so the "flash this if the IDE fails" image is versioned. `attack_thermal.py` was missing from the kit: reconstructed from the demonstrator note and labelled as such. Predictor's printed "+/-0.05" footnoted as not computed (comment + README).
- Phase 8: the bridge is self-contained (pyserial + paho-mqtt only, no pydantic) so it runs from a bare Windows Python; tests validate its output with the contract schemas. Windows venv `.venv-win` created (Python 3.12.10 — CONTEXT says 3.11, but the bridge needs nothing 3.11-specific and 3.12 is what Windows has; gitignored).
- Phase 8: core flag `--twin-sats A,C-L` gives every satellite not listed `satellites.board_profile` (hw, 60 C, 4-tick window, subs thermal + obc); `make demo-sw SATS=A,C-L` passes it to the plant and the core.
- Found tonight: **WSL2 shuts its VM down when no WSL process runs, and the broker stops with it**; Windows then gets "connection refused". Documented in `hardware/BRINGUP.md` (keep WSL terminal 1 running).
- Phase 2 (ML fixes) is on branch `p2-ml-fixes`, created from `p8-board` (Youssef pushed nothing, and the fixes switch the core's sandbox adapter, which lives on our branches).
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
- Retraining: required, because **no trained models existed on this machine**. Neither M4 nor S10 forces it on its own: the generator writes spaced JSON, so the old string match was right for synthetic training data (M4 only matters for recorder/hardware episodes), and `src` is not a model input. Full scope trained (5 seeds + ablations), well under the 5 h limit (train 21 min, whole second pass ~73 min).
- Phase 3 (`p3-twin`): `twin/service.py` runs the real engine on the bus (12 satellites, red-team effects, faults, `--chunk`, `--orbit-speedup`); `scripts/replay_twin.py` stays as an alias so every make target and test keeps working; `twin/recorder.py` writes §6.3 episodes.
- Phase 7 (`p7-dashboard`): **Open MCT skipped**, the single-page dashboard (MASTER_PLAN fallback) built instead: plain HTML/JS, MQTT.js 5.16.0 vendored (license kept), served by `make dashboard` on 8080, subscribes over WebSocket 9001 and never to `gt/`. Reason: Open MCT needs a plugin layer and a build step for a 2-day window; the fallback shows the same tiles, events, actions and J values.
- Ensemble alert threshold: the core flags a twin satellite at **tau = 0.0153** from `calibration.json` (0.1 % FPR on the evaluation split) instead of the trajectory's 0.30; planning still starts at P ≥ 0.50. Kept as designed (M5); one consequence is seen in C1 seed 1 (E flagged twice for a few ticks, no action).
- Twin `heating_rate_c_s` 0.2 → **0.3** (the B1 failure above): the rate only matters when `temp_c` is untrusted, so a conservative value can only make the estimate early. Re-ran all 12 Phase 6 runs with it (not only B1) so every reported run uses the same configuration.
- Venv: the project `.venv` lives on `/mnt/c`, where `import torch` takes ~86 s, so the core takes ~2 min to start. A WSL-native venv `~/aurora-venv` (same `requirements.lock`, Python 3.11.17) imports torch in 2.6 s. **Not swapped:** the Makefile already takes `VENV=...`, so `make <target> VENV=$HOME/aurora-venv` uses it without renaming anything. `scripts/preflight.sh` warns when torch import is slow.
- Latency number: `ml.export` ran twice (both pipeline passes) and `results/latency.json` keeps the second: 34.1 ms median (5 models, 1 thread). The 18.4 ms from the first pass is not reported.
- Integration test fix: retained node states from earlier accelerated runs are dropped once the core is up (see failures above).

## Open questions
- **C1 source quarantine is now emergent but not systematic.** After the shadow started propagating ISL geometry (rollout 60 s), J quarantined the relaying source by itself in **2 of 3 seeds** (D at 137 s, K at 57 s after the attack); in seed 1 the source A stayed flagged with "relaying HEATER on … (suspected compromised neighbour, IA-0003)" and was left for ground. Every victim was contained before its limit in all 3. Nothing is forced. If you want it systematic: (a) add the expected containment cost of satellites a threat will newly reach to J; (b) count residual risk over a longer horizon (e.g. 300 s); (c) keep it, and say "flagged for ground" when J does not quarantine. Demo script follows (c) for now.
- **Contract v1.3 (message format, not changed):** fault injections (F2 SEU spike, F3 battery ageing, F5 stuck heater) have no `redteam/{id}` effect, so they are injected in the twin with `--fault TYPE:SAT@T`. Adding `spike_tm`, `age_battery`, `stuck_heater` effects would let `attacks/run.py` drive them over the bus.
- **Board demo visibility.** With the 4-tick window and decisions on P(F<60s), the core contains the board's A1 about **2 s** after the heater latches, at about **28.6 °C** (dry run). Phase 2 showed a climb to 53 °C before quarantine. Safer, but the audience barely sees B heat up. Options: (a) keep it, and show the climb with `--reassert` or the heating-curve video; (b) give the board profile its own `warn_p`/`plan_p` (e.g. plan at P ≥ 0.85) for a longer visible climb. Not changed: it is a demo-narrative choice.
- **Ensemble at the action threshold misses more than its headline says.** At tau (0.1 % FPR) the ensemble catches every failing satellite (recall 1.00, lead 133 s), but scored at the core's act thresholds (`ensemble@act`) recall is 0.89 (ID), 0.74 (OOD), 0.70 (Real) with ~38 s lead. Which one the paper headlines is your call; both are in `results/manifest.json`. The closed loop and Phase 6 use the act thresholds.
- **Closed-loop evaluator over the 150 ms planning budget** (461 ms median). Options: fewer candidates per plan, shorter rollout in `eval/closed_loop.py`, or report it as offline-only. Not changed.

Plan: [docs/MASTER_PLAN.md](MASTER_PLAN.md). Context: [docs/CONTEXT.md](CONTEXT.md).
Ownership: since the night of 11 Oct, **Mayssam owns every lane** again (Youssef has been told); the 11 Oct split below is kept for history.

| Component | Path | Owner | State | Evidence |
|---|---|---|---|---|
| Repo health (Python 3.11 venv, pinned deps, LF endings) | `Makefile`, `mk/`, `requirements.lock`, `.gitattributes` | Mayssam | done (G0) | `make setup && make test` |
| Interface contract | `docs/interfaces.md`, `aurora_common/` | Mayssam | done (G1), frozen v1.2 (SPARTA mapping per P4; SAFE semantics) | `pytest tests/test_schemas.py tests/test_topics.py` |
| MQTT broker | `ground/`, `mk/core.mk` | Mayssam | done (G1): Mosquitto 1883 + 9001, reachable from Windows | `make up && make bus-check` |
| CASSANDRA core | `cassandra/` | Mayssam | done: ingest, forecast (ensemble for twin satellites, trajectory for the board), monitor, sandbox (real engine hooks), act with escalation, explain, main loop | `pytest tests/cassandra tests/integration`; Phase 6 |
| Twin (plant) | `twin/service.py` (`scripts/replay_twin.py` alias), `twin/recorder.py` | Mayssam + Youssef | done (`p3-twin`, merged in `p9-integration`): real engine on the bus, red-team effects, faults, `gt/event critical`, recorder labels from truth | `tests/test_twin_service.py`, `tests/test_replay_twin.py` |
| Live attack scripts | `attacks/` | Mayssam | done: A1 (`--reassert`), B1, C1, F1, N, `--stop`; ground truth on `gt/` only | `make attack A1 TARGET=E` |
| ESP8266 board as satellite B | `hardware/`, `prototype/phase2-demo/` | Mayssam | software done (`p8-board`); **real-board bring-up pending** | `tests/hardware/`, `tests/integration/test_board_dry_run.py`; `hardware/BRINGUP.md` |
| Demo script, preflight, backup recording | `docs/DEMO_SCRIPT.md`, `scripts/preflight.sh` | Mayssam | draft script + preflight done; recording missing | `bash scripts/preflight.sh` |
| Learned forecaster, baselines, calibration | `ml/` | Mayssam + Youssef | done: M1–M5, S1–S4, S6, S7, S9, S10 fixed (both branches merged); retrained 12 Oct; `make models` (v2 release pending upload) | `tests/test_ml_fixes.py`, `tests/test_ml_review_fixes.py`, `tests/test_online_live.py`; `results/manifest.json` |
| Evaluation harness | `eval/` | Mayssam (was Youssef) | done: oracle removed (M1, M2), T_alert fixed (M3), pre-onset alarms reported | `results/eval/*.json` |
| Synthetic episode generator | `ml/synthetic/` | Mayssam (was Youssef) | done, plus engine hooks `snapshot(include_queue=False)`, `seed_from_observation(obs)` | `tests/test_synthetic_and_schema.py`, `tests/test_ml_fixes.py` |
| Dashboard | `dashboard/` | Youssef | single page (his, uPlot, 3 views; in `p9-integration`); checked in Chrome against the live core 12 Oct; Open MCT skipped | `make dashboard-test` / `tests/test_dashboard.py`; `make dashboard` |
| Paper source and numbers | `docs/paper/` | Mayssam (was Youssef) | paper source not in repo; numbers ready here and in `results/manifest.json` | — |
| Trained models, results | `data/models/`, `results/` | Mayssam | done locally (gitignored except the manifests) | `results/manifest.json` |

## Gates
G0 ✅ 2026-10-10 · G1 ✅ 2026-10-11 · G2 🔎 ready for review 2026-10-12 (ML fixed and retrained; second pipeline pass all exit 0; `results/manifest.json`) · G3 🔎 ready for review (live twin on the bus; Phase 6 ran on it) · G4 🔎 ready for review 2026-10-11, re-verified 12 Oct with the ensemble (`tests/integration`: 5 passed) · G5 🔎 ready for review (12 Phase 6 runs, none reached its limit after the heating-rate fix) · G6 ⏳ (real board) · G7 ⏳ (demo rehearsal, recording)

## Numbers (each with source file + command)
All simulation. Pipeline numbers: `bash scripts/overnight_pipeline.sh` (second pass, 12 Oct 02:12–03:25), collected by `python scripts/write_manifest.py` into `results/manifest.json`.

### Old (PR #1 report) vs new (12 Oct retrain), test_id unless stated
| Metric | Old (PR #1) | New | Source |
|---|---|---|---|
| Ensemble recall / median lead at 0.1 % FPR, ID | 1.00 / 133 s | **1.00 / 133 s** (IQR 114–163), realised FPR 0.15 %, AUROC 0.999 | `results/eval/metrics.json` |
| Ensemble, OOD | 1.00 / 135 s | **1.00 / 135 s** (139 s overnight, before the M3 correction) | same |
| Ensemble, Real (OPS-SAT noise) | 1.00 / 162 s | **1.00 / 158 s** | same |
| Threshold baseline | 0.90 / 49 s | 0.90 / 49 s (Real: 0.59 / 84 s) | same |
| Trajectory baseline | 0.86 / 20 s | 0.86 / 20 s (Real: 0.58 / 23 s) | same |
| LSTM-AE | 0.34 / 187 s | 0.35 / 197 s (overnight 0.38 / 205 s counted pre-onset alarms, M3) | same |
| GDN | 0.68 / 167 s | 0.69 / 167 s, and **6.5 pre-onset false alarms per satellite-hour** (others ≤ 0.6); overnight 0.80 / 222 s counted those alarms as detections (M3, corrected) | same (`pre_onset_false_alarms_per_sat_hour`) |
| Ensemble at the core's act thresholds (`ensemble@act`) | not reported | 0.89 / 38 s (OOD 0.74 / 36 s, Real 0.70 / 40 s) | same |
| Closed loop: failures averted | 61 % (inflated: oracle in simulator safe mode, M1) | **72 % (13 of 18 failing episodes; 95 % Wilson CI 49–87 %)**, 0 new failures, availability 0.999, 1 false containment (0.05 per orbit) | `results/eval/closed_loop.json` (`python -m eval.closed_loop --episodes 24`) |
| Inference latency, 5 models, 1 thread, x86 | 30.6 ms | **17.6–34.1 ms median** over three runs on this laptop (18.4, 34.1, 17.6 ms; the spread is machine load); latest 17.6 ms median, 20.9 ms p95 | `results/latency.json` (`python -m ml.export`) |

How to read the closed-loop line honestly: the old 61 % and the new 72 % are **not comparable** and neither is a field result. The old run let the simulator's safe mode delete malicious commands by their hidden label (M1); the new run has no oracle but still plans with the simulator as its own model (upper bound), on 24 episodes (18 failing without a response), hence the wide interval. Say "in simulation, about 7 in 10 failures averted, upper bound" or give 13/18 with the interval.

### Other pipeline numbers
| Metric | Value | Source |
|---|---|---|
| Calibration (ECE, 10 bins) | ≤ 0.005 at every horizon on all 3 splits (ID: 0.0007 / 0.0011 / 0.0021 / 0.0034 at 10 / 30 / 60 / 120 s) | `metrics.json` `calibration` |
| Temperature / tau | T = 0.730; tau = 0.0153 (0.1 % FPR) | `data/models/calibration.json` |
| Ablations (single model, no engineered features, unconstrained, no residual) | all recall 1.00 on ID with leads 132–135 s (OOD no_eng 0.90): **benchmark saturates** | `metrics.json` |
| Next subsystem to fail, top-1 / top-3 | ID 0.86 / 1.00; OOD 0.83 / 0.99; **Real 0.24 / 0.82** | `metrics.json` `next_sub` |
| Malicious vs fault, AUROC | ID 0.95, OOD 0.94, Real 0.94 | `metrics.json` `malicious` |
| Robustness (telemetry perturbation ε): ensemble recall / median lead; threshold recall | ε 0: 1.00 / 147 s; 0.25: 0.75 / 210 s; 0.5: 0.92 / 183 s; 1.0: 1.00 / 198 s; threshold 0.83 at every ε | `results/eval/robustness.json` (`python -m eval.robustness --episodes 12`); non-monotonic at 12 episodes: small sample |
| Stress (spoofed ground origin, no commands at all) | recall 1.00 in every variant, leads 133–135 s: **the cyber features are not what detects** | `results/eval/stress.json` |
| Wall time, second pass | evaluate 19 min, closed loop 13 min, robustness 28 min, stress 11 min, export 1 min, figures 1 min; train (first pass) 21 min | `results/logs/pipeline_times.tsv` |

### Phase 6: live scenarios, core in closed loop with the twin (in process, simulated), ensemble `tgn-v1`
`python scripts/phase6_runs.py` → `results/live_manifest.json` (12 runs, 449 s wall). Attack at t = 60 s; times are seconds after the attack; limit 45 °C.
| Run | Target | First action | Action at | True temp at action | Max temp | Healed / final |
|---|---|---|---|---|---|---|
| A1 seed 1 | E | isolate_subsystem | 62 s | 33.4 °C | 33.7 °C | healed 151 s |
| A1 seed 2 | G | isolate_subsystem | 116 s | 39.5 °C | 39.6 °C | healed |
| A1 seed 3 | J | isolate_subsystem | 216 s | 36.3 °C | 36.5 °C | healed |
| A1 `--reassert` seed 1 | E | safe_mode | 43 s | 28.8 °C | 29.0 °C | healed 351 s (after stop) |
| A1 `--reassert` seed 2 | G | safe_mode | 84 s | 35.9 °C | 36.0 °C | healed |
| A1 `--reassert` seed 3 | J | safe_mode | 203 s | 34.2 °C | 34.4 °C | healed |
| B1 seed 1 (masking) | E | isolate_subsystem | 75 s | 36.3 °C | 36.5 °C | healed (with rate 0.2: 124 s, 45.17 °C, **max 45.32 °C, limit crossed**) |
| B1 seed 2 | G | isolate_subsystem | 55 s | 32.0 °C | 32.1 °C | healed (rate 0.2: 94 s, 37.1 °C) |
| B1 seed 3 | J | isolate_subsystem | 75 s | 25.0 °C | 25.1 °C | healed (rate 0.2: 124 s, 29.2 °C) |
| C1 seed 1 (source A) | victims G, H, I, J | isolate / safe_mode | 44–421 s | 29.3–38.1 °C | ≤ 38.3 °C | all healed; **source A flagged, not quarantined**; E flagged twice briefly, no action (false alarm) |
| C1 seed 2 (source D) | D; victims G, L | **quarantine D** at 137 s; victims isolated 120–134 s | | 33.5–37.2 °C | ≤ 37.4 °C | victims healed; D held quarantined for ground |
| C1 seed 3 (source K) | K; victim E | **quarantine K** at 57 s; E isolated at 120 s | | 38.8 °C | 39.0 °C | E healed; K held quarantined |

No satellite reached its limit and no non-attacked satellite was contained in any run of the second pass. Core timing: tick median 28–36 ms, planning median 61–89 ms per run (laptop, WSL, ensemble; midday re-run on `p9-integration`, same outcomes as the morning run). First pass (rate 0.2): `results/live_manifest_heat0.2.json`.

### Earlier numbers (trajectory forecaster, 11 Oct; still valid for the board profile)
| Metric | Value | Source |
|---|---|---|
| Ported trajectory forecaster on the Phase 2 heater latch (board profile, T_max 60 C, +1.5 C/s, 1 Hz ticks) | P(F<30 s) >= 0.85 at t = 19 s, 53.5 C (paper Table II: 18.8 s, 53.3 C); flag (P >= 0.30) at t = 7 s, 35.5 C (paper: 2.3 s, 28.5 C; slower because the slope window is 8 ticks at 1 Hz instead of 8 samples at 2 Hz) | `tests/cassandra/test_forecast.py::test_phase2_heater_latch_on_the_board_profile` (simulated input) |
| Monitor false alarms, clean replay, 12 twin satellites x 600 s | 0 | `tests/cassandra/test_monitor.py::test_clean_replay_run_raises_no_alarm` (simulated) |
| A1 live through Mosquitto, `make demo-sw SPEED=20 SEED=3` + `make attack A1 TARGET=E` (2026-10-11, trajectory) | flagged at P(F<60s)=0.31; isolate_subsystem at 41.2 C (ETA to 45 C: 29 s), cause "heater latched ON (origin=ground, 116 s ago)", SPARTA IA-0009; healed at 29.4 C; tick latency median 1.8 ms, p95 3.1 ms; planning 41 ms | core console; `tests/integration/test_a1_software.py` |
| A1 with re-assertion (in process, seed 3, trajectory) | every 150 s: isolate at t = 187 s, escalated to safe_mode at 211 s, healed 511 s, max 41.6 C; every 5 s: safe_mode first at 187 s | `tests/cassandra/test_escalation.py` |
| Board dry run, A1 on B (fake board = firmware model, real time, 2026-10-12) | flagged and `isolate_subsystem` ~2 s after the latch at 28.6 C (P(F<60s)=0.58, ETA to 60 C ~25 s, IA-0009); LED B red; healed at 26.2 C; B telemetry src `hw` | `tests/integration/test_board_dry_run.py` (simulated board) |
| Core with the ensemble through Mosquitto (20x) | tick latency median 47.9 ms, p95 60.3 ms (120 ticks); at 20x the tick budget is 50 ms, so **use SPEED ≤ 10 for live demos with the ensemble** | `tests/integration` run, 12 Oct |

## Known limitations (said out loud in the demo and paper)
- Closed-loop results are an upper bound (sandbox uses the simulator as its model), and come from 18 failing episodes.
- Benchmark saturates: every ablation and the no-telecommand stress test reach recall 1.00, so the benefit of the graph structure and cyber features is not shown.
- On OPS-SAT-noised data, next-subsystem top-1 drops to 0.24.
- Board temperature is emulated by firmware (no sensor); the ISL cut on the board is logical, not a radio cut.
- Training and evaluation data are synthetic (`ml/synthetic/`), not twin recordings or hardware.

## Notes for Youssef (history; Mayssam owns these lanes since 11 Oct)
- M1 fix follows the SAFE rule of `docs/interfaces.md` Section 3.2 (v1.2); `twin/service.py`, `scripts/replay_twin.py` and the board bridge apply the same rule. Done on `p2-ml-fixes`.
- Engine hooks `snapshot(include_queue=False)` and `seed_from_observation(obs)` exist in `ml/synthetic/engine.py`; the core's stand-ins were deleted.
- S10: offline synthetic episodes say `src: "synthetic"`. Done.

## For the paper, slides, demo script and voiceover
- **A1 wording: say "contains", not "quarantines".** For a single latched heater command, J chooses `isolate_subsystem` (cut the heater, satellite stays in service). The demo script and voiceover must say "CASSANDRA contains B/E" and name the action J chose on screen; quarantine appears only when J escalates or for C1 (2 of 3 seeds).
- SPARTA ids changed from the submitted paper (P4 adopted 2026-10-11): masking is now DE-0002 / DE-0003 (EX-0001 for the replay variant), propagation IA-0003; Scenario A stays IA-0009. Wherever the new ids appear, add the footnote **"Technique mapping refined in Phase 3."** Table: `docs/interfaces.md` Section 7.
- Numbers come from `results/manifest.json` / the Numbers section above, never from PR #1's report.

## Decisions log
- 2026-10-10: T_max per satellite: 60 °C for the ESP8266 board (emulated temperature, matches the submitted paper), 45 °C default for twin and future ESP32 hardware.
- 2026-10-10: core ticks on message timestamps with a wall-clock watchdog; warn when the hw-vs-twin clock offset exceeds 0.5 s.
- 2026-10-10: Mosquitto on 1883/9001; port 6379 stays with the Phase 2 Redis fallback (`sat_b_redis`).
- 2026-10-11: SPARTA proposal P4 adopted (interfaces.md v1.1, Section 7).
- 2026-10-11: SAFE semantics (interfaces.md v1.2): HEATER on refused in SAFE from any origin; only SET_MODE NOMINAL exits SAFE. A1 attack = one latched HEATER on (`--reassert` optional).
- 2026-10-11: scope split; Makefile split into `mk/*.mk`. Night of 11 Oct: Mayssam owns every lane.
- 2026-10-11: G4 wording "flagged, contained (action chosen by J), healed"; escalation made real; 4-tick slope window for the board.
- 2026-10-12 (overnight): see "Decisions taken overnight".

## Changelog
- 2026-10-10: Phase 0 done (branch `p0-health`, commit a9e107a).
- 2026-10-11: Phase 1 done (branch `p1-contract`): interfaces.md v1, aurora_common, Mosquitto, Makefile split into mk/*.mk, paho-mqtt 2.1.0 added.
- 2026-10-11: Phase 4 steps 1–3 (`p4-core`), escalation (0c380f1).
- 2026-10-12 (overnight, local only): Phase 8 software (`p8-board`), ML fixes + retrain (`p2-ml-fixes`), Phase 5 (`p5-scenarios`), Phase 3 (`p3-twin`), Phase 7 + Phase 6 runs + manifests + preflight + demo script (`p7-dashboard`).
