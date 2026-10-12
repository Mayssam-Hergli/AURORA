# AURORA. Run from WSL (Ubuntu). Every target uses the repo venv explicitly (CONTEXT Section 10.7).
# Shared targets live here; each lane's targets live in mk/*.mk:
#   mk/core.mk  bus, CASSANDRA core, attacks, demo   (Mayssam)
#   mk/ml.mk    ML pipeline: generate, train, evaluate (Youssef)
#   mk/twin.mk  live twin, recorder, dashboard        (Youssef, when it exists)
#   make setup   -> .venv (Python 3.11) with the pinned set in requirements.lock
#   make lock    -> regenerate requirements.lock from requirements-dev.txt (then re-run make setup)
SHELL := /bin/bash
VENV ?= .venv
PY ?= $(VENV)/bin/python
UV ?= $(HOME)/.local/bin/uv
TORCH_INDEX = --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match
LINT_DIRS = $(wildcard aurora_common cassandra attacks scripts ground hardware ml eval twin tests)

.PHONY: setup lock test lint

setup:           ## needs uv: curl -LsSf https://astral.sh/uv/install.sh | sh
	UV_LINK_MODE=copy $(UV) venv --python 3.11 $(VENV)
	UV_LINK_MODE=copy $(UV) pip sync --python $(PY) requirements.lock $(TORCH_INDEX)

lock:
	$(UV) pip compile requirements-dev.txt --python-version 3.11 $(TORCH_INDEX) -o requirements.lock

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check $(LINT_DIRS)
	$(PY) -m black --check $(LINT_DIRS)

include $(wildcard mk/*.mk)
