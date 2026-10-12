"""Live digital twin on the bus (docs/MASTER_PLAN.md Phase 3, CONTEXT 5.3).

Steps ml.synthetic.engine.SyntheticTwin once per tick on a *clean* live scenario (no attack is
scheduled inside the engine; background ground traffic, heater tests, science sessions and benign
ISL pings still happen) and publishes what the satellites would downlink, in contract form:

    aurora/v1/sat/{id}/tm/{sub}   telemetry, src "twin", seq continuous over the whole run
    aurora/v1/link/{a}-{b}        link geometry and enable flags (all 66 pairs)
    aurora/v1/sat/{id}/tc         the background traffic the engine executes (ground, isl relays)
    aurora/v1/sat/{id}/state      "nominal" once at startup (retained); after that it is the core's
    aurora/v1/gt/event            "critical" when a satellite truly reaches F (recorder only)

and acts on what arrives:

    aurora/v1/sat/+/tc            applied through the engine (SAFE rules of interfaces.md 3.2 are in
                                  the engine); commands this service published itself are skipped
    aurora/v1/redteam/+           freeze_tm, bias_tm (applied to the reported value only, physics
                                  continues), relay_tc (forwarded to neighbours over feasible,
                                  enabled ISLs after a delay, origin isl:<sat>), stop

Runs:
    python -m twin.service                       # A-L, 1 tick/s, until Ctrl-C
    python -m twin.service --sats A,C-L          # B is the hardware board: not published
    python -m twin.service --rate 20 --duration 600 # fast-forward (message time = start + tick)

Honesty: everything published here is simulated (src "twin"). The engine is the offline synthetic
generator's physics (ml/synthetic/), not a flight model. The run is cut into segments of segment_s
(twin/config.yaml); at each boundary the physical state, mode, counters, link flags, pending
commands, sequence numbers and orbit time carry over, so subscribers see one continuous run.
"""

from __future__ import annotations

import argparse
import copy
import logging
import queue
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

import numpy as np
import yaml

from aurora_common import schemas as S
from aurora_common import topics as T
from ml import config as ml_config
from ml.synthetic.engine import MODE_NOMINAL, SyntheticTwin
from ml.synthetic.scenarios import sample_spec

log = logging.getLogger("twin")
CONFIG = Path(__file__).with_name("config.yaml")
CARRY = (  # engine state that continues across a segment boundary
    "temp",
    "thermostat",
    "heater_cmd",
    "heater_power",
    "heater_stuck",
    "science",
    "soc",
    "e_cap_j",
    "wheel_ref",
    "wheels",
    "theta_drift",
    "theta_prev",
    "extra_load",
    "mode",
    "cmd_ok",
    "cmd_rej",
    "last_origin",
    "isl_enabled",
)


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


def build_segment(tw_cfg: dict, seed: int, t0_orbit_s: float) -> SyntheticTwin:
    """One engine segment of a clean live scenario (no internally scheduled anomaly)."""
    cfg = copy.deepcopy(ml_config.load("synthetic"))
    cfg["episode_s"] = int(tw_cfg["segment_s"])
    cfg["orbit"]["orbit_speedup"] = tw_cfg["orbit_speedup"]
    spec = sample_spec(cfg, "train", seed, np.random.default_rng(seed))
    spec.type, spec.target, spec.onset_s, spec.channel, spec.n_victims = "N", None, None, None, 0
    spec.noise, spec.realnoise, spec.t0_orbit_s = tw_cfg["noise"], False, float(t0_orbit_s)
    spec.episode_id = f"live-{seed}"
    return SyntheticTwin(cfg, spec, anomalies=False)


def carry_over(old: SyntheticTwin, new: SyntheticTwin) -> SyntheticTwin:
    for name in CARRY:
        setattr(new, name, copy.deepcopy(getattr(old, name)))
    new.blocked = copy.deepcopy(getattr(old, "blocked", {}))
    new.seq0 = old.seq0 + old.T
    new.queue = [(k - old.T, m) for k, m in old.queue if k >= old.T] + new.queue
    return new


class TwinService:
    def __init__(self, bus, tw_cfg: dict, sats: list[str], start_t: float, seed: int | None = None):
        self.bus, self.cfg = bus, tw_cfg
        self.seed = tw_cfg["seed"] if seed is None else seed
        self.published = set(sats)  # satellites this service speaks for (others: hardware slots)
        self.start_t = float(start_t)
        self.session = f"twin-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(start_t))}"
        self.segment = 0
        self.twin = build_segment(tw_cfg, self.seed, 0.0)
        self.tick = 0  # ticks since start; published t = start_t + tick
        self.inbox: queue.Queue = queue.Queue()
        self.own_ids: dict[str, int] = {}  # ids we published -> tick (not re-applied when they echo)
        self.applied_ids: dict[str, int] = {}  # ids received and applied -> tick (not republished)
        self.effects: dict[str, dict] = {}  # sat -> {"freeze": {(sub, field): value}, "bias": {...}}
        self.relays: list[tuple[int, str, str, dict]] = []  # (due tick, origin sat, cmd, args)
        self.last_fields: dict[tuple[str, str], dict] = {}
        self.failed_prev = np.zeros(self.twin.S, bool)
        self.stats: Counter = Counter()

    # ------------------------------------------------------------------ bus handlers (paho thread)
    def on_tc(self, topic: str, msg: S.Telecommand):
        if msg.id not in self.own_ids:
            self.inbox.put(("tc", msg))

    def on_redteam(self, topic: str, msg: S.RedTeam):
        self.inbox.put(("redteam", msg))

    # ------------------------------------------------------------------ one tick
    def t_now(self) -> float:
        return self.start_t + self.tick

    def start(self):
        for sat in sorted(self.published):
            self.bus.publish(T.state(sat), S.NodeStateMsg(sat=sat, t=self.t_now(), state="nominal"))

    def step(self):
        self._drain_inbox()
        self._send_due_relays()
        tw = self.twin
        k = tw.t
        tw.step(1)
        for kind, raw in tw.messages_at(k):
            if kind == "tm":
                self._publish_tm(raw)
            elif kind == "tc":
                self._publish_engine_tc(raw)
            else:
                self._publish_link(raw)
        self._critical_events(k)
        self.tick += 1
        if tw.t >= tw.T:
            self._next_segment()
        if self.tick % 60 == 0:
            self._forget_old_ids()

    # ------------------------------------------------------------------ inputs
    def _drain_inbox(self):
        while True:
            try:
                kind, msg = self.inbox.get_nowait()
            except queue.Empty:
                return
            if kind == "tc":
                self._apply_tc(msg)
            else:
                self._apply_redteam(msg)

    def _engine_t(self) -> float:
        return self.twin.t0_unix + self.twin.t * self.twin.dt

    def _apply_tc(self, msg: S.Telecommand):
        if msg.id in self.applied_ids or msg.sat not in self.published:
            return  # already executed, or a hardware slot (the bridge executes it)
        self.applied_ids[msg.id] = self.tick
        cmd = {"v": 1, "id": msg.id, "t": self._engine_t(), "sat": msg.sat, "origin": msg.origin}
        cmd.update(cmd=msg.cmd, args=dict(msg.args))
        if S.args_problems(msg.cmd, msg.args):
            cmd["_auth_ok"] = False  # malformed: rejected on board, cmd_rej rises
        self.twin.apply(cmd)
        self.stats["applied"] += 1
        log.info("applied %s %s %s from %s", msg.sat, msg.cmd, dict(msg.args), msg.origin)

    def _apply_redteam(self, msg: S.RedTeam):
        if msg.sat not in self.published:
            return
        eff = self.effects.setdefault(msg.sat, {"freeze": {}, "bias": {}})
        a = msg.args
        if msg.effect == "freeze_tm":
            key = (a["sub"], a["field"])
            eff["freeze"][key] = self.last_fields.get((msg.sat, a["sub"]), {}).get(a["field"])
        elif msg.effect == "bias_tm":
            eff["bias"][(a["sub"], a["field"])] = (float(a["rate_per_s"]), self.tick)
        elif msg.effect == "relay_tc":
            delay = int(a.get("delay_s", self.cfg["relay_delay_s"]))
            self.relays.append((self.tick + delay, msg.sat, a["cmd"], dict(a["args"])))
        else:  # stop
            self.effects.pop(msg.sat, None)
            self.relays = [r for r in self.relays if r[1] != msg.sat]
        self.stats[f"redteam_{msg.effect}"] += 1
        log.info("red team %s on %s %s", msg.effect, msg.sat, dict(a))

    def _send_due_relays(self):
        due = [r for r in self.relays if r[0] <= self.tick]
        self.relays = [r for r in self.relays if r[0] > self.tick]
        tw = self.twin
        for _, origin, cmd, args in due:
            o = tw.idx[origin]
            if tw.mode[o] != MODE_NOMINAL:
                self.stats["relay_contained"] += 1  # a satellite in SAFE/quarantine forwards nothing
                continue
            usable = tw.geo.feasible[tw.t] & tw.isl_enabled & tw.isl_enabled.T
            for j in np.flatnonzero(usable[o]):
                nbr = tw.sats[j]
                msg = S.Telecommand(
                    id=uuid.uuid4().hex, t=self.t_now(), sat=nbr, origin=f"isl:{origin}", cmd=cmd, args=args
                )
                self.own_ids[msg.id] = self.tick
                self.bus.publish(T.tc(nbr), msg)
                self.stats["relayed"] += 1
                if nbr in self.published:
                    self.applied_ids[msg.id] = self.tick
                    tw.apply({**msg.model_dump(), "t": self._engine_t()})

    # ------------------------------------------------------------------ outputs
    def _publish_tm(self, raw: dict):
        sat, sub = raw["sat"], raw["sub"]
        if sat not in self.published:
            return
        fields = dict(raw["fields"])
        eff = self.effects.get(sat)
        if eff:
            for (esub, field), value in eff["freeze"].items():
                if esub == sub and field in fields:
                    if value is None:  # no value reported before the effect: freeze this one
                        value = eff["freeze"][(esub, field)] = fields[field]
                    fields[field] = value
            for (esub, field), (rate, since) in eff["bias"].items():
                if esub == sub and isinstance(fields.get(field), (int, float)):
                    fields[field] = fields[field] + rate * (self.tick - since)
        msg = S.Telemetry(sat=sat, src="twin", t=self.t_now(), seq=raw["seq"], sub=sub, fields=fields)
        self.last_fields[(sat, sub)] = msg.fields
        self.bus.publish(T.tm(sat, sub), msg)
        self.stats["tm"] += 1

    def _publish_engine_tc(self, raw: dict):
        if raw["id"] in self.applied_ids or raw["id"] in self.own_ids or raw["sat"] not in self.published:
            return
        m = {k: raw[k] for k in ("id", "sat", "origin", "cmd", "args")}
        msg = S.Telecommand(t=self.t_now(), **m)
        self.own_ids[msg.id] = self.tick
        self.bus.publish(T.tc(msg.sat), msg)
        self.stats["tc"] += 1

    def _publish_link(self, raw: dict):
        m = {k: raw[k] for k in ("a", "b", "feasible", "enabled", "range_km")}
        self.bus.publish(T.link(raw["a"], raw["b"]), S.Link(t=self.t_now(), **m))

    def _critical_events(self, k: int):
        tw = self.twin
        failed = tw.failed[k]
        for s in np.flatnonzero(failed & ~self.failed_prev):
            sat = tw.sats[s]
            if sat in self.published:
                data = {"rule": str(tw.fail_rule[k][s]), "source": "twin physics (true state)"}
                ev = S.GtEvent(t=self.t_now(), episode_id=self.session, event="critical", sat=sat, data=data)
                self.bus.publish(T.GT_EVENT, ev)
                self.stats["critical"] += 1
        self.failed_prev = failed.copy()

    # ------------------------------------------------------------------ long runs
    def _next_segment(self):
        old = self.twin
        self.segment += 1
        new = build_segment(self.cfg, self.seed + self.segment, old.spec.t0_orbit_s + old.T * old.dt)
        self.twin = carry_over(old, new)
        log.info("segment %d starts at tick %d", self.segment, self.tick)

    def _forget_old_ids(self):
        horizon = self.tick - int(self.cfg["id_memory_s"])
        for ids in (self.own_ids, self.applied_ids):
            for i in [i for i, k in ids.items() if k < horizon]:
                del ids[i]


def load_config(path: Path = CONFIG) -> dict:
    return yaml.safe_load(Path(path).read_text())


def main(argv=None) -> int:
    from aurora_common.bus import Bus

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--sats", default="A-L", help="satellites to publish, e.g. A-L or A,C-L")
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--speedup", type=float, default=None, help="orbit time compression (geometry only)")
    ap.add_argument("--rate", type=float, default=1.0, help="ticks per wall-clock second (fast-forward)")
    ap.add_argument("--duration", type=int, default=None, help="stop after this many ticks")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if a.verbose else logging.WARNING, format="%(asctime)s %(name)s %(message)s"
    )
    cfg = load_config(a.config)
    if a.speedup is not None:
        cfg["orbit_speedup"] = a.speedup
    sats = parse_sats(a.sats)
    bus = Bus(f"twin-{uuid.uuid4().hex[:6]}", a.host, a.port)
    svc = TwinService(bus, cfg, sats, start_t=time.time(), seed=a.seed)
    bus.subscribe(f"{T.PREFIX}/sat/+/tc", svc.on_tc)
    bus.subscribe(f"{T.PREFIX}/redteam/+", svc.on_redteam)
    if not bus.start():
        print(f"no broker at {a.host}:{a.port} (make up)", file=sys.stderr)
        return 1
    svc.start()
    print(
        f"twin: {len(sats)} satellites ({','.join(sats)}), seed {svc.seed}, {a.rate:g} tick/s, orbit "
        f"speedup {cfg['orbit_speedup']:g}. Simulated (src twin). Ctrl-C to stop.",
        flush=True,
    )
    period, nxt = 1.0 / a.rate, time.monotonic()
    try:
        while a.duration is None or svc.tick < a.duration:
            svc.step()
            if svc.tick % 60 == 0:
                print(f"t+{svc.tick:5d}s segment {svc.segment} {dict(svc.stats)}", flush=True)
            nxt += period
            time.sleep(max(0.0, nxt - time.monotonic()))
    except KeyboardInterrupt:
        pass
    finally:
        bus.stop()
    print(f"twin stopped after {svc.tick} ticks: {dict(svc.stats)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
