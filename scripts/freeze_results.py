"""Freeze measured outputs with actual package versions and reproducibility digests."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root, patterns):
    return {
        str(p.relative_to(ROOT)): sha(p)
        for p in sorted({p for pattern in patterns for p in root.rglob(pattern)})
        if p.is_file()
    }


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main():
    res = ROOT / "results"
    required = ["metrics.json", "runtime_policy.json", "robustness.json", "stress.json"]
    if any(not (res / "eval" / name).exists() for name in required) or not (res / "latency.json").exists():
        raise FileNotFoundError("complete fresh evaluation, runtime, robustness, stress and export first")
    stages = json.loads((res / "logs" / "hardening_stages.json").read_text())
    if not stages or any(stage["exit_code"] != 0 for stage in stages):
        raise ValueError("stage ledger must contain only completed successful verification commands")
    frozen = res / "frozen"
    frozen.mkdir(exist_ok=True)
    for name in required:
        shutil.copy2(res / "eval" / name, frozen / name)
    shutil.copy2(res / "latency.json", frozen / "latency.json")
    shutil.copy2(res / "logs" / "hardening_stages.json", frozen / "hardening_stages.json")
    data = inventory(ROOT / "data" / "episodes", ["*.parquet", "*.json", "index.csv"])
    external = inventory(ROOT / "data" / "opssat-ad", ["*.csv", "*.npz"])
    (res / "DATASET_SHA256SUMS.json").write_text(json.dumps({**data, **external}, indent=1))
    manifest = {
        "schema": "CASSANDRA hardened run v1",
        "git": {
            "branch": git("branch", "--show-current"),
            "source_commit": git("rev-parse", "HEAD"),
            "working_changes": git("status", "--porcelain"),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": {
                name: importlib.metadata.version(name)
                for name in ["torch", "numpy", "pandas", "pyarrow", "pydantic", "paho-mqtt", "scikit-learn"]
            },
        },
        "configs": inventory(ROOT / "ml" / "configs", ["*.yaml"])
        | inventory(ROOT / "eval" / "configs", ["*.yaml"])
        | inventory(ROOT / "cassandra", ["*.yaml"]),
        "source": inventory(ROOT / "ml", ["*.py"])
        | inventory(ROOT / "cassandra", ["*.py"])
        | inventory(ROOT / "aurora_common", ["*.py"])
        | inventory(ROOT / "eval", ["*.py"])
        | inventory(ROOT / "twin", ["*.py"]),
        "models": inventory(ROOT / "data" / "models", ["*.pt", "*.npz", "*.json"]),
        "dataset_inventory_sha256": sha(res / "DATASET_SHA256SUMS.json"),
        "results": inventory(frozen, ["*.json"]),
        "verification_commands": stages,
        "limits": [
            "Custom synthetic twin; OPS-SAT supplies residual noise and gaps only.",
            "Actual Core mismatch campaign varies thermal plant only.",
            "Legacy closed_loop snapshot evaluator excluded from primary claims.",
            "No physical thermal test, embedded ARM timing, native NOS3/cFS or ESA-ADB validation.",
        ],
    }
    (res / "FINAL_RUN_MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    print("Frozen", len(data), "episode files and", len(manifest["models"]), "model artifacts")


if __name__ == "__main__":
    main()
