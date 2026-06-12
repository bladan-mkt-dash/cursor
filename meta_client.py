"""Meta (Facebook) Marketing API client for ad account insights."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.campaign import Campaign
from facebook_business.exceptions import FacebookRequestError

try:
    from facebook_business.exceptions import FacebookBadObjectError
except ImportError:  # pragma: no cover - older SDKs
    FacebookBadObjectError = FacebookRequestError  # type: ignore[misc,assignment]

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

API_VERSION = "v21.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{API_VERSION}"
_INSIGHTS_MAX_RETRIES = 5
_INSIGHTS_CHUNK_DAYS = 31
_META_API_LOCK = threading.Lock()

# Primary Care lead form campaign (Feb 2026); used by weekly click reporting.
ZM_PRIMARY_CARE_FEB_2026_CAMPAIGN_NAME = "ZM Primary Care Lead Form l Feb. 2026"

# Default inclusive start date for campaign insights (matches weekly Meta charts).
ZM_PRIMARY_CARE_CAMPAIGN_INSIGHTS_SINCE = "2026-02-01"


def _access_token() -> str:
    return (
        (
            os.getenv("META_SYSTEM_USER_TOKEN")
            or os.getenv("META_USER_ACCESS_TOKEN")
            or os.getenv("META_ACCESS_TOKEN")
            or os.getenv("FB_ACCESS_TOKEN")
            or ""
        ).strip()
    )


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


def _meta_error_code(exc: FacebookRequestError) -> int | None:
    code = getattr(exc, "api_error_code", None)
    if callable(code):
        try:
            return int(code())
        except Exception:
            return None
    if code is not None:
        try:
            return int(code)
        except (TypeError, ValueError):
            return None
    return None


def _meta_http_status(exc: FacebookRequestError) -> int | None:
    status = getattr(exc, "http_status", None)
    if callable(status):
        try:
            return int(status())
        except Exception:
            return None
    if status is not None:
        try:
            return int(status)
        except (TypeError, ValueError):
            return None
    return None


def _is_retryable_meta_error(exc: FacebookRequestError) -> bool:
    """True for transient Meta outages, unknown errors, and rate limits."""
    http_status = _meta_http_status(exc)
    if http_status in (429, 500, 502, 503, 504):
        return True
    code = _meta_error_code(exc)
    return code in {1, 2, 4, 17, 32, 613}


def _is_retryable_http_status(status_code: int) -> bool:
    return status_code in (429, 500, 502, 503, 504)


def _retry_sleep_seconds(attempt: int, *, rate_limited: bool = False) -> float:
    base = 2 ** attempt
    return float(min(60, base * (3 if rate_limited else 1)))


def _insights_date_chunks(
    since: str,
    until: str,
    *,
    chunk_days: int = _INSIGHTS_CHUNK_DAYS,
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


def _insights_request_params(params: dict[str, Any]) -> dict[str, Any]:
    """Shape params for Graph GET /insights (time_range must be a JSON string)."""
    shaped: dict[str, Any] = {}
    for key, value in params.items():
        if key == "time_range" and isinstance(value, dict):
            shaped[key] = json.dumps(value)
        else:
            shaped[key] = value
    return shaped


def _fetch_insight_rows_http(
    *,
    fields: list[str],
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fetch insights via raw Graph HTTP using cursor tokens (avoids SDK pagination bugs)."""
    token = _access_token()
    if not token:
        raise ValueError(
            "Set one of META_SYSTEM_USER_TOKEN, META_USER_ACCESS_TOKEN, "
            "META_ACCESS_TOKEN, or FB_ACCESS_TOKEN in .env"
        )

    url = f"{GRAPH_BASE_URL}/{_ad_account_id()}/insights"
    query: dict[str, Any] = {
        "access_token": token,
        "fields": ",".join(fields),
        "limit": 500,
        **_insights_request_params(params),
    }

    rows: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        page_params = dict(query)
        if after:
            page_params["after"] = after
        response = requests.get(url, params=page_params, timeout=120)
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Meta returned non-JSON (HTTP {response.status_code}): {response.text[:200]}"
            ) from exc

        if payload.get("error"):
            raise RuntimeError(json.dumps(payload["error"]))

        batch = payload.get("data") or []
        if isinstance(batch, list):
            rows.extend(row for row in batch if isinstance(row, dict))

        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        cursors = paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}
        after = str(cursors.get("after") or "").strip() or None
        if not after:
            break

    return rows


def _fetch_insight_rows_with_retry(
    account: AdAccount,
    *,
    fields: list[str],
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(_INSIGHTS_MAX_RETRIES):
        try:
            with _META_API_LOCK:
                return _fetch_insight_rows_http(fields=fields, params=params)
        except (FacebookRequestError, FacebookBadObjectError) as exc:
            last_error = exc
            retryable = isinstance(exc, FacebookRequestError) and _is_retryable_meta_error(exc)
        except RuntimeError as exc:
            last_error = exc
            message = str(exc)
            retryable = (
                "HTTP 429" in message
                or "HTTP 500" in message
                or "HTTP 502" in message
                or "HTTP 503" in message
                or "HTTP 504" in message
                or "non-JSON" in message
                or '"code": 1' in message
                or '"code": 4' in message
            )
        except Exception as exc:
            last_error = exc
            retryable = False

        if attempt + 1 < _INSIGHTS_MAX_RETRIES and retryable:
            rate_limited = last_error is not None and "429" in str(last_error)
            time.sleep(_retry_sleep_seconds(attempt, rate_limited=rate_limited))
            continue
        break

    if isinstance(last_error, FacebookRequestError):
        raise RuntimeError(f"Meta API error: {_meta_api_error_message(last_error)}") from last_error
    if last_error is not None:
        raise RuntimeError(f"Meta API error: {last_error}") from last_error
    raise RuntimeError("Meta API error: insights request failed with no details")


def _init_api() -> AdAccount:
    token = _access_token()
    if not token:
        raise ValueError(
            "Set one of META_SYSTEM_USER_TOKEN, META_USER_ACCESS_TOKEN, "
            "META_ACCESS_TOKEN, or FB_ACCESS_TOKEN in .env"
        )
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
        raise RuntimeError(f"Meta API error: {_meta_api_error_message(e)}") from e

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

    raw_rows = _fetch_insight_rows_with_retry(account, fields=fields, params=params)
    return _summarize_daily_insight_rows(raw_rows, include_leads=False)


_LEAD_ACTION_TYPES = frozenset(
    {
        "lead",
        "onsite_conversion.lead_grouped",
        "offsite_conversion.fb_pixel_lead",
    }
)


def _lead_count_from_actions(actions: list[dict[str, Any]] | None) -> int:
    total = 0
    for action in actions or []:
        if (action.get("action_type") or "") in _LEAD_ACTION_TYPES:
            total += int(_parse_float(action.get("value")))
    return total


def _summarize_daily_insight_rows(
    raw_rows: list[dict[str, Any]],
    *,
    include_leads: bool,
) -> dict[str, Any]:
    daily: list[dict[str, Any]] = []
    total_spend = 0.0
    total_impressions = 0.0
    total_clicks = 0.0
    total_leads = 0

    for row in raw_rows:
        spend = _parse_float(row.get("spend"))
        impressions = _parse_float(row.get("impressions"))
        clicks = _parse_float(row.get("clicks"))
        leads = _lead_count_from_actions(row.get("actions")) if include_leads else 0
        date_start = (row.get("date_start") or "").strip()
        if not date_start:
            continue
        entry: dict[str, Any] = {
            "date_start": date_start,
            "spend": spend,
            "impressions": int(impressions),
            "clicks": int(clicks),
        }
        if include_leads:
            entry["leads"] = leads
        daily.append(entry)
        total_spend += spend
        total_impressions += impressions
        total_clicks += clicks
        total_leads += leads

    daily.sort(key=lambda r: r["date_start"])
    totals: dict[str, Any] = {
        "spend": total_spend,
        "impressions": int(total_impressions),
        "clicks": int(total_clicks),
    }
    if include_leads:
        totals["leads"] = total_leads
    return {"daily": daily, "totals": totals}


def fetch_account_daily_insights(
    *,
    since: str,
    until: str,
) -> dict[str, Any]:
    """
    Ad-account daily spend, clicks, and lead actions for an inclusive date range.

    Args:
        since: YYYY-MM-DD start date.
        until: YYYY-MM-DD end date.

    Returns:
        {
            "daily": list of {
                "date_start", "spend", "impressions", "clicks", "leads"
            },
            "totals": { "spend", "impressions", "clicks", "leads" },
        }
    """
    account = _init_api()
    fields = [
        "spend",
        "impressions",
        "clicks",
        "actions",
        "date_start",
        "date_stop",
    ]
    raw_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for chunk_since, chunk_until in _insights_date_chunks(since, until):
        chunk_params: dict[str, Any] = {
            "time_increment": 1,
            "time_range": {"since": chunk_since, "until": chunk_until},
        }
        try:
            raw_rows.extend(
                _fetch_insight_rows_with_retry(
                    account, fields=fields, params=chunk_params
                )
            )
        except RuntimeError as exc:
            errors.append(f"{chunk_since}→{chunk_until}: {exc}")

    if errors and not raw_rows:
        raise RuntimeError(
            "Meta API error (all insight chunks failed):\n" + "\n".join(errors)
        )

    result = _summarize_daily_insight_rows(raw_rows, include_leads=True)
    if errors:
        result["partial_errors"] = errors
    return result


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
        raise RuntimeError(f"Meta API error: {_meta_api_error_message(e)}") from e

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
        raise RuntimeError(f"Meta API error: {_meta_api_error_message(e)}") from e

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
