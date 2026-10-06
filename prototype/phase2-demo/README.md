# prototype/phase2-demo/

**Purpose:** The working Phase 2 demonstrator — the ESP8266 firmware (`sat8266_main.cpp`), the PowerShell↔Redis bridge (`esp_link_thermal.ps1`), the Python services (`predictor.py`, `monitor.py`), and the attack scripts (`attack_thermal.py`, `attack_masked_thermal.py`, `attack_redis_inject.py`) that produced the measured Scenario A run in the interim paper (flagged at 2.3 s, quarantined at 18.8 s, healed at 27 s — see `docs/CONTEXT.md` Section 4.1).

**Status:** done (already built and demonstrated per the interim paper) — **but the source files are not yet present in this repository.** This folder currently holds only this README as a placeholder. The actual Phase 2 code (per the gotchas in `docs/CONTEXT.md` Section 10, item 7, it was run from `~/cassandra-demo/venv/...` in WSL) needs to be imported from wherever it currently lives before "getting started" instructions here can be verified as real and runnable.

**Owner:** Mayssam Hergli.

**Interfaces it uses:** Phase 2's own ad hoc interface (Redis keys: `telemetry:temp`, `truth:temp`, `hw:heater`, `attack:thermal`, `node:sat_a|b|c`) — **not** the Phase 3 `aurora/v1/...` MQTT contract in `docs/interfaces.md`. This prototype is kept as a reference and fallback; it is not wired into the Phase 3 stack.
