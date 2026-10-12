"""Live twin, recorder and tools (docs/MASTER_PLAN.md Phase 3), without a broker: FakeBus validates
every message exactly like aurora_common.bus.Bus.publish (dump, then parse against the topic)."""

import numpy as np
import pytest

from aurora_common import schemas as S
from aurora_common import topics as T
from ml import features, schema
from twin import launch, send_tc
from twin.recorder import Recorder
from twin.service import TwinService, load_config, parse_sats

T0 = 1760000000.0


class FakeBus:
    def __init__(self):
        self.sent = []

    def publish(self, topic, msg):
        self.sent.append((topic, S.parse(topic, S.dump(msg))))

    def of(self, kind, sat=None):
        return [m for t, m in self.sent if T.kind_of(t)[0] == kind and (sat is None or m.sat == sat)]


@pytest.fixture
def cfg():
    c = load_config()
    c["segment_s"] = 200  # short segments so the rollover is exercised
    return c


def _svc(cfg, sats="A-L"):
    bus = FakeBus()
    return TwinService(bus, cfg, parse_sats(sats), start_t=T0), bus


def _tc(sat, cmd, args, origin="ground"):
    return S.Telecommand(id=f"test-{sat}-{cmd}-{origin}", t=T0, sat=sat, origin=origin, cmd=cmd, args=args)


def _temps(bus, sat):
    return [m.fields["temp_c"] for m in bus.of("tm", sat) if m.sub == "thermal"]


def test_twelve_satellites_per_tick_in_contract_form(cfg):
    svc, bus = _svc(cfg)
    svc.start()
    svc.step()
    tm = bus.of("tm")
    assert len(tm) == 72 and {m.sat for m in tm} == set(T.SATS) and {m.src for m in tm} == {"twin"}
    assert len(bus.of("link")) == 66 and len(bus.of("state")) == 12
    assert {m.t for m in tm} == {T0}


def test_heater_command_heats_and_is_not_published_back(cfg):
    (hot, hot_bus), (cold, cold_bus) = _svc(cfg), _svc(cfg)
    cmd = _tc("E", "HEATER", {"on": True})
    hot.on_tc(T.tc("E"), cmd)
    for svc in (hot, cold):
        for _ in range(40):
            svc.step()
    assert _temps(hot_bus, "E")[-1] > _temps(cold_bus, "E")[-1] + 2.0
    assert cmd.id not in {m.id for m in hot_bus.of("tc")}  # already on the bus: not republished
    own = hot_bus.of("tc")[0] if hot_bus.of("tc") else None
    if own:  # background traffic echoing back is not executed twice
        n = hot.stats["applied"]
        hot.on_tc(T.tc(own.sat), own)
        hot.step()
        assert hot.stats["applied"] == n


def test_safe_semantics_come_from_the_engine(cfg):
    svc, _ = _svc(cfg)
    svc.on_tc(T.tc("E"), _tc("E", "SAFE_MODE", {}, origin="core"))
    svc.on_tc(T.tc("E"), _tc("E", "HEATER", {"on": True}, origin="core"))
    svc.step()
    assert not svc.twin.heater_cmd[svc.twin.idx["E"]]  # interfaces.md 3.2: refused in SAFE


def test_freeze_bias_and_stop(cfg):
    svc, bus = _svc(cfg)
    e = svc.twin.idx["E"]
    for _ in range(5):
        svc.step()
    svc.on_tc(T.tc("E"), _tc("E", "HEATER", {"on": True}))
    svc.on_redteam(
        T.redteam("E"),
        S.RedTeam(t=T0, sat="E", effect="freeze_tm", args={"sub": "thermal", "field": "temp_c"}),
    )
    for _ in range(30):
        svc.step()
    frozen = _temps(bus, "E")[-25:]
    assert len(set(frozen)) == 1 and svc.twin.temp[e] > frozen[0] + 2.0  # physics goes on
    svc.on_redteam(T.redteam("E"), S.RedTeam(t=T0, sat="E", effect="stop", args={}))
    svc.step()
    assert abs(_temps(bus, "E")[-1] - svc.twin.temp[e]) < 1.0
    args = {"sub": "eps", "field": "soc_pct", "rate_per_s": -0.5}
    svc.on_redteam(T.redteam("F"), S.RedTeam(t=T0, sat="F", effect="bias_tm", args=args))
    for _ in range(21):
        svc.step()
    f = svc.twin.idx["F"]
    reported = [m.fields["soc_pct"] for m in bus.of("tm", "F") if m.sub == "eps"][-1]
    assert reported == pytest.approx(100 * svc.twin.soc[f] - 10.0, abs=1.5)


def _first_tick_with_neighbours(svc, sat):
    tw = svc.twin
    for _ in range(300):
        usable = tw.geo.feasible[tw.t] & tw.isl_enabled & tw.isl_enabled.T
        if usable[tw.idx[sat]].any():
            return
        svc.step()
    pytest.skip("no feasible link for this satellite in 300 s of this geometry")


def test_relay_goes_over_usable_links_only_and_stops_when_contained(cfg):
    svc, bus = _svc(cfg)
    _first_tick_with_neighbours(svc, "A")
    relay = {"cmd": "HEATER", "args": {"on": True}, "delay_s": 0}
    svc.on_redteam(T.redteam("A"), S.RedTeam(t=T0, sat="A", effect="relay_tc", args=relay))
    tw, a = svc.twin, svc.twin.idx["A"]
    usable = tw.geo.feasible[tw.t] & tw.isl_enabled & tw.isl_enabled.T
    svc.step()
    relayed = [m for m in bus.of("tc") if m.origin == "isl:A"]
    assert {m.sat for m in relayed} == {tw.sats[j] for j in np.flatnonzero(usable[a])} != set()
    assert all(tw.heater_cmd[tw.idx[m.sat]] for m in relayed)
    svc.on_tc(T.tc("A"), _tc("A", "SAFE_MODE", {}, origin="core"))
    svc.on_redteam(T.redteam("A"), S.RedTeam(t=T0, sat="A", effect="relay_tc", args=relay))
    n = len(relayed)
    svc.step()
    assert len([m for m in bus.of("tc") if m.origin == "isl:A"]) == n and svc.stats["relay_contained"] == 1


def test_long_run_rolls_over_seamlessly(cfg):
    svc, bus = _svc(cfg)
    svc.on_tc(T.tc("E"), _tc("E", "HEATER", {"on": True}))
    for _ in range(450):  # segments of 200 ticks: two boundaries
        svc.step()
    assert svc.segment == 2 and svc.twin.T == 200
    seq = [m.seq for m in bus.of("tm", "E") if m.sub == "thermal"]
    assert np.all(np.diff(seq) == 1)  # one stream across segments
    temps = np.array(_temps(bus, "E"))
    assert np.abs(np.diff(temps)).max() < 2.0  # no jump at a boundary
    t = [m.t for m in bus.of("tm", "E") if m.sub == "thermal"]
    assert t[0] == T0 and t[-1] == T0 + 449
    assert len(svc.own_ids) + len(svc.applied_ids) < 2000  # id memory is bounded


def test_true_critical_state_is_published_once_as_ground_truth(cfg):
    svc, bus = _svc(cfg)
    svc.twin.temp[svc.twin.idx["G"]] = 60.0
    svc.step()
    svc.step()
    ev = bus.of("gt_event", "G")
    assert len(ev) == 1 and ev[0].event == "critical" and ev[0].data["rule"] == "T_bus>T_max"


def test_hardware_slot_is_left_to_the_bridge(cfg):
    svc, bus = _svc(cfg, "A,C-L")
    svc.on_tc(T.tc("B"), _tc("B", "HEATER", {"on": True}))
    svc.step()
    assert not bus.of("tm", "B") and svc.stats["applied"] == 0 and len(bus.of("link")) == 66


def _labelled_run(cfg, with_gt: bool):
    svc, bus = _svc(cfg)
    rec = Recorder()
    if with_gt:
        gt = dict(episode_id="live-test", generator="attacks-v1", seed=0, scenario="A", kind="attack")
        gt.update(type="A1", target="E", onset_t=T0 + 5, critical_t=None, critical_rule=None)
        gt.update(satellites=list(T.SATS), t=T0, phase="start")
        rec.on_message(T.GT_EPISODE, S.GtEpisode(**gt))
        onset = S.GtEvent(t=T0 + 5, episode_id="live-test", event="onset", sat="E", data={"cmd": "HEATER"})
        rec.on_message(T.GT_EVENT, onset)
    for k in range(40):
        if k == 5:
            svc.on_tc(T.tc("E"), _tc("E", "HEATER", {"on": True}))
            rec.on_message(T.tc("E"), _tc("E", "HEATER", {"on": True}))
        svc.step()
    for topic, msg in bus.sent:
        rec.on_message(topic, msg)
    return rec


def test_recorder_writes_a_readable_labelled_episode(cfg, tmp_path):
    out = _labelled_run(cfg, True).write(tmp_path / "live-test")
    ep = schema.read_episode(out)
    assert ep["labels"].type == "A1" and ep["ml_labels"].t0 == T0 and ep["ml_labels"].n_ticks == 40
    assert ep["ml_labels"].satellites["E"].onset_t == 5.0 and ep["ml_labels"].satellites["E"].malicious
    assert len(ep["telemetry"]) == 40 * 72 and set(ep["telemetry"]["src"]) == {"twin"}
    arrays = features.episode_arrays(out)
    assert arrays["raw"].shape == (40, 12, 27) and arrays["onset"][4] == 5


def test_recorder_never_invents_labels(cfg, tmp_path):
    out = _labelled_run(cfg, False).write(tmp_path / "live-nogt")
    assert not (out / "labels.json").exists() and not (out / "ml_labels.json").exists()
    ep = schema.read_episode(out, with_labels=False)
    assert len(ep["links"]) == 40 * 66
    assert features.episode_arrays(out)["raw"].shape == (40, 12, 27)  # S2: featurised without labels


def test_send_tc_builds_valid_commands():
    m = send_tc.build("e", "heater", ["on"])
    assert (m.sat, m.cmd, m.args, m.origin) == ("E", "HEATER", {"on": True}, "ground")
    assert send_tc.build("B", "ISL_ENABLE", ["a", "off"], origin="core").args == {"peer": "A", "on": False}
    with pytest.raises(ValueError):
        send_tc.build("E", "SET_MODE", ["QUARANTINED"])  # a core state, not a commandable mode (D3)


def test_launcher_starts_the_core_only_when_it_exists():
    names = [n for n, _ in launch.commands("--sats A-L", core=True)]
    assert names[0] == "twin" and names[1:] in ([], ["core"])
    assert [n for n, _ in launch.commands("", core=False)] == ["twin"]
