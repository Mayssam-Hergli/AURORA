"""Baselines evaluated with exactly the same harness as the forecaster (CONTEXT 5.5).

All return a score per (episode, tick, satellite) computed from reported telemetry only:
    threshold   static limits: max proximity of temp_c, soc_pct, att_err_deg to their critical limits
    trajectory  Phase-2 prototype: 8-sample least-squares slope -> time to limit t_c, clip(1 - t_c / H)
    lstm_ae     LSTM autoencoder on 30 s windows of one satellite's 27 channels (clean training data)
    gdn         Graph Deviation Network (Deng & Hooi, AAAI 2021): learned sensor graph (top-k cosine
                similarity of sensor embeddings), graph-attention forecast of each sensor, deviation
                score = max over sensors of the robust-normalised forecast error
"""

from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from ml import graph as G

N_SENSORS = len(G.FLAT)  # channels per satellite

CH = {c: i for i, c in enumerate(G.FLAT)}


def threshold_score(raw: np.ndarray, crit: dict, t_ref=20.0, soc_ref=90.0) -> np.ndarray:
    """raw [n, T, S, 27] -> [n, T, S]."""
    t = (raw[..., CH["temp_c"]] - t_ref) / (crit["t_max_c"] - t_ref)
    s = (soc_ref - raw[..., CH["soc_pct"]]) / (soc_ref - crit["soc_min_pct"])
    a = np.abs(raw[..., CH["att_err_deg"]]) / crit["theta_max_deg"]
    return np.maximum.reduce([t, s, a]).astype(np.float32)


def _slope(x: np.ndarray, w: int) -> np.ndarray:
    k = np.arange(w) - (w - 1) / 2
    pad = np.concatenate([np.repeat(x[:, :1], w - 1, 1), x], 1)
    win = np.lib.stride_tricks.sliding_window_view(pad, w, axis=1)
    return (win * k).sum(-1) / (k**2).sum()


def trajectory_score(raw: np.ndarray, crit: dict, horizon: float = 60.0, w: int = 8) -> np.ndarray:
    out = []
    for name, limit, sign in (
        ("temp_c", crit["t_max_c"], 1),
        ("soc_pct", crit["soc_min_pct"], -1),
        ("att_err_deg", crit["theta_max_deg"], 1),
    ):
        x = raw[..., CH[name]].astype(np.float64)
        rate = _slope(x, w) * sign
        gap = (limit - x) * sign
        with np.errstate(divide="ignore", invalid="ignore"):
            tc = np.where(rate > 1e-6, gap / rate, np.inf)
        tc = np.where(gap <= 0, 0.0, tc)
        out.append(np.clip(1 - tc / horizon, 0, 1))
    return np.maximum.reduce(out).astype(np.float32)


# ----------------------------------------------------------------------------- sequence baselines
def _windows(Z: np.ndarray, W: int) -> np.ndarray:
    """Z [T, S, C] -> [T, S, W, C] windows ending at each tick (left padded)."""
    pad = np.concatenate([np.repeat(Z[:1], W - 1, 0), Z], 0)
    return np.lib.stride_tricks.sliding_window_view(pad, W, axis=0).transpose(0, 1, 3, 2)


class LSTMAE(nn.Module):
    def __init__(self, d=N_SENSORS, hid=32):
        super().__init__()
        self.enc, self.dec = nn.LSTM(d, hid, batch_first=True), nn.LSTM(hid, hid, batch_first=True)
        self.out = nn.Linear(hid, d)

    def forward(self, x):
        _, (h, _) = self.enc(x)
        y, _ = self.dec(h[-1].unsqueeze(1).expand(-1, x.shape[1], -1))
        return self.out(y)

    def score(self, x):
        return ((self(x) - x) ** 2).mean((1, 2))


class GDN(nn.Module):
    """Graph Deviation Network (Deng & Hooi 2021), compact version.

    Sensor embeddings v_i define a learned graph (top-k cosine neighbours). Each sensor's history
    is embedded (W x_i), attention over [v_i | W x_i] of itself and its neighbours aggregates
    neighbour histories into z_i, and the next value is predicted as f(z_i * v_i).
    """

    def __init__(self, n_sensors=N_SENSORS, W=30, dim=32, topk=5):
        super().__init__()
        self.emb = nn.Parameter(torch.randn(n_sensors, dim) * 0.1)
        self.proj = nn.Linear(W, dim, bias=False)
        self.att = nn.Linear(4 * dim, 1, bias=False)
        self.out = nn.Sequential(nn.Linear(dim, dim), nn.ReLU(), nn.Linear(dim, 1))
        self.topk = topk

    def adjacency(self):
        e = F.normalize(self.emb, dim=-1)
        sim = e @ e.T - 2 * torch.eye(len(e))
        nb = sim.topk(self.topk, -1).indices  # [n, k], excludes self
        return torch.cat([torch.arange(len(e))[:, None], nb], 1)  # self + neighbours

    def forward(self, x):
        """x [B, W, n] (history) -> prediction of the next value of each sensor [B, n]."""
        B, W, n = x.shape
        wx = self.proj(x.transpose(1, 2))  # [B, n, dim]
        g = torch.cat([self.emb.expand(B, -1, -1), wx], -1)  # [B, n, 2 dim]
        idx = self.adjacency()  # [n, k+1]
        gj = g[:, idx]  # [B, n, k+1, 2 dim]
        gi = g.unsqueeze(2).expand_as(gj)
        alpha = torch.softmax(F.leaky_relu(self.att(torch.cat([gi, gj], -1)).squeeze(-1), 0.2), -1)
        z = F.relu((alpha.unsqueeze(-1) * wx[:, idx]).sum(2))  # [B, n, dim]
        return self.out(z * self.emb).squeeze(-1)


def train_sequence_baseline(
    kind: str,
    Z_clean: np.ndarray,
    W: int = 30,
    epochs: int = 4,
    n_windows: int = 40000,
    seed: int = 0,
    log=print,
):
    """Z_clean [n, T, S, C] normalised clean episodes."""
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    n, T, S, C = Z_clean.shape
    model = LSTMAE() if kind == "lstm_ae" else GDN(W=W)
    opt = torch.optim.Adam(model.parameters(), 2e-3)
    for ep in range(epochs):
        e, t, s = (
            rng.integers(n, size=n_windows),
            rng.integers(W, T, size=n_windows),
            rng.integers(S, size=n_windows),
        )
        idx = t[:, None] + np.arange(-W, 1)[None]  # W history + 1 target
        seq = torch.tensor(Z_clean[e[:, None], idx, s[:, None]], dtype=torch.float32)  # [N, W+1, C]
        losses = []
        for k in range(0, n_windows, 256):
            b = seq[k : k + 256]
            if kind == "lstm_ae":
                loss = ((model(b[:, 1:]) - b[:, 1:]) ** 2).mean()
            else:
                loss = ((model(b[:, :-1]) - b[:, -1]) ** 2).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
            losses.append(loss.item())
        log(f"  {kind} epoch {ep} loss {np.mean(losses):.4f}")
    return model.eval()


@torch.no_grad()
def sequence_scores(kind: str, model, Z: np.ndarray, W: int = 30, err_stats=None) -> np.ndarray:
    """Z [T, S, C] for one episode -> score [T, S]."""
    T, S, C = Z.shape
    if kind == "lstm_ae":
        win = torch.tensor(np.ascontiguousarray(_windows(Z, W)).reshape(T * S, W, C), dtype=torch.float32)
        return model.score(win).numpy().reshape(T, S)
    hist = _windows(np.concatenate([Z[:1], Z[:-1]], 0), W)  # history ending at t-1
    x = torch.tensor(np.ascontiguousarray(hist).reshape(T * S, W, C), dtype=torch.float32)
    err = np.abs(model(x).numpy() - Z.reshape(T * S, C))
    if err_stats is not None:
        med, iqr = err_stats
        err = (err - med) / iqr
    return err.max(-1).reshape(T, S)
