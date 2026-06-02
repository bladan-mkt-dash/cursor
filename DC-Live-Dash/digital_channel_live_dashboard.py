"""
Digital Channel Dashboard on localhost — live Google Ads, Meta, and GoHighLevel data.

    streamlit run DC-Live-Dash/digital_channel_live_dashboard.py --server.port 8850

Open:

    http://127.0.0.1:8850/
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from digital_channel_live_data import (
    DEFAULT_SINCE,
    clear_ghl_leads_day_cache,
    load_live_campaign_data,
    monthly_campaign_summary,
    scorecard_metrics,
)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

COLORS = {
    "accent": "#5DA68A",
    "accent_dark": "#264540",
    "2023": "#4C78A8",
    "2024": "#F58518",
    "muted": "#6B7C93",
}

MONTH_ORDER = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

CHANNEL_GOOGLE = "Google Ads"
CHANNEL_META = "FB/IG"


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
    plot_df = df.copy()
    plot_df["month_label"] = plot_df["month"].dt.strftime("%b %Y")
    melted = plot_df.melt(
        id_vars=["month_label"],
        value_vars=y_cols,
        var_name="Metric",
        value_name="Value",
    )
    label_map = {
        "spend": "Spend",
        "leads": "Leads",
        "dcs": "DCs",
        "conversions": "Conversions",
    }
    melted["Metric"] = melted["Metric"].map(label_map).fillna(melted["Metric"])

    fig = px.line(
        melted,
        x="month_label",
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


def _cpl_over_time_chart(monthly: pd.DataFrame) -> go.Figure | None:
    """Monthly CPL (spend ÷ leads) for the current filter selection."""
    if monthly.empty:
        return None

    plot_df = monthly.copy()
    plot_df["cpl"] = plot_df.apply(
        lambda r: r["spend"] / r["leads"] if r["leads"] and r["leads"] > 0 else pd.NA,
        axis=1,
    )
    plot_df["month_label"] = plot_df["month"].dt.strftime("%b %Y")
    plot_df = plot_df.dropna(subset=["cpl"])
    if plot_df.empty:
        return None

    fig = px.line(
        plot_df,
        x="month_label",
        y="cpl",
        markers=True,
        title="CPL Over Time",
        color_discrete_sequence=[COLORS["2024"]],
    )
    fig.update_layout(
        height=320,
        margin=dict(l=20, r=20, t=50, b=20),
        showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Roboto, sans-serif", color=COLORS["accent_dark"]),
        xaxis_title="",
        yaxis_title="CPL ($)",
    )
    fig.update_yaxes(tickformat="$,.0f")
    fig.update_traces(
        line=dict(width=2.5, color=COLORS["2024"]),
        marker=dict(size=7),
        hovertemplate="%{x}<br>CPL: $%{y:,.2f}<extra></extra>",
    )
    return fig


def _spend_click_correlation(df: pd.DataFrame) -> go.Figure | None:
    if df.empty:
        return None
    by_campaign = (
        df.groupby("campaign", as_index=False)
        .agg(spend=("spend", "sum"), clicks=("clicks", "sum"))
        .sort_values("spend", ascending=False)
        .head(15)
    )
    if by_campaign.empty:
        return None
    fig = px.bar(
        by_campaign,
        x="campaign",
        y=["spend", "clicks"],
        barmode="group",
        title="Spend / Click Correlation Per Campaign",
        color_discrete_sequence=["#0B5394", "#C9DAF8"],
        labels={"value": "Amount", "variable": "Metric", "campaign": "Campaign"},
    )
    fig.update_layout(
        height=420,
        margin=dict(l=20, r=20, t=60, b=120),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        xaxis_tickangle=-35,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def _creative_allocation_pie(df: pd.DataFrame) -> go.Figure | None:
    if df.empty:
        return None
    by_type = (
        df.groupby("creative_type", as_index=False)["spend"]
        .sum()
        .sort_values("spend", ascending=False)
    )
    if by_type["spend"].sum() <= 0:
        return None
    fig = px.pie(
        by_type,
        names="creative_type",
        values="spend",
        title="Campaign Asset Marketing Creative Allocations",
        color_discrete_sequence=px.colors.sequential.Teal,
        hole=0.35,
    )
    fig.update_layout(
        height=420,
        margin=dict(l=20, r=20, t=60, b=20),
        font=dict(family="Roboto, sans-serif", color=COLORS["accent_dark"]),
    )
    return fig


def _scorecard_leads_total(
    df: pd.DataFrame,
    *,
    selected_channels: list[str],
    all_channels: list[str],
    selected_campaigns: list[str],
    campaign_pool: list[str],
    lead_summary: dict[str, int],
) -> float:
    """
    Headline lead count: all new GHL contacts when both channels and all campaigns
    are in view; channel totals for a single channel; otherwise allocated row sum.
    """
    all_channels_selected = set(selected_channels) >= set(all_channels)
    all_campaigns_selected = (
        len(selected_campaigns) == len(campaign_pool) if campaign_pool else True
    )
    if all_channels_selected and all_campaigns_selected:
        if selected_channels == [CHANNEL_GOOGLE]:
            return float(lead_summary.get("google_leads") or 0)
        if selected_channels == [CHANNEL_META]:
            return float(lead_summary.get("meta_leads") or 0)
        if all_channels_selected and len(all_channels) > 1:
            total = float(lead_summary.get("total_new_contacts") or 0)
            if total > 0:
                return total
    row_leads = float(df["leads"].sum())
    if row_leads > 0:
        return row_leads
    return float(lead_summary.get("total_new_contacts") or 0)


def _weighted_scorecard_metrics(
    df: pd.DataFrame,
    *,
    selected_channels: list[str],
    all_channels: list[str],
    selected_campaigns: list[str],
    campaign_pool: list[str],
    lead_summary: dict[str, int],
) -> dict[str, float | None]:
    """Scorecard totals with properly weighted averages for rate metrics."""
    if df.empty:
        return {k: None for k in scorecard_metrics(df).keys()}

    spend = df["spend"].sum()
    clicks = df["clicks"].sum()
    leads = _scorecard_leads_total(
        df,
        selected_channels=selected_channels,
        all_channels=all_channels,
        selected_campaigns=selected_campaigns,
        campaign_pool=campaign_pool,
        lead_summary=lead_summary,
    )
    dcs = df["dcs"].sum()
    conversions = df["conversions"].sum()

    return {
        "spend": spend,
        "clicks": clicks,
        "cpc": spend / clicks if clicks else None,
        "leads": leads,
        "cpl": spend / leads if leads else None,
        "dcs": dcs,
        "cpdc": spend / dcs if dcs else None,
        "conversions": conversions,
        "cac": spend / conversions if conversions else None,
        "lead_to_patient_pct": (conversions / dcs * 100.0) if dcs else None,
    }


@st.cache_data(ttl=86400, show_spinner=False)
def _load_data(since: str, until: str) -> tuple[pd.DataFrame, tuple[str, ...], dict[str, int]]:
    df, notes, lead_summary = load_live_campaign_data(since=since, until=until)
    return df, tuple(notes), lead_summary


def main() -> None:
    st.set_page_config(
        page_title="Digital Channel Dashboard (Live)",
        layout="wide",
        page_icon="📊",
    )
    _inject_styles()

    st.title("Digital Channel Dashboard")
    st.caption(
        "Live data from **Google Ads**, **Meta**, and **GoHighLevel**. "
        "Leads = new GHL contacts (Meta: meta lead tag / Meta pixel; "
        "Google: dc thru g-ad tag / Google Tag). "
        "**First load** for a wide date range can take a few minutes while GHL "
        "lead data is pulled day-by-day; **repeat loads use cache** and are much faster."
    )

    today = date.today()
    default_until = today - timedelta(days=1)
    default_since = pd.Timestamp(DEFAULT_SINCE).date()

    with st.sidebar:
        st.header("Filters")
        since = st.date_input(
            "Start date",
            value=default_since,
            max_value=default_until,
        )
        until = st.date_input(
            "End date",
            value=default_until,
            max_value=default_until,
        )
        if since > until:
            st.error("Start date must be on or before end date.")
            st.stop()

        if st.button("Refresh data", help="Reload ads + GHL. Keeps cached GHL daily lead files."):
            _load_data.clear()
            st.rerun()

        if st.button(
            "Hard refresh GHL leads",
            help="Clear cached GHL daily lead files and reload (use if lead counts look wrong).",
        ):
            clear_ghl_leads_day_cache()
            _load_data.clear()
            st.rerun()

    load_label = (
        "Loading Google Ads, Meta, and GoHighLevel… "
        "(GHL leads may take several minutes on first load for a wide range)"
    )
    with st.spinner(load_label):
        try:
            raw_df, notes, lead_summary = _load_data(since.isoformat(), until.isoformat())
        except Exception as exc:
            st.error(f"Could not load live data.\n\n{exc}")
            st.stop()

    if raw_df.empty:
        st.warning("No campaign data returned for the selected date range.")
        st.stop()

    with st.expander("Data sources & methodology", expanded=False):
        for note in notes:
            st.markdown(f"- {note}")

    with st.sidebar:
        channels = sorted(raw_df["channel"].dropna().unique())
        selected_channels = st.multiselect("Channel", channels, default=channels)

        channel_mask = raw_df["channel"].isin(selected_channels)
        campaign_pool = sorted(raw_df.loc[channel_mask, "campaign"].dropna().unique())
        selected_campaigns = st.multiselect(
            "Campaign",
            campaign_pool,
            placeholder="All campaigns",
        )
        if not selected_campaigns:
            selected_campaigns = campaign_pool

        creative_pool = sorted(
            raw_df.loc[channel_mask, "creative_type"].dropna().unique()
        )
        selected_creatives = st.multiselect(
            "Asset type",
            creative_pool,
            default=creative_pool,
            help="Matches Sheet slicer: Text, Video, Image, Combo, etc.",
        )

        meta_types = sorted(
            raw_df.loc[raw_df["channel"] == "FB/IG", "fb_ig_type"]
            .dropna()
            .unique()
        )
        meta_types = [t for t in meta_types if str(t).strip()]
        selected_meta_types = st.multiselect(
            "FB/IG type",
            meta_types,
            default=meta_types,
            help="Meta campaign objective bucket (Lead Gen, Traffic, etc.).",
        )

    mask = (
        raw_df["channel"].isin(selected_channels)
        & (raw_df["campaign"].isin(selected_campaigns))
        & (raw_df["creative_type"].isin(selected_creatives))
    )
    if meta_types:
        meta_type_mask = (raw_df["channel"] != "FB/IG") | (
            raw_df["fb_ig_type"].isin(selected_meta_types)
        )
        mask &= meta_type_mask

    df = raw_df.loc[mask].copy()
    monthly = monthly_campaign_summary(df)
    scores = _weighted_scorecard_metrics(
        df,
        selected_channels=selected_channels,
        all_channels=channels,
        selected_campaigns=selected_campaigns,
        campaign_pool=campaign_pool,
        lead_summary=lead_summary,
    )

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
        st.plotly_chart(
            _line_chart(monthly, ["spend"], "Spend Over Time", "Spend ($)"),
            use_container_width=True,
        )
    with c2:
        st.plotly_chart(
            _line_chart(
                monthly,
                ["conversions"],
                "Patient Acquisition Over Time",
                "Conversions",
            ),
            use_container_width=True,
        )

    c3, c4 = st.columns(2)
    with c3:
        cpl_chart = _cpl_over_time_chart(monthly)
        if cpl_chart:
            st.plotly_chart(cpl_chart, use_container_width=True)
        else:
            st.info("CPL over time unavailable (no leads in the selected range).")
    with c4:
        st.plotly_chart(
            _line_chart(
                monthly,
                ["leads", "dcs"],
                "Lead Acquisition & DCs Over Time",
                "Count",
            ),
            use_container_width=True,
        )

    st.markdown("---")
    st.subheader("Campaign breakdown")

    col_pie, col_bar = st.columns(2)
    with col_pie:
        pie = _creative_allocation_pie(df)
        if pie:
            st.plotly_chart(pie, use_container_width=True)
    with col_bar:
        corr = _spend_click_correlation(df)
        if corr:
            st.plotly_chart(corr, use_container_width=True)

    with st.expander("View filtered campaign data"):
        display_cols = [
            "date",
            "channel",
            "campaign",
            "creative_type",
            "fb_ig_type",
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
        st.dataframe(
            df[display_cols].sort_values("date", ascending=False),
            use_container_width=True,
            hide_index=True,
        )


if __name__ == "__main__":
    main()
