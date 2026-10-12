"""Gate G4: software-only A1 through the real broker, no manual step.

Three separate processes talk only over Mosquitto: the plant (scripts/replay_twin.py, 12 twin
satellites at 20x), the CASSANDRA core (python -m cassandra) and the attacker (python -m attacks.run
--scenario A1 --target E). This test watches the public topics like a dashboard would and checks that
E is flagged, contained and healed before its critical temperature, and that nobody else is touched.
Skipped when no broker listens on localhost:1883 (make up).
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from aurora_common import schemas as S
from aurora_common import topics as T
from aurora_common.bus import Bus

ROOT = Path(__file__).resolve().parents[2]
SPEED = 20


def _broker_up() -> bool:
    try:
        with socket.create_connection(("localhost", 1883), timeout=0.5):
            return True
    except OSError:
        return False


pytestmark = [pytest.mark.slow, pytest.mark.skipif(not _broker_up(), reason="no MQTT broker (make up)")]


class Proc:
    """A child process whose output is collected; ready() waits for a line containing `marker`."""

    def __init__(self, name: str, args: list[str]):
        self.name, self.lines = name, []
        self.p = subprocess.Popen(
            [sys.executable, "-u", *args],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            self.lines.append(line.rstrip())

    def ready(self, marker: str, timeout: float = 240) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if any(marker in ln for ln in self.lines):
                return True
            if self.p.poll() is not None:
                return False
            time.sleep(0.1)
        return False

    def stop(self):
        if self.p.poll() is None:
            self.p.send_signal(2)  # SIGINT: clean shutdown
            try:
                self.p.wait(5)
            except subprocess.TimeoutExpired:
                self.p.kill()

    def log(self) -> str:
        return f"--- {self.name} ---\n" + "\n".join(self.lines[-40:])


def live_run(attack_steps: list[list[str]], seed: int = 3, settle_sim_s: int = 600) -> dict:
    """Core + plant (20x) + the given attacker commands, run one after the other. Returns what a
    dashboard would have seen: node states, actions, events and E's reported temperature."""
    seen = {"state": [], "action": [], "event": [], "temp_E": [], "logs": ""}
    lock = threading.Lock()
    t_start = time.time()

    def on_msg(topic, msg):
        with lock:
            kind = T.kind_of(topic)[0]
            if kind == "state" and msg.t >= t_start - 1:  # ignore retained states of earlier runs
                seen["state"].append((msg.sat, msg.state, msg.t))
            elif kind == "action":
                seen["action"].append(msg)
            elif kind == "event":
                seen["event"].append(msg)
            elif kind == "tm":
                seen["temp_E"].append(msg.fields["temp_c"])
                seen["latest_tm_t"] = msg.t

    def e_states():
        with lock:
            return [s for sat, s, _ in seen["state"] if sat == "E"]

    watcher = Bus(f"g4-watch-{uuid.uuid4().hex[:6]}")
    for f in (f"{T.PREFIX}/sat/+/state", T.tm("E", "thermal"), T.ACTION, T.EVENT):
        watcher.subscribe(f, on_msg)
    assert watcher.start()
    core = Proc("core", ["-m", "cassandra", "--client-id", f"g4-core-{uuid.uuid4().hex[:6]}"])
    plant = None
    try:
        assert core.ready("CASSANDRA core running"), core.log()
        # Retained node states of earlier runs arrive right after subscribing. The t filter alone does
        # not catch them: a plant at 20x stamps simulated time, which runs ahead of the wall clock.
        with lock:
            seen["state"].clear()
        plant = Proc(
            "plant",
            ["scripts/replay_twin.py", "--speed", str(SPEED), "--duration", "1800", "--seed", str(seed)],
        )
        assert plant.ready("replay twin:"), plant.log()
        time.sleep(60 / SPEED)  # one minute of clean operations first
        for argv in attack_steps:
            r = subprocess.run(
                [sys.executable, "-m", "attacks.run", *argv],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert r.returncode == 0, r.stdout + r.stderr
        with lock:
            seen["attack_stopped_t"] = seen.get("latest_tm_t", 0)
        deadline = time.time() + settle_sim_s / SPEED + 30
        while time.time() < deadline:
            st = e_states()
            if "flagged" in st and st[-1] == "nominal" and st.index("flagged") < len(st) - 1:
                break
            time.sleep(0.5)
        time.sleep(1.0)
    finally:
        if plant:
            plant.stop()
        core.stop()
        watcher.stop()
    seen["logs"] = core.log() + "\n" + (plant.log() if plant else "")
    seen["E"] = e_states()
    return seen


def check_contained_and_healed(seen: dict) -> list:
    logs, st = seen["logs"], seen["E"]
    assert "flagged" in st and st[-1] == "nominal", f"E states {st}\n{logs}"
    acts = [a for a in seen["action"] if a.action != "none"]
    assert {a.target for a in acts} == {"E"}, f"actions {[(a.target, a.action) for a in acts]}\n{logs}"
    assert max(seen["temp_E"]) < 45.0, f"E reached {max(seen['temp_E']):.1f} C"
    heal = [e for e in seen["event"] if e.kind == "heal" and e.sat == "E" and "healed" in e.text]
    assert heal, logs
    others = {(sat, s) for sat, s, _ in seen["state"] if sat != "E" and s != "nominal"}
    assert not others, f"other satellites changed state: {others}"
    for a in seen["action"]:
        S.parse(T.ACTION, S.dump(a))
    return acts


def test_a1_flagged_contained_and_healed_through_the_broker():
    seen = live_run([["--scenario", "A1", "--target", "E"]])
    acts = check_contained_and_healed(seen)
    assert acts[0].action in ("cut_heater", "safe_mode", "quarantine")


def test_a1_reassert_escalates_beyond_isolation_and_heals_after_stop():
    """--reassert: the attacker keeps re-sending HEATER on (every 5 simulated s at 20x), then stops."""
    period_wall, attack_wall = 5 / SPEED, 300 / SPEED
    seen = live_run(
        [
            [
                "--scenario",
                "A1",
                "--target",
                "E",
                "--reassert",
                str(period_wall),
                "--duration",
                str(attack_wall),
            ],
            ["--stop", "--target", "E"],
        ]
    )
    acts = check_contained_and_healed(seen)
    assert acts[-1].action in ("safe_mode", "quarantine"), [a.action for a in acts]  # beyond isolation
    hold = [e for e in seen["event"] if e.sat == "E" and "holding containment" in e.text]
    heal = [e for e in seen["event"] if e.sat == "E" and e.kind == "heal" and "healed" in e.text]
    assert heal[0].t > seen["attack_stopped_t"], seen["logs"]
    if hold:  # Quarantine can reject commands before the hold diagnostic is needed.
        assert heal[0].t > hold[0].t, seen["logs"]
