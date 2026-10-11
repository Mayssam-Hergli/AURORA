# hardware/

**Purpose:** connect real boards to the AURORA bus. Today: the ESP8266 NodeMCU from Phase 2 plays satellite B through a Windows serial ↔ MQTT bridge (MASTER_PLAN D10). ESP32 firmware with ESP-NOW (`firmware/`) is a stretch goal.

**Owner:** Mayssam Hergli. **Status:** bridge done and tested with a fake board (`--dry-run`); real-board bring-up: see [BRINGUP.md](BRINGUP.md).

| Path | What |
|---|---|
| `esp8266_bridge/bridge.py` | serial `TELEM sim=… heater=…` ↔ `aurora/v1/sat/B/tm/thermal` (src `hw`, `emulated: ["temp_c"]`) and `tm/obc`; `sat/B/tc` → `H`/`C` with bridge-enforced SAFE (interfaces.md 3.2); `sat/{A,B,C}/state` → LEDs `1/2/3` + `G/R/O`; `redteam/B` freeze/bias of the reported temperature; `--log curve.csv`; `--dry-run` fake board |
| `BRINGUP.md` | morning steps, expected output, troubleshooting |

Dependencies on Windows: `pyserial`, `paho-mqtt` (the `.venv-win` venv at the repo root). Tests: `tests/hardware/`, `tests/integration/test_board_dry_run.py`.
