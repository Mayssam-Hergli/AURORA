# Interface proposals from the ML work package (for team review)

**Status:** proposals only. They are **not** applied to `docs/interfaces.md`, which stays the contract until the team agrees. Once a proposal is accepted, the contract and the code change in the same commit.

**Author:** Youssef Mhamdi (data, ML).

---

## P1. `ml_labels.json`: per-satellite ground truth next to `labels.json`

**Why.** Section 6.3 `labels.json` has one `onset_t` and one `critical_t` per episode. A constellation episode can have several affected and failing satellites; in a C1 episode, for example, the source and its ISL victims fail at different times. Lead time is measured per satellite (CONTEXT Section 5.8), so the evaluation needs per-satellite truth. The next-subsystem head also needs to know when each subsystem was first affected.

**What.** An extra file in each episode folder. `labels.json` is unchanged. Like all ground truth, the file is read only by training and evaluation, never by the defence.

```json
{
 "episode_id": "test_id-00023",
 "t0": 1760000000.0,
 "dt_s": 1.0,
 "n_ticks": 600,
 "family": {"drift": "saturating", "noise": "ar1", "precursor": "burst", "intensity": 0.951, "realnoise": false},
 "satellites": {
  "C": {
   "onset_t": 330.0,
   "critical_t": null,
   "critical_rule": null,
   "malicious": true,
   "affected_t": {"obc": 330.0, "comms": 330.0, "thermal": 349.0, "eps": 355.0},
   "reported_masked": ["temp_c"]
  }
 }
}
```

| Field | Meaning |
|---|---|
| `t0`, `dt_s`, `n_ticks` | time base of the episode |
| `family` | generator knobs, used to build held-out test splits |
| `satellites.{id}.onset_t` | first anomalous activity on this satellite |
| `satellites.{id}.critical_t` | first tick of the true critical state F with responses disabled; `null` if never reached |
| `satellites.{id}.critical_rule` | `T_bus>T_max`, `SOC<SOC_min` or `att_err>theta_max` |
| `satellites.{id}.malicious` | `true` for an attack, `false` for a natural fault |
| `satellites.{id}.affected_t` | first time each subsystem departs from a counterfactual run without the anomaly |
| `satellites.{id}.reported_masked` | channels whose reported telemetry was forged (B1/B2) |

Only satellites with an anomaly are listed. **Time unit to decide:** the current implementation stores seconds since `t0`, while `labels.json` uses Unix time. Proposal: switch to Unix time for consistency (a one-line change in `ml/synthetic/engine.py` plus regeneration).

**Who produces it.** `twin/` (v1) and the recorder, which already know the injected scenario. For hardware runs, `affected_t` can be left empty; only `onset_t` and `critical_t` are needed for lead time.

## P2. `SET_MODE` values

**Why.** The attack library uses `SET_MODE` (A2 `WHEEL_TEST`, A3 SCIENCE/IDLE toggling) and the response engine uses it (safe mode, quarantine). The contract does not list the allowed values yet.

**What.** `args.mode` ∈ {`NOMINAL`, `SAFE`, `SCIENCE`, `IDLE`, `WHEEL_TEST`, `QUARANTINED`}. Telemetry `mode` reports the same values.

## P3. Forecast message: `p_std` is a standard deviation

**Why.** The paper defines the uncertainty Q as a *variance* across the ensemble. The variance of a probability is at most 0.25, so the gate θ_uncert = 0.35 could never trigger.

**What.** In `aurora/v1/core/forecast/{id}`, `p_std[Δ]` is the standard deviation of P(F < t + Δ) across the ensemble members, after temperature scaling. The gate compares `p_std["60"]` with 0.35.

```json
{"v": 1, "sat": "B", "t": 1760000123.0, "model": "tgn-v1",
 "p_fail": {"10": 0.02, "30": 0.11, "60": 0.43, "120": 0.71},
 "p_std":  {"10": 0.01, "30": 0.03, "60": 0.06, "120": 0.05},
 "next_sub": "eps", "p_malicious": 0.93}
```

## P4. SPARTA identifiers (paper Table I and threat-model docs)

| Where | Current | Proposed | Reason |
|---|---|---|---|
| Telemetry masking (B1, B2) | IA-0006 | DE-0002 Disrupt or Deceive Downlink; EX-0001 Replay; DE-0003 On-Board Values Obfuscation for a slow bias | IA-0006 is "Compromise Hosted Payload" |
| Crosslink propagation (C1) | IA-0009 | IA-0003 Crosslink via Compromised Neighbor | exact technique exists |
| Reroute (Fig. 4) | isolation = CM0070 | isolation = CM0032 / CM0042; reroute = CM0070 | CM0070 is Alternate Communications Paths |

## P5. Telecommand `origin` values

**What.** The forecaster relies on `origin` ∈ {`ground`, `isl:<sat>`, `unknown`, `core`}, where `core` marks the defence's own commands.
- `core` commands are not counted as ground, ISL or unknown traffic, the strongest evidence channels.
- They do appear in the per-type counts (for example `SET_MODE`) and in the mode flags, which reflect the defence's own actions.

Commands are `HEATER` (`args.on`), `SAFE_MODE`, `SET_MODE`, `ISL_ENABLE` and `PING`. Please confirm that the hardware bridge sets `origin` the same way.
