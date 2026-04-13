import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from ghl_client import contact_custom_field_value, fetch_committed_yes_contacts

load_dotenv(Path(__file__).resolve().parent / ".env")

st.set_page_config(page_title="Marketing reporting", layout="wide")
st.title("Marketing reporting dashboard")

st.subheader("GoHighLevel — Committed = Yes (by membership level)")
st.caption(
    "All contacts where custom field **Committed?** is **Yes**, shown as a pie chart "
    "by **Membership Level**. Optional: set `GHL_COMMITTED_FIELD_ID` or "
    "`GHL_MEMBERSHIP_LEVEL_FIELD_ID` in `.env` if a field is not found by name."
)

ghl_location_id = os.getenv("GHL_LOCATION_ID", "").strip()

try:
    data = fetch_committed_yes_contacts(location_id=ghl_location_id or None)
except Exception as e:
    st.error(str(e))
    st.stop()

contacts = data["contacts"]
mid = data.get("membership_level_field_id") or ""

st.metric("Committed = Yes (total records)", f"{len(contacts):,}")
if data.get("truncated_pages"):
    st.warning(
        "Search stopped at the pagination safety cap; counts may be incomplete. "
        "Raise `max_pages` in `fetch_committed_yes_contacts` if needed."
    )

if not contacts:
    st.info("No contacts matched Committed = Yes.")
else:
    levels: list[str] = []
    for c in contacts:
        if mid:
            lv = contact_custom_field_value(c, mid).strip()
        else:
            lv = ""
        levels.append(lv if lv else "(blank)")

    agg = (
        pd.DataFrame({"Membership Level": levels})
        .groupby("Membership Level")
        .size()
        .reset_index(name="Records")
    )

    pie = (
        alt.Chart(agg)
        .mark_arc(innerRadius=50)
        .encode(
            theta=alt.Theta("Records:Q", title="Records"),
            color=alt.Color("Membership Level:N", legend=alt.Legend(title="Membership level")),
            tooltip=[
                alt.Tooltip("Membership Level:N", title="Membership level"),
                alt.Tooltip("Records:Q", title="Records", format=","),
            ],
        )
        .properties(height=400)
    )
    st.altair_chart(pie, use_container_width=True)

    show_table = st.checkbox("Show contact list", value=False)
    if show_table:
        rows = []
        for c in contacts:
            rows.append(
                {
                    "Membership level": (
                        contact_custom_field_value(c, mid).strip() if mid else ""
                    )
                    or "(blank)",
                    "First name": (c.get("firstName") or "") or "",
                    "Last name": (c.get("lastName") or "") or "",
                    "Email": (c.get("email") or "") or "",
                    "Date added": (c.get("dateAdded") or "") or "",
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)
