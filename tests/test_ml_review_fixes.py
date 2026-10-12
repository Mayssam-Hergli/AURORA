"""Regression tests for the PR #1 review fixes (docs/MASTER_PLAN.md Phase 2: M1-M5, S1-S4, S7, S9, S10)
and the engine hook the core's sandbox needs (seed_from_observation)."""

import inspect
import json
import shutil

import numpy as np
import pandas as pd
import pytest
import torch

from aurora_common import schemas as S
from aurora_common import topics as T
from eval import metrics as M
from ml import calibration as Cal
from ml import config, features, schema
from ml import graph as G
from ml.online import OnlineForecaster, episode_messages
from ml.synthetic.engine import MODE_NOMINAL, MODE_SAFE, SyntheticTwin, refused_in_safe
from tests.conftest import make_spec


def _twin(etype="N", seed=3):
    cfg, spec = make_spec(etype, seed)
    return SyntheticTwin(cfg, spec)


# ----------------------------------------------------------------------------- M1 safe mode
def test_m1_safe_mode_never_drops_queued_commands():
    tw = _twin()
    b = tw.idx["B"]
    tw.step(10)
    tw.apply({"sat": "B", "origin": "core", "cmd": "SAFE_MODE", "args": {}})
    heat = tw._cmd(tw.t + 3, "B", "ground", "HEATER", {"on": True}, malicious=True)
    science = tw._cmd(tw.t + 8, "B", "ground", "SET_MODE", {"mode": "SCIENCE"}, malicious=True)
    later = tw._cmd(tw.t + 12, "B", "ground", "HEATER", {"on": True}, malicious=True)
    queued = {m["id"] for _, m in tw.queue}
    assert {heat["id"], science["id"], later["id"]} <= queued  # nothing deleted by an oracle
    tw.step(10)  # in SAFE both are delivered and refused by the SAFE rule
    rec = {r["id"]: r for r in tw.commands}
    assert rec[heat["id"]]["accepted"] is False and rec[science["id"]]["accepted"] is False
    assert not tw.heater_cmd[b]
    tw.apply({"sat": "B", "origin": "core", "cmd": "SET_MODE", "args": {"mode": "NOMINAL"}})
    tw.step(5)  # out of SAFE, the attacker's queued ground command executes: that is realistic
    assert {r["id"]: r for r in tw.commands}[later["id"]]["accepted"] is True
    assert tw.heater_cmd[b]
    assert "_malicious" not in inspect.getsource(SyntheticTwin._safe_mode)


def test_m1_safe_semantics_match_interfaces_3_2():
    tw = _twin()
    b = tw.idx["B"]
    tw.apply({"sat": "B", "origin": "ground", "cmd": "SAFE_MODE", "args": {}})
    rej = tw.cmd_rej[b]
    tw.apply({"sat": "B", "origin": "ground", "cmd": "HEATER", "args": {"on": True}})
    tw.apply({"sat": "B", "origin": "core", "cmd": "HEATER", "args": {"on": True}})
    tw.apply({"sat": "B", "origin": "core", "cmd": "SET_MODE", "args": {"mode": "IDLE"}})
    tw.apply({"sat": "B", "origin": "isl:A", "cmd": "PING", "args": {}})
    assert tw.cmd_rej[b] == rej + 4 and not tw.heater_cmd[b] and tw.mode[b] == MODE_SAFE
    ok = tw.cmd_ok[b]
    for cmd, args in (("HEATER", {"on": False}), ("PING", {}), ("ISL_ENABLE", {"peer": "A", "on": True})):
        tw.apply({"sat": "B", "origin": "ground", "cmd": cmd, "args": args})
    tw.apply({"sat": "B", "origin": "core", "cmd": "SAFE_MODE", "args": {}})
    assert tw.cmd_ok[b] == ok + 4 and tw.mode[b] == MODE_SAFE
    tw.apply({"sat": "B", "origin": "ground", "cmd": "SET_MODE", "args": {"mode": "NOMINAL"}})
    assert tw.mode[b] == MODE_NOMINAL


def test_m1_engine_and_replay_plant_refuse_the_same_commands():
    from scripts.replay_twin import ReplayTwin

    rt = ReplayTwin.__new__(ReplayTwin)
    rt.twin = _twin()
    rt.twin.mode[rt.twin.idx["B"]] = MODE_SAFE
    for cmd, args in (
        ("HEATER", {"on": True}),
        ("HEATER", {"on": False}),
        ("SET_MODE", {"mode": "NOMINAL"}),
        ("SET_MODE", {"mode": "SAFE"}),
        ("SET_MODE", {"mode": "SCIENCE"}),
        ("SET_MODE", {"mode": "WHEEL_TEST"}),
        ("SAFE_MODE", {}),
        ("ISL_ENABLE", {"peer": "A", "on": False}),
        ("PING", {}),
    ):
        assert rt.refused_in_safe("B", cmd, args) == refused_in_safe(cmd, args), (cmd, args)


# ----------------------------------------------------------------------------- M2 planner snapshot
def test_m2_planner_snapshot_has_no_attacker_queue():
    cfg, spec = make_spec("C1")
    tw = SyntheticTwin(cfg, spec)
    tw.step(5)
    assert tw.queue and tw.c1
    snap = tw.snapshot(include_queue=False)
    assert snap["queue"] == [] and snap["c1"] is None
    assert tw.snapshot()["queue"] and tw.queue  # the world keeps its future


def test_m2_closed_loop_plans_without_the_queue(monkeypatch):
    from eval import closed_loop as CL

    cfg, spec = make_spec("A1")
    tw = SyntheticTwin(cfg, spec)
    tw.step(40)
    fc = OnlineForecaster([], tw.sats)
    for k in range(30, 40):
        CL.feed(fc, tw, k)
        fc.push_frame()
    queued = len(tw.queue)
    seen, real_restore = [], tw.restore
    monkeypatch.setattr(tw, "restore", lambda snap: (seen.append(len(snap["queue"])), real_restore(snap)))
    CL.plan(tw, fc, "B", tw.t, config.load("eval")["response"], 0, len(tw.sats))
    assert queued > 0 and seen[:-1] and all(n == 0 for n in seen[:-1])  # every candidate rollout
    assert seen[-1] == queued and len(tw.queue) == queued  # then the real world, queue intact


# ----------------------------------------------------------------------------- sandbox hook
def _seeded(heater_on: int):
    tw = _twin()
    tw._cmd(150, "E", "ground", "HEATER", {"on": False})  # a real-world queued command: must not survive
    return tw.seed_from_observation(
        {
            "tick": 100,
            "values": {
                "E": {
                    "thermal": {"temp_c": 40.0, "heater_on": heater_on},
                    "eps": {"soc_pct": 55.0, "bus_v": 28, "load_w": 40, "solar_w": 0, "current_ma": 1500},
                    "obc": {"mode": "NOMINAL", "cmd_ok": 7, "cmd_rej": 2, "last_origin": "ground"},
                },
                "F": {"obc": {"mode": "SAFE", "cmd_ok": 1, "cmd_rej": 0, "last_origin": "core"}},
            },
            "links": {("E", "F"): {"feasible": True, "enabled": False}},
        }
    )


def test_seed_from_observation_shadows_the_world():
    on, off = _seeded(1), _seeded(0)
    e, f = on.idx["E"], on.idx["F"]
    assert on.t == 100 and on.queue == [] and on.temp[e] == 40.0 and on.heater_cmd[e]
    assert on.mode[f] == MODE_SAFE and on.cmd_ok[e] == 7 and abs(on.soc[e] - 0.55) < 1e-9
    on.step(10)
    off.step(10)
    assert on.temp[e] - off.temp[e] > 1.0  # the latched heater stays on in the shadow
    assert not on.link_enabled[109, e, f]  # the disabled link stays disabled over the rollout
    assert all(r["sat"] != "E" or r["cmd"] != "HEATER" for r in on.commands)


# ----------------------------------------------------------------------------- M3 alert time
def test_m3_pre_onset_alarms_are_false_alarms_and_earn_no_lead():
    score = np.zeros((1, 300, 2))
    score[0, 10, 0] = 0.9  # a blip before the attack starts
    score[0, 150:, 0] = 0.9  # the real alert
    truth = {"onset": np.array([[100, -1]]), "crit": np.array([[200, -1]]), "malicious": np.array([[1, -1]])}
    (r,) = M.alerts(score, truth, tau=0.5)
    assert r["t_alert_any"] == 10 and r["pre_onset_alarm"] and r["t_alert"] == 150 and r["lead"] == 50
    fa = M.pre_onset_false_alarms(score, truth, np.array(["attack"]), 0.5)
    assert fa["pre_onset_alarm_events"] == 1 and fa["pre_onset_sats_alarmed"] == 1
    assert fa["pre_onset_false_alarms_per_sat_hour"] == pytest.approx(3600 / 200)


# ----------------------------------------------------------------------------- M4 args parsing
def test_m4_heater_on_count_ignores_json_spacing(episodes, tmp_path):
    assert features.heater_on_arg('{"on":true}') and features.heater_on_arg('{"on": true}')
    assert features.heater_on_arg({"on": True}) and not features.heater_on_arg('{"on":false}')
    assert not features.heater_on_arg("") and not features.heater_on_arg(None)
    path, _ = episodes["A1"]
    compact = tmp_path / "compact"
    shutil.copytree(path, compact)
    cmds = pd.read_parquet(compact / "commands.parquet")
    cmds["args"] = [json.dumps(json.loads(a), separators=(",", ":")) for a in cmds["args"]]
    cmds.to_parquet(compact / "commands.parquet", index=False)
    col = G.FLAT.index("tc_heater_on")
    spaced, tight = features.episode_arrays(path)["raw"], features.episode_arrays(compact)["raw"]
    assert spaced[..., col].sum() > 0 and np.array_equal(spaced[..., col], tight[..., col])


# ----------------------------------------------------------------------------- M5 calibration
@pytest.fixture
def one_model(tmp_path, monkeypatch, understanding):
    from ml.train import build_model

    monkeypatch.setattr(config, "MODELS", tmp_path)
    understanding.save(tmp_path / "understanding.npz")
    mc = config.load("model")
    m = build_model("full", mc, 12)
    ck = {
        "state": m.state_dict(),
        "variant": "full",
        "seed": 0,
        "model_cfg": mc,
        "n_sats": 12,
        "channels": features.graph.FLAT,
        "feature_schema": "causal-v2",
    }
    torch.save(ck, tmp_path / "full_s0.pt")
    return tmp_path


def test_m5_models_without_calibration_refuse_to_run(one_model, caplog):
    with pytest.raises(FileNotFoundError, match="calibration.json"):
        OnlineForecaster.from_models()
    with caplog.at_level("WARNING", logger="ml.online"):
        fc = OnlineForecaster.from_models(allow_uncalibrated=True)
    assert fc.model_name == "tgn-v1-uncalibrated" and "uncalibrated" in caplog.text
    (one_model / "calibration.json").write_text(json.dumps({"temperature": 0.7, "tau": 0.02}))
    fc = OnlineForecaster.from_models()
    assert fc.temperature == 0.7 and fc.model_name == "tgn-v1"


def test_m5_calibration_record_has_the_planned_fields():
    rec = Cal.calibration_record(0.74, 0.018, ["full_s0"], config.load("eval"))
    assert {"temperature", "tau", "fpr_target", "git", "horizon_s", "models"} <= set(rec)
    assert rec["fpr_target"] == 0.001 and rec["horizon_s"] == 60


# ----------------------------------------------------------------------------- S4 + contract
def test_forecasts_follow_the_contract_before_and_after_the_first_tick(one_model, episodes):
    (one_model / "calibration.json").write_text(json.dumps({"temperature": 0.7, "tau": 0.02}))
    fc_model = OnlineForecaster.from_models()
    fc_traj = OnlineForecaster([], fc_model.sats)
    out = fc_model.forecast(1760000000.0)  # S4: before the first tick, no IndexError
    first = out[0]
    assert first["warming_up"] and first["p_fail"]["120"] == 0 and first["model"] == "trajectory-v0"
    ep = schema.read_episode(episodes["A1"][0], with_labels=False)
    for k, (t, msgs) in enumerate(episode_messages(ep)):
        for kind, msg in msgs:
            for fc in (fc_model, fc_traj):
                {"tm": fc.on_telemetry, "tc": fc.on_command, "link": fc.on_link}[kind](msg)
        out += fc_model.tick(t) + fc_traj.tick(t)
        if k == 35:
            break
    for m in out:
        S.parse(T.forecast(m["sat"]), m)  # docs/interfaces.md 3.5
    single = [m for m in out if m["model"] == "tgn-v1"]
    assert single and all(m["p_std"] is None and m["q"] is None for m in single)  # D4: one model
    assert single[-1]["warming_up"] is False
    traj = [m for m in out if m["model"] == "trajectory-v0"]
    assert all(m["next_sub"] is None and m["p_malicious"] is None for m in traj)


# ----------------------------------------------------------------------------- S1, S9 harness
def test_s1_scoring_schedule_ignores_ground_truth():
    from eval import harness as H

    assert list(inspect.signature(H.eval_ticks).parameters) == ["T", "stride"]
    assert list(H.eval_ticks(10, 2)) == [0, 2, 4, 6, 8]
    x = np.arange(10, dtype=float)[None, :, None]
    assert H.hold_on_stride(x, 3)[0, :, 0].tolist() == [0, 0, 0, 3, 3, 3, 6, 6, 6, 9]


def test_s9_ablations_run_on_test_ood(tmp_path, monkeypatch):
    from eval import harness as H

    monkeypatch.setattr(config, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(config, "MODELS", tmp_path / "models")
    monkeypatch.setattr(H, "OUT", tmp_path / "out")
    (tmp_path / "cache").mkdir()
    (tmp_path / "models").mkdir()
    for split in ("val", "test_ood"):
        (tmp_path / "cache" / f"{split}.npz").write_bytes(b"")
    for name in ("full_s0", "no_eng_s0"):
        (tmp_path / "models" / f"{name}.pt").write_bytes(b"")
    jobs = H.missing_jobs()
    assert ["predict", "no_eng_s0", "test_ood"] in jobs and ["predict", "full_s0", "test_ood"] in jobs
    assert not any(j[-1] == "test_real" for j in jobs)  # not generated (S7): not scheduled


# ----------------------------------------------------------------------------- S2, S3, S7, S10
def test_s2_features_without_label_files(episodes, tmp_path):
    path, _ = episodes["B1"]
    bare = tmp_path / "bare"
    shutil.copytree(path, bare)
    (bare / "labels.json").unlink()
    (bare / "ml_labels.json").unlink()
    a, b = features.episode_arrays(path), features.episode_arrays(bare)
    assert np.array_equal(a["raw"], b["raw"]) and np.array_equal(a["links"], b["links"])
    assert (b["crit"] == -1).all() and (b["onset"] == -1).all()


def test_s3_train_job_makes_its_own_dirs(tmp_path, monkeypatch):
    from ml import train

    def stop(split):
        raise RuntimeError("stop")

    monkeypatch.setattr(config, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(config, "MODELS", tmp_path / "models")
    monkeypatch.setattr(train.features, "load_cache", stop)
    with pytest.raises(RuntimeError, match="stop"):
        train.train_job("full", 0)
    assert (tmp_path / "results" / "logs").is_dir() and (tmp_path / "models").is_dir()


def test_s7_generate_skips_test_real_without_opssat(monkeypatch, capsys):
    from ml.synthetic import generate as Gen

    calls = []
    monkeypatch.setattr(Gen, "opssat_available", lambda: False)
    monkeypatch.setattr(Gen, "generate", lambda split, n=None, workers=4: calls.append(split))
    Gen.main(["--split", "test_real"])
    assert calls == [] and "test_real skipped" in capsys.readouterr().out


def test_s10_offline_episodes_say_synthetic_live_messages_say_twin(episodes):
    back = schema.read_episode(episodes["A1"][0], with_labels=False)
    assert set(back["telemetry"]["src"]) == {"synthetic"} and set(back["commands"]["src"]) == {"synthetic"}
    tw = _twin()
    tw.step(1)
    assert {m["src"] for kind, m in tw.messages_at(0) if kind == "tm"} == {"twin"}
    assert MODE_NOMINAL == 0  # mode codes are part of the engine API used by the core's adapter
