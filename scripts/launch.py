"""Run several processes as one: prefixed output, one Ctrl-C stops them all.

    python scripts/launch.py "twin=python scripts/replay_twin.py" "core=python -m cassandra"
If any process exits, the others are stopped too, and the exit code is the first non-zero one.
"""

from __future__ import annotations

import shlex
import signal
import subprocess
import sys
import threading
import time

COLORS = ["\033[36m", "\033[35m", "\033[33m", "\033[32m", "\033[34m"]
RESET = "\033[0m"


def pump(name: str, color: str, proc: subprocess.Popen) -> None:
    for line in proc.stdout:
        sys.stdout.write(f"{color}{name:>6} |{RESET} {line}")
        sys.stdout.flush()


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    procs: list[tuple[str, subprocess.Popen]] = []
    for i, spec in enumerate(argv):
        name, _, cmd = spec.partition("=")
        p = subprocess.Popen(
            shlex.split(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,  # Ctrl-C reaches only the launcher, which forwards it once
        )
        procs.append((name, p))
        threading.Thread(target=pump, args=(name, COLORS[i % len(COLORS)], p), daemon=True).start()
    code = 0
    try:
        while all(p.poll() is None for _, p in procs):
            time.sleep(0.2)
        code = next((p.returncode for _, p in procs if p.returncode), 0)
        gone = [n for n, p in procs if p.poll() is not None]
        print(f"launch: {', '.join(gone)} exited; stopping the rest", flush=True)
    except KeyboardInterrupt:
        print("\nlaunch: stopping ...", flush=True)
    for _, p in procs:
        if p.poll() is None:
            p.send_signal(signal.SIGINT)
    deadline = time.time() + 5
    for _, p in procs:
        try:
            p.wait(max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            p.kill()
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
