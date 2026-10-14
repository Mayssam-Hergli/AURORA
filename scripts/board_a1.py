"""A1 on the REAL board (satellite B), timed: seconds after the attack command at which the dashboard
receives each milestone. The stack must already run (hardware/BRINGUP.md: make demo-sw SATS=A,C-L in
WSL, the bridge on Windows). Listens like the dashboard (never gt/ or redteam/).

    PY=$HOME/aurora-venv/bin/python; $PY scripts/board_a1.py [--runs 1] [--out results/board_a1.json]

Between runs it releases B (attack-stop) and waits --gap seconds: a repeated A1 within ~5 min is read
as a continuing attack (escalation), which is a different scenario.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.rehearse import Watch, action, heal, incident_has, make, state  # noqa: E402


def peak_temp(w: Watch, since: float) -> float | None:
    with w.lock:
        temps = [
            m.fields.get("temp_c")
            for t, kind, m in w.log
            if t >= since and kind == "tm" and m.sat == "B" and "temp_c" in (m.fields or {})
        ]
    return max(temps) if temps else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--gap", type=float, default=330.0, help="seconds between runs (> escalation window)")
    ap.add_argument("--out", default=str(ROOT / "results" / "board_a1.json"))
    a = ap.parse_args(argv)
    w = Watch()
    w.bus.subscribe("aurora/v1/sat/B/tm/thermal", w.on)
    runs, ok = [], True
    for k in range(a.runs):
        if k:
            print(f"waiting {a.gap:.0f} s before the next run", flush=True)
            time.sleep(a.gap)
        t0 = w.mark()
        make("attack", "A1", "TARGET=B")
        r = {
            "run": k + 1,
            "wall_start": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0)),
            "flagged_s": w.wait(t0, state("B", "flagged"), 60),
            "chain_command_s": w.wait(t0, incident_has("B", "command"), 60),
            "heater_cut_s": w.wait(t0, action("B", "isolate_subsystem"), 90),
            "healed_s": w.wait(t0, heal("B"), 150),
        }
        r["peak_temp_c"] = peak_temp(w, t0)
        make("attack-stop", "TARGET=B")
        r["missing"] = [m for m, v in r.items() if v is None]
        ok &= not r["missing"]
        runs.append(r)
        print(json.dumps(r), flush=True)
    w.bus.stop()
    Path(a.out).write_text(
        json.dumps(
            {
                "what": "A1 (latched ground HEATER on) on the real ESP8266 board as satellite B; the other "
                "11 satellites simulated by the twin. Seconds after the attack command, wall clock, as the "
                "dashboard receives them. Board temperature is computed by the firmware (no sensor).",
                "command": f"python scripts/board_a1.py --runs {a.runs}",
                "limit_c": 60,
                "runs": runs,
            },
            indent=1,
        )
    )
    print("BOARD A1", "OK" if ok else "FAILED", f"-> {a.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
