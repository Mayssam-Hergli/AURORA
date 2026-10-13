# CASSANDRA live demo script (draft, 12 Oct 2026)

Target: about 3 minutes (CONTEXT Section 11), adjusted to what exists (MASTER_PLAN D10: the ESP8266 board plays B). Timings below were **measured overnight in simulation** (dry run with the firmware's model, twin runs); confirm them at the first rehearsal.

**Wording rules:** say "**contains**", not "quarantines", unless the screen shows a quarantine; say "minimises mission impact", never "prevents all attacks"; satellites marked EMULATED are simulated; the board's temperature is computed by its firmware; with one board, cutting B's links is logical.

## Before going on stage (10 min)
1. WSL: `cd /mnt/c/Users/USER/AURORA && PY=$HOME/aurora-venv/bin/python bash scripts/preflight.sh` → all green (or known warnings). Use `VENV=$HOME/aurora-venv` on every `make` line below: the core starts in seconds instead of ~2 minutes (torch import from /mnt/c).
2. Board plugged in, Arduino IDE closed, router/hotspot on a fixed channel.
3. Laptop on mains power; projector at 1080p.

## Terminals
| # | Where | Command | Keep visible? |
|---|---|---|---|
| T1 | WSL | `make up && make demo-sw SATS=A,C-L` (twin for A, C–L + core) | yes (core decisions print here) |
| T2 | Windows PowerShell | `cd C:\Users\USER\AURORA; .\.venv-win\Scripts\python.exe hardware\esp8266_bridge\bridge.py --port COM9 --sat B` | small |
| T3 | WSL | `make dashboard`, then open http://localhost:8080 in the browser, press F11 | projector |
| T4 | WSL | attacker: `make attack A1 TARGET=B` and later `make attack-stop TARGET=B` | yes |

## Part 1: Scenario A on the real board (about 60 s, real time)
| Say | Do | Expect on screen |
|---|---|---|
| "Twelve satellites: three physical slots, B is this board; the other eleven are simulated: see the EMULATED badges." | point at the tiles | all green; B shows "HW · temp emulated" |
| "An attacker who abused ground trust sends one heater command to B." | T4: `make attack A1 TARGET=B` | blue LED on; B's temperature climbs 1.5 °C/s |
| "CASSANDRA does not wait for the limit. It forecasts the time to failure." | | ~3 s after the command: B turns amber on the screen (LED B red) at ~28 °C and the countdown starts. B keeps climbing ~1.5 °C/s. **~19 s after the command, at ~54 °C**, CASSANDRA cuts the heater (board rule: act at P(F<30 s) ≥ 0.85; measured on the dry-run board, `results/board_rule_dryrun.txt`). Timeline: *"B contained: heater cut. Temperature limit avoided by ~4 s."* |
| "It simulated the alternatives (J values on the right) and chose the least disruptive one: it cuts the heater, B stays in service." | point at Last action | alternatives safe_mode / quarantine / none with their J |
| "B cools and is healed automatically." | | `healed` event, B green again (seconds) |

The board rule (`satellites.board_profile`: `act_horizon_s: 30`, `plan_p: 0.85`) gives the audience ~16 s of visible climb before the cut, with a 6 °C margin to the 60 °C limit. To act earlier, set `act_horizon_s: 60`. Escalation demo: `make attack A1 TARGET=B ATTACK_ARGS="--reassert 5 --duration 40"`: the attacker keeps re-sending; CASSANDRA escalates to safe mode (LEDs off), holds while commands keep coming, and heals after `make attack-stop TARGET=B`.

## Part 2: masking and propagation in software (about 90 s, 10x)
Stop T1 (Ctrl-C) and the bridge; then T1: `make demo-sw SPEED=10`. Explain: "the same system, every satellite simulated, time ×10."
| Say | Do | Expect |
|---|---|---|
| "Scenario B: the attacker freezes E's temperature report while heating it." | `make attack B1 TARGET=E` | monitor alarm ≈ 7 s (sim) after the heater: *temp_c frozen … treating temp_c as untrusted* (DE-0002); the forecast switches to an estimate (`trajectory-v0+est`) and E is contained before 45 °C |
| "Scenario C: a compromised satellite relays the attack to its neighbours." | `make attack C1 TARGET=A` | A flagged: *relaying HEATER on … over ISL (suspected compromised neighbour, IA-0003)*; each neighbour that receives the command is contained before its limit |
| | `make attack-stop TARGET=E` and `make attack-stop TARGET=A` | everything heals |

Honest note for C1: CASSANDRA always contains the victims; whether J also quarantines the source depends on the run (overnight: 2 of 3 seeds quarantined it, 1 left it flagged for ground; see docs/STATUS.md). Narrate what the screen shows: "quarantined" only if the source tile says QUARANTINED, otherwise "the source is flagged for ground".

## Evidence slide (numbers from results/manifest.json only)
Lead time at 0.1 % false-positive rate vs baselines, calibration (ECE), latency, closed-loop prevention (upper bound), with the caveats: simulation only; benchmark saturates; closed loop uses the simulator as its model. Copy numbers from `docs/STATUS.md` → Numbers, never from memory.

## When something fails
| Symptom | Say / do |
|---|---|
| Bridge does not connect | keep talking; T2: check WSL is running (`make up`), rerun the bridge; see hardware/BRINGUP.md |
| Board not responding | switch to Part 2 directly (software-only), then the recorded video |
| Dashboard frozen | reload the page (it reconnects by itself after a broker restart) |
| Core slow to start | it loads 5 models; start T1 before going on stage |

**Fallback order:** live board → `make demo-sw` → recorded backup video → `prototype/phase2-demo` (Phase 2 demonstrator).
