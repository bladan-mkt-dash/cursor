"""Streamlit: GoHighLevel contacts with cancellation date in Q1 2026."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from ghl_client import (
    contact_custom_field_value,
    fetch_contacts_cancellation_date_in_range,
)

load_dotenv(Path(__file__).resolve().parent / ".env")

Q1_2026_SINCE = "2026-01-01"
Q1_2026_UNTIL = "2026-03-31"


def _week_start_monday(d: date) -> date:
    """Calendar week beginning Monday (not ISO week-year)."""
    return d - timedelta(days=d.weekday())


def _parse_cancel_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


st.set_page_config(page_title="GHL Q1 2026 cancellations", layout="wide")
st.title("GoHighLevel — cancellations in Q1 2026")
st.caption(
    "Contacts whose **Membership Cancellation Date** (or configured field) falls "
    f"between **{Q1_2026_SINCE}** and **{Q1_2026_UNTIL}** inclusive. "
    "Set `GHL_CANCELLATION_DATE_FIELD_ID` in `.env` if the field is not found by name."
)

ghl_location_id = os.getenv("GHL_LOCATION_ID", "").strip()

with st.spinner("Loading contacts from GoHighLevel…"):
    try:
        data = fetch_contacts_cancellation_date_in_range(
            Q1_2026_SINCE,
            Q1_2026_UNTIL,
            location_id=ghl_location_id or None,
        )
    except Exception as e:
        st.error(str(e))
        st.stop()

contacts = data["contacts"]
cancel_fid = data["cancellation_field_id"]
mid = data.get("membership_level_field_id") or ""
total_api = int(data.get("total_reported") or 0)

st.metric("Records (cancellation date in Q1 2026)", f"{len(contacts):,}")
if total_api and total_api != len(contacts):
    st.warning(f"API reported total {total_api:,}; loaded {len(contacts):,} — counts should match after pagination.")
if data.get("truncated_pages"):
    st.warning(
        "Stopped at the pagination safety cap; raise `max_pages` in "
        "`search_contacts_custom_field_date_range` if needed."
    )

if not contacts:
    st.info("No contacts matched this cancellation date range.")
    st.stop()

rows = []
for c in contacts:
    raw = contact_custom_field_value(c, cancel_fid)
    d = _parse_cancel_date(raw)
    rows.append(
        {
            "Cancellation date": d,
            "Membership level": (
                (contact_custom_field_value(c, mid).strip() if mid else "") or "(blank)"
            ),
            "First name": (c.get("firstName") or "") or "",
            "Last name": (c.get("lastName") or "") or "",
            "Email": (c.get("email") or "") or "",
        }
    )

df = pd.DataFrame(rows)
unknown = df["Cancellation date"].isna().sum()
if unknown:
    st.warning(f"{unknown} contact(s) had a cancellation value that could not be parsed for charting.")

# Weekly line chart: x = Monday (week-of date), all weeks overlapping Q1, zeros filled
q1_start = date.fromisoformat(Q1_2026_SINCE)
q1_end = date.fromisoformat(Q1_2026_UNTIL)
first_mon = _week_start_monday(q1_start)
last_mon = _week_start_monday(q1_end)
week_mondays: list[date] = []
cur = first_mon
while cur <= last_mon:
    week_mondays.append(cur)
    cur += timedelta(days=7)

parsed_dates = df[df["Cancellation date"].notna()].copy()
if parsed_dates.empty:
    st.info("No parsable dates for aggregation.")
else:
    parsed_dates["Week of (Monday)"] = pd.to_datetime(
        parsed_dates["Cancellation date"].apply(_week_start_monday)
    )
    week_counts = (
        parsed_dates.groupby("Week of (Monday)", as_index=False)
        .size()
        .rename(columns={"size": "Cancellations"})
    )
    full_weeks = pd.DataFrame(
        {"Week of (Monday)": pd.to_datetime(week_mondays)}
    )
    week_line = full_weeks.merge(week_counts, on="Week of (Monday)", how="left")
    week_line["Cancellations"] = week_line["Cancellations"].fillna(0).astype(int)
    week_line["Week label"] = week_line["Week of (Monday)"].dt.strftime("%b %d, %Y")

    chart = (
        alt.Chart(week_line)
        .mark_line(point=True, interpolate="monotone")
        .encode(
            x=alt.X(
                "Week of (Monday):T",
                title="Week of (Monday)",
                axis=alt.Axis(format="%b %d", labelAngle=-45),
            ),
            y=alt.Y("Cancellations:Q", title="Cancellations"),
            tooltip=[
                alt.Tooltip("Week label:N", title="Week of (Monday)"),
                alt.Tooltip("Cancellations:Q", title="Count", format=","),
            ],
        )
        .properties(height=360)
    )
    st.subheader("Cancellations by week (Q1 2026)")
    st.caption("Each point is the Monday starting that week; weeks with no cancellations show as zero.")
    st.altair_chart(chart, use_container_width=True)

# Optional: by membership level
if mid:
    level_agg = (
        df.groupby("Membership level", dropna=False)
        .size()
        .reset_index(name="Records")
        .sort_values("Records", ascending=False)
    )
    pie = (
        alt.Chart(level_agg)
        .mark_arc(innerRadius=40)
        .encode(
            theta=alt.Theta("Records:Q", title="Records"),
            color=alt.Color("Membership level:N", legend=alt.Legend(title="Membership")),
            tooltip=[
                alt.Tooltip("Membership level:N", title="Level"),
                alt.Tooltip("Records:Q", title="Records", format=","),
            ],
        )
        .properties(height=380)
    )
    st.subheader("By membership level")
    st.altair_chart(pie, use_container_width=True)

show_table = st.checkbox("Show all records", value=False)
if show_table:
    display_df = df.copy()
    display_df["Cancellation date"] = display_df["Cancellation date"].apply(
        lambda x: x.isoformat() if isinstance(x, date) else ""
    )
    st.dataframe(display_df, use_container_width=True, hide_index=True)
