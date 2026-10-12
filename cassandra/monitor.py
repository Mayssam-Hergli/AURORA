"""Cross-channel consistency (Scenario B, telemetry masking): is what a satellite reports physically
possible given its other channels?

Rule `heater_inconsistent`: heater_on = 1 for the whole window, yet the reported temp_c does not rise
(least-squares slope below the satellite's heater_min_rise_c_s). A frozen or replayed temperature
under a running heater looks exactly like this. Works with noisy and exact freezes alike.
Rule `frozen`: temp_c identical over the window's messages (distinct message times, not ticks).
A real, noisy sensor never does that.

A hit marks temp_c untrusted for that satellite; forecast.py then estimates the temperature from the
heater's on-time instead of the reported value. The channel is trusted again after `recover_s`
consecutive consistent ticks. The monitor sees only telemetry, never ground truth.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from cassandra.world import SatProfile, WorldTick


@dataclass(frozen=True)
class MonitorEvent:
    sat: str
    kind: str  # "hit" | "clear"
    rule: str  # "heater_inconsistent" | "frozen" | "recovered"
    fields: tuple[str, ...]
    text: str
    sparta: tuple[str, ...] = ()
    data: dict = field(default_factory=dict)


def slope(t: np.ndarray, x: np.ndarray) -> float:
    tc = t - t.mean()
    den = float((tc * tc).sum())
    return float((tc * (x - x.mean())).sum() / den) if den > 0 else 0.0


class Monitor:
    def __init__(self, profiles: dict[str, SatProfile], cfg: dict):
        c = cfg["monitor"]
        self.profiles = profiles
        self.window = int(c["window_s"])
        self.freeze_eps = float(c["freeze_eps_c"])
        self.recover = int(c["recover_s"])
        self.hist: dict[str, deque] = {s: deque(maxlen=self.window) for s in profiles}
        self.untrusted: dict[str, set[str]] = {s: set() for s in profiles}
        self.cross_active: set[tuple[str, str]] = set()
        self.active_rule: dict[str, str] = {}
        self.consistent_run: dict[str, int] = {s: 0 for s in profiles}

    def step(self, tick: WorldTick) -> list[MonitorEvent]:
        events: list[MonitorEvent] = self._cross_checks(tick)
        for sat, prof in self.profiles.items():
            th = tick.fresh(sat, "thermal")
            h = self.hist[sat]
            if th is None:
                h.clear()  # a gap breaks the window; stale data is ingest's business
                continue
            # The window holds *messages*, not ticks: when the core catches up on several ticks at once,
            # or a value is held between messages, the same message would otherwise repeat and look
            # like a frozen sensor (seen when a 20x plant outran the core: all 12 satellites "frozen").
            msg_t = tick.t + 1.0 - tick.age_s.get(sat, {}).get("thermal", 0.0)
            if h and abs(msg_t - h[-1][0]) < 1e-6:
                continue
            if h and msg_t - h[-1][0] > 1.5:
                h.clear()
            h.append((msg_t, float(th["temp_c"]), int(th["heater_on"])))
            if len(h) < self.window:
                continue
            rule, data = self._check(prof, h)
            if rule:
                self.consistent_run[sat] = 0
                if "temp_c" not in self.untrusted[sat]:
                    self.untrusted[sat].add("temp_c")
                    self.active_rule[sat] = rule
                    events.append(self._hit(sat, rule, data))
            elif "temp_c" in self.untrusted[sat]:
                self.consistent_run[sat] += 1
                if self.consistent_run[sat] >= self.recover:
                    self.untrusted[sat].discard("temp_c")
                    self.active_rule.pop(sat, None)
                    events.append(
                        MonitorEvent(
                            sat,
                            "clear",
                            "recovered",
                            ("temp_c",),
                            f"[{sat}] temp_c consistent with the heater again for {self.recover} s: trusted",
                        )
                    )
        return events

    def _cross_checks(self, tick: WorldTick) -> list[MonitorEvent]:
        """Independent consistency evidence, not proof of malicious intent."""
        events = []
        for sat in self.profiles:
            eps = tick.fresh(sat, "eps") or {}
            adcs = tick.fresh(sat, "adcs") or {}
            checks = {}
            if all(k in eps for k in ("bus_v", "current_ma", "load_w")):
                measured = eps["bus_v"] * eps["current_ma"] / 1000
                checks["eps_power_balance"] = (
                    abs(measured - eps["load_w"]) > max(5.0, 0.25 * abs(eps["load_w"])),
                    ("bus_v", "current_ma", "load_w"),
                )
            if "wheel_rpm" in adcs:
                wheels = np.asarray(adcs["wheel_rpm"], float)
                checks["adcs_sensor_range"] = (
                    not np.isfinite(wheels).all() or (abs(wheels) > 100_000).any(),
                    ("wheel_rpm",),
                )
            for rule, (hit, fields) in checks.items():
                key = (sat, rule)
                if hit != (key in self.cross_active):
                    if hit:
                        self.cross_active.add(key)
                    else:
                        self.cross_active.discard(key)
                    events.append(
                        MonitorEvent(
                            sat,
                            "hit" if hit else "clear",
                            rule,
                            fields,
                            f"[{sat}] {rule}: {'inconsistent' if hit else 'consistent again'}; "
                            "sensor/model evidence requires corroboration",
                            data={"assurance": "LOW_ASSURANCE", "malicious": None},
                        )
                    )
        return events

    def _check(self, prof: SatProfile, h: deque) -> tuple[str | None, dict]:
        arr = np.array(h, dtype=float)
        t, temp, heater = arr[:, 0], arr[:, 1], arr[:, 2]
        rate = slope(t, temp)
        if heater.min() >= 1 and rate < prof.heater_min_rise_c_s:
            return "heater_inconsistent", {
                "heater_on_s": len(h),
                "temp_rate_c_s": round(rate, 4),
                "min_rise_c_s": prof.heater_min_rise_c_s,
            }
        if float(temp.max() - temp.min()) <= self.freeze_eps:
            return "frozen", {"value_c": float(temp[-1]), "window_s": len(h)}
        return None, {}

    def _hit(self, sat: str, rule: str, data: dict) -> MonitorEvent:
        if rule == "heater_inconsistent":
            text = (
                f"[{sat}] heater ON for {data['heater_on_s']} s but reported temp_c rises at "
                f"{data['temp_rate_c_s']:+.3f} C/s (expected >= {data['min_rise_c_s']}): "
                "temperature telemetry inconsistent, possibly frozen or replayed; "
                "treating temp_c as untrusted"
            )
            sparta = ("DE-0002", "EX-0001")
        else:
            text = (
                f"[{sat}] temp_c frozen at {data['value_c']:.2f} C for {data['window_s']} s "
                "(a live sensor is never constant): treating temp_c as untrusted"
            )
            sparta = ("DE-0002",)
        return MonitorEvent(sat, "hit", rule, ("temp_c",), text, sparta, data)
