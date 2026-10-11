"""Data types shared by the core's stages: satellite profiles, ingest notes and the per-tick world."""

from __future__ import annotations

from dataclasses import dataclass, field

from aurora_common.schemas import Telecommand


@dataclass(frozen=True)
class SatProfile:
    """What the core expects from one satellite (cassandra/config.yaml)."""

    sat: str
    kind: str  # "twin" | "hw": the src its telemetry must carry
    subs: tuple[str, ...]  # subsystems it publishes
    t_max_c: float | None
    soc_min_pct: float | None
    theta_max_deg: float | None


@dataclass(frozen=True)
class Note:
    """Something ingest noticed: becomes a core/event (explain.py)."""

    kind: str  # seq_gap | seq_reset | provenance | stale | fresh | clock | invalid | late
    level: str  # info | warning | alarm
    text: str
    sat: str | None = None
    sub: str | None = None
    data: dict = field(default_factory=dict)


@dataclass
class WorldTick:
    """Everything observed for one 1 s tick [t, t + 1) of message time."""

    t: float  # tick start (whole second, message time)
    values: dict[str, dict[str, dict]]  # sat -> sub -> fields, fresh (held <= hold_s) values only
    age_s: dict[str, dict[str, float]]  # sat -> sub -> seconds since its last message, at tick end
    stale: dict[str, list[str]]  # sat -> expected subsystems with no fresh value
    src: dict[str, str]  # sat -> src of its latest telemetry
    emulated: dict[str, list[str]]  # sat -> fields the publisher marked as emulated
    links: dict[tuple[str, str], dict]  # (a, b) with a < b -> feasible, enabled, range_km, t
    tcs: list[Telecommand]  # telecommands received while this tick was open
    notes: list[Note]
    closed_by: str  # "message" | "watchdog"

    def fresh(self, sat: str, sub: str) -> dict | None:
        return self.values.get(sat, {}).get(sub)
