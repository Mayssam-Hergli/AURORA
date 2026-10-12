"""Phase 3: twin/service.py (rollover, initial states) and twin/recorder.py (Section 6.3 episodes that
ml.schema and ml.features can open)."""

import numpy as np

from aurora_common import schemas as S
from aurora_common import topics as T
from ml import features, schema
from twin.recorder import Recorder
from twin.service import TwinService, build_twin

T0 = 1_800_000_000.0


class FakeBus:
    def __init__(self, sink=None):
        self.sent, self.sink = [], sink

    def publish(self, topic, model):
        S.parse(topic, S.dump(model))
        self.sent.append((topic, model))
        if self.sink:
            self.sink(topic, model)


def test_initial_states_are_nominal_and_retained_topics():
    bus = FakeBus()
    tw = TwinService(bus, build_twin(1, 150), list(T.SATS), start_t=T0)
    tw.initial_states(T0)
    assert {(t, m.state) for t, m in bus.sent} == {(T.state(s), "nominal") for s in T.SATS}
    assert all(T.retain_of(t) for t, _ in bus.sent)


def test_critical_ground_truth_is_published_once_and_only_on_gt():
    """A latched heater takes E past T_max: one gt/event "critical" for E, at the crossing, never for
    a satellite this twin does not publish (a hardware slot), and nothing on a core topic."""
    bus = FakeBus()
    tw = TwinService(bus, build_twin(2, 400), [s for s in T.SATS if s != "B"], start_t=T0, seed=2)
    for sat in ("E", "B"):
        tw.on_tc(
            T.tc(sat),
            S.Telecommand(id=f"h{sat}", t=T0, sat=sat, origin="ground", cmd="HEATER", args={"on": True}),
        )
    for _ in range(400):
        tw.step()
    crit = [m for t, m in bus.sent if t == T.GT_EVENT]
    assert [(m.event, m.sat) for m in crit] == [("critical", "E")]
    k = int(round(crit[0].t - T0))
    temps = {int(round(m.t - T0)): m.fields["temp_c"] for t, m in bus.sent if t == T.tm("E", "thermal")}
    assert temps[k] > 45.0 and temps[k - 1] <= 45.5  # published when the true state reaches F
    assert crit[0].data["rule"] and crit[0].data["source"] == "twin physics (true state)"


def test_rollover_keeps_physics_seq_and_time_continuous():
    bus = FakeBus()
    tw = TwinService(bus, build_twin(2, 150), list(T.SATS), start_t=T0, seed=2)
    tw.on_tc(
        T.tc("E"), S.Telecommand(id="h1", t=T0, sat="E", origin="ground", cmd="HEATER", args={"on": True})
    )
    for _ in range(320):  # two rollovers at 150 and 300
        tw.step()
    assert tw.base_tick == 300 and tw.tick == 320
    th = [m for t, m in bus.sent if t == T.tm("E", "thermal")]
    temps = np.array([m.fields["temp_c"] for m in th])
    assert np.abs(np.diff(temps)).max() < 1.5  # no jump at a rollover
    assert temps[-1] > temps[0] + 10  # the heater stayed on across rollovers
    seqs = [m.seq for m in th]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))  # seq continues
    ts = [m.t for m in th]
    assert np.allclose(np.diff(ts), 1.0)  # published time keeps counting


def test_recorded_episode_opens_with_ml_schema_and_features(tmp_path):
    rec = Recorder({s: {"t_max_c": 45.0, "soc_min_pct": 30.0, "theta_max_deg": 10.0} for s in T.SATS})
    bus = FakeBus(sink=rec.on_msg)
    tw = TwinService(bus, build_twin(3, 200), list(T.SATS), start_t=T0, seed=3)
    for _ in range(10):
        tw.step()
    gt = dict(
        episode_id="rec-test",
        generator="attacks-v1",
        seed=0,
        scenario="A",
        kind="attack",
        type="A1",
        target="E",
        onset_t=T0 + 10,
        critical_t=None,
        critical_rule=None,
        params={},
        satellites=list(T.SATS),
    )
    rec.on_msg(T.GT_EPISODE, S.GtEpisode(t=T0 + 10, phase="start", **gt))
    rec.on_msg(T.GT_EVENT, S.GtEvent(t=T0 + 10, episode_id="rec-test", event="onset", sat="E"))
    tw.on_tc(
        T.tc("E"),
        S.Telecommand(id="h2", t=T0 + 10, sat="E", origin="ground", cmd="HEATER", args={"on": True}),
    )
    for _ in range(170):
        tw.step()
    path = rec.write(tmp_path / "rec-test")
    ep = schema.read_episode(path)
    assert ep["labels"].type == "A1" and ep["ml_labels"].t0 == T0
    e = ep["ml_labels"].satellites["E"]
    assert e.onset_t == 10.0 and e.malicious is True
    arr = features.episode_arrays(path)
    assert arr["raw"].shape == (180, 12, len(features.graph.FLAT)) and arr["onset"][4] == 10
    truth = sorted({m.sat for t, m in bus.sent if t == T.GT_EVENT})
    assert ep["labels"].params["critical_from_truth"] == truth


def test_recorder_takes_the_true_crossing_under_masking(tmp_path):
    """B1: temp_c frozen while the heater runs. The reported value never crosses 45 C; the label must
    still say when E truly failed (gt/event critical from the twin), not "never"."""
    rec = Recorder({s: {"t_max_c": 45.0, "soc_min_pct": 30.0, "theta_max_deg": 10.0} for s in T.SATS})
    bus = FakeBus(sink=rec.on_msg)
    tw = TwinService(bus, build_twin(2, 400), list(T.SATS), start_t=T0, seed=2)
    for _ in range(5):
        tw.step()
    tw.on_redteam(
        T.redteam("E"),
        S.RedTeam(t=T0 + 5, sat="E", effect="freeze_tm", args={"sub": "thermal", "field": "temp_c"}),
    )
    tw.on_tc(
        T.tc("E"), S.Telecommand(id="h3", t=T0 + 5, sat="E", origin="ground", cmd="HEATER", args={"on": True})
    )
    for _ in range(395):
        tw.step()
    reported = [m.fields["temp_c"] for t, m in bus.sent if t == T.tm("E", "thermal")]
    assert max(reported) < 45.0  # masked: the telemetry alone would label E as never failing
    crit = next(m for t, m in bus.sent if t == T.GT_EVENT and m.sat == "E")
    ep = schema.read_episode(rec.write(tmp_path / "b1"))
    e = ep["ml_labels"].satellites["E"]
    assert e.critical_t == crit.t - T0 and ep["labels"].params["critical_from_truth"] == ["E"]
