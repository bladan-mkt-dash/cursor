"""Shared config for Digital Cross-Channel Tracker → Monthly Tracker sheet."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone

from tracker_layout import LAYOUTS, TrackerLayout

_layout: TrackerLayout = LAYOUTS[2026]

SPREADSHEET_ID = _layout.spreadsheet_id
SHEET = _layout.sheet
TRACKER_YEAR = _layout.version
JAN_COLUMN = _layout.jan_column


def configure_tracker(version: int) -> TrackerLayout:
    """Select 2025 or 2026 spreadsheet + row layout."""
    global _layout, SPREADSHEET_ID, SHEET, TRACKER_YEAR, JAN_COLUMN
    if version not in LAYOUTS:
        raise ValueError(f"Unknown tracker version {version}; use 2024, 2025, or 2026")
    _layout = LAYOUTS[version]
    SPREADSHEET_ID = _layout.spreadsheet_id
    SHEET = _layout.sheet
    TRACKER_YEAR = _layout.version
    JAN_COLUMN = _layout.jan_column
    return _layout


def active_layout() -> TrackerLayout:
    return _layout


def column_for_month(year: int, month: int) -> str:
    """Return sheet column letter for a calendar month (Jan = column H)."""
    if year != _layout.version:
        raise ValueError(
            f"Column map for tracker {_layout.version} does not include year {year}"
        )
    if not 1 <= month <= 12:
        raise ValueError("month must be 1–12")
    base = ord(JAN_COLUMN) - ord("A")
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
    """Accept ``2026-06`` or ``6`` (year defaults to active tracker year)."""
    value = value.strip()
    if "-" in value:
        y, m = value.split("-", 1)
        return int(y), int(m)
    return TRACKER_YEAR, int(value)


def iter_months(year_from: int, month_from: int, year_to: int, month_to: int):
    """Yield (year, month) inclusive."""
    y, m = year_from, month_from
    while (y, m) <= (year_to, month_to):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1
