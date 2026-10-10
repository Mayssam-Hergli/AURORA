# data/

**Purpose:** Destination for downloaded datasets and generated episodes. **Nothing in this folder is committed** (`data/*` is gitignored, except this README) — datasets are fetched by download scripts, and episodes are generated locally by `twin/`'s episode generator.

**Status:** download scripts exist (`python -m eval.datasets.download all`, checksummed): CuCD-ID, OPS-SAT-AD and a SCAD subset are downloaded and used; the ESA-ADB loader is written but not yet run. Until twin v1 exists, `python -m ml.synthetic.generate` writes stand-in episodes in the Section 6.3 format.

**Owner:** TBD (digital twin, episode generator, evaluation harness).

**Contents (all gitignored):**
- `data/opssat-ad/` — OPS-SAT-AD (Zenodo 12588359), used as a bank of real telemetry noise and gaps (`noise_bank.npz`).
- `data/cache/`, `data/models/` — per-split arrays and trained models written by `ml/` (regenerable).
- `data/scad/` — SCAD dataset (space-cyber-anomaly-detection-dataset), CC BY 4.0. github.com/jwd164/space-cyber-anomaly-detection-dataset
- `data/esa-adb/` — ESA Anomaly Dataset / ESA-ADB. github.com/esa/anomaly-dataset, github.com/kplabs-pl/ESA-ADB
- `data/cucd-id/` — CuCD-ID (NOS3/cFS telemetry with cyber attacks). data.mendeley.com/datasets/7n2d42pm3n/3
- `data/episodes/` — twin-generated episodes (`telemetry.parquet`, `commands.parquet`, `links.parquet`, `labels.json` per satellite episode), per `docs/interfaces.md` Section 6.3.

See `docs/CONTEXT.md` Section 5.6 for licences and dataset details.

**Interfaces it uses:** none directly — this is a data store, not a running service.
