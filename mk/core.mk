# Bus, CASSANDRA core, attacks, demo (owner: Mayssam Hergli). Included by the top-level Makefile.
#   make up / down / reset     Mosquitto on 1883 (MQTT) + 9001 (WebSockets)
#   make tail [TAIL_ARGS=...]  watch the bus, e.g. make tail TAIL_ARGS="--rate"
#   make bus-check             publish one sample message and confirm it comes back

COMPOSE = docker compose -p aurora-ground -f ground/docker-compose.yml
TAIL_ARGS ?=

.PHONY: up down reset tail bus-check

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
