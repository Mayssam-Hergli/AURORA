"""Incident-correlator metrics for the report (docs/NUMBERS.md): chain-assembly delay (first observed
evidence -> incident opened) per scenario, and for C1 the victims touched before the source was contained.
Core in closed loop with the live twin, in process (simulated plant), deployed configuration.

    python scripts/incident_metrics.py [--out results/incident_metrics.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aurora_common import schemas as S  # noqa: E402
from aurora_common import topics as T  # noqa: E402
from tests.cassandra.loop import Loop  # noqa: E402

TARGETS = {1: "E", 2: "G", 3: "J"}
SOURCES = {1: "A", 2: "D", 3: "K", 4: "F", 5: "I", 6: "L"}


def redteam(lp, sat, effect, args):
    lp.plant.on_redteam(T.redteam(sat), S.RedTeam(t=lp.now(), sat=sat, effect=effect, args=args))


def run(scenario: str, seed: int) -> dict:
    lp = Loop(seed=seed, duration_s=1000, cfg_overrides={"forecast": {"ensemble": {"enabled": True}}}).run(60)
    target = SOURCES[seed] if scenario == "C1" else TARGETS[seed]
    if scenario == "A1":
        lp.send(target, "HEATER", {"on": True})
        lp.run(500)
    elif scenario == "B1":
        redteam(lp, target, "freeze_tm", {"sub": "thermal", "field": "temp_c"})
        lp.send(target, "HEATER", {"on": True})
        lp.run(400)
        redteam(lp, target, "stop", {})
        lp.send(target, "HEATER", {"on": False})
        lp.run(200)
    else:
        args = {"cmd": "HEATER", "args": {"on": True}, "period_s": 10.0, "delay_s": 2.0}
        redteam(lp, target, "relay_tc", args)
        lp.run(500)
        redteam(lp, target, "stop", {})
        lp.run(200)
    last = {}
    for e in lp.events:
        if e.kind == "incident":
            last[e.data["incident_id"]] = e.data
    confirmed = [d for d in last.values() if d.get("confirmed") and (target in d["sats"])]
    inc = confirmed[0] if confirmed else None
    out = {"scenario": scenario, "seed": seed, "target": target, "incident": None}
    if inc:
        kinds = [s["kind"] for s in inc["steps"]]
        out["incident"] = {
            "id": inc["incident_id"],
            "status": inc["status"],
            "assembly_delay_s": inc["assembly_delay_s"],
            "steps": kinds,
            "has_deception": "deception" in kinds,
            "source": inc["source"],
            "victims": inc["victims"],
            "victims_touched_before_source_contained": inc["victims_touched_before_source_contained"],
        }
    out["unconfirmed_incidents"] = sum(1 for d in last.values() if not d.get("confirmed"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "results" / "incident_metrics.json"))
    a = ap.parse_args(argv)
    t0 = time.time()
    runs = (
        [run("A1", s) for s in (1, 2, 3)]
        + [run("B1", s) for s in (1, 2, 3)]
        + [run("C1", s) for s in range(1, 7)]
    )
    for r in runs:
        print(json.dumps(r), flush=True)
    summary = {}
    for sc in ("A1", "B1", "C1"):
        d = [r["incident"]["assembly_delay_s"] for r in runs if r["scenario"] == sc and r["incident"]]
        summary[sc] = {
            "runs": sum(1 for r in runs if r["scenario"] == sc),
            "chains_built": len(d),
            "assembly_delay_s": d,
            "assembly_delay_median_s": statistics.median(d) if d else None,
        }
    c1 = [r["incident"] for r in runs if r["scenario"] == "C1" and r["incident"]]
    summary["C1"]["victims_touched_before_source_contained"] = [
        (
            None
            if i["victims_touched_before_source_contained"] is None
            else len(i["victims_touched_before_source_contained"])
        )
        for i in c1
    ]
    summary["B1"]["deception_step"] = [
        r["incident"]["has_deception"] for r in runs if r["scenario"] == "B1" and r["incident"]
    ]
    git = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    out = {
        "what": "Incident correlator metrics: chain-assembly delay (first observed evidence -> incident "
        "opened) and C1 victims touched before the source was contained. In process, simulated plant, "
        "learned ensemble.",
        "command": "python scripts/incident_metrics.py",
        "git": git,
        "wall_s": round(time.time() - t0, 1),
        "summary": summary,
        "runs": runs,
    }
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps(summary, indent=1))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
