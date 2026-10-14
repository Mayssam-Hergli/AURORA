# AURORA numbers for the paper and slides

Every number here comes from a file in `results/` produced by code, with the command that made it. Simulation only (synthetic plant / digital twin); nothing here is flight data. Block 7 adds the forecasting and closed-loop headline numbers from `results/manifest.json`.

## Attack-chain correlation (incident correlator, 14 Oct)

Source: `results/incident_metrics.json`, command `python scripts/incident_metrics.py` (core in closed loop with the twin, in process, learned ensemble for twin satellites). Each chain is built only from what the core observes: telecommand origins, monitor hits, forecasts, and the core's own actions. The core never reads ground truth (`tests/cassandra/test_gt_isolation.py`).

| Scenario | Runs | Chain built | Chain-assembly delay, first observed evidence → incident opened | Steps in the chain |
|---|---|---|---|---|
| A1 cyber-to-physical (one latched heater command) | 3 (seeds 1–3) | 3 / 3 | 22, 52, 197 s (median **52 s**) | command → effect → predicted → action → healed (all 3) |
| B1 telemetry masking (heater + frozen report) | 3 (seeds 1–3) | 3 / 3 | 8, 8, 8 s (median **8 s**) | command → **deception** → effect (estimated) → predicted → action → healed (all 3) |
| C1 propagation (compromised neighbour relays) | 6 (seeds 1–6) | 6 / 6 | 0 s in all 6 (a relayed hazardous command is evidence on its own) | relayed commands → source quarantined → victim effect / predicted → victim action |

How to read the delay:
- **A1:** the first evidence is the ground command itself, which looks legitimate. The incident opens when the forecaster first flags the satellite (risk above its alert level), so the delay is the forecaster's warning time after the command. It is not a correlation cost.
- **B1:** 8 s is the monitor's confirmation window for the frozen report.
- **C1:** 0 s, because the relayed command is evidence by itself.

**C1, victims touched before the source was contained:** **1** in each of the 6 runs (the first relay, sent 2 s after the compromise, before anything is observable). Source: `results/incident_metrics.json` (`summary.C1.victims_touched_before_source_contained`). With the pre-13-Oct sandbox (no future-victim term), the same 6 seeds touched 10 victims in total and contained the source in 5 of 6 (`results/phase6_c1_prefix.json`; docs/STATUS.md Block 3).

Caveats:
- In C1 seed 4 the victim's chain also contains a deception step: the monitor flagged the victim's heating as slower than plausible (an equilibrium edge case of the heater-consistency rule), not a masking attack.
- Some runs also open *suspected* incidents (a risk flag after a routine ground heater command) that close by themselves as dismissed. They are never shown as attack chains (`unconfirmed_incidents` in the file).
- The chains are SPARTA-tagged with the ids of `docs/interfaces.md` Section 7 only: IA-0009 / IA-0003 for the commands, DE-0002 (and EX-0001) for the masking, CM0032 for forecasts, CM0042 / CM0070 for responses.

Dashboard screenshots of each chain: `results/dashboard_screens/8_chain_*` (A1 on the dry-run board, B1 and C1 on the twin at 10×).
