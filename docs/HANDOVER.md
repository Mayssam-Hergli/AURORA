# AURORA handover (14 Oct 2026, deadline day)

From Mayssam Hergli (with Claude Code). This file is the single starting point: what exists, how to run it, what is left in order, and the rules. Details: [STATUS.md](STATUS.md) (history and decisions), [NUMBERS.md](NUMBERS.md) (every number, with its file and command), [DEMO_SCRIPT.md](DEMO_SCRIPT.md) (the stage script, measured), [interfaces.md](interfaces.md) (the contract, v1.3), [hardware/BRINGUP.md](../hardware/BRINGUP.md) (the board).

## 1. State in one paragraph

The software system is finished and rehearsed:
- **Constellation:** a 12-satellite digital twin.
- **CASSANDRA core:** forecasting with the learned ensemble, monitor, sandbox, response with escalation, explanations, and an **incident correlator** that assembles the attack chain from observed evidence only.
- **Attacks:** A1, B1, C1, faults, N.
- **Board bridge:** the ESP8266 bridge, tested with a fake board running the firmware's model.
- **Operator dashboard:** one screen, "dark cockpit", the attack chain, `/detail`.

Test and rehearsal status:
- **Tests:** about 200 pass (Python 3.11, broker up, on mains power); lint is clean.
- **Rehearsal:** the unattended rehearsal (`scripts/rehearse.py`) passed **3 times** end to end (board Part 1 on the dry-run board, Part 2 at 10×).

What is left:
- the **real board** (Block 5),
- the **backup video**,
- the **paper and slides text** (the numbers are ready),
- a few decisions (section 6).

## 2. Code and GitHub

| Item | State |
|---|---|
| Branch to use | **`p9-integration`**: everything. `main` untouched (merging needs Mayssam's OK). |
| Not used | `rami` (Rami Troudi): not merged; not used for this submission. Our local `p2-ml-fixes`, `p3-twin`, `p7-dashboard`: never push them (Youssef's branches with the same names are on GitHub). |
| Models | GitHub release **`models-v2`** (`aurora-models-v2.zip`, sha256 `87253c74…bff7`, pinned in `eval/datasets/download.py`). `make models` downloads and verifies it; verified on a clean clone. |
| Last local commits | `4892e26` (incident correlator) is committed **but was not pushed** at the time of writing; the commit block at the end of the latest session pushes it with Blocks 6–7. Check with `git log --oneline origin/p9-integration..HEAD` (empty = all pushed). |

## 3. Run it (Windows 11 + WSL Ubuntu, on mains power)

```bash
cd /mnt/c/Users/USER/AURORA                      # WSL; keep one WSL terminal open (WSL stops the broker otherwise)
export V="VENV=$HOME/aurora-venv"                # Linux-side venv: the core starts in seconds
make up $V                                       # Mosquitto: 1883 MQTT, 9001 WebSocket
PY=$HOME/aurora-venv/bin/python bash scripts/preflight.sh    # green/red checklist (power, broker, models, dashboard, COM port, disk)
make demo-board-dry $V                           # twin A, C-L + core + fake board as B   (or: make demo-sw $V, all simulated)
make dashboard $V                                # second terminal -> http://localhost:8080 (F11); /detail/ for per-subsystem charts
make attack A1 TARGET=B $V                       # also: B1 TARGET=E, C1 TARGET=D; ATTACK_ARGS="--reassert 5 --duration 40"
make attack-stop TARGET=B $V
$HOME/aurora-venv/bin/python scripts/rehearse.py --runs 1    # the whole demo, unattended and timed (~3.5 min)
make test $V && make lint $V
```

Real board as B: `make demo-sw SATS=A,C-L $V` in WSL, plus in Windows PowerShell
`.\.venv-win\Scripts\python.exe hardware\esp8266_bridge\bridge.py --port COMx --sat B` (COMx from preflight).

## 4. Done, with evidence

| Block | What | Evidence |
|---|---|---|
| 1 | Staged work committed; heal-temperature test fixed | commit 93e7ac2 |
| 2 | Dashboard redesign ("dark cockpit", plain language, constellation, options, satellite at risk, `/detail`) | `results/dashboard_screens/1_…` to `7_…` (1920, 1280, projector) |
| 3 | Board rule P(F<30 s) ≥ 0.85; C1 future victims; physics-bounded monitor and estimate; re-runs | STATUS.md "Block 3"; `results/phase6_block3.json`, `phase6_c1_prefix.json`, `board_rule_dryrun.txt` |
| 4 | models-v2 release; `make models` verified on a clean clone | release page; HANDOVER section 2 |
| Incident | Attack-chain correlator (`cassandra/incident.py`, event kind `incident`); chain view on the dashboard; rule-based next subsystem on the board; live 10× monitor bug fixed | `tests/cassandra/test_incident.py`; `results/incident_metrics.json`; screenshots `results/dashboard_screens/8_chain_*` |
| 6 | DEMO_SCRIPT.md rewritten with measured times; preflight.sh with green/red summary; 3 unattended rehearsals, all milestones met | `results/rehearsal.json` (2 runs), `results/logs/show_run.json` (watched run) |
| 7 | NUMBERS.md generated from the results files (headline test_ood at 0.1 % FPR with intervals; ID/real-noise table with the saturation caveat; closed loop as upper bound; live scenarios; attack-chain metrics) | `python scripts/write_numbers.py` |

Note: the rehearsals ran **on battery** (laptop unplugged at the time) and still met every milestone; re-run once on mains power before going on stage.

## 5. What is left, in order

**Block 5: the real board (G6), ~30–45 min, at the board.** Follow `hardware/BRINGUP.md`, one step at a time:
1. Plug the NodeMCU in with a **data** USB cable. `preflight.sh` (board section) must show "USB-serial board found: … (COMx)". On 13 Oct only Bluetooth ports (COM5–8) were visible; if it is still missing, try another cable, then install the CP210x driver (Silicon Labs).
2. Wiring per `prototype/phase2-demo/docs/amica_wiring.png`: 3 dual-colour common-cathode LEDs (A: D1 green, D2 red; B: D5/D6; C: D7/D0), each common leg → 150 Ω → GND; leave D3, D4, D8 free.
3. WSL: `make up $V`, then `make demo-sw SATS=A,C-L $V`; Windows: the bridge on that COMx. Expect "connected to localhost:1883", LEDs green, B a **square** on the dashboard.
4. `make attack A1 TARGET=B $V`. Expect, as rehearsed on the dry-run board: B amber ~6 s after Enter, heater cut ~22 s at ~54 °C, healed ~31 s. Record: the bridge with `--log curve.csv`, and the times (`python scripts/rehearse.py` cannot drive the real bridge; note them by hand or film the screen).
5. If the real board differs from the dry run (timing, margin to 60 °C), adjust `satellites.board_profile` (`act_horizon_s`, `plan_p`) and note it in STATUS.md.

**Backup video (G7).** On mains power: Part 1 with the real board (or `make demo-board-dry`) and Part 2 at 10×, following DEMO_SCRIPT.md. OBS or Windows Game Bar (Win+Alt+R) on the browser window. Keep it under 4 minutes.

**Paper and slides.** Copy numbers from `docs/NUMBERS.md` only (one set: `results/manifest.json`). Headline: test_ood at 0.1 % FPR with its interval. ID table with the saturation caveat. Closed loop labelled upper bound. Footnote "Technique mapping refined in Phase 3." wherever IA-0003 / DE-0002 / DE-0003 appear. The paper source is not in this repo.

**Final checks (on stage day).** `preflight.sh` all green on mains power; one `rehearse.py --runs 1`; push; freeze (no code changes after the last rehearsal).

## 6. Decisions still open (Mayssam's call)

- **Board margin:** the board rule cuts the heater at ~54 °C, 6 °C under the 60 °C limit (dry run). Keep, or act earlier (`act_horizon_s: 60`)? Decide after the real board.
- **Paper headline:** ensemble recall at the 0.1 % FPR alert level (1.00, 135 s on test_ood) vs at the core's action threshold (0.73, 36 s on test_ood). NUMBERS.md gives both.
- **Offline closed-loop planner** is over the 150 ms budget (254–461 ms); the live core is within it (~60–160 ms). Report as offline-only, or optimise.
- **Merge into `main`:** when, and whether PR #2 (Youssef) is closed as superseded by `p9-integration`.

## 7. Known issues (say them, or avoid them on stage)

- **Battery:** on battery the CPU is throttled; the core can fall behind a 10× plant. Plug in.
- **Brief false flags:** the ensemble flags at a low alert level (τ ≈ 0.015), so a healthy satellite is sometimes flagged for a few seconds (no action). On the dashboard they never become an attack chain (suspected incidents are dismissed).
- **Heater-consistency rule near equilibrium:** a heater warming very slowly can be flagged as inconsistent; the core then estimates the temperature (conservative). Seen twice as an extra "deception" step on a C1 victim (F); not fixed (code freeze).
- **Repeated A1 on the same satellite within ~5 min** is read as a continuing attack (escalation to safe mode). For a clean A1 on stage, reset with `attack-stop` and wait, or restart the demo.
- **Board:** temperature computed by the firmware (no sensor); ISL cuts on the board are logical; one board only.
- **Benchmark saturation:** every ablation reaches recall 1.00 on the synthetic sets; the graph's benefit is not shown.

## 8. Gotchas

- Between demo runs use `make stage-reset` (all attacks released, all satellites ground-cleared, broker restarted, dashboard session cleared). After it, satellites read "no anomaly reported" until the core publishes a new state.
- Never run `make test` while a demo stack is up: the live core on the same broker makes the A1 integration tests fail.

- WSL shuts down when no WSL program runs, and the broker with it ("connection refused" on Windows): keep one WSL terminal running.
- Use `VENV=$HOME/aurora-venv`: the repo's `.venv` on /mnt/c imports torch in ~90 s.
- `python -m ml.export --bench-only` overwrites `results/latency.json`; never under load. Restore from `results/manifest.json`.
- Re-running `make evaluate` rewrites `calibration.json` → the models-v2 hash changes: rebuild with `python -m ml.export --bundle v2`, re-pin, new release.
- Close the Arduino IDE serial monitor before the bridge (it holds the COM port).
- Port 6379 belongs to the Phase 2 Redis fallback (`sat_b_redis`); leave it alone.

## 9. Rules that still hold

- **Honesty:** every published number comes from a `results/` file produced by code, with its command (NUMBERS.md). Simulated satellites, firmware temperatures and logical link cuts are said out loud. "Contains" for A1.
- **Ground-truth isolation:** the core and the dashboard never subscribe to `gt/` or `redteam/`; `tests/cassandra/test_gt_isolation.py` enforces it for the core.
- **Git:** never force-push; never push our local p2/p3/p7; nothing into `main` without Mayssam's OK; the `rami` branch is not merged.
- **Contract:** `docs/interfaces.md` v1.3 is frozen. Extra telemetry fields and `core/event.data` are allowed; anything else needs agreement.
