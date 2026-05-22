"""Streamlit: top organic Meta posts (women's health text), ranked by interactions then impressions.

Run (use its own port if other Streamlit apps are open):

    streamlit run meta_womens_health_top_posts_table.py --server.port 8512
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from meta_top_womens_health_organic_posts import fetch_womens_health_organic_ranked

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")


def main() -> None:
    st.set_page_config(page_title="Meta — Women's health top posts", layout="wide")
    st.title("Meta — top organic posts (women's health)")
    st.caption(
        "Organic only (paid/boosted post IDs excluded when ad creatives load). "
        "Text filter: women's-health–related keywords. "
        "Rank: **interactions** (FB: reactions + comments + shares; IG: likes + comments), "
        "then **impressions** when the API returns them."
    )

    with st.sidebar:
        top_n = st.number_input("Rows to show", min_value=1, max_value=25, value=5, step=1)
        max_scan = st.number_input(
            "Max posts to scan (per platform)", min_value=50, value=400, step=50
        )
        load = st.button("Load / refresh", type="primary")

    if load:
        with st.spinner("Calling Meta Graph API (can take 1–2 minutes)…"):
            top, info = fetch_womens_health_organic_ranked(
                top_n=int(top_n),
                max_scan=int(max_scan),
            )
        st.session_state["wh_top"] = top
        st.session_state["wh_info"] = info
    elif "wh_top" not in st.session_state:
        st.info("Click **Load / refresh** in the sidebar to fetch from Meta.")
        st.stop()

    top = st.session_state.get("wh_top") or []
    info = st.session_state.get("wh_info") or {}

    for err in info.get("errors") or []:
        st.warning(err)

    if info.get("page_id"):
        st.caption(
            f"Facebook Page `{info.get('page_id')}` · Instagram user `{info.get('ig_user_id')}` · "
            f"**{info.get('matched_count', 0)}** posts matched the filter in this scan."
        )

    if not top:
        st.error("No rows to show. Check tokens, permissions, or broaden the scan.")
        st.stop()

    rows_out: list[dict[str, object]] = []
    for i, row in enumerate(top, 1):
        imp = row.get("impressions", -1)
        imp_disp: object = int(imp) if isinstance(imp, int) and imp >= 0 else "n/a"
        preview = (row.get("text") or "").replace("\n", " ").strip()
        if len(preview) > 400:
            preview = preview[:397] + "…"
        rows_out.append(
            {
                "Rank": i,
                "Platform": row.get("platform", ""),
                "Interactions": int(row.get("interactions") or 0),
                "Impressions": imp_disp,
                "Posted": str(row.get("created_time") or ""),
                "URL": str(row.get("permalink") or ""),
                "Preview": preview or "—",
            }
        )

    df = pd.DataFrame(rows_out)
    st.dataframe(
        df,
        column_config={
            "URL": st.column_config.LinkColumn("URL", display_text="Open"),
            "Preview": st.column_config.TextColumn("Preview", width="large"),
        },
        hide_index=True,
        use_container_width=True,
    )


if __name__ == "__main__":
    main()
