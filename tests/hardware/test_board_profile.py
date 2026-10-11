"""Board profile (Phase 8): --twin-sats A,C-L makes B the ESP8266 (hw, 60 C, 4-tick window); the core
then forecasts B with trajectory-v0 and acts within seconds of a heater latch (simulated input)."""

import numpy as np

from cassandra import config
from cassandra.forecast import ForecastStage
from tests.cassandra.helpers import T0, thermal, world


def test_twin_sats_turns_the_missing_satellites_into_the_board():
    cfg = config.with_twin_sats(config.load(), "A,C-L")
    prof = config.profiles(cfg)
    b = prof["B"]
    assert (b.kind, b.t_max_c, b.window_s, b.subs) == ("hw", 60.0, 4, ("thermal", "obc"))
    assert b.soc_min_pct is None and b.theta_max_deg is None
    assert all(prof[s].kind == "twin" and prof[s].t_max_c == 45.0 for s in "ACDEFGHIJKL")
    assert config.parse_sats("A,C-L") == list("ACDEFGHIJKL")


def test_board_sandbox_prefers_isolation_once_and_safe_mode_under_reassertion():
    from cassandra.sandbox import Sandbox, Threat

    cfg = config.with_twin_sats(config.load(), "A,C-L")
    sb = Sandbox(config.profiles(cfg), cfg)
    tick = world(T0, {"B": thermal(40.0, 1)})
    once = sb.plan(tick, "B", None, {}, {}, [], True)
    assert once[0].action == "isolate_subsystem"
    th = Threat("ground", "HEATER", (("on", True),), ("B",), 5.0, T0, 6)
    again = sb.plan(tick, "B", None, {}, {}, [], True, [th])
    assert again[0].action in ("safe_mode", "quarantine")
    assert next(s for s in again if s.action == "isolate_subsystem").crossed  # re-latched: 60 C reached


def test_board_flags_within_seconds_with_the_4_tick_window():
    cfg = config.with_twin_sats(config.load(), "A,C-L")
    stage = ForecastStage.build(config.profiles(cfg), cfg)
    assert stage.model_for("B") == "trajectory"  # D7: hardware always on the trajectory forecaster
    rng = np.random.default_rng(0)
    first = None
    for k in range(20):
        temp = 25.0 + 1.5 * k + rng.uniform(-0.1, 0.1)
        r = stage.step(world(T0 + k, {"B": thermal(temp, 1)}), {})["B"]
        if first is None and not r.warming_up and r.p(60) >= 0.30:
            first = (k, temp)
    assert first is not None and first[0] <= 4 and first[1] < 32  # Phase 2 flagged at 2.3 s, 28.5 C
