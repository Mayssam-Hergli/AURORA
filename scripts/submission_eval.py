"""Deadline-bounded preliminary evaluation; never replaces the full campaign."""

import json
import time

import numpy as np
import torch

from eval import metrics as M
from ml import baselines, config, features
from ml.calibration import fit_temperature, write_calibration
from ml.dataset import episode_windows, make_batch
from ml.train import load_model, val_items


def main():
    start = time.time()
    torch.set_num_threads(1)
    out = config.RESULTS / "submission"
    out.mkdir(parents=True, exist_ok=True)
    names = ["full_s0", "full_s1"]
    models = [load_model(n) for n in names]
    data = {}
    for split in ["val", "test_id", "test_ood", "test_hard"]:
        c, idx = features.load_cache(split)
        chosen = (
            np.arange(len(idx))
            if split == "test_hard"
            else np.sort(
                np.concatenate(
                    [np.flatnonzero(idx.kind.to_numpy() == kind)[:8] for kind in sorted(idx.kind.unique())]
                )
            )
        )
        data[split] = ({k: v[chosen] for k, v in c.items()}, idx.iloc[chosen].reset_index(drop=True), chosen)
    cv = data["val"][0]
    b = make_batch(cv, val_items(cv, 6), 30)
    with torch.no_grad():
        logits = np.mean([m(b["raw"], b["links"])["bin_logits"].numpy() for m in models], 0)
    temperature = fit_temperature(logits, b["y"].numpy(), b["m"].numpy())
    result = dict(
        status="preliminary deadline-bounded evaluation",
        models=names,
        temperature=temperature,
        stride_s=20,
        selection="first 8 episodes of each kind in standard split index; all 48 hard episodes",
        limitations=[
            "Two seeds, not the planned five.",
            "No completed ablations, PGD or full runtime campaign.",
            "Standard test subsets are small; not full-split performance.",
            "20-second scoring stride; lead resolution up to 19 seconds.",
        ],
        splits={},
    )
    thresholds = None
    for split, (c, idx, chosen) in data.items():
        scores = {k: [] for k in ["ensemble", "threshold", "trajectory"]}
        with torch.no_grad():
            for e in range(len(idx)):
                raw, links = episode_windows(c, e, 30, 0, c["raw"].shape[1])
                ticks = np.arange(0, len(raw), 20)
                logits = np.mean([m(raw[ticks], links[ticks])["bin_logits"].numpy() for m in models], 0)
                probability = 1 - np.cumprod(1 - 1 / (1 + np.exp(-logits / temperature)), -1)
                scores["ensemble"].append(probability[(np.arange(len(raw)) // 20), :, 2])
                physical = c["raw"][e : e + 1].astype(np.float32)
                scores["threshold"].append(
                    baselines.threshold_score(physical, config.load("synthetic")["critical"])[0][
                        np.arange(len(raw)) // 20 * 20
                    ]
                )
                scores["trajectory"].append(
                    baselines.trajectory_score(physical, config.load("synthetic")["critical"], horizon=60)[0][
                        np.arange(len(raw)) // 20 * 20
                    ]
                )
        scores = {k: np.stack(v) for k, v in scores.items()}
        benign = M.benign_mask(c, idx.kind.to_numpy())
        if split == "val":
            thresholds = {k: M.tau_at_fpr(v, benign, 0.001) for k, v in scores.items()}
            write_calibration(
                temperature,
                thresholds["ensemble"],
                0.001,
                2,
                "scripts/submission_eval.py: preliminary 24-episode validation subset",
            )
        methods = {}
        for k, score in scores.items():
            rows = M.alerts(score, c, thresholds[k])
            methods[k] = dict(
                tau=thresholds[k],
                realized_fpr=M.realized_fpr(score, benign, thresholds[k]),
                **M.lead_summary(rows),
                **M.bootstrap(rows, 1000),
                **M.pre_onset_false_alarms(score, c, idx.kind.to_numpy(), thresholds[k]),
            )
        result["splits"][split] = dict(
            episodes=len(idx),
            indices=chosen.tolist(),
            kinds=idx.kind.value_counts().to_dict(),
            methods=methods,
        )
        result["seconds"] = time.time() - start
        (out / "metrics.json").write_text(json.dumps(result, indent=2))
        print(split, len(idx), methods["ensemble"], flush=True)
    assert set(result["splits"]) == {"val", "test_id", "test_ood", "test_hard"}


if __name__ == "__main__":
    main()
