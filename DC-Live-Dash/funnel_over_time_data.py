"""
Monthly funnel totals for the Leads / DCs / Signups over-time chart.

Sources (no campaign attribution, no spend weighting):
- Before Sep 2025: **Digital Channel Dashboard 2024-25** Data tab (all channels summed).
- From Sep 2025: GoHighLevel — new contacts (dateAdded), discovery-call meetings
  (configured calendar IDs, startTime), committed signups (Sign Up Date).
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from digital_channel_sheets import SPREADSHEET_NAME, load_campaign_data
from ghl_client import (
    contact_custom_field_value,
    discovery_call_calendar_ids,
    fetch_discovery_call_meetings_monthly_by_channel,
    fetch_signup_date_range_committed_yes_contacts,
)

# Bump when loader logic changes (Streamlit cache key).
FUNNEL_OVER_TIME_REVISION = "2026-06-19-funnel-v1"

GHL_FUNNEL_SINCE = "2025-09-01"
_FUNNEL_COLUMNS = ("month", "leads", "dcs", "signups", "source")


def _month_range(since: str, until: str) -> list[pd.Timestamp]:
    return [
        p.to_timestamp()
        for p in pd.period_range(
            start=pd.Timestamp(since).to_period("M"),
            end=pd.Timestamp(until).to_period("M"),
            freq="M",
        )
    ]


def _empty_funnel() -> pd.DataFrame:
    return pd.DataFrame(columns=list(_FUNNEL_COLUMNS))


def load_sheet_funnel_monthly(since: str, until: str) -> tuple[pd.DataFrame, list[str]]:
    """Channel-agnostic monthly leads, DCs, and signups from the Data tab."""
    notes: list[str] = []
    try:
        sheet_df = load_campaign_data()
    except Exception as exc:
        return _empty_funnel(), [f"Sheet funnel skipped: {exc}"]

    if sheet_df.empty:
        return _empty_funnel(), notes

    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()
    ghl_cutoff = pd.Timestamp(GHL_FUNNEL_SINCE).to_period("M").to_timestamp()
    sheet_until = min(until_month, ghl_cutoff - pd.offsets.MonthBegin(1))

    subset = sheet_df[
        (sheet_df["month"] >= since_month) & (sheet_df["month"] <= sheet_until)
    ]
    if subset.empty:
        return _empty_funnel(), notes

    monthly = (
        subset.groupby("month", as_index=False)
        .agg(
            leads=("leads", "sum"),
            dcs=("dcs", "sum"),
            signups=("conversions", "sum"),
        )
        .sort_values("month")
    )
    monthly["source"] = "sheet"
    notes.append(
        f"Funnel (sheet): {SPREADSHEET_NAME} Data tab through "
        f"{sheet_until.strftime('%b %Y')} — all channels summed."
    )
    return monthly, notes


def _ghl_leads_monthly(since: str, until: str) -> tuple[dict[pd.Timestamp, int], list[str]]:
    """Total new GHL contacts by calendar month of dateAdded."""
    from digital_channel_live_data import _fetch_ghl_leads_by_date_added

    notes: list[str] = []
    try:
        payload = _fetch_ghl_leads_by_date_added(since, until)
    except Exception as exc:
        return {}, [f"GHL leads skipped: {exc}"]

    if payload.get("truncated_pages"):
        notes.append(
            "GHL leads: pagination cap on at least one day — counts may be low."
        )

    by_month: dict[pd.Timestamp, int] = {}
    for row in payload.get("monthly") or []:
        ms = (row.get("month_start") or "")[:10]
        if not ms:
            continue
        month = pd.Timestamp(ms).to_period("M").to_timestamp()
        by_month[month] = int(row.get("total_new_contacts") or 0)

    notes.append(
        f"GHL leads: {int(payload.get('total_new_contacts') or 0):,} new contacts "
        f"({since} → {until}), by dateAdded."
    )
    return by_month, notes


def _ghl_dcs_monthly(since: str, until: str) -> tuple[dict[pd.Timestamp, int], list[str]]:
    """Discovery-call meetings (startTime) on configured GHL calendar IDs."""
    notes: list[str] = []
    cal_ids = discovery_call_calendar_ids()
    try:
        dc = fetch_discovery_call_meetings_monthly_by_channel(since, until)
    except Exception as exc:
        return {}, [f"GHL discovery calls skipped: {exc}"]

    if dc.get("calendar_api_errors"):
        notes.append("GHL discovery calls: at least one calendar API error.")
    if dc.get("missing_contact_link"):
        notes.append(
            f"GHL discovery calls: {dc['missing_contact_link']} meeting(s) had no "
            "linked contact (still counted)."
        )

    by_month: dict[pd.Timestamp, int] = {}
    for row in dc.get("monthly") or []:
        ms = (row.get("month_start") or "")[:10]
        if not ms:
            continue
        month = pd.Timestamp(ms).to_period("M").to_timestamp()
        total = int(row.get("google") or 0) + int(row.get("meta") or 0) + int(
            row.get("unallocated") or 0
        )
        by_month[month] = total

    notes.append(
        f"GHL DCs: {int(dc.get('meetings_total') or 0):,} discovery-call meeting(s) "
        f"on {len(cal_ids)} calendar(s), by startTime ({since} → {until})."
    )
    return by_month, notes


def _ghl_signups_monthly(since: str, until: str) -> tuple[dict[pd.Timestamp, int], list[str]]:
    """Committed? = Yes contacts grouped by Sign Up Date month."""
    notes: list[str] = []
    try:
        signup = fetch_signup_date_range_committed_yes_contacts(since, until)
    except Exception as exc:
        return {}, [f"GHL signups skipped: {exc}"]

    if signup.get("truncated_pages"):
        notes.append("GHL signups: pagination cap — counts may be low.")

    sid = signup.get("sign_up_date_field_id") or ""
    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()
    by_month: dict[pd.Timestamp, int] = {}

    for contact in signup.get("contacts") or []:
        raw = contact_custom_field_value(contact, sid).strip()
        if not raw:
            continue
        try:
            signup_day = pd.to_datetime(raw[:10])
        except (ValueError, TypeError):
            continue
        month = signup_day.to_period("M").to_timestamp()
        if month < since_month or month > until_month:
            continue
        by_month[month] = by_month.get(month, 0) + 1

    total = sum(by_month.values())
    notes.append(
        f"GHL signups: {total:,} committed (Sign Up Date {since} → {until})."
    )
    return by_month, notes


def load_ghl_funnel_monthly(since: str, until: str) -> tuple[pd.DataFrame, list[str]]:
    """Monthly funnel from GHL (Sep 2025+ in production use)."""
    ghl_since = max(since, GHL_FUNNEL_SINCE)
    if pd.Timestamp(until) < pd.Timestamp(ghl_since):
        return _empty_funnel(), []

    notes: list[str] = []
    leads_by_month, lead_notes = _ghl_leads_monthly(ghl_since, until)
    notes.extend(lead_notes)
    dcs_by_month, dc_notes = _ghl_dcs_monthly(ghl_since, until)
    notes.extend(dc_notes)
    signups_by_month, signup_notes = _ghl_signups_monthly(ghl_since, until)
    notes.extend(signup_notes)

    rows: list[dict[str, Any]] = []
    for month in _month_range(ghl_since, until):
        rows.append(
            {
                "month": month,
                "leads": float(leads_by_month.get(month, 0)),
                "dcs": float(dcs_by_month.get(month, 0)),
                "signups": float(signups_by_month.get(month, 0)),
                "source": "ghl",
            }
        )
    return pd.DataFrame(rows), notes


def load_funnel_over_time(since: str, until: str) -> tuple[pd.DataFrame, list[str]]:
    """
    Stitch sheet months (before Sep 2025) with GHL months (Sep 2025 onward).

    Returns one row per calendar month in range with columns:
    month, leads, dcs, signups, source ('sheet' | 'ghl').
    """
    notes: list[str] = []
    sheet_df, sheet_notes = load_sheet_funnel_monthly(since, until)
    notes.extend(sheet_notes)
    ghl_df, ghl_notes = load_ghl_funnel_monthly(since, until)
    notes.extend(ghl_notes)

    if sheet_df.empty and ghl_df.empty:
        return _empty_funnel(), notes

    combined = pd.concat([sheet_df, ghl_df], ignore_index=True)
    combined = combined.sort_values("month").drop_duplicates(subset=["month"], keep="last")

    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()
    combined = combined[
        (combined["month"] >= since_month) & (combined["month"] <= until_month)
    ].copy()

    for col in ("leads", "dcs", "signups"):
        combined[col] = pd.to_numeric(combined[col], errors="coerce").fillna(0.0)

    return combined[list(_FUNNEL_COLUMNS)], notes


__all__ = [
    "FUNNEL_OVER_TIME_REVISION",
    "GHL_FUNNEL_SINCE",
    "load_funnel_over_time",
    "load_sheet_funnel_monthly",
    "load_ghl_funnel_monthly",
]
