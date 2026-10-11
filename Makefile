# AURORA. Run from WSL (Ubuntu). Every target uses the repo venv explicitly (CONTEXT Section 10.7).
#   make setup   -> .venv (Python 3.11) with the pinned set in requirements.lock
#   make lock    -> regenerate requirements.lock from requirements-dev.txt (then re-run make setup)
VENV ?= .venv
PY ?= $(VENV)/bin/python
UV ?= $(HOME)/.local/bin/uv
TORCH_INDEX = --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match

.PHONY: setup lock data generate cache train evaluate closed-loop robustness stress export figures numbers test lint all

setup:           ## needs uv: curl -LsSf https://astral.sh/uv/install.sh | sh
	UV_LINK_MODE=copy $(UV) venv --python 3.11 $(VENV)
	UV_LINK_MODE=copy $(UV) pip sync --python $(PY) requirements.lock $(TORCH_INDEX)

lock:
	$(UV) pip compile requirements-dev.txt --python-version 3.11 $(TORCH_INDEX) -o requirements.lock

data:            ## CuCD-ID, OPS-SAT-AD and a SCAD subset into data/ (gitignored)
	$(PY) -m eval.datasets.download all

generate:        ## M1: synthetic episodes in the Section 6.3 format
	$(PY) -m ml.synthetic.generate

cache:
	$(PY) -m ml.features

train:           ## M2: 5-model ensemble + ablations (resumable)
	$(PY) -m ml.train all --parallel 1

evaluate:        ## M4: forecaster, ablations and baselines on every test split
	$(PY) -m eval.harness run --workers 1

closed-loop:
	$(PY) -m eval.closed_loop --episodes 24

robustness:
	$(PY) -m eval.robustness --episodes 12

stress:          ## cyber-evidence stress tests (origin spoofing, no telecommands)
	$(PY) -m eval.stress

export:          ## TorchScript + latency (run this one on the Raspberry Pi too)
	$(PY) -m ml.export

figures:
	$(PY) -m eval.figures

numbers:         ## results -> docs/paper, docs/technical_report, README
	$(PY) -m eval.paper_numbers

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check ml eval tests
	$(PY) -m black --check ml eval tests

all: data generate cache train evaluate closed-loop robustness stress export figures numbers test
