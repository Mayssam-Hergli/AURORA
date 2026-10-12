"""Run dependent hardening evaluations, recording real commands and stopping on failure."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGES = [
    ("evaluate", ["-m", "eval.harness", "run", "--workers", "2"]),
    ("runtime_policy", ["-m", "eval.runtime_policy", "--seeds", "4"]),
    ("robustness", ["-m", "eval.robustness", "--episodes", "64"]),
    ("stress", ["-m", "eval.stress"]),
    ("export", ["-m", "ml.export"]),
    ("figures", ["-m", "eval.figures"]),
    ("numbers", ["-m", "eval.paper_numbers"]),
]


def main():
    first = sys.argv[1] if len(sys.argv) > 1 else "evaluate"
    names = [name for name, _ in STAGES]
    if first not in names:
        raise ValueError(f"first stage must be one of {names}")
    logs = ROOT / "results" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    ledger = logs / "hardening_stages.json"
    entries = json.loads(ledger.read_text()) if ledger.exists() else []
    for name, args in STAGES[names.index(first) :]:
        command = [sys.executable, *args]
        start = time.time()
        with (logs / f"hardening_{name}.log").open("w") as output:
            proc = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
        entry = dict(
            stage=name,
            command=command,
            start_unix=start,
            seconds=time.time() - start,
            exit_code=proc.returncode,
            log=f"results/logs/hardening_{name}.log",
        )
        entries = [e for e in entries if e["stage"] != name] + [entry]
        ledger.write_text(json.dumps(entries, indent=2))
        print(name, "exit", proc.returncode, round(entry["seconds"]), "s", flush=True)
        if proc.returncode:
            return proc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
