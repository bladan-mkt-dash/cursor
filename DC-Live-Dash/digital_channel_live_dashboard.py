"""
Digital Channel Dashboard on localhost — live Google Ads, Meta, and GoHighLevel data.

    streamlit run DC-Live-Dash/digital_channel_live_dashboard.py --server.port 8850

Open:

    http://127.0.0.1:8850/
"""

from __future__ import annotations

import importlib
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from ghl_client import discovery_call_calendar_ids

_DC_LIVE_DIR = Path(__file__).resolve().parent
if str(_DC_LIVE_DIR) not in sys.path:
    sys.path.insert(0, str(_DC_LIVE_DIR))

import digital_channel_live_data as _live_data_mod

_EXPECTED_LIVE_DATA_REVISION = "2026-06-19-signups-tracker-q2-v1"
if (
    getattr(_live_data_mod, "LIVE_DATA_REVISION", None)
    != _EXPECTED_LIVE_DATA_REVISION
):
    _live_data_mod = importlib.reload(_live_data_mod)

from funnel_over_time_data import FUNNEL_OVER_TIME_REVISION, GHL_FUNNEL_SINCE, load_funnel_over_time
from digital_channel_live_data import (
    DEFAULT_SINCE,
    GHL_ATTRIBUTION_HEAR_ABOUT,
    GHL_ATTRIBUTION_OPTIONS,
    GHL_ATTRIBUTION_TRACKER,
    GHL_SIGNUPS_SINCE,
    GHL_DCS_SINCE,
    LIVE_DATA_REVISION,
    MEMBERSHIP_LEVELS,
    SHEETS_SIGNUPS_UNTIL,
    SHEETS_DCS_UNTIL,
    apply_dashboard_ghl_attribution,
    build_spend_trend_monthly,
    build_trend_chart_monthlies,
    channel_month_leads_total,
    clear_ghl_leads_day_cache,
    load_live_campaign_data,
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

_QUARTERLY_RANGE_MONTHS = 9


def _use_quarterly_grouping(since: date, until: date) -> bool:
    """Monthly x-axis through 9 calendar months; quarterly when the range is longer."""
    return pd.Timestamp(until) > pd.Timestamp(since) + pd.DateOffset(
        months=_QUARTERLY_RANGE_MONTHS
    )


def _time_period_summary(
    monthly: pd.DataFrame, since: date, until: date
) -> tuple[pd.DataFrame, bool]:
    """Monthly totals, or quarterly rollups when the selected range exceeds 9 months."""
    if monthly.empty:
        return monthly, False

    value_cols = [
        c for c in monthly.columns if c not in {"month", "period_label", "quarter"}
    ]

    if not _use_quarterly_grouping(since, until):
        out = monthly.copy()
        out["period_label"] = out["month"].dt.strftime("%b %Y")
        return out, False

    df = monthly.copy()
    df["quarter"] = df["month"].dt.to_period("Q")
    out = df.groupby("quarter", as_index=False)[value_cols].sum().sort_values("quarter")
    out["month"] = out["quarter"].dt.to_timestamp()
    out["period_label"] = out["quarter"].apply(lambda p: f"Q{p.quarter} {p.year}")
    return out, True


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.5rem; max-width: 100%; }
        [data-testid="stMetric"] {
            background: white;
            border-radius: 12px;
            padding: 0.5rem 0.6rem;
            box-shadow: 0 1px 3px rgba(38,69,64,0.08);
            border: 1px solid rgba(93,166,138,0.15);
        }
        [data-testid="stMetricLabel"] {
            color: #264540;
            font-weight: 600;
            font-size: 0.72rem;
            white-space: nowrap;
        }
        [data-testid="stMetricValue"] { color: #5DA68A; font-size: 1.05rem; }
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


def _prior_year_date(d: date) -> date:
    """Same calendar day one year earlier (Feb 29 → Feb 28)."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


def _spend_over_time_chart(
    spend_period_df: pd.DataFrame,
    prior_spend_monthly: pd.DataFrame,
    *,
    prior_since: date,
    prior_until: date,
) -> go.Figure:
    """Spend for the selected range with prior-year same-period spend overlaid."""
    x_order = spend_period_df["period_label"].tolist()
    current_y = spend_period_df["spend"].tolist()

    prior_period_df, _ = _time_period_summary(
        prior_spend_monthly, prior_since, prior_until
    )
    prior_by_month = (
        prior_period_df.set_index("month")["spend"].to_dict()
        if not prior_period_df.empty
        else {}
    )

    prior_y: list[float | None] = []
    for _, row in spend_period_df.iterrows():
        prior_key = row["month"] - pd.DateOffset(years=1)
        val = prior_by_month.get(prior_key)
        prior_y.append(float(val) if val is not None and not pd.isna(val) else None)

    has_prior = any(v is not None for v in prior_y)
    prior_label = (
        f"{prior_since.strftime('%b %Y')}–{prior_until.strftime('%b %Y')}"
    )

    fig = go.Figure()
    if has_prior:
        fig.add_trace(
            go.Scatter(
                x=x_order,
                y=prior_y,
                mode="lines+markers",
                name=f"Spend ({prior_label})",
                line=dict(color=COLORS["muted"], width=2, dash="dash"),
                marker=dict(size=6, color=COLORS["muted"]),
            )
        )
    fig.add_trace(
        go.Scatter(
            x=x_order,
            y=current_y,
            mode="lines+markers",
            name="Spend (selected range)",
            line=dict(color=COLORS["accent"], width=2.5),
            marker=dict(size=7, color=COLORS["accent"]),
        )
    )

    bottom_margin = 70 if has_prior else 20
    fig.update_layout(
        height=320,
        title="Spend Over Time",
        margin=dict(l=20, r=20, t=56, b=bottom_margin),
        showlegend=has_prior,
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.28,
            x=0.5,
            xanchor="center",
            title_text="",
        ),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Roboto, sans-serif", color=COLORS["accent_dark"]),
        xaxis_title="",
        yaxis_title="Spend ($)",
        xaxis=dict(categoryorder="array", categoryarray=x_order),
    )
    return fig


def _line_chart(
    df: pd.DataFrame,
    y_cols: list[str],
    title: str,
    y_label: str,
    *,
    y_count_ticks: bool = False,
    height: int = 320,
) -> go.Figure:
    plot_df = df.copy()
    label_map = {
        "spend": "Spend",
        "leads": "Leads",
        "dcs": "DCs",
        "conversions": "Signups",
    }
    melted = plot_df.melt(
        id_vars=["period_label"],
        value_vars=y_cols,
        var_name="series",
        value_name="Value",
    )
    melted["series"] = melted["series"].map(label_map).fillna(melted["series"])
    show_legend = len(y_cols) > 1

    fig = px.line(
        melted,
        x="period_label",
        y="Value",
        color="series",
        markers=True,
        title=title,
        labels={"series": "", "period_label": "", "Value": y_label},
        color_discrete_map={
            "Spend": COLORS["accent"],
            "Leads": COLORS["accent"],
            "DCs": COLORS["2023"],
            "Signups": COLORS["2024"],
        },
        category_orders={"period_label": plot_df["period_label"].tolist()},
    )
    bottom_margin = 70 if show_legend else 20
    fig.update_layout(
        height=height,
        margin=dict(l=20, r=20, t=56, b=bottom_margin),
        showlegend=show_legend,
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.28,
            x=0.5,
            xanchor="center",
            title_text="",
        ),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Roboto, sans-serif", color=COLORS["accent_dark"]),
        xaxis_title="",
        yaxis_title=y_label,
    )
    fig.update_traces(line=dict(width=2.5), marker=dict(size=7))

    if y_count_ticks:
        max_val = float(melted["Value"].max() or 0)
        upper = max(100, int((max_val + 49) // 50) * 50)
        fig.update_yaxes(
            range=[0, upper + upper * 0.05],
            tick0=0,
            dtick=50,
            showgrid=True,
            gridcolor="rgba(107,124,147,0.14)",
            griddash="dot",
        )
        for y in range(100, upper + 1, 100):
            fig.add_hline(
                y=y,
                line_color="rgba(38,69,64,0.35)",
                line_width=1.2,
            )

    return fig


def _funnel_over_time_chart(funnel_df: pd.DataFrame) -> go.Figure | None:
    """Org-wide monthly Leads, Discovery Calls, and Signups — independent of campaign filters."""
    if funnel_df.empty:
        return None

    plot_df = funnel_df.sort_values("month").copy()
    plot_df["period_label"] = plot_df["month"].dt.strftime("%b %Y")
    x_order = plot_df["period_label"].tolist()

    series = (
        ("leads", "Leads", COLORS["accent"]),
        ("dcs", "Discovery Calls", COLORS["2023"]),
        ("signups", "Signups", COLORS["2024"]),
    )

    fig = go.Figure()
    for col, label, color in series:
        fig.add_trace(
            go.Scatter(
                x=plot_df["period_label"],
                y=plot_df[col],
                mode="lines+markers",
                name=label,
                line=dict(color=color, width=2.5),
                marker=dict(size=8, color=color),
                hovertemplate=f"{label}: %{{y:,0f}}<extra></extra>",
            )
        )

    cutover = pd.Timestamp(GHL_FUNNEL_SINCE).to_period("M").to_timestamp()
    cutover_label = cutover.strftime("%b %Y")
    if (
        cutover_label in x_order
        and plot_df["month"].min() < cutover <= plot_df["month"].max()
    ):
        fig.add_shape(
            type="line",
            x0=cutover_label,
            x1=cutover_label,
            y0=0,
            y1=1,
            yref="paper",
            line=dict(dash="dot", color=COLORS["muted"], width=1),
        )
        fig.add_annotation(
            x=cutover_label,
            y=1,
            yref="paper",
            text="GHL from here",
            showarrow=False,
            yanchor="bottom",
            font=dict(size=11, color=COLORS["muted"]),
        )

    n_months = len(x_order)
    fig.update_layout(
        height=440,
        title="Leads, Discovery Calls & Signups Over Time",
        margin=dict(l=48, r=24, t=56, b=80 if n_months > 12 else 56),
        hovermode="x unified",
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            x=0,
            xanchor="left",
            title_text="",
        ),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Roboto, sans-serif", color=COLORS["accent_dark"], size=13),
        xaxis=dict(
            title="",
            categoryorder="array",
            categoryarray=x_order,
            tickangle=-45 if n_months > 8 else 0,
        ),
        yaxis=dict(
            title="Count",
            tickformat=",",
            rangemode="tozero",
            showgrid=True,
            gridcolor="rgba(107,124,147,0.18)",
            griddash="dot",
        ),
    )
    return fig


def _cpl_over_time_chart(monthly: pd.DataFrame) -> go.Figure | None:
    """Monthly CPL (spend / leads) for the current filter selection."""
    if monthly.empty:
        return None

    plot_df = monthly.copy()
    leads = pd.to_numeric(plot_df["leads"], errors="coerce").fillna(0)
    plot_df["cpl"] = plot_df["spend"] / leads.where(leads > 0)
    plot_df = plot_df.dropna(subset=["cpl"])
    if plot_df.empty:
        return None

    fig = px.line(
        plot_df,
        x="period_label",
        y="cpl",
        markers=True,
        title="CPL Over Time",
        color_discrete_sequence=[COLORS["2024"]],
        category_orders={"period_label": plot_df["period_label"].tolist()},
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
    fig.update_yaxes(tickformat="$,.2f")
    fig.update_traces(
        line=dict(width=2.5, color=COLORS["2024"]),
        marker=dict(size=7),
        hovertemplate=(
            "%{x}<br>CPL: $%{y:,.2f}<br>Leads: %{customdata[0]:,.0f}<extra></extra>"
        ),
        customdata=plot_df[["leads"]].values,
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
        labels={"value": "Amount", "variable": "", "campaign": "Campaign"},
    )
    fig.update_layout(
        height=420,
        margin=dict(l=20, r=20, t=56, b=120),
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.22,
            x=0.5,
            xanchor="center",
            title_text="",
        ),
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
    channel_month_leads: pd.DataFrame,
    since_month: pd.Timestamp,
    until_month: pd.Timestamp,
    use_hear_about: bool,
    use_tracker: bool,
) -> float:
    """
    Headline lead count from filtered rows (sheet baseline + live GHL), with
    channel-month totals as fallback when row sums are empty.
    """
    row_leads = float(df["leads"].sum())
    if row_leads > 0:
        return row_leads

    range_leads = channel_month_leads_total(
        channel_month_leads,
        since_month=since_month,
        until_month=until_month,
        selected_channels=selected_channels,
        use_hear_about=use_hear_about,
        use_tracker=use_tracker,
    )
    if range_leads > 0:
        return range_leads

    all_channels_selected = set(selected_channels) >= set(all_channels)
    all_campaigns_selected = (
        len(selected_campaigns) == len(campaign_pool) if campaign_pool else True
    )
    if all_channels_selected and all_campaigns_selected:
        if selected_channels == [CHANNEL_GOOGLE]:
            if use_hear_about and use_tracker:
                key = "google_leads_combined"
            elif use_hear_about:
                key = "google_leads_hear_about"
            else:
                key = "google_leads"
            return float(lead_summary.get(key) or 0)
        if selected_channels == [CHANNEL_META]:
            if use_hear_about and use_tracker:
                key = "meta_leads_combined"
            elif use_hear_about:
                key = "meta_leads_hear_about"
            else:
                key = "meta_leads"
            return float(lead_summary.get(key) or 0)
        if all_channels_selected and len(all_channels) > 1:
            if use_hear_about and use_tracker:
                return float(
                    (lead_summary.get("meta_leads_combined") or 0)
                    + (lead_summary.get("google_leads_combined") or 0)
                )
            if use_hear_about:
                return float(
                    (lead_summary.get("meta_leads_hear_about") or 0)
                    + (lead_summary.get("google_leads_hear_about") or 0)
                )
            return float(lead_summary.get("total_new_contacts") or 0)
    return float(lead_summary.get("total_new_contacts") or 0)


def _weighted_scorecard_metrics(
    df: pd.DataFrame,
    *,
    selected_channels: list[str],
    all_channels: list[str],
    selected_campaigns: list[str],
    campaign_pool: list[str],
    lead_summary: dict[str, int],
    channel_month_leads: pd.DataFrame,
    since_month: pd.Timestamp,
    until_month: pd.Timestamp,
    use_hear_about: bool,
    use_tracker: bool,
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
        channel_month_leads=channel_month_leads,
        since_month=since_month,
        until_month=until_month,
        use_hear_about=use_hear_about,
        use_tracker=use_tracker,
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
def _load_data(
    since: str,
    until: str,
    _revision: str = LIVE_DATA_REVISION,
) -> tuple[
    pd.DataFrame,
    tuple[str, ...],
    dict[str, int],
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    frozenset[pd.Timestamp],
    pd.DataFrame,
    pd.DataFrame,
    dict[str, dict[pd.Timestamp, float]],
    dict[pd.Timestamp, float],
    dict[pd.Timestamp, float],
    dict[pd.Timestamp, float],
]:
    (
        df,
        notes,
        lead_summary,
        conv_by_level,
        unallocated_conv,
        wom_conv,
        tracker_conv_by_level,
        tracker_unallocated,
        combined_conv_by_level,
        combined_unallocated,
        sheet_months,
        channel_month_leads,
        cpl_channel_month_leads,
        unallocated_leads_by_attr,
        sheet_signup_totals,
        ghl_signups_by_month,
        sheet_dcs_totals,
        ghl_dcs_by_month,
    ) = load_live_campaign_data(since=since, until=until)
    return (
        df,
        tuple(notes),
        lead_summary,
        conv_by_level,
        unallocated_conv,
        wom_conv,
        tracker_conv_by_level,
        tracker_unallocated,
        combined_conv_by_level,
        combined_unallocated,
        frozenset(sheet_months),
        channel_month_leads,
        cpl_channel_month_leads,
        unallocated_leads_by_attr,
        sheet_signup_totals,
        ghl_signups_by_month,
        sheet_dcs_totals,
        ghl_dcs_by_month,
    )


@st.cache_data(ttl=86400, show_spinner=False)
def _load_funnel_over_time(
    since: str,
    until: str,
    _revision: str = FUNNEL_OVER_TIME_REVISION,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    df, notes = load_funnel_over_time(since, until)
    return df, tuple(notes)


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
        "Leads through Jun 2025 use **Digital Channel Dashboard 2024-25** Data tab; "
        "later months use GHL new contacts (attributed + unallocated spread by spend, "
        "same approach as DCs). "
        "**First load** for a wide date range can take a few minutes while GHL "
        "lead data is pulled day-by-day; **repeat loads use cache** and are much faster. "
        "**Signups** through Aug 2025: **GRAND TOTAL New Members** from Digital Cross-Channel "
        "Tracker sheets; from Sep 2025: GHL **Committed?** = Yes + **Sign Up Date**, with "
        "**Membership Level** slicer (sheet months ignore membership filter)."
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

        st.markdown("**Refresh**")
        if st.button(
            "Design only",
            help="Reload layout and styles only. Uses cached data — no Google Ads, Meta, or GHL calls.",
        ):
            st.rerun()

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
    prior_since = _prior_year_date(since)
    prior_until = _prior_year_date(until)
    load_since = min(since, prior_since)
    with st.spinner(load_label):
        try:
            raw_df, notes, lead_summary, conv_by_level_df, unallocated_conv_df, wom_conv_df, tracker_conv_by_level_df, tracker_unallocated_conv_df, combined_conv_by_level_df, combined_unallocated_conv_df, sheet_months, channel_month_leads, cpl_channel_month_leads, unallocated_leads_by_attr, sheet_signup_totals, ghl_signups_by_month, sheet_dcs_totals, ghl_dcs_by_month = _load_data(
                load_since.isoformat(), until.isoformat()
            )
            funnel_df, funnel_notes = _load_funnel_over_time(
                since.isoformat(), until.isoformat()
            )
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

        membership_options = list(MEMBERSHIP_LEVELS)
        selected_membership_levels = st.multiselect(
            "Membership level",
            membership_options,
            default=membership_options,
            help=(
                "Signups from Sep 2025: GHL Committed? = Yes, Sign Up Date in range, "
                "filtered by Membership Level. Pre-Sep 2025 uses tracker sheet totals."
            ),
        )
        if not selected_membership_levels:
            selected_membership_levels = membership_options

        st.markdown("**GHL leads & signup attribution** (Sep 2025+)")
        attribution_labels = {key: label for key, label in GHL_ATTRIBUTION_OPTIONS}
        use_hear_about = st.checkbox(
            attribution_labels[GHL_ATTRIBUTION_HEAR_ABOUT],
            value=True,
            help=(
                "Self-reported **How did you hear about us?** field. Available for "
                "full history; recommended for conservative CPA/CPL."
            ),
        )
        use_tracker = st.checkbox(
            attribution_labels[GHL_ATTRIBUTION_TRACKER],
            value=False,
            help=(
                "Google: **dc thru g-ad** tag or gaClientId. Meta: meta lead tag or "
                "pixel (sparse for older months — Google tracking was added to GHL "
                "more recently). With both boxes on, each contact counts once if "
                "either source matches (conflicts are unallocated)."
            ),
        )
        if not use_hear_about and not use_tracker:
            st.warning(
                "Select at least one attribution source for GHL leads and signups."
            )

        include_organic_leads = st.checkbox(
            "Include Organic leads",
            value=False,
            help=(
                "Leads only (not signups). Adds GHL new contacts without Google/Meta "
                "attribution for the active source(s): blank hear-about, Word of "
                "Mouth, other non-paid values, or hear-about/tracker conflicts. "
                "Off by default. Not channel-filtered — included when checked even "
                "if only Google Ads or FB/IG is selected."
            ),
        )

        include_wom_signups = False
        if use_hear_about:
            include_wom_signups = st.checkbox(
                "Include Word of Mouth signups",
                value=True,
                help=(
                    "Hear-about only. When on, signups whose hear-about contains "
                    "\"word of mouth\" are spread by spend share (lowers CPA). "
                    "Pre-Sep 2025 tracker sheet months are unchanged."
                ),
            )

    attr_kwargs = dict(
        conv_by_level_df=conv_by_level_df,
        tracker_conv_by_level_df=tracker_conv_by_level_df,
        combined_conv_by_level_df=combined_conv_by_level_df,
        selected_levels=selected_membership_levels,
        unallocated_conv_df=unallocated_conv_df,
        tracker_unallocated_conv_df=tracker_unallocated_conv_df,
        combined_unallocated_conv_df=combined_unallocated_conv_df,
        wom_conv_df=wom_conv_df,
        include_wom_signups=include_wom_signups,
        sheet_signup_months=set(sheet_months),
    )

    if "month" not in raw_df.columns:
        raw_df = raw_df.copy()
        raw_df["month"] = raw_df["date"].dt.to_period("M").dt.to_timestamp()

    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()
    prior_since_month = pd.Timestamp(prior_since).to_period("M").to_timestamp()
    prior_until_month = pd.Timestamp(prior_until).to_period("M").to_timestamp()

    raw_selected = raw_df[
        (raw_df["month"] >= since_month) & (raw_df["month"] <= until_month)
    ].copy()

    filtered = apply_dashboard_ghl_attribution(
        raw_selected,
        use_hear_about=use_hear_about,
        use_tracker=use_tracker,
        **attr_kwargs,
    )

    mask = (
        filtered["channel"].isin(selected_channels)
        & (filtered["campaign"].isin(selected_campaigns))
        & (filtered["creative_type"].isin(selected_creatives))
    )
    if meta_types:
        meta_type_mask = (filtered["channel"] != "FB/IG") | (
            filtered["fb_ig_type"].isin(selected_meta_types)
        )
        mask &= meta_type_mask

    df = filtered.loc[mask].copy()
    if "month" not in df.columns:
        df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()

    prior_df = raw_df.loc[
        (raw_df["month"] >= prior_since_month)
        & (raw_df["month"] <= prior_until_month)
    ].copy()
    prior_df = prior_df.loc[
        prior_df["channel"].isin(selected_channels)
        & (prior_df["campaign"].isin(selected_campaigns))
        & (prior_df["creative_type"].isin(selected_creatives))
    ].copy()
    if meta_types:
        prior_meta_mask = (prior_df["channel"] != "FB/IG") | (
            prior_df["fb_ig_type"].isin(selected_meta_types)
        )
        prior_df = prior_df.loc[prior_meta_mask].copy()
    prior_spend_monthly = build_spend_trend_monthly(prior_df)

    trend_monthlies = build_trend_chart_monthlies(
        df,
        channel_month_leads,
        cpl_channel_month_leads,
        unallocated_leads_by_attr,
        since=since.isoformat(),
        until=until.isoformat(),
        sheet_signup_totals=sheet_signup_totals,
        ghl_signups_by_month=ghl_signups_by_month,
        sheet_dcs_totals=sheet_dcs_totals,
        ghl_dcs_by_month=ghl_dcs_by_month,
        selected_channels=selected_channels,
        use_hear_about=use_hear_about,
        use_tracker=use_tracker,
        include_organic=include_organic_leads,
    )
    spend_period_df, use_quarterly = _time_period_summary(
        trend_monthlies.spend, since, until
    )
    cpl_period_df, _ = _time_period_summary(trend_monthlies.cpl, since, until)
    dcs_period_df, _ = _time_period_summary(trend_monthlies.dcs, since, until)
    signups_period_df, _ = _time_period_summary(trend_monthlies.signups, since, until)
    scores = _weighted_scorecard_metrics(
        df,
        selected_channels=selected_channels,
        all_channels=channels,
        selected_campaigns=selected_campaigns,
        campaign_pool=campaign_pool,
        lead_summary=lead_summary,
        channel_month_leads=channel_month_leads,
        since_month=since_month,
        until_month=until_month,
        use_hear_about=use_hear_about,
        use_tracker=use_tracker,
    )

    _metric_row(
        [
            ("Spend", _fmt_currency(scores["spend"])),
            ("Clicks", _fmt_int(scores["clicks"])),
            ("Cost per click", _fmt_currency(scores["cpc"])),
            ("Leads", _fmt_int(scores["leads"])),
            ("Cost per lead", _fmt_currency(scores["cpl"])),
            ("DCs", _fmt_int(scores["dcs"])),
            ("Avg. $ per DC", _fmt_currency(scores["cpdc"])),
            ("Signups", _fmt_int(scores["conversions"])),
            ("Avg. CPA", _fmt_currency(scores["cac"])),
            ("Signup to DC %", _fmt_pct(scores["lead_to_patient_pct"])),
        ],
    )

    if use_hear_about and use_tracker:

        def _signup_cpa(use_hear: bool, use_track: bool) -> str:
            snap = apply_dashboard_ghl_attribution(
                raw_selected.copy(),
                use_hear_about=use_hear,
                use_tracker=use_track,
                **attr_kwargs,
            )
            snap = snap.loc[mask]
            spend = float(snap["spend"].sum())
            signups = float(snap["conversions"].sum())
            return _fmt_currency(spend / signups if signups else None)

        st.caption(
            "Signup CPA with current filters — "
            f"Hear-about only: {_signup_cpa(True, False)} · "
            f"Tracker only: {_signup_cpa(False, True)} · "
            f"Both (deduped OR): {_signup_cpa(True, True)}"
        )

    st.markdown("---")
    st.subheader("Trends over time")
    if use_quarterly:
        st.caption(
            "Date range exceeds 9 months — trend charts show **quarterly** totals on the x-axis."
        )
    else:
        st.caption("Trend charts show **monthly** totals on the x-axis.")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.plotly_chart(
            _spend_over_time_chart(
                spend_period_df,
                prior_spend_monthly,
                prior_since=prior_since,
                prior_until=prior_until,
            ),
            use_container_width=True,
        )
    with c2:
        cpl_chart = _cpl_over_time_chart(cpl_period_df)
        if cpl_chart:
            st.plotly_chart(cpl_chart, use_container_width=True)
            july = pd.Timestamp("2025-07-01")
            if not cpl_period_df.empty and (
                july in set(pd.to_datetime(cpl_period_df["month"]).dt.to_period("M").dt.to_timestamp())
                or (
                    cpl_period_df["period_label"].astype(str).str.contains("Q3 2025", na=False).any()
                )
            ):
                st.caption(
                    "Jul 2025 uses the average of Jun and Aug 2025 (legacy CRM import into GHL)."
                )
            if include_organic_leads:
                st.caption(
                    "CPL includes **Organic** leads (non–paid-attributed contacts) in the "
                    "denominator; spend still follows channel and campaign filters."
                )
        else:
            st.info("CPL over time unavailable (no leads in the selected range).")
    with c3:
        st.plotly_chart(
            _line_chart(dcs_period_df, ["dcs"], "DCs Over Time", "DCs"),
            use_container_width=True,
        )
        st.caption(
            "Org-wide discovery calls — **Digital Cross-Channel Tracker** Calls completed "
            f"through {pd.Timestamp(SHEETS_DCS_UNTIL).strftime('%b %Y')}; "
            f"**GoHighLevel** calendar meetings (startTime) on "
            f"{len(discovery_call_calendar_ids())} discovery-call calendar(s) from "
            f"{pd.Timestamp(GHL_DCS_SINCE).strftime('%b %Y')} onward. "
            "Not affected by campaign or attribution filters."
        )
    with c4:
        st.plotly_chart(
            _line_chart(
                signups_period_df,
                ["conversions"],
                "Signups Over Time",
                "Signups",
            ),
            use_container_width=True,
        )
        st.caption(
            "Org-wide signups — **Digital Cross-Channel Tracker** GRAND TOTAL New Members "
            f"through {pd.Timestamp(SHEETS_SIGNUPS_UNTIL).strftime('%b %Y')}; "
            f"**GoHighLevel** committed members by Sign Up Date from "
            f"{pd.Timestamp(GHL_SIGNUPS_SINCE).strftime('%b %Y')} onward. "
            "Not affected by campaign or attribution filters."
        )

    funnel_chart = _funnel_over_time_chart(funnel_df)
    if funnel_chart:
        st.plotly_chart(funnel_chart, use_container_width=True)
    else:
        st.info("No funnel data for the selected date range.")
    st.caption(
        "Org-wide monthly totals through Aug 30, 2025 — **HubSpot** leads, **Digital "
        "Cross-Channel Tracker** Calls completed and GRAND TOTAL signups (signups match "
        "Signups Over Time). **GoHighLevel** from "
        f"{pd.Timestamp(GHL_SIGNUPS_SINCE).strftime('%b %Y')} onward (new contacts by "
        f"date added, {len(discovery_call_calendar_ids())} discovery-call calendars by "
        "meeting date, committed signups by sign-up date). "
        "Not affected by campaign or attribution filters above."
    )
    with st.expander("Funnel chart — data sources"):
        for note in funnel_notes:
            st.markdown(f"- {note}")

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
