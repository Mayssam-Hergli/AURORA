"""ESA Anomaly Dataset / ESA-ADB (Kotowski et al., arXiv:2406.17826; data: Zenodo 12528696).

Status: loader written against the format used by the official preprocessing scripts
(kplabs-pl/ESA-ADB, notebooks/data-prep). It has NOT been run here yet: each mission is a
3.7-4.1 GB download (`python -m eval.datasets.download esa-adb --mission 1 --allow-large`).

Unzipped layout of one mission (e.g. data/esa-adb/ESA-Mission1/):
    channels/<channel>.zip       pandas pickle (zip-compressed), DatetimeIndex, one value column
    telecommands/<name>.zip      pandas pickle, telecommand activity
    labels.csv                   ID, Channel, StartTime, EndTime (annotated anomalies)
    anomaly_types.csv            ID, Category, ...
    telecommands.csv             Telecommand, Priority

Role in AURORA (CONTEXT 5.6): false alarms on real operational noise. Use `nominal_windows` to
pull anomaly-free stretches of selected channels and measure each method's false-positive rate.
"""

from __future__ import annotations

import pandas as pd

from ml.config import DATA

SRC = DATA / "esa-adb"


def mission_dir(mission: int):
    return SRC / f"ESA-Mission{mission}"


def channels(mission: int) -> list[str]:
    return sorted(p.stem for p in (mission_dir(mission) / "channels").glob("*.zip"))


def load_channel(mission: int, channel: str, resample: str = "1s") -> pd.Series:
    df = pd.read_pickle(mission_dir(mission) / "channels" / f"{channel}.zip")
    s = df[channel] if channel in df.columns else df.iloc[:, 0]
    return s.resample(resample).last().ffill()


def labels(mission: int) -> pd.DataFrame:
    lab = pd.read_csv(mission_dir(mission) / "labels.csv", parse_dates=["StartTime", "EndTime"])
    types = pd.read_csv(mission_dir(mission) / "anomaly_types.csv")
    return lab.merge(types, on="ID", how="left")


def nominal_windows(
    mission: int, channel: str, length_s: int = 600, n: int = 100, seed: int = 0
) -> list[pd.Series]:
    """Random anomaly-free windows of one channel (for false-positive measurements)."""
    s = load_channel(mission, channel)
    lab = labels(mission)
    bad = pd.Series(False, index=s.index)
    for r in lab[lab.Channel == channel].itertuples():
        bad[(s.index >= r.StartTime) & (s.index <= r.EndTime)] = True
    out, rng = [], pd.Series(range(len(s))).sample(frac=1.0, random_state=seed)
    for start in rng:
        w = slice(start, start + length_s)
        if start + length_s <= len(s) and not bad.iloc[w].any():
            out.append(s.iloc[w])
        if len(out) >= n:
            break
    return out
