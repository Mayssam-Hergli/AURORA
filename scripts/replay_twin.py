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

Red team (aurora/v1/redteam/{id}, interfaces.md 3.8): freeze_tm holds the reported value of one
field while the physics continues (B1); bias_tm adds a growing offset (B2, also F1 drift); relay_tc
makes that satellite forward a command to every neighbour over feasible, enabled links every
period_s (default 10 s, first after delay_s) with origin isl:<id> (C1). The compromise is in the
relaying satellite's software, so it keeps relaying whatever its mode; only disabled links stop it.
stop clears every effect on that satellite.

Faults (--fault TYPE:SAT@T[:value], repeatable; T in simulated seconds): F1 sensor drift on temp_c
(value = C/s, default 0.02), F2 single-tick SEU spike on temp_c (value = C, default 64), F3 battery
ageing (value = capacity factor, default 0.6), F5 heater stuck on (hardware: commands cannot clear it).
F4 (eclipse entry) needs no injection. Faults are not on the bus: the contract's red-team effects do
not include them (open question for interfaces v1.3).

Honesty: everything published here is simulated (src "twin"). It is not the Phase 3 live twin, and
the engine's offline modelling choices apply.
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


def parse_fault(spec: str) -> tuple[int, str, str, float | None]:
    """'F5:E@60' -> (60, 'F5', 'E', None); 'F1:E@30:0.03' -> (30, 'F1', 'E', 0.03)."""
    kind, rest = spec.split(":", 1)
    sat, rest = rest.split("@", 1)
    tick, _, value = rest.partition(":")
    if kind not in ("F1", "F2", "F3", "F5") or sat not in T.SATS:
        raise ValueError(f"bad --fault {spec!r}")
    return int(tick), kind, sat, (float(value) if value else None)


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
        self.stats = {"published": 0, "applied": 0, "rejected_args": 0, "relayed": 0}
        self.redteam_inbox: queue.Queue = queue.Queue()
        self.effects: dict[str, dict] = (
            {}
        )  # sat -> {"freeze": {(sub, field): v}, "bias": {...}, "relay": {...}}
        self.last_pub: dict[tuple[str, str, str], float] = {}
        self.spikes: dict[tuple[str, str, str], float] = {}  # one-tick additive spikes (F2)
        self.faults: list[tuple[int, str, str, float | None]] = []  # (tick, type, sat, value)

    # ------------------------------------------------------------------ red team and faults
    def on_redteam(self, topic: str, msg: S.RedTeam):
        self.redteam_inbox.put(msg)

    def _now_sim(self) -> float:
        return float(self.twin.t)

    def apply_redteam(self):
        while True:
            try:
                m = self.redteam_inbox.get_nowait()
            except queue.Empty:
                return
            eff = self.effects.setdefault(m.sat, {"freeze": {}, "bias": {}, "relay": None})
            a = dict(m.args)
            if m.effect == "freeze_tm":
                key = (m.sat, a["sub"], a["field"])
                eff["freeze"][(a["sub"], a["field"])] = self.last_pub.get(key)
            elif m.effect == "bias_tm":
                eff["bias"][(a["sub"], a["field"])] = (float(a["rate_per_s"]), self._now_sim())
            elif m.effect == "relay_tc":
                eff["relay"] = {
                    "cmd": a["cmd"],
                    "args": dict(a["args"]),
                    "period": float(a.get("period_s", 10.0)),
                    "next": self._now_sim() + float(a.get("delay_s", 5.0)),
                }
            elif m.effect == "stop":
                self.effects.pop(m.sat, None)
            log.info("red team %s on %s %s", m.effect, m.sat, a)

    def inject_fault(self, kind: str, sat: str, value: float | None = None):
        s = self.twin.idx[sat]
        if kind == "F1":
            eff = self.effects.setdefault(sat, {"freeze": {}, "bias": {}, "relay": None})
            eff["bias"][("thermal", "temp_c")] = (0.02 if value is None else value, self._now_sim())
        elif kind == "F2":
            self.spikes[(sat, "thermal", "temp_c")] = 64.0 if value is None else value
        elif kind == "F3":
            self.twin.e_cap_j[s] *= 0.6 if value is None else value
        elif kind == "F5":
            self.twin.fault = {"type": "F5", "sat": s, "onset": self.twin.t}
        else:
            raise ValueError(f"unknown fault {kind}")
        log.info("fault %s injected on %s", kind, sat)

    def _relays(self):
        """C1: compromised satellites forward their command to every reachable neighbour."""
        tw, t = self.twin, self.twin.t
        for sat, eff in self.effects.items():
            r = eff.get("relay")
            if not r or self._now_sim() < r["next"]:
                continue
            r["next"] += r["period"]
            x = tw.idx[sat]
            for j, nbr in enumerate(tw.sats):
                if j == x or not (tw.geo.feasible[t, x, j] and tw.isl_enabled[x, j] and tw.isl_enabled[j, x]):
                    continue
                # executed now, so messages_at(t) publishes it with origin isl:<sat>
                tw.apply({"sat": nbr, "origin": f"isl:{sat}", "cmd": r["cmd"], "args": dict(r["args"])})
                self.stats["relayed"] += 1

    def _reported(self, raw: dict) -> dict:
        """Apply freeze / bias / spike to a telemetry message before it is published."""
        sat, sub = raw["sat"], raw["sub"]
        eff = self.effects.get(sat)
        spike_keys = [k for k in self.spikes if k[0] == sat and k[1] == sub]
        if not eff and not spike_keys:
            for f, v in raw["fields"].items():
                if isinstance(v, (int, float)):
                    self.last_pub[(sat, sub, f)] = v
            return raw
        fields = dict(raw["fields"])
        for (s_, f), v in (eff or {}).get("freeze", {}).items():
            if s_ == sub and f in fields:
                if v is None:  # frozen before anything was published: hold the current value
                    eff["freeze"][(s_, f)] = v = fields[f]
                fields[f] = v
        for (s_, f), (rate, t0) in (eff or {}).get("bias", {}).items():
            if s_ == sub and f in fields:
                fields[f] = fields[f] + rate * (self._now_sim() - t0)
        for k in spike_keys:
            fields[k[2]] = fields[k[2]] + self.spikes.pop(k)
        for f, v in fields.items():
            if isinstance(v, (int, float)):
                self.last_pub[(sat, sub, f)] = v
        return {**raw, "fields": fields}

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
        self.apply_redteam()
        for f in [f for f in self.faults if f[0] <= self.twin.t]:
            self.inject_fault(f[1], f[2], f[3])
            self.faults.remove(f)
        self._relays()
        self._apply_safe_rule_to_schedule()
        tick = self.twin.t
        self.twin.step(1)
        for kind, raw in self.twin.messages_at(tick):
            if kind == "tm":
                raw = self._reported(raw)
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
    ap.add_argument(
        "--fault",
        action="append",
        default=[],
        help="inject a fault, e.g. F5:E@60 or F1:E@30:0.03 (TYPE:SAT@T[:value], T in simulated s)",
    )
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
    bus.subscribe(f"{T.PREFIX}/redteam/+", rt.on_redteam)
    for spec in a.fault:
        rt.faults.append(parse_fault(spec))
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
