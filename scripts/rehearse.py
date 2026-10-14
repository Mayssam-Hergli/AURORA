"""Unattended demo rehearsal in software (docs/DEMO_SCRIPT.md, Parts 1 and 2), timed.

    PY=$HOME/aurora-venv/bin/python; $PY scripts/rehearse.py [--runs 2] [--out results/rehearsal.json]

Part 1, board (real time): `make demo-board-dry` (the fake board runs the ESP8266 firmware's thermal model),
A1 on B, wait for healed; then A1 --reassert on B (escalation to safe mode), stop, wait for healed.
Part 2, software constellation at 10x: `make demo-sw SPEED=10 SEED=2`, B1 on E (masking), stop; C1 from D
(relay), wait until the source is quarantined, stop.
It listens on the bus like the dashboard (never gt/ or redteam/) and records the wall-clock time of every
milestone after its attack command. A missing milestone is a failure (exit 1).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aurora_common import topics as T  # noqa: E402
from aurora_common.bus import Bus  # noqa: E402

VENV = os.environ.get("VENV", str(Path.home() / "aurora-venv"))


class Watch:
    """What a dashboard would see: state changes, events (incl. incidents) and actions, with wall time."""

    def __init__(self):
        self.lock = threading.Lock()
        self.log: list[tuple[float, str, object]] = []
        self.bus = Bus(f"rehearse-{uuid.uuid4().hex[:6]}")
        for f in (f"{T.PREFIX}/sat/+/state", T.ACTION, T.EVENT):
            self.bus.subscribe(f, self.on)
        assert self.bus.start(), "no broker: make up"

    def on(self, topic, msg):
        with self.lock:
            self.log.append((time.time(), T.kind_of(topic)[0], msg))

    def mark(self) -> float:
        return time.time()

    def wait(self, since: float, pred, timeout: float) -> float | None:
        """Seconds after `since` at which a message satisfying pred(kind, msg) arrived, or None."""
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                for t, kind, msg in self.log:
                    if t >= since and pred(kind, msg):
                        return round(t - since, 1)
            time.sleep(0.2)
        return None


def make(*args, bg=False, log=None):
    cmd = ["make", "--no-print-directory", *args, f"VENV={VENV}"]
    if bg:
        f = open(log, "w") if log else subprocess.DEVNULL
        return subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=120)


def stop(p):
    if p and p.poll() is None:
        os.killpg(p.pid, signal.SIGINT)
        try:
            p.wait(15)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
    time.sleep(2)


def wait_log(path: Path, text: str, timeout=240) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if path.exists() and text in path.read_text(errors="ignore"):
            return True
        time.sleep(1)
    return False


def state(sat, st):
    return lambda k, m: k == "state" and m.sat == sat and m.state == st


def action(sat, name=None):
    return (
        lambda k, m: k == "action"
        and m.target == sat
        and m.action != "none"
        and (name is None or m.action == name)
    )


def incident_has(sat, step_kind):
    return lambda k, m: (
        k == "event"
        and m.kind == "incident"
        and sat in (m.data or {}).get("sats", [])
        and any(s["kind"] == step_kind for s in (m.data or {}).get("steps", []))
    )


def heal(sat):
    return lambda k, m: k == "event" and m.kind == "heal" and m.sat == sat


def part1(w: Watch, logdir: Path) -> dict:
    out = {}
    log = logdir / "rehearse_part1.log"
    p = make("demo-board-dry", bg=True, log=log)
    try:
        assert wait_log(log, "connected to") and wait_log(log, "core running"), "Part 1 stack did not start"
        time.sleep(15)
        t0 = w.mark()
        make("attack", "A1", "TARGET=B")
        out["A1_board"] = {
            "flagged_s": w.wait(t0, state("B", "flagged"), 60),
            "chain_command_s": w.wait(t0, incident_has("B", "command"), 60),
            "heater_cut_s": w.wait(t0, action("B", "isolate_subsystem"), 90),
            "healed_s": w.wait(t0, heal("B"), 150),
        }
        make("attack-stop", "TARGET=B")
        time.sleep(20)
        t0 = w.mark()
        make("attack", "A1", "TARGET=B", "ATTACK_ARGS=--reassert 5 --duration 40")
        out["A1_reassert_board"] = {
            "first_action_s": w.wait(t0, action("B"), 90),
            "safe_mode_s": w.wait(t0, action("B", "safe_mode"), 120),
        }
        make("attack-stop", "TARGET=B")
        out["A1_reassert_board"]["healed_after_stop_s"] = w.wait(t0, heal("B"), 240)
    finally:
        stop(p)
    return out


def part2(w: Watch, logdir: Path) -> dict:
    out = {}
    log = logdir / "rehearse_part2.log"
    p = make("demo-sw", "SPEED=10", "SEED=2", bg=True, log=log)
    try:
        assert wait_log(log, "core running"), "Part 2 stack did not start"
        time.sleep(15)
        t0 = w.mark()
        make("attack", "B1", "TARGET=E")
        out["B1_10x"] = {
            "deception_s": w.wait(t0, incident_has("E", "deception"), 120),
            "contained_s": w.wait(t0, action("E"), 240),
        }
        make("attack-stop", "TARGET=E")
        out["B1_10x"]["healed_s"] = w.wait(t0, heal("E"), 300)
        time.sleep(10)
        t0 = w.mark()
        make("attack", "C1", "TARGET=D")
        out["C1_10x"] = {
            "source_flagged_s": w.wait(t0, state("D", "flagged"), 120),
            "source_quarantined_s": w.wait(t0, action("D", "quarantine"), 240),
        }
        make("attack-stop", "TARGET=D")
    finally:
        stop(p)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--out", default=str(ROOT / "results" / "rehearsal.json"))
    a = ap.parse_args(argv)
    logdir = ROOT / "results" / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    assert make("up").returncode == 0, "make up failed"
    w = Watch()
    runs, ok = [], True
    for k in range(a.runs):
        t0 = time.time()
        r = {"run": k + 1, **part1(w, logdir), **part2(w, logdir)}
        r["wall_s"] = round(time.time() - t0, 1)
        missing = [
            f"{s}.{m}" for s, ms in r.items() if isinstance(ms, dict) for m, v in ms.items() if v is None
        ]
        r["missing"] = missing
        ok &= not missing
        runs.append(r)
        print(json.dumps(r), flush=True)
    w.bus.stop()
    Path(a.out).write_text(
        json.dumps(
            {
                "what": "Unattended demo rehearsal in software (board dry run real time; constellation "
                "at 10x). Seconds after each attack command, wall clock, as the dashboard receives them.",
                "command": f"python scripts/rehearse.py --runs {a.runs}",
                "runs": runs,
            },
            indent=1,
        )
    )
    print("REHEARSAL", "OK" if ok else "FAILED", f"-> {a.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
