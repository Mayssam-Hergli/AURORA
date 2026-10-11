"""Simulate stage: score candidate responses with J(a) on a shadow model (CONTEXT Section 2, 5.4).

    J(a) = w_r R(a) + w_m (1 - U(a)) + w_c C(a) + w_q Q

R: highest P(F < t + decision horizon) over the constellation after a `rollout_s` rollout with
action a applied (1 if a limit is crossed during the rollout). U: fraction of satellites still in
service. C: operational cost of a (config). Q: the forecast's q; when q is null (single model, D4)
the Q term is left out, never replaced by 0 as if the model were certain.

Shadow model (fixes review finding M2). Twin satellites: a fresh SyntheticTwin seeded from the latest
*observations* and with an *empty* command queue. The real attacker's future commands are never
copied. Threat assumption: the last observed command effects persist (a latched heater stays on).
Hardware satellites: the ESP8266 firmware's own thermal model (+1.5 C/s heating, 3 % per 100 ms
cooling towards 25 C).

Engine hooks. The shadow needs SyntheticTwin.snapshot(include_queue=False) and
SyntheticTwin.seed_from_observation(obs). They belong to ml/synthetic/engine.py (Youssef). Until they
exist, EngineAdapter provides stand-ins with the same signatures, and switches to the real methods as
soon as the engine has them, with no change to the core.

Closed-loop numbers from this sandbox are an upper bound: it uses the simulator as its model.
"""

from __future__ import annotations

import copy
import inspect
import math
from dataclasses import dataclass, field

import numpy as np

from cassandra.monitor import slope
from cassandra.world import SatProfile, WorldTick

ACTIONS = ("none", "safe_mode", "block_source", "isolate_subsystem", "quarantine")


@dataclass
class Candidate:
    action: str
    target: str
    args: dict = field(default_factory=dict)

    def commands(self, peers: list[str]) -> list[tuple[str, str, dict]]:
        """(sat, cmd, args) telecommands that carry out this action (origin 'core')."""
        x = self.target
        if self.action == "safe_mode":
            return [(x, "SAFE_MODE", {})]
        if self.action == "isolate_subsystem":
            return [(x, "HEATER", {"on": False})]  # cut the heater: the subsystem driving the hazard
        if self.action == "block_source":
            peer = self.args["origin"].split(":", 1)[1]
            return [
                (x, "ISL_ENABLE", {"peer": peer, "on": False}),
                (peer, "ISL_ENABLE", {"peer": x, "on": False}),
            ]
        if self.action == "quarantine":
            cmds = [(x, "SAFE_MODE", {})]
            for p in peers:
                cmds += [
                    (x, "ISL_ENABLE", {"peer": p, "on": False}),
                    (p, "ISL_ENABLE", {"peer": x, "on": False}),
                ]
            return cmds
        return []


@dataclass
class Score:
    action: str
    target: str
    args: dict
    R: float
    U: float
    C: float
    Q: float | None
    J: float
    crossed: bool = False
    detail: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------- engine hooks
class EngineAdapter:
    """snapshot(include_queue=False) and seed_from_observation(obs) on a SyntheticTwin, using the
    engine's own methods when present (Youssef, Phase 2) and the stand-ins below otherwise.

    obs = {"t": float,
           "sats": {sat: {"temp_c", "heater_on", "soc_pct", "mode", "eclipse", "ground_contact",
                          "temp_rate_c_s"(optional)}},
           "links": {"A-B": {"feasible": bool, "enabled": bool}}}
    """

    def __init__(self, seed: int = 0, duration_s: int = 120):
        self.base = _clean_twin(seed, max(120, duration_s))
        snap = getattr(type(self.base), "snapshot", None)
        self.native = hasattr(type(self.base), "seed_from_observation") and (
            snap is not None and "include_queue" in inspect.signature(snap).parameters
        )

    def shadow(self, obs: dict):
        """A fresh shadow twin seeded from observations, with no queued commands."""
        tw = copy.deepcopy(self.base)
        if self.native:
            tw.seed_from_observation(obs)
            tw.restore(tw.snapshot(include_queue=False))
        else:
            _seed_from_observation(tw, obs)
            _drop_queue(tw)
        return tw

    @property
    def kind(self) -> str:
        return "engine" if self.native else "stand-in"


def _clean_twin(seed: int, duration_s: int):
    """A SyntheticTwin on a clean scenario (no attack planned inside the engine)."""
    from ml import config as ml_config
    from ml.synthetic.engine import SyntheticTwin
    from ml.synthetic.scenarios import sample_spec

    cfg = copy.deepcopy(ml_config.load("synthetic"))
    cfg["episode_s"] = duration_s
    spec = sample_spec(cfg, "train", seed, np.random.default_rng(seed))
    spec.type, spec.target, spec.onset_s, spec.channel, spec.n_victims = "N", None, None, None, 0
    spec.episode_id = f"shadow-{seed}"
    return SyntheticTwin(cfg, spec)


def _drop_queue(tw) -> None:
    """Stand-in for snapshot(include_queue=False): no planned or attacker command survives."""
    tw.queue = []
    tw.commands = []


def _seed_from_observation(tw, obs: dict) -> None:
    """Stand-in for SyntheticTwin.seed_from_observation: set the physical and OBC state from what the
    core observed. Geometry over the rollout is held at the observed eclipse, contact and link
    feasibility (persistence over 30-60 s)."""
    from ml.synthetic.engine import MODE_NOMINAL, MODE_SAFE

    th = tw.cfg["thermal"]
    sats = obs["sats"]
    for s, sat in enumerate(tw.sats):
        o = sats.get(sat)
        if not o:
            continue
        if o.get("temp_c") is not None:
            tw.temp[s] = o["temp_c"]
        if o.get("soc_pct") is not None:
            tw.soc[s] = o["soc_pct"] / 100.0
        on = bool(o.get("heater_on", 0))
        tw.heater_cmd[s] = on
        tw.heater_attack[s] = -1  # full power while latched (drive shape unknown to the defender)
        tw.heater_stuck[s] = False
        tw.science[s] = False
        tw.thermostat[s] = False
        tw.mode[s] = MODE_SAFE if o.get("mode") in ("SAFE", "QUARANTINED") else MODE_NOMINAL
        rate = o.get("temp_rate_c_s")
        if on and rate is not None and o.get("temp_c") is not None:
            # heater power that explains the observed heating rate (thermal model of interfaces 5.3)
            ecl = float(o.get("eclipse", 0))
            p = (
                th["heat_capacity_j_per_k"] * rate
                - th["p_sun_absorbed_w"] * (1 - ecl)
                - th["p_internal_w"]
                + th["conductance_w_per_k"] * (o["temp_c"] - th["t_env_c"])
            )
            tw.heater_power[s] = float(np.clip(p, 0.0, 200.0))
        if "eclipse" in o:
            tw.geo.eclipse[:, s] = bool(o["eclipse"])
        if "ground_contact" in o:
            tw.geo.contact[:, s] = bool(o["ground_contact"])
    for key, lk in obs.get("links", {}).items():
        a, b = key.split("-")
        i, j = tw.idx[a], tw.idx[b]
        tw.geo.feasible[:, i, j] = tw.geo.feasible[:, j, i] = bool(lk["feasible"])
        tw.isl_enabled[i, j] = tw.isl_enabled[j, i] = bool(lk["enabled"])
    tw.t = 0


# ----------------------------------------------------------------------------- sandbox
class Sandbox:
    def __init__(self, profiles: dict[str, SatProfile], cfg: dict):
        sb = cfg["sandbox"]
        self.profiles = profiles
        self.cfg = sb
        self.rollout = int(sb["rollout_s"])
        self.horizon = int(cfg["forecast"]["decision_horizon_s"])
        self.window = int(cfg["forecast"]["window_s"])
        self.w = sb["weights"]
        self.cost = sb["cost"]
        self.allowed = tuple(a for a in sb["candidates"] if a in ACTIONS)
        self.engine = EngineAdapter(int(sb.get("shadow_seed", 0)), self.rollout + 10)
        self.board = sb["board_model"]

    # ------------------------------------------------------------------ observation
    @staticmethod
    def observation(tick: WorldTick, temps: dict[str, float], rates: dict[str, float]) -> dict:
        """What the shadow is seeded with. temps/rates may replace reported values (untrusted temp_c)."""
        sats = {}
        for sat, subs in tick.values.items():
            th, eps, obc = subs.get("thermal"), subs.get("eps"), subs.get("obc")
            sun, comms = subs.get("sun"), subs.get("comms")
            sats[sat] = {
                "temp_c": temps.get(sat, th["temp_c"] if th else None),
                "heater_on": th["heater_on"] if th else 0,
                "soc_pct": eps["soc_pct"] if eps else None,
                "mode": obc["mode"] if obc else "NOMINAL",
                "temp_rate_c_s": rates.get(sat),
                **({"eclipse": sun["eclipse"]} if sun else {}),
                **({"ground_contact": comms["ground_contact"]} if comms else {}),
            }
        links = {
            f"{a}-{b}": {"feasible": v["feasible"], "enabled": v["enabled"]}
            for (a, b), v in tick.links.items()
        }
        return {"t": tick.t, "sats": sats, "links": links}

    # ------------------------------------------------------------------ planning
    def candidates(self, target: str, suspicious_origins: list[str], budget_ok: bool) -> list[Candidate]:
        out = []
        for a in self.allowed:
            if a == "quarantine" and not budget_ok:
                continue
            if a == "block_source":
                # the contract can only block a relaying neighbour (ISL_ENABLE); ground cannot be blocked
                out += [
                    Candidate(a, target, {"origin": o}) for o in suspicious_origins if o.startswith("isl:")
                ]
                continue
            out.append(Candidate(a, target))
        return out

    def plan(
        self,
        tick: WorldTick,
        target: str,
        q: float | None,
        temps: dict[str, float],
        rates: dict[str, float],
        suspicious_origins: list[str],
        budget_ok: bool,
    ) -> list[Score]:
        """Every candidate scored, best (lowest J) first."""
        cands = self.candidates(target, suspicious_origins, budget_ok)
        prof = self.profiles[target]
        if prof.kind == "hw":
            return sorted((self._score_board(c, tick, temps, q) for c in cands), key=lambda s: s.J)
        obs = self.observation(tick, temps, rates)
        base = self.engine.shadow(obs)
        hw_out = [s for s, p in self.profiles.items() if p.kind == "hw"]
        return sorted((self._score_twin(c, base, q, hw_out) for c in cands), key=lambda s: s.J)

    def _j(self, c: Candidate, R: float, U: float, q: float | None) -> float:
        J = self.w["w_r"] * R + self.w["w_m"] * (1 - U) + self.w["w_c"] * self.cost[c.action]
        return J + (self.w["w_q"] * q if q is not None else 0.0)

    def _score_twin(self, c: Candidate, base, q: float | None, ignore: list[str]) -> Score:
        from ml.synthetic.engine import MODE_NOMINAL

        tw = copy.deepcopy(base)
        peers = [s for s in tw.sats if s != c.target]
        for sat, cmd, args in c.commands(peers):
            tw.apply({"sat": sat, "origin": "core", "cmd": cmd, "args": args})
        S = tw.S
        temp = np.zeros((self.rollout, S))
        soc = np.zeros((self.rollout, S))
        crossed = np.zeros(S, bool)
        for k in range(self.rollout):
            tw.step(1)
            temp[k], soc[k] = tw.temp, tw.soc * 100
            for s, sat in enumerate(tw.sats):
                p = self.profiles.get(sat)
                if p is None:
                    continue
                if (p.t_max_c is not None and tw.temp[s] > p.t_max_c) or (
                    p.soc_min_pct is not None and tw.soc[s] * 100 < p.soc_min_pct
                ):
                    crossed[s] = True
        risk = {}
        for s, sat in enumerate(tw.sats):
            p = self.profiles.get(sat)
            if p is None or sat in ignore:
                continue
            risk[sat] = 1.0 if crossed[s] else self._risk(p, temp[:, s], soc[:, s])
        R = max(risk.values()) if risk else 0.0
        in_service = [tw.mode[tw.idx[s]] == MODE_NOMINAL for s in tw.sats if s in self.profiles]
        U = float(np.mean(in_service)) if in_service else 1.0
        worst = max(risk, key=risk.get) if risk else None
        return Score(
            c.action,
            c.target,
            c.args,
            R,
            U,
            self.cost[c.action],
            q,
            self._j(c, R, U, q),
            bool(crossed.any()),
            {
                "worst_sat": worst,
                "target_temp_end_c": round(float(temp[-1, tw.idx[c.target]]), 2),
                "target_risk": round(risk.get(c.target, 0.0), 3),
            },
        )

    def _risk(self, p: SatProfile, temp: np.ndarray, soc: np.ndarray) -> float:
        """P(F < t + horizon) a trajectory forecaster would see at the end of the rollout."""
        t = np.arange(len(temp), dtype=float)[-self.window :]
        best = 0.0
        for x, limit, sign in ((temp, p.t_max_c, 1.0), (soc, p.soc_min_pct, -1.0)):
            if limit is None:
                continue
            x = x[-self.window :]
            gap = (limit - x[-1]) * sign
            rate = slope(t, x) * sign
            eta = 0.0 if gap <= 0 else (gap / rate if rate > 1e-6 else math.inf)
            best = max(best, float(np.clip(1 - eta / self.horizon, 0, 1)))
        return best

    def _score_board(self, c: Candidate, tick: WorldTick, temps: dict[str, float], q: float | None) -> Score:
        """ESP8266 board: the firmware's thermal model, one step per second."""
        b, p = self.board, self.profiles[c.target]
        th = tick.fresh(c.target, "thermal") or {
            "temp_c": temps.get(c.target, b["ambient_c"]),
            "heater_on": 0,
        }
        T = temps.get(c.target, th["temp_c"])
        heater = bool(th["heater_on"]) and c.action not in ("safe_mode", "quarantine", "isolate_subsystem")
        series, crossed = [], False
        for _ in range(self.rollout):
            T = T + b["heat_c_s"] if heater else T + (b["ambient_c"] - T) * b["cool_per_s"]
            T = min(T, b["ceiling_c"])
            series.append(T)
            crossed |= p.t_max_c is not None and T > p.t_max_c
        R = 1.0 if crossed else self._risk(p, np.array(series), np.full(len(series), 100.0))
        n = len(self.profiles)
        U = 1.0 - (1.0 / n if c.action in ("safe_mode", "quarantine") else 0.0)
        return Score(
            c.action,
            c.target,
            c.args,
            R,
            U,
            self.cost[c.action],
            q,
            self._j(c, R, U, q),
            crossed,
            {"worst_sat": c.target, "target_temp_end_c": round(series[-1], 2), "target_risk": round(R, 3)},
        )
