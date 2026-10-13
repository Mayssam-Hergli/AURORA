# dashboard/

**Purpose:** The operator screen for the projector (docs/MASTER_PLAN.md Phase 7): one page, no tabs, read-only, over MQTT WebSockets (port 9001). It shows where the 12 satellites are, what CASSANDRA is doing in plain language, the options it compared, and the satellite at risk.

**Status:** redesigned 12 Oct ("dark cockpit"), checked with Playwright against the live core at 1920×1080 and 1280×720, and with contrast reduced 30 % to simulate a projector. The data plumbing (mqtt.js, topics, reconnect, `store.js`) is Youssef's single-page fallback (cut list 6, instead of Open MCT).

**Owners:** Youssef Mhamdi (data layer), Mayssam Hergli (presentation).

## Run

```bash
make up                                    # Mosquitto with the WebSocket listener on 9001 (ground/)
make demo-sw                               # twin + core (or make demo-board-dry, or the real board)
make dashboard                             # http://localhost:8080  (F11 for full screen)
```

Without `make`: `python -m http.server 8080 --bind 127.0.0.1 --directory dashboard`. No build step, no internet: mqtt.js 5.16.0 and uPlot 1.6.32 are vendored in `vendor/` (MIT, SHA-256 in `vendor/README.md`), and the Barlow font is in `fonts/` (SIL OFL 1.1, `fonts/OFL.txt`).

Settings are in `config.js`, each overridable in the URL: `?theme=dark`, `?follow=E` (pin the bottom strip), `?broker=ws://192.168.1.10:9001`.

## Design rules ("dark cockpit")

- **Nominal is quiet and colourless.** Colour only marks an abnormal state, so on stage the attacked satellite is the only colour on screen: amber `#D98E04` at risk (flagged), red `#C0392B` contained / quarantined / cut link, blue `#2E6DA4` safe hold or healing (contained and cooling below 30 °C). Same semantics in `?theme=dark`.
- Sentence case, no monospace, no raw jargon on the main screen (no "P60", "tgn-v1", `isolate_subsystem`). Barlow with tabular figures; 15 / 18 / 24 / 36 / 56 px at 1920×1080.
- The only motion that is not user-triggered: a rerouted link draws in once (off with `prefers-reduced-motion`). Satellites move because they orbit.
- Everything is readable without clicking.

## The screen

| Area | Shows | Source |
|---|---|---|
| Header | "AURORA ground segment", UTC clock, "live" / "reconnecting…" | browser clock, MQTT connection |
| Left, ~60 % | Constellation seen from above the North Pole: 3 orbital planes, 4 satellites each, at the plane and phase the twin flies. Hollow circle = simulated, filled square = physical board. Active links thin ink, cut links red dashed, reroutes drawn in. Temperature next to a marker only when it is not nominal | `comms.plane` / `comms.phase_deg` (extra telemetry fields from the twin; a satellite the twin does not publish, e.g. the board, is placed from a plane-mate), `link/+` |
| Right, ~40 % | "What CASSANDRA is doing": one plain sentence per event, e.g. "B: heater switched on by a ground command", "B: failure risk 62% within 60 s (alert level 30%)", "B contained: heater cut. Limit avoided by 23 s." Below: "Options compared (lower cost is better)", the chosen action and the alternatives as bars with their J. Empty: "Watching 12 satellites. No anomalies." | `core/event` (`data`: event, cause, p, eta_s, action…), `core/action` |
| Bottom | The satellite at risk (auto-follows the abnormal / highest-risk satellite; B when all is nominal): temperature on a fixed axis (ambient − 5 to limit + 5) with the limit in red; "Failure risk, next 60 s" (0–1) with the decision threshold and a ±σ band only when the forecast has `p_std`; a "limit in N s" countdown from the forecaster's ETA (hidden when there is none, or once contained); the forecaster in words | `sat/+/tm/thermal`, `core/forecast/+`, `core/event.data.eta_s` |
| Footer | Honesty note (simulated vs physical, forecasts are model outputs) | — |

`/detail/` (not used in the demo): every satellite's numbers (battery shows "n/a (board has no battery)" for the board), temperature / battery / pointing charts of all 12, the link matrix and the telecommand log.

## Files

| File | What it does |
|---|---|
| `store.js` | data layer, no DOM: topic parsing, per-satellite state and history, orbit positions, colour roles, plain-language timeline (`narrate`), the satellite at risk, the ETA countdown |
| `app.js` | MQTT over WebSocket (auto-reconnect, re-subscribe), SVG constellation, timeline, option bars, uPlot plots |
| `index.html`, `style.css`, `fonts.css`, `config.js` | layout, palette as CSS variables (light and dark), settings |
| `detail/` | the secondary page |
| `tests/store.test.js` | `node --test "dashboard/tests/*.test.js"` (also run by `tests/test_dashboard.py`) |

**Interfaces it uses:** reads `sat/+/tm/+`, `sat/+/tc`, `sat/+/state`, `link/+`, `core/forecast/+`, `core/action`, `core/event` (docs/interfaces.md). It never subscribes to `gt/` or `redteam/` and never publishes. Text from the bus is escaped before display. The extra fields it relies on (`comms.plane`, `comms.phase_deg`, `core/event.data`) are allowed by the contract (3.1 extra fields; 3.7 `data` is an optional object); without them it still works (default orbit layout, raw events are not narrated).
