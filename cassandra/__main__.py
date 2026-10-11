"""CASSANDRA core main loop.

    python -m cassandra [--config cassandra/config.yaml] [--host localhost] [--port 1883]

Observe (ingest) -> Understand/Forecast (monitor, forecast) -> Simulate (sandbox) -> Act -> Verify
-> Explain, once per tick of message time. Subscribes only to
aurora_common.topics.CORE_ALLOWED_SUBSCRIPTIONS (never gt/ or redteam/); the Bus refuses anything else.
"""

from __future__ import annotations

import argparse
import json
import logging
import queue
import statistics
import sys
import time

from aurora_common import schemas as S
from aurora_common import topics as T
from aurora_common.bus import Bus
from cassandra import config, explain
from cassandra.act import Outbox, Responder
from cassandra.forecast import ROOT, ForecastStage
from cassandra.ingest import Ingest
from cassandra.monitor import Monitor
from cassandra.sandbox import Sandbox
from cassandra.world import WorldTick

log = logging.getLogger("cassandra")


class Core:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.profiles = config.profiles(cfg)
        self.ingest = Ingest(self.profiles, cfg)
        self.monitor = Monitor(self.profiles, cfg)
        self.forecast = ForecastStage.build(self.profiles, cfg)
        self.sandbox = Sandbox(self.profiles, cfg)
        self.responder = Responder(self.profiles, cfg, self.sandbox, act_tau=self._ensemble_tau())
        self.latency_ms: list[float] = []
        self.plan_ms: list[float] = []

    def _ensemble_tau(self) -> float | None:
        if self.forecast.ensemble is None:
            return None
        path = (ROOT / self.cfg["forecast"]["ensemble"]["models_dir"] / "calibration.json").resolve()
        return json.loads(path.read_text()).get("tau") if path.exists() else None

    def startup(self, t: float) -> Outbox:
        out = Outbox(states=self.responder.initial_states(t))
        out.events += [explain.note(t, n) for n in self.forecast.startup_notes]
        out.events.append(
            S.Event(
                t=t,
                level="info",
                kind="startup",
                text=f"sandbox shadow model: {self.sandbox.engine.kind} engine hooks; "
                f"subscriptions: {', '.join(T.CORE_ALLOWED_SUBSCRIPTIONS)}",
            )
        )
        return out

    def handle_tick(self, tick: WorldTick) -> tuple[Outbox, list[S.Forecast]]:
        t0 = time.perf_counter()
        events = self.monitor.step(tick)
        forecasts = self.forecast.step(tick, self.monitor.untrusted)
        out = self.responder.step(tick, forecasts, events, self.monitor.untrusted)
        now = tick.t + 1.0
        out.events = [explain.note(now, n) for n in tick.notes] + out.events
        self.latency_ms.append((time.perf_counter() - t0) * 1000)
        self.plan_ms += out.plan_ms
        return out, [r.to_message() for r in forecasts.values()]

    def latency_line(self) -> str:
        if not self.latency_ms:
            return "no ticks yet"
        lat = sorted(self.latency_ms)
        p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
        plan = (
            f", plan median {statistics.median(self.plan_ms):.0f} ms over {len(self.plan_ms)}"
            if self.plan_ms
            else ""
        )
        return (
            f"tick latency median {statistics.median(lat):.1f} ms, p95 {p95:.1f} ms ({len(lat)} ticks){plan}"
        )


def publish(bus: Bus, out: Outbox, forecasts: list[S.Forecast]) -> None:
    for tc in out.tcs:
        bus.publish(T.tc(tc.sat), tc)
    for st in out.states:
        bus.publish(T.state(st.sat), st)
    for f in forecasts:
        bus.publish(T.forecast(f.sat), f)
    for a in out.actions:
        bus.publish(T.ACTION, a)
    for e in out.events:
        bus.publish(T.EVENT, e)


def make_bus(cfg: dict, inbox: queue.Queue, host: str, port: int) -> Bus:
    """The core's only bus connection: subscriptions limited to CORE_ALLOWED_SUBSCRIPTIONS."""
    bus = Bus(
        cfg["bus"]["client_id"],
        host,
        port,
        allowed_subscriptions=T.CORE_ALLOWED_SUBSCRIPTIONS,
        on_invalid=lambda topic, payload, exc: inbox.put((topic, None, time.time())),
    )
    for filt in T.CORE_ALLOWED_SUBSCRIPTIONS:
        bus.subscribe(filt, lambda topic, msg: inbox.put((topic, msg, time.time())))
    return bus


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CASSANDRA core")
    ap.add_argument("--config", default=None)
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--client-id", default=None, help="MQTT client id (default: config bus.client_id)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if a.verbose else logging.WARNING, format="%(asctime)s %(name)s %(message)s"
    )
    cfg = config.load(a.config)
    if a.client_id:
        cfg["bus"]["client_id"] = a.client_id
    host, port = a.host or cfg["bus"]["host"], a.port or cfg["bus"]["port"]

    core = Core(cfg)
    inbox: queue.Queue = queue.Queue()
    bus = make_bus(cfg, inbox, host, port)
    if not bus.start():
        print(f"waiting for the broker at {host}:{port} (make up) ...", file=sys.stderr)
    publish(bus, core.startup(time.time()), [])
    print(f"CASSANDRA core running: {len(core.profiles)} satellites. Ctrl-C to stop.", flush=True)
    for n in core.forecast.startup_notes:
        print("  " + n.text, flush=True)
    last_stats = time.time()
    try:
        while True:
            try:
                topic, msg, wall = inbox.get(timeout=0.05)
                while True:
                    if msg is None:
                        core.ingest.feed_invalid(topic, wall)
                    else:
                        core.ingest.feed(topic, msg, wall)
                    topic, msg, wall = inbox.get_nowait()
            except queue.Empty:
                pass
            for tick in core.ingest.poll(time.time()):
                out, forecasts = core.handle_tick(tick)
                publish(bus, out, forecasts)
                for e in out.events:
                    if e.level != "info" or e.kind in ("heal", "startup"):
                        print(e.text, flush=True)
            if time.time() - last_stats >= 30:
                last_stats = time.time()
                print("  [stats] " + core.latency_line(), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        bus.stop()
        print("CASSANDRA core stopped. " + core.latency_line())
    return 0


if __name__ == "__main__":
    sys.exit(main())
