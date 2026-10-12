"""cassandra/ingest.py: message-time ticks, wall-clock watchdog, hold/stale, seq, provenance, clock offset."""

import uuid

import pytest

from aurora_common import schemas as S
from aurora_common import topics as T
from cassandra import config
from cassandra.ingest import Ingest

FIELDS = {
    "thermal": {"temp_c": 20.0, "heater_on": 0},
    "eps": {"soc_pct": 80.0, "bus_v": 7.4, "load_w": 25.0, "solar_w": 40.0, "current_ma": 900.0},
    "adcs": {"att_err_deg": 0.1, "wheel_rpm": [1000.0, 1100.0, 1200.0], "rate_dps": 0.01},
    "obc": {"mode": "NOMINAL", "cmd_ok": 10, "cmd_rej": 0, "last_origin": "ground"},
    "comms": {"ground_contact": 0, "isl_peers": ["A"], "rssi_dbm": -80.0},
    "sun": {"level": 1.0, "eclipse": 0},
}
T0 = 1_800_000_000.0


def make(overrides=None, subs=("thermal",), sats=("B", "E")):
    """Ingest for a small constellation; only `subs` are expected, to keep tests readable."""
    cfg = config.load(
        overrides={
            "satellites": {"ids": list(sats), "defaults": {"subs": list(subs)}, "overrides": overrides or {}}
        }
    )
    return Ingest(config.profiles(cfg), cfg)


def tm(sat, sub, t, seq, src="twin", **fields):
    return S.Telemetry(sat=sat, src=src, t=t, seq=seq, sub=sub, fields={**FIELDS[sub], **fields})


def feed_tm(ing, sat, sub, t, seq, wall=None, **kw):
    msg = tm(sat, sub, t, seq, **kw)
    ing.feed(T.tm(sat, sub), msg, wall if wall is not None else t)


def test_ticks_close_on_message_time_not_before():
    ing = make()
    feed_tm(ing, "B", "thermal", T0 + 0.2, 1)
    feed_tm(ing, "E", "thermal", T0 + 0.2, 1)
    assert ing.poll(wall=T0 + 0.5) == []  # tick T0 still open
    feed_tm(ing, "E", "thermal", T0 + 1.2, 2)
    (tick,) = ing.poll(wall=T0 + 1.3)
    assert tick.t == T0 and tick.closed_by == "message"
    assert tick.values["B"]["thermal"]["temp_c"] == 20.0
    assert tick.stale == {}


def test_fast_forward_closes_one_tick_per_second_of_message_time():
    ing = make()
    for k in range(30):  # 30 s of message time delivered in 1.5 s of wall time
        feed_tm(ing, "B", "thermal", T0 + k, k, wall=T0 + k * 0.05)
        feed_tm(ing, "E", "thermal", T0 + k, k, wall=T0 + k * 0.05)
    ticks = ing.poll(wall=T0 + 1.5)
    assert [t.t for t in ticks] == [T0 + k for k in range(29)]


def test_values_are_held_then_marked_stale_with_one_note_each_way():
    ing = make()
    for k in range(10):
        feed_tm(ing, "E", "thermal", T0 + k, k)
        if k == 0:
            feed_tm(ing, "B", "thermal", T0, 0)  # B speaks once, then goes quiet
    ticks = ing.poll(wall=T0 + 9.5)
    held = [t for t in ticks if "thermal" in t.values.get("B", {})]
    assert [t.t - T0 for t in held] == [0, 1, 2]  # end of tick 2 is 3 s after B's message
    assert ticks[3].stale == {"B": ["thermal"]}
    stale_notes = [n for t in ticks for n in t.notes if n.kind == "stale"]
    assert len(stale_notes) == 1 and stale_notes[0].sat == "B"
    feed_tm(ing, "B", "thermal", T0 + 9.5, 1)
    feed_tm(ing, "E", "thermal", T0 + 10.1, 10)
    (tick,) = ing.poll(wall=T0 + 10.2)
    assert "thermal" in tick.values["B"]
    assert [n.kind for n in tick.notes if n.sat == "B"] == ["fresh"]


def test_watchdog_closes_ticks_when_messages_stop():
    ing = make()
    feed_tm(ing, "B", "thermal", T0, 0)
    feed_tm(ing, "E", "thermal", T0, 0)
    assert ing.poll(wall=T0 + 1.0) == []  # within the watchdog
    ticks = ing.poll(wall=T0 + 2.0)
    assert ticks and all(t.closed_by == "watchdog" for t in ticks)
    later = ing.poll(wall=T0 + 6.0)
    assert len(later) == 4  # one per second of wall time
    assert later[-1].stale == {"B": ["thermal"], "E": ["thermal"]}  # values aged out


def test_seq_gaps_and_resets_become_notes():
    ing = make()
    for t, seq in ((0, 5), (0.5, 6), (1.2, 9), (1.4, 2)):
        feed_tm(ing, "B", "thermal", T0 + t, seq)
    feed_tm(ing, "E", "thermal", T0 + 2.1, 1)
    ticks = ing.poll(wall=T0 + 2.2)
    notes = {n.kind: n for t in ticks for n in t.notes}
    assert notes["seq_gap"].data == {"missing": 2} and notes["seq_gap"].sat == "B"
    assert notes["seq_reset"].data == {"count": 1}


def test_provenance_mismatch_is_reported_once():
    ing = make(overrides={"B": {"kind": "hw"}})
    for k in range(3):
        feed_tm(ing, "B", "thermal", T0 + k, k, src="twin")  # B is the board: must be src hw
        feed_tm(ing, "E", "thermal", T0 + k, k, src="synthetic")  # offline data on the live bus
    notes = [n for t in ing.poll(wall=T0 + 2.5) for n in t.notes if n.kind == "provenance"]
    assert sorted((n.sat, n.data["src"]) for n in notes) == [("B", "twin"), ("E", "synthetic")]


@pytest.mark.parametrize("offset,warn", [(0.8, True), (-0.7, True), (0.3, False)])
def test_hw_vs_twin_clock_offset(offset, warn):
    ing = make(overrides={"B": {"kind": "hw"}})
    ticks = []
    for k in range(10):
        wall = T0 + k
        feed_tm(ing, "E", "thermal", T0 + k, k, wall=wall)  # twin clock = wall
        feed_tm(ing, "B", "thermal", T0 + k + offset, k, src="hw", wall=wall)  # Windows clock off
        ticks += ing.poll(wall=wall + 0.1)
    clock = [n for t in ticks for n in t.notes if n.kind == "clock"]
    assert len(clock) == (1 if warn else 0)  # rate-limited to one per minute
    assert ing.clock_offsets(T0 + 8)["hw"] == pytest.approx(offset, abs=1e-6)


def test_hw_drives_the_clock_when_no_twin_is_running():
    ing = make(overrides={"B": {"kind": "hw"}}, sats=("B",))
    for k in range(4):
        feed_tm(ing, "B", "thermal", T0 + k, k, src="hw", wall=T0 + k)
    ticks = ing.poll(wall=T0 + 3.1)
    assert [t.t - T0 for t in ticks] == [0, 1, 2] and ticks[0].closed_by == "message"


def test_telecommands_links_and_invalid_messages_land_in_the_tick():
    ing = make()
    feed_tm(ing, "E", "thermal", T0, 0)
    tc = S.Telecommand(id=uuid.uuid4().hex, t=T0 + 0.3, sat="E", origin="unknown", cmd="PING")
    ing.feed(T.tc("E"), tc, T0 + 0.3)
    ing.feed(T.link("B", "E"), S.Link(t=T0, a="B", b="E", feasible=True, enabled=False, range_km=900.0), T0)
    ing.feed_invalid(T.tc("E"), T0 + 0.4)
    feed_tm(ing, "E", "thermal", T0 + 1, 1)
    (tick,) = ing.poll(wall=T0 + 1.1)
    assert tick.tcs == [tc]
    assert tick.links[("B", "E")]["enabled"] is False
    assert [n.kind for n in tick.notes if n.kind == "invalid"] == ["invalid"]


def test_a_bogus_far_future_timestamp_resyncs_instead_of_replaying_years():
    ing = make()
    feed_tm(ing, "E", "thermal", T0, 0)
    feed_tm(ing, "E", "thermal", T0 + 10_000_000, 1)  # broken clock or spoofed message
    ticks = ing.poll(wall=T0 + 0.5)
    assert len(ticks) <= 2
    assert any(
        n.kind == "clock" and "jumped" in n.text for n in ing.notes + [n for t in ticks for n in t.notes]
    )
