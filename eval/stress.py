"""Stress tests of the cyber evidence (simulation): how much lead time is left when the attacker hides
the origin of its telecommands, or when its telecommands are not observable at all.

    python -m eval.stress                      # results/eval/stress.json

Both transforms touch only attack episodes and only observable channels (the telecommand statistics
of the OBC node); the models and the threshold tau (validation, 0.1 % FPR) are unchanged.
    spoof_ground   unknown- and ISL-origin commands are reported as ground-origin commands
    no_commands    no telecommand evidence at all (per-origin and per-type counts and the accepted /
                   rejected counters set to zero): the physics-only lower bound
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import torch

from eval import metrics as M
from ml import config, features
from ml import graph as G
from ml.dataset import episode_windows
from ml.model import HORIZONS
from ml.train import load_model

TC = ["tc_ground", "tc_isl", "tc_unknown", "tc_heater_on", "tc_set_mode", "tc_ping"]
VARIANTS = ["original", "spoof_ground", "no_commands"]


def transform(raw: np.ndarray, variant: str) -> np.ndarray:
    r = raw.astype(np.float32)
    k = {n: G.FLAT.index(n) for n in [*TC, "d_cmd_ok", "d_cmd_rej"]}
    if variant == "spoof_ground":
        r[..., k["tc_ground"]] += r[..., k["tc_isl"]] + r[..., k["tc_unknown"]]
        r[..., k["tc_isl"]] = 0
        r[..., k["tc_unknown"]] = 0
    elif variant == "no_commands":
        for i in k.values():
            r[..., i] = 0
    return r


@torch.no_grad()
def ensemble_p60(models, c: dict, ticks: np.ndarray, temp: float, W: int, chunk: int = 150) -> np.ndarray:
    T, S = c["raw"].shape[1:3]
    raw, lk = episode_windows(c, 0, W, 0, T)
    out = np.full((T, S), np.nan, np.float32)
    h = HORIZONS.index(60)
    for k in range(0, len(ticks), chunk):
        tk = ticks[k : k + chunk]
        bins = torch.stack([m(raw[tk], lk[tk])["bin_logits"] for m in models]).mean(0) / temp
        out[tk] = (1 - torch.cumprod(1 - torch.sigmoid(bins), -1))[..., h].numpy()
    return out


def _summary(rows, labels):
    out = {k: v for k, v in M.lead_summary(rows).items() if k in ("failing", "recall", "median_lead_s")}
    out["by_type"] = {}
    for g in sorted({labels[r["episode"]] for r in rows}):
        rr = [r for r in rows if labels[r["episode"]] == g]
        out["by_type"][g] = {
            k: v for k, v in M.lead_summary(rr).items() if k in ("failing", "recall", "median_lead_s")
        }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test_id")
    ap.add_argument("--stride", type=int, default=2)
    a = ap.parse_args(argv)
    torch.set_num_threads(3)
    met = json.loads((config.RESULTS / "eval" / "metrics.json").read_text())
    temp, tau = met["temperature"], met["tau"]["ensemble"]
    models = [load_model(n) for n in sorted(p.stem for p in config.MODELS.glob("full_s*.pt"))]
    W = config.load("model")["window_s"]
    c, idx = features.load_cache(a.split)
    T, S = c["raw"].shape[1:3]
    eps = [e for e in range(len(idx)) if idx.kind[e] == "attack" and (c["crit"][e] >= 0).any()]
    truth = {k: c[k][eps] for k in ("crit", "onset", "malicious")}
    types = idx.type.to_numpy()[eps]
    res = {"split": a.split, "attack_episodes": len(eps), "tau": tau, "variants": {}}
    for variant in VARIANTS:
        score = np.full((len(eps), T, S), np.nan, np.float32)
        for i, e in enumerate(eps):
            on, cr = c["onset"][e], c["crit"][e]
            start, end = int(on[on >= 0].min()), int(cr.max()) + 1
            ticks = np.arange(start - start % a.stride, end, a.stride)
            ce = {"raw": transform(c["raw"][e : e + 1], variant), "links": c["links"][e : e + 1]}
            score[i] = ensemble_p60(models, ce, ticks, temp, W)
        res["variants"][variant] = _summary(M.alerts(score, truth, tau), types)
        print(variant, {k: v for k, v in res["variants"][variant].items() if k != "by_type"}, flush=True)
    (config.RESULTS / "eval" / "stress.json").write_text(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
