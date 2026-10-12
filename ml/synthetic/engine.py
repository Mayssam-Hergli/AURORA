"""Synthetic twin engine: the ML side's stopgap data factory until twin/ v1 exists.

It follows docs/CONTEXT.md Section 5.3 (subsystem models, 12 satellites, geometry) and emits the
Section 6.3 episode format with Section 6.2 field names, so the forecaster trained on it can be
retrained on the official twin without code changes. It also exposes the sandbox API the core
expects (snapshot / restore / apply / step / run_episode).

Ground truth (labels.json, ml_labels.json) is produced here for training and evaluation only.
The "affected" times of each subsystem come from a counterfactual run of the same episode with
attacks and faults disabled (same seed, same noise, same legitimate traffic): a subsystem is
affected at the first tick where its true state departs from the counterfactual.
"""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml import schema
from ml.synthetic import constellation
from ml.synthetic.scenarios import EpisodeSpec

NOISY = [
    "temp_c",
    "soc_pct",
    "bus_v",
    "load_w",
    "solar_w",
    "current_ma",
    "att_err_deg",
    "wheel_rpm_0",
    "wheel_rpm_1",
    "wheel_rpm_2",
    "rate_dps",
    "rssi_dbm",
    "level",
]
SUB_OF_CHANNEL = {
    "temp_c": "thermal",
    "heater_on": "thermal",
    "soc_pct": "eps",
    "bus_v": "eps",
    "load_w": "eps",
    "solar_w": "eps",
    "current_ma": "eps",
    "att_err_deg": "adcs",
    "wheel_rpm_0": "adcs",
    "wheel_rpm_1": "adcs",
    "wheel_rpm_2": "adcs",
    "rate_dps": "adcs",
    "level": "sun",
}
MODE_NOMINAL, MODE_SAFE, MODE_QUARANTINED = 0, 1, 2
OFFLINE_SRC = "synthetic"  # src of generated episode files (D9); live messages_at keeps "twin"


def refused_in_safe(cmd: str, args: dict, origin: str = "ground") -> bool:
    """docs/interfaces.md 3.2 (SAFE semantics): in SAFE a satellite refuses commands from origins other
    than ground/core, HEATER on from any origin (load shedding) and SET_MODE other than NOMINAL/SAFE;
    only SET_MODE NOMINAL exits SAFE. Decided from the command alone: the satellite cannot know which
    commands are malicious (review finding M1). The one copy of the rule for the engine, the twin
    service and the core's sandbox (the board bridge keeps its own: it runs without this repo's deps)."""
    if origin.split(":", 1)[0] not in ("ground", "core"):
        return True
    if cmd == "HEATER":
        return bool(args.get("on"))
    if cmd == "SET_MODE":
        return args.get("mode") not in ("NOMINAL", "SAFE")
    return False


@dataclass
class Episode:
    telemetry: pd.DataFrame
    commands: pd.DataFrame
    links: pd.DataFrame
    labels: schema.EpisodeLabels
    ml_labels: schema.MLLabels


class SyntheticTwin:
    def __init__(self, cfg: dict, spec: EpisodeSpec, noise_bank: dict | None = None, anomalies: bool = True):
        self.cfg, self.spec, self.anomalies = cfg, spec, anomalies
        self.sats = list(cfg["satellites"])
        self.idx = {s: i for i, s in enumerate(self.sats)}
        S = self.S = len(self.sats)
        self.dt = cfg["dt_s"]
        self.T = int(cfg["episode_s"] / self.dt)
        self.t = 0
        self.t0_unix = float(cfg["epoch_unix"])
        self.geo = constellation.compute(cfg, self.T, spec.t0_orbit_s, self.dt)
        rng = self.rng = np.random.default_rng(spec.seed)
        # separate stream for everything anomaly-related, so that the counterfactual run (anomalies
        # disabled) sees exactly the same legitimate traffic, physics noise and measurement noise
        self.rng_att = np.random.default_rng([spec.seed, 1])
        self._n_cmd = 0
        th, ep, ad = cfg["thermal"], cfg["eps"], cfg["adcs"]

        # ----- physical state (true values)
        self.temp = rng.uniform(15, 25, S)
        self.thermostat = np.zeros(S, bool)
        self.heater_cmd = np.zeros(S, bool)  # commanded heater (legit tests or attack)
        self.heater_power = rng.uniform(*th["attack_heater_w"], S)
        self.heater_stuck = np.zeros(S, bool)  # F5
        self.science = np.zeros(S, bool)
        self.soc = rng.uniform(*ep["soc0_pct"], S) / 100
        self.e_cap_j = np.full(S, ep["capacity_wh"] * 3600.0)
        self.wheel_ref = rng.uniform(*ad["wheel_nominal_rpm"], (S, 3)) * rng.choice([-1, 1], (S, 3))
        self.wheels = self.wheel_ref.copy()
        self.theta_drift = np.zeros(S)
        self.theta_prev = np.zeros(S)
        self.extra_load = np.zeros(S)
        # malicious effects: start tick per satellite (-1 = inactive), and their parameters
        self.heater_attack = np.full(S, -1)
        self.wheel_attack = np.full(S, -1)
        self.wheel_attack_idx = np.zeros(S, int)
        self.drain_attack = np.full(S, -1)
        # ----- OBC / COMMS state
        self.mode = np.full(S, MODE_NOMINAL)
        self.cmd_ok = rng.integers(100, 1000, S)
        self.cmd_rej = rng.integers(0, 5, S)
        self.last_origin = ["ground"] * S
        self.isl_enabled = np.ones((S, S), bool)
        self.seq0 = rng.integers(0, 10_000, S)
        # ----- recorders
        n_ch = len(NOISY)
        self.true = {
            k: np.zeros((self.T, S))
            for k in ["temp_c", "soc_pct", "att_err_deg", "load_w", "level", "wheel_max"]
        }
        self.rep = np.zeros((self.T, S, n_ch), np.float64)  # reported analog channels
        self.rep_heater = np.zeros((self.T, S), np.int8)
        self.rep_mode = np.zeros((self.T, S), np.int8)
        self.rep_cmd_ok = np.zeros((self.T, S), np.int64)
        self.rep_cmd_rej = np.zeros((self.T, S), np.int64)
        self.rep_origin = np.empty((self.T, S), object)
        self.rep_contact = np.zeros((self.T, S), np.int8)
        self.rep_peers = np.empty((self.T, S), object)
        self.link_enabled = np.zeros((self.T, S, S), bool)
        self.failed = np.zeros((self.T, S), bool)
        self.fail_rule = np.empty((self.T, S), object)
        self.commands: list[dict] = []
        self.queue: list[tuple[int, dict]] = []  # (tick, command) to deliver
        self.malicious_cmd_ids: set[str] = set()
        self.truth: dict[str, dict] = {s: {} for s in self.sats}
        self._init_noise(noise_bank)
        self._plan_legit_traffic()
        if anomalies:
            self._plan_anomalies()

    # ================================================================== noise
    def _init_noise(self, noise_bank):
        rng, sp, T, S = self.rng, self.spec, self.T, self.S
        shape = (T, S, len(NOISY))
        if sp.realnoise and noise_bank is not None:
            res, gaps = noise_bank["residual"], noise_bank["gap"]
            st = rng.integers(0, len(res) - T, size=shape[1:])
            idx = st[None] + np.arange(T)[:, None, None]
            self.eps = res[idx].astype(np.float64)
            self.gap = gaps[idx[..., 0]].astype(bool)
        else:
            if sp.noise == "ar1":
                phi = 0.85
                w = rng.standard_normal(shape) * np.sqrt(1 - phi**2)
                eps = np.zeros(shape)
                eps[0] = rng.standard_normal(shape[1:])
                for k in range(1, T):
                    eps[k] = phi * eps[k - 1] + w[k]
            elif sp.noise == "student_t":
                eps = rng.standard_t(3, shape) / np.sqrt(3)
            else:
                eps = rng.standard_normal(shape)
            self.eps = eps
            self.gap = rng.random((T, S)) < (0.02 if sp.noise == "student_t" else 0.0)
        sig = self.cfg["noise_sigma"]
        self.sigma = np.array(
            [
                (
                    sig[c.split("_")[0] + "_" + c.split("_")[1] if c.startswith("wheel") else c]
                    if not c.startswith("wheel")
                    else sig["wheel_rpm"]
                )
                for c in NOISY
            ]
        )

    # ================================================================== command plan
    def _cmd(self, tick, sat, origin, cmd, args=None, auth_ok=True, malicious=False):
        msg = {
            "v": 1,
            "id": self._next_id(),
            "t": self.t0_unix + tick * self.dt,
            "sat": sat,
            "origin": origin,
            "cmd": cmd,
            "args": args or {},
            "_auth_ok": auth_ok,
            "_malicious": malicious,
        }
        self.queue.append((int(tick), msg))
        if malicious:
            self.malicious_cmd_ids.add(msg["id"])
        return msg

    def _next_id(self):
        self._n_cmd += 1
        return f"{self.spec.seed:08x}{self._n_cmd:08x}"

    def _contact_windows(self, s):
        c = self.geo.contact[:, s].astype(int)
        starts = list(np.flatnonzero(np.diff(np.concatenate([[0], c])) == 1))
        ends = list(np.flatnonzero(np.diff(np.concatenate([c, [0]])) == -1))
        return list(zip(starts, ends))

    def _plan_legit_traffic(self):
        rng, cc, T = self.rng, self.cfg["commands"], self.T
        for s, name in enumerate(self.sats):
            for a, b in self._contact_windows(s):
                for k in range(a + 1, b + 1, cc["ping_period_s"]):
                    self._cmd(k, name, "ground", "PING")
                if rng.random() < cc["heater_test_prob"]:
                    on = a + int(rng.integers(0, max(1, b - a)))
                    self._cmd(on, name, "ground", "HEATER", {"on": True})
                    self._cmd(
                        min(T - 1, on + int(rng.integers(30, 61))), name, "ground", "HEATER", {"on": False}
                    )
            # science sessions from the stored schedule (uplinked earlier, executed on board)
            for _ in range(int(rng.integers(0, 3))):
                st = int(rng.integers(0, T - 60))
                self._cmd(st, name, "ground", "SET_MODE", {"mode": "SCIENCE"})
                self._cmd(
                    min(T - 1, st + int(rng.integers(*cc["science_window_s"]))),
                    name,
                    "ground",
                    "SET_MODE",
                    {"mode": "IDLE"},
                )
            # rare benign unknown-origin traffic (misconfigured ground tool): a hard negative
            for k in np.flatnonzero(rng.random(T) < cc["benign_unknown_rate"]):
                self._cmd(k, name, "unknown", "PING")

    def _drive(self, start, t):
        """Malicious actuation level in [0, 1] (drift shape of the generator family)."""
        if start < 0 or t < start:
            return 0.0
        dt = t - start
        if self.spec.drift == "ramp":
            return min(1.0, (dt + 1) / self.spec.extra.get("ramp_s", 40.0))
        if self.spec.drift == "pulsed":
            period, duty = self.spec.extra.get("period_s", 12.0), self.spec.extra.get("duty", 0.6)
            return 1.0 if (dt % period) < duty * period else 0.0
        return 1.0

    def _plan_anomalies(self):
        sp, rng, cc = self.spec, self.rng_att, self.cfg["commands"]
        if sp.type == "N":
            return
        sp.extra.setdefault("ramp_s", float(rng.uniform(20, 60)))
        sp.extra.setdefault("period_s", float(rng.uniform(8, 16)))
        sp.extra.setdefault("duty", float(rng.uniform(0.5, 0.75)))
        s = self.idx[sp.target]
        onset = int(sp.onset_s)
        if sp.kind == "fault":
            self._plan_fault(s, onset)
            return
        # ground-path attacks prefer a real pass of the target; otherwise a stored schedule
        windows = [w for w in self._contact_windows(s) if 100 <= w[0] <= 320]
        if windows and rng.random() < 0.7:
            onset = int(windows[0][0])
        lead = int(rng.integers(*cc["precursor_lead_s"])) if sp.precursor != "none" else 0
        exec_t = min(self.T - 120, onset + lead)
        self.truth[sp.target]["exec_t"] = exec_t
        pre = cc["precursor"][sp.precursor]
        contact_ticks = [k for k in range(onset, exec_t) if self.geo.contact[k, s]]
        pre_ticks = []
        if contact_ticks:
            for _ in range(int(rng.integers(pre["pings"][0], pre["pings"][1] + 1))):
                pre_ticks.append(int(rng.choice(contact_ticks)))
                self._cmd(pre_ticks[-1], sp.target, "unknown", "PING", malicious=True)
            for _ in range(int(rng.integers(pre["rejected"][0], pre["rejected"][1] + 1))):
                pre_ticks.append(int(rng.choice(contact_ticks)))
                self._cmd(
                    pre_ticks[-1],
                    sp.target,
                    "ground",
                    str(rng.choice(["HEATER", "SET_MODE"])),
                    {"on": True},
                    auth_ok=False,
                    malicious=True,
                )
        # onset = first malicious activity that actually happens
        self.truth[sp.target]["onset_t"] = min(pre_ticks) if pre_ticks else exec_t
        sp.onset_s = float(self.truth[sp.target]["onset_t"])
        base = {
            "A1": "A1",
            "B2": "A1",
            "A2": "A2",
            "A3": "A3",
            "C1": sp.extra.get("victim_attack", "A1"),
            "B1": "A1" if sp.channel == "temp_c" else "A3",
        }[sp.type]
        self._plan_attack_effect(sp.target, base, exec_t, "ground")
        if sp.type in ("B1", "B2"):
            self.truth[sp.target]["mask_start"] = exec_t - 2
            # masking is itself malicious activity: it can start before the command executes
            self.truth[sp.target]["onset_t"] = min(self.truth[sp.target]["onset_t"], exec_t - 2)
            sp.onset_s = float(self.truth[sp.target]["onset_t"])
            self.truth[sp.target]["reported_masked"] = [sp.channel]
            self.mask_mode = str(rng.choice(["freeze", "replay"]))
        if sp.type == "C1":
            self.c1 = dict(
                origin=s,
                attack=sp.extra.get("victim_attack", "A1"),
                n=sp.n_victims,
                earliest=exec_t + int(rng.integers(*cc["isl_relay_delay_s"])),
                victims=[],
            )

    def _plan_attack_effect(self, sat, kind, tick, origin):
        if kind == "A1":
            self._cmd(tick, sat, origin, "HEATER", {"on": True}, malicious=True)
        elif kind == "A2":
            self._cmd(tick, sat, origin, "SET_MODE", {"mode": "WHEEL_TEST"}, malicious=True)
        else:  # A3 malicious schedule
            k = tick
            while k < self.T:
                self._cmd(k, sat, origin, "SET_MODE", {"mode": "SCIENCE"}, malicious=True)
                k += int(self.rng_att.integers(5, 16))
                if k < self.T:
                    self._cmd(k, sat, origin, "SET_MODE", {"mode": "IDLE"}, malicious=True)
                k += int(self.rng_att.integers(5, 16))

    def _plan_fault(self, s, onset):
        sp, name = self.spec, self.spec.target
        tr = self.truth[name]
        if sp.type == "F4":  # natural eclipse entry: pick a satellite entering eclipse
            e = self.geo.eclipse.astype(int)
            entries = [
                (int(k), j)
                for j in range(self.S)
                for k in np.flatnonzero(np.diff(e[:, j]) == 1) + 1
                if 100 <= k <= 320
            ]
            if entries:
                k, j = entries[int(self.rng_att.integers(len(entries)))]
                sp.target, sp.onset_s = self.sats[j], float(k)
                self.truth = {x: {} for x in self.sats}
                self.truth[sp.target]["onset_t"] = k
            return
        tr["onset_t"] = 0 if sp.type == "F3" else onset
        self.fault = dict(type=sp.type, sat=s, onset=0 if sp.type == "F3" else onset)
        if sp.type == "F3":
            self.e_cap_j[s] *= self.rng_att.uniform(*self.cfg["eps"]["ageing_capacity_factor"])
            tr["onset_t"] = 0
        if sp.type == "F1":
            lo, hi = self.cfg["faults"]["drift_rate"][sp.channel]
            self.fault["rate"] = float(self.rng_att.uniform(lo, hi) * self.rng_att.choice([-1, 1]))
        if sp.type in ("F1", "F2"):
            tr["reported_masked"] = [sp.channel]

    # ================================================================== sandbox API
    def snapshot(self, include_queue: bool = True):
        """Copy of the full state. include_queue=False is what a planner may see: no queued future
        commands and no pending ISL propagation, only the effects already in the physical state
        (a latched heater stays latched). M2: the sandbox must not know the attacker's plan."""
        snap = copy.deepcopy(self.__dict__)
        if not include_queue:
            snap["queue"] = []
            snap["c1"] = None
        return snap

    def restore(self, snap):
        self.__dict__.update(copy.deepcopy(snap))

    def seed_from_observation(
        self, obs: dict, hold_geometry: bool = True, t_index: int | None = None
    ) -> SyntheticTwin:
        """Make this twin a shadow of what a defender observed (the core's sandbox, M2). Returns self.

        Two input shapes:
          flat:      {"sats": {sat: {"temp_c", "heater_on", "soc_pct", "mode", "eclipse", "ground_contact",
                      "temp_rate_c_s" (optional)}}, "links": {"A-B": {"feasible", "enabled"}},
                      "disabled": ["A-B", ...] (optional)}
          WorldTick: {"tick": k, "values": {sat: {sub: fields}}, "links": {(a, b) or "a-b": {...}}}
                     (cassandra.world.WorldTick: pass tick, wt.values, wt.links)
        Observed: temperature, SOC, OBC mode and counters, wheels, attitude, heater state, link flags.
        Unobserved state keeps its model value. Nothing about the scenario survives: no queued
        commands, attack, fault, block or propagation plan, no scripted anomaly (threat model "observed
        effects persist": a heater seen on stays commanded on, at full power unless the observed heating
        rate says otherwise; heater_on below the thermostat band is the thermostat, not a command).

        Geometry: hold_geometry=True holds the observed eclipse, contact and link feasibility over the
        rollout (persistence). hold_geometry=False keeps this twin's own orbit propagation from tick
        t_index (the caller matched its orbit phase to the observed links), so links that open during
        the rollout are seen; ground contact is still held. Link enable flags: observed where the link
        is feasible, assumed on elsewhere, except links in obs["disabled"] (cut on purpose)."""
        th = self.cfg["thermal"]
        if t_index is None:
            t_index = obs.get("tick") or 0
        self.t = int(np.clip(t_index, 0, self.T - 1))
        self.queue, self.commands, self.c1, self.fault, self.blocked = [], [], None, None, {}
        self.anomalies, self.truth = False, {s: {} for s in self.sats}
        self.heater_attack[:] = self.wheel_attack[:] = self.drain_attack[:] = -1
        self.heater_stuck[:] = False
        modes = {"NOMINAL": MODE_NOMINAL, "SAFE": MODE_SAFE, "QUARANTINED": MODE_QUARANTINED}
        sats = obs.get("sats") or {}
        if "values" in obs:  # WorldTick shape: flatten each satellite's subsystems
            sats = {
                sat: {k: v for f in subs.values() if f for k, v in f.items()}
                for sat, subs in obs["values"].items()
            }
        for sat, o in sats.items():
            if sat not in self.idx or not o:
                continue
            s = self.idx[sat]
            if o.get("temp_c") is not None:
                self.temp[s] = float(o["temp_c"])
            if o.get("soc_pct") is not None:
                self.soc[s] = float(np.clip(o["soc_pct"] / 100.0, 0, 1))
            if o.get("wheel_rpm") is not None:
                self.wheels[s] = np.asarray(o["wheel_rpm"], float)
            if o.get("att_err_deg") is not None:
                self.theta_drift[s] = self.theta_prev[s] = abs(float(o["att_err_deg"]))
            on = bool(o.get("heater_on", 0)) and self.temp[s] > th["thermostat_off_c"]
            self.heater_cmd[s] = on
            self.science[s] = self.thermostat[s] = False
            self.mode[s] = modes.get(o.get("mode"), MODE_NOMINAL)
            for k, arr in (("cmd_ok", self.cmd_ok), ("cmd_rej", self.cmd_rej)):
                if o.get(k) is not None:
                    arr[s] = int(o[k])
            if o.get("last_origin"):
                self.last_origin[s] = o["last_origin"]
            if self.mode[s] != MODE_NOMINAL:
                self.heater_cmd[s] = on = False
            rate = o.get("temp_rate_c_s")
            if on and rate is not None:
                ecl = float(o.get("eclipse", 0))
                p = (
                    th["heat_capacity_j_per_k"] * rate
                    - th["p_sun_absorbed_w"] * (1 - ecl)
                    - th["p_internal_w"]
                    + th["conductance_w_per_k"] * (self.temp[s] - th["t_env_c"])
                )
                self.heater_power[s] = float(np.clip(p, 0.0, 200.0))
            if "eclipse" in o and hold_geometry:
                self.geo.eclipse[self.t :, s] = bool(o["eclipse"])
            if "ground_contact" in o:
                self.geo.contact[self.t :, s] = bool(o["ground_contact"])
        for key, lk in (obs.get("links") or {}).items():
            a, b = key if isinstance(key, tuple) else key.split("-")
            if a not in self.idx or b not in self.idx:
                continue
            i, j = self.idx[a], self.idx[b]
            feasible = bool(lk.get("feasible", self.geo.feasible[self.t, i, j]))
            if hold_geometry:
                self.geo.feasible[self.t :, i, j] = self.geo.feasible[self.t :, j, i] = feasible
            on = bool(lk.get("enabled", True)) if feasible else True
            self.isl_enabled[i, j] = self.isl_enabled[j, i] = on
        for key in obs.get("disabled", []):
            a, b = key.split("-")
            i, j = self.idx[a], self.idx[b]
            self.isl_enabled[i, j] = self.isl_enabled[j, i] = False
        return self

    def apply(self, msg: dict):
        """Deliver a telecommand now (Section 6.2 message; origin decides trust)."""
        msg = dict(msg)
        msg.setdefault("_auth_ok", True)
        msg.setdefault("_malicious", False)
        msg.setdefault("id", uuid.uuid4().hex[:16])
        msg.setdefault("t", self.t0_unix + self.t * self.dt)
        self._execute(msg)

    def _execute(self, msg):
        s = self.idx[msg["sat"]]
        t = self.t
        kind = schema.origin_kind(msg["origin"])
        accepted = bool(msg["_auth_ok"])
        if self.mode[s] in (MODE_SAFE, MODE_QUARANTINED) and self._refused_in_safe(kind, msg):
            accepted = False
        if getattr(self, "blocked", {}).get(s, set()) and msg["origin"] in self.blocked[s]:
            accepted = False
        rec = {k: v for k, v in msg.items() if not k.startswith("_")}
        rec["accepted"] = accepted
        rec["args"] = json.dumps(msg.get("args", {}))
        self.commands.append(rec)
        self.last_origin[s] = msg["origin"]
        if not accepted:
            self.cmd_rej[s] += 1
            return
        self.cmd_ok[s] += 1
        if msg["_malicious"]:
            tr = self.truth[msg["sat"]]
            tr.setdefault("first_malicious_exec", t)
        cmd, args = msg["cmd"], msg.get("args", {})
        if cmd == "HEATER":
            on = bool(args.get("on", False))
            self.heater_cmd[s] = on
            if on and msg["_malicious"]:
                self.heater_attack[s] = t
            elif not on:
                self.heater_attack[s] = -1
        elif cmd == "SAFE_MODE" or (cmd == "SET_MODE" and args.get("mode") == "SAFE"):
            self._safe_mode(s)
        elif cmd == "SET_MODE":
            mode = args.get("mode")
            if mode == "NOMINAL":
                self.mode[s] = MODE_NOMINAL
            elif mode == "QUARANTINED":
                self.mode[s] = MODE_QUARANTINED
            elif mode == "SCIENCE":
                self.science[s] = True
                if msg["_malicious"] and self.drain_attack[s] < 0:
                    self.drain_attack[s] = t
            elif mode == "IDLE":
                self.science[s] = False
            elif mode == "WHEEL_TEST":
                if msg["_malicious"]:
                    self.wheel_attack[s] = t
                    self.wheel_attack_idx[s] = int(self.rng_att.integers(3))
        elif cmd == "ISL_ENABLE":
            peer = args.get("peer")
            if peer in self.idx:
                self.isl_enabled[s, self.idx[peer]] = bool(args.get("on", True))

    @staticmethod
    def _refused_in_safe(kind: str, msg: dict) -> bool:
        return refused_in_safe(msg["cmd"], msg.get("args") or {}, kind)

    def _safe_mode(self, s):
        """Safe mode (docs/interfaces.md 3.2): heater off and non-essential loads off. Queued commands
        stay queued: they are refused or accepted on delivery by the SAFE rule in _execute, which looks
        only at the command and the mode, never at whether it is malicious (M1)."""
        self.mode[s] = MODE_SAFE
        self.heater_cmd[s] = False
        self.science[s] = False
        self.heater_attack[s] = self.wheel_attack[s] = self.drain_attack[s] = -1

    def isolate_heater(self, s):
        """Core action 'isolate subsystem': cut the heater power rail (also stops a stuck heater)."""
        self.heater_cmd[s] = False
        self.heater_stuck[s] = False
        self.heater_attack[s] = -1

    def block_source(self, s, origin):
        self.blocked = getattr(self, "blocked", {})
        self.blocked.setdefault(s, set()).add(origin)

    # ================================================================== one second of physics
    def step(self, n: int = 1):
        for _ in range(n):
            if self.t >= self.T:
                return
            self._step_once()

    def _step_once(self):
        t, S, cfg = self.t, self.S, self.cfg
        th, ep, ad = cfg["thermal"], cfg["eps"], cfg["adcs"]
        rng = self.rng
        ecl = self.geo.eclipse[t].astype(float)
        con = self.geo.contact[t]
        # deliver due commands (legit plan, attacks, relays)
        due = [m for k, m in self.queue if k == t]
        self.queue = [(k, m) for k, m in self.queue if k != t]
        for m in due:
            self._execute(m)
        self._isl_traffic(t)
        self._fault_physics(t)

        # ---------------- thermal
        self.thermostat = np.where(
            self.temp < th["thermostat_on_c"],
            True,
            np.where(self.temp > th["thermostat_off_c"], False, self.thermostat),
        )
        u_heat = np.array(
            [self._drive(self.heater_attack[s], t) if self.heater_attack[s] >= 0 else 1.0 for s in range(S)]
        )
        cmd_heat = (self.heater_cmd * u_heat + self.heater_stuck * self._stuck_drive(t)) * self.heater_power
        p_heater = th["heater_nominal_w"] * self.thermostat + cmd_heat
        p_in = (
            p_heater
            + th["p_sun_absorbed_w"] * (1 - ecl)
            + th["p_internal_w"]
            + th["science_heat_w"] * self.science
        )
        self.temp = (
            self.temp
            + (p_in - th["conductance_w_per_k"] * (self.temp - th["t_env_c"]))
            * self.dt
            / th["heat_capacity_j_per_k"]
            + rng.normal(0, 0.01, S)
        )
        heater_on = (p_heater > 0.5).astype(np.int8)

        # ---------------- ADCS
        slew = self._slew_offset(t)
        relax = np.where(self.mode == MODE_SAFE, 1 / 8.0, 1 / ad["wheel_relax_s"])
        self.wheels += (self.wheel_ref - self.wheels) * relax[:, None] * self.dt
        sat_drift = np.zeros(S)
        for s in range(S):
            u = self._drive(self.wheel_attack[s], t) if self.wheel_attack[s] >= 0 else 0.0
            if u > 0:
                k = self.wheel_attack_idx[s]
                acc = self.spec.intensity * float(np.mean(ad["attack_wheel_accel_rpm_s"]))
                self.wheels[s, k] += (
                    np.sign(self.wheel_ref[s, k]) * acc * u * self.dt
                    + (self.wheel_ref[s, k] - self.wheels[s, k]) * relax[s] * self.dt * -1
                )
                if abs(self.wheels[s, k]) >= ad["wheel_max_rpm"]:
                    sat_drift[s] = self.spec.intensity * float(np.mean(ad["saturated_drift_deg_s"]))
        self.wheels = np.clip(self.wheels, -ad["wheel_max_rpm"], ad["wheel_max_rpm"])
        self.theta_drift = np.where(
            sat_drift > 0,
            self.theta_drift + sat_drift * self.dt,
            self.theta_drift * np.exp(-self.dt / ad["control_tau_s"]),
        )
        near_sat = np.clip(
            (np.abs(self.wheels).max(1) - 0.8 * ad["wheel_max_rpm"]) / (0.2 * ad["wheel_max_rpm"]), 0, 1
        )
        theta = np.abs(slew + self.theta_drift + rng.normal(0, ad["jitter_deg"], S) * (1 + 3 * near_sat))
        rate = (theta - self.theta_prev) / self.dt
        self.theta_prev = theta

        # ---------------- EPS
        drain = np.array(
            [self._drive(self.drain_attack[s], t) if self.drain_attack[s] >= 0 else 0.0 for s in range(S)]
        )
        self.extra_load = drain * self.spec.intensity * float(np.mean(ep["attack_extra_load_w"]))
        p_solar = ep["panel_w"] * np.cos(np.deg2rad(np.minimum(theta, 90.0))) * (1 - ecl)
        p_load = (
            ep["base_load_w"]
            + ep["science_w"] * self.science
            + p_heater
            + ep["comms_tx_w"] * con
            + ep["wheel_w_per_krpm"] * np.abs(self.wheels).sum(1) / 1000
            + self.extra_load
            + rng.normal(0, 0.2, S)
        )
        self.soc = np.clip(self.soc + (p_solar - p_load) * self.dt / self.e_cap_j, 0, 1)
        bus_v = 24 + 8 * self.soc - 0.005 * p_load
        current_ma = p_load / bus_v * 1000

        # ---------------- COMMS, SUN
        feas = self.geo.feasible[t]
        enabled = feas & self.isl_enabled & self.isl_enabled.T
        self.link_enabled[t] = enabled
        rssi = np.where(con, -60 - 0.4 * (90 - self.geo.elevation_deg[t]), -120.0)
        level = np.clip(np.cos(np.deg2rad(np.minimum(theta, 90.0))), 0, 1) * (1 - ecl)

        # ---------------- truth and reported telemetry
        tr = self.true
        tr["temp_c"][t], tr["soc_pct"][t], tr["att_err_deg"][t] = self.temp, 100 * self.soc, theta
        tr["load_w"][t], tr["level"][t], tr["wheel_max"][t] = p_load, level, np.abs(self.wheels).max(1)
        crit = cfg["critical"]
        f_t, f_s, f_a = (
            self.temp > crit["t_max_c"],
            100 * self.soc < crit["soc_min_pct"],
            theta > crit["theta_max_deg"],
        )
        self.failed[t] = f_t | f_s | f_a
        self.fail_rule[t] = np.where(
            f_t, "T_bus>T_max", np.where(f_s, "SOC<SOC_min", np.where(f_a, "att_err>theta_max", ""))
        )
        vals = np.stack(
            [
                self.temp,
                100 * self.soc,
                bus_v,
                p_load,
                p_solar,
                current_ma,
                theta,
                self.wheels[:, 0],
                self.wheels[:, 1],
                self.wheels[:, 2],
                rate,
                rssi,
                level,
            ],
            -1,
        )
        rep = vals + self.sigma * self.eps[t]
        rep = self._corrupt_reported(t, rep)
        if t > 0 and self.gap[t].any():
            rep[self.gap[t]] = self.rep[t - 1][self.gap[t]]
        self.rep[t] = rep
        self.rep_heater[t] = heater_on if not self._masked("temp_c", t) else self.rep_heater[max(t - 1, 0)]
        self.rep_mode[t] = self.mode
        self.rep_cmd_ok[t], self.rep_cmd_rej[t] = self.cmd_ok, self.cmd_rej
        self.rep_origin[t] = list(self.last_origin)
        self.rep_contact[t] = con
        self.rep_peers[t] = [",".join(self.sats[j] for j in np.flatnonzero(enabled[s])) for s in range(S)]
        self.t += 1

    # ================================================================== helpers
    def _stuck_drive(self, t):
        f = getattr(self, "fault", None)
        if not f or f["type"] != "F5" or t < f["onset"]:
            return 0.0
        return self._drive(f["onset"], t)

    def _fault_physics(self, t):
        f = getattr(self, "fault", None)
        if f and f["type"] == "F5" and t == f["onset"]:
            self.heater_stuck[f["sat"]] = True

    def _slew_offset(self, t):
        """Benign pointing changes towards the ground station during passes."""
        if not hasattr(self, "_slews"):
            amp = self.rng.uniform(*self.cfg["adcs"]["slew_amp_deg"], self.S)
            self._slews = np.zeros((self.T, self.S))
            for s in range(self.S):
                for a, b in self._contact_windows(s):
                    L = b - a + 1
                    self._slews[a : b + 1, s] = amp[s] * np.sin(np.pi * (np.arange(L) + 0.5) / L)
        return self._slews[t]

    def _isl_traffic(self, t):
        """Benign relays over feasible links, and Scenario C propagation."""
        enabled = self.geo.feasible[t] & self.isl_enabled & self.isl_enabled.T
        rate = self.cfg["commands"]["benign_isl_relay_rate"]
        for a, b in zip(*np.nonzero(enabled & (self.rng.random(enabled.shape) < rate))):
            self._execute(
                {
                    "v": 1,
                    "id": self._next_id(),
                    "t": self.t0_unix + t * self.dt,
                    "sat": self.sats[b],
                    "origin": f"isl:{self.sats[a]}",
                    "cmd": "PING",
                    "args": {},
                    "_auth_ok": True,
                    "_malicious": False,
                }
            )
        c1 = getattr(self, "c1", None)
        if not c1 or t < c1["earliest"] or len(c1["victims"]) >= c1["n"]:
            return
        o = c1["origin"]
        if self.mode[o] != MODE_NOMINAL:
            return  # contained origin stops propagating
        for v in np.flatnonzero(enabled[o]):
            if len(c1["victims"]) >= c1["n"] or v in c1["victims"]:
                continue
            c1["victims"].append(int(v))
            name = self.sats[v]
            self.truth[name]["onset_t"] = t
            self.truth[name]["victim_of"] = self.sats[o]
            self._plan_attack_effect(
                name, c1["attack"], t + int(self.rng_att.integers(2, 6)), f"isl:{self.sats[o]}"
            )

    def _masked(self, channel, t):
        sp = self.spec
        tr = self.truth.get(sp.target or "", {})
        return sp.type in ("B1", "B2") and channel == sp.channel and t >= tr.get("mask_start", 10**9)

    def _corrupt_reported(self, t, rep):
        sp = self.spec
        if not self.anomalies or sp.target is None:
            return rep
        s = self.idx[sp.target]
        if sp.channel in NOISY:
            c = NOISY.index(sp.channel)
            tr = self.truth[sp.target]
            if sp.type == "B1" and t >= tr.get("mask_start", 10**9):
                ms = tr["mask_start"]
                src = ms - 1 if self.mask_mode == "freeze" else max(0, ms - 40 + ((t - ms) % 40))
                rep[s, c] = self.rep[src, s, c] + (
                    self.sigma[c] * self.eps[t, s, c] if self.mask_mode == "freeze" else 0
                )
            elif sp.type == "B2" and t >= tr.get("mask_start", 10**9):
                rate = float(np.mean(self.cfg["timing"]["b2_bias_rate_c_s"]))
                rep[s, c] -= rate * self.spec.intensity * (t - tr["mask_start"])
            elif sp.type == "F1" and t >= self.fault["onset"]:
                rep[s, c] += self.fault["rate"] * (t - self.fault["onset"])
            elif sp.type == "F2" and t == self.fault["onset"]:
                rep[s, c] += self.cfg["faults"]["seu_magnitude"][sp.channel] * self.rng_att.choice([-1, 1])
        return rep

    # ================================================================== live messages (Section 6.2)
    def messages_at(self, t: int):
        """Telemetry, telecommand and link messages of tick t, as the core would receive them."""
        ts = self.t0_unix + t * self.dt
        rep = self.rep[t]
        c = {name: rep[:, i] for i, name in enumerate(NOISY)}
        for s, sat in enumerate(self.sats):
            base = {"v": 1, "sat": sat, "src": "twin", "t": ts, "seq": int(self.seq0[s] + t)}
            yield "tm", {
                **base,
                "sub": "thermal",
                "fields": {"temp_c": float(c["temp_c"][s]), "heater_on": int(self.rep_heater[t, s])},
            }
            yield "tm", {
                **base,
                "sub": "eps",
                "fields": {
                    k: float(c[k][s]) for k in ("soc_pct", "bus_v", "load_w", "solar_w", "current_ma")
                },
            }
            yield "tm", {
                **base,
                "sub": "adcs",
                "fields": {
                    "att_err_deg": float(c["att_err_deg"][s]),
                    "wheel_rpm": [float(c[f"wheel_rpm_{k}"][s]) for k in range(3)],
                    "rate_dps": float(c["rate_dps"][s]),
                },
            }
            yield "tm", {
                **base,
                "sub": "obc",
                "fields": {
                    "mode": schema.OBC_MODES[int(self.rep_mode[t, s])],
                    "cmd_ok": int(self.rep_cmd_ok[t, s]),
                    "cmd_rej": int(self.rep_cmd_rej[t, s]),
                    "last_origin": self.rep_origin[t][s],
                },
            }
            yield "tm", {
                **base,
                "sub": "comms",
                "fields": {
                    "ground_contact": int(self.rep_contact[t, s]),
                    "isl_peers": self.rep_peers[t][s],
                    "rssi_dbm": float(c["rssi_dbm"][s]),
                },
            }
            yield "tm", {
                **base,
                "sub": "sun",
                "fields": {"level": float(max(c["level"][s], 0.0)), "eclipse": int(self.geo.eclipse[t, s])},
            }
        for rec in self.commands:
            if abs(rec["t"] - ts) < 1e-6:
                yield "tc", rec
        a, b = np.triu_indices(self.S, 1)
        for i, j in zip(a, b):
            yield "link", {
                "v": 1,
                "t": ts,
                "a": self.sats[i],
                "b": self.sats[j],
                "feasible": bool(self.geo.feasible[t, i, j]),
                "enabled": bool(self.link_enabled[t, i, j]),
                "range_km": float(round(self.geo.range_km[t, i, j], 1)),
            }

    # ================================================================== episode assembly
    def run(self) -> SyntheticTwin:
        while self.t < self.T:
            self._step_once()
        return self

    def run_episode(self) -> Episode:
        self.run()
        cf = None
        if self.anomalies and self.spec.type != "N":
            cf = SyntheticTwin(self.cfg, self.spec, noise_bank=None, anomalies=False)
            cf.eps, cf.gap = self.eps, self.gap
            cf.run()
        return self._episode(cf)

    def _affected_times(self, cf, name):
        s = self.idx[name]
        out = {}
        if cf is None:
            return out
        tol = {"thermal": 0.5, "eps": 0.5, "adcs": 0.5, "sun": 0.05}
        series = {
            "thermal": np.abs(self.true["temp_c"][:, s] - cf.true["temp_c"][:, s]),
            "eps": np.abs(self.true["soc_pct"][:, s] - cf.true["soc_pct"][:, s]),
            "adcs": np.maximum(
                np.abs(self.true["att_err_deg"][:, s] - cf.true["att_err_deg"][:, s]),
                np.abs(self.true["wheel_max"][:, s] - cf.true["wheel_max"][:, s]) / 200,
            ),
            "sun": np.abs(self.true["level"][:, s] - cf.true["level"][:, s]),
        }
        for sub, x in series.items():
            hit = np.flatnonzero(x > tol[sub])
            if len(hit):
                out[sub] = float(hit[0])
        cmd_diff = np.flatnonzero(
            (self.rep_cmd_ok[:, s] != cf.rep_cmd_ok[:, s]) | (self.rep_cmd_rej[:, s] != cf.rep_cmd_rej[:, s])
        )
        if len(cmd_diff):
            out["comms"] = float(cmd_diff[0])
            out["obc"] = float(cmd_diff[0])
        for ch in self.truth[name].get("reported_masked", []):
            sub = SUB_OF_CHANNEL[ch]
            start = self.truth[name].get("mask_start", self.spec.onset_s or 0)
            out[sub] = min(out.get(sub, 1e9), float(start))
        return out

    def _episode(self, cf) -> Episode:
        sp, T, S = self.spec, self.T, self.S
        tick_t = self.t0_unix + np.arange(T) * self.dt
        frames = []
        base = pd.DataFrame({"t": np.repeat(tick_t, S), "sat": np.tile(self.sats, T), "src": OFFLINE_SRC})
        seq = (self.seq0[None, :] + np.arange(T)[:, None]).reshape(-1)
        R = self.rep.reshape(T * S, -1)
        cols = {c: R[:, i] for i, c in enumerate(NOISY)}
        frames.append(
            base.assign(
                seq=seq,
                sub="thermal",
                temp_c=cols["temp_c"],
                heater_on=self.rep_heater.reshape(-1).astype(np.int64),
            )
        )
        frames.append(
            base.assign(
                seq=seq,
                sub="eps",
                soc_pct=cols["soc_pct"],
                bus_v=cols["bus_v"],
                load_w=cols["load_w"],
                solar_w=cols["solar_w"],
                current_ma=cols["current_ma"],
            )
        )
        frames.append(
            base.assign(
                seq=seq,
                sub="adcs",
                att_err_deg=cols["att_err_deg"],
                wheel_rpm_0=cols["wheel_rpm_0"],
                wheel_rpm_1=cols["wheel_rpm_1"],
                wheel_rpm_2=cols["wheel_rpm_2"],
                rate_dps=cols["rate_dps"],
            )
        )
        frames.append(
            base.assign(
                seq=seq,
                sub="obc",
                mode=np.array(schema.OBC_MODES)[self.rep_mode.reshape(-1)],
                cmd_ok=self.rep_cmd_ok.reshape(-1),
                cmd_rej=self.rep_cmd_rej.reshape(-1),
                last_origin=self.rep_origin.reshape(-1).astype(str),
            )
        )
        frames.append(
            base.assign(
                seq=seq,
                sub="comms",
                ground_contact=self.rep_contact.reshape(-1).astype(np.int64),
                isl_peers=self.rep_peers.reshape(-1).astype(str),
                rssi_dbm=cols["rssi_dbm"],
            )
        )
        frames.append(
            base.assign(
                seq=seq,
                sub="sun",
                level=np.clip(cols["level"], 0, None),
                eclipse=self.geo.eclipse.reshape(-1).astype(np.int64),
            )
        )
        telemetry = pd.concat(frames, ignore_index=True).sort_values(["t", "sat", "sub"], kind="stable")
        telemetry = telemetry[[c for c in schema.telemetry_columns() if c in telemetry.columns]]
        commands = pd.DataFrame(
            self.commands, columns=["v", "id", "t", "sat", "origin", "cmd", "args", "accepted"]
        )
        commands["src"] = OFFLINE_SRC
        a, b = np.triu_indices(S, 1)
        links = pd.DataFrame(
            {
                "t": np.repeat(tick_t, len(a)),
                "a": np.tile(np.array(self.sats)[a], T),
                "b": np.tile(np.array(self.sats)[b], T),
                "feasible": self.geo.feasible[:, a, b].reshape(-1),
                "enabled": self.link_enabled[:, a, b].reshape(-1),
                "range_km": np.round(self.geo.range_km[:, a, b].reshape(-1), 1),
            }
        )
        sat_truth = {}
        first_crit, first_rule = None, None
        for name in self.sats:
            s = self.idx[name]
            tr = self.truth[name]
            hit = np.flatnonzero(self.failed[:, s])
            crit_t = float(hit[0]) if len(hit) else None
            rule = str(self.fail_rule[hit[0], s]) if len(hit) else None
            malicious = None
            if tr.get("onset_t") is not None or crit_t is not None:
                malicious = sp.kind == "attack" and tr.get("onset_t") is not None
            sat_truth[name] = schema.SatelliteTruth(
                onset_t=tr.get("onset_t"),
                critical_t=crit_t,
                critical_rule=rule,
                malicious=malicious,
                affected_t=self._affected_times(cf, name) if tr.get("onset_t") is not None else {},
                reported_masked=tr.get("reported_masked", []),
            )
            if crit_t is not None and (first_crit is None or crit_t < first_crit):
                first_crit, first_rule = crit_t, rule
        onset = self.truth.get(sp.target, {}).get("onset_t") if sp.target else None
        labels = schema.EpisodeLabels(
            episode_id=sp.episode_id,
            generator=self.cfg["generator"],
            seed=sp.seed,
            scenario=sp.scenario,
            kind=sp.kind,
            type=sp.type,
            target=sp.target,
            onset_t=None if onset is None else float(self.t0_unix + onset * self.dt),
            critical_t=None if first_crit is None else float(self.t0_unix + first_crit * self.dt),
            critical_rule=first_rule,
            params=dict(
                intensity=round(sp.intensity, 3),
                channel=sp.channel,
                drift=sp.drift,
                precursor=sp.precursor,
                victims=[self.sats[v] for v in getattr(self, "c1", {}).get("victims", [])],
            ),
            satellites=self.sats,
        )
        ml = schema.MLLabels(
            episode_id=sp.episode_id,
            t0=self.t0_unix,
            dt_s=self.dt,
            n_ticks=T,
            family=sp.family(),
            satellites=sat_truth,
        )
        return Episode(telemetry, commands, links, labels, ml)
