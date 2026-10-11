"""Run one red-team scenario against the live bus (twin, replay plant or hardware bridge).

    python -m attacks.run --scenario A1 --target E                 # one latched HEATER on (default)
    python -m attacks.run --scenario A1 --target E --reassert 5    # re-send it every 5 s for --duration
    python -m attacks.run --stop --target E                        # release: HEATER off, effects cleared

A1 (SPARTA IA-0009, trusted relationship): a malicious telecommand accepted through abused ground
trust. The command is an ordinary contract telecommand with a spoofed origin (`ground` by default);
it carries no "malicious" mark of any kind. Ground truth goes to aurora/v1/gt/... only, which the
core never subscribes to.

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

SCENARIOS = {"A1": ("A", "attack")}  # B1, C1, faults and N arrive in Phase 5


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="A1")
    ap.add_argument("--target", required=True, choices=list(T.SATS))
    ap.add_argument("--origin", default="ground", help="spoofed origin of the malicious command")
    ap.add_argument("--onset", type=float, default=0.0, help="seconds to wait before the attack")
    ap.add_argument("--duration", type=float, default=0.0, help="seconds to stay attached after onset")
    ap.add_argument(
        "--reassert", type=float, default=0.0, help="re-send the command every N s (0 = latch once)"
    )
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
        bus.publish(T.tc(a.target), tc(clock, a.target, "HEATER", {"on": False}, "ground"))
        bus.publish(T.redteam(a.target), S.RedTeam(t=clock.now(), sat=a.target, effect="stop"))
        print(f"released {a.target}: HEATER off (origin ground), red-team effects cleared")
        time.sleep(0.5)
        bus.stop()
        return 0

    scenario, kind = SCENARIOS[a.scenario]
    episode = f"live-{time.strftime('%Y%m%d-%H%M%S')}-{a.scenario}-{a.target}"
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
        target=a.target,
        onset_t=onset_t,
        critical_t=None,
        critical_rule=None,
        params={"origin": a.origin, "reassert_s": a.reassert, "duration_s": a.duration},
        satellites=list(T.SATS),
    )
    bus.publish(T.GT_EPISODE, S.GtEpisode(t=onset_t, phase="start", **labels))  # ground truth: recorder only
    bus.publish(
        T.GT_EVENT,
        S.GtEvent(t=onset_t, episode_id=episode, event="onset", sat=a.target, data={"cmd": "HEATER"}),
    )
    bus.publish(T.tc(a.target), tc(clock, a.target, "HEATER", {"on": True}, a.origin))
    print(f"[{a.scenario}] HEATER on -> {a.target} (origin={a.origin}), episode {episode}", flush=True)

    end = time.time() + a.duration
    sends = 1
    try:
        while time.time() < end:
            time.sleep(min(a.reassert or 0.5, max(0.0, end - time.time())))
            if a.reassert and time.time() < end:
                bus.publish(T.tc(a.target), tc(clock, a.target, "HEATER", {"on": True}, a.origin))
                sends += 1
    except KeyboardInterrupt:
        pass
    t_end = clock.now()
    bus.publish(T.GT_EVENT, S.GtEvent(t=t_end, episode_id=episode, event="stop", sat=a.target))
    bus.publish(T.GT_EPISODE, S.GtEpisode(t=t_end, phase="end", **labels))
    print(
        f"[{a.scenario}] done: {sends} command(s) sent; the heater stays latched until something turns it off"
    )
    time.sleep(0.5)
    bus.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
