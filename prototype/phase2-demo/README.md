# prototype/phase2-demo/

**Purpose:** the working Phase 2 demonstrator, kept runnable on its own as the **last-resort fallback demo**: the ESP8266 plays satellite B, a Windows bridge talks to a Redis bus in WSL, and the Phase 2 predictor forecasts, contains and heals. It uses Phase 2's own Redis keys, **not** the Phase 3 MQTT contract (`docs/interfaces.md`), and it is not wired into the Phase 3 stack.

**Status:** imported from `phase2_kit.zip` on 2026-10-12. **Owner:** Mayssam Hergli.

Measured run (interim paper, Table II): flagged 2.3 s after the attack (28.5 °C), quarantined at 18.8 s (53.3 °C), healed at 27 s; limit 60 °C never reached. **What is emulated:** B's temperature is computed by the firmware (no sensor); A and C are LEDs only; the forecaster is a heuristic, not a calibrated probability.

## Contents

| Path | What |
|---|---|
| `firmware/sat8266/sat8266.ino` + `sat8266_main.cpp` | Arduino sketch for the NodeMCU Amica (ESP8266). Both files must stay in the `sat8266/` folder: Arduino IDE compiles only files inside the sketch folder (this was the "missing file"). |
| `firmware/sat_thermal.bin` | the compiled image, to flash if the IDE fails (`esptool.py --port COM9 write_flash 0x0 sat_thermal.bin`) |
| `bridge_windows/esp_link_thermal.ps1` | the Phase 2 bridge (pure PowerShell, no installs): serial COM9 ↔ Redis `localhost:6379` |
| `bridge_windows/esp_link_win.py` | older Python bridge for the ESP32 chip-temperature variant (archive only) |
| `services/predictor.py` | Phase 2 predictor (trajectory forecaster, flag 0.30 / quarantine 0.85 on the 30 s horizon, heal < 30 °C for 3 s) |
| `services/cassandra-demo/` | Redis compose file, `reset.sh`, the Scenario B `monitor.py`, `telemetry_pub.py`, `attacks/` |
| `services/cassandra-demo/attacks/attack_thermal.py` | **reconstructed** (not in the kit): sets `attack:thermal` to 1, or 0 with `stop` |
| `docs/amica_wiring.png`, `docs/cassandra_demo.pdf` | wiring diagram and the demonstrator technical note |

Board pins (firmware): SatA green D1 / red D2, SatB green D5 / red D6, SatC green D7 / red D0; each LED pair shares one 150 Ω resistor to G; D3, D4, D8 left free (boot pins). Serial 115200: out `TELEM sim=<°C> heater=<0|1> heap=<n>` every 500 ms; in `H` / `C` (heater on / off) and `<idx><G|R|O>` with idx 1 = A, 2 = B, 3 = C.

## Run it (from the demonstrator note, Section VII)

The Phase 2 Redis container is `sat_b_redis` on port 6379. The Phase 3 broker uses 1883 / 9001, so both can run at once.

1. **WSL:** `cd prototype/phase2-demo/services/cassandra-demo && bash reset.sh` (clean Redis).
2. **WSL, T1:** `python3 cassandra/monitor.py`, needs `pip install -r requirements.txt` (redis, docker).
3. **WSL, T2:** `python3 ../predictor.py`
4. **Windows PowerShell**, in `prototype\phase2-demo\bridge_windows`: `powershell -ExecutionPolicy Bypass -File .\esp_link_thermal.ps1 -Port COM9`. All three satellites light green.
5. **WSL, T3:** `python3 attacks/attack_thermal.py`. Expect: B red at about 28 °C, LEDs off at about 53 °C (quarantine + safe mode), green again below 30 °C.

**Reset between runs:** Ctrl+C the attacker, then `python3 attacks/attack_thermal.py stop`. The predictor re-arms itself.
**Checks:** if the bridge prints nothing, close anything holding COM9 (Arduino IDE Serial Monitor). An LED that stays dark is reversed; red instead of green means swap that satellite's two jumpers.

## Footnote (honesty)
`services/predictor.py` prints `P(fail<30s)=… +/-0.05`. The `+/-0.05` was a fixed display string, **not a computed uncertainty**. The Phase 3 core never prints an uncertainty it did not compute (MASTER_PLAN D4).
