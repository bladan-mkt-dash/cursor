"""Probe Meta metrics for May 2026 vs April sheet values (Five Journeys)."""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import sys

_eom = Path(__file__).resolve().parent.parent
if str(_eom) not in sys.path:
    sys.path.insert(0, str(_eom))
from _bootstrap import setup

setup()

GRAPH = "v21.0"
BASE = f"https://graph.facebook.com/{GRAPH}"
MAY_START = datetime(2026, 5, 1, tzinfo=timezone.utc)
MAY_END = datetime(2026, 6, 1, tzinfo=timezone.utc)
APR_START = datetime(2026, 4, 1, tzinfo=timezone.utc)
APR_END = datetime(2026, 5, 1, tzinfo=timezone.utc)

SPREADSHEET_ID = "1F7Lq0IBrOWolov5vEx5ztcBsZTbZCKfalQ1bwHuqakc"
SHEET = "Monthly Tracker"
COL_L = 11  # 0-based May 2026 in row 7


def token() -> str:
    return (
        os.getenv("META_SYSTEM_USER_TOKEN")
        or os.getenv("META_USER_ACCESS_TOKEN")
        or os.getenv("META_ACCESS_TOKEN")
        or os.getenv("FB_ACCESS_TOKEN")
        or ""
    ).strip()


def graph(path: str, params: dict) -> dict:
    r = requests.get(f"{BASE}/{path.lstrip('/')}", params=params, timeout=90)
    data = r.json()
    if not r.ok or data.get("error"):
        raise RuntimeError(f"{r.status_code}\n{json.dumps(data, indent=2)}")
    return data


def epoch(d: datetime) -> int:
    return int(d.timestamp())


def resolve_five_journeys_page(user_token: str) -> tuple[str, str, str]:
    data = graph("me/accounts", {"fields": "id,name,access_token", "limit": 200, "access_token": user_token})
    for p in data.get("data") or []:
        name = (p.get("name") or "").lower()
        if "five journeys" in name or "fivejourneys" in name:
            return str(p["id"]), str(p["access_token"]), str(p.get("name") or "")
    raise RuntimeError("Five Journeys page not found")


def sum_page_insights(page_id: str, page_token: str, metric: str, start: datetime, end: datetime) -> int:
    data = graph(
        f"{page_id}/insights",
        {
            "metric": metric,
            "period": "day",
            "since": epoch(start),
            "until": epoch(end) - 1,
            "access_token": page_token,
        },
    )
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


def try_page_metrics(page_id: str, page_token: str, start: datetime, end: datetime) -> dict[str, int]:
    candidates = [
        "page_media_view",
        "page_views_total",
        "page_posts_impressions",
        "page_posts_impressions_unique",
        "page_impressions_unique",
        "page_post_engagements",
        "page_actions_post_reactions_total",
        "page_total_actions",
        "page_link_clicks",
        "page_website_clicks",
        "page_profile_views",
        "page_views_logged_in_total",
        "page_daily_follows_unique",
        "page_daily_follows",
        "page_fan_adds_unique",
    ]
    out: dict[str, int] = {}
    for m in candidates:
        try:
            out[m] = sum_page_insights(page_id, page_token, m, start, end)
        except Exception as e:
            out[m] = -1
            print(f"  FB metric {m}: FAIL {str(e)[:120]}")
    return out


def count_fb_posts(page_id: str, page_token: str, start: datetime, end: datetime) -> dict[str, int]:
    fields = "id,created_time,status_type,permalink_url"
    params = {"fields": fields, "limit": 100, "access_token": page_token}
    posts = stories = reels = 0
    url = f"{BASE}/{page_id}/posts"
    while url:
        r = requests.get(url, params=params, timeout=90)
        data = r.json()
        if not r.ok or data.get("error"):
            raise RuntimeError(json.dumps(data))
        for post in data.get("data") or []:
            ts_raw = post.get("created_time")
            if not ts_raw:
                continue
            ts = datetime.strptime(ts_raw, "%Y-%m-%dT%H:%M:%S%z")
            if not (start <= ts < end):
                continue
            st = (post.get("status_type") or "").lower()
            if "video" in st or st == "added_video":
                reels += 1
            else:
                posts += 1
        url = (data.get("paging") or {}).get("next")
        params = {}
        if data.get("data"):
            oldest = min(
                datetime.strptime(p["created_time"], "%Y-%m-%dT%H:%M:%S%z")
                for p in data["data"]
                if p.get("created_time")
            )
            if oldest < start:
                break
    return {"posts_stories": posts, "reels": reels, "total": posts + reels}


def resolve_ig(page_id: str, page_token: str) -> str:
    data = graph(page_id, {"fields": "instagram_business_account", "access_token": page_token})
    ig = (data.get("instagram_business_account") or {}).get("id")
    if not ig:
        raise RuntimeError("No IG linked")
    return str(ig)


def sum_ig_insights(ig_id: str, page_token: str, metric: str, start: datetime, end: datetime) -> int:
    data = graph(
        f"{ig_id}/insights",
        {
            "metric": metric,
            "period": "day",
            "since": epoch(start),
            "until": epoch(end) - 1,
            "access_token": page_token,
        },
    )
    rows = data.get("data") or []
    if not rows:
        return 0
    total = 0
    for item in rows[0].get("values") or []:
        v = item.get("value")
        if v is not None:
            total += int(float(v))
    return total


def try_ig_metrics(ig_id: str, page_token: str, start: datetime, end: datetime) -> dict[str, int]:
    candidates = [
        "reach",
        "views",
        "impressions",
        "profile_views",
        "website_clicks",
        "profile_links_taps",
        "accounts_engaged",
        "total_interactions",
        "likes",
        "comments",
        "shares",
        "saves",
        "follower_count",
    ]
    out: dict[str, int] = {}
    for m in candidates:
        try:
            out[m] = sum_ig_insights(ig_id, page_token, m, start, end)
        except Exception as e:
            out[m] = -1
            print(f"  IG metric {m}: FAIL {str(e)[:120]}")
    return out


def count_ig_media(ig_id: str, page_token: str, start: datetime, end: datetime) -> dict[str, int]:
    fields = "id,timestamp,media_type,media_product_type"
    posts = reels = 0
    url = f"{BASE}/{ig_id}/media"
    params = {"fields": fields, "limit": 100, "access_token": page_token}
    while url:
        r = requests.get(url, params=params, timeout=90)
        data = r.json()
        if not r.ok or data.get("error"):
            raise RuntimeError(json.dumps(data))
        oldest = None
        for m in data.get("data") or []:
            ts_raw = m.get("timestamp")
            if not ts_raw:
                continue
            ts = datetime.strptime(ts_raw, "%Y-%m-%dT%H:%M:%S%z")
            if oldest is None or ts < oldest:
                oldest = ts
            if not (start <= ts < end):
                continue
            if (m.get("media_product_type") or "").upper() == "REELS":
                reels += 1
            else:
                posts += 1
        url = (data.get("paging") or {}).get("next")
        params = {}
        if oldest and oldest < start:
            break
    return {"posts_stories": posts, "reels": reels, "total": posts + reels}


def sheet_row_values(row: int) -> list:
    token_path = Path.home() / ".config" / "mcp-google-sheets" / "token.json"
    info = json.loads(token_path.read_text(encoding="utf-8"))
    creds = Credentials.from_authorized_user_info(info, info["scopes"])
    sheets = build("sheets", "v4", credentials=creds)
    vals = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=SPREADSHEET_ID, range=f"'{SHEET}'!A{row}:L{row}")
        .execute()
        .get("values", [[]])
    )
    return vals[0] if vals else []


def main() -> None:
    t = token()
    if not t:
        raise SystemExit("No Meta token in .env")

    page_id, page_token, page_name = resolve_five_journeys_page(t)
    ig_id = resolve_ig(page_id, page_token)
    print(f"Page: {page_name} ({page_id})")
    print(f"IG: {ig_id}\n")

    print("=== Sheet April (col K) vs May probe ===")
    for r in range(8, 18):
        row = sheet_row_values(r)
        label = row[2] if len(row) > 2 else f"row{r}"
        apr = row[COL_L - 1] if len(row) > COL_L - 1 else ""
        print(f"  FB row {r} {label}: Apr={apr}")
    for r in range(32, 42):
        row = sheet_row_values(r)
        label = row[2] if len(row) > 2 else f"row{r}"
        apr = row[COL_L - 1] if len(row) > COL_L - 1 else ""
        print(f"  IG row {r} {label}: Apr={apr}")

    print("\n=== FB post counts ===")
    for label, start, end in [("Apr", APR_START, APR_END), ("May", MAY_START, MAY_END)]:
        c = count_fb_posts(page_id, page_token, start, end)
        print(f"  {label}: {c}")

    print("\n=== FB page insights (sum daily May) ===")
    print(try_page_metrics(page_id, page_token, MAY_START, MAY_END))
    print("\n=== FB page insights (sum daily Apr) ===")
    print(try_page_metrics(page_id, page_token, APR_START, APR_END))

    print("\n=== IG media counts ===")
    for label, start, end in [("Apr", APR_START, APR_END), ("May", MAY_START, MAY_END)]:
        c = count_ig_media(ig_id, page_token, start, end)
        print(f"  {label}: {c}")

    print("\n=== IG account insights May ===")
    print(try_ig_metrics(ig_id, page_token, MAY_START, MAY_END))
    print("\n=== IG account insights Apr ===")
    print(try_ig_metrics(ig_id, page_token, APR_START, APR_END))

    # page fans lifetime
    fans = graph(
        page_id,
        {"fields": "followers_count,fan_count", "access_token": page_token},
    )
    print(f"\nFB followers_count now: {fans}")


if __name__ == "__main__":
    main()
