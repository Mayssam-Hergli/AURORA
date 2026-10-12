"""Regression tests for the PR #1 review fixes (MASTER_PLAN Phase 2): M1-M5, S1-S4, S7, S10."""

import json
import shutil

import numpy as np
import pandas as pd
import pytest

from aurora_common import schemas as AS
from aurora_common import topics as T
from ml import config, features, schema
from ml.synthetic.engine import MODE_SAFE, SyntheticTwin
from tests.conftest import make_spec


def _twin(etype="N", seed=3):
    cfg, spec = make_spec(etype, seed=seed)
    return SyntheticTwin(cfg, spec)


def _queue_cmd(tw, tick, cmd, args, origin="ground", malicious=True):
    tw._cmd(tick, "B", origin, cmd, args, malicious=malicious)


# ----------------------------------------------------------------------------- M1
def test_m1_safe_mode_keeps_the_queue_and_applies_the_safe_rule_to_everyone():
    tw = _twin()
    tw.step(10)
    _queue_cmd(tw, 20, "HEATER", {"on": True}, malicious=True)  # the attacker's
    _queue_cmd(tw, 21, "HEATER", {"on": True}, malicious=False)  # a legitimate one: same fate
    _queue_cmd(tw, 22, "PING", {}, malicious=True)
    tw.apply({"sat": "B", "origin": "core", "cmd": "SAFE_MODE", "args": {}})
    assert len([k for k, m in tw.queue if m["sat"] == "B" and k in (20, 21, 22)]) == 3  # nothing purged
    tw.step(15)
    recs = {r["id"]: r for r in tw.commands}
    mine = [
        r
        for r in recs.values()
        if r["sat"] == "B" and r["cmd"] in ("HEATER", "PING") and r["origin"] == "ground"
    ]
    by_cmd = {(r["cmd"], r["args"]): r["accepted"] for r in mine}
    assert by_cmd[("HEATER", json.dumps({"on": True}))] is False  # refused: SAFE, not "malicious"
    assert by_cmd[("PING", json.dumps({}))] is True  # a malicious PING still executes: realistic
    assert tw.mode[tw.idx["B"]] == MODE_SAFE and not tw.heater_cmd[tw.idx["B"]]
    tw.apply({"sat": "B", "origin": "isl:A", "cmd": "PING", "args": {}})
    assert tw.commands[-1]["accepted"] is False  # non-ground/core origins refused in SAFE
    tw.apply({"sat": "B", "origin": "ground", "cmd": "SET_MODE", "args": {"mode": "SCIENCE"}})
    assert tw.commands[-1]["accepted"] is False
    tw.apply({"sat": "B", "origin": "core", "cmd": "SET_MODE", "args": {"mode": "NOMINAL"}})
    assert tw.mode[tw.idx["B"]] != MODE_SAFE


# ----------------------------------------------------------------------------- M2
def test_m2_planning_snapshot_has_no_future():
    tw = _twin("C1", seed=5)
    tw.step(5)
    full, plan = tw.snapshot(), tw.snapshot(include_queue=False)
    assert full["queue"] and plan["queue"] == []
    assert plan["c1"] is None and full.get("c1") is not None
    assert np.allclose(full["temp"], plan["temp"])  # same physical state


def test_m2_closed_loop_plans_without_the_attackers_queue():
    from eval.closed_loop import plan
    from ml.online import OnlineForecaster

    tw = _twin()
    tw.step(40)
    queued_before = list(tw.queue)
    for k in range(41, 75, 5):  # the attacker's future heater commands, already queued
        _queue_cmd(tw, k, "HEATER", {"on": True})
    fc = OnlineForecaster([], tw.sats)
    rc = config.load("eval")["response"]
    fc.push_frame()
    best, table, _ = plan(tw, fc, "B", tw.t, rc, 0, tw.S)
    assert len(tw.queue) == len(queued_before) + 7  # the real twin keeps its future
    none = next(r for r in table if r["action"] == "none")
    assert none["Q"] is None and best["gated"] is None  # one (fallback) model: D4
    assert none["R"] < 1.0  # the planner did not see the queued heater commands heat B


# ----------------------------------------------------------------------------- M3
# ----------------------------------------------------------------------------- M4 + S2
@pytest.fixture(scope="module")
def a1_episode(tmp_path_factory):
    root = tmp_path_factory.mktemp("ep")
    cfg, spec = make_spec("A1", seed=7)
    ep = SyntheticTwin(cfg, spec).run_episode()
    schema.write_episode(root, "a1", ep.telemetry, ep.commands, ep.links, ep.labels, ep.ml_labels)
    return root / "a1"


def test_m4_compact_and_spaced_json_give_the_same_heater_feature(a1_episode, tmp_path):
    col = features.graph.FLAT.index("tc_heater_on")
    spaced = features.episode_arrays(a1_episode)["raw"][..., col].sum()
    copy = tmp_path / "compact"
    shutil.copytree(a1_episode, copy)
    c = pd.read_parquet(copy / "commands.parquet")
    c["args"] = [json.dumps(json.loads(a), separators=(",", ":")) for a in c.args]
    c.to_parquet(copy / "commands.parquet", index=False)
    compact = features.episode_arrays(copy)["raw"][..., col].sum()
    assert spaced > 0 and compact == spaced


def test_s2_episodes_without_label_files_can_be_featurised(a1_episode, tmp_path):
    bare = tmp_path / "bare"
    shutil.copytree(a1_episode, bare)
    (bare / "labels.json").unlink()
    (bare / "ml_labels.json").unlink()
    a, b = features.episode_arrays(bare), features.episode_arrays(a1_episode)
    assert np.allclose(a["raw"], b["raw"]) and (a["crit"] == -1).all() and (a["onset"] == -1).all()


# ----------------------------------------------------------------------------- M5
# ----------------------------------------------------------------------------- S1
# ----------------------------------------------------------------------------- S4 + honest nulls
def test_s4_forecast_before_the_first_tick_and_null_fallback_fields():
    from ml.online import OnlineForecaster

    fc = OnlineForecaster([], list(T.SATS))
    early = fc.forecast(1_800_000_000.0)
    for m in early:
        f = AS.parse(T.forecast(m["sat"]), m)
        assert f.warming_up and f.p_std is None and f.next_sub is None and f.p_malicious is None
    tw = _twin()
    for t in range(10):
        tw.step(1)
        for kind, msg in tw.messages_at(t):
            {"tm": fc.on_telemetry, "tc": fc.on_command, "link": fc.on_link}[kind](msg)
        out = fc.tick(tw.t0_unix + t)
    f = AS.parse(T.forecast(out[0]["sat"]), out[0])
    assert f.model == "trajectory-v0" and f.p_std is None and f.next_sub is None


# ----------------------------------------------------------------------------- S7
# ----------------------------------------------------------------------------- S10
def test_s10_offline_episodes_are_synthetic_live_messages_are_twin(a1_episode):
    ep = schema.read_episode(a1_episode)
    assert set(ep["telemetry"].src) == {"synthetic"} and set(ep["commands"].src) == {"synthetic"}
    tw = _twin()
    tw.step(1)
    assert {m["src"] for k, m in tw.messages_at(0) if k == "tm"} == {"twin"}
