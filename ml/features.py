"""Understand stage, offline part: episode files -> arrays, normalisation, engineering model, couplings.

Input is any Section 6.3 episode (twin, recorder or converted dataset). Per satellite and tick we
build 33 channels (graph.FLAT): the Section 6.2 telemetry fields plus command statistics derived
from the telecommand log (counts per origin and per command type). Only observable data is used:
labels.json / ml_labels.json feed the training targets, never the model inputs.

    python -m ml.features            # build data/cache/<split>.npz for every generated split
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from ml import config, graph, schema

MAX_LAG = 15


# ----------------------------------------------------------------------------- episode -> arrays
def episode_arrays(path, sats: list[str] | None = None) -> dict:
    """Label files are optional (S2): without them, t0 and the satellite list come from the telemetry
    (1 s ticks), so recorder and hardware episodes can be featurised; truth arrays are then all -1."""
    path = Path(path)
    has_labels = (path / "labels.json").exists() and (path / "ml_labels.json").exists()
    ep = schema.read_episode(path, with_labels=has_labels)
    tel, cmds, links = ep["telemetry"], ep["commands"], ep["links"]
    if has_labels:
        ml = ep["ml_labels"]
        sats = sats or ep["labels"].satellites
        T, dt, t0 = ml.n_ticks, ml.dt_s, ml.t0
    else:
        ts = pd.concat([tel.t, links.t])
        ml, dt, t0 = None, 1.0, float(ts.min())
        sats = sats or sorted(tel.sat.unique())
        T = int(np.rint((ts.max() - t0) / dt)) + 1  # every rint(tick) below stays < T
    tel, cmds = tel[tel.sat.isin(sats)], cmds[cmds.sat.isin(sats)] if len(cmds) else cmds
    links = links[links.a.isin(sats) & links.b.isin(sats)]
    S = len(sats)
    from ml.frames import FrameBuilder, episode_messages

    builder = FrameBuilder(sats)
    raw_frames, link_frames = [], []
    observed = dict(telemetry=tel, commands=cmds, links=links)
    for t, messages in episode_messages(observed, t0=t0, n_ticks=T, dt=dt):
        for kind, msg in messages:
            {"tm": builder.on_telemetry, "tc": builder.on_command, "link": builder.on_link}[kind](msg)
        frame, link = builder.frame(t)
        raw_frames.append(frame)
        link_frames.append(link)
    raw, lk = np.stack(raw_frames), np.stack(link_frames)
    truth = truth_arrays(ml, sats) if ml is not None else no_truth(S)
    return dict(raw=raw, links=lk, **truth)


def heater_on_arg(args) -> bool:
    """`args` of a HEATER command as stored (JSON string, compact or spaced) or as a dict (M4). A
    malformed string is not a heater command (it was rejected on board), so it counts as False."""
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError:
            return False
    return isinstance(args, dict) and args.get("on") is True


def no_truth(S: int) -> dict:
    return dict(
        crit=np.full(S, -1), onset=np.full(S, -1), malicious=np.full(S, -1), affected=np.full((S, 6), 10**6)
    )


def _ffill(g):
    out = pd.DataFrame(g).ffill().to_numpy(np.float32)
    return np.nan_to_num(out)


def truth_arrays(ml: schema.MLLabels, sats: list[str]) -> dict:
    """Per-satellite ground truth in ticks (-1 = none)."""
    S = len(sats)
    crit, onset, mal = np.full(S, -1), np.full(S, -1), np.full(S, -1)
    affected = np.full((S, 6), 10**6)
    for i, s in enumerate(sats):
        st = ml.satellites.get(s)
        if st is None:  # interfaces.md 4: only satellites with an anomaly need to be listed
            continue
        crit[i] = -1 if st.critical_t is None else int(round(st.critical_t))
        onset[i] = -1 if st.onset_t is None else int(round(st.onset_t))
        mal[i] = -1 if st.malicious is None else int(st.malicious)
        for sub, tt in st.affected_t.items():
            affected[i, graph.SUBSYSTEM_TYPES.index(sub)] = int(round(tt))
    return dict(crit=crit, onset=onset, malicious=mal, affected=affected)


# ----------------------------------------------------------------------------- cache
def build_cache(split: str, workers: int = 1) -> dict:
    root = config.EPISODES / split
    index = pd.read_csv(root / "index.csv")
    paths = [root / e for e in index.episode_id]
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            arrays = list(pool.map(episode_arrays, paths, chunksize=4))
    else:
        arrays = [episode_arrays(p) for p in paths]
    out = {k: np.stack([a[k] for a in arrays]) for k in arrays[0]}
    out["raw"] = out["raw"].astype(np.float16)
    config.CACHE.mkdir(parents=True, exist_ok=True)
    temporary = config.CACHE / f"{split}.tmp.npz"
    np.savez(temporary, **out)
    index.to_csv(config.CACHE / f"{split}_index.csv", index=False)
    temporary.replace(config.CACHE / f"{split}.npz")
    return out


def load_cache(split: str) -> tuple[dict, pd.DataFrame]:
    z = np.load(config.CACHE / f"{split}.npz")
    return {k: z[k] for k in z.files}, pd.read_csv(config.CACHE / f"{split}_index.csv")


# ----------------------------------------------------------------------------- understanding
class Understanding:
    """Normaliser, engineering model g_ij (ridge, per engineering edge type) and learned couplings."""

    def fit(self, raw_benign: np.ndarray, raw_attack: np.ndarray, lam: float = 1.0):
        X = raw_benign.astype(np.float32)  # [n, T, S, 33]
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
