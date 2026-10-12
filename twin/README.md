# twin/

**Purpose:** the live digital twin on the AURORA bus and the episode recorder (MASTER_PLAN Phase 3, CONTEXT 5.3).

**Status:** done (v1 on the synthetic engine): `service.py` and `recorder.py`, branch `p3-twin`. **Owner:** Mayssam Hergli (took over from Youssef Mhamdi on 11 Oct).

| File | What |
|---|---|
| `service.py` | `python -m twin.service` / `make twin`: steps `ml.synthetic.engine.SyntheticTwin` on a clean scenario, publishes 12 satellites (or `--sats A,C-L`) at 1 Hz (`--speed` for fast-forward), applies bus telecommands (SAFE rule of interfaces.md 3.2), red-team effects on `redteam/{id}`, faults with `--fault`, retained initial states, rollover every `--chunk` seconds so memory stays flat |
| `recorder.py` | `python -m twin.recorder` / `make record`: subscribes to `aurora/v1/#` **including `gt/`** (the scorer, never the defence) and writes a Section 6.3 episode (Parquet + `labels.json` + `ml_labels.json` with `t0`); critical times from recorded telemetry |

`scripts/replay_twin.py` is an alias of `service.py` kept for older commands. The sandbox API (`snapshot(include_queue=False)`, `restore`, `apply`, `step`, `seed_from_observation`) is on the engine (`ml/synthetic/engine.py`).

Honesty: everything the twin publishes is simulated (`src: "twin"`); offline training episodes say `src: "synthetic"`.
