"""Live use of the online forecaster: no false alarm from telemetry that has not arrived yet, and the
model bundle the team downloads (`make models`) installs with its calibration."""

import hashlib
import json

import pytest

from eval.datasets import download
from ml import config, export, online
from ml.online import OnlineForecaster

SATS = config.load("synthetic")["satellites"]
EPS = {"soc_pct": 80.0, "bus_v": 5.0, "load_w": 0.3, "solar_w": 0.5, "current_ma": 60.0}


def _fallback_p60(temps, eps=True, thermal_from=0):
    """No-model forecaster fed board-like telemetry; P(F < t + 60 s) of satellite B per tick."""
    fc = OnlineForecaster([], SATS)
    out = []
    for k, temp in enumerate(temps):
        t = 1760000000.0 + k
        for s in SATS:
            base = {"v": 1, "sat": s, "src": "hw", "t": t, "seq": k}
            if eps:
                fc.on_telemetry({**base, "sub": "eps", "fields": EPS})
            if k >= thermal_from:
                fc.on_telemetry({**base, "sub": "thermal", "fields": {"temp_c": temp, "heater_on": 0}})
        out.append(next(m for m in fc.tick(t) if m["sat"] == "B")["p_fail"]["60"])
    return out


def test_fallback_ignores_a_channel_that_never_arrives():
    """A board that publishes thermal only: SOC held at 0 is not SOC < SOC_min."""
    assert max(_fallback_p60([25.0] * 20, eps=False)) == 0.0


def test_fallback_ignores_a_late_first_reading():
    """First temperature one tick after the core starts: 0 -> 25 C is not a heating trend."""
    assert max(_fallback_p60([25.0] * 20, thermal_from=1)) == 0.0


def test_fallback_still_sees_a_heating_trend():
    p = _fallback_p60([25.0 + k for k in range(20)])  # +1 C/s, ends at 44 C, 1 s from T_max
    assert p[-1] > 0.9


def test_from_models_warns_without_models(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODELS", tmp_path)
    with pytest.warns(UserWarning, match="make models"):
        fc = OnlineForecaster.from_models()
    assert fc.models == []


def test_model_bundle_round_trip(tmp_path, monkeypatch):
    """pack (ml.export --bundle) -> checksummed fetch (make models) -> calibration found by the core."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "data" / "models").mkdir(parents=True)
    (src / "results" / "eval").mkdir(parents=True)
    names = ["full_s0.pt", "full_s1.pt", "understanding.npz", "understanding.json"]
    for name in names:
        (src / "data" / "models" / name).write_bytes(name.encode())
    metrics = {"temperature": 0.74, "tau": {"ensemble": 0.02}}
    (src / "results" / "eval" / "metrics.json").write_text(json.dumps(metrics))
    monkeypatch.setattr(config, "ROOT", src)
    monkeypatch.setattr(config, "MODELS", src / "data" / "models")
    monkeypatch.setattr(config, "RESULTS", src / "results")

    first = export.bundle("test").read_bytes()
    archive = export.bundle("test")
    assert archive.read_bytes() == first  # reproducible: the release hash stays valid

    monkeypatch.setattr(download, "DATA", dst)
    monkeypatch.setattr(download, "MODELS", dst / "models")
    download.models(archive.as_uri(), hashlib.sha256(first).hexdigest())
    assert sorted(p.name for p in (dst / "models").iterdir()) == sorted(names + ["calibration.json"])

    monkeypatch.setattr(config, "MODELS", dst / "models")
    monkeypatch.setattr(config, "RESULTS", tmp_path / "no-results")
    assert online.calibration_temperature() == 0.74

    with pytest.raises(RuntimeError, match="checksum"):
        download.models(archive.as_uri(), "0" * 64)
