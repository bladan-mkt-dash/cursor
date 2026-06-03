"""Fetch Instagram @wendietrubowmd metrics into the 2026 cross-channel tracker."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from _bootstrap import setup

setup()

from tracker_config import column_for_month
from tracker_sheets import write_column

# Reuse Five Journeys Meta helpers (shared IG insight logic).
from _fetch_may_meta_tracker import (  # noqa: E402
    MAY_END,
    MAY_START,
    BASE,
    aggregate_ig_media_month,
    count_ig_media,
    days_28_metric,
    epoch,
    graph,
    set_period,
    token,
    _fmt_metric,
    _sheet_prior_followers,
    _sum_ig_daily_total_value,
)

WT_USERNAME = "wendietrubowmd"
WT_IG_DEFAULT = "17841405750107916"
QUERY_IG_ID = "17841406339340514"  # Five Journeys — used for business_discovery lookup

WT_IG_ROWS = {
    44: "contents",
    45: "posts_stories",
    46: "reels",
    47: "views",
    48: "reach",
    49: "interactions",
    50: "link_clicks",
    51: "visits",
    52: "new_followers",
    53: "ig_followers",
}


def _env_wt_ig_id() -> str:
    return (
        os.getenv("WENDIE_INSTAGRAM_BUSINESS_ACCOUNT_ID")
        or os.getenv("WENDIE_IG_BUSINESS_ACCOUNT_ID")
        or WT_IG_DEFAULT
    ).strip()


def _page_token_for_five_journeys() -> str:
    tok = token()
    data = graph("me/accounts", {"fields": "id,name,access_token", "limit": 200, "access_token": tok})
    for p in data.get("data") or []:
        name = (p.get("name") or "").lower()
        if "five journeys" in name or "fivejourneys" in name:
            return str(p["access_token"])
    raise RuntimeError("Five Journeys Facebook Page not found on this Meta token")


def resolve_wt_instagram() -> tuple[str, str]:
    """
    Return (instagram_business_account_id, page_access_token) when the token can
    manage @wendietrubowmd directly; otherwise raise with setup instructions.
    """
    wt_id = _env_wt_ig_id()
    user_tok = token()
    if not user_tok:
        raise SystemExit("Missing Meta token in .env (META_SYSTEM_USER_TOKEN, etc.)")

    # 1) Page linked to this IG username (ideal — full Insights API).
    data = graph(
        "me/accounts",
        {
            "fields": "id,name,access_token,instagram_business_account{id,username}",
            "limit": 200,
            "access_token": user_tok,
        },
    )
    for p in data.get("data") or []:
        ig = p.get("instagram_business_account") or {}
        username = (ig.get("username") or "").lower()
        if username == WT_USERNAME or str(ig.get("id") or "") == wt_id:
            page_token = str(p.get("access_token") or "")
            ig_id = str(ig.get("id") or wt_id)
            if page_token and ig_id:
                return ig_id, page_token

    # 2) Direct IG object access with any page token (some setups grant partner access).
    for page_token in {_page_token_for_five_journeys(), user_tok}:
        try:
            graph(
                wt_id,
                {"fields": "id,username,followers_count", "access_token": page_token},
            )
            return wt_id, page_token
        except RuntimeError:
            continue

    raise RuntimeError(
        "This Meta token cannot manage @wendietrubowmd for Insights (only Five Journeys is assigned).\n"
        "To fill views/reach/interactions/link clicks/visits, assign the Wendie Trubow Instagram\n"
        "account to your Business portfolio and grant the System User access:\n"
        "  Meta Business Settings > Accounts > Instagram accounts > Add @wendietrubowmd\n"
        "  > assign to the same System User as Five Journeys.\n"
        "Optional .env override once linked:\n"
        "  WENDIE_INSTAGRAM_BUSINESS_ACCOUNT_ID=17841405750107916\n"
        f"Discovered IG id via business_discovery: {wt_id}"
    )


def follower_delta_wt(ig_id: str, page_token: str) -> int:
    """Net new followers in May; uses column K (April) on row 53 for fallback."""
    today = datetime.now(tz=timezone.utc).date()
    api_since = datetime.combine(
        max(MAY_START.date(), today - timedelta(days=30)),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    api_until = datetime.combine(
        min(MAY_END.date(), today - __import__("datetime").timedelta(days=1)),
        datetime.max.time(),
        tzinfo=timezone.utc,
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
    prior = _sheet_prior_followers(53)
    if prior is not None and current >= prior:
        return current - prior
    return 0


def fetch_wt_ig_metrics(ig_id: str, page_token: str) -> dict[str, int]:
    counts = count_ig_media(ig_id, page_token)
    reach = days_28_metric(ig_id, page_token, "reach")
    media_agg = aggregate_ig_media_month(ig_id, page_token)
    account = graph(ig_id, {"fields": "followers_count", "access_token": page_token})

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
        "new_followers": follower_delta_wt(ig_id, page_token),
        "ig_followers": int(account.get("followers_count") or 0),
    }


def _discovery_counts_may() -> dict[str, int]:
    """Limited public fields when Insights API access is missing."""
    page_token = _page_token_for_five_journeys()
    fields = (
        f"business_discovery.username({WT_USERNAME})"
        "{followers_count,media.limit(50){id,timestamp,media_product_type,like_count,comments_count}}"
    )
    r = requests.get(
        f"{BASE}/{QUERY_IG_ID}",
        params={"fields": fields, "access_token": page_token},
        timeout=120,
    )
    data = r.json()
    if not r.ok or data.get("error"):
        raise RuntimeError(json.dumps(data)[:500])

    bd = data.get("business_discovery") or {}
    posts_stories = reels = 0
    interactions = 0
    for m in (bd.get("media") or {}).get("data") or []:
        raw = m.get("timestamp")
        if not raw:
            continue
        ts = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S%z")
        if not (MAY_START <= ts < MAY_END):
            continue
        if (m.get("media_product_type") or "").upper() == "REELS":
            reels += 1
        else:
            posts_stories += 1
        interactions += int(m.get("like_count") or 0) + int(m.get("comments_count") or 0)

    followers = int(bd.get("followers_count") or 0)
    prior = _sheet_prior_followers(53)
    new_followers = max(0, followers - prior) if prior is not None else 0

    return {
        "contents": posts_stories + reels,
        "posts_stories": posts_stories,
        "reels": reels,
        "views": 0,
        "reach": 0,
        "interactions": interactions,
        "link_clicks": 0,
        "visits": 0,
        "new_followers": new_followers,
        "ig_followers": followers,
    }


def run_month(
    year: int,
    month: int,
    *,
    dry_run: bool = False,
    allow_discovery: bool = False,
) -> int:
    set_period(year, month)
    col = column_for_month(year, month)
    updates: dict[int, str] = {}
    try:
        ig_id, page_token = resolve_wt_instagram()
        print(f"Resolved @wendietrubowmd — fetching {year}-{month:02d} Insights…")
        metrics = fetch_wt_ig_metrics(ig_id, page_token)
        source = "insights"
    except RuntimeError as exc:
        if not allow_discovery:
            print(str(exc))
            print("\nRe-run with --allow-discovery for partial counts only.")
            return 1
        print(str(exc))
        print("\nUsing business_discovery fallback (partial metrics)…")
        metrics = _discovery_counts_may()
        source = "business_discovery"

    for row, key in WT_IG_ROWS.items():
        unavailable = key in ("link_clicks", "visits") or (
            source == "business_discovery"
            and key in ("views", "reach", "interactions", "link_clicks", "visits")
        )
        updates[row] = "-" if unavailable else _fmt_metric(metrics[key], unavailable=False)

    for row in range(44, 54):
        print(f"  {col}{row}: {updates[row]}  ({source})")

    if dry_run:
        print("(dry-run: sheet not updated)")
        return 0

    write_column(col, updates)
    print(f"Updated Instagram W. Trubow rows 44–53, column {col}.")
    return 0


def main() -> int:
    from tracker_config import parse_month_arg

    year, month = parse_month_arg("2026-05")
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--month" and i < len(sys.argv) - 1:
            year, month = parse_month_arg(sys.argv[i + 1])
            break
    return run_month(
        year,
        month,
        dry_run="--dry-run" in sys.argv,
        allow_discovery="--allow-discovery" in sys.argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
