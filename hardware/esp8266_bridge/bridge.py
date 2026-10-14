"""ESP8266 board <-> AURORA bus bridge: the board plays satellite B (Phase 8, MASTER_PLAN D10).

Runs on WINDOWS (USB/COM ports are visible only there); reaches the broker in WSL at localhost:1883.

    python bridge.py --port COM9 --sat B                 # the real board
    python bridge.py --dry-run --sat B                   # no board: a fake serial port running the
                                                         # same thermal model as the firmware
    python bridge.py --port COM9 --log curve.csv         # also log t, true/reported temp, heater (2 Hz)

Board -> bus. Each `TELEM sim=<C> heater=<0|1> heap=<n>` line (2 Hz) becomes
aurora/v1/sat/B/tm/thermal with src "hw" and emulated ["temp_c"] (the firmware computes the
temperature: there is no sensor). A minimal aurora/v1/sat/B/tm/obc (mode, command counters, last
origin) is published once per second.

Bus -> board. aurora/v1/sat/B/tc: HEATER on/off -> "H"/"C"; SAFE_MODE -> "C" and SAFE. The bridge
enforces SAFE for this firmware (interfaces.md 3.2): in SAFE it refuses HEATER on from any origin,
commands from origins other than ground/core, and SET_MODE other than NOMINAL/SAFE; only
SET_MODE NOMINAL exits SAFE; and if the board reports heater=1 while in SAFE (someone pressed FLASH)
the bridge sends "C" again. ISL_ENABLE is accepted and logged: with one board the link cut is
logical, not a radio cut.

LEDs. aurora/v1/sat/{A,B,C}/state -> "<idx><G|R|O>" with idx 1 = A, 2 = B, 3 = C:
nominal -> G, flagged -> R, quarantined / safe_hold -> O (both off). A and C are twin satellites;
the board only shows their state.

Red team. aurora/v1/redteam/B freeze_tm {sub: thermal, field: temp_c} -> the bridge holds the
reported temp_c while the board keeps heating (emulates compromised on-board software; label the run
as such); bias_tm adds a growing offset; stop clears both. relay_tc is not possible with one board.

Dependencies: pyserial (not needed with --dry-run) and paho-mqtt >= 2. No other part of the repo.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import threading
import time
import uuid

PREFIX = "aurora/v1"
LED_IDX = {"A": 1, "B": 2, "C": 3}
LED_CODE = {"nominal": "G", "flagged": "R", "quarantined": "O", "safe_hold": "O"}
ORIGIN_RE = re.compile(r"^(ground|core|unknown|isl:[A-L])$")
TELEM_RE = re.compile(r"^TELEM\s+(?:sim|temp)=([-0-9.]+)(?:\s+heater=([01]))?")
SET_MODES = ("NOMINAL", "SAFE", "SCIENCE", "IDLE", "WHEEL_TEST")


# ----------------------------------------------------------------------------- fake board (--dry-run)
class FakeBoard:
    """The ESP8266 firmware (prototype/phase2-demo/firmware/sat8266/sat8266_main.cpp) in software:
    25 C ambient, +1.5 C/s while the heater is latched (ceiling 95 C), 3 % per 100 ms cooling,
    +-0.10 C noise, TELEM every 500 ms, accepts H / C / <idx><G|R|O>; FLASH toggles the heater."""

    AMBIENT, CEILING, RAMP, COOL_K = 25.0, 95.0, 1.5, 0.03

    def __init__(self, seed: int = 0, clock=time.monotonic):
        self.clock = clock
        self.temp, self.heater = self.AMBIENT, False
        self.leds = {1: "G", 2: "G", 3: "G"}
        self.rng = random.Random(seed)
        self._out: list[str] = []
        self._inbuf = ""
        self._last_step = self._last_tele = clock()
        self.lock = threading.Lock()
        self.is_open = True

    def _advance(self):
        now = self.clock()
        while now - self._last_step >= 0.1:
            self._last_step += 0.1
            if self.heater:
                self.temp = min(self.CEILING, self.temp + self.RAMP * 0.1)
            else:
                self.temp += (self.AMBIENT - self.temp) * self.COOL_K
        while now - self._last_tele >= 0.5 - 1e-9:
            self._last_tele += 0.5
            noise = self.rng.randint(-10, 10) / 100.0
            self._out.append(f"TELEM sim={self.temp + noise:.2f} heater={int(self.heater)} heap=40000\r\n")

    def press_flash(self):
        with self.lock:
            self.heater = not self.heater

    # pyserial-like interface used by the bridge
    @property
    def in_waiting(self) -> int:
        with self.lock:
            self._advance()
            return sum(len(x) for x in self._out)

    def read(self, n: int = 1) -> bytes:
        with self.lock:
            data, self._out = "".join(self._out), []
        return data.encode()

    def write(self, data: bytes) -> int:
        with self.lock:
            self._inbuf += data.decode()
            while "\n" in self._inbuf:
                line, self._inbuf = self._inbuf.split("\n", 1)
                line = line.strip()
                if line == "H":
                    self.heater = True
                elif line == "C":
                    self.heater = False
                elif len(line) >= 2 and line[0] in "123":
                    self.leds[int(line[0])] = line[1]
        return len(data)

    def close(self):
        self.is_open = False


# ----------------------------------------------------------------------------- bridge logic
def args_ok(cmd: str, args: dict) -> bool:
    spec = {
        "HEATER": {"on": bool},
        "SAFE_MODE": {},
        "SET_MODE": {"mode": str},
        "ISL_ENABLE": {"peer": str, "on": bool},
        "PING": {},
    }
    if cmd not in spec or set(args) != set(spec[cmd]):
        return False
    if not all(isinstance(args[k], t) for k, t in spec[cmd].items()):
        return False
    return cmd != "SET_MODE" or args["mode"] in SET_MODES


class Bridge:
    """Pure logic: serial lines and bus messages in, serial writes and bus messages out."""

    def __init__(
        self, ser, publish, sat: str = "B", log_path: str | None = None, clock=time.time, echo=print
    ):
        self.ser, self.publish, self.sat, self.clock, self.echo = ser, publish, sat, clock, echo
        self.mode = "NOMINAL"
        self.cmd_ok = self.cmd_rej = 0
        self.last_origin = ""
        self.seq = {"thermal": 0, "obc": 0}
        self.temp_true: float | None = None
        self.temp_rep: float | None = None
        self.heater = 0
        self.freeze_at: float | None = None  # red-team freeze of temp_c
        self.bias: tuple[float, float] | None = None  # (rate per s, start time)
        self.leds: dict[str, str] = {}
        self.isl: dict[str, bool] = {}
        self.last_enforce = 0.0
        self.buf = ""
        self.log_file = open(log_path, "w", newline="") if log_path else None
        self.log = csv.writer(self.log_file) if self.log_file else None
        if self.log:
            self.log.writerow(["t_unix", "temp_true_c", "temp_reported_c", "heater_on", "mode"])

    # ---------------------------------------------------------------- board -> bus
    def poll_serial(self) -> None:
        n = getattr(self.ser, "in_waiting", 0)
        if not n:
            return
        self.buf += self.ser.read(n).decode(errors="ignore")
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.on_serial_line(line.strip())

    def on_serial_line(self, line: str) -> None:
        m = TELEM_RE.match(line)
        if not m:
            return
        t = self.clock()
        self.temp_true = float(m.group(1))
        self.heater = int(m.group(2) or 0)
        rep = self.temp_true
        if self.freeze_at is not None:
            rep = self.freeze_at
        if self.bias is not None:
            rate, t0 = self.bias
            rep = rep + rate * (t - t0)
        self.temp_rep = round(rep, 2)
        self.seq["thermal"] += 1
        self.publish(
            f"{PREFIX}/sat/{self.sat}/tm/thermal",
            {
                "v": 1,
                "sat": self.sat,
                "src": "hw",
                "t": t,
                "seq": self.seq["thermal"],
                "sub": "thermal",
                "fields": {"temp_c": self.temp_rep, "heater_on": self.heater},
                "emulated": ["temp_c"],
            },
            0,
            False,
        )
        if self.log:
            self.log.writerow(
                [f"{t:.3f}", f"{self.temp_true:.2f}", f"{self.temp_rep:.2f}", self.heater, self.mode]
            )
            self.log_file.flush()
        if self.mode == "SAFE" and self.heater and t - self.last_enforce > 1.0:
            self.last_enforce = t
            self._serial("C")
            self.echo("  SAFE: board reports heater=1 (FLASH pressed?): heater forced OFF by the bridge")

    def obc(self) -> None:
        self.seq["obc"] += 1
        self.publish(
            f"{PREFIX}/sat/{self.sat}/tm/obc",
            {
                "v": 1,
                "sat": self.sat,
                "src": "hw",
                "t": self.clock(),
                "seq": self.seq["obc"],
                "sub": "obc",
                "fields": {
                    "mode": "SAFE" if self.mode == "SAFE" else "NOMINAL",
                    "cmd_ok": self.cmd_ok,
                    "cmd_rej": self.cmd_rej,
                    "last_origin": self.last_origin,
                },
            },
            0,
            False,
        )

    # ---------------------------------------------------------------- bus -> board
    def _serial(self, s: str) -> None:
        self.ser.write((s + "\n").encode())

    def refused(self, origin: str, cmd: str, args: dict) -> str:
        """Why the command is refused ('' = accepted). interfaces.md 3.2; never asks who is malicious."""
        if not ORIGIN_RE.match(origin or ""):
            return "unknown origin format"
        if not args_ok(cmd, args):
            return "malformed args"
        if self.mode == "SAFE":
            if origin not in ("ground", "core"):
                return f"SAFE: origin {origin} refused"
            if cmd == "HEATER" and args["on"]:
                return "SAFE: HEATER on refused (load shedding)"
            if cmd == "SET_MODE" and args["mode"] not in ("NOMINAL", "SAFE"):
                return f"SAFE: SET_MODE {args['mode']} refused"
        return ""

    def on_tc(self, msg: dict) -> None:
        if msg.get("sat") != self.sat:
            return
        origin, cmd, args = msg.get("origin", ""), msg.get("cmd", ""), msg.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"_": args}
        self.last_origin = origin if ORIGIN_RE.match(origin or "") else self.last_origin
        why = self.refused(origin, cmd, args)
        if why:
            self.cmd_rej += 1
            self.echo(f"  TC {cmd} {args} from {origin}: REFUSED ({why})")
            return
        self.cmd_ok += 1
        if cmd == "HEATER":
            self._serial("H" if args["on"] else "C")
        elif cmd == "SAFE_MODE" or (cmd == "SET_MODE" and args["mode"] == "SAFE"):
            self.mode = "SAFE"
            self._serial("C")
        elif cmd == "SET_MODE" and args["mode"] == "NOMINAL":
            self.mode = "NOMINAL"
        elif cmd == "ISL_ENABLE":
            self.isl[args["peer"]] = args["on"]
        self.echo(f"  TC {cmd} {args} from {origin}: accepted (mode {self.mode})")

    def on_state(self, sat: str, state: str) -> None:
        if sat not in LED_IDX or state not in LED_CODE:
            return
        code = LED_CODE[state]
        if self.leds.get(sat) != code:
            self.leds[sat] = code
            self._serial(f"{LED_IDX[sat]}{code}")

    def on_redteam(self, msg: dict) -> None:
        if msg.get("sat") != self.sat:
            return
        eff, a = msg.get("effect"), msg.get("args") or {}
        if eff == "freeze_tm" and a.get("field") == "temp_c":
            self.freeze_at = self.temp_rep if self.temp_rep is not None else self.temp_true
            self.echo(
                f"  RED TEAM: reported temp_c frozen at {self.freeze_at} (emulated compromised software)"
            )
        elif eff == "bias_tm" and a.get("field") == "temp_c":
            self.bias = (float(a.get("rate_per_s", 0.0)), self.clock())
            self.echo(f"  RED TEAM: temp_c bias {self.bias[0]:+.3f} C/s (emulated compromised software)")
        elif eff == "stop":
            self.freeze_at, self.bias = None, None
            self.echo("  RED TEAM: effects cleared, telemetry honest again")
        else:
            self.echo(f"  RED TEAM: {eff} not supported on the board")

    def status(self) -> str:
        leds = " ".join(f"{s}:{self.leds.get(s, '-')}" for s in "ABC")
        frz = " FROZEN" if self.freeze_at is not None else ""
        t = "-" if self.temp_true is None else f"{self.temp_true:.2f}"
        r = "-" if self.temp_rep is None else f"{self.temp_rep:.2f}"
        return (
            f"  temp={t}C reported={r}C{frz} heater={self.heater} mode={self.mode} {leds} "
            f"ok={self.cmd_ok} rej={self.cmd_rej}"
        )

    def close(self):
        if self.log_file:
            self.log_file.close()


# ----------------------------------------------------------------------------- MQTT + main loop
def run(a) -> int:
    import paho.mqtt.client as mqtt

    if a.dry_run:
        ser = FakeBoard(seed=a.seed)
        print("[bridge] DRY RUN: fake serial port running the firmware's thermal model (no board)")
    else:
        import serial  # pyserial

        # DTR/RTS low before opening: on the NodeMCU, DTR drives GPIO0 (the FLASH button) and a default
        # open toggles the heater as if FLASH were pressed (seen on the real board, 14 Oct). Then "C" so
        # every run starts with the heater off.
        ser = serial.Serial()
        ser.port, ser.baudrate, ser.timeout = a.port, a.baud, 0
        ser.dtr = ser.rts = False
        ser.open()
        time.sleep(2.0)
        ser.write(b"C\n")
    inbox: list[tuple[str, bytes]] = []
    lock = threading.Lock()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"bridge-{a.sat}-{uuid.uuid4().hex[:6]}")
    client.reconnect_delay_set(1, 8)
    subs = [f"{PREFIX}/sat/{a.sat}/tc", f"{PREFIX}/redteam/{a.sat}"] + [
        f"{PREFIX}/sat/{s}/state" for s in LED_IDX
    ]

    def on_connect(c, userdata, flags, rc, props):
        for s in subs:
            c.subscribe(s, qos=1)
        print(f"[bridge] connected to {a.broker}:{a.mqtt_port}; satellite {a.sat}; LEDs A=1 B=2 C=3")

    def on_message(c, userdata, m):
        with lock:
            inbox.append((m.topic, m.payload))

    def publish(topic, payload, qos, retain):
        client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=qos, retain=retain)

    client.on_connect, client.on_message = on_connect, on_message
    client.connect_async(a.broker, a.mqtt_port, keepalive=10)
    client.loop_start()
    br = Bridge(ser, publish, a.sat, a.log)
    for s in LED_IDX:
        br.on_state(s, "nominal")
    last_obc = last_print = 0.0
    try:
        while True:
            br.poll_serial()
            with lock:
                todo, inbox[:] = list(inbox), []
            for topic, payload in todo:
                try:
                    msg = json.loads(payload)
                except ValueError:
                    continue
                if topic.endswith("/tc"):
                    br.on_tc(msg)
                elif topic.endswith("/state"):
                    br.on_state(topic.split("/")[-2], msg.get("state", ""))
                elif "/redteam/" in topic:
                    br.on_redteam(msg)
            now = time.time()
            if now - last_obc >= 1.0:
                last_obc = now
                br.obc()
            if now - last_print >= 1.0 and not a.quiet:
                last_print = now
                print(br.status(), flush=True)
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
        br.close()
        ser.close()
        print("[bridge] stopped")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ESP8266 board <-> AURORA bus bridge (satellite B)")
    ap.add_argument("--port", default="COM9", help="serial port of the board (Windows: COMx)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--sat", default="B", choices=list("ABCDEFGHIJKL"))
    ap.add_argument("--broker", default="localhost")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument(
        "--dry-run", action="store_true", help="no board: fake serial port with the firmware's model"
    )
    ap.add_argument("--log", default=None, help="CSV of t, true and reported temp_c, heater, mode (2 Hz)")
    ap.add_argument("--seed", type=int, default=0, help="noise seed of the fake board")
    ap.add_argument("--quiet", action="store_true", help="no status line every second")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
