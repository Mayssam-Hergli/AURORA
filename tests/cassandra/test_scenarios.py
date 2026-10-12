"""Phase 5 scenarios with the full core in the loop (replay plant, in process, trajectory forecaster):
B1 masking, C1 relay propagation, faults F1/F2/F5, clean N. What is asserted is what the core really
does; see docs/STATUS.md for the C1 quarantine discussion."""

import pytest

from aurora_common import schemas as S
from aurora_common import topics as T
from tests.cassandra.loop import T0, Loop


def redteam(lp, sat, effect, args):
    lp.plant.on_redteam(T.redteam(sat), S.RedTeam(t=lp.now(), sat=sat, effect=effect, args=args))


def changed(lp, except_=()):
    return {s: v for s, v in lp.states.items() if v and s not in except_}


@pytest.fixture(scope="module")
def b1():
    lp = Loop(seed=3, duration_s=900).run(60)
    redteam(lp, "E", "freeze_tm", {"sub": "thermal", "field": "temp_c"})
    lp.run(1)
    lp.send("E", "HEATER", {"on": True})  # the real effect under the mask
    lp.run(400)
    redteam(lp, "E", "stop", {})
    lp.send("E", "HEATER", {"on": False})
    return lp.run(300)


def test_b1_masking_is_caught_by_the_monitor_within_20_s(b1):
    hit = next(e for e in b1.events if e.kind == "monitor" and e.sat == "E" and e.level == "alarm")
    assert hit.t - (T0 + 61) <= 20 and "DE-0002" in hit.sparta


def test_b1_forecasts_on_the_estimate_contains_and_heals_after_stop(b1):
    dec = next(e for e in b1.events if e.kind == "decision" and e.sat == "E" and "action:" in e.text)
    assert "estimated: reported value untrusted" in dec.text and "DE-0002" in dec.sparta
    acts = [a for a in b1.actions if a.target == "E" and a.action != "none"]
    assert acts and max(b1.true_temp["E"]) < 45.0
    assert [s for _, s in b1.states["E"]][-1] == "nominal"
    assert changed(b1, ("E",)) == {}


@pytest.fixture(scope="module")
def c1():
    lp = Loop(seed=3, duration_s=1200).run(60)
    redteam(lp, "A", "relay_tc", {"cmd": "HEATER", "args": {"on": True}, "period_s": 10.0, "delay_s": 2.0})
    lp.run(500)
    redteam(lp, "A", "stop", {})
    return lp.run(400)


def test_c1_source_is_flagged_as_a_suspected_relay(c1):
    flag = next(e for e in c1.events if e.sat == "A" and "relaying HEATER on" in e.text)
    assert "IA-0003" in flag.text
    assert c1.plant.stats["relayed"] > 0


def test_c1_every_latched_victim_is_contained_before_its_limit(c1):
    victims = {a.target for a in c1.actions if a.action != "none" and a.target != "A"}
    assert victims, "relayed heater commands should force containments"
    for v in victims:
        assert max(c1.true_temp[v]) < 45.0, v
        dec = next(e for e in c1.events if e.sat == v and e.kind == "decision" and "origin=isl:A" in e.text)
        assert "IA-0003" in dec.sparta
    for v in victims:
        assert [s for _, s in c1.states[v]][-1] == "nominal"  # healed once the relays stopped


@pytest.mark.parametrize("fault", ["F1", "F2"])
def test_benign_sensor_faults_trigger_no_action(fault):
    lp = Loop(seed=3, duration_s=900).run(60)
    lp.plant.inject_fault(fault, "E")
    lp.run(600)
    assert [a for a in lp.actions if a.action != "none"] == []
    assert changed(lp) == {}


def test_f5_stuck_heater_escalates_without_quarantine_and_asks_for_ground():
    lp = Loop(seed=3, duration_s=900).run(60)
    lp.plant.inject_fault("F5", "E")
    lp.run(400)
    acts = [a.action for a in lp.actions if a.target == "E" and a.action != "none"]
    assert "quarantine" not in acts and acts[:2] == ["isolate_subsystem", "safe_mode"]
    assert any("ground action required" in e.text and "possible hardware fault" in e.text for e in lp.events)
    for e in lp.events:
        if e.kind == "decision" and e.sat == "E" and e.sparta:
            assert not [s for s in e.sparta if s.startswith("IA-")], e.text  # no attack attributed


def test_clean_run_takes_no_action():
    lp = Loop(seed=11, duration_s=700).run(600)
    assert [a for a in lp.actions if a.action != "none"] == [] and changed(lp) == {}
