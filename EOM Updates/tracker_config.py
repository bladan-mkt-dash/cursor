"""Shared config for 2026 Digital Cross-Channel Tracker → Monthly Tracker sheet."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone

# 2026 Digital Cross-Channel Tracker
SPREADSHEET_ID = "1F7Lq0IBrOWolov5vEx5ztcBsZTbZCKfalQ1bwHuqakc"
SHEET = "Monthly Tracker"
TRACKER_YEAR = 2026
# Jan 2026 = column H (see sheet header row)
JAN_2026_COLUMN = "H"


def column_for_month(year: int, month: int) -> str:
    """Return sheet column letter for a calendar month on the 2026 tracker."""
    if year != TRACKER_YEAR:
        raise ValueError(f"Column map is only defined for {TRACKER_YEAR}, got {year}")
    if not 1 <= month <= 12:
        raise ValueError("month must be 1–12")
    base = ord(JAN_2026_COLUMN) - ord("A")
    return chr(ord("A") + base + month - 1)


def prior_column(year: int, month: int) -> str | None:
    if month <= 1:
        return None
    return column_for_month(year, month - 1)


def month_date_range(year: int, month: int) -> tuple[str, str]:
    """Inclusive GA4 / GHL date strings YYYY-MM-DD."""
    last = calendar.monthrange(year, month)[1]
    return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last:02d}"


def month_period_utc(
    year: int, month: int
) -> tuple[datetime, datetime, datetime]:
    """
    Return (period_start, period_end_exclusive, insights_until) in UTC.

    period_end_exclusive: first instant of next month (Meta/IG windowing).
    insights_until: last instant usable in <=30-day Meta insight chunks
    (May uses day 30 when the month has 31 days).
    """
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    last_day = end - timedelta(seconds=1)
    cap = start + timedelta(days=29, hours=23, minutes=59, seconds=59)
    insights_until = min(last_day, cap)
    return start, end, insights_until


def month_period_dates(year: int, month: int) -> tuple[date, date]:
    """Inclusive calendar dates for YouTube and similar APIs."""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def parse_month_arg(value: str) -> tuple[int, int]:
    """Accept ``2026-06`` or ``6`` (year defaults to TRACKER_YEAR)."""
    value = value.strip()
    if "-" in value:
        y, m = value.split("-", 1)
        return int(y), int(m)
    return TRACKER_YEAR, int(value)
