# ml/

**Purpose:** The learned forecaster — a temporal graph network (relation-aware message passing + GRU memory per node) that outputs P(F < t + Δ) for Δ ∈ {10, 30, 60, 120} s, next-affected-subsystem, and malicious-vs-fault probability. Includes training, calibration (temperature scaling, ECE), uncertainty (ensemble), baselines (static thresholds, LSTM autoencoder, trajectory forecaster, GDN, E_eng ablation), and export to TorchScript/ONNX.

**Status:** planned (later task, after the twin v1 and episode generator produce enough training data).

**Owner:** TBD (learned forecaster, calibration, baselines).

**Interfaces it uses:**
- Trains on episodes produced by `twin/`'s episode generator (`episodes/{episode_id}/...`), with optional transfer/pre-training on the SCAD dataset (`eval/` loader).
- Exported model is loaded by `cassandra/forecast`, which publishes to `aurora/v1/core/forecast/{id}` per `docs/interfaces.md`.
- Training uses ground-truth labels (`labels.json`) directly — that is expected and does not violate Rule 7, since training happens offline, not inside the live CASSANDRA defence loop reading `aurora/v1/gt/...` at runtime.
