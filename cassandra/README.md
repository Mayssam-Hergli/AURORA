# cassandra/

**Purpose:** The CASSANDRA defence core — the Observe → Understand → Forecast → Simulate → Act → Verify loop. Modules: `ingest`, `graph`, `monitor`, `forecast`, `sandbox`, `safety`, `executor`, `verify`, `explain` (see `docs/CONTEXT.md` Section 5.4 for each module's responsibility).

**Status:** planned (Task 6 for the skeleton — ingest, graph, forecast with the Phase 2 trajectory forecaster as default, safety, executor, explain, wired to MQTT).

**Owner:** Mayssam Hergli (architecture, CASSANDRA core, red team, integration).

**Interfaces it uses:**
- Subscribes to `aurora/v1/sat/{id}/tm/{sub}`, `aurora/v1/sat/{id}/tc`, `aurora/v1/link/{a}-{b}`.
- Publishes `aurora/v1/sat/{id}/state`, `aurora/v1/core/forecast/{id}`, `aurora/v1/core/action`, `aurora/v1/core/event`.
- **Must never subscribe to `aurora/v1/gt/episode` or `aurora/v1/gt/event`** (ground truth, recorder only — Rule 7 in `docs/CONTEXT.md` Section 0).
- Calls into `twin/`'s sandbox API for counterfactual simulation of candidate actions.
