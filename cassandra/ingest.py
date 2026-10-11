"""Observe stage: bus messages -> one WorldTick per second of message time.

Clock. Ticks are whole seconds of *message* time t (so a fast-forward replay runs the core
fast-forward). The clock source is the twin's messages; when no twin message has arrived for
`watchdog_s` of wall time, the hardware's messages drive it. Tick k closes when the clock reaches
k + 1 + close_grace_s. If the clock stops (broker down, plant stopped), a wall-clock watchdog keeps
closing ticks every second, so held values age and go stale instead of freezing.

Per (sat, sub): the latest value is held for `hold_s` seconds of message time, then marked stale;
`seq` must grow by 1 (gaps and resets become notes); `src` must match the satellite's profile.
Clock offset: the median of (t - receipt wall time) per source kind; hw vs twin above
`clock_offset_warn_s` gives a warning (Windows and WSL clocks can drift apart).

Single-threaded: the bus thread only enqueues; the core loop calls feed() and poll().
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict, deque
from dataclasses import dataclass

from aurora_common import schemas as S
from cassandra.world import Note, SatProfile, WorldTick


@dataclass
class _Obs:
    fields: dict
    t: float
    src: str
    emulated: tuple[str, ...]


class Ingest:
    def __init__(self, profiles: dict[str, SatProfile], cfg: dict):
        self.profiles = profiles
        c = cfg["ingest"]
        self.hold_s = float(c["hold_s"])
        self.grace = float(c["close_grace_s"])
        self.watchdog_s = float(c["watchdog_s"])
        self.offset_warn = float(c["clock_offset_warn_s"])
        self.offset_window = int(c["clock_offset_window"])
        self.clock_warn_every = float(c["clock_warn_every_s"])
        self.max_jump_s = float(c["max_jump_s"])

        self.obs: dict[tuple[str, str], _Obs] = {}
        self.last_seq: dict[tuple[str, str], int] = {}
        self.sat_src: dict[str, str] = {}
        self.links: dict[tuple[str, str], dict] = {}
        self.open_tick: int | None = None
        self.tcs: list[S.Telecommand] = []
        self.notes: list[Note] = []
        self.gaps: Counter = Counter()
        self.resets: Counter = Counter()
        self.invalid: Counter = Counter()
        self.late = 0
        self.stale_now: set[tuple[str, str]] = set()
        self.bad_src_seen: set[tuple[str, str]] = set()
        # clock
        self.clock_t: dict[str, float] = {}  # src kind -> latest message t
        self.clock_wall: dict[str, float] = {}  # src kind -> wall time of that message
        self.last_progress_wall: float | None = None
        self.offsets: dict[str, deque] = defaultdict(lambda: deque(maxlen=self.offset_window))
        self.offset_sample_wall: dict[str, float] = {}
        self.last_clock_warn = -math.inf
        self.stats: Counter = Counter()

    # ------------------------------------------------------------------ input
    def feed(self, topic: str, msg, wall: float) -> None:
        """One validated message (aurora_common model) received at wall time `wall` (time.time())."""
        if isinstance(msg, S.Telemetry):
            self._telemetry(msg, wall)
        elif isinstance(msg, S.Telecommand):
            self.tcs.append(msg)
            self.stats["tc"] += 1
        elif isinstance(msg, S.Link):
            self.links[(msg.a, msg.b)] = {
                "feasible": msg.feasible,
                "enabled": msg.enabled,
                "range_km": msg.range_km,
                "t": msg.t,
            }
            self._advance("twin", msg.t, wall)
            self.stats["link"] += 1

    def feed_invalid(self, topic: str, wall: float) -> None:
        """A message that failed the contract: counted and reported (it may be an attacker's)."""
        self.invalid[topic] += 1

    def _telemetry(self, m: S.Telemetry, wall: float) -> None:
        key = (m.sat, m.sub)
        self.stats["tm"] += 1
        if m.sat not in self.profiles:
            return
        prev = self.last_seq.get(key)
        if prev is not None:
            if m.seq > prev + 1:
                self.gaps[key] += m.seq - prev - 1
            elif m.seq <= prev:
                self.resets[key] += 1
        self.last_seq[key] = m.seq
        old = self.obs.get(key)
        if self.open_tick is not None and m.t < self.open_tick:
            self.late += 1  # still used: it updates the held value for the next tick
        if old is None or m.t >= old.t:
            self.obs[key] = _Obs(dict(m.fields), m.t, m.src, tuple(m.emulated or ()))
        self.sat_src[m.sat] = m.src
        want = self.profiles[m.sat].kind
        if m.src != want and (m.sat, m.src) not in self.bad_src_seen:
            self.bad_src_seen.add((m.sat, m.src))
            self.notes.append(
                Note(
                    "provenance",
                    "warning",
                    f"[{m.sat}] telemetry src={m.src!r} but this satellite is configured as {want!r}",
                    sat=m.sat,
                    sub=m.sub,
                    data={"src": m.src, "expected": want},
                )
            )
        kind = "hw" if m.src == "hw" else "twin"
        self.offsets[kind].append(m.t - wall)
        self.offset_sample_wall[kind] = wall
        self._advance(kind, m.t, wall)

    def _advance(self, kind: str, t: float, wall: float) -> None:
        if t > self.clock_t.get(kind, -math.inf):
            self.clock_t[kind] = t
            self.clock_wall[kind] = wall
        if self.open_tick is None:
            self.open_tick = math.floor(t)
            self.last_progress_wall = wall

    # ------------------------------------------------------------------ output
    def clock(self, wall: float) -> tuple[float | None, str]:
        """Current message-time clock and its source ('twin', 'hw' or 'none')."""
        for kind in ("twin", "hw"):
            if kind in self.clock_t and wall - self.clock_wall[kind] <= self.watchdog_s:
                return self.clock_t[kind], kind
        return None, "none"

    def poll(self, wall: float) -> list[WorldTick]:
        """Close every tick the clock (or the watchdog) has passed."""
        out: list[WorldTick] = []
        if self.open_tick is None:
            return out
        now, src = self.clock(wall)
        if now is not None and now - self.open_tick > self.max_jump_s:
            self.notes.append(
                Note(
                    "clock",
                    "warning",
                    f"message clock ({src}) jumped {now - self.open_tick:.0f} s ahead: re-syncing ticks",
                    data={"jump_s": round(now - self.open_tick, 1), "source": src},
                )
            )
            self.open_tick = math.floor(now) - 1
        while now is not None and now >= self.open_tick + 1 + self.grace:
            out.append(self._close("message"))
            self.last_progress_wall = wall
        if not out and wall - self.last_progress_wall >= self.watchdog_s:
            # no clock progress: close one tick per second of wall time
            n = int((wall - self.last_progress_wall) // 1.0)
            for _ in range(max(1, n)):
                out.append(self._close("watchdog"))
            self.last_progress_wall = wall
        if out:
            self._check_clock_offset(wall)
        return out

    def _close(self, by: str) -> WorldTick:
        k = self.open_tick
        end = k + 1.0
        values: dict[str, dict[str, dict]] = {}
        ages: dict[str, dict[str, float]] = {}
        stale: dict[str, list[str]] = {}
        emulated: dict[str, list[str]] = {}
        notes = self.notes
        self.notes = []
        for sat, prof in self.profiles.items():
            for sub in prof.subs:
                o = self.obs.get((sat, sub))
                age = math.inf if o is None else end - o.t
                ages.setdefault(sat, {})[sub] = age
                key = (sat, sub)
                if o is not None and age <= self.hold_s:
                    values.setdefault(sat, {})[sub] = o.fields
                    if o.emulated:
                        emulated.setdefault(sat, []).extend(o.emulated)
                    if key in self.stale_now:
                        self.stale_now.discard(key)
                        notes.append(Note("fresh", "info", f"[{sat}] {sub} telemetry is back", sat, sub))
                else:
                    stale.setdefault(sat, []).append(sub)
                    if key not in self.stale_now:
                        self.stale_now.add(key)
                        what = "never received" if o is None else f"no message for {age:.0f} s"
                        notes.append(
                            Note("stale", "warning", f"[{sat}] {sub} telemetry stale ({what})", sat, sub)
                        )
        for (sat, sub), n in self.gaps.items():
            notes.append(
                Note(
                    "seq_gap",
                    "warning",
                    f"[{sat}] {sub}: {n} message(s) missing (seq gap)",
                    sat,
                    sub,
                    {"missing": n},
                )
            )
        for (sat, sub), n in self.resets.items():
            notes.append(
                Note(
                    "seq_reset",
                    "warning",
                    f"[{sat}] {sub}: seq went backwards {n} time(s) (restart or replayed messages)",
                    sat,
                    sub,
                    {"count": n},
                )
            )
        for topic, n in self.invalid.items():
            notes.append(
                Note(
                    "invalid",
                    "warning",
                    f"{n} message(s) on {topic} broke the contract",
                    data={"topic": topic},
                )
            )
        if self.late:
            notes.append(Note("late", "info", f"{self.late} message(s) arrived after their tick closed"))
        self.gaps.clear()
        self.resets.clear()
        self.invalid.clear()
        self.late = 0
        tick = WorldTick(
            t=float(k),
            values=values,
            age_s=ages,
            stale=stale,
            src=dict(self.sat_src),
            emulated=emulated,
            links=dict(self.links),
            tcs=self.tcs,
            notes=notes,
            closed_by=by,
        )
        self.tcs = []
        self.open_tick = k + 1
        self.stats[f"ticks_{by}"] += 1
        return tick

    def clock_offsets(self, wall: float) -> dict[str, float]:
        """Median (message t - receipt wall time) per source kind seen in the last 10 s."""
        return {
            k: statistics.median(v)
            for k, v in self.offsets.items()
            if v and wall - self.offset_sample_wall.get(k, -math.inf) <= 10.0
        }

    def _check_clock_offset(self, wall: float) -> None:
        off = self.clock_offsets(wall)
        if "hw" in off and "twin" in off:
            d = off["hw"] - off["twin"]
            if abs(d) > self.offset_warn and wall - self.last_clock_warn >= self.clock_warn_every:
                self.last_clock_warn = wall
                self.notes.append(
                    Note(
                        "clock",
                        "warning",
                        f"hardware clock is {d:+.2f} s off the twin clock (limit {self.offset_warn} s): "
                        "check Windows/WSL time sync",
                        data={"offset_s": round(d, 3)},
                    )
                )
