"""Closed loop on the twin: forecast -> simulate -> act -> verify (CONTEXT 2, 5.4, 5.8 "response").

This is the ML side's reference implementation used to measure response effectiveness; the
production sandbox / safety / executor modules belong to cassandra/ (core owner). It consumes
only Section 6.2 messages (twin.messages_at), never ground truth; ground truth is read afterwards
to score the run.

Actions (CONTEXT 5.4): none, safe_mode (CM0042), block_source (CM0032), isolate_subsystem
(cut the heater / payload rail), quarantine_reroute (CM0070: disable the node's ISLs; traffic uses
the remaining feasible links). J(a) = w_r R + w_m (1 - U) + w_c C + w_q Q, with R the ensemble
P(F < t + 60 s) after a rollout, U the fraction of the constellation in service, Q the ensemble
standard deviation. Gates: Q > theta_uncert -> safe hold + alert ground; quarantines limited to
25 % of the constellation per orbital period.

    python -m eval.closed_loop --episodes 36      # results/eval/closed_loop.json
"""

from __future__ import annotations

import argparse
import copy
import json
import time

import numpy as np

from ml import config, schema
from ml.online import OnlineForecaster
from ml.synthetic.constellation import orbital_period_s
from ml.synthetic.engine import MODE_NOMINAL, SyntheticTwin
from ml.synthetic.scenarios import EpisodeSpec

ACTIONS = ["none", "safe_mode", "block_source", "isolate_subsystem", "quarantine_reroute"]


def apply_action(twin: SyntheticTwin, sat: str, action: str, t: int):
    s = twin.idx[sat]
    core = lambda cmd, args=None: twin.apply({"sat": sat, "origin": "core", "cmd": cmd, "args": args or {}})
    if action == "safe_mode":
        core("SAFE_MODE")
    elif action == "block_source":
        recent = {
            r["origin"]
            for r in twin.commands
            if r["sat"] == sat
            and schema.origin_kind(r["origin"]) in ("unknown", "isl")
            and r["t"] >= twin.t0_unix + (t - 120) * twin.dt
        }
        for o in recent:
            twin.block_source(s, o)  # later commands from these origins are rejected when they arrive
    elif action == "isolate_subsystem":
        twin.isolate_heater(s)
        core("SET_MODE", {"mode": "IDLE"})
    elif action == "quarantine_reroute":
        core("SAFE_MODE")  # QUARANTINED is a core state, not a commandable mode (D3)
        for peer in twin.sats:
            if peer != sat:
                core("ISL_ENABLE", {"peer": peer, "on": False})
                twin.apply(
                    {"sat": peer, "origin": "core", "cmd": "ISL_ENABLE", "args": {"peer": sat, "on": False}}
                )


def restore(twin: SyntheticTwin, sat: str):
    twin.apply({"sat": sat, "origin": "core", "cmd": "SET_MODE", "args": {"mode": "NOMINAL"}})
    for peer in twin.sats:
        if peer != sat:
            twin.apply(
                {"sat": sat, "origin": "core", "cmd": "ISL_ENABLE", "args": {"peer": peer, "on": True}}
            )
            twin.apply(
                {"sat": peer, "origin": "core", "cmd": "ISL_ENABLE", "args": {"peer": sat, "on": True}}
            )
    getattr(twin, "blocked", {}).pop(twin.idx[sat], None)


def feed(fc, twin, t):
    for kind, msg in twin.messages_at(t):
        {"tm": fc.on_telemetry, "tc": fc.on_command, "link": fc.on_link}[kind](msg)


def in_service(twin) -> float:
    return float((twin.mode == MODE_NOMINAL).mean())


def plan(twin, fc, sat, t, rc, quarantines_this_orbit, n_sats):
    t0 = time.perf_counter()
    snap = twin.snapshot()  # the real twin, restored after planning
    # M2: candidates are rolled out from a copy without queued or planned future commands, so the
    # planner never sees the attacker's next moves; the last observed effects persist
    plan_base = twin.snapshot(include_queue=False)
    fc_state = (copy.deepcopy(fc.frames), copy.deepcopy(fc.buffer))
    budget_ok = (quarantines_this_orbit + 1) / n_sats <= rc["isolation_budget"] + 1e-9
    cands = [a for a in ACTIONS if a != "quarantine_reroute" or budget_ok]
    table = []
    for a in cands:
        twin.restore(plan_base)
        fc.frames, fc.buffer = copy.deepcopy(fc_state[0]), copy.deepcopy(fc_state[1])
        apply_action(twin, sat, a, t)
        k = t
        for _ in range(rc["rollout_s"]):
            if twin.t >= twin.T:
                break
            k = twin.t
            twin.step()
            feed(fc, twin, k)
            fc.push_frame()
        last = fc.forecast(twin.t0_unix + k * twin.dt)  # Section 6.2: Unix time
        # R = expected number of satellites reaching F (sum of risks), as in the CASSANDRA core
        R = float(sum(f["p_fail"]["60"] for f in last))
        mine = next(f for f in last if f["sat"] == sat)
        Q = mine["p_std"]["60"] if mine.get("p_std") else None  # D4: no Q from a single model
        U = in_service(twin)
        w = rc["weights"]
        J = (
            w["w_r"] * R
            + w["w_m"] * (1 - U)
            + w["w_c"] * rc["cost"][a]
            + (w["w_q"] * Q if Q is not None else 0.0)
        )
        table.append(dict(action=a, R=R, U=U, Q=Q, J=J))
    twin.restore(snap)
    fc.frames, fc.buffer = fc_state
    best = min(table, key=lambda r: r["J"])
    best["gated"] = None if best["Q"] is None else best["Q"] > rc["theta_uncert"]
    return best, table, (time.perf_counter() - t0) * 1000


def run_episode(cfg, spec: EpisodeSpec, fc: OnlineForecaster, rc: dict) -> dict:
    twin = SyntheticTwin(cfg, spec)
    fc.frames = type(fc.frames)(twin.sats)
    fc.buffer.clear()
    S = twin.S
    contained = np.zeros(S, bool)
    healthy = np.zeros(S, int)
    next_plan = np.zeros(S, int)
    period = orbital_period_s(cfg["orbit"]["altitude_km"]) / cfg["orbit"]["orbit_speedup"]
    quarantine_log: list[int] = []
    events, step_ms, plan_ms, availability = [], [], [], []
    while twin.t < twin.T:
        t = twin.t
        twin.step()
        t0 = time.perf_counter()
        feed(fc, twin, t)
        forecasts = fc.tick(twin.t0_unix + t * twin.dt)
        step_ms.append((time.perf_counter() - t0) * 1000)
        availability.append(in_service(twin))
        for s, f in enumerate(forecasts):
            p = f["p_fail"]["60"]
            if p >= rc["act_threshold"] and not contained[s] and t >= next_plan[s]:
                recent_q = sum(1 for k in quarantine_log if t - k < period)
                best, table, ms = plan(twin, fc, twin.sats[s], t, rc, recent_q, S)
                plan_ms.append(ms)
                action = "safe_mode" if best["gated"] else best["action"]
                next_plan[s] = t + 10
                if action != "none":
                    apply_action(twin, twin.sats[s], action, t)
                    contained[s] = True
                    if action == "quarantine_reroute":
                        quarantine_log.append(t)
                events.append(
                    dict(
                        t=t,
                        sat=twin.sats[s],
                        event="act",
                        action=action,
                        gated=best["gated"],
                        p=p,
                        table=table,
                        plan_ms=ms,
                    )
                )
            if contained[s]:
                ok = p < rc["flag_threshold"]
                healthy[s] = healthy[s] + 1 if ok else 0
                if healthy[s] >= rc["heal_steps"]:
                    restore(twin, twin.sats[s])
                    contained[s] = False
                    healthy[s] = 0
                    events.append(dict(t=t, sat=twin.sats[s], event="restore"))
    failed = twin.failed.any(0)
    return dict(
        events=events,
        failed=failed.tolist(),
        step_ms=step_ms,
        plan_ms=plan_ms,
        availability=float(np.mean(availability)),
        sats=twin.sats,
    )


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=36)
    ap.add_argument("--split", default="test_id")
    a = ap.parse_args(argv)
    cfg, rc = config.load("synthetic"), config.load("eval")["response"]
    fc = OnlineForecaster.from_models()
    root = config.EPISODES / a.split
    import pandas as pd

    idx = pd.read_csv(root / "index.csv")
    failing = idx[idx.critical_t.notna()]
    benign = idx[idx.critical_t.isna() & idx.kind.isin(["clean", "fault"])]
    n_f = min(len(failing), int(a.episodes * 2 / 3))
    chosen = list(failing.sample(n_f, random_state=7).episode_id) + list(
        benign.sample(min(len(benign), a.episodes - n_f), random_state=7).episode_id
    )
    rows = []
    seeds = np.random.default_rng(cfg["split_seeds"][a.split]).integers(0, 2**31, cfg["splits"][a.split])
    for eid in chosen:
        ep = schema.read_episode(root / eid)
        from ml.synthetic.scenarios import sample_spec

        i = int(eid.split("-")[-1])
        spec = sample_spec(cfg, a.split, i, np.random.default_rng(int(seeds[i])))
        truth = ep["ml_labels"].satellites
        out = run_episode(cfg, spec, fc, rc)
        fail_without = [s for s, v in truth.items() if v.critical_t is not None]
        at_risk = {s for s, v in truth.items() if v.onset_t is not None or v.critical_t is not None}
        contain = [e for e in out["events"] if e["event"] == "act" and e["action"] != "none"]
        rows.append(
            dict(
                episode=eid,
                type=ep["labels"].type,
                failing_without=fail_without,
                failing_with=[s for s, f in zip(out["sats"], out["failed"]) if f],
                contained=[e["sat"] for e in contain],
                actions=[e["action"] for e in contain],
                false_containments=[e["sat"] for e in contain if e["sat"] not in at_risk],
                gated=sum(bool(e.get("gated")) for e in out["events"]),
                availability=out["availability"],
                step_ms=float(np.median(out["step_ms"])),
                plan_ms=[float(x) for x in out["plan_ms"]],
            )
        )
        print(
            eid,
            ep["labels"].type,
            "fail",
            len(fail_without),
            "->",
            len(rows[-1]["failing_with"]),
            "actions",
            rows[-1]["actions"],
            flush=True,
        )
    fw = sum(len(r["failing_without"]) for r in rows)
    prevented = sum(len(set(r["failing_without"]) - set(r["failing_with"])) for r in rows)
    plan = [x for r in rows for x in r["plan_ms"]]
    acts = [x for r in rows for x in r["actions"]]
    orbits = (
        len(rows)
        * cfg["episode_s"]
        / (orbital_period_s(cfg["orbit"]["altitude_km"]) / cfg["orbit"]["orbit_speedup"])
    )
    summary = dict(
        episodes=len(rows),
        failing_without=fw,
        prevented=prevented,
        prevented_frac=prevented / max(fw, 1),
        new_failures=sum(len(set(r["failing_with"]) - set(r["failing_without"])) for r in rows),
        mean_availability=float(np.mean([r["availability"] for r in rows])),
        false_containments=sum(len(r["false_containments"]) for r in rows),
        false_containments_per_orbit=sum(len(r["false_containments"]) for r in rows) / orbits,
        actions={a: acts.count(a) for a in ACTIONS if acts.count(a)},
        gated=sum(bool(r["gated"]) for r in rows),
        step_ms_median=float(np.median([r["step_ms"] for r in rows])),
        plan_ms_median=float(np.median(plan)) if plan else None,
        plan_ms_p95=float(np.percentile(plan, 95)) if plan else None,
        note="sandbox uses the simulator as its state-transition model: an upper bound",
    )
    out_dir = config.RESULTS / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "closed_loop.json").write_text(
        json.dumps(dict(summary=summary, episodes=rows), indent=1, default=float)
    )
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
