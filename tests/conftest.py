"""Shared fixtures: a few small synthetic episodes generated on the fly (no dependency on data/)."""

from __future__ import annotations

import numpy as np
import pytest

from ml import config, features, schema
from ml.synthetic.engine import SyntheticTwin
from ml.synthetic.scenarios import sample_spec


def make_spec(etype: str, seed: int = 3, target: str = "B"):
    cfg = config.load("synthetic")
    spec = sample_spec(cfg, "train", seed, np.random.default_rng(seed))
    spec.type = etype
    spec.target = None if etype == "N" else target
    spec.onset_s = None if etype == "N" else 150.0
    spec.precursor = "burst" if etype[0] in "ABC" else "none"
    spec.channel = "temp_c" if etype in ("B1", "B2", "F1", "F2") else None
    spec.n_victims = 2 if etype == "C1" else 0
    spec.noise, spec.drift, spec.intensity = "gauss", "saturating", 1.0
    return cfg, spec


@pytest.fixture(scope="session")
def episodes(tmp_path_factory):
    root = tmp_path_factory.mktemp("episodes")
    out = {}
    for etype in ("N", "A1", "F5", "B1"):
        cfg, spec = make_spec(etype)
        ep = SyntheticTwin(cfg, spec).run_episode()
        schema.write_episode(
            root, spec.episode_id + etype, ep.telemetry, ep.commands, ep.links, ep.labels, ep.ml_labels
        )
        out[etype] = (root / (spec.episode_id + etype), ep)
    return out


@pytest.fixture(scope="session")
def understanding(episodes):
    raws = {k: features.episode_arrays(p)["raw"] for k, (p, _) in episodes.items()}
    return features.Understanding().fit(np.stack([raws["N"], raws["F5"]]), np.stack([raws["A1"], raws["B1"]]))
