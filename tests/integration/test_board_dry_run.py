"""Phase 8 software check without hardware: the ESP8266 bridge with its fake board plays satellite B
next to 11 twin satellites, through the real broker, in real time (the board model runs on the wall
clock). A1 on B must be flagged, contained (action chosen by J) and healed before B's 60 C limit,
with the LEDs following B's state. Skipped without a broker (make up)."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid

import pytest

from aurora_common import topics as T
from aurora_common.bus import Bus
from tests.integration.test_a1_software import ROOT, Proc, _broker_up

pytestmark = [pytest.mark.slow, pytest.mark.skipif(not _broker_up(), reason="no MQTT broker (make up)")]


def test_a1_on_the_board_dry_run():
    seen = {"B": [], "temp": [], "src": set(), "actions": [], "events": []}
    lock = threading.Lock()
    t_start = time.time()

    def on_msg(topic, msg):
        with lock:
            kind = T.kind_of(topic)[0]
            if kind == "state" and msg.sat == "B" and msg.t >= t_start - 1:
                seen["B"].append(msg.state)
            elif kind == "tm":
                seen["temp"].append(msg.fields["temp_c"])
                seen["src"].add(msg.src)
            elif kind == "action":
                seen["actions"].append(msg)
            elif kind == "event":
                seen["events"].append(msg)

    w = Bus(f"board-watch-{uuid.uuid4().hex[:6]}")
    for f in (T.state("B"), T.tm("B", "thermal"), T.ACTION, T.EVENT):
        w.subscribe(f, on_msg)
    assert w.start()
    core = Proc(
        "core", ["-m", "cassandra", "--twin-sats", "A,C-L", "--client-id", f"bd-core-{uuid.uuid4().hex[:6]}"]
    )
    plant = board = None
    try:
        assert core.ready("CASSANDRA core running"), core.log()
        assert any("trajectory-v0 for hardware B" in ln for ln in core.lines), core.log()
        plant = Proc(
            "plant", ["scripts/replay_twin.py", "--sats", "A,C-L", "--speed", "1", "--duration", "600"]
        )
        board = Proc("board", ["hardware/esp8266_bridge/bridge.py", "--dry-run", "--sat", "B"])
        assert plant.ready("replay twin:") and board.ready("connected to"), plant.log() + board.log()
        time.sleep(12)  # windows fill, everything nominal
        r = subprocess.run(
            [sys.executable, "-m", "attacks.run", "--scenario", "A1", "--target", "B"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        deadline = time.time() + 90
        while time.time() < deadline:
            with lock:
                st = list(seen["B"])
            if "flagged" in st and st[-1] == "nominal" and st.index("flagged") < len(st) - 1:
                break
            time.sleep(0.5)
        time.sleep(2)
    finally:
        for p in (board, plant):
            if p:
                p.stop()
        core.stop()
        w.stop()
    logs = core.log() + "\n" + (board.log() if board else "")
    st = seen["B"]
    assert "flagged" in st and st[-1] == "nominal", f"B states {st}\n{logs}"
    acts = [a for a in seen["actions"] if a.target == "B" and a.action != "none"]
    assert acts, logs
    assert max(seen["temp"]) < 60.0, f"B reached {max(seen['temp']):.1f} C\n{logs}"
    assert seen["src"] == {"hw"}  # B's telemetry came from the bridge, not the twin
    assert any("TC HEATER {'on': True} from ground: accepted" in ln for ln in board.lines), logs
    assert any(
        " B:R " in ln or ln.endswith(" B:R") or "B:R" in ln for ln in board.lines
    ), "red LED never shown"
    heal = [e for e in seen["events"] if e.sat == "B" and e.kind == "heal" and "healed" in e.text]
    assert heal, logs
