# dashboard/

**Purpose:** The operator dashboard for the projector (docs/MASTER_PLAN.md Phase 7). A single page that subscribes to the bus over MQTT WebSockets (port 9001) and shows the constellation, the focused satellite's failure probability, the core's decisions and event log, the link matrix and the telecommand log.

**Status:** in progress. This is the plan's cut-list fallback (Section 5, item 6: "a single-page uPlot dashboard over WebSocket with the same views") instead of NASA Open MCT: Open MCT 4.3.1 is 34 MB unpacked and could not be installed on the development machine (disk full). The data path is tested (unit tests, and a live run against the twin over WebSockets); the views of forecasts, decisions and events still need the running core (Phase 4) and a browser check on the demo laptop.

**Owner:** Youssef Mhamdi.

## Run

```bash
make up                                    # Mosquitto with the WebSocket listener on 9001 (ground/)
make twin                                  # 12 twin satellites (or make demo-sw: twin + core)
make dashboard                             # http://localhost:8080  (F11 for full screen)
```

Without `make`: `python -m http.server 8080 --bind 127.0.0.1 --directory dashboard`. The page needs no build step and no internet: the two libraries are vendored in `vendor/` (mqtt.js 5.16.0, uPlot 1.6.32, MIT; versions and SHA-256 in `vendor/README.md`).

Settings are in `config.js`, and each can be overridden in the URL: `?broker=ws://192.168.1.10:9001&focus=E&act=0.85`. The Windows Mosquitto service has no WebSocket listener by default; use the broker in `ground/` (Docker) or start Mosquitto with `ground/mosquitto/mosquitto.conf`.

## Views

| Key | View | Shows |
|---|---|---|
| `1` | Overview (the demo screen) | 12 tiles: state colour (green nominal, red flagged, orange quarantined, amber safe hold), **EMULATED** / **HW** badge, temperature (`*` = emulated by firmware), SOC, P(F<60 s) and model. Focused satellite (click a tile; auto-focus on a new flag or quarantine): P(F<30 s) and P(F<60 s) with ±σ band and the flag / act lines, temperature, SOC. Last `core/action` with J, gate, Q and alternatives; `core/event` log |
| `2` | Links | 12 × 12 matrix: up, cut (feasible but disabled: quarantine or reroute), no line of sight |
| `3` | Satellites | temperature, SOC and attitude error of all 12; telecommand log with origin highlighted (`isl:*`, `unknown`) |

## Files

| File | What it does |
|---|---|
| `store.js` | data layer, no DOM: topic parsing, per-satellite state, 15-min history, link matrix, events (`node --test "dashboard/tests/*.test.js"`) |
| `app.js` | MQTT over WebSocket (auto-reconnect, re-subscribe), rendering, uPlot charts |
| `index.html`, `style.css`, `config.js` | layout for 1080p at 5 m, settings |

**Interfaces it uses:** reads `sat/+/tm/+`, `sat/+/tc`, `sat/+/state`, `link/+`, `core/forecast/+`, `core/action`, `core/event` (docs/interfaces.md). It never subscribes to `gt/` or `redteam/` and never publishes. Text from the bus is escaped before display.
