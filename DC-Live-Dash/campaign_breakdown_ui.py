"""Campaign breakdown charts and table — shared by live dashboard and channel report."""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

COLORS = {
    "accent_dark": "#264540",
}

_TITLE_LEGEND_GAP_PX = 20
_TITLE_BLOCK_PX = 22
_LEGEND_BLOCK_PX = 22
_BASE_CHART_PADDING_PX = 12

CAMPAIGN_DISPLAY_COLS = [
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


def _help_paragraphs_html(*paragraphs: str) -> str:
    parts = [p.strip() for p in paragraphs if p and p.strip()]
    return "".join(f"<p>{part}</p>" for part in parts)


def campaign_breakdown_title_help_html(*, default_months: int) -> str:
    return _help_paragraphs_html(
        f"""Live <strong>Google Ads</strong> and <strong>Meta</strong> campaign performance
with GHL-attributed leads, discovery calls, and signups. Default view is the last
<strong>{default_months} months</strong>.""",
        """Use sidebar filters for channel, campaign, asset type, FB/IG type, and GHL
attribution. Charts show creative spend allocation and spend vs clicks for the
top campaigns.""",
        """<strong>First load</strong> for a wide date range can take a minute while GHL data
is pulled; repeat loads use disk cache.""",
    )


def campaign_breakdown_filters_help_html() -> str:
    return _help_paragraphs_html(
        """Channel, campaign, asset type, and FB/IG type narrow the charts and table.
Membership level affects GHL signups from Sep 2025 onward.""",
        """<strong>GHL attribution (Sep 2025+)</strong> — <strong>How did you hear about us?</strong>
maps self-reported Google / FB/IG responses. <strong>Tracker</strong> uses tag/pixel
attribution. When both are checked, counts use deduped hear-about ∪ tracker.""",
        """Optional toggles spread Word of Mouth or Other signups by spend share.""",
    )


def campaign_breakdown_help_html() -> str:
    return _help_paragraphs_html(
        """Filtered campaign rows for the selected date range and sidebar filters.""",
        """<strong>Creative allocation</strong> — spend share by asset type (Text, Video,
Image, Combo, etc.).""",
        """<strong>Spend vs clicks</strong> — top campaigns by spend with grouped spend
and click bars.""",
        """Expand <strong>View filtered campaign data</strong> for the underlying table.""",
    )


def _chart_margin(
    *,
    has_legend: bool = False,
    bottom: int = 20,
    left: int = 20,
    right: int = 20,
    extra_top: int = 0,
    legend_plot_gap_px: int = 0,
) -> dict[str, int]:
    t = _BASE_CHART_PADDING_PX + _TITLE_BLOCK_PX + extra_top
    if has_legend:
        t += _TITLE_LEGEND_GAP_PX + _LEGEND_BLOCK_PX + legend_plot_gap_px
    return dict(l=left, r=right, t=t, b=bottom)


def _chart_legend_top(
    *,
    chart_height: int = 360,
    plot_gap_px: int = 0,
    **overrides: Any,
) -> dict[str, Any]:
    y = 1.0 + (plot_gap_px / chart_height if plot_gap_px else 0.0)
    legend = dict(
        orientation="h",
        yanchor="bottom",
        y=y,
        x=0,
        xanchor="left",
        title_text="",
    )
    legend.update(overrides)
    return legend


def spend_click_correlation(df: pd.DataFrame) -> go.Figure | None:
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
        margin=_chart_margin(has_legend=True, bottom=100),
        legend=_chart_legend_top(x=0.5, xanchor="center"),
        xaxis_tickangle=-35,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def creative_allocation_pie(df: pd.DataFrame) -> go.Figure | None:
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
        margin=_chart_margin(has_legend=False, extra_top=8),
        font=dict(family="Roboto, sans-serif", color=COLORS["accent_dark"]),
    )
    return fig


def render_campaign_breakdown(
    df: pd.DataFrame,
    *,
    render_heading_with_help: Callable[..., None],
    show_section_divider: bool = True,
) -> None:
    """Render creative allocation, spend/clicks chart, and campaign table."""
    if show_section_divider:
        st.markdown("---")
    render_heading_with_help(
        "Campaign breakdown",
        campaign_breakdown_help_html(),
        style="section",
    )

    col_pie, col_bar = st.columns(2)
    with col_pie:
        pie = creative_allocation_pie(df)
        if pie:
            st.plotly_chart(pie, use_container_width=True)
    with col_bar:
        corr = spend_click_correlation(df)
        if corr:
            st.plotly_chart(corr, use_container_width=True)

    with st.expander("View filtered campaign data"):
        st.dataframe(
            df[CAMPAIGN_DISPLAY_COLS].sort_values("date", ascending=False),
            use_container_width=True,
            hide_index=True,
        )
