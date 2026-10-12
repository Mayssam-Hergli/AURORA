"""The typed cyber-physical graph G_t = (V, E_t, X_t) (docs/CONTEXT.md Section 2).

Nodes: for every satellite, its subsystems THERMAL, EPS, ADCS, OBC, COMMS, SUN and one node for
the satellite as a whole (SAT). Edge families, encoded as relation types for relation-aware
message passing:
    0 eng     E_eng: engineering data / power / heat / pointing dependencies
    1 sparta  E_SPARTA: attack relations, tagged with SPARTA technique IDs
    2 agg     subsystem -> SAT (the satellite as a whole)
    3 isl     E_ISL(t): COMMS_i -> COMMS_j, present only while the link is feasible and enabled
E_learned is not a separate edge set: learned couplings (strength w, delay tau) are attributes of
edges already permitted by E_eng U E_SPARTA, so the model cannot invent an impossible influence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

NODE_TYPES = ["thermal", "eps", "adcs", "obc", "comms", "sun", "sat"]
SUBSYSTEM_TYPES = NODE_TYPES[:6]
NPS = len(NODE_TYPES)  # nodes per satellite
REL = {"eng": 0, "sparta": 1, "agg": 2, "isl": 3}

E_ENG = [
    ("obc", "thermal"),
    ("obc", "eps"),
    ("obc", "adcs"),
    ("obc", "comms"),
    ("comms", "obc"),
    ("eps", "thermal"),
    ("thermal", "eps"),
    ("eps", "adcs"),
    ("adcs", "eps"),
    ("sun", "eps"),
    ("sun", "thermal"),
    ("adcs", "sun"),
]
E_SPARTA = {
    ("comms", "obc"): "IA-0009 / EX-0001.01 / EX-0013",
    ("obc", "thermal"): "EX-0012",
    ("obc", "adcs"): "EX-0012.08",
    ("obc", "eps"): "EX-0012.09",
    ("obc", "comms"): "DE-0002",
}
ISL_TECHNIQUE = "IA-0003"

# per-satellite raw channels (telemetry fields of Section 6.2 + command statistics from the tc topic)
CHANNELS = {
    "thermal": ["temp_c", "heater_on", "temp_c_missing", "temp_c_age_s"],
    "eps": ["soc_pct", "soc_pct_missing", "soc_pct_age_s", "bus_v", "load_w", "solar_w", "current_ma"],
    "adcs": [
        "att_err_deg",
        "att_err_deg_missing",
        "att_err_deg_age_s",
        "wheel_rpm_0",
        "wheel_rpm_1",
        "wheel_rpm_2",
        "rate_dps",
    ],
    "obc": [
        "mode_safe",
        "mode_quarantined",
        "d_cmd_ok",
        "d_cmd_rej",
        "tc_ground",
        "tc_isl",
        "tc_unknown",
        "tc_heater_on",
        "tc_set_mode",
        "tc_ping",
    ],
    "comms": ["ground_contact", "n_isl_peers", "rssi_dbm"],
    "sun": ["level", "eclipse"],
}
FLAT = [c for t in SUBSYSTEM_TYPES for c in CHANNELS[t]]  # 33 channels per satellite
SLICE = {}
_o = 0
for _t in SUBSYSTEM_TYPES:
    SLICE[_t] = (_o, _o + len(CHANNELS[_t]))
    _o += len(CHANNELS[_t])
F_RAW = max(len(v) for v in CHANNELS.values())  # 10, node inputs are padded to this


@dataclass
class Graph:
    n_sats: int
    src: np.ndarray
    dst: np.ndarray
    rel: np.ndarray
    flags: np.ndarray  # [E, 4] multi-hot edge families
    isl_pair: np.ndarray  # [E] index into the unordered satellite pairs, -1 if not ISL
    eng_pos: np.ndarray  # [E] index into the engineering-edge list, -1 if none
    eng_src: np.ndarray = field(default_factory=lambda: np.zeros(0, int))
    eng_dst: np.ndarray = field(default_factory=lambda: np.zeros(0, int))

    @property
    def n_nodes(self):
        return self.n_sats * NPS

    @property
    def n_edges(self):
        return len(self.src)


def node_id(sat: int, ntype: str) -> int:
    return sat * NPS + NODE_TYPES.index(ntype)


def node_type_of(n_sats: int) -> np.ndarray:
    return np.tile(np.arange(NPS), n_sats)


def sat_pairs(n_sats: int) -> list[tuple[int, int]]:
    a, b = np.triu_indices(n_sats, 1)
    return list(zip(a.tolist(), b.tolist()))


def build(n_sats: int, variant: str = "full") -> Graph:
    """variant: full | no_eng (E_eng removed) | unconstrained (every intra-satellite pair) | no_residual."""
    intra: dict[tuple[str, str], set] = {}
    if variant == "unconstrained":
        for a in SUBSYSTEM_TYPES:
            for b in SUBSYSTEM_TYPES:
                if a != b:
                    intra.setdefault((a, b), set()).add("eng" if (a, b) in E_ENG else "unc")
    else:
        if variant != "no_eng":
            for p in E_ENG:
                intra.setdefault(p, set()).add("eng")
        if variant != "no_sparta":
            for p in E_SPARTA:
                intra.setdefault(p, set()).add("sparta")
    for a in SUBSYSTEM_TYPES:
        intra.setdefault((a, "sat"), set()).add("agg")
    eng_list = [p for p in E_ENG if variant not in ("no_eng",)]
    src, dst, rel, flags, isl_pair, eng_pos = [], [], [], [], [], []
    eng_src, eng_dst = [], []
    for s in range(n_sats):
        for a, b in eng_list:
            eng_src.append(node_id(s, a))
            eng_dst.append(node_id(s, b))
    eng_index = {(u, v): k for k, (u, v) in enumerate(zip(eng_src, eng_dst))}
    for s in range(n_sats):
        for (a, b), fams in sorted(intra.items()):
            u, v = node_id(s, a), node_id(s, b)
            primary = (
                "eng"
                if "eng" in fams
                else "sparta" if "sparta" in fams else "agg" if "agg" in fams else "eng"
            )
            src.append(u)
            dst.append(v)
            rel.append(REL[primary])
            flags.append([float("eng" in fams), float("sparta" in fams), float("agg" in fams), 0.0])
            isl_pair.append(-1)
            eng_pos.append(eng_index.get((u, v), -1) if variant != "no_residual" else -1)
    for k, (a, b) in enumerate(sat_pairs(n_sats)):
        for u, v in ((a, b), (b, a)):
            src.append(node_id(u, "comms"))
            dst.append(node_id(v, "comms"))
            rel.append(REL["isl"])
            flags.append([0.0, 0.0, 0.0, 1.0])
            isl_pair.append(k)
            eng_pos.append(-1)
    return Graph(
        n_sats,
        np.array(src),
        np.array(dst),
        np.array(rel),
        np.array(flags, np.float32),
        np.array(isl_pair),
        np.array(eng_pos),
        np.array(eng_src, int),
        np.array(eng_dst, int),
    )
