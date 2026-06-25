"""
Monthly funnel totals for the Leads / DCs / Signups over-time chart.

Sources (no campaign attribution, no spend weighting):
- Through Aug 30, 2025:
  - **Leads** — HubSpot migration export (deduped new contacts by create date).
    Aug 2025 uses GHL new contacts (dateAdded), same method as Sep 2025 onward.
  - **Discovery calls** — Digital Cross-Channel Tracker **Calls completed** row.
  - **Signups** — Tracker **GRAND TOTAL New Members** (same as Signups Over Time).
- From Sep 1, 2025 (after Aug 30): GoHighLevel — new contacts (dateAdded),
  discovery-call meetings (calendar startTime), committed signups (Sign Up Date).
- **Terminations** — Terminated Memberships 2023-2025 **Consolidated Data** tab
  (Date of Termination), all months in range.
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

_DC_LIVE_DASH = Path(__file__).resolve().parent
if str(_DC_LIVE_DASH) not in sys.path:
    sys.path.insert(0, str(_DC_LIVE_DASH))

from hubspot_migration_data import HUBSPOT_DATA_UNTIL, load_hubspot_leads_monthly
from ghl_client import (
    contact_custom_field_value,
    discovery_call_calendar_ids,
    fetch_discovery_call_meetings_monthly_by_channel,
    fetch_signup_date_range_committed_yes_contacts,
)

# Bump when loader logic changes (Streamlit cache key).
FUNNEL_OVER_TIME_REVISION = "2026-06-25-consolidated-terminations-v1"

_TERMINATIONS_SPREADSHEET_ID = "18fDtd3xEHHXC6sCeRFFadSwcshk4SJqFUG6aV006DhU"
_TERMINATIONS_SHEET = "Consolidated Data"

_FUNNEL_COLUMNS = ("month", "leads", "dcs", "terminations", "signups", "source")


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


def load_sheet_funnel_monthly(
    since: str,
    until: str,
    *,
    ghl_leads_by_month: dict[pd.Timestamp, float] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Pre-GHL org-wide funnel through Aug 30, 2025.

    HubSpot leads, tracker completed calls and signups (signups match Signups Over Time).
    """
    from digital_channel_live_data import (
        SHEETS_DCS_UNTIL,
        SHEETS_SIGNUPS_UNTIL,
        _fetch_tracker_calls_completed,
        _fetch_tracker_grand_total_signups,
    )

    notes: list[str] = []
    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()
    sheet_until = min(
        until_month,
        pd.Timestamp(SHEETS_SIGNUPS_UNTIL).to_period("M").to_timestamp(),
    )
    if since_month > sheet_until:
        return _empty_funnel(), notes

    range_until = (sheet_until + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")

    hubspot_leads, lead_notes = load_hubspot_leads_monthly(
        since_month.strftime("%Y-%m-%d"),
        range_until,
    )
    notes.extend(lead_notes)

    tracker_signups, _, tracker_signup_notes = _fetch_tracker_grand_total_signups(
        since_month.strftime("%Y-%m-%d"),
        range_until,
    )
    notes.extend(tracker_signup_notes)

    tracker_dcs, _, tracker_dc_notes = _fetch_tracker_calls_completed(
        since_month.strftime("%Y-%m-%d"),
        range_until,
    )
    notes.extend(tracker_dc_notes)

    ghl_leads_aug: dict[pd.Timestamp, float] = {}
    hubspot_until = pd.Timestamp(HUBSPOT_DATA_UNTIL).to_period("M").to_timestamp()
    if sheet_until > hubspot_until:
        bridge_since = (hubspot_until + pd.offsets.MonthBegin(1)).strftime("%Y-%m-%d")
        if ghl_leads_by_month is not None:
            bridge_start = pd.Timestamp(bridge_since).to_period("M").to_timestamp()
            bridge_end = pd.Timestamp(range_until).to_period("M").to_timestamp()
            ghl_leads_aug = {
                month: float(total)
                for month, total in ghl_leads_by_month.items()
                if bridge_start <= month <= bridge_end
            }
            if ghl_leads_aug:
                notes.append(
                    "Funnel Aug 2025 leads: reused GHL new contacts by dateAdded "
                    f"(HubSpot export ends {hubspot_until.strftime('%b %Y')})."
                )
        else:
            ghl_leads_raw, ghl_lead_notes = _ghl_leads_monthly(bridge_since, range_until)
            ghl_leads_aug = {pd.Timestamp(m): float(v) for m, v in ghl_leads_raw.items()}
            if ghl_leads_aug:
                notes.extend(ghl_lead_notes)
                notes.append(
                    "Funnel Aug 2025 leads: GHL new contacts by dateAdded (HubSpot export "
                    f"ends {hubspot_until.strftime('%b %Y')})."
                )

    sheet_signup_cutoff = pd.Timestamp(SHEETS_SIGNUPS_UNTIL).to_period("M").to_timestamp()
    sheet_dcs_cutoff = pd.Timestamp(SHEETS_DCS_UNTIL).to_period("M").to_timestamp()

    rows: list[dict[str, Any]] = []
    for month in _month_range(since_month.strftime("%Y-%m-%d"), sheet_until.strftime("%Y-%m-%d")):
        if month > hubspot_until:
            leads = float(ghl_leads_aug.get(month, 0.0))
        else:
            leads = float(hubspot_leads.get(month, 0.0))

        if month <= sheet_dcs_cutoff:
            dcs = float(tracker_dcs.get(month, 0.0))
        else:
            dcs = 0.0

        if month <= sheet_signup_cutoff:
            signups = float(tracker_signups.get(month, 0.0))
        else:
            signups = 0.0

        rows.append(
            {
                "month": month,
                "leads": leads,
                "dcs": dcs,
                "terminations": 0.0,
                "signups": signups,
                "source": "hubspot+tracker",
            }
        )

    if not rows:
        return _empty_funnel(), notes

    monthly = pd.DataFrame(rows).sort_values("month").reset_index(drop=True)
    notes.append(
        "Funnel (through Aug 30, 2025): HubSpot leads (through Jul); GHL dateAdded "
        "leads for Aug 2025; tracker GRAND TOTAL signups (aligned with Signups Over "
        "Time) and Calls completed for DCs."
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


def load_ghl_funnel_monthly(
    since: str,
    until: str,
    *,
    ghl_leads_by_month: dict[pd.Timestamp, float] | None = None,
    ghl_dcs_by_month: dict[pd.Timestamp, float] | None = None,
    ghl_signups_by_month: dict[pd.Timestamp, float] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Monthly funnel from GHL (from Sep 1, 2025 — after Aug 30)."""
    from digital_channel_live_data import GHL_SIGNUPS_SINCE

    ghl_since = max(since, GHL_SIGNUPS_SINCE)
    if pd.Timestamp(until) < pd.Timestamp(ghl_since):
        return _empty_funnel(), []

    ghl_since_month = pd.Timestamp(ghl_since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()

    notes: list[str] = []
    if ghl_leads_by_month is not None:
        leads_by_month = {
            month: int(total)
            for month, total in ghl_leads_by_month.items()
            if ghl_since_month <= month <= until_month
        }
        if leads_by_month:
            notes.append(
                f"GHL leads: {sum(leads_by_month.values()):,} new contacts "
                f"({ghl_since} → {until}), by dateAdded (shared loader)."
            )
    else:
        leads_by_month, lead_notes = _ghl_leads_monthly(ghl_since, until)
        notes.extend(lead_notes)

    if ghl_dcs_by_month is not None:
        dcs_by_month = {
            month: int(total)
            for month, total in ghl_dcs_by_month.items()
            if ghl_since_month <= month <= until_month
        }
        if dcs_by_month:
            notes.append(
                f"GHL DCs: {sum(dcs_by_month.values()):,} discovery-call meeting(s) "
                f"({ghl_since} → {until}, shared loader)."
            )
    else:
        dcs_by_month, dc_notes = _ghl_dcs_monthly(ghl_since, until)
        notes.extend(dc_notes)

    if ghl_signups_by_month is not None:
        signups_by_month = {
            month: int(total)
            for month, total in ghl_signups_by_month.items()
            if ghl_since_month <= month <= until_month
        }
        if signups_by_month:
            notes.append(
                f"GHL signups: {sum(signups_by_month.values()):,} committed "
                f"(Sign Up Date {ghl_since} → {until}, shared loader)."
            )
    else:
        signups_by_month, signup_notes = _ghl_signups_monthly(ghl_since, until)
        notes.extend(signup_notes)

    rows: list[dict[str, Any]] = []
    for month in _month_range(ghl_since, until):
        rows.append(
            {
                "month": month,
                "leads": float(leads_by_month.get(month, 0)),
                "dcs": float(dcs_by_month.get(month, 0)),
                "terminations": 0.0,
                "signups": float(signups_by_month.get(month, 0)),
                "source": "ghl",
            }
        )
    return pd.DataFrame(rows), notes


def load_consolidated_terminations_monthly(
    since: str,
    until: str,
) -> tuple[dict[pd.Timestamp, int], list[str]]:
    """
    Monthly termination counts from **Consolidated Data** (Date of Termination).
    """
    from acquisition_retention_data import load_consolidated_by_name

    since_day = pd.Timestamp(since).date()
    until_day = pd.Timestamp(until).date()
    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()

    try:
        consolidated = load_consolidated_by_name()
    except Exception as exc:
        return {}, [f"Terminations skipped: {exc}"]

    by_month: dict[pd.Timestamp, int] = {}
    for row in consolidated.values():
        term_day = row.get("termination_date")
        if term_day is None:
            continue
        if term_day < since_day or term_day > until_day:
            continue
        month = pd.Timestamp(term_day).to_period("M").to_timestamp()
        if month < since_month or month > until_month:
            continue
        by_month[month] = by_month.get(month, 0) + 1

    total = sum(by_month.values())
    notes = [
        f"Terminations: {total:,} from **Terminated Memberships 2023-2025** "
        f"**{_TERMINATIONS_SHEET}** tab (Date of Termination, {since} → {until})."
    ]
    return by_month, notes


def load_funnel_over_time(
    since: str,
    until: str,
    *,
    ghl_leads_by_month: dict[pd.Timestamp, float] | None = None,
    ghl_dcs_by_month: dict[pd.Timestamp, float] | None = None,
    ghl_signups_by_month: dict[pd.Timestamp, float] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Stitch pre-GHL months (through Aug 30, 2025) with GHL months (Sep 2025 onward).

    Returns one row per calendar month in range with columns:
    month, leads, dcs, terminations, signups, source ('hubspot+tracker' | 'ghl').

    When ``ghl_*_by_month`` dicts are supplied (from :func:`load_live_campaign_data`),
    GHL API calls are skipped for the funnel chart.
    """
    notes: list[str] = []
    sheet_df, sheet_notes = load_sheet_funnel_monthly(
        since,
        until,
        ghl_leads_by_month=ghl_leads_by_month,
    )
    notes.extend(sheet_notes)
    ghl_df, ghl_notes = load_ghl_funnel_monthly(
        since,
        until,
        ghl_leads_by_month=ghl_leads_by_month,
        ghl_dcs_by_month=ghl_dcs_by_month,
        ghl_signups_by_month=ghl_signups_by_month,
    )
    notes.extend(ghl_notes)

    term_by_month, term_notes = load_consolidated_terminations_monthly(since, until)
    notes.extend(term_notes)

    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()

    if sheet_df.empty and ghl_df.empty and not term_by_month:
        return _empty_funnel(), notes

    combined = pd.concat([sheet_df, ghl_df], ignore_index=True)
    combined = combined.sort_values("month").drop_duplicates(subset=["month"], keep="last")
    combined["month"] = pd.to_datetime(combined["month"])

    by_month: dict[pd.Timestamp, dict[str, Any]] = {}
    for _, row in combined.iterrows():
        month = pd.Timestamp(row["month"]).to_period("M").to_timestamp()
        by_month[month] = row.to_dict()

    rows: list[dict[str, Any]] = []
    for month in _month_range(since, until):
        existing = by_month.get(month, {})
        rows.append(
            {
                "month": month,
                "leads": float(existing.get("leads", 0.0)),
                "dcs": float(existing.get("dcs", 0.0)),
                "terminations": float(term_by_month.get(month, 0)),
                "signups": float(existing.get("signups", 0.0)),
                "source": existing.get("source", ""),
            }
        )

    out = pd.DataFrame(rows)
    out = out[
        (out["month"] >= since_month) & (out["month"] <= until_month)
    ].copy()

    for col in ("leads", "dcs", "terminations", "signups"):
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)

    return out[list(_FUNNEL_COLUMNS)], notes


__all__ = [
    "FUNNEL_OVER_TIME_REVISION",
    "GHL_FUNNEL_SINCE",
    "load_consolidated_terminations_monthly",
    "load_funnel_over_time",
    "load_sheet_funnel_monthly",
    "load_ghl_funnel_monthly",
]


def _ghl_funnel_since() -> str:
    from digital_channel_live_data import GHL_SIGNUPS_SINCE

    return GHL_SIGNUPS_SINCE


# Same cutover as Signups Over Time / DCs charts (GHL after Aug 30, 2025).
GHL_FUNNEL_SINCE = _ghl_funnel_since()
