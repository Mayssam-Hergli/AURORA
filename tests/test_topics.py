"""Topic rules, and the static half of ground-truth isolation:
no core subscription can reach gt/ or redteam/."""

import pytest

from aurora_common import topics as T


def test_builders_and_parser_agree():
    cases = {
        T.tm("B", "thermal"): ("tm", {"sat": "B", "sub": "thermal"}),
        T.tc("E"): ("tc", {"sat": "E"}),
        T.state("L"): ("state", {"sat": "L"}),
        T.link("C", "A"): ("link", {"a": "A", "b": "C"}),
        T.forecast("B"): ("forecast", {"sat": "B"}),
        T.ACTION: ("action", {}),
        T.EVENT: ("event", {}),
        T.redteam("A"): ("redteam", {"sat": "A"}),
        T.GT_EPISODE: ("gt_episode", {}),
        T.GT_EVENT: ("gt_event", {}),
    }
    for topic, want in cases.items():
        assert T.kind_of(topic) == want


@pytest.mark.parametrize(
    "topic", ["aurora/v1/sat/M/tc", "aurora/v1/sat/B/tm/payload", "aurora/v2/sat/B/tc", "aurora/v1/core/x"]
)
def test_topics_outside_the_contract_are_rejected(topic):
    with pytest.raises(ValueError):
        T.kind_of(topic)


def test_qos_and_retain():
    assert T.qos_of(T.tm("B", "eps")) == 0 and T.qos_of(T.tc("B")) == 1
    assert T.qos_of(T.ACTION) == 1 and T.qos_of(T.forecast("B")) == 0
    assert T.retain_of(T.state("B")) and not T.retain_of(T.tc("B"))


def test_wildcard_matching():
    assert T.matches("aurora/v1/sat/+/tm/+", "aurora/v1/sat/B/tm/thermal")
    assert not T.matches("aurora/v1/sat/+/tm/+", "aurora/v1/sat/B/tc")
    assert T.matches("aurora/v1/#", "aurora/v1/gt/event")
    assert not T.matches("aurora/v1/link/+", "aurora/v1/link")


def test_core_subscriptions_cannot_reach_ground_truth_or_redteam():
    every_topic = (
        [T.tm(s, sub) for s in T.SATS for sub in T.SUBSYSTEMS]
        + [T.tc(s) for s in T.SATS]
        + [T.redteam(s) for s in T.SATS]
        + [T.GT_EPISODE, T.GT_EVENT]
    )
    for f in T.CORE_ALLOWED_SUBSCRIPTIONS:
        assert T.core_may_subscribe(f)
        reached = [t for t in every_topic if T.matches(f, t)]
        assert not [t for t in reached if "/gt/" in t or "/redteam/" in t], f
    for bad in ("aurora/v1/#", "#", "aurora/v1/gt/#", "aurora/v1/+/episode", "aurora/v1/redteam/+"):
        assert not T.core_may_subscribe(bad), bad
