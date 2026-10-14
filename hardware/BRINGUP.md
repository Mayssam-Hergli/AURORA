# ESP8266 board bring-up: satellite B on the AURORA bus

The NodeMCU Amica running the Phase 2 firmware plays **satellite B** (`src: "hw"`, temperature **emulated** by the firmware). The 11 other satellites come from the twin stand-in (`scripts/replay_twin.py --sats A,C-L`). The board's 6 LEDs show the states of A, B and C. With one board, cutting B's inter-satellite links is **logical** (core state + dashboard), not a radio cut. Say so in the demo.

## Before you start (once)
- The board is flashed with `prototype/phase2-demo/firmware/sat8266/` (it already is from Phase 2). To reflash: Arduino IDE, open `sat8266/sat8266.ino` (keep `sat8266_main.cpp` in the same folder), board "NodeMCU 1.0 (ESP-12E)", 115200. Or flash `firmware/sat_thermal.bin` with esptool.
- Wiring: `prototype/phase2-demo/docs/amica_wiring.png`. SatA D1/D2, SatB D5/D6, SatC D7/D0 (green/red); D3, D4, D8 free.
- Windows venv for the bridge: **already created overnight** as `.venv-win` (Python 3.12, pyserial 3.5, paho-mqtt 2.1.0). If it is missing: `py -3 -m venv .venv-win` then `.\.venv-win\Scripts\python.exe -m pip install pyserial "paho-mqtt>=2.1"`.
- Close the Arduino IDE Serial Monitor (it holds COM9).

## The 5 commands

| # | Where | Command |
|---|---|---|
| 1 | WSL (Ubuntu) terminal 1 | `cd /mnt/c/Users/USER/AURORA && make up` |
| 2 | WSL terminal 1 (leave it running) | `make demo-sw SATS=A,C-L` |
| 3 | Windows PowerShell | `cd C:\Users\USER\AURORA; .\.venv-win\Scripts\python.exe hardware\esp8266_bridge\bridge.py --port COM9 --sat B --log curve.csv` |
| 4 | WSL terminal 2 | `cd /mnt/c/Users/USER/AURORA && make attack A1 TARGET=B` |
| 5 | WSL terminal 2 (reset between runs) | `make attack-stop TARGET=B` |

Watch the bus at any time: `make tail TAIL_ARGS="--filter 'aurora/v1/sat/B/#'"` or the core's lines in terminal 1.

### What you should see
1. Bridge: `[bridge] connected to localhost:1883; satellite B; LEDs A=1 B=2 C=3`, then one status line per second, about 25 °C, all LEDs green.
2. Core, at start: `forecaster: trajectory-v0 for all satellites (…); trajectory-v0 for hardware B (D7)`.
3. After command 4. Measured in tonight's dry run with the fake board (simulated; the real board should match because it runs the same model):
   - the bridge prints `TC HEATER {'on': True} from ground: accepted`; the blue LED comes on and the temperature climbs at 1.5 °C/s;
   - **about 2 s later** the core prints `[B] nominal -> flagged`, then the decision line, e.g. `P(F<60s)=0.58 … cause: heater latched ON (origin=ground, 2 s ago) … T_max 60 in ~25 s · SPARTA IA-0009 · action: isolate_subsystem (J=0.07)`;
   - the bridge prints `TC HEATER {'on': False} from core: accepted`, B's LED turns **red**, and the temperature falls;
   - a few seconds later: `[B] healed: temp_c 26.2 C …` and B is green again.
   **Real board, 14 Oct** (`python scripts/board_a1.py`, `results/board_a1.json`): flagged 5.9 s, heater cut 21.9 s at 54.4 °C peak (limit 60), healed 30.0 s after the attack command; the dry-run rehearsal gave 5.4–6.1 / 21.4–22.1 / 30.4–31.1 s. Timings are bus-side; the board's LEDs were **not** seen to change in that run (open item below).
   The action is whatever J picks: `isolate_subsystem` for a single latched command. **Say "contains", not "quarantines".**
4. With re-assertion (`make attack A1 TARGET=B ATTACK_ARGS="--reassert 5 --duration 60"`), J escalates beyond isolation (safe mode: B's LEDs **off**). In SAFE the bridge refuses `HEATER on` from any origin, the core prints "holding containment …" while the commands keep coming, and B heals after command 5.

### Heating curve for the record (step 8d)
Run **only the bridge** (no `make demo-sw`, or the core will contain it): command 3 with `--log curve_full.csv`, press **FLASH** once (the heater latches; the emulated temperature rises to the firmware's 95 °C ceiling), wait about 50 s, press FLASH again, and wait until it is back near 25 °C. `curve_full.csv` has `t_unix, temp_true_c, temp_reported_c, heater_on, mode` at 2 Hz. It documents the **firmware's emulated model**, not a sensor.

## Rehearse without the board
`make demo-board-dry` (WSL) runs the twin (A, C–L), the core and the bridge with a fake board that runs the firmware's model. Then `make attack A1 TARGET=B`. The test `tests/integration/test_board_dry_run.py` does exactly this.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Bridge: `could not open port 'COM9'` | Device Manager → Ports: check the COM number (`--port COMx`). CP2102 boards need the Silicon Labs CP210x driver. Close the Arduino IDE Serial Monitor; only one program can hold the port. |
| Bridge prints `temp=-C` forever | No TELEM lines: wrong port or baud (115200), or the board is resetting. Unplug and replug, wait 2 s after opening. |
| Bridge never prints `connected to localhost:1883` | The broker is not running in WSL. **WSL2 shuts its VM down when no WSL program is running**, and Docker stops with it (seen tonight). Keep WSL terminal 1 running (`make demo-sw …`), then `make up`. If `localhost` still fails from Windows, use the WSL address: run `hostname -I` in WSL and pass `--broker <that IP>`. |
| Core warns `[B] telemetry src='twin' but this satellite is configured as 'hw'` | The twin is still publishing B: use `make demo-sw SATS=A,C-L` (not the default A–L). |
| Core warns `hardware clock is +x.xx s off the twin clock` | Windows and WSL clocks disagree by more than 0.5 s. Windows: `w32tm /resync` (admin). WSL: `sudo hwclock -s`. |
| Core warns `[B] thermal telemetry stale` | The bridge stopped or the port dropped: restart command 3. |
| A LED stays dark | That LED is reversed (long leg to the pin). Red instead of green: swap that satellite's two jumpers. |
| Heater turns back on in SAFE | Someone pressed FLASH: the bridge switches it off again within 1 s (`SAFE: … heater forced OFF`). |
| Bridge prints `temp=-C`; after a reset the board prints something other than `TELEM …` (e.g. "Focus Station ready") | The board runs another program. Back it up, then flash ours: `.\.venv-win\Scripts\python.exe -m esptool --port COM9 read-flash 0 0x400000 backup.bin`, then `… write-flash 0x0 prototype\phase2-demoirmware\sat_thermal.bin` (needs `pip install esptool` in `.venv-win`). |
| Heater turns on by itself when a program opens COM9 | DTR is wired to GPIO0 (FLASH): a default port open "presses" it. The bridge opens with DTR/RTS low and sends `C` at start; other tools (Arduino Serial Monitor, plain pyserial) can still toggle it. |
| Core warns the hardware clock drifts further and further off the twin | Fixed 14 Oct: the twin paced on WSL's monotonic clock, which runs ~2 % slow. If it comes back, restart `make demo-sw`. |
| LEDs stay green although the bridge status line shows `B:R` (seen 14 Oct; the board heats on `H`, so serial works) | Wiring, not software: `2R` drives D5 low and D6 high. Check B's green leg is on D5 (not 3V3) and red on D6, common cathode through 150 Ω to G (`prototype/phase2-demo/docs/amica_wiring.png`). Test with the bridge stopped: send `2R` / `2O` at 115200 with DTR/RTS low. |
| Board does not boot with LEDs plugged in | D3, D4, D8 must stay free (boot pins). |

## Fallback order for the demo
1. Live board: the 5 commands above.
2. `make demo-sw` (twin plays all 12, including B).
3. The recorded backup video.
4. The Phase 2 demonstrator: `prototype/phase2-demo/README.md` (Redis on 6379; independent of this stack).
