# CASSANDRA live demo script (14 Oct 2026)

About 3 minutes on stage. Every time below is **measured** by two unattended rehearsals (`scripts/rehearse.py`, `results/rehearsal.json`, 14 Oct; the two runs agree within ~1 s), counted from pressing Enter on the attack (the command itself takes ~2 s to start). Part 1 was rehearsed with the dry-run board (the firmware's model); the real board must confirm it (hardware/BRINGUP.md).

**Wording rules**
- Say "**contains**" for A1 (the core cuts the heater; the satellite stays in service). Say "**safe mode**" for the escalation and "**quarantined**" only for the C1 source: exactly what the screen says.
- Say "minimises mission impact", never "prevents all attacks". Hollow circles are simulated by the digital twin; the square is the physical board; its temperature is computed by its firmware (no sensor). With one board, cutting B's links is logical.
- Numbers on slides come from `docs/NUMBERS.md` only.

## Before going on stage (10 minutes)
1. Laptop **on mains power** (on battery, Windows throttles the CPU and the core falls behind).
2. WSL terminal: `cd /mnt/c/Users/USER/AURORA && make up VENV=$HOME/aurora-venv && PY=$HOME/aurora-venv/bin/python bash scripts/preflight.sh` → **ALL GREEN**. The board line tells you its COM port.
3. Board plugged in, Arduino IDE closed; projector at 1920×1080; browser full screen (F11) on http://localhost:8080.

## Terminals (open all four before you start talking)
| # | Where | Command | On screen? |
|---|---|---|---|
| T1 | WSL | `make demo-sw SATS=A,C-L VENV=$HOME/aurora-venv` (twin A, C–L + core; B is the board) | no |
| T2 | Windows PowerShell | `cd C:\Users\USER\AURORA; .\.venv-win\Scripts\python.exe hardware\esp8266_bridge\bridge.py --port COMx --sat B` (COMx from preflight) | small, optional |
| T3 | WSL | `make dashboard VENV=$HOME/aurora-venv`, then the browser on http://localhost:8080 | **projector** |
| T4 | WSL | the attacker (commands below) | yes, small |

Without the board, T1+T2 become one command: `make demo-board-dry VENV=$HOME/aurora-venv` (same screen; the fake board runs the firmware's model).

## The screen, in one sentence each
- **Left:** the constellation seen from above the North Pole, every satellite at its real orbital position. Hollow circle = simulated, square = the board. Nominal is grey; colour appears only for trouble: **amber** at risk, **red** contained / quarantined / cut link, **blue** safe hold or healing.
- **Right:** "What CASSANDRA is doing": the **attack chain** as it builds up (solid = observed, dashed = forecast), each step with its SPARTA tag; below it, the options CASSANDRA compared, lower cost is better.
- **Bottom:** the satellite at risk: temperature with its limit in red, failure risk with the decision line, and a countdown to the limit.

## Part 1: the attack on the real board (real time, about 60 s)
| Say | Do (T4) | You will see (measured) |
|---|---|---|
| "Twelve satellites. The square is this board; the hollow circles are simulated." | point | all grey; "Watching 12 satellites. No anomalies." |
| "An attacker abuses ground trust and sends one heater command to B." | `make attack A1 TARGET=B VENV=$HOME/aurora-venv` | blue LED on; B climbs ~1.5 °C/s |
| "CASSANDRA does not wait for the limit: it forecasts the time to failure." | | **~6 s**: B turns **amber**, LED red; the chain shows *Command: heater switched on by a ground command (IA-0009)* and *Effect: temperature rising*; a dashed *Forecast* step gives the time to the limit and the next subsystem (power), the countdown runs |
| "It simulated every response and chose the least disruptive: cut the heater, B stays in service." | point at Options compared | **~22 s, at ~54 °C**: B **red**, *Response: B contained: heater cut*; bars: Heater cut lowest |
| "B cools and heals by itself." | | **~31 s**: B back to grey, *Healed* step |
| (optional) "If the attacker insists, CASSANDRA escalates." | `make attack A1 TARGET=B VENV=$HOME/aurora-venv ATTACK_ARGS="--reassert 5 --duration 40"` | with commands re-sent every 5 s, CASSANDRA goes straight to **safe mode** at ~22 s (B **blue**, LEDs off); holds while commands keep arriving; heals ~53 s after the start once they stop (`make attack-stop TARGET=B VENV=$HOME/aurora-venv`) |

Reset before Part 2: `make attack-stop TARGET=B VENV=$HOME/aurora-venv`.

Between full runs (rehearsal, or a second audience): `make stage-reset VENV=$HOME/aurora-venv` releases every attack, ground-clears all 12 satellites (a held quarantine too), restarts the broker, and the dashboard starts a clean session ("Watching 12 satellites. No anomalies."). Incidents also leave the main screen by themselves 10 min after their last update; `/detail` keeps them all.

## Part 2: masking and propagation, all simulated, time ×10 (about 90 s)
Stop T1 (Ctrl-C) and the bridge, then T1: `make demo-sw SPEED=10 SEED=2 VENV=$HOME/aurora-venv`. Say: "the same system, every satellite simulated, time ten times faster."

| Say | Do (T4) | You will see (measured at 10×) |
|---|---|---|
| "Scenario B: the attacker heats E and freezes its temperature report." | `make attack B1 TARGET=E VENV=$HOME/aurora-venv` | **~3 s**: chain step *Deception: temperature report stopped changing … the report is being masked (DE-0002)*; the bottom temperature plot is flat: that is the lie |
| "CASSANDRA no longer trusts the report; it estimates the temperature and acts on the estimate." | | **~13 s**: E **red**, *Response: E contained: heater cut*; the effect step is marked *predicted, not observed* (estimated) |
| | `make attack-stop TARGET=E VENV=$HOME/aurora-venv` | E heals |
| "Scenario C: a compromised satellite relays the attack to its neighbours." | `make attack C1 TARGET=D VENV=$HOME/aurora-venv` | **~5 s**: D amber, chain *D relaying heater on over inter-satellite links (IA-0003)*, fan-out *D relayed to → victim*, **amber attack paths** on the diagram |
| "It quarantines the source, so no further victims." | | **~6 s**: D **red, quarantined**, its links **red dashed**; *Response: D quarantined: links cut, traffic rerouted (CM0070)* |
| | `make attack-stop TARGET=D VENV=$HOME/aurora-venv` | (a quarantined source stays held until ground clears it: `python scripts/send_tc.py D SET_MODE mode=NOMINAL`) |

## Closing (evidence slide)
Numbers from `docs/NUMBERS.md` only: out-of-distribution warning recall at 0.1 % false-positive rate with its interval and median lead; the closed-loop "about 7 in 10 failures averted, upper bound"; the attack-chain metrics. Caveats out loud: simulation only; the benchmark saturates; the closed loop uses the simulator as its model.

## When something fails
| Symptom | Say / do |
|---|---|
| Bridge does not connect | keep talking; check WSL T1 is still running (WSL stops the broker when nothing runs); restart the bridge |
| Board not responding | go to `make demo-board-dry` (same screen), then Part 2 |
| Dashboard frozen or "reconnecting…" | `make up`, reload the page (it reconnects by itself) |
| Core slow / screen lags | laptop on battery? plug in; use `SPEED=10` at most |
| Nothing appears after an attack | `make attack-stop TARGET=…`, wait 20 s, repeat once; then switch to the video |

**Fallback order:** live board → `make demo-sw` (all simulated) → the recorded backup video → `prototype/phase2-demo` (the Phase 2 demonstrator, see its README).
