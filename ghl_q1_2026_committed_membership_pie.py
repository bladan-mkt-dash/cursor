"""Streamlit: GHL Q1 2026 — committed sign-ups vs cancellations, pie by membership level."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from ghl_client import (
    contact_custom_field_value,
    fetch_cancellation_date_range_membership_cancelled_true_contacts,
    fetch_signup_date_range_committed_yes_contacts,
    parse_membership_cancellation_date,
)

load_dotenv(Path(__file__).resolve().parent / ".env")

Q1_2026_SINCE = "2026-01-01"
Q1_2026_UNTIL = "2026-03-31"


def _membership_level_pie_figure(level_agg: pd.DataFrame) -> go.Figure:
    """Donut + outside labels + right legend (same styling for both charts)."""
    level_agg = level_agg.copy()
    level_agg["Legend label"] = level_agg.apply(
        lambda r: f"{r['Membership level']} ({int(r['Members']):,})",
        axis=1,
    )
    outside = [
        f"<b>{row['Membership level']}</b><br>{int(row['Members']):,} members"
        for _, row in level_agg.iterrows()
    ]
    fig = go.Figure(
        data=[
            go.Pie(
                labels=level_agg["Legend label"],
                values=level_agg["Members"],
                text=outside,
                textinfo="text",
                textposition="outside",
                hole=0.38,
                sort=False,
                direction="clockwise",
                # Moderate domain + height keeps Streamlit/Cursor Simple Browser scroll working.
                domain=dict(x=[0.04, 0.84], y=[0.08, 0.92]),
                pull=0.015,
                marker=dict(line=dict(color="white", width=2)),
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>%{value:,} members<br><extra></extra>"
                ),
                customdata=level_agg[["Membership level"]],
            )
        ]
    )
    fig.update_traces(outsidetextfont=dict(size=13), textfont=dict(size=13))
    fig.update_layout(
        height=680,
        autosize=True,
        margin=dict(t=88, b=88, l=52, r=220),
        legend=dict(
            title=dict(text="Membership level", font=dict(size=17)),
            font=dict(size=14),
            traceorder="normal",
            itemsizing="constant",
            orientation="v",
            xref="paper",
            yref="paper",
            x=1.0,
            xanchor="left",
            y=0.5,
            yanchor="middle",
        ),
        showlegend=True,
    )
    return fig


def _membership_level_sort_key(label: str) -> tuple[int, str]:
    """
    Sort key: Platinum, Gold, Silver, Standard, then other levels A–Z,
    then **Nutrition only**, then ``(blank)``.
    """
    s = str(label).strip()
    cf = s.casefold()
    if cf == "platinum":
        return (0, s)
    if cf == "gold":
        return (1, s)
    if cf == "silver":
        return (2, s)
    if cf == "standard":
        return (3, s)
    if cf == "nutrition only":
        return (900, s)
    if cf == "(blank)":
        return (901, s)
    return (100, cf)


def _order_membership_level_rows(
    df: pd.DataFrame, col: str = "Membership level"
) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()
    keys = out[col].astype(str).map(_membership_level_sort_key)
    out["_k0"] = keys.map(lambda t: t[0])
    out["_k1"] = keys.map(lambda t: t[1])
    out = out.sort_values(["_k0", "_k1"], kind="stable").drop(
        columns=["_k0", "_k1"]
    )
    return out.reset_index(drop=True)


def _breakdown_table(agg: pd.DataFrame) -> pd.DataFrame:
    """Membership level, count, share of total (%)."""
    if agg is None or agg.empty:
        return pd.DataFrame(
            columns=["Membership level", "Members", "Share of total (%)"]
        )
    out = agg[["Membership level", "Members"]].copy()
    total = int(out["Members"].sum())
    if total > 0:
        out["Share of total (%)"] = (out["Members"] / total * 100).round(1)
    else:
        out["Share of total (%)"] = 0.0
    return out


def _cancel_count_for_level(cancel_agg: pd.DataFrame | None, label: str) -> int:
    """Sum ``Members`` for a membership level; ``label`` match is case-insensitive."""
    if cancel_agg is None or cancel_agg.empty:
        return 0
    label_cf = label.strip().casefold()
    for _, r in cancel_agg.iterrows():
        if str(r["Membership level"]).strip().casefold() == label_cf:
            return int(r["Members"])
    return 0


def _net_signups_minus_cancel_by_level(
    committed_agg: pd.DataFrame | None,
    cancel_agg: pd.DataFrame | None,
) -> pd.DataFrame:
    """
    Per membership level: sign-ups minus cancel counts (sign-up levels only; no ``(blank)`` row).

    **Standard:** ``Cancel`` = ``(blank)`` cancels + **Standard** cancels (from the full
    cancel table), so ``Net = Standard sign-ups - ((blank) + Standard)`` cancellations.

    Other levels: ``Cancel`` = that level's cancel count only (blank not included).
    """
    cols = ["Membership level", "Sign-ups", "Cancel", "Net"]
    if committed_agg is None or committed_agg.empty:
        return pd.DataFrame(columns=cols)

    blank_cancel = _cancel_count_for_level(cancel_agg, "(blank)")
    standard_cancel = _cancel_count_for_level(cancel_agg, "Standard")
    standard_cancel_total = int(blank_cancel + standard_cancel)

    sign = committed_agg.rename(columns={"Members": "Sign-ups"}).copy()
    sign = sign[sign["Membership level"] != "(blank)"].reset_index(drop=True)

    can_nomerge = (
        cancel_agg.rename(columns={"Members": "Cancel"}).copy()
        if cancel_agg is not None and not cancel_agg.empty
        else pd.DataFrame(columns=["Membership level", "Cancel"])
    )
    if not can_nomerge.empty:
        can_nomerge = can_nomerge[
            can_nomerge["Membership level"] != "(blank)"
        ].reset_index(drop=True)

    merged = sign.merge(can_nomerge, on="Membership level", how="left")
    merged["Cancel"] = merged["Cancel"].fillna(0).astype(int)
    merged["Sign-ups"] = merged["Sign-ups"].astype(int)

    def _effective_cancel(row: pd.Series) -> int:
        if str(row["Membership level"]).strip().casefold() == "standard":
            return standard_cancel_total
        return int(row["Cancel"])

    merged["Cancel"] = merged.apply(_effective_cancel, axis=1)
    merged["Net"] = merged["Sign-ups"] - merged["Cancel"]
    out = merged[cols].reset_index(drop=True)
    return _order_membership_level_rows(out)


st.set_page_config(page_title="GHL Q1 2026 — membership pies", layout="wide")
st.title("GoHighLevel — Q1 2026 membership views")
st.caption(
    f"**Left:** **Sign Up Date** in **{Q1_2026_SINCE}**–**{Q1_2026_UNTIL}** and **Committed?** = **Yes**. "
    f"**Right:** **Membership Cancellation Date** in that range and **Membership Cancelled** is truthy. "
    "Both pies count contacts by **Membership Level**. "
    "Env: `GHL_SIGN_UP_DATE_FIELD_ID`, `GHL_COMMITTED_FIELD_ID`, `GHL_CANCELLATION_DATE_FIELD_ID`, "
    "`GHL_MEMBERSHIP_CANCELLED_FIELD_ID`, `GHL_MEMBERSHIP_LEVEL_FIELD_ID`."
)

ghl_location_id = os.getenv("GHL_LOCATION_ID", "").strip()

cancel_data = None
cancel_err: str | None = None
committed_data = None
committed_err: str | None = None

with st.spinner("Loading contacts from GoHighLevel…"):
    try:
        cancel_data = fetch_cancellation_date_range_membership_cancelled_true_contacts(
            Q1_2026_SINCE,
            Q1_2026_UNTIL,
            location_id=ghl_location_id or None,
        )
    except Exception as e:
        cancel_err = str(e)
    try:
        committed_data = fetch_signup_date_range_committed_yes_contacts(
            Q1_2026_SINCE,
            Q1_2026_UNTIL,
            location_id=ghl_location_id or None,
        )
    except Exception as e:
        committed_err = str(e)

# --- Build committed (left) frames ---
committed_df: pd.DataFrame | None = None
committed_agg: pd.DataFrame | None = None
if committed_data is not None and committed_err is None:
    contacts = committed_data["contacts"]
    sign_fid = committed_data["sign_up_date_field_id"]
    mid = committed_data.get("membership_level_field_id") or ""
    committed_fid = committed_data["committed_field_id"]
    if contacts:
        rows = []
        for c in contacts:
            raw_sign = contact_custom_field_value(c, sign_fid)
            d = parse_membership_cancellation_date(raw_sign)
            rows.append(
                {
                    "Membership level": (
                        (contact_custom_field_value(c, mid).strip() if mid else "")
                        or "(blank)"
                    ),
                    "Sign up date": d,
                    "Committed?": contact_custom_field_value(
                        c, committed_fid
                    ).strip(),
                    "First name": (c.get("firstName") or "") or "",
                    "Last name": (c.get("lastName") or "") or "",
                    "Email": (c.get("email") or "") or "",
                }
            )
        committed_df = pd.DataFrame(rows)
        committed_agg = (
            committed_df.groupby("Membership level", dropna=False)
            .size()
            .reset_index(name="Members")
            .sort_values("Members", ascending=False)
        )
        committed_agg = _order_membership_level_rows(committed_agg)

# --- Build cancelled (right) frames ---
cancel_df: pd.DataFrame | None = None
cancel_agg: pd.DataFrame | None = None
if cancel_data is not None and cancel_err is None:
    c_contacts = cancel_data["contacts"]
    c_mid = cancel_data.get("membership_level_field_id") or ""
    c_cancel_fid = cancel_data["cancellation_field_id"]
    c_mc_fid = cancel_data["membership_cancelled_field_id"]
    if c_contacts:
        c_rows = []
        for c in c_contacts:
            raw_c = contact_custom_field_value(c, c_cancel_fid)
            c_rows.append(
                {
                    "Membership level": (
                        (contact_custom_field_value(c, c_mid).strip() if c_mid else "")
                        or "(blank)"
                    ),
                    "Cancellation date": parse_membership_cancellation_date(raw_c),
                    "Membership cancelled": contact_custom_field_value(
                        c, c_mc_fid
                    ).strip(),
                    "First name": (c.get("firstName") or "") or "",
                    "Last name": (c.get("lastName") or "") or "",
                    "Email": (c.get("email") or "") or "",
                }
            )
        cancel_df = pd.DataFrame(c_rows)
        cancel_agg = (
            cancel_df.groupby("Membership level", dropna=False)
            .size()
            .reset_index(name="Members")
            .sort_values("Members", ascending=False)
        )
        cancel_agg = _order_membership_level_rows(cancel_agg)

# --- Row 1: charts (Committed left, Cancelled right) ---
col_committed, col_cancelled = st.columns(2, gap="large")

with col_committed:
    st.markdown("#### Committed = Yes (sign-up in Q1)")
    st.caption(
        f"**Sign Up Date** in **{Q1_2026_SINCE}**–**{Q1_2026_UNTIL}** · **Committed?** = **Yes**."
    )
    if committed_err:
        st.error(committed_err)
    elif committed_data is None:
        st.warning("No committed sign-up data loaded.")
    else:
        contacts = committed_data["contacts"]
        signup_loaded = int(committed_data.get("signup_matches_loaded") or 0)
        excluded = int(committed_data.get("excluded_not_committed_yes") or 0)
        total_api = int(committed_data.get("total_reported") or 0)
        m1, m2, m3 = st.columns(3)
        m1.metric("Committed = Yes (in range)", f"{len(contacts):,}")
        m2.metric("Sign-up in range (before Yes filter)", f"{signup_loaded:,}")
        m3.metric("Excluded (not Yes)", f"{excluded:,}")
        if total_api and total_api != signup_loaded:
            st.warning(
                f"API reported **{total_api:,}** contacts with sign-up in range; "
                f"loaded **{signup_loaded:,}** — check pagination if these differ."
            )
        if committed_data.get("truncated_pages"):
            st.warning(
                "Sign-up search hit the pagination safety cap; see "
                "`search_contacts_custom_field_date_range` in `ghl_client.py` if needed."
            )
        if not contacts:
            st.info(
                "No contacts matched **Sign Up Date** in Q1 2026 with **Committed?** = **Yes**."
            )
        elif committed_agg is not None and not committed_agg.empty:
            st.plotly_chart(
                _membership_level_pie_figure(committed_agg),
                width="stretch",
            )

with col_cancelled:
    st.markdown("#### Membership cancelled (true)")
    st.caption(
        "**Membership Cancellation Date** in range · **Membership Cancelled** = true / yes."
    )
    if cancel_err:
        st.error(cancel_err)
    elif cancel_data is None:
        st.warning("No cancellation data loaded.")
    else:
        c_contacts = cancel_data["contacts"]
        c_loaded = int(cancel_data.get("cancellation_matches_loaded") or 0)
        c_excl = int(cancel_data.get("excluded_not_cancelled_true") or 0)
        c_total = int(cancel_data.get("total_reported") or 0)
        m1, m2, m3 = st.columns(3)
        m1.metric("Cancelled = true (in range)", f"{len(c_contacts):,}")
        m2.metric("Cancellation date in range (before filter)", f"{c_loaded:,}")
        m3.metric("Excluded (not truthy)", f"{c_excl:,}")
        if c_total and c_total != c_loaded:
            st.warning(
                f"API total **{c_total:,}** vs loaded **{c_loaded:,}** — check pagination."
            )
        if cancel_data.get("truncated_pages"):
            st.warning("Cancellation date search hit the pagination cap.")
        if not c_contacts:
            st.info("No contacts matched **Membership Cancelled** with date in Q1 2026.")
        elif cancel_agg is not None and not cancel_agg.empty:
            st.plotly_chart(
                _membership_level_pie_figure(cancel_agg),
                width="stretch",
            )

# --- Row 2: breakdown tables below both charts ---
st.divider()
st.subheader("Q1 2026 Results")

tbl_left, tbl_mid, tbl_right = st.columns([1.05, 1, 1.05], gap="medium")

with tbl_left:
    st.markdown("**Q1 2026 Sign ups**")
    if committed_err:
        st.error(committed_err)
    elif committed_agg is None or committed_agg.empty:
        st.caption("No rows to show.")
    else:
        st.dataframe(
            _breakdown_table(committed_agg),
            width="stretch",
            hide_index=True,
        )
        if st.checkbox("Show committed contact rows", key="tbl_committed"):
            if committed_df is not None:
                show_df = committed_df.copy()
                show_df["Sign up date"] = show_df["Sign up date"].apply(
                    lambda x: x.isoformat()
                    if hasattr(x, "isoformat") and x is not None
                    else ""
                )
                st.dataframe(show_df, width="stretch", hide_index=True)

with tbl_mid:
    st.markdown("**Q1 2026 Cancellations**")
    if cancel_err:
        st.error(cancel_err)
    elif cancel_agg is None or cancel_agg.empty:
        st.caption("No rows to show.")
    else:
        st.dataframe(
            _breakdown_table(cancel_agg),
            width="stretch",
            hide_index=True,
        )
        if st.checkbox("Show cancellation contact rows", key="tbl_cancel"):
            if cancel_df is not None:
                disp = cancel_df.copy()
                disp["Cancellation date"] = disp["Cancellation date"].apply(
                    lambda x: x.isoformat()
                    if hasattr(x, "isoformat") and x is not None
                    else ""
                )
                st.dataframe(disp, width="stretch", hide_index=True)

with tbl_right:
    st.markdown("**Q1 Net**")
    if committed_err:
        st.error(committed_err)
    elif committed_agg is None or committed_agg.empty:
        st.caption("No sign-up rows — net table not shown.")
    else:
        net_df = _net_signups_minus_cancel_by_level(committed_agg, cancel_agg)
        if not net_df.empty:
            total_row = pd.DataFrame(
                [
                    {
                        "Membership level": "Total",
                        "Sign-ups": int(net_df["Sign-ups"].sum()),
                        "Cancel": int(net_df["Cancel"].sum()),
                        "Net": int(net_df["Net"].sum()),
                    }
                ]
            )
            net_df = pd.concat([net_df, total_row], ignore_index=True)
        st.dataframe(net_df, width="stretch", hide_index=True)
