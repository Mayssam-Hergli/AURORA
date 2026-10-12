"""Episode recorder (docs/MASTER_PLAN.md Phase 3): the bus -> a docs/interfaces.md Section 4 folder.

    python -m twin.recorder                           # data/episodes/live/<UTC time>/; Ctrl-C writes it
    python -m twin.recorder --out data/episodes/live/run1 --duration 120

Subscribes to aurora/v1/#, gt/ and redteam/ included: the recorder is the scorer, not the defence
(the ground-truth isolation rule binds cassandra/, never this process). It writes:

    telemetry.parquet, commands.parquet, links.parquet   everything seen on the bus
    forecasts.parquet, core.jsonl                        the core's forecasts, states, actions, events
    labels.json, ml_labels.json                          only if a gt/episode message described the run

It never invents ground truth: without gt/episode there are no label files, and the episode is read
with ml.schema.read_episode(path, with_labels=False) (ml.features handles it, S2). Per-satellite
truth comes from gt/event: onset (attack scripts), critical (the twin's true physics). It stops on
Ctrl-C, after --duration seconds, or when a gt/episode with phase "end" arrives.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

from aurora_common import schemas as S
from aurora_common import topics as T
from ml import config as ml_config
from ml import schema

log = logging.getLogger("recorder")
TEL_COLS = schema.telemetry_columns() + ["emulated"]
CMD_COLS = ["v", "id", "t", "sat", "origin", "cmd", "args", "accepted", "src"]
LINK_COLS = ["t", "a", "b", "feasible", "enabled", "range_km"]
FC_COLS = ["t", "sat", "model", "p_10", "p_30", "p_60", "p_120", "q", "warming_up"]


class Recorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.tel: list[tuple] = []
        self.cmds: list[tuple] = []
        self.links: list[tuple] = []
        self.forecasts: list[tuple] = []
        self.core: list[str] = []
        self.gt_episode: dict | None = None
        self.gt_events: list[dict] = []
        self.redteam: list[dict] = []
        self.ended = threading.Event()

    # ------------------------------------------------------------------ bus handler (paho thread)
    def on_message(self, topic: str, msg):
        kind, _ = T.kind_of(topic)
        with self.lock:
            if kind == "tm":
                self.tel.append(_tel_row(msg))
            elif kind == "tc":
                args = json.dumps(dict(msg.args), separators=(",", ":"))
                self.cmds.append((1, msg.id, msg.t, msg.sat, msg.origin, msg.cmd, args, None, "bus"))
            elif kind == "link":
                self.links.append((msg.t, msg.a, msg.b, msg.feasible, msg.enabled, msg.range_km))
            elif kind == "forecast":
                p = msg.p_fail
                self.forecasts.append(
                    (msg.t, msg.sat, msg.model, p["10"], p["30"], p["60"], p["120"], msg.q, msg.warming_up)
                )
            elif kind in ("state", "action", "event"):
                self.core.append(json.dumps({"topic": topic, **msg.model_dump(mode="json")}))
            elif kind == "gt_episode":
                self.gt_episode = {**(self.gt_episode or {}), **msg.model_dump(mode="json")}
                if msg.phase == "end":
                    self.ended.set()
            elif kind == "gt_event":
                self.gt_events.append(msg.model_dump(mode="json"))
            elif kind == "redteam":
                self.redteam.append(msg.model_dump(mode="json"))

    # ------------------------------------------------------------------ output
    def frames(self) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        with self.lock:
            tel = pd.DataFrame(self.tel, columns=TEL_COLS)
            cmds = pd.DataFrame(self.cmds, columns=CMD_COLS)
            links = pd.DataFrame(self.links, columns=LINK_COLS)
        cmds["accepted"] = cmds["accepted"].astype("boolean")  # unknown from the bus: <NA>
        return tel, cmds, links

    def labels(self, episode_id: str, tel: pd.DataFrame, links: pd.DataFrame):
        """(labels.json, ml_labels.json) from the gt messages, or (None, None) without gt/episode."""
        if self.gt_episode is None:
            return None, None
        g = self.gt_episode
        ts = pd.concat([tel.t, links.t])
        t0 = float(ts.min())
        crit: dict[str, tuple[float, str | None]] = {}
        onset: dict[str, float] = {}
        for ev in sorted(self.gt_events, key=lambda e: e["t"]):
            if ev["event"] == "critical":
                crit.setdefault(ev["sat"], (ev["t"], (ev.get("data") or {}).get("rule")))
            elif ev["event"] == "onset":
                onset.setdefault(ev["sat"], ev["t"])
        masked: dict[str, list[str]] = {}
        for r in self.redteam:
            if r["effect"] in ("freeze_tm", "bias_tm"):
                masked.setdefault(r["sat"], []).append(r["args"]["field"])
        first_crit = min(crit.values(), default=(None, None))
        labels = schema.EpisodeLabels(
            episode_id=episode_id,
            generator=g.get("generator", "live"),
            seed=int(g.get("seed", 0)),
            scenario=g["scenario"],
            kind=g["kind"],
            type=g["type"],
            target=g.get("target"),
            onset_t=g.get("onset_t"),
            critical_t=g.get("critical_t") if g.get("critical_t") is not None else first_crit[0],
            critical_rule=g.get("critical_rule") or first_crit[1],
            params=g.get("params") or {},
            satellites=g.get("satellites") or sorted(tel.sat.unique()),
        )
        sats = {}
        for sat in sorted(set(onset) | set(crit) | set(masked)):
            c = crit.get(sat)
            sats[sat] = schema.SatelliteTruth(
                onset_t=None if sat not in onset else onset[sat] - t0,
                critical_t=None if c is None else c[0] - t0,
                critical_rule=None if c is None else c[1],
                malicious=(g["kind"] == "attack") if sat in onset else None,
                reported_masked=sorted(set(masked.get(sat, []))),
            )
        ml = schema.MLLabels(
            episode_id=episode_id,
            t0=t0,
            dt_s=1.0,
            n_ticks=int(np.rint(ts.max() - t0)) + 1,
            family={"live": True},
            satellites=sats,
        )
        return labels, ml

    def write(self, out: Path) -> Path:
        out = Path(out)
        tel, cmds, links = self.frames()
        if tel.empty:
            raise RuntimeError("no telemetry recorded: is the twin (or the board bridge) running?")
        labels, ml = self.labels(out.name, tel, links)
        if labels is not None:
            schema.write_episode(out.parent, out.name, tel, cmds, links, labels, ml)
        else:
            out.mkdir(parents=True, exist_ok=True)
            for name, df in (("telemetry", tel), ("commands", cmds), ("links", links)):
                df.to_parquet(out / f"{name}.parquet", index=False, compression="zstd")
        with self.lock:
            fc = pd.DataFrame(self.forecasts, columns=FC_COLS)
            core = list(self.core)
        if len(fc):
            fc.to_parquet(out / "forecasts.parquet", index=False, compression="zstd")
        if core:
            (out / "core.jsonl").write_text("\n".join(core) + "\n")
        return out


def _tel_row(msg: S.Telemetry) -> tuple:
    f = dict(msg.fields)
    if "wheel_rpm" in f:
        w = f.pop("wheel_rpm")
        f.update(wheel_rpm_0=w[0], wheel_rpm_1=w[1], wheel_rpm_2=w[2])
    if "isl_peers" in f:
        f["isl_peers"] = ",".join(f["isl_peers"])
    base = {"t": msg.t, "sat": msg.sat, "src": msg.src, "seq": msg.seq, "sub": msg.sub}
    base["emulated"] = ",".join(msg.emulated or [])
    return tuple({**f, **base}.get(c) for c in TEL_COLS)


def main(argv=None) -> int:
    from aurora_common.bus import Bus

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--out", default=None, help="episode folder (default data/episodes/live/<UTC time>)")
    ap.add_argument("--duration", type=float, default=None, help="seconds to record")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING)
    stamp = time.strftime("live-%Y%m%d-%H%M%S", time.gmtime())
    out = Path(a.out) if a.out else ml_config.EPISODES / "live" / stamp  # gitignored (data/)
    rec = Recorder()
    bus = Bus(f"recorder-{uuid.uuid4().hex[:6]}", a.host, a.port)
    bus.subscribe(f"{T.PREFIX}/#", rec.on_message)
    if not bus.start():
        print(f"no broker at {a.host}:{a.port} (make up)", file=sys.stderr)
        return 1
    print(f"recording aurora/v1/# (gt included) into {out}. Ctrl-C to stop and write.", flush=True)
    t_end = None if a.duration is None else time.monotonic() + a.duration
    try:
        while not rec.ended.is_set() and (t_end is None or time.monotonic() < t_end):
            rec.ended.wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        bus.stop()
    path = rec.write(out)
    print(
        f"wrote {path}: {len(rec.tel)} telemetry, {len(rec.cmds)} commands, {len(rec.links)} link rows, "
        f"labels {'yes' if rec.gt_episode else 'no (no gt/episode seen)'}; invalid messages "
        f"{bus.stats.get('invalid', 0)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
