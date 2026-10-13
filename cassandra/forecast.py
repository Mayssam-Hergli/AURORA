"""Forecast stage: one aurora/v1/core/forecast/{sat} message per satellite per tick.

Model per satellite (MASTER_PLAN D7, interfaces.md 3.5):
- hardware satellites (kind "hw"): the Phase 2 trajectory forecaster, always;
- twin satellites: the learned ensemble (ml.online.OnlineForecaster, Youssef's code, used as is) when
  its models and calibration.json load; otherwise the trajectory forecaster, and a startup event
  says why.

Trajectory forecaster (Phase 2 predictor.py, generalised): for each limited channel (temp_c up to
T_max, soc_pct down to SOC_min, att_err_deg up to theta_max; limits per satellite) take the
least-squares slope over the last `window_s` ticks, t_c = gap / rate, and
P(F < t + D) = clip(1 - t_c / D, 0, 1), maximised over channels; monotone in D by construction.
It predicts neither the next subsystem nor maliciousness, and has no ensemble: next_sub,
p_malicious, p_std and q are null (D4), never invented. When the monitor marks temp_c untrusted,
the temperature is estimated from the last trusted value plus heater on-time x the fastest plausible
heating rate at that temperature (SatProfile.max_rise: design thermal model, or heating_rate_c_s)
(cooling ignored: conservative), and the model name says so ("trajectory-v0+est").
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from aurora_common import schemas as S
from cassandra.monitor import slope  # noqa: F401  (re-exported for callers)
from cassandra.world import Note, SatProfile, WorldTick

HORIZONS = (10, 30, 60, 120)


def theil_sen(t: np.ndarray, x: np.ndarray) -> float:
    """Median of the pairwise slopes: one outlier in the window cannot move the trend."""
    i, j = np.triu_indices(len(t), 1)
    dt = t[j] - t[i]
    keep = dt > 0
    return float(np.median((x[j] - x[i])[keep] / dt[keep])) if keep.any() else 0.0


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Driver:
    """The channel closest to its limit: what explain.py turns into a sentence."""

    channel: str
    value: float
    limit: float
    rate_per_s: float
    eta_s: float  # time to the limit at the current rate (inf if not approaching)


@dataclass
class ForecastResult:
    sat: str
    t: float
    model: str
    p_fail: dict[int, float]
    p_std: dict[int, float] | None = None
    q: float | None = None
    next_sub: str | None = None
    p_malicious: float | None = None
    warming_up: bool = False
    driver: Driver | None = None
    estimated: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)

    def p(self, horizon: int) -> float:
        return self.p_fail[horizon]

    def to_message(self) -> S.Forecast:
        return S.Forecast(
            sat=self.sat,
            t=self.t,
            model=self.model,
            p_fail={str(h): round(min(1.0, max(0.0, self.p_fail[h])), 4) for h in HORIZONS},
            p_std=None if self.p_std is None else {str(h): round(v, 4) for h, v in self.p_std.items()},
            q=None if self.q is None else round(self.q, 4),
            next_sub=self.next_sub,
            p_malicious=None if self.p_malicious is None else round(self.p_malicious, 4),
            warming_up=self.warming_up or None,
        )


# ----------------------------------------------------------------------------- trajectory
class Trajectory:
    """Phase 2 trajectory forecaster for one satellite."""

    name = "trajectory-v0"

    def __init__(self, prof: SatProfile, window: int, min_level: dict[str, float] | None = None):
        self.prof = prof
        self.window = window
        self.min_level = min_level or {}  # channel -> level below which it is not extrapolated
        self.hist: deque = deque(maxlen=window)  # (t, temp_c, soc_pct, att_err_deg)
        self.est_temp: float | None = None  # running estimate while temp_c is untrusted
        self.last_trusted_temp: float | None = None

    def channels(self) -> list[tuple[str, int, float, float]]:
        """(name, column, limit, sign): sign +1 = failure when rising above the limit."""
        p, out = self.prof, []
        if p.t_max_c is not None:
            out.append(("temp_c", 1, p.t_max_c, +1.0))
        if p.soc_min_pct is not None:
            out.append(("soc_pct", 2, p.soc_min_pct, -1.0))
        if p.theta_max_deg is not None:
            out.append(("att_err_deg", 3, p.theta_max_deg, +1.0))
        return out

    def step(self, tick: WorldTick, untrusted: set[str]) -> ForecastResult | None:
        sat = self.prof.sat
        th, eps, adcs = (tick.fresh(sat, s) for s in ("thermal", "eps", "adcs"))
        if th is None and eps is None and adcs is None:
            return None  # nothing fresh: ingest reports the staleness
        temp = None if th is None else float(th["temp_c"])
        estimated: tuple[str, ...] = ()
        if "temp_c" in untrusted and th is not None:
            if self.est_temp is None:
                self.est_temp = self.last_trusted_temp if self.last_trusted_temp is not None else temp
            if int(th["heater_on"]):
                self.est_temp += self.prof.max_rise(
                    self.est_temp
                )  # fastest plausible: the estimate errs early
            temp, estimated = self.est_temp, ("temp_c",)
        else:
            self.est_temp = None
            if temp is not None:
                self.last_trusted_temp = temp
        if self.hist and estimated != self.hist[-1][4]:
            self.hist.clear()  # switching between reported and estimated temperature: restart the slope
        nan = math.nan
        self.hist.append(
            (
                tick.t,
                nan if temp is None else temp,
                nan if eps is None else float(eps["soc_pct"]),
                nan if adcs is None else abs(float(adcs["att_err_deg"])),
                estimated,
            )
        )
        model = self.name + ("+est" if estimated else "")
        if len(self.hist) < self.window:
            return ForecastResult(
                sat, tick.t, model, {h: 0.0 for h in HORIZONS}, warming_up=True, estimated=estimated
            )
        arr = np.array([r[:4] for r in self.hist], dtype=float)
        best_p = {h: 0.0 for h in HORIZONS}
        driver: Driver | None = None
        for name, col, limit, sign in self.channels():
            x = arr[:, col]
            ok = ~np.isnan(x)
            if ok.sum() < 2:
                continue
            # robust to a single bad sample (F2 SEU bit-flip): Theil-Sen trend (median of pairwise
            # slopes) and its robust line evaluated at the last tick as the current level (no lag)
            tt, xx = arr[ok, 0], x[ok]
            b = theil_sen(tt, xx)
            value = float(np.median(xx - b * tt) + b * tt[-1])
            rate = b * sign  # > 0: heading for the limit
            gap = (limit - value) * sign
            if gap <= 0:
                eta = 0.0
            elif sign > 0 and value < self.min_level.get(name, -math.inf):
                eta = math.inf  # inside the benign envelope: do not extrapolate (config min_level)
            elif rate > 1e-6:
                eta = gap / rate
            else:
                eta = math.inf
            p = {h: float(np.clip(1.0 - eta / h, 0.0, 1.0)) for h in HORIZONS}
            if driver is None or eta < driver.eta_s:
                driver = Driver(name, value, limit, rate * sign, eta)
            best_p = {h: max(best_p[h], p[h]) for h in HORIZONS}
        return ForecastResult(sat, tick.t, model, best_p, driver=driver, estimated=estimated)


# ----------------------------------------------------------------------------- learned ensemble
class ModelUnavailable(Exception):
    """The ensemble cannot run; the message says why (shown in a startup core/event)."""


class EnsembleAdapter:
    """Feeds WorldTicks to ml.online.OnlineForecaster and converts its output to ForecastResults."""

    def __init__(self, online, n_models: int, calibrated: bool, window: int):
        self.online = online
        self.n_models = n_models
        self.window = window
        self.model = "tgn-v1" if calibrated else "tgn-v1-uncalibrated"

    @classmethod
    def load(cls, cfg: dict, sats: list[str]) -> EnsembleAdapter:
        ec = cfg["forecast"]["ensemble"]
        if not ec.get("enabled", True):
            raise ModelUnavailable("ensemble disabled in cassandra/config.yaml")
        models_dir = (ROOT / ec["models_dir"]).resolve()
        names = sorted(p.stem for p in models_dir.glob("full_s*.pt"))
        if not names:
            raise ModelUnavailable(f"no trained models (full_s*.pt) in {models_dir}")
        cal_path = models_dir / "calibration.json"
        calibrated = cal_path.exists()
        if not calibrated and not ec.get("allow_uncalibrated", False):
            raise ModelUnavailable(f"models found but no {cal_path.name} (fix M5); not running uncalibrated")
        try:
            import json

            from ml import config as ml_config
            from ml.model import HORIZONS as ML_HORIZONS
            from ml.online import OnlineForecaster
            from ml.train import load_model

            if tuple(ML_HORIZONS) != HORIZONS:
                raise ModelUnavailable(f"model horizons {ML_HORIZONS} differ from the contract {HORIZONS}")
            ml_config.MODELS = models_dir
            models = [load_model(n) for n in names]
            temp = json.loads(cal_path.read_text())["temperature"] if calibrated else 1.0
            window = ml_config.load("model")["window_s"]
        except ModelUnavailable:
            raise
        except Exception as exc:  # a broken model file must not take the core down
            raise ModelUnavailable(f"loading the ensemble failed: {type(exc).__name__}: {exc}") from exc
        return cls(OnlineForecaster(models, sats, temp, window), len(models), calibrated, window)

    def step(self, tick: WorldTick, decision_horizon: int) -> dict[str, ForecastResult]:
        fb = self.online.frames
        for sat, subs in tick.values.items():
            if sat not in fb.idx:
                continue
            for sub, fields in subs.items():
                fb.on_telemetry({"sat": sat, "sub": sub, "fields": fields})
        for tc in tick.tcs:
            if tc.sat in fb.idx:
                fb.on_command({"sat": tc.sat, "origin": tc.origin, "cmd": tc.cmd, "args": dict(tc.args)})
        for (a, b), lk in tick.links.items():
            fb.on_link({"a": a, "b": b, "enabled": lk["enabled"]})
        msgs = self.online.tick(tick.t)
        warming = len(self.online.buffer) < self.window
        out = {}
        for m in msgs:
            p_std = {int(k): float(v) for k, v in m["p_std"].items()} if self.n_models >= 2 else None
            out[m["sat"]] = ForecastResult(
                sat=m["sat"],
                t=tick.t,
                model=self.model,
                p_fail={int(k): float(v) for k, v in m["p_fail"].items()},
                p_std=p_std,
                q=None if p_std is None else p_std[decision_horizon],  # D4: no Q with one model
                next_sub=m["next_sub"],
                p_malicious=float(m["p_malicious"]),
                warming_up=warming,
            )
        return out


# ----------------------------------------------------------------------------- stage
class ForecastStage:
    def __init__(self, profiles: dict[str, SatProfile], cfg: dict, ensemble: EnsembleAdapter | None = None):
        fc = cfg["forecast"]
        self.profiles = profiles
        self.decision_horizon = int(fc["decision_horizon_s"])
        self.traj = {
            s: Trajectory(p, p.window_s or int(fc["window_s"]), fc.get("min_level"))
            for s, p in profiles.items()
        }
        self.ensemble = ensemble
        self.startup_notes: list[Note] = []

    @classmethod
    def build(cls, profiles: dict[str, SatProfile], cfg: dict) -> ForecastStage:
        twins = [s for s, p in profiles.items() if p.kind == "twin"]
        hw = [s for s, p in profiles.items() if p.kind == "hw"]
        ensemble, why = None, ""
        if twins:
            try:
                ensemble = EnsembleAdapter.load(cfg, list(profiles))
            except ModelUnavailable as exc:
                why = str(exc)
        stage = cls(profiles, cfg, ensemble)
        if ensemble is not None:
            q = "available" if ensemble.n_models >= 2 else "inactive (single model)"
            text = (
                f"forecaster: {ensemble.model} ({ensemble.n_models} models) for {','.join(twins)}; "
                f"uncertainty gate {q}"
            )
        else:
            text = "forecaster: trajectory-v0 for all satellites" + (
                f" (ensemble unavailable: {why})" if why else ""
            )
        if hw:
            text += f"; trajectory-v0 for hardware {','.join(hw)} (D7)"
        stage.startup_notes.append(Note("startup", "info", text, data={"ensemble_error": why or None}))
        return stage

    def model_for(self, sat: str) -> str:
        if self.profiles[sat].kind == "hw" or self.ensemble is None:
            return "trajectory"
        return "ensemble"

    def step(self, tick: WorldTick, untrusted: dict[str, set[str]]) -> dict[str, ForecastResult]:
        traj = {}
        for sat, tr in self.traj.items():
            r = tr.step(tick, untrusted.get(sat, set()))  # always run: it is also the fallback and explains
            if r is not None:
                traj[sat] = r
        if self.ensemble is None:
            return traj
        ens = self.ensemble.step(tick, self.decision_horizon)
        out = {}
        for sat in self.profiles:
            use_traj = self.model_for(sat) == "trajectory" or untrusted.get(sat) or sat not in ens
            r = traj.get(sat) if use_traj else ens[sat]
            if r is None:
                continue
            if not use_traj and sat in traj:
                r.driver = traj[sat].driver  # explain still needs the physical driver
            out[sat] = r
        return out
