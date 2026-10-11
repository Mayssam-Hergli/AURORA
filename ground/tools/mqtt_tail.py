"""Watch the AURORA bus: one line per message, `topic | src | t | payload`.

python ground/tools/mqtt_tail.py                          # everything except ground truth
python ground/tools/mqtt_tail.py --filter 'aurora/v1/sat/E/#'
python ground/tools/mqtt_tail.py --gt                     # also show gt/ (humans only, never the core)
python ground/tools/mqtt_tail.py --rate                   # messages per second per topic kind, every 5 s
python ground/tools/mqtt_tail.py --check                  # also flag messages that break the contract
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aurora_common import schemas, topics  # noqa: E402
from aurora_common.bus import Bus  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--filter", default=f"{topics.PREFIX}/#")
    ap.add_argument("--gt", action="store_true", help="show ground-truth topics too")
    ap.add_argument("--rate", action="store_true", help="print message rates instead of messages")
    ap.add_argument("--check", action="store_true", help="validate every message against the contract")
    ap.add_argument("--width", type=int, default=160, help="truncate payloads to this many characters")
    a = ap.parse_args(argv)

    counts: Counter = Counter()
    gt_prefix = f"{topics.PREFIX}/gt/"

    def on_raw(topic: str, payload: bytes):
        if topic.startswith(gt_prefix) and not a.gt:
            return
        try:
            kind = topics.kind_of(topic)[0]
        except ValueError:
            kind = "?"
        if a.rate:
            counts[kind] += 1
            return
        text = payload.decode(errors="replace")
        try:
            d = json.loads(text)
            src, t = d.get("src", "-"), d.get("t", "-")
            text = json.dumps(d, separators=(",", ":"))
        except (ValueError, AttributeError):
            src, t = "-", "-"
        bad = ""
        if a.check:
            try:
                schemas.parse(topic, payload)
            except (ValueError, KeyError) as exc:
                bad = f"  !! INVALID: {str(exc).splitlines()[0]}"
        if len(text) > a.width:
            text = text[: a.width - 3] + "..."
        print(f"{topic} | {src} | {t} | {text}{bad}", flush=True)

    bus = Bus(f"mqtt-tail-{int(time.time())}", a.host, a.port)
    bus.subscribe(a.filter, on_raw, parse=False)
    if not bus.start():
        print(f"waiting for broker at {a.host}:{a.port} ...", file=sys.stderr)
    try:
        while True:
            time.sleep(5)
            if a.rate:
                line = "  ".join(f"{k}={n / 5:.1f}/s" for k, n in sorted(counts.items())) or "no messages"
                print(time.strftime("%H:%M:%S"), line, flush=True)
                counts.clear()
    except KeyboardInterrupt:
        pass
    finally:
        bus.stop()


if __name__ == "__main__":
    main()
