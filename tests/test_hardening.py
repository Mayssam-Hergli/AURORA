"""Hardening regressions: causal observations, complete failure dimensions and conservative gates."""

import numpy as np
import pytest

from aurora_common.criticality import critical, observed_safe
from cassandra.act import Safety
from cassandra.sandbox import Sandbox
from ml import features, schema
from ml.frames import CH, FrameBuilder, episode_messages
from tests.cassandra.helpers import cfg_and_profiles, thermal, world
from tests.cassandra.test_ingest import FIELDS, T0, feed_tm, make


def test_initial_missing_and_dropout_do_not_read_future():
    b = FrameBuilder(["B"])
    a, _ = b.frame(0)
    assert a[0, CH["temp_c"]] == 0
    assert a[0, CH["temp_c_missing"]] == 1 and a[0, CH["temp_c_age_s"]] == 600
    b.on_telemetry({"sat": "B", "sub": "thermal", "t": 2, "fields": {"temp_c": 42}})
    a, _ = b.frame(2)
    assert a[0, CH["temp_c"]] == 42 and a[0, CH["temp_c_missing"]] == 0
    a, _ = b.frame(17)
    assert a[0, CH["temp_c"]] == 42 and a[0, CH["temp_c_age_s"]] == 15
    b.on_telemetry({"sat": "B", "sub": "thermal", "t": 1, "fields": {"temp_c": 99}})
    a, _ = b.frame(18)
    assert a[0, CH["temp_c"]] == 42  # late older packet cannot overwrite newer value
    assert features._ffill(np.array([[np.nan], [np.nan], [42.0]]))[:, 0].tolist() == [0, 0, 42]


def test_offline_online_parity_with_initial_gap_delay_and_dropout(episodes, tmp_path):
    path, _ = episodes["A1"]
    ep = schema.read_episode(path, with_labels=True)
    tel = ep["telemetry"].copy()
    t0 = ep["ml_labels"].t0
    tel = tel[~((tel["sub"] == "thermal") & (tel.t < t0 + 5))].copy()
    tel["arrival_t"] = tel.t
    tel.loc[(tel["sub"] == "eps") & (tel.t < t0 + 10), "arrival_t"] += 12
    tel = tel[~((tel["sub"] == "adcs") & tel.t.between(t0 + 30, t0 + 80))]
    dest = tmp_path / "gaps"
    import shutil

    shutil.copytree(path, dest)
    tel.to_parquet(dest / "telemetry.parquet", index=False)
    ep["telemetry"] = tel
    offline = features.episode_arrays(dest)
    b = FrameBuilder(ep["labels"].satellites)
    for k, (t, messages) in enumerate(episode_messages(ep, t0=t0, n_ticks=ep["ml_labels"].n_ticks)):
        for kind, msg in messages:
            {"tm": b.on_telemetry, "tc": b.on_command, "link": b.on_link}[kind](msg)
        raw, _ = b.frame(t)
        np.testing.assert_allclose(raw, offline["raw"][k])
    assert offline["raw"][:5, :, CH["temp_c"]].max() == 0


@pytest.mark.parametrize(
    "temp,soc,att,expected",
    [(20, 80, 11, True), (20, 29, 0, True), (46, 80, 0, True), (-60, 80, 0, False), (20, 80, -11, True)],
)
def test_all_critical_dimensions(temp, soc, att, expected):
    limits = {"t_max_c": 45, "soc_min_pct": 30, "theta_max_deg": 10}
    assert bool(critical(temp, soc, att, limits)) == expected


def test_planner_attitude_risk_and_recovery_missing_evidence():
    cfg, profiles = cfg_and_profiles(("B",))
    sb = Sandbox(profiles, cfg)
    p = profiles["B"]
    assert sb._risk(p, np.full(8, 20), np.full(8, 80), np.full(8, 11)) == 1
    assert sb._risk(p, np.full(8, 20), np.full(8, 80)) == 1
    assert not observed_safe(thermal(20), p)
    tick = world(T0, {"B": {**thermal(20), "adcs": FIELDS["adcs"]}})
    obs = sb.observation(tick, {}, {})
    assert obs["sats"]["B"]["wheel_rpm"] == FIELDS["adcs"]["wheel_rpm"]
    assert sb._j(
        __import__("cassandra.sandbox", fromlist=["Candidate"]).Candidate("none", "B"), 0.5, 1, None
    ) == sb._j(__import__("cassandra.sandbox", fromlist=["Candidate"]).Candidate("none", "B"), 0.5, 1, 0.5)


def test_unknown_high_and_low_uncertainty():
    cfg, _ = cfg_and_profiles()
    safety = Safety(cfg, 12)
    assert safety.uncertainty_state(None) == "UNKNOWN" and safety.gate(None)
    assert safety.uncertainty_state(float("nan")) == "UNKNOWN"
    assert safety.uncertainty_state(0.5) == "KNOWN_HIGH" and safety.gate(0.5)
    assert safety.uncertainty_state(0.1) == "KNOWN_LOW" and not safety.gate(0.1)


def test_source_mismatch_cannot_overwrite_trusted_state():
    ing = make()
    feed_tm(ing, "E", "thermal", T0, 0, fields={"temp_c": 20.0, "heater_on": 0})
    feed_tm(ing, "E", "thermal", T0 + 1, 1, src="hw", fields={"temp_c": 99.0, "heater_on": 1})
    assert ing.obs["E", "thermal"].fields["temp_c"] == 20
    assert len(ing.rejected_observations) == 1
    assert ing.notes[-1].data["provenance_status"] == "SOURCE_MISMATCH"


def test_tick_does_not_see_a_future_observation():
    ing = make()
    feed_tm(ing, "E", "thermal", T0, 0, fields={"temp_c": 20.0, "heater_on": 0})
    feed_tm(ing, "E", "thermal", T0 + 1, 1, fields={"temp_c": 42.0, "heater_on": 1})
    ticks = ing.poll(T0 + 1.1)
    assert ticks[0].values["E"]["thermal"]["temp_c"] == 20


def test_unknown_uncertainty_action_permissions():
    from cassandra.actions import ACTIONS

    assert ACTIONS["cut_heater"].allow_unknown and ACTIONS["safe_mode"].allow_unknown
    assert not ACTIONS["quarantine"].allow_unknown and not ACTIONS["block_source"].allow_unknown


def test_multi_domain_rules_do_not_claim_attack():
    from cassandra.monitor import Monitor

    cfg, profiles = cfg_and_profiles(("B",))
    monitor = Monitor(profiles, cfg)
    values = {
        "B": {
            "eps": {"bus_v": 30.0, "current_ma": 1000.0, "load_w": 100.0},
            "adcs": {"wheel_rpm": [200_000.0, 0.0, 0.0]},
        }
    }
    events = monitor.step(world(T0, values))
    assert {e.rule for e in events} == {"eps_power_balance", "adcs_sensor_range"}
    assert all(e.data["malicious"] is None for e in events)
    assert monitor.step(world(T0 + 1, values)) == []


def test_new_ablations_and_export_shapes(understanding):
    import torch

    from ml import graph as G
    from ml.export import example_inputs
    from ml.model import Forecaster

    raw, links = example_inputs()
    assert raw.shape[-1] == len(G.FLAT) == 33
    full, no_sparta = G.build(12), G.build(12, "no_sparta")
    assert len(no_sparta.src) <= len(full.src)
    temporal = Forecaster(understanding, full, rounds=0)
    out = temporal(raw, links, return_att=True)
    assert torch.isfinite(out["p_fail"]).all()
    temporal.telemetry_only = True
    altered = raw.clone()
    altered[..., temporal.command_indices] = 100
    torch.testing.assert_close(temporal(raw, links)["p_fail"], temporal(altered, links)["p_fail"])
