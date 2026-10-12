"""Online forecaster: the ML entry point for cassandra/forecast (CONTEXT 5.4, Section 6.2).

Feed it the messages CASSANDRA already receives (telemetry, telecommands, link status), call
`tick(t)` once per second, and publish what it returns on aurora/v1/core/forecast/{sat}.

    fc = OnlineForecaster.from_models()          # loads data/models/full_s*.pt (ensemble)
    fc.on_telemetry(msg); fc.on_command(msg); fc.on_link(msg)
    forecasts = fc.tick(t)                       # list of Section 6.2 forecast messages

The per-tick frame is built exactly like ml/features.py builds it offline (same channels, hold
last value on gaps, counter increments, command counts per origin and type);
tests/test_model_features_metrics.py
checks that both paths give identical model inputs. If no model is loaded, the Phase-2 trajectory
forecaster is used as the fallback (model = "trajectory-v0"); it ignores a limit until its channel
has arrived for a full slope window. Trained models and their calibration.json: `make train`.
Messages follow docs/interfaces.md 3.5: what a model does not know is null (D4), and warming_up is
true until the 30 s window is full (the core should not act on those).
"""

from __future__ import annotations

import json
import logging
from collections import deque

import numpy as np
import torch

from ml import config, schema
from ml import graph as G
from ml.frames import CH, LIMIT_CHANNELS, SLOPE_W, FrameBuilder
from ml.frames import episode_messages as episode_messages
from ml.model import HORIZONS

log = logging.getLogger(__name__)


def load_calibration() -> dict | None:
    """data/models/calibration.json (M5): written by eval.harness, shipped by `make train`."""
    path = config.MODELS / "calibration.json"
    return json.loads(path.read_text()) if path.exists() else None


class OnlineForecaster:
    def __init__(
        self,
        models: list,
        satellites: list[str],
        temperature: float = 1.0,
        window: int = 30,
        model_name: str = "tgn-v1",
    ):
        self.models = models
        self.temperature = temperature
        self.window = window
        self.model_name = model_name
        self.frames = FrameBuilder(satellites)
        self.buffer: deque = deque(maxlen=window)
        self.sats = list(satellites)

    @classmethod
    def from_models(cls, names: list[str] | None = None, allow_uncalibrated: bool = False):
        """The ensemble in data/models with its calibration.json. Without models: the trajectory
        fallback (logged). With models but no calibration.json: an error (M5), unless
        allow_uncalibrated=True, which logs a WARNING and labels forecasts "tgn-v1-uncalibrated"."""
        from ml.train import load_model

        names = names or sorted(p.stem for p in config.MODELS.glob("full_s*.pt"))
        sats, window = config.load("synthetic")["satellites"], config.load("model")["window_s"]
        if not names:
            log.warning("no trained models in %s: trajectory fallback (run `make train`)", config.MODELS)
            return cls([], sats, 1.0, window)
        cal = load_calibration()
        if cal is None and not allow_uncalibrated:
            raise FileNotFoundError(
                f"trained models in {config.MODELS} but no calibration.json: run `make evaluate` "
                "(or `make train`, which writes temperature calibration); "
                "allow_uncalibrated=True runs at T = 1.0"
            )
        models = [load_model(n) for n in names]
        if cal is None:
            log.warning("no calibration.json: P(F) is uncalibrated (T = 1.0), model tgn-v1-uncalibrated")
            return cls(models, sats, 1.0, window, "tgn-v1-uncalibrated")
        return cls(models, sats, float(cal["temperature"]), window)

    on_telemetry = property(lambda self: self.frames.on_telemetry)
    on_command = property(lambda self: self.frames.on_command)
    on_link = property(lambda self: self.frames.on_link)

    def window_tensor(self):
        frames = list(self.buffer)
        frames = [frames[0]] * (self.window - len(frames)) + frames  # left pad like offline
        return torch.tensor(np.stack(frames)[None], dtype=torch.float32)

    def push_frame(self, t=None):
        """Close the current tick: build its frame and append it to the 30 s window."""
        f, self._links = self.frames.frame(t)
        self.buffer.append(f)

    @torch.no_grad()
    def tick(self, t: float) -> list[dict]:
        self.push_frame(t)
        return self.forecast(t)

    @torch.no_grad()
    def forecast(self, t: float) -> list[dict]:
        if not self.buffer:
            return self._no_data(t)
        warming = len(self.buffer) < self.window
        if not self.models:
            return self._fallback(t, warming)
        raw, lk = self.window_tensor(), torch.tensor(self._links[None])
        outs = [m(raw, lk) for m in self.models]
        bins = torch.stack([o["bin_logits"][0] for o in outs]) / self.temperature  # [K, S, 4]
        p_members = 1 - torch.cumprod(1 - torch.sigmoid(bins), -1)
        p = 1 - torch.cumprod(1 - torch.sigmoid(bins.mean(0)), -1)
        q = p_members.std(0) if len(self.models) > 1 else None  # D4: no Q with one model
        nxt = torch.softmax(torch.stack([o["next_logits"][0] for o in outs]).mean(0), -1)
        mal = torch.sigmoid(torch.stack([o["mal_logit"][0] for o in outs]).mean(0))
        return [
            schema.forecast_message(
                s,
                t,
                dict(zip(HORIZONS, p[i].tolist())),
                None if q is None else dict(zip(HORIZONS, q[i].tolist())),
                G.SUBSYSTEM_TYPES[int(nxt[i].argmax())],
                float(mal[i]),
                model=self.model_name,
                warming_up=warming,
            )
            for i, s in enumerate(self.sats)
        ]

    def _no_data(self, t: float) -> list[dict]:
        """S4: before the first tick there is nothing to forecast from (p = 0, warming up)."""
        zero = {h: 0.0 for h in HORIZONS}
        return [
            schema.forecast_message(s, t, zero, None, None, None, model="trajectory-v0", warming_up=True)
            for s in self.sats
        ]

    def _fallback(self, t: float, warming: bool) -> list[dict]:
        from ml.baselines import trajectory_score

        crit = config.load("synthetic")["critical"]
        raw = np.stack(list(self.buffer))[None]
        out = []
        for i, s in enumerate(self.sats):
            # A channel not yet received is held at 0, which reads as SOC below SOC_min or as a jump
            # to the first real temperature. A limit counts only once its slope window is all real.
            ready = tuple(c for c in LIMIT_CHANNELS if self.frames.age[i, CH[c]] >= SLOPE_W)
            x = raw[:, :, i : i + 1]
            p = {
                h: float(trajectory_score(x, crit, horizon=h, w=SLOPE_W, channels=ready)[0, -1, 0])
                for h in HORIZONS
            }
            # the trajectory forecaster has no ensemble, next-subsystem or malicious head: null (D4)
            out.append(
                schema.forecast_message(s, t, p, None, None, None, model="trajectory-v0", warming_up=warming)
            )
        return out
