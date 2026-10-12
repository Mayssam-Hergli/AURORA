# Live twin, recorder, software-only demo (owner: Youssef Mhamdi). Included by the top-level Makefile.
#   make twin [TWIN_ARGS="--sats A,C-L"]      12 simulated satellites on the bus at 1 Hz (src twin)
#   make record [REC_ARGS="--duration 60"]    bus -> data/episodes/live/<time>/ (gt included: the scorer)
#   make demo-sw                              broker + twin (A-L) + core; one Ctrl-C stops everything
#   make tc TC="E HEATER on"                  send one telecommand

TWIN_ARGS ?=
REC_ARGS ?=
TC ?=

.PHONY: twin record demo-sw tc

twin:
	$(PY) -m twin.service $(TWIN_ARGS)

record:
	$(PY) -m twin.recorder $(REC_ARGS)

demo-sw: up
	$(PY) -m twin.launch

tc:
	$(PY) -m twin.send_tc $(TC)
