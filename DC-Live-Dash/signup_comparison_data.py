"""
Org-wide signups by membership level for YoY / QoQ comparison charts.

- Through Aug 2025: Digital Cross-Channel Tracker **Both Locations** tier rows.
- From Sep 2025: GoHighLevel committed signups by Sign Up Date + Membership Level.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from april_new_members_tier_yoy import BOTH_LOCATIONS_ROWS, TRACKERS
from digital_channel_live_data import (
    GHL_SIGNUPS_SINCE,
    MEMBERSHIP_LEVELS,
    SHEETS_SIGNUPS_UNTIL,
)
from total_new_members_yoy_chart import (
    DEFAULT_SHEET,
    _credentials,
    _load_row_year_series,
    _resolve_sheet_name,
    _resolve_tracker_spreadsheet,
)

SIGNUP_COMPARISON_REVISION = "2026-06-22-ghl-signups-by-tier-v1"

_SIGNUP_COLUMNS = ("month", "membership_level", "signups")


def _month_bounds(since: str, until: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()
    return since_month, until_month


def _year_chart_label(year: int, *, until: date) -> str:
    year_end = date(year, 12, 31)
    if until < year_end:
        return f"{year} YTD"
    return str(year)


def load_tracker_signups_by_level_monthly(
    since: str,
    until: str,
) -> tuple[pd.DataFrame, list[str]]:
    """Both Locations tier rows from cross-channel tracker workbooks."""
    from googleapiclient.discovery import build

    since_month, until_month = _month_bounds(since, until)
    sheet_until = min(
        until_month,
        pd.Timestamp(SHEETS_SIGNUPS_UNTIL).to_period("M").to_timestamp(),
    )
    if since_month > sheet_until:
        return pd.DataFrame(columns=_SIGNUP_COLUMNS), []

    creds = _credentials()
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)

    records: list[dict[str, Any]] = []
    notes: list[str] = []
    for year in range(since_month.year, sheet_until.year + 1):
        if year not in TRACKERS or year not in BOTH_LOCATIONS_ROWS:
            continue
        file_info = _resolve_tracker_spreadsheet(
            drive, {"year": year, "name": TRACKERS[year]}
        )
        sid = file_info["id"]
        tab = _resolve_sheet_name(sheets, sid, DEFAULT_SHEET)
        for tier, row_num in BOTH_LOCATIONS_ROWS[year].items():
            if tier not in MEMBERSHIP_LEVELS:
                continue
            series = _load_row_year_series(sheets, sid, tab, year, row_num)
            for month_num, val in series.items():
                month = pd.Timestamp(year=year, month=int(month_num), day=1)
                if month < since_month or month > sheet_until:
                    continue
                records.append(
                    {
                        "month": month,
                        "membership_level": tier,
                        "signups": float(val),
                    }
                )
        notes.append(
            f"Signups by tier (sheet): {TRACKERS[year]} / {tab} — Both Locations rows."
        )

    if not records:
        return pd.DataFrame(columns=_SIGNUP_COLUMNS), notes
    return pd.DataFrame(records), notes


def ghl_signups_by_level_monthly_from_loader(
    ghl_signups_by_level_df: pd.DataFrame,
    *,
    since: str,
    until: str,
) -> pd.DataFrame:
    """Org-wide GHL signups by month and tier (direct per-contact count)."""
    since_month, until_month = _month_bounds(since, until)
    ghl_since = max(
        since_month,
        pd.Timestamp(GHL_SIGNUPS_SINCE).to_period("M").to_timestamp(),
    )
    if until_month < ghl_since or ghl_signups_by_level_df.empty:
        return pd.DataFrame(columns=_SIGNUP_COLUMNS)

    work = ghl_signups_by_level_df.copy()
    work["month"] = pd.to_datetime(work["month"])
    work = work[
        (work["month"] >= ghl_since)
        & (work["month"] <= until_month)
        & (work["membership_level"].isin(MEMBERSHIP_LEVELS))
    ]
    if work.empty:
        return pd.DataFrame(columns=_SIGNUP_COLUMNS)
    return (
        work.groupby(["month", "membership_level"], as_index=False)["signups"]
        .sum()
        .sort_values(["month", "membership_level"])
        .reset_index(drop=True)
    )


def load_signups_by_level_monthly(
    since: str,
    until: str,
    *,
    ghl_signups_by_level_df: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:
    """Sheet + GHL monthly signups by membership tier."""
    sheet_df, notes = load_tracker_signups_by_level_monthly(since, until)
    ghl_df = ghl_signups_by_level_monthly_from_loader(
        ghl_signups_by_level_df,
        since=since,
        until=until,
    )
    if sheet_df.empty and ghl_df.empty:
        return pd.DataFrame(columns=_SIGNUP_COLUMNS), notes
    if ghl_df.empty:
        return sheet_df, notes
    if sheet_df.empty:
        notes.append(
            f"Signups by tier (GHL): Sign Up Date from {GHL_SIGNUPS_SINCE} onward."
        )
        return ghl_df, notes

    combined = pd.concat([sheet_df, ghl_df], ignore_index=True)
    combined = (
        combined.groupby(["month", "membership_level"], as_index=False)["signups"]
        .sum()
        .sort_values(["month", "membership_level"])
        .reset_index(drop=True)
    )
    notes.append(
        f"Signups by tier (GHL): Sign Up Date from {GHL_SIGNUPS_SINCE} onward."
    )
    return combined, notes


def aggregate_signups_yoy(
    monthly: pd.DataFrame,
    *,
    since: date,
    until: date,
    levels: tuple[str, ...] = MEMBERSHIP_LEVELS,
) -> pd.DataFrame:
    """Sum signups by calendar year and membership level within the selected range."""
    if monthly.empty:
        return pd.DataFrame(columns=["year", "year_label", "membership_level", "signups"])

    since_month, until_month = _month_bounds(since.isoformat(), until.isoformat())
    work = monthly.copy()
    work["month"] = pd.to_datetime(work["month"])
    work = work[
        (work["month"] >= since_month)
        & (work["month"] <= until_month)
        & (work["membership_level"].isin(levels))
    ]
    if work.empty:
        return pd.DataFrame(columns=["year", "year_label", "membership_level", "signups"])

    work["year"] = work["month"].dt.year
    out = (
        work.groupby(["year", "membership_level"], as_index=False)["signups"]
        .sum()
        .sort_values(["year", "membership_level"])
    )
    out["year_label"] = out["year"].apply(
        lambda y: _year_chart_label(int(y), until=until)
    )
    return out


def aggregate_signups_qoq(
    monthly: pd.DataFrame,
    *,
    since: date,
    until: date,
    levels: tuple[str, ...] = MEMBERSHIP_LEVELS,
) -> pd.DataFrame:
    """
    Sum signups by calendar quarter and year (same quarter compared across years).

    ``quarter_label`` is Q1–Q4; ``year_label`` identifies the year (YTD when partial).
    """
    if monthly.empty:
        return pd.DataFrame(
            columns=["year", "year_label", "quarter", "quarter_label", "membership_level", "signups"]
        )

    since_month, until_month = _month_bounds(since.isoformat(), until.isoformat())
    work = monthly.copy()
    work["month"] = pd.to_datetime(work["month"])
    work = work[
        (work["month"] >= since_month)
        & (work["month"] <= until_month)
        & (work["membership_level"].isin(levels))
    ]
    if work.empty:
        return pd.DataFrame(
            columns=["year", "year_label", "quarter", "quarter_label", "membership_level", "signups"]
        )

    work["year"] = work["month"].dt.year
    work["quarter"] = work["month"].dt.quarter
    work["quarter_label"] = "Q" + work["quarter"].astype(str)
    out = (
        work.groupby(
            ["year", "quarter", "quarter_label", "membership_level"],
            as_index=False,
        )["signups"]
        .sum()
        .sort_values(["quarter", "year", "membership_level"])
    )
    out["year_label"] = out["year"].apply(
        lambda y: _year_chart_label(int(y), until=until)
    )
    return out


__all__ = [
    "SIGNUP_COMPARISON_REVISION",
    "aggregate_signups_qoq",
    "aggregate_signups_yoy",
    "ghl_signups_by_level_monthly_from_loader",
    "load_signups_by_level_monthly",
    "load_tracker_signups_by_level_monthly",
]
