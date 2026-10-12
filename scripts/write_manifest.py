"""results/manifest.json: what produced the numbers (git hash, versions, seeds, commands, wall time per
step) and the headline metrics, so every number in STATUS.md, the README, the paper and the slides can
be traced to a run (MASTER_PLAN Phase 2, review finding S5).

    python scripts/write_manifest.py
"""

from __future__ import annotations

import csv
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ml import config  # noqa: E402

RES = ROOT / "results"


def _load(p: Path):
    return json.loads(p.read_text()) if p.exists() else None


def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def main() -> int:
    metrics = _load(RES / "eval" / "metrics.json")
    steps = []
    tsv = RES / "logs" / "pipeline_times.tsv"
    if tsv.exists():
        steps = list(csv.DictReader(tsv.open(), delimiter="\t"))
    lock = {}
    for line in (ROOT / "requirements.lock").read_text().splitlines():
        if "==" in line and not line.startswith("#"):
            k, v = line.split("==", 1)
            lock[k.strip()] = v.strip()
    syn, mc = config.load("synthetic"), config.load("model")
    headline = {}
    if metrics:
        for split, R in metrics["splits"].items():
            headline[split] = {
                k: {
                    "recall": round(v["recall"], 3),
                    "median_lead_s": v["median_lead_s"],
                    "iqr_lead_s": v.get("iqr_lead_s"),
                    "realized_fpr_pct": round(100 * v["realized_fpr"], 3),
                    "auroc": round(v.get("auroc", float("nan")), 4),
                    "pre_onset_alarm_per_sat_hour": round(
                        v.get("pre_onset", {}).get("per_sat_hour", float("nan")), 3
                    ),
                }
                for k, v in R["methods"].items()
                if k
                in (
                    "ensemble",
                    "single",
                    "no_eng",
                    "unconstrained",
                    "no_residual",
                    "threshold",
                    "trajectory",
                    "lstm_ae",
                    "gdn",
                    "ensemble@act",
                )
            }
            headline[split]["ece_10_bins"] = R["calibration"]
            headline[split]["next_subsystem"] = R["next_sub"]
            headline[split]["malicious_vs_fault"] = R["malicious"]
    cal = _load(config.MODELS / "calibration.json")
    manifest = {
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git": {
            "commit": _git("rev-parse", "--short", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(_git("status", "--porcelain")),
        },
        "machine": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.machine(),
        },
        "packages": {
            k: lock.get(k)
            for k in ("torch", "numpy", "pandas", "scikit-learn", "pydantic", "pyarrow", "paho-mqtt")
        },
        "data": {
            "generator": syn["generator"],
            "splits": syn["splits"],
            "split_seeds": syn["split_seeds"],
            "external": "CuCD-ID, OPS-SAT-AD (real noise for test_real), SCAD subset: "
            "python -m eval.datasets.download all",
        },
        "model": {
            "ensemble_seeds": mc["ensemble_seeds"],
            "ablations": mc["ablations"],
            "epochs": mc["epochs"],
            "hidden": mc["hidden"],
            "window_s": mc["window_s"],
            "calibration": cal,
        },
        "commands": [
            "python -m eval.datasets.download all",
            "python -m ml.synthetic.generate --workers 8",
            "python -m ml.features",
            "python -m ml.train all --parallel 4   # includes calibration (temperature)",
            "python -m eval.harness run --workers 4   # adds tau to calibration.json",
            "python -m eval.closed_loop --episodes 24",
            "python -m eval.robustness --episodes 12",
            "python -m eval.stress",
            "python -m ml.export",
            "python -m eval.figures",
            "(all of the above: bash scripts/overnight_pipeline.sh)",
        ],
        "wall_time_per_step": steps,
        "notes": [
            "Simulation only: synthetic 12-satellite episodes (ml/synthetic), not hardware or flight data.",
            "Closed loop: the sandbox uses the simulator as its model, so 'prevented' is an upper bound.",
            "Benchmark saturates: every ablation reaches recall 1.00, so the benefit of the graph structure "
            "is not shown.",
            "Overnight run 11-12 Oct: generate failed once on test_real (OPS-SAT noise bank race, fixed); "
            "test_real was regenerated with the same seeds and the pipeline re-run from evaluate.",
        ],
        "results_files": {
            "metrics": "results/eval/metrics.json",
            "closed_loop": "results/eval/closed_loop.json",
            "robustness": "results/eval/robustness.json",
            "stress": "results/eval/stress.json",
            "latency": "results/latency.json",
            "phase6": "results/live_manifest.json",
        },
        "headline": headline,
        "closed_loop": (_load(RES / "eval" / "closed_loop.json") or {}).get("summary"),
        "robustness": (_load(RES / "eval" / "robustness.json") or {}).get("results"),
        "stress": _load(RES / "eval" / "stress.json"),
        "latency": (_load(RES / "latency.json") or {}).get("latency"),
    }
    (RES / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str))
    print(f"wrote results/manifest.json ({len(steps)} timed steps, {len(headline)} splits)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
