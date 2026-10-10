import numpy as np
import torch

from eval import metrics as M
from ml import baselines as BL
from ml import calibration as Cal
from ml import features, schema
from ml import graph as G
from ml.model import Forecaster
from ml.online import OnlineForecaster, episode_messages


def test_online_frames_equal_offline_features(episodes, understanding):
    path, _ = episodes["A1"]
    offline = features.episode_arrays(path)
    ep = schema.read_episode(path, with_labels=True)
    fc = OnlineForecaster([], ep["labels"].satellites)
    for k, (_t, msgs) in enumerate(episode_messages(ep)):
        for kind, msg in msgs:
            {"tm": fc.on_telemetry, "tc": fc.on_command, "link": fc.on_link}[kind](msg)
        fc.push_frame()
        assert np.allclose(fc.buffer[-1], offline["raw"][k], rtol=1e-5, atol=1e-4), f"tick {k}"
        if k == 200:
            break


def test_hazard_is_monotone_and_shapes(episodes, understanding):
    raw = features.episode_arrays(episodes["A1"][0])
    for variant in ("full", "no_eng", "unconstrained", "no_residual"):
        g = G.build(12, variant)
        m = Forecaster(understanding, g, hid=16, rounds=2, use_residual=variant != "no_residual").eval()
        x = torch.tensor(raw["raw"][None, 100:130])
        out = m(x, torch.tensor(raw["links"][None, 129]))
        p = out["p_fail"]
        assert p.shape == (1, 12, 4) and out["next_logits"].shape == (1, 12, 6)
        assert bool((p[..., 1:] >= p[..., :-1] - 1e-6).all())


def test_messages_only_travel_along_permitted_edges(episodes, understanding):
    """Physics constraint: with every ISL disabled, perturbing satellite A cannot change B's forecast."""
    raw = features.episode_arrays(episodes["N"][0])["raw"]
    m = Forecaster(understanding, G.build(12, "full"), hid=16, rounds=3).eval()
    x = torch.tensor(raw[None, 100:130])
    no_links = torch.zeros(1, 66, dtype=torch.bool)
    base = m(x, no_links)["p_fail"]
    x2 = x.clone()
    x2[:, :, 0, :] += 50.0
    pert = m(x2, no_links)["p_fail"]
    assert not torch.allclose(base[0, 0], pert[0, 0])
    assert torch.allclose(base[0, 1:], pert[0, 1:], atol=1e-6)


def test_tau_and_lead_time():
    score = np.zeros((2, 100, 1))
    score[1, 40:, 0] = 1.0
    truth = {
        "crit": np.array([[-1], [70]]),
        "onset": np.array([[-1], [30]]),
        "malicious": np.array([[-1], [1]]),
    }
    benign = np.array([[True], [False]])
    tau = M.tau_at_fpr(score, benign, 0.001)
    rows = M.alerts(score, truth, max(tau, 0.5))
    assert rows[0]["detected"] and rows[0]["lead"] == 30 and rows[0]["delay"] == 10
    s = M.lead_summary(rows)
    assert s["recall"] == 1.0 and s["median_lead_s"] == 30


def test_ece_of_perfect_calibration_is_small():
    rng = np.random.default_rng(0)
    p = rng.random(200_000)
    y = (rng.random(200_000) < p).astype(float)
    assert Cal.ece(p, y) < 0.01


def test_trajectory_baseline_reaches_one_at_limit():
    raw = np.zeros((1, 60, 1, len(G.FLAT)), np.float32)
    raw[0, :, 0, G.FLAT.index("temp_c")] = np.linspace(20, 50, 60)
    raw[0, :, 0, G.FLAT.index("soc_pct")] = 80
    crit = {"t_max_c": 45.0, "soc_min_pct": 30.0, "theta_max_deg": 10.0}
    s = BL.trajectory_score(raw, crit, horizon=60)
    assert s[0, -1, 0] == 1.0 and s[0, 5, 0] < 0.5
