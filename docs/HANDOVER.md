# AURORA handover (12 Oct 2026, evening)

From Mayssam Hergli (with Claude Code). **Deadline: 14 Oct 2026.** This file covers what exists, how to run it, and what is left, in order. The long history is in [STATUS.md](STATUS.md); the plan and gates are in [MASTER_PLAN.md](MASTER_PLAN.md); the message contract is [interfaces.md](interfaces.md) (v1.2, frozen).

## 1. State in one paragraph

The whole software system works end to end:
- the digital twin (12 satellites);
- the CASSANDRA core (forecast, sandbox, act, explain), with the trained ensemble loaded;
- the attack scripts (A1, B1, C1, faults);
- the ESP8266 board bridge (tested with a fake board only);
- the operator dashboard, redesigned today.

All 186 tests pass, broker tests included, and lint is clean. The real board has **not** been connected yet. The demo has **not** been rehearsed or recorded. The paper text is not in the repo.

## 2. Where the code is

| Branch | On GitHub? | What |
|---|---|---|
| `p9-integration` | yes (2de8c05) | **Everything. Work here.** Our chain plus Youssef's p2/p3/p7 merged onto it |
| `p4-core`, `p5-scenarios`, `p8-board` | yes | earlier steps of the same chain (contained in p9) |
| our local `p2-ml-fixes`, `p3-twin`, `p7-dashboard` | **no, and never push them** | Youssef's branches with the same names are on GitHub with a different history. p9 contains all of ours |
| `main` | yes (2cd9cfa, PR #1) | untouched. Merging into main needs Mayssam's OK |
| `rami` | yes (Rami Troudi) | rami branch not merged; not used for this submission. |

**Uncommitted on `p9-integration` (commit this first):** the dashboard redesign, plus the small core and twin changes it needs, plus this file. Tests pass (on mains power):
```bash
cd /c/Users/USER/AURORA        # Git Bash
git switch p9-integration
git add cassandra/act.py cassandra/explain.py ml/synthetic/constellation.py twin/service.py \
        tests/cassandra/test_act.py tests/test_twin_service.py dashboard/ docs/HANDOVER.md
git commit -m "Dashboard redesign (dark cockpit) + handover notes"
git push origin p9-integration
```

**Models release: done (13 Oct).** GitHub release `models-v2` holds `aurora-models-v2.zip` (869,874 B, sha256 `87253c7408976cf9b40100997aa84ee75d9b3783cfef16e75292c560e547bff7`, pinned in `eval/datasets/download.py`; per-file SHA-256s in the release notes). Verified on a clean clone of `p9-integration`: `make models` downloads it, checks the hash, unpacks 8 files into `data/models`, and the core loads `tgn-v1` with 5 models. The GitHub CLI is not installed on this laptop: release edits go through the web page, and a release is only usable once its asset shows as uploaded.

**Not yet done on GitHub:**
- No PR is open. Youssef's PR #2 sits on top of his own branches and is superseded by p9-integration; agree with him before closing it.

## 3. Run it (laptop: Windows 11 + WSL Ubuntu)

Always work **on mains power**. On battery, Windows throttles the CPU: the ensemble then takes about 160 ms per tick instead of about 40 ms, and the broker tests fail.

```bash
# WSL. Use the Linux-side venv: the repo's .venv on /mnt/c takes ~90 s to import torch
cd /mnt/c/Users/USER/AURORA
export V="VENV=$HOME/aurora-venv"
make up $V                          # Mosquitto in Docker: 1883 (MQTT), 9001 (WebSocket). Keep this WSL terminal open
make demo-sw $V                     # twin (A-L) + core. SPEED=10 to fast-forward; keep SPEED <= 10 with the ensemble
make dashboard $V                   # second terminal -> http://localhost:8080 (F11). /detail/ = per-subsystem page
make attack A1 TARGET=B $V          # also B1, C1, F1, N; ATTACK_ARGS="--reassert 5" for A1
make attack-stop TARGET=B $V        # reset
PY=$HOME/aurora-venv/bin/python bash scripts/preflight.sh   # pre-demo checklist, all green expected
~/aurora-venv/bin/python -m pytest -q tests     # 186 tests; needs `make up` for the broker tests
make lint $V
```

With the board as satellite B: `make demo-sw SATS=A,C-L $V`, then in **Windows PowerShell**:
`cd C:\Users\USER\AURORA; .\.venv-win\Scripts\python.exe hardware\esp8266_bridge\bridge.py --port COM9 --sat B --log curve.csv`.
Without the board: `make demo-board-dry $V` (a fake board running the firmware's model). Details and troubleshooting: [hardware/BRINGUP.md](../hardware/BRINGUP.md).

## 4. How it works, and where it lives

| Step | What | Code |
|---|---|---|
| Plant | 12 satellites, 3 orbital planes × 4, real engine physics, telemetry at 1 Hz on `aurora/v1/sat/{id}/tm/{sub}`. Applies telecommands with the SAFE rule (interfaces.md 3.2). Red-team effects (freeze, bias, relay) and faults. Ground truth only on `gt/` | `twin/service.py`, `ml/synthetic/engine.py` |
| Board | ESP8266 NodeMCU = satellite B; temperature computed by its firmware; serial ↔ MQTT bridge with its own SAFE rule and LEDs for A/B/C | `hardware/esp8266_bridge/bridge.py`, `prototype/phase2-demo/` |
| Attacks | A1 latched heater, B1 heater + frozen report, C1 compromised neighbour relaying over ISL, faults F1–F5, N clean | `attacks/run.py` |
| Core: ingest | 1 s ticks on message time, freshness, seq/provenance checks | `cassandra/ingest.py` |
| Core: monitor | tampered telemetry (frozen, or heater on but temperature not rising) → temperature untrusted, estimated instead | `cassandra/monitor.py` |
| Core: forecast | P(failure within 10/30/60/120 s): trained 5-model ensemble for twin satellites (flags at τ = 0.0153), trajectory forecaster for the board; time to the limit | `cassandra/forecast.py`, `ml/online.py` |
| Core: sandbox | from P ≥ 0.5: simulate none / isolate / safe mode / block source / quarantine for 60 s on a shadow twin built only from observations; score J = risk + lost service + cost + uncertainty | `cassandra/sandbox.py` |
| Core: act + explain | cheapest safe option, uncertainty gate, 25 % quarantine budget, escalation ladder, heal; every step on `core/event` (with machine-readable `data`) | `cassandra/act.py`, `cassandra/explain.py` |
| Dashboard | one read-only screen: constellation, "What CASSANDRA is doing" in plain language, options compared, satellite at risk. Never reads `gt/` | `dashboard/` (see its README) |
| ML pipeline | generate → train → evaluate → closed loop → robustness → stress → export → figures | `bash scripts/overnight_pipeline.sh`, `ml/`, `eval/` |

## 5. Gates (MASTER_PLAN)

| Gate | State |
|---|---|
| G0 repo health, G1 contract + broker | ✅ |
| G2 ML fixed and retrained, G3 live twin, G4 A1 software loop, G5 scenarios | 🔎 ready for Mayssam's review (evidence in STATUS.md) |
| G6 real board | ⏳ **not started**: needs the board plugged in |
| G7 demo rehearsed + backup recording | ⏳ **not started** |

## 6. Numbers (only quote these, with their source)

All from `results/manifest.json` (run on 12 Oct; commands are inside) and the tables in STATUS.md. Simulation only.
- **Ensemble, at 0.1 % false-positive rate:**

  | Split | Recall | Median lead |
  |---|---|---|
  | ID | 1.00 | 133 s |
  | OOD | 1.00 | 135 s |
  | Real noise | 1.00 | 158 s |

  At the core's action thresholds, recall is 0.89 / 0.74 / 0.70 with about 38 s lead.
- **Baselines (ID):**

  | Baseline | Recall | Median lead | Note |
  |---|---|---|---|
  | Threshold | 0.90 | 49 s | |
  | Trajectory | 0.86 | 20 s | |
  | GDN | 0.69 | 167 s | 6.5 pre-onset false alarms per satellite-hour |
  | LSTM-AE | 0.35 | 197 s | |

  The benchmark saturates: every ablation also reaches recall 1.00.
- **Closed loop:** 13 of 18 failures averted (72 %, 95 % CI 49–87 %). This is an upper bound, because the planner uses the simulator as its model.
- **Latency:** 17.6–34.1 ms median for the 5-model ensemble, depending on machine load.
- **Phase 6 live runs:** 12 of 12 within limits (`results/live_manifest.json`). C1 quarantines the source in 2 of 3 seeds.

Do **not** use PR #1's old numbers or the overnight GDN figure (0.80 / 222 s): that one was inflated by the M3 bug, since fixed.

## 7. What is left, in order

**Today / first thing**
1. ~~Commit and push the dashboard work; upload the models-v2 release.~~ Done 13 Oct.
2. Send Youssef the merge summary; Mayssam has a draft. Agree what happens to PR #2 and his remote p2/p3/p7.
3. **Board bring-up (G6), about 30 min:** follow [hardware/BRINGUP.md](../hardware/BRINGUP.md) (5 commands).
   - Check that the bridge prints `connected`, B appears as a **square** on the dashboard, and the LEDs follow the states.
   - Run `make attack A1 TARGET=B`. Expected: contained about 2–4 s after the command, then healed.
   - Record `curve.csv` (bridge only, press FLASH) for the paper.

**13 Oct**

4. **Fix the monitor's false alarms** (core; needs Mayssam's OK, see section 8). An honestly heating twin satellite is sometimes flagged "heater on but temperature not rising": the 10 s slope is too noisy for the 0.02 °C/s threshold. Fix options:
   - (a) a significance test on the slope (flag only when slope + k·SE is still below the threshold);
   - (b) compare against the thermal model's expected rise.

   Then re-run `python scripts/phase6_runs.py` and the tests. Seen live on 12 Oct; `results/dashboard_screens/` shows it in the timeline.
5. **Rewrite [DEMO_SCRIPT.md](DEMO_SCRIPT.md)** for the new dashboard. It still describes the old tiles and event log. Say "contains", not "quarantines", unless the screen says quarantined.
6. **Two timed rehearsals** with the board (G7). Then **record the backup video** of the same run.
7. **Paper and slides:** copy numbers only from section 6 / `results/manifest.json`. Add the SPARTA footnote "Technique mapping refined in Phase 3." wherever the new IDs appear. The figures are in `docs/figures/v1/` (regenerated 12 Oct).

**14 Oct**

8. `bash scripts/preflight.sh` (all green), a final rehearsal, then freeze: no code changes after it.

## 8. Open decisions (Mayssam decides, not the implementer)

- **Monitor fix:** (a) or (b) above. It changes detection behaviour, so Phase 6 must be re-run.
- **Board demo length:** today the core contains the board about 2 s after the attack, at about 29 °C, so the audience barely sees B heat up. Options:
  - keep it, and show the climb with `--reassert` or the heating-curve video;
  - give the board profile a later action threshold (e.g. plan at 0.85).
- **C1:** should quarantine of the relaying source be systematic? Today J chooses it in 2 of 3 runs; making it systematic means changing J's objective.
- **Paper headline:** ensemble recall at τ (1.00 / 133 s) or at the action thresholds (0.89 / 38 s).
- **Offline closed-loop planner:** 254–461 ms against the 150 ms budget (the live core is within budget). Fix it, or report it as offline-only?
- **Contract v1.3** (fault effects on `redteam/`) is a message-format change: needs explicit agreement.

## 9. Gotchas (each one cost time)

- **WSL shuts down when no WSL program is running, and the broker dies with it.** Keep one WSL terminal open (`make demo-sw`). Windows then reports "connection refused".
- **Battery = throttled CPU:** the ensemble core falls behind a 20x plant, and integration tests fail. Plug in.
- **Retained MQTT states from earlier fast-forwarded runs** carry future timestamps. The integration tests drop states received before the core is ready; do the same in any new test that watches states.
- `python -m ml.export --bench-only` **overwrites `results/latency.json`**. Don't run it on battery or under load. If it happens, restore the file from the copy inside `results/manifest.json`.
- Re-running `make evaluate` rewrites `data/models/calibration.json`. That changes the models-v2 zip hash: rebuild with `python -m ml.export --bundle v2` and update the pin in `eval/datasets/download.py`.
- Port 6379 belongs to the Phase 2 Redis fallback (`sat_b_redis`); leave it alone. The Phase 2 demonstrator in `prototype/phase2-demo/` is the last-resort demo.
- Close the Arduino IDE serial monitor before starting the bridge: it holds COM9.

## 10. Rules that still hold

- **Honesty:** every number in the paper, slides or voiceover comes from a `results/` file produced by code, with its command. Simulated satellites, firmware-computed temperatures and logical link cuts are said out loud.
- **Ground-truth isolation:** the core and the dashboard never subscribe to `gt/` or `redteam/`. A static test enforces this for the core: `tests/cassandra/test_gt_isolation.py`.
- **Git:** never force-push. Never push our local p2/p3/p7. Nothing goes into `main` without Mayssam's OK.
- **Contract:** `docs/interfaces.md` is frozen. Extra telemetry fields and `core/event.data` are allowed; anything else needs agreement.
