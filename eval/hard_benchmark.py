"""Matched-origin test set, held out from training/threshold selection."""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd

from ml import config, schema
from ml.synthetic.engine import SyntheticTwin
from ml.synthetic.scenarios import sample_spec


def generate(pairs=24):
    rows = []
    for i in range(pairs):
        for attack in (True, False):
            cfg = copy.deepcopy(config.load("synthetic"))
            typ = ("A1", "A2", "A3")[i % 3]
            cfg["mix"] = {typ if attack else "N": 1.0}
            cfg["families"]["train"]["precursor"] = ["burst", "sparse", "none"]
            spec = sample_spec(cfg, "test_id", i, np.random.default_rng(90_000 + i))
            spec.seed = 90_000 + i
            spec.precursor = "none"
            spec.episode_id = f"test_hard-{i:03d}-{int(attack)}"
            spec.split = "test_hard"
            if attack:
                spec.target = "E"
                spec.onset_s = 60.0
            twin = SyntheticTwin(cfg, spec)
            if attack:
                paired_onset = int(twin.truth["E"]["exec_t"])
            # Identical diagnostic bursts, rejection counts, timing and origin on both classes.
            for t in range(35, 41):
                twin._cmd(t, "E", "unknown", "PING")
                twin._cmd(t, "E", "ground", "HEATER", {"on": True}, auth_ok=False)
            if not attack:
                cmd, args = {
                    "A1": ("HEATER", {"on": True}),
                    "A2": ("SET_MODE", {"mode": "WHEEL_TEST"}),
                    "A3": ("SET_MODE", {"mode": "SCIENCE"}),
                }[typ]
                twin._cmd(paired_onset, "E", "ground", cmd, args)
                twin._cmd(paired_onset + 30, "E", "ground", "HEATER", {"on": False})
                twin._cmd(paired_onset + 30, "E", "ground", "SET_MODE", {"mode": "IDLE"})
            ep = twin.run_episode()
            schema.write_episode(
                config.EPISODES / "test_hard",
                spec.episode_id,
                ep.telemetry,
                ep.commands,
                ep.links,
                ep.labels,
                ep.ml_labels,
            )
            rows.append(
                dict(
                    episode_id=spec.episode_id,
                    split="test_hard",
                    type=ep.labels.type,
                    kind=ep.labels.kind,
                    scenario=ep.labels.scenario,
                    matched_type=typ,
                    seed=spec.seed,
                    n_failing=sum(x.critical_t is not None for x in ep.ml_labels.satellites.values()),
                )
            )
    pd.DataFrame(rows).to_csv(config.EPISODES / "test_hard" / "index.csv", index=False)


if __name__ == "__main__":
    generate()
