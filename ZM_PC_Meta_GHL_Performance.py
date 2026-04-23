"""Streamlit: Meta campaign + GHL Facebook/Instagram hear-about-us (META_* and GHL_* from .env)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from ghl_client import fetch_facebook_instagram_conversions
from meta_client import ZM_PRIMARY_CARE_FEB_2026_CAMPAIGN_NAME, fetch_campaign_daily_insights

# Resolves to the same GHL field as "How did you hear about us?" (picklist: Facebook / Instagram).
GHL_HEAR_ABOUT_FIELD_NAME = "How Did You Hear About Us"

load_dotenv(Path(__file__).resolve().parent / ".env")

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
    since = st.date_input("Start date", value=pd.Timestamp("2026-02-01").date())
    default_until = (pd.Timestamp.today() - pd.Timedelta(days=1)).date()
    until = st.date_input("End date (today excluded)", value=default_until)

if not campaign_name.strip():
    st.warning("Enter a campaign name.")
    st.stop()

today = pd.Timestamp.today().date()
effective_until = min(until, today - timedelta(days=1))
if effective_until < since:
    st.warning("Date range is invalid after excluding today. Pick an earlier end date.")
    st.stop()
if until >= today:
    st.info(f"Today is excluded. Using end date: {effective_until.isoformat()}.")

try:
    report = fetch_campaign_daily_insights(
        campaign_name.strip(),
        since=since.isoformat(),
        until=effective_until.isoformat(),
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
df["cpc"] = df.apply(
    lambda r: (float(r["spend"]) / float(r["clicks"])) if float(r["clicks"]) > 0 else 0.0,
    axis=1,
)

def _with_trendline(
    data: pd.DataFrame,
    *,
    y_field: str,
    y_title: str,
    chart_title: str,
    tooltip_format: str,
) -> alt.Chart:
    base = (
        alt.Chart(data)
        .mark_line(point=True)
        .encode(
            x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d")),
            y=alt.Y(f"{y_field}:Q", title=y_title),
            tooltip=[
                alt.Tooltip("date:T", title="Date", format="%Y-%m-%d"),
                alt.Tooltip(f"{y_field}:Q", title=y_title, format=tooltip_format),
            ],
        )
    )
    trend = (
        alt.Chart(data)
        .transform_regression("date", y_field)
        .mark_line(color="#d62728", strokeDash=[6, 4], size=2)
        .encode(
            x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d")),
            y=alt.Y(f"{y_field}:Q", title=y_title),
        )
    )
    return (base + trend).properties(height=420, title=chart_title)


impressions_chart = _with_trendline(
    df,
    y_field="impressions",
    y_title="Impressions",
    chart_title="Impressions by day",
    tooltip_format=",",
)

clicks_chart = _with_trendline(
    df,
    y_field="clicks",
    y_title="Clicks",
    chart_title="Clicks by day",
    tooltip_format=",",
)

spend_chart = _with_trendline(
    df,
    y_field="spend",
    y_title="Spend ($)",
    chart_title="Spend by day",
    tooltip_format="$,.2f",
)

cpc_chart = _with_trendline(
    df,
    y_field="cpc",
    y_title="Cost Per Click ($)",
    chart_title="Cost Per Click by day",
    tooltip_format="$,.2f",
)

st.altair_chart(impressions_chart, width="stretch")
st.altair_chart(clicks_chart, width="stretch")
st.altair_chart(spend_chart, width="stretch")
st.altair_chart(cpc_chart, width="stretch")

with st.expander("Meta — daily table"):
    st.dataframe(
        df[["date_start", "impressions", "clicks", "spend", "cpc"]].rename(
            columns={
                "date_start": "Date",
                "impressions": "Impressions",
                "clicks": "Clicks",
                "spend": "Spend",
                "cpc": "Cost Per Click",
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
        effective_until.isoformat(),
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
    cpa_df = pd.DataFrame(ghl["daily"]).merge(
        df[["date_start", "spend"]],
        on="date_start",
        how="left",
    )
    cpa_df["spend"] = cpa_df["spend"].fillna(0.0)
    cpa_df["cpa"] = cpa_df.apply(
        lambda r: (float(r["spend"]) / float(r["total"])) if float(r["total"]) > 0 else 0.0,
        axis=1,
    )
    with_conv = cpa_df[cpa_df["total"] > 0]
    avg_cpa_with_conv = float(with_conv["cpa"].mean()) if not with_conv.empty else 0.0
    overall_campaign_cpa = (
        float(cpa_df["spend"].sum()) / float(cpa_df["total"].sum())
        if float(cpa_df["total"].sum()) > 0
        else 0.0
    )

    g1, g2, g3, g4, g5 = st.columns(5)
    g1.metric("GHL — Facebook (period)", f"{bs.get('Facebook', 0):,}")
    g2.metric("GHL — Instagram (period)", f"{bs.get('Instagram', 0):,}")
    g3.metric("GHL — Total FB + IG (period)", f"{len(ghl['contacts']):,}")
    g4.metric("Avg CPA (days with conversions)", f"${avg_cpa_with_conv:,.2f}")
    g5.metric("Campaign CPA (all days)", f"${overall_campaign_cpa:,.2f}")

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
