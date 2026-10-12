"""Understand stage, offline part: episode files -> arrays, normalisation, engineering model, couplings.

Input is any Section 6.3 episode (twin, recorder or converted dataset). Per satellite and tick we
build 27 channels (graph.FLAT): the Section 6.2 telemetry fields plus command statistics derived
from the telecommand log (counts per origin and per command type). Only observable data is used:
labels.json / ml_labels.json feed the training targets, never the model inputs.

    python -m ml.features            # build data/cache/<split>.npz for every generated split
"""

from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from ml import config, graph, schema

MAX_LAG = 15


# ----------------------------------------------------------------------------- episode -> arrays
def episode_arrays(path) -> dict:
    """Arrays of one episode. Label files are optional (S2): without them (a recorder or hardware
    episode) the time base and satellite list come from the telemetry, and the truth arrays are empty."""
    from pathlib import Path

    labelled = (Path(path) / "labels.json").exists() and (Path(path) / "ml_labels.json").exists()
    ep = schema.read_episode(path, with_labels=labelled)
    tel, cmds, links = ep["telemetry"], ep["commands"], ep["links"]
    if labelled:
        ml = ep["ml_labels"]
        sats = ep["labels"].satellites
        S, T, dt, t0 = len(sats), ml.n_ticks, ml.dt_s, ml.t0
    else:
        ml = None
        sats = sorted(tel.sat.unique())
        dt = 1.0
        t0 = float(tel.t.min())
        S, T = len(sats), int(round((float(tel.t.max()) - t0) / dt)) + 1
    sidx = {s: i for i, s in enumerate(sats)}
    raw = np.zeros((T, S, len(graph.FLAT)), np.float32)
    tel = tel.assign(tick=np.rint((tel.t - t0) / dt).astype(int), si=tel.sat.map(sidx))
    col = {c: i for i, c in enumerate(graph.FLAT)}
    direct = {
        "thermal": ["temp_c", "heater_on"],
        "eps": ["soc_pct", "bus_v", "load_w", "solar_w", "current_ma"],
        "adcs": ["att_err_deg", "wheel_rpm_0", "wheel_rpm_1", "wheel_rpm_2", "rate_dps"],
        "comms": ["ground_contact", "rssi_dbm"],
        "sun": ["level", "eclipse"],
    }
    for sub, fields in direct.items():
        d = tel[tel["sub"] == sub]
        for f in fields:
            grid = np.full((T, S), np.nan, np.float32)
            grid[d.tick.to_numpy(), d.si.to_numpy()] = d[f].to_numpy(np.float32)
            raw[:, :, col[f]] = _ffill(grid)
    d = tel[tel["sub"] == "comms"]
    peers = np.zeros((T, S), np.float32)
    peers[d.tick.to_numpy(), d.si.to_numpy()] = [
        0 if not p or p == "None" else len(p.split(",")) for p in d.isl_peers
    ]
    raw[:, :, col["n_isl_peers"]] = peers
    d = tel[tel["sub"] == "obc"]
    for name, mode in (("mode_safe", "SAFE"), ("mode_quarantined", "QUARANTINED")):
        g = np.zeros((T, S), np.float32)
        g[d.tick.to_numpy(), d.si.to_numpy()] = (d["mode"] == mode).to_numpy(np.float32)
        raw[:, :, col[name]] = g
    for name, field in (("d_cmd_ok", "cmd_ok"), ("d_cmd_rej", "cmd_rej")):
        g = np.full((T, S), np.nan, np.float32)
        g[d.tick.to_numpy(), d.si.to_numpy()] = d[field].to_numpy(np.float32)
        g = _ffill(g)
        raw[:, :, col[name]] = np.clip(np.diff(g, axis=0, prepend=g[:1]), 0, None)
    if len(cmds):
        c = cmds.assign(
            tick=np.clip(np.rint((cmds.t - t0) / dt).astype(int), 0, T - 1), si=cmds.sat.map(sidx)
        )
        kind = c.origin.map(schema.origin_kind)
        args_on = c.args.map(_heater_on)  # M4: parse the JSON, never match its spelling
        for name, m in (
            ("tc_ground", kind == "ground"),
            ("tc_isl", kind == "isl"),
            ("tc_unknown", kind.isin(["unknown", "none"])),
            ("tc_heater_on", (c.cmd == "HEATER") & args_on),
            ("tc_set_mode", c.cmd == "SET_MODE"),
            ("tc_ping", c.cmd == "PING"),
        ):
            g = np.zeros((T, S), np.float32)
            np.add.at(g, (c.tick[m].to_numpy(), c.si[m].to_numpy()), 1.0)
            raw[:, :, col[name]] = g
    pairs = graph.sat_pairs(S)
    pidx = {(sats[a], sats[b]): k for k, (a, b) in enumerate(pairs)}
    lk = np.zeros((T, len(pairs)), bool)
    li = links.assign(
        tick=np.rint((links.t - t0) / dt).astype(int), k=[pidx[(a, b)] for a, b in zip(links.a, links.b)]
    )
    lk[li.tick.to_numpy(), li.k.to_numpy()] = li.enabled.to_numpy(bool)
    truth = truth_arrays(ml, sats) if ml is not None else empty_truth(len(sats))
    return dict(raw=raw, links=lk, **truth)


def _heater_on(args) -> bool:
    """True for HEATER args {"on": true}, whether stored as a dict, compact or spaced JSON."""
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError:
            return False
    return isinstance(args, dict) and args.get("on") is True


def empty_truth(S: int) -> dict:
    return dict(
        crit=np.full(S, -1), onset=np.full(S, -1), malicious=np.full(S, -1), affected=np.full((S, 6), 10**6)
    )


def _ffill(g):
    out = pd.DataFrame(g).ffill().bfill().to_numpy(np.float32)
    return np.nan_to_num(out)


def truth_arrays(ml: schema.MLLabels, sats: list[str]) -> dict:
    """Per-satellite ground truth in ticks (-1 = none)."""
    S = len(sats)
    crit, onset, mal = np.full(S, -1), np.full(S, -1), np.full(S, -1)
    affected = np.full((S, 6), 10**6)
    for i, s in enumerate(sats):
        # interfaces.md 4: only satellites with an anomaly need to be listed
        st = ml.satellites.get(s) or schema.SatelliteTruth()
        crit[i] = -1 if st.critical_t is None else int(round(st.critical_t))
        onset[i] = -1 if st.onset_t is None else int(round(st.onset_t))
        mal[i] = -1 if st.malicious is None else int(st.malicious)
        for sub, tt in st.affected_t.items():
            affected[i, graph.SUBSYSTEM_TYPES.index(sub)] = int(round(tt))
    return dict(crit=crit, onset=onset, malicious=mal, affected=affected)


# ----------------------------------------------------------------------------- cache
def build_cache(split: str) -> dict:
    root = config.EPISODES / split
    index = pd.read_csv(root / "index.csv")
    arrays = [episode_arrays(root / e) for e in index.episode_id]
    out = {k: np.stack([a[k] for a in arrays]) for k in arrays[0]}
    out["raw"] = out["raw"].astype(np.float16)
    config.CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(config.CACHE / f"{split}.npz", **out)
    index.to_csv(config.CACHE / f"{split}_index.csv", index=False)
    return out


def load_cache(split: str) -> tuple[dict, pd.DataFrame]:
    z = np.load(config.CACHE / f"{split}.npz")
    return {k: z[k] for k in z.files}, pd.read_csv(config.CACHE / f"{split}_index.csv")


# ----------------------------------------------------------------------------- understanding
class Understanding:
    """Normaliser, engineering model g_ij (ridge, per engineering edge type) and learned couplings."""

    def fit(self, raw_benign: np.ndarray, raw_attack: np.ndarray, lam: float = 1.0):
        X = raw_benign.astype(np.float32)  # [n, T, S, 27]
        self.mu = X.mean((0, 1, 2))
        self.sd = X.std((0, 1, 2)) + 1e-3
        self.dsd = np.diff(X, axis=1).std((0, 1, 2)) + 1e-3
        Z = (X - self.mu) / self.sd
        self.ridge = {}
        for a, b in graph.E_ENG:
            (a0, a1), (b0, b1) = graph.SLICE[a], graph.SLICE[b]
            za, zb = Z[..., a0:a1], Z[..., b0:b1]
            Xc = np.concatenate([za[:, :-1], zb[:, :-1], np.ones_like(zb[:, :-1, :, :1])], -1).reshape(
                -1, (a1 - a0) + (b1 - b0) + 1
            )
            Yc = (zb[:, 1:] - zb[:, :-1]).reshape(-1, b1 - b0)
            Xl = np.concatenate([za, np.ones_like(za[..., :1])], -1).reshape(-1, a1 - a0 + 1)
            Yl = zb.reshape(-1, b1 - b0)
            A, rsd = _ridge(Xc, Yc, lam)
            B, lsd = _ridge(Xl, Yl, lam)
            self.ridge[(a, b)] = dict(A=A, B=B, rsd=rsd, lsd=lsd)
        self.couplings = self._couplings(raw_attack)
        return self

    def _couplings(self, raw):
        Z = (raw.astype(np.float32) - self.mu) / self.sd
        dZ = np.diff(Z, axis=1)
        act = {t: np.sqrt((dZ[..., slice(*graph.SLICE[t])] ** 2).mean(-1)) for t in graph.SUBSYSTEM_TYPES}
        out = {}
        for a, b in sorted(set(graph.E_ENG) | set(graph.E_SPARTA)):
            x, y = act[a], act[b]  # [n, T-1, S]
            x = (x - x.mean(1, keepdims=True)) / (x.std(1, keepdims=True) + 1e-6)
            y = (y - y.mean(1, keepdims=True)) / (y.std(1, keepdims=True) + 1e-6)
            T = x.shape[1]
            c = [float((x[:, : T - L] * y[:, L:]).mean()) for L in range(MAX_LAG + 1)]
            out[f"{a}->{b}"] = (float(np.clip(max(c), 0, 1)), int(np.argmax(c)))
        return out

    def save(self, path):
        arrays = dict(mu=self.mu, sd=self.sd, dsd=self.dsd)
        for (a, b), r in self.ridge.items():
            for k, v in r.items():
                arrays[f"{a}->{b}:{k}"] = v
        np.savez(path, **arrays)
        path.with_suffix(".json").write_text(json.dumps(self.couplings, indent=1))

    def load(self, path):
        z = np.load(path)
        self.mu, self.sd, self.dsd = z["mu"], z["sd"], z["dsd"]
        self.ridge = {}
        for a, b in graph.E_ENG:
            self.ridge[(a, b)] = {k: z[f"{a}->{b}:{k}"] for k in ("A", "B", "rsd", "lsd")}
        self.couplings = {k: tuple(v) for k, v in json.loads(path.with_suffix(".json").read_text()).items()}
        return self


def _ridge(X, Y, lam):
    W = np.linalg.solve(X.T @ X + lam * np.eye(X.shape[1]), X.T @ Y)
    return W.astype(np.float32), ((Y - X @ W).std(0) + 1e-3).astype(np.float32)


def main():
    for split in config.load("synthetic")["splits"]:
        if (config.EPISODES / split / "index.csv").exists():
            t0 = time.time()
            out = build_cache(split)
            print(f"{split:9s} cache {out['raw'].shape} in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
