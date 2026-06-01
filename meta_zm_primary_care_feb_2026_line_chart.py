"""Streamlit line charts for ZM Primary Care Lead Form | Feb. 2026 campaign."""

from __future__ import annotations

from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from meta_client import (
    ZM_PRIMARY_CARE_FEB_2026_CAMPAIGN_NAME,
    fetch_campaign_daily_insights,
)

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

st.set_page_config(
    page_title="Meta Campaign Line Chart - ZM Primary Care",
    layout="wide",
)
st.title("Meta Ads - ZM Primary Care Lead Form l Feb. 2026")
st.caption(
    "Daily campaign performance from Meta Ads API. "
    "Requires `META_ACCESS_TOKEN` and `META_AD_ACCOUNT_ID` in `.env`."
)

campaign_name = ZM_PRIMARY_CARE_FEB_2026_CAMPAIGN_NAME

with st.sidebar:
    st.text_input("Campaign", value=campaign_name, disabled=True)
    since = st.date_input("Start date", value=pd.Timestamp("2026-02-01").date())
    until = st.date_input("End date", value=pd.Timestamp("2026-02-28").date())

try:
    report = fetch_campaign_daily_insights(
        campaign_name=campaign_name,
        since=since.isoformat(),
        until=until.isoformat(),
    )
except Exception as exc:
    st.error(str(exc))
    st.stop()

days = report["days"]
totals = report["totals"]

st.subheader(report["campaign_name"])
st.caption(f"Campaign ID: `{report['campaign_id']}`")

m1, m2, m3 = st.columns(3)
m1.metric("Impressions (period)", f"{totals['impressions']:,}")
m2.metric("Clicks (period)", f"{totals['clicks']:,}")
m3.metric("Spend (period)", f"${totals['spend']:,.2f}")

if not days:
    st.info("No insight rows returned for this date range.")
    st.stop()

df = pd.DataFrame(days)
df["date"] = pd.to_datetime(df["date_start"])

impressions_chart = (
    alt.Chart(df)
    .mark_line(point=True)
    .encode(
        x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d")),
        y=alt.Y("impressions:Q", title="Impressions"),
        tooltip=[
            alt.Tooltip("date:T", title="Date", format="%Y-%m-%d"),
            alt.Tooltip("impressions:Q", title="Impressions", format=","),
        ],
    )
    .properties(height=350, title="Impressions by day")
)

clicks_chart = (
    alt.Chart(df)
    .mark_line(point=True)
    .encode(
        x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d")),
        y=alt.Y("clicks:Q", title="Clicks"),
        tooltip=[
            alt.Tooltip("date:T", title="Date", format="%Y-%m-%d"),
            alt.Tooltip("clicks:Q", title="Clicks", format=","),
        ],
    )
    .properties(height=350, title="Clicks by day")
)

spend_chart = (
    alt.Chart(df)
    .mark_line(point=True)
    .encode(
        x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d")),
        y=alt.Y("spend:Q", title="Spend ($)"),
        tooltip=[
            alt.Tooltip("date:T", title="Date", format="%Y-%m-%d"),
            alt.Tooltip("spend:Q", title="Spend", format="$,.2f"),
        ],
    )
    .properties(height=350, title="Spend by day")
)

st.altair_chart(impressions_chart, width="stretch")
st.altair_chart(clicks_chart, width="stretch")
st.altair_chart(spend_chart, width="stretch")

with st.expander("Daily values"):
    st.dataframe(
        df[["date_start", "impressions", "clicks", "spend"]].rename(
            columns={
                "date_start": "Date",
                "impressions": "Impressions",
                "clicks": "Clicks",
                "spend": "Spend",
            }
        ),
        width="stretch",
        hide_index=True,
    )
