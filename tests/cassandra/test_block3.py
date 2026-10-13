"""13 Oct behaviour fixes: board decision rule, physics-bounded heater monitor and estimate, and the C1
sandbox seeing a continuing relay's future victims (docs/STATUS.md, Block 3)."""

import numpy as np
import pytest

from cassandra import config
from cassandra.act import Responder
from cassandra.monitor import Monitor
from tests.cassandra.helpers import T0, cfg_and_profiles, thermal, world
from tests.cassandra.loop import Loop


# ----------------------------------------------------------------------------- board rule
def test_board_acts_at_p30_085_twins_keep_p60_050():
    cfg = config.with_twin_sats(config.load(), "A,C-L")
    r = Responder(config.profiles(cfg), cfg, sandbox=None)
    assert (r.hz("B"), r.plan_at("B")) == (30, 0.85)  # Phase 2 rule, configurable to 60 s
    assert (r.hz("E"), r.plan_at("E")) == (60, 0.5)
    cfg60 = config.load()
    cfg60["satellites"]["board_profile"]["act_horizon_s"] = 60
    cfg60 = config.with_twin_sats(cfg60, "A,C-L")
    assert Responder(config.profiles(cfg60), cfg60, sandbox=None).hz("B") == 60


# ----------------------------------------------------------------------------- monitor and estimate
def twin_monitor():
    cfg, prof = cfg_and_profiles(sats=("E",))
    return Monitor(prof, cfg), prof["E"]


def test_plausible_heating_bounds_come_from_the_design_model():
    _, e = twin_monitor()
    assert e.min_rise(29.0) == pytest.approx((50 + 30 - 2 * 39) / 300)  # weakest heater, eclipse: ~0.007
    assert e.min_rise(5.0) == pytest.approx((15 + 30 - 2 * 15) / 300)  # cold: may be the thermostat
    assert e.max_rise(20.0) == pytest.approx((90 + 40 + 30 - 2 * 30) / 300)  # ~0.33 C/s: estimate errs early
    assert e.max_rise(40.0) < e.max_rise(20.0)


def test_honest_slow_heating_near_equilibrium_is_not_masking():
    """Seen live 12-13 Oct: a latched 50 W heater in eclipse at ~29 C rises ~0.007 C/s. That is physics,
    not a frozen report: the old fixed 0.02 C/s rule flagged it and the core then acted on an estimate."""
    mon, _ = twin_monitor()
    rng = np.random.default_rng(4)
    events = []
    for k in range(40):
        temp = 28.5 + 0.007 * k + rng.normal(0, 0.03)
        events += mon.step(world(T0 + k, {"E": thermal(temp, 1)}))
    assert events == [] and mon.untrusted["E"] == set()


def test_a_report_that_does_not_rise_where_heating_must_be_fast_is_caught():
    mon, _ = twin_monitor()
    rng = np.random.default_rng(5)
    events = []
    for k in range(20):  # 18 C with the heater on: the slowest plausible rise is ~0.11 C/s
        events += mon.step(world(T0 + k, {"E": thermal(18.0 + rng.normal(0, 0.1), 1)}))
    assert [e.rule for e in events] == ["heater_inconsistent"]


def test_estimate_rises_at_the_fastest_plausible_rate():
    from cassandra.forecast import Trajectory

    _, e = twin_monitor()
    tr = Trajectory(e, 8)
    for k in range(8):
        tr.step(world(T0 + k, {"E": thermal(20.0 + 0.1 * k, 1)}), set())  # last trusted: 20.7 C
    r = None
    for k in range(8, 18):
        r = tr.step(world(T0 + k, {"E": thermal(20.7, 1)}), {"temp_c"})  # frozen report, untrusted
    expected = 20.7
    for _ in range(10):
        expected += e.max_rise(expected)
    assert r.estimated == ("temp_c",) and tr.est_temp == pytest.approx(expected)
    assert r.driver.value == pytest.approx(expected, abs=0.05)  # the robust trend line through the estimates


# ----------------------------------------------------------------------------- C1 sandbox
@pytest.fixture(scope="module")
def c1_snapshot():
    """C1 from A (relay every 10 s): run until the core has seen the relays, keep the last tick."""
    lp = Loop(seed=3, duration_s=900)
    seen = {}
    orig = lp.core.handle_tick

    def keep(tick):
        seen["tick"] = tick
        return orig(tick)

    lp.core.handle_tick = keep
    lp.run(40)
    from aurora_common import schemas as S
    from aurora_common import topics as T

    msg = S.RedTeam(
        t=lp.now(),
        sat="A",
        effect="relay_tc",
        args={"cmd": "HEATER", "args": {"on": True}, "period_s": 10.0, "delay_s": 2.0},
    )
    lp.plant.on_redteam(T.redteam("A"), msg)
    for _ in range(50):  # until the core has seen A relay repeatedly (A needs neighbours in range)
        lp.run(10)
        if any(th.origin == "isl:A" for th in lp.core.responder.threats(seen["tick"].t + 1.0)):
            break
    return lp, seen["tick"]


def test_c1_sandbox_sees_future_victims_of_a_continuing_relay(c1_snapshot):
    lp, tick = c1_snapshot
    r = lp.core.responder
    now = tick.t + 1.0
    threats = [th for th in r.threats(now) if th.origin == "isl:A"]
    assert threats, "the repeated relay from A is a continuing threat"
    scores = {
        s.action: s
        for s in lp.core.sandbox.plan(tick, "A", None, {}, {}, [], True, threats, ("none", "quarantine"))
    }
    none_future = scores["none"].detail["future_victims"]
    quarantine_future = scores["quarantine"].detail["future_victims"]
    assert quarantine_future == []  # A's links cut: the relay reaches no one new
    assert set(quarantine_future) <= set(none_future)
    # J weighs it: every future victim costs as much as a failure, so it is the sandbox, not a rule,
    # that can make quarantining the source the cheapest option
    if none_future:
        assert scores["none"].R - scores["quarantine"].R >= len(none_future) - 1e-6 - 1.0
