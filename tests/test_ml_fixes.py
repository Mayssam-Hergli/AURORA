"""Regression tests for the PR #1 review fixes (MASTER_PLAN Phase 2): M1-M5, S1-S4, S7, S10."""

import json
import shutil

import numpy as np
import pandas as pd
import pytest

from aurora_common import schemas as AS
from aurora_common import topics as T
from eval import metrics as M
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
def test_m3_alert_time_is_the_first_alert_anywhere_and_pre_onset_alarms_count():
    score = np.zeros((1, 100, 2))
    score[0, 10:12, 1] = 1.0  # satellite 1: false alarm before the onset at t = 30
    score[0, 40:, 0] = 1.0
    truth = {"crit": np.array([[70, -1]]), "onset": np.array([[30, 30]]), "malicious": np.array([[1, 1]])}
    rows = M.alerts(score, truth, 0.5)
    assert rows[0]["t_alert"] == 40 and rows[0]["lead"] == 30 and not rows[0]["pre_onset"]
    score[0, 5, 0] = 1.0  # now satellite 0 also alarms before the onset
    rows = M.alerts(score, truth, 0.5)
    assert rows[0]["t_alert"] == 5 and rows[0]["pre_onset"]
    pre = M.pre_onset_alarms(score, truth, np.array(["attack"]), 0.5)
    assert pre["satellites_with_alarm"] == 2 and pre["alarm_events"] == 2
    assert M.lead_summary(rows)["pre_onset_alerts"] == 1


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
def test_m5_calibration_is_required_unless_explicitly_waived(tmp_path, monkeypatch):
    from ml.calibration import read_calibration, write_calibration
    from ml.online import OnlineForecaster

    monkeypatch.setattr(config, "MODELS", tmp_path)
    with pytest.raises(FileNotFoundError, match="calibration.json"):
        OnlineForecaster.from_models(names=[])
    fc = OnlineForecaster.from_models(names=[], allow_uncalibrated=True)
    assert fc.model_name == "tgn-v1-uncalibrated" and fc.temperature == 1.0
    write_calibration(1.7, None, 0.001, 5, "ml.train")
    write_calibration(1.8, 0.42, 0.001, 5, "eval.harness")
    cal = read_calibration()
    assert cal["temperature"] == 1.8 and cal["tau"] == 0.42 and cal["n_models"] == 5
    assert OnlineForecaster.from_models(names=[]).temperature == 1.8


# ----------------------------------------------------------------------------- S1
def test_s1_evaluation_ticks_do_not_depend_on_ground_truth():
    from eval.harness import eval_ticks

    c = {"onset": np.array([[100], [-1]]), "crit": np.array([[200], [-1]])}
    assert np.array_equal(eval_ticks(c, 0, 600, 2), eval_ticks(c, 1, 600, 2))


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
def test_s7_generate_skips_test_real_without_opssat(monkeypatch, capsys):
    from ml.synthetic import generate as G

    calls = []
    monkeypatch.setattr(G, "_opssat_available", lambda: False)
    monkeypatch.setattr(
        G, "generate", lambda split, n, w: calls.append(split) or pd.DataFrame({"kind": [], "critical_t": []})
    )
    G.main(["--split", "test_real"])
    assert calls == [] and "test_real  skipped" in capsys.readouterr().out


# ----------------------------------------------------------------------------- S10
def test_s10_offline_episodes_are_synthetic_live_messages_are_twin(a1_episode):
    ep = schema.read_episode(a1_episode)
    assert set(ep["telemetry"].src) == {"synthetic"} and set(ep["commands"].src) == {"synthetic"}
    tw = _twin()
    tw.step(1)
    assert {m["src"] for k, m in tw.messages_at(0) if k == "tm"} == {"twin"}
