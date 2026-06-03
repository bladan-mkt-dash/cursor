"""Refined Meta probe: stories, IG metric_type, monthly reach, FB viewers."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
import sys

_eom = Path(__file__).resolve().parent.parent
if str(_eom) not in sys.path:
    sys.path.insert(0, str(_eom))
from _bootstrap import setup

setup()

GRAPH = "v21.0"
BASE = f"https://graph.facebook.com/{GRAPH}"
APR = (datetime(2026, 4, 1, tzinfo=timezone.utc), datetime(2026, 5, 1, tzinfo=timezone.utc))
MAY = (datetime(2026, 5, 1, tzinfo=timezone.utc), datetime(2026, 6, 1, tzinfo=timezone.utc))


def token() -> str:
    return (
        os.getenv("META_SYSTEM_USER_TOKEN")
        or os.getenv("META_USER_ACCESS_TOKEN")
        or os.getenv("META_ACCESS_TOKEN")
        or ""
    ).strip()


def graph(path: str, params: dict) -> dict:
    r = requests.get(f"{BASE}/{path.lstrip('/')}", params=params, timeout=90)
    data = r.json()
    if not r.ok or data.get("error"):
        raise RuntimeError(json.dumps(data)[:500])
    return data


def epoch(d: datetime) -> int:
    return int(d.timestamp())


def fj_page(tok: str) -> tuple[str, str]:
    for p in graph("me/accounts", {"fields": "id,name,access_token", "limit": 200, "access_token": tok})["data"]:
        if "five journeys" in (p.get("name") or "").lower():
            return str(p["id"]), str(p["access_token"])
    raise RuntimeError("no FJ page")


def sum_metric(obj_id: str, tok: str, metric: str, start: datetime, end: datetime, **extra) -> int:
    params = {
        "metric": metric,
        "period": "day",
        "since": epoch(start),
        "until": epoch(end) - 1,
        "access_token": tok,
        **extra,
    }
    data = graph(f"{obj_id}/insights", params)
    rows = data.get("data") or []
    if not rows:
        return 0
    total = 0
    for item in rows[0].get("values") or []:
        v = item.get("value")
        if isinstance(v, dict):
            total += sum(int(x) for x in v.values() if x is not None)
        elif v is not None:
            total += int(float(v))
    return total


def ig_metric_month(ig_id: str, tok: str, metric: str, start: datetime, end: datetime, **extra) -> list:
    """Chunk IG daily metrics (max 30 days per request)."""
    chunks = []
    cur = start
    while cur < end:
        chunk_end = min(end, cur.replace(day=28) if cur.month == 2 else cur)  # noqa
        from datetime import timedelta

        chunk_end = min(end, cur + timedelta(days=29))
        try:
            v = sum_metric(ig_id, tok, metric, cur, chunk_end, **extra)
            chunks.append((cur.date(), chunk_end.date(), v))
        except Exception as e:
            chunks.append((cur.date(), chunk_end.date(), f"ERR:{e}"[:80]))
        cur = chunk_end
    return chunks


def count_fb_all(page_id: str, tok: str, start: datetime, end: datetime) -> dict:
    """Posts + video/reels + stories."""
    counts = {"feed_posts": 0, "videos": 0, "reels_guess": 0, "stories": 0}

    # Stories
    try:
        url = f"{BASE}/{page_id}/stories"
        params = {"fields": "id,creation_time", "access_token": tok}
        while url:
            r = requests.get(url, params=params, timeout=60)
            data = r.json()
            if not r.ok:
                break
            for s in data.get("data") or []:
                ts_raw = s.get("creation_time")
                if not ts_raw:
                    continue
                ts = datetime.strptime(ts_raw, "%Y-%m-%dT%H:%M:%S%z")
                if start <= ts < end:
                    counts["stories"] += 1
            url = (data.get("paging") or {}).get("next")
            params = {}
    except Exception:
        pass

    fields = "id,created_time,status_type"
    url = f"{BASE}/{page_id}/posts"
    params = {"fields": fields, "limit": 100, "access_token": tok}
    while url:
        r = requests.get(url, params=params, timeout=90)
        data = r.json()
        if not r.ok:
            break
        oldest = None
        for post in data.get("data") or []:
            ts_raw = post.get("created_time")
            if not ts_raw:
                continue
            ts = datetime.strptime(ts_raw, "%Y-%m-%dT%H:%M:%S%z")
            if oldest is None or ts < oldest:
                oldest = ts
            if not (start <= ts < end):
                continue
            st = (post.get("status_type") or "").lower()
            if "video" in st or st == "added_video":
                counts["videos"] += 1
            else:
                counts["feed_posts"] += 1
        url = (data.get("paging") or {}).get("next")
        params = {}
        if oldest and oldest < start:
            break
    counts["posts_plus_stories"] = counts["feed_posts"] + counts["stories"]
    counts["total"] = counts["posts_plus_stories"] + counts["videos"]
    return counts


def main() -> None:
    t = token()
    page_id, pt = fj_page(t)
    ig_id = str(graph(page_id, {"fields": "instagram_business_account", "access_token": pt})["instagram_business_account"]["id"])

    extra_fb = [
        "page_impressions_viral_unique",
        "page_impressions_paid_unique",
        "page_impressions_organic_unique",
        "page_posts_impressions_organic_unique",
        "page_posts_impressions_viral_unique",
        "page_posts_impressions_paid_unique",
        "page_content_activity",
        "page_consumptions",
        "page_consumptions_unique",
        "page_plays",
        "page_video_views",
    ]
    for label, window in [("Apr", APR), ("May", MAY)]:
        start, end = window
        print(f"\n--- FB {label} ---")
        print("counts:", count_fb_all(page_id, pt, start, end))
        for m in ["page_media_view", "page_posts_impressions_unique", "page_impressions_unique", "page_post_engagements", "page_views_total", "page_daily_follows_unique"] + extra_fb:
            try:
                print(f"  {m}: {sum_metric(page_id, pt, m, start, end)}")
            except Exception as e:
                print(f"  {m}: fail")

    # IG views with metric_type
    for label, window in [("Apr", APR), ("May", MAY)]:
        start, end = window
        print(f"\n--- IG {label} ---")
        for m, extra in [
            ("views", {"metric_type": "total_value"}),
            ("reach", {}),
            ("profile_views", {"metric_type": "total_value"}),
            ("website_clicks", {"metric_type": "total_value"}),
            ("profile_links_taps", {"metric_type": "total_value"}),
            ("total_interactions", {"metric_type": "total_value"}),
        ]:
            total = 0
            from datetime import timedelta

            cur = start
            while cur < end:
                ce = min(end, cur + timedelta(days=29))
                try:
                    total += sum_metric(ig_id, pt, m, cur, ce, **extra)
                except Exception as e:
                    print(f"  {m} chunk {cur.date()}: {e}")
                    break
                cur = ce
            print(f"  {m} {extra}: {total}")

    # IG follower end of month
    data = graph(
        ig_id,
        {"fields": "followers_count,follows_count,media_count", "access_token": pt},
    )
    print("\nIG account fields:", data)


if __name__ == "__main__":
    main()
