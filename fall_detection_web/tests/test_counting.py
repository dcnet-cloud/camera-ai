from datetime import datetime, date

from counting import (
    bucket_hourly, bucket_daily, summarize,
    block_from_counts, side_of, resolve_side, crossing_direction, in_x_range,
)


def _c(iso: str, direction: str) -> dict:
    return {"ts": datetime.fromisoformat(iso), "direction": direction}


def test_hourly_converts_utc_to_vn_hour():
    # 01:30 UTC == 08:30 VN -> hour 8
    rows = bucket_hourly([_c("2026-06-23T01:30:00+00:00", "in")], date(2026, 6, 23))
    assert rows[8]["in"] == 1
    assert sum(r["in"] for r in rows) == 1
    assert len(rows) == 24


def test_hourly_separates_in_out():
    rows = bucket_hourly(
        [
            _c("2026-06-23T02:00:00+00:00", "in"),
            _c("2026-06-23T02:10:00+00:00", "in"),
            _c("2026-06-23T02:20:00+00:00", "out"),
        ],
        date(2026, 6, 23),
    )
    assert rows[9]["in"] == 2   # 02 UTC == 09 VN
    assert rows[9]["out"] == 1


def test_hourly_vn_midnight_boundary():
    # 2026-06-22T17:30Z == 2026-06-23T00:30 VN -> belongs to the 23rd, hour 0
    c = [_c("2026-06-22T17:30:00+00:00", "in")]
    assert bucket_hourly(c, date(2026, 6, 23))[0]["in"] == 1
    assert sum(r["in"] for r in bucket_hourly(c, date(2026, 6, 22))) == 0


def test_daily_fills_range():
    rows = bucket_daily(
        [_c("2026-06-23T02:00:00+00:00", "in")], date(2026, 6, 21), date(2026, 6, 23)
    )
    assert len(rows) == 3
    assert rows[0]["date"] == date(2026, 6, 21) and rows[0]["in"] == 0
    assert rows[2]["date"] == date(2026, 6, 23) and rows[2]["in"] == 1


def test_summarize_occupancy():
    s = summarize(
        [
            _c("2026-06-23T02:00:00+00:00", "in"),
            _c("2026-06-23T02:00:00+00:00", "in"),
            _c("2026-06-23T03:00:00+00:00", "out"),
        ]
    )
    assert s == {"in": 2, "out": 1, "occupancy": 1}


def test_block_from_counts_basic():
    assert block_from_counts(3, 1) == {"in": 3, "out": 1, "occupancy": 2}


def test_block_from_counts_baseline_sets_in_only():
    # reset N=5 -> in = 5 + new_in, out = new_out, occ clamp
    assert block_from_counts(2, 0, baseline=5) == {"in": 7, "out": 0, "occupancy": 7}


def test_block_from_counts_occupancy_clamped():
    assert block_from_counts(1, 4)["occupancy"] == 0


def test_side_of_dead_band_returns_none():
    assert side_of(100, 100, 10) is None      # ngay vạch -> dead-band
    assert side_of(85, 100, 10) == "above"     # cy < 90
    assert side_of(115, 100, 10) == "below"    # cy > 110


def test_resolve_side_keeps_prev_in_dead_band():
    assert resolve_side("above", 100, 100, 10) == "above"   # vẫn trong band
    assert resolve_side("above", 115, 100, 10) == "below"   # đã qua band


def test_crossing_direction_above_to_below_is_in():
    assert crossing_direction("above", "below") == "in"
    assert crossing_direction("below", "above") == "out"


def test_crossing_direction_invert():
    assert crossing_direction("above", "below", invert=True) == "out"


def test_crossing_direction_no_change_or_none():
    assert crossing_direction("above", "above") is None
    assert crossing_direction(None, "below") is None
    assert crossing_direction("above", None) is None


def test_in_x_range():
    # frame_w=1000, 40%..70% -> [400,700]
    assert in_x_range(500, 1000, 40, 70) is True
    assert in_x_range(399, 1000, 40, 70) is False
    assert in_x_range(701, 1000, 40, 70) is False
