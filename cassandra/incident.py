"""Incident correlator: the attack chain, assembled from what the core observes.

Evidence it uses (observed only, never gt/, redteam/ or labels): telecommands and their origin
(ingest), monitor hits, forecasts (p_fail, the driver's ETA, next_sub and p_malicious when not null)
and the core's own actions, heals and state changes. It groups evidence into incidents by satellite
and time window, and across satellites when hazardous commands share an ISL origin (isl:A: A's
relays and its victims are one incident).

Each incident is published on core/event with kind "incident" (interfaces.md 3.7): an id and ordered
steps. A step has a time, a satellite, a kind (command, effect, deception, predicted, action, healed),
plain-language text, SPARTA technique ids from interfaces.md Section 7 with their tactic, an evidence
reference, and observed = true/false. Predicted steps (next affected subsystem, projected failure with
its ETA) are forecasts, never observations, and are refreshed rather than stacked.

An incident opens on evidence: a monitor hit, an action, a hazardous command arriving over an
inter-satellite link (anomalous in itself), or a risk flag that follows a hazardous command. A risk
flag alone (e.g. a brief false flag at the ensemble's alert level) opens nothing, and a hazardous ground
command alone opens nothing (legitimate heater tests use it every orbit).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from aurora_common import schemas as S
from cassandra.act import hazardous

TACTIC = {"IA": "Initial Access", "EX": "Execution", "DE": "Defense Evasion", "CM": "Countermeasure"}
SUB_NAME = {
    "thermal": "thermal",
    "eps": "power (EPS)",
    "adcs": "attitude control",
    "obc": "on-board computer",
    "comms": "communications",
    "sun": "sun sensor",
}
CHANNEL = {
    "temp_c": ("temperature", "°C"),
    "soc_pct": ("battery charge", "%"),
    "att_err_deg": ("pointing error", "°"),
}
ACTION_TEXT = {
    "isolate_subsystem": "contained: heater cut",
    "safe_mode": "contained: safe mode, heater and non-essential loads off",
    "block_source": "contained: commands from that source blocked",
    "quarantine": "quarantined: links cut, traffic rerouted",
}
ACTION_CM = {
    "isolate_subsystem": ["CM0042", "CM0032"],
    "safe_mode": ["CM0042", "CM0032"],
    "block_source": ["CM0042", "CM0032"],
    "quarantine": ["CM0042", "CM0070"],
}


def tags(ids: list[str]) -> list[dict]:
    return [{"id": i, "tactic": TACTIC.get(i.split("-")[0][:2], "")} for i in ids]


def origin_words(origin: str) -> str:
    if origin == "ground":
        return "a ground command"
    if origin.startswith("isl:"):
        return f"a command relayed by {origin[4:]} over an inter-satellite link"
    return f"a command from {origin}"


def command_words(cmd: str, args: dict) -> str:
    if cmd == "HEATER":
        return f"heater switched {'on' if args.get('on') else 'off'}"
    if cmd == "SET_MODE":
        return f"mode set to {str(args.get('mode', '')).lower().replace('_', ' ')}"
    return cmd.lower()


@dataclass
class Step:
    t: float
    sat: str
    kind: str  # command | effect | deception | predicted | action | healed
    text: str
    sparta: list[str]
    evidence: dict
    observed: bool
    key: str  # one step per key (predicted steps are refreshed under the same key)

    def dump(self) -> dict:
        return {
            "t": round(self.t, 2),
            "sat": self.sat,
            "kind": self.kind,
            "text": self.text,
            "sparta": tags(self.sparta),
            "evidence": self.evidence,
            "observed": self.observed,
        }


@dataclass
class Incident:
    id: str
    opened_t: float
    source: str | None = None  # satellite relaying hazardous commands (C1), if any
    sats: set = field(default_factory=set)
    steps: dict = field(default_factory=dict)  # key -> Step
    status: str = "open"  # open | contained | closed
    acted: dict = field(default_factory=dict)  # sat -> first action time
    healed: set = field(default_factory=set)
    calm: set = field(default_factory=set)  # satellites back to nominal on their own (no action)
    dismissed: bool = False
    dirty: bool = True
    last_pub: float = -math.inf
    last_evidence_t: float = -math.inf

    def add(self, step: Step) -> bool:
        old = self.steps.get(step.key)
        if old is not None and (step.kind != "predicted" or old.text == step.text):
            return False
        self.steps[step.key] = step
        self.sats.add(step.sat)
        self.dirty = True
        return True

    def ordered(self) -> list[Step]:
        rank = {"command": 0, "effect": 1, "deception": 1, "predicted": 2, "action": 3, "healed": 4}
        return sorted(self.steps.values(), key=lambda s: (s.t, rank.get(s.kind, 5)))

    @property
    def first_evidence_t(self) -> float:
        obs = [s.t for s in self.steps.values() if s.observed]
        return min(obs) if obs else self.opened_t

    @property
    def confirmed(self) -> bool:
        """An action, a deception or a relaying source: an attack chain, not just a risk flag."""
        return bool(self.source) or any(s.kind in ("action", "deception") for s in self.steps.values())

    def victims(self) -> list[str]:
        return sorted(self.sats - {self.source}) if self.source else []


class Correlator:
    def __init__(self, cfg: dict):
        a = cfg["act"]
        self.window = float(a["suspicious_window_s"])
        self.hazard_modes = tuple(a.get("hazard_modes", ("WHEEL_TEST",)))
        self.close_after = float(cfg.get("incident", {}).get("close_after_s", 120.0))
        self.refresh = float(cfg.get("incident", {}).get("refresh_s", 10.0))
        self.recent: dict[str, list] = {}  # sat -> hazardous telecommands seen (not the core's own)
        self.incidents: list[Incident] = []
        self.by_sat: dict[str, Incident] = {}  # open incident each satellite belongs to
        self.n = 0

    # ------------------------------------------------------------------ grouping
    def _incident_for(self, sat: str, now: float, source: str | None = None) -> Incident:
        if source and source in self.by_sat:
            inc = self.by_sat[source]
        elif sat in self.by_sat:
            inc = self.by_sat[sat]
        else:
            relay = self._relayer(sat, now)
            if relay and relay in self.by_sat:
                inc = self.by_sat[relay]
            else:
                self.n += 1
                inc = Incident(id=f"INC-{self.n:03d}-{source or sat}", opened_t=now)
                self.incidents.append(inc)
        if source:
            inc.source = source
            self.by_sat[source] = inc
            inc.sats.add(source)
        # a victim incident opened before its relay source was known joins the source's incident
        if sat in self.by_sat and self.by_sat[sat] is not inc:
            self._merge(self.by_sat[sat], inc)
        self.by_sat[sat] = inc
        inc.sats.add(sat)
        return inc

    def _merge(self, old: Incident, into: Incident) -> None:
        for k, st in old.steps.items():
            into.steps.setdefault(k, st)
        into.sats |= old.sats
        into.acted.update({s: t for s, t in old.acted.items() if s not in into.acted})
        into.healed |= old.healed
        into.opened_t = min(into.opened_t, old.opened_t)
        for s in old.sats:
            self.by_sat[s] = into
        if old in self.incidents:
            self.incidents.remove(old)
        into.dirty = True

    def _relayer(self, sat: str, now: float) -> str | None:
        for tc in reversed(self.recent.get(sat, [])):
            if now - tc.t <= self.window and tc.origin.startswith("isl:"):
                return tc.origin[4:]
        return None

    def _causes(self, inc: Incident, sat: str, now: float) -> None:
        """The hazardous commands that preceded the evidence on this satellite: the chain's first steps.
        A satellite that received relayed hazardous commands owes its state to the relay: a ground
        command in the same window (e.g. a routine heater test) is not attached as its cause."""
        recent = [tc for tc in self.recent.get(sat, []) if now - tc.t <= self.window]
        relayed = [tc for tc in recent if tc.origin.startswith("isl:")]
        groups: dict[tuple, list] = {}
        for tc in relayed or recent:  # repeated copies of one command from one origin: one step
            groups.setdefault((tc.origin, tc.cmd, tuple(sorted(tc.args.items()))), []).append(tc)
        for (origin, cmd, args), tcs in groups.items():
            first = min(tcs, key=lambda x: x.t)
            n = len({round(x.t, 1) for x in tcs})
            text = f"{sat}: {command_words(cmd, dict(args))} by {origin_words(origin)}"
            if n > 1:
                text += f" ({n} copies)"
            key = f"cmd:{sat}:{origin}:{cmd}"
            old = inc.steps.get(key)
            if old is not None and old.text != text:
                old.text, inc.dirty = text, True
                continue
            inc.add(
                Step(
                    first.t,
                    sat,
                    "command",
                    text,
                    ["IA-0003" if origin.startswith("isl:") else "IA-0009"],
                    {"type": "telecommand", "id": first.id, "origin": origin, "copies": n},
                    True,
                    key,
                )
            )

    # ------------------------------------------------------------------ one tick
    def step(self, tick, monitor_events, forecasts: dict, out) -> list[S.Event]:
        now = tick.t + 1.0
        for tc in tick.tcs:
            if tc.origin == "core" or not hazardous(tc, self.hazard_modes):
                continue
            self.recent.setdefault(tc.sat, []).append(tc)
            self.recent[tc.sat] = [x for x in self.recent[tc.sat] if now - x.t <= self.window]
            if tc.origin.startswith("isl:"):  # a relayed hazardous command is evidence by itself
                src = tc.origin[4:]
                inc = self._incident_for(tc.sat, now, source=src)
                self._causes(inc, tc.sat, now)
                inc.last_evidence_t = now
        for ev in monitor_events:
            if ev.kind != "hit":
                continue
            inc = self._incident_for(ev.sat, now)
            self._causes(inc, ev.sat, now)
            ids = list(ev.sparta) or ["DE-0002"]
            what = {
                "frozen": "temperature report stopped changing (a live sensor never does)",
                "heater_inconsistent": "heater on but the reported temperature is not rising",
            }.get(ev.rule, ev.rule)
            inc.add(
                Step(
                    now,
                    ev.sat,
                    "deception",
                    f"{ev.sat}: {what}: the report is being masked; "
                    "CASSANDRA estimates the temperature instead",
                    ids,
                    {"type": "monitor", "rule": ev.rule},
                    True,
                    f"dec:{ev.sat}:{ev.rule}",
                )
            )
            inc.last_evidence_t = now
        for ev in out.events:
            d = ev.data or {}
            if d.get("event") == "state" and d.get("to") == "flagged" and ev.sat:
                if d.get("trigger") == "relay":  # the source: its activity is the relaying itself
                    inc = self._incident_for(ev.sat, now, source=ev.sat)
                    what = str(d.get("relaying", "hazardous commands"))
                    inc.add(
                        Step(
                            now,
                            ev.sat,
                            "command",
                            f"{ev.sat}: relaying {what} over inter-satellite links "
                            "(suspected compromised satellite)",
                            ["IA-0003"],
                            {"type": "core/event", "trigger": "relay"},
                            True,
                            f"relay:{ev.sat}",
                        )
                    )
                else:
                    # a risk flag alone is not an attack chain: it opens an incident only behind a
                    # hazardous command (monitor hits and actions open one on their own)
                    cause = [tc for tc in self.recent.get(ev.sat, []) if now - tc.t <= self.window]
                    if not cause and ev.sat not in self.by_sat:
                        continue
                    inc = self._incident_for(ev.sat, now)
                    self._causes(inc, ev.sat, now)
                    self._effect(inc, ev.sat, forecasts.get(ev.sat), now)
                inc.last_evidence_t = now
            elif (
                d.get("event") == "state"
                and d.get("to") == "nominal"
                and d.get("reason") == "calm"
                and ev.sat in self.by_sat
            ):
                inc = self.by_sat[ev.sat]
                inc.calm.add(ev.sat)
                if not inc.confirmed and inc.calm >= inc.sats:
                    # every satellite calmed down by itself: a risk flag after a command, not an attack
                    inc.status, inc.dismissed, inc.dirty = "closed", True, True
                    for s_ in list(inc.sats):
                        if self.by_sat.get(s_) is inc:
                            del self.by_sat[s_]
            elif d.get("event") == "heal" and ev.sat in self.by_sat:
                inc = self.by_sat[ev.sat]
                temp = d.get("temp_c")
                txt = f"{ev.sat} healed: back to normal" + (
                    f" at {temp:.1f} °C" if isinstance(temp, (int, float)) else ""
                )
                inc.add(
                    Step(
                        now,
                        ev.sat,
                        "healed",
                        txt,
                        ["CM0042"],
                        {"type": "core/event", "kind": "heal"},
                        True,
                        f"heal:{ev.sat}:{len(inc.healed)}",
                    )
                )
                inc.healed.add(ev.sat)
            elif d.get("event") == "ground_cleared" and ev.sat in self.by_sat:
                inc = self.by_sat[ev.sat]
                inc.add(
                    Step(
                        now,
                        ev.sat,
                        "healed",
                        f"{ev.sat} cleared by ground, back in service",
                        ["CM0042"],
                        {"type": "core/event", "kind": "heal"},
                        True,
                        f"clear:{ev.sat}",
                    )
                )
                inc.healed.add(ev.sat)
        for a in out.actions:
            if a.action == "none":
                continue
            inc = self._incident_for(a.target, now)
            if a.target != inc.source:
                self._causes(inc, a.target, now)
                self._effect(inc, a.target, forecasts.get(a.target), now)
                self._predict(inc, a.target, forecasts.get(a.target), now, force=True)
            inc.add(
                Step(
                    a.t,
                    a.target,
                    "action",
                    f"{a.target} {ACTION_TEXT.get(a.action, a.action)}",
                    ACTION_CM.get(a.action, ["CM0042"]),
                    {"type": "core/action", "action": a.action, "t": a.t},
                    True,
                    f"act:{a.target}:{a.action}:{round(a.t)}",
                )
            )
            inc.acted.setdefault(a.target, a.t)
            inc.last_evidence_t = now
        for inc in self.incidents:
            if inc.status == "closed":
                continue
            for sat in sorted(inc.sats - {inc.source}):
                f = forecasts.get(sat)
                # a victim reached by the relay: its heating is the effect, even before it is flagged
                if (
                    sat not in inc.acted
                    and f is not None
                    and f.driver is not None
                    and f.driver.channel == "temp_c"
                    and f.driver.rate_per_s > 0.02
                    and not any(st.kind == "effect" and st.sat == sat for st in inc.steps.values())
                    and any(st.kind == "command" and st.sat == sat for st in inc.steps.values())
                ):
                    self._effect(inc, sat, f, now)
                if sat not in inc.acted:  # forecasts are refreshed until the satellite is contained
                    self._predict(inc, sat, f, now)
            self._status(inc, now)
        return self._publish(now)

    # ------------------------------------------------------------------ steps from forecasts
    def _effect(self, inc: Incident, sat: str, f, now: float) -> None:
        if f is None or f.driver is None:
            return
        d = f.driver
        name, unit = CHANNEL.get(d.channel, (d.channel, ""))
        est = d.channel in f.estimated
        verb = "rising" if (d.rate_per_s > 0) == (d.channel != "soc_pct") else "falling"
        text = f"{sat}: {name} {verb} {abs(d.rate_per_s):.2f} {unit}/s, now {d.value:.1f} {unit}"
        if est:
            text += " (estimated: the report is untrusted)"
        technique = "IA-0003" if self._relayer(sat, now) else "IA-0009"
        inc.add(
            Step(
                now,
                sat,
                "effect",
                text,
                [technique],
                {"type": "telemetry", "channel": d.channel, "model": f.model},
                not est,
                f"eff:{sat}:{d.channel}",
            )
        )

    def _predict(self, inc: Incident, sat: str, f, now: float, force: bool = False) -> None:
        if f is None or f.warming_up:
            return
        d = f.driver
        eta = math.inf if d is None else d.eta_s
        if eta > 600 and f.p(60) < 0.02:
            return  # no meaningful risk: no forecast step (an ETA of hours is not a projection)
        last = inc.steps.get(f"pred:{sat}")
        if not force and last is not None and now - last.t < self.refresh:
            return
        rule = f.model.startswith("trajectory")
        model = "rule-based, from the engineering dependency graph" if rule else "learned model"
        parts, evidence = [], {"type": "core/forecast", "model": f.model, "p60": round(f.p(60), 3)}
        if d is not None and eta <= 600:
            name, _ = CHANNEL.get(d.channel, (d.channel, ""))
            parts.append(
                f"{sat} reaches its {name} limit in ~{eta:.0f} s if nothing is done "
                f"(failure risk {f.p(60):.0%} within 60 s)"
            )
            evidence["eta_s"] = round(eta, 1)
        if f.next_sub:
            mal = "" if f.p_malicious is None else f", P(malicious) {f.p_malicious:.2f}"
            parts.append(f"next affected subsystem: {SUB_NAME.get(f.next_sub, f.next_sub)} ({model}{mal})")
            evidence.update(next_sub=f.next_sub, p_malicious=f.p_malicious)
        if parts:  # one forecast step per satellite, refreshed (never stacked)
            inc.add(
                Step(
                    now,
                    sat,
                    "predicted",
                    "Forecast: " + "; ".join(parts),
                    ["CM0032"],
                    evidence,
                    False,
                    f"pred:{sat}",
                )
            )

    # ------------------------------------------------------------------ status, output
    def _status(self, inc: Incident, now: float) -> None:
        harmed = {s.sat for s in inc.steps.values() if s.kind in ("effect", "deception")} | (
            {inc.source} if inc.source else set()
        )
        new = inc.status
        if harmed and all(s in inc.acted for s in harmed):
            new = "contained"
        if (
            inc.acted
            and inc.healed >= (set(inc.acted) - ({inc.source} if inc.source else set()))
            and now - inc.last_evidence_t > self.close_after
        ):
            new = "closed"
            inc.add(
                Step(
                    now,
                    sorted(inc.sats)[0],
                    "healed",
                    f"Incident closed: {', '.join(sorted(inc.healed))} back to normal",
                    ["CM0042"],
                    {"type": "incident", "status": "closed"},
                    True,
                    "closed",
                )
            )
            for s in list(inc.sats):
                if self.by_sat.get(s) is inc:
                    del self.by_sat[s]
        if new != inc.status:
            inc.status, inc.dirty = new, True

    def summary(self, inc: Incident) -> dict:
        steps = inc.ordered()
        src_t = inc.acted.get(inc.source) if inc.source else None
        touched = [s for s in inc.victims() if any(st.kind == "command" and st.sat == s for st in steps)]
        return {
            "event": "incident",
            "incident_id": inc.id,
            "status": inc.status,
            "confirmed": inc.confirmed,
            "dismissed": inc.dismissed,
            "opened_t": round(inc.opened_t, 2),
            "first_evidence_t": round(inc.first_evidence_t, 2),
            "assembly_delay_s": round(inc.opened_t - inc.first_evidence_t, 1),
            "source": inc.source,
            "sats": sorted(inc.sats),
            "victims": inc.victims(),
            "victims_touched_before_source_contained": (
                None
                if src_t is None
                else sorted(
                    s
                    for s in touched
                    if min(st.t for st in steps if st.sat == s and st.kind == "command") < src_t
                )
            ),
            "steps": [s.dump() for s in steps],
        }

    def _publish(self, now: float) -> list[S.Event]:
        out = []
        for inc in self.incidents:
            if not inc.dirty:
                continue
            inc.dirty, inc.last_pub = False, now
            data = self.summary(inc)
            ids = sorted({i for s in inc.steps.values() for i in s.sparta})
            who = f"{inc.source} -> {', '.join(inc.victims())}" if inc.source else ", ".join(sorted(inc.sats))
            out.append(
                S.Event(
                    t=now,
                    sat=inc.source or sorted(inc.sats)[0],
                    level={"open": "alarm", "contained": "warning", "closed": "info"}[inc.status],
                    kind="incident",
                    text=f"incident {inc.id} ({inc.status}): {who}, {len(inc.steps)} steps",
                    sparta=ids or None,
                    data=data,
                )
            )
        return out
