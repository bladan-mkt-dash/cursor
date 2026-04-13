"""Streamlit: GHL contacts — How did you hear about us? WOM vs Google by date added (monthly)."""

from __future__ import annotations

import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from ghl_client import (
    HEAR_ABOUT_US_FIELD_NAME,
    fetch_cancellation_counts_by_month,
    fetch_hear_about_wom_google_monthly_by_date_added,
)

load_dotenv(Path(__file__).resolve().parent / ".env")

DEFAULT_SINCE = "2025-09-01"
DEFAULT_UNTIL = "2026-03-31"

CHART_TITLE = "Discovery Calls Scheduled - Word of Mouth vs. Google"
CHART2_TITLE = "WOM-Generated Dcs vs. Membership Cancellations"
st.set_page_config(page_title=CHART_TITLE, layout="wide")
st.title(CHART_TITLE)
st.caption(
    f"**Contacts** with **{HEAR_ABOUT_US_FIELD_NAME}** filled in, created between your dates "
    "(UTC **date added**). **Word of mouth** = answer contains *word of mouth*; **Google** = contains *google*. "
    "Other values are excluded. Set `GHL_HEAR_ABOUT_US_FIELD_ID` if the field is not found by name."
)

ghl_location_id = os.getenv("GHL_LOCATION_ID", "").strip()
col_a, col_b = st.columns(2)
since = col_a.text_input("From (YYYY-MM-DD)", value=DEFAULT_SINCE)
until = col_b.text_input("Through (YYYY-MM-DD)", value=DEFAULT_UNTIL)

cancel_load_error: str | None = None
cancel_data: dict | None = None
with st.spinner("Loading contacts from GoHighLevel…"):
    try:
        data = fetch_hear_about_wom_google_monthly_by_date_added(
            since.strip(),
            until.strip(),
            location_id=ghl_location_id or None,
        )
    except Exception as e:
        st.error(str(e))
        st.stop()
    try:
        cancel_data = fetch_cancellation_counts_by_month(
            since.strip(),
            until.strip(),
            location_id=ghl_location_id or None,
        )
    except Exception as e:
        cancel_load_error = str(e)

monthly = data["monthly"]
by_ch = data.get("by_channel") or {}
total_api = int(data.get("total_reported_in_range") or 0)
loaded = int(data.get("contacts_loaded") or 0)

st.caption(
    f"Custom field id: `{data.get('field_id', '')}` · "
    f"API total in date range: **{total_api:,}** · Loaded: **{loaded:,}**"
)

m1, m2, m3 = st.columns(3)
m1.metric("Word of mouth", f"{int(by_ch.get('Word of mouth', 0)):,}")
m2.metric("Google", f"{int(by_ch.get('Google', 0)):,}")
m3.metric("Other values (excluded)", f"{int(data.get('other_hear_about_in_range', 0)):,}")

if total_api and loaded != total_api:
    st.warning(
        f"Loaded {loaded:,} of {total_api:,} contacts in range — increase `max_pages` in "
        "`search_contacts_date_added_range` if needed."
    )
if data.get("truncated_pages"):
    st.warning(
        "Pagination stopped at the safety cap; raise `max_pages` in "
        "`search_contacts_date_added_range` to load everyone."
    )
if int(data.get("blank_hear_about_in_range", 0) or 0):
    st.caption(
        f"{int(data['blank_hear_about_in_range']):,} contact(s) in range had a blank hear-about field (excluded)."
    )

if not monthly:
    st.info("No months in range.")
    st.stop()

df = pd.DataFrame(monthly)
# One categorical position per calendar month (avoids duplicate temporal ticks from two series).
month_order = df["month_label"].tolist()
long_df = df.melt(
    id_vars=["month_label"],
    value_vars=["word_of_mouth", "google"],
    var_name="_k",
    value_name="Contacts",
)
long_df["Channel"] = long_df["_k"].map(
    {"word_of_mouth": "Word of mouth", "google": "Google"}
)

chart = (
    alt.Chart(long_df)
    .mark_line(point=True, interpolate="monotone")
    .encode(
        x=alt.X(
            "month_label:O",
            title="Month (date added)",
            sort=month_order,
            axis=alt.Axis(labelAngle=0),
        ),
        y=alt.Y("Contacts:Q", title="Discovery Calls"),
        color=alt.Color(
            "Channel:N",
            title="Channel",
            scale=alt.Scale(
                domain=["Word of mouth", "Google"],
                range=["#59a14f", "#4e79a7"],
            ),
        ),
        tooltip=[
            alt.Tooltip("month_label:N", title="Month"),
            alt.Tooltip("Channel:N", title="Channel"),
            alt.Tooltip("Contacts:Q", title="Discovery calls", format=","),
        ],
    )
    .properties(height=440)
)

st.altair_chart(chart, use_container_width=True)

st.divider()
st.subheader(CHART2_TITLE)
st.caption(
    "**WOM-generated DCs** use the same monthly counts as the Word of mouth line above (contacts **date added**, hear-about contains *word of mouth*). "
    "**Membership cancellations** are contacts whose **Membership Cancellation Date** custom field falls in the same date range, "
    "bucketed by **month of that cancellation date**. Set `GHL_CANCELLATION_DATE_FIELD_ID` if the field is not found by name."
)

if cancel_load_error:
    st.error(cancel_load_error)
elif cancel_data is not None:
    if int(cancel_data.get("unparseable_cancellation_dates") or 0):
        st.warning(
            f"{int(cancel_data['unparseable_cancellation_dates']):,} cancellation record(s) could not be parsed for monthly buckets."
        )
    if cancel_data.get("truncated_pages"):
        st.warning(
            "Cancellation search hit the pagination cap; raise `max_pages` in "
            "`search_contacts_custom_field_date_range` if needed."
        )
    st.caption(
        f"Cancellation field id: `{cancel_data.get('cancellation_field_id', '')}` · "
        f"Loaded: **{int(cancel_data.get('contacts_loaded') or 0):,}**"
    )

    cmp = df.copy()
    cmp["cancellations"] = [row["cancellations"] for row in cancel_data["monthly"]]
    long2 = cmp.melt(
        id_vars=["month_label"],
        value_vars=["word_of_mouth", "cancellations"],
        var_name="_k",
        value_name="Count",
    )
    long2["Series"] = long2["_k"].map(
        {
            "word_of_mouth": "WOM-Generated Dcs",
            "cancellations": "Membership Cancellations",
        }
    )

    chart2 = (
        alt.Chart(long2)
        .mark_line(point=True, interpolate="monotone")
        .encode(
            x=alt.X(
                "month_label:O",
                title="Month",
                sort=month_order,
                axis=alt.Axis(labelAngle=0),
            ),
            y=alt.Y("Count:Q", title="Count"),
            color=alt.Color(
                "Series:N",
                title="Series",
                scale=alt.Scale(
                    domain=["WOM-Generated Dcs", "Membership Cancellations"],
                    range=["#59a14f", "#e45756"],
                ),
            ),
            tooltip=[
                alt.Tooltip("month_label:N", title="Month"),
                alt.Tooltip("Series:N", title="Series"),
                alt.Tooltip("Count:Q", title="Count", format=","),
            ],
        )
        .properties(height=440)
    )
    st.altair_chart(chart2, use_container_width=True)
