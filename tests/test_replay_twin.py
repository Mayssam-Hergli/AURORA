"""scripts/replay_twin.py: contract-valid output, and bus commands act on the simulated physics."""

import uuid

import pytest

from aurora_common import schemas as S
from aurora_common import topics as T
from scripts.replay_twin import ReplayTwin, build_twin, parse_sats


class FakeBus:
    def __init__(self):
        self.sent = []

    def publish(self, topic, model):
        S.parse(topic, S.dump(model))  # what a receiver would see
        self.sent.append((topic, model))


def temps(bus, sat):
    return [m.fields["temp_c"] for t, m in bus.sent if t == T.tm(sat, "thermal")]


def tc(sat, cmd, args=None, origin="ground"):
    return S.Telecommand(id=uuid.uuid4().hex, t=0.0, sat=sat, origin=origin, cmd=cmd, args=args or {})


def _rt(rt, sat, effect, args):
    rt.on_redteam(T.redteam(sat), S.RedTeam(t=0.0, sat=sat, effect=effect, args=args))


def test_redteam_freeze_and_bias_change_only_the_reported_value():
    bus = FakeBus()
    rt = ReplayTwin(bus, build_twin(seed=2, duration_s=300), list(T.SATS), start_t=1_800_000_000.0)
    rt.run = lambda n: [rt.step() for _ in range(n)]
    rt.run(5)
    _rt(rt, "E", "freeze_tm", {"sub": "thermal", "field": "temp_c"})
    rt.on_tc(T.tc("E"), tc("E", "HEATER", {"on": True}))
    rt.run(60)
    rep = temps(bus, "E")
    assert len(set(rep[-55:])) == 1  # frozen
    assert rt.twin.temp[rt.twin.idx["E"]] > rep[-1] + 5  # physics kept heating
    _rt(rt, "E", "stop", {})
    rt.run(2)
    assert temps(bus, "E")[-1] > rep[-1] + 5
    _rt(rt, "F", "bias_tm", {"sub": "thermal", "field": "temp_c", "rate_per_s": 0.5})
    rt.run(20)
    f = temps(bus, "F")
    assert f[-1] - rt.twin.temp[rt.twin.idx["F"]] > 8  # reported runs away from the true value


def test_relay_reaches_only_feasible_enabled_neighbours_with_isl_origin():
    bus = FakeBus()
    rt = ReplayTwin(bus, build_twin(seed=3, duration_s=300), list(T.SATS), start_t=1_800_000_000.0)
    rt.step()
    tw = rt.twin
    x = tw.idx["A"]
    _rt(rt, "A", "relay_tc", {"cmd": "HEATER", "args": {"on": True}, "period_s": 10.0, "delay_s": 0.0})
    relayed = []
    for _ in range(280):
        rt.step()
        relayed = [m for t, m in bus.sent if T.kind_of(t)[0] == "tc" and m.origin == "isl:A"]
        if relayed:
            break
    assert relayed, "A never had a neighbour in 280 s"
    for m in relayed:  # each copy crossed a link that was feasible at its tick
        k = int(round(m.t - 1_800_000_000.0))
        assert tw.geo.feasible[k, x, tw.idx[m.sat]], m
    reach = set(tw.sats) - {"A"}
    for s in reach:  # cut every link of A (what a quarantine does)
        rt.on_tc(T.tc("A"), tc("A", "ISL_ENABLE", {"peer": s, "on": False}, origin="core"))
    n = len(relayed)
    for _ in range(25):
        rt.step()
    after = [m for t, m in bus.sent if T.kind_of(t)[0] == "tc" and m.origin == "isl:A"]
    assert len(after) == n  # nothing crosses a disabled link


def test_fault_injection_parsing_and_effects():
    from scripts.replay_twin import parse_fault

    assert parse_fault("F5:E@60") == (60, "F5", "E", None)
    assert parse_fault("F1:B@30:0.03") == (30, "F1", "B", 0.03)
    with pytest.raises(ValueError):
        parse_fault("F9:E@1")
    bus = FakeBus()
    rt = ReplayTwin(bus, build_twin(seed=4, duration_s=300), list(T.SATS), start_t=1_800_000_000.0)
    rt.faults.append((3, "F2", "E", 64.0))
    for _ in range(6):
        rt.step()
    e = temps(bus, "E")
    assert max(e) - sorted(e)[-2] > 50  # one spike, then back


def test_parse_sats():
    assert parse_sats("A-L") == list(T.SATS)
    assert parse_sats("A,C-L") == ["A"] + list("CDEFGHIJKL")


def test_publishes_contract_messages_and_skips_hardware_slots():
    bus = FakeBus()
    rt = ReplayTwin(bus, build_twin(seed=1, duration_s=120), parse_sats("A,C-L"), start_t=1_800_000_000.0)
    for _ in range(5):
        rt.step()
    kinds = {T.kind_of(t)[0] for t, _ in bus.sent}
    assert {"tm", "link"} <= kinds
    assert not [t for t, _ in bus.sent if "/sat/B/" in t]  # B is the board's slot
    assert all(m.t >= 1_800_000_000.0 for _, m in bus.sent)  # shifted to the live time base


def test_bus_heater_command_heats_and_safe_mode_stops_it():
    bus = FakeBus()
    rt = ReplayTwin(bus, build_twin(seed=2, duration_s=400), list(T.SATS), start_t=1_800_000_000.0)
    for _ in range(10):
        rt.step()
    rt.on_tc(T.tc("E"), tc("E", "HEATER", {"on": True}))
    for _ in range(120):
        rt.step()
    e = temps(bus, "E")
    rise = e[-1] - e[9]
    assert rise > 10, f"E heated only {rise:.1f} C in 120 s"
    rt.on_tc(T.tc("E"), tc("E", "SAFE_MODE", origin="core"))
    for _ in range(120):
        rt.step()
    e = temps(bus, "E")
    assert e[-1] < e[129] - 5, "E did not cool after SAFE_MODE"
    obc = [m.fields["mode"] for t, m in bus.sent if t == T.tm("E", "obc")]
    assert obc[-1] == "SAFE"


def test_safe_refuses_heater_on_from_any_origin_and_only_nominal_exits():
    """interfaces.md 3.2 (v1.2): load shedding in SAFE, decided without knowing who is malicious."""
    bus = FakeBus()
    rt = ReplayTwin(bus, build_twin(seed=5, duration_s=300), list(T.SATS), start_t=1_800_000_000.0)

    def run(n):
        for _ in range(n):
            rt.step()

    def obc():
        return [m.fields for t, m in bus.sent if t == T.tm("G", "obc")][-1]

    run(5)
    rt.on_tc(T.tc("G"), tc("G", "SAFE_MODE", origin="core"))
    run(2)
    assert obc()["mode"] == "SAFE"

    def accepted(m):
        return next(r["accepted"] for r in rt.twin.commands if r["id"] == m.id)

    refused = [
        tc("G", "HEATER", {"on": True}, origin="ground"),  # a spoofed ground command ...
        tc("G", "HEATER", {"on": True}, origin="core"),  # ... and the core's own: both refused
        tc("G", "SET_MODE", {"mode": "SCIENCE"}, origin="ground"),
    ]
    rej = obc()["cmd_rej"]
    for m in refused:
        rt.on_tc(T.tc("G"), m)
    run(60)
    assert [accepted(m) for m in refused] == [False, False, False]
    assert obc()["cmd_rej"] >= rej + 3  # background ISL relays to a SAFE satellite are refused too
    assert not rt.twin.heater_cmd[rt.twin.idx["G"]]
    heater = [m.fields["heater_on"] for t, m in bus.sent if t == T.tm("G", "thermal")]
    assert heater[-60:] == [0] * 60
    assert obc()["mode"] == "SAFE"  # nothing but SET_MODE NOMINAL leaves SAFE
    off = tc("G", "HEATER", {"on": False}, origin="ground")
    nominal = tc("G", "SET_MODE", {"mode": "NOMINAL"}, origin="core")
    rt.on_tc(T.tc("G"), off)  # still accepted in SAFE
    rt.on_tc(T.tc("G"), nominal)
    run(2)
    assert accepted(off) and accepted(nominal) and obc()["mode"] == "NOMINAL"
    rt.on_tc(T.tc("G"), tc("G", "HEATER", {"on": True}, origin="ground"))
    run(3)
    assert rt.twin.heater_cmd[rt.twin.idx["G"]]  # back in NOMINAL, the heater can be commanded again


def test_own_commands_are_not_executed_twice_and_malformed_ones_are_rejected():
    bus = FakeBus()
    rt = ReplayTwin(bus, build_twin(seed=3, duration_s=200), list(T.SATS), start_t=1_800_000_000.0)
    for _ in range(150):
        rt.step()
    own = [m for t, m in bus.sent if T.kind_of(t)[0] == "tc"]
    assert own, "background traffic should include telecommands"
    before = rt.stats["applied"]
    rt.on_tc(T.tc(own[0].sat), own[0])  # our own command echoed back by the broker
    rt.apply_inbox()
    assert rt.stats["applied"] == before
    rej = [m.fields["cmd_rej"] for t, m in bus.sent if t == T.tm("F", "obc")][-1]
    rt.on_tc(T.tc("F"), tc("F", "SET_MODE", {"on": True}))  # attacker's malformed probe
    rt.step()
    rt.step()
    assert [m.fields["cmd_rej"] for t, m in bus.sent if t == T.tm("F", "obc")][-1] == rej + 1
