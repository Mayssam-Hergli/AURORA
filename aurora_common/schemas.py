"""Pydantic v2 models for every message of docs/interfaces.md, plus parse() and dump().

    msg = parse(topic, payload)      # bytes or str, compact or spaced JSON -> model (or ValueError)
    data = dump(msg)                 # model -> compact JSON bytes

Receivers are tolerant where the contract says so (extra fields, malformed telecommand args,
isl_peers as a comma string, args as a JSON string); publishers use args_problems() to stay strict.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from aurora_common import topics

SCHEMA_VERSION = 1
HORIZONS = ("10", "30", "60", "120")

SatId = Annotated[str, StringConstraints(pattern=r"^[A-L]$")]
Origin = Annotated[str, StringConstraints(pattern=r"^(ground|core|unknown|isl:[A-L])$")]
Prob = Annotated[float, Field(ge=0.0, le=1.0)]
Src = Literal["hw", "twin", "synthetic"]
Sub = Literal["thermal", "eps", "adcs", "obc", "comms", "sun"]
Cmd = Literal["HEATER", "SAFE_MODE", "SET_MODE", "ISL_ENABLE", "PING"]
SetMode = Literal["NOMINAL", "SAFE", "SCIENCE", "IDLE", "WHEEL_TEST"]
ObcMode = Literal["NOMINAL", "SAFE", "QUARANTINED"]
NodeState = Literal["nominal", "flagged", "quarantined", "safe_hold"]
ActionName = Literal["none", "safe_mode", "block_source", "isolate_subsystem", "quarantine"]
Flag = Annotated[int, Field(ge=0, le=1)]

SET_MODES = SetMode.__args__
COMMANDS = Cmd.__args__
NODE_STATES = NodeState.__args__


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow")
    # keys written as null by dump() even when None (D4: "gated": null, "q": null)
    KEEP_NULL: ClassVar[frozenset[str]] = frozenset()


class _Msg(_Model):
    v: Literal[1] = 1


# ----------------------------------------------------------------------------- telemetry (3.1)
class ThermalFields(_Model):
    temp_c: float
    heater_on: Flag


class EpsFields(_Model):
    soc_pct: float
    bus_v: float
    load_w: float
    solar_w: float
    current_ma: float


class AdcsFields(_Model):
    att_err_deg: float
    wheel_rpm: Annotated[list[float], Field(min_length=3, max_length=3)]
    rate_dps: float


class ObcFields(_Model):
    mode: ObcMode
    cmd_ok: Annotated[int, Field(ge=0)]
    cmd_rej: Annotated[int, Field(ge=0)]
    last_origin: Origin | Literal[""]


class CommsFields(_Model):
    ground_contact: Flag
    isl_peers: list[SatId]
    rssi_dbm: float

    @field_validator("isl_peers", mode="before")
    @classmethod
    def _peers_from_string(cls, v):
        if v is None:
            return []
        if isinstance(v, str):  # offline generator form: "A,C" ("" or "None" = no peers)
            return [p for p in v.split(",") if p and p != "None"]
        return v


class SunFields(_Model):
    level: Annotated[float, Field(ge=0.0)]
    eclipse: Flag


FIELD_MODELS: dict[str, type[_Model]] = {
    "thermal": ThermalFields,
    "eps": EpsFields,
    "adcs": AdcsFields,
    "obc": ObcFields,
    "comms": CommsFields,
    "sun": SunFields,
}


class Telemetry(_Msg):
    sat: SatId
    src: Src
    t: float
    seq: Annotated[int, Field(ge=0)]
    sub: Sub
    fields: dict[str, Any]
    emulated: list[str] | None = None

    @model_validator(mode="after")
    def _check_fields(self):
        self.fields = FIELD_MODELS[self.sub].model_validate(self.fields).model_dump()
        if self.emulated:
            unknown = set(self.emulated) - set(self.fields)
            if unknown:
                raise ValueError(f"emulated names fields not in this message: {sorted(unknown)}")
        return self


# ----------------------------------------------------------------------------- telecommand (3.2)
class Telecommand(_Msg):
    id: Annotated[str, StringConstraints(min_length=1)]
    t: float
    sat: SatId
    origin: Origin
    cmd: Cmd
    args: dict[str, Any] = Field(default_factory=dict)

    @field_validator("args", mode="before")
    @classmethod
    def _args_from_string(cls, v):
        if v is None:
            return {}
        if isinstance(v, str):  # episode Parquet form: args stored as a JSON string
            return json.loads(v) if v.strip() else {}
        return v


def args_problems(cmd: str, args: dict) -> list[str]:
    """Why these args are not valid for cmd (empty list = valid). For publishers we write."""
    spec: dict[str, dict] = {
        "HEATER": {"on": bool},
        "SAFE_MODE": {},
        "SET_MODE": {"mode": str},
        "ISL_ENABLE": {"peer": str, "on": bool},
        "PING": {},
    }
    if cmd not in spec:
        return [f"unknown cmd {cmd!r}"]
    want, out = spec[cmd], []
    for k, typ in want.items():
        if k not in args:
            out.append(f"missing {k!r}")
        elif not isinstance(args[k], typ):
            out.append(f"{k!r} must be {typ.__name__}")
    out += [f"unexpected {k!r}" for k in args if k not in want]
    if cmd == "SET_MODE" and isinstance(args.get("mode"), str) and args["mode"] not in SET_MODES:
        out.append(f"mode must be one of {SET_MODES}")
    if cmd == "ISL_ENABLE" and isinstance(args.get("peer"), str) and args["peer"] not in topics.SATS:
        out.append("peer must be a satellite id A-L")
    return out


# ----------------------------------------------------------------------------- state, link (3.3, 3.4)
class NodeStateMsg(_Msg):
    sat: SatId
    t: float
    state: NodeState
    reason: str | None = None


class Link(_Msg):
    t: float
    a: SatId
    b: SatId
    feasible: bool
    enabled: bool
    range_km: Annotated[float, Field(ge=0.0)]

    @model_validator(mode="after")
    def _ordered(self):
        if not self.a < self.b:
            raise ValueError("link requires a < b alphabetically")
        return self


# ----------------------------------------------------------------------------- core outputs (3.5-3.7)
class Forecast(_Msg):
    KEEP_NULL = frozenset({"p_std", "q", "next_sub", "p_malicious"})
    sat: SatId
    t: float
    model: Annotated[str, StringConstraints(min_length=1)]
    p_fail: dict[str, Prob]
    p_std: dict[str, Annotated[float, Field(ge=0.0)]] | None = None
    q: Annotated[float, Field(ge=0.0)] | None = None
    next_sub: Sub | None = None
    p_malicious: Prob | None = None
    warming_up: bool | None = None

    @model_validator(mode="after")
    def _horizons(self):
        if tuple(self.p_fail) != HORIZONS:
            raise ValueError(f"p_fail keys must be {HORIZONS} in this order")
        vals = [self.p_fail[h] for h in HORIZONS]
        if any(b < a - 1e-6 for a, b in zip(vals, vals[1:])):
            raise ValueError("p_fail must be non-decreasing in the horizon")
        if self.p_std is not None and not set(self.p_std) <= set(HORIZONS):
            raise ValueError(f"p_std keys must be among {HORIZONS}")
        return self


class Alternative(_Model):
    action: ActionName
    J: float | None = None


class Action(_Msg):
    KEEP_NULL = frozenset({"J", "gated", "q"})
    t: float
    action: ActionName
    target: SatId
    args: dict[str, Any] | None = None
    J: float | None = None
    alternatives: list[Alternative] = Field(default_factory=list)
    gated: bool | None = None
    q: Annotated[float, Field(ge=0.0)] | None = None
    reason: str


class Event(_Msg):
    KEEP_NULL = frozenset({"sat"})
    t: float
    sat: SatId | None = None
    level: Literal["info", "warning", "alarm"]
    kind: Literal[
        "startup", "forecast", "monitor", "decision", "gated", "budget", "heal", "ingest", "clock", "incident"
    ]
    text: str
    sparta: list[str] | None = None
    data: dict[str, Any] | None = None


# ----------------------------------------------------------------------------- red team (3.8)
_REDTEAM_ARGS = {
    "freeze_tm": {"sub", "field"},
    "bias_tm": {"sub", "field", "rate_per_s"},
    "relay_tc": {"cmd", "args"},
    "stop": set(),
}


class RedTeam(_Msg):
    t: float
    sat: SatId
    effect: Literal["freeze_tm", "bias_tm", "relay_tc", "stop"]
    args: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _args(self):
        missing = _REDTEAM_ARGS[self.effect] - set(self.args)
        if missing:
            raise ValueError(f"{self.effect} needs args {sorted(missing)}")
        if self.effect == "relay_tc":
            probs = args_problems(self.args["cmd"], self.args["args"])
            if probs:
                raise ValueError(f"relay_tc command invalid: {probs}")
        return self


# ----------------------------------------------------------------------------- labels and episodes (4)
class EpisodeLabels(_Model):
    """labels.json (field-for-field compatible with ml.schema.EpisodeLabels)."""

    episode_id: str
    generator: str
    seed: int
    scenario: str  # A | B | C | F | N
    kind: Literal["attack", "fault", "clean"]
    type: str  # A1..A3, B1, B2, C1, F1..F5, N
    target: str | None
    onset_t: float | None
    critical_t: float | None
    critical_rule: str | None
    params: dict = Field(default_factory=dict)
    satellites: list[str]


class SatelliteTruth(_Model):
    onset_t: float | None = None
    critical_t: float | None = None
    critical_rule: str | None = None
    malicious: bool | None = None
    affected_t: dict[str, float] = Field(default_factory=dict)
    reported_masked: list[str] = Field(default_factory=list)


class MLLabels(_Model):
    """ml_labels.json (D2): times in seconds since t0; t0 in Unix seconds."""

    episode_id: str
    t0: float
    dt_s: float
    n_ticks: int
    family: dict = Field(default_factory=dict)
    satellites: dict[str, SatelliteTruth]


# ----------------------------------------------------------------------------- ground truth (3.9)
class GtEpisode(EpisodeLabels, _Msg):
    t: float
    phase: Literal["start", "end"]


class GtEvent(_Msg):
    t: float
    episode_id: str
    event: Literal["onset", "stop", "critical"]
    sat: SatId
    data: dict[str, Any] | None = None


# ----------------------------------------------------------------------------- parse / dump
MODEL_OF_KIND: dict[str, type[_Model]] = {
    "tm": Telemetry,
    "tc": Telecommand,
    "state": NodeStateMsg,
    "link": Link,
    "forecast": Forecast,
    "action": Action,
    "event": Event,
    "redteam": RedTeam,
    "gt_episode": GtEpisode,
    "gt_event": GtEvent,
}


def parse(topic: str, payload: bytes | str | dict) -> _Model:
    """Validate a message against its topic. Raises ValueError (pydantic's ValidationError is one)."""
    kind, params = topics.kind_of(topic)
    data = json.loads(payload) if isinstance(payload, (bytes, str)) else payload
    msg = MODEL_OF_KIND[kind].model_validate(data)
    for key, val in params.items():  # topic and payload must agree
        if key in ("a", "b", "sat", "sub") and getattr(msg, key) != val:
            raise ValueError(f"topic says {key}={val!r} but payload says {getattr(msg, key)!r}")
    return msg


def dump(msg: _Model) -> bytes:
    """Compact JSON. None-valued optional fields are omitted; required fields (e.g. critical_t) and the
    fields the contract writes as null (KEEP_NULL, e.g. q and gated) are always present."""
    data = msg.model_dump(mode="json")
    fields = type(msg).model_fields
    keep = type(msg).KEEP_NULL | {k for k, f in fields.items() if f.is_required()}
    data = {k: v for k, v in data.items() if v is not None or k in keep}
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()


__all__ = [
    "SCHEMA_VERSION",
    "HORIZONS",
    "Telemetry",
    "Telecommand",
    "NodeStateMsg",
    "Link",
    "Forecast",
    "Action",
    "Alternative",
    "Event",
    "RedTeam",
    "EpisodeLabels",
    "SatelliteTruth",
    "MLLabels",
    "GtEpisode",
    "GtEvent",
    "args_problems",
    "parse",
    "dump",
    "ValidationError",
]
