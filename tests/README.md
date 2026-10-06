# tests/

**Purpose:** Shared/cross-cutting pytest suite (unit tests for `aurora_common`, integration tests spanning `twin/`, `cassandra/`, `eval/`, etc.). Package-local tests may also live alongside their package (e.g. `twin/tests/`) as those packages are built — this top-level folder is for tests that don't belong to a single package, plus message-schema validation tests (Task 2).

**Status:** planned. First tests land with Task 2 (interface/schema validation).

**Owner:** Mayssam Hergli (architecture, CASSANDRA core, red team, integration), shared across the team.

**Interfaces it uses:** validates messages against `docs/interfaces.md` (via `aurora_common/schemas.py`, planned in Task 2); exercises the MQTT contract end-to-end once the ground station and twin v0 exist.
