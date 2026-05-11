"""Streamlit: Meta YoY new followers line chart (Facebook + Instagram).

Run:

    streamlit run meta_followers_yoy_line_chart.py

For year-to-date top posts only (separate browser tab / port), use `meta_top_posts_ytd.py`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import altair as alt
import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

GRAPH_API_VERSION = "v21.0"
IG_MAX_WINDOW_DAYS = 30
FB_HISTORY_START_DATE = "2005-01-01"
FB_MAX_WINDOW_DAYS = 93


def _token() -> str:
    return (
        (
            os.getenv("META_SYSTEM_USER_TOKEN")
            or os.getenv("META_USER_ACCESS_TOKEN")
            or os.getenv("META_ACCESS_TOKEN")
            or os.getenv("FB_ACCESS_TOKEN")
            or ""
        )
        .strip()
    )


def _token_source_name() -> str:
    if (os.getenv("META_SYSTEM_USER_TOKEN") or "").strip():
        return "META_SYSTEM_USER_TOKEN"
    if (os.getenv("META_USER_ACCESS_TOKEN") or "").strip():
        return "META_USER_ACCESS_TOKEN"
    if (os.getenv("META_ACCESS_TOKEN") or "").strip():
        return "META_ACCESS_TOKEN"
    if (os.getenv("FB_ACCESS_TOKEN") or "").strip():
        return "FB_ACCESS_TOKEN"
    return "(none)"


def _user_token() -> str:
    return (os.getenv("META_USER_ACCESS_TOKEN") or "").strip()


def _diagnostics_user_token() -> str:
    """
    Prefer META_USER_ACCESS_TOKEN for identity + page listing diagnostics.

    This avoids confusion when META_SYSTEM_USER_TOKEN is set for other flows
    but page post engagement requires a user token with the right Page access.
    """
    return _user_token() or _token()


def _post_ranking_user_token() -> str:
    """
    Prefer META_USER_ACCESS_TOKEN for /{page-id}/posts ranking.

    System user tokens often won't have the same Page post read behavior as the
    human user token used in Graph API Explorer testing.
    """
    return _user_token() or _token()


def _preferred_page_id() -> str:
    return (os.getenv("FACEBOOK_PAGE_ID") or "").strip()


def _preferred_ig_id() -> str:
    return (os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID") or "").strip()


def _ad_account_id() -> str:
    raw = (os.getenv("META_AD_ACCOUNT_ID") or "").strip()
    if not raw:
        return ""
    if raw.startswith("act_"):
        return raw
    return f"act_{raw}"


def _graph(path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{path.lstrip('/')}"
    response = requests.get(url, params=params, timeout=60)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}\n{response.text}")
    err = payload.get("error")
    if err:
        raise RuntimeError(json.dumps(err))
    return payload


def _page_access_token(user_token: str, page_id: str | None) -> tuple[str, str]:
    payload = _graph(
        "me/accounts",
        {"fields": "id,name,access_token", "limit": 200, "access_token": user_token},
    )
    pages: list[dict[str, Any]] = payload.get("data") or []
    if not pages:
        raise RuntimeError("No Facebook Pages returned for this token.")

    if page_id:
        for page in pages:
            if str(page.get("id")) == page_id:
                token = (page.get("access_token") or "").strip()
                if token:
                    return page_id, token
                break
        raise RuntimeError(f"FACEBOOK_PAGE_ID={page_id} not found in /me/accounts.")

    first = pages[0]
    first_id = str(first.get("id") or "").strip()
    first_token = (first.get("access_token") or "").strip()
    if not first_id or not first_token:
        raise RuntimeError("Could not read Page id/access_token from /me/accounts.")
    return first_id, first_token


def _resolve_ig_user_id(page_id: str, page_token: str) -> str:
    data = _graph(
        page_id,
        {"fields": "instagram_business_account", "access_token": page_token},
    )
    ig = data.get("instagram_business_account") or {}
    ig_id = str(ig.get("id") or "").strip()
    if ig_id:
        return ig_id

    fallback = _preferred_ig_id()
    if fallback:
        return fallback

    raise RuntimeError(
        "No linked Instagram business account found on the Page. "
        "Link Instagram to the Page or set INSTAGRAM_BUSINESS_ACCOUNT_ID in .env."
    )


def _epoch_seconds(ts: pd.Timestamp) -> int:
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return int(ts.timestamp())


def _to_day_frame(values: list[dict[str, Any]], value_key: str, source: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in values:
        end_time_raw = item.get("end_time")
        value_raw = item.get("value")
        if not end_time_raw or value_raw is None:
            continue
        date_value = pd.to_datetime(end_time_raw, utc=True, errors="coerce")
        if pd.isna(date_value):
            continue
        try:
            value_num = float(value_raw)
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "date": date_value.normalize(),
                value_key: value_num,
                "source": source,
            }
        )

    if not rows:
        return pd.DataFrame(columns=["date", value_key, "source"])

    frame = pd.DataFrame(rows).sort_values("date").drop_duplicates(subset=["date"], keep="last")
    return frame.reset_index(drop=True)


def _fetch_first_supported_metric(
    object_id: str,
    access_token: str,
    metrics: list[str],
    *,
    period: str = "day",
    extra_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return first metric values list accepted by the API."""
    errors: list[str] = []
    params = dict(extra_params or {})
    params["period"] = period
    params["access_token"] = access_token

    for metric_name in metrics:
        try:
            data = _graph(
                f"{object_id}/insights",
                {**params, "metric": metric_name},
            )
            rows = data.get("data") or []
            if not rows:
                return []
            return rows[0].get("values") or []
        except RuntimeError as exc:
            errors.append(f"{metric_name}: {exc}")
            continue

    joined = "\n\n".join(errors[:2])
    raise RuntimeError(
        "Could not fetch follower insights with any supported metric name. "
        "Last errors:\n\n"
        f"{joined}"
    )


def _fetch_metric_in_windows(
    object_id: str,
    access_token: str,
    metric_name: str,
    *,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    max_window_days: int,
) -> list[dict[str, Any]]:
    """Fetch day-period insights in API-safe windows and merge values."""
    all_values: list[dict[str, Any]] = []
    cursor = start_ts
    while cursor < end_ts:
        window_end = min(cursor + pd.Timedelta(days=max_window_days), end_ts)
        values = _fetch_first_supported_metric(
            object_id,
            access_token,
            metrics=[metric_name],
            period="day",
            extra_params={
                "since": _epoch_seconds(cursor),
                "until": _epoch_seconds(window_end),
            },
        )
        if values:
            all_values.extend(values)
        cursor = window_end
    return all_values


def _fetch_fb_page_fans_daily(page_id: str, page_token: str) -> pd.DataFrame:
    errors: list[str] = []
    today_utc = pd.Timestamp.now(tz="UTC").normalize()
    history_start = pd.Timestamp(FB_HISTORY_START_DATE, tz="UTC")
    candidates: list[tuple[str, str]] = [
        ("page_daily_follows_unique", "day"),
        ("page_daily_follows", "day"),
        ("page_follows", "day"),
        ("page_follows", "lifetime"),
    ]
    for metric_name, period in candidates:
        try:
            if period == "day":
                values = _fetch_metric_in_windows(
                    page_id,
                    page_token,
                    metric_name,
                    start_ts=history_start,
                    end_ts=today_utc,
                    max_window_days=FB_MAX_WINDOW_DAYS,
                )
            else:
                values = _fetch_first_supported_metric(
                    page_id,
                    page_token,
                    metrics=[metric_name],
                    period=period,
                )
            if not values:
                continue
            frame = _to_day_frame(values, "followers", "Facebook")
            if frame.empty:
                continue
            # Mark which metric was used so downstream math is correct.
            frame["metric_used"] = metric_name
            frame["metric_period"] = period
            return frame
        except RuntimeError as exc:
            errors.append(f"{metric_name} ({period}): {exc}")
            continue

    joined = "\n\n".join(errors[:3])
    raise RuntimeError(
        "Could not fetch Facebook follower insight series with supported metrics. "
        "Last errors:\n\n"
        f"{joined}"
    )


def _fetch_ig_followers_daily(ig_user_id: str, page_token: str) -> pd.DataFrame:
    today_utc = pd.Timestamp.now(tz="UTC").normalize()
    # Meta constraint for IG follower_count:
    # - only last 30 days
    # - excludes current day
    end_exclusive = today_utc
    start_inclusive = end_exclusive - pd.Timedelta(days=IG_MAX_WINDOW_DAYS)

    values = _fetch_first_supported_metric(
        ig_user_id,
        page_token,
        metrics=["follower_count"],
        period="day",
        extra_params={
            "since": _epoch_seconds(start_inclusive),
            "until": _epoch_seconds(end_exclusive),
        },
    )

    if not values:
        return pd.DataFrame(columns=["date", "followers", "source"])

    merged = _to_day_frame(values, "followers", "Instagram")
    if not merged.empty:
        merged["metric_used"] = "follower_count"
        merged["metric_period"] = "day"
    return merged.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(
        drop=True
    )


def _yearly_new_followers(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["year", "new_followers", "source"])

    out_frames: list[pd.DataFrame] = []
    for source, source_df in df.groupby("source"):
        s = source_df.sort_values("date").copy()
        s["year"] = s["date"].dt.year
        is_daily_new = "metric_used" in s.columns and s["metric_used"].fillna("").str.contains(
            "page_daily_follows", regex=False
        ).any()

        if is_daily_new:
            s["daily_new"] = s["followers"].clip(lower=0)
        else:
            s["daily_new"] = s["followers"].diff().fillna(0).clip(lower=0)

        yearly = (
            s.groupby("year")
            .agg(
                new_followers=("daily_new", "sum"),
            )
            .reset_index()
            .sort_values("year")
        )
        yearly["new_followers"] = yearly["new_followers"].round().astype(int)
        yearly["source"] = source
        out_frames.append(yearly[["year", "new_followers", "source"]])

    return pd.concat(out_frames, ignore_index=True).sort_values(["year", "source"])


def _plot_platform_section(yoy: pd.DataFrame, platform: str) -> None:
    platform_yoy = yoy.loc[yoy["source"] == platform].copy()
    platform_yoy = platform_yoy.loc[platform_yoy["new_followers"] > 0].sort_values("year")

    st.subheader(f"{platform} — New Followers Year over Year")
    if platform_yoy.empty:
        st.info(f"No non-zero yearly follower values available for {platform}.")
        return

    chart = (
        alt.Chart(platform_yoy)
        .mark_line(point=True)
        .encode(
            x=alt.X("year:O", title="Year"),
            y=alt.Y("new_followers:Q", title="New followers"),
            tooltip=[
                alt.Tooltip("year:O", title="Year"),
                alt.Tooltip("new_followers:Q", title="New followers", format=","),
            ],
        )
        .properties(height=360, title=f"{platform} — New followers by year")
    )
    st.altair_chart(chart, width="stretch")

    table = platform_yoy.rename(
        columns={
            "year": "Year",
            "new_followers": "New followers",
        }
    )
    st.dataframe(table[["Year", "New followers"]], hide_index=True, width="stretch")


def _instagram_monthly_new_followers(ig_daily: pd.DataFrame) -> pd.DataFrame:
    if ig_daily.empty:
        return pd.DataFrame(columns=["month", "new_followers"])

    s = ig_daily.sort_values("date").copy()
    s["daily_new"] = s["followers"].diff().fillna(0).clip(lower=0)
    s["month_start"] = s["date"].dt.to_period("M").dt.to_timestamp().dt.tz_localize("UTC")
    monthly = (
        s.groupby("month_start")
        .agg(new_followers=("daily_new", "sum"))
        .reset_index()
        .sort_values("month_start")
    )
    monthly["new_followers"] = monthly["new_followers"].round().astype(int)
    monthly = monthly.loc[monthly["new_followers"] > 0].copy()
    monthly["month"] = monthly["month_start"].dt.strftime("%Y-%m")
    return monthly[["month", "new_followers"]]


def _instagram_post_type_label(
    media_product_type: str, media_type: str, permalink: str
) -> str:
    """Human-readable Instagram media type for reporting."""
    mpt = (media_product_type or "").strip().upper()
    mt = (media_type or "").strip().upper()
    pl = (permalink or "").lower()
    if mpt == "REELS" or "/reel/" in pl:
        return "Reel"
    if mpt == "STORY":
        return "Story"
    if mpt == "AD":
        return "Ad"
    if mt == "CAROUSEL_ALBUM":
        return "Carousel"
    if mt == "IMAGE":
        return "Single image"
    if mt == "VIDEO":
        return "Video"
    if mt:
        return mt.replace("_", " ").title()
    return "Unknown"


def _facebook_post_type_label(post: dict[str, Any], permalink: str) -> str:
    """Infer Facebook Page post format from attachments and permalink."""
    pl = (permalink or "").lower()
    if "/reel/" in pl or "facebook.com/reel/" in pl or "fb.watch/" in pl:
        return "Reel"
    atts = post.get("attachments") or {}
    items = atts.get("data") or []
    if not items:
        return "Text / link"

    first = items[0]
    typ = (first.get("type") or "").strip().lower()
    subs = (first.get("subattachments") or {}).get("data") or []
    n_sub = len(subs)
    if typ == "album" or n_sub > 1:
        return "Carousel"
    if typ in ("photo",) or (first.get("media_type") or "").strip().lower() == "photo":
        return "Single image"
    if typ in ("video_inline", "video_autoplay", "video_direct_response"):
        return "Video"
    if typ == "share":
        return "Shared post"
    if typ in ("multi_share", "native_templates"):
        return "Link / other"
    if typ:
        return typ.replace("_", " ").title()
    return "Unknown"


def _fetch_facebook_posts_engagement(
    page_id: str, page_token: str, *, max_posts: int = 500
) -> pd.DataFrame:
    empty_cols = [
        "created_time",
        "post_id",
        "message",
        "permalink_url",
        "post_type",
        "reactions",
        "comments",
        "shares",
        "engagement",
    ]
    fields_rich = (
        "id,created_time,message,permalink_url,shares,"
        "attachments{type,media_type,subattachments},"
        "reactions.limit(0).summary(true),comments.limit(0).summary(true)"
    )
    fields_plain = (
        "id,created_time,message,permalink_url,shares,"
        "reactions.limit(0).summary(true),comments.limit(0).summary(true)"
    )

    def _pull(fields: str) -> list[dict[str, Any]]:
        base = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}/posts"
        url: str = base
        params: dict[str, Any] = {
            "fields": fields,
            "limit": 100,
            "access_token": page_token,
        }
        acc: list[dict[str, Any]] = []
        while url and len(acc) < max_posts:
            response = requests.get(url, params=params, timeout=60)
            if not response.ok:
                raise RuntimeError(f"HTTP {response.status_code}\n{response.text}")
            payload = response.json()
            err = payload.get("error")
            if err:
                raise RuntimeError(json.dumps(err))

            for post in payload.get("data") or []:
                reactions = (
                    (post.get("reactions") or {}).get("summary") or {}
                ).get("total_count", 0)
                comments = (
                    (post.get("comments") or {}).get("summary") or {}
                ).get("total_count", 0)
                shares = (post.get("shares") or {}).get("count", 0)
                created_time = post.get("created_time")
                created = pd.to_datetime(created_time, utc=True, errors="coerce")
                if pd.isna(created):
                    continue
                engagement = int(reactions or 0) + int(comments or 0) + int(shares or 0)
                permalink = (post.get("permalink_url") or "").strip()
                acc.append(
                    {
                        "created_time": created,
                        "post_id": str(post.get("id") or ""),
                        "message": (post.get("message") or "").strip(),
                        "permalink_url": permalink,
                        "post_type": _facebook_post_type_label(post, permalink),
                        "reactions": int(reactions or 0),
                        "comments": int(comments or 0),
                        "shares": int(shares or 0),
                        "engagement": engagement,
                    }
                )
                if len(acc) >= max_posts:
                    break

            next_url = (payload.get("paging") or {}).get("next")
            url = str(next_url) if next_url else ""
            params = {}
        return acc

    rows: list[dict[str, Any]] = []
    try:
        rows = _pull(fields_rich)
    except RuntimeError as exc:
        err_text = str(exc).lower()
        if "attachment" in err_text:
            rows = _pull(fields_plain)
        else:
            raise

    if not rows:
        return pd.DataFrame(columns=empty_cols)

    out = pd.DataFrame(rows)
    out = out.sort_values(["engagement", "created_time"], ascending=[False, False]).reset_index(
        drop=True
    )
    return out


def _fetch_instagram_posts_engagement(
    ig_user_id: str, page_token: str, *, max_posts: int = 1000
) -> pd.DataFrame:
    empty_cols = [
        "created_time",
        "post_id",
        "caption",
        "permalink_url",
        "post_type",
        "likes",
        "comments",
        "engagement",
    ]
    fields_rich = (
        "id,caption,timestamp,permalink,like_count,comments_count,"
        "media_type,media_product_type"
    )
    fields_plain = "id,caption,timestamp,permalink,like_count,comments_count"

    def _pull(fields: str) -> list[dict[str, Any]]:
        base = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media"
        url: str = base
        params: dict[str, Any] = {
            "fields": fields,
            "limit": 100,
            "access_token": page_token,
        }
        acc: list[dict[str, Any]] = []
        while url and len(acc) < max_posts:
            response = requests.get(url, params=params, timeout=60)
            if not response.ok:
                raise RuntimeError(f"HTTP {response.status_code}\n{response.text}")
            payload = response.json()
            err = payload.get("error")
            if err:
                raise RuntimeError(json.dumps(err))

            for post in payload.get("data") or []:
                created = pd.to_datetime(post.get("timestamp"), utc=True, errors="coerce")
                if pd.isna(created):
                    continue
                likes = int(post.get("like_count") or 0)
                comments = int(post.get("comments_count") or 0)
                engagement = likes + comments
                permalink = (post.get("permalink") or "").strip()
                acc.append(
                    {
                        "created_time": created,
                        "post_id": str(post.get("id") or ""),
                        "caption": (post.get("caption") or "").strip(),
                        "permalink_url": permalink,
                        "post_type": _instagram_post_type_label(
                            str(post.get("media_product_type") or ""),
                            str(post.get("media_type") or ""),
                            permalink,
                        ),
                        "likes": likes,
                        "comments": comments,
                        "engagement": engagement,
                    }
                )
                if len(acc) >= max_posts:
                    break

            next_url = (payload.get("paging") or {}).get("next")
            url = str(next_url) if next_url else ""
            params = {}
        return acc

    rows: list[dict[str, Any]] = []
    try:
        rows = _pull(fields_rich)
    except RuntimeError as exc:
        err_text = str(exc).lower()
        if "media_type" in err_text or "media_product_type" in err_text:
            rows = _pull(fields_plain)
        else:
            raise

    if not rows:
        return pd.DataFrame(columns=empty_cols)

    out = pd.DataFrame(rows)
    out = out.sort_values(["engagement", "created_time"], ascending=[False, False]).reset_index(
        drop=True
    )
    return out


def _page_token_for_posts(page_id: str, user_token: str) -> str:
    payload = _graph(
        "me/accounts",
        {
            "fields": "id,access_token",
            "limit": 200,
            "access_token": user_token,
        },
    )
    for page in payload.get("data") or []:
        if str(page.get("id") or "").strip() == str(page_id).strip():
            token = (page.get("access_token") or "").strip()
            if token:
                return token
    raise RuntimeError(
        "Could not resolve a Page access token for post reads. "
        "Your user token does not appear to include this Page in /me/accounts."
    )


def _top_facebook_posts(posts_df: pd.DataFrame, top_n: int = 7) -> pd.DataFrame:
    if posts_df.empty:
        return pd.DataFrame(
            columns=[
                "Rank",
                "Date",
                "Post preview",
                "Engagement",
                "Reactions",
                "Comments",
                "Shares",
                "Link",
            ]
        )

    top = posts_df.head(top_n).copy()
    top["Rank"] = range(1, len(top) + 1)
    top["Date"] = top["created_time"].dt.strftime("%Y-%m-%d")
    top["Post preview"] = top["message"].apply(
        lambda s: (s[:117] + "...") if len(s) > 120 else (s or "(no text)")
    )
    top["Link"] = top["permalink_url"].fillna("")
    top = top.rename(
        columns={
            "engagement": "Engagement",
            "reactions": "Reactions",
            "comments": "Comments",
            "shares": "Shares",
        }
    )
    return top[
        ["Rank", "Date", "Post preview", "Engagement", "Reactions", "Comments", "Shares", "Link"]
    ]


def _top_instagram_posts(posts_df: pd.DataFrame, top_n: int = 7) -> pd.DataFrame:
    if posts_df.empty:
        return pd.DataFrame(
            columns=[
                "Rank",
                "Date",
                "Post preview",
                "Engagement",
                "Likes",
                "Comments",
                "Link",
            ]
        )

    top = posts_df.head(top_n).copy()
    top["Rank"] = range(1, len(top) + 1)
    top["Date"] = top["created_time"].dt.strftime("%Y-%m-%d")
    top["Post preview"] = top["caption"].apply(
        lambda s: (s[:117] + "...") if len(s) > 120 else (s or "(no caption)")
    )
    top["Link"] = top["permalink_url"].fillna("")
    top = top.rename(
        columns={
            "engagement": "Engagement",
            "likes": "Likes",
            "comments": "Comments",
        }
    )
    return top[
        ["Rank", "Date", "Post preview", "Engagement", "Likes", "Comments", "Link"]
    ]


TOP_POSTS_YTD_N = 7
_YTD_RANK_COLS = ["Rank", "Date", "Post type", "Engagement", "Breakdown", "Preview", "URL"]


def _empty_ytd_rank_table() -> pd.DataFrame:
    return pd.DataFrame(columns=_YTD_RANK_COLS)


def _finalize_ytd_ranked(df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if df.empty:
        return _empty_ytd_rank_table()
    out = df.sort_values("Engagement", ascending=False).head(top_n).reset_index(drop=True)
    out.insert(0, "Rank", range(1, len(out) + 1))
    out["Date"] = out["Date"].dt.strftime("%Y-%m-%d")
    return out


def _build_ytd_ranked_post_tables(
    fb_df: pd.DataFrame,
    ig_df: pd.DataFrame,
    since: pd.Timestamp,
    *,
    top_n: int = TOP_POSTS_YTD_N,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    From pre-fetched post DataFrames (with post_type, engagement, etc.), return
    (facebook display table, instagram display table) for posts on/after `since`,
    each ranked by engagement (top_n rows).
    """
    fb_f = fb_df.loc[fb_df["created_time"] >= since] if not fb_df.empty else fb_df
    ig_f = ig_df.loc[ig_df["created_time"] >= since] if not ig_df.empty else ig_df

    fb_rows: list[dict[str, Any]] = []
    for _, r in fb_f.iterrows():
        msg = str(r.get("message") or "").replace("\n", " ").strip()
        fb_rows.append(
            {
                "Date": r["created_time"],
                "Post type": str(r.get("post_type") or "Unknown"),
                "Engagement": int(r["engagement"]),
                "Breakdown": (
                    f"{int(r['reactions'])} reactions, "
                    f"{int(r['comments'])} comments, "
                    f"{int(r['shares'])} shares"
                ),
                "Preview": (msg[:280] + "…") if len(msg) > 280 else (msg or "(no text)"),
                "URL": str(r.get("permalink_url") or ""),
            }
        )

    ig_rows: list[dict[str, Any]] = []
    for _, r in ig_f.iterrows():
        cap = str(r.get("caption") or "").replace("\n", " ").strip()
        ig_rows.append(
            {
                "Date": r["created_time"],
                "Post type": str(r.get("post_type") or "Unknown"),
                "Engagement": int(r["engagement"]),
                "Breakdown": f"{int(r['likes'])} likes, {int(r['comments'])} comments",
                "Preview": (cap[:280] + "…") if len(cap) > 280 else (cap or "(no text)"),
                "URL": str(r.get("permalink_url") or ""),
            }
        )

    fb_out = _finalize_ytd_ranked(pd.DataFrame(fb_rows), top_n) if fb_rows else _empty_ytd_rank_table()
    ig_out = _finalize_ytd_ranked(pd.DataFrame(ig_rows), top_n) if ig_rows else _empty_ytd_rank_table()
    return fb_out, ig_out


def fetch_ytd_top_post_display_tables(
    since: pd.Timestamp,
    *,
    max_fb_posts: int,
    max_ig_posts: int,
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """
    Fetch Facebook/Instagram posts from Meta and return ranked YTD display tables
    (top ``TOP_POSTS_YTD_N`` per platform). Does **not** exclude paid/boosted posts
    (use the YoY dashboard for paid-filtered lists).

    Returns ``(facebook_df, instagram_df, error_message)`` — error_message empty on success.
    """
    user_token = _token()
    if not user_token:
        return (
            _empty_ytd_rank_table(),
            _empty_ytd_rank_table(),
            "Set one of META_SYSTEM_USER_TOKEN, META_USER_ACCESS_TOKEN, META_ACCESS_TOKEN, "
            "or FB_ACCESS_TOKEN in .env.",
        )

    try:
        page_id_pref = _preferred_page_id() or None
        page_id, page_token = _page_access_token(user_token, page_id_pref)
        ig_user_id = _resolve_ig_user_id(page_id, page_token)
        posts_page_token = _page_token_for_posts(page_id, user_token)
        fb = _fetch_facebook_posts_engagement(
            page_id, posts_page_token, max_posts=max_fb_posts
        )
        ig = _fetch_instagram_posts_engagement(
            ig_user_id, page_token, max_posts=max_ig_posts
        )
    except Exception as exc:
        return _empty_ytd_rank_table(), _empty_ytd_rank_table(), str(exc)

    fb_out, ig_out = _build_ytd_ranked_post_tables(fb, ig, since, top_n=TOP_POSTS_YTD_N)
    return fb_out, ig_out, ""


def _normalize_permalink(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    parsed = urlparse(u)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return f"{scheme}://{netloc}{path}"


def _extract_fb_post_ids_from_story_id(story_id: str) -> tuple[str, str]:
    sid = (story_id or "").strip()
    if not sid:
        return "", ""
    if "_" not in sid:
        return sid, ""
    head, tail = sid.split("_", 1)
    return sid, tail if head and tail else ""


def _fetch_paid_content_markers(token: str) -> dict[str, set[str]]:
    """
    Return marker sets used to exclude paid/boosted posts.

    Uses ad creatives from META_AD_ACCOUNT_ID and tracks:
    - Facebook post ids used in ads
    - Instagram media ids used in ads
    - Instagram permalinks used in ads
    """
    markers = {
        "fb_post_ids": set(),
        "ig_media_ids": set(),
        "ig_permalinks": set(),
    }
    acct = _ad_account_id()
    if not acct:
        return markers

    fields = (
        "id,object_story_id,effective_object_story_id,"
        "source_instagram_media_id,instagram_permalink_url"
    )
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{acct}/adcreatives"
    params: dict[str, Any] = {
        "fields": fields,
        "limit": 200,
        "access_token": token,
    }
    pages_scanned = 0
    while url and pages_scanned < 100:
        response = requests.get(url, params=params, timeout=60)
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status_code}\n{response.text}")
        payload = response.json()
        err = payload.get("error")
        if err:
            raise RuntimeError(json.dumps(err))

        for c in payload.get("data") or []:
            for key in ("object_story_id", "effective_object_story_id"):
                sid = str(c.get(key) or "").strip()
                full_id, leaf_id = _extract_fb_post_ids_from_story_id(sid)
                if full_id:
                    markers["fb_post_ids"].add(full_id)
                if leaf_id:
                    markers["fb_post_ids"].add(leaf_id)

            ig_mid = str(c.get("source_instagram_media_id") or "").strip()
            if ig_mid:
                markers["ig_media_ids"].add(ig_mid)

            ig_link = _normalize_permalink(str(c.get("instagram_permalink_url") or ""))
            if ig_link:
                markers["ig_permalinks"].add(ig_link)

        next_url = (payload.get("paging") or {}).get("next")
        url = str(next_url) if next_url else ""
        params = {}
        pages_scanned += 1

    return markers


def _exclude_paid_facebook_posts(
    posts_df: pd.DataFrame, paid_markers: dict[str, set[str]]
) -> tuple[pd.DataFrame, int]:
    if posts_df.empty:
        return posts_df, 0
    paid_ids = paid_markers.get("fb_post_ids", set())
    if not paid_ids:
        return posts_df, 0

    work = posts_df.copy()
    work["post_leaf_id"] = work["post_id"].astype(str).str.split("_").str[-1]
    keep_mask = ~(
        work["post_id"].astype(str).isin(paid_ids)
        | work["post_leaf_id"].astype(str).isin(paid_ids)
    )
    filtered = work.loc[keep_mask].drop(columns=["post_leaf_id"])
    return filtered.reset_index(drop=True), int((~keep_mask).sum())


def _exclude_paid_instagram_posts(
    posts_df: pd.DataFrame, paid_markers: dict[str, set[str]]
) -> tuple[pd.DataFrame, int]:
    if posts_df.empty:
        return posts_df, 0
    paid_ids = paid_markers.get("ig_media_ids", set())
    paid_links = paid_markers.get("ig_permalinks", set())
    if not paid_ids and not paid_links:
        return posts_df, 0

    work = posts_df.copy()
    work["norm_link"] = work["permalink_url"].astype(str).map(_normalize_permalink)
    keep_mask = ~(
        work["post_id"].astype(str).isin(paid_ids)
        | work["norm_link"].astype(str).isin(paid_links)
    )
    filtered = work.loc[keep_mask].drop(columns=["norm_link"])
    return filtered.reset_index(drop=True), int((~keep_mask).sum())


def _format_date(value: pd.Timestamp | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return value.strftime("%Y-%m-%d")


def _fetch_me_identity(user_token: str) -> dict[str, Any]:
    return _graph("me", {"fields": "id,name", "access_token": user_token})


def _fetch_me_accounts(user_token: str) -> list[dict[str, str]]:
    data = _graph(
        "me/accounts",
        {
            "fields": "id,name",
            "limit": 200,
            "access_token": user_token,
        },
    )
    out: list[dict[str, str]] = []
    for page in data.get("data") or []:
        out.append(
            {
                "id": str(page.get("id") or "").strip(),
                "name": str(page.get("name") or "").strip(),
            }
        )
    return out


def main() -> None:
    st.set_page_config(page_title="Meta YoY New Followers", layout="wide")
    st.title("Meta — New Followers Year over Year")
    st.caption(
        "Computes annual net new followers from daily totals returned by the Graph API "
        "(Facebook Page insights, Instagram `follower_count`) using all available history "
        "that each endpoint returns."
    )
    st.info(
        "Instagram `follower_count` is limited by Meta to the last 30 days "
        "(excluding the current day), so full-inception YoY is only fully available for Facebook."
    )

    user_token = _token()
    if not user_token:
        st.error(
            "Set one of META_SYSTEM_USER_TOKEN, META_USER_ACCESS_TOKEN, META_ACCESS_TOKEN, "
            "or FB_ACCESS_TOKEN in .env."
        )
        st.stop()

    page_id_pref = _preferred_page_id() or None
    try:
        page_id, page_token = _page_access_token(user_token, page_id_pref)
        ig_user_id = _resolve_ig_user_id(page_id, page_token)
        fb_daily = _fetch_fb_page_fans_daily(page_id, page_token)
        ig_daily = _fetch_ig_followers_daily(ig_user_id, page_token)
    except Exception as exc:
        st.error(str(exc))
        st.stop()

    fb_posts = pd.DataFrame()
    fb_posts_error = ""
    paid_markers: dict[str, set[str]] = {
        "fb_post_ids": set(),
        "ig_media_ids": set(),
        "ig_permalinks": set(),
    }
    paid_markers_error = ""
    try:
        # Same token as page resolution: stale META_USER_ACCESS_TOKEN can break
        # `_post_ranking_user_token()` while META_SYSTEM_USER_TOKEN still works.
        posts_page_token = _page_token_for_posts(page_id, user_token)
        try:
            paid_token = _post_ranking_user_token() or user_token
            paid_markers = _fetch_paid_content_markers(paid_token)
        except Exception as exc:
            paid_markers_error = str(exc)
        fb_posts = _fetch_facebook_posts_engagement(page_id, posts_page_token)
    except Exception as exc:
        fb_posts_error = str(exc)

    ig_posts = pd.DataFrame()
    ig_posts_error = ""
    try:
        ig_posts = _fetch_instagram_posts_engagement(ig_user_id, page_token)
    except Exception as exc:
        ig_posts_error = str(exc)

    fb_posts, fb_paid_excluded = _exclude_paid_facebook_posts(fb_posts, paid_markers)
    ig_posts, ig_paid_excluded = _exclude_paid_instagram_posts(ig_posts, paid_markers)

    if fb_daily.empty and ig_daily.empty:
        st.warning("No follower history returned for either Facebook or Instagram.")
        st.stop()

    with st.expander("Connection diagnostics"):
        st.write(f"Token source in use: `{_token_source_name()}`")
        diag_token = _diagnostics_user_token()
        st.write(
            f"Diagnostics user token source: "
            f"`{'META_USER_ACCESS_TOKEN' if _user_token() else _token_source_name()}`"
        )
        st.write(
            f"Configured FACEBOOK_PAGE_ID: `{page_id_pref or '(not set)'}`"
        )
        try:
            identity = _fetch_me_identity(diag_token)
            st.write(
                f"Token identity: `{identity.get('name', '')}` (id `{identity.get('id', '')}`)"
            )
        except Exception as exc:
            st.warning(f"Could not fetch `/me`: {exc}")

        try:
            accounts = _fetch_me_accounts(diag_token)
            if not accounts:
                st.warning("`/me/accounts` returned no pages for this token.")
            else:
                names = ", ".join(
                    f"{row['name']} ({row['id']})" for row in accounts[:20]
                )
                st.write(f"Pages visible to token: {names}")
                if page_id_pref:
                    matches = [row for row in accounts if row["id"] == page_id_pref]
                    if matches:
                        st.success("Configured FACEBOOK_PAGE_ID is visible to this token.")
                    else:
                        st.error(
                            "Configured FACEBOOK_PAGE_ID is NOT visible to this token."
                        )
        except Exception as exc:
            st.warning(f"Could not fetch `/me/accounts`: {exc}")

    combined_daily = pd.concat([fb_daily, ig_daily], ignore_index=True)
    yoy = _yearly_new_followers(combined_daily)

    c1, c2, c3 = st.columns(3)
    c1.metric("Facebook data points", f"{len(fb_daily):,}")
    c2.metric("Instagram data points", f"{len(ig_daily):,}")
    c3.metric("Years in chart", f"{yoy['year'].nunique():,}" if not yoy.empty else "0")

    ranges: list[str] = []
    for source, frame in [("Facebook", fb_daily), ("Instagram", ig_daily)]:
        if frame.empty:
            ranges.append(f"{source}: n/a")
            continue
        ranges.append(
            f"{source}: {_format_date(frame['date'].min())} to {_format_date(frame['date'].max())}"
        )
    st.caption(" | ".join(ranges))
    if not fb_daily.empty and fb_daily["date"].dt.year.nunique() <= 1:
        st.warning(
            "Facebook insights returned only one calendar year. The script requested full history, "
            "so this usually means your token/page only has recent Page-insights retention available."
        )
    if not ig_daily.empty:
        st.caption(
            "Instagram maximum data points are constrained by API limits: daily follower_count "
            "for the last 30 days only."
        )

    if yoy.empty:
        st.info("Follower series loaded, but there was not enough data to compute YoY changes.")
        st.stop()

    _plot_platform_section(yoy, "Facebook")
    st.subheader("Facebook — Top 10 Posts by Engagement")
    if paid_markers_error:
        st.caption(
            "Paid-content exclusion could not be fully loaded from ad creatives; "
            "results may include boosted/paid posts."
        )
    else:
        st.caption(f"Excluded {fb_paid_excluded} paid/boosted Facebook posts.")
    if fb_posts_error:
        st.warning(
            "Could not load Facebook post engagement ranking. "
            "Your token likely needs `pages_read_engagement` (or approved Page Public Content Access)."
        )
        with st.expander("Technical details"):
            st.code(fb_posts_error)
    else:
        top_posts = _top_facebook_posts(fb_posts, top_n=10)
        if top_posts.empty:
            st.info("No Facebook posts were returned for engagement ranking.")
        else:
            st.dataframe(top_posts, hide_index=True, width="stretch")
    st.divider()
    st.subheader("Instagram — New Followers Month over Month")
    ig_monthly = _instagram_monthly_new_followers(ig_daily)
    if ig_monthly.empty:
        st.info("No non-zero monthly follower values available for Instagram.")
    else:
        ig_chart = (
            alt.Chart(ig_monthly)
            .mark_line(point=True)
            .encode(
                x=alt.X("month:O", title="Month"),
                y=alt.Y("new_followers:Q", title="New followers"),
                tooltip=[
                    alt.Tooltip("month:O", title="Month"),
                    alt.Tooltip("new_followers:Q", title="New followers", format=","),
                ],
            )
            .properties(height=360, title="Instagram — New followers by month")
        )
        st.altair_chart(ig_chart, width="stretch")
        st.dataframe(
            ig_monthly.rename(
                columns={"month": "Month", "new_followers": "New followers"}
            ),
            hide_index=True,
            width="stretch",
        )

    st.subheader("Instagram — Top 10 Posts by Engagement (All Time)")
    if paid_markers_error:
        st.caption(
            "Paid-content exclusion could not be fully loaded from ad creatives; "
            "results may include boosted/paid posts."
        )
    else:
        st.caption(f"Excluded {ig_paid_excluded} paid/boosted Instagram posts.")
    if ig_posts_error:
        st.warning("Could not load Instagram post engagement ranking.")
        with st.expander("Technical details"):
            st.code(ig_posts_error)
    else:
        top_ig_posts = _top_instagram_posts(ig_posts, top_n=10)
        if top_ig_posts.empty:
            st.info("No Instagram posts were returned for engagement ranking.")
        else:
            st.dataframe(top_ig_posts, hide_index=True, width="stretch")

if __name__ == "__main__":
    main()
