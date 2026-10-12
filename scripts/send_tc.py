"""Send one telecommand as the ground station (or another origin) on the AURORA bus.

    python scripts/send_tc.py E HEATER on=true
    python scripts/send_tc.py A SET_MODE mode=NOMINAL           # ground clears a held relay source
    python scripts/send_tc.py B ISL_ENABLE peer=C on=false --origin core
Values: true/false become booleans; anything else stays a string. The command is checked against the
contract (aurora_common.schemas.args_problems) before it is sent.
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


def parse_args_kv(items: list[str]) -> dict:
    out = {}
    for it in items:
        k, _, v = it.partition("=")
        out[k] = {"true": True, "false": False}.get(v.lower(), v)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="send one telecommand")
    ap.add_argument("sat", choices=list(T.SATS))
    ap.add_argument("cmd", choices=list(S.COMMANDS))
    ap.add_argument("args", nargs="*", help="key=value pairs")
    ap.add_argument("--origin", default="ground")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    a = ap.parse_args(argv)
    args = parse_args_kv(a.args)
    problems = S.args_problems(a.cmd, args)
    if problems:
        ap.error(f"{a.cmd} {args}: {problems}")
    msg = S.Telecommand(id=uuid.uuid4().hex, t=time.time(), sat=a.sat, origin=a.origin, cmd=a.cmd, args=args)
    bus = Bus(f"send-tc-{uuid.uuid4().hex[:6]}", a.host, a.port)
    if not bus.start():
        print(f"no broker at {a.host}:{a.port} (make up)", file=sys.stderr)
        return 1
    bus.publish(T.tc(a.sat), msg)
    time.sleep(0.5)
    bus.stop()
    print(f"sent {a.cmd} {args} to {a.sat} (origin {a.origin})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
