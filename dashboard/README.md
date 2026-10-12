# dashboard/

**Purpose:** the operator dashboard on the projector: 12 satellite tiles (state colour, **EMULATED** / **HW** badge, temperature, P(F < t + 60 s)), the focused satellite's P(F) curve with the ±1 std ensemble band and the core's flag/plan thresholds, its temperature and SOC with limits, the last `core/action` with its alternatives and J, the `core/event` log, and the link status (feasible, enabled, cut).

**Status:** done as the **single-page fallback** (MASTER_PLAN cut list item 6): plain HTML/JS, no build step, no database, works offline (MQTT.js 5.16.0 vendored in `vendor/`, MIT). Open MCT was not attempted overnight (11–12 Oct): with the 9am deadline, the time went to the board, the ML fixes and the scenario runs. **Owner:** Mayssam Hergli.

```bash
make up            # broker with the WebSocket listener on 9001
make demo-sw       # twin + core
make dashboard     # http://localhost:8080 (WSL forwards it to Windows); F11 for full screen
```
Click a tile to focus that satellite. The page reconnects by itself (every 2 s) after a broker restart and re-subscribes.

| File | What |
|---|---|
| `index.html`, `style.css` | layout for a 1080p projector, large fonts, dark theme |
| `config.js` | broker URL (`ws://<host>:9001`), history length (15 min), threshold lines |
| `store.js` | all message handling (pure, tested with Node: `tests/dashboard/store_test.js`) |
| `app.js` | MQTT over WebSocket, drawing (canvas), panels |

**Subscriptions:** `sat/+/tm/+`, `sat/+/state`, `link/+`, `core/forecast/+`, `core/action`, `core/event`. Never `gt/` or `redteam/`.

**Verified overnight:** store logic (Node test); the live data path ws://localhost:9001 → `store.js` with twin + core + A1 running (about 67 000 messages in 45 s, E contained and healed). **Not verified:** the page in a real browser (no browser on this machine overnight): open it once before rehearsal.
