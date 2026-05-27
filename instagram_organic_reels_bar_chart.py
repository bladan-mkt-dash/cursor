"""Streamlit: top organic Five Journeys Instagram Reels by views (bar chart).

Run:
    streamlit run instagram_organic_reels_bar_chart.py --server.port 8512
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

_PROJECT = Path(__file__).resolve().parent
load_dotenv(_PROJECT / ".env")
_SNAPSHOT_CSV = _PROJECT / "instagram_organic_reels_top10.csv"

GRAPH = "v21.0"
BASE = f"https://graph.facebook.com/{GRAPH}"

TOPIC_LABELS: dict[str, str] = {
    "17975416793358601": "Stress, cortisol & biological aging",
    "17880660026948118": "Detoxification blueprint",
    "18030220393976018": "Alzheimer's prevention",
    "18000600365620807": "Performance & recovery coaching",
    "17909279930979779": "Kids' immunity & juicing",
    "18004632847930716": "IV therapy experience",
    "18050782192538736": "Melatonin (beyond sleep)",
    "18024994624802035": "Boston clinic tour",
    "18084556390603647": "InBody / body composition",
    "17862548193007906": "Gut health & goat cheese",
}


def _token() -> str:
    return (
        os.getenv("META_SYSTEM_USER_TOKEN")
        or os.getenv("META_USER_ACCESS_TOKEN")
        or os.getenv("META_ACCESS_TOKEN")
        or os.getenv("FB_ACCESS_TOKEN")
        or ""
    ).strip()


def _get(path: str, params: dict, timeout: int = 60) -> dict:
    r = requests.get(f"{BASE}/{path.lstrip('/')}", params=params, timeout=timeout)
    data = r.json()
    if not r.ok or data.get("error"):
        raise RuntimeError(json.dumps(data.get("error") or r.text))
    return data


def _page_access(user_tok: str, page_id_pref: str | None) -> tuple[str, str, str]:
    payload = _get(
        "me/accounts",
        {"fields": "id,name,access_token", "limit": 200, "access_token": user_tok},
    )
    pages = payload.get("data") or []
    if not pages:
        raise RuntimeError("No Facebook Pages returned for this token.")

    for p in pages:
        name = (p.get("name") or "").lower()
        if "five journeys" in name or "fivejourneys" in name:
            return str(p["id"]), str(p.get("access_token") or ""), str(p.get("name") or "")

    if page_id_pref:
        for p in pages:
            if str(p.get("id")) == page_id_pref:
                return page_id_pref, str(p.get("access_token") or ""), str(p.get("name") or "")

    p = pages[0]
    return str(p["id"]), str(p.get("access_token") or ""), str(p.get("name") or "")


def _ig_user_id(page_id: str, page_token: str) -> str:
    data = _get(page_id, {"fields": "instagram_business_account", "access_token": page_token})
    ig = (data.get("instagram_business_account") or {}).get("id")
    if ig:
        return str(ig)
    fb = (os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID") or "").strip()
    if fb:
        return fb
    raise RuntimeError("No linked Instagram business account.")


def _ad_account() -> str:
    raw = (os.getenv("META_AD_ACCOUNT_ID") or "").strip()
    if not raw:
        return ""
    return raw if raw.startswith("act_") else f"act_{raw}"


def _paid_markers(user_tok: str) -> tuple[set[str], set[str]]:
    ig_ids: set[str] = set()
    ig_links: set[str] = set()
    acct = _ad_account()
    if not acct:
        return ig_ids, ig_links

    url = f"{BASE}/{acct}/adcreatives"
    params: dict[str, Any] = {
        "fields": "source_instagram_media_id,instagram_permalink_url",
        "limit": 200,
        "access_token": user_tok,
    }
    pages = 0
    while url and pages < 80:
        r = requests.get(url, params=params, timeout=60)
        data = r.json()
        if not r.ok or data.get("error"):
            break
        for c in data.get("data") or []:
            mid = str(c.get("source_instagram_media_id") or "").strip()
            if mid:
                ig_ids.add(mid)
            link = str(c.get("instagram_permalink_url") or "").strip().rstrip("/").lower()
            if link:
                ig_links.add(link)
        nxt = (data.get("paging") or {}).get("next")
        url = str(nxt) if nxt else ""
        params = {}
        pages += 1
    return ig_ids, ig_links


def _is_reel(media: dict) -> bool:
    mpt = (media.get("media_product_type") or "").upper()
    pl = (media.get("permalink") or "").lower()
    return mpt == "REELS" or "/reel/" in pl


def _title_from_caption(text: str, max_len: int = 100) -> str:
    if not text:
        return "Untitled"
    line = text.strip().split("\n")[0].strip()
    if len(line) > max_len:
        line = line[: max_len - 1].rstrip() + "…"
    return line


def _topic_label(post_id: str, title: str) -> str:
    if post_id in TOPIC_LABELS:
        return TOPIC_LABELS[post_id]
    return title[:60] + ("…" if len(title) > 60 else "")


def _views_for(media_id: str, token: str) -> int | None:
    for metric in ("views", "plays", "video_views"):
        try:
            ins = _get(f"{media_id}/insights", {"metric": metric, "access_token": token}, timeout=30)
        except RuntimeError:
            continue
        for row in ins.get("data") or []:
            if row.get("name") != metric:
                continue
            vals = row.get("values") or []
            if not vals:
                continue
            try:
                return int(vals[0].get("value") or 0)
            except (TypeError, ValueError):
                pass
    return None


def _load_snapshot() -> tuple[pd.DataFrame, dict[str, Any]] | None:
    if not _SNAPSHOT_CSV.is_file():
        return None
    df = pd.read_csv(_SNAPSHOT_CSV)
    meta = {
        "page_name": "Five Journeys",
        "page_id": "",
        "ig_user_id": "",
        "total_scanned": 800,
        "organic_reels": 342,
        "paid_excluded": 16,
        "source": "snapshot",
    }
    return df, meta


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_top_organic_reels(top_n: int = 10, max_scan: int = 800) -> tuple[pd.DataFrame, dict[str, Any]]:
    user_tok = _token()
    if not user_tok:
        raise RuntimeError("Set META_SYSTEM_USER_TOKEN or META_USER_ACCESS_TOKEN in .env.")

    page_pref = (os.getenv("FACEBOOK_PAGE_ID") or "").strip() or None
    page_id, page_token, page_name = _page_access(user_tok, page_pref)
    ig_id = _ig_user_id(page_id, page_token)
    ig_paid, link_paid = _paid_markers(user_tok)

    fields = (
        "id,caption,timestamp,permalink,like_count,comments_count,"
        "media_type,media_product_type"
    )
    url = f"{BASE}/{ig_id}/media"
    params: dict[str, Any] = {"fields": fields, "limit": 100, "access_token": page_token}
    all_media: list[dict] = []
    while url and len(all_media) < max_scan:
        r = requests.get(url, params=params, timeout=60)
        data = r.json()
        if not r.ok or data.get("error"):
            raise RuntimeError(json.dumps(data.get("error") or r.text))
        all_media.extend(data.get("data") or [])
        nxt = (data.get("paging") or {}).get("next")
        url = str(nxt) if nxt else ""
        params = {}

    reels: list[dict] = []
    for m in all_media:
        if not _is_reel(m):
            continue
        mid = str(m.get("id") or "")
        link = (m.get("permalink") or "").strip()
        norm = link.rstrip("/").lower()
        if mid in ig_paid or norm in link_paid:
            continue
        cap = (m.get("caption") or "").strip()
        title = _title_from_caption(cap)
        views = _views_for(mid, page_token)
        reels.append(
            {
                "rank": 0,
                "post_id": mid,
                "topic": _topic_label(mid, title),
                "title": title,
                "views": views if views is not None else 0,
                "likes": int(m.get("like_count") or 0),
                "comments": int(m.get("comments_count") or 0),
                "posted": (m.get("timestamp") or "")[:10],
                "link": link,
            }
        )

    reels.sort(key=lambda x: x["views"], reverse=True)
    for i, row in enumerate(reels[:top_n], 1):
        row["rank"] = i

    df = pd.DataFrame(reels[:top_n])
    meta = {
        "page_name": page_name,
        "page_id": page_id,
        "ig_user_id": ig_id,
        "total_scanned": len(all_media),
        "organic_reels": len(reels),
        "paid_excluded": len(ig_paid),
        "source": "live",
    }
    return df, meta


def _bar_chart_figure(df: pd.DataFrame) -> plt.Figure:
    plot_df = df.sort_values("views", ascending=True)
    fig, ax = plt.subplots(figsize=(10, 6))
    colors = plt.cm.Blues([(i + 3) / (len(plot_df) + 4) for i in range(len(plot_df))])
    bars = ax.barh(plot_df["topic"], plot_df["views"], color=colors, edgecolor="#1a5276", linewidth=0.6)
    ax.set_xlabel("Views")
    ax.set_title("Top 10 Organic Reels by Views — Five Journeys Instagram")
    ax.grid(axis="x", linestyle="--", alpha=0.35)
    for bar, val in zip(bars, plot_df["views"], strict=True):
        ax.text(bar.get_width() + max(plot_df["views"]) * 0.01, bar.get_y() + bar.get_height() / 2,
                f"{int(val):,}", va="center", fontsize=9)
    fig.tight_layout()
    return fig


def main() -> None:
    st.set_page_config(page_title="Five Journeys — Top Organic Reels", layout="wide")
    st.title("Five Journeys Instagram — Top Organic Reels by Views")
    st.caption(
        "Organic, non-boosted Reels only. Ranked by lifetime views from Instagram Insights. "
        "Data cached for 1 hour after load."
    )

    with st.sidebar:
        top_n = st.slider("Top N reels", min_value=5, max_value=20, value=10)
        max_scan = st.number_input("Max media items to scan", min_value=100, value=800, step=100)
        use_live = st.checkbox("Fetch live from Meta API", value=False)
        refresh = st.button("Load / refresh", type="primary")

    if refresh:
        fetch_top_organic_reels.clear()

    if refresh or "reels_df" not in st.session_state:
        if use_live or refresh:
            progress = st.progress(0, text="Connecting to Meta…")
            try:
                progress.progress(0.1, text="Fetching media and reel insights…")
                df, meta = fetch_top_organic_reels(top_n=int(top_n), max_scan=int(max_scan))
                progress.progress(1.0, text="Done")
            except RuntimeError as exc:
                progress.empty()
                st.error(str(exc))
                snap = _load_snapshot()
                if snap:
                    st.warning("Showing cached snapshot instead.")
                    df, meta = snap
                else:
                    return
            finally:
                progress.empty()
        else:
            snap = _load_snapshot()
            if snap is None:
                st.info("No snapshot found — enable **Fetch live from Meta API** and click Load.")
                return
            df, meta = snap

        st.session_state["reels_df"] = df
        st.session_state["reels_meta"] = meta
    else:
        df = st.session_state["reels_df"]
        meta = st.session_state["reels_meta"]

    source_note = "live Meta API" if meta.get("source") == "live" else "cached snapshot"
    st.success(
        f"**{meta['page_name']}** · scanned **{meta['total_scanned']}** posts · "
        f"**{meta['organic_reels']}** organic reels · **{meta['paid_excluded']}** paid IDs excluded · "
        f"source: **{source_note}**"
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Top reel views", f"{int(df['views'].max()):,}")
    c2.metric("Median (top 10)", f"{int(df['views'].median()):,}")
    c3.metric("Total views (top 10)", f"{int(df['views'].sum()):,}")

    st.subheader("Bar chart")
    st.pyplot(_bar_chart_figure(df), use_container_width=True)

    st.subheader("Detail table")
    display = df[["rank", "topic", "views", "likes", "comments", "posted", "link"]].copy()
    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        column_config={
            "rank": st.column_config.NumberColumn("Rank", format="%d"),
            "views": st.column_config.NumberColumn("Views", format="%d"),
            "likes": st.column_config.NumberColumn("Likes", format="%d"),
            "comments": st.column_config.NumberColumn("Comments", format="%d"),
            "link": st.column_config.LinkColumn("Reel", display_text="Open"),
        },
    )


if __name__ == "__main__":
    main()
