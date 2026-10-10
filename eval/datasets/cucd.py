"""CuCD-ID (Fayyaz et al., Data in Brief 65, 112598, 2026): audit and cyber-evidence scorer.

Each CuCD-ID class was recorded in its own session; several columns are session constants, so a
classifier can reach ~100 % accuracy by recognising the session rather than the attack. We
therefore use traffic-behaviour features only, a per-class time split, and a one-class detector
fitted on normal traffic, evaluated leave-one-attack-out.

    python -m eval.datasets.cucd        # writes results/datasets/cucd_audit.json
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest
from sklearn.metrics import roc_auc_score

from ml.config import DATA, RESULTS

SRC = DATA / "cucd-id"
LABELS = {
    0: "command_flooding",
    1: "data_injection",
    2: "defence_impairment",
    3: "normal",
    4: "storage_exhaustion",
}
TRAFFIC = [
    "FlowLengthInWindow",
    "SlidingWindowMeanIntervalSec",
    "SlidingWindowMaxIntervalSec",
    "SlidingWindowMinIntervalSec",
    "AverageMessageLengthInWindow",
    "MessageRateInWindow",
    "StdDevMessageLengthInWindow",
]
MEMORY = [
    "MemoryAnonMB",
    "MemoryFileMB",
    "MemoryKernelstackMB",
    "MemoryPageTableMB",
    "MemorySocketMB",
    "MemoryPercpuMB",
    "MemoryShmemMB",
    "MemorySlabUnreclaimableMB",
]


def load(noised: bool = True) -> pd.DataFrame:
    df = pd.read_csv(SRC / ("noised_dataset.csv" if noised else "consolidated_dataset_raw.csv"))
    df["Label"] = df["Label"].astype(int)
    return df


def time_split(df, frac=0.7, gap=30):
    tr, te = [], []
    for _, d in df.groupby("Label"):
        d = d.sort_values("TimeRadians", kind="stable")
        n = int(frac * len(d))
        tr.append(d.iloc[:n])
        te.append(d.iloc[n + gap :])
    return pd.concat(tr), pd.concat(te)


class OneClassScorer:
    """Isolation Forest on normal traffic; score = empirical CDF against normal training rows."""

    def fit(self, normal):
        self.iso = IsolationForest(n_estimators=300, random_state=0).fit(normal[TRAFFIC])
        self.ref = np.sort(-self.iso.score_samples(normal[TRAFFIC]))
        return self

    def raw(self, df):
        return -self.iso.score_samples(df[TRAFFIC])

    def score(self, df):
        return np.searchsorted(self.ref, self.raw(df), side="right") / len(self.ref)


def audit() -> dict:
    raw = load(noised=False)
    tr, te = time_split(raw)
    shortcut = {}
    for name, cols in (("memory_only", MEMORY), ("traffic_only", TRAFFIC)):
        m = HistGradientBoostingClassifier(max_iter=200).fit(tr[cols], tr.Label)
        shortcut[name] = float((m.predict(te[cols]) == te.Label).mean())
    out = {"shortcut_accuracy_time_split": shortcut, "leave_one_attack_out": {}}
    noised = load(noised=True)
    tr, te = time_split(noised)
    scorer = OneClassScorer().fit(tr[tr.Label == 3])
    for held in (0, 1, 2, 4):
        b = te[te.Label.isin([3, held])]
        y = (b.Label != 3).astype(int)
        out["leave_one_attack_out"][LABELS[held]] = float(roc_auc_score(y, scorer.raw(b)))
    normal_test = scorer.score(te[te.Label == 3])
    out["normal_score_drift"] = {
        "median_test_normal_score": float(np.median(normal_test)),
        "frac_test_normal_above_0.95": float((normal_test > 0.95).mean()),
    }
    return out


def main():
    res = audit()
    (RESULTS / "datasets").mkdir(parents=True, exist_ok=True)
    (RESULTS / "datasets" / "cucd_audit.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
