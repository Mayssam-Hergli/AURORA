> Historical integration/planning record. The hardened `rami` behavior, current checks and fresh result provenance are documented in [HARDENING_REPORT.md](HARDENING_REPORT.md). Earlier action names, uncertainty rules and numbers below describe earlier revisions.

# AURORA / CASSANDRA: Master Plan to Done

**For:** Claude Code, working in the `AURORA` repo with Mayssam Hergli.
**Written:** Saturday, 10 October 2026. Build deadline: **Wednesday, 14 October 2026**, then the live final at the Congress, with a recorded backup.
**Ownership update, night of 11 Oct:** Mayssam takes over every lane again, including Youssef's folders below (he has been told). The table is kept for history.

**Ownership (updated 11 Oct, supersedes the 10 Oct "Mayssam owns every lane"):**

| Owner | Folders | Phases |
|---|---|---|
| **Mayssam Hergli** (with Claude Code) | `docs/interfaces.md`, `aurora_common/`, `ground/`, `cassandra/`, `attacks/`, `hardware/`, `prototype/phase2-demo/`, `scripts/`, `mk/core.mk` | 0, 1, 4, 5, 6, 8, and the demo script, preflight and recording of 9 |
| **Youssef Mhamdi** | `ml/`, `eval/`, `twin/`, `dashboard/`, `docs/paper/`, `mk/ml.mk`, `mk/twin.mk` | 2, 3, 7, and the numbers and paper of 9 |

Neither side edits the other's folders. What the core needs from `ml/` goes through an adapter on the core side (`cassandra/sandbox.py` stubs `snapshot(include_queue=False)` and `seed_from_observation(obs)` until Youssef's engine provides them). Until `twin/service.py` exists, the core is tested with `scripts/replay_twin.py`, which publishes `SyntheticTwin.messages_at(t)` onto the bus. Youssef's PR #1 (`ml/`, `eval/`) is merged and is the foundation.
**Companion files:** `docs/CONTEXT.md` (full project context: architecture, schemas, scenarios) and `phase2_kit.zip` (the working Phase 2 demo code).

---

## 0. Read first: rules for Claude Code

1. **Work phase by phase, in order.** Each phase ends with a **gate**: an acceptance check you run yourself. Show Mayssam the gate output, then stop and wait for her "go" before starting the next phase. Inside a phase, keep going without asking.
2. **Read the code first.** Before you change a module, read it. This plan cites file:line locations from the PR #1 review; they can drift. If the code differs from what this plan assumes, adapt to the code and say so in one line.
3. **Git workflow:**
   - Create one branch per phase (`p0-health`, `p1-contract`, …).
   - Commit on the branch once that phase's tests pass. Use small commits with clear messages.
   - **Never push, open a PR, merge or touch `main` without Mayssam's explicit OK.**
   - Never force-push. Never rewrite history.
4. **Honesty:**
   - Never invent a number. Every number in the README, paper, slides or `STATUS.md` must come from a file in `results/` that code produced. Include the command that produced it.
   - Label everything simulated. Twin satellites carry `src: "twin"`. Synthetic training episodes carry `src: "synthetic"`. Anything emulated on a board (for example, a temperature computed by firmware instead of measured) is listed in an `emulated` field.
   - Closed-loop results are an **upper bound**, because the sandbox uses the simulator as its model. Say so wherever they appear.
   - Wording: "minimises mission impact", never "prevents all attacks".
5. **Ground truth isolation:**
   - Nothing under `cassandra/` may subscribe to, import or read `aurora/v1/gt/#`, `aurora/v1/redteam/#`, any `_malicious` or `_auth_ok` flag, or `labels.json`/`ml_labels.json`.
   - Add a test that enforces this: grep the package source and assert at runtime that the core's subscriptions are exactly the allowed list.
6. **Never commit** `data/`, `results/` raw files, `*.pt`, `*.npz`, `*.parquet`, `.env`, secrets or anything over 1 MB. The exceptions are the small `results/manifest.json` and figures that are already tracked.
7. **Environment:**
   - Use Python **3.11** in `.venv/`. Every Makefile target calls `.venv/bin/python` explicitly.
   - Mayssam works on Windows + WSL Ubuntu. Docker runs in WSL. USB/COM ports are visible only on **Windows**, so anything that touches a serial port is a Windows-side script.
   - The Windows locale is French. Parse and format numbers with `.` explicitly; never depend on the locale.
8. **Human steps:** some steps need Mayssam's hands (flashing, wiring, GitHub settings, uploading files). They are marked **[HUMAN]**. When you reach one, print exact copy-paste instructions and wait.
9. **Keep `docs/STATUS.md` current.** Its format is in Section 6. Update it at every gate.
10. **Cutting scope:** if a phase blows past its time box by more than ~50%, apply the cut list (Section 5) and tell Mayssam what you cut. Don't let a nice-to-have block a gate.

---

## 1. Where things stand (verify in Phase 0)

| Component | State on 10 Oct | Source |
|---|---|---|
| `ml/`: 12-satellite synthetic engine (`ml/synthetic/engine.py`), features, GNN+GRU ensemble, calibration, baselines, `ml/online.py` OnlineForecaster, export | Done (PR #1), with must-fix bugs | review |
| `eval/`: harness, metrics, `closed_loop.py` (J(a), 0.35 gate, 25% budget, healing), robustness, stress, loaders, figures, `paper_numbers.py` | Done, with must-fix bugs | review |
| `docs/interfaces.md`, `aurora_common/` | **Missing**: every lane is blocked on it | status |
| MQTT broker (`ground/`) | **Missing** | status |
| Twin service on MQTT (`twin/`), recorder | **Missing**: physics exist in `ml/synthetic/` | status |
| CASSANDRA core (`cassandra/`) | **Missing**: reference loop exists in `eval/closed_loop.py` | status |
| Live attack scripts (`attacks/`) | **Missing**: attacks exist only inside the engine | status |
| Dashboard (`dashboard/`) | **Missing** | status |
| Firmware (`firmware/`), Pi, router | **Missing**. One ESP8266 NodeMCU (Amica) runs the Phase 2 firmware; one ESP32 failed to flash | Mayssam |
| `prototype/phase2-demo/` | README only. The code is in `phase2_kit.zip` | status |
| Paper source (`docs/paper/`) | **Missing** in the repo | status |
| Trained models, `results/` | Gitignored; they exist only on Youssef's machine | review |

### Known bugs from the PR #1 review (all fixed in Phase 3)

**Must fix:**
- **M1, `ml/synthetic/engine.py:425-428`:** simulator safe mode deletes queued commands flagged `_malicious`. That is an oracle, and it inflates the "61% averted" figure.
- **M2, `eval/closed_loop.py:89`:** the sandbox snapshots the real simulator, including the attacker's queued future commands.
- **M3, `eval/metrics.py:52-55`:** T_alert only counts from the true onset, so pre-onset false alarms are ignored.
- **M4, `ml/features.py:68`:** string-matches `'"on": true'`, so compact JSON loses the feature.
- **M5, `ml/online.py:127-130`:** the calibration temperature comes only from the gitignored `results/eval/metrics.json`. Without it the model silently runs at T = 1.0.

**Should fix:**
- **S1, `eval/harness.py:37-47`:** the model is scored on a ground-truth-dependent schedule, unlike the baselines.
- **S2, `ml/features.py:26-29`:** the feature builder needs label files.
- **S3, `ml/train.py:108`:** the log dir only exists for `train all`.
- **S4, `ml/online.py:137-140`:** IndexError before the first `tick()`.
- **S5:** dependencies are unpinned, and there is no results manifest.
- **S6, `docs/ML_GUIDE.md:20,34`:** doc mismatches, including the topic `aurora/v1/sat/{id}/tm/{sub}`.
- **S7:** `make generate` crashes without OPS-SAT data.
- **S8:** the benchmark saturates (every ablation reaches recall 1.00).
- **S9, `eval/harness.py:152`:** ablations are skipped on `test_ood`.
- **S10:** synthetic episodes say `src: "twin"`.

---

## 2. Decisions already made (do not reopen)

| # | Decision |
|---|---|
| D1 | **Software-only mode first.** The twin plays all 12 satellites and the core defends them. This is also the backup demo. Hardware joins afterwards. |
| D2 | **P1 accepted:** per-satellite `ml_labels.json` with times relative to `t0`, **and `t0` (Unix seconds) stored in the file**. |
| D3 | **P2 accepted:** `SET_MODE.mode` ∈ the contract modes plus the values the generator uses (`NOMINAL`, `SAFE`, `SCIENCE`, `IDLE`, `WHEEL_TEST`). `QUARANTINED` is a core **state**, not a `SET_MODE` value. Reconcile with `docs/INTERFACE_PROPOSALS.md` and list the final enum in `interfaces.md`. |
| D4 | **P3 accepted:** `p_std` is the ensemble **standard deviation**. Gate: Q = p_std at the decision horizon, threshold θ = 0.35. **With fewer than 2 models, Q is unavailable.** The gate is then reported as `"gated": null, "q": null`, the explain line says "uncertainty gate inactive (single model)", and the core never pretends Q = 0 means certainty. |
| D5 | **P4 accepted** as written in `docs/INTERFACE_PROPOSALS.md`, **unless** it renames IA-0009 or IA-0006. Both appear in the submitted paper. In that case, stop and ask Mayssam. |
| D6 | **P5 accepted** as written, as long as it stays compatible with `ground | core | isl:<peer_id> | unknown`. |
| D7 | **Live board forecasting uses the trajectory forecaster** (Phase 2 logic) for Scenarios A–C. The synthetic thermal model (−10 °C, 50–90 W heater) doesn't match a 0.25 W resistor at room temperature, and there's no time to retrain properly. The learned ensemble defends the twin satellites. Each forecast message names its `model`. |
| D8 | **Red-team side channel:** effects that need a compromised on-board computer (B1 telemetry freeze, C1 relay) are triggered on a new topic `aurora/v1/redteam/{sat}`. Only the twin service and the hardware bridge subscribe to it. The core must never subscribe to it (rule 0.5). Add it to `interfaces.md`. |
| D9 | **Rename synthetic episode `src` to `"synthetic"`** (S10), so offline data can never be confused with the live twin. |
| D10 | **The hardware path for the final is the working ESP8266 board, bridged to MQTT**, playing satellite **B** (`src: "hw"`, temperature listed as `emulated`). Its 6 LEDs show the states of A, B and C. ESP32 firmware with ESP-NOW is a stretch goal (Phase 9). |
| D11 | **Merge `safety`, `executor` and `verify` into `cassandra/act.py`** (three classes, one file). The other modules stay as in CONTEXT §5.4. |
| D12 | `explain` uses **templates**, not an LLM. |
| D13 | The evidence slide shows twin-episode results only, with the saturation caveat. ESA-ADB and SCAD go on it only if lead-time numbers are actually computed. |

---

## 3. Gates (definition of done)

| Gate | Done when | Owner | Target |
|---|---|---|---|
| **G0** Healthy repo | `make test` green on Python 3.11 with pinned deps; `STATUS.md` exists | Mayssam | Sat 10 |
| **G1** Contract + bus | `docs/interfaces.md` frozen; `aurora_common` tests pass; `make up` starts Mosquitto (1883 + WebSocket 9001); `mqtt_tail.py` shows a test message | Mayssam | Sat 10 |
| **G2** ML fixed | M1–M5 and S1–S7, S9, S10 fixed with tests; pipeline re-run; `results/manifest.json` committed; trained models on disk | Youssef | Sun 11 |
| **G3** Live twin | 12 twin satellites on the bus at 1 Hz; a HEATER command visibly heats; a recorded run opens with `ml.schema.read_episode` | Youssef | Sun 11 |
| **G4** Lane 1 done | `make demo-sw` + `attacks A1`: the target is flagged, **contained (action chosen by J)** and healed **with no manual step**, and the gt-isolation test passes. Until G3, the plant is `scripts/replay_twin.py` | Mayssam | Mon 12 |
| **G5** All scenarios + dashboard | A1, B1 and C1 each handled in software (Mayssam); Open MCT shows telemetry, P(F), states, events and links, and survives a broker restart (Youssef) | both | Tue 13 |
| **G6** Board joins | The ESP8266 board is satellite B on the bus; A1 against B heats the board, the core quarantines and heals it, and the LEDs follow | Mayssam | Tue 13 |
| **G7** Ship | Numbers regenerated and paper compiles with macros (Youssef); demo script, preflight, backup video recorded (Mayssam); `v1.0-final` tag (with OK) | both | Wed 14 |

---

## 4. Phases

### Phase 0: Repo health (≈1 h) → G0 · owner: Mayssam

1. `git pull`. Read `README.md`, `docs/CONTEXT.md`, `docs/ML_GUIDE.md`, `docs/INTERFACE_PROPOSALS.md`, `Makefile`, `pyproject.toml`, `ml/schema.py`, `ml/online.py`, `ml/synthetic/engine.py` (the API part), and `eval/closed_loop.py`.
2. Create `.venv` with Python 3.11 and run `make setup` then `make test`. Report anything that fails on 3.11 and fix it.
3. **Pin dependencies (S5):** freeze the working set into `requirements.lock` (or pin in `pyproject.toml`). Use the CPU-only PyTorch index if the GPU wheel is too large.
4. Create `docs/STATUS.md` (format in Section 6).
5. **[HUMAN]** Ask Mayssam to:
   - Protect `main` on GitHub: Settings → Branches → add a rule for `main` that requires a pull request before merging, and blocks force pushes.
   - Tell the team that she's taking over all lanes. Youssef should not push to `ml/` or `eval/` this week without coordinating, to avoid conflicts.
6. **Gate G0:** `make test` output plus the pinned file. Stop for approval.

### Phase 1: Interface contract + bus (≈3 h) → G1 · owner: Mayssam

**1a. `docs/interfaces.md`**
- Start from `docs/CONTEXT.md` §6 and apply decisions D2–D6, D8 and D9.
- It must contain:
  - The topic tree, including `redteam/{sat}` (D8), with a "who may subscribe" column. Core: never `gt/`, never `redteam/`.
  - QoS: 1 for `tc`, `state`, `action` and `event`; 0 for `tm`, `link` and `forecast`.
  - Every JSON schema with field units, including the minimum per-subsystem fields.
  - Optional fields:
    - `emulated: [field names]` on telemetry.
    - `model` and `q` on forecasts.
    - `id` (uuid4 hex) on every `tc`.
    - `seq` on every telemetry stream, per (sat, sub).
  - The final enums:
    - `cmd`: HEATER, SAFE_MODE, SET_MODE, ISL_ENABLE, PING.
    - `SET_MODE.mode` (D3).
    - `origin` (D6).
    - `state`: nominal, flagged, quarantined, safe_hold.
    - `src`: hw, twin, synthetic.
  - The episode format (§6.3) plus `ml_labels.json` with `t0`.
  - A changelog line: "v1 frozen 2026-10-10".
- **Red-team message** `aurora/v1/redteam/{sat}`: `{"v":1,"t":…,"sat":"B","effect":"freeze_tm|bias_tm|relay_tc|stop","args":{…}}`.
- **Ground-truth messages** `gt/episode` and `gt/event`: same keys as `labels.json`, plus `event` ∈ {onset, stop, critical}.

**1b. `aurora_common/`** (package at the repo root, installed with `pip install -e .`)
- `schemas.py`: pydantic v2 models for every message. Include `parse(topic, payload) -> Model` and `dump(model) -> bytes` (compact JSON).
  - Define the label and episode models here, field-for-field compatible with `ml/schema.py` (a test checks this). `ml/` is Youssef's folder, so `ml/schema.py` is not edited; switching it to re-export from `aurora_common` is his call.
- `topics.py`: builders and parsers (`tm(sat, sub)`, `tc(sat)`, `link(a, b)` with alphabetical ordering, …), plus `CORE_ALLOWED_SUBSCRIPTIONS` and `FORBIDDEN_FOR_CORE = ["aurora/v1/gt/#", "aurora/v1/redteam/#"]`.
- `bus.py`: a thin paho-mqtt v2 wrapper with auto-reconnect, JSON helpers and `publish(model)`. It logs reconnects; it never silently drops QoS 1 messages.
- `tests/test_schemas.py`: every example in `interfaces.md` validates; compact and spaced JSON both parse; a bad enum fails.

**1c. Ground bus (`ground/`)**
- `ground/docker-compose.yml`: `eclipse-mosquitto:2` with `ground/mosquitto/mosquitto.conf`. Listeners: 1883 (MQTT) and 9001 (WebSockets). Anonymous access is allowed on the demo LAN only; add a comment saying so. Persistence off.
- `Makefile`:
  - `make up` / `make down` / `make reset`. `reset` restarts the broker and deletes any retained messages under `aurora/v1/#`.
  - `make tail` → `ground/tools/mqtt_tail.py`, which prints `topic | src | t | compact payload`. Options: `--filter`, `--gt` (shows gt/ only when asked) and `--rate` (messages/s per topic).
- WSL2 forwards `localhost` to Windows, so Windows scripts reach the broker at `localhost:1883`. If that fails, print the `hostname -I` fallback.

**Gate G1:** run `make up`, publish a sample telemetry message with `bus.py`, show the `make tail` output and the green test run. Stop for approval.

### Phase 2: Fix the ML lane (≈4–6 h, mostly pipeline runtime) → G2 · owner: Youssef

Fix these on branch `p2-ml-fixes`. Every fix gets a regression test.

| ID | Fix |
|---|---|
| M1 | Remove the oracle from `engine.py` safe mode. Safe mode now does what a real one can: heater off, non-essential loads off, and it **rejects non-ground-origin commands while in SAFE**. It never inspects `_malicious`. Test: a malicious command queued with origin `ground` still executes after safe mode (that is realistic). |
| M2 | The sandbox may not copy the attacker's queue. Add `SyntheticTwin.snapshot(include_queue=False)` (or equivalent), and make `closed_loop.py` plan on a snapshot with the queue stripped. The planning model of the threat is "the last observed command effects persist" (for example, the heater stays latched on). Test: the snapshot used for planning has an empty future queue. |
| M3 | T_alert is the first tick with P ≥ τ **anywhere** in the episode (CONTEXT §5.8). Count and report pre-onset alarms in attack episodes as false alarms. Test with a synthetic P series. |
| M4 | Parse `args` with `json.loads` (or use the dict directly). Test that compact and spaced JSON give the same count. |
| M5 | `make train` writes `data/models/calibration.json` (`{"temperature": T, "tau": τ, "fpr_target": 0.001, "git": hash}`) next to the `.pt` files. `OnlineForecaster` loads it. If models exist but calibration doesn't, **raise with a clear message**. Allow an override with `allow_uncalibrated=True`, which logs WARNING and sets `model: "…-uncalibrated"`. |
| S1 | Score the model on every tick, like the baselines. If that is too slow, use a fixed stride for all methods that doesn't depend on onset. |
| S2 | The feature builder takes `t0` and the satellite list from the telemetry itself when label files are absent, so recorder and hardware episodes can be featurised. |
| S3 | `train_job` creates the log dir. |
| S4 | Return a well-formed "no data yet" forecast (p = 0, `model: "trajectory-v0"`, `warming_up: true`) before the first tick. |
| S6 | Fix `ML_GUIDE.md` lines 20 and 34 and the topic name. |
| S7 | `make generate` skips `test_real` with a clear message when the OPS-SAT data is missing. |
| S9 | Run the ablations on `test_ood` too. |
| S10 | Synthetic episodes write `src: "synthetic"` (D9). |

**Then:**
1. Re-run `make generate cache train evaluate closed-loop robustness stress export figures`. Use `make data` first if the network allows. **[HUMAN]** If GitHub or Zenodo downloads are blocked, ask Mayssam to switch to the phone hotspot.
2. Time each step and record the machine specs.
3. Write `results/manifest.json`: git hash, Python and package versions, seeds, the command run, wall time per step, the paths of the metrics files, and the headline metrics. **Commit only the manifest**, not the raw results.
4. Record the new closed-loop "% averted" without the oracle. It will drop. That is the honest number. Note it in `STATUS.md`.
5. **[HUMAN]** Upload `data/models/*` (with `calibration.json`) as a GitHub Release asset `models-v1`, or to Drive. Add `make models`, which downloads it into `data/models/` and checks a SHA256.
6. **S8 (benchmark saturates):** don't fix it now. In `STATUS.md`, write that every ablation and the no-telecommand stress test reach recall 1.00, so the benchmark can't yet show that graph structure or cyber features help. Phase 9 has an optional harder generator.

**Gate G2:** test output, `manifest.json` and a before/after table of headline numbers (old report vs re-run). Stop for approval.

### Phase 3: Live twin + recorder (≈3 h) → G3 · owner: Youssef

**`twin/service.py`** (`python -m twin.service --sats A-L|D-L --speedup 1 --config twin/config.yaml`):
- Wrap `ml.synthetic.engine.SyntheticTwin` in a **clean live scenario**: no internally scheduled attacks; all attacks come over the bus.
- Every real second:
  - `step(1)`.
  - Publish `messages_at(t)` on `sat/{id}/tm/{sub}` (with `seq`, `src: "twin"`) and `link/{a}-{b}`, using `aurora_common`.
  - Publish `sat/{id}/state` only at startup (`nominal`). After that, state belongs to the core.
- Subscribe to `sat/+/tc` and pass each command to `twin.apply()`. **Skip commands whose `id` the twin published itself**, so relayed ISL commands don't execute twice.
  - `ISL_ENABLE` sets the link's enable flag.
  - `SAFE_MODE` and `SET_MODE` change the OBC mode.
- Subscribe to `redteam/+` (D8):
  - `freeze_tm` holds the last reported value of the named channel while physics continues.
  - `bias_tm` adds a slow drift.
  - `relay_tc` makes that satellite forward the given command to every neighbour over **feasible, enabled** ISLs, with a relay delay. It publishes on `sat/{nbr}/tc` with `origin: "isl:<sat>"`.
  - `stop` clears the effect.
- **Long runs:** the engine sizes its arrays from `episode_s` (600 s). Add a live mode: either a ring buffer, or set `episode_s` to 4 h and roll over cleanly. It must run 60 minutes without growing memory.
- `--sats D-L`: skip publishing A–C, which are the hardware slots.
- `--speedup N`: orbit time compression, as `orbit_speedup` in CONTEXT. Subsystem physics stays at 1 s steps.

**`twin/recorder.py`** (`python -m twin.recorder --out episodes/live-<timestamp>`):
- Subscribe to `aurora/v1/#`, including `gt/`. The recorder is the scorer, not the defence.
- On Ctrl-C or a `gt/episode` stop, write the §6.3 folder: telemetry, commands and links Parquet, plus `labels.json` built from the gt messages and `ml_labels.json` with `t0`.

**`Makefile`:** `make demo-sw` = `up` + twin (A–L) + core. Use a process supervisor such as `honcho`, or a small Python launcher, so one Ctrl-C stops everything. Each component also gets its own `make twin`, `make core` and `make record`.

**Gate G3:**
- `make up && make twin`: the tail shows 12 satellites at ~1 Hz.
- `scripts/send_tc.py E HEATER on`: E's `temp_c` rises.
- Record 60 s, then `ml.schema.read_episode` opens it.

Stop for approval.

### Phase 4: CASSANDRA core (≈6–8 h) → G4 (Lane 1 done) · owner: Mayssam

Package layout:

```
cassandra/
  __main__.py      # python -m cassandra --config cassandra/config.yaml
  config.yaml      # thresholds, horizons, weights, budgets, model paths, allowed sats
  ingest.py        # MQTT → per-tick world state
  forecast.py      # OnlineForecaster wrapper (+ trajectory fallback) → core/forecast/{id}
  monitor.py       # cross-channel consistency (Scenario B)
  sandbox.py       # shadow twin seeded from observations; scores candidate actions with J(a)
  act.py           # Safety (gate + budget), Executor (TCs/state/links), Verifier (heal)
  explain.py       # templates → core/event
  world.py         # dataclasses for the observed state per sat and the link table
```

**`ingest.py`**
- Subscribe only to `sat/+/tm/+`, `sat/+/tc` and `link/+`, plus the core's own topics if it needs them. Nothing else.
- Keep a per-satellite, per-subsystem latest-value store with an age.
- Close a **1 s tick** on wall-clock seconds. Missing values are held for at most 3 s, then marked stale.
- Check `seq` per (sat, sub): count gaps and duplicates. Check provenance: `src` must match the satellite's configured kind (hw or twin). A mismatch becomes a `core/event` warning.
- Record every observed `tc` with its `origin` (that is evidence for C1).
- Output: `WorldTick(t, sats{…}, links{…}, tcs[…])`.

**`forecast.py`**
- Per satellite, feed `OnlineForecaster.on_telemetry`, `on_command` and `on_link` as messages arrive, call `tick(t)` once per tick, and publish `core/forecast/{id}` with `p_fail{10,30,60,120}`, `p_std`, `q`, `next_sub`, `p_malicious` and `model`.
- **Model choice per satellite:**
  - `src: "hw"` uses the trajectory forecaster (D7). Port the Phase 2 `t_to_crit` logic, with `pfail = clip(1 − t_to_crit/HORIZON)` and limits from config: hardware T_max = 45 °C.
  - Twin satellites use the ensemble if `data/models` and `calibration.json` load. Otherwise they fall back to trajectory, and a startup `core/event` says which model is in use and why.
- Log the per-tick latency (forecast + plan) to a rolling stats line every 30 s.

**`monitor.py`** (Scenario B, IA-0006 masking)
- Rule 1: `heater_on == 1` (or heater current above threshold) for at least N s, **and** reported `temp_c` changes by less than ε over that window. The reported value is frozen while the heater runs, so the sensor is inconsistent.
- Rule 2: the reported temperature trend disagrees with the expected trend from the sun level and eclipse (twin satellites only), beyond the tolerance in config.
- On a hit:
  - Set the satellite's state to `flagged`.
  - Publish a `core/event` naming the inconsistent channels.
  - Mark the reported channel **untrusted**, so `forecast` switches that satellite to an estimate from the independent channels: heater on-time × known heating rate. The trajectory forecaster then runs on the estimate.
- Tolerances live in config. Test against twin runs: zero hits on 10 min clean, and a hit within ≤20 s of a B1 onset.

**`sandbox.py`** (fixes M2 and answers Youssef's open question)
- Before each decision, build a **shadow `SyntheticTwin`** and seed it from the latest `WorldTick`: temperatures, SOC, modes, heater flags, link enable and feasible flags, and orbit phase.
  - If the engine has no setter for some state, add `SyntheticTwin.seed_from_observation(obs)` in `ml/synthetic/` with a test.
- **No queued commands from the real world.** Threat assumption: the last observed command effects persist, for example a latched heater stays on.
- Candidates: `none`, `safe_mode(X)`, `block_source(origin)`, `isolate_subsystem(X, sub)`, `quarantine(X) + reroute`. Port the bodies of `plan()` and `apply_action()` from `eval/closed_loop.py`.
- Roll each candidate forward over the horizon (60 s by default) and compute R, U, C and Q, then J(a) = w_r·R + w_m·(1−U) + w_c·C + w_q·Q, with weights from config.
- Return a ranked list. Time box: under 150 ms for 5 candidates on a laptop. Otherwise shorten the horizon or the number of candidates, and log it.

**`act.py`**
- **Safety:**
  - If Q > 0.35, hold and alert: publish `core/action` with `gated: true`, set state `flagged`, take no isolation.
  - If Q is unavailable, follow D4.
  - Isolation budget: at most 25% of satellites (3 of 12) quarantined per orbit window. The window is the orbit period divided by `speedup`. Over budget, the core takes the next-best non-isolating action and explains why.
- **Executor:**
  - Publish `core/action` (chosen action, J, alternatives with J, `gated`, `reason`).
  - Publish `sat/{X}/state`.
  - Publish TCs with `origin: "core"` and a fresh `id`.
  - Quarantine sends `SAFE_MODE` to X, then `ISL_ENABLE{peer, on:false}` for every link of X (to both ends), then enables alternative links **only where `feasible: true`**.
- **Verifier:**
  - After an action, watch residual risk. Heal when the satellite has been back under nominal (default 30 °C on hardware, or the config value per subsystem) **and** P(F<30 s) < 0.1 for N = 3 consecutive ticks.
  - Healing restores the links that were disabled, sends `SET_MODE NOMINAL`, sets state `nominal` and publishes a `core/event`.
- **State machine per satellite:**
  - nominal → flagged when P(F<30 s) ≥ `warn` (0.30) or a monitor hit.
  - flagged → quarantined when P ≥ τ (from `calibration.json` for the ensemble; 0.85 for trajectory) and the plan picks it.
  - quarantined → nominal through the Verifier.
  - Each state has a minimum dwell time (default 2 s) to avoid flapping.

**`explain.py`**
- One line per decision on `core/event`, shaped like:
  `"[B] P(F<30s)=0.88 (trajectory-v0) · cause: heater latched ON (origin=ground) → THERMAL→EPS path · SPARTA IA-0009 · action: quarantine (J=0.21) · alternatives: safe_mode 0.34, none 0.92 · ETA to T_max 23 s"`.
- Also emit events for: model in use at startup, monitor hits, gated decisions, budget limits, heals, and seq or provenance anomalies.
- Use SPARTA IDs from the D5 table.

**Tests (`tests/cassandra/`)**
- Unit tests: tick alignment, seq gaps, state machine transitions, the budget, the Q gate (including D4), and quarantine sending only feasible reroutes.
- **gt isolation:**
  - A static grep finds no `gt/`, `redteam/`, `_malicious`, `labels.json` or `ml_labels` under `cassandra/`.
  - A runtime check confirms the core's subscriptions equal `CORE_ALLOWED_SUBSCRIPTIONS`.
- **Integration** (`tests/integration/test_a1_software.py`, marked slow): start the broker (or an embedded test broker such as `amqtt`, if Docker isn't available in CI), the twin with `--speedup`, the core and attack A1 on E. Assert that E goes flagged → quarantined → nominal within a time limit, with no manual step.

**Gate G4 (Lane 1 done):** `make demo-sw`, then `make attack A1 TARGET=E`. Show the tail of `core/event` and the state sequence for E. Show the integration test green. Stop for approval.

### Phase 5: Attacks over MQTT (≈2 h, can overlap Phase 4's end) → part of G5 · owner: Mayssam

`attacks/run.py --scenario A1|B1|C1 --target E [--onset 10] [--duration 120] [--intensity 1.0] [--seed 0]`
- Before sending anything, publish `gt/episode` (start) with `labels.json` keys. Publish `gt/event` (onset, stop) at each step. **Truth goes to `gt/` only.**
- **A1** (IA-0009 heater latch):
  - Publish `HEATER{on:true}` to `sat/{target}/tc` with `origin` = whatever the threat model spoofs (`ground` by default), and re-assert every 5 s for the duration, like a latched command.
  - No malicious flag anywhere in the TC.
- **B1** (IA-0006 masking):
  - `redteam/{target}` with `freeze_tm {sub:"thermal", field:"temp_c"}`.
  - Plus A1's heater command, so a real effect runs under the mask.
- **C1** (propagation):
  - `redteam/{source}` with `relay_tc {cmd:"HEATER", args:{on:true}}`, so the compromised satellite pushes to its neighbours over feasible ISLs.
  - The defence should contain the source (block_source or quarantine) before the neighbours reach critical.
- `--stop`: clear all red-team effects and send `HEATER off`. Use it for resets.
- Faults F1–F5 and a clean run N go through the same script (`--scenario F1` … `N`), so benign runs can be recorded too.
- `make attack A1 TARGET=E` wraps this.

### Phase 6: Scenarios B and C in software (≈2 h) → part of G5 · owner: Mayssam

- Run each scenario three times with different seeds and targets while recording.
- Pass criteria:
  - **B1:** monitor hit within ≤20 s of onset; untrusted-channel switch; flagged; then contained.
  - **C1:** source contained; no neighbour reaches critical; the alternative path is used.
- Score the recorded episodes with `eval` (S2 makes this possible). Commit only `results/live_manifest.json` with the per-run outcomes, not the episodes.
- Tune `monitor` and `act` tolerances in config only, and log every change in `STATUS.md`.

### Phase 7: Dashboard (≈4 h) → completes G5 · owner: Youssef

`dashboard/`: NASA **Open MCT**, pinned exact version, served by `npm run start`.
- **Telemetry plugin:** connect to `ws://localhost:9001` with `mqtt.js` (pinned) and map `aurora/v1/...` topics to Open MCT telemetry objects (one per satellite/subsystem/field). Historical data comes from an in-memory ring buffer (last 15 min) in the plugin. No database.
- **Views, in priority order:**
  1. **Overview layout** (the demo screen):
     - 12 satellite tiles showing state colour (green, red, off/orange) and an **"EMULATED" badge on every `src: "twin"` satellite**, plus "HW" on the board.
     - The P(F) chart of the focused satellite, with a ±p_std band and the τ line.
     - `temp_c` and `soc_pct` plots.
     - The event log from `core/event`.
     - The last `core/action` with alternatives and J.
  2. The link status table from `link/+`.
  3. Per-satellite plots for `temp_c`, `soc_pct` and `att_err_deg`.
  4. *(If time)* a constellation map with orbits and live links.
- **Robustness:** auto-reconnect; resubscribe after `make reset`; full-screen F11 on a 1080p projector, readable from 5 m (large fonts).
- **Gate G5:**
  - `make demo-sw` + the dashboard.
  - Run A1, B1 and C1 in sequence. Each is visible end to end on the overview.
  - Kill the broker, restart it, and the dashboard recovers.
  - Take screenshots for `STATUS.md`.
  - Stop for approval.

### Phase 8: Hardware joins: the ESP8266 board as satellite B (≈3 h) → G6 · owner: Mayssam

**8a. Import Phase 2 (`prototype/phase2-demo/`)**
- Unzip `phase2_kit.zip`.
- Copy its contents into `prototype/phase2-demo/`: the firmware (`sat8266_main.cpp`, `sat8266.ino`; not the `.bin` if it is over 1 MB, otherwise keep it), the Windows bridge (`esp_link_thermal.ps1`, `esp_link_win.py`), `predictor.py`, the `cassandra-demo/` services (Redis, monitor, attacks), `amica_wiring.png` and `cassandra_demo.pdf`.
- **[HUMAN]** If Mayssam's own `~/cassandra-demo` has newer edits (monitor.py turning `node:sat_b` red and green, the masked attack), ask her to copy that folder over the kit's version.
- Write `prototype/phase2-demo/README.md` with the real run steps from the Phase 2 demo note. This is the **last-resort fallback demo** and must stay runnable on its own.

**8b. Serial ↔ MQTT bridge** (`hardware/esp8266_bridge/bridge.py`, runs on **Windows** Python)
- Dependencies: `pyserial` and `paho-mqtt`.
- Usage: `python bridge.py --port COM9 --sat B --broker localhost`.
- **Serial → MQTT:**
  - Parse `TELEM sim=<°C> heater=<0|1> heap=<n>` (2 Hz). Also accept `temp=` and `raw=`, as the old bridge did.
  - Publish `sat/B/tm/thermal` with `{"temp_c":…, "heater_on":…}`, `src: "hw"`, `seq`, and **`emulated: ["temp_c"]`**. The board has no sensor; the firmware models the temperature: ambient 25 °C, +1.5 °C/s heating, cooling factor 0.03.
  - Also publish a minimal `sat/B/tm/obc` (mode, last origin).
- **MQTT → serial:**
  - `sat/B/tc` HEATER on/off → `H`/`C`.
  - SAFE_MODE → `C`, then ignore HEATER on until `SET_MODE NOMINAL`. The bridge enforces safe mode for this firmware.
  - Log each command's origin.
  - `redteam/B` `freeze_tm` → the bridge itself freezes the reported `temp_c`. That emulates compromised on-board software, so label the run as such.
- **LEDs:**
  - Subscribe to `sat/A/state`, `sat/B/state` and `sat/C/state`.
  - Send `<idx><G|R|O>` with idx 0 for A, 1 for B, 2 for C. nominal → G, flagged → R, quarantined/safe_hold → O (off).
  - The board's 6 LEDs show the states of A, B and C, even though A and C are twin satellites. The dashboard says so.
- Pass `--sats C-L` to the twin plus `--sat-a-twin`, or simply `--sats A,C-L`, so the twin plays everything except B.
- **Config:** `cassandra/config.yaml` marks B as `kind: hw`, with T_max 45 °C (the board's emulated model saturates at 95 °C; the core's T_max applies) and the trajectory forecaster (D7).
- **Honesty:** with one board, the ISL cut on B is logical (dashboard plus core state), not a physical radio cut. Say so in the demo script and on the dashboard.

**8c. [HUMAN] Bring-up**, with exact steps printed by Claude Code:
1. Plug in the board. Check that COM9 shows up in Device Manager.
2. Close the Arduino IDE serial monitor.
3. In Windows PowerShell, run `py -3.11 -m pip install pyserial paho-mqtt`, then `py -3.11 hardware\esp8266_bridge\bridge.py --port COM9 --sat B`.
4. In WSL, run `make up`, `make twin SATS=A,C-L`, `make core`, `make dashboard`.
5. Run `make attack A1 TARGET=B`.
   - Expected: the board's heater flag goes on, `temp_c` climbs, B turns red, the core quarantines B (LED off) with SAFE_MODE (H→C), B cools, and B heals (green).
   - Record the timings from `core/event`.

**8d. Heating curve for the record:** add `bridge.py --log curve.csv` (2 Hz `t, temp_c, heater_on`). **[HUMAN]** Run heater on from ambient to the model ceiling, then off to ambient. This documents the board model. With the ESP8266 it is an emulated curve; say so.

**Gate G6:** a video or screenshots of 8c step 5 and the timing table. Stop for approval.

### Phase 9: Numbers, paper, rehearsal, freeze (Wed 14) → G7 · owners: Youssef (numbers, paper), Mayssam (demo script, preflight, recording)

1. `make evaluate figures numbers` from the fixed pipeline, and update `results/manifest.json`.
2. **[HUMAN]** Ask Mayssam for the submitted Phase 2 paper `.tex` and figures. Put them in `docs/paper/CASSANDRA_paper_updated.tex` and `docs/paper/figs/`, then run `python -m eval.paper_numbers`.
   - Update the text so it matches reality: the oracle fix, the closed-loop number as an upper bound, the benchmark saturation caveat, and that the live board uses the trajectory forecaster and an emulated temperature.
   - Compile with `latexmk -pdf`. Add a "Phase 3 implementation" subsection listing what exists and what is planned. Mark planned items as planned.
3. `docs/DEMO_SCRIPT.md` follows CONTEXT §11, adjusted to D10. Include:
   - Exact commands per terminal.
   - Expected on-screen events with typical timings, taken from Phase 6 and 8 recordings.
   - What to say when something fails.
   - The fallback order: live board → `make demo-sw` → recorded video → `prototype/phase2-demo`.
4. Write `scripts/preflight.sh`: checks Docker, broker, Python, models, `calibration.json`, the dashboard port and free disk, then prints green/red.
5. **[HUMAN]** Record the backup video: a full `make demo-sw` run with A1, B1 and C1, and the board run if G6 passed. Screen capture with OBS, at 1080p.
6. Final pass on `README.md`: the status table matches `STATUS.md`, the real "getting started" commands are listed, and the honesty note is present.
7. **With OK:** open the PR to `main`, Mayssam merges, then tag `v1.0-final`.

**Stretch work, in this order, only after G7 or if the final is later than Oct 14:**
- **H2: ESP32 firmware** (`firmware/`, PlatformIO) per CONTEXT §5.1: DS18B20, INA219, LDR, MOSFET heater, LEDs, emulated battery, ESP-NOW with `ISL_ENABLE`, 55 °C hard cut-off. Only if Mayssam confirms working ESP32 boards.
- Raspberry Pi deployment (`ground/pi/`), plus a forecaster latency measurement against the 150 ms budget with `python -m ml.export`.
- A harder generator (S8): weaker and slower attacks, attacks hidden in normal ops, faults that mimic attacks. Re-run the ablations so the graph and cyber features can show (or fail to show) a benefit.
- Lead-time numbers on SCAD and the FPR on ESA-ADB. Only then can they go on the evidence slide.
- An ONNX export; ECE on at-risk ticks only.

---

## 5. Cut list (in this order, when time runs out)

1. Constellation map in the dashboard (keep tiles + plots + log).
2. Scenario C live (keep it in software; show it on the recording).
3. Monitor rule 2 (sun/eclipse); keep rule 1 (heater vs frozen temp).
4. `isolate_subsystem` and `block_source` candidates (keep none / safe_mode / quarantine).
5. Ensemble on twin satellites (use trajectory everywhere and say so).
6. Open MCT itself. If the plugin fights back for more than 3 h, use a single-page Plotly/uPlot dashboard over WebSocket with the same views.
7. The board (G6). The software demo plus the recorded video is the final, and Phase 2's board video shows hardware.

**Never cut:** gt isolation, honest labelling, the recorded backup, `make demo-sw` working with one command.

---

## 6. `docs/STATUS.md` format

```markdown
# AURORA status. Updated <date time> by <who>

| Component | Path | Owner | State | Evidence |
|---|---|---|---|---|
| Interface contract | docs/interfaces.md, aurora_common/ | Mayssam | done | tests/test_schemas.py green |
| ... | | | missing / partial / done | command or file that proves it |

## Gates
G0 ✅ <date> · G1 ⏳ · ...

## Numbers (each with source file + command)
| Metric | Value | Source |

## Known limitations (said out loud in the demo and paper)
- Closed-loop results are an upper bound (sandbox uses the simulator as its model).
- Benchmark saturates: ablations reach recall 1.00; architecture benefit not yet shown.
- Board temperature is emulated by firmware (no sensor); ISL cut on the board is logical.
- ...

## Changelog
```

---

## 7. Schedule (local time, Africa/Lagos)

| Day | Phases | Gates |
|---|---|---|
| **Sat 10** (afternoon/evening) | 0, 1 | G0, G1 |
| **Sun 11** | 2 (start the pipeline re-run early and let it run while doing 3), 3 | G2, G3 |
| **Mon 12** | 4, start of 5 | G4 |
| **Tue 13** | 5, 6, 7, 8 | G5, G6 |
| **Wed 14** | 9, recording, freeze | G7 |
| After 14 | Stretch list | |

---

## 8. Human-only checklist (Mayssam)

- [ ] Protect `main` (Phase 0)
- [ ] Tell the team she's taking over; Youssef coordinates before touching `ml/` or `eval/` (Phase 0)
- [ ] Phone hotspot ready for downloads (Phase 2)
- [ ] Upload the models release (Phase 2)
- [ ] Copy newer `~/cassandra-demo` edits if they exist (Phase 8)
- [ ] Board bring-up on COM9 and the heating curve run (Phase 8)
- [ ] Give the submitted paper `.tex` and figures (Phase 9)
- [ ] Record the backup video with OBS (Phase 9)
- [ ] Final demo kit: dedicated router or hotspot on a fixed channel, spare board and cables, charged power bank, HDMI adapter, a laptop with Docker running before going on stage
