"""Supported effects only; latency is one simulator tick, not a measured hardware guarantee."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionSpec:
    target: str
    prerequisite: str
    effect: str
    rollback: str
    verification: str
    allow_unknown: bool


ACTIONS = {
    "none": ActionSpec("none", "always", "abstain", "none", "continue observing", True),
    "cut_heater": ActionSpec(
        "thermal/heater",
        "observed heater on",
        "HEATER off",
        "operator HEATER on",
        "heater off and thermal risk falling",
        True,
    ),
    "safe_mode": ActionSpec(
        "local satellite",
        "supported SAFE_MODE command",
        "protective reversible safe mode",
        "SET_MODE NOMINAL after verification",
        "all enabled critical dimensions safe",
        True,
    ),
    "block_source": ActionSpec(
        "ISL source",
        "observed relaying peer",
        "disable both link ends",
        "restore feasible links",
        "no further relayed commands",
        False,
    ),
    "quarantine": ActionSpec(
        "satellite links",
        "isolation budget available",
        "safe mode and disable links",
        "verified ground reintegration",
        "all enabled critical dimensions safe",
        False,
    ),
}
