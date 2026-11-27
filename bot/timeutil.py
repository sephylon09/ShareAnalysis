"""Time helpers. US/Eastern handled by hand so no tzdata package is needed (Windows)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: str) -> datetime:
    """Parse an RFC3339 timestamp (Alpaca returns nanosecond precision sometimes)."""
    v = value.strip().replace("Z", "+00:00")
    # trim fractional seconds to 6 digits
    if "." in v:
        head, rest = v.split(".", 1)
        frac = ""
        tail = ""
        for i, ch in enumerate(rest):
            if ch.isdigit():
                frac += ch
            else:
                tail = rest[i:]
                break
        v = f"{head}.{frac[:6].ljust(6, '0')}{tail}"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nth_sunday(year: int, month: int, n: int) -> datetime:
    d = datetime(year, month, 1)
    offset = (6 - d.weekday()) % 7  # weekday(): Mon=0 .. Sun=6
    return d + timedelta(days=offset + 7 * (n - 1))


def et_offset(dt_utc: datetime) -> timedelta:
    """UTC offset of US/Eastern at the given UTC instant (EDT -4h / EST -5h)."""
    y = dt_utc.year
    # DST starts 2nd Sunday of March 02:00 EST (=07:00 UTC), ends 1st Sunday of Nov 02:00 EDT (=06:00 UTC)
    start = _nth_sunday(y, 3, 2).replace(hour=7, tzinfo=timezone.utc)
    end = _nth_sunday(y, 11, 1).replace(hour=6, tzinfo=timezone.utc)
    return timedelta(hours=-4) if start <= dt_utc < end else timedelta(hours=-5)


def to_et(dt_utc: datetime) -> datetime:
    """Naive datetime in US/Eastern wall-clock time."""
    dt_utc = dt_utc.astimezone(timezone.utc)
    return (dt_utc + et_offset(dt_utc)).replace(tzinfo=None)


def et_date(dt_utc: datetime) -> str:
    return to_et(dt_utc).strftime("%Y-%m-%d")


def et_hhmm(dt_utc: datetime) -> str:
    return to_et(dt_utc).strftime("%H:%M")


def minutes_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60.0
