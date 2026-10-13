"""Phase 6 scenario runs: A1, A1 with re-assertion, B1, C1 x 3 seeds, the CASSANDRA core in its
deployed configuration (learned ensemble for twin satellites when data/models has it) in closed loop
with the live twin (twin/service.py), in process (no broker; the broker path is covered by
tests/integration). Writes results/live_manifest.json with per-run outcomes.

    python scripts/phase6_runs.py [--seeds 1 2 3] [--scenarios A1 C1 N] [--out results/live_manifest.json]
    python scripts/phase6_runs.py --scenarios C1 --seeds 1 2 3 4 5 6 --no-future-victims  # pre-fix sandbox

Per run: the target's first flag and first action (time after the attack starts, action, true
temperature), its maximum true temperature and whether it crossed its limit, when it was healed,
state changes and actions on satellites that were not attacked (false alarms / false containments),
median tick latency and planning time. Simulated plant: every number is a simulation result.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aurora_common import schemas as S  # noqa: E402
from aurora_common import topics as T  # noqa: E402
from tests.cassandra.loop import T0, Loop  # noqa: E402

ATTACK_T = 60
TARGETS = {1: "E", 2: "G", 3: "J", 4: "C", 5: "H", 6: "L"}
SOURCES = {1: "A", 2: "D", 3: "K", 4: "F", 5: "I", 6: "L"}


def redteam(lp, sat, effect, args):
    lp.plant.on_redteam(T.redteam(sat), S.RedTeam(t=lp.now(), sat=sat, effect=effect, args=args))


def run(scenario: str, seed: int, ensemble: bool, future_victims: bool = True) -> dict:
    over = {"forecast": {"ensemble": {"enabled": ensemble}}}
    if not future_victims:  # the sandbox before 13 Oct: relays projected over the rollout only
        over["sandbox"] = {"future_victim_risk": 0.0}
    lp = Loop(seed=seed, duration_s=1000, cfg_overrides=over)
    model = lp.core.forecast.ensemble.model if lp.core.forecast.ensemble else "trajectory-v0"
    target = None if scenario == "N" else SOURCES[seed] if scenario == "C1" else TARGETS[seed]
    lp.run(ATTACK_T)
    stop_t = {"A1": None, "A1-reassert": 400, "B1": 460, "C1": 560, "N": None}[scenario]
    if scenario == "B1":
        redteam(lp, target, "freeze_tm", {"sub": "thermal", "field": "temp_c"})
    if scenario == "C1":
        redteam(
            lp, target, "relay_tc", {"cmd": "HEATER", "args": {"on": True}, "period_s": 10.0, "delay_s": 2.0}
        )
    elif scenario != "N":
        lp.send(target, "HEATER", {"on": True})
    t = ATTACK_T
    while t < 900:
        if scenario == "A1-reassert" and t < stop_t and (t - ATTACK_T) % 5 == 0 and t > ATTACK_T:
            lp.send(target, "HEATER", {"on": True})
        if stop_t and t == stop_t:
            if scenario in ("B1", "C1"):
                redteam(lp, target, "stop", {})
            if scenario != "C1":
                lp.send(target, "HEATER", {"on": False})
        lp.run(1)
        t += 1
    prof = lp.core.profiles
    attacked = set() if target is None else {target}
    if scenario == "C1":  # the victims are the satellites that received relayed hazardous commands
        attacked |= {tc.sat for tc in lp.core.responder.hazards if tc.origin == f"isl:{target}"}
    out = {
        "scenario": scenario,
        "seed": seed,
        "target": target,
        "forecaster": model,
        "attacked": sorted(attacked),
    }
    sats = {}
    for s in sorted(attacked):
        st = lp.states[s]
        acts = [a for a in lp.actions if a.target == s and a.action != "none"]
        temps = lp.true_temp[s]
        limit = prof[s].t_max_c
        first_flag = next((x - T0 for x, v in st if v == "flagged"), None)
        first_act = acts[0] if acts else None
        heal = next((e.t - T0 for e in lp.events if e.sat == s and e.kind == "heal"), None)
        sats[s] = {
            "flag_s_after_attack": None if first_flag is None else first_flag - ATTACK_T,
            "first_action": None if not first_act else first_act.action,
            "actions": [a.action for a in acts],
            "action_s_after_attack": None if not first_act else first_act.t - T0 - ATTACK_T,
            "true_temp_at_action_c": None if not first_act else round(temps[int(first_act.t - T0) - 1], 2),
            "max_true_temp_c": round(max(temps), 2),
            "limit_c": limit,
            "critical_reached": bool(max(temps) > limit),
            "healed_s_after_attack": None if heal is None else heal - ATTACK_T,
            "final_state": st[-1][1] if st else "nominal",
        }
    out["satellites"] = sats
    if scenario == "C1":
        src_acts = [a for a in lp.actions if a.target == target and a.action != "none"]
        out["c1"] = {
            "source_contained": bool(src_acts),
            "source_action": src_acts[0].action if src_acts else None,
            "source_action_s_after_attack": None if not src_acts else src_acts[0].t - T0 - ATTACK_T,
            "victims_touched": sorted(attacked - {target}),  # received a relayed hazardous command
            "victims_reaching_limit": sorted(s for s in attacked - {target} if sats[s]["critical_reached"]),
        }
    others_changed = {
        s: [v for _, v in lp.states[s]] for s in lp.states if s not in attacked and lp.states[s]
    }
    out["false_alarms"] = {s: v for s, v in others_changed.items() if "flagged" in v}
    out["false_containments"] = sorted(
        {a.target for a in lp.actions if a.action != "none" and a.target not in attacked}
    )
    out["tick_ms_median"] = round(statistics.median(lp.core.latency_ms), 2)
    out["plan_ms_median"] = round(statistics.median(lp.core.plan_ms), 1) if lp.core.plan_ms else None
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--scenarios", nargs="+", default=["A1", "A1-reassert", "B1", "C1"])
    ap.add_argument("--trajectory", action="store_true", help="force the trajectory forecaster")
    ap.add_argument("--no-future-victims", action="store_true", help="pre-fix C1 sandbox (comparison)")
    ap.add_argument("--out", default=str(ROOT / "results" / "live_manifest.json"))
    a = ap.parse_args(argv)
    t0 = time.time()
    runs = []
    for sc in a.scenarios:
        for seed in a.seeds:
            r = run(sc, seed, ensemble=not a.trajectory, future_victims=not a.no_future_victims)
            runs.append(r)
            print(
                json.dumps({k: r[k] for k in ("scenario", "seed", "target", "forecaster")}),
                json.dumps(r["satellites"]),
                json.dumps(r.get("c1", "")),
            )
            print(
                "   false alarms:",
                r["false_alarms"],
                "false containments:",
                r["false_containments"],
                flush=True,
            )
    git = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    manifest = {
        "what": "Phase 6 scenario runs, core in closed loop with the live twin (in process, simulated plant)",
        "command": "python scripts/phase6_runs.py " + " ".join(argv or sys.argv[1:]),
        "git": git,
        "python": platform.python_version(),
        "machine": platform.machine(),
        "wall_s": round(time.time() - t0, 1),
        "attack_at_s": ATTACK_T,
        "runs": runs,
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(manifest, indent=1))
    print(f"wrote {a.out} ({len(runs)} runs, {manifest['wall_s']} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
