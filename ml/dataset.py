"""Windows and training targets (episode-level splits; labels never enter the model inputs).

For a window ending at tick t and satellite s (critical tick c, onset tick o, -1 if none):
    hazard  y_D = 1 if t < c <= t + D, for D in {10, 30, 60, 120} s
            masked after failure (t >= c) and right-censored when the satellite is at risk but
            the episode ends before t + D without a failure (its future is unknown)
    next    the next subsystem to be affected after t (defined once the anomaly has started)
    mal     1 for an attack, 0 for a natural fault (defined once the anomaly has started)
"""

from __future__ import annotations

import numpy as np
import torch

from ml.model import HORIZONS


def window_targets(c: dict, ep: int, t: int, T: int) -> dict:
    crit, onset, mal, aff = c["crit"][ep], c["onset"][ep], c["malicious"][ep], c["affected"][ep]
    at_risk = (onset >= 0) | (crit >= 0)
    H = np.array(HORIZONS)
    y = (crit[:, None] >= 0) & (t < crit[:, None]) & (crit[:, None] <= t + H[None])
    failed = (crit >= 0) & (t >= crit)
    censored = (crit[:, None] < 0) & at_risk[:, None] & (t + H[None] > T - 1)
    m = ~failed[:, None] & ~censored
    started = (onset >= 0) & (t >= onset)
    nxt = np.full(len(crit), -1)
    future = np.where(aff > t, aff, 10**7)
    has = started & (future.min(1) < 10**6)
    nxt[has] = future[has].argmin(1)
    mal_y = np.where(started & (mal >= 0), mal, -1)
    return dict(y=y.astype(np.float32), m=m.astype(np.float32), next=nxt, mal=mal_y)


def sample_ticks(c: dict, ep: int, T: int, k: int, rng: np.random.Generator) -> np.ndarray:
    onset, crit = c["onset"][ep], c["crit"][ep]
    ticks = list(rng.integers(0, T, k - k // 2))
    if (onset >= 0).any() or (crit >= 0).any():
        lo = max(0, int(onset[onset >= 0].min()) - 30) if (onset >= 0).any() else 0
        hi = int(crit[crit >= 0].max()) + 5 if (crit >= 0).any() else T - 1
        ticks += list(rng.integers(lo, max(lo + 1, min(hi, T - 1)), k // 2))
    else:
        ticks += list(rng.integers(0, T, k // 2))
    return np.array(ticks)


def make_batch(c: dict, items: list[tuple[int, int]], W: int) -> dict:
    raw, links, tg = [], [], []
    T = c["raw"].shape[1]
    for ep, t in items:
        idx = np.clip(np.arange(t - W + 1, t + 1), 0, None)
        raw.append(c["raw"][ep, idx])
        links.append(c["links"][ep, t])
        tg.append(window_targets(c, ep, t, T))
    f = lambda k: torch.tensor(np.stack([g[k] for g in tg]))
    return dict(
        raw=torch.tensor(np.stack(raw), dtype=torch.float32),
        links=torch.tensor(np.stack(links)),
        y=f("y"),
        m=f("m"),
        next=f("next").long(),
        mal=f("mal").long(),
    )


def episode_windows(c: dict, ep: int, W: int, t_from: int = 0, t_to: int | None = None):
    """All windows of one episode (for per-tick evaluation): raw [T, W, S, 33], links [T, P]."""
    T = c["raw"].shape[1]
    t_to = T if t_to is None else t_to
    ticks = np.arange(t_from, t_to)
    idx = np.clip(ticks[:, None] + np.arange(-W + 1, 1)[None], 0, None)
    return (torch.tensor(c["raw"][ep][idx], dtype=torch.float32), torch.tensor(c["links"][ep][ticks]))
