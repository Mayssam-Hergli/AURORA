"""Causal event-to-frame conversion shared by replay and live inference.

Events take effect on arrival. Values are held forward only; unseen channels start at zero.
Critical channels also carry a fresh-sample mask and capped seconds since observation.
"""

from __future__ import annotations

import json

import numpy as np

from ml import graph as G
from ml import schema

CH = {c: i for i, c in enumerate(G.FLAT)}
LIMIT_CHANNELS = ("temp_c", "soc_pct", "att_err_deg")  # the three terms of F (CONTEXT Section 3)
SLOPE_W = 8  # samples in the fallback's trajectory slope (Phase-2 predictor)
DIRECT = {
    "thermal": ["temp_c", "heater_on"],
    "eps": ["soc_pct", "bus_v", "load_w", "solar_w", "current_ma"],
    "adcs": ["att_err_deg", "wheel_rpm_0", "wheel_rpm_1", "wheel_rpm_2", "rate_dps"],
    "comms": ["ground_contact", "rssi_dbm"],
    "sun": ["level", "eclipse"],
}


class FrameBuilder:
    """Accumulates Section 6.2 messages into the 33-channel frame of one tick."""

    def __init__(self, satellites: list[str]):
        self.sats = list(satellites)
        self.idx = {s: i for i, s in enumerate(self.sats)}
        S = len(self.sats)
        self.last = np.zeros((S, len(G.FLAT)), np.float32)  # held telemetry values
        self.seen = np.zeros((S, len(G.FLAT)), bool)
        self.age = np.zeros((S, len(G.FLAT)), np.int64)  # frames closed since the channel first arrived
        self.observed_t = np.full((S, len(G.FLAT)), -np.inf)
        self.fresh = np.zeros((S, len(G.FLAT)), bool)
        self.clock = None
        self.closed_t = None
        self.counters = np.full((S, 2), np.nan)  # cmd_ok, cmd_rej
        self.prev_counters = np.full((S, 2), np.nan)
        self.cmd_counts = np.zeros((S, 6), np.float32)
        pairs = G.sat_pairs(S)
        self.pair_idx = {(self.sats[a], self.sats[b]): k for k, (a, b) in enumerate(pairs)}
        self.links = np.zeros(len(pairs), bool)

    def on_telemetry(self, msg: dict):
        s, sub, f = self.idx[msg["sat"]], msg["sub"], msg["fields"]
        stamp = float(msg.get("t", (self.closed_t + 1) if self.closed_t is not None else 0.0))
        self.clock = stamp if self.clock is None else max(self.clock, stamp)
        previous = getattr(self, "sub_times", {})
        if stamp <= previous.get((s, sub), -np.inf):
            return
        self.sub_times = previous
        previous[s, sub] = stamp
        if sub in DIRECT:
            for name in DIRECT[sub]:
                key = name
                val = f.get(key)
                if name.startswith("wheel_rpm_"):
                    w = f.get("wheel_rpm")
                    val = None if w is None else w[int(name[-1])]
                if val is not None and np.isfinite(float(val)):
                    self.last[s, CH[name]] = float(val)
                    self.seen[s, CH[name]] = True
                    self.observed_t[s, CH[name]] = stamp
                    self.fresh[s, CH[name]] = True
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
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        flags = [
            kind == "ground",
            kind == "isl",
            kind in ("unknown", "none"),
            msg["cmd"] == "HEATER" and args.get("on") is True,
            msg["cmd"] == "SET_MODE",
            msg["cmd"] == "PING",
        ]
        self.cmd_counts[s] += np.array(flags, np.float32)

    def on_link(self, msg: dict):
        k = self.pair_idx.get((msg["a"], msg["b"]))
        if k is not None:
            self.links[k] = bool(msg["enabled"])

    def frame(self, t: float | None = None) -> tuple[np.ndarray, np.ndarray]:
        if t is None:
            t = max(self.clock or 0.0, (self.closed_t + 1) if self.closed_t is not None else 0.0)
        self.closed_t = t
        f = self.last.copy()
        for name in LIMIT_CHANNELS:
            i = CH[name]
            f[:, CH[name + "_missing"]] = ~self.fresh[:, i]
            f[:, CH[name + "_age_s"]] = np.minimum(600.0, np.maximum(0.0, t - self.observed_t[:, i]))
        self.fresh[:] = False
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
        self.age += self.seen
        return f, self.links.copy()


def episode_messages(ep: dict, t0=None, n_ticks=None, dt=1.0):
    """Replay a Section 6.3 episode as per-tick Section 6.2 messages (for tests and demos)."""
    tel, cmds, links = ep["telemetry"], ep["commands"], ep["links"]
    if t0 is None:
        t0 = min(float(d.t.min()) for d in (tel, links, cmds) if len(d))
    if n_ticks is None:
        n_ticks = int(np.floor((max(float(d.t.max()) for d in (tel, links, cmds) if len(d)) - t0) / dt)) + 1
    groups = []
    for d in (tel, cmds, links):
        d = d.copy()
        arrival = np.maximum(d.t, d["arrival_t"]) if "arrival_t" in d else d.t
        d["tick"] = np.floor((arrival - t0) / dt + 1e-7).astype(int)
        by_tick = {}
        for row in d.sort_values("t", kind="stable").itertuples(index=False):
            by_tick.setdefault(row.tick, []).append(row)
        groups.append(by_tick)
    tel_by_t, cmd_by_t, link_by_t = groups
    for k in range(n_ticks):
        t = t0 + k * dt
        msgs = []
        for r in tel_by_t.get(k, ()):
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
        for r in cmd_by_t.get(k, ()):
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
        for r in link_by_t.get(k, ()):
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
