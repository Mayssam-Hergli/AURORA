"""Generate labelled episodes in the Section 6.3 format (milestone M1 / CONTEXT Task 5).

    python -m ml.synthetic.generate                       # all splits from ml/configs/synthetic.yaml
    python -m ml.synthetic.generate --split train --n 50  # a small batch
Output: data/episodes/<split>/<episode_id>/{telemetry,commands,links}.parquet, labels.json,
ml_labels.json, plus data/episodes/<split>/index.csv (one row per episode). Deterministic by seed.
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from ml import config, schema
from ml.synthetic.engine import SyntheticTwin
from ml.synthetic.scenarios import sample_spec

_NOISE = {}


def _one(args):
    split, index, seed = args
    cfg = config.load("synthetic")
    spec = sample_spec(cfg, split, index, np.random.default_rng(seed))
    bank = None
    if spec.realnoise:
        if not _NOISE:
            from eval.datasets.opssat import load_bank

            _NOISE["bank"] = load_bank()
        bank = _NOISE["bank"]
    ep = SyntheticTwin(cfg, spec, noise_bank=bank).run_episode()
    schema.write_episode(
        config.EPISODES / split, spec.episode_id, ep.telemetry, ep.commands, ep.links, ep.labels, ep.ml_labels
    )
    L = ep.labels
    sats = ep.ml_labels.satellites
    return dict(
        episode_id=spec.episode_id,
        split=split,
        type=L.type,
        scenario=L.scenario,
        kind=L.kind,
        target=L.target,
        onset_t=L.onset_t,
        critical_t=L.critical_t,
        critical_rule=L.critical_rule,
        n_failing=sum(v.critical_t is not None for v in sats.values()),
        **spec.family(),
    )


def generate(split: str, n: int | None = None, workers: int = 4) -> pd.DataFrame:
    cfg = config.load("synthetic")
    n = n or cfg["splits"][split]
    seeds = np.random.default_rng(cfg["split_seeds"][split]).integers(0, 2**31, n)
    jobs = [(split, i, int(s)) for i, s in enumerate(seeds)]
    if workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            rows = list(ex.map(_one, jobs, chunksize=4))
    else:
        rows = [_one(j) for j in jobs]
    index = pd.DataFrame(rows)
    index.to_csv(config.EPISODES / split / "index.csv", index=False)
    return index


def _opssat_available() -> bool:
    """S7: test_real needs OPS-SAT-AD (segments.csv or a built noise bank)."""
    from eval.datasets.opssat import BANK, SRC

    return BANK.exists() or (SRC / "segments.csv").exists()


def main(argv=None):
    cfg = config.load("synthetic")
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=list(cfg["splits"]), default=None)
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    for split in [a.split] if a.split else list(cfg["splits"]):
        if split == "test_real" and not _opssat_available():
            print(
                "test_real  skipped: the OPS-SAT-AD real-noise data is not downloaded "
                "(run `make data`, then `python -m ml.synthetic.generate --split test_real`)",
                flush=True,
            )
            continue
        t0 = time.time()
        idx = generate(split, a.n, a.workers)
        print(
            f"{split:9s} {len(idx):4d} episodes  {idx.kind.value_counts().to_dict()}  "
            f"reach F: {int(idx.critical_t.notna().sum())}  ({time.time() - t0:.0f}s)",
            flush=True,
        )


if __name__ == "__main__":
    main()
