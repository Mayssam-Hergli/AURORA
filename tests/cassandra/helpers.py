"""Shared helpers for the core's tests: profiles, config and hand-made WorldTicks."""

from __future__ import annotations

from cassandra import config
from cassandra.world import WorldTick

T0 = 1_800_000_000.0
BOARD = {
    "kind": "hw",
    "subs": ["thermal", "obc"],
    "t_max_c": 60.0,
    "soc_min_pct": None,
    "theta_max_deg": None,
    "heating_rate_c_s": 1.5,
    "heater_min_rise_c_s": 0.5,
}


def cfg_and_profiles(sats=("B", "E"), overrides=None, **sections):
    cfg = config.load(overrides={"satellites": {"ids": list(sats), "overrides": overrides or {}}, **sections})
    return cfg, config.profiles(cfg)


def world(t, values, tcs=(), links=None, stale=None):
    """WorldTick with only the given fresh values: {sat: {sub: fields}}."""
    return WorldTick(
        t=t,
        values=values,
        age_s={},
        stale=stale or {},
        src={},
        emulated={},
        links=links or {},
        tcs=list(tcs),
        notes=[],
        closed_by="message",
    )


def thermal(temp_c, heater_on=0):
    return {"thermal": {"temp_c": float(temp_c), "heater_on": int(heater_on)}}
