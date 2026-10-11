# ml/

**Purpose:** The learned forecaster: a temporal graph network (GRU memory per node over a 30 s window + relation-aware message passing over E_eng, E_SPARTA, aggregation and E_ISL(t) edges) that outputs P(F < t + Δ) for Δ ∈ {10, 30, 60, 120} s, the next affected subsystem and P(malicious | anomaly). Includes training (5-model ensemble + ablations), calibration (temperature scaling, ECE), the online forecaster for `cassandra/forecast`, TorchScript export, the four baselines (static thresholds, Phase-2 trajectory forecaster, LSTM autoencoder, GDN), and a synthetic episode generator that stands in for `twin/` until twin v1 exists.

**Status:** done (v1), evaluated in simulation — see `docs/ML_GUIDE.md` and `results/eval/RESULTS.md`.

**Owner:** Youssef Mhamdi (learned forecaster, calibration, baselines, data).

| Module | What it does |
|---|---|
| `schema.py` | Section 6.2 fields, forecast message, Section 6.3 episode read/write (+ `ml_labels.json` extension) |
| `synthetic/` | 12-satellite twin stand-in: geometry, physics, attacks A1–C1, faults F1–F5, sandbox API, generator |
| `graph.py` | node set (THERMAL, EPS, ADCS, OBC, COMMS, SUN, SAT), edge families and ablation variants |
| `features.py` | episode → per-tick arrays (27 channels/satellite), normaliser, engineering model g_ij, couplings |
| `model.py` | the forecaster (preprocessing inside the module: same code for training, online use, export, PGD) |
| `dataset.py`, `train.py` | windows + targets (with censoring), ensemble/ablation training |
| `calibration.py`, `baselines.py` | temperature scaling + ECE; baselines |
| `online.py` | `OnlineForecaster`: Section 6.2 messages in → Section 6.2 forecast messages out |
| `export.py` | TorchScript export + latency benchmark (run it on the Raspberry Pi) |

```bash
make models                          # trained ensemble from the GitHub release (enough to run the core)
python -m ml.synthetic.generate      # episodes -> data/episodes/<split>/
python -m ml.features                # caches -> data/cache/
python -m ml.train all --parallel 1  # models -> data/models/ (gitignored)
python -m ml.export                  # TorchScript + results/latency.json
python -m ml.export --bundle v1      # dist/aurora-models-v1.zip, the release asset
```

**Interfaces it uses:**
- Reads episodes in the Section 6.3 format (from `ml.synthetic` today, from `twin/` and the recorder later).
- `OnlineForecaster` is what `cassandra/forecast` calls; it publishes to `aurora/v1/core/forecast/{id}` per Section 6.2.
- Training reads `labels.json` / `ml_labels.json` offline (allowed: not the live loop; Rule 7 holds — the model inputs are telemetry, telecommands and link status only).
