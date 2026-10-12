# Live twin and recorder (Phase 3). Included by the top-level Makefile.
#   make twin [SATS=A-L SPEED=1 SEED=0 TWIN_ARGS="--fault F5:E@60"]   the live twin on the bus
#   make record [RECORD_ARGS="--duration 60 --out episodes/x"]       record the bus into a 6.3 episode
#   make tc TC="E HEATER on=true"                                     send one telecommand (scripts/send_tc.py)
#   make dashboard-test                                               dashboard data-layer tests (Node, no npm)

TWIN_ARGS ?=
RECORD_ARGS ?=
TC ?=

.PHONY: twin record tc dashboard-test

twin:
	$(PY) -u -m twin.service --sats $(SATS) --speed $(SPEED) --seed $(SEED) $(TWIN_ARGS)

record:
	$(PY) -u -m twin.recorder --twin-sats $(SATS) $(RECORD_ARGS)

tc:
	$(PY) scripts/send_tc.py $(TC)

dashboard-test:
	node --test "dashboard/tests/*.test.js"
