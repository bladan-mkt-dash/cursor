"""
HubSpot CRM migration exports (Google Sheets) for org-wide funnel metrics.

Spreadsheet: all-contacts migration workbook (HubSpot export at GHL migration).
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from googleapiclient.discovery import build

from total_new_members_yoy_chart import _credentials

HUBSPOT_ALL_CONTACTS_ID = "18YwAZoROBfA88KPT_M6m2jA6v9xDS7aHOzOXJTyO2LM"

# Migration snapshot ends ~2025-07-25; Aug 2025 uses tracker / Data-tab fallbacks.
HUBSPOT_DATA_UNTIL = "2025-07-31"

_LEAD_TABS: tuple[tuple[str, str], ...] = (
    ("Import All Contacts", "HS Created Date"),
    ("Maybes and Nos with Notes", "HS Created Date"),
    ("All Sign Ups with Notes", "HS Create Date"),
)
_SIGNUPS_TAB = "All Sign Ups with Notes"
_SIGNUPS_DATE_COL = "HS Create Date"

_tab_cache: dict[str, pd.DataFrame] = {}


def _load_tab(tab: str) -> pd.DataFrame:
    if tab in _tab_cache:
        return _tab_cache[tab]

    from total_new_members_yoy_chart import _credentials, _execute_with_retry

    sheets = build("sheets", "v4", credentials=_credentials())
    rows = _execute_with_retry(
        lambda: sheets.spreadsheets()
        .values()
        .get(spreadsheetId=HUBSPOT_ALL_CONTACTS_ID, range=f"'{tab}'!A1:ZZ")
        .execute()
    ).get("values", [])
    if not rows:
        df = pd.DataFrame()
    else:
        width = max(len(r) for r in rows)
        padded = [r + [""] * (width - len(r)) for r in rows]
        headers = [str(h).strip() for h in padded[0]]
        df = pd.DataFrame(padded[1:], columns=headers)

    _tab_cache[tab] = df
    return df


def _normalize_email(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.casefold()


def _deduped_hubspot_contacts() -> pd.DataFrame:
    """Unique contacts across migration tabs, keeping earliest create timestamp."""
    frames: list[pd.DataFrame] = []
    for tab, date_col in _LEAD_TABS:
        df = _load_tab(tab)
        if df.empty or "Email" not in df.columns or date_col not in df.columns:
            continue
        sub = df[["Email", date_col]].copy()
        sub.columns = ["email", "created"]
        sub["email"] = _normalize_email(sub["email"])
        sub = sub.loc[sub["email"].ne("") & sub["email"].ne("nan")]
        sub["created"] = pd.to_datetime(sub["created"], errors="coerce")
        sub = sub.dropna(subset=["created"])
        frames.append(sub)

    if not frames:
        return pd.DataFrame(columns=["email", "created"])

    combined = pd.concat(frames, ignore_index=True)
    return (
        combined.sort_values("created")
        .drop_duplicates(subset=["email"], keep="first")
        .reset_index(drop=True)
    )


def _hubspot_signups_frame() -> pd.DataFrame:
    df = _load_tab(_SIGNUPS_TAB)
    if df.empty or _SIGNUPS_DATE_COL not in df.columns:
        return pd.DataFrame(columns=["signup_date"])

    out = df[[_SIGNUPS_DATE_COL]].copy()
    out.columns = ["signup_date"]
    out["signup_date"] = pd.to_datetime(out["signup_date"], errors="coerce")
    return out.dropna(subset=["signup_date"])


def _counts_by_month(
    dates: pd.Series,
    *,
    since: str,
    until: str,
) -> dict[pd.Timestamp, int]:
    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()
    hubspot_until = pd.Timestamp(HUBSPOT_DATA_UNTIL).to_period("M").to_timestamp()
    effective_until = min(until_month, hubspot_until)

    parsed = pd.to_datetime(dates, errors="coerce").dropna()
    if parsed.empty:
        return {}

    months = parsed.dt.to_period("M").dt.to_timestamp()
    mask = (months >= since_month) & (months <= effective_until)
    if not mask.any():
        return {}

    counts = months.loc[mask].value_counts()
    return {pd.Timestamp(m): int(c) for m, c in counts.items()}


def load_hubspot_leads_monthly(
    since: str,
    until: str,
) -> tuple[dict[pd.Timestamp, float], list[str]]:
    """
    Org-wide new contacts by HubSpot create date (deduped across migration tabs).
    """
    contacts = _deduped_hubspot_contacts()
    notes: list[str] = []
    if contacts.empty:
        return {}, ["HubSpot leads: no contact rows loaded from migration export."]

    by_month = _counts_by_month(contacts["created"], since=since, until=until)
    total = sum(by_month.values())
    notes.append(
        f"HubSpot leads: {total:,} unique new contacts by create date "
        f"({since} → {min(until, HUBSPOT_DATA_UNTIL)}), deduped across "
        f"{len(_LEAD_TABS)} migration tabs."
    )
    if pd.Timestamp(until) > pd.Timestamp(HUBSPOT_DATA_UNTIL):
        notes.append(
            f"HubSpot leads export ends {HUBSPOT_DATA_UNTIL}; later months use fallbacks."
        )
    return {m: float(v) for m, v in by_month.items()}, notes


def load_hubspot_signups_monthly(
    since: str,
    until: str,
) -> tuple[dict[pd.Timestamp, float], list[str]]:
    """
    Org-wide signups from **All Sign Ups with Notes** (HS Create Date).

    Each row is a converted customer with membership level assigned.
    """
    signups = _hubspot_signups_frame()
    notes: list[str] = []
    if signups.empty:
        return {}, ["HubSpot signups: no rows in All Sign Ups with Notes."]

    by_month = _counts_by_month(signups["signup_date"], since=since, until=until)
    total = sum(by_month.values())
    notes.append(
        f"HubSpot signups: {total:,} from All Sign Ups with Notes by HS Create Date "
        f"({since} → {min(until, HUBSPOT_DATA_UNTIL)})."
    )
    if pd.Timestamp(until) > pd.Timestamp(HUBSPOT_DATA_UNTIL):
        notes.append(
            f"HubSpot signups export ends {HUBSPOT_DATA_UNTIL}; Aug 2025 uses tracker fallback."
        )
    return {m: float(v) for m, v in by_month.items()}, notes


def clear_hubspot_tab_cache() -> None:
    """Clear in-memory tab cache (for tests or forced refresh)."""
    _tab_cache.clear()


__all__ = [
    "HUBSPOT_ALL_CONTACTS_ID",
    "HUBSPOT_DATA_UNTIL",
    "clear_hubspot_tab_cache",
    "load_hubspot_leads_monthly",
    "load_hubspot_signups_monthly",
]
