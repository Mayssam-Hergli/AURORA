"""cassandra/monitor.py: heater-vs-temperature consistency (Scenario B), recovery, and no false alarms
on a clean replay run (legitimate heater tests included)."""

import numpy as np

from aurora_common import topics as T
from cassandra import config
from cassandra.ingest import Ingest
from cassandra.monitor import Monitor
from tests.cassandra.helpers import BOARD, T0, cfg_and_profiles, thermal, world


def run(mon, temps, heaters, sat="B", t0=T0):
    events = []
    for k, (temp, h) in enumerate(zip(temps, heaters)):
        events += mon.step(world(t0 + k, {sat: thermal(temp, h)}))
    return events


def board_monitor():
    cfg, prof = cfg_and_profiles(sats=("B",), overrides={"B": BOARD})
    return Monitor(prof, cfg)


def test_honest_heating_and_honest_idle_raise_nothing():
    mon = board_monitor()
    rng = np.random.default_rng(1)
    temps = [25 + 1.5 * k + rng.uniform(-0.1, 0.1) for k in range(20)] + [
        55 - k + rng.uniform(-0.1, 0.1) for k in range(20)
    ]
    heaters = [1] * 20 + [0] * 20
    assert run(mon, temps, heaters) == []
    assert mon.untrusted["B"] == set()


def test_frozen_temperature_under_a_running_heater_is_caught():
    mon = board_monitor()
    temps = [25 + 1.5 * k for k in range(4)] + [30.0] * 20  # attacker freezes the report at 30 C
    events = run(mon, temps, [1] * 24)
    (hit,) = events
    assert hit.kind == "hit" and hit.rule == "heater_inconsistent" and hit.sat == "B"
    assert "DE-0002" in hit.sparta and mon.untrusted["B"] == {"temp_c"}


def test_noisy_freeze_is_caught_too():
    """The offline engine's B1 freeze adds sensor noise to the held value: no exact constant."""
    cfg, prof = cfg_and_profiles(sats=("E",))
    mon = Monitor(prof, cfg)
    rng = np.random.default_rng(2)
    temps = [24.0 + rng.normal(0, 0.15) for _ in range(20)]
    events = run(mon, temps, [1] * 20, sat="E")
    assert [e.rule for e in events] == ["heater_inconsistent"]


def test_exactly_constant_temperature_with_heater_off_is_frozen():
    mon = board_monitor()
    (hit,) = run(mon, [26.0] * 12, [0] * 12)
    assert hit.rule == "frozen" and hit.sparta == ("DE-0002",)


def test_trust_returns_after_consistent_ticks():
    mon = board_monitor()
    temps = [30.0] * 12 + [30.0 - 0.8 * k + 0.05 * (k % 2) for k in range(1, 25)]  # thaws and cools
    heaters = [1] * 12 + [0] * 24
    events = run(mon, temps, heaters)
    assert [e.kind for e in events] == ["hit", "clear"]
    assert mon.untrusted["B"] == set()


def test_a_gap_restarts_the_window():
    mon = board_monitor()
    events = []
    for k in range(20):
        vals = {} if k == 8 else {"B": thermal(30.0, 1)}
        events += mon.step(world(T0 + k, vals))
    assert len(events) == 1  # needs 10 fresh ticks after the gap: hit at k = 18, not at k = 9


def test_clean_replay_run_raises_no_alarm():
    """Ten minutes of the stand-in plant, all 12 twin satellites, legitimate heater tests included."""
    from scripts.replay_twin import ReplayTwin, build_twin

    cfg = config.load()
    prof = config.profiles(cfg)
    ing, mon = Ingest(prof, cfg), Monitor(prof, cfg)

    class Pipe:
        def publish(self, topic, model):
            ing.feed(topic, model, model.t)

    rt = ReplayTwin(Pipe(), build_twin(seed=11, duration_s=600), list(T.SATS), start_t=T0)
    events, heater_ticks = [], 0
    for _ in range(600):
        rt.step()
        for tick in ing.poll(wall=T0 + rt.twin.t):
            events += mon.step(tick)
            heater_ticks += sum(v["thermal"]["heater_on"] for v in tick.values.values() if "thermal" in v)
    assert heater_ticks > 0, "the run should contain legitimate heater activity"
    assert events == [], [e.text for e in events]
