"""Escalation: repeated hazardous commands are modelled as continuing, a containment that does not
hold is escalated (cut_heater -> safe_mode -> quarantine, each re-planned with J), and healing
waits until the repeated commands stop. In process, replay plant, no broker."""

import pytest

from aurora_common import schemas as S
from aurora_common import topics as T
from cassandra.act import Responder, hazardous
from tests.cassandra.loop import T0, Loop


def attack_with_reassert(period: int, stop_at: int, total: int = 1400, seed: int = 3) -> Loop:
    lp = Loop(seed=seed, duration_s=total + 100).run(60)
    for t in range(60, stop_at):
        if (t - 60) % period == 0:
            lp.send("E", "HEATER", {"on": True})
        lp.run(1)
    lp.send("E", "HEATER", {"on": False})  # attack-stop
    return lp.run(total - stop_at)


@pytest.fixture(scope="module")
def slow_reassert():
    """The second copy arrives after the first containment (cut_heater) is in place."""
    return attack_with_reassert(period=150, stop_at=500)


@pytest.fixture(scope="module")
def fast_reassert():
    """--reassert 5: the threat is already visible when the core first plans."""
    return attack_with_reassert(period=5, stop_at=400)


def test_failed_isolation_escalates_to_safe_mode_with_an_explanation(slow_reassert):
    lp = slow_reassert
    acts = [a.action for a in lp.actions if a.target == "E"]
    assert acts[:2] == ["cut_heater", "safe_mode"]
    (esc,) = [e for e in lp.events if e.sat == "E" and "escalation:" in e.text]
    assert "cut_heater did not hold (heater ON again" in esc.text
    second = [a for a in lp.actions if a.target == "E"][1]
    assert [x.action for x in second.alternatives] == []  # unknown uncertainty withholds quarantine


def test_heal_waits_for_the_repeated_commands_to_stop(slow_reassert):
    lp = slow_reassert
    hold = [e for e in lp.events if e.sat == "E" and "holding containment" in e.text]
    heal = [e for e in lp.events if e.sat == "E" and e.kind == "heal" and "healed" in e.text]
    assert hold and heal and heal[0].t > hold[0].t
    assert heal[0].t - T0 > 500  # not before the attacker stopped at t = 500
    assert [s for _, s in lp.states["E"]][-1] == "nominal"
    assert max(lp.true_temp["E"]) < 45.0


def test_fast_reassert_is_planned_beyond_isolation_from_the_start(fast_reassert):
    lp = fast_reassert
    first = next(a for a in lp.actions if a.target == "E")
    assert first.action in ("safe_mode", "quarantine")
    iso = next(x for x in first.alternatives if x.action == "cut_heater")
    assert iso.J > first.J + 0.5  # the shadow sees the heater latched again after the cut
    assert [s for _, s in lp.states["E"]][-1] == "nominal"
    assert max(lp.true_temp["E"]) < 45.0
    assert {s: v for s, v in lp.states.items() if s != "E" and v} == {}


def test_threats_need_repetition_and_go_quiet():
    lp = Loop(seed=1, duration_s=200)
    r: Responder = lp.core.responder

    def tc(t, origin="ground", sat="E"):
        return S.Telecommand(
            id=f"x{t}{sat}", t=T0 + t, sat=sat, origin=origin, cmd="HEATER", args={"on": True}
        )

    r.hazards.extend([tc(10)])
    assert r.threats(T0 + 12) == []  # once is a latch, not a repeated threat
    r.hazards.extend([tc(15), tc(20)])
    (th,) = r.threats(T0 + 22)
    assert th.period_s == pytest.approx(5) and th.sats == ("E",)
    assert r.threats(T0 + 20 + 2 * 5 + 6) == []  # quiet for 2 x period + 5 s
    relays = [tc(30, "isl:A", "B"), tc(40, "isl:A", "C")]
    r.hazards.extend(relays)
    (th,) = [x for x in r.threats(T0 + 41) if x.origin == "isl:A"]
    assert th.sats == ("B", "C")  # one relaying neighbour, many victims
    assert not hazardous(S.Telecommand(id="y", t=T0, sat="E", origin="core", cmd="HEATER", args={"on": True}))
    assert not hazardous(S.Telecommand(id="z", t=T0, sat="E", origin="ground", cmd="PING"))
    assert T.kind_of(T.tc("E"))[0] == "tc"
