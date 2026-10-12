"""Calibration of P(F < t + D) (CONTEXT 5.5): temperature scaling on validation, ECE with 10 bins.

data/models/calibration.json (M5) travels with the .pt files: eval.harness writes it where the
temperature and tau are fitted, the model bundle (ml/export.py --bundle) ships it, and
OnlineForecaster and cassandra/forecast.py refuse to run the ensemble without it.
"""

from __future__ import annotations

import subprocess

import numpy as np
import torch

from ml import config


def git_version() -> str:
    try:
        out = subprocess.run(
            ["git", "describe", "--always", "--dirty"], cwd=config.ROOT, capture_output=True, text=True
        )
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def calibration_record(temperature: float, tau: float, models: list[str], ev: dict) -> dict:
    """Content of data/models/calibration.json: temperature and the ensemble's alert threshold tau at
    the primary horizon, chosen on validation benign timesteps at the target false-positive rate."""
    return {
        "temperature": float(temperature),
        "tau": float(tau),
        "horizon_s": int(ev["primary_horizon_s"]),
        "fpr_target": float(ev["target_fpr"]),
        "models": list(models),
        "git": git_version(),
    }


def fit_temperature(bin_logits: np.ndarray, y: np.ndarray, m: np.ndarray) -> float:
    """One temperature on the hazard-bin logits, fitted by masked NLL of the cumulative P(F < t+D).
    bin_logits [n, S, 4], y and m [n, S, 4]."""
    z = torch.tensor(bin_logits, dtype=torch.float64)
    yt, mt = torch.tensor(y, dtype=torch.float64), torch.tensor(m, dtype=torch.float64)
    log_t = torch.zeros(1, dtype=torch.float64, requires_grad=True)
    opt = torch.optim.LBFGS([log_t], lr=0.1, max_iter=200)

    def closure():
        opt.zero_grad()
        p = (1 - torch.cumprod(1 - torch.sigmoid(z / log_t.exp()), -1)).clamp(1e-7, 1 - 1e-7)
        loss = -((yt * torch.log(p) + (1 - yt) * torch.log(1 - p)) * mt).sum() / mt.sum()
        loss.backward()
        return loss

    opt.step(closure)
    return float(log_t.detach().exp())


def reliability(p: np.ndarray, y: np.ndarray, n_bins: int = 10):
    edges = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, n_bins - 1)
    cnt = np.bincount(idx, minlength=n_bins)
    conf = np.array([p[idx == b].mean() if cnt[b] else np.nan for b in range(n_bins)])
    acc = np.array([y[idx == b].mean() if cnt[b] else np.nan for b in range(n_bins)])
    return conf, acc, cnt


def ece(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> float:
    conf, acc, cnt = reliability(p, y, n_bins)
    ok = cnt > 0
    return float(np.sum(cnt[ok] / cnt.sum() * np.abs(acc[ok] - conf[ok])))
