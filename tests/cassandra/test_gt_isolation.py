"""Ground truth never reaches the defence (CONTEXT rule 7, MASTER_PLAN rule 0.5).

Static half: no code under cassandra/ names a ground-truth topic, the red-team topic, the label
files, or the engine's hidden attack flags. Comments and docstrings may explain the rule; code may not
break it. Runtime half: the core's bus subscribes to exactly CORE_ALLOWED_SUBSCRIPTIONS and refuses
anything else.
"""

import ast
import queue
import re
from pathlib import Path

import pytest

from aurora_common import topics as T

CORE = Path(__file__).resolve().parents[2] / "cassandra"
# The engine's hidden flags are "_malicious" / "_auth_ok" on their own; "p_malicious" is the
# forecast's contract field (a prediction), so a letter right before the underscore is allowed.
FORBIDDEN = re.compile(
    r"gt/|/gt\b|redteam|labels\.json|ml_labels|(?<![A-Za-z])_malicious|(?<![A-Za-z])_auth_ok|"
    r"malicious_cmd_ids|truth"
)


def code_tokens(path: Path) -> list[tuple[int, str]]:
    """Every identifier, attribute and string literal in the file, docstrings excluded."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            out.append((node.lineno, node.value))
        elif isinstance(node, ast.Name):
            out.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute):
            out.append((node.lineno, node.attr))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", "") or ""
            out += [(node.lineno, f"{mod}.{a.name}") for a in node.names]
    return out


@pytest.mark.parametrize("path", sorted(CORE.glob("*.py")), ids=lambda p: p.name)
def test_core_code_never_names_ground_truth(path):
    bad = [(line, tok) for line, tok in code_tokens(path) if FORBIDDEN.search(tok)]
    assert not bad, f"{path.name}: {bad}"


def test_the_scan_would_catch_a_leak():
    leaky = "msg['_malicious']; subscribe('aurora/v1/gt/event'); open('labels.json')"
    assert len(FORBIDDEN.findall(leaky)) == 3
    assert not FORBIDDEN.search("p_malicious")


def test_core_config_has_no_ground_truth_source():
    text = (CORE / "config.yaml").read_text(encoding="utf-8")
    assert not FORBIDDEN.search(text)


def test_core_bus_subscribes_to_exactly_the_allowed_filters():
    from cassandra import config
    from cassandra.__main__ import make_bus

    bus = make_bus(config.load(), queue.Queue(), "localhost", 1883)  # not started: no broker needed
    assert set(bus.subscriptions) == set(T.CORE_ALLOWED_SUBSCRIPTIONS)
    for filt in bus.subscriptions:
        assert T.core_may_subscribe(filt)
    for bad in ("aurora/v1/gt/#", "aurora/v1/gt/event", "aurora/v1/redteam/+", "aurora/v1/#", "#"):
        with pytest.raises(PermissionError):
            bus.subscribe(bad, lambda *_: None)
