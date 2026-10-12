"""Paired runtime-policy campaign; planner sees observations, scoring alone reads plant truth."""

from __future__ import annotations

import argparse
import copy
import json

import numpy as np

from aurora_common import topics as T
from cassandra import config as core_config
from cassandra.__main__ import Core
from ml import config
from ml.synthetic.engine import MODE_NOMINAL
from twin.service import TwinService, build_twin

T0 = 1_800_000_000.0


def run(seed, scenario, mismatch, defend, duration=480):
    plant = build_twin(seed, duration + 100)
    if mismatch:
        plant.cfg = copy.deepcopy(plant.cfg)
        plant.cfg["thermal"]["conductance_w_per_k"] *= 0.7
        plant.cfg["thermal"]["heat_capacity_j_per_k"] *= 0.8
    cfg = core_config.load()
    core = Core(cfg) if defend else None

    class Pipe:
        def publish(self, topic, model):
            if core:
                core.ingest.feed(topic, model, model.t)

    service = TwinService(Pipe(), plant, list(T.SATS), start_t=T0, seed=seed)
    actions, states, events, availability = [], [], [], []
    onset = 60
    for t in range(duration):
        if scenario != "clean" and t >= onset and (t - onset) % 30 == 0 and t < 240:
            cmd, args = (
                ("HEATER", {"on": True}) if scenario == "heater" else ("SET_MODE", {"mode": "WHEEL_TEST"})
            )
            # Hidden marker configures the attack actuator; contract telemetry strips it.
            plant._cmd(t, "E", "ground", cmd, args, malicious=True)
        service.step()
        availability.append(float((plant.mode == MODE_NOMINAL).mean()))
        if core:
            for tick in core.ingest.poll(T0 + t + 1):
                out, _ = core.handle_tick(tick)
                actions.extend(
                    {"t": a.t - T0, "sat": a.target, "action": a.action, "gated": a.gated, "q": a.q}
                    for a in out.actions
                )
                events.extend(
                    {"t": e.t - T0, "sat": e.sat, "kind": e.kind, "text": e.text}
                    for e in out.events
                    if e.kind in ("decision", "gated", "heal")
                )
                states.extend({"t": s.t - T0, "sat": s.sat, "state": s.state} for s in out.states)
                for tc in out.tcs:
                    service.on_tc(T.tc(tc.sat), tc)
                    core.ingest.feed(T.tc(tc.sat), tc, tc.t)
    failed = plant.failed[:duration].any(0)
    return {
        "failed_sats": [s for s, f in zip(plant.sats, failed) if f],
        "actions": actions,
        "states": states,
        "events": events,
        "first_critical_s": {
            sat: int(np.flatnonzero(plant.failed[:duration, i])[0])
            for i, sat in enumerate(plant.sats)
            if failed[i]
        },
        "mean_availability": float(np.mean(availability)),
        "latency_ms": (
            {
                "tick_median": float(np.median(core.latency_ms)),
                "tick_p95": float(np.percentile(core.latency_ms, 95)),
                "plan_median": float(np.median(core.plan_ms)) if core.plan_ms else None,
                "plan_p95": float(np.percentile(core.plan_ms, 95)) if core.plan_ms else None,
            }
            if core
            else None
        ),
        "final_states": {
            s: next((x["state"] for x in reversed(states) if x["sat"] == s), "nominal") for s in plant.sats
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=4)
    args = ap.parse_args()
    rows = []
    for mismatch in (False, True):
        for scenario in ("clean", "heater", "wheel"):
            for seed in range(args.seeds):
                baseline = run(seed, scenario, mismatch, False)
                defended = run(seed, scenario, mismatch, True)
                row = {
                    "seed": seed,
                    "scenario": scenario,
                    "mismatch": mismatch,
                    "baseline": baseline,
                    "defended": defended,
                    "prevented": sorted(set(baseline["failed_sats"]) - set(defended["failed_sats"])),
                    "new_failures": sorted(set(defended["failed_sats"]) - set(baseline["failed_sats"])),
                }
                rows.append(row)
                print(scenario, mismatch, seed, len(row["prevented"]), len(row["new_failures"]), flush=True)
    result = {
        "protocol": "paired seeds; actual Core; 480s; ground-origin repeated commands; no truth in planner",
        "mismatch": "plant thermal capacity -20%, conductance -30%; sandbox nominal",
        "episodes": len(rows),
        "rows": rows,
    }
    (config.RESULTS / "eval").mkdir(parents=True, exist_ok=True)
    (config.RESULTS / "eval" / "runtime_policy.json").write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
