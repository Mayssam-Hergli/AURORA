"""cassandra/act.py and sandbox.py: safety gate (D4), isolation budget, executor commands, the shadow
model's isolation from the real attacker (M2), and the closed loop on A1 (no broker)."""

import json

import pytest

from aurora_common import schemas as S
from aurora_common import topics as T
from cassandra import config
from cassandra.act import Executor, Safety
from cassandra.sandbox import Candidate, EngineAdapter
from tests.cassandra.loop import T0, Loop


def test_uncertainty_gate():
    s = Safety(config.load(), 12)
    assert s.gate(None) is None  # single model: gate inactive, never "certain"
    assert s.gate(0.10) is False
    assert s.gate(0.50) is True


def test_isolation_budget_is_25_percent_per_orbit_window():
    cfg = config.load()
    s = Safety(cfg, 12)
    window = cfg["act"]["orbit_period_s"] / cfg["act"]["orbit_speedup"]
    for k in range(3):
        assert s.budget_ok(T0 + k)
        s.record_quarantine(T0 + k)
    assert not s.budget_ok(T0 + 10)  # 3 of 12 already quarantined in this window
    assert s.budget_ok(T0 + window + 5)  # the window has passed


def test_quarantine_commands_cut_both_ends_and_reroute_only_over_feasible_links():
    ex = Executor()
    links = {
        ("A", "B"): {"feasible": True, "enabled": True},
        ("C", "D"): {"feasible": True, "enabled": False},  # disabled but feasible: re-enabled (reroute)
        ("C", "E"): {"feasible": False, "enabled": False},  # infeasible: never touched
    }
    tcs = ex.carry_out(T0, Candidate("quarantine", "B"), ["A", "C", "D", "E"], links)
    for tc in tcs:
        S.parse(T.tc(tc.sat), S.dump(tc))
        assert tc.origin == "core"
    assert (tcs[0].sat, tcs[0].cmd) == ("B", "SAFE_MODE")
    cut = {(tc.sat, tc.args["peer"]) for tc in tcs if tc.cmd == "ISL_ENABLE" and not tc.args["on"]}
    assert cut == {(x, y) for p in "ACDE" for x, y in (("B", p), (p, "B"))}
    enabled = {(tc.sat, tc.args["peer"]) for tc in tcs if tc.cmd == "ISL_ENABLE" and tc.args["on"]}
    assert enabled == {("C", "D"), ("D", "C")}
    back = ex.restore(T0 + 60, "B", links, exit_safe=True)
    assert back[0].cmd == "SET_MODE" and back[0].args == {"mode": "NOMINAL"}
    assert {(tc.sat, tc.args["peer"]) for tc in back[1:]} == cut


def test_executor_refuses_to_emit_a_malformed_command():
    with pytest.raises(ValueError):
        Executor.tc(T0, "B", "SET_MODE", {"mode": "QUARANTINED"})  # a state, not a mode (D3)


def test_shadow_never_inherits_the_real_queue():
    """M2: the sandbox plans on observations; the attacker's queued future commands are not copied."""
    ad = EngineAdapter(seed=0, rollout_s=60)
    assert ad.base.queue, "the clean engine schedules background traffic"
    obs = {"t": T0, "sats": {"E": {"temp_c": 40.0, "heater_on": 1, "mode": "NOMINAL"}}, "links": {}}
    sh = ad.shadow(obs)
    assert sh.queue == [] and sh.commands == [] and sh.c1 is None
    assert sh.heater_cmd[sh.idx["E"]] and sh.temp[sh.idx["E"]] == 40.0  # last observed effect persists
    assert ad.kind.startswith("engine")


def test_shadow_geometry_matches_the_observed_orbit_phase():
    """The shadow finds the orbit phase whose link pattern matches the observation, then propagates."""
    ad = EngineAdapter(seed=0, rollout_s=60)
    from tests.cassandra.loop import Loop

    lp = Loop(seed=5, duration_s=400).run(150)
    tw = lp.plant.twin
    links = {}
    for i in range(tw.S):
        for j in range(i + 1, tw.S):
            links[f"{tw.sats[i]}-{tw.sats[j]}"] = {"feasible": bool(tw.geo.feasible[tw.t - 1, i, j]), "enabled": True}
    t, mism = ad.phase({"links": links})
    assert mism <= 2  # the same constellation: some phase matches (almost) exactly
    sh = ad.shadow({"t": T0, "sats": {}, "links": links})
    assert sh.t == t and ad.last_match == (t, mism)


@pytest.fixture(scope="module")
def a1_run():
    lp = Loop(seed=3, duration_s=600).run(60)
    lp.send("E", "HEATER", {"on": True})  # A1: one latched heater command, spoofed ground origin
    return lp.run(480)


def test_a1_is_contained_before_the_limit_and_healed(a1_run):
    lp = a1_run
    seq = [s for _, s in lp.states["E"]]
    assert seq[0] == "flagged" and seq[-1] == "nominal"
    (act,) = [a for a in lp.actions if a.target == "E" and a.action != "none"]
    assert act.action in ("isolate_subsystem", "safe_mode", "quarantine")
    assert max(lp.true_temp["E"]) < 45.0, "E reached its critical temperature"
    i_act = round(act.t - T0) - 1
    assert lp.true_temp["E"][i_act] < 43.0  # acted with margin, not at the limit
    heal = [e for e in lp.events if e.kind == "heal" and e.sat == "E"]
    assert heal and "healed" in heal[0].text


def test_a1_decision_is_explained_with_cause_and_sparta(a1_run):
    (dec,) = [e for e in a1_run.events if e.kind == "decision" and e.sat == "E" and "action:" in e.text]
    assert "heater latched ON (origin=ground" in dec.text and "IA-0009" in dec.sparta
    assert "uncertainty gate inactive (single model)" in dec.text  # trajectory: q is null (D4)
    (act,) = [a for a in a1_run.actions if a.target == "E"]
    out = json.loads(S.dump(act))
    assert out["gated"] is None and out["q"] is None and len(out["alternatives"]) >= 2


def test_a1_leaves_every_other_satellite_alone(a1_run):
    assert {s: v for s, v in a1_run.states.items() if s != "E" and v} == {}
    assert all(a.target == "E" for a in a1_run.actions)


def test_every_core_message_satisfies_the_contract(a1_run):
    for a in a1_run.actions:
        S.parse(T.ACTION, S.dump(a))
    for e in a1_run.events:
        S.parse(T.EVENT, S.dump(e))
