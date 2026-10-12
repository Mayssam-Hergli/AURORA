"""Export the forecaster ensemble to TorchScript and measure per-tick latency (CONTEXT 5.5).

    python -m ml.export                    # data/models/ts/<name>.ts.pt + results/latency.json
    python -m ml.export --bench-only       # latency of the eager models on this machine
    python -m ml.export --bundle v1        # dist/aurora-models-v1.zip for a GitHub release

Run the same command on the Raspberry Pi to get the number the paper needs (budget: 150 ms per
tick for inference + planning). TorchScript files are loaded with torch.jit.load and need only
`torch` on the target, not this repository.

The bundle is how the team gets the trained ensemble (data/models/ is gitignored): the release
asset is fetched and checksummed by `make models` (eval/datasets/download.py).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
import zipfile
from pathlib import Path

import numpy as np
import torch

from ml import config
from ml import graph as G
from ml.calibration import read_calibration
from ml.train import load_model


def example_inputs(n_sats=12, window=30):
    return torch.zeros(1, window, n_sats, len(G.FLAT)), torch.zeros(
        1, n_sats * (n_sats - 1) // 2, dtype=torch.bool
    )


class _Wrapped(torch.nn.Module):
    """Tuple output (TorchScript tracing does not keep dict outputs stable across versions)."""

    def __init__(self, m):
        super().__init__()
        self.m = m

    def forward(self, raw, links):
        o = self.m(raw, links)
        return o["p_fail"], o["bin_logits"], o["next_logits"], o["mal_logit"]


def export(names: list[str]) -> list[str]:
    out_dir = config.MODELS / "ts"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    raw, lk = example_inputs()
    for n in names:
        m = _Wrapped(load_model(n)).eval()
        with torch.no_grad():
            ts = torch.jit.trace(m, (raw, lk), check_trace=False)
        p = out_dir / f"{n}.ts.pt"
        ts.save(str(p))
        ref, got = m(raw + 1.0, lk)[0], torch.jit.load(str(p))(raw + 1.0, lk)[0]
        assert torch.allclose(ref, got, atol=1e-5), f"TorchScript mismatch for {n}"
        paths.append(str(p))
    return paths


@torch.no_grad()
def latency(models, threads: int = 1, repeats: int = 50) -> dict:
    torch.set_num_threads(threads)
    raw, lk = example_inputs()
    raw = raw + torch.randn_like(raw)
    for m in models:
        m(raw, lk)  # warm-up
    ts = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        for m in models:
            m(raw, lk)
        ts.append((time.perf_counter() - t0) * 1000)
    return dict(
        threads=threads,
        ensemble_size=len(models),
        median_ms=float(np.median(ts)),
        p95_ms=float(np.percentile(ts, 95)),
        machine=platform.machine(),
        processor=platform.processor(),
        note="inference only; add the sandbox planning time for the 150 ms budget",
    )


def _entry(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))  # fixed time: same files, same zip
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 0
    return info


def bundle(tag: str, out_dir: Path | None = None) -> Path:
    """Zip what OnlineForecaster.from_models needs: ensemble, understanding, calibration."""
    members = sorted(config.MODELS.glob("full_s*.pt"))
    if not members:
        raise FileNotFoundError(f"no full_s*.pt in {config.MODELS}: train or copy the ensemble first")
    files = members + [config.MODELS / "understanding.npz", config.MODELS / "understanding.json"]
    calibration = read_calibration(required=True)  # written by ml.train + eval.harness (M5)
    out = (out_dir or config.ROOT / "dist") / f"aurora-models-{tag}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w") as z:
        for p in files:
            z.writestr(_entry(p.name), p.read_bytes())
        z.writestr(_entry("calibration.json"), json.dumps(calibration, indent=1))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench-only", action="store_true")
    ap.add_argument("--bundle", metavar="TAG", help="zip the ensemble into dist/aurora-models-TAG.zip")
    a = ap.parse_args(argv)
    if a.bundle:
        out = bundle(a.bundle)
        print(f"{out}\nsha256 {hashlib.sha256(out.read_bytes()).hexdigest()}")
        return
    names = sorted(p.stem for p in config.MODELS.glob("full_s*.pt"))
    res = {}
    if a.bench_only:
        models = [load_model(n) for n in names]
    else:
        paths = export(names)
        res["exported"] = paths
        models = [torch.jit.load(p) for p in paths]
    res["latency"] = [latency(models, t) for t in (1, 4)]
    config.RESULTS.mkdir(parents=True, exist_ok=True)
    (config.RESULTS / "latency.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res["latency"], indent=1))


if __name__ == "__main__":
    main()
