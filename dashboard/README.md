# dashboard/

**Purpose:** The operator dashboard, built on NASA Open MCT (Node.js). Views: constellation map (orbits, live links, node colours Green/Red/Off), per-satellite telemetry plots, P(F) curves with confidence band and threshold line, event log from `cassandra/explain`, link status table, and an "emulated" badge on simulated satellites. Must run full-screen on a projector and survive reconnects.

**Status:** planned (later task, dashboard v0 targeted for live telemetry once the ground station and twin v0/v1 are up).

**Owner:** TBD (digital twin, episode generator, evaluation harness — or reassigned at dashboard v0).

**Interfaces it uses:**
- Consumes all `aurora/v1/...` topics via an MQTT → WebSocket bridge (hosted in `ground/`), per `docs/interfaces.md`.
- Never consumes `aurora/v1/gt/...` directly in its live view, except where explicitly labelling emulated/ground-truth context for operator honesty (e.g. the "emulated" badge), consistent with Rule 2 (be honest about what is emulated).
