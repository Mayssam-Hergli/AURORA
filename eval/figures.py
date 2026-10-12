"""Figures for the docs and the paper, from the result files only (docs/figures/v1/*.png|pdf).

python -m eval.figures
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from ml import config, features
from ml import graph as G
from ml.model import HORIZONS

BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
NEUTRAL, INK, INK2, GRID, CRIT = "#b9b8b2", "#0b0b0b", "#52514e", "#e6e5e0", "#d03b3b"
plt.rcParams.update(
    {
        "font.size": 8,
        "axes.edgecolor": INK2,
        "axes.labelcolor": INK,
        "xtick.color": INK2,
        "ytick.color": INK2,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "axes.axisbelow": True,
        "savefig.dpi": 220,
        "savefig.bbox": "tight",
        "figure.facecolor": "white",
    }
)
OUT = config.FIGURES / "v1"
EV = config.RESULTS / "eval"
LABEL = {
    "ensemble": "CASSANDRA (5-model ensemble)",
    "single": "Single model",
    "no_eng": "Without E_eng",
    "unconstrained": "Unconstrained edges",
    "no_residual": "Without residuals",
    "threshold": "Static thresholds",
    "trajectory": "Phase-2 trajectory forecaster",
    "lstm_ae": "LSTM autoencoder",
    "gdn": "GDN",
}
SPLIT = {
    "test_id": "In-distribution",
    "test_ood": "Held-out families",
    "test_real": "Synthetic + OPS-SAT noise/gaps",
}


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{name}.png")
    fig.savefig(OUT / f"{name}.pdf")
    plt.close(fig)


def fig_lead(met):
    methods = [k for k in LABEL if k in met["splits"]["test_id"]["methods"]]
    order = sorted(methods, key=lambda k: met["splits"]["test_id"]["methods"][k]["mean_lead_s"])
    gmax = max(
        met["splits"][s]["methods"][k]["mean_lead_s"]
        for s in SPLIT
        for k in order
        if k in met["splits"][s]["methods"]
    )
    fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.3), sharey=True)
    for ax, sp in zip(axes, SPLIT):
        Mt = met["splits"][sp]["methods"]
        vals = [Mt[k]["mean_lead_s"] if k in Mt else 0 for k in order]
        ax.barh(
            range(len(order)), vals, color=[BLUE if k == "ensemble" else NEUTRAL for k in order], height=0.62
        )
        for i, k in enumerate(order):
            if k in Mt:
                ax.text(
                    vals[i] + 1,
                    i,
                    f"{vals[i]:.0f} s · recall {Mt[k]['recall']:.2f}",
                    va="center",
                    fontsize=6.8,
                    color=INK if k == "ensemble" else INK2,
                )
        ax.set_xlim(0, gmax * 1.6 + 1)
        ax.set_title(SPLIT[sp], loc="left", fontsize=8.5, color=INK)
        ax.set_xlabel("Mean warning lead time (s), misses = 0")
        ax.grid(axis="y", visible=False)
    axes[0].set_yticks(range(len(order)), [LABEL[k] for k in order])
    fig.suptitle(
        "Warning lead time before the critical state, tau at 0.1 % FPR on validation clean + fault episodes",
        x=0.01,
        ha="left",
        fontsize=9.5,
        color=INK,
    )
    save(fig, "fig_lead_time")


def fig_types(met):
    bt = met["splits"]["test_id"].get("by_type", {})
    types = [t for t in ["A1", "A2", "A3", "B1", "B2", "C1", "F5"] if t in bt]
    if not types:
        return
    fig, ax = plt.subplots(figsize=(6.4, 2.7))
    ax.bar(range(len(types)), [bt[t]["median_lead_s"] for t in types], color=BLUE, width=0.6)
    for i, t in enumerate(types):
        ax.text(
            i,
            bt[t]["median_lead_s"] + 2,
            f"recall {bt[t]['recall']:.2f}\n(n={bt[t]['failing']})",
            ha="center",
            fontsize=6.5,
            color=INK2,
        )
    ax.set_xticks(range(len(types)), types)
    ax.set_ylabel("Median lead (s)")
    ax.grid(axis="x", visible=False)
    ax.set_title(
        "CASSANDRA by episode type (A attacks, B masking, C propagation, F5 stuck heater)",
        loc="left",
        fontsize=8.5,
        color=INK,
    )
    save(fig, "fig_by_type")


def fig_calibration(met):
    fig, ax = plt.subplots(figsize=(3.6, 2.6))
    for sp, col in zip(SPLIT, (BLUE, ORANGE, AQUA)):
        e = met["splits"][sp]["calibration"]
        ax.plot(HORIZONS, [e[str(h)] for h in HORIZONS], marker="o", color=col, lw=2, label=SPLIT[sp])
    ax.set_xlabel("Horizon D (s)")
    ax.set_ylabel("ECE of P(F < t + D), 10 bins")
    ax.set_xticks(HORIZONS)
    ax.legend(frameon=False, fontsize=6.5)
    save(fig, "fig_calibration")


def fig_scad():
    p = config.RESULTS / "datasets" / "scad_profile.json"
    if not p.exists():
        return
    prof = json.loads(p.read_text())["subsystems"]
    names = list(prof)
    frac = [prof[n]["frac_affected"] for n in names]
    fig, ax = plt.subplots(figsize=(5.2, 2.5))
    ax.barh(range(len(names)), frac, color=BLUE, height=0.6)
    for i, n in enumerate(names):
        d = prof[n]["median_delay_s"]
        ax.text(
            frac[i] + 0.01,
            i,
            f"{frac[i]:.0%} · median delay {d:.0f} s" if d is not None else f"{frac[i]:.0%}",
            va="center",
            fontsize=6.8,
            color=INK2,
        )
    ax.set_yticks(range(len(names)), [n.replace("_", " ") for n in names])
    ax.invert_yaxis()
    ax.set_xlim(0, 1.45)
    ax.set_xlabel("Fraction of SCAD attack runs where the subsystem departs from its matched baseline")
    ax.grid(axis="y", visible=False)
    ax.set_title(
        "SCAD: measured attack propagation after the reaction-wheel override",
        loc="left",
        fontsize=8.5,
        color=INK,
    )
    save(fig, "fig_scad_cascade")


def fig_example(met):
    """One masked-telemetry (B1) test episode: reported vs reality, and the forecast."""
    c, idx = features.load_cache("test_id")
    tau = met["tau"]["ensemble"]
    cand = [e for e in range(len(idx)) if idx.type[e] == "B1" and (c["crit"][e] >= 0).any()]
    if not cand:
        return
    e = cand[0]
    s = int(np.flatnonzero(c["crit"][e] >= 0)[0])
    T = c["raw"].shape[1]
    t = np.arange(T)
    raw = c["raw"][e].astype(np.float32)
    # ensemble mean of the hazard logits, temperature-scaled: the same score as the harness
    members = sorted(EV.glob("pred_full_s*_test_id.npz"))
    bins = np.mean([np.load(m)["bins"][e, :, s].astype(np.float32) for m in members], 0)
    p = 1 - np.cumprod(1 - 1 / (1 + np.exp(-bins / met["temperature"])), -1)
    # timings quoted in the captions, measured on this episode (P(F < t + 60 s) is column 2)
    onset, crit = int(c["onset"][e][c["onset"][e] >= 0].min()), int(c["crit"][e, s])
    p60 = p[:, HORIZONS.index(60)]
    alert = np.flatnonzero(p60[onset:] >= tau)
    high = np.flatnonzero(p60[:crit] >= 0.5)
    (EV / "fig_example.json").write_text(
        json.dumps(
            dict(
                episode=str(idx.episode_id[e]),
                sat=s,
                onset_t=onset,
                crit_t=crit,
                alert_t=int(onset + alert[0]) if len(alert) else None,
                fall_t=int(high[-1] + 1) if len(high) else None,
            )
        )
    )
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(5.8, 4.0), sharex=True)
    a1.plot(t, raw[:, s, G.FLAT.index("temp_c")], color=ORANGE, lw=1.6, label="reported temp_c")
    a1.plot(t, raw[:, s, G.FLAT.index("soc_pct")], color=AQUA, lw=1.6, label="reported soc_pct")
    a1.plot(
        t, raw[:, s, G.FLAT.index("current_ma")] / 100, color=BLUE, lw=1.2, label="current_ma / 100 (INA219)"
    )
    a1.legend(frameon=False, fontsize=6.5, loc="upper left")
    a1.set_ylabel("Telemetry")
    for k, col in zip(range(4), (NEUTRAL, YELLOW, BLUE, INK2)):
        a2.plot(t, p[:, k], color=col, lw=1.6, label=f"P(F < t + {HORIZONS[k]} s)")
    a2.axhline(tau, color=BLUE, lw=0.8, ls=":")
    a2.set_ylabel("Forecast")
    a2.set_xlabel("Time (s)")
    a2.legend(frameon=False, fontsize=6.5, loc="upper left")
    for ax in (a1, a2):
        ax.axvline(c["onset"][e][c["onset"][e] >= 0].min(), color=INK2, lw=0.8)
        ax.axvline(c["crit"][e, s], color=CRIT, lw=1)
    fig.suptitle(
        f"Masked telemetry (B1, test episode {idx.episode_id[e]}): forecast at four horizons",
        x=0.01,
        ha="left",
        fontsize=9,
        color=INK,
    )
    save(fig, "fig_example_masking")


def main():
    met = json.loads((EV / "metrics.json").read_text())
    fig_lead(met)
    fig_types(met)
    fig_calibration(met)
    fig_scad()
    fig_example(met)
    print("figures in", OUT)


if __name__ == "__main__":
    main()
