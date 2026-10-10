"""Constellation geometry (docs/CONTEXT.md Section 5.3).

12 satellites A-L on circular orbits at 550 km (period ~95.6 min), 3 planes x 4 satellites
(Walker-like phasing), a ground station at Tunis (36.8 N, 10.2 E, minimum elevation 10 deg),
cylindrical Earth shadow, and inter-satellite links that exist iff the segment between the two
satellites clears the Earth radius + 100 km and the range is <= R_comm (default 5 000 km).

Geometry may run time-compressed (`orbit_speedup`); subsystem physics always runs at 1 s steps.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MU_KM3_S2 = 398600.4418
R_EARTH_KM = 6371.0
OMEGA_EARTH = 7.2921159e-5  # rad/s


@dataclass
class Geometry:
    pos: np.ndarray  # [T, S, 3] km, inertial frame
    eclipse: np.ndarray  # [T, S] bool
    contact: np.ndarray  # [T, S] bool, ground contact (elevation > min)
    elevation_deg: np.ndarray  # [T, S]
    feasible: np.ndarray  # [T, S, S] bool, ISL line of sight and range
    range_km: np.ndarray  # [T, S, S]


def orbital_period_s(altitude_km: float) -> float:
    a = R_EARTH_KM + altitude_km
    return 2 * np.pi * np.sqrt(a**3 / MU_KM3_S2)


def compute(cfg: dict, n_ticks: int, t0_s: float, dt_s: float = 1.0) -> Geometry:
    """Geometry for n_ticks starting at orbit time t0_s (seconds since the epoch of the plane set)."""
    o = cfg["orbit"]
    S = o["planes"] * o["per_plane"]
    a = R_EARTH_KM + o["altitude_km"]
    n = np.sqrt(MU_KM3_S2 / a**3)
    speed = o.get("orbit_speedup", 1.0)
    t = speed * (t0_s + np.arange(n_ticks) * dt_s)  # geometry time
    inc = np.deg2rad(o["inclination_deg"])
    p_idx = np.repeat(np.arange(o["planes"]), o["per_plane"])
    k_idx = np.tile(np.arange(o["per_plane"]), o["planes"])
    raan = np.deg2rad(360.0 / o["planes"]) * p_idx
    u0 = np.deg2rad(360.0 / o["per_plane"] * k_idx + o["phasing_deg"] * p_idx)
    u = u0[None, :] + n * t[:, None]
    x_o, y_o = a * np.cos(u), a * np.sin(u)
    cO, sO, ci, si = np.cos(raan), np.sin(raan), np.cos(inc), np.sin(inc)
    pos = np.stack([x_o * cO - y_o * ci * sO, x_o * sO + y_o * ci * cO, y_o * si], -1)

    sun = np.asarray(cfg.get("sun_direction", [1.0, 0.0, 0.25]), float)
    sun /= np.linalg.norm(sun)
    along = pos @ sun
    perp = np.linalg.norm(pos - along[..., None] * sun, axis=-1)
    eclipse = (along < 0) & (perp < R_EARTH_KM)

    gs = cfg["ground_station"]
    lat, lon = np.deg2rad(gs["lat_deg"]), np.deg2rad(gs["lon_deg"])
    theta = lon + OMEGA_EARTH * t  # Earth rotation (same speedup)
    r_gs = R_EARTH_KM * np.stack(
        [np.cos(lat) * np.cos(theta), np.cos(lat) * np.sin(theta), np.full_like(theta, np.sin(lat))], -1
    )  # [T, 3]
    up = r_gs / np.linalg.norm(r_gs, axis=-1, keepdims=True)
    d = pos - r_gs[:, None, :]
    elev = np.rad2deg(np.arcsin(np.clip((d * up[:, None, :]).sum(-1) / np.linalg.norm(d, axis=-1), -1, 1)))
    contact = elev > gs["min_elevation_deg"]

    diff = pos[:, None, :, :] - pos[:, :, None, :]  # [T, S, S, 3]
    rng = np.linalg.norm(diff, axis=-1)
    p1 = np.broadcast_to(pos[:, :, None, :], diff.shape)
    tt = np.clip(-(p1 * diff).sum(-1) / np.maximum((diff * diff).sum(-1), 1e-9), 0, 1)
    closest = np.linalg.norm(p1 + tt[..., None] * diff, axis=-1)
    los = closest > R_EARTH_KM + o["los_margin_km"]
    feasible = los & (rng <= o["r_comm_km"])
    idx = np.arange(S)
    feasible[:, idx, idx] = False
    return Geometry(
        pos=pos, eclipse=eclipse, contact=contact, elevation_deg=elev, feasible=feasible, range_km=rng
    )
