# attacks/

**Purpose:** The red-team and fault library — parameterised (onset, intensity, target, duration), seeded attack and fault generators used both against the digital twin (for episode generation) and against real hardware (for live demos). Covers attacks A1–A3, B1–B2, C1, faults F1–F5, and clean/noise episodes N (see `docs/CONTEXT.md` Section 5.7).

**Status:** planned. Attack A1 (heater latch) and fault F1 are first priority, alongside the episode generator (Task 5). On hardware, A1/B1/C1 are executed as real commands to the ESP32 nodes (Task 8+).

**Owner:** Mayssam Hergli (red team), with hardware owner for on-hardware execution (TBD).

**Interfaces it uses:**
- Issues telecommands on `aurora/v1/sat/{id}/tc` (as the `attacker` origin, or via `isl:<peer_id>` for C1 propagation) to drive twin or hardware state.
- Publishes ground truth to `aurora/v1/gt/episode` and `aurora/v1/gt/event` — **recorder only**; `cassandra/` must never subscribe to these topics.
