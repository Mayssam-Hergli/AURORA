"""SCAD (Space Cyber Anomaly Detection dataset, BigBear.ai / Redwire / Proof Labs, CC BY 4.0).

FireSat digital twin, 10-minute runs at 1 Hz, ~140 channels, per-second `result_label`; the
reaction-wheel override attack starts at ~302 s. Every attack run has a matched baseline run
(same month and time offset, identical before onset), which gives ground truth for when each
subsystem is affected.

SCAD has no mission-failure label, so F is ours to define (CONTEXT 5.6). Measured on the
downloaded subset: pointing deviates from the matched baseline by more than 2 deg in only ~3 %
of attack runs within the 10-minute window, while torque-rod commands leave the baseline
envelope at onset in ~86 % of runs. SCAD is therefore used here for detection delay and
propagation order; lead time to F is reported only for the few runs that reach F.

    python -m eval.datasets.scad          # results/datasets/scad_profile.json
"""

from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pandas as pd

from ml.config import DATA, RESULTS

SRC = DATA / "scad"
ONSET_S = 302
GROUPS = {  # SCAD channels grouped into subsystem nodes (graph node set for a single satellite)
    "wheels": [
        "SensedWheelSpeed__RWA_A(rad/sec)",
        "SensedWheelSpeed__RWA_B(rad/sec)",
        "SensedWheelSpeed__RWA_C(rad/sec)",
        "WheelCmd__RWA_A(N*m)",
        "WheelCmd__RWA_B(N*m)",
        "WheelCmd__RWA_C(N*m)",
    ],
    "torque_rods": [
        "TotalTorqueRodCommand[0](A*m^2)",
        "TotalTorqueRodCommand[1](A*m^2)",
        "TotalTorqueRodCommand[2](A*m^2)",
    ],
    "attitude": [
        "AttitudeError[0](rad)",
        "AttitudeError[1](rad)",
        "AttitudeError[2](rad)",
        "AngVel_B_I[0](rad/sec)",
        "AngVel_B_I[1](rad/sec)",
        "AngVel_B_I[2](rad/sec)",
    ],
    "imu": ["IMURate_B__IMU[0](rad/sec)", "IMURate_B__IMU[1](rad/sec)", "IMURate_B__IMU[2](rad/sec)"],
    "star_tracker": ["TrackerValid__ST()", "TrackerRateMag__ST(rad/sec)"],
    "sun": ["SunUnit_B[0](1)", "SunUnit_B[1](1)", "SunUnit_B[2](1)"],
    "power": ["PercentSoC__BAT(1)", "IncomingCharge__BAT(W)", "TotalLoad__BAT(W)", "InEclipse()"],
}
CHANNELS = [c for g in GROUPS.values() for c in g]


def metadata() -> pd.DataFrame:
    path = SRC / "selected_metadata.csv"
    if not path.exists():
        path = SRC / "all_metadata.csv"
    m = pd.read_csv(path)
    m["key"] = m.Month.astype(str) + "_" + m.Time_offset.astype(str)
    m["path"] = [SRC / f.lstrip("./") for f in m.data_file]
    return m[[p.exists() for p in m.path]].reset_index(drop=True)


@lru_cache(maxsize=256)
def load_run(path: str) -> pd.DataFrame:
    return pd.read_csv(path)


def attitude_deg(d: pd.DataFrame) -> np.ndarray:
    return np.rad2deg(np.linalg.norm(d[GROUPS["attitude"][:3]].to_numpy(float), axis=1))


def pairs(meta: pd.DataFrame) -> list[tuple[pd.Series, pd.Series]]:
    base = {r.key: r for r in meta[meta.Scenario == "baseline"].itertuples()}
    return [(r, base[r.key]) for r in meta[meta.Scenario != "baseline"].itertuples() if r.key in base]


def critical_time(attack: pd.DataFrame, baseline: pd.DataFrame, pointing_dev_deg: float = 2.0) -> int | None:
    """F on SCAD (our definition): attitude error departs from the matched baseline by more than
    `pointing_dev_deg`, i.e. the attack caused pointing loss the nominal mission would not have."""
    n = min(len(attack), len(baseline))
    dev = np.abs(attitude_deg(attack)[:n] - attitude_deg(baseline)[:n])
    hit = np.flatnonzero(dev > pointing_dev_deg)
    return int(hit[0]) if len(hit) else None


def affected_times(attack, baseline, rel_tol: float = 0.05) -> dict[str, int]:
    """First tick at which each subsystem group departs from the matched baseline."""
    n = min(len(attack), len(baseline))
    out = {}
    for g, cols in GROUPS.items():
        a, b = attack[cols].to_numpy(float)[:n], baseline[cols].to_numpy(float)[:n]
        scale = np.nanstd(b, axis=0) + 1e-9
        dev = (np.abs(a - b) / scale).max(1)
        hit = np.flatnonzero(dev > rel_tol)
        if len(hit):
            out[g] = int(hit[0])
    return out


def profile(pointing_dev_deg: float = 2.0) -> dict:
    meta = metadata()
    rows = []
    for a, b in pairs(meta):
        A, B = load_run(str(a.path)), load_run(str(b.path))
        aff = affected_times(A, B)
        rows.append(
            dict(
                scenario=a.Scenario,
                intensity=float(A.AttackIntensity.iloc[0]),
                critical=critical_time(A, B, pointing_dev_deg),
                **{f"delay_{g}": (aff[g] - ONSET_S if g in aff else None) for g in GROUPS},
            )
        )
    r = pd.DataFrame(rows)
    out = {
        "attack_runs": len(r),
        "pointing_dev_deg": pointing_dev_deg,
        "frac_reaching_F": float(r.critical.notna().mean()) if len(r) else None,
        "subsystems": {},
    }
    for g in GROUPS:
        col = r[f"delay_{g}"]
        out["subsystems"][g] = {
            "frac_affected": float(col.notna().mean()),
            "median_delay_s": float(col.dropna().median()) if col.notna().any() else None,
        }
    return out


def main():
    res = profile()
    (RESULTS / "datasets").mkdir(parents=True, exist_ok=True)
    (RESULTS / "datasets" / "scad_profile.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
