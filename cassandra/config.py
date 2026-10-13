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


def parse_sats(spec: str) -> list[str]:
    """'A-L' -> A..L, 'A,C-L' -> A, C..L."""
    ids = "ABCDEFGHIJKL"
    out: list[str] = []
    for part in spec.split(","):
        part = part.strip().upper()
        if "-" in part:
            a, b = part.split("-")
            out += [s for s in ids if a <= s <= b]
        elif part:
            out.append(part)
    return list(dict.fromkeys(out))


def with_twin_sats(cfg: dict, spec: str | None) -> dict:
    """Satellites not in `spec` are hardware: they get satellites.board_profile (Phase 8)."""
    if not spec:
        return cfg
    twins = set(parse_sats(spec))
    sc = cfg["satellites"]
    over = dict(sc.get("overrides") or {})
    for sat in sc["ids"]:
        if sat not in twins:
            over[sat] = {**sc["board_profile"], **over.get(sat, {})}
    sc["overrides"] = over
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
            heal_below_c=float(p["heal_below_c"]),
            window_s=int(p["window_s"]) if p.get("window_s") else None,
            act_horizon_s=int(p["act_horizon_s"]) if p.get("act_horizon_s") else None,
            plan_p=float(p["plan_p"]) if p.get("plan_p") is not None else None,
            thermal=dict(p["thermal"]) if p.get("thermal") else None,
        )
    return out


def _merge(base: dict, extra: dict) -> None:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
