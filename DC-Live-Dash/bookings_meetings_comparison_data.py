"""
Org-wide bookings / meetings for YoY / QoQ comparison charts.

- Through Aug 2025: Digital Cross-Channel Tracker **Bookings (all booked calls)** row.
- From Sep 2025: GoHighLevel calendar **meetings** (``startTime`` in range, all calendars).
"""

from __future__ import annotations

import importlib
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DC_LIVE_DIR = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_DC_LIVE_DIR) not in sys.path:
    sys.path.insert(0, str(_DC_LIVE_DIR))

from digital_channel_live_data import (
    DEFAULT_SINCE,
    GHL_SIGNUPS_SINCE,
    SHEETS_SIGNUPS_UNTIL,
)
from signup_comparison_data import (
    aggregate_signups_qoq,
    aggregate_signups_yoy,
    qoq_quarter_numbers,
    tier_quarter_filter_options,
    tier_signup_until,
    tier_year_filter_options,
)

BOOKINGS_MEETINGS_COMPARISON_REVISION = "2026-06-24-discovery-calls-charts-v5"
BOOKINGS_MEETINGS_CATEGORY = ""
DISCOVERY_CALLS_LABEL = "Discovery Calls"
BOOKINGS_ROW_LABEL = "Bookings (all booked calls)"

_MONTHLY_COLUMNS = ("month", "count")
_CHART_COLUMNS = ("month", "membership_level", "signups")


def _tracker_chart_module():
    """Return tracker sheet helpers, reloading when Streamlit has a stale cache."""
    import total_new_members_yoy_chart as mod

    needs_reload = not all(
        hasattr(mod, name)
        for name in (
            "_get_tracker_grid",
            "_grid_col_c_label",
            "_load_row_year_series",
            "TRACKERS",
        )
    )
    if needs_reload:
        mod = importlib.reload(mod)
    return mod


def _bookings_row_num(
    tracker: Any, sheets: Any, spreadsheet_id: str, sheet_name: str
) -> int:
    """Row index for Bookings (all booked calls) — works with stale tracker modules."""
    if hasattr(tracker, "_find_bookings_all_booked_calls_row"):
        return tracker._find_bookings_all_booked_calls_row(
            sheets, spreadsheet_id, sheet_name
        )
    grid = tracker._get_tracker_grid(sheets, spreadsheet_id, sheet_name)
    for i in range(1, min(len(grid), 250) + 1):
        if tracker._grid_col_c_label(grid, i) == BOOKINGS_ROW_LABEL:
            return i
    raise RuntimeError(
        f"Could not find {BOOKINGS_ROW_LABEL!r} row in {spreadsheet_id}"
    )


def _load_bookings_year_series(
    tracker: Any, sheets: Any, spreadsheet_id: str, sheet_name: str, year: int
) -> pd.Series:
    row_num = _bookings_row_num(tracker, sheets, spreadsheet_id, sheet_name)
    return tracker._load_row_year_series(
        sheets, spreadsheet_id, sheet_name, year, row_num
    )


def _month_bounds(since: str, until: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()
    return since_month, until_month


def load_tracker_bookings_monthly(
    since: str,
    until: str,
) -> tuple[pd.DataFrame, list[str]]:
    """Bookings (all booked calls) from cross-channel tracker workbooks."""
    from googleapiclient.discovery import build

    tracker = _tracker_chart_module()

    since_month, until_month = _month_bounds(since, until)
    sheet_until = min(
        until_month,
        pd.Timestamp(SHEETS_SIGNUPS_UNTIL).to_period("M").to_timestamp(),
    )
    if since_month > sheet_until:
        return pd.DataFrame(columns=_MONTHLY_COLUMNS), []

    creds = tracker._credentials()
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)

    records: list[dict[str, Any]] = []
    notes: list[str] = []
    years_needed = set(range(since_month.year, sheet_until.year + 1))

    for meta in tracker.TRACKERS.values():
        year = meta["year"]
        if year not in years_needed:
            continue
        file_info = tracker._resolve_tracker_spreadsheet(drive, meta)
        sid = file_info["id"]
        tab = tracker._resolve_sheet_name(sheets, sid, tracker.DEFAULT_SHEET)
        series = _load_bookings_year_series(tracker, sheets, sid, tab, year)
        for month_num, val in series.items():
            month = pd.Timestamp(year=year, month=int(month_num), day=1)
            if month < since_month or month > sheet_until:
                continue
            records.append({"month": month, "count": float(val)})
        notes.append(
            f"Bookings (sheet): {meta['name']} / {tab} — "
            "Bookings (all booked calls) row."
        )

    if not records:
        return pd.DataFrame(columns=_MONTHLY_COLUMNS), notes
    return pd.DataFrame(records), notes


def _fetch_ghl_meetings_monthly_cached(since: str, until: str) -> dict[str, Any]:
    from dashboard_disk_cache import read_json_range_cache
    from ghl_client import fetch_calendar_meetings_monthly_by_start_time

    return read_json_range_cache(
        "ghl_calendar_meetings",
        since,
        until,
        lambda: fetch_calendar_meetings_monthly_by_start_time(since, until),
    )


def load_ghl_meetings_monthly(since: str, until: str) -> tuple[pd.DataFrame, list[str]]:
    """Org-wide GHL meetings by calendar month (``startTime``)."""
    from ghl_client import fetch_calendar_meetings_monthly_by_start_time

    since_month, until_month = _month_bounds(since, until)
    ghl_since = max(
        since_month,
        pd.Timestamp(GHL_SIGNUPS_SINCE).to_period("M").to_timestamp(),
    )
    if until_month < ghl_since:
        return pd.DataFrame(columns=_MONTHLY_COLUMNS), []

    payload = _fetch_ghl_meetings_monthly_cached(
        ghl_since.date().isoformat(),
        until,
    )
    records: list[dict[str, Any]] = []
    for row in payload.get("monthly") or []:
        month = pd.Timestamp(row["month_start"])
        if month < ghl_since or month > until_month:
            continue
        records.append({"month": month, "count": float(row.get("meetings") or 0)})

    notes = [
        f"Meetings (GHL): calendar startTime from {GHL_SIGNUPS_SINCE} through "
        f"{until_month:%b %Y}."
    ]
    if payload.get("calendar_api_errors"):
        notes.append(
            f"GHL calendar API errors while loading meetings: "
            f"{payload['calendar_api_errors']}."
        )
    if not records:
        return pd.DataFrame(columns=_MONTHLY_COLUMNS), notes
    return pd.DataFrame(records), notes


def load_bookings_meetings_monthly(
    since: str,
    until: str,
) -> tuple[pd.DataFrame, list[str]]:
    """Tracker bookings through Aug 2025 + GHL meetings from Sep 2025."""
    sheet_df, notes = load_tracker_bookings_monthly(since, until)
    ghl_df, ghl_notes = load_ghl_meetings_monthly(since, until)
    notes.extend(ghl_notes)

    if sheet_df.empty and ghl_df.empty:
        return pd.DataFrame(columns=_MONTHLY_COLUMNS), notes
    if ghl_df.empty:
        return sheet_df, notes
    if sheet_df.empty:
        return ghl_df, notes

    combined = pd.concat([sheet_df, ghl_df], ignore_index=True)
    combined = (
        combined.groupby("month", as_index=False)["count"]
        .sum()
        .sort_values("month")
        .reset_index(drop=True)
    )
    return combined, notes


def bookings_meetings_since() -> date:
    return pd.Timestamp(DEFAULT_SINCE).date()


def bookings_meetings_until() -> date:
    return tier_signup_until()


def monthly_for_signup_charts(monthly: pd.DataFrame) -> pd.DataFrame:
    """Shape monthly bookings/meetings for reuse of signup tier YoY / QoQ helpers."""
    if monthly.empty:
        return pd.DataFrame(columns=_CHART_COLUMNS)
    out = monthly.copy()
    out["membership_level"] = BOOKINGS_MEETINGS_CATEGORY
    out["signups"] = out["count"]
    return out[["month", "membership_level", "signups"]]


def load_bookings_meetings_comparison_monthly() -> tuple[pd.DataFrame, list[str]]:
    """
    Full-history monthly series for bookings / meetings YoY and QoQ charts.

    Independent of the dashboard performance date range.
    """
    since = bookings_meetings_since()
    until = bookings_meetings_until()
    since_s = since.isoformat()
    until_s = until.isoformat()

    monthly, notes = load_bookings_meetings_monthly(since_s, until_s)
    notes.insert(
        0,
        f"Bookings & meetings: {since.year} through {until:%b %d, %Y} "
        "(independent of the performance date range).",
    )
    return monthly, notes


__all__ = [
    "BOOKINGS_MEETINGS_CATEGORY",
    "BOOKINGS_MEETINGS_COMPARISON_REVISION",
    "DISCOVERY_CALLS_LABEL",
    "aggregate_signups_qoq",
    "aggregate_signups_yoy",
    "bookings_meetings_since",
    "bookings_meetings_until",
    "load_bookings_meetings_comparison_monthly",
    "load_bookings_meetings_monthly",
    "load_ghl_meetings_monthly",
    "load_tracker_bookings_monthly",
    "monthly_for_signup_charts",
    "qoq_quarter_numbers",
    "tier_quarter_filter_options",
    "tier_year_filter_options",
]
