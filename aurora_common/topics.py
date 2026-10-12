"""Topic names of docs/interfaces.md Section 2: builders, a parser, QoS/retain rules and the
subscription rules that keep ground truth away from the CASSANDRA core."""

from __future__ import annotations

import re

PREFIX = "aurora/v1"
SATS = tuple("ABCDEFGHIJKL")
SUBSYSTEMS = ("thermal", "eps", "adcs", "obc", "comms", "sun")


def tm(sat: str, sub: str) -> str:
    return f"{PREFIX}/sat/{sat}/tm/{sub}"


def tc(sat: str) -> str:
    return f"{PREFIX}/sat/{sat}/tc"


def state(sat: str) -> str:
    return f"{PREFIX}/sat/{sat}/state"


def link(a: str, b: str) -> str:
    """Link topic with the ids in alphabetical order (link/A-B, never link/B-A)."""
    a, b = sorted((a, b))
    return f"{PREFIX}/link/{a}-{b}"


def forecast(sat: str) -> str:
    return f"{PREFIX}/core/forecast/{sat}"


def redteam(sat: str) -> str:
    return f"{PREFIX}/redteam/{sat}"


ACTION = f"{PREFIX}/core/action"
EVENT = f"{PREFIX}/core/event"
GT_EPISODE = f"{PREFIX}/gt/episode"
GT_EVENT = f"{PREFIX}/gt/event"

# The core subscribes to exactly these filters (interfaces.md Section 2) ...
CORE_ALLOWED_SUBSCRIPTIONS = (
    f"{PREFIX}/sat/+/tm/+",
    f"{PREFIX}/sat/+/tc",
    f"{PREFIX}/link/+",
)
# ... and must never see anything under these.
FORBIDDEN_FOR_CORE = (f"{PREFIX}/gt/#", f"{PREFIX}/redteam/#")

_PATTERNS = [
    ("tm", re.compile(rf"^{PREFIX}/sat/(?P<sat>[A-L])/tm/(?P<sub>{'|'.join(SUBSYSTEMS)})$")),
    ("tc", re.compile(rf"^{PREFIX}/sat/(?P<sat>[A-L])/tc$")),
    ("state", re.compile(rf"^{PREFIX}/sat/(?P<sat>[A-L])/state$")),
    ("link", re.compile(rf"^{PREFIX}/link/(?P<a>[A-L])-(?P<b>[A-L])$")),
    ("forecast", re.compile(rf"^{PREFIX}/core/forecast/(?P<sat>[A-L])$")),
    ("action", re.compile(rf"^{PREFIX}/core/action$")),
    ("event", re.compile(rf"^{PREFIX}/core/event$")),
    ("redteam", re.compile(rf"^{PREFIX}/redteam/(?P<sat>[A-L])$")),
    ("gt_episode", re.compile(rf"^{PREFIX}/gt/episode$")),
    ("gt_event", re.compile(rf"^{PREFIX}/gt/event$")),
]

QOS = {
    "tm": 0,
    "tc": 1,
    "state": 1,
    "link": 0,
    "forecast": 0,
    "action": 1,
    "event": 1,
    "redteam": 1,
    "gt_episode": 1,
    "gt_event": 1,
}
RETAIN = {"state"}


def kind_of(topic: str) -> tuple[str, dict[str, str]]:
    """Message kind and topic parameters, e.g. ('tm', {'sat': 'B', 'sub': 'thermal'}).
    Raises ValueError for a topic outside the contract."""
    for kind, rx in _PATTERNS:
        m = rx.match(topic)
        if m:
            params = m.groupdict()
            if kind == "link" and not params["a"] < params["b"]:
                raise ValueError(f"link topic must have a < b: {topic}")
            return kind, params
    raise ValueError(f"topic not in the contract: {topic}")


def qos_of(topic: str) -> int:
    return QOS[kind_of(topic)[0]]


def retain_of(topic: str) -> bool:
    return kind_of(topic)[0] in RETAIN


def matches(filt: str, topic: str) -> bool:
    """MQTT topic-filter matching (+ = one level, # = this level and below)."""
    f, t = filt.split("/"), topic.split("/")
    for i, part in enumerate(f):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(f) == len(t)


def filters_overlap(a: str, b: str) -> bool:
    """True if some topic could match both filters."""
    fa, fb = a.split("/"), b.split("/")
    for i in range(max(len(fa), len(fb))):
        pa = fa[i] if i < len(fa) else None
        pb = fb[i] if i < len(fb) else None
        if pa == "#" or pb == "#":
            return True
        if pa is None or pb is None:
            return False
        if pa != "+" and pb != "+" and pa != pb:
            return False
    return True


def core_may_subscribe(filt: str) -> bool:
    """The core may subscribe only to the allowed filters, and none of them may reach ground truth."""
    return filt in CORE_ALLOWED_SUBSCRIPTIONS and not any(
        filters_overlap(filt, bad) for bad in FORBIDDEN_FOR_CORE
    )
