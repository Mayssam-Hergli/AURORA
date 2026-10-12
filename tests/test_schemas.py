"""The contract (docs/interfaces.md) and aurora_common agree: every example in the document validates,
compact and spaced JSON both parse, bad enums and topic/payload mismatches fail."""

import json
import re
from pathlib import Path

import pytest

from aurora_common import schemas as S
from aurora_common import topics as T

DOC = Path(__file__).resolve().parents[1] / "docs" / "interfaces.md"


def doc_examples() -> list[tuple[str, dict]]:
    """(section heading, payload) for every JSON line in a ```json block of interfaces.md."""
    out, heading, in_block = [], None, False
    for line in DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
        elif line.strip() == "```json":
            in_block = True
        elif line.strip() == "```":
            in_block = False
        elif in_block and line.strip():
            out.append((heading, json.loads(line)))
    return out


def topic_for(heading: str, d: dict) -> str | None:
    """Topic of a document example, from its section heading."""
    rules = {
        "3.1": lambda: T.tm(d["sat"], d["sub"]),
        "3.2": lambda: T.tc(d["sat"]),
        "3.3": lambda: T.state(d["sat"]),
        "3.4": lambda: T.link(d["a"], d["b"]),
        "3.5": lambda: T.forecast(d["sat"]),
        "3.6": lambda: T.ACTION,
        "3.7": lambda: T.EVENT,
        "3.8": lambda: T.redteam(d["sat"]),
        "3.9": lambda: T.GT_EPISODE if "phase" in d else T.GT_EVENT,
    }
    m = re.match(r"(\d\.\d)", heading)
    return rules[m.group(1)]() if m and m.group(1) in rules else None


EXAMPLES = doc_examples()


def test_document_has_examples_for_every_message_kind():
    kinds = {T.kind_of(topic_for(h, d))[0] for h, d in EXAMPLES if topic_for(h, d)}
    assert kinds == set(S.MODEL_OF_KIND)


@pytest.mark.parametrize("heading,payload", EXAMPLES, ids=[h.split(":")[0] for h, _ in EXAMPLES])
def test_every_document_example_validates(heading, payload):
    topic = topic_for(heading, payload)
    if topic is not None:
        msg = S.parse(topic, payload)
        assert S.parse(topic, S.dump(msg)) == msg  # dump/parse round trip
    elif "episode_id" in payload and "t0" in payload:
        S.MLLabels.model_validate(payload)
    else:
        S.EpisodeLabels.model_validate(payload)


def test_compact_and_spaced_json_parse_the_same():
    compact = '{"v":1,"id":"ab12","t":1.0,"sat":"B","origin":"ground","cmd":"HEATER","args":{"on":true}}'
    spaced = json.dumps(json.loads(compact), indent=2)
    a, b = S.parse(T.tc("B"), compact), S.parse(T.tc("B"), spaced.encode())
    assert a == b and a.args == {"on": True}
    assert S.dump(a).decode().startswith('{"v":1,"id":"ab12"')  # compact out


@pytest.mark.parametrize(
    "topic,payload",
    [
        (T.tc("B"), {"id": "x", "t": 1.0, "sat": "B", "origin": "ground", "cmd": "LAUNCH", "args": {}}),
        (T.tc("B"), {"id": "x", "t": 1.0, "sat": "B", "origin": "attacker", "cmd": "PING", "args": {}}),
        (T.state("B"), {"sat": "B", "t": 1.0, "state": "purple"}),
        (
            T.tm("B", "thermal"),
            {
                "sat": "B",
                "src": "sim",
                "t": 1.0,
                "seq": 0,
                "sub": "thermal",
                "fields": {"temp_c": 1, "heater_on": 0},
            },
        ),
        (
            T.tm("B", "obc"),
            {
                "sat": "B",
                "src": "hw",
                "t": 1.0,
                "seq": 0,
                "sub": "obc",
                "fields": {"mode": "SCIENCE", "cmd_ok": 0, "cmd_rej": 0, "last_origin": ""},
            },
        ),
        (T.redteam("B"), {"t": 1.0, "sat": "B", "effect": "melt", "args": {}}),
        (T.EVENT, {"t": 1.0, "level": "panic", "kind": "decision", "text": "x"}),
    ],
)
def test_bad_enum_fails(topic, payload):
    with pytest.raises(ValueError):
        S.parse(topic, payload)


def test_topic_and_payload_must_agree():
    payload = {"sat": "C", "t": 1.0, "state": "nominal"}
    with pytest.raises(ValueError, match="topic says"):
        S.parse(T.state("B"), payload)
    tm = {"sat": "B", "src": "hw", "t": 1.0, "seq": 0, "sub": "eps", "fields": {}}
    with pytest.raises(ValueError):
        S.parse(T.tm("B", "thermal"), tm)


def test_missing_required_telemetry_field_fails():
    with pytest.raises(ValueError):
        S.parse(
            T.tm("B", "thermal"),
            {"sat": "B", "src": "hw", "t": 1.0, "seq": 0, "sub": "thermal", "fields": {"temp_c": 30.0}},
        )


def test_emulated_must_name_fields_of_the_message():
    base = {
        "sat": "B",
        "src": "hw",
        "t": 1.0,
        "seq": 0,
        "sub": "thermal",
        "fields": {"temp_c": 30.0, "heater_on": 1},
    }
    S.parse(T.tm("B", "thermal"), {**base, "emulated": ["temp_c"]})
    with pytest.raises(ValueError):
        S.parse(T.tm("B", "thermal"), {**base, "emulated": ["soc_pct"]})


def test_forecast_rules():
    ok = {
        "sat": "B",
        "t": 1.0,
        "model": "trajectory-v0",
        "p_fail": {"10": 0.1, "30": 0.2, "60": 0.5, "120": 0.9},
    }
    f = S.parse(T.forecast("B"), ok)
    assert f.q is None and f.p_std is None
    out = json.loads(S.dump(f))
    assert out["q"] is None and out["p_std"] is None and out["p_malicious"] is None  # D4: explicit nulls
    with pytest.raises(ValueError, match="non-decreasing"):
        S.parse(T.forecast("B"), {**ok, "p_fail": {"10": 0.5, "30": 0.2, "60": 0.5, "120": 0.9}})
    with pytest.raises(ValueError):
        S.parse(T.forecast("B"), {**ok, "p_fail": {"10": 0.1, "30": 0.2, "60": 1.5, "120": 1.5}})
    with pytest.raises(ValueError):
        S.parse(T.forecast("B"), {**ok, "p_fail": {"30": 0.2, "60": 0.5, "120": 0.9}})


def test_action_writes_gated_null_when_the_gate_is_inactive():
    a = S.Action(t=1.0, action="safe_mode", target="B", J=None, gated=None, q=None, reason="single model")
    out = json.loads(S.dump(a))
    assert out["gated"] is None and out["q"] is None and "args" not in out


def test_telecommand_tolerance_and_publisher_strictness():
    # receivers: malformed args are delivered (the attacker's probe is evidence) ...
    probe = S.parse(
        T.tc("B"),
        {"id": "x", "t": 1.0, "sat": "B", "origin": "ground", "cmd": "SET_MODE", "args": {"on": True}},
    )
    assert probe.args == {"on": True}
    # ... args stored as a JSON string (episode Parquet) are accepted ...
    s = S.parse(
        T.tc("B"),
        {"id": "x", "t": 1.0, "sat": "B", "origin": "isl:A", "cmd": "HEATER", "args": '{"on": true}'},
    )
    assert s.args == {"on": True}
    # ... and publishers check args strictly.
    assert S.args_problems("SET_MODE", {"on": True}) == ["missing 'mode'", "unexpected 'on'"]
    assert S.args_problems("SET_MODE", {"mode": "QUARANTINED"})  # a state, not a mode (D3)
    assert S.args_problems("ISL_ENABLE", {"peer": "Z", "on": False})
    assert S.args_problems("HEATER", {"on": True}) == []
    assert S.args_problems("ISL_ENABLE", {"peer": "C", "on": False}) == []


def test_isl_peers_accepts_list_and_comma_string():
    base = {"sat": "B", "src": "twin", "t": 1.0, "seq": 0, "sub": "comms"}
    for peers, want in (("A,C", ["A", "C"]), ("", []), ("None", []), (["D"], ["D"])):
        m = S.parse(
            T.tm("B", "comms"),
            {**base, "fields": {"ground_contact": 0, "isl_peers": peers, "rssi_dbm": -80.0}},
        )
        assert m.fields["isl_peers"] == want


def test_link_order():
    assert T.link("B", "A") == "aurora/v1/link/A-B"
    with pytest.raises(ValueError):
        T.kind_of("aurora/v1/link/B-A")
    with pytest.raises(ValueError):
        S.Link(t=1.0, a="B", b="A", feasible=True, enabled=True, range_km=1.0)


def test_redteam_relay_command_must_be_valid():
    S.RedTeam(t=1.0, sat="A", effect="relay_tc", args={"cmd": "HEATER", "args": {"on": True}})
    with pytest.raises(ValueError):
        S.RedTeam(t=1.0, sat="A", effect="relay_tc", args={"cmd": "HEATER", "args": {}})
    with pytest.raises(ValueError):
        S.RedTeam(t=1.0, sat="A", effect="freeze_tm", args={"sub": "thermal"})


def test_label_models_match_ml_schema_field_for_field():
    """ml/schema.py (Youssef's folder) keeps its own copies; they must not drift from the contract."""
    from ml import schema as ml_schema

    for name in ("EpisodeLabels", "SatelliteTruth", "MLLabels"):
        assert set(getattr(S, name).model_fields) == set(getattr(ml_schema, name).model_fields), name


def test_live_engine_messages_satisfy_the_contract():
    """What scripts/replay_twin.py will publish: SyntheticTwin.messages_at(t), unchanged."""
    from ml import config
    from ml.synthetic.engine import SyntheticTwin
    from tests.conftest import make_spec

    cfg, spec = make_spec("A1", seed=4)
    tw = SyntheticTwin(cfg, spec).run()
    seen = set()
    for t in (0, 100, 200, 300, 400):
        for kind, msg in tw.messages_at(t):
            if kind == "tm":
                topic = T.tm(msg["sat"], msg["sub"])
            elif kind == "tc":
                topic = T.tc(msg["sat"])
            else:
                topic = T.link(msg["a"], msg["b"])
            S.parse(topic, msg)
            seen.add(kind)
    assert {"tm", "link"} <= seen
    assert config.load("synthetic")["satellites"] == list(T.SATS)
