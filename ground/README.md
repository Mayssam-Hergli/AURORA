# ground/

**Purpose:** Raspberry Pi ground-station setup: Mosquitto MQTT broker configuration, `docker-compose.yml` (or systemd units) to run the broker, digital twin, CASSANDRA core, recorder and the MQTT↔WebSocket bridge for the dashboard. One command starts everything; one command resets.

**Status:** planned (Task 3 for the Mosquitto bring-up and `mqtt_tail.py` monitor tool; later tasks add the full compose stack).

**Owner:** TBD (hardware nodes, firmware, ESP-NOW links, Raspberry Pi).

**Interfaces it uses:**
- Hosts the Mosquitto broker that carries every topic under `aurora/v1/...` (see `docs/interfaces.md`).
- `tools/mqtt_tail.py` (planned) subscribes to all `aurora/v1/#` topics for debugging/echo, excluding ground-truth handling beyond passive display.
