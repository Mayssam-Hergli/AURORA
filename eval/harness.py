"""Evaluation harness (CONTEXT 5.8 / Task 7): every method, every split, one protocol.

    python -m eval.harness run --workers 4     # predictions (resumable) + metrics
    python -m eval.harness metrics             # metrics only, from saved predictions
Outputs: results/eval/pred_<method>_<split>.npz, results/eval/metrics.json, results/eval/RESULTS.md
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

from eval import metrics as M
from ml import baselines as BL
from ml import calibration as Cal
from ml import config, features
from ml.dataset import episode_windows, make_batch, window_targets
from ml.model import HORIZONS
from ml.train import load_model, val_items

OUT = config.RESULTS / "eval"
SPLITS = ["val", "test_id", "test_ood", "test_real"]


def splits() -> list[str]:
    """The splits that were generated and cached (test_real needs OPS-SAT data, S7)."""
    return [s for s in SPLITS if (config.CACHE / f"{s}.npz").exists()]


def model_names():
    return sorted(p.stem for p in config.MODELS.glob("*.pt") if p.stem != "baselines")


# ----------------------------------------------------------------------------- forecaster predictions
def eval_ticks(T: int, stride: int) -> np.ndarray:
    """Ticks at which every method is scored: every `stride` s, the same for all episodes (S1).
    Scores are held until the next scored tick, so lead time is resolved to within stride - 1 s."""
    return np.arange(0, T, stride)


def hold_on_stride(score: np.ndarray, stride: int) -> np.ndarray:
    """[n, T, S] per-tick scores -> the value at the last scored tick, as for the forecaster."""
    T = score.shape[1]
    return score[:, (np.arange(T) // stride) * stride]


@torch.no_grad()
def predict_model(name: str, split: str, stride: int, chunk: int = 150):
    torch.set_num_threads(3)
    model = load_model(name)
    W = config.load("model")["window_s"]
    c, _ = features.load_cache(split)
    n, T, S = c["raw"].shape[:3]
    bins = np.full((n, T, S, len(HORIZONS)), np.nan, np.float32)
    nxt = np.full((n, T, S, 6), np.nan, np.float16)
    mal = np.full((n, T, S), np.nan, np.float32)
    for e in range(n):
        raw, lk = episode_windows(c, e, W, 0, T)
        ticks = eval_ticks(T, stride)
        for k in range(0, len(ticks), chunk):
            tk = ticks[k : k + chunk]
            out = model(raw[tk], lk[tk])
            bins[e, tk] = out["bin_logits"].numpy()
            nxt[e, tk] = torch.softmax(out["next_logits"], -1).numpy()
            mal[e, tk] = out["mal_logit"].numpy()
    OUT.mkdir(parents=True, exist_ok=True)
    arrays = {"bins": _ffill(bins).astype(np.float16)}
    if name == "full_s0":  # heads other than P(F) are stored once, to keep files small
        arrays.update(next=_ffill(nxt).astype(np.float16), mal=_ffill(mal).astype(np.float16))
    np.savez_compressed(OUT / f"pred_{name}_{split}.npz", **arrays)


def _ffill(a):
    """Forward-fill along the tick axis (axis 1)."""
    a = a.copy()
    valid = ~np.isnan(a)
    idx = np.where(valid, np.arange(a.shape[1]).reshape((1, -1) + (1,) * (a.ndim - 2)), 0)
    np.maximum.accumulate(idx, axis=1, out=idx)
    return np.take_along_axis(a, idx, axis=1)


# ----------------------------------------------------------------------------- baselines
def baseline_scores(split: str):
    torch.set_num_threads(3)
    syn = config.load("synthetic")
    c, _ = features.load_cache(split)
    n, T, S = c["raw"].shape[:3]
    horizon = config.load("eval")["primary_horizon_s"]
    und = features.Understanding().load(config.MODELS / "understanding.npz")
    seq_models = _sequence_baselines(und)
    out = {k: np.zeros((n, T, S), np.float32) for k in ["threshold", "trajectory", *seq_models]}
    for e in range(n):  # one episode at a time keeps the memory peak small
        raw = c["raw"][e : e + 1].astype(np.float32)
        out["threshold"][e] = BL.threshold_score(raw, syn["critical"])[0]
        out["trajectory"][e] = BL.trajectory_score(raw, syn["critical"], horizon=horizon)[0]
        for kind, (model, stats) in seq_models.items():
            out[kind][e] = BL.sequence_scores(kind, model, (raw[0] - und.mu) / und.sd, err_stats=stats)
    stride = config.load("eval")["stride_s"]
    out = {k: hold_on_stride(v, stride) for k, v in out.items()}  # same schedule as the models (S1)
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT / f"pred_baselines_{split}.npz", **out)


def _sequence_baselines(und):
    path = config.MODELS / "baselines.pt"
    if path.exists():
        ck = torch.load(path, weights_only=False)
        res = {}
        for kind in ("lstm_ae", "gdn"):
            m = BL.LSTMAE() if kind == "lstm_ae" else BL.GDN()
            m.load_state_dict(ck[kind])
            res[kind] = (m.eval(), ck.get(f"{kind}_stats"))
        return res
    c, idx = features.load_cache("train")
    clean = np.flatnonzero((idx.kind == "clean").to_numpy())
    Z = (c["raw"][clean].astype(np.float32) - und.mu) / und.sd
    ck, res = {}, {}
    for kind in ("lstm_ae", "gdn"):
        m = BL.train_sequence_baseline(kind, Z, log=lambda s: None)
        stats = None
        if kind == "gdn":  # robust normalisation of each sensor's error on clean validation data
            cv, iv = features.load_cache("val")
            zc = (
                cv["raw"][np.flatnonzero((iv.kind == "clean").to_numpy())[:10]].astype(np.float32) - und.mu
            ) / und.sd
            errs = []
            for z in zc:
                hist = BL._windows(np.concatenate([z[:1], z[:-1]], 0), 30)
                x = torch.tensor(np.ascontiguousarray(hist).reshape(-1, 30, z.shape[-1]), dtype=torch.float32)
                with torch.no_grad():
                    errs.append(np.abs(m(x).numpy() - z.reshape(-1, z.shape[-1])))
            errs = np.concatenate(errs)
            med = np.median(errs, 0)
            iqr = np.percentile(errs, 75, 0) - np.percentile(errs, 25, 0) + 1e-6
            stats = (med, iqr)
            ck["gdn_stats"] = stats
        ck[kind] = m.state_dict()
        res[kind] = (m, stats)
    torch.save(ck, path)
    return res


# ----------------------------------------------------------------------------- orchestration
def missing_jobs() -> list[list[str]]:
    """Prediction jobs whose output is missing: baselines, ensemble and ablations on every split (S9)."""
    jobs = []
    for split in splits():
        if not (OUT / f"pred_baselines_{split}.npz").exists():
            jobs.append(["baselines", split])
        for name in model_names():
            if not (OUT / f"pred_{name}_{split}.npz").exists():
                jobs.append(["predict", name, split])
    return jobs


def run(workers: int):
    ev = config.load("eval")
    jobs = missing_jobs()
    # baselines first: they also train the sequence baselines once
    first = [j for j in jobs if j[0] == "baselines"][:1]
    for j in first:
        subprocess.run([sys.executable, "-m", "eval.harness", *j], cwd=config.ROOT, check=True)
        jobs.remove(j)
    env = dict(os.environ, OMP_NUM_THREADS="3")
    running = []
    while jobs or running:
        while jobs and len(running) < workers:
            j = jobs.pop(0)
            running.append(
                subprocess.Popen(
                    (
                        [sys.executable, "-m", "eval.harness", *j, "--stride", str(ev["stride_s"])]
                        if j[0] == "predict"
                        else [sys.executable, "-m", "eval.harness", *j]
                    ),
                    cwd=config.ROOT,
                    env=env,
                )
            )
        time.sleep(3)
        running = [p for p in running if p.poll() is None]
    compute_metrics()


# ----------------------------------------------------------------------------- metrics
def _load_pred(name, split, T):
    z = np.load(OUT / f"pred_{name}_{split}.npz")
    return {k: z[k].astype(np.float32) for k in z.files}


def compute_metrics():
    ev = config.load("eval")
    ens_names = [m for m in model_names() if m.startswith("full_s")]
    abl_names = [m for m in model_names() if not m.startswith("full_s")]
    h_idx = HORIZONS.index(ev["primary_horizon_s"])
    data = {}
    for split in splits():
        c, idx = features.load_cache(split)
        data[split] = (c, idx)

    # temperature on validation windows (ensemble mean logits), CONTEXT 5.5
    cv = data["val"][0]
    items = val_items(cv, config.load("model")["val_windows_per_episode"])
    b = make_batch(cv, items, config.load("model")["window_s"])
    with torch.no_grad():
        logits = np.mean([load_model(m)(b["raw"], b["links"])["bin_logits"].numpy() for m in ens_names], 0)
    temp = Cal.fit_temperature(logits, b["y"].numpy(), b["m"].numpy())

    def scores(split):
        c, idx = data[split]
        T = c["raw"].shape[1]
        members = [_load_pred(m, split, T) for m in ens_names]
        bins = np.mean([p["bins"] for p in members], 0)
        p_ens = 1 - np.cumprod(1 - 1 / (1 + np.exp(-bins / temp)), -1)
        p_mem = np.stack([1 - np.cumprod(1 - 1 / (1 + np.exp(-p["bins"] / temp)), -1) for p in members])
        out = {
            "ensemble": p_ens[..., h_idx],
            "single": 1 - np.cumprod(1 - 1 / (1 + np.exp(-members[0]["bins"])), -1)[..., h_idx],
        }
        for k in range(len(HORIZONS)):
            out[f"ensemble@{HORIZONS[k]}"] = p_ens[..., k]
        out["ensemble@act"] = out["ensemble"]
        for m in abl_names:
            if (OUT / f"pred_{m}_{split}.npz").exists():
                pb = _load_pred(m, split, T)["bins"]
                out[m.replace("_s0", "")] = (1 - np.cumprod(1 - 1 / (1 + np.exp(-pb)), -1))[..., h_idx]
        bl = np.load(OUT / f"pred_baselines_{split}.npz")
        out.update({k: bl[k] for k in bl.files})
        extra = dict(
            p_all=p_ens, q=p_mem.std(0), next=members[0]["next"], mal=1 / (1 + np.exp(-members[0]["mal"]))
        )
        return out, extra

    val_scores, _ = scores("val")
    cv, iv = data["val"]
    benign_v = M.benign_mask(cv, iv.kind.to_numpy())
    taus = {k: M.tau_at_fpr(v, benign_v, ev["target_fpr"]) for k, v in val_scores.items()}
    taus["ensemble@act"] = float(
        ev["response"]["act_threshold"]
    )  # the response engine's own action threshold
    results = {"temperature": temp, "tau": taus, "splits": {}}
    cal = Cal.calibration_record(temp, taus["ensemble"], ens_names, ev)
    (config.MODELS / "calibration.json").write_text(json.dumps(cal, indent=1))
    for split in [x for x in ev["test_splits"] if x in data]:
        c, idx = data[split]
        kinds = idx.kind.to_numpy()
        benign = M.benign_mask(c, kinds)
        sc, extra = scores(split)
        R = {"methods": {}, "by_scenario": {}, "by_precursor": {}}
        sample = _ranking_sample(c, h_idx)
        for k, s in sc.items():
            rows = M.alerts(s, c, taus[k])
            R["methods"][k] = dict(
                tau=taus[k],
                realized_fpr=M.realized_fpr(s, benign, taus[k]),
                false_alarms_per_sat_hour=M.false_alarms_per_hour(s, benign, taus[k]),
                **M.pre_onset_false_alarms(s, c, kinds, taus[k]),
                **M.lead_summary(rows),
                **M.bootstrap(rows, ev["bootstrap"]),
                episode=M.episode_pr(s, c, kinds, taus[k]),
                **_ranking(s, sample),
            )
            if k == "ensemble":
                R["by_type"] = _by_type(rows, idx)
            if "@" not in k:
                R["by_scenario"][k] = _group(rows, idx.scenario.to_numpy())
                R["by_precursor"][k] = _group(rows, idx.precursor.to_numpy())
        R["calibration"] = _calibration(c, extra["p_all"], ev["ece_bins"])
        R["next_sub"], R["malicious"] = _heads(c, extra)
        R["uncertainty"] = dict(
            frac_steps_q_above_gate=float((extra["q"][..., h_idx] > ev["response"]["theta_uncert"]).mean()),
            median_q_benign=float(np.median(extra["q"][..., h_idx].transpose(0, 2, 1)[benign])),
        )
        results["splits"][split] = R
        print(f"{split}: done", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "metrics.json").write_text(json.dumps(results, indent=1, default=float))
    _write_calibration(temp, taus.get("ensemble"), ev["target_fpr"], len(ens_names))
    write_markdown(results)
    return results


def _write_calibration(temp: float, tau: float | None, fpr: float, n_models: int) -> None:
    """M5: the calibration lives next to the models (data/models/calibration.json), so the online
    forecaster never runs uncalibrated by accident. train writes T; evaluation refits T on the same
    validation windows and adds tau (0.1 % FPR on validation benign ticks, primary horizon)."""
    from ml.calibration import write_calibration

    write_calibration(temperature=temp, tau=tau, fpr_target=fpr, n_models=n_models, source="eval.harness")


def _ranking_sample(c, h_idx, every=5):
    """Satellite-ticks with a defined P(F < t + D) target, every `every` ticks (for AUROC/AUPRC)."""
    T = c["raw"].shape[1]
    E, Tt, S, Y = [], [], [], []
    for e in range(len(c["raw"])):
        for t in range(0, T, every):
            tg = window_targets(c, e, t, T)
            ok = np.flatnonzero(tg["m"][:, h_idx] > 0)
            E += [e] * len(ok)
            Tt += [t] * len(ok)
            S += ok.tolist()
            Y += tg["y"][ok, h_idx].tolist()
    return np.array(E), np.array(Tt), np.array(S), np.array(Y)


def _ranking(score, sample):
    from sklearn.metrics import average_precision_score, roc_auc_score

    e, t, s, y = sample
    x = np.nan_to_num(score[e, t, s], posinf=1e6)
    if 0 < y.mean() < 1:
        return dict(auroc=float(roc_auc_score(y, x)), auprc=float(average_precision_score(y, x)))
    return dict(auroc=np.nan, auprc=np.nan)


def _group(rows, labels):
    out = {}
    for g in sorted({str(labels[r["episode"]]) for r in rows}):
        rr = [r for r in rows if str(labels[r["episode"]]) == g]
        out[g] = {k: v for k, v in M.lead_summary(rr).items() if k in ("failing", "recall", "median_lead_s")}
    return out


def _by_type(rows, idx):
    types = idx.type.to_numpy()
    out = {}
    for t in sorted(set(types)):
        rr = [r for r in rows if types[r["episode"]] == t]
        if rr:
            out[t] = {
                k: v for k, v in M.lead_summary(rr).items() if k in ("failing", "recall", "median_lead_s")
            }
    return out


def _calibration(c, p_all, bins):
    T = c["raw"].shape[1]
    out = {}
    for k, h in enumerate(HORIZONS):
        ps, ys = [], []
        for e in range(len(c["raw"])):
            for t in range(0, T, 5):
                tg = window_targets(c, e, t, T)
                ok = tg["m"][:, k] > 0
                ps.append(p_all[e, t, ok, k])
                ys.append(tg["y"][ok, k])
        out[str(h)] = Cal.ece(np.concatenate(ps), np.concatenate(ys), bins)
    return out


def _heads(c, extra):
    T = c["raw"].shape[1]
    P, Y, PM, YM = [], [], [], []
    for e in range(len(c["raw"])):
        for t in range(0, T, 5):
            tg = window_targets(c, e, t, T)
            P.append(extra["next"][e, t])
            Y.append(tg["next"])
            PM.append(extra["mal"][e, t])
            YM.append(tg["mal"])
    return (
        M.next_sub_accuracy(np.concatenate(P), np.concatenate(Y)),
        M.malicious_auroc(np.concatenate(PM), np.concatenate(YM)),
    )


def write_markdown(res):
    L = [
        "# CASSANDRA ML v1: evaluation results",
        "",
        "Synthetic constellation episodes (12 satellites); tau chosen on validation clean + fault "
        "episodes at 0.1 % FPR and applied unchanged to each test split. Lead time = T_critical - T_alert. "
        "Every method is scored every stride_s seconds from tick 0; alarms before the attack onset "
        "count as false alarms (Pre-onset FA).",
        "",
    ]
    for split, R in res["splits"].items():
        L += [
            f"## {split}",
            "",
            "| Method | Recall | Median lead (s) | IQR (s) | Realized FPR | Pre-onset FA / sat-h "
            "| Episode P / R (reaches F) |",
            "|---|---|---|---|---|---|---|",
        ]
        for k, v in R["methods"].items():
            ep = v["episode"]["reaches_F"]
            iqr = f"{v['iqr_lead_s'][0]:.0f}-{v['iqr_lead_s'][1]:.0f}"
            L.append(
                f"| {k} | {v['recall']:.2f} | {v['median_lead_s']:.0f} | {iqr} "
                f"| {100 * v['realized_fpr']:.2f} % | {v['pre_onset_false_alarms_per_sat_hour']:.2f} "
                f"| {ep['precision']:.2f} / {ep['recall']:.2f} |"
            )
        L += [
            "",
            f"ECE (10 bins) by horizon: {R['calibration']}",
            f"Next subsystem: {R['next_sub']}",
            f"Malicious vs fault: {R['malicious']}",
            f"Uncertainty: {R['uncertainty']}",
            "",
        ]
    (OUT / "RESULTS.md").write_text("\n".join(L))


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("predict")
    p.add_argument("model")
    p.add_argument("split")
    p.add_argument("--stride", type=int, default=2)
    b = sub.add_parser("baselines")
    b.add_argument("split")
    r = sub.add_parser("run")
    r.add_argument("--workers", type=int, default=3)
    sub.add_parser("metrics")
    a = ap.parse_args(argv)
    if a.cmd == "predict":
        predict_model(a.model, a.split, a.stride)
    elif a.cmd == "baselines":
        baseline_scores(a.split)
    elif a.cmd == "run":
        run(a.workers)
    else:
        compute_metrics()


if __name__ == "__main__":
    main()
