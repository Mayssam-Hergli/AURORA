"""Send one telecommand on the bus (tests, demos, the G3 gate).

python -m twin.send_tc E HEATER on
python -m twin.send_tc E SET_MODE NOMINAL --origin core
python -m twin.send_tc B ISL_ENABLE A off
python -m twin.send_tc C SAFE_MODE
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid

from aurora_common import schemas as S
from aurora_common import topics as T


def _on(word: str) -> bool:
    w = word.lower()
    if w not in ("on", "off", "true", "false", "1", "0"):
        raise ValueError(f"expected on/off, got {word!r}")
    return w in ("on", "true", "1")


def build(sat: str, cmd: str, rest: list[str], origin: str = "ground") -> S.Telecommand:
    cmd = cmd.upper()
    if cmd == "HEATER":
        args = {"on": _on(rest[0])}
    elif cmd == "SET_MODE":
        args = {"mode": rest[0].upper()}
    elif cmd == "ISL_ENABLE":
        args = {"peer": rest[0].upper(), "on": _on(rest[1])}
    else:
        args = {}
    problems = S.args_problems(cmd, args)
    if problems:
        raise ValueError(f"{cmd} {args}: {problems}")
    return S.Telecommand(
        id=uuid.uuid4().hex, t=time.time(), sat=sat.upper(), origin=origin, cmd=cmd, args=args
    )


def main(argv=None) -> int:
    from aurora_common.bus import Bus

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sat")
    ap.add_argument("cmd")
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--origin", default="ground")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    a = ap.parse_args(argv)
    msg = build(a.sat, a.cmd, a.rest, a.origin)
    bus = Bus(f"send-tc-{uuid.uuid4().hex[:6]}", a.host, a.port)
    if not bus.start():
        print(f"no broker at {a.host}:{a.port} (make up)", file=sys.stderr)
        return 1
    bus.publish(T.tc(msg.sat), msg).wait_for_publish(5)
    bus.stop()
    print(f"sent {T.tc(msg.sat)} {S.dump(msg).decode()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
