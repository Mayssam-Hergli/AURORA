# Bus, CASSANDRA core, attacks, demo, board (owner: Mayssam Hergli). Included by the top-level Makefile.
#   make up / down / reset          Mosquitto on 1883 (MQTT) + 9001 (WebSockets)
#   make tail [TAIL_ARGS=...]       watch the bus, e.g. make tail TAIL_ARGS="--rate"
#   make bus-check                  publish one sample message and confirm it comes back
#   make twin-replay [SATS=A-L]     alias of `make twin` (twin/service.py, mk/twin.mk)
#   make core [SATS=A-L]            the CASSANDRA core; satellites not in SATS get the board profile
#   make demo-sw [SATS=A-L SPEED=1] up + plant + core in one terminal; Ctrl-C stops both
#                                   with the board as B: make demo-sw SATS=A,C-L (bridge runs on Windows)
#   make demo-board-dry             demo-sw SATS=A,C-L plus the bridge with a fake board (no hardware)
#   make bridge-dry                 the board bridge alone, with a fake board (WSL or Windows)
#   make attack A1 TARGET=E         run a red-team scenario (ATTACK_ARGS="--reassert 5 --duration 120")
#   make attack-stop TARGET=E       release the target (HEATER off, red-team effects cleared)

COMPOSE = docker compose -p aurora-ground -f ground/docker-compose.yml
TAIL_ARGS ?=
SATS ?= A-L
SPEED ?= 1
SEED ?= 0
TARGET ?= E
ATTACK_ARGS ?=
BRIDGE_ARGS ?=
SCENARIOS = A1 B1 C1 N F1 F2 F3 F4 F5
SCENARIO ?= $(or $(firstword $(filter $(SCENARIOS),$(MAKECMDGOALS))),A1)
TWIN = $(PY) -u -m twin.service --sats $(SATS) --speed $(SPEED) --seed $(SEED)
CORE = $(PY) -u -m cassandra --twin-sats $(SATS)

.PHONY: up down reset tail bus-check twin-replay core demo-sw demo-board-dry bridge-dry attack attack-stop $(SCENARIOS)

up:
	$(COMPOSE) up -d
	@for i in $$(seq 1 20); do \
	  (echo > /dev/tcp/127.0.0.1/1883) 2>/dev/null && echo "broker up: localhost:1883 (MQTT), localhost:9001 (WebSockets)" && exit 0; \
	  sleep 0.5; done; echo "broker did not open port 1883"; exit 1

down:
	$(COMPOSE) down

reset:           ## restart clean: persistence is off, so retained states are gone too
	$(COMPOSE) down
	$(MAKE) up

tail:
	$(PY) ground/tools/mqtt_tail.py $(TAIL_ARGS)

bus-check:
	$(PY) ground/tools/bus_check.py

twin-replay:
	$(TWIN)

core:
	$(CORE)

demo-sw: up
	$(PY) scripts/launch.py "twin=$(TWIN)" "core=$(CORE)"

demo-board-dry: up
	$(PY) scripts/launch.py "twin=$(PY) -u -m twin.service --sats A,C-L --speed 1 --seed $(SEED)" \
	  "core=$(PY) -u -m cassandra --twin-sats A,C-L" "board=$(PY) -u hardware/esp8266_bridge/bridge.py --dry-run --sat B $(BRIDGE_ARGS)"

bridge-dry:
	$(PY) hardware/esp8266_bridge/bridge.py --dry-run --sat B $(BRIDGE_ARGS)

attack:
	$(PY) -m attacks.run --scenario $(SCENARIO) --target $(TARGET) $(ATTACK_ARGS)

attack-stop:
	$(PY) -m attacks.run --stop --target $(TARGET)

$(SCENARIOS):    # lets `make attack A1 TARGET=E` name the scenario as a goal
	@:

.PHONY: dashboard
dashboard:       ## single-page dashboard on http://localhost:8080 (needs make up + a running core)
	$(PY) -m http.server 8080 --directory dashboard
