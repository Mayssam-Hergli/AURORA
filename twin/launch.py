"""Software-only demo in one command (D1): the twin plays all 12 satellites and the core defends them.

    python -m twin.launch                    # twin (A-L) + CASSANDRA core; one Ctrl-C stops both
    python -m twin.launch --no-core          # twin only
    python -m twin.launch --twin-args "--sats A,C-L"   # leave B to the hardware bridge

The broker must already be up (`make up`; `make demo-sw` does both). If cassandra/__main__.py does not
exist yet (Phase 4 in progress), only the twin starts and a line says so.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time

from ml.config import ROOT


def commands(twin_args: str, core: bool) -> list[tuple[str, list[str]]]:
    out = [("twin", [sys.executable, "-m", "twin.service", *shlex.split(twin_args)])]
    if core:
        if (ROOT / "cassandra" / "__main__.py").exists():
            out.append(("core", [sys.executable, "-m", "cassandra"]))
        else:
            print(
                "core: cassandra/__main__.py does not exist yet (Phase 4); starting the twin only", flush=True
            )
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-core", action="store_true")
    ap.add_argument("--twin-args", default="--sats A-L")
    a = ap.parse_args(argv)
    procs = [(name, subprocess.Popen(cmd, cwd=ROOT)) for name, cmd in commands(a.twin_args, not a.no_core)]
    print(f"started {', '.join(n for n, _ in procs)}. Ctrl-C stops everything.", flush=True)
    code = 0
    try:
        while all(p.poll() is None for _, p in procs):
            time.sleep(0.5)
        code = next(p.returncode for _, p in procs if p.poll() is not None)
        print(f"{next(n for n, p in procs if p.poll() is not None)} exited ({code}); stopping the rest")
    except KeyboardInterrupt:
        pass
    finally:
        for _, p in procs:
            if p.poll() is None:
                p.terminate()
        for _, p in procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
    return code


if __name__ == "__main__":
    sys.exit(main())
