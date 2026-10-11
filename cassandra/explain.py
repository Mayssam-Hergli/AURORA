"""Explain stage: template sentences for aurora/v1/core/event (D12: templates, no LLM).

Decision line shape (MASTER_PLAN Phase 4):
  [E] P(F<60s)=0.88 (trajectory-v0) · cause: heater latched ON (origin=ground) -> temp_c 41.2 C,
  +0.21 C/s · T_max 45 C in ~18 s · SPARTA IA-0009 · action: quarantine (J=0.21)
  · alternatives: safe_mode 0.34, none 0.92

SPARTA ids follow docs/interfaces.md Section 7 (proposal P4).
"""

from __future__ import annotations

import math

from aurora_common import schemas as S
from cassandra.forecast import ForecastResult
from cassandra.monitor import MonitorEvent
from cassandra.sandbox import Score
from cassandra.world import Note

COUNTERMEASURES = {
    "none": ("CM0032",),
    "safe_mode": ("CM0032", "CM0042"),
    "isolate_subsystem": ("CM0032", "CM0042"),
    "block_source": ("CM0032", "CM0042"),
    "quarantine": ("CM0032", "CM0042", "CM0070"),
}


def techniques(cause: dict | None, untrusted: bool) -> list[str]:
    """SPARTA techniques consistent with what was observed (never with what ground truth says)."""
    out = []
    origin = (cause or {}).get("origin", "")
    if origin.startswith("isl:"):
        out.append("IA-0003")
    elif cause:
        out.append("IA-0009")
    if untrusted:
        out += ["DE-0002", "EX-0001"]
    return out


def cause_text(cause: dict | None, f: ForecastResult | None) -> str:
    parts = []
    if cause:
        what = {
            ("HEATER", True): "heater latched ON",
            ("SET_MODE", None): f"mode change to {cause.get('args', {}).get('mode')}",
        }.get((cause["cmd"], cause.get("args", {}).get("on") if cause["cmd"] == "HEATER" else None))
        what = what or f"{cause['cmd']} {cause.get('args', {})}"
        parts.append(f"{what} (origin={cause['origin']}, {cause['age_s']:.0f} s ago)")
    if f is not None and f.driver is not None:
        d = f.driver
        est = " (estimated: reported value untrusted)" if d.channel in f.estimated else ""
        parts.append(f"{d.channel} {d.value:.1f}{est}, {d.rate_per_s:+.2f}/s")
    return " -> ".join(parts) if parts else "rising risk with no recent command"


def eta_text(f: ForecastResult | None) -> str:
    if f is None or f.driver is None or math.isinf(f.driver.eta_s):
        return ""
    d = f.driver
    name = {"temp_c": "T_max", "soc_pct": "SOC_min", "att_err_deg": "theta_max"}[d.channel]
    return f" · {name} {d.limit:g} in ~{d.eta_s:.0f} s"


def decision(
    t: float,
    sat: str,
    f: ForecastResult,
    chosen: Score,
    ranked: list[Score],
    cause: dict | None,
    untrusted: bool,
    gated: bool | None,
    budget_note: str = "",
    horizon: int = 60,
    theta: float = 0.35,
    lead: str = "",
) -> tuple[S.Action, S.Event]:
    alts = [s for s in ranked if s is not chosen]
    sparta = techniques(cause, untrusted)
    gate = (
        " · uncertainty gate inactive (single model)"
        if gated is None
        else f" · GATED: q={f.q:.2f} > {theta}, safe hold, alert ground" if gated else ""
    )
    text = (
        f"[{sat}] {lead}P(F<{horizon}s)={f.p(horizon):.2f} ({f.model})"
        f" · cause: {cause_text(cause, f)}{eta_text(f)}"
        f"{' · SPARTA ' + ', '.join(sparta) if sparta else ''} · action: {chosen.action} (J={chosen.J:.2f})"
        f" · alternatives: {', '.join(f'{a.action} {a.J:.2f}' for a in alts) or 'none'}{gate}{budget_note}"
    )
    action = S.Action(
        t=t,
        action=chosen.action,
        target=sat,
        args=chosen.args or None,
        J=round(chosen.J, 4),
        alternatives=[S.Alternative(action=a.action, J=round(a.J, 4)) for a in alts],
        gated=gated,
        q=f.q,
        reason=text.split("] ", 1)[1],
    )
    event = S.Event(
        t=t,
        sat=sat,
        level="alarm" if chosen.action != "none" or gated else "warning",
        kind="gated" if gated else "decision",
        text=text,
        sparta=sparta + list(COUNTERMEASURES[chosen.action]),
        data={
            "action": chosen.action,
            "R": round(chosen.R, 3),
            "U": round(chosen.U, 3),
            "C": chosen.C,
            "Q": chosen.Q,
            "J": round(chosen.J, 4),
            "rollout": chosen.detail,
        },
    )
    return action, event


def holding(t: float, sat: str, th) -> S.Event:
    who = th.origin
    return S.Event(
        t=t,
        sat=sat,
        level="warning",
        kind="decision",
        text=(
            f"[{sat}] holding containment: {th.cmd} {dict(th.args)} from origin={who} still arriving "
            f"every ~{th.period_s:.0f} s ({th.count} seen); heal waits until it stops"
        ),
        data={"origin": who, "period_s": th.period_s, "count": th.count},
    )


def exhausted(t: float, sat: str, action: str | None, why: str, budget_ok: bool) -> S.Event:
    extra = "" if budget_ok else " (isolation budget reached: quarantine not available)"
    return S.Event(
        t=t,
        sat=sat,
        level="alarm",
        kind="decision",
        text=(
            f"[{sat}] {action} did not hold ({why}) and no stronger action is left{extra}: "
            "ground action required"
        ),
        sparta=["CM0042"],
    )


def state_change(t: float, sat: str, old: str, new: str, why: str) -> S.Event:
    level = {"nominal": "info", "flagged": "warning", "quarantined": "alarm", "safe_hold": "alarm"}[new]
    kind = "heal" if new == "nominal" and old != "flagged" else "decision"
    return S.Event(t=t, sat=sat, level=level, kind=kind, text=f"[{sat}] {old} -> {new}: {why}")


def heal(
    t: float, sat: str, temp: float | None, restored: int, calm_s: int, horizon: int = 60, calm_p: float = 0.1
) -> S.Event:
    tt = f"temp_c {temp:.1f} C, " if temp is not None else ""
    return S.Event(
        t=t,
        sat=sat,
        level="info",
        kind="heal",
        text=(
            f"[{sat}] healed: {tt}P(F<{horizon}s) < {calm_p} for {calm_s} s; "
            f"back to NOMINAL, {restored} link(s) restored"
        ),
        sparta=["CM0042"],
    )


def monitor(t: float, ev: MonitorEvent) -> S.Event:
    return S.Event(
        t=t,
        sat=ev.sat,
        level="alarm" if ev.kind == "hit" else "info",
        kind="monitor",
        text=ev.text,
        sparta=list(ev.sparta) or None,
        data={"rule": ev.rule, **ev.data},
    )


def note(t: float, n: Note) -> S.Event:
    kind = {"startup": "startup", "clock": "clock"}.get(n.kind, "ingest")
    return S.Event(
        t=t, sat=n.sat, level=n.level, kind=kind, text=n.text, data={"note": n.kind, **n.data} or None
    )
