"""Adversarial robustness (CONTEXT 5.5 M4): PGD (Madry et al. 2018) against the forecaster ensemble.

Threat model (CONTEXT 3): the adversary forges housekeeping telemetry (THERMAL, EPS, ADCS fields)
within +-eps nominal standard deviations per channel, for the whole episode, to suppress the
failure forecast of the attacked satellite. It cannot forge the independent SUN reference or the
ground-side command and link logs. The static-threshold baseline is attacked with the same budget
(the critical channels are shifted away from their limits).

    python -m eval.robustness --episodes 16       # results/eval/robustness.json
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import torch

from ml import baselines as BL
from ml import config, features
from ml import graph as G
from ml.train import load_model

FORGEABLE = [c for t in ("thermal", "eps", "adcs") for c in G.CHANNELS[t] if c != "heater_on"]


def ensemble_p60(models, raw, links, temp):
    bins = torch.stack([m(raw, links)["bin_logits"] for m in models]).mean(0) / temp
    return 1 - torch.cumprod(1 - torch.sigmoid(bins), -1)[..., 2]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=16)
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--chunk", type=int, default=8)
    a = ap.parse_args(argv)
    torch.set_num_threads(2)
    met = json.loads((config.RESULTS / "eval" / "metrics.json").read_text())
    temp, tau, tau_thr = met["temperature"], met["tau"]["ensemble"], met["tau"]["threshold"]
    models = [load_model(n) for n in sorted(p.stem for p in config.MODELS.glob("full_s*.pt"))]
    for m in models:
        for p in m.parameters():
            p.requires_grad_(False)
    und = features.Understanding().load(config.MODELS / "understanding.npz")
    c, idx = features.load_cache("test_id")
    W = 30
    crit = config.load("synthetic")["critical"]
    mask = torch.zeros(len(G.FLAT))
    mask[[G.FLAT.index(ch) for ch in FORGEABLE]] = 1
    scale = torch.tensor(und.sd, dtype=torch.float32) * mask
    cands = [
        (e, s)
        for e in range(len(idx))
        for s in range(c["raw"].shape[2])
        if c["crit"][e, s] >= 0 and idx.kind[e] == "attack" and c["onset"][e].max() >= 0
    ]
    rng = np.random.default_rng(0)
    pick = [cands[i] for i in rng.choice(len(cands), min(a.episodes, len(cands)), replace=False)]
    results = {}
    for eps in (0.0, 0.25, 0.5, 1.0):
        leads, thr_leads, q_means = [], [], []
        for e, s in pick:
            on = int(c["onset"][e][c["onset"][e] >= 0].min())
            tc = int(c["crit"][e, s])
            ticks = np.arange(on, tc, 2)
            base = torch.tensor(c["raw"][e].astype(np.float32))  # [T, S, 27]
            links = torch.tensor(c["links"][e][ticks])
            widx = torch.tensor(np.clip(ticks[:, None] + np.arange(-W + 1, 1)[None], 0, None))
            delta = torch.zeros_like(base, requires_grad=True)
            for _ in range(a.steps if eps > 0 else 0):
                # gradient accumulated over chunks of ticks: the full-batch gradient, small memory peak
                for k in range(0, len(ticks), a.chunk):
                    x = (base + delta * scale)[widx[k : k + a.chunk]]
                    loss = ensemble_p60(models, x, links[k : k + a.chunk], temp)[:, s].sum()
                    loss.backward()
                with torch.no_grad():
                    delta -= (eps / 4) * delta.grad.sign()
                    delta.clamp_(-eps, eps)
                delta.grad.zero_()
            with torch.no_grad():  # one pass of every member, in chunks
                p_parts, q_parts = [], []
                for k in range(0, len(ticks), a.chunk):
                    x = (base + delta * scale)[widx[k : k + a.chunk]]
                    b = torch.stack([m(x, links[k : k + a.chunk])["bin_logits"] for m in models]) / temp
                    p_mem = (1 - torch.cumprod(1 - torch.sigmoid(b), -1))[..., 2]  # [K, n, S]
                    p_parts.append((1 - torch.cumprod(1 - torch.sigmoid(b.mean(0)), -1))[..., 2][:, s])
                    q_parts.append(p_mem.std(0)[:, s])
                p = torch.cat(p_parts).numpy()
                q_means.append(float(torch.cat(q_parts).mean()))
            hit = np.flatnonzero(p >= tau)
            leads.append(tc - ticks[hit[0]] if len(hit) else 0)
            # static thresholds, same budget: critical channels moved away from their limits
            r = c["raw"][e : e + 1].astype(np.float32).copy()
            sd = und.sd
            for ch, sign in (("temp_c", -1), ("soc_pct", 1), ("att_err_deg", -1)):
                k = G.FLAT.index(ch)
                r[..., k] = (
                    r[..., k] + sign * eps * sd[k]
                    if ch != "att_err_deg"
                    else np.maximum(r[..., k] - eps * sd[k], 0)
                )
            ts = BL.threshold_score(r, crit)[0, on:tc, s]
            hit = np.flatnonzero(ts >= tau_thr)
            thr_leads.append(tc - on - hit[0] if len(hit) else 0)
        L, TL = np.array(leads, float), np.array(thr_leads, float)
        results[f"eps={eps}"] = dict(
            recall=float((L > 0).mean()),
            median_lead_s=float(np.median(L[L > 0])) if (L > 0).any() else 0.0,
            mean_q=float(np.mean(q_means)),
            threshold_recall=float((TL > 0).mean()),
            threshold_median_lead_s=float(np.median(TL[TL > 0])) if (TL > 0).any() else 0.0,
        )
        print(eps, results[f"eps={eps}"], flush=True)
    out = dict(episodes=len(pick), steps=a.steps, forgeable=FORGEABLE, results=results)
    (config.RESULTS / "eval" / "robustness.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
