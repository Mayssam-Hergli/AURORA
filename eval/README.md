# eval/

**Purpose:** The evaluation harness — runs N episodes (twin-generated or external datasets) through the CASSANDRA pipeline and writes a results folder with tables and plots for the paper. Implements the metrics in `docs/CONTEXT.md` Section 5.8: alert time, warning lead time, detection delay, precision/recall, next-affected-subsystem accuracy, expected calibration error, response effectiveness, and per-tick latency vs the 150 ms budget. Also hosts dataset loaders for SCAD, ESA-ADB, and CuCD-ID.

**Status:** planned (Task 7 for v0 — threshold and trajectory baselines + SCAD loader).

**Owner:** TBD (digital twin, episode generator, evaluation harness).

**Interfaces it uses:**
- Reads episodes from `twin/`'s episode generator output (`episodes/{episode_id}/...`) and from `data/` (external datasets, downloaded by scripts, never committed).
- Reads `labels.json` ground truth directly (it is the evaluation recorder's job — this is the one place ground truth is allowed to be read alongside CASSANDRA's output).
- Drives `cassandra/` end-to-end per episode to measure its outputs on `aurora/v1/core/forecast/{id}` and `aurora/v1/core/action`.
