"""Paths and YAML configuration for the ML side (configs live in ml/configs and eval/configs)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"  # gitignored: datasets, episodes, caches, trained models
EPISODES = DATA / "episodes"
CACHE = DATA / "cache"
MODELS = DATA / "models"
RESULTS = ROOT / "results"  # gitignored: metrics, logs, predictions
FIGURES = ROOT / "docs" / "figures"  # committed: figures used by the docs and the paper


def load(name: str) -> dict:
    """Load ml/configs/<name>.yaml, or eval/configs/<name>.yaml for evaluation settings."""
    for folder in (ROOT / "ml" / "configs", ROOT / "eval" / "configs"):
        path = folder / f"{name}.yaml"
        if path.exists():
            return yaml.safe_load(path.read_text())
    raise FileNotFoundError(f"no config named {name!r}")
