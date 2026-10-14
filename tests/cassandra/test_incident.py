"""Incident correlator (cassandra/incident.py): the attack chain per scenario, built only from what the
core observes. The core never subscribes to gt/ or redteam/ (test_gt_isolation.py), so nothing here
can come from ground truth: the plant's red-team effects reach the core only as telemetry."""

import pytest

from aurora_common import schemas as S
from aurora_common import topics as T
from tests.cassandra.loop import Loop


def redteam(lp, sat, effect, args):
    lp.plant.on_redteam(T.redteam(sat), S.RedTeam(t=lp.now(), sat=sat, effect=effect, args=args))


def chains(lp) -> dict:
    """incident id -> its latest core/event (kind incident)."""
    out = {}
    for e in lp.events:
        if e.kind == "incident":
            S.parse(T.EVENT, S.dump(e))  # every incident message satisfies the contract
            out[e.data["incident_id"]] = e
    return out


def kinds(ev) -> list[str]:
    return [s["kind"] for s in ev.data["steps"]]


def is_subsequence(want, have) -> bool:
    it = iter(have)
    return all(k in it for k in want)


def test_a1_chain_command_effect_predicted_action_healed():
    lp = Loop(seed=3, duration_s=900).run(60)
    lp.send("E", "HEATER", {"on": True})
    lp.run(500)
    (inc,) = [e for e in chains(lp).values() if "E" in e.data["sats"]]
    steps = inc.data["steps"]
    assert is_subsequence(["command", "effect", "predicted", "action", "healed"], kinds(inc)), kinds(inc)
    cmd = steps[0]
    assert cmd["kind"] == "command" and cmd["evidence"]["origin"] == "ground" and cmd["observed"]
    assert [t["id"] for t in cmd["sparta"]] == ["IA-0009"] and cmd["sparta"][0]["tactic"] == "Initial Access"
    for s in steps:  # forecasts are marked as forecasts, everything else is observed
        assert s["observed"] == (s["kind"] != "predicted"), s
        assert s["sparta"], s  # every step carries a SPARTA tag (interfaces.md 7)
        assert "P60" not in s["text"] and "tgn" not in s["text"]
    pred = [s for s in steps if s["kind"] == "predicted"]
    assert len(pred) == 1  # one forecast step per satellite, refreshed, never stacked
    assert "next affected subsystem: power" in pred[0]["text"] and "rule-based" in pred[0]["text"]
    assert "temperature limit in" in pred[0]["text"] and pred[0]["evidence"]["p_malicious"] is None
    assert inc.data["status"] in ("contained", "closed") and inc.data["assembly_delay_s"] >= 0


@pytest.fixture(scope="module")
def b1():
    lp = Loop(seed=3, duration_s=900).run(60)
    redteam(lp, "E", "freeze_tm", {"sub": "thermal", "field": "temp_c"})
    lp.send("E", "HEATER", {"on": True})
    lp.run(400)
    redteam(lp, "E", "stop", {})
    lp.send("E", "HEATER", {"on": False})
    return lp.run(200)


def test_b1_chain_includes_the_deception(b1):
    (inc,) = [e for e in chains(b1).values() if "E" in e.data["sats"]]
    dec = [s for s in inc.data["steps"] if s["kind"] == "deception"]
    assert dec and dec[0]["observed"] and "masked" in dec[0]["text"]
    assert (
        "DE-0002" in [t["id"] for t in dec[0]["sparta"]]
        and dec[0]["sparta"][0]["tactic"] == "Defense Evasion"
    )
    assert is_subsequence(["command", "deception", "action"], kinds(inc)), kinds(inc)
    eff = [s for s in inc.data["steps"] if s["kind"] == "effect"]
    assert all(
        ("estimated" in s["text"]) == (not s["observed"]) for s in eff
    )  # an estimate is not an observation


@pytest.fixture(scope="module")
def c1_slow_source():
    """C1 from D (seed 2) with the pre-fix sandbox (future_victim_risk 0): D relays for minutes and
    reaches two victims before J quarantines it, so the chain must link more than one victim. With the
    fix, D is quarantined after its first victim (docs/STATUS.md Block 3)."""
    lp = Loop(seed=2, duration_s=1000, cfg_overrides={"sandbox": {"future_victim_risk": 0.0}}).run(60)
    redteam(lp, "D", "relay_tc", {"cmd": "HEATER", "args": {"on": True}, "period_s": 10.0, "delay_s": 2.0})
    lp.run(500)
    redteam(lp, "D", "stop", {})
    return lp.run(200)


def test_c1_chain_links_victims_to_the_source_and_shows_its_containment(c1_slow_source):
    (inc,) = [e for e in chains(c1_slow_source).values() if e.data["source"] == "D"]
    d = inc.data
    assert len(d["victims"]) >= 2, d["victims"]
    for v in d["victims"]:  # each victim is linked to D by the relayed command it received
        cmds = [s for s in d["steps"] if s["sat"] == v and s["kind"] == "command"]
        assert cmds and all(s["evidence"]["origin"] == "isl:D" for s in cmds), v
        assert all([t["id"] for t in s["sparta"]] == ["IA-0003"] for s in cmds)
    src = [s for s in d["steps"] if s["sat"] == "D" and s["kind"] == "action"]
    assert src and "quarantined" in src[0]["text"] and "CM0070" in [t["id"] for t in src[0]["sparta"]]
    assert len(d["victims_touched_before_source_contained"]) >= 2


def test_c1_with_the_fix_contains_the_source_after_one_victim():
    lp = Loop(seed=2, duration_s=600).run(60)
    redteam(lp, "D", "relay_tc", {"cmd": "HEATER", "args": {"on": True}, "period_s": 10.0, "delay_s": 2.0})
    lp.run(200)
    (inc,) = [e for e in chains(lp).values() if e.data["source"] == "D"]
    assert inc.data["victims_touched_before_source_contained"] is not None
    assert len(inc.data["victims_touched_before_source_contained"]) <= 1


def test_a_clean_run_opens_no_incident():
    """Brief false flags at the ensemble's alert level are risk flags, not attack chains."""
    lp = Loop(seed=1, duration_s=700, cfg_overrides={"forecast": {"ensemble": {"enabled": True}}}).run(600)
    assert chains(lp) == {}


def test_a_risk_flag_that_calms_down_is_dismissed_not_left_open():
    """A ground heater test (legitimate, short) raises the risk; the satellite calms down by itself."""
    lp = Loop(seed=3, duration_s=700).run(60)
    lp.send("E", "HEATER", {"on": True})
    lp.run(40)
    lp.send("E", "HEATER", {"on": False})
    lp.run(300)
    for e in chains(lp).values():
        if "E" in e.data["sats"] and not e.data["confirmed"]:
            assert e.data["status"] == "closed" and e.data["dismissed"]


def test_c1_chain_is_complete_ordered_and_every_step_has_text():
    """With the fix (source quarantined quickly): relay -> victim command (collapsed) -> effect -> source
    quarantined (with its time); steps in time order; no empty step (seen empty on the dashboard, 14 Oct)."""
    lp = Loop(seed=2, duration_s=900).run(60)
    redteam(lp, "D", "relay_tc", {"cmd": "HEATER", "args": {"on": True}, "period_s": 10.0, "delay_s": 2.0})
    lp.run(400)
    (inc,) = [e for e in chains(lp).values() if e.data["source"] == "D"]
    steps = inc.data["steps"]
    assert all(s["text"].strip() for s in steps)
    assert [s["t"] for s in steps] == sorted(s["t"] for s in steps)
    for v in inc.data["victims"]:
        cmds = [s for s in steps if s["sat"] == v and s["kind"] == "command"]
        assert len(cmds) == 1, cmds  # every relayed copy to one victim is one step
        assert any(s["sat"] == v and s["kind"] == "effect" for s in steps), v
    act = [s for s in steps if s["sat"] == "D" and s["kind"] == "action"]
    assert act and "quarantined" in act[0]["text"] and "links cut" in act[0]["text"] and act[0]["t"] > 0
