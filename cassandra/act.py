"""Act stage (D11: safety, executor and verify in one file) and the per-satellite state machine.

States (interfaces.md 3.3): nominal -> flagged -> quarantined | safe_hold -> nominal.
P below is P(F < t + horizon_s), horizon_s = 60 by default (config explains why not 30).
- nominal -> flagged: P >= warn_p (the ensemble uses tau from calibration.json), or a monitor hit.
- flagged -> plan: P >= plan_p, re-planned at most every replan_s. The sandbox ranks the candidates
  by J(a); "none" is a candidate, so J decides whether acting is worth it.
  Safety then applies the uncertainty gate (q > theta: safe hold + alert ground instead of
  isolating; q null: gate inactive, D4) and the isolation budget (quarantine offered only while
  quarantines in the current orbit window stay within 25 % of the constellation).
- quarantine -> state quarantined; safe_mode -> safe_hold; isolate_subsystem / block_source ->
  the satellite stays in service, flagged, until the verifier clears it.
- Verifier: heal when temp_c is trusted and below heal_below_c and P < calm_p for calm_ticks
  consecutive ticks: SET_MODE NOMINAL, re-enable the links the core cut, state nominal.
- A flagged satellite on which nothing was done returns to nominal after unflag_ticks calm ticks.
- Every state is held at least min_dwell_s (no flapping).

The executor only publishes contract messages with origin "core" (interfaces.md 3.2). It never
reads ground truth.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field

from aurora_common import schemas as S
from cassandra import explain
from cassandra.forecast import ForecastResult
from cassandra.monitor import MonitorEvent
from cassandra.sandbox import Candidate, Sandbox, Score
from cassandra.world import SatProfile, WorldTick


@dataclass
class Outbox:
    """Everything the core publishes after one tick, in order."""

    tcs: list[S.Telecommand] = field(default_factory=list)
    states: list[S.NodeStateMsg] = field(default_factory=list)
    actions: list[S.Action] = field(default_factory=list)
    events: list[S.Event] = field(default_factory=list)
    plan_ms: list[float] = field(default_factory=list)


# ----------------------------------------------------------------------------- safety
class Safety:
    def __init__(self, cfg: dict, n_sats: int):
        a = cfg["act"]
        self.theta = float(a["theta_uncert"])
        self.max_isolated = max(1, math.floor(float(a["isolation_budget"]) * n_sats))
        self.window_s = float(a["orbit_period_s"]) / float(a["orbit_speedup"])
        self.quarantines: deque[float] = deque()

    def gate(self, q: float | None) -> bool | None:
        """True: too uncertain to isolate autonomously. None: Q unavailable, gate inactive (D4)."""
        return None if q is None else q > self.theta

    def budget_ok(self, t: float) -> bool:
        while self.quarantines and t - self.quarantines[0] >= self.window_s:
            self.quarantines.popleft()
        return len(self.quarantines) + 1 <= self.max_isolated

    def record_quarantine(self, t: float) -> None:
        self.quarantines.append(t)


# ----------------------------------------------------------------------------- executor
class Executor:
    def __init__(self):
        self.cut: dict[str, list[tuple[str, str]]] = defaultdict(list)  # sat -> links the core disabled

    @staticmethod
    def tc(t: float, sat: str, cmd: str, args: dict) -> S.Telecommand:
        problems = S.args_problems(cmd, args)
        if problems:  # the core must never emit a malformed command
            raise ValueError(f"invalid {cmd} {args}: {problems}")
        return S.Telecommand(id=uuid.uuid4().hex, t=t, sat=sat, origin="core", cmd=cmd, args=args)

    def carry_out(self, t: float, cand: Candidate, peers: list[str], links: dict) -> list[S.Telecommand]:
        out = [self.tc(t, sat, cmd, args) for sat, cmd, args in cand.commands(peers)]
        if cand.action == "quarantine":
            x = cand.target
            self.cut[x] = [tuple(sorted((x, p))) for p in peers]
            # reroute (CM0070): other links stay as they are; nothing is enabled unless feasible
            for (a, b), lk in links.items():
                if x in (a, b) or lk["enabled"] or not lk["feasible"]:
                    continue
                if any((a, b) in v for v in self.cut.values()):
                    continue  # cut on purpose for another quarantined satellite
                out += [
                    self.tc(t, a, "ISL_ENABLE", {"peer": b, "on": True}),
                    self.tc(t, b, "ISL_ENABLE", {"peer": a, "on": True}),
                ]
        elif cand.action == "block_source":
            peer = cand.args["origin"].split(":", 1)[1]
            self.cut[cand.target].append(tuple(sorted((cand.target, peer))))
        return out

    def restore(self, t: float, sat: str, links: dict, exit_safe: bool) -> list[S.Telecommand]:
        out = [self.tc(t, sat, "SET_MODE", {"mode": "NOMINAL"})] if exit_safe else []
        for a, b in self.cut.pop(sat, []):
            if any((a, b) in v for v in self.cut.values()):
                continue  # the other end is still quarantined
            out += [
                self.tc(t, a, "ISL_ENABLE", {"peer": b, "on": True}),
                self.tc(t, b, "ISL_ENABLE", {"peer": a, "on": True}),
            ]
        return out


# ----------------------------------------------------------------------------- verifier
class Verifier:
    def __init__(self, cfg: dict):
        a = cfg["act"]
        self.calm_p = float(a["calm_p"])
        self.calm_ticks = int(a["calm_ticks"])
        self.horizon = int(a["horizon_s"])
        self.run: dict[str, int] = defaultdict(int)

    def update(
        self, sat: str, prof: SatProfile, tick: WorldTick, f: ForecastResult | None, untrusted: bool
    ) -> bool:
        """True once the satellite has been calm for calm_ticks consecutive ticks."""
        th = tick.fresh(sat, "thermal")
        calm = (
            f is not None
            and not f.warming_up
            and f.p(self.horizon) < self.calm_p
            and not untrusted
            and (th is None or prof.t_max_c is None or th["temp_c"] <= prof.heal_below_c)
        )
        self.run[sat] = self.run[sat] + 1 if calm else 0
        return self.run[sat] >= self.calm_ticks

    def reset(self, sat: str) -> None:
        self.run[sat] = 0


# ----------------------------------------------------------------------------- state machine
class Responder:
    def __init__(
        self, profiles: dict[str, SatProfile], cfg: dict, sandbox: Sandbox, act_tau: float | None = None
    ):
        a = cfg["act"]
        self.profiles = profiles
        self.sandbox = sandbox
        self.safety = Safety(cfg, len(profiles))
        self.executor = Executor()
        self.verifier = Verifier(cfg)
        self.horizon = int(a["horizon_s"])
        self.warn_traj = float(a["warn_p"])
        self.plan_p = float(a["plan_p"])
        self.tau = act_tau  # ensemble alert threshold from calibration.json (0.1 % FPR), if any
        self.calm = float(a["calm_p"])
        self.unflag = int(a["unflag_ticks"])
        self.dwell = float(a["min_dwell_s"])
        self.replan = float(a["replan_s"])
        self.sus_window = float(a["suspicious_window_s"])
        self.state = {s: "nominal" for s in profiles}
        self.since = {s: -math.inf for s in profiles}
        self.last_plan = {s: -math.inf for s in profiles}
        self.contained = {s: None for s in profiles}  # action that is holding the satellite
        self.recent_tcs: dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        self.calm_run: dict[str, int] = defaultdict(int)

    def initial_states(self, t: float) -> list[S.NodeStateMsg]:
        return [S.NodeStateMsg(sat=s, t=t, state="nominal", reason="core started") for s in self.profiles]

    def warn_threshold(self, f: ForecastResult | None) -> float:
        if f is not None and self.tau is not None and f.model.startswith("tgn"):
            return self.tau
        return self.warn_traj

    # ------------------------------------------------------------------ helpers
    def _set(self, out: Outbox, t: float, sat: str, new: str, why: str) -> None:
        old = self.state[sat]
        if old == new:
            return
        self.state[sat], self.since[sat] = new, t
        out.states.append(S.NodeStateMsg(sat=sat, t=t, state=new, reason=why[:200]))
        out.events.append(explain.state_change(t, sat, old, new, why))

    def cause(self, sat: str, t: float) -> dict | None:
        """Most recent telecommand to this satellite that could drive the hazard (not the core's own)."""
        for tc in reversed(self.recent_tcs[sat]):
            if t - tc.t > self.sus_window:
                break
            if tc.origin == "core":
                continue
            if (tc.cmd == "HEATER" and tc.args.get("on") is True) or tc.cmd == "SET_MODE":
                return {"cmd": tc.cmd, "args": dict(tc.args), "origin": tc.origin, "age_s": t - tc.t}
        return None

    def suspicious_origins(self, sat: str, t: float) -> list[str]:
        return sorted(
            {
                tc.origin
                for tc in self.recent_tcs[sat]
                if t - tc.t <= self.sus_window
                and tc.origin.startswith(("isl:", "unknown"))
                and tc.cmd != "PING"  # relayed health checks are routine traffic
            }
        )

    # ------------------------------------------------------------------ one tick
    def step(
        self,
        tick: WorldTick,
        forecasts: dict[str, ForecastResult],
        monitor_events: list[MonitorEvent],
        untrusted: dict[str, set[str]],
    ) -> Outbox:
        out = Outbox()
        now = tick.t + 1.0  # the tick just closed; messages are stamped with its end
        for tc in tick.tcs:
            self.recent_tcs[tc.sat].append(tc)
        hits = {e.sat for e in monitor_events if e.kind == "hit"}
        for e in monitor_events:
            out.events.append(explain.monitor(now, e))
        for sat, prof in self.profiles.items():
            f = forecasts.get(sat)
            h = self.horizon
            p = 0.0 if f is None or f.warming_up else f.p(h)
            st, dwell_ok = self.state[sat], now - self.since[sat] >= self.dwell
            bad_trust = bool(untrusted.get(sat))
            if st == "nominal":
                warn = self.warn_threshold(f)
                if sat in hits or p >= warn:
                    why = (
                        "telemetry inconsistent (monitor)" if sat in hits else f"P(F<{h}s)={p:.2f} >= {warn}"
                    )
                    self._set(out, now, sat, "flagged", why)
                    self.calm_run[sat] = 0
                    st = "flagged"
            if st == "flagged" and dwell_ok and f is not None and not f.warming_up:
                if p >= self.plan_p and now - self.last_plan[sat] >= self.replan:
                    self._plan_and_act(out, now, sat, tick, f, forecasts, bad_trust)
                elif self.contained[sat] is None:
                    self.calm_run[sat] = self.calm_run[sat] + 1 if (p < self.calm and not bad_trust) else 0
                    if self.calm_run[sat] >= self.unflag:
                        self._set(
                            out,
                            now,
                            sat,
                            "nominal",
                            f"P(F<{h}s)={p:.2f} < {self.calm} for {self.calm_run[sat]} s",
                        )
                        self.calm_run[sat] = 0
            if self.contained[sat] is not None and self.state[sat] != "nominal":
                ready = self.verifier.update(sat, prof, tick, f, bad_trust)
                if ready and now - self.since[sat] >= self.dwell:
                    self._heal(out, now, sat, tick)
        return out

    def _plan_and_act(self, out, now, sat, tick, f, forecasts, untrusted_temp):
        import time as _time

        self.last_plan[sat] = now
        temps = {
            s: r.driver.value
            for s, r in forecasts.items()
            if r.driver and r.driver.channel == "temp_c" and r.estimated
        }
        rates = {
            s: r.driver.rate_per_s for s, r in forecasts.items() if r.driver and r.driver.channel == "temp_c"
        }
        budget_ok = self.safety.budget_ok(now)
        t0 = _time.perf_counter()
        ranked = self.sandbox.plan(tick, sat, f.q, temps, rates, self.suspicious_origins(sat, now), budget_ok)
        out.plan_ms.append((_time.perf_counter() - t0) * 1000)
        gated = self.safety.gate(f.q)
        chosen: Score = ranked[0]
        budget_note = "" if budget_ok else " · isolation budget reached (25 %/orbit): quarantine not offered"
        if gated:
            # CONTEXT 2 safety gate: do not act autonomously; safe hold and alert ground
            chosen = next((s for s in ranked if s.action == "safe_mode"), chosen)
        action, event = explain.decision(
            now,
            sat,
            f,
            chosen,
            ranked,
            self.cause(sat, now),
            untrusted_temp,
            gated,
            budget_note,
            horizon=self.horizon,
            theta=self.safety.theta,
        )
        out.actions.append(action)
        out.events.append(event)
        if chosen.action == "none":
            return
        peers = [s for s in self.profiles if s != sat]
        out.tcs += self.executor.carry_out(now, Candidate(chosen.action, sat, chosen.args), peers, tick.links)
        self.contained[sat] = chosen.action
        self.verifier.reset(sat)
        if chosen.action == "quarantine":
            self.safety.record_quarantine(now)
            self._set(out, now, sat, "quarantined", f"{chosen.action} (J={chosen.J:.2f})")
        elif chosen.action == "safe_mode":
            self._set(
                out,
                now,
                sat,
                "safe_hold",
                "uncertainty gate: safe hold, ground alerted" if gated else f"safe_mode (J={chosen.J:.2f})",
            )

    def _heal(self, out, now, sat, tick):
        action = self.contained[sat]
        tcs = self.executor.restore(now, sat, tick.links, exit_safe=action in ("quarantine", "safe_mode"))
        out.tcs += tcs
        th = tick.fresh(sat, "thermal")
        restored = sum(1 for tc in tcs if tc.cmd == "ISL_ENABLE") // 2
        out.events.append(
            explain.heal(
                now,
                sat,
                th["temp_c"] if th else None,
                restored,
                self.verifier.calm_ticks,
                self.horizon,
                self.calm,
            )
        )
        self.contained[sat] = None
        self.verifier.reset(sat)
        self._set(out, now, sat, "nominal", f"healed after {action}")
