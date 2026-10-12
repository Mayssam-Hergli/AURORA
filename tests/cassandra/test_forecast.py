"""cassandra/forecast.py: trajectory forecaster (Phase 2 logic), honest nulls, untrusted-temperature
estimate, model choice, and the ensemble adapter's uncertainty rules (D4)."""

import json

import numpy as np
import pytest

from aurora_common import schemas as S
from aurora_common import topics as T
from cassandra import config
from cassandra.forecast import EnsembleAdapter, ForecastStage, ModelUnavailable, Trajectory
from cassandra.ingest import Ingest
from tests.cassandra.helpers import BOARD, T0, cfg_and_profiles, thermal, world


def board_trajectory():
    cfg, prof = cfg_and_profiles(sats=("B",), overrides={"B": BOARD})
    return Trajectory(prof["B"], int(cfg["forecast"]["window_s"]))


def test_phase2_heater_latch_on_the_board_profile():
    """Paper Table II: flagged at 28.5 C, quarantined at 53.3 C (P >= 0.30 / 0.85 on the 30 s horizon)."""
    tr = board_trajectory()
    rng = np.random.default_rng(0)
    flag_t = quar_t = None
    for k in range(40):
        temp = 25.0 + 1.5 * k + rng.uniform(-0.1, 0.1)  # firmware model, +-0.10 C noise
        r = tr.step(world(T0 + k, {"B": thermal(temp, heater_on=1)}), set())
        if r.warming_up:
            continue
        if flag_t is None and r.p(30) >= 0.30:
            flag_t = temp
        if quar_t is None and r.p(30) >= 0.85:
            quar_t = temp
    assert flag_t is not None and 25.0 < flag_t < 40.0  # flags as soon as the slope is known
    assert quar_t == pytest.approx(53.3, abs=1.6)
    assert r.driver.channel == "temp_c" and r.driver.limit == 60.0


def test_forecast_message_is_honest_about_what_trajectory_does_not_know():
    tr = board_trajectory()
    for k in range(10):
        r = tr.step(world(T0 + k, {"B": thermal(30 + k, 1)}), set())
    msg = r.to_message()
    S.parse(T.forecast("B"), S.dump(msg))
    out = json.loads(S.dump(msg))
    assert out["model"] == "trajectory-v0"
    assert (
        out["q"] is None and out["p_std"] is None and out["next_sub"] is None and out["p_malicious"] is None
    )
    p = [out["p_fail"][h] for h in ("10", "30", "60", "120")]
    assert p == sorted(p) and p[-1] > 0


def test_warming_up_then_stable_values_give_zero():
    tr = board_trajectory()
    rs = [tr.step(world(T0 + k, {"B": thermal(25.0)}), set()) for k in range(9)]
    assert all(r.warming_up for r in rs[:7]) and not rs[8].warming_up
    assert rs[8].p_fail == {10: 0.0, 30: 0.0, 60: 0.0, 120: 0.0}
    assert json.loads(S.dump(rs[0].to_message()))["warming_up"] is True


def test_no_fresh_data_no_forecast():
    tr = board_trajectory()
    assert tr.step(world(T0, {}), set()) is None


def test_untrusted_temperature_is_estimated_from_heater_on_time():
    """Masking (B1): the reported temperature is frozen at 30 C while the heater runs."""
    tr = board_trajectory()
    for k in range(5):
        tr.step(world(T0 + k, {"B": thermal(25 + 1.5 * k, 1)}), set())  # honest
    frozen = 31.0
    rs = [tr.step(world(T0 + 5 + k, {"B": thermal(frozen, 1)}), {"temp_c"}) for k in range(30)]
    assert rs[-1].model == "trajectory-v0+est" and rs[-1].estimated == ("temp_c",)
    assert rs[-1].driver.value == pytest.approx(31.0 + 1.5 * 30, abs=0.01)  # from the last trusted 31 C
    assert rs[-1].p(30) >= 0.85  # the forecaster still sees the danger the telemetry hides
    honest = board_trajectory()
    blind = [honest.step(world(T0 + k, {"B": thermal(frozen, 1)}), set()) for k in range(30)]
    assert blind[-1].p(120) == 0.0  # without the monitor, a frozen value looks perfectly safe


def test_twin_limits_on_soc_and_attitude():
    cfg, prof = cfg_and_profiles(sats=("E",))
    tr = Trajectory(prof["E"], 8)
    for k in range(10):
        vals = {
            "eps": {"soc_pct": 40.0 - 0.5 * k, "bus_v": 7.0, "load_w": 1, "solar_w": 1, "current_ma": 1},
            "adcs": {"att_err_deg": 1.0, "wheel_rpm": [0, 0, 0], "rate_dps": 0},
        }
        r = tr.step(world(T0 + k, {"E": vals}), set())
    assert r.driver.channel == "soc_pct" and r.driver.eta_s == pytest.approx((35.5 - 30) / 0.5, rel=0.05)


def test_stage_without_models_uses_trajectory_and_says_why(tmp_path):
    cfg, prof = cfg_and_profiles(forecast={"ensemble": {"models_dir": str(tmp_path)}})
    stage = ForecastStage.build(prof, cfg)
    assert stage.ensemble is None
    (note,) = stage.startup_notes
    assert "trajectory-v0 for all satellites" in note.text and "no trained models" in note.text


def test_models_without_calibration_are_refused(tmp_path):
    (tmp_path / "full_s0.pt").write_bytes(b"not really a model")
    cfg, _ = cfg_and_profiles(forecast={"ensemble": {"models_dir": str(tmp_path)}})
    with pytest.raises(ModelUnavailable, match="calibration.json"):
        EnsembleAdapter.load(cfg, ["B", "E"])


@pytest.mark.parametrize("n_models", [1, 2])
def test_ensemble_adapter_uncertainty_rule(understanding, n_models):
    """D4: one model -> p_std and q are null (gate inactive); two or more -> q = p_std at 60 s.
    Untrained models: this checks the plumbing, not forecast quality."""
    import torch

    from ml import graph as G
    from ml.model import Forecaster
    from ml.online import OnlineForecaster
    from scripts.replay_twin import ReplayTwin, build_twin

    torch.manual_seed(0)
    models = [Forecaster(understanding, G.build(12), hid=16, rounds=1).eval() for _ in range(n_models)]
    sats = list(T.SATS)
    adapter = EnsembleAdapter(OnlineForecaster(models, sats, 1.0, 30), n_models, calibrated=True, window=30)
    cfg = config.load()
    ing = Ingest(config.profiles(cfg), cfg)

    class Pipe:  # replay twin -> ingest, no broker
        def publish(self, topic, model):
            ing.feed(topic, model, model.t)

    rt = ReplayTwin(Pipe(), build_twin(seed=7, duration_s=120), sats, start_t=T0)
    results = []
    for _ in range(35):
        rt.step()
        for tick in ing.poll(wall=rt.twin.t0_unix + rt.twin.t + rt.t_shift):
            results.append(adapter.step(tick, decision_horizon=60))
    last = results[-1]
    assert set(last) == set(sats)
    r = last["E"]
    S.parse(T.forecast("E"), S.dump(r.to_message()))
    assert r.model == "tgn-v1" and not r.warming_up and r.next_sub in T.SUBSYSTEMS
    if n_models == 1:
        assert r.p_std is None and r.q is None
    else:
        assert r.q == r.p_std[60] and r.q >= 0
