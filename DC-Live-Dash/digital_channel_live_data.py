"""
Build Digital Channel Dashboard rows from live Google Ads, Meta, and GoHighLevel APIs.

Replaces the Google Sheet **Data** tab as the source of truth. Metrics mapping:

- **Spend / clicks / impressions** — Google Ads & Meta campaign insights (daily → month-end rows).
- **Leads** — Through Jun 2025: **Digital Channel Dashboard 2024-25** Data tab totals
  (always preferred over partial GHL attribution). From Jul 2025: GHL new contacts
  (``dateAdded``), attributed by hear-about and/or tracker, with unallocated contacts
  spread by spend share (same approach as DCs).
- **DCs** — GHL calendar **meetings** (``startTime``) on configured discovery-call
  calendars; Google/Meta split via hear-about on the linked contact; remainder
  allocated by spend share. Sheet Data tab fills months where live counts are zero.
- **Conversions (signups)** — Through Aug 2025: **GRAND TOTAL New Members** from Digital
  Cross-Channel Tracker Google Sheets; from Sep 2025: GHL committed members with Sign Up Date
  in range (hear-about attribution where available, remainder by spend share).
"""

from __future__ import annotations

import importlib
import sys
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Bump when loader logic or ghl_client signup/DC helpers change — Streamlit keeps
# imported modules across reruns; reload ghl_client when its revision differs.
LIVE_DATA_REVISION = "2026-06-23-meta-tracker-ghl-backfill-v1"
GHL_ATTRIBUTION_HEAR_ABOUT = "hear_about"
GHL_ATTRIBUTION_TRACKER = "tracker"
GHL_ATTRIBUTION_OPTIONS: tuple[tuple[str, str], ...] = (
    (GHL_ATTRIBUTION_HEAR_ABOUT, "How did you hear about us?"),
    (GHL_ATTRIBUTION_TRACKER, "Tracker (GHL tag / pixel)"),
)
_EXPECTED_GHL_CLIENT_REVISION = "2026-06-18-signup-search-resilient-v1"

import ghl_client as _ghl_client

if getattr(_ghl_client, "GHL_CLIENT_REVISION", None) != _EXPECTED_GHL_CLIENT_REVISION:
    _ghl_client = importlib.reload(_ghl_client)

import pandas as pd
from facebook_business.exceptions import FacebookRequestError
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

from digital_channel_sheets import (
    DATA_COLUMNS,
    SPREADSHEET_NAME,
    load_campaign_data,
    monthly_campaign_summary,
    scorecard_metrics,
)
from ghl_client import (
    HEAR_ABOUT_US_FIELD_NAME,
    classify_hear_about_wom_vs_google,
    contact_custom_field_value,
    fetch_discovery_call_meetings_monthly_by_channel,
    fetch_signup_date_range_committed_yes_contacts,
    load_contacts_for_calendar_day,
    resolve_hear_about_us_custom_field_id,
)
from google_ads_ghl_paid_cohort import (
    _google_ads_customer_id,
    _google_ads_login_customer_id,
    _google_ads_yaml_path,
)
from meta_client import _init_api, _parse_float

from dashboard_disk_cache import (
    clear_dashboard_disk_cache,
    read_json_range_cache,
    read_parquet_range_cache,
)

CHANNEL_GOOGLE = "Google Ads"
CHANNEL_META = "FB/IG"

MEMBERSHIP_LEVELS = ("Standard", "Silver", "Gold", "Platinum")
CONV_BY_LEVEL_COLUMNS = ["month", "channel", "membership_level", "conversions"]
SIGNUP_BY_LEVEL_COLUMNS = ["month", "membership_level", "signups"]


def norm_membership_level(raw: str) -> str:
    """Map GHL Membership Level picklist values to dashboard tiers."""
    v = (raw or "").strip()
    if not v or v.casefold() in {"(blank)", "blank", "n/a", "none", "unknown", "-"}:
        return "n/a"
    for level in MEMBERSHIP_LEVELS:
        if v.casefold() == level.casefold():
            return level
    cf = v.casefold()
    for level in MEMBERSHIP_LEVELS:
        if level.lower() in cf:
            return level
    return "n/a"


def build_ghl_signups_by_level_monthly(
    signup: dict[str, Any] | None,
    since: str,
    until: str,
) -> pd.DataFrame:
    """
    Org-wide committed signups by Sign Up Date month and membership tier.

    Counts each contact once (independent of hear-about / tracker attribution).
    Only tiers in ``MEMBERSHIP_LEVELS`` are included.
    """
    if not signup:
        return pd.DataFrame(columns=SIGNUP_BY_LEVEL_COLUMNS)

    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()
    sid = signup.get("sign_up_date_field_id") or ""
    mid = signup.get("membership_level_field_id") or ""
    if not sid:
        return pd.DataFrame(columns=SIGNUP_BY_LEVEL_COLUMNS)

    counts: dict[tuple[pd.Timestamp, str], float] = {}
    for contact in signup.get("contacts") or []:
        raw_signup = contact_custom_field_value(contact, sid).strip()
        if not raw_signup:
            continue
        try:
            signup_day = pd.to_datetime(raw_signup[:10])
        except (ValueError, TypeError):
            continue
        month = signup_day.to_period("M").to_timestamp()
        if month < since_month or month > until_month:
            continue
        level = norm_membership_level(
            contact_custom_field_value(contact, mid) if mid else ""
        )
        if level not in MEMBERSHIP_LEVELS:
            continue
        key = (month, level)
        counts[key] = counts.get(key, 0.0) + 1.0

    if not counts:
        return pd.DataFrame(columns=SIGNUP_BY_LEVEL_COLUMNS)
    records = [
        {"month": month, "membership_level": level, "signups": count}
        for (month, level), count in counts.items()
    ]
    return pd.DataFrame(records, columns=SIGNUP_BY_LEVEL_COLUMNS)


DEFAULT_SINCE = "2023-01-01"
DEFAULT_DASHBOARD_MONTHS = 12
GHL_SIGNUPS_SINCE = "2025-06-01"
SHEETS_SIGNUPS_UNTIL = "2025-05-31"
# DCs trend chart uses the same sheet / GHL cutover as signups.
SHEETS_DCS_UNTIL = SHEETS_SIGNUPS_UNTIL
GHL_DCS_SINCE = GHL_SIGNUPS_SINCE
SHEET_LEADS_UNTIL = "2025-06-30"
# Fetch GHL leads from June onward; sheet Data tab still wins through Jun 2025.
GHL_LEADS_SINCE = "2025-06-01"


def default_dashboard_since(
    *,
    until: date | None = None,
    months: int = DEFAULT_DASHBOARD_MONTHS,
) -> date:
    """Rolling default start date for the live dashboard (last N full months)."""
    until_eff = until or (date.today() - timedelta(days=1))
    return (pd.Timestamp(until_eff) - pd.DateOffset(months=months)).date()
# Jul 2025 GHL bulk import from legacy CRM — CPL trend uses Jun/Aug average.
GHL_CRM_DUMP_MONTH = pd.Timestamp("2025-07-01")
_GHL_LEADS_CACHE_DIR = _PROJECT_ROOT / ".cache" / "ghl_daily_leads"
_GHL_LEADS_FETCH_WORKERS = 8

UNALLOCATED_CONV_COLUMNS = ["month", "membership_level", "conversions"]
WOM_CONV_COLUMNS = ["month", "membership_level", "conversions"]

_GADS_CHANNEL_TO_CREATIVE = {
    "SEARCH": "Text",
    "DISPLAY": "Image",
    "VIDEO": "Video",
    "SHOPPING": "Combo",
    "PERFORMANCE_MAX": "Combo",
    "SMART": "Combo",
    "LOCAL": "Text",
    "DISCOVERY": "Combo",
    "HOTEL": "Text",
    "MULTI_CHANNEL": "Combo",
}

_META_OBJECTIVE_TO_TYPE = {
    "OUTCOME_LEADS": "Lead Gen",
    "LEAD_GENERATION": "Leadgen",
    "OUTCOME_TRAFFIC": "Traffic",
    "LINK_CLICKS": "Traffic",
    "OUTCOME_ENGAGEMENT": "Traffic",
    "POST_ENGAGEMENT": "Traffic",
    "OUTCOME_AWARENESS": "Traffic",
    "BRAND_AWARENESS": "Traffic",
    "REACH": "Traffic",
    "VIDEO_VIEWS": "Traffic",
    "CONVERSIONS": "Lead Gen",
}


def _month_end(ts: pd.Timestamp) -> pd.Timestamp:
    return ts + pd.offsets.MonthEnd(0)


def _fetch_tracker_grand_total_signups(
    since: str, until: str
) -> tuple[dict[pd.Timestamp, float], set[pd.Timestamp], list[str]]:
    """
    Monthly TOTAL / GRAND TOTAL New Members from Digital Cross-Channel Tracker sheets.

    Used for signups through ``SHEETS_SIGNUPS_UNTIL`` (May 31, 2025).
    """
    from googleapiclient.discovery import build
    from total_new_members_yoy_chart import (
        DEFAULT_SHEET,
        TRACKERS,
        _credentials,
        _resolve_tracker_spreadsheet,
        _load_year_series,
        _resolve_sheet_name,
    )

    since_d = pd.Timestamp(since)
    until_d = pd.Timestamp(until)
    sheet_until = min(until_d, pd.Timestamp(SHEETS_SIGNUPS_UNTIL))
    if since_d > sheet_until:
        return {}, set(), []

    creds = _credentials()
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)

    totals: dict[pd.Timestamp, float] = {}
    notes: list[str] = []
    years_needed = set(range(since_d.year, sheet_until.year + 1))

    for meta in TRACKERS.values():
        year = meta["year"]
        if year not in years_needed:
            continue
        file_info = _resolve_tracker_spreadsheet(drive, meta)
        sheet_name = _resolve_sheet_name(sheets, file_info["id"], DEFAULT_SHEET)
        series = _load_year_series(sheets, file_info["id"], sheet_name, year)
        for month_num, val in series.items():
            month_ts = pd.Timestamp(year=year, month=int(month_num), day=1)
            if since_d.to_period("M") <= month_ts.to_period("M") <= sheet_until.to_period(
                "M"
            ):
                totals[month_ts] = float(val)
        notes.append(
            f"Signups (sheet): {meta['name']} / {sheet_name} — "
            "GRAND TOTAL New Members row."
        )

    return totals, set(totals.keys()), notes


def _fetch_tracker_calls_completed(
    since: str, until: str
) -> tuple[dict[pd.Timestamp, float], set[pd.Timestamp], list[str]]:
    """
    Monthly **Calls completed** from Digital Cross-Channel Tracker sheets (2023–2025).

    Used for DCs through ``SHEETS_DCS_UNTIL`` (May 31, 2025).
    """
    from googleapiclient.discovery import build
    from total_new_members_yoy_chart import (
        DEFAULT_SHEET,
        TRACKERS,
        _credentials,
        _resolve_tracker_spreadsheet,
        _load_calls_completed_year_series,
        _resolve_sheet_name,
    )

    since_d = pd.Timestamp(since)
    until_d = pd.Timestamp(until)
    sheet_until = min(until_d, pd.Timestamp(SHEETS_DCS_UNTIL))
    if since_d > sheet_until:
        return {}, set(), []

    creds = _credentials()
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)

    totals: dict[pd.Timestamp, float] = {}
    notes: list[str] = []
    years_needed = set(range(since_d.year, sheet_until.year + 1))

    for meta in TRACKERS.values():
        year = meta["year"]
        if year not in years_needed or year > 2025:
            continue
        file_info = _resolve_tracker_spreadsheet(drive, meta)
        sheet_name = _resolve_sheet_name(sheets, file_info["id"], DEFAULT_SHEET)
        series = _load_calls_completed_year_series(
            sheets, file_info["id"], sheet_name, year
        )
        for month_num, val in series.items():
            month_ts = pd.Timestamp(year=year, month=int(month_num), day=1)
            if since_d.to_period("M") <= month_ts.to_period("M") <= sheet_until.to_period(
                "M"
            ):
                totals[month_ts] = float(val)
        notes.append(
            f"DCs (sheet): {meta['name']} / {sheet_name} — Calls completed row."
        )

    return totals, set(totals.keys()), notes


def _allocate_monthly_signup_totals(
    df: pd.DataFrame,
    monthly_totals: dict[pd.Timestamp, float],
) -> pd.DataFrame:
    """Spread undifferentiated monthly signup totals across rows by spend share."""
    if df.empty or not monthly_totals:
        return df

    out = df.copy()
    if "month" not in out.columns:
        out["month"] = out["date"].dt.to_period("M").dt.to_timestamp()

    for month, total in monthly_totals.items():
        if total <= 0:
            continue
        mask = out["month"] == month
        chunk = out.loc[mask]
        if chunk.empty:
            continue
        total_spend = chunk["spend"].sum()
        if total_spend > 0:
            weights = chunk["spend"] / total_spend
            out.loc[mask, "conversions"] = weights * total
        else:
            share = total / len(chunk)
            out.loc[mask, "conversions"] = share

    return out


def _sheet_leads_by_month_channel(
    since: str, until: str
) -> tuple[pd.DataFrame, pd.Timestamp | None, list[str]]:
    """Channel-month lead totals from the Digital Channel Dashboard Data tab."""
    try:
        sheet_df = load_campaign_data()
    except Exception as exc:
        return pd.DataFrame(), None, [f"Sheet lead baseline skipped: {exc}"]

    if sheet_df.empty:
        return pd.DataFrame(), None, []

    sheet_max_month = sheet_df["date"].max().to_period("M").to_timestamp()
    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = min(
        pd.Timestamp(until).to_period("M").to_timestamp(), sheet_max_month
    )

    subset = sheet_df[
        (sheet_df["month"] >= since_month) & (sheet_df["month"] <= until_month)
    ]
    if subset.empty:
        return pd.DataFrame(), sheet_max_month, []

    monthly = (
        subset.groupby(["month", "channel"], as_index=False)
        .agg(leads=("leads", "sum"))
        .sort_values(["month", "channel"])
    )
    notes = [
        f"Leads (sheet baseline): {SPREADSHEET_NAME} Data tab through "
        f"{sheet_df['date'].max().date()}."
    ]
    return monthly, sheet_max_month, notes


def _apply_sheet_lead_baseline(
    df: pd.DataFrame,
    sheet_monthly: pd.DataFrame,
    sheet_max_month: pd.Timestamp | None,
) -> pd.DataFrame:
    """
    Apply channel-month lead totals from the sheet through ``sheet_max_month``.

    Sheet months always win over partial GHL attribution (e.g. June 2025).
    """
    if df.empty or sheet_monthly.empty:
        return df

    out = df.copy()
    if "month" not in out.columns:
        out["month"] = out["date"].dt.to_period("M").dt.to_timestamp()

    for row in sheet_monthly.itertuples(index=False):
        month, channel, sheet_leads = row.month, row.channel, float(row.leads)
        if sheet_leads <= 0:
            continue
        if sheet_max_month is not None and month > sheet_max_month:
            continue

        mask = (out["month"] == month) & (out["channel"] == channel)
        chunk = out.loc[mask]
        if chunk.empty:
            continue

        force_sheet = sheet_max_month is not None and month <= sheet_max_month
        total_spend = chunk["spend"].sum()
        for col in ("leads", "leads_hear_about", "leads_combined"):
            if col not in out.columns:
                continue
            if not force_sheet and float(out.loc[mask, col].sum()) > 0:
                continue
            if total_spend > 0:
                weights = chunk["spend"] / total_spend
                values = weights * sheet_leads
            else:
                share = sheet_leads / len(chunk)
                values = share
            out.loc[mask, col] = values
            cpl_col = f"{col}_cpl"
            if cpl_col in out.columns and force_sheet:
                out.loc[mask, cpl_col] = values

    return out


def _sheet_dcs_by_month_channel(
    since: str, until: str
) -> tuple[pd.DataFrame, pd.Timestamp | None, list[str]]:
    """Channel-month DC totals from the Digital Channel Dashboard Data tab."""
    try:
        sheet_df = load_campaign_data()
    except Exception as exc:
        return pd.DataFrame(), None, [f"Sheet DC baseline skipped: {exc}"]

    if sheet_df.empty:
        return pd.DataFrame(), None, []

    sheet_max_month = sheet_df["date"].max().to_period("M").to_timestamp()
    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = min(
        pd.Timestamp(until).to_period("M").to_timestamp(), sheet_max_month
    )

    subset = sheet_df[
        (sheet_df["month"] >= since_month) & (sheet_df["month"] <= until_month)
    ]
    if subset.empty:
        return pd.DataFrame(), sheet_max_month, []

    monthly = (
        subset.groupby(["month", "channel"], as_index=False)
        .agg(dcs=("dcs", "sum"))
        .sort_values(["month", "channel"])
    )
    notes = [
        f"DCs (sheet baseline): {SPREADSHEET_NAME} Data tab through "
        f"{sheet_df['date'].max().date()}."
    ]
    return monthly, sheet_max_month, notes


def _apply_sheet_dc_baseline(
    df: pd.DataFrame,
    sheet_monthly: pd.DataFrame,
    sheet_max_month: pd.Timestamp | None,
    *,
    column: str = "dcs_hear_about",
) -> pd.DataFrame:
    """Fill channel-month DC counts from the sheet when live GHL allocation is zero."""
    if df.empty or sheet_monthly.empty or column not in df.columns:
        return df

    out = df.copy()
    if "month" not in out.columns:
        out["month"] = out["date"].dt.to_period("M").dt.to_timestamp()

    for row in sheet_monthly.itertuples(index=False):
        month, channel, sheet_dcs = row.month, row.channel, float(row.dcs)
        if sheet_dcs <= 0:
            continue
        if sheet_max_month is not None and month > sheet_max_month:
            continue

        mask = (out["month"] == month) & (out["channel"] == channel)
        if float(out.loc[mask, column].sum()) > 0:
            continue

        chunk = out.loc[mask]
        if chunk.empty:
            continue

        total_spend = chunk["spend"].sum()
        if total_spend > 0:
            weights = chunk["spend"] / total_spend
            out.loc[mask, column] = weights * sheet_dcs
        else:
            share = sheet_dcs / len(chunk)
            out.loc[mask, column] = share

    return out


def _escape_gaql(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _google_ads_client() -> GoogleAdsClient:
    client = GoogleAdsClient.load_from_storage(path=str(_google_ads_yaml_path()))
    client.login_customer_id = _google_ads_login_customer_id()
    return client


def _gads_creative_type(channel_type: str) -> str:
    key = (channel_type or "").upper().replace(" ", "_")
    if key.startswith("ADVERTISING_CHANNEL_TYPE."):
        key = key.split(".")[-1]
    return _GADS_CHANNEL_TO_CREATIVE.get(key, "Text")


def _meta_creative_type(campaign_name: str, objective: str) -> str:
    name = (campaign_name or "").lower()
    if "video" in name:
        return "Video"
    if "post:" in name or "instagram post" in name:
        return "Text"
    obj = (objective or "").upper()
    if "VIDEO" in obj:
        return "Video"
    if "LEAD" in obj:
        return "Combo"
    return "Video"


def _meta_fb_ig_type(objective: str, campaign_name: str) -> str:
    obj = (objective or "").upper()
    if obj in _META_OBJECTIVE_TO_TYPE:
        return _META_OBJECTIVE_TO_TYPE[obj]
    name = (campaign_name or "").lower()
    if "lead" in name:
        return "Lead Gen"
    if "traffic" in name:
        return "Traffic"
    return "Lead Gen" if "LEAD" in obj else "Traffic"


def fetch_google_ads_campaign_daily(since: str, until: str) -> pd.DataFrame:
    """Daily campaign metrics from Google Ads."""
    query = f"""
        SELECT
            segments.date,
            campaign.name,
            campaign.advertising_channel_type,
            metrics.cost_micros,
            metrics.clicks,
            metrics.impressions,
            metrics.conversions
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          AND segments.date BETWEEN '{since}' AND '{until}'
    """
    client = _google_ads_client()
    service = client.get_service("GoogleAdsService")
    rows: list[dict[str, Any]] = []
    try:
        stream = service.search_stream(
            customer_id=_google_ads_customer_id(), query=query
        )
        for batch in stream:
            for row in batch.results:
                channel_type = row.campaign.advertising_channel_type
                type_name = (
                    channel_type.name
                    if hasattr(channel_type, "name")
                    else str(channel_type).split(".")[-1]
                )
                rows.append(
                    {
                        "date": pd.to_datetime(row.segments.date),
                        "channel": CHANNEL_GOOGLE,
                        "campaign": str(row.campaign.name or "").strip(),
                        "creative_type": _gads_creative_type(type_name),
                        "fb_ig_type": "",
                        "spend": float(row.metrics.cost_micros or 0) / 1_000_000.0,
                        "clicks": int(row.metrics.clicks or 0),
                        "impressions": int(row.metrics.impressions or 0),
                        "leads": 0.0,
                        "dcs": 0.0,
                    }
                )
    except GoogleAdsException as ex:
        msg = "\n".join(err.message for err in ex.failure.errors)
        raise RuntimeError(f"Google Ads API error:\n{msg}") from ex

    if not rows:
        return pd.DataFrame(
            columns=[
                "date",
                "channel",
                "campaign",
                "creative_type",
                "fb_ig_type",
                "spend",
                "clicks",
                "impressions",
                "leads",
                "dcs",
            ]
        )
    return pd.DataFrame(rows)


_META_INSIGHTS_CHUNK_DAYS = 31
_META_INSIGHTS_MAX_RETRIES = 3
_META_EMPTY_DAILY_COLUMNS = [
    "date",
    "channel",
    "campaign",
    "creative_type",
    "fb_ig_type",
    "spend",
    "clicks",
    "impressions",
    "leads",
    "dcs",
]


def _meta_api_error_message(exc: FacebookRequestError) -> str:
    body = getattr(exc, "body", None)
    if callable(body):
        try:
            return str(body())
        except Exception:
            pass
    api_msg = getattr(exc, "api_error_message", None)
    if callable(api_msg):
        try:
            return str(api_msg())
        except Exception:
            pass
    return str(exc)


def _insights_date_chunks(
    since: str, until: str, *, chunk_days: int = _META_INSIGHTS_CHUNK_DAYS
) -> list[tuple[str, str]]:
    start = date.fromisoformat(since)
    end = date.fromisoformat(until)
    chunks: list[tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        chunks.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _meta_campaign_daily_rows(
    account: Any, *, since: str, until: str
) -> list[dict[str, Any]]:
    fields = [
        "campaign_name",
        "campaign_id",
        "spend",
        "impressions",
        "clicks",
        "actions",
        "objective",
        "date_start",
    ]
    params: dict[str, Any] = {
        "time_increment": 1,
        "time_range": {"since": since, "until": until},
        "level": "campaign",
        "limit": 500,
    }
    rows: list[dict[str, Any]] = []
    for row in account.get_insights(fields=fields, params=params):
        data = row.export_all_data()
        campaign_name = (data.get("campaign_name") or "").strip()
        date_start = (data.get("date_start") or "").strip()
        if not campaign_name or not date_start:
            continue
        objective = (data.get("objective") or "").strip()
        rows.append(
            {
                "date": pd.to_datetime(date_start),
                "channel": CHANNEL_META,
                "campaign": campaign_name,
                "creative_type": _meta_creative_type(campaign_name, objective),
                "fb_ig_type": _meta_fb_ig_type(objective, campaign_name),
                "spend": _parse_float(data.get("spend")),
                "clicks": int(_parse_float(data.get("clicks"))),
                "impressions": int(_parse_float(data.get("impressions"))),
                "leads": 0.0,
                "dcs": 0.0,
            }
        )
    return rows


def fetch_meta_campaign_daily(since: str, until: str) -> pd.DataFrame:
    """Daily campaign metrics from Meta (spend, clicks); fetched in monthly chunks."""
    account = _init_api()
    rows: list[dict[str, Any]] = []
    chunks = _insights_date_chunks(since, until)
    errors: list[str] = []

    for chunk_since, chunk_until in chunks:
        last_exc: FacebookRequestError | None = None
        for attempt in range(_META_INSIGHTS_MAX_RETRIES):
            try:
                rows.extend(
                    _meta_campaign_daily_rows(
                        account, since=chunk_since, until=chunk_until
                    )
                )
                last_exc = None
                break
            except FacebookRequestError as exc:
                last_exc = exc
                if attempt + 1 < _META_INSIGHTS_MAX_RETRIES:
                    time.sleep(2**attempt)
        if last_exc is not None:
            errors.append(
                f"{chunk_since}→{chunk_until}: {_meta_api_error_message(last_exc)}"
            )

    if errors and not rows:
        raise RuntimeError(
            "Meta API error (all insight chunks failed):\n" + "\n".join(errors)
        )

    if not rows:
        return pd.DataFrame(columns=_META_EMPTY_DAILY_COLUMNS)

    out = pd.DataFrame(rows)
    if errors:
        # Partial data is still useful; callers can surface errors via notes if needed.
        out.attrs["meta_partial_errors"] = errors
    return out


def fetch_google_ads_campaign_daily_cached(since: str, until: str) -> pd.DataFrame:
    """Google Ads daily rows with disk cache for completed date ranges."""
    return read_parquet_range_cache(
        "google_ads_daily",
        since,
        until,
        lambda: fetch_google_ads_campaign_daily(since, until),
    )


def fetch_meta_campaign_daily_cached(since: str, until: str) -> pd.DataFrame:
    """Meta daily rows with disk cache for completed date ranges."""
    return read_parquet_range_cache(
        "meta_daily",
        since,
        until,
        lambda: fetch_meta_campaign_daily(since, until),
    )


def _fetch_discovery_call_meetings_cached(since: str, until: str) -> dict[str, Any]:
    return read_json_range_cache(
        "ghl_dc_meetings",
        since,
        until,
        lambda: fetch_discovery_call_meetings_monthly_by_channel(since, until),
    )


def _fetch_signup_contacts_cached(since: str, until: str) -> dict[str, Any]:
    return read_json_range_cache(
        "ghl_signups",
        since,
        until,
        lambda: fetch_signup_date_range_committed_yes_contacts(since, until),
    )


def _ghl_channel_for_hear_about(raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    lower = text.casefold()
    if lower in {"facebook", "instagram"}:
        return CHANNEL_META
    if classify_hear_about_wom_vs_google(text) == "Google":
        return CHANNEL_GOOGLE
    if "google" in lower:
        return CHANNEL_GOOGLE
    if "facebook" in lower or "instagram" in lower or "fb" in lower:
        return CHANNEL_META
    return None


def _is_wom_hear_about(raw: str) -> bool:
    """True when hear-about text is classified as Word of mouth (not paid media)."""
    return classify_hear_about_wom_vs_google((raw or "").strip()) == "Word of mouth"


META_LEAD_TAG = "meta lead"
GOOGLE_LEAD_TAG = "dc thru g-ad"


def _contact_tag_names(contact: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for tag in contact.get("tags") or []:
        if isinstance(tag, str):
            name = tag
        else:
            name = str((tag or {}).get("name") or "")
        name = name.strip()
        if name:
            out.append(name)
    return out


def _contact_has_tag(contact: dict[str, Any], tag_name: str) -> bool:
    target = (tag_name or "").strip().casefold()
    if not target:
        return False
    return any(t.casefold() == target for t in _contact_tag_names(contact))


def _contact_attribution_dicts(contact: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in ("attributionSource", "lastAttributionSource"):
        val = contact.get(key)
        if isinstance(val, dict):
            out.append(val)
    return out


def _contact_fired_meta_pixel(contact: dict[str, Any]) -> bool:
    for attr in _contact_attribution_dicts(contact):
        if attr.get("fbp") or attr.get("fbc"):
            return True
    return False


def _contact_fired_google_tag(contact: dict[str, Any]) -> bool:
    for attr in _contact_attribution_dicts(contact):
        if attr.get("gaClientId"):
            return True
    return False


def _is_meta_lead_contact(contact: dict[str, Any]) -> bool:
    return _contact_has_tag(contact, META_LEAD_TAG) or _contact_fired_meta_pixel(contact)


def _is_google_lead_contact(contact: dict[str, Any]) -> bool:
    return _contact_has_tag(contact, GOOGLE_LEAD_TAG) or _contact_fired_google_tag(contact)


def _calendar_days_inclusive(since: str, until: str) -> list[str]:
    start = date.fromisoformat(since)
    end = date.fromisoformat(until)
    days: list[str] = []
    cursor = start
    while cursor <= end:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def _ghl_day_cache_path(day: str) -> Path:
    return _GHL_LEADS_CACHE_DIR / f"{day}.json"


def _ghl_day_cache_is_fresh(day: str, *, today: date | None = None) -> bool:
    """Past days are cached indefinitely; today and yesterday always refresh."""
    today = today or date.today()
    age_days = (today - date.fromisoformat(day)).days
    return age_days > 1


def _combined_paid_channel_from_parts(
    hear_channel: str | None, tracker_channel: str | None
) -> str | None:
    """OR attribution: one channel when sources agree; unallocated when they conflict."""
    if hear_channel and tracker_channel and hear_channel != tracker_channel:
        return None
    return hear_channel or tracker_channel


def _ghl_channel_for_tracker(contact: dict[str, Any]) -> str | None:
    """
    Exclusive Google/Meta channel from GHL tag or pixel.

    Meta ``meta lead`` tag (or Meta pixel) wins over Google tag/pixel so FB/IG
    acquisition tracks like Google when both signals are present on a contact.
    """
    if _is_meta_lead_contact(contact):
        return CHANNEL_META
    if _is_google_lead_contact(contact):
        return CHANNEL_GOOGLE
    return None


def _classify_day_contacts(
    contacts: list[dict[str, Any]],
    hear_id: str = "",
) -> dict[str, int]:
    total = len(contacts)
    meta_tag = google_tag = meta_hear = google_hear = 0
    meta_combined = google_combined = 0
    unallocated_hear = unallocated_tracker = unallocated_combined = 0
    for contact in contacts:
        if _is_meta_lead_contact(contact):
            meta_tag += 1
        if _is_google_lead_contact(contact):
            google_tag += 1
        hear_ch: str | None = None
        if hear_id:
            hear = contact_custom_field_value(contact, hear_id)
            if not _is_wom_hear_about(hear):
                hear_ch = _ghl_channel_for_hear_about(hear)
                if hear_ch == CHANNEL_META:
                    meta_hear += 1
                elif hear_ch == CHANNEL_GOOGLE:
                    google_hear += 1
                else:
                    unallocated_hear += 1
            else:
                unallocated_hear += 1
        else:
            unallocated_hear += 1
        track_ch = _ghl_channel_for_tracker(contact)
        if track_ch is None:
            unallocated_tracker += 1
        combined_ch = _combined_paid_channel_from_parts(hear_ch, track_ch)
        if combined_ch == CHANNEL_META:
            meta_combined += 1
        elif combined_ch == CHANNEL_GOOGLE:
            google_combined += 1
        else:
            unallocated_combined += 1
    return {
        "total": total,
        "meta": meta_tag,
        "google": google_tag,
        "meta_hear": meta_hear,
        "google_hear": google_hear,
        "meta_combined": meta_combined,
        "google_combined": google_combined,
        "unallocated_hear": unallocated_hear,
        "unallocated_tracker": unallocated_tracker,
        "unallocated_combined": unallocated_combined,
    }


def _fetch_day_lead_counts(day: str, hear_id: str = "") -> dict[str, Any]:
    """Load or fetch lead counts for one calendar day (disk-cached)."""
    cache_path = _ghl_day_cache_path(day)
    if cache_path.is_file() and _ghl_day_cache_is_fresh(day):
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if "meta_hear" in payload and "google_hear" in payload and "meta_combined" in payload:
            if "unallocated_hear" in payload:
                return payload

    contacts, truncated = load_contacts_for_calendar_day(day)
    counts = _classify_day_contacts(contacts, hear_id)
    payload = {
        "date": day,
        "total": counts["total"],
        "meta": counts["meta"],
        "google": counts["google"],
        "meta_hear": counts["meta_hear"],
        "google_hear": counts["google_hear"],
        "meta_combined": counts["meta_combined"],
        "google_combined": counts["google_combined"],
        "unallocated_hear": counts["unallocated_hear"],
        "unallocated_tracker": counts["unallocated_tracker"],
        "unallocated_combined": counts["unallocated_combined"],
        "truncated": truncated,
    }
    _GHL_LEADS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def clear_ghl_leads_day_cache() -> None:
    """Remove cached daily GHL lead files (used by dashboard hard refresh)."""
    if not _GHL_LEADS_CACHE_DIR.is_dir():
        return
    for path in _GHL_LEADS_CACHE_DIR.glob("*.json"):
        path.unlink(missing_ok=True)


def _fetch_ghl_leads_by_date_added(since: str, until: str) -> dict[str, Any]:
    """Classify new GHL contacts by date added into total / Meta / Google lead counts."""
    try:
        hear_id = resolve_hear_about_us_custom_field_id()
    except Exception:
        hear_id = ""

    days = _calendar_days_inclusive(since, until)
    day_rows: list[dict[str, Any]] = []
    truncated = False

    pending = [
        day
        for day in days
        if not (_ghl_day_cache_path(day).is_file() and _ghl_day_cache_is_fresh(day))
    ]
    for day in days:
        if day not in pending:
            cached = json.loads(_ghl_day_cache_path(day).read_text(encoding="utf-8"))
            if "meta_hear" in cached and "google_hear" in cached and "meta_combined" in cached:
                if "unallocated_hear" in cached:
                    day_rows.append(cached)
                else:
                    pending.append(day)

    pending = sorted(set(pending))
    if pending:
        workers = min(_GHL_LEADS_FETCH_WORKERS, len(pending))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_fetch_day_lead_counts, day, hear_id): day for day in pending
            }
            for future in as_completed(futures):
                day_rows.append(future.result())

    meta_by_month: dict[str, int] = {}
    google_by_month: dict[str, int] = {}
    meta_hear_by_month: dict[str, int] = {}
    google_hear_by_month: dict[str, int] = {}
    meta_combined_by_month: dict[str, int] = {}
    google_combined_by_month: dict[str, int] = {}
    unallocated_hear_by_month: dict[str, int] = {}
    unallocated_tracker_by_month: dict[str, int] = {}
    unallocated_combined_by_month: dict[str, int] = {}
    total_by_month: dict[str, int] = {}
    meta_total = google_total = meta_hear_total = google_hear_total = 0
    meta_combined_total = google_combined_total = total_new = 0

    for row in day_rows:
        day = row["date"]
        month_key = day[:7] + "-01"
        total = int(row.get("total") or 0)
        meta = int(row.get("meta") or 0)
        google = int(row.get("google") or 0)
        meta_hear = int(row.get("meta_hear") or 0)
        google_hear = int(row.get("google_hear") or 0)
        meta_combined = int(row.get("meta_combined") or 0)
        google_combined = int(row.get("google_combined") or 0)
        unalloc_hear = int(row.get("unallocated_hear") or 0)
        unalloc_tracker = int(row.get("unallocated_tracker") or 0)
        unalloc_combined = int(row.get("unallocated_combined") or 0)
        truncated = truncated or bool(row.get("truncated"))

        total_by_month[month_key] = total_by_month.get(month_key, 0) + total
        meta_by_month[month_key] = meta_by_month.get(month_key, 0) + meta
        google_by_month[month_key] = google_by_month.get(month_key, 0) + google
        meta_hear_by_month[month_key] = meta_hear_by_month.get(month_key, 0) + meta_hear
        google_hear_by_month[month_key] = (
            google_hear_by_month.get(month_key, 0) + google_hear
        )
        meta_combined_by_month[month_key] = (
            meta_combined_by_month.get(month_key, 0) + meta_combined
        )
        google_combined_by_month[month_key] = (
            google_combined_by_month.get(month_key, 0) + google_combined
        )
        unallocated_hear_by_month[month_key] = (
            unallocated_hear_by_month.get(month_key, 0) + unalloc_hear
        )
        unallocated_tracker_by_month[month_key] = (
            unallocated_tracker_by_month.get(month_key, 0) + unalloc_tracker
        )
        unallocated_combined_by_month[month_key] = (
            unallocated_combined_by_month.get(month_key, 0) + unalloc_combined
        )
        total_new += total
        meta_total += meta
        google_total += google
        meta_hear_total += meta_hear
        google_hear_total += google_hear
        meta_combined_total += meta_combined
        google_combined_total += google_combined

    monthly: list[dict[str, Any]] = []
    for period in pd.period_range(
        start=pd.Timestamp(since).to_period("M"),
        end=pd.Timestamp(until).to_period("M"),
        freq="M",
    ):
        month_start = period.to_timestamp().strftime("%Y-%m-%d")
        monthly.append(
            {
                "month_start": month_start,
                "total_new_contacts": int(total_by_month.get(month_start, 0)),
                "meta_leads": int(meta_by_month.get(month_start, 0)),
                "google_leads": int(google_by_month.get(month_start, 0)),
                "meta_leads_hear_about": int(meta_hear_by_month.get(month_start, 0)),
                "google_leads_hear_about": int(
                    google_hear_by_month.get(month_start, 0)
                ),
                "meta_leads_combined": int(meta_combined_by_month.get(month_start, 0)),
                "google_leads_combined": int(
                    google_combined_by_month.get(month_start, 0)
                ),
                "unallocated_leads_hear_about": int(
                    unallocated_hear_by_month.get(month_start, 0)
                ),
                "unallocated_leads_tracker": int(
                    unallocated_tracker_by_month.get(month_start, 0)
                ),
                "unallocated_leads_combined": int(
                    unallocated_combined_by_month.get(month_start, 0)
                ),
            }
        )

    cache_note = (
        f"GHL leads: loaded {len(pending)} day(s) from API, "
        f"{len(days) - len(pending)} from cache."
        if pending
        else f"GHL leads: all {len(days)} day(s) served from cache."
    )

    return {
        "since": since,
        "until": until,
        "contacts_loaded": total_new,
        "total_new_contacts": total_new,
        "meta_leads": meta_total,
        "google_leads": google_total,
        "meta_leads_hear_about": meta_hear_total,
        "google_leads_hear_about": google_hear_total,
        "meta_leads_combined": meta_combined_total,
        "google_leads_combined": google_combined_total,
        "monthly": monthly,
        "truncated_pages": truncated,
        "total_reported": total_new,
        "cache_note": cache_note,
        "days_fetched_live": len(pending),
    }


def fetch_ghl_channel_monthly(
    since: str, until: str
) -> tuple[
    pd.DataFrame,
    dict[str, Any],
    list[str],
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    dict[pd.Timestamp, float],
    dict[str, dict[pd.Timestamp, float]],
    dict[pd.Timestamp, float],
    dict[pd.Timestamp, float],
    dict[pd.Timestamp, float],
    pd.DataFrame,
]:
    """
    Channel-month GHL funnel metrics not available at campaign level in ads APIs.

    Returns channel-month frame plus conversions by membership level and
    unallocated committed signups (no paid-media hear-about match).
    """
    notes: list[str] = []
    lead_summary: dict[str, Any] = {
        "total_new_contacts": 0,
        "meta_leads": 0,
        "google_leads": 0,
        "meta_leads_hear_about": 0,
        "google_leads_hear_about": 0,
        "meta_leads_combined": 0,
        "google_leads_combined": 0,
    }

    leads_by_month_channel: dict[tuple[pd.Timestamp, str], float] = {}
    leads_hear_by_month_channel: dict[tuple[pd.Timestamp, str], float] = {}
    leads_combined_by_month_channel: dict[tuple[pd.Timestamp, str], float] = {}
    unallocated_leads_by_attr: dict[str, dict[pd.Timestamp, float]] = {
        "leads": {},
        "leads_hear_about": {},
        "leads_combined": {},
    }
    ghl_leads_org_by_month: dict[pd.Timestamp, float] = {}
    leads_since = max(since, GHL_LEADS_SINCE)
    dc_since = max(since, GHL_DCS_SINCE)
    ghl_conv_since = max(since, GHL_SIGNUPS_SINCE)
    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()

    ghl_leads: dict[str, Any] | None = None
    dc_meetings: dict[str, Any] | None = None
    signup: dict[str, Any] | None = None

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures: dict[str, Any] = {}
        if pd.Timestamp(until) >= pd.Timestamp(leads_since):
            futures["leads"] = pool.submit(
                _fetch_ghl_leads_by_date_added, leads_since, until
            )
        else:
            notes.append(
                f"GHL leads not queried (range ends before {GHL_LEADS_SINCE}; "
                f"sheet baseline through {SHEET_LEADS_UNTIL})."
            )
        if pd.Timestamp(until) >= pd.Timestamp(dc_since):
            futures["dcs"] = pool.submit(
                _fetch_discovery_call_meetings_cached, dc_since, until
            )
        else:
            notes.append(
                f"GHL DCs not queried (range ends before {GHL_DCS_SINCE}; "
                f"sheet baseline through {SHEETS_DCS_UNTIL})."
            )
        if pd.Timestamp(until) >= pd.Timestamp(ghl_conv_since):
            futures["signups"] = pool.submit(
                _fetch_signup_contacts_cached, ghl_conv_since, until
            )
        else:
            notes.append(
                f"GHL signups not queried (range ends before {GHL_SIGNUPS_SINCE})."
            )

        for name, future in futures.items():
            try:
                result = future.result()
            except Exception as exc:
                if name == "leads":
                    notes.append(f"GHL lead counts skipped: {exc}")
                elif name == "dcs":
                    notes.append(f"GHL discovery-call meetings skipped: {exc}")
                else:
                    notes.append(f"GHL new-patient counts skipped: {exc}")
                continue
            if name == "leads":
                ghl_leads = result
            elif name == "dcs":
                dc_meetings = result
            else:
                signup = result

    if ghl_leads is not None:
        if leads_since > since:
            notes.append(
                f"GHL leads API scoped to {leads_since} → {until} "
                f"(sheet baseline through {SHEET_LEADS_UNTIL})."
            )
        lead_summary = {
            "total_new_contacts": int(ghl_leads.get("total_new_contacts") or 0),
            "meta_leads": int(ghl_leads.get("meta_leads") or 0),
            "google_leads": int(ghl_leads.get("google_leads") or 0),
            "meta_leads_hear_about": int(
                ghl_leads.get("meta_leads_hear_about") or 0
            ),
            "google_leads_hear_about": int(
                ghl_leads.get("google_leads_hear_about") or 0
            ),
            "meta_leads_combined": int(ghl_leads.get("meta_leads_combined") or 0),
            "google_leads_combined": int(
                ghl_leads.get("google_leads_combined") or 0
            ),
        }
        if ghl_leads.get("truncated_pages"):
            notes.append(
                "GHL date-added search hit pagination cap on at least one day; "
                "lead counts may be low."
            )
        cache_note = ghl_leads.get("cache_note")
        if cache_note:
            notes.append(str(cache_note))
        live_days = int(ghl_leads.get("days_fetched_live") or 0)
        if live_days > 30:
            notes.append(
                f"First load for {live_days} days can take several minutes "
                "(GHL requires one API call per day). Later loads reuse cache."
            )
        for row in ghl_leads.get("monthly") or []:
            ms = (row.get("month_start") or "")[:10]
            if not ms:
                continue
            month = pd.Timestamp(ms).to_period("M").to_timestamp()
            total_n = float(row.get("total_new_contacts") or 0)
            if total_n:
                ghl_leads_org_by_month[month] = total_n
            meta_n = float(row.get("meta_leads") or 0)
            google_n = float(row.get("google_leads") or 0)
            meta_hear_n = float(row.get("meta_leads_hear_about") or 0)
            google_hear_n = float(row.get("google_leads_hear_about") or 0)
            meta_combined_n = float(row.get("meta_leads_combined") or 0)
            google_combined_n = float(row.get("google_leads_combined") or 0)
            if meta_n:
                leads_by_month_channel[(month, CHANNEL_META)] = meta_n
            if google_n:
                leads_by_month_channel[(month, CHANNEL_GOOGLE)] = google_n
            if meta_hear_n:
                leads_hear_by_month_channel[(month, CHANNEL_META)] = meta_hear_n
            if google_hear_n:
                leads_hear_by_month_channel[(month, CHANNEL_GOOGLE)] = google_hear_n
            if meta_combined_n:
                leads_combined_by_month_channel[(month, CHANNEL_META)] = meta_combined_n
            if google_combined_n:
                leads_combined_by_month_channel[(month, CHANNEL_GOOGLE)] = (
                    google_combined_n
                )
            unalloc_hear = float(row.get("unallocated_leads_hear_about") or 0)
            unalloc_tracker = float(row.get("unallocated_leads_tracker") or 0)
            unalloc_combined = float(row.get("unallocated_leads_combined") or 0)
            if unalloc_hear:
                unallocated_leads_by_attr["leads_hear_about"][month] = unalloc_hear
            if unalloc_tracker:
                unallocated_leads_by_attr["leads"][month] = unalloc_tracker
            if unalloc_combined:
                unallocated_leads_by_attr["leads_combined"][month] = unalloc_combined

    # Discovery calls: calendar meetings on configured GHL calendars
    meta_dcs_hear_by_month: dict[pd.Timestamp, float] = {}
    google_dcs_hear_by_month: dict[pd.Timestamp, float] = {}
    meta_dcs_tracker_by_month: dict[pd.Timestamp, float] = {}
    google_dcs_tracker_by_month: dict[pd.Timestamp, float] = {}
    unallocated_dcs_by_attr: dict[str, dict[pd.Timestamp, float]] = {
        "dcs_hear_about": {},
        "dcs_tracker": {},
    }
    ghl_dcs_by_month: dict[pd.Timestamp, float] = {}
    if dc_meetings is not None:
        if dc_since > since:
            notes.append(
                f"GHL DCs API scoped to {dc_since} → {until} "
                f"(sheet baseline through {SHEETS_DCS_UNTIL})."
            )
        if dc_meetings.get("calendar_api_errors"):
            notes.append(
                "GHL discovery-call calendar API returned errors for at least one "
                "calendar; DC counts may be low."
            )
        if dc_meetings.get("missing_contact_link"):
            notes.append(
                f"GHL discovery calls: {dc_meetings['missing_contact_link']} meeting(s) "
                "had no linked contact (counted as unallocated)."
            )
        notes.append(
            "GHL DCs: calendar meetings (startTime) on "
            f"{dc_meetings.get('calendar_count', 0)} discovery-call calendar(s); "
            f"{int(dc_meetings.get('meetings_total') or 0):,} meeting(s) in range."
        )
        for row in dc_meetings.get("monthly") or []:
            ms = (row.get("month_start") or "")[:10]
            if not ms:
                continue
            month = pd.Timestamp(ms).to_period("M").to_timestamp()
            google_dcs_hear_by_month[month] = float(row.get("google") or 0)
            meta_dcs_hear_by_month[month] = float(row.get("meta") or 0)
            google_dcs_tracker_by_month[month] = float(row.get("google_tracker") or 0)
            meta_dcs_tracker_by_month[month] = float(row.get("meta_tracker") or 0)
            unallocated_hear = float(row.get("unallocated") or 0)
            unallocated_tracker = float(row.get("unallocated_tracker") or 0)
            if unallocated_hear:
                unallocated_dcs_by_attr["dcs_hear_about"][month] = unallocated_hear
            if unallocated_tracker:
                unallocated_dcs_by_attr["dcs_tracker"][month] = unallocated_tracker
            ghl_dcs_by_month[month] = (
                float(row.get("google") or 0)
                + float(row.get("meta") or 0)
                + unallocated_hear
            )

    # New patients: committed + sign-up date + hear-about channel + membership level
    # (GHL only from GHL_SIGNUPS_SINCE; earlier months come from tracker sheets.)
    conv_by_month_channel: dict[tuple[pd.Timestamp, str], float] = {}
    conv_by_month_channel_level: dict[tuple[pd.Timestamp, str, str], float] = {}
    tracker_conv_by_month_channel_level: dict[tuple[pd.Timestamp, str, str], float] = {}
    combined_conv_by_month_channel_level: dict[tuple[pd.Timestamp, str, str], float] = {}
    unallocated_by_month_level: dict[tuple[pd.Timestamp, str], float] = {}
    tracker_unallocated_by_month_level: dict[tuple[pd.Timestamp, str], float] = {}
    combined_unallocated_by_month_level: dict[tuple[pd.Timestamp, str], float] = {}
    wom_by_month_level: dict[tuple[pd.Timestamp, str], float] = {}
    ghl_signups_by_month: dict[pd.Timestamp, float] = {}
    ghl_signups_by_level_df = pd.DataFrame(columns=SIGNUP_BY_LEVEL_COLUMNS)
    if signup is not None:
        try:
            hear_id = resolve_hear_about_us_custom_field_id()
            if signup.get("truncated_pages"):
                notes.append(
                    "GHL sign-up date search hit pagination cap or skipped day(s) "
                    "after API errors; signup counts may be low."
                )
            committed_n = len(signup.get("contacts") or [])
            notes.append(
                f"GHL signups loaded: {committed_n:,} committed (Sign Up Date "
                f"{ghl_conv_since} → {until})."
            )
            mid = signup.get("membership_level_field_id") or ""
            for contact in signup.get("contacts") or []:
                raw_signup = contact_custom_field_value(
                    contact, signup["sign_up_date_field_id"]
                ).strip()
                if not raw_signup:
                    continue
                try:
                    signup_day = pd.to_datetime(raw_signup[:10])
                except (ValueError, TypeError):
                    continue
                month = signup_day.to_period("M").to_timestamp()
                if month < since_month or month > until_month:
                    continue
                ghl_signups_by_month[month] = ghl_signups_by_month.get(month, 0.0) + 1.0
                hear = contact_custom_field_value(contact, hear_id)
                level = norm_membership_level(
                    contact_custom_field_value(contact, mid) if mid else ""
                )
                if _is_wom_hear_about(hear):
                    key_wom = (month, level)
                    wom_by_month_level[key_wom] = (
                        wom_by_month_level.get(key_wom, 0.0) + 1.0
                    )
                    continue
                channel = _ghl_channel_for_hear_about(hear)
                tracker_channel = _ghl_channel_for_tracker(contact)
                combined_channel = _combined_paid_channel_from_parts(
                    channel, tracker_channel
                )
                if channel is None:
                    key_ul = (month, level)
                    unallocated_by_month_level[key_ul] = (
                        unallocated_by_month_level.get(key_ul, 0.0) + 1.0
                    )
                else:
                    key = (month, channel)
                    conv_by_month_channel[key] = conv_by_month_channel.get(key, 0.0) + 1.0
                    key_level = (month, channel, level)
                    conv_by_month_channel_level[key_level] = (
                        conv_by_month_channel_level.get(key_level, 0.0) + 1.0
                    )
                if tracker_channel is None:
                    key_tul = (month, level)
                    tracker_unallocated_by_month_level[key_tul] = (
                        tracker_unallocated_by_month_level.get(key_tul, 0.0) + 1.0
                    )
                else:
                    key_tlevel = (month, tracker_channel, level)
                    tracker_conv_by_month_channel_level[key_tlevel] = (
                        tracker_conv_by_month_channel_level.get(key_tlevel, 0.0)
                        + 1.0
                    )
                if combined_channel is None:
                    key_cul = (month, level)
                    combined_unallocated_by_month_level[key_cul] = (
                        combined_unallocated_by_month_level.get(key_cul, 0.0) + 1.0
                    )
                else:
                    key_clevel = (month, combined_channel, level)
                    combined_conv_by_month_channel_level[key_clevel] = (
                        combined_conv_by_month_channel_level.get(key_clevel, 0.0)
                        + 1.0
                    )
        except Exception as exc:
            notes.append(f"GHL new-patient counts skipped: {exc}")
        ghl_signups_by_level_df = build_ghl_signups_by_level_monthly(
            signup, since, until
        )

    records: list[dict[str, Any]] = []
    months = pd.period_range(
        start=pd.Timestamp(since).to_period("M"),
        end=pd.Timestamp(until).to_period("M"),
        freq="M",
    )
    for period in months:
        month = period.to_timestamp()
        records.append(
            {
                "month": month,
                "channel": CHANNEL_META,
                "leads": leads_by_month_channel.get((month, CHANNEL_META), 0.0),
                "leads_hear_about": leads_hear_by_month_channel.get(
                    (month, CHANNEL_META), 0.0
                ),
                "leads_combined": leads_combined_by_month_channel.get(
                    (month, CHANNEL_META), 0.0
                ),
                "dcs_hear_about": meta_dcs_hear_by_month.get(month, 0.0),
                "dcs_tracker": meta_dcs_tracker_by_month.get(month, 0.0),
                "conversions": conv_by_month_channel.get((month, CHANNEL_META), 0.0),
            }
        )
        records.append(
            {
                "month": month,
                "channel": CHANNEL_GOOGLE,
                "leads": leads_by_month_channel.get((month, CHANNEL_GOOGLE), 0.0),
                "leads_hear_about": leads_hear_by_month_channel.get(
                    (month, CHANNEL_GOOGLE), 0.0
                ),
                "leads_combined": leads_combined_by_month_channel.get(
                    (month, CHANNEL_GOOGLE), 0.0
                ),
                "dcs_hear_about": google_dcs_hear_by_month.get(month, 0.0),
                "dcs_tracker": google_dcs_tracker_by_month.get(month, 0.0),
                "conversions": conv_by_month_channel.get((month, CHANNEL_GOOGLE), 0.0),
            }
        )

    conv_by_level_records = [
        {
            "month": month,
            "channel": channel,
            "membership_level": level,
            "conversions": count,
        }
        for (month, channel, level), count in conv_by_month_channel_level.items()
    ]
    conv_by_level_df = (
        pd.DataFrame(conv_by_level_records, columns=CONV_BY_LEVEL_COLUMNS)
        if conv_by_level_records
        else pd.DataFrame(columns=CONV_BY_LEVEL_COLUMNS)
    )

    unallocated_records = [
        {"month": month, "membership_level": level, "conversions": count}
        for (month, level), count in unallocated_by_month_level.items()
    ]
    unallocated_conv_df = (
        pd.DataFrame(unallocated_records, columns=UNALLOCATED_CONV_COLUMNS)
        if unallocated_records
        else pd.DataFrame(columns=UNALLOCATED_CONV_COLUMNS)
    )

    wom_records = [
        {"month": month, "membership_level": level, "conversions": count}
        for (month, level), count in wom_by_month_level.items()
    ]
    wom_conv_df = (
        pd.DataFrame(wom_records, columns=WOM_CONV_COLUMNS)
        if wom_records
        else pd.DataFrame(columns=WOM_CONV_COLUMNS)
    )
    tracker_conv_by_level_records = [
        {
            "month": month,
            "channel": channel,
            "membership_level": level,
            "conversions": count,
        }
        for (month, channel, level), count in tracker_conv_by_month_channel_level.items()
    ]
    tracker_conv_by_level_df = (
        pd.DataFrame(tracker_conv_by_level_records, columns=CONV_BY_LEVEL_COLUMNS)
        if tracker_conv_by_level_records
        else pd.DataFrame(columns=CONV_BY_LEVEL_COLUMNS)
    )

    tracker_unallocated_records = [
        {"month": month, "membership_level": level, "conversions": count}
        for (month, level), count in tracker_unallocated_by_month_level.items()
    ]
    tracker_unallocated_conv_df = (
        pd.DataFrame(tracker_unallocated_records, columns=UNALLOCATED_CONV_COLUMNS)
        if tracker_unallocated_records
        else pd.DataFrame(columns=UNALLOCATED_CONV_COLUMNS)
    )

    combined_conv_by_level_records = [
        {
            "month": month,
            "channel": channel,
            "membership_level": level,
            "conversions": count,
        }
        for (month, channel, level), count in combined_conv_by_month_channel_level.items()
    ]
    combined_conv_by_level_df = (
        pd.DataFrame(combined_conv_by_level_records, columns=CONV_BY_LEVEL_COLUMNS)
        if combined_conv_by_level_records
        else pd.DataFrame(columns=CONV_BY_LEVEL_COLUMNS)
    )
    combined_unallocated_records = [
        {"month": month, "membership_level": level, "conversions": count}
        for (month, level), count in combined_unallocated_by_month_level.items()
    ]
    combined_unallocated_conv_df = (
        pd.DataFrame(combined_unallocated_records, columns=UNALLOCATED_CONV_COLUMNS)
        if combined_unallocated_records
        else pd.DataFrame(columns=UNALLOCATED_CONV_COLUMNS)
    )
    combined_attr_n = (
        int(combined_conv_by_level_df["conversions"].sum())
        if not combined_conv_by_level_df.empty
        else 0
    )
    combined_unalloc_n = (
        int(combined_unallocated_conv_df["conversions"].sum())
        if not combined_unallocated_conv_df.empty
        else 0
    )
    if combined_attr_n or combined_unalloc_n:
        notes.append(
            f"GHL signups (combined OR): {combined_attr_n:,} deduped hear-about ∪ "
            f"tracker; {combined_unalloc_n:,} unallocated (conflict, blank, or neither)."
        )

    tracker_attr_n = int(tracker_conv_by_level_df["conversions"].sum()) if not tracker_conv_by_level_df.empty else 0
    tracker_unalloc_n = int(tracker_unallocated_conv_df["conversions"].sum()) if not tracker_unallocated_conv_df.empty else 0
    if tracker_attr_n or tracker_unalloc_n:
        notes.append(
            f"GHL signups (tracker): {tracker_attr_n:,} attributed via tag/pixel "
            f"({GOOGLE_LEAD_TAG!r} or gaClientId / meta lead tag or pixel); "
            f"{tracker_unalloc_n:,} unallocated (both tags, neither, or ambiguous). "
            "Google tracking is recent in GHL; Meta tracker counts may be sparse."
        )

    wom_total = int(wom_conv_df["conversions"].sum()) if not wom_conv_df.empty else 0
    if wom_total:
        notes.append(
            f"GHL signups (Word of mouth): {wom_total:,} with hear-about containing "
            '"word of mouth" — excluded from paid channels unless WOM or Other '
            "signup toggles are on."
        )

    unalloc_lead_total = sum(
        float(v) for bucket in unallocated_leads_by_attr.values() for v in bucket.values()
    )
    if unalloc_lead_total:
        notes.append(
            "GHL leads (unallocated): contacts without paid attribution for the "
            "selected source — included only when **Include Organic leads** is on."
        )
    unalloc_signup_n = (
        int(unallocated_conv_df["conversions"].sum())
        if not unallocated_conv_df.empty
        else 0
    )
    if unalloc_signup_n:
        notes.append(
            f"GHL signups (Other/hear-about): {unalloc_signup_n:,} blank/other — "
            "excluded from channel CPA unless **Include Other signups** is on."
        )

    return (
        pd.DataFrame(records),
        lead_summary,
        notes,
        conv_by_level_df,
        unallocated_conv_df,
        wom_conv_df,
        tracker_conv_by_level_df,
        tracker_unallocated_conv_df,
        combined_conv_by_level_df,
        combined_unallocated_conv_df,
        unallocated_dcs_by_attr,
        unallocated_leads_by_attr,
        ghl_signups_by_month,
        ghl_dcs_by_month,
        ghl_leads_org_by_month,
        ghl_signups_by_level_df,
    )


def _aggregate_to_month_end(df: pd.DataFrame) -> pd.DataFrame:
    """Roll daily campaign rows up to month-end snapshots (matches Sheet grain)."""
    if df.empty:
        return pd.DataFrame(columns=DATA_COLUMNS)

    df = df.copy()
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
    agg = (
        df.groupby(
            ["month", "channel", "campaign", "creative_type", "fb_ig_type"],
            as_index=False,
        )
        .agg(
            spend=("spend", "sum"),
            clicks=("clicks", "sum"),
            impressions=("impressions", "sum"),
            leads=("leads", "sum"),
            dcs=("dcs", "sum"),
        )
    )
    agg["date"] = agg["month"].apply(_month_end)
    return agg


def _allocate_ghl_metrics(
    df: pd.DataFrame, ghl_monthly: pd.DataFrame
) -> pd.DataFrame:
    """Spread channel-month GHL leads, DCs & conversions across campaigns by spend share."""
    if df.empty:
        return df

    out = df.copy()
    out["leads"] = 0.0
    out["dcs_hear_about"] = 0.0
    out["conversions"] = 0.0

    for (month, channel), ghl_row in ghl_monthly.groupby(["month", "channel"]):
        mask = (out["month"] == month) & (out["channel"] == channel)
        chunk = out.loc[mask]
        if chunk.empty:
            continue

        total_spend = chunk["spend"].sum()
        channel_leads = float(ghl_row["leads"].sum())
        channel_dcs = float(ghl_row["dcs_hear_about"].sum())
        channel_conv = float(ghl_row["conversions"].sum())

        if channel_leads > 0:
            if total_spend > 0:
                weights = chunk["spend"] / total_spend
                out.loc[mask, "leads"] = weights * channel_leads
            else:
                share = channel_leads / len(chunk)
                out.loc[mask, "leads"] = share

        if channel_dcs > 0:
            if total_spend > 0:
                weights = chunk["spend"] / total_spend
                out.loc[mask, "dcs_hear_about"] = weights * channel_dcs
            else:
                share = channel_dcs / len(chunk)
                out.loc[mask, "dcs_hear_about"] = share

        if channel_conv > 0:
            if total_spend > 0:
                weights = chunk["spend"] / total_spend
                out.loc[mask, "conversions"] = weights * channel_conv
            else:
                share = channel_conv / len(chunk)
                out.loc[mask, "conversions"] = share

    return out


def _snapshot_cpl_lead_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Copy attributed lead columns before unallocated spread (used for CPL trends)."""
    out = df.copy()
    for col in ("leads", "leads_hear_about", "leads_combined"):
        if col in out.columns:
            out[f"{col}_cpl"] = out[col]
    return out


def _allocate_ghl_column(
    df: pd.DataFrame,
    ghl_monthly: pd.DataFrame,
    *,
    ghl_column: str,
    out_column: str,
) -> pd.DataFrame:
    """Spread one channel-month GHL metric column across campaigns by spend share."""
    if df.empty or ghl_column not in ghl_monthly.columns:
        return df

    out = df.copy()
    out[out_column] = 0.0

    for (month, channel), ghl_row in ghl_monthly.groupby(["month", "channel"]):
        mask = (out["month"] == month) & (out["channel"] == channel)
        chunk = out.loc[mask]
        if chunk.empty:
            continue
        channel_total = float(ghl_row[ghl_column].sum())
        if channel_total <= 0:
            continue
        total_spend = chunk["spend"].sum()
        if total_spend > 0:
            weights = chunk["spend"] / total_spend
            out.loc[mask, out_column] = weights * channel_total
        else:
            share = channel_total / len(chunk)
            out.loc[mask, out_column] = share

    return out


def _allocate_unallocated_monthly_metric(
    df: pd.DataFrame,
    unallocated_by_month: dict[pd.Timestamp, float],
    *,
    column: str,
) -> pd.DataFrame:
    """Spread undifferentiated monthly totals across rows by spend share."""
    if df.empty or not unallocated_by_month:
        return df

    out = df.copy()
    if "month" not in out.columns:
        out["month"] = out["date"].dt.to_period("M").dt.to_timestamp()

    for month, total in unallocated_by_month.items():
        if total <= 0:
            continue
        mask = out["month"] == month
        chunk = out.loc[mask]
        if chunk.empty:
            continue
        total_spend = chunk["spend"].sum()
        if total_spend > 0:
            weights = chunk["spend"] / total_spend
            out.loc[mask, column] = out.loc[mask, column] + weights * total
        else:
            share = total / len(chunk)
            out.loc[mask, column] = out.loc[mask, column] + share

    return out


def apply_membership_conversion_filter(
    df: pd.DataFrame,
    conv_by_level_df: pd.DataFrame,
    selected_levels: list[str],
    *,
    unallocated_conv_df: pd.DataFrame | None = None,
    wom_conv_df: pd.DataFrame | None = None,
    include_wom_signups: bool = True,
    include_other_signups: bool = False,
    sheet_signup_months: set[pd.Timestamp] | None = None,
) -> pd.DataFrame:
    """
    Re-allocate new-patient (conversion) counts for selected membership tiers.

    Sheet-sourced months (pre-Jun 2025 tracker totals) are preserved unchanged.
    Other (unattributed) GHL signups spread by spend share only when
    ``include_other_signups`` is True. Word-of-mouth signups spread only when
    ``include_wom_signups`` is True.
    """
    if df.empty:
        return df

    out = df.copy()
    if "month" not in out.columns:
        out["month"] = out["date"].dt.to_period("M").dt.to_timestamp()

    sheet_months = sheet_signup_months or set()
    sheet_mask = out["month"].isin(sheet_months)
    preserved = out.loc[sheet_mask, ["conversions"]].copy()

    out["conversions"] = 0.0
    levels = [lv for lv in selected_levels if lv in MEMBERSHIP_LEVELS]
    if not levels:
        if not preserved.empty:
            out.loc[sheet_mask, "conversions"] = preserved["conversions"]
        return _update_conversion_derived_columns(out)

    ghl_month_mask = ~out["month"].isin(sheet_months)

    if conv_by_level_df is not None and not conv_by_level_df.empty:
        filtered = conv_by_level_df[
            conv_by_level_df["membership_level"].isin(levels)
        ]
        if not filtered.empty:
            totals = (
                filtered.groupby(["month", "channel"], as_index=False)["conversions"]
                .sum()
            )
            for row in totals.itertuples(index=False):
                month, channel, channel_conv = (
                    row.month,
                    row.channel,
                    float(row.conversions),
                )
                if channel_conv <= 0:
                    continue
                mask = ghl_month_mask & (out["month"] == month) & (
                    out["channel"] == channel
                )
                chunk = out.loc[mask]
                if chunk.empty:
                    continue
                total_spend = chunk["spend"].sum()
                if total_spend > 0:
                    weights = chunk["spend"] / total_spend
                    out.loc[mask, "conversions"] = weights * channel_conv
                else:
                    share = channel_conv / len(chunk)
                    out.loc[mask, "conversions"] = share

    spread_pools: list[pd.DataFrame] = []
    if (
        include_other_signups
        and unallocated_conv_df is not None
        and not unallocated_conv_df.empty
    ):
        spread_pools.append(unallocated_conv_df)
    if include_wom_signups and wom_conv_df is not None and not wom_conv_df.empty:
        spread_pools.append(wom_conv_df)

    if spread_pools:
        combined_unalloc = pd.concat(spread_pools, ignore_index=True)
        unalloc = combined_unalloc[
            combined_unalloc["membership_level"].isin(levels)
        ]
        if not unalloc.empty:
            by_month = (
                unalloc.groupby("month", as_index=False)["conversions"].sum()
            )
            for row in by_month.itertuples(index=False):
                month, month_conv = row.month, float(row.conversions)
                if month_conv <= 0:
                    continue
                mask = ghl_month_mask & (out["month"] == month)
                chunk = out.loc[mask]
                if chunk.empty:
                    continue
                total_spend = chunk["spend"].sum()
                if total_spend > 0:
                    weights = chunk["spend"] / total_spend
                    out.loc[mask, "conversions"] += weights * month_conv
                else:
                    share = month_conv / len(chunk)
                    out.loc[mask, "conversions"] += share

    if not preserved.empty:
        out.loc[sheet_mask, "conversions"] = preserved["conversions"]

    return _update_conversion_derived_columns(out)


def _split_month_unallocated_across_channels(
    out: pd.DataFrame,
    month: pd.Timestamp,
    unallocated: float,
    col: str,
) -> None:
    """Add month-level unallocated leads to channel-month totals by attributed share."""
    if unallocated <= 0 or col not in out.columns:
        return
    month_mask = out["month"] == month
    if not month_mask.any():
        return
    google_val = float(
        out.loc[month_mask & (out["channel"] == CHANNEL_GOOGLE), col].sum()
    )
    meta_val = float(
        out.loc[month_mask & (out["channel"] == CHANNEL_META), col].sum()
    )
    attr_total = google_val + meta_val
    if attr_total > 0:
        shares = {
            CHANNEL_GOOGLE: google_val / attr_total,
            CHANNEL_META: meta_val / attr_total,
        }
    else:
        shares = {CHANNEL_GOOGLE: 0.5, CHANNEL_META: 0.5}
    for channel, share in shares.items():
        key_mask = month_mask & (out["channel"] == channel)
        out.loc[key_mask, col] = out.loc[key_mask, col].astype(float) + (
            unallocated * share
        )


def _build_source_channel_month_leads(
    ghl_monthly: pd.DataFrame,
    sheet_leads_monthly: pd.DataFrame,
    sheet_leads_max: pd.Timestamp | None,
    unallocated_leads_by_attr: dict[str, dict[pd.Timestamp, float]] | None = None,
) -> pd.DataFrame:
    """
    Channel-month lead totals from GHL plus sheet baseline (before campaign split).

    Used when spend-weighted row allocation yields zero leads but channel-month
    totals exist — e.g. after sidebar filters or sparse ad rows for a month.
    """
    lead_cols = ("leads", "leads_hear_about", "leads_combined")
    if ghl_monthly.empty and sheet_leads_monthly.empty:
        return pd.DataFrame(columns=["month", "channel", *lead_cols])

    if ghl_monthly.empty:
        out = sheet_leads_monthly[["month", "channel"]].copy()
        sheet_n = sheet_leads_monthly["leads"].astype(float)
        for col in lead_cols:
            out[col] = sheet_n
    else:
        out = ghl_monthly[["month", "channel"]].copy()
        for col in lead_cols:
            out[col] = (
                ghl_monthly[col].astype(float)
                if col in ghl_monthly.columns
                else 0.0
            )

        if not sheet_leads_monthly.empty:
            out = out.set_index(["month", "channel"])
            for row in sheet_leads_monthly.itertuples(index=False):
                month, channel, sheet_leads = row.month, row.channel, float(row.leads)
                if sheet_leads <= 0:
                    continue
                if sheet_leads_max is not None and month > sheet_leads_max:
                    continue
                key = (month, channel)
                force_sheet = sheet_leads_max is not None and month <= sheet_leads_max
                if key not in out.index:
                    out.loc[key, list(lead_cols)] = sheet_leads
                    continue
                for col in lead_cols:
                    if force_sheet or float(out.at[key, col]) <= 0:
                        out.at[key, col] = sheet_leads
            out = out.reset_index()

    if unallocated_leads_by_attr:
        sheet_cutoff = sheet_leads_max
        for col, by_month in unallocated_leads_by_attr.items():
            if col not in out.columns:
                continue
            for month, unallocated in by_month.items():
                if sheet_cutoff is not None and month <= sheet_cutoff:
                    continue
                _split_month_unallocated_across_channels(out, month, unallocated, col)
    return out


def _attribution_leads_column(*, use_hear_about: bool, use_tracker: bool) -> str:
    if use_tracker:
        return "leads"
    if use_hear_about:
        return "leads_hear_about"
    return "leads"


def _unallocated_leads_attr_key(*, use_hear_about: bool, use_tracker: bool) -> str:
    """Key in ``unallocated_leads_by_attr`` for organic (non–paid-attributed) leads."""
    if use_tracker:
        return "leads"
    if use_hear_about:
        return "leads_hear_about"
    return "leads"


def _active_dcs_column(*, use_hear_about: bool, use_tracker: bool) -> str | None:
    if use_tracker:
        return "dcs_tracker"
    if use_hear_about:
        return "dcs_hear_about"
    return None


def build_spend_trend_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """Monthly spend totals only — independent of leads, DCs, and signups aggregation."""
    if df.empty:
        return pd.DataFrame(columns=["month", "spend"])
    return (
        df.groupby("month", as_index=False)["spend"]
        .sum()
        .sort_values("month")
        .reset_index(drop=True)
    )


def _cpl_source_leads_column(*, use_hear_about: bool, use_tracker: bool) -> str:
    return f"{_attribution_leads_column(use_hear_about=use_hear_about, use_tracker=use_tracker)}_cpl"


def build_cpl_trend_monthly(
    df: pd.DataFrame,
    cpl_channel_month_leads: pd.DataFrame,
    *,
    selected_channels: list[str],
    use_hear_about: bool,
    use_tracker: bool,
    include_organic: bool = False,
    unallocated_leads_by_attr: dict[str, dict[pd.Timestamp, float]] | None = None,
) -> pd.DataFrame:
    """
    Monthly spend and leads for CPL trend charts.

    Paid-attributed leads follow sidebar filters (channel, campaign, asset type).
    Organic contacts (unallocated for the active attribution mode) are optional and
    added at month level when ``include_organic`` is True — not channel-filtered.
    Sheet baseline through Jun 2025; GHL attribution from Jul 2025 onward.
    """
    spend = build_spend_trend_monthly(df)
    if df.empty and cpl_channel_month_leads.empty:
        return pd.DataFrame(columns=["month", "spend", "leads"])

    sheet_leads_max = pd.Timestamp(SHEET_LEADS_UNTIL).to_period("M").to_timestamp()
    leads_monthly = pd.DataFrame(columns=["month", "leads"])

    if not df.empty and (use_hear_about or use_tracker):
        work = df.copy()
        cpl_col = _cpl_source_leads_column(
            use_hear_about=use_hear_about, use_tracker=use_tracker
        )
        attr_col = _attribution_leads_column(
            use_hear_about=use_hear_about, use_tracker=use_tracker
        )
        if cpl_col in work.columns:
            work["leads"] = work[cpl_col]
        elif attr_col in work.columns:
            work["leads"] = work[attr_col]
        else:
            work["leads"] = work.get("leads", 0.0)

        leads_monthly = (
            work.groupby("month", as_index=False)["leads"]
            .sum()
            .sort_values("month")
            .reset_index(drop=True)
        )

    if not cpl_channel_month_leads.empty and (use_hear_about or use_tracker):
        leads_monthly = overlay_monthly_leads_for_trends(
            leads_monthly,
            cpl_channel_month_leads,
            selected_channels=selected_channels,
            use_hear_about=use_hear_about,
            use_tracker=use_tracker,
        )

    if include_organic and unallocated_leads_by_attr and (use_hear_about or use_tracker):
        organic_key = _unallocated_leads_attr_key(
            use_hear_about=use_hear_about, use_tracker=use_tracker
        )
        organic_by_month = unallocated_leads_by_attr.get(organic_key, {})
        if organic_by_month:
            if leads_monthly.empty:
                leads_monthly = pd.DataFrame(
                    {
                        "month": [
                            pd.Timestamp(m).to_period("M").to_timestamp()
                            for m in organic_by_month
                        ],
                        "leads": 0.0,
                    }
                )
            leads_monthly = leads_monthly.copy()
            leads_monthly["month"] = (
                pd.to_datetime(leads_monthly["month"]).dt.to_period("M").dt.to_timestamp()
            )
            by_month = leads_monthly.set_index("month")
            for month, organic_n in organic_by_month.items():
                month_ts = pd.Timestamp(month).to_period("M").to_timestamp()
                if month_ts <= sheet_leads_max:
                    continue
                organic_n = float(organic_n)
                if organic_n <= 0:
                    continue
                if month_ts in by_month.index:
                    by_month.at[month_ts, "leads"] = (
                        float(by_month.at[month_ts, "leads"]) + organic_n
                    )
                else:
                    by_month.loc[month_ts, "leads"] = organic_n
            leads_monthly = (
                by_month.reset_index().sort_values("month").reset_index(drop=True)
            )

    out = spend.merge(leads_monthly, on="month", how="outer").fillna(0.0)
    return _smooth_ghl_crm_dump_month_cpl(out[["month", "spend", "leads"]])


def build_signups_trend_monthly(
    *,
    since: str,
    until: str,
    sheet_signup_totals: dict[pd.Timestamp, float],
    ghl_signups_by_month: dict[pd.Timestamp, float],
) -> pd.DataFrame:
    """
    Org-wide monthly signups for the Signups Over Time chart.

    Through May 2025: Digital Cross-Channel Tracker **GRAND TOTAL New Members** row.
    From Jun 2025: GHL committed contacts by **Sign Up Date** (all members, not
    campaign- or attribution-filtered).
    """
    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()
    sheet_cutoff = pd.Timestamp(SHEETS_SIGNUPS_UNTIL).to_period("M").to_timestamp()
    ghl_since_month = pd.Timestamp(GHL_SIGNUPS_SINCE).to_period("M").to_timestamp()

    rows: list[dict[str, Any]] = []
    for period in pd.period_range(since_month, until_month, freq="M"):
        month = period.to_timestamp()
        if month <= sheet_cutoff:
            conversions = float(sheet_signup_totals.get(month, 0.0))
        elif month >= ghl_since_month:
            conversions = float(ghl_signups_by_month.get(month, 0.0))
        else:
            conversions = 0.0
        rows.append({"month": month, "conversions": conversions})

    if not rows:
        return pd.DataFrame(columns=["month", "conversions"])
    return pd.DataFrame(rows).sort_values("month").reset_index(drop=True)


def build_dcs_trend_monthly(
    *,
    since: str,
    until: str,
    sheet_dcs_totals: dict[pd.Timestamp, float],
    ghl_dcs_by_month: dict[pd.Timestamp, float],
) -> pd.DataFrame:
    """
    Org-wide monthly discovery calls for the DCs Over Time chart.

    Through May 2025: Digital Cross-Channel Tracker **Calls completed** row.
    From Jun 2025: GHL calendar **meetings** (``startTime``) on configured
    discovery-call calendar IDs (all meetings, not campaign-filtered).
    """
    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()
    sheet_cutoff = pd.Timestamp(SHEETS_DCS_UNTIL).to_period("M").to_timestamp()
    ghl_since_month = pd.Timestamp(GHL_DCS_SINCE).to_period("M").to_timestamp()

    rows: list[dict[str, Any]] = []
    for period in pd.period_range(since_month, until_month, freq="M"):
        month = period.to_timestamp()
        if month <= sheet_cutoff:
            dcs = float(sheet_dcs_totals.get(month, 0.0))
        elif month >= ghl_since_month:
            dcs = float(ghl_dcs_by_month.get(month, 0.0))
        else:
            dcs = 0.0
        rows.append({"month": month, "dcs": dcs})

    if not rows:
        return pd.DataFrame(columns=["month", "dcs"])
    return pd.DataFrame(rows).sort_values("month").reset_index(drop=True)


def _smooth_ghl_crm_dump_month_cpl(cpl_monthly: pd.DataFrame) -> pd.DataFrame:
    """Replace Jul 2025 spend/leads with the mean of Jun and Aug (legacy CRM import)."""
    if cpl_monthly.empty:
        return cpl_monthly

    july = GHL_CRM_DUMP_MONTH
    june = july - pd.DateOffset(months=1)
    august = july + pd.DateOffset(months=1)
    months = {pd.Timestamp(m).to_period("M").to_timestamp() for m in cpl_monthly["month"]}
    if july not in months or june not in months or august not in months:
        return cpl_monthly

    out = cpl_monthly.copy()
    out["month"] = pd.to_datetime(out["month"]).dt.to_period("M").dt.to_timestamp()
    by_month = out.set_index("month")
    for col in ("spend", "leads"):
        if col not in out.columns:
            continue
        smoothed = (float(by_month.at[june, col]) + float(by_month.at[august, col])) / 2.0
        out.loc[out["month"] == july, col] = smoothed
    return out.sort_values("month").reset_index(drop=True)


class TrendChartMonthlies:
    """Monthly aggregates sliced for isolated trend charts (no shared mutable frame)."""

    __slots__ = ("spend", "cpl", "dcs", "signups")

    def __init__(
        self,
        spend: pd.DataFrame,
        cpl: pd.DataFrame,
        dcs: pd.DataFrame,
        signups: pd.DataFrame,
    ) -> None:
        self.spend = spend
        self.cpl = cpl
        self.dcs = dcs
        self.signups = signups


def build_trend_chart_monthlies(
    df: pd.DataFrame,
    channel_month_leads: pd.DataFrame,
    cpl_channel_month_leads: pd.DataFrame,
    unallocated_leads_by_attr: dict[str, dict[pd.Timestamp, float]] | None,
    *,
    since: str,
    until: str,
    sheet_signup_totals: dict[pd.Timestamp, float],
    ghl_signups_by_month: dict[pd.Timestamp, float],
    sheet_dcs_totals: dict[pd.Timestamp, float],
    ghl_dcs_by_month: dict[pd.Timestamp, float],
    selected_channels: list[str],
    use_hear_about: bool,
    use_tracker: bool,
    include_organic: bool = False,
) -> TrendChartMonthlies:
    """
    Build separate monthly series for spend, CPL, DCs, and signups trend charts.

    Lead overlay applies only to the CPL series so DC and signup trends are not
    affected by lead reconciliation. Signups and DCs use org-wide tracker + GHL totals.
    """
    spend = build_spend_trend_monthly(df)
    cpl = build_cpl_trend_monthly(
        df,
        cpl_channel_month_leads,
        selected_channels=selected_channels,
        use_hear_about=use_hear_about,
        use_tracker=use_tracker,
        include_organic=include_organic,
        unallocated_leads_by_attr=unallocated_leads_by_attr,
    )
    signups = build_signups_trend_monthly(
        since=since,
        until=until,
        sheet_signup_totals=sheet_signup_totals,
        ghl_signups_by_month=ghl_signups_by_month,
    )
    dcs = build_dcs_trend_monthly(
        since=since,
        until=until,
        sheet_dcs_totals=sheet_dcs_totals,
        ghl_dcs_by_month=ghl_dcs_by_month,
    )

    return TrendChartMonthlies(spend=spend, cpl=cpl, dcs=dcs, signups=signups)


def overlay_monthly_leads_for_trends(
    monthly: pd.DataFrame,
    channel_month_leads: pd.DataFrame,
    *,
    selected_channels: list[str],
    use_hear_about: bool,
    use_tracker: bool,
) -> pd.DataFrame:
    """Prefer channel-month lead totals when row sums under-count (filters or attribution)."""
    if monthly.empty or channel_month_leads.empty or (not use_hear_about and not use_tracker):
        return monthly

    col = _attribution_leads_column(
        use_hear_about=use_hear_about, use_tracker=use_tracker
    )
    if col not in channel_month_leads.columns:
        col = "leads"

    totals = channel_month_leads[
        channel_month_leads["channel"].isin(selected_channels)
    ]
    if totals.empty:
        return monthly

    by_month = totals.groupby("month", as_index=False)[col].sum()
    source = by_month.set_index("month")[col].to_dict()

    out = monthly.copy()
    for idx, row in out.iterrows():
        month = row["month"]
        source_leads = float(source.get(month) or 0)
        if source_leads <= 0:
            continue
        row_leads = float(row["leads"] or 0)
        if source_leads <= row_leads:
            continue
        out.at[idx, "leads"] = source_leads
    return out


def channel_month_leads_total(
    channel_month_leads: pd.DataFrame,
    *,
    since_month: pd.Timestamp,
    until_month: pd.Timestamp,
    selected_channels: list[str],
    use_hear_about: bool,
    use_tracker: bool,
) -> float:
    """Sum channel-month lead totals for a date range and channel selection."""
    if channel_month_leads.empty or (not use_hear_about and not use_tracker):
        return 0.0

    col = _attribution_leads_column(
        use_hear_about=use_hear_about, use_tracker=use_tracker
    )
    if col not in channel_month_leads.columns:
        col = "leads"

    subset = channel_month_leads[
        channel_month_leads["channel"].isin(selected_channels)
        & (channel_month_leads["month"] >= since_month)
        & (channel_month_leads["month"] <= until_month)
    ]
    if subset.empty:
        return 0.0
    return float(subset[col].sum())


def apply_dashboard_ghl_attribution(
    df: pd.DataFrame,
    *,
    use_hear_about: bool,
    use_tracker: bool,
    conv_by_level_df: pd.DataFrame,
    tracker_conv_by_level_df: pd.DataFrame,
    combined_conv_by_level_df: pd.DataFrame,
    selected_levels: list[str],
    unallocated_conv_df: pd.DataFrame | None = None,
    tracker_unallocated_conv_df: pd.DataFrame | None = None,
    combined_unallocated_conv_df: pd.DataFrame | None = None,
    wom_conv_df: pd.DataFrame | None = None,
    include_wom_signups: bool = True,
    include_other_signups: bool = False,
    sheet_signup_months: set[pd.Timestamp] | None = None,
) -> pd.DataFrame:
    """
    Apply hear-about or tracker attribution to leads, DCs, and signups.

    Tracker takes precedence when both checkboxes are on. Hear-about uses only
    self-reported Google / FB/IG mapping; tracker uses tag/pixel signals.
    Signups use strict channel counts unless Other (or WOM) inclusion is enabled.
    """
    work = df.copy()
    sheet_months = sheet_signup_months or set()
    if "month" not in work.columns:
        work["month"] = work["date"].dt.to_period("M").dt.to_timestamp()

    if use_tracker:
        conv_df = tracker_conv_by_level_df
        unalloc_df = tracker_unallocated_conv_df
        use_wom = False
        if "dcs_tracker" in work.columns:
            work["dcs"] = work["dcs_tracker"]
    elif use_hear_about:
        if "leads_hear_about" in work.columns:
            work["leads"] = work["leads_hear_about"]
        if "dcs_hear_about" in work.columns:
            work["dcs"] = work["dcs_hear_about"]
        conv_df = conv_by_level_df
        unalloc_df = unallocated_conv_df
        use_wom = include_wom_signups
    else:
        conv_df = pd.DataFrame(columns=CONV_BY_LEVEL_COLUMNS)
        unalloc_df = pd.DataFrame(columns=UNALLOCATED_CONV_COLUMNS)
        use_wom = False
        ghl_mask = ~work["month"].isin(sheet_months)
        work.loc[ghl_mask, "leads"] = 0.0
        work.loc[ghl_mask, "dcs"] = 0.0

    work["cpl"] = work.apply(
        lambda r: r["spend"] / r["leads"] if r["leads"] and r["leads"] > 0 else pd.NA,
        axis=1,
    )
    work["cpdc"] = work.apply(
        lambda r: r["spend"] / r["dcs"] if r["dcs"] and r["dcs"] > 0 else pd.NA,
        axis=1,
    )
    return apply_membership_conversion_filter(
        work,
        conv_df,
        selected_levels,
        unallocated_conv_df=unalloc_df,
        wom_conv_df=wom_conv_df if use_wom else None,
        include_wom_signups=use_wom,
        include_other_signups=include_other_signups,
        sheet_signup_months=sheet_signup_months,
    )


def _update_conversion_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["cac"] = out.apply(
        lambda r: r["spend"] / r["conversions"]
        if r["conversions"] and r["conversions"] > 0
        else pd.NA,
        axis=1,
    )
    out["lead_to_patient_pct"] = out.apply(
        lambda r: (r["conversions"] / r["dcs"] * 100.0)
        if r["dcs"] and r["dcs"] > 0
        else pd.NA,
        axis=1,
    )
    return out


def _add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["spend_month"] = out["spend"]
    out["creative"] = ""
    out["reach"] = pd.NA

    out["cpc"] = out.apply(
        lambda r: r["spend"] / r["clicks"] if r["clicks"] and r["clicks"] > 0 else pd.NA,
        axis=1,
    )
    out["cpl"] = out.apply(
        lambda r: r["spend"] / r["leads"] if r["leads"] and r["leads"] > 0 else pd.NA,
        axis=1,
    )
    out["cpdc"] = out.apply(
        lambda r: r["spend"] / r["dcs"] if r["dcs"] and r["dcs"] > 0 else pd.NA,
        axis=1,
    )
    out["cac"] = out.apply(
        lambda r: r["spend"] / r["conversions"]
        if r["conversions"] and r["conversions"] > 0
        else pd.NA,
        axis=1,
    )
    out["lead_to_patient_pct"] = out.apply(
        lambda r: (r["conversions"] / r["dcs"] * 100.0)
        if r["dcs"] and r["dcs"] > 0
        else pd.NA,
        axis=1,
    )
    return out


def load_live_campaign_data(
    since: str | None = None,
    until: str | None = None,
) -> tuple[
    pd.DataFrame,
    list[str],
    dict[str, int],
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    set[pd.Timestamp],
    pd.DataFrame,
    pd.DataFrame,
    dict[str, dict[pd.Timestamp, float]],
    dict[pd.Timestamp, float],
    dict[pd.Timestamp, float],
    dict[pd.Timestamp, float],
    dict[pd.Timestamp, float],
    pd.DataFrame,
]:
    """
    Fetch and normalize paid-media rows for the Digital Channel Dashboard.

    Returns:
        (dataframe, notes, GHL lead summary, hear conv_by_level, hear unallocated,
         wom_conv, tracker conv_by_level, tracker unallocated,
         combined conv_by_level, combined unallocated, sheet_signup_months,
         channel_month_leads, cpl_channel_month_leads, unallocated_leads_by_attr,
         sheet_signup_totals, ghl_signups_by_month, sheet_dcs_totals, ghl_dcs_by_month,
         ghl_leads_org_by_month, ghl_signups_by_level_df)
    """
    today = date.today()
    until_eff = until or (today - timedelta(days=1)).isoformat()
    since_eff = since or DEFAULT_SINCE
    notes: list[str] = [
        f"Google Ads + Meta insights: {since_eff} → {until_eff} (Meta excludes today).",
        "GHL leads: new GHL contacts (date added) from live APIs where available; "
        f"earlier months use {SPREADSHEET_NAME} Data tab (allocated by spend share).",
        "GHL DCs: discovery-call calendar meetings (startTime); hear-about or "
        "tag/pixel attribution selected in sidebar.",
        f"Signups (GHL): Committed? = Yes, Sign Up Date from {GHL_SIGNUPS_SINCE} onward; "
        "hear-about **or** tracker (not combined) for leads, DCs, and signups.",
        "Leads (GHL tracker): new contacts with meta lead tag / pixel or "
        f"{GOOGLE_LEAD_TAG!r} / gaClientId.",
        "Leads (GHL hear-about): new contacts whose hear-about maps to Google/Meta.",
        f"Signups (sheet): GRAND TOTAL New Members through {SHEETS_SIGNUPS_UNTIL} from "
        "Digital Cross-Channel Tracker workbooks.",
        "Campaign-level signups are allocated by spend share within channel-month.",
    ]

    meta_daily = pd.DataFrame(columns=_META_EMPTY_DAILY_COLUMNS)
    with ThreadPoolExecutor(max_workers=3) as pool:
        gads_future = pool.submit(
            fetch_google_ads_campaign_daily_cached, since_eff, until_eff
        )
        meta_future = pool.submit(
            fetch_meta_campaign_daily_cached, since_eff, until_eff
        )
        ghl_future = pool.submit(fetch_ghl_channel_monthly, since_eff, until_eff)
        gads_daily = gads_future.result()
        try:
            meta_daily = meta_future.result()
            partial_meta_errors = meta_daily.attrs.get("meta_partial_errors") or []
            if partial_meta_errors:
                notes.append(
                    "Meta insights partially loaded (some date chunks failed); "
                    "FB/IG spend may be incomplete."
                )
                for err in partial_meta_errors[:3]:
                    notes.append(f"Meta chunk error: {err}")
                if len(partial_meta_errors) > 3:
                    notes.append(
                        f"… and {len(partial_meta_errors) - 3} more Meta chunk errors."
                    )
        except Exception as exc:
            notes.append(f"Meta campaign insights skipped: {exc}")
            meta_daily = pd.DataFrame(columns=_META_EMPTY_DAILY_COLUMNS)
        (
            ghl_monthly,
            lead_summary,
            ghl_notes,
            conv_by_level_df,
            unallocated_conv_df,
            wom_conv_df,
            tracker_conv_by_level_df,
            tracker_unallocated_conv_df,
            combined_conv_by_level_df,
            combined_unallocated_conv_df,
            unallocated_dcs_by_attr,
            unallocated_leads_by_attr,
            ghl_signups_by_month,
            ghl_dcs_by_month,
            ghl_leads_org_by_month,
            ghl_signups_by_level_df,
        ) = ghl_future.result()

    daily = pd.concat([gads_daily, meta_daily], ignore_index=True)

    monthly_ads = _aggregate_to_month_end(daily)
    notes.extend(ghl_notes)
    notes.append(
        "GHL new contacts in range: "
        f"{lead_summary.get('total_new_contacts', 0):,} total · "
        f"{lead_summary.get('meta_leads', 0):,} Meta (tracker) · "
        f"{lead_summary.get('google_leads', 0):,} Google (tracker) · "
        f"{lead_summary.get('meta_leads_hear_about', 0):,} Meta (hear-about) · "
        f"{lead_summary.get('google_leads_hear_about', 0):,} Google (hear-about) · "
        f"{lead_summary.get('google_leads_combined', 0):,} Google (combined OR)."
    )

    sheet_totals, sheet_months, sheet_notes = _fetch_tracker_grand_total_signups(
        since_eff, until_eff
    )
    notes.extend(sheet_notes)
    if sheet_totals:
        notes.append(
            f"Tracker sheet signups loaded for {len(sheet_totals)} month(s) "
            f"({int(sum(sheet_totals.values())):,} total)."
        )

    sheet_dcs_totals, sheet_dcs_months, sheet_dcs_notes = _fetch_tracker_calls_completed(
        since_eff, until_eff
    )
    notes.extend(sheet_dcs_notes)
    if sheet_dcs_totals:
        notes.append(
            f"Tracker sheet DCs loaded for {len(sheet_dcs_totals)} month(s) "
            f"({int(sum(sheet_dcs_totals.values())):,} Calls completed total)."
        )

    if monthly_ads.empty:
        return (
            pd.DataFrame(columns=DATA_COLUMNS),
            notes,
            lead_summary,
            conv_by_level_df,
            unallocated_conv_df,
            wom_conv_df,
            tracker_conv_by_level_df,
            tracker_unallocated_conv_df,
            combined_conv_by_level_df,
            combined_unallocated_conv_df,
            sheet_months,
            pd.DataFrame(columns=["month", "channel", "leads", "leads_hear_about", "leads_combined"]),
            pd.DataFrame(columns=["month", "channel", "leads", "leads_hear_about", "leads_combined"]),
            unallocated_leads_by_attr,
            sheet_totals,
            ghl_signups_by_month,
            sheet_dcs_totals,
            ghl_dcs_by_month,
            ghl_leads_org_by_month,
            ghl_signups_by_level_df,
        )

    if not ghl_monthly.empty and sheet_months:
        ghl_monthly = ghl_monthly.copy()
        ghl_monthly.loc[ghl_monthly["month"].isin(sheet_months), "conversions"] = 0.0

    sheet_leads_monthly, sheet_leads_max, sheet_lead_notes = (
        _sheet_leads_by_month_channel(since_eff, until_eff)
    )
    notes.extend(sheet_lead_notes)

    if sheet_leads_max is not None and not ghl_monthly.empty:
        ghl_monthly = ghl_monthly.copy()
        sheet_lead_months = {
            month
            for month in ghl_monthly["month"].dropna().unique()
            if month <= sheet_leads_max
        }
        if sheet_lead_months:
            ghl_monthly.loc[
                ghl_monthly["month"].isin(sheet_lead_months),
                ["leads", "leads_hear_about", "leads_combined"],
            ] = 0.0
            notes.append(
                f"GHL lead attribution suppressed for {len(sheet_lead_months)} sheet month(s) "
                f"through {sheet_leads_max.date()}."
            )

    merged = _allocate_ghl_metrics(monthly_ads, ghl_monthly)
    merged = _allocate_ghl_column(
        merged,
        ghl_monthly,
        ghl_column="leads_hear_about",
        out_column="leads_hear_about",
    )
    merged = _allocate_ghl_column(
        merged,
        ghl_monthly,
        ghl_column="leads_combined",
        out_column="leads_combined",
    )
    merged = _allocate_ghl_column(
        merged,
        ghl_monthly,
        ghl_column="dcs_tracker",
        out_column="dcs_tracker",
    )
    merged = _snapshot_cpl_lead_columns(merged)
    if unallocated_dcs_by_attr:
        for col, by_month in unallocated_dcs_by_attr.items():
            if col in merged.columns and by_month:
                merged = _allocate_unallocated_monthly_metric(
                    merged, by_month, column=col
                )
    if unallocated_leads_by_attr:
        for col, by_month in unallocated_leads_by_attr.items():
            if col in merged.columns and by_month:
                merged = _allocate_unallocated_monthly_metric(
                    merged, by_month, column=col
                )
    merged = _allocate_monthly_signup_totals(merged, sheet_totals)

    if not sheet_leads_monthly.empty:
        merged = _apply_sheet_lead_baseline(
            merged, sheet_leads_monthly, sheet_leads_max
        )
        filled_months = sheet_leads_monthly["month"].nunique()
        notes.append(
            f"Sheet lead baseline applied through {sheet_leads_max.date() if sheet_leads_max is not None else 'sheet end'} "
            f"({filled_months} month(s))."
        )

    sheet_dcs_monthly, sheet_dcs_max, sheet_dc_notes = _sheet_dcs_by_month_channel(
        since_eff, until_eff
    )
    notes.extend(sheet_dc_notes)
    if not sheet_dcs_monthly.empty:
        merged = _apply_sheet_dc_baseline(
            merged, sheet_dcs_monthly, sheet_dcs_max, column="dcs_hear_about"
        )
        merged = _apply_sheet_dc_baseline(
            merged, sheet_dcs_monthly, sheet_dcs_max, column="dcs_tracker"
        )
        filled_dc_months = sheet_dcs_monthly["month"].nunique()
        notes.append(
            f"Sheet DC baseline applied to {filled_dc_months} month(s) where live GHL "
            "DC counts were zero."
        )

    merged = _add_derived_columns(merged)

    channel_month_leads = _build_source_channel_month_leads(
        ghl_monthly,
        sheet_leads_monthly,
        sheet_leads_max,
        unallocated_leads_by_attr,
    )
    cpl_channel_month_leads = _build_source_channel_month_leads(
        ghl_monthly,
        sheet_leads_monthly,
        sheet_leads_max,
        None,
    )

    for col in DATA_COLUMNS:
        if col not in merged.columns:
            merged[col] = pd.NA

    result = merged[DATA_COLUMNS].copy()
    if "leads_hear_about" in merged.columns:
        result["leads_hear_about"] = merged["leads_hear_about"].values
    if "leads_combined" in merged.columns:
        result["leads_combined"] = merged["leads_combined"].values
    if "dcs_hear_about" in merged.columns:
        result["dcs_hear_about"] = merged["dcs_hear_about"].values
    if "dcs_tracker" in merged.columns:
        result["dcs_tracker"] = merged["dcs_tracker"].values
    for col in ("leads_cpl", "leads_hear_about_cpl", "leads_combined_cpl"):
        if col in merged.columns:
            result[col] = merged[col].values
    result["month"] = result["date"].dt.to_period("M").dt.to_timestamp()
    return (
        result,
        notes,
        lead_summary,
        conv_by_level_df,
        unallocated_conv_df,
        wom_conv_df,
        tracker_conv_by_level_df,
        tracker_unallocated_conv_df,
        combined_conv_by_level_df,
        combined_unallocated_conv_df,
        sheet_months,
        channel_month_leads,
        cpl_channel_month_leads,
        unallocated_leads_by_attr,
        sheet_totals,
        ghl_signups_by_month,
        sheet_dcs_totals,
        ghl_dcs_by_month,
        ghl_leads_org_by_month,
        ghl_signups_by_level_df,
    )


def load_dashboard_bundle(
    campaign_since: str,
    until: str,
    *,
    funnel_since: str | None = None,
    funnel_until: str | None = None,
) -> tuple[
    pd.DataFrame,
    list[str],
    dict[str, int],
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    set[pd.Timestamp],
    pd.DataFrame,
    pd.DataFrame,
    dict[str, dict[pd.Timestamp, float]],
    dict[pd.Timestamp, float],
    dict[pd.Timestamp, float],
    dict[pd.Timestamp, float],
    dict[pd.Timestamp, float],
    pd.DataFrame,
    pd.DataFrame,
    list[str],
]:
    """Load campaign metrics and funnel-over-time chart from one shared fetch pass."""
    from funnel_over_time_data import load_funnel_over_time

    (
        df,
        notes,
        lead_summary,
        conv_by_level_df,
        unallocated_conv_df,
        wom_conv_df,
        tracker_conv_by_level_df,
        tracker_unallocated_conv_df,
        combined_conv_by_level_df,
        combined_unallocated_conv_df,
        sheet_months,
        channel_month_leads,
        cpl_channel_month_leads,
        unallocated_leads_by_attr,
        sheet_signup_totals,
        ghl_signups_by_month,
        sheet_dcs_totals,
        ghl_dcs_by_month,
        ghl_leads_org_by_month,
        ghl_signups_by_level_df,
    ) = load_live_campaign_data(campaign_since, until)

    funnel_df, funnel_notes = load_funnel_over_time(
        funnel_since or campaign_since,
        funnel_until or until,
        ghl_leads_by_month=ghl_leads_org_by_month,
        ghl_dcs_by_month=ghl_dcs_by_month,
        ghl_signups_by_month=ghl_signups_by_month,
    )
    return (
        df,
        notes,
        lead_summary,
        conv_by_level_df,
        unallocated_conv_df,
        wom_conv_df,
        tracker_conv_by_level_df,
        tracker_unallocated_conv_df,
        combined_conv_by_level_df,
        combined_unallocated_conv_df,
        sheet_months,
        channel_month_leads,
        cpl_channel_month_leads,
        unallocated_leads_by_attr,
        sheet_signup_totals,
        ghl_signups_by_month,
        sheet_dcs_totals,
        ghl_dcs_by_month,
        ghl_leads_org_by_month,
        ghl_signups_by_level_df,
        funnel_df,
        funnel_notes,
    )


__all__ = [
    "MEMBERSHIP_LEVELS",
    "GHL_SIGNUPS_SINCE",
    "GHL_LEADS_SINCE",
    "SHEETS_SIGNUPS_UNTIL",
    "GHL_DCS_SINCE",
    "SHEETS_DCS_UNTIL",
    "SHEET_LEADS_UNTIL",
    "GHL_ATTRIBUTION_HEAR_ABOUT",
    "GHL_ATTRIBUTION_TRACKER",
    "GHL_ATTRIBUTION_OPTIONS",
    "apply_dashboard_ghl_attribution",
    "apply_membership_conversion_filter",
    "channel_month_leads_total",
    "clear_dashboard_disk_cache",
    "clear_ghl_leads_day_cache",
    "default_dashboard_since",
    "load_dashboard_bundle",
    "load_live_campaign_data",
    "TrendChartMonthlies",
    "build_cpl_trend_monthly",
    "build_dcs_trend_monthly",
    "build_ghl_signups_by_level_monthly",
    "build_signups_trend_monthly",
    "build_spend_trend_monthly",
    "build_trend_chart_monthlies",
    "monthly_campaign_summary",
    "overlay_monthly_leads_for_trends",
    "scorecard_metrics",
    "DEFAULT_SINCE",
    "DEFAULT_DASHBOARD_MONTHS",
    "LIVE_DATA_REVISION",
]
