"""Train the forecaster ensemble and the ablations (milestone M2).

    python -m ml.train understanding          # fit normaliser, engineering model, couplings
    python -m ml.train job full 0             # one model (graph variant, seed)
    python -m ml.train all --parallel 2       # ensemble + ablations, resumable
Models go to data/models/ (gitignored, *.pt); training logs to results/logs/.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

from ml import config, features
from ml import graph as G
from ml.dataset import make_batch, sample_ticks
from ml.model import Forecaster

VARIANTS = [
    "full",
    "no_eng",
    "unconstrained",
    "no_residual",
    "no_sparta",
    "no_coupling",
    "temporal",
    "telemetry_only",
]


def understanding_path():
    return config.MODELS / "understanding.npz"


def fit_understanding():
    c, idx = features.load_cache("train")
    benign = np.flatnonzero(idx.kind.isin(["clean"]).to_numpy())
    attack = np.flatnonzero(idx.kind.isin(["attack"]).to_numpy())
    und = features.Understanding().fit(c["raw"][benign], c["raw"][attack])
    config.MODELS.mkdir(parents=True, exist_ok=True)
    und.save(understanding_path())
    top = sorted(und.couplings.items(), key=lambda kv: -kv[1][0])[:6]
    print("understanding saved; strongest couplings:", top)


def build_model(variant: str, mc: dict, n_sats: int):
    und = features.Understanding().load(understanding_path())
    if variant == "no_coupling":
        und.couplings = {}
    g = G.build(n_sats, variant)
    model = Forecaster(
        und,
        g,
        hid=mc["hidden"],
        rounds=0 if variant == "temporal" else mc["rounds"],
        edge_hid=mc["edge_hidden"],
        use_residual=variant != "no_residual",
    )

    model.telemetry_only = variant == "telemetry_only"
    return model


def loss_fn(out, b, mc):
    p = out["p_fail"].clamp(1e-5, 1 - 1e-5)
    w = 1 + (mc["pos_weight"] - 1) * b["y"]
    lh = -(w * (b["y"] * torch.log(p) + (1 - b["y"]) * torch.log(1 - p)) * b["m"]).sum() / b[
        "m"
    ].sum().clamp_min(1)
    nx = out["next_logits"].reshape(-1, 6)
    yn = b["next"].reshape(-1)
    ln = F.cross_entropy(nx, yn, ignore_index=-1) if (yn >= 0).any() else nx.sum() * 0
    ml_ = out["mal_logit"].reshape(-1)
    ym = b["mal"].reshape(-1)
    keep = ym >= 0
    lm = F.binary_cross_entropy_with_logits(ml_[keep], ym[keep].float()) if keep.any() else ml_.sum() * 0
    lw = mc["loss_weights"]
    return lw["hazard"] * lh + lw["next"] * ln + lw["malicious"] * lm, dict(
        h=lh.item(), n=ln.item(), m=lm.item()
    )


def val_items(cv, k, seed=123):
    rng = np.random.default_rng(seed)
    T = cv["raw"].shape[1]
    return [(e, int(t)) for e in range(len(cv["raw"])) for t in sample_ticks(cv, e, T, k, rng)]


@torch.no_grad()
def evaluate(model, cv, items, mc):
    model.eval()
    tot, n = 0.0, 0
    for i in range(0, len(items), mc["batch"]):
        b = make_batch(cv, items[i : i + mc["batch"]], mc["window_s"])
        loss, _ = loss_fn(model(b["raw"], b["links"]), b, mc)
        tot += loss.item() * len(b["raw"])
        n += len(b["raw"])
    return tot / n


def train_job(variant: str, seed: int):
    (config.RESULTS / "logs").mkdir(parents=True, exist_ok=True)  # S3: also for a single `train job`
    config.MODELS.mkdir(parents=True, exist_ok=True)
    mc = config.load("model")
    torch.set_num_threads(mc["threads"])
    torch.manual_seed(seed)
    ct, _ = features.load_cache("train")
    cv, _ = features.load_cache("val")
    n_sats, T = ct["raw"].shape[2], ct["raw"].shape[1]
    model = build_model(variant, mc, n_sats)
    opt = torch.optim.Adam(model.parameters(), lr=mc["lr"])
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, mc["epochs"])
    rng = np.random.default_rng(seed)
    vitems = val_items(cv, mc["val_windows_per_episode"])
    name = f"{variant}_s{seed}"
    (config.RESULTS / "logs").mkdir(parents=True, exist_ok=True)  # S3: also for a single `job`
    log = open(config.RESULTS / "logs" / f"train_{name}.log", "w")
    best, best_state, bad, hist = np.inf, None, 0, []
    for ep in range(mc["epochs"]):
        model.train()
        t0 = time.time()
        items = [
            (e, int(t))
            for e in rng.permutation(len(ct["raw"]))
            for t in sample_ticks(ct, e, T, mc["windows_per_episode"], rng)
        ]
        rng.shuffle(items)
        losses = []
        for i in range(0, len(items), mc["batch"]):
            b = make_batch(ct, items[i : i + mc["batch"]], mc["window_s"])
            loss, _ = loss_fn(model(b["raw"], b["links"]), b, mc)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            losses.append(loss.item())
        sched.step()
        val = evaluate(model, cv, vitems, mc)
        hist.append(dict(epoch=ep, train=float(np.mean(losses)), val=val, sec=time.time() - t0))
        print(
            f"{name} epoch {ep} train {np.mean(losses):.4f} val {val:.4f} ({time.time() - t0:.0f}s)",
            file=log,
            flush=True,
        )
        if val < best - 1e-4:
            best, bad, best_state = val, 0, {k: v.clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= mc["patience"]:
                break
    torch.save(
        {
            "state": best_state,
            "variant": variant,
            "seed": seed,
            "model_cfg": mc,
            "n_sats": n_sats,
            "channels": G.FLAT,
            "feature_schema": "causal-v2",
        },
        config.MODELS / f"{name}.pt",
    )
    (config.MODELS / f"{name}.json").write_text(json.dumps(hist, indent=1))
    print(f"{name} done, best val {best:.4f}", file=log, flush=True)


def jobs():
    mc = config.load("model")
    return [("full", s) for s in mc["ensemble_seeds"]] + [(v, 0) for v in mc["ablations"]]


def train_all(parallel: int):
    (config.RESULTS / "logs").mkdir(parents=True, exist_ok=True)
    if not understanding_path().exists():
        fit_understanding()
    todo = [j for j in jobs() if not (config.MODELS / f"{j[0]}_s{j[1]}.pt").exists()]
    env = dict(os.environ, OMP_NUM_THREADS="3", MKL_NUM_THREADS="3")
    running = []
    while todo or running:
        while todo and len(running) < parallel:
            v, s = todo.pop(0)
            err = open(config.RESULTS / "logs" / f"train_{v}_s{s}.err", "w")
            running.append(
                subprocess.Popen(
                    [sys.executable, "-m", "ml.train", "job", v, str(s)], cwd=config.ROOT, env=env, stderr=err
                )
            )
        time.sleep(5)
        failed = [p.returncode for p in running if p.poll() not in (None, 0)]
        if failed:
            raise RuntimeError(f"training jobs failed: {failed}; inspect results/logs/*.err")
        running = [p for p in running if p.poll() is None]
    print("all models trained")
    calibrate()


def calibrate() -> dict:
    """M5: fit the temperature of the ensemble on validation windows and save calibration.json next
    to the models (tau is added by eval.harness once the validation predictions exist)."""
    from ml.calibration import fit_temperature, write_calibration

    mc = config.load("model")
    names = sorted(p.stem for p in config.MODELS.glob("full_s*.pt"))
    if not names:
        raise FileNotFoundError(f"no full_s*.pt models in {config.MODELS}")
    cv, _ = features.load_cache("val")
    b = make_batch(cv, val_items(cv, mc["val_windows_per_episode"]), mc["window_s"])
    with torch.no_grad():
        logits = np.mean([load_model(n)(b["raw"], b["links"])["bin_logits"].numpy() for n in names], 0)
    temp = fit_temperature(logits, b["y"].numpy(), b["m"].numpy())
    data = write_calibration(temp, None, config.load("eval")["target_fpr"], len(names), "ml.train")
    print(f"calibration.json: temperature {temp:.3f} on {len(names)} models (tau added by make evaluate)")
    return data


def load_model(name: str):
    ck = torch.load(config.MODELS / f"{name}.pt", weights_only=False)
    if ck.get("channels") != G.FLAT or ck.get("feature_schema") != "causal-v2":
        raise ValueError(f"{name}: incompatible feature schema; retrain causal 33-channel models")
    model = build_model(ck["variant"], ck["model_cfg"], ck["n_sats"])
    model.load_state_dict(ck["state"])
    return model.eval()


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("understanding")
    sub.add_parser("calibrate")
    j = sub.add_parser("job")
    j.add_argument("variant", choices=VARIANTS)
    j.add_argument("seed", type=int)
    a_ = sub.add_parser("all")
    a_.add_argument("--parallel", type=int, default=2)
    a = ap.parse_args(argv)
    if a.cmd == "understanding":
        fit_understanding()
    elif a.cmd == "calibrate":
        calibrate()
    elif a.cmd == "job":
        train_job(a.variant, a.seed)
    else:
        train_all(a.parallel)


if __name__ == "__main__":
    main()
