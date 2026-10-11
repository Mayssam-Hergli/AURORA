"""Download external datasets into data/ (gitignored, never committed; CONTEXT Rule 5).

python -m eval.datasets.download cucd            # CuCD-ID (Mendeley, CC BY 4.0), SHA-256 verified
python -m eval.datasets.download opssat          # OPS-SAT-AD (Zenodo), MD5 verified
python -m eval.datasets.download scad --attack-runs-per-wheel 30   # SCAD subset + matched baselines
python -m eval.datasets.download scad --all      # full SCAD (~1.4 GB)
python -m eval.datasets.download esa-adb --mission 1 --allow-large   # ESA-AD (3.7-4.1 GB per mission)
python -m eval.datasets.download all             # cucd + opssat + SCAD subset
python -m eval.datasets.download models          # trained forecaster (GitHub release), SHA-256 verified
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

from ml.config import DATA, MODELS

UA = {"User-Agent": "aurora-ml-downloader"}
# Built with `python -m ml.export --bundle v1`; a new bundle needs a new tag and hash here.
MODELS_URL = "https://github.com/Mayssam-Hergli/AURORA/releases/download/models-v1/aurora-models-v1.zip"
MODELS_SHA256 = "77cac5191735478f554f4e8a590065e60968a041312f226c6b94545f0d259d48"
MENDELEY = "https://data.mendeley.com/public-api/datasets/7n2d42pm3n"
CUCD_FOLDERS = {
    "Raw": "bf667113-fbe6-43a4-8f8b-bbd3f32db4be",
    "Augmented": "c24df693-2f5b-42cf-a277-d0b5c3bee568",
    "Metadata": "0f7594ae-df6b-4230-ab7d-594c3f60b5fa",
}
OPSSAT_RECORD = "12588359"
ESA_RECORD = "12528696"
SCAD_RAW = "https://raw.githubusercontent.com/jwd164/space-cyber-anomaly-detection-dataset/main"


def _get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        return r.read()


def _fetch(url: str, dest: Path, overwrite: bool = False) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not overwrite:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    with (
        urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r,
        open(tmp, "wb") as f,
    ):
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.replace(dest)
    return dest


def _hash(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def cucd() -> Path:
    out = DATA / "cucd-id"
    for _folder, fid in CUCD_FOLDERS.items():
        files = json.loads(_get(f"{MENDELEY}/files?folder_id={fid}&version=3"))
        for f in files:
            _fetch(f["content_details"]["download_url"], out / f["filename"])
    expected = [
        ln.split()[0]
        for ln in (out / "checksums.txt").read_text().splitlines()
        if ln and not ln.startswith("#")
    ]
    for name in ("consolidated_dataset_raw.csv", "noised_dataset.csv"):
        if _hash(out / name, "sha256") not in expected:
            raise RuntimeError(f"CuCD-ID checksum mismatch for {name}")
    print(f"CuCD-ID ready in {out} (SHA-256 verified)")
    return out


def _zenodo(record: str, out: Path, names: list[str] | None = None, allow_large: bool = False) -> list[Path]:
    meta = json.loads(_get(f"https://zenodo.org/api/records/{record}"))
    paths = []
    for f in meta["files"]:
        if names and f["key"] not in names:
            continue
        if f["size"] > 2e9 and not allow_large:
            print(f"skip {f['key']} ({f['size'] / 1e9:.1f} GB): pass --allow-large and check free disk space")
            continue
        p = _fetch(f["links"]["self"], out / f["key"])
        algo, digest = f["checksum"].split(":")
        if _hash(p, algo) != digest:
            raise RuntimeError(f"checksum mismatch for {p}")
        paths.append(p)
    return paths


def opssat() -> Path:
    out = DATA / "opssat-ad"
    _zenodo(OPSSAT_RECORD, out)
    print(f"OPS-SAT-AD ready in {out} (MD5 verified)")
    return out


def scad(attack_runs_per_wheel: int = 30, all_runs: bool = False, seed: int = 0) -> Path:
    """SCAD subset: N attack runs per reaction wheel plus the matched baseline of each run."""
    out = DATA / "scad"
    meta_path = _fetch(f"{SCAD_RAW}/data/raw/all_metadata.csv", out / "all_metadata.csv")
    meta = pd.read_csv(meta_path)
    if all_runs:
        pick = meta
    else:
        attacks = meta[meta.Scenario != "baseline"]
        pick = attacks.groupby("Scenario").sample(n=attack_runs_per_wheel, random_state=seed)
        keys = pick.Month.astype(str) + "_" + pick.Time_offset.astype(str)
        base = meta[
            (meta.Scenario == "baseline")
            & (meta.Month.astype(str) + "_" + meta.Time_offset.astype(str)).isin(set(keys))
        ]
        pick = pd.concat([pick, base])
    for rel in pick.data_file:
        rel = rel.lstrip("./")
        _fetch(f"{SCAD_RAW}/{rel}", out / rel)
    pick.to_csv(out / "selected_metadata.csv", index=False)
    print(f"SCAD: {len(pick)} runs in {out} ({(pick.Scenario == 'baseline').sum()} baseline)")
    return out


def esa_adb(mission: int, allow_large: bool) -> Path:
    out = DATA / "esa-adb"
    paths = _zenodo(ESA_RECORD, out, [f"ESA-Mission{mission}.zip"], allow_large=allow_large)
    for p in paths:
        with zipfile.ZipFile(p) as z:
            z.extractall(out)
    print(f"ESA-AD mission {mission} in {out}" if paths else "ESA-AD not downloaded")
    return out


def models(url: str = MODELS_URL, sha256: str = MODELS_SHA256) -> Path:
    """Trained ensemble, understanding and calibration into data/models/ (what the core loads)."""
    archive = _fetch(url, DATA / url.rsplit("/", 1)[-1])
    if _hash(archive, "sha256") != sha256:
        archive.unlink()
        raise RuntimeError(f"checksum mismatch for {url}")
    with zipfile.ZipFile(archive) as z:
        z.extractall(MODELS)
    print(f"models ready in {MODELS} (SHA-256 verified)")
    return MODELS


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", choices=["cucd", "opssat", "scad", "esa-adb", "all", "models"])
    ap.add_argument("--attack-runs-per-wheel", type=int, default=30)
    ap.add_argument("--all", action="store_true", help="SCAD: every run (~1.4 GB)")
    ap.add_argument("--mission", type=int, default=1)
    ap.add_argument("--allow-large", action="store_true")
    a = ap.parse_args(argv)
    if a.dataset in ("cucd", "all"):
        cucd()
    if a.dataset in ("opssat", "all"):
        opssat()
    if a.dataset in ("scad", "all"):
        scad(a.attack_runs_per_wheel, a.all)
    if a.dataset == "esa-adb":
        esa_adb(a.mission, a.allow_large)
    if a.dataset == "models":
        models()


if __name__ == "__main__":
    sys.exit(main())
