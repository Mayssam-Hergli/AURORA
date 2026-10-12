# ML pipeline (owner: Youssef Mhamdi). Moved verbatim from the top-level Makefile on 2026-10-11.

.PHONY: data models generate cache train evaluate closed-loop robustness stress export figures numbers all

data:            ## CuCD-ID, OPS-SAT-AD and a SCAD subset into data/ (gitignored)
	$(PY) -m eval.datasets.download all

models:          ## trained ensemble + calibration.json from the GitHub release into data/models/ (SHA-256 checked)
	$(PY) -m eval.datasets.download models

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

all: data generate cache train evaluate closed-loop robustness stress export figures numbers test
