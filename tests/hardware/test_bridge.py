"""hardware/esp8266_bridge/bridge.py with its fake board (no serial port, no broker): contract-valid
telemetry, bridge-enforced SAFE, LED indices 1/2/3, red-team freeze, CSV log."""

import csv
import sys
from pathlib import Path

import pytest

from aurora_common import schemas as S

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hardware" / "esp8266_bridge"))
from bridge import Bridge, FakeBoard  # noqa: E402

T0 = 1_800_000_000.0


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


class Rig:
    """Fake board + bridge on one simulated clock; published messages are validated as they go out."""

    def __init__(self, log_path=None):
        self.mono = Clock(100.0)
        self.board = FakeBoard(seed=1, clock=self.mono)
        self.sent: list[tuple[str, object]] = []
        self.lines: list[str] = []

        def publish(topic, payload, qos, retain):
            self.sent.append((topic, S.parse(topic, payload)))

        self.br = Bridge(
            self.board, publish, "B", log_path, clock=lambda: T0 + self.mono.t, echo=self.lines.append
        )

    def run(self, seconds: float, dt: float = 0.1):
        for _ in range(int(round(seconds / dt))):
            self.mono.t += dt
            self.br.poll_serial()

    def tc(self, cmd, args=None, origin="ground"):
        self.br.on_tc(
            {"v": 1, "id": "x", "t": T0, "sat": "B", "origin": origin, "cmd": cmd, "args": args or {}}
        )

    def thermal(self):
        return [m for t, m in self.sent if t.endswith("/tm/thermal")]


def test_telemetry_is_contract_valid_hardware_and_marked_emulated():
    rig = Rig()
    rig.run(3)
    th = rig.thermal()
    assert len(th) == 6  # 2 Hz
    assert all(m.src == "hw" and m.emulated == ["temp_c"] and m.sat == "B" for m in th)
    assert [m.seq for m in th] == list(range(1, 7))
    assert abs(th[-1].fields["temp_c"] - 25.0) < 0.2
    rig.br.obc()
    (obc,) = [m for t, m in rig.sent if t.endswith("/tm/obc")]
    assert obc.fields == {"mode": "NOMINAL", "cmd_ok": 0, "cmd_rej": 0, "last_origin": ""}


def test_heater_command_heats_at_the_firmware_rate():
    rig = Rig()
    rig.run(1)
    rig.tc("HEATER", {"on": True})
    rig.run(10)
    t = rig.thermal()[-1].fields
    assert t["heater_on"] == 1 and t["temp_c"] == pytest.approx(25 + 1.5 * 10, abs=1.0)
    rig.tc("HEATER", {"on": False})
    rig.run(10)
    assert rig.thermal()[-1].fields["temp_c"] < 30


def test_bridge_enforces_safe():
    """interfaces.md 3.2: HEATER on refused from any origin in SAFE; only SET_MODE NOMINAL exits."""
    rig = Rig()
    rig.tc("HEATER", {"on": True})
    rig.run(5)
    rig.tc("SAFE_MODE", origin="core")
    rig.run(1)
    assert not rig.board.heater and rig.br.mode == "SAFE"
    for origin in ("ground", "core"):
        rig.tc("HEATER", {"on": True}, origin=origin)
    rig.tc("SET_MODE", {"mode": "SCIENCE"})
    rig.tc("PING", origin="isl:A")
    rig.tc("SET_MODE", {"on": True})  # malformed probe
    rig.run(1)
    assert not rig.board.heater and rig.br.cmd_rej == 5
    rig.board.press_flash()  # someone presses FLASH on the board while it is in SAFE
    rig.run(1.5)
    assert not rig.board.heater, "the bridge must switch the heater off again in SAFE"
    assert any("forced OFF" in ln for ln in rig.lines)
    rig.tc("HEATER", {"on": False})
    rig.tc("SET_MODE", {"mode": "NOMINAL"}, origin="core")
    assert rig.br.mode == "NOMINAL" and rig.br.cmd_ok >= 3
    rig.tc("HEATER", {"on": True})
    rig.run(0.5)
    assert rig.board.heater


def test_leds_follow_node_states_with_indices_1_2_3():
    rig = Rig()
    for sat, state in (
        ("A", "flagged"),
        ("B", "quarantined"),
        ("C", "nominal"),
        ("B", "safe_hold"),
        ("D", "flagged"),
    ):
        rig.br.on_state(sat, state)
    assert rig.board.leds == {1: "R", 2: "O", 3: "G"}
    rig.br.on_state("B", "nominal")
    assert rig.board.leds[2] == "G"


def test_redteam_freeze_hides_the_real_rise_until_stop():
    rig = Rig()
    rig.tc("HEATER", {"on": True})
    rig.run(2)
    rig.br.on_redteam(
        {"v": 1, "t": T0, "sat": "B", "effect": "freeze_tm", "args": {"sub": "thermal", "field": "temp_c"}}
    )
    frozen = rig.thermal()[-1].fields["temp_c"]
    rig.run(8)
    th = rig.thermal()
    assert {m.fields["temp_c"] for m in th[-10:]} == {frozen}  # reported: frozen
    assert rig.br.temp_true > frozen + 10  # true: still heating
    assert all(m.fields["heater_on"] == 1 for m in th[-10:])  # the heater channel stays honest
    rig.br.on_redteam({"v": 1, "t": T0, "sat": "B", "effect": "stop", "args": {}})
    rig.run(1)
    assert rig.thermal()[-1].fields["temp_c"] > frozen + 10


def test_curve_log(tmp_path):
    path = tmp_path / "curve.csv"
    rig = Rig(log_path=str(path))
    rig.tc("HEATER", {"on": True})
    rig.run(5)
    rig.br.close()
    rows = list(csv.reader(path.open()))
    assert rows[0] == ["t_unix", "temp_true_c", "temp_reported_c", "heater_on", "mode"]
    assert len(rows) == 1 + 10 and rows[-1][3] == "1"
