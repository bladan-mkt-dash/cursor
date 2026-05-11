"""
Aggregate Instagram performance for March 2026 by post format (reel, single image, carousel).

Uses Instagram Graph API (same token precedence as meta_client.py).
Requires: META_SYSTEM_USER_TOKEN or META_USER_ACCESS_TOKEN, linked Page; scopes
instagram_basic, instagram_manage_insights, pages_show_list (and pages_read_engagement per Meta docs).

Outputs counts only — no comment text.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

GRAPH_API_VERSION = "v21.0"

MARCH_2026_START = datetime(2026, 3, 1, tzinfo=timezone.utc)
MARCH_2026_END = datetime(2026, 4, 1, tzinfo=timezone.utc)


def _get_graph(path: str, params: dict) -> dict:
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{path.lstrip('/')}"
    response = requests.get(url, params=params, timeout=60)
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}\n{response.text}")
    return response.json()


def _graph_access_token() -> str:
    return (
        (
            os.getenv("META_SYSTEM_USER_TOKEN")
            or os.getenv("META_USER_ACCESS_TOKEN")
            or os.getenv("META_ACCESS_TOKEN")
            or os.getenv("FB_ACCESS_TOKEN")
            or ""
        ).strip()
    )


def _page_id() -> str:
    return (os.getenv("FACEBOOK_PAGE_ID") or "").strip()


def _instagram_business_account_id() -> str:
    return (os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID") or "").strip()


def _page_access_token(access_token: str, page_id: str | None) -> tuple[str, str]:
    payload = _get_graph(
        "me/accounts",
        {"fields": "id,access_token", "access_token": access_token},
    )
    err = payload.get("error")
    if err:
        raise RuntimeError(json.dumps(err))

    pages: list[dict] = payload.get("data") or []
    if not pages:
        raise RuntimeError("No Facebook Pages returned for this access token.")

    if page_id:
        for p in pages:
            if str(p.get("id")) == page_id:
                token = (p.get("access_token") or "").strip()
                if token:
                    return page_id, token
        raise RuntimeError(f"FACEBOOK_PAGE_ID={page_id} not found in /me/accounts.")

    first = pages[0]
    pid = str(first.get("id") or "").strip()
    token = (first.get("access_token") or "").strip()
    if not pid or not token:
        raise RuntimeError("Could not read Page id/access_token from /me/accounts.")
    return pid, token


def _instagram_user_id_from_page(page_id: str, page_token: str) -> str | None:
    data = _get_graph(
        page_id,
        {"fields": "instagram_business_account", "access_token": page_token},
    )
    err = data.get("error")
    if err:
        raise RuntimeError(json.dumps(err))
    ig = data.get("instagram_business_account") or {}
    ig_id = ig.get("id")
    return str(ig_id).strip() if ig_id else None


def _resolve_ig_user_id(access_token: str) -> tuple[str, str, str]:
    page_id_pref = _page_id()
    page_id, page_token = _page_access_token(access_token, page_id_pref or None)

    ig_from_page = _instagram_user_id_from_page(page_id, page_token)
    if ig_from_page:
        return ig_from_page, page_id, page_token

    fallback = _instagram_business_account_id()
    if fallback:
        return fallback, page_id, page_token

    raise RuntimeError(
        "Could not determine Instagram user id. Link IG professional account to the Page "
        "or set INSTAGRAM_BUSINESS_ACCOUNT_ID."
    )


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        # e.g. 2026-03-15T12:00:00+0000
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def _post_bucket(media_type: str | None, media_product_type: str | None) -> str:
    mt = (media_type or "").upper()
    mpt = (media_product_type or "").upper()
    if mpt == "REELS":
        return "Reel"
    if mt == "CAROUSEL_ALBUM":
        return "Carousel"
    if mt == "IMAGE":
        return "Single image"
    if mt == "VIDEO":
        return "Feed video (not Reel)"
    return f"Other ({mt or '?'}/{mpt or '?'})"


def _insights_value(payload: dict, metric_name: str) -> int | None:
    for row in payload.get("data") or []:
        if row.get("name") != metric_name:
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


def _fetch_media_insights(media_id: str, token: str, metrics: str) -> dict:
    return _get_graph(
        f"{media_id}/insights",
        {"metric": metrics, "access_token": token},
    )


def _safe_insights(media_id: str, token: str, metric_sets: list[str]) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for ms in metric_sets:
        try:
            data = _fetch_media_insights(media_id, token, ms)
            err = data.get("error")
            if err:
                continue
            for name in ms.split(","):
                name = name.strip()
                v = _insights_value(data, name)
                if v is not None and name not in out:
                    out[name] = v
        except RuntimeError:
            continue
    return out


def _simplify_pagination(
    ig_user_id: str,
    token: str,
    *,
    on_page: Callable[[int], None] | None = None,
) -> list[dict[str, Any]]:
    """Paginate all media; keep only March 2026 UTC."""
    fields = "id,timestamp,media_type,media_product_type,like_count,comments_count"
    items: list[dict[str, Any]] = []
    url: str | None = None
    params: dict[str, Any] = {
        "fields": fields,
        "limit": 100,
        "access_token": token,
    }
    first_path = f"{ig_user_id}/media"
    page_num = 0

    while True:
        if url:
            response = requests.get(url, timeout=60)
        else:
            response = requests.get(
                f"https://graph.facebook.com/{GRAPH_API_VERSION}/{first_path}",
                params=params,
                timeout=60,
            )
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status_code}\n{response.text}")
        data = response.json()
        err = data.get("error")
        if err:
            raise RuntimeError(json.dumps(err))
        batch = data.get("data") or []
        page_num += 1
        if on_page is not None:
            on_page(page_num)
        oldest_in_page: datetime | None = None
        for m in batch:
            ts = _parse_ts(m.get("timestamp"))
            if ts is None:
                continue
            if oldest_in_page is None or ts < oldest_in_page:
                oldest_in_page = ts
            if MARCH_2026_START <= ts < MARCH_2026_END:
                items.append(m)
        next_url = (data.get("paging") or {}).get("next")
        if not next_url:
            break
        if oldest_in_page and oldest_in_page < MARCH_2026_START:
            break
        url = next_url
        params = {}  # next URL already has query string

    return items


_METRIC_SETS_FEED_REEL = [
    "reach,views,likes,comments",
    "reach,likes,comments",
    "reach",
]


def _build_march_aggregates(
    access_token: str,
    *,
    on_media_page: Callable[[int], None] | None = None,
    on_insight_progress: Callable[[int, int], None] | None = None,
) -> tuple[str, str, dict[str, dict[str, float]], dict[str, float]]:
    """Resolve IG + Page, paginate March 2026 media, aggregate insights. Raises RuntimeError."""
    ig_id, page_id, page_token = _resolve_ig_user_id(access_token)
    march_media = _simplify_pagination(ig_id, page_token, on_page=on_media_page)

    agg: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "posts": 0,
            "reach": 0.0,
            "views": 0.0,
            "likes": 0.0,
            "comments": 0.0,
        }
    )

    n_media = len(march_media)
    for i, m in enumerate(march_media, start=1):
        mid = m.get("id")
        if not mid:
            if on_insight_progress is not None and n_media:
                on_insight_progress(i, n_media)
            continue
        bucket = _post_bucket(m.get("media_type"), m.get("media_product_type"))
        agg[bucket]["posts"] += 1

        like_f = m.get("like_count")
        com_f = m.get("comments_count")
        likes_media = int(like_f) if like_f is not None else None
        comments_media = int(com_f) if com_f is not None else None

        ins = _safe_insights(str(mid), page_token, _METRIC_SETS_FEED_REEL)
        reach = ins.get("reach")
        views = ins.get("views")
        likes_i = ins.get("likes")
        comments_i = ins.get("comments")

        likes = float(likes_i if likes_i is not None else (likes_media or 0))
        comments = float(
            comments_i if comments_i is not None else (comments_media or 0)
        )
        agg[bucket]["likes"] += likes
        agg[bucket]["comments"] += comments
        if reach is not None:
            agg[bucket]["reach"] += float(reach)
        if views is not None:
            agg[bucket]["views"] += float(views)

        if on_insight_progress is not None and n_media:
            on_insight_progress(i, n_media)

    total = {
        "posts": sum(v["posts"] for v in agg.values()),
        "reach": sum(v["reach"] for v in agg.values()),
        "views": sum(v["views"] for v in agg.values()),
        "likes": sum(v["likes"] for v in agg.values()),
        "comments": sum(v["comments"] for v in agg.values()),
    }
    return ig_id, page_id, dict(agg), total


def _print_report(ig_id: str, page_id: str, agg: dict[str, dict[str, float]], total: dict[str, float]) -> None:
    order = ["Reel", "Single image", "Carousel", "Feed video (not Reel)"]
    keys = [k for k in order if k in agg]
    keys += sorted(k for k in agg if k not in order)

    print("\n=== March 2026 aggregate (UTC) — by format ===\n")
    hdr = f"{'Format':<28} {'Posts':>7} {'Reach':>12} {'Views':>12} {'Likes':>10} {'Comments':>10}"
    print(hdr)
    print("-" * len(hdr))
    for k in keys:
        v = agg[k]
        print(
            f"{k:<28} {int(v['posts']):>7} "
            f"{int(v['reach']):>12} {int(v['views']):>12} "
            f"{int(v['likes']):>10} {int(v['comments']):>10}"
        )
    print("-" * len(hdr))
    print(
        f"{'TOTAL':<28} {int(total['posts']):>7} "
        f"{int(total['reach']):>12} {int(total['views']):>12} "
        f"{int(total['likes']):>10} {int(total['comments']):>10}"
    )
    print(
        "\nNotes:\n"
        "- Reach/views come from /insights where the API returns them; empty insight sets "
        "show as 0 for that metric.\n"
        "- Meta does not expose album (carousel) insights the same way as single posts; "
        "carousel rows may show reach/views as 0 while likes/comments use media fields.\n"
        "- Likes/comments use insights when available, otherwise like_count/comments_count "
        "on the media object."
    )


def _streamlit_script_context() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        return get_script_run_ctx() is not None
    except Exception:
        return False


def _agg_to_dataframe(agg: dict[str, dict[str, float]], total: dict[str, float]) -> pd.DataFrame:
    order = ["Reel", "Single image", "Carousel", "Feed video (not Reel)"]
    keys = [k for k in order if k in agg]
    keys += sorted(k for k in agg if k not in order)
    rows = []
    for k in keys:
        v = agg[k]
        rows.append(
            {
                "Format": k,
                "Posts": int(v["posts"]),
                "Reach": int(v["reach"]),
                "Views": int(v["views"]),
                "Likes": int(v["likes"]),
                "Comments": int(v["comments"]),
            }
        )
    rows.append(
        {
            "Format": "TOTAL",
            "Posts": int(total["posts"]),
            "Reach": int(total["reach"]),
            "Views": int(total["views"]),
            "Likes": int(total["likes"]),
            "Comments": int(total["comments"]),
        }
    )
    return pd.DataFrame(rows)


def _streamlit_app() -> None:
    st.set_page_config(page_title="Instagram — March 2026 report", layout="wide")
    st.title("Instagram — March 2026 by format (UTC)")
    st.caption(
        "Loads `.env` from the project folder. Token: META_SYSTEM_USER_TOKEN or "
        "META_USER_ACCESS_TOKEN. Pagination walks backward through media until before "
        "2026-03-01 — large accounts can take several minutes."
    )

    access_token = _graph_access_token()
    if not access_token:
        st.error("Set META_SYSTEM_USER_TOKEN or META_USER_ACCESS_TOKEN in `.env`.")
        return

    if st.button("Run report", type="primary"):
        prog = st.progress(0)
        cap = st.empty()

        def on_media_page(n: int) -> None:
            cap.caption(f"Listing media (newest first)… Graph API page **{n}**")
            prog.progress(min(0.15, 0.02 + n * 0.02))

        def on_insight(i: int, n: int) -> None:
            cap.caption(f"Post insights **{i}** / **{n}**")
            prog.progress(0.15 + 0.85 * (i / max(n, 1)))

        try:
            ig_id, page_id, agg, total = _build_march_aggregates(
                access_token,
                on_media_page=on_media_page,
                on_insight_progress=on_insight,
            )
        except RuntimeError as e:
            prog.empty()
            cap.empty()
            st.session_state.pop("ig_march_2026_report", None)
            st.error(str(e))
        else:
            prog.progress(1)
            cap.empty()
            prog.empty()
            st.session_state["ig_march_2026_report"] = {
                "ig_id": ig_id,
                "page_id": page_id,
                "agg": agg,
                "total": total,
            }

    saved = st.session_state.get("ig_march_2026_report")
    if isinstance(saved, dict) and "agg" in saved:
        st.success(
            f"Instagram user `{saved['ig_id']}` · Facebook Page `{saved['page_id']}`"
        )
        total = saved["total"]
        if total.get("posts", 0) <= 0:
            st.info("No media published in March 2026 (UTC).")
        else:
            st.dataframe(
                _agg_to_dataframe(saved["agg"], total),
                use_container_width=True,
                hide_index=True,
            )
        with st.expander("Notes"):
            st.markdown(
                "- Reach/views use `/insights` when returned; otherwise 0.\n"
                "- Carousel reach/views are often unavailable from insights.\n"
                "- Likes/comments fall back to media fields when insights omit them."
            )
        if st.button("Clear results"):
            st.session_state.pop("ig_march_2026_report", None)
            st.rerun()


def main() -> int:
    access_token = _graph_access_token()
    if not access_token:
        print(
            "Set META_SYSTEM_USER_TOKEN or META_USER_ACCESS_TOKEN in .env.",
            file=sys.stderr,
        )
        return 1

    print("Resolving Page / Instagram user and fetching March 2026 (UTC)…")
    try:
        ig_id, page_id, agg, total = _build_march_aggregates(access_token)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    print(f"Instagram user id: {ig_id}  |  Page id: {page_id}\n")
    if total["posts"] <= 0:
        print("No media found in March 2026.")
        return 0
    _print_report(ig_id, page_id, agg, total)
    return 0


if __name__ == "__main__":
    if _streamlit_script_context():
        _streamlit_app()
    else:
        raise SystemExit(main())
