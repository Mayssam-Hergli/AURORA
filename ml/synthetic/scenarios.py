"""Episode specifications: the red-team and fault library of docs/CONTEXT.md Section 5.7.

    A1 heater latch (thermal -> power)          B1 telemetry freeze/replay while a real effect runs
    A2 wheel torque bias (attitude -> power)    B2 slow bias injected into one reported channel
    A3 malicious schedule draining power        C1 propagation to neighbours over feasible ISLs
    F1 sensor drift   F2 SEU bit-flip   F3 battery ageing   F4 eclipse entry   F5 stuck heater
    N  clean operations

Generator families (drift shape, noise model, precursor style, intensity range) are split
between training and the held-out test so that a model cannot score well by learning the
generator's own regularities (CONTEXT Section 5.5, "Splits").
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

SCENARIO_OF = {
    "A1": "A",
    "A2": "A",
    "A3": "A",
    "B1": "B",
    "B2": "B",
    "C1": "C",
    "F1": "F",
    "F2": "F",
    "F3": "F",
    "F4": "F",
    "F5": "F",
    "N": "N",
}
KIND_OF = {k: ("clean" if v == "N" else "fault" if v == "F" else "attack") for k, v in SCENARIO_OF.items()}
DRIFTS = ["ramp", "saturating", "pulsed"]
NOISES = ["gauss", "ar1", "student_t"]
PRECURSORS = ["burst", "sparse", "none"]


@dataclass
class EpisodeSpec:
    episode_id: str
    type: str  # A1 ... N
    seed: int
    split: str
    target: str | None = None  # target satellite id
    onset_s: float | None = None  # seconds after the episode start
    drift: str = "ramp"
    noise: str = "gauss"
    precursor: str = "none"
    intensity: float = 1.0
    channel: str | None = None  # reported channel for B1/B2/F1/F2
    n_victims: int = 0  # C1
    realnoise: bool = False
    t0_orbit_s: float = 0.0  # orbit time of the first tick (varies geometry per episode)
    extra: dict = field(default_factory=dict)

    @property
    def scenario(self) -> str:
        return SCENARIO_OF[self.type]

    @property
    def kind(self) -> str:
        return KIND_OF[self.type]

    def family(self) -> dict:
        return dict(
            drift=self.drift,
            noise=self.noise,
            precursor=self.precursor,
            intensity=round(self.intensity, 3),
            realnoise=self.realnoise,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def _choice(rng, items):
    return items[int(rng.integers(len(items)))]


def sample_spec(cfg: dict, split: str, index: int, rng: np.random.Generator) -> EpisodeSpec:
    mix = cfg["mix_test_real"] if split == "test_real" else cfg["mix"]
    types, p = zip(*mix.items())
    etype = str(rng.choice(types, p=np.array(p) / sum(p)))
    fam_tr, fam_ood = cfg["families"]["train"], cfg["families"]["ood"]
    drift, noise = _choice(rng, fam_tr["drift"]), _choice(rng, fam_tr["noise"])
    lo, hi = fam_tr["intensity"]
    intensity = float(rng.uniform(lo, hi))
    if split == "test_ood":
        # at least one held-out knob, the others drawn from the union
        held = _choice(rng, ["drift", "noise", "intensity"])
        drift = _choice(rng, fam_ood["drift"]) if held == "drift" else _choice(rng, DRIFTS)
        noise = _choice(rng, fam_ood["noise"]) if held == "noise" else _choice(rng, NOISES)
        if held == "intensity" or rng.random() < 0.3:
            intensity = float(rng.uniform(*fam_ood["intensity"]))
    precursor = str(rng.choice(fam_tr["precursor"], p=[0.4, 0.4, 0.2]))
    sats = cfg["satellites"]
    spec = EpisodeSpec(
        episode_id=f"{split}-{index:05d}",
        type=etype,
        seed=int(rng.integers(2**31)),
        split=split,
        drift=drift,
        noise=noise,
        precursor=precursor,
        intensity=intensity,
        realnoise=split == "test_real",
        t0_orbit_s=float(rng.uniform(0, 86400)),
    )
    if etype == "N":
        return spec
    spec.target = _choice(rng, sats)
    spec.onset_s = float(rng.uniform(*cfg["timing"]["onset_s"]))
    if etype in ("B1", "B2"):
        spec.channel = "temp_c" if etype == "B2" else _choice(rng, ["temp_c", "soc_pct"])
    elif etype == "F1":
        spec.channel = _choice(rng, ["temp_c", "soc_pct", "att_err_deg"])
    elif etype == "F2":
        spec.channel = _choice(rng, ["temp_c", "soc_pct", "att_err_deg"])
    if etype == "C1":
        spec.n_victims = int(rng.integers(1, 3))
        spec.extra["victim_attack"] = _choice(rng, ["A1", "A2"])
    if spec.kind == "fault":
        spec.precursor = "none"
    return spec
