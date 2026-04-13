"""Instagram recent posts with per-post performance (reach, engagement rate) via Graph API."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests
import streamlit as st
from dotenv import load_dotenv

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

GRAPH_API_VERSION = "v21.0"

st.set_page_config(page_title="Instagram — recent posts", layout="wide")


def _get_graph(path: str, params: dict, timeout: int = 30) -> dict:
    """GET Graph API path; on failure raise RuntimeError without echoing the tokenized URL."""
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{path.lstrip('/')}"
    response = requests.get(url, params=params, timeout=timeout)
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}\n{response.text}")
    return response.json()


def _user_access_token() -> str:
    return (os.getenv("META_USER_ACCESS_TOKEN") or "").strip()


def _page_id() -> str:
    return (os.getenv("FACEBOOK_PAGE_ID") or "").strip()


def _instagram_business_account_id() -> str:
    return (os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID") or "").strip()


def _page_access_token(user_token: str, page_id: str | None) -> tuple[str, str]:
    """Return (page_id, page_access_token) using /me/accounts."""
    payload = _get_graph(
        "me/accounts",
        {"fields": "id,access_token", "access_token": user_token},
    )
    err = payload.get("error")
    if err:
        raise RuntimeError(json.dumps(err))

    pages: list[dict] = payload.get("data") or []
    if not pages:
        raise RuntimeError(
            "No Facebook Pages returned for this user token. "
            "Grant pages_show_list and ensure the user manages at least one Page."
        )

    if page_id:
        for p in pages:
            if str(p.get("id")) == page_id:
                token = (p.get("access_token") or "").strip()
                if not token:
                    break
                return page_id, token
        raise RuntimeError(
            f"FACEBOOK_PAGE_ID={page_id} not found in /me/accounts for this user."
        )

    first = pages[0]
    pid = str(first.get("id") or "").strip()
    token = (first.get("access_token") or "").strip()
    if not pid or not token:
        raise RuntimeError("Could not read Page id/access_token from /me/accounts.")
    return pid, token


def _instagram_user_id_from_page(page_id: str, page_token: str) -> str | None:
    data = _get_graph(
        page_id,
        {
            "fields": "instagram_business_account",
            "access_token": page_token,
        },
    )
    err = data.get("error")
    if err:
        raise RuntimeError(json.dumps(err))
    ig = data.get("instagram_business_account") or {}
    ig_id = ig.get("id")
    return str(ig_id).strip() if ig_id else None


@st.cache_data(ttl=3600)
def _resolve_ig_user_id(user_token: str) -> tuple[str, str, str]:
    """
    Return (instagram_user_id, page_id, page_access_token).
    Prefers IG id from the linked Page; falls back to INSTAGRAM_BUSINESS_ACCOUNT_ID.
    """
    page_id_pref = _page_id()
    page_id, page_token = _page_access_token(user_token, page_id_pref or None)

    ig_from_page = _instagram_user_id_from_page(page_id, page_token)
    if ig_from_page:
        return ig_from_page, page_id, page_token

    fallback = _instagram_business_account_id()
    if fallback:
        return fallback, page_id, page_token

    raise RuntimeError(
        "Could not determine Instagram user id. Your Page has no "
        "instagram_business_account in Graph API, and INSTAGRAM_BUSINESS_ACCOUNT_ID "
        "is not set. Link the Instagram professional account to this Facebook Page "
        "in Meta settings, and ensure the token includes instagram_basic (and "
        "pages_show_list)."
    )


@st.cache_data(ttl=3600)
def fetch_recent_media(
    ig_user_id: str,
    access_token: str,
    limit: int,
) -> dict:
    return _get_graph(
        f"{ig_user_id}/media",
        {
            "fields": "id,caption,like_count,comments_count,timestamp",
            "limit": limit,
            "access_token": access_token,
        },
    )


def _insights_reach_value(payload: dict) -> int | None:
    for row in payload.get("data") or []:
        if row.get("name") != "reach":
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


@st.cache_data(ttl=3600)
def fetch_media_reach(media_id: str, access_token: str) -> int | None:
    """Lifetime reach for a media object; None if unavailable or permission denied."""
    try:
        data = _get_graph(
            f"{media_id}/insights",
            {"metric": "reach", "access_token": access_token},
            timeout=45,
        )
    except RuntimeError:
        return None
    if data.get("error"):
        return None
    return _insights_reach_value(data)


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def engagement_rate_pct(likes: int, comments: int, reach: int | None) -> float | None:
    """((Likes + Comments) / Reach) * 100, or None if reach is missing or zero."""
    if reach is None or reach <= 0:
        return None
    return round(((likes + comments) / reach) * 100, 2)


def engagement_label(rate: float | None) -> tuple[str, str]:
    """Return (label, streamlit badge color). Thresholds: high >5%, average 2–5%, low <2%."""
    if rate is None:
        return "No reach data", "gray"
    if rate > 5:
        return "High Engagement", "green"
    if rate >= 2:
        return "Average Engagement", "blue"
    return "Low Engagement", "orange"


def enrich_posts(
    raw_posts: list[dict[str, Any]],
    page_token: str,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    n = len(raw_posts)
    for idx, post in enumerate(raw_posts):
        if on_progress is not None:
            on_progress(idx + 1, n)
        mid = post.get("id")
        likes = int(post["like_count"]) if post.get("like_count") is not None else 0
        comments = (
            int(post["comments_count"]) if post.get("comments_count") is not None else 0
        )
        reach: int | None = None
        if mid:
            reach = fetch_media_reach(str(mid), page_token)
        rate = engagement_rate_pct(likes, comments, reach)
        ts = _parse_timestamp(post.get("timestamp"))
        out.append(
            {
                "raw": post,
                "likes": likes,
                "comments": comments,
                "reach": reach,
                "engagement_rate": rate,
                "sort_ts": ts,
            }
        )
    return out


st.title("Recent Instagram posts")
st.caption(
    "Captions and metrics from the Instagram Graph API. Engagement rate = "
    "((Likes + Comments) / Reach) × 100 when reach is available."
)
summary_metric_slot = st.empty()

with st.sidebar:
    st.header("Display")
    sort_mode = st.radio(
        "Sort posts",
        ["Most Recent", "Highest Engagement Rate"],
        index=0,
    )
    post_limit = st.slider(
        "Number of posts to load",
        min_value=1,
        max_value=50,
        value=10,
        help="Fewer posts = faster first load. Media list + reach insights are cached for 1 hour.",
    )
    st.caption("API responses are cached 1 hour (`ttl=3600`); refresh is quick until cache expires.")

user_token = _user_access_token()
if not user_token:
    st.error(
        "Set **META_USER_ACCESS_TOKEN** in `.env` (user token with Page + Instagram access)."
    )
    st.stop()

try:
    with st.spinner("Resolving Page and Instagram user…"):
        ig_id, page_id, page_token = _resolve_ig_user_id(user_token)
except RuntimeError as e:
    st.error(str(e))
    st.stop()
except requests.RequestException as e:
    st.error(f"Request failed while resolving Page / IG id: {e}")
    st.stop()

try:
    with st.spinner("Loading recent media…"):
        data = fetch_recent_media(ig_id, page_token, limit=post_limit)
except RuntimeError as e:
    st.error(str(e))
    st.caption(
        "Troubleshooting: Link the Instagram professional account to this Facebook Page "
        "in Meta; use a user token with **instagram_basic** and **pages_show_list**; "
        "confirm **INSTAGRAM_BUSINESS_ACCOUNT_ID** matches the linked IG user id (Graph "
        "Explorer: your Page id with `fields=instagram_business_account`)."
    )
    st.stop()
except requests.RequestException as e:
    st.error(f"Request failed: {e}")
    st.stop()

err = data.get("error")
if err:
    st.error(json.dumps(err, indent=2))
    st.caption(
        "If you see **error_subcode 33**: the Instagram user id may be wrong, or the "
        "Instagram account is not linked to the Facebook Page used for the Page token "
        f"(Page id used: `{page_id}`)."
    )
    st.stop()

items = data.get("data") or []

if items:
    progress = st.progress(0)
    insight_status = st.empty()
    insight_status.caption("Fetching insights…")
    try:

        def _insight_progress(done: int, total: int) -> None:
            frac = (done / total) if total else 1.0
            progress.progress(frac)
            insight_status.caption(f"Fetching insights ({done}/{total})…")

        enriched = enrich_posts(items, page_token, on_progress=_insight_progress)
    finally:
        progress.empty()
        insight_status.empty()

    defined_rates = [e["engagement_rate"] for e in enriched if e["engagement_rate"] is not None]
    avg_rate: float | None = None
    if defined_rates:
        avg_rate = round(sum(defined_rates) / len(defined_rates), 2)

    summary_metric_slot.metric(
        "Average engagement rate (loaded posts)",
        f"{avg_rate:.2f}%" if avg_rate is not None else "—",
        help=(
            "Mean of per-post engagement rates for posts where reach > 0. "
            "Posts with no reach data are excluded from this average."
        ),
    )
else:
    enriched = []

st.subheader("Account")
st.write(f"Instagram user id **`{ig_id}`** · Facebook Page **`{page_id}`**")

st.subheader("Posts")
if not enriched:
    st.info("No media returned (empty list). Check token scopes and IG account.")
else:
    if sort_mode == "Most Recent":
        enriched.sort(
            key=lambda e: e["sort_ts"] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
    else:
        enriched.sort(
            key=lambda e: (
                e["engagement_rate"] if e["engagement_rate"] is not None else -1.0,
            ),
            reverse=True,
        )

    for i, entry in enumerate(enriched, start=1):
        post = entry["raw"]
        caption = post.get("caption")
        ts = post.get("timestamp")
        likes = entry["likes"]
        comments = entry["comments"]
        reach = entry["reach"]
        rate = entry["engagement_rate"]
        label, badge_color = engagement_label(rate)

        with st.container(border=True):
            head_l, head_r = st.columns([3, 1])
            with head_l:
                st.subheader(f"Post {i}")
            with head_r:
                st.badge(label, color=badge_color)

            if caption:
                st.markdown(caption)
            else:
                st.caption("_(no caption)_")

            st.markdown("##### Performance analysis")
            c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
            with c1:
                st.metric("Likes", likes)
            with c2:
                st.metric("Comments", comments)
            with c3:
                st.metric("Reach", reach if reach is not None else "—")
            with c4:
                st.metric(
                    "Engagement rate",
                    f"{rate:.2f}%" if rate is not None else "—",
                    help="— when reach is zero or unavailable (division not applied).",
                )

            st.caption(f"Posted: {ts if ts else '—'}")
