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
forecaster is used as the fallback (model = "trajectory-v0").
"""

from __future__ import annotations

import json
from collections import deque

import numpy as np
import torch

from ml import config, schema
from ml import graph as G
from ml.model import HORIZONS

CH = {c: i for i, c in enumerate(G.FLAT)}
DIRECT = {
    "thermal": ["temp_c", "heater_on"],
    "eps": ["soc_pct", "bus_v", "load_w", "solar_w", "current_ma"],
    "adcs": ["att_err_deg", "wheel_rpm_0", "wheel_rpm_1", "wheel_rpm_2", "rate_dps"],
    "comms": ["ground_contact", "rssi_dbm"],
    "sun": ["level", "eclipse"],
}


class FrameBuilder:
    """Accumulates Section 6.2 messages into the 27-channel frame of one tick."""

    def __init__(self, satellites: list[str]):
        self.sats = list(satellites)
        self.idx = {s: i for i, s in enumerate(self.sats)}
        S = len(self.sats)
        self.last = np.zeros((S, len(G.FLAT)), np.float32)  # held telemetry values
        self.seen = np.zeros((S, len(G.FLAT)), bool)
        self.counters = np.full((S, 2), np.nan)  # cmd_ok, cmd_rej
        self.prev_counters = np.full((S, 2), np.nan)
        self.cmd_counts = np.zeros((S, 6), np.float32)
        pairs = G.sat_pairs(S)
        self.pair_idx = {(self.sats[a], self.sats[b]): k for k, (a, b) in enumerate(pairs)}
        self.links = np.zeros(len(pairs), bool)

    def on_telemetry(self, msg: dict):
        s, sub, f = self.idx[msg["sat"]], msg["sub"], msg["fields"]
        if sub in DIRECT:
            for name in DIRECT[sub]:
                key = name
                val = f.get(key)
                if name.startswith("wheel_rpm_"):
                    w = f.get("wheel_rpm")
                    val = None if w is None else w[int(name[-1])]
                if val is not None:
                    self.last[s, CH[name]] = float(val)
                    self.seen[s, CH[name]] = True
        if sub == "comms":
            peers = f.get("isl_peers") or []
            peers = peers.split(",") if isinstance(peers, str) else peers
            self.last[s, CH["n_isl_peers"]] = len([p for p in peers if p and p != "None"])
        if sub == "obc":
            self.last[s, CH["mode_safe"]] = float(f.get("mode") == "SAFE")
            self.last[s, CH["mode_quarantined"]] = float(f.get("mode") == "QUARANTINED")
            self.counters[s] = [f.get("cmd_ok", np.nan), f.get("cmd_rej", np.nan)]

    def on_command(self, msg: dict):
        s = self.idx[msg["sat"]]
        kind = schema.origin_kind(msg.get("origin"))
        args = msg.get("args") or {}
        if isinstance(args, str):
            args = json.loads(args)
        flags = [
            kind == "ground",
            kind == "isl",
            kind in ("unknown", "none"),
            msg["cmd"] == "HEATER" and bool(args.get("on")),
            msg["cmd"] == "SET_MODE",
            msg["cmd"] == "PING",
        ]
        self.cmd_counts[s] += np.array(flags, np.float32)

    def on_link(self, msg: dict):
        k = self.pair_idx.get((msg["a"], msg["b"]))
        if k is not None:
            self.links[k] = bool(msg["enabled"])

    def frame(self) -> tuple[np.ndarray, np.ndarray]:
        f = self.last.copy()
        cur = np.where(np.isnan(self.counters), self.prev_counters, self.counters)
        prev = np.where(np.isnan(self.prev_counters), cur, self.prev_counters)
        d = np.nan_to_num(np.clip(cur - prev, 0, None))
        f[:, CH["d_cmd_ok"]], f[:, CH["d_cmd_rej"]] = d[:, 0], d[:, 1]
        names = ["tc_ground", "tc_isl", "tc_unknown", "tc_heater_on", "tc_set_mode", "tc_ping"]
        for j, name in enumerate(names):
            f[:, CH[name]] = self.cmd_counts[:, j]
        self.prev_counters = cur
        self.counters = np.full_like(self.counters, np.nan)
        self.cmd_counts[:] = 0
        return f, self.links.copy()


class OnlineForecaster:
    def __init__(self, models: list, satellites: list[str], temperature: float = 1.0, window: int = 30):
        self.models = models
        self.temperature = temperature
        self.window = window
        self.frames = FrameBuilder(satellites)
        self.buffer: deque = deque(maxlen=window)
        self.sats = list(satellites)

    @classmethod
    def from_models(cls, names: list[str] | None = None):
        from ml.train import load_model

        names = names or sorted(p.stem for p in config.MODELS.glob("full_s*.pt"))
        models = [load_model(n) for n in names]
        temp = 1.0
        metrics = config.RESULTS / "eval" / "metrics.json"
        if metrics.exists():
            temp = json.loads(metrics.read_text()).get("temperature", 1.0)
        return cls(models, config.load("synthetic")["satellites"], temp, config.load("model")["window_s"])

    on_telemetry = property(lambda self: self.frames.on_telemetry)
    on_command = property(lambda self: self.frames.on_command)
    on_link = property(lambda self: self.frames.on_link)

    def window_tensor(self):
        frames = list(self.buffer)
        frames = [frames[0]] * (self.window - len(frames)) + frames  # left pad like offline
        return torch.tensor(np.stack(frames)[None], dtype=torch.float32)

    def push_frame(self):
        """Close the current tick: build its frame and append it to the 30 s window."""
        f, self._links = self.frames.frame()
        self.buffer.append(f)

    @torch.no_grad()
    def tick(self, t: float) -> list[dict]:
        self.push_frame()
        return self.forecast(t)

    @torch.no_grad()
    def forecast(self, t: float) -> list[dict]:
        raw, lk = self.window_tensor(), torch.tensor(self._links[None])
        if not self.models:
            return self._fallback(t)
        outs = [m(raw, lk) for m in self.models]
        bins = torch.stack([o["bin_logits"][0] for o in outs]) / self.temperature  # [K, S, 4]
        p_members = 1 - torch.cumprod(1 - torch.sigmoid(bins), -1)
        p = 1 - torch.cumprod(1 - torch.sigmoid(bins.mean(0)), -1)
        q = p_members.std(0) if len(self.models) > 1 else torch.zeros_like(p)
        nxt = torch.softmax(torch.stack([o["next_logits"][0] for o in outs]).mean(0), -1)
        mal = torch.sigmoid(torch.stack([o["mal_logit"][0] for o in outs]).mean(0))
        return [
            schema.forecast_message(
                s,
                t,
                dict(zip(HORIZONS, p[i].tolist())),
                dict(zip(HORIZONS, q[i].tolist())),
                G.SUBSYSTEM_TYPES[int(nxt[i].argmax())],
                float(mal[i]),
            )
            for i, s in enumerate(self.sats)
        ]

    def _fallback(self, t: float) -> list[dict]:
        from ml.baselines import trajectory_score

        crit = config.load("synthetic")["critical"]
        raw = np.stack(list(self.buffer))[None]
        out = []
        for i, s in enumerate(self.sats):
            p = {
                h: float(trajectory_score(raw[:, :, i : i + 1], crit, horizon=h)[0, -1, 0]) for h in HORIZONS
            }
            msg = schema.forecast_message(s, t, p, {h: 0.0 for h in HORIZONS}, "thermal", 0.0)
            msg["model"] = "trajectory-v0"
            out.append(msg)
        return out


def episode_messages(ep: dict):
    """Replay a Section 6.3 episode as per-tick Section 6.2 messages (for tests and demos)."""
    tel, cmds, links = ep["telemetry"], ep["commands"], ep["links"]
    tel_by_t = {t: g for t, g in tel.groupby("t")}
    cmd_by_t = {t: g for t, g in cmds.groupby("t")} if len(cmds) else {}
    link_by_t = {t: g for t, g in links.groupby("t")}
    for t in sorted(tel_by_t):
        msgs = []
        for r in tel_by_t[t].itertuples(index=False):
            fields = {k: getattr(r, k) for k in schema.FIELDS[r.sub] if k != "wheel_rpm"}
            if r.sub == "adcs":
                fields["wheel_rpm"] = [r.wheel_rpm_0, r.wheel_rpm_1, r.wheel_rpm_2]
            msgs.append(
                (
                    "tm",
                    {
                        "v": 1,
                        "sat": r.sat,
                        "src": r.src,
                        "t": r.t,
                        "seq": r.seq,
                        "sub": r.sub,
                        "fields": fields,
                    },
                )
            )
        for r in (cmd_by_t.get(t, []).itertuples(index=False) if t in cmd_by_t else []):
            msgs.append(
                (
                    "tc",
                    {
                        "v": 1,
                        "id": r.id,
                        "t": r.t,
                        "sat": r.sat,
                        "origin": r.origin,
                        "cmd": r.cmd,
                        "args": r.args,
                    },
                )
            )
        for r in link_by_t.get(t, []).itertuples(index=False):
            msgs.append(
                (
                    "link",
                    {
                        "v": 1,
                        "t": r.t,
                        "a": r.a,
                        "b": r.b,
                        "feasible": r.feasible,
                        "enabled": r.enabled,
                        "range_km": r.range_km,
                    },
                )
            )
        yield t, msgs
