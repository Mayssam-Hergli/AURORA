"""Load cassandra/config.yaml and resolve each satellite's profile (defaults + overrides)."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from cassandra.world import SatProfile

DEFAULT_PATH = Path(__file__).with_name("config.yaml")


def load(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    """The config as a dict; `overrides` is deep-merged on top (used by tests and demo variants)."""
    cfg = yaml.safe_load(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    if overrides:
        _merge(cfg, copy.deepcopy(overrides))
    return cfg


def profiles(cfg: dict) -> dict[str, SatProfile]:
    sc = cfg["satellites"]
    out = {}
    for sat in sc["ids"]:
        p = {**sc["defaults"], **(sc.get("overrides") or {}).get(sat, {})}
        out[sat] = SatProfile(
            sat=sat,
            kind=p["kind"],
            subs=tuple(p["subs"]),
            t_max_c=p.get("t_max_c"),
            soc_min_pct=p.get("soc_min_pct"),
            theta_max_deg=p.get("theta_max_deg"),
            heating_rate_c_s=float(p["heating_rate_c_s"]),
            heater_min_rise_c_s=float(p["heater_min_rise_c_s"]),
        )
    return out


def _merge(base: dict, extra: dict) -> None:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
