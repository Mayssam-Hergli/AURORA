"""Stand-in plant for testing the CASSANDRA core until Youssef's twin/service.py exists.

Steps ml.synthetic.engine.SyntheticTwin one tick at a time on a *clean* scenario (no attack is
scheduled inside the engine; background ground traffic, heater tests and ISL relays still happen),
publishes SyntheticTwin.messages_at(t) on the bus in contract form, and applies every telecommand
it receives on aurora/v1/sat/+/tc through the engine's public apply(). So attacks sent over the
bus (attacks/) and the core's responses both act on the simulated physics.

    python scripts/replay_twin.py                      # A-L at 1 tick/s, 1 h
    python scripts/replay_twin.py --sats A,C-L         # leave B to the hardware bridge
    python scripts/replay_twin.py --speed 20 --duration 600   # fast-forward for tests

SAFE semantics (docs/interfaces.md 3.2): while a satellite is in SAFE, this plant refuses HEATER on
from any origin and SET_MODE other than NOMINAL/SAFE, for bus commands and the engine's own
scheduled traffic alike; only SET_MODE NOMINAL exits SAFE. It never looks at whether a command is
malicious.

Honesty: everything published here is simulated (src "twin"). It is not the Phase 3 live twin:
no red-team effects (redteam/{id}) yet, and the engine's offline modelling choices apply.
Timestamps are message time: t = start + tick, so --speed > 1 runs the whole system fast-forward
(the core ticks on message time).
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import queue
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurora_common import schemas as S  # noqa: E402
from aurora_common import topics as T  # noqa: E402
from aurora_common.bus import Bus  # noqa: E402
from ml import config as ml_config  # noqa: E402
from ml.synthetic.engine import MODE_SAFE, SyntheticTwin  # noqa: E402
from ml.synthetic.scenarios import sample_spec  # noqa: E402

log = logging.getLogger("replay_twin")


def parse_sats(spec: str) -> list[str]:
    """'A-L' -> A..L, 'A,C-L' -> A, C..L."""
    out: list[str] = []
    for part in spec.split(","):
        part = part.strip().upper()
        if "-" in part:
            a, b = part.split("-")
            out += [s for s in T.SATS if a <= s <= b]
        elif part:
            out.append(part)
    bad = [s for s in out if s not in T.SATS]
    if bad:
        raise ValueError(f"unknown satellites {bad}")
    return list(dict.fromkeys(out))


def build_twin(seed: int, duration_s: int) -> SyntheticTwin:
    if duration_s < 120:
        raise ValueError("duration must be at least 120 s (the engine schedules science windows)")
    cfg = copy.deepcopy(ml_config.load("synthetic"))
    cfg["episode_s"] = duration_s
    spec = sample_spec(cfg, "train", seed, np.random.default_rng(seed))
    spec.type, spec.target, spec.onset_s, spec.channel, spec.n_victims = "N", None, None, None, 0
    spec.episode_id = f"replay-{seed}"
    return SyntheticTwin(cfg, spec)


def to_contract(kind: str, msg: dict, t_shift: float) -> tuple[str, S._Model]:
    """Engine message -> (topic, validated contract model), with the time base shifted to now."""
    m = {k: v for k, v in msg.items() if not k.startswith("_") and k != "accepted"}
    m["t"] = float(m["t"]) + t_shift
    if kind == "tm":
        if m["sub"] == "comms" and isinstance(m["fields"].get("isl_peers"), str):
            m["fields"] = {
                **m["fields"],
                "isl_peers": S.CommsFields._peers_from_string(m["fields"]["isl_peers"]),
            }
        topic = T.tm(m["sat"], m["sub"])
    elif kind == "tc":
        if isinstance(m.get("args"), str):
            m["args"] = json.loads(m["args"]) if m["args"] else {}
        m.pop("src", None)
        topic = T.tc(m["sat"])
    else:
        topic = T.link(m["a"], m["b"])
    return topic, S.parse(topic, m)


class ReplayTwin:
    def __init__(self, bus: Bus, twin: SyntheticTwin, sats: list[str], start_t: float):
        self.bus, self.twin, self.sats = bus, twin, set(sats)
        self.t_shift = start_t - twin.t0_unix  # engine time -> published time
        self.inbox: queue.Queue = queue.Queue()
        self.own_ids: set[str] = set()  # commands we published: do not execute twice
        self.applied_ids: set[str] = set()  # commands we received: do not republish
        self.stats = {"published": 0, "applied": 0, "rejected_args": 0}

    def on_tc(self, topic: str, msg: S.Telecommand):
        if msg.id not in self.own_ids and msg.sat in self.sats:
            self.inbox.put(msg)

    def apply_inbox(self):
        while True:
            try:
                msg = self.inbox.get_nowait()
            except queue.Empty:
                return
            if msg.id in self.applied_ids:
                continue
            self.applied_ids.add(msg.id)
            engine_t = self.twin.t0_unix + self.twin.t * self.twin.dt
            cmd = {
                "v": 1,
                "id": msg.id,
                "t": engine_t,
                "sat": msg.sat,
                "origin": msg.origin,
                "cmd": msg.cmd,
                "args": dict(msg.args),
            }
            if S.args_problems(msg.cmd, msg.args):
                self.stats["rejected_args"] += 1
                cmd["_auth_ok"] = False  # a malformed command is rejected on board (cmd_rej rises)
            elif self.refused_in_safe(msg.sat, msg.cmd, msg.args):
                self.stats["refused_in_safe"] = self.stats.get("refused_in_safe", 0) + 1
                cmd["_auth_ok"] = False
            self.twin.apply(cmd)
            self.stats["applied"] += 1
            log.info("applied %s %s %s from %s", msg.sat, msg.cmd, msg.args, msg.origin)

    def refused_in_safe(self, sat: str, cmd: str, args: dict) -> bool:
        """interfaces.md 3.2: in SAFE, HEATER on (any origin) and SET_MODE other than NOMINAL/SAFE are
        refused. Decided from the command and the OBC mode only, never from who sent it."""
        if self.twin.mode[self.twin.idx[sat]] != MODE_SAFE:
            return False
        if cmd == "HEATER":
            return bool(args.get("on"))
        if cmd == "SET_MODE":
            return args.get("mode") not in ("NOMINAL", "SAFE")
        return False

    def _apply_safe_rule_to_schedule(self):
        """The engine's own commands due this tick (background ground traffic) obey the same rule."""
        t = self.twin.t
        for k, m in self.twin.queue:
            if k == t and self.refused_in_safe(m["sat"], m["cmd"], m.get("args") or {}):
                m["_auth_ok"] = False

    def step(self):
        self.apply_inbox()
        self._apply_safe_rule_to_schedule()
        tick = self.twin.t
        self.twin.step(1)
        for kind, raw in self.twin.messages_at(tick):
            if kind == "tc" and raw["id"] in self.applied_ids:
                continue  # received over the bus; already visible there
            sat = raw.get("sat")
            if kind in ("tm", "tc") and sat not in self.sats:
                continue  # hardware slot: the bridge publishes it
            topic, model = to_contract(kind, raw, self.t_shift)
            if kind == "tc":
                self.own_ids.add(model.id)
            self.bus.publish(topic, model)
            self.stats["published"] += 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--sats", default="A-L", help="satellites to publish, e.g. A-L or A,C-L")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--duration", type=int, default=3600, help="seconds of simulated time")
    ap.add_argument("--speed", type=float, default=1.0, help="ticks per wall-clock second")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if a.verbose else logging.WARNING, format="%(asctime)s %(message)s"
    )

    sats = parse_sats(a.sats)
    twin = build_twin(a.seed, a.duration)
    bus = Bus(f"replay-twin-{a.seed}", a.host, a.port)
    rt = ReplayTwin(bus, twin, sats, start_t=time.time())
    bus.subscribe(f"{T.PREFIX}/sat/+/tc", rt.on_tc)
    if not bus.start():
        print(f"no broker at {a.host}:{a.port} (make up)", file=sys.stderr)
        return 1
    print(
        f"replay twin: {len(sats)} satellites ({','.join(sats)}), seed {a.seed}, {a.speed:g} tick/s, "
        f"{a.duration} s. Simulated (src twin). Ctrl-C to stop.",
        flush=True,
    )
    period, nxt = 1.0 / a.speed, time.monotonic()
    try:
        while twin.t < twin.T:
            rt.step()
            if twin.t % 60 == 0:
                print(
                    f"t={twin.t:5d}s  published={rt.stats['published']}  applied={rt.stats['applied']}",
                    flush=True,
                )
            nxt += period
            time.sleep(max(0.0, nxt - time.monotonic()))
    except KeyboardInterrupt:
        pass
    finally:
        bus.stop()
    print(f"replay twin stopped at t={twin.t}s: {rt.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
