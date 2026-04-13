"""Meta (Facebook) Marketing API client for ad account insights."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.campaign import Campaign
from facebook_business.exceptions import FacebookRequestError

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

API_VERSION = "v21.0"

# Primary Care lead form campaign (Feb 2026); used by weekly click reporting.
ZM_PRIMARY_CARE_FEB_2026_CAMPAIGN_NAME = "ZM Primary Care Lead Form l Feb. 2026"

# Default inclusive start date for campaign insights (matches weekly Meta charts).
ZM_PRIMARY_CARE_CAMPAIGN_INSIGHTS_SINCE = "2026-02-01"


def _access_token() -> str:
    return (os.getenv("META_ACCESS_TOKEN") or os.getenv("FB_ACCESS_TOKEN") or "").strip()


def _ad_account_id() -> str:
    raw = (os.getenv("META_AD_ACCOUNT_ID") or "").strip()
    if not raw:
        raise ValueError(
            "Set META_AD_ACCOUNT_ID in .env (numeric ID or act_<numeric_id>)"
        )
    if raw.startswith("act_"):
        return raw
    return f"act_{raw}"


def _parse_float(value: str | None) -> float:
    if value is None or value == "":
        return 0.0
    return float(value)


def _init_api() -> AdAccount:
    token = _access_token()
    if not token:
        raise ValueError("Set META_ACCESS_TOKEN (or FB_ACCESS_TOKEN) in .env")
    FacebookAdsApi.init(access_token=token, api_version=API_VERSION)
    return AdAccount(_ad_account_id())


def find_campaign_id_by_name(campaign_name: str) -> str:
    """Return campaign id for an exact name match in the configured ad account."""
    account = _init_api()
    try:
        campaigns = account.get_campaigns(
            fields=["id", "name"],
            params={
                "filtering": [
                    {"field": "name", "operator": "EQUAL", "value": campaign_name}
                ],
                "limit": 100,
            },
        )
    except FacebookRequestError as e:
        body = getattr(e, "body", None) or str(e)
        raise RuntimeError(f"Meta API error: {body}") from e

    for c in campaigns:
        data = c.export_all_data()
        if (data.get("name") or "").strip() == campaign_name.strip():
            cid = data.get("id")
            if cid:
                return str(cid)
    raise ValueError(
        f"No campaign with exact name {campaign_name!r} in ad account {_ad_account_id()}"
    )


def fetch_last_30_days_insights() -> dict[str, Any]:
    """
    Fetch daily spend, impressions, and clicks for the last 30 days.

    Environment:
        META_ACCESS_TOKEN — Marketing API user/system user access token (ads_read)
        META_AD_ACCOUNT_ID — ad account numeric ID or act_<id>
        FB_ACCESS_TOKEN — optional alias for META_ACCESS_TOKEN

    Returns:
        {
            "daily": list of { "date_start", "spend", "impressions", "clicks" },
            "totals": { "spend", "impressions", "clicks" } (sums of daily rows)
        }
    """
    account = _init_api()
    fields = ["spend", "impressions", "clicks", "date_start", "date_stop"]
    params: dict[str, Any] = {
        "date_preset": "last_30d",
        "time_increment": 1,
    }

    raw_rows: list[dict[str, Any]] = []
    try:
        for row in account.get_insights(fields=fields, params=params):
            raw_rows.append(row.export_all_data())
    except FacebookRequestError as e:
        body = getattr(e, "body", None) or str(e)
        raise RuntimeError(f"Meta API error: {body}") from e

    daily: list[dict[str, Any]] = []
    total_spend = 0.0
    total_impressions = 0.0
    total_clicks = 0.0

    for row in raw_rows:
        spend = _parse_float(row.get("spend"))
        impressions = _parse_float(row.get("impressions"))
        clicks = _parse_float(row.get("clicks"))
        date_start = (row.get("date_start") or "").strip()
        if not date_start:
            continue
        daily.append(
            {
                "date_start": date_start,
                "spend": spend,
                "impressions": int(impressions),
                "clicks": int(clicks),
            }
        )
        total_spend += spend
        total_impressions += impressions
        total_clicks += clicks

    daily.sort(key=lambda r: r["date_start"])

    return {
        "daily": daily,
        "totals": {
            "spend": total_spend,
            "impressions": int(total_impressions),
            "clicks": int(total_clicks),
        },
    }


def fetch_campaign_weekly_click_performance(
    campaign_name: str | None = None,
    *,
    since: str = ZM_PRIMARY_CARE_CAMPAIGN_INSIGHTS_SINCE,
    until: str | None = None,
) -> dict[str, Any]:
    """
    Week-by-week clicks (and link clicks) for a single campaign.

    Uses Insights ``time_increment=7`` (seven-day buckets aligned to the API).
    Default ``since`` is 2026-02-01; default ``until`` is today (UTC calendar date).
    Pass explicit ``since`` / ``until`` (YYYY-MM-DD) to change the window.

    Args:
        campaign_name: Exact Meta campaign name. Defaults to
            ``ZM_PRIMARY_CARE_FEB_2026_CAMPAIGN_NAME``.
        since: Inclusive start date for insights.
        until: Inclusive end date for insights (default: today).

    Returns:
        {
            "campaign_name": str,
            "campaign_id": str,
            "weeks": list of {
                "date_start", "date_stop", "clicks", "inline_link_clicks",
                "impressions", "spend"
            },
            "totals": { same numeric fields summed over weeks }
        }
    """
    name = (campaign_name or ZM_PRIMARY_CARE_FEB_2026_CAMPAIGN_NAME).strip()
    campaign_id = find_campaign_id_by_name(name)
    _init_api()
    campaign = Campaign(campaign_id)

    until_str = until or date.today().isoformat()

    fields = [
        "clicks",
        "inline_link_clicks",
        "impressions",
        "spend",
        "date_start",
        "date_stop",
    ]
    params: dict[str, Any] = {
        "time_increment": 7,
        "time_range": {"since": since, "until": until_str},
    }

    raw_rows: list[dict[str, Any]] = []
    try:
        for row in campaign.get_insights(fields=fields, params=params):
            raw_rows.append(row.export_all_data())
    except FacebookRequestError as e:
        body = getattr(e, "body", None) or str(e)
        raise RuntimeError(f"Meta API error: {body}") from e

    weeks: list[dict[str, Any]] = []
    total_clicks = 0.0
    total_link_clicks = 0.0
    total_impressions = 0.0
    total_spend = 0.0

    for row in raw_rows:
        clicks = _parse_float(row.get("clicks"))
        link_clicks = _parse_float(row.get("inline_link_clicks"))
        impressions = _parse_float(row.get("impressions"))
        spend = _parse_float(row.get("spend"))
        date_start = (row.get("date_start") or "").strip()
        date_stop = (row.get("date_stop") or "").strip()
        if not date_start:
            continue
        weeks.append(
            {
                "date_start": date_start,
                "date_stop": date_stop,
                "clicks": int(clicks),
                "inline_link_clicks": int(link_clicks),
                "impressions": int(impressions),
                "spend": spend,
            }
        )
        total_clicks += clicks
        total_link_clicks += link_clicks
        total_impressions += impressions
        total_spend += spend

    weeks.sort(key=lambda r: r["date_start"])

    return {
        "campaign_name": name,
        "campaign_id": campaign_id,
        "weeks": weeks,
        "totals": {
            "clicks": int(total_clicks),
            "inline_link_clicks": int(total_link_clicks),
            "impressions": int(total_impressions),
            "spend": total_spend,
        },
    }


def fetch_campaign_daily_insights(
    campaign_name: str | None = None,
    *,
    since: str = ZM_PRIMARY_CARE_CAMPAIGN_INSIGHTS_SINCE,
    until: str | None = None,
) -> dict[str, Any]:
    """
    Day-by-day spend, clicks, link clicks, and impressions for a single campaign.

    Args:
        campaign_name: Exact Meta campaign name. Defaults to
            ``ZM_PRIMARY_CARE_FEB_2026_CAMPAIGN_NAME``.
        since: Inclusive start date (YYYY-MM-DD).
        until: Inclusive end date (YYYY-MM-DD); default is today (UTC).

    Returns:
        Same shape as weekly helper but ``days`` instead of ``weeks``.
    """
    name = (campaign_name or ZM_PRIMARY_CARE_FEB_2026_CAMPAIGN_NAME).strip()
    campaign_id = find_campaign_id_by_name(name)
    _init_api()
    campaign = Campaign(campaign_id)

    until_str = until or date.today().isoformat()

    fields = [
        "clicks",
        "inline_link_clicks",
        "impressions",
        "spend",
        "date_start",
        "date_stop",
    ]
    params: dict[str, Any] = {
        "time_increment": 1,
        "time_range": {"since": since, "until": until_str},
    }

    raw_rows: list[dict[str, Any]] = []
    try:
        for row in campaign.get_insights(fields=fields, params=params):
            raw_rows.append(row.export_all_data())
    except FacebookRequestError as e:
        body = getattr(e, "body", None) or str(e)
        raise RuntimeError(f"Meta API error: {body}") from e

    days: list[dict[str, Any]] = []
    total_clicks = 0.0
    total_link_clicks = 0.0
    total_impressions = 0.0
    total_spend = 0.0

    for row in raw_rows:
        clicks = _parse_float(row.get("clicks"))
        link_clicks = _parse_float(row.get("inline_link_clicks"))
        impressions = _parse_float(row.get("impressions"))
        spend = _parse_float(row.get("spend"))
        date_start = (row.get("date_start") or "").strip()
        date_stop = (row.get("date_stop") or "").strip()
        if not date_start:
            continue
        days.append(
            {
                "date_start": date_start,
                "date_stop": date_stop,
                "clicks": int(clicks),
                "inline_link_clicks": int(link_clicks),
                "impressions": int(impressions),
                "spend": spend,
            }
        )
        total_clicks += clicks
        total_link_clicks += link_clicks
        total_impressions += impressions
        total_spend += spend

    days.sort(key=lambda r: r["date_start"])

    return {
        "campaign_name": name,
        "campaign_id": campaign_id,
        "days": days,
        "totals": {
            "clicks": int(total_clicks),
            "inline_link_clicks": int(total_link_clicks),
            "impressions": int(total_impressions),
            "spend": total_spend,
        },
    }


if __name__ == "__main__":
    import json

    report = fetch_campaign_weekly_click_performance()
    print(json.dumps(report, indent=2))
