# ground/

**Purpose:** the ground-station bus. Mosquitto carries every `aurora/v1/...` topic (`docs/interfaces.md`) on port **1883** (MQTT) and **9001** (MQTT over WebSockets, for the dashboard). Runs in Docker inside WSL today; the same compose file is meant for the Raspberry Pi later.

**Status:** done (Phase 1, gate G1).

**Owner:** Mayssam Hergli.

```bash
make up          # start the broker (compose project "aurora-ground", container aurora_mqtt)
make tail        # watch the bus; TAIL_ARGS="--rate", "--check", "--gt", "--filter 'aurora/v1/sat/E/#'"
make bus-check   # publish one message of each kind and confirm the round trip + retained state
make reset       # restart clean (persistence is off, so retained states are cleared)
make down
```

| File | What it does |
|---|---|
| `docker-compose.yml` | `eclipse-mosquitto:2`, pinned by digest; ports 1883 and 9001 |
| `mosquitto/mosquitto.conf` | listeners, anonymous access (demo LAN only), no persistence |
| `tools/mqtt_tail.py` | one line per message: `topic \| src \| t \| payload`; hides `gt/` unless `--gt` |
| `tools/bus_check.py` | end-to-end check used by gate G1 |

**Notes**
- Port 6379 belongs to the Phase 2 Redis fallback (`sat_b_redis`); this stack never touches it.
- Windows programs (the board bridge) reach the broker at `localhost:1883`, forwarded by WSL. If that ever fails, use the address printed by `hostname -I` in WSL.
- `mqtt_tail.py --gt` shows ground truth to humans. The CASSANDRA core never subscribes to `gt/` or `redteam/` (`aurora_common.topics.CORE_ALLOWED_SUBSCRIPTIONS`).
