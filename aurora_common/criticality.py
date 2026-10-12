"""Shared strict critical boundaries. None disables a limit (e.g. thermal-only hardware)."""

from __future__ import annotations

import math

RULES = (
    ("thermal", "temp_c", "t_max_c", 1, "T_bus>T_max"),
    ("eps", "soc_pct", "soc_min_pct", -1, "SOC<SOC_min"),
    ("adcs", "att_err_deg", "theta_max_deg", 1, "att_err>theta_max"),
)


def violated(value, limit, direction):
    if limit is None:
        return False
    if direction == 1:
        return value > limit
    return value < limit


def critical(temp, soc, attitude, limits):
    return (
        violated(temp, limits.get("t_max_c"), 1)
        | violated(soc, limits.get("soc_min_pct"), -1)
        | violated(abs(attitude), limits.get("theta_max_deg"), 1)
    )


def observed_safe(values, profile):
    """Recovery requires fresh observations for every enabled failure dimension."""
    for sub, channel, key, direction, _ in RULES:
        limit = getattr(profile, key)
        if limit is None:
            continue
        value = values.get(sub, {}).get(channel)
        if (
            value is None
            or not math.isfinite(value)
            or violated(abs(value) if channel == "att_err_deg" else value, limit, direction)
        ):
            return False
    return True
