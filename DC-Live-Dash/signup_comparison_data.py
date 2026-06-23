"""
Org-wide signups by membership level for YoY / QoQ comparison charts.

- Through Aug 2025: Digital Cross-Channel Tracker **Both Locations** tier rows.
- From Sep 2025: GoHighLevel committed signups by Sign Up Date + Membership Level.
"""

from __future__ import annotations

import calendar
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from april_new_members_tier_yoy import BOTH_LOCATIONS_ROWS, TRACKERS
from digital_channel_live_data import (
    DEFAULT_SINCE,
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

SIGNUP_COMPARISON_REVISION = "2026-06-23-meta-tracker-ghl-backfill-v1"

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


def _quarter_chart_label(year: int, quarter: int, *, until: date) -> str:
    """Legend label for same-quarter comparison; QTD when that quarter is still open."""
    end_month = quarter * 3
    end_day = calendar.monthrange(year, end_month)[1]
    quarter_end = date(year, end_month, end_day)
    if until < quarter_end:
        return f"{year} QTD"
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


def tier_signup_since() -> date:
    return pd.Timestamp(DEFAULT_SINCE).date()


def tier_signup_until() -> date:
    return date.today() - timedelta(days=1)


def tier_year_filter_options(monthly: pd.DataFrame) -> tuple[str, ...]:
    """Calendar years available for the YoY tier chart multiselect."""
    if monthly.empty:
        return ()
    years = sorted({int(y) for y in pd.to_datetime(monthly["month"]).dt.year.unique()})
    return tuple(str(y) for y in years)


def tier_quarter_filter_options() -> tuple[str, ...]:
    """Quarter labels for the QoQ tier chart multiselect."""
    return ("Q1", "Q2", "Q3", "Q4")


def load_tier_signups_by_level_monthly() -> tuple[pd.DataFrame, list[str]]:
    """
    Sheet + GHL signups for tier YoY / QoQ charts.

    Always loads full history through yesterday — independent of the dashboard
    performance date range.
    """
    since = tier_signup_since()
    until = tier_signup_until()
    since_s = since.isoformat()
    until_s = until.isoformat()

    sheet_df, notes = load_tracker_signups_by_level_monthly(since_s, until_s)
    notes.insert(
        0,
        f"Signups by tier: {since.year} through {until:%b %d, %Y} "
        "(independent of the performance date range).",
    )

    ghl_df = pd.DataFrame(columns=_SIGNUP_COLUMNS)
    if until >= pd.Timestamp(GHL_SIGNUPS_SINCE).date():
        from digital_channel_live_data import (
            _fetch_signup_contacts_cached,
            build_ghl_signups_by_level_monthly,
        )

        ghl_fetch_since = max(
            since, pd.Timestamp(GHL_SIGNUPS_SINCE).date()
        ).isoformat()
        signup = _fetch_signup_contacts_cached(ghl_fetch_since, until_s)
        ghl_df = build_ghl_signups_by_level_monthly(signup, since_s, until_s)

        notes.append(
            f"Signups by tier (GHL): Sign Up Date from {GHL_SIGNUPS_SINCE} through "
            f"{until:%b %Y}."
        )

    if sheet_df.empty and ghl_df.empty:
        return pd.DataFrame(columns=_SIGNUP_COLUMNS), notes
    if ghl_df.empty:
        return sheet_df, notes
    if sheet_df.empty:
        return ghl_df, notes

    combined = pd.concat([sheet_df, ghl_df], ignore_index=True)
    combined = (
        combined.groupby(["month", "membership_level"], as_index=False)["signups"]
        .sum()
        .sort_values(["month", "membership_level"])
        .reset_index(drop=True)
    )
    return combined, notes


def qoq_quarter_numbers(labels: tuple[str, ...] | list[str]) -> tuple[int, ...]:
    """Map multiselect labels (``Q1``–``Q4``) to calendar quarter numbers."""
    return tuple(int(label.removeprefix("Q")) for label in labels)


def aggregate_signups_qoq(
    monthly: pd.DataFrame,
    *,
    until: date,
    levels: tuple[str, ...] = MEMBERSHIP_LEVELS,
    selected_quarters: tuple[int, ...] | None = None,
) -> pd.DataFrame:
    """
    Sum signups by calendar quarter and year (same quarter compared across years).

    ``quarter_label`` is Q1–Q4; ``year_label`` identifies the year (QTD when partial).

    Tier charts use full history through ``until``; sidebar start date is not applied.
    When ``selected_quarters`` is set, only those calendar quarters are included.
    """
    if monthly.empty:
        return pd.DataFrame(
            columns=["year", "year_label", "quarter", "quarter_label", "membership_level", "signups"]
        )

    until_month = _month_bounds(until.isoformat(), until.isoformat())[1]
    work = monthly.copy()
    work["month"] = pd.to_datetime(work["month"])
    work = work[
        (work["month"] <= until_month)
        & (work["membership_level"].isin(levels))
    ]
    if selected_quarters:
        quarters = {int(q) for q in selected_quarters}
        work = work[work["month"].dt.quarter.isin(quarters)]
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
    out["year_label"] = out.apply(
        lambda row: _quarter_chart_label(
            int(row["year"]), int(row["quarter"]), until=until
        ),
        axis=1,
    )
    return out


def aggregate_signups_yoy(
    monthly: pd.DataFrame,
    *,
    until: date,
    levels: tuple[str, ...] = MEMBERSHIP_LEVELS,
    selected_years: tuple[int, ...] | None = None,
) -> pd.DataFrame:
    """
    Sum signups by calendar year and membership level through ``until``.

    Tier charts pass full monthly history; sidebar start date is not applied.
    When ``selected_years`` is set, only those calendar years are included.
    The current year may show as YTD when still open.
    """
    if monthly.empty:
        return pd.DataFrame(columns=["year", "year_label", "membership_level", "signups"])

    until_month = _month_bounds(until.isoformat(), until.isoformat())[1]
    work = monthly.copy()
    work["month"] = pd.to_datetime(work["month"])
    work = work[
        (work["month"] <= until_month)
        & (work["membership_level"].isin(levels))
    ]
    if selected_years:
        years = {int(y) for y in selected_years}
        work = work[work["month"].dt.year.isin(years)]
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


__all__ = [
    "SIGNUP_COMPARISON_REVISION",
    "aggregate_signups_qoq",
    "aggregate_signups_yoy",
    "ghl_signups_by_level_monthly_from_loader",
    "load_signups_by_level_monthly",
    "load_tier_signups_by_level_monthly",
    "load_tracker_signups_by_level_monthly",
    "qoq_quarter_numbers",
    "tier_quarter_filter_options",
    "tier_signup_until",
    "tier_year_filter_options",
]
