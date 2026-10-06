# firmware/

**Purpose:** ESP32 satellite node firmware (PlatformIO project) for the 3 physical nodes (A, B, C) in the Phase 3 architecture. Reads real sensors (DS18B20 temperature, INA219 current/voltage, LDR/BH1750 light), drives the heater MOSFET and status LEDs, talks WiFi/MQTT to the ground station and ESP-NOW to neighbouring satellites.

**Status:** planned (Task 8). The working Phase 2 firmware (ESP8266, serial-based) lives in `prototype/phase2-demo/` and is not reused directly — Phase 3 firmware is a new PlatformIO project targeting ESP32.

**Owner:** TBD (hardware nodes, firmware, ESP-NOW links, Raspberry Pi).

**Interfaces it uses:**
- Publishes telemetry to `aurora/v1/sat/{id}/tm/{sub}` (`src: "hw"`), per `docs/interfaces.md`.
- Subscribes to telecommands on `aurora/v1/sat/{id}/tc` (origin `ground`, `core`, or `isl:<peer_id>`).
- Publishes node state is owned by the core, not firmware — firmware only reports telemetry and obeys commands.
- ESP-NOW for inter-satellite links (not MQTT), gated by `ISL_ENABLE` commands.

See `docs/CONTEXT.md` Section 5.1 for the bill of materials, pin map, and physical safety cut-off (55 °C hard limit in firmware, regardless of commands).
