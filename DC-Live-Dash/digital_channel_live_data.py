"""
Build Digital Channel Dashboard rows from live Google Ads, Meta, and GoHighLevel APIs.

Replaces the Google Sheet **Data** tab as the source of truth. Metrics mapping:

- **Spend / clicks / impressions** — Google Ads & Meta campaign insights (daily → month-end rows).
- **Leads** — GHL new contacts (``dateAdded``): Meta = ``meta lead`` tag and/or Meta pixel
  (``fbp`` / ``fbc``); Google = ``dc thru g-ad`` tag and/or Google Tag (``gaClientId``).
  Campaign rows receive channel-month totals allocated by spend share.
- **DCs** — Google Ads conversions by month; Meta = GHL Facebook/Instagram contacts (date added),
  allocated to campaigns by spend share within channel-month.
- **Conversions (new patients)** — GHL committed members with Sign Up Date in range, classified
  by *How did you hear about us?*, allocated by spend share within channel-month.
"""

from __future__ import annotations

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
    load_contacts_for_calendar_day,
    resolve_hear_about_us_custom_field_id,
)
from google_ads_ghl_paid_cohort import (
    _google_ads_customer_id,
    _google_ads_login_customer_id,
    _google_ads_yaml_path,
)
from meta_client import _init_api, _parse_float

CHANNEL_GOOGLE = "Google Ads"
CHANNEL_META = "FB/IG"

MEMBERSHIP_LEVELS = ("Standard", "Silver", "Gold", "Platinum")
CONV_BY_LEVEL_COLUMNS = ["month", "channel", "membership_level", "conversions"]


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

DEFAULT_SINCE = "2025-01-01"
_GHL_LEADS_CACHE_DIR = _PROJECT_ROOT / ".cache" / "ghl_daily_leads"
_GHL_LEADS_FETCH_WORKERS = 8

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
                        "leads": 0.0,
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


def _classify_day_contacts(
    contacts: list[dict[str, Any]],
) -> tuple[int, int, int]:
    total = len(contacts)
    meta = sum(1 for contact in contacts if _is_meta_lead_contact(contact))
    google = sum(1 for contact in contacts if _is_google_lead_contact(contact))
    return total, meta, google


def _fetch_day_lead_counts(day: str) -> dict[str, Any]:
    """Load or fetch lead counts for one calendar day (disk-cached)."""
    cache_path = _ghl_day_cache_path(day)
    if cache_path.is_file() and _ghl_day_cache_is_fresh(day):
        return json.loads(cache_path.read_text(encoding="utf-8"))

    contacts, truncated = load_contacts_for_calendar_day(day)
    total, meta, google = _classify_day_contacts(contacts)
    payload = {
        "date": day,
        "total": total,
        "meta": meta,
        "google": google,
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
            day_rows.append(json.loads(_ghl_day_cache_path(day).read_text(encoding="utf-8")))

    if pending:
        workers = min(_GHL_LEADS_FETCH_WORKERS, len(pending))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_day_lead_counts, day): day for day in pending}
            for future in as_completed(futures):
                day_rows.append(future.result())

    meta_by_month: dict[str, int] = {}
    google_by_month: dict[str, int] = {}
    total_by_month: dict[str, int] = {}
    meta_total = google_total = total_new = 0

    for row in day_rows:
        day = row["date"]
        month_key = day[:7] + "-01"
        total = int(row.get("total") or 0)
        meta = int(row.get("meta") or 0)
        google = int(row.get("google") or 0)
        truncated = truncated or bool(row.get("truncated"))

        total_by_month[month_key] = total_by_month.get(month_key, 0) + total
        meta_by_month[month_key] = meta_by_month.get(month_key, 0) + meta
        google_by_month[month_key] = google_by_month.get(month_key, 0) + google
        total_new += total
        meta_total += meta
        google_total += google

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
        "monthly": monthly,
        "truncated_pages": truncated,
        "total_reported": total_new,
        "cache_note": cache_note,
        "days_fetched_live": len(pending),
    }


def fetch_ghl_channel_monthly(
    since: str, until: str
) -> tuple[pd.DataFrame, dict[str, Any], list[str]]:
    """
    Channel-month GHL funnel metrics not available at campaign level in ads APIs.

    Returns a frame with columns: month, channel, leads, dcs, conversions
    """
    notes: list[str] = []
    lead_summary: dict[str, Any] = {
        "total_new_contacts": 0,
        "meta_leads": 0,
        "google_leads": 0,
    }

    leads_by_month_channel: dict[tuple[pd.Timestamp, str], float] = {}
    try:
        ghl_leads = _fetch_ghl_leads_by_date_added(since, until)
        lead_summary = {
            "total_new_contacts": int(ghl_leads.get("total_new_contacts") or 0),
            "meta_leads": int(ghl_leads.get("meta_leads") or 0),
            "google_leads": int(ghl_leads.get("google_leads") or 0),
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
            meta_n = float(row.get("meta_leads") or 0)
            google_n = float(row.get("google_leads") or 0)
            if meta_n:
                leads_by_month_channel[(month, CHANNEL_META)] = meta_n
            if google_n:
                leads_by_month_channel[(month, CHANNEL_GOOGLE)] = google_n
    except Exception as exc:
        notes.append(f"GHL lead counts skipped: {exc}")

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

    # New patients: committed + sign-up date + hear-about channel + membership level
    conv_by_month_channel: dict[tuple[pd.Timestamp, str], float] = {}
    conv_by_month_channel_level: dict[tuple[pd.Timestamp, str, str], float] = {}
    try:
        hear_id = resolve_hear_about_us_custom_field_id()
        signup = fetch_signup_date_range_committed_yes_contacts(since, until)
        if signup.get("truncated_pages"):
            notes.append(
                "GHL sign-up date search hit pagination cap; new-patient counts may be low."
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
            hear = contact_custom_field_value(contact, hear_id)
            channel = _ghl_channel_for_hear_about(hear)
            if channel is None:
                continue
            level = norm_membership_level(
                contact_custom_field_value(contact, mid) if mid else ""
            )
            key = (month, channel)
            conv_by_month_channel[key] = conv_by_month_channel.get(key, 0.0) + 1.0
            key_level = (month, channel, level)
            conv_by_month_channel_level[key_level] = (
                conv_by_month_channel_level.get(key_level, 0.0) + 1.0
            )
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
                "leads": leads_by_month_channel.get((month, CHANNEL_META), 0.0),
                "dcs": meta_dcs_by_month.get(month, 0.0),
                "conversions": conv_by_month_channel.get((month, CHANNEL_META), 0.0),
            }
        )
        records.append(
            {
                "month": month,
                "channel": CHANNEL_GOOGLE,
                "leads": leads_by_month_channel.get((month, CHANNEL_GOOGLE), 0.0),
                "dcs": 0.0,
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

    return pd.DataFrame(records), lead_summary, notes, conv_by_level_df


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
    out["conversions"] = 0.0

    for (month, channel), ghl_row in ghl_monthly.groupby(["month", "channel"]):
        mask = (out["month"] == month) & (out["channel"] == channel)
        chunk = out.loc[mask]
        if chunk.empty:
            continue

        total_spend = chunk["spend"].sum()
        channel_leads = float(ghl_row["leads"].sum())
        channel_dcs = float(ghl_row["dcs"].sum())
        channel_conv = float(ghl_row["conversions"].sum())

        if channel_leads > 0:
            if total_spend > 0:
                weights = chunk["spend"] / total_spend
                out.loc[mask, "leads"] = weights * channel_leads
            else:
                share = channel_leads / len(chunk)
                out.loc[mask, "leads"] = share

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


def apply_membership_conversion_filter(
    df: pd.DataFrame,
    conv_by_level_df: pd.DataFrame,
    selected_levels: list[str],
) -> pd.DataFrame:
    """
    Re-allocate new-patient (conversion) counts for selected membership tiers.

    Leads, DCs, spend, and clicks are unchanged. Uses the same spend-share rules as
    :func:`_allocate_ghl_metrics` for conversions only.
    """
    if df.empty:
        return df

    out = df.copy()
    if "month" not in out.columns:
        out["month"] = out["date"].dt.to_period("M").dt.to_timestamp()

    out["conversions"] = 0.0
    if conv_by_level_df is None or conv_by_level_df.empty:
        return _update_conversion_derived_columns(out)

    levels = [lv for lv in selected_levels if lv in MEMBERSHIP_LEVELS]
    if not levels:
        return _update_conversion_derived_columns(out)

    filtered = conv_by_level_df[
        conv_by_level_df["membership_level"].isin(levels)
    ]
    if filtered.empty:
        return _update_conversion_derived_columns(out)

    totals = (
        filtered.groupby(["month", "channel"], as_index=False)["conversions"]
        .sum()
    )
    for row in totals.itertuples(index=False):
        month, channel, channel_conv = row.month, row.channel, float(row.conversions)
        if channel_conv <= 0:
            continue
        mask = (out["month"] == month) & (out["channel"] == channel)
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

    return _update_conversion_derived_columns(out)


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
) -> tuple[pd.DataFrame, list[str], dict[str, int], pd.DataFrame]:
    """
    Fetch and normalize paid-media rows for the Digital Channel Dashboard.

    Returns:
        (dataframe matching DATA_COLUMNS schema, list of warning/info notes,
         GHL lead summary with total_new_contacts, meta_leads, google_leads,
         conversions broken down by month, channel, and membership_level)
    """
    today = date.today()
    until_eff = until or (today - timedelta(days=1)).isoformat()
    since_eff = since or DEFAULT_SINCE
    notes: list[str] = [
        f"Google Ads + Meta insights: {since_eff} → {until_eff} (Meta excludes today).",
        "GHL leads: all new contacts (date added); Meta = meta lead tag and/or Meta pixel; "
        "Google = dc thru g-ad tag and/or Google Tag (gaClientId).",
        "GHL DCs (Meta): Facebook/Instagram contacts by date added.",
        "GHL conversions: Committed? = Yes, Sign Up Date (by month) + hear-about attribution; "
        "membership level from GHL Membership Level field.",
        "Campaign-level GHL metrics are allocated by spend share within channel-month.",
    ]

    gads_daily = fetch_google_ads_campaign_daily(since_eff, until_eff)
    try:
        meta_daily = fetch_meta_campaign_daily(since_eff, until_eff)
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

    daily = pd.concat([gads_daily, meta_daily], ignore_index=True)

    monthly_ads = _aggregate_to_month_end(daily)
    ghl_monthly, lead_summary, ghl_notes, conv_by_level_df = fetch_ghl_channel_monthly(
        since_eff, until_eff
    )
    notes.extend(ghl_notes)
    notes.append(
        "GHL new contacts in range: "
        f"{lead_summary.get('total_new_contacts', 0):,} total · "
        f"{lead_summary.get('meta_leads', 0):,} Meta · "
        f"{lead_summary.get('google_leads', 0):,} Google."
    )

    if monthly_ads.empty:
        return pd.DataFrame(columns=DATA_COLUMNS), notes, lead_summary, conv_by_level_df

    merged = _allocate_ghl_metrics(monthly_ads, ghl_monthly)
    merged = _add_derived_columns(merged)

    for col in DATA_COLUMNS:
        if col not in merged.columns:
            merged[col] = pd.NA

    result = merged[DATA_COLUMNS].copy()
    result["month"] = result["date"].dt.to_period("M").dt.to_timestamp()
    return result, notes, lead_summary, conv_by_level_df


__all__ = [
    "MEMBERSHIP_LEVELS",
    "apply_membership_conversion_filter",
    "clear_ghl_leads_day_cache",
    "load_live_campaign_data",
    "monthly_campaign_summary",
    "scorecard_metrics",
    "DEFAULT_SINCE",
]
