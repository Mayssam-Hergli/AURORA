# CASSANDRA ML guide (v1)

This guide explains the learned forecaster end to end, for the team and for reviewers. It covers what goes in, what comes out, how it is trained and evaluated, and how to plug in the twin and the hardware. Section numbers such as "Section 6.2" refer to `AURORA_CONTEXT`. Owner: Youssef Mhamdi.

**Rule that runs through everything:** the forecaster sees only what the defence would see live:
- telemetry;
- telecommands with their origin;
- link status.

Ground truth (`labels.json`, `ml_labels.json`, `aurora/v1/gt/...`) is read only by training and evaluation, offline. The tests `test_messages_only_travel_along_permitted_edges` and `test_online_frames_equal_offline_features` protect it.

---

## 1. Data contract

### 1.1 Inputs (Section 6.2 messages)

| Topic | Fields used | Per-second channel(s) in [`ml/graph.py`](../ml/graph.py) |
|---|---|---|
| `aurora/v1/sat/{id}/tm/{sub}` | `temp_c`, `heater_on`, `soc_pct`, `bus_v`, `load_w`, `solar_w`, `current_ma`, `att_err_deg`, `wheel_rpm[3]`, `rate_dps`, `mode`, `cmd_ok`, `cmd_rej`, `rssi_dbm`, `sun_level`, `eclipse` | THERMAL, EPS, ADCS, OBC, COMMS, SUN channels |
| `aurora/v1/sat/{id}/tc` | `cmd`, `origin` (`ground`, `isl:X`, `unknown`) | `tc_ground`, `tc_isl`, `tc_unknown`, `tc_heater_on`, `tc_set_mode`, `tc_ping` on OBC |
| `aurora/v1/link/...` | per-pair link up/down | E_ISL(t) mask (66 pairs for 12 satellites) and `n_isl_peers` |

[`ml/online.py`](../ml/online.py) (`FrameBuilder`) turns the message stream into one frame per second: 27 channels per satellite plus 66 link states. [`ml/features.py`](../ml/features.py) (`episode_arrays`) builds the same frames offline from an episode folder. The two agree to a relative difference of 2.5e-4, which the tests check.

### 1.2 Output (Section 6.2 forecast message)

`aurora/v1/core/forecast/{sat}`, built by `schema.forecast_message`:

```json
{"v": 1, "sat": "B", "t": 1760000123.0, "model": "tgn-v1",
 "p_fail": {"10": 0.02, "30": 0.11, "60": 0.43, "120": 0.71},
 "p_std":  {"10": 0.01, "30": 0.03, "60": 0.06, "120": 0.05},
 "next_sub": "EPS", "p_malicious": 0.93}
```

`p_fail` is calibrated and monotone in the horizon. `p_std` is the ensemble standard deviation, the Q of the uncertainty gate.

### 1.3 Episodes (Section 6.3) and the ML extension

An episode folder holds:
- `telemetry.parquet`;
- `commands.parquet`;
- `links.parquet`;
- `labels.json` (Section 6.3);
- **`ml_labels.json`** (proposed extension).

The extension adds per-satellite truth: `onset_t`, `critical_t`, `critical_rule`, `malicious`, `affected_t` (first affected time per subsystem, from a counterfactual run) and `reported_masked`. Section 6.3 has one `critical_t` per episode; a constellation needs one per satellite, which is why the extension exists. It is a proposal for `docs/interfaces.md`, not yet applied.

## 2. Targets

[`ml/dataset.py`](../ml/dataset.py) defines the targets. For a window ending at tick t and satellite s, with critical tick c:

| Target | Definition | Masking |
|---|---|---|
| hazard y_Δ | 1 if t < c ≤ t + Δ, for Δ ∈ {10, 30, 60, 120} s | Masked after failure. **Right-censored** when the satellite is at risk but the episode ends before t + Δ without failing, because its future is unknown. |
| next subsystem | the next subsystem whose `affected_t` is after t | Defined once the anomaly has started |
| malicious | 1 for an attack, 0 for a natural fault | Defined once the anomaly has started |

Training samples half of its windows uniformly and half around the anomaly, from 30 s before onset to just after the last failure.

## 3. Model

[`ml/model.py`](../ml/model.py) builds a forecaster of 42.7k parameters. One forward pass maps 30 s of frames to all heads.

1. **Prep (inside the module).** Normalise each channel with training statistics. Add first differences. For every E_eng edge (i → j), compute two residuals against the ridge engineering model g_ij fitted on nominal data (`Understanding`):
   - the **physical residual** ΔΦ: the observed change of j minus the change g_ij predicts from i;
   - the **level residual**: the observed level of j minus the level predicted from i.

   Doing this in the module means the same code serves training, online use, TorchScript export and PGD.
2. **Temporal encoder.** A GRU per node over the window. A small GRU per E_eng edge summarises that edge's residual sequence.
3. **Graph.** Three rounds of relation-aware message passing: one weight matrix per relation (R-GCN) and attention over incoming edges (GAT).
   - Relations: `eng`, `sparta`, `agg` (subsystem → SAT) and `isl`.
   - ISL edges are multiplied by the live link mask, so a message crosses only a feasible, enabled link.
   - Learned couplings exist only as attributes of permitted edges.
4. **Heads.**
   - **Hazard:** one logit per horizon bin. P(F < t+Δ_k) = 1 − Π_{j≤k}(1 − σ(b_j)), monotone by construction.
   - **Next subsystem:** softmax over 6 subsystems.
   - **Malicious vs fault:** sigmoid.

| Ablation | What changes |
|---|---|
| `no_eng` | E_eng removed, so no residuals on engineering edges and no engineering messages |
| `unconstrained` | every intra-satellite pair is an edge; the physics constraint is lifted |
| `no_residual` | ΔΦ and the level residual are switched off |

## 4. Training and calibration

[`ml/train.py`](../ml/train.py) sets up training as follows:
- **Loss.** BCE on the hazard bins (positive weight 3, censoring mask), plus 0.3 × cross-entropy for the next subsystem, plus 0.3 × BCE for malicious.
- **Optimiser.** Adam, lr 2e-3, 8 epochs, early stopping on validation loss (patience 3), 3 threads per job.
- **Ensemble and ablations.** Five seeds form the ensemble; the three ablations use seed 0. Models are saved as `data/models/{variant}_s{seed}.pt`, with a JSON history beside each.
- **Calibration.** One temperature on the ensemble-mean logits, fitted on validation windows (`ml/calibration.py`). ECE uses 10 equal-width bins per horizon.

Best validation losses this run:

| Model | Best validation loss |
|---|---|
| full, 5 seeds | 0.187–0.196 |
| `no_eng` | 0.267 |
| `unconstrained` | 0.198 |
| `no_residual` | 0.176 (lower than the full model; see the ablation results) |

Commands:
```bash
python -m ml.train understanding          # normaliser + engineering models -> data/models/understanding.*
python -m ml.train job full 0             # one model
python -m ml.train all --parallel 1       # everything that is missing (resumable)
```

## 5. Evaluation

[`eval/harness.py`](../eval/harness.py) runs every model and baseline on every split. It saves compact predictions and writes `results/eval/metrics.json` and `RESULTS.md`. The definitions are in [`eval/metrics.py`](../eval/metrics.py):

- **τ.** For each method, the 99.9 % quantile of its scores on validation benign timesteps (clean and fault episodes, satellites that never reach F), so τ sits at 0.1 % FPR. The same τ is applied to every test split, and the realised FPR and false alarms per satellite-hour are reported.
- **Lead time.**
  - T_alert is the first tick at or after the episode onset with score ≥ τ.
  - An alert at or after T_critical is a miss.
  - T_lead = T_critical − T_alert, reported as the median and IQR over detected satellites, with recall and a 95 % bootstrap CI over episodes.
- **Two operating points.** Matched FPR is the comparison point. `ensemble@act` is the response engine's own action threshold (P ≥ 0.85), so the paper can say how early the system *acts*, not only how early it *warns*.
- **Ranking and heads.**
  - AUROC and AUPRC of P(F < t + 60 s) on satellite-ticks with a defined target;
  - ECE per horizon;
  - next-subsystem top-1 and top-3;
  - malicious-vs-fault AUROC.
- **Breakdowns.** By type (A1 … F5), by scenario (A/B/C/F) and by precursor (burst, sparse, none).
- **Evaluation ticks.** Every 6 s everywhere, an unbiased sample for FPR, and every 2 s from onset to the last failure, where lead time is measured. Ground truth only schedules when the model is called; it never enters its inputs.

[`eval/closed_loop.py`](../eval/closed_loop.py) runs the closed loop. It re-runs test episodes on the twin with CASSANDRA acting. At each decision the sandbox snapshots the twin, rolls each candidate action 30 s forward and scores J(a). It applies the action with the lowest J. If that action has Q > 0.35, the satellite goes to safe mode instead (a safe hold for ground review). `quarantine_reroute` is offered only while quarantines in the current orbit stay within 25 % of the constellation. A satellite is restored after 10 consecutive steps below the flag threshold (0.30). It reports failures averted, new failures, false containments per orbit, availability and planning time. The sandbox uses the simulator as its model, so these numbers are an upper bound.

[`eval/robustness.py`](../eval/robustness.py) runs PGD on the forgeable channels (thermal, EPS and ADCS housekeeping, but not `heater_on`, telecommands, the sun sensor or links). The ε levels are 0, 0.25, 0.5 and 1 nominal standard deviation, with the attacker trying to suppress P(F < t + 60 s). It reports recall, lead and the mean Q under attack, and the same budget applied to static thresholds.

## 6. Deployment

- **Get the models.** `make models` downloads the ensemble, `understanding.*` and `calibration.json` (the validation temperature and alert thresholds) from the `models-v1` GitHub release into `data/models/`, SHA-256 verified. The release asset is built with `python -m ml.export --bundle v1`; a new bundle needs a new tag and hash in `eval/datasets/download.py`.
- **Export.** `python -m ml.export` traces each ensemble member to TorchScript (`data/models/ts/`), checks the traced output against eager mode, and writes `results/latency.json` (median and p95 per tick, 1 and 4 threads).
- **On the Raspberry Pi.** Clone the repository, run `make models`, then `python -m ml.export` there. It exports and times the same TorchScript ensemble, and that measurement replaces the x86 one in the paper. `--bench-only` times the eager models instead.
- **Live.** `OnlineForecaster.from_models([...])` exposes `on_telemetry`, `on_command` and `on_link` for each message, then `tick(t)`. It publishes one forecast message per satellite per second. If no model is available it warns and falls back to the Phase-2 trajectory forecaster, so the core never runs blind. The fallback ignores a limit until its channel has arrived for a full 8-sample slope window, so a board that skips or delays a subsystem does not raise a false alarm.

## 7. Extending

| To … | Do this |
|---|---|
| add an attack or fault type | add it to `ml/synthetic/scenarios.py` (sampling) and `ml/synthetic/engine.py` (effect and counterfactual), then add it to the `mix` in `ml/configs/synthetic.yaml`. The affected-before-onset test will tell you if the labels are wrong. |
| add a telemetry field | add it to `CHANNELS` in `ml/graph.py` and to `FIELDS` in `ml/schema.py`, then rebuild caches and retrain. This is an interface change: update `docs/interfaces.md` in the same commit. |
| use twin v1 episodes | point `config.EPISODES` at the recorder output (Section 6.3 plus `ml_labels.json`), run `python -m ml.features`, then `python -m eval.harness run`. No retraining is needed to *evaluate* transfer, and that is the honest test. |
| change the decision weights | `response` block of `eval/configs/eval.yaml`; the gates are `theta_uncert` and `isolation_budget` |

## 8. Known gaps (honest list)

- Results come from simulation; nothing here is a flight or hardware measurement.
- ESA-ADB is not evaluated yet, and SCAD is used for propagation profiling only (3.3 % of its attack runs reach F).
- Latency is measured on x86; the Raspberry Pi measurement is open.
- The closed loop uses the simulator as the sandbox model, so it is an upper bound.
- The interface proposals (`ml_labels.json`, `SET_MODE` values, Q as standard deviation, SPARTA ID fixes) need team approval before `docs/interfaces.md` changes.
