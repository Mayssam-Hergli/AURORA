# twin/

**Purpose:** The digital twin — subsystem models (THERMAL, EPS, ADCS, OBC, COMMS, SUN), the orbit/geometry engine, the episode generator, and the counterfactual sandbox API. Three jobs: (1) scale the constellation to 12 satellites (3 physical + 9 simulated), (2) act as a data factory generating labelled attack/fault/clean episodes at ~100× real time, (3) serve as the sandbox CASSANDRA clones to evaluate candidate responses (J(a)).

**Status:** planned (Task 4 for v0 — one satellite, thermal + EPS; Task 5 for the episode generator; later tasks for v1 — 12 satellites, orbits, ADCS, ISL geometry).

**Owner:** TBD (digital twin, episode generator, evaluation harness).

**Interfaces it uses:**
- Publishes telemetry/state/link messages identical in schema to hardware, but with `src: "twin"` (see `docs/interfaces.md` Section 6.2).
- Accepts telecommands the same way hardware nodes do.
- Exposes a Python sandbox API: `twin.snapshot()`, `twin.restore(s)`, `twin.apply(command)`, `twin.step(n)`, `twin.run_episode(spec) -> Episode`, used by `cassandra/sandbox`.
- Episode generator writes `episodes/{episode_id}/telemetry.parquet`, `commands.parquet`, `links.parquet`, `labels.json` per `docs/interfaces.md` Section 6.3. `labels.json` (ground truth) is for the evaluation harness/recorder only — never read by `cassandra/`.
