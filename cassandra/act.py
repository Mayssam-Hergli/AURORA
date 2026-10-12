"""Act stage (D11: safety, executor and verify in one file) and the per-satellite state machine.

States (interfaces.md 3.3): nominal -> flagged -> quarantined | safe_hold -> nominal.
P below is P(F < t + horizon_s), horizon_s = 60 by default (config explains why not 30).
- nominal -> flagged: P >= warn_p (the ensemble uses tau from calibration.json), or a monitor hit.
- flagged -> plan: P >= plan_p, re-planned at most every replan_s. The sandbox ranks the candidates
  by J(a); "none" is a candidate, so J decides whether acting is worth it.
  Safety then applies the uncertainty gate (q > theta: safe hold + alert ground instead of
  isolating; q null: gate inactive, D4) and the isolation budget (quarantine offered only while
  quarantines in the current orbit window stay within 25 % of the constellation).
- quarantine -> state quarantined; safe_mode -> safe_hold; cut_heater / block_source ->
  the satellite stays in service, flagged, until the verifier clears it.
- Verifier: heal when temp_c is trusted and below heal_below_c and P < calm_p for calm_ticks
  consecutive ticks: SET_MODE NOMINAL, re-enable the links the core cut, state nominal.
- A flagged satellite on which nothing was done returns to nominal after unflag_ticks ticks below
  min(calm_p, half its flag threshold) (hysteresis also for the ensemble's small tau).
- Repeated commands: a hazardous command (HEATER on, SET_MODE SCIENCE / WHEEL_TEST) seen at least
  threat_min_count times from the same origin is a Threat that the sandbox models as continuing.
  Healing waits until it has gone quiet (no copy for threat_quiet_factor x its period + 5 s).
- Escalation: when a containment does not hold (the heater is ON again after a cut, or P stays
  >= plan_p for escalate_after_s), the satellite is re-planned with J over the stronger actions only:
  cut_heater / block_source -> safe_mode -> quarantine (+ reroute). Each step is explained;
  when quarantine does not hold either, the core says so and asks for ground action. Escalation
  never reaches quarantine when no cyber cause was observed (no hazardous command, no suspicious
  origin): a hardware fault does not propagate, so it ends in safe mode and a call for ground action.
- Relay sources (Scenario C): a satellite seen relaying a hazardous command over ISL (origin isl:X)
  is flagged as a suspected source and planned like any other satellite; the sandbox models its relays
  as continuing (only cut links stop them). If J quarantines it, it stays quarantined until ground
  clears it with SET_MODE NOMINAL (origin ground): compromised flight software cannot be shown healthy
  from telemetry, so there is no autonomous heal for a source.
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
from aurora_common.criticality import observed_safe
from cassandra import explain
from cassandra.actions import ACTIONS as ACTION_SPECS
from cassandra.forecast import ForecastResult
from cassandra.monitor import MonitorEvent
from cassandra.sandbox import Candidate, Sandbox, Score, Threat
from cassandra.world import SatProfile, WorldTick

LADDER = {"block_source": 0, "cut_heater": 0, "safe_mode": 1, "quarantine": 2}
HAZARD_MODES = ("WHEEL_TEST",)


def hazardous(tc: S.Telecommand, modes: tuple[str, ...] = HAZARD_MODES) -> bool:
    """Commands that drive a satellite towards F when they come from someone else than the core."""
    if tc.origin == "core":
        return False
    if tc.cmd == "HEATER":
        return tc.args.get("on") is True
    return tc.cmd == "SET_MODE" and tc.args.get("mode") in modes


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

    def uncertainty_state(self, q: float | None) -> str:
        if q is None or not math.isfinite(q) or q < 0:
            return "UNKNOWN"
        return "KNOWN_HIGH" if q > self.theta else "KNOWN_LOW"

    def gate(self, q: float | None) -> bool:
        """True: high-impact isolation withheld for unknown or high uncertainty."""
        return self.uncertainty_state(q) != "KNOWN_LOW"

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
            observed_safe(tick.values.get(sat, {}), prof)
            and f is not None
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
        self.escalate_after = float(a["escalate_after_s"])
        self.hazard_modes = tuple(a.get("hazard_modes", HAZARD_MODES))
        self.none_repeat = float(a.get("none_repeat_s", 30.0))
        self.last_none: dict[str, float] = {}
        self.threat_min = int(a["threat_min_count"])
        self.threat_quiet = float(a["threat_quiet_factor"])
        self.hazards: deque = deque(maxlen=2000)  # hazardous telecommands seen on the bus
        self.contained_at: dict[str, float] = {}
        self.failing: dict[str, int] = defaultdict(int)
        self.exhausted: set[str] = set()
        self.holding: set[str] = set()
        self.ground_hold: set[str] = set()  # relay sources quarantined until ground clears them
        self.held_noted: set[str] = set()
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

    def unflag_threshold(self, f: ForecastResult | None) -> float:
        """Hysteresis: a flagged satellite returns to nominal only below half its own flag threshold
        (at most calm_p). With the ensemble's tau (~0.015) below calm_p, P = 0.03 was both a flag and
        calm, and satellites flapped between flagged and nominal."""
        return min(self.calm, 0.5 * self.warn_threshold(f))

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
            if hazardous(tc, self.hazard_modes):
                return {"cmd": tc.cmd, "args": dict(tc.args), "origin": tc.origin, "age_s": t - tc.t}
        return None

    def threats(self, now: float) -> list[Threat]:
        """Hazardous commands repeated from the same origin and still arriving."""
        groups: dict[tuple, list[S.Telecommand]] = defaultdict(list)
        for tc in self.hazards:
            if now - tc.t > self.sus_window:
                continue
            args = tuple(sorted(tc.args.items()))
            # a relaying neighbour pushes to many satellites; a ground/unknown origin targets one
            key = (
                (tc.origin, tc.cmd, args)
                if tc.origin.startswith("isl:")
                else (tc.origin, tc.cmd, args, tc.sat)
            )
            groups[key].append(tc)
        out = []
        for key, tcs in groups.items():
            times = sorted({round(tc.t, 1) for tc in tcs})
            if len(times) < self.threat_min:
                continue
            gaps = [b - a for a, b in zip(times, times[1:]) if b - a > 0.5]
            if not gaps:
                continue
            period = float(sorted(gaps)[len(gaps) // 2])
            if now - times[-1] > self.threat_quiet * period + 5.0:
                continue  # gone quiet
            out.append(
                Threat(
                    key[0],
                    key[1],
                    key[2],
                    tuple(sorted({tc.sat for tc in tcs})),
                    period,
                    times[-1],
                    len(times),
                )
            )
        return out

    def relay_sources(self, now: float) -> dict[str, str]:
        """Satellites seen relaying hazardous commands over ISL recently: X -> 'HEATER on to B, C'."""
        seen: dict[str, dict[str, set]] = {}
        for tc in self.hazards:
            if now - tc.t > self.sus_window or not tc.origin.startswith("isl:"):
                continue
            x = tc.origin.split(":", 1)[1]
            what = f"{tc.cmd} {'on' if tc.args.get('on') else tc.args.get('mode', '')}".strip()
            seen.setdefault(x, {}).setdefault(what, set()).add(tc.sat)
        return {
            x: "; ".join(f"{w} to {', '.join(sorted(v))}" for w, v in d.items())
            for x, d in seen.items()
            if x in self.profiles
        }

    def _ground_clears(self, out: Outbox, now: float, tick: WorldTick) -> None:
        """Ground reintegrates a held source with SET_MODE NOMINAL (origin ground)."""
        for tc in tick.tcs:
            if (
                tc.sat in self.ground_hold
                and tc.origin == "ground"
                and tc.cmd == "SET_MODE"
                and tc.args.get("mode") == "NOMINAL"
            ):
                sat = tc.sat
                self.ground_hold.discard(sat)
                self.held_noted.discard(sat)
                self.hazards = deque(
                    (h for h in self.hazards if h.origin != f"isl:{sat}"), maxlen=self.hazards.maxlen
                )
                out.tcs += self.executor.restore(now, sat, tick.links, exit_safe=False)
                out.events.append(explain.ground_cleared(now, sat))
                self.contained[sat] = None
                self.contained_at.pop(sat, None)
                self.verifier.reset(sat)
                self._set(out, now, sat, "nominal", "cleared by ground")

    def threat_on(self, sat: str, threats: list[Threat]) -> Threat | None:
        return next((th for th in threats if sat in th.sats), None)

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
            if hazardous(tc, self.hazard_modes):
                self.hazards.append(tc)
        threats = self.threats(now)
        sources = self.relay_sources(now)
        self._ground_clears(out, now, tick)
        hits = {e.sat for e in monitor_events if e.kind == "hit"}
        for e in monitor_events:
            out.events.append(explain.monitor(now, e))
        for sat, prof in self.profiles.items():
            f = forecasts.get(sat)
            h = self.horizon
            p = 0.0 if f is None or f.warming_up else f.p(h)
            st, dwell_ok = self.state[sat], now - self.since[sat] >= self.dwell
            bad_trust = bool(untrusted.get(sat))
            src = sources.get(sat)
            if st == "nominal":
                warn = self.warn_threshold(f)
                if sat in hits or p >= warn or src:
                    if src:
                        why = f"relaying {src} over ISL (suspected compromised neighbour, IA-0003)"
                    elif sat in hits:
                        why = "telemetry inconsistent (monitor)"
                    else:
                        why = f"P(F<{h}s)={p:.3g} >= {warn:.3g}"
                    self._set(out, now, sat, "flagged", why)
                    self.calm_run[sat] = 0
                    st = "flagged"
            if st == "flagged" and dwell_ok and f is not None and not f.warming_up:
                if (
                    self.contained[sat] is None
                    and (p >= self.plan_p or src)
                    and now - self.last_plan[sat] >= self.replan
                ):
                    self._plan_and_act(
                        out, now, sat, tick, f, forecasts, bad_trust, threats, source=bool(src)
                    )
                elif self.contained[sat] is None and not src:
                    calm = self.unflag_threshold(f)
                    self.calm_run[sat] = self.calm_run[sat] + 1 if (p < calm and not bad_trust) else 0
                    if self.calm_run[sat] >= self.unflag:
                        self._set(
                            out,
                            now,
                            sat,
                            "nominal",
                            f"P(F<{h}s)={p:.3g} < {calm:.3g} for {self.calm_run[sat]} s",
                        )
                        self.calm_run[sat] = 0
            if sat in self.ground_hold:
                if sat not in self.held_noted:
                    self.held_noted.add(sat)
                    out.events.append(explain.ground_hold(now, sat))
                continue
            if self.contained[sat] is not None and self.state[sat] != "nominal":
                why = self._not_holding(sat, tick, p, now)
                self.failing[sat] = self.failing[sat] + 1 if why else 0
                if self.failing[sat] >= 2:
                    self._escalate(out, now, sat, tick, f, forecasts, bad_trust, threats, why)
                    continue
                ready = self.verifier.update(sat, prof, tick, f, bad_trust)
                if ready and now - self.since[sat] >= self.dwell:
                    th = self.threat_on(sat, threats)
                    if th is not None:
                        if sat not in self.holding:
                            self.holding.add(sat)
                            out.events.append(explain.holding(now, sat, th))
                        continue
                    self._heal(out, now, sat, tick)
        return out

    def _not_holding(self, sat: str, tick: WorldTick, p: float, now: float) -> str:
        """Why the current containment is failing ('' = it holds)."""
        action, since = self.contained[sat], self.contained_at.get(sat, now)
        th = tick.fresh(sat, "thermal")
        if action in ("cut_heater", "safe_mode", "quarantine") and th and th["heater_on"] and now - since > 2:
            return f"heater ON again {now - since:.0f} s after {action}"
        if p >= self.plan_p and now - since >= self.escalate_after:
            return f"P(F<{self.horizon}s)={p:.2f} still >= {self.plan_p} {now - since:.0f} s after {action}"
        return ""

    def _inputs(self, forecasts):
        temps = {
            s: r.driver.value
            for s, r in forecasts.items()
            if r.driver and r.driver.channel == "temp_c" and r.estimated
        }
        rates = {
            s: r.driver.rate_per_s for s, r in forecasts.items() if r.driver and r.driver.channel == "temp_c"
        }
        return temps, rates

    def _execute(self, out, now, sat, tick, chosen: Score, gated) -> None:
        peers = [s for s in self.profiles if s != sat]
        out.tcs += self.executor.carry_out(now, Candidate(chosen.action, sat, chosen.args), peers, tick.links)
        self.contained[sat] = chosen.action
        self.contained_at[sat] = now
        self.failing[sat] = 0
        self.holding.discard(sat)
        self.verifier.reset(sat)
        if chosen.action == "quarantine":
            self.safety.record_quarantine(now)
            self._set(out, now, sat, "quarantined", f"{chosen.action} (J={chosen.J:.2f})")
        elif chosen.action == "safe_mode":
            why = "uncertainty gate: safe hold, ground alerted" if gated else f"safe_mode (J={chosen.J:.2f})"
            self._set(out, now, sat, "safe_hold", why)

    def _escalate(self, out, now, sat, tick, f, forecasts, untrusted_temp, threats, why):
        import time as _time

        self.failing[sat] = 0
        level = LADDER.get(self.contained[sat], 0)
        stronger = tuple(a for a, lv in LADDER.items() if lv > level and a != "block_source")
        if self.safety.gate(f.q if f else None):
            stronger = tuple(a for a in stronger if ACTION_SPECS[a].allow_unknown)
        budget_ok = self.safety.budget_ok(now)
        if not budget_ok:
            stronger = tuple(a for a in stronger if a != "quarantine")
        if self.cause(sat, now) is None and not self.suspicious_origins(sat, now):
            # no cyber cause observed: a hardware fault does not propagate, quarantine buys nothing
            stronger = tuple(a for a in stronger if a != "quarantine")
            why += "; no command cause observed (possible hardware fault)"
        if not stronger or f is None:
            if sat not in self.exhausted:
                self.exhausted.add(sat)
                out.events.append(explain.exhausted(now, sat, self.contained[sat], why, budget_ok))
            return
        temps, rates = self._inputs(forecasts)
        t0 = _time.perf_counter()
        ranked = self.sandbox.plan(
            tick, sat, f.q, temps, rates, self.suspicious_origins(sat, now), budget_ok, threats, stronger
        )
        out.plan_ms.append((_time.perf_counter() - t0) * 1000)
        if not ranked:
            return
        gated = self.safety.gate(f.q)
        chosen = ranked[0]
        if gated:
            chosen = next((s for s in ranked if s.action in ("none", "cut_heater", "safe_mode")), chosen)
        action, event = explain.decision(
            now,
            sat,
            f,
            chosen,
            ranked,
            self.cause(sat, now),
            untrusted_temp,
            gated,
            "",
            horizon=self.horizon,
            theta=self.safety.theta,
            lead=f"escalation: {self.contained[sat]} did not hold ({why}) -> ",
        )
        out.actions.append(action)
        out.events.append(event)
        self._execute(out, now, sat, tick, chosen, gated)

    def _plan_and_act(self, out, now, sat, tick, f, forecasts, untrusted_temp, threats=(), source=False):
        import time as _time

        self.last_plan[sat] = now
        temps, rates = self._inputs(forecasts)
        budget_ok = self.safety.budget_ok(now)
        t0 = _time.perf_counter()
        allowed = ("none", "safe_mode", "quarantine") if source else None
        if self.safety.gate(f.q):
            allowed = ("none",) if source else ("none", "cut_heater", "safe_mode")
        disabled = [f"{a}-{b}" for v in self.executor.cut.values() for a, b in v]
        ranked = self.sandbox.plan(
            tick,
            sat,
            f.q,
            temps,
            rates,
            self.suspicious_origins(sat, now),
            budget_ok,
            list(threats),
            allowed,
            disabled,
        )
        if not ranked:
            return
        out.plan_ms.append((_time.perf_counter() - t0) * 1000)
        gated = self.safety.gate(f.q)
        chosen: Score = ranked[0]
        budget_note = "" if budget_ok else " · isolation budget reached (25 %/orbit): quarantine not offered"
        if gated:
            # CONTEXT 2 safety gate: do not act autonomously; safe hold and alert ground
            chosen = next((s for s in ranked if s.action in ("none", "cut_heater", "safe_mode")), chosen)
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
        if chosen.action == "none":
            # a repeated 'none' is published at most every none_repeat_s (no event flood)
            if now - self.last_none.get(sat, -math.inf) >= self.none_repeat:
                self.last_none[sat] = now
                out.actions.append(action)
                out.events.append(event)
            return
        out.actions.append(action)
        out.events.append(event)
        self._execute(out, now, sat, tick, chosen, gated)
        if source and chosen.action == "quarantine":
            self.ground_hold.add(sat)

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
        self.contained_at.pop(sat, None)
        self.exhausted.discard(sat)
        self.holding.discard(sat)
        self.verifier.reset(sat)
        self._set(out, now, sat, "nominal", f"healed after {action}")
