# twin/

**Purpose:** The live digital twin, the episode recorder and the software-only demo launcher (docs/MASTER_PLAN.md Phase 3). The twin runs the 12-satellite physics of `ml/synthetic/engine.py` (`SyntheticTwin`) on a clean live scenario and puts it on the bus in contract form; attacks arrive only over the bus.

**Status:** live twin, recorder, `send_tc` and launcher done (G3); `make demo-sw` starts the core as soon as `cassandra/__main__.py` exists (Phase 4, Mayssam).

**Owner:** Youssef Mhamdi.

| File | What it does |
|---|---|
| `service.py` | `python -m twin.service --sats A-L|A,C-L --speedup 8 --rate 1`: steps the engine once per tick; publishes `sat/{id}/tm/{sub}` (`src: "twin"`), `link/{a}-{b}`, the background `tc` traffic, `sat/{id}/state` once at startup, and `gt/event` `critical` when a satellite truly reaches F. Applies `sat/+/tc` through the engine (SAFE rules of interfaces.md 3.2 live in the engine) and `redteam/+` (`freeze_tm`, `bias_tm`, `relay_tc`, `stop`). Runs indefinitely: segments of `segment_s` roll over with state, seq and orbit time carried over |
| `recorder.py` | `python -m twin.recorder --duration 60`: bus → `data/episodes/live/<time>/` (Section 4 format + `forecasts.parquet`, `core.jsonl`); label files only when a `gt/episode` described the run |
| `send_tc.py` | `python -m twin.send_tc E HEATER on` |
| `launch.py` | `python -m twin.launch`: twin + core, one Ctrl-C stops both (`make demo-sw` = `make up` + this) |
| `config.yaml` | seed, segment length, orbit speed-up, relay delay |

**Interfaces it uses:** publishes and consumes the topics of `docs/interfaces.md` through `aurora_common` (every message is validated before it is sent). The twin subscribes to `sat/+/tc` and `redteam/+`, never to `gt/`; the recorder subscribes to everything because it is the scorer, not the defence.

**Honesty:** everything the twin publishes is simulated (`src: "twin"`); the physics is the synthetic generator's modelling (ml/configs/synthetic.yaml), not a flight model. Red-team effects change what is reported, never the physics; the `critical` ground-truth event comes from the simulated true state.
