# eval/

**Purpose:** The evaluation harness — runs every method on the same episodes and writes tables and figures for the paper. Implements CONTEXT Section 5.8: alert time, warning lead time (median, IQR, 95 % bootstrap CI), detection delay, episode-level precision/recall, next-affected-subsystem accuracy (top-1/top-3), expected calibration error (10 bins), malicious-vs-fault AUROC, response effectiveness (closed loop), robustness (PGD) and latency. Also hosts the dataset downloaders and loaders (CuCD-ID, OPS-SAT-AD, SCAD, ESA-ADB).

**Status:** done (v1) for synthetic episodes and SCAD profiling; ESA-ADB loader written but not run (3.7–4.1 GB per mission).

**Owner:** Youssef Mhamdi (with the digital-twin / evaluation owner once assigned).

| Module | What it does |
|---|---|
| `harness.py` | predictions for every model and baseline on every split (resumable), then `results/eval/metrics.json` + `RESULTS.md` |
| `metrics.py` | Section 5.8 definitions (tau at 0.1 % FPR on benign = clean + fault timesteps) |
| `closed_loop.py` | forecast → sandbox J(a) → act → verify on the twin (reference implementation for the core) |
| `robustness.py` | PGD on forgeable housekeeping channels |
| `stress.py` | cyber-evidence stress tests: origin spoofing, no telecommands (physics-only lower bound) |
| `figures.py`, `paper_numbers.py` | figures into `docs/figures/v1/`, numbers into the paper, report and README |
| `datasets/` | `download.py` (checksummed), `cucd.py` (shortcut audit, leave-one-attack-out), `opssat.py` (real-noise bank), `scad.py` (matched-baseline cascade profile, F definition), `esa_adb.py` |

```bash
python -m eval.datasets.download all     # data/ (gitignored)
python -m eval.harness run --workers 1   # results/eval/
python -m eval.closed_loop --episodes 24
python -m eval.robustness --episodes 12
python -m eval.figures && python -m eval.paper_numbers
```

**Interfaces it uses:** episodes in the Section 6.3 format; reads ground truth (`labels.json`, `ml_labels.json`) — the one place this is allowed, alongside the forecaster's outputs.
