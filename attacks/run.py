"""Run one red-team or fault scenario against the live bus (twin, replay plant or hardware bridge).

    python -m attacks.run --scenario A1 --target E                 # one latched HEATER on (default)
    python -m attacks.run --scenario A1 --target E --reassert 5    # re-send it every 5 s for --duration
    python -m attacks.run --scenario B1 --target E                 # freeze reported temp_c, then A1
    python -m attacks.run --scenario C1 --target A                 # A relays HEATER on to its neighbours
    python -m attacks.run --scenario F1 --target E                 # sensor drift on temp_c (a fault)
    python -m attacks.run --scenario N                             # clean run: ground-truth marker only
    python -m attacks.run --stop --target E                        # release: HEATER off, effects cleared

Scenarios (CONTEXT 5.7, SPARTA per interfaces.md 7):
  A1  IA-0009  malicious telecommand through abused ground trust: HEATER on with a spoofed origin
  B1  DE-0002  telemetry masking: redteam freeze_tm of temp_c, then the A1 heater command underneath
  C1  IA-0003  compromised neighbour: redteam relay_tc makes the target forward HEATER on to every
               satellite it has a feasible, enabled link to, every --period seconds
  F1  fault    sensor drift: redteam bias_tm on temp_c (--drift C/s). Not an attack: ground truth
               says kind "fault". F2 (SEU), F3 (battery ageing) and F5 (stuck heater) have no bus
               message in the contract: inject them in the plant (scripts/replay_twin.py --fault).
  N   clean    no action; publishes the ground-truth episode so the recorder can score false alarms
Every command is an ordinary contract message with no "malicious" mark. Ground truth goes to
aurora/v1/gt/... only, which the core never subscribes to.

Clock: commands are stamped with the bus's message time, taken from the target's own telemetry when
it is fresh (the plant may run fast-forward); otherwise with the wall clock.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurora_common import schemas as S  # noqa: E402
from aurora_common import topics as T  # noqa: E402
from aurora_common.bus import Bus  # noqa: E402

SCENARIOS = {  # type -> (scenario letter, kind)
    "A1": ("A", "attack"),
    "B1": ("B", "attack"),
    "C1": ("C", "attack"),
    "F1": ("F", "fault"),
    "N": ("N", "clean"),
}


class BusClock:
    """Message time as seen on the bus (the target's telemetry), falling back to the wall clock."""

    def __init__(self):
        self.t, self.wall = None, 0.0
        self.lock = threading.Lock()

    def on_tm(self, topic, msg):
        with self.lock:
            self.t, self.wall = msg.t, time.time()

    def now(self) -> float:
        with self.lock:
            if self.t is not None and time.time() - self.wall < 2.0:
                return self.t + (time.time() - self.wall)
            return time.time()


def tc(clock: BusClock, sat: str, cmd: str, args: dict, origin: str) -> S.Telecommand:
    problems = S.args_problems(cmd, args)
    if problems:
        raise ValueError(f"{cmd} {args}: {problems}")
    return S.Telecommand(id=uuid.uuid4().hex, t=clock.now(), sat=sat, origin=origin, cmd=cmd, args=args)


def redteam(clock: BusClock, sat: str, effect: str, args: dict) -> S.RedTeam:
    return S.RedTeam(t=clock.now(), sat=sat, effect=effect, args=args)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="A1")
    ap.add_argument("--target", default="E", choices=list(T.SATS))
    ap.add_argument("--origin", default="ground", help="spoofed origin of the malicious command (A1, B1)")
    ap.add_argument("--onset", type=float, default=0.0, help="seconds to wait before the attack")
    ap.add_argument("--duration", type=float, default=0.0, help="seconds to stay attached after onset")
    ap.add_argument(
        "--reassert", type=float, default=0.0, help="re-send the heater command every N s (A1, B1)"
    )
    ap.add_argument("--period", type=float, default=10.0, help="C1: relay period in simulated seconds")
    ap.add_argument("--drift", type=float, default=0.02, help="F1: drift of the reported temp_c in C/s")
    ap.add_argument("--stop", action="store_true", help="release the target instead of attacking it")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    a = ap.parse_args(argv)
    if not (a.origin in ("ground", "core", "unknown") or a.origin.startswith("isl:")):
        ap.error("--origin must be ground, core, unknown or isl:<id>")

    clock = BusClock()
    bus = Bus(f"attacker-{uuid.uuid4().hex[:6]}", a.host, a.port)
    bus.subscribe(T.tm(a.target, "thermal"), clock.on_tm)
    if not bus.start():
        print(f"no broker at {a.host}:{a.port} (make up)", file=sys.stderr)
        return 1
    time.sleep(1.2)  # let the target's telemetry set the clock

    if a.stop:
        bus.publish(T.redteam(a.target), redteam(clock, a.target, "stop", {}))
        bus.publish(T.tc(a.target), tc(clock, a.target, "HEATER", {"on": False}, "ground"))
        print(f"released {a.target}: red-team effects cleared, HEATER off (origin ground)")
        time.sleep(0.5)
        bus.stop()
        return 0

    scenario, kind = SCENARIOS[a.scenario]
    target = None if a.scenario == "N" else a.target
    episode = f"live-{time.strftime('%Y%m%d-%H%M%S')}-{a.scenario}-{target or 'none'}"
    if a.onset:
        time.sleep(a.onset)
    onset_t = clock.now()
    labels = dict(
        episode_id=episode,
        generator="attacks-v1",
        seed=a.seed,
        scenario=scenario,
        kind=kind,
        type=a.scenario,
        target=target,
        onset_t=None if a.scenario == "N" else onset_t,
        critical_t=None,
        critical_rule=None,
        params={"origin": a.origin, "reassert_s": a.reassert, "duration_s": a.duration, "period_s": a.period},
        satellites=list(T.SATS),
    )
    bus.publish(T.GT_EPISODE, S.GtEpisode(t=onset_t, phase="start", **labels))  # ground truth: recorder only
    if target:
        bus.publish(T.GT_EVENT, S.GtEvent(t=onset_t, episode_id=episode, event="onset", sat=target, data={}))

    heater = None
    if a.scenario == "A1":
        heater = tc(clock, target, "HEATER", {"on": True}, a.origin)
    elif a.scenario == "B1":
        bus.publish(
            T.redteam(target), redteam(clock, target, "freeze_tm", {"sub": "thermal", "field": "temp_c"})
        )
        time.sleep(0.5)  # the mask is in place before the heater starts
        heater = tc(clock, target, "HEATER", {"on": True}, a.origin)
    elif a.scenario == "C1":
        bus.publish(
            T.redteam(target),
            redteam(
                clock,
                target,
                "relay_tc",
                {"cmd": "HEATER", "args": {"on": True}, "period_s": a.period, "delay_s": 2.0},
            ),
        )
    elif a.scenario == "F1":
        bus.publish(
            T.redteam(target),
            redteam(clock, target, "bias_tm", {"sub": "thermal", "field": "temp_c", "rate_per_s": a.drift}),
        )
    if heater is not None:
        bus.publish(T.tc(target), heater)
    print(f"[{a.scenario}] started on {target or 'no satellite'} (episode {episode})", flush=True)

    end = time.time() + a.duration
    sends = 1 if heater is not None else 0
    try:
        while time.time() < end:
            time.sleep(min(a.reassert or 0.5, max(0.0, end - time.time())))
            if heater is not None and a.reassert and time.time() < end:
                bus.publish(T.tc(target), tc(clock, target, "HEATER", {"on": True}, a.origin))
                sends += 1
    except KeyboardInterrupt:
        pass
    t_end = clock.now()
    if target:
        bus.publish(T.GT_EVENT, S.GtEvent(t=t_end, episode_id=episode, event="stop", sat=target))
    bus.publish(T.GT_EPISODE, S.GtEpisode(t=t_end, phase="end", **labels))
    note = "effects stay until `--stop`" if a.scenario in ("B1", "C1", "F1") else "the heater stays latched"
    print(f"[{a.scenario}] done: {sends} heater command(s) sent; {note}")
    time.sleep(0.5)
    bus.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
