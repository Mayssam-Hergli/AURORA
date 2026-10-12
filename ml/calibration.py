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


# ----------------------------------------------------------------------------- calibration.json (M5)
def calibration_path():
    from ml import config

    return config.MODELS / "calibration.json"


def write_calibration(temperature: float, tau: float | None, fpr_target: float, n_models: int, source: str):
    """data/models/calibration.json, next to the models it calibrates (review fix M5)."""
    import json
    import subprocess
    import time

    from ml import config

    try:
        git = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=config.ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        git = ""
    path = calibration_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    old = json.loads(path.read_text()) if path.exists() else {}
    data = {
        "temperature": float(temperature),
        "tau": None if tau is None else float(tau),
        "fpr_target": float(fpr_target),
        "n_models": int(n_models),
        "git": git,
        "source": source,
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if data["tau"] is None and old.get("tau") is not None and source != "ml.train":
        data["tau"] = old["tau"]
    path.write_text(json.dumps(data, indent=1))
    return data


def read_calibration(required: bool = True) -> dict | None:
    """The calibration of the models in data/models. Missing and required: a clear error (M5)."""
    import json

    path = calibration_path()
    if not path.exists():
        if required:
            raise FileNotFoundError(
                f"{path} is missing: the models would run uncalibrated. Run `make train` (writes the "
                "temperature) and `make evaluate` (adds tau), or pass allow_uncalibrated=True."
            )
        return None
    return json.loads(path.read_text())
