"""OPS-SAT-AD (Ruszczak et al., Scientific Data 12, 710, 2025): real-noise bank.

Nominal segments of the test partition become unit-variance residual sequences (signal minus a
rolling median, divided by a robust scale) and real gap indicators. The synthetic generator's
`test_real` split uses them instead of synthetic measurement noise, to measure false-positive
behaviour under real noise and gaps (CONTEXT 5.6 lists ESA-ADB for this role; OPS-SAT-AD is the
small stand-in until ESA-ADB can be downloaded).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.config import DATA

SRC = DATA / "opssat-ad"
BANK = DATA / "opssat-ad" / "noise_bank.npz"


def build_bank(min_len: int = 20) -> dict:
    seg = pd.read_csv(SRC / "segments.csv", parse_dates=["timestamp"])
    seg = seg[(seg.anomaly == 0) & (seg.train == 0)].sort_values(["segment", "timestamp"])
    res, gap = [], []
    for _, d in seg.groupby("segment"):
        v = d.value.to_numpy(float)
        if len(v) < min_len:
            continue
        r = v - pd.Series(v).rolling(9, center=True, min_periods=1).median().to_numpy()
        mad = np.median(np.abs(r - np.median(r))) * 1.4826
        sd = mad if mad > 0 else r.std()
        if not np.isfinite(sd) or sd == 0:
            continue
        r = np.clip(r / sd, -15, 15)
        r = r / max(r.std(), 1e-6)
        dt = d.timestamp.diff().dt.total_seconds().to_numpy()
        nominal = np.nanmedian(dt)
        res.append(r)
        gap.append(np.nan_to_num(dt, nan=nominal) > 1.5 * nominal)
    return {"residual": np.concatenate(res).astype(np.float32), "gap": np.concatenate(gap)}


def load_bank() -> dict:
    if not BANK.exists():
        np.savez(BANK, **build_bank())
    z = np.load(BANK)
    return {k: z[k] for k in z.files}
