"""Reset the stage between demo runs (make stage-reset): every attack released, every satellite back to
nominal, the dashboard on a clean session.

    python scripts/stage_reset.py [--host localhost] [--wait 20]

Publishes, for every satellite: redteam stop (clears freezes, biases, relays), a ground HEATER off, and a
ground SET_MODE NOMINAL (clears a held quarantine or safe mode: the ground clearance of interfaces.md 3.2).
Then waits for the core to heal, and the caller (make stage-reset) restarts the broker: retained states
are dropped and the dashboard, seeing the reconnect, starts a clean session (old incidents leave the screen).
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurora_common import schemas as S  # noqa: E402
from aurora_common import topics as T  # noqa: E402
from aurora_common.bus import Bus  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--wait", type=float, default=20.0, help="seconds to let the core heal before returning")
    a = ap.parse_args(argv)
    bus = Bus(f"stage-reset-{uuid.uuid4().hex[:6]}", a.host, a.port)
    if not bus.start():
        print("no broker: make up", file=sys.stderr)
        return 1
    now = time.time()
    for sat in T.SATS:
        bus.publish(T.redteam(sat), S.RedTeam(t=now, sat=sat, effect="stop", args={}))
        for cmd, args in (("HEATER", {"on": False}), ("SET_MODE", {"mode": "NOMINAL"})):
            tc = S.Telecommand(id=uuid.uuid4().hex, t=now, sat=sat, origin="ground", cmd=cmd, args=args)
            bus.publish(T.tc(sat), tc)
    print(f"released all 12 satellites; waiting {a.wait:.0f} s for the core to heal")
    time.sleep(a.wait)
    bus.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
