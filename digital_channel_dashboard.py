"""Streamlit replica of the Digital Channel Dashboard 2024-25 Google Sheet."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from digital_channel_sheets import (
    SPREADSHEET_NAME,
    load_campaign_data,
    load_comparison_data,
    monthly_campaign_summary,
    scorecard_metrics,
)

load_dotenv(Path(__file__).resolve().parent / ".env")

# Brand palette derived from the Google Sheets theme
COLORS = {
    "accent": "#5DA68A",
    "accent_dark": "#264540",
    "paid_bg": "#E9F8FF",
    "lead_bg": "#CCECF7",
    "patient_bg": "#C0DDF7",
    "muted": "#6B7C93",
    "2023": "#4C78A8",
    "2024": "#F58518",
}

MONTH_ORDER = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.5rem; max-width: 1400px; }
        [data-testid="stMetric"] {
            background: white;
            border-radius: 12px;
            padding: 0.75rem 1rem;
            box-shadow: 0 1px 3px rgba(38,69,64,0.08);
            border: 1px solid rgba(93,166,138,0.15);
        }
        [data-testid="stMetricLabel"] { color: #264540; font-weight: 600; }
        [data-testid="stMetricValue"] { color: #5DA68A; }
        h5 { color: #264540; margin-top: 1rem !important; }
        [data-testid="stSidebar"] { background: #f7fbff; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _fmt_currency(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"${value:,.2f}"


def _fmt_int(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{int(round(value)):,}"


def _fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value:.1f}%"


def _metric_row(items: list[tuple[str, str]]) -> None:
    cols = st.columns(len(items))
    for col, (label, value) in zip(cols, items):
        col.metric(label, value)


def _line_chart(df: pd.DataFrame, y_cols: list[str], title: str, y_label: str) -> go.Figure:
    melted = df.melt(id_vars=["month"], value_vars=y_cols, var_name="Metric", value_name="Value")
    label_map = {
        "spend": "Spend",
        "leads": "Leads",
        "dcs": "DCs",
        "conversions": "Conversions",
    }
    melted["Metric"] = melted["Metric"].map(label_map).fillna(melted["Metric"])

    fig = px.line(
        melted,
        x="month",
        y="Value",
        color="Metric",
        markers=True,
        title=title,
        color_discrete_sequence=[COLORS["accent"], COLORS["2024"], COLORS["2023"]],
    )
    fig.update_layout(
        height=320,
        margin=dict(l=20, r=20, t=50, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Roboto, sans-serif", color=COLORS["accent_dark"]),
        xaxis_title="",
        yaxis_title=y_label,
    )
    fig.update_traces(line=dict(width=2.5), marker=dict(size=7))
    return fig


def _yoy_line_chart(comparison: dict) -> go.Figure | None:
    metrics = comparison.get("metrics", {})
    target = {
        "Booking Page Views (Traffic)": "Traffic",
        "Calls completed": "Calls completed",
        "TOTAL New Members": "New sign-ups",
    }
    rows: list[dict] = []
    for metric_name, short in target.items():
        series = metrics.get(metric_name, {})
        for period, value in series.items():
            month, year = period.split()
            rows.append({"month": month, "year": int(year), "metric": short, "value": value})

    if not rows:
        return None

    df = pd.DataFrame(rows)
    df["month"] = pd.Categorical(df["month"], categories=MONTH_ORDER, ordered=True)
    df = df.sort_values(["month", "year"])
    df["series"] = df["metric"] + " (" + df["year"].astype(str) + ")"

    fig = px.line(
        df,
        x="month",
        y="value",
        color="series",
        markers=True,
        title="YoY Comparison of Traffic, DCs & New Sign Ups",
        color_discrete_sequence=[COLORS["accent"], COLORS["2024"], COLORS["2023"], COLORS["muted"], "#E45756", "#72B7B2"],
    )
    fig.update_layout(
        height=420,
        margin=dict(l=20, r=20, t=60, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.08, x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis_title="Month",
        yaxis_title="Count",
    )
    return fig


def _booking_column_chart(long_df: pd.DataFrame) -> go.Figure | None:
    if long_df.empty:
        return None

    metrics = [
        ("Booking Page Views (Traffic)", "Page views"),
        ("Bookings (all booked calls)", "Bookings"),
        ("Calls completed", "Calls completed"),
    ]
    rows: list[dict] = []
    for _, row in long_df.iterrows():
        for source, label in metrics:
            if source in row and pd.notna(row[source]):
                rows.append(
                    {
                        "month": row["month"],
                        "year": str(row["year"]),
                        "metric": label,
                        "value": row[source],
                    }
                )

    if not rows:
        return None

    plot_df = pd.DataFrame(rows)
    plot_df["month"] = pd.Categorical(plot_df["month"], categories=MONTH_ORDER, ordered=True)
    plot_df = plot_df.sort_values(["month", "year"])

    fig = px.bar(
        plot_df,
        x="month",
        y="value",
        color="metric",
        barmode="group",
        facet_col="year",
        title="Booking Page Views (Traffic), Bookings & Calls Completed",
        color_discrete_sequence=[COLORS["accent"], COLORS["2023"], COLORS["2024"]],
        category_orders={"month": MONTH_ORDER},
    )
    fig.update_layout(
        height=400,
        margin=dict(l=20, r=20, t=60, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.05, x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1].strip()))
    fig.update_yaxes(matches=None, showticklabels=True)
    return fig


def _membership_column_chart(comparison: dict) -> go.Figure | None:
    metrics = comparison.get("metrics", {})
    levels = [
        ("New Standard Members", "Standard"),
        ("New Silver Members", "Silver"),
        ("New Gold Members", "Gold"),
        ("New Platinum Members", "Platinum"),
    ]
    rows: list[dict] = []
    for source, label in levels:
        series = metrics.get(source, {})
        for period, value in series.items():
            month, year = period.split()
            rows.append({"month": month, "year": str(year), "level": label, "value": value})

    if not rows:
        return None

    df = pd.DataFrame(rows)
    df["month"] = pd.Categorical(df["month"], categories=MONTH_ORDER, ordered=True)
    df = df.sort_values(["month", "year"])

    fig = px.bar(
        df,
        x="month",
        y="value",
        color="level",
        barmode="group",
        facet_col="year",
        title="Breakdown By Sign Up Levels 2023 vs. 2024",
        color_discrete_sequence=[COLORS["accent"], COLORS["2023"], COLORS["2024"], COLORS["muted"]],
        category_orders={"month": MONTH_ORDER},
    )
    fig.update_layout(
        height=400,
        margin=dict(l=20, r=20, t=60, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.05, x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1].strip()))
    fig.update_yaxes(matches=None, showticklabels=True)
    return fig


@st.cache_data(ttl=600, show_spinner=False)
def _load_data():
    campaign_df = load_campaign_data()
    comparison = load_comparison_data()
    return campaign_df, comparison


def main() -> None:
    st.set_page_config(page_title="Digital Channel Dashboard", layout="wide", page_icon="📊")
    _inject_styles()

    st.title("Digital Channel Dashboard 2024–25")
    st.caption(f"Live data from Google Sheets · {SPREADSHEET_NAME}")

    with st.spinner("Loading dashboard data from Google Sheets…"):
        try:
            raw_df, comparison = _load_data()
        except Exception as exc:
            st.error(
                "Could not load Google Sheets data. Run `python auth_google_sheets_mcp.py` "
                f"if your token is missing or expired.\n\n{exc}"
            )
            st.stop()

    if raw_df.empty:
        st.warning("No campaign data found in the Data sheet.")
        st.stop()

    with st.sidebar:
        st.header("Filters")
        min_date = raw_df["date"].min().date()
        max_date = raw_df["date"].max().date()
        date_range = st.date_input("Date range", value=(min_date, max_date), min_value=min_date, max_value=max_date)
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_date, end_date = date_range
        else:
            start_date, end_date = min_date, max_date

        channels = sorted(raw_df["channel"].unique())
        selected_channels = st.multiselect("Channel", channels, default=channels)

        all_campaigns = sorted(raw_df["campaign"].unique())
        selected_campaigns = st.multiselect(
            "Campaign",
            all_campaigns,
            placeholder="All campaigns",
        )
        if not selected_campaigns:
            selected_campaigns = all_campaigns

        if st.button("Refresh data"):
            _load_data.clear()
            st.rerun()

    mask = (
        (raw_df["date"].dt.date >= start_date)
        & (raw_df["date"].dt.date <= end_date)
        & (raw_df["channel"].isin(selected_channels))
        & (raw_df["campaign"].isin(selected_campaigns))
    )
    df = raw_df.loc[mask].copy()
    monthly = monthly_campaign_summary(df)
    scores = scorecard_metrics(df)

    st.markdown("##### Paid media performance")
    _metric_row(
        [
            ("Spend", _fmt_currency(scores["spend"])),
            ("Clicks", _fmt_int(scores["clicks"])),
            ("Cost per click", _fmt_currency(scores["cpc"])),
        ],
    )

    st.markdown("##### Lead funnel")
    _metric_row(
        [
            ("Leads", _fmt_int(scores["leads"])),
            ("Cost per lead", _fmt_currency(scores["cpl"])),
            ("DCs", _fmt_int(scores["dcs"])),
            ("Avg. $ per DC", _fmt_currency(scores["cpdc"])),
        ],
    )

    st.markdown("##### Patient acquisition")
    _metric_row(
        [
            ("New patient count", _fmt_int(scores["conversions"])),
            ("Avg. $ CAC (CPA)", _fmt_currency(scores["cac"])),
            ("Patient to DC %", _fmt_pct(scores["lead_to_patient_pct"])),
        ],
    )

    st.markdown("---")
    st.subheader("Trends over time")

    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(_line_chart(monthly, ["spend"], "Spend Over Time", "Spend ($)"), use_container_width=True)
    with c2:
        st.plotly_chart(_line_chart(monthly, ["conversions"], "Patient Acquisition Over Time", "Conversions"), use_container_width=True)

    c3, c4 = st.columns(2)
    with c3:
        st.plotly_chart(_line_chart(monthly, ["leads"], "Lead Acquisition Over Time", "Leads"), use_container_width=True)
    with c4:
        st.plotly_chart(_line_chart(monthly, ["leads", "dcs"], "Lead Acquisition & DCs Over Time", "Count"), use_container_width=True)

    st.markdown("---")
    st.subheader("Booking & membership comparisons")

    booking_fig = _booking_column_chart(comparison.get("long", pd.DataFrame()))
    if booking_fig:
        st.plotly_chart(booking_fig, use_container_width=True)

    col_left, col_right = st.columns(2)
    with col_left:
        member_fig = _membership_column_chart(comparison)
        if member_fig:
            st.plotly_chart(member_fig, use_container_width=True)
    with col_right:
        yoy_fig = _yoy_line_chart(comparison)
        if yoy_fig:
            st.plotly_chart(yoy_fig, use_container_width=True)

    with st.expander("View filtered campaign data"):
        display_cols = [
            "date",
            "channel",
            "campaign",
            "creative_type",
            "spend",
            "clicks",
            "cpc",
            "leads",
            "cpl",
            "dcs",
            "cpdc",
            "conversions",
            "lead_to_patient_pct",
            "cac",
        ]
        st.dataframe(df[display_cols].sort_values("date", ascending=False), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
