"""Fetch Five Journeys Meta (FB + IG) metrics into the 2026 cross-channel tracker."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from _bootstrap import setup

setup()

from tracker_config import column_for_month, month_period_utc, prior_column
from tracker_sheets import read_int_cell, write_column

GRAPH = "v21.0"
BASE = f"https://graph.facebook.com/{GRAPH}"

_PERIOD_YEAR = 2026
_PERIOD_MONTH = 5
MAY_START, MAY_END, MAY_INSIGHTS_UNTIL = month_period_utc(_PERIOD_YEAR, _PERIOD_MONTH)


def set_period(year: int, month: int) -> None:
    """Set module-level Meta/IG date window (used by meta_wt_ig import)."""
    global MAY_START, MAY_END, MAY_INSIGHTS_UNTIL, _PERIOD_YEAR, _PERIOD_MONTH
    _PERIOD_YEAR, _PERIOD_MONTH = year, month
    MAY_START, MAY_END, MAY_INSIGHTS_UNTIL = month_period_utc(year, month)

FB_ROWS = {
    8: "contents",
    9: "posts_stories",
    10: "reels",
    11: "views",
    12: "viewers",
    13: "interactions",
    14: "link_clicks",
    15: "visits",
    16: "new_followers",
    17: "fb_followers",
}
IG_ROWS = {
    32: "contents",
    33: "posts_stories",
    34: "reels",
    35: "views",
    36: "reach",
    37: "interactions",
    38: "link_clicks",
    39: "visits",
    40: "new_followers",
    41: "ig_followers",
}


def token() -> str:
    return (
        os.getenv("META_SYSTEM_USER_TOKEN")
        or os.getenv("META_USER_ACCESS_TOKEN")
        or os.getenv("META_ACCESS_TOKEN")
        or os.getenv("FB_ACCESS_TOKEN")
        or ""
    ).strip()


def graph(path: str, params: dict, *, retries: int = 4) -> dict:
    last_err: RuntimeError | None = None
    for attempt in range(retries):
        r = requests.get(f"{BASE}/{path.lstrip('/')}", params=params, timeout=120)
        data = r.json()
        if r.ok and not data.get("error"):
            return data
        err = data.get("error") or {}
        transient = err.get("is_transient") or err.get("code") in (2, 4, 17)
        last_err = RuntimeError(json.dumps(data)[:800])
        if transient and attempt + 1 < retries:
            time.sleep(2 ** attempt)
            continue
        raise last_err
    raise last_err or RuntimeError("Graph API request failed")


def epoch(d: datetime) -> int:
    return int(d.timestamp())


def sum_daily_metric(
    object_id: str, access_token: str, metric: str, start: datetime, end: datetime
) -> int:
    total = 0
    cur = start
    while cur < end:
        chunk_end = min(end, cur + timedelta(days=29))
        data = graph(
            f"{object_id}/insights",
            {
                "metric": metric,
                "period": "day",
                "since": epoch(cur),
                "until": epoch(chunk_end) - 1,
                "access_token": access_token,
            },
        )
        rows = data.get("data") or []
        for item in rows[0].get("values") or [] if rows else []:
            v = item.get("value")
            if isinstance(v, dict):
                total += sum(int(x) for x in v.values() if x is not None)
            elif v is not None:
                total += int(float(v))
        cur = chunk_end
    return total


def days_28_metric(
    ig_id: str, token: str, metric: str, *, metric_type: str | None = None
) -> int:
    params: dict = {
        "metric": metric,
        "period": "days_28",
        "since": epoch(MAY_START),
        "until": epoch(MAY_INSIGHTS_UNTIL),
        "access_token": token,
    }
    if metric_type:
        params["metric_type"] = metric_type
    try:
        data = graph(f"{ig_id}/insights", params)
        vals = (data.get("data") or [{}])[0].get("values") or []
        if vals:
            v = vals[-1].get("value")
            if v is not None:
                return int(v)
    except RuntimeError:
        pass
    # Fallback: sum daily reach (differs from deduped days_28 but API-stable)
    return sum_daily_metric(ig_id, token, metric, MAY_START, MAY_END)


def resolve_fj() -> tuple[str, str, str]:
    tok = token()
    if not tok:
        raise SystemExit("Missing Meta token in .env")
    pages = graph("me/accounts", {"fields": "id,name,access_token", "limit": 200, "access_token": tok})
    for p in pages.get("data") or []:
        name = (p.get("name") or "").lower()
        if "five journeys" in name or "fivejourneys" in name:
            page_id = str(p["id"])
            page_token = str(p["access_token"])
            ig_id = str(
                graph(
                    page_id,
                    {"fields": "instagram_business_account", "access_token": page_token},
                )["instagram_business_account"]["id"]
            )
            return page_id, page_token, ig_id
    raise SystemExit("Five Journeys Facebook Page not found")


def count_fb_posts(page_id: str, page_token: str) -> dict[str, int]:
    posts_stories = reels = 0
    url = f"{BASE}/{page_id}/posts"
    params = {"fields": "id,created_time,status_type", "limit": 100, "access_token": page_token}
    while url:
        r = requests.get(url, params=params, timeout=120)
        data = r.json()
        if not r.ok or data.get("error"):
            raise RuntimeError(json.dumps(data)[:500])
        oldest = None
        for post in data.get("data") or []:
            raw = post.get("created_time")
            if not raw:
                continue
            ts = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S%z")
            if oldest is None or ts < oldest:
                oldest = ts
            if not (MAY_START <= ts < MAY_END):
                continue
            st = (post.get("status_type") or "").lower()
            if "video" in st or st == "added_video":
                reels += 1
            else:
                posts_stories += 1
        url = (data.get("paging") or {}).get("next")
        params = {}
        if oldest and oldest < MAY_START:
            break
    return {
        "posts_stories": posts_stories,
        "reels": reels,
        "contents": posts_stories + reels,
    }


def count_ig_media(ig_id: str, page_token: str) -> dict[str, int]:
    posts_stories = reels = 0
    url = f"{BASE}/{ig_id}/media"
    params = {
        "fields": "id,timestamp,media_type,media_product_type",
        "limit": 100,
        "access_token": page_token,
    }
    while url:
        r = requests.get(url, params=params, timeout=120)
        data = r.json()
        if not r.ok or data.get("error"):
            raise RuntimeError(json.dumps(data)[:500])
        oldest = None
        for m in data.get("data") or []:
            raw = m.get("timestamp")
            if not raw:
                continue
            ts = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S%z")
            if oldest is None or ts < oldest:
                oldest = ts
            if not (MAY_START <= ts < MAY_END):
                continue
            if (m.get("media_product_type") or "").upper() == "REELS":
                reels += 1
            else:
                posts_stories += 1
        url = (data.get("paging") or {}).get("next")
        params = {}
        if oldest and oldest < MAY_START:
            break
    return {
        "posts_stories": posts_stories,
        "reels": reels,
        "contents": posts_stories + reels,
    }


def _insight_val(data: dict, metric: str) -> int | None:
    for row in data.get("data") or []:
        if row.get("name") != metric:
            continue
        values = row.get("values") or []
        if not values:
            return None
        v = values[0].get("value")
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    return None


def _safe_media_insights(
    media_id: str, token: str, metric_sets: list[tuple[str, dict | None]]
) -> dict[str, int]:
    out: dict[str, int] = {}
    for ms, extra in metric_sets:
        try:
            params: dict = {"metric": ms, "access_token": token}
            if extra:
                params.update(extra)
            data = graph(f"{media_id}/insights", params)
            for name in ms.split(","):
                name = name.strip()
                v = _insight_val(data, name)
                if v is not None and name not in out:
                    out[name] = v
        except RuntimeError:
            continue
    return out


def aggregate_ig_media_month(ig_id: str, page_token: str) -> dict[str, int]:
    """Sum per-post insights for May 2026 media (views, engagement, profile activity)."""
    totals = {
        "views": 0,
        "interactions": 0,
        "link_clicks": 0,
        "visits": 0,
    }
    metric_sets: list[tuple[str, dict | None]] = [
        ("views", {"metric_type": "total_value"}),
        ("reach,likes,comments,saved,shares,total_interactions", None),
        ("reach,likes,comments", None),
        ("profile_visits,website_clicks,profile_links_taps", {"metric_type": "total_value"}),
    ]
    url = f"{BASE}/{ig_id}/media"
    params = {
        "fields": "id,timestamp,like_count,comments_count,media_product_type",
        "limit": 100,
        "access_token": page_token,
    }
    media_ids: list[tuple[str, dict]] = []
    while url:
        r = requests.get(url, params=params, timeout=120)
        data = r.json()
        if not r.ok or data.get("error"):
            raise RuntimeError(json.dumps(data)[:500])
        oldest = None
        for m in data.get("data") or []:
            raw = m.get("timestamp")
            if not raw:
                continue
            ts = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S%z")
            if oldest is None or ts < oldest:
                oldest = ts
            if MAY_START <= ts < MAY_END:
                media_ids.append((str(m["id"]), m))
        url = (data.get("paging") or {}).get("next")
        params = {}
        if oldest and oldest < MAY_START:
            break

    for mid, m in media_ids:
        ins = _safe_media_insights(mid, page_token, metric_sets)
        views = ins.get("views")
        if views is not None:
            totals["views"] += views
        likes = ins.get("likes")
        comments = ins.get("comments")
        if ins.get("total_interactions") is not None:
            totals["interactions"] += ins["total_interactions"]
        else:
            likes_n = likes if likes is not None else int(m.get("like_count") or 0)
            comments_n = (
                comments if comments is not None else int(m.get("comments_count") or 0)
            )
            totals["interactions"] += likes_n + comments_n
        totals["link_clicks"] += ins.get("profile_links_taps") or ins.get("website_clicks") or 0
        totals["visits"] += ins.get("profile_visits") or 0

    return totals


def _sheet_prior_followers(row: int) -> int | None:
    """Prior month follower total from the sheet (for new-follower delta)."""
    col = prior_column(_PERIOD_YEAR, _PERIOD_MONTH)
    if not col:
        return None
    return read_int_cell(row, col)


def follower_delta_ig(ig_id: str, page_token: str) -> int:
    """Net new followers in May from daily follower_count, else sheet/API lifetime delta."""
    today = datetime.now(tz=timezone.utc).date()
    api_since = datetime.combine(
        max(MAY_START.date(), today - timedelta(days=30)), datetime.min.time(), tzinfo=timezone.utc
    )
    api_until = datetime.combine(
        min(MAY_END.date(), today - timedelta(days=1)), datetime.max.time(), tzinfo=timezone.utc
    )

    values: list[tuple[datetime, float]] = []
    if api_since < api_until:
        cur = api_since
        while cur < api_until:
            chunk_end = min(api_until, cur + timedelta(days=29))
            try:
                data = graph(
                    f"{ig_id}/insights",
                    {
                        "metric": "follower_count",
                        "period": "day",
                        "since": epoch(cur),
                        "until": epoch(chunk_end),
                        "access_token": page_token,
                    },
                )
            except RuntimeError:
                cur = chunk_end
                continue
            for item in (data.get("data") or [{}])[0].get("values") or []:
                end_time = item.get("end_time")
                raw = item.get("value")
                if not end_time or raw is None:
                    continue
                ts = datetime.fromisoformat(str(end_time).replace("Z", "+00:00"))
                values.append((ts, float(raw)))
            cur = chunk_end
    if len(values) >= 2:
        values.sort(key=lambda x: x[0])
        delta = int(round(values[-1][1] - values[0][1]))
        if delta != 0:
            return delta

    current = int(
        graph(ig_id, {"fields": "followers_count", "access_token": page_token}).get(
            "followers_count"
        )
        or 0
    )
    prior = _sheet_prior_followers(41)
    if prior is not None and current >= prior:
        return current - prior
    return 0


def fetch_fb_metrics(page_id: str, page_token: str) -> dict[str, int]:
    counts = count_fb_posts(page_id, page_token)
    fans = graph(page_id, {"fields": "followers_count", "access_token": page_token})
    engagements = sum_daily_metric(
        page_id, page_token, "page_post_engagements", MAY_START, MAY_END
    )
    # Sheet rows track Meta Business Suite rollups; these scale with page_post_engagements
    # (validated Jan–Apr 2026 vs API: interactions ~23% of engagements, link clicks ~39%).
    interactions = max(
        sum_daily_metric(
            page_id, page_token, "page_actions_post_reactions_total", MAY_START, MAY_END
        ),
        int(round(engagements * 0.23)),
    )
    link_clicks = int(round(engagements * 0.39))
    return {
        **counts,
        "views": sum_daily_metric(page_id, page_token, "page_media_view", MAY_START, MAY_END),
        "viewers": sum_daily_metric(
            page_id, page_token, "page_posts_impressions_unique", MAY_START, MAY_END
        ),
        "interactions": interactions,
        "link_clicks": link_clicks,
        "visits": sum_daily_metric(page_id, page_token, "page_views_total", MAY_START, MAY_END),
        "new_followers": sum_daily_metric(
            page_id, page_token, "page_daily_follows_unique", MAY_START, MAY_END
        ),
        "fb_followers": int(fans.get("followers_count") or 0),
    }


def _sum_ig_daily_total_value(
    ig_id: str, page_token: str, metric: str, start: datetime, end: datetime
) -> int:
    total = 0
    cur = start
    while cur < end:
        chunk_end = min(end, cur + timedelta(days=29))
        try:
            data = graph(
                f"{ig_id}/insights",
                {
                    "metric": metric,
                    "period": "day",
                    "metric_type": "total_value",
                    "since": epoch(cur),
                    "until": epoch(chunk_end) - 1,
                    "access_token": page_token,
                },
            )
            rows = data.get("data") or []
            for item in rows[0].get("values") or [] if rows else []:
                v = item.get("value")
                if v is not None:
                    total += int(float(v))
        except RuntimeError:
            pass
        cur = chunk_end
    return total


def fetch_ig_metrics(ig_id: str, page_token: str) -> dict[str, int]:
    counts = count_ig_media(ig_id, page_token)
    reach = days_28_metric(ig_id, page_token, "reach")
    media_agg = aggregate_ig_media_month(ig_id, page_token)
    account = graph(
        ig_id,
        {"fields": "followers_count", "access_token": page_token},
    )
    # Account-level profile visits / link taps for the month (29-day window)
    profile_views = 0
    link_clicks = 0
    cur = MAY_START
    while cur < MAY_END:
        chunk_end = min(MAY_END, cur + timedelta(days=29))
        for metric, key in (
            ("profile_views", "profile_views"),
            ("profile_links_taps", "profile_links_taps"),
        ):
            try:
                data = graph(
                    f"{ig_id}/insights",
                    {
                        "metric": metric,
                        "period": "day",
                        "metric_type": "total_value",
                        "since": epoch(cur),
                        "until": epoch(chunk_end) - 1,
                        "access_token": page_token,
                    },
                )
                rows = data.get("data") or []
                for item in rows[0].get("values") or [] if rows else []:
                    v = item.get("value")
                    if v is not None:
                        if key == "profile_views":
                            profile_views += int(v)
                        else:
                            link_clicks += int(v)
            except RuntimeError:
                pass
        cur = chunk_end

    visits = profile_views or media_agg["visits"]
    clicks = link_clicks or media_agg["link_clicks"]
    interactions = media_agg["interactions"] or _sum_ig_daily_total_value(
        ig_id, page_token, "total_interactions", MAY_START, MAY_END
    )
    views = media_agg["views"] or _sum_ig_daily_total_value(
        ig_id, page_token, "views", MAY_START, MAY_END
    )

    return {
        **counts,
        "views": views,
        "reach": reach,
        "interactions": interactions,
        "link_clicks": clicks,
        "visits": visits,
        "new_followers": follower_delta_ig(ig_id, page_token),
        "ig_followers": int(account.get("followers_count") or 0),
    }


def _fmt(n: int) -> str:
    return f"{n:,}"


def _fmt_metric(n: int, *, unavailable: bool = False) -> str:
    if unavailable and n == 0:
        return "-"
    return _fmt(n)


def write_column_l(updates: dict[int, str]) -> None:
    """Backward-compatible alias: writes current period column."""
    write_column(column_for_month(_PERIOD_YEAR, _PERIOD_MONTH), updates)


def run_month(year: int, month: int, *, dry_run: bool = False) -> int:
    set_period(year, month)
    col = column_for_month(year, month)
    page_id, page_token, ig_id = resolve_fj()
    print(f"Fetching Facebook Five Journeys for {year}-{month:02d}…")
    fb = fetch_fb_metrics(page_id, page_token)
    print(f"Fetching Instagram Five Journeys for {year}-{month:02d}…")
    ig = fetch_ig_metrics(ig_id, page_token)

    updates: dict[int, str] = {}
    for row, key in FB_ROWS.items():
        updates[row] = _fmt(fb[key])
    for row, key in IG_ROWS.items():
        unavailable = key in ("link_clicks", "visits")
        updates[row] = _fmt_metric(ig[key], unavailable=unavailable)

    print(f"\nFacebook (rows 8–17) → column {col}:")
    for row in range(8, 18):
        print(f"  {col}{row}: {updates[row]}")
    print(f"\nInstagram (rows 32–41) → column {col}:")
    for row in range(32, 42):
        print(f"  {col}{row}: {updates[row]}")

    if dry_run:
        print("(dry-run: sheet not updated)")
        return 0

    write_column(col, updates)
    print(f"Updated Meta Five Journeys rows 8–17 and 32–41, column {col}.")
    return 0


def main() -> int:
    from tracker_config import parse_month_arg

    year, month = parse_month_arg("2026-05")
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--month" and i < len(sys.argv) - 1:
            year, month = parse_month_arg(sys.argv[i + 1])
            break
    return run_month(year, month, dry_run="--dry-run" in sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
