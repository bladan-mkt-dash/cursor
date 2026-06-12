"""Instagram organic metrics for Marketing War Room (Meta Graph API)."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

GRAPH_API_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
_GRAPH_MAX_RETRIES = 5


@dataclass
class OrganicSocialSnapshot:
    period_since: str = ""
    period_until: str = ""
    ig_reach_7d: int | None = None
    ig_engagement_7d: int | None = None
    follower_delta_7d: int | None = None
    top_post: str | None = None
    top_post_engagement: int | None = None
    ig_user_id: str | None = None
    page_name: str | None = None
    posts_in_period: int | None = None
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


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


def _graph_error_message(response: requests.Response) -> str:
    if response.status_code == 429:
        return "Meta rate limit (HTTP 429). Too many Graph API calls — retry shortly."
    text = (response.text or "").strip()
    if text.startswith("<!DOCTYPE") or text.startswith("<html"):
        return (
            f"Meta returned an HTML error page (HTTP {response.status_code}). "
            "Usually a temporary outage or rate limit."
        )
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {text[:300]}"
    if payload.get("error"):
        return json.dumps(payload["error"])
    return f"HTTP {response.status_code}: {text[:300]}"


def _is_retryable_graph_response(response: requests.Response) -> bool:
    if response.status_code in (429, 500, 502, 503, 504):
        return True
    text = (response.text or "").strip()
    return text.startswith("<!DOCTYPE") or text.startswith("<html")


def _graph(path: str, params: dict[str, Any], *, timeout: int = 60) -> dict[str, Any]:
    from meta_client import _META_API_LOCK

    url = f"{BASE_URL}/{path.lstrip('/')}"
    last_error = "Meta Graph request failed"
    for attempt in range(_GRAPH_MAX_RETRIES):
        with _META_API_LOCK:
            response = requests.get(url, params=params, timeout=timeout)
        if response.ok:
            try:
                payload = response.json()
            except ValueError as exc:
                last_error = _graph_error_message(response)
                if attempt + 1 < _GRAPH_MAX_RETRIES:
                    time.sleep(min(60, 2**attempt * (3 if response.status_code == 429 else 1)))
                    continue
                raise RuntimeError(last_error) from exc
            if payload.get("error"):
                last_error = json.dumps(payload["error"])
                code = payload["error"].get("code") if isinstance(payload["error"], dict) else None
                if attempt + 1 < _GRAPH_MAX_RETRIES and code in {1, 2, 4, 17, 32, 613}:
                    time.sleep(min(60, 2**attempt * 2))
                    continue
                raise RuntimeError(last_error)
            return payload

        last_error = _graph_error_message(response)
        if attempt + 1 < _GRAPH_MAX_RETRIES and _is_retryable_graph_response(response):
            time.sleep(min(60, 2**attempt * (3 if response.status_code == 429 else 1)))
            continue
        raise RuntimeError(last_error)

    raise RuntimeError(last_error)


def _epoch_seconds(day: date, *, end_of_day: bool = False) -> int:
    if end_of_day:
        dt = datetime.combine(day, datetime.max.time(), tzinfo=timezone.utc)
    else:
        dt = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    return int(dt.timestamp())


def _parse_media_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def _truncate_caption(text: str | None, *, limit: int = 72) -> str | None:
    if not text:
        return None
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _env_page_access_token() -> str:
    return (
        os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
        or os.getenv("META_PAGE_ACCESS_TOKEN")
        or ""
    ).strip()


def _fetch_page_details(
    page_id: str,
    access_token: str,
) -> tuple[str, str, str | None]:
    """
    Load Page name, access token, and linked IG user id via GET /{page-id}.

    Meta sometimes omits ``access_token`` on ``/me/accounts`` rows (common with
    system-user tokens); this endpoint is the reliable fallback.
    """
    payload = _graph(
        page_id,
        {
            "fields": "access_token,name,instagram_business_account",
            "access_token": access_token,
        },
    )
    page_token = str(payload.get("access_token") or "").strip()
    page_name = str(payload.get("name") or "").strip()
    ig_id = str((payload.get("instagram_business_account") or {}).get("id") or "").strip()
    return page_token, page_name, ig_id or None


def _choose_page_from_accounts(
    pages: list[dict[str, Any]],
    *,
    page_pref: str | None,
) -> dict[str, Any] | None:
    for page in pages:
        name = (page.get("name") or "").lower()
        if "five journeys" in name or "fivejourneys" in name:
            return page

    if page_pref:
        for page in pages:
            if str(page.get("id")) == page_pref:
                return page

    return pages[0] if pages else None


def resolve_instagram_context(access_token: str) -> tuple[str, str, str, str]:
    """
    Return ``(ig_user_id, page_id, page_token, page_name)``.

    Prefers a Page whose name contains "five journeys", then ``FACEBOOK_PAGE_ID``,
    then the first Page returned. When ``/me/accounts`` omits ``access_token``,
    falls back to GET /{page-id} or ``FACEBOOK_PAGE_ACCESS_TOKEN`` in .env.
    """
    page_pref = (os.getenv("FACEBOOK_PAGE_ID") or "").strip() or None
    ig_pref = (os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID") or "").strip() or None
    page_token_env = _env_page_access_token()

    if page_pref and page_token_env and ig_pref:
        return ig_pref, page_pref, page_token_env, page_pref

    pages: list[dict[str, Any]] = []
    try:
        payload = _graph(
            "me/accounts",
            {"fields": "id,name,access_token", "limit": 200, "access_token": access_token},
        )
        pages = payload.get("data") or []
    except Exception:
        pages = []

    chosen = _choose_page_from_accounts(pages, page_pref=page_pref)
    page_id = str((chosen or {}).get("id") or page_pref or "").strip()
    page_token = str((chosen or {}).get("access_token") or "").strip()
    page_name = str((chosen or {}).get("name") or "").strip()

    if not page_id:
        raise RuntimeError(
            "No Facebook Pages returned for this Meta token. "
            "Set FACEBOOK_PAGE_ID in .env or grant pages_show_list on the token."
        )

    ig_id = ig_pref or None
    if not page_token or page_token_env:
        fetched_token, fetched_name, fetched_ig = _fetch_page_details(page_id, access_token)
        if fetched_token:
            page_token = fetched_token
        elif page_token_env:
            page_token = page_token_env
        if fetched_name:
            page_name = fetched_name
        if fetched_ig:
            ig_id = fetched_ig

    if not page_token:
        # Some tokens can call Instagram Graph endpoints directly.
        page_token = access_token

    if not page_token:
        raise RuntimeError(
            "Could not read Page access token. Set FACEBOOK_PAGE_ACCESS_TOKEN in .env "
            "or ensure the Meta token can access FACEBOOK_PAGE_ID."
        )

    if not ig_id:
        ig_payload = _graph(
            page_id,
            {"fields": "instagram_business_account", "access_token": page_token},
        )
        ig_id = str((ig_payload.get("instagram_business_account") or {}).get("id") or "").strip()

    if not ig_id:
        raise RuntimeError(
            "No linked Instagram business account. Link IG to the Facebook Page or set "
            "INSTAGRAM_BUSINESS_ACCOUNT_ID in .env."
        )

    return ig_id, page_id, page_token, page_name or page_id


def _fetch_daily_insight_values(
    ig_user_id: str,
    page_token: str,
    metric: str,
    *,
    since: date,
    until: date,
) -> list[dict[str, Any]]:
    payload = _graph(
        f"{ig_user_id}/insights",
        {
            "metric": metric,
            "period": "day",
            "since": _epoch_seconds(since),
            "until": _epoch_seconds(until, end_of_day=True),
            "access_token": page_token,
        },
    )
    rows = payload.get("data") or []
    if not rows:
        return []
    return rows[0].get("values") or []


def _sum_daily_values(values: list[dict[str, Any]]) -> int:
    total = 0
    for item in values:
        raw = item.get("value")
        if raw is None:
            continue
        try:
            total += int(float(raw))
        except (TypeError, ValueError):
            continue
    return total


def _follower_delta(values: list[dict[str, Any]]) -> int | None:
    """Net follower change from first to last daily snapshot in the series."""
    points: list[tuple[datetime, float]] = []
    for item in values:
        end_time = item.get("end_time")
        raw = item.get("value")
        if not end_time or raw is None:
            continue
        try:
            ts = datetime.fromisoformat(str(end_time).replace("Z", "+00:00"))
            points.append((ts, float(raw)))
        except (TypeError, ValueError):
            continue
    if len(points) < 2:
        return None
    points.sort(key=lambda row: row[0])
    return int(round(points[-1][1] - points[0][1]))


def _fetch_media_in_range(
    ig_user_id: str,
    page_token: str,
    *,
    since: date,
    until: date,
    max_scan: int = 300,
) -> list[dict[str, Any]]:
    since_dt = datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc)
    until_dt = datetime.combine(until, datetime.max.time(), tzinfo=timezone.utc)

    fields = "id,caption,timestamp,like_count,comments_count"
    params: dict[str, Any] = {
        "fields": fields,
        "limit": 100,
        "access_token": page_token,
    }
    matched: list[dict[str, Any]] = []
    scanned = 0
    after: str | None = None

    while scanned < max_scan:
        page_params = dict(params)
        if after:
            page_params["after"] = after
        data = _graph(f"{ig_user_id}/media", page_params, timeout=60)

        batch = data.get("data") or []
        scanned += len(batch)
        oldest_in_batch: datetime | None = None

        for media in batch:
            ts = _parse_media_timestamp(media.get("timestamp"))
            if ts is None:
                continue
            if oldest_in_batch is None or ts < oldest_in_batch:
                oldest_in_batch = ts
            if since_dt <= ts <= until_dt:
                matched.append(media)

        paging = data.get("paging") if isinstance(data.get("paging"), dict) else {}
        cursors = paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}
        after = str(cursors.get("after") or "").strip() or None
        if not after:
            break
        if oldest_in_batch and oldest_in_batch < since_dt:
            break

    return matched


def fetch_organic_social_7d(*, as_of: date | None = None) -> OrganicSocialSnapshot:
    """
    Instagram organic summary for the last 7 calendar days ending on ``as_of``.

    - **Reach** — sum of account daily reach insights
    - **Engagement** — likes + comments on posts published in range
    - **Follower Δ** — net change in daily follower_count snapshots
    - **Top post** — highest engagement post published in range (caption preview)
    """
    today = as_of or date.today()
    since = today - timedelta(days=6)
    snapshot = OrganicSocialSnapshot(
        period_since=since.isoformat(),
        period_until=today.isoformat(),
    )

    token = _access_token()
    if not token:
        snapshot.errors.append(
            "Meta: set META_SYSTEM_USER_TOKEN, META_USER_ACCESS_TOKEN, or META_ACCESS_TOKEN in .env."
        )
        return snapshot

    try:
        ig_id, _page_id, page_token, page_name = resolve_instagram_context(token)
        snapshot.ig_user_id = ig_id
        snapshot.page_name = page_name
    except Exception as exc:
        snapshot.errors.append(f"Meta Instagram: {exc}")
        return snapshot

    try:
        reach_values = _fetch_daily_insight_values(
            ig_id, page_token, "reach", since=since, until=today
        )
        snapshot.ig_reach_7d = _sum_daily_values(reach_values) if reach_values else None
    except Exception as exc:
        snapshot.errors.append(f"Meta reach: {exc}")

    try:
        follower_values = _fetch_daily_insight_values(
            ig_id, page_token, "follower_count", since=since, until=today
        )
        snapshot.follower_delta_7d = _follower_delta(follower_values)
        snapshot.notes.append(
            "Follower Δ uses Instagram follower_count; Meta may exclude today."
        )
    except Exception as exc:
        snapshot.errors.append(f"Meta followers: {exc}")

    try:
        media = _fetch_media_in_range(ig_id, page_token, since=since, until=today)
        snapshot.posts_in_period = len(media)
        engagement_total = 0
        top_engagement = -1
        top_caption: str | None = None

        for item in media:
            likes = int(item.get("like_count") or 0)
            comments = int(item.get("comments_count") or 0)
            engagement = likes + comments
            engagement_total += engagement
            if engagement > top_engagement:
                top_engagement = engagement
                top_caption = _truncate_caption(item.get("caption"))

        snapshot.ig_engagement_7d = engagement_total
        if top_engagement >= 0:
            snapshot.top_post = top_caption or "(no caption)"
            snapshot.top_post_engagement = top_engagement
        snapshot.notes.append(
            "Engagement = likes + comments on posts published in the 7-day window."
        )
    except Exception as exc:
        snapshot.errors.append(f"Meta media: {exc}")

    return snapshot
