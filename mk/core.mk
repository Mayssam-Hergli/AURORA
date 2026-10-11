# Bus, CASSANDRA core, attacks, demo (owner: Mayssam Hergli). Included by the top-level Makefile.
#   make up / down / reset          Mosquitto on 1883 (MQTT) + 9001 (WebSockets)
#   make tail [TAIL_ARGS=...]       watch the bus, e.g. make tail TAIL_ARGS="--rate"
#   make bus-check                  publish one sample message and confirm it comes back
#   make twin-replay [SATS=A-L]     stand-in plant (scripts/replay_twin.py) until twin/service.py exists
#   make core                       the CASSANDRA core (python -m cassandra)
#   make demo-sw [SPEED=1]          up + plant + core in one terminal; Ctrl-C stops both
#   make attack A1 TARGET=E         run a red-team scenario (ATTACK_ARGS="--reassert 5 --duration 120")
#   make attack-stop TARGET=E       release the target (HEATER off, red-team effects cleared)

COMPOSE = docker compose -p aurora-ground -f ground/docker-compose.yml
TAIL_ARGS ?=
SATS ?= A-L
SPEED ?= 1
SEED ?= 0
TARGET ?= E
ATTACK_ARGS ?=
SCENARIOS = A1 B1 C1 N F1 F2 F3 F4 F5
SCENARIO ?= $(or $(firstword $(filter $(SCENARIOS),$(MAKECMDGOALS))),A1)

.PHONY: up down reset tail bus-check twin-replay core demo-sw attack attack-stop $(SCENARIOS)

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
	$(PY) scripts/replay_twin.py --sats $(SATS) --speed $(SPEED) --seed $(SEED)

core:
	$(PY) -m cassandra

demo-sw: up
	$(PY) scripts/launch.py "twin=$(PY) -u scripts/replay_twin.py --sats $(SATS) --speed $(SPEED) --seed $(SEED)" "core=$(PY) -u -m cassandra"

attack:
	$(PY) -m attacks.run --scenario $(SCENARIO) --target $(TARGET) $(ATTACK_ARGS)

attack-stop:
	$(PY) -m attacks.run --stop --target $(TARGET)

$(SCENARIOS):    # lets `make attack A1 TARGET=E` name the scenario as a goal
	@:
