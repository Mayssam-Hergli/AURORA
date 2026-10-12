"""The AURORA interface contract as used by the ML side (docs/CONTEXT.md Section 6).

This module mirrors Section 6.2 (message schemas) and Section 6.3 (episode format). When the
team's `aurora_common/schemas.py` lands (Task 2), import from there instead and delete the
duplicated definitions below; the episode reader/writer can stay.

Episode layout (Section 6.3):
    {root}/{episode_id}/telemetry.parquet   one row per (t, sat, sub): t, sat, src, sub, <fields>
    {root}/{episode_id}/commands.parquet    one row per telecommand (Section 6.2 message fields)
    {root}/{episode_id}/links.parquet       one row per (t, a, b): feasible, enabled, range_km
    {root}/{episode_id}/labels.json         Section 6.3 labels (recorder / evaluation only)

ML extension (proposal, see docs/ML_GUIDE.md): {episode_id}/ml_labels.json holds per-satellite
ground truth that labels.json cannot express (true critical times of every satellite, onset
of anomalous activity per satellite, the order in which subsystems are affected, and whether
each anomaly is malicious or a natural fault). Like labels.json it is ground truth: it is read
by training and evaluation only, never by the CASSANDRA core (Rule 7).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

SCHEMA_VERSION = 1
SUBSYSTEMS = ["thermal", "eps", "adcs", "obc", "comms", "sun"]

# Section 6.2 "per-subsystem fields (minimum)". wheel_rpm is a 3-vector and isl_peers a list:
# in the flat Parquet table they are stored as wheel_rpm_0..2 and as a comma-separated string.
FIELDS: dict[str, list[str]] = {
    "thermal": ["temp_c", "heater_on"],
    "eps": ["soc_pct", "bus_v", "load_w", "solar_w", "current_ma"],
    "adcs": ["att_err_deg", "wheel_rpm", "rate_dps"],
    "obc": ["mode", "cmd_ok", "cmd_rej", "last_origin"],
    "comms": ["ground_contact", "isl_peers", "rssi_dbm"],
    "sun": ["level", "eclipse"],
}
OBC_MODES = ["NOMINAL", "SAFE", "QUARANTINED"]
COMMANDS = ["HEATER", "SAFE_MODE", "SET_MODE", "ISL_ENABLE", "PING"]
NODE_STATES = ["nominal", "flagged", "quarantined", "safe_hold"]
HORIZONS_S = [10, 30, 60, 120]


def origin_kind(origin: str | None) -> str:
    """ground | core | isl | unknown | none (origin strings are 'isl:<peer_id>' for relays)."""
    if not origin:
        return "none"
    if origin.startswith("isl:"):
        return "isl"
    return origin if origin in ("ground", "core", "unknown") else "unknown"


# ----------------------------------------------------------------------------- labels (6.3)
class EpisodeLabels(BaseModel):
    episode_id: str
    generator: str
    seed: int
    scenario: str  # A | B | C | F | N
    kind: Literal["attack", "fault", "clean"]
    type: str  # A1..A3, B1, B2, C1, F1..F5, N
    target: str | None
    onset_t: float | None
    critical_t: float | None  # null if F is never reached
    critical_rule: str | None
    params: dict = Field(default_factory=dict)
    satellites: list[str]


class SatelliteTruth(BaseModel):
    """ML extension: ground truth for one satellite of an episode."""

    onset_t: float | None = None  # first anomalous activity on this satellite
    critical_t: float | None = None  # true critical state F (responses disabled)
    critical_rule: str | None = None
    malicious: bool | None = None  # attack (True) vs natural fault (False)
    affected_t: dict[str, float] = Field(default_factory=dict)  # subsystem -> first affected time
    reported_masked: list[str] = Field(default_factory=list)  # channels whose telemetry was forged


class MLLabels(BaseModel):
    episode_id: str
    t0: float  # Unix time of the first tick
    dt_s: float
    n_ticks: int
    family: dict = Field(default_factory=dict)  # generator family knobs (for held-out splits)
    satellites: dict[str, SatelliteTruth]


# ----------------------------------------------------------------------------- forecast (6.2)
def forecast_message(
    sat: str,
    t: float,
    p_fail: dict[int, float],
    p_std: dict[int, float] | None,
    next_sub: str | None,
    p_malicious: float | None,
    model: str = "tgn-v1",
    warming_up: bool | None = None,
    q_horizon_s: int = 60,
) -> dict:
    """Payload for aurora/v1/core/forecast/{sat} (docs/interfaces.md 3.5). What a model does not know
    is null, never 0 (D4): p_std and q with fewer than 2 models, next_sub and p_malicious for the
    trajectory forecaster. q is p_std at the decision horizon."""
    msg = {
        "v": SCHEMA_VERSION,
        "sat": sat,
        "t": float(t),
        "model": model,
        "p_fail": {str(k): round(float(v), 4) for k, v in p_fail.items()},
        "p_std": None if p_std is None else {str(k): round(float(v), 4) for k, v in p_std.items()},
        "q": None if p_std is None or q_horizon_s not in p_std else round(float(p_std[q_horizon_s]), 4),
        "next_sub": next_sub,
        "p_malicious": None if p_malicious is None else round(float(p_malicious), 4),
    }
    if warming_up is not None:
        msg["warming_up"] = bool(warming_up)
    return msg


# ----------------------------------------------------------------------------- episode IO (6.3)
def write_episode(
    root: Path,
    episode_id: str,
    telemetry: pd.DataFrame,
    commands: pd.DataFrame,
    links: pd.DataFrame,
    labels: EpisodeLabels,
    ml_labels: MLLabels,
) -> Path:
    d = Path(root) / episode_id
    d.mkdir(parents=True, exist_ok=True)
    telemetry.to_parquet(d / "telemetry.parquet", index=False, compression="zstd")
    commands.to_parquet(d / "commands.parquet", index=False, compression="zstd")
    links.to_parquet(d / "links.parquet", index=False, compression="zstd")
    (d / "labels.json").write_text(labels.model_dump_json(indent=1))
    (d / "ml_labels.json").write_text(ml_labels.model_dump_json(indent=1))
    return d


def read_episode(path: Path, with_labels: bool = True) -> dict:
    path = Path(path)
    ep = {
        "telemetry": pd.read_parquet(path / "telemetry.parquet"),
        "commands": pd.read_parquet(path / "commands.parquet"),
        "links": pd.read_parquet(path / "links.parquet"),
    }
    if with_labels:
        ep["labels"] = EpisodeLabels.model_validate_json((path / "labels.json").read_text())
        ep["ml_labels"] = MLLabels.model_validate_json((path / "ml_labels.json").read_text())
    return ep


def list_episodes(root: Path) -> list[Path]:
    return sorted(p for p in Path(root).iterdir() if (p / "labels.json").exists())


def telemetry_columns() -> list[str]:
    """Flat column set of telemetry.parquet (vectors and lists flattened)."""
    cols = ["t", "sat", "src", "seq", "sub"]
    for sub in SUBSYSTEMS:
        for f in FIELDS[sub]:
            if f == "wheel_rpm":
                cols += ["wheel_rpm_0", "wheel_rpm_1", "wheel_rpm_2"]
            else:
                cols.append(f)
    return cols


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text())
