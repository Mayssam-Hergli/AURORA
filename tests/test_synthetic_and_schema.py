import numpy as np

from ml import schema
from ml.synthetic.engine import SyntheticTwin
from tests.conftest import make_spec


def test_forecast_message_matches_section_6_2():
    m = schema.forecast_message(
        "B", 1760000002.0, {10: 0.08, 30: 0.41, 60: 0.77, 120: 0.93}, {30: 0.05}, "eps", 0.86
    )
    assert set(m) == {"v", "sat", "t", "model", "p_fail", "p_std", "q", "next_sub", "p_malicious"}
    assert list(m["p_fail"]) == ["10", "30", "60", "120"]


def test_episode_roundtrip_and_labels(episodes):
    path, ep = episodes["A1"]
    back = schema.read_episode(path)
    assert back["labels"] == ep.labels
    assert set(schema.SUBSYSTEMS) == set(back["telemetry"]["sub"].unique())
    assert len(back["telemetry"]) == 600 * 12 * 6
    assert back["labels"].kind == "attack" and back["labels"].critical_t is not None


def test_generation_is_deterministic():
    cfg, spec = make_spec("A1", seed=11)
    _, spec2 = make_spec("A1", seed=11)
    a, b = SyntheticTwin(cfg, spec).run_episode(), SyntheticTwin(cfg, spec2).run_episode()
    assert a.telemetry.equals(b.telemetry) and a.commands.equals(b.commands)


def test_clean_episode_never_fails(episodes):
    _, ep = episodes["N"]
    assert ep.labels.critical_t is None
    assert all(v.critical_t is None for v in ep.ml_labels.satellites.values())


def test_affected_times_follow_onset(episodes):
    for key in ("A1", "B1", "F5"):
        for st in episodes[key][1].ml_labels.satellites.values():
            if st.onset_t is not None:
                assert all(t >= st.onset_t - 1e-6 for t in st.affected_t.values())


def test_fault_is_not_malicious(episodes):
    st = episodes["F5"][1].ml_labels.satellites["B"]
    assert st.malicious is False


def test_masking_hides_reported_temperature(episodes):
    path, ep = episodes["B1"]
    tel = ep.telemetry
    b = tel[(tel["sat"] == "B") & (tel["sub"] == "thermal")].temp_c.to_numpy()
    crit_t = ep.ml_labels.satellites["B"].critical_t
    if crit_t is not None:  # the true temperature crossed 45 C, the report did not
        assert b[int(crit_t)] < 45.0


def test_sandbox_snapshot_restore():
    cfg, spec = make_spec("A1", seed=5)
    tw = SyntheticTwin(cfg, spec)
    tw.step(100)
    snap = tw.snapshot()
    tw.step(20)
    t_a = tw.temp.copy()
    tw.restore(snap)
    tw.step(20)
    assert np.allclose(t_a, tw.temp)
