"""Closed loop without a broker: replay plant -> ingest -> core -> core telecommands back into the plant."""

from __future__ import annotations

import uuid

from aurora_common import schemas as S
from aurora_common import topics as T
from cassandra import config
from cassandra.__main__ import Core
from scripts.replay_twin import ReplayTwin, build_twin

T0 = 1_800_000_000.0


class Loop:
    def __init__(self, seed=3, duration_s=600, cfg_overrides=None):
        # trajectory forecaster unless a test asks for the ensemble: results must not depend on
        # whether trained models happen to be in data/models
        base = {"forecast": {"ensemble": {"enabled": False}}}
        self.cfg = config.load(overrides=base)
        if cfg_overrides:
            config._merge(self.cfg, cfg_overrides)
        self.core = Core(self.cfg)
        self.published: list[tuple[str, object]] = []
        loop = self

        class Pipe:
            def publish(self, topic, model):
                loop.core.ingest.feed(topic, model, model.t)

        self.plant = ReplayTwin(
            Pipe(), build_twin(seed=seed, duration_s=duration_s), list(T.SATS), start_t=T0
        )
        self.states: dict[str, list[tuple[float, str]]] = {s: [] for s in T.SATS}
        self.actions: list[S.Action] = []
        self.events: list[S.Event] = []
        self.true_temp: dict[str, list[float]] = {s: [] for s in T.SATS}

    def now(self) -> float:
        return T0 + self.plant.twin.t

    def send(self, sat, cmd, args=None, origin="ground"):
        """An outside command (the attacker), straight onto the plant's bus input."""
        tc = S.Telecommand(
            id=uuid.uuid4().hex, t=self.now(), sat=sat, origin=origin, cmd=cmd, args=args or {}
        )
        self.plant.on_tc(T.tc(sat), tc)
        self.core.ingest.feed(T.tc(sat), tc, tc.t)  # the core sees the command on the bus too

    def run(self, seconds: int):
        for _ in range(seconds):
            self.plant.step()
            tw = self.plant.twin
            for s, sat in enumerate(tw.sats):
                self.true_temp[sat].append(float(tw.temp[s]))
            for tick in self.core.ingest.poll(wall=self.now()):
                out, _ = self.core.handle_tick(tick)
                for tc in out.tcs:
                    self.plant.on_tc(T.tc(tc.sat), tc)
                    self.core.ingest.feed(T.tc(tc.sat), tc, tc.t)
                for st in out.states:
                    self.states[st.sat].append((st.t, st.state))
                self.actions += out.actions
                self.events += out.events
        return self
