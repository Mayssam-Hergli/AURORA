"""Recorder: the bus -> a Section 6.3 episode folder (MASTER_PLAN Phase 3).

    python -m twin.recorder --out episodes/live-<timestamp>          # until Ctrl-C or gt/episode end
    python -m twin.recorder --out episodes/x --duration 60            # 60 s of wall time

The recorder is the scorer, not the defence: it subscribes to aurora/v1/# *including* gt/ (the core
never does). On Ctrl-C, after --duration, or when a gt/episode "end" message arrives, it writes
    telemetry.parquet   one row per (t, sat, sub): t, sat, src, seq, sub, <fields>
                        (wheel_rpm flattened to wheel_rpm_0..2, isl_peers as a comma string)
    commands.parquet    v, id, t, sat, origin, cmd, args (JSON string), src
    links.parquet       t, a, b, feasible, enabled, range_km
    labels.json         from the last gt/episode message (a clean label if there was none)
    ml_labels.json      t0 = first telemetry time; per-satellite onset_t from gt/event "onset" and
                        critical_t = first tick the *recorded* telemetry crosses its limit (seconds
                        since t0, interfaces.md 4)
Critical times: the twin's gt/event "critical" (the true crossing, right also under telemetry masking)
wherever the twin published one; otherwise the first crossing in the reported telemetry with the
limits in cassandra/config.yaml (twin 45 C, board 60 C, SOC 30 %, attitude 10 deg), e.g. for the board,
which has no ground truth. labels.params says which satellites came from truth ("critical_from_truth").
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
from pathlib import Path

from aurora_common.criticality import RULES, violated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from aurora_common import schemas as S  # noqa: E402
from aurora_common import topics as T  # noqa: E402
from aurora_common.bus import Bus  # noqa: E402


class Recorder:
    def __init__(self, limits: dict[str, dict] | None = None):
        self.lock = threading.Lock()
        self.tm: list[dict] = []
        self.tc: list[dict] = []
        self.links: list[dict] = []
        self.gt_episode: dict | None = None
        self.gt_events: list[dict] = []
        self.ended = threading.Event()
        self.limits = limits or {}

    def on_msg(self, topic: str, msg) -> None:
        kind = T.kind_of(topic)[0]
        with self.lock:
            if kind == "tm":
                row = {"t": msg.t, "sat": msg.sat, "src": msg.src, "seq": msg.seq, "sub": msg.sub}
                for k, v in msg.fields.items():
                    if k == "wheel_rpm":
                        for i, w in enumerate(v):
                            row[f"wheel_rpm_{i}"] = w
                    elif k == "isl_peers":
                        row[k] = ",".join(v)
                    else:
                        row[k] = v
                self.tm.append(row)
            elif kind == "tc":
                self.tc.append(
                    {
                        "v": 1,
                        "id": msg.id,
                        "t": msg.t,
                        "sat": msg.sat,
                        "origin": msg.origin,
                        "cmd": msg.cmd,
                        "args": json.dumps(msg.args),
                        "src": "bus",
                    }
                )
            elif kind == "link":
                self.links.append(
                    {
                        "t": msg.t,
                        "a": msg.a,
                        "b": msg.b,
                        "feasible": msg.feasible,
                        "enabled": msg.enabled,
                        "range_km": msg.range_km,
                    }
                )
            elif kind == "gt_episode":
                self.gt_episode = msg.model_dump()
                if msg.phase == "end":
                    self.ended.set()
            elif kind == "gt_event":
                self.gt_events.append(msg.model_dump())

    # ------------------------------------------------------------------ episode
    def write(self, out: Path) -> Path:
        with self.lock:
            tm, tc, links = list(self.tm), list(self.tc), list(self.links)
            gt, events = self.gt_episode, list(self.gt_events)
        if not tm:
            raise RuntimeError("nothing recorded (is the twin running?)")
        tel = pd.DataFrame(tm).sort_values(["t", "sat", "sub"], kind="stable")
        t0 = float(np.floor(tel.t.min()))
        sats = list(T.SATS)
        out.mkdir(parents=True, exist_ok=True)
        tel.to_parquet(out / "telemetry.parquet", index=False)
        cmd_cols = ["v", "id", "t", "sat", "origin", "cmd", "args", "src"]
        pd.DataFrame(tc, columns=cmd_cols).to_parquet(out / "commands.parquet", index=False)
        link_cols = ["t", "a", "b", "feasible", "enabled", "range_km"]
        pd.DataFrame(links, columns=link_cols).to_parquet(out / "links.parquet", index=False)

        crit = self._critical(tel, t0)
        true_crit = {}  # gt/event "critical" from the twin: the true crossing, also under masking (B1)
        for e in sorted((e for e in events if e["event"] == "critical"), key=lambda e: e["t"]):
            rule = (e.get("data") or {}).get("rule") or "true state reached F"
            true_crit.setdefault(e["sat"], (e["t"] - t0, rule))
        crit.update(true_crit)
        onset = {e["sat"]: e["t"] - t0 for e in events if e["event"] == "onset"}
        kind = (gt or {}).get("kind", "clean")
        sat_truth = {}
        for s in sorted(set(crit) | set(onset)):
            c = crit.get(s)
            sat_truth[s] = S.SatelliteTruth(
                onset_t=onset.get(s),
                critical_t=None if c is None else c[0],
                critical_rule=None if c is None else c[1],
                malicious=None if s not in onset else kind == "attack",
            )
        n_ticks = int(round(tel.t.max() - t0)) + 1
        first = min(crit.values(), default=None, key=lambda c: c[0])
        if gt:
            labels = S.EpisodeLabels(
                **{
                    k: gt[k]
                    for k in S.EpisodeLabels.model_fields
                    if k in gt and k not in ("critical_t", "critical_rule")
                },
                critical_t=None if first is None else t0 + first[0],
                critical_rule=None if first is None else first[1],
            )
        else:
            labels = S.EpisodeLabels(
                episode_id=out.name,
                generator="recorder-v1",
                seed=0,
                scenario="N",
                kind="clean",
                type="N",
                target=None,
                onset_t=None,
                critical_t=None if first is None else t0 + first[0],
                critical_rule=None if first is None else first[1],
                params={},
                satellites=sats,
            )
        labels.params = {
            **labels.params,
            "critical_source": "gt/event critical (twin truth) where published, else reported telemetry",
            "critical_from_truth": sorted(true_crit),
        }
        ml = S.MLLabels(
            episode_id=labels.episode_id,
            t0=t0,
            dt_s=1.0,
            n_ticks=n_ticks,
            family={"source": "live recording"},
            satellites=sat_truth,
        )
        (out / "labels.json").write_text(labels.model_dump_json(indent=1))
        (out / "ml_labels.json").write_text(ml.model_dump_json(indent=1))
        return out

    def _critical(self, tel: pd.DataFrame, t0: float) -> dict[str, tuple[float, str]]:
        """First crossing per satellite in the recorded telemetry: sat -> (seconds since t0, rule)."""
        out: dict[str, tuple[float, str]] = {}
        rules = RULES
        for sat, g in tel.groupby("sat"):
            lim = self.limits.get(sat, {})
            for sub, col, key, sign, rule in rules:
                limit = lim.get(key)
                if limit is None or col not in g:
                    continue
                x = g[g["sub"] == sub][["t", col]].dropna()
                hit = x[violated(x[col].abs() if col == "att_err_deg" else x[col], limit, sign)]
                if len(hit):
                    t = float(hit.t.iloc[0]) - t0
                    if sat not in out or t < out[sat][0]:
                        out[sat] = (t, rule)
        return out


def limits_from_core_config(twin_sats: str | None = None) -> dict[str, dict]:
    from cassandra import config

    prof = config.profiles(config.with_twin_sats(config.load(), twin_sats))
    return {
        s: {"t_max_c": p.t_max_c, "soc_min_pct": p.soc_min_pct, "theta_max_deg": p.theta_max_deg}
        for s, p in prof.items()
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="record the AURORA bus into a Section 6.3 episode")
    ap.add_argument("--out", default=None, help="episode folder (default episodes/live-<timestamp>)")
    ap.add_argument(
        "--duration", type=float, default=0.0, help="wall seconds to record (0 = until Ctrl-C / gt end)"
    )
    ap.add_argument("--twin-sats", default=None, help="as for the core: satellites not listed are the board")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    a = ap.parse_args(argv)
    out = Path(a.out or f"episodes/live-{time.strftime('%Y%m%d-%H%M%S')}")
    rec = Recorder(limits_from_core_config(a.twin_sats))
    bus = Bus(f"recorder-{uuid.uuid4().hex[:6]}", a.host, a.port)
    bus.subscribe(f"{T.PREFIX}/#", lambda topic, raw: _dispatch(rec, topic, raw), parse=False)
    if not bus.start():
        print(f"no broker at {a.host}:{a.port} (make up)", file=sys.stderr)
        return 1
    print(f"recording aurora/v1/# (ground truth included) into {out}; Ctrl-C to stop", flush=True)
    try:
        end = time.time() + a.duration if a.duration else None
        while not rec.ended.is_set() and (end is None or time.time() < end):
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    bus.stop()
    path = rec.write(out)
    print(
        f"episode written: {path} ({len(rec.tm)} telemetry, {len(rec.tc)} commands, "
        f"{len(rec.links)} link rows)"
    )
    return 0


def _dispatch(rec: Recorder, topic: str, raw: bytes) -> None:
    try:
        kind = T.kind_of(topic)[0]
    except ValueError:
        return
    if kind in ("tm", "tc", "link", "gt_episode", "gt_event"):
        try:
            rec.on_msg(topic, S.parse(topic, raw))
        except ValueError:
            pass  # a malformed message is not part of the episode


if __name__ == "__main__":
    sys.exit(main())
