"""
Build Digital Channel Dashboard rows from live Google Ads, Meta, and GoHighLevel APIs.

Replaces the Google Sheet **Data** tab as the source of truth. Metrics mapping:

- **Spend / clicks / impressions** — Google Ads & Meta campaign insights (daily → month-end rows).
- **Leads** — Meta lead actions; Google Ads ``metrics.conversions`` (discovery-call conversions).
- **DCs** — Google Ads conversions by month; Meta = GHL Facebook/Instagram contacts (date added),
  allocated to campaigns by spend share within channel-month.
- **Conversions (new patients)** — GHL committed members with Sign Up Date in range, classified
  by *How did you hear about us?*, allocated by spend share within channel-month.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd
from facebook_business.exceptions import FacebookRequestError
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

from digital_channel_sheets import DATA_COLUMNS, monthly_campaign_summary, scorecard_metrics
from ghl_client import (
    HEAR_ABOUT_US_FIELD_NAME,
    classify_hear_about_wom_vs_google,
    contact_custom_field_value,
    fetch_facebook_instagram_conversions,
    fetch_signup_date_range_committed_yes_contacts,
    resolve_hear_about_us_custom_field_id,
)
from google_ads_ghl_paid_cohort import (
    _google_ads_customer_id,
    _google_ads_login_customer_id,
    _google_ads_yaml_path,
)
from meta_client import _init_api, _lead_count_from_actions, _parse_float

CHANNEL_GOOGLE = "Google Ads"
CHANNEL_META = "FB/IG"

DEFAULT_SINCE = "2024-01-01"

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
                        "leads": float(row.metrics.conversions or 0),
                        "dcs": float(row.metrics.conversions or 0),
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


def fetch_meta_campaign_daily(since: str, until: str) -> pd.DataFrame:
    """Daily campaign metrics from Meta (spend, clicks, lead actions)."""
    account = _init_api()
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
    try:
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
                    "leads": _lead_count_from_actions(data.get("actions")),
                    "dcs": 0.0,
                }
            )
    except FacebookRequestError as exc:
        body = getattr(exc, "body", None) or str(exc)
        raise RuntimeError(f"Meta API error: {body}") from exc

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


def fetch_ghl_channel_monthly(since: str, until: str) -> tuple[pd.DataFrame, list[str]]:
    """
    Channel-month GHL funnel metrics not available at campaign level in ads APIs.

    Returns a frame with columns: month, channel, dcs, conversions
    """
    notes: list[str] = []

    # Meta DCs proxy: FB/IG contacts by date added
    meta_dcs_by_month: dict[str, float] = {}
    try:
        fb_ig = fetch_facebook_instagram_conversions(
            since, until, field_name=HEAR_ABOUT_US_FIELD_NAME
        )
        if fb_ig.get("truncated_pages"):
            notes.append(
                "GHL Facebook/Instagram search hit pagination cap; DC counts may be low."
            )
        for day in fb_ig.get("daily") or []:
            ds = (day.get("date_start") or "")[:10]
            if not ds:
                continue
            month = pd.Timestamp(ds).to_period("M").to_timestamp()
            meta_dcs_by_month[month] = meta_dcs_by_month.get(month, 0.0) + float(
                day.get("total") or 0
            )
    except Exception as exc:
        notes.append(f"GHL Facebook/Instagram DCs skipped: {exc}")

    # New patients: committed + sign-up date + hear-about channel
    conv_by_month_channel: dict[tuple[pd.Timestamp, str], float] = {}
    try:
        hear_id = resolve_hear_about_us_custom_field_id()
        signup = fetch_signup_date_range_committed_yes_contacts(since, until)
        if signup.get("truncated_pages"):
            notes.append(
                "GHL sign-up date search hit pagination cap; new-patient counts may be low."
            )
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
            hear = contact_custom_field_value(contact, hear_id)
            channel = _ghl_channel_for_hear_about(hear)
            if channel is None:
                continue
            key = (month, channel)
            conv_by_month_channel[key] = conv_by_month_channel.get(key, 0.0) + 1.0
    except Exception as exc:
        notes.append(f"GHL new-patient counts skipped: {exc}")

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
                "dcs": meta_dcs_by_month.get(month, 0.0),
                "conversions": conv_by_month_channel.get((month, CHANNEL_META), 0.0),
            }
        )
        records.append(
            {
                "month": month,
                "channel": CHANNEL_GOOGLE,
                "dcs": 0.0,
                "conversions": conv_by_month_channel.get((month, CHANNEL_GOOGLE), 0.0),
            }
        )

    return pd.DataFrame(records), notes


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
    """Spread channel-month GHL DCs & conversions across campaigns by spend share."""
    if df.empty:
        return df

    out = df.copy()
    out["conversions"] = 0.0

    for (month, channel), ghl_row in ghl_monthly.groupby(["month", "channel"]):
        mask = (out["month"] == month) & (out["channel"] == channel)
        chunk = out.loc[mask]
        if chunk.empty:
            continue

        total_spend = chunk["spend"].sum()
        channel_dcs = float(ghl_row["dcs"].sum())
        channel_conv = float(ghl_row["conversions"].sum())

        if channel == CHANNEL_GOOGLE:
            # Google DCs already come from Ads conversions at campaign level.
            pass
        elif channel_dcs > 0:
            if total_spend > 0:
                weights = chunk["spend"] / total_spend
                out.loc[mask, "dcs"] = weights * channel_dcs
            else:
                share = channel_dcs / len(chunk)
                out.loc[mask, "dcs"] = share

        if channel_conv > 0:
            if total_spend > 0:
                weights = chunk["spend"] / total_spend
                out.loc[mask, "conversions"] = weights * channel_conv
            else:
                share = channel_conv / len(chunk)
                out.loc[mask, "conversions"] = share

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
) -> tuple[pd.DataFrame, list[str]]:
    """
    Fetch and normalize paid-media rows for the Digital Channel Dashboard.

    Returns:
        (dataframe matching DATA_COLUMNS schema, list of warning/info notes)
    """
    today = date.today()
    until_eff = until or (today - timedelta(days=1)).isoformat()
    since_eff = since or DEFAULT_SINCE
    notes: list[str] = [
        f"Google Ads + Meta insights: {since_eff} → {until_eff} (Meta excludes today).",
        "GHL DCs (Meta): Facebook/Instagram contacts by date added.",
        "GHL conversions: committed members with Sign Up Date + hear-about attribution.",
        "Campaign-level GHL metrics are allocated by spend share within channel-month.",
    ]

    gads_daily = fetch_google_ads_campaign_daily(since_eff, until_eff)
    meta_daily = fetch_meta_campaign_daily(since_eff, until_eff)
    daily = pd.concat([gads_daily, meta_daily], ignore_index=True)

    monthly_ads = _aggregate_to_month_end(daily)
    ghl_monthly, ghl_notes = fetch_ghl_channel_monthly(since_eff, until_eff)
    notes.extend(ghl_notes)

    if monthly_ads.empty:
        return pd.DataFrame(columns=DATA_COLUMNS), notes

    merged = _allocate_ghl_metrics(monthly_ads, ghl_monthly)
    merged = _add_derived_columns(merged)

    for col in DATA_COLUMNS:
        if col not in merged.columns:
            merged[col] = pd.NA

    result = merged[DATA_COLUMNS].copy()
    result["month"] = result["date"].dt.to_period("M").dt.to_timestamp()
    return result, notes


__all__ = [
    "load_live_campaign_data",
    "monthly_campaign_summary",
    "scorecard_metrics",
    "DEFAULT_SINCE",
]
