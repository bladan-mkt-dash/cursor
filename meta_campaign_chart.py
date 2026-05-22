"""Streamlit: Meta campaign + GHL Facebook/Instagram hear-about-us (META_* and GHL_* from .env)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from ghl_client import fetch_facebook_instagram_conversions
from meta_client import ZM_PRIMARY_CARE_FEB_2026_CAMPAIGN_NAME, fetch_campaign_daily_insights

# Resolves to the same GHL field as "How did you hear about us?" (picklist: Facebook / Instagram).
GHL_HEAR_ABOUT_FIELD_NAME = "How Did You Hear About Us"


def _linear_trend(dates: pd.Series, y: pd.Series) -> pd.Series:
    """Ordinary least squares line over elapsed time from the first day (seconds since min date)."""
    yf = y.astype(float)
    if len(yf) < 2:
        return yf
    xd = (dates - dates.min()).dt.total_seconds().astype(float)
    m, b = np.polyfit(xd.to_numpy(), yf.to_numpy(), 1)
    return pd.Series(m * xd.to_numpy() + b, index=y.index)


def _daily_line_with_trend(
    frame: pd.DataFrame,
    *,
    value_col: str,
    trend_col: str,
    y_title: str,
    chart_title: str,
    value_format: str,
):
    x_enc = alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d"))
    line = (
        alt.Chart(frame)
        .mark_line(point=True)
        .encode(
            x=x_enc,
            y=alt.Y(f"{value_col}:Q", title=y_title),
            tooltip=[
                alt.Tooltip("date:T", title="Date", format="%Y-%m-%d"),
                alt.Tooltip(f"{value_col}:Q", title=y_title, format=value_format),
            ],
        )
    )
    trend = (
        alt.Chart(frame)
        .mark_line(strokeDash=[8, 4], color="#c44e52", strokeWidth=2)
        .encode(x=x_enc, y=alt.Y(f"{trend_col}:Q", title=y_title))
    )
    return (line + trend).properties(height=420, title=chart_title)

load_dotenv(Path(__file__).resolve().parent / ".env")

_today = pd.Timestamp.today().date()
_latest_in_range = _today - timedelta(days=1)
_default_start = min(pd.Timestamp("2026-02-01").date(), _latest_in_range)

st.set_page_config(page_title="ZM Primary Care — Meta + GHL", layout="wide")
st.title("ZM Primary Care — Meta + GHL (Facebook / Instagram)")
st.caption(
    "Meta: `META_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID`. "
    "GHL: `GHL_ACCESS_TOKEN`, `GHL_LOCATION_ID`, optional `GHL_HEAR_ABOUT_US_FIELD_ID`. "
    "Hear-about-us field must include picklist values **Facebook** and **Instagram**."
)

with st.sidebar:
    campaign_name = st.text_input(
        "Campaign name (exact)",
        value=ZM_PRIMARY_CARE_FEB_2026_CAMPAIGN_NAME,
    )
    since = st.date_input(
        "Start date",
        value=_default_start,
        max_value=_latest_in_range,
    )
    until = st.date_input(
        "End date (excludes today)",
        value=_latest_in_range,
        max_value=_latest_in_range,
    )

if not campaign_name.strip():
    st.warning("Enter a campaign name.")
    st.stop()

try:
    report = fetch_campaign_daily_insights(
        campaign_name.strip(),
        since=since.isoformat(),
        until=until.isoformat(),
    )
except Exception as e:
    st.error(str(e))
    st.stop()

days = report["days"]
totals = report["totals"]

st.subheader(report["campaign_name"])
st.caption(f"Campaign ID: `{report['campaign_id']}`")

m1, m2, m3 = st.columns(3)
m1.metric("Total impressions (period)", f"{totals['impressions']:,}")
m2.metric("Total clicks (period)", f"{totals['clicks']:,}")
m3.metric("Total spend (period)", f"${totals['spend']:,.2f}")

if not days:
    st.info("No insight rows returned for this date range.")
    st.stop()

df = pd.DataFrame(days)
df["date"] = pd.to_datetime(df["date_start"])
df = df.sort_values("date").reset_index(drop=True)
df["impressions_trend"] = _linear_trend(df["date"], df["impressions"])
df["clicks_trend"] = _linear_trend(df["date"], df["clicks"])
df["spend_trend"] = _linear_trend(df["date"], df["spend"])

impressions_chart = _daily_line_with_trend(
    df,
    value_col="impressions",
    trend_col="impressions_trend",
    y_title="Impressions",
    chart_title="Impressions by day",
    value_format=",",
)

clicks_chart = _daily_line_with_trend(
    df,
    value_col="clicks",
    trend_col="clicks_trend",
    y_title="Clicks",
    chart_title="Clicks by day",
    value_format=",",
)

spend_chart = _daily_line_with_trend(
    df,
    value_col="spend",
    trend_col="spend_trend",
    y_title="Spend ($)",
    chart_title="Spend by day",
    value_format="$,.2f",
)

st.altair_chart(impressions_chart, width="stretch")
st.altair_chart(clicks_chart, width="stretch")
st.altair_chart(spend_chart, width="stretch")

with st.expander("Meta — daily table"):
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

st.divider()
st.subheader("GoHighLevel — How did you hear about us?")
st.caption(
    "Contacts with that field equal to **Facebook** or **Instagram**, counted by **date added** (UTC), "
    f"same date range as above. Field label used for lookup: {GHL_HEAR_ABOUT_FIELD_NAME!r}."
)

try:
    ghl = fetch_facebook_instagram_conversions(
        since.isoformat(),
        until.isoformat(),
        field_name=GHL_HEAR_ABOUT_FIELD_NAME,
    )
except Exception as e:
    st.error(f"GHL: {e}")
else:
    if ghl.get("truncated_pages"):
        st.warning(
            "GHL search hit the pagination safety cap; daily counts may be incomplete."
        )

    bs = ghl["by_source"]
    g1, g2, g3 = st.columns(3)
    g1.metric("GHL — Facebook (period)", f"{bs.get('Facebook', 0):,}")
    g2.metric("GHL — Instagram (period)", f"{bs.get('Instagram', 0):,}")
    g3.metric("GHL — Total FB + IG (period)", f"{len(ghl['contacts']):,}")

    ghl_df = pd.DataFrame(ghl["daily"])
    ghl_df["date"] = pd.to_datetime(ghl_df["date_start"])
    long_ghl = ghl_df.melt(
        id_vars=["date"],
        value_vars=["facebook", "instagram"],
        var_name="source_key",
        value_name="contacts",
    )
    long_ghl["source"] = long_ghl["source_key"].map(
        {"facebook": "Facebook", "instagram": "Instagram"}
    )

    ghl_chart = (
        alt.Chart(long_ghl)
        .mark_line(point=True)
        .encode(
            x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d")),
            y=alt.Y("contacts:Q", title="New contacts"),
            color=alt.Color("source:N", legend=alt.Legend(title="Hear about us")),
            tooltip=[
                alt.Tooltip("date:T", title="Date", format="%Y-%m-%d"),
                alt.Tooltip("source:N", title="Source"),
                alt.Tooltip("contacts:Q", title="Contacts", format=","),
            ],
        )
        .properties(
            height=420,
            title="GHL — Facebook / Instagram by day (date added, UTC)",
        )
    )
    st.altair_chart(ghl_chart, width="stretch")

    with st.expander("GHL — daily counts"):
        st.dataframe(
            ghl_df[["date_start", "facebook", "instagram", "total"]].rename(
                columns={
                    "date_start": "Date",
                    "facebook": "Facebook",
                    "instagram": "Instagram",
                    "total": "Total",
                }
            ),
            width="stretch",
            hide_index=True,
        )
