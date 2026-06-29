"""Pure crossing-event bucketing. Stdlib only — unit-testable, no DB.

A crossing = {"ts": aware datetime (UTC from DB), "direction": "in"|"out"}.
Counting = COUNT of crossings, bucketed in Asia/Ho_Chi_Minh (UTC+7).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

VN = ZoneInfo("Asia/Ho_Chi_Minh")


def _vn_dt(ts: datetime) -> datetime:
    return ts.astimezone(VN)


def bucket_hourly(crossings: list[dict], day: date) -> list[dict]:
    """24 rows for the given VN day."""
    buckets = [{"hour": h, "in": 0, "out": 0} for h in range(24)]
    for c in crossings:
        t = _vn_dt(c["ts"])
        if t.date() != day:
            continue
        if c["direction"] in ("in", "out"):
            buckets[t.hour][c["direction"]] += 1
    return buckets


def bucket_daily(crossings: list[dict], since: date, until: date) -> list[dict]:
    """One row per VN date in [since, until], zero-filled."""
    days: dict[date, dict] = {}
    cur = since
    while cur <= until:
        days[cur] = {"date": cur, "in": 0, "out": 0}
        cur += timedelta(days=1)
    for c in crossings:
        d = _vn_dt(c["ts"]).date()
        if d in days and c["direction"] in ("in", "out"):
            days[d][c["direction"]] += 1
    return [days[k] for k in sorted(days)]


def summarize(crossings: list[dict]) -> dict:
    ins = sum(1 for c in crossings if c["direction"] == "in")
    outs = sum(1 for c in crossings if c["direction"] == "out")
    return {"in": ins, "out": outs, "occupancy": ins - outs}


# ── Dual-counting test helpers (block compute + YOLO line-crossing) ──

def block_from_counts(raw_in: int, raw_out: int, baseline: int = 0) -> dict:
    """Số hiển thị 1 block: baseline cộng vào IN, OUT giữ nguyên, occupancy clamp."""
    ins = int(baseline) + int(raw_in)
    outs = int(raw_out)
    return {"in": ins, "out": outs, "occupancy": max(0, ins - outs)}


def side_of(cy: float, y_line: float, band: float) -> str | None:
    """Phía của tâm so với vạch, với dead-band ±band. None = đang trong band."""
    if cy < y_line - band:
        return "above"
    if cy > y_line + band:
        return "below"
    return None


def resolve_side(prev: str | None, cy: float, y_line: float, band: float) -> str | None:
    s = side_of(cy, y_line, band)
    return s if s is not None else prev


def crossing_direction(prev_side: str | None, new_side: str | None,
                       invert: bool = False) -> str | None:
    """above→below = 'in' (đảo nếu invert). None nếu chưa đổi side rõ ràng."""
    if prev_side is None or new_side is None or prev_side == new_side:
        return None
    direction = "in" if (prev_side == "above" and new_side == "below") else "out"
    if invert:
        direction = "out" if direction == "in" else "in"
    return direction


def in_x_range(cx: float, frame_w: float, x_start_pct: float, x_end_pct: float) -> bool:
    x0 = x_start_pct / 100.0 * frame_w
    x1 = x_end_pct / 100.0 * frame_w
    return x0 <= cx <= x1
