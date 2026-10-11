"""
CASSANDRA serial bridge - WINDOWS side.

Owns the ESP32 (SatB) on a COM port, and talks to the Redis that runs
inside WSL via localhost (WSL2 forwards localhost between Windows and Linux).

  - Reads the board's real chip temp from TELEM lines -> Redis
        hw:chip_temp   = raw chip temp
        truth:temp     = mapped "satellite" temp (ground truth, always real)
        telemetry:temp = same, EXCEPT while a masking attack is active
                         (attack:active set) it leaves telemetry alone so the
                         poisoned value sticks and the monitor can catch it.
  - Drives the 3 dual-color LEDs from node:sat_a / node:sat_b / node:sat_c
        "green" -> G , "red" -> R , anything else (dark/quarantined) -> O

Run:
    python esp_link_win.py COM5
    (defaults to COM5; pass your real port as the first argument)

Only ONE program can hold the COM port at a time. Close arduino-cli monitor
before you start this.
"""
import os
import re
import sys
import time

import serial
import redis

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM5"
BAUD = 115200
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

NODES = {"sat_a": 1, "sat_b": 2, "sat_c": 3}


def to_sat_temp(chip):
    # remap raw ESP32 chip temp to a plausible "satellite bus" temp
    return round(20 + (chip - 45) * 1.25, 2)


def state_char(v):
    return {"green": "G", "red": "R"}.get(v, "O")


def main():
    print(f"[esp_link] opening {PORT} @ {BAUD} ...")
    ser = serial.Serial(PORT, BAUD, timeout=0.2)
    time.sleep(2)  # ESP32 resets when the port opens; wait for it
    print(f"[esp_link] linked  {PORT} <-> redis {REDIS_HOST}:{REDIS_PORT}")
    print("[esp_link] ctrl+c to stop\n")

    buf, last_led, last_print = "", {}, 0.0
    telem_re = re.compile(r"temp=([-\d.]+)")

    while True:
        # 1) board -> redis (real temperature)
        chunk = ser.read(256).decode(errors="ignore")
        if chunk:
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if line.startswith("TELEM"):
                    m = telem_re.search(line)
                    if m:
                        chip = float(m.group(1))
                        sat = to_sat_temp(chip)
                        r.set("hw:chip_temp", chip)
                        r.set("truth:temp", sat)          # always the truth
                        if not r.get("attack:active"):     # don't stomp a mask
                            r.set("telemetry:temp", sat)
                        now = time.time()
                        if now - last_print > 1.0:
                            last_print = now
                            print(f"  chip={chip:5.1f}C  sat_temp={sat:6.2f}C")

        # 2) redis -> LEDs (only send on change)
        for node, idx in NODES.items():
            sc = state_char(r.get(f"node:{node}") or "green")
            if last_led.get(idx) != sc:
                ser.write(f"{idx}{sc}\n".encode())
                last_led[idx] = sc

        time.sleep(0.1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[esp_link] stopped")
