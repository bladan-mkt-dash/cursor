"""Streamlit: Meta top posts (YTD) only — **separate** from the YoY followers dashboard.

Run on its **own port** so you can keep both apps open at once, for example:

    streamlit run meta_top_posts_ytd.py --server.port 8511

YoY followers (different app):

    streamlit run meta_followers_yoy_line_chart.py --server.port 8501

This page shows top 7 Instagram + top 7 Facebook by engagement with post type.
It does **not** exclude paid/boosted posts (the YoY app’s Top 10 tables do when ad data loads).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from meta_followers_yoy_line_chart import (
    TOP_POSTS_YTD_N,
    fetch_ytd_top_post_display_tables,
)

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")


def main() -> None:
    st.set_page_config(page_title="Meta — Top posts (YTD)", layout="wide")
    st.title("Meta — Top posts (year-to-date)")
    st.info(
        "**This is a separate app** from `meta_followers_yoy_line_chart.py` (YoY followers). "
        "Run this file on **another port** so both can stay open, e.g. "
        "`streamlit run meta_top_posts_ytd.py --server.port 8511` "
        "while YoY uses `--server.port 8501`."
    )
    st.caption(
        f"Top **{TOP_POSTS_YTD_N}** Instagram, then top **{TOP_POSTS_YTD_N}** Facebook, by engagement. "
        "Cross-posts may appear twice. "
        "Facebook engagement = reactions + comments + shares; Instagram = likes + comments."
    )

    today = date.today()
    default_since = date(today.year, 1, 1)

    with st.sidebar:
        since_d = st.date_input("Posts on or after", value=default_since, max_value=today)
        max_fb = st.number_input("Max Facebook posts to scan", min_value=50, value=300, step=50)
        max_ig = st.number_input(
            "Max Instagram media items to scan", min_value=50, value=400, step=50
        )
        load = st.button("Load / refresh", type="primary")

    since_ts = pd.Timestamp(since_d.isoformat(), tz="UTC")

    if load:
        with st.spinner("Fetching from Meta (can take 30–60+ s)…"):
            fb_table, ig_table, err = fetch_ytd_top_post_display_tables(
                since_ts,
                max_fb_posts=int(max_fb),
                max_ig_posts=int(max_ig),
            )
        if err:
            st.error(err)
            st.stop()
        st.session_state["solo_fb"] = fb_table
        st.session_state["solo_ig"] = ig_table
        st.session_state["solo_since"] = since_d.isoformat()
        st.session_state["solo_params"] = (since_d.isoformat(), int(max_fb), int(max_ig))
    elif "solo_fb" not in st.session_state:
        st.warning("Click **Load / refresh** in the sidebar to fetch posts.")
        st.stop()

    params_now = (since_d.isoformat(), int(max_fb), int(max_ig))
    if (
        st.session_state.get("solo_params") is not None
        and st.session_state.get("solo_params") != params_now
        and not load
    ):
        st.warning("Sidebar changed — click **Load / refresh** to apply (showing cached data below).")

    fb_table: pd.DataFrame = st.session_state["solo_fb"]
    ig_table: pd.DataFrame = st.session_state["solo_ig"]
    if fb_table.empty and ig_table.empty:
        st.warning("No posts in that date range for the scan limits.")
        st.stop()

    st.success(
        f"Posts on or after **{st.session_state['solo_since']}** — "
        f"top **{TOP_POSTS_YTD_N}** per platform."
    )

    col_cfg = {
        "URL": st.column_config.LinkColumn("Link", display_text="Open"),
        "Engagement": st.column_config.NumberColumn(format="%d"),
        "Post type": st.column_config.TextColumn(),
    }

    st.subheader(f"Instagram — top {TOP_POSTS_YTD_N} by engagement")
    if ig_table.empty:
        st.caption("No Instagram rows in range.")
    else:
        st.dataframe(ig_table, hide_index=True, width="stretch", column_config=col_cfg)

    st.subheader(f"Facebook — top {TOP_POSTS_YTD_N} by engagement")
    if fb_table.empty:
        st.caption("No Facebook rows in range.")
    else:
        st.dataframe(fb_table, hide_index=True, width="stretch", column_config=col_cfg)


if __name__ == "__main__":
    main()
