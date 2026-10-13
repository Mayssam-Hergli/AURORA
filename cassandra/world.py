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
    heating_rate_c_s: float = 0.2  # known heater effect, used to estimate T when temp_c is untrusted
    heater_min_rise_c_s: float = 0.02  # with the heater on, temp_c must rise at least this fast
    heal_below_c: float = 30.0  # heal only once temp_c is back under this
    window_s: int | None = None  # trajectory slope window for this satellite (None: forecast.window_s)
    act_horizon_s: int | None = None  # decision horizon for this satellite (None: act.horizon_s)
    plan_p: float | None = None  # P(F < t + act_horizon_s) at which J compares responses (None: act.plan_p)
    thermal: dict | None = None  # design thermal model: plausible heating-rate bounds (None: the constants)

    def min_rise(self, temp_c: float) -> float:
        """Slowest plausible heating with the heater commanded on (worst case: eclipse, weakest heater),
        from the design model C dT/dt = P_heater + P_int - k (T - T_env); the constant without a model."""
        m = self.thermal
        if not m:
            return self.heater_min_rise_c_s
        p_heat = m["heater_w_min"] if temp_c > m["thermostat_off_c"] else m["thermostat_w"]
        return (p_heat + m["p_internal_w"] - m["conductance_w_per_k"] * (temp_c - m["t_env_c"])) / m[
            "heat_capacity_j_per_k"
        ]

    def max_rise(self, temp_c: float) -> float:
        """Fastest plausible heating with the heater on (full sun, strongest heater): the estimate used
        while temp_c is untrusted, so it errs early. The constant heating_rate_c_s without a model."""
        m = self.thermal
        if not m:
            return self.heating_rate_c_s
        p_in = m["heater_w_max"] + m["p_sun_absorbed_w"] + m["p_internal_w"]
        return (p_in - m["conductance_w_per_k"] * (temp_c - m["t_env_c"])) / m["heat_capacity_j_per_k"]


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
