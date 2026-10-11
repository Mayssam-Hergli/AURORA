"""Round trip through the real broker: publish one valid message of each core-relevant kind with
aurora_common.bus, receive them back, validate them, and check the retained node state.

    python ground/tools/bus_check.py [--host localhost] [--port 1883]
Exit code 0 = the bus works end to end.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aurora_common import schemas as S  # noqa: E402
from aurora_common import topics as T  # noqa: E402
from aurora_common.bus import Bus  # noqa: E402


def samples(now: float) -> list[tuple[str, object]]:
    return [
        (
            T.tm("E", "thermal"),
            S.Telemetry(
                sat="E", src="twin", t=now, seq=1, sub="thermal", fields={"temp_c": 21.5, "heater_on": 0}
            ),
        ),
        (
            T.tc("E"),
            S.Telecommand(id=uuid.uuid4().hex, t=now, sat="E", origin="ground", cmd="PING", args={}),
        ),
        (T.link("E", "D"), S.Link(t=now, a="D", b="E", feasible=True, enabled=True, range_km=2100.0)),
        (T.state("E"), S.NodeStateMsg(sat="E", t=now, state="nominal", reason="bus check")),
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    a = ap.parse_args(argv)

    got: dict[str, object] = {}
    done = threading.Event()
    sent = samples(time.time())

    def on_msg(topic, msg):
        got[topic] = msg
        if len(got) == len(sent):
            done.set()

    rx = Bus(f"bus-check-rx-{uuid.uuid4().hex[:6]}", a.host, a.port)
    for f in (*T.CORE_ALLOWED_SUBSCRIPTIONS, f"{T.PREFIX}/sat/+/state"):
        rx.subscribe(f, on_msg)
    if not rx.start():
        print(f"FAIL: no broker at {a.host}:{a.port} (run `make up`)")
        return 1
    tx = Bus(f"bus-check-tx-{uuid.uuid4().hex[:6]}", a.host, a.port)
    tx.start()
    for topic, msg in sent:
        tx.publish(topic, msg)
    ok = done.wait(5)
    for topic, msg in sent:
        status = "ok " if topic in got and got[topic] == msg else "MISSING/DIFFERENT"
        print(f"  {status} {topic}  {S.dump(msg).decode()}")

    # a client that connects later still gets the retained node state
    late: dict[str, object] = {}
    lb = Bus(f"bus-check-late-{uuid.uuid4().hex[:6]}", a.host, a.port)
    lb.subscribe(T.state("E"), lambda t, m: late.setdefault(t, m))
    lb.start()
    time.sleep(1.0)
    retained_ok = T.state("E") in late
    print(f"  {'ok ' if retained_ok else 'MISSING'} retained {T.state('E')} for a late subscriber")
    for b in (rx, tx, lb):
        b.stop()
    passed = ok and retained_ok and all(got.get(t) == m for t, m in sent)
    print("PASS: bus round trip" if passed else "FAIL: bus round trip")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
