"""
Digital Channel Dashboard on localhost — live Google Ads, Meta, and GoHighLevel data.

    streamlit run DC-Live-Dash/digital_channel_live_dashboard.py --server.port 8850

Open:

    http://127.0.0.1:8850/
"""

from __future__ import annotations

import importlib
import html as html_module
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DC_LIVE_DIR = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_DC_LIVE_DIR) not in sys.path:
    sys.path.insert(0, str(_DC_LIVE_DIR))

import ghl_client as _ghl_client_mod

_EXPECTED_GHL_CLIENT_REVISION = "2026-06-24-calendar-meetings-monthly-v1"
if (
    getattr(_ghl_client_mod, "GHL_CLIENT_REVISION", None)
    != _EXPECTED_GHL_CLIENT_REVISION
):
    _ghl_client_mod = importlib.reload(_ghl_client_mod)

discovery_call_calendar_ids = _ghl_client_mod.discovery_call_calendar_ids

import total_new_members_yoy_chart as _tracker_chart_mod

if not hasattr(_tracker_chart_mod, "_load_row_year_series"):
    _tracker_chart_mod = importlib.reload(_tracker_chart_mod)

import digital_channel_live_data as _live_data_mod

_EXPECTED_LIVE_DATA_REVISION = "2026-06-24-hear-about-data-tab-signups-v1"
if (
    getattr(_live_data_mod, "LIVE_DATA_REVISION", None)
    != _EXPECTED_LIVE_DATA_REVISION
):
    _live_data_mod = importlib.reload(_live_data_mod)

import funnel_over_time_data as _funnel_mod
import signup_comparison_data as _signup_cmp_mod
import bookings_meetings_comparison_data as _bookings_meetings_mod

_EXPECTED_FUNNEL_REVISION = "2026-06-25-consolidated-terminations-v1"
if (
    getattr(_funnel_mod, "FUNNEL_OVER_TIME_REVISION", None)
    != _EXPECTED_FUNNEL_REVISION
):
    _funnel_mod = importlib.reload(_funnel_mod)

_EXPECTED_SIGNUP_CMP_REVISION = "2026-06-23-meta-tracker-ghl-backfill-v1"
if (
    getattr(_signup_cmp_mod, "SIGNUP_COMPARISON_REVISION", None)
    != _EXPECTED_SIGNUP_CMP_REVISION
):
    _signup_cmp_mod = importlib.reload(_signup_cmp_mod)

_EXPECTED_BOOKINGS_MEETINGS_REVISION = "2026-06-24-discovery-calls-charts-v5"
if (
    getattr(_bookings_meetings_mod, "BOOKINGS_MEETINGS_COMPARISON_REVISION", None)
    != _EXPECTED_BOOKINGS_MEETINGS_REVISION
):
    _bookings_meetings_mod = importlib.reload(_bookings_meetings_mod)

FUNNEL_OVER_TIME_REVISION = _funnel_mod.FUNNEL_OVER_TIME_REVISION
GHL_FUNNEL_SINCE = _funnel_mod.GHL_FUNNEL_SINCE
SIGNUP_COMPARISON_REVISION = _signup_cmp_mod.SIGNUP_COMPARISON_REVISION
BOOKINGS_MEETINGS_COMPARISON_REVISION = (
    _bookings_meetings_mod.BOOKINGS_MEETINGS_COMPARISON_REVISION
)
BOOKINGS_MEETINGS_CATEGORY = _bookings_meetings_mod.BOOKINGS_MEETINGS_CATEGORY
DISCOVERY_CALLS_LABEL = _bookings_meetings_mod.DISCOVERY_CALLS_LABEL
aggregate_signups_qoq = _signup_cmp_mod.aggregate_signups_qoq
aggregate_signups_yoy = _signup_cmp_mod.aggregate_signups_yoy
load_tier_signups_by_level_monthly = _signup_cmp_mod.load_tier_signups_by_level_monthly
load_bookings_meetings_comparison_monthly = (
    _bookings_meetings_mod.load_bookings_meetings_comparison_monthly
)
monthly_for_signup_charts = _bookings_meetings_mod.monthly_for_signup_charts
bookings_meetings_until = _bookings_meetings_mod.bookings_meetings_until
qoq_quarter_numbers = _signup_cmp_mod.qoq_quarter_numbers
tier_quarter_filter_options = _signup_cmp_mod.tier_quarter_filter_options
tier_signup_until = _signup_cmp_mod.tier_signup_until
tier_year_filter_options = _signup_cmp_mod.tier_year_filter_options
from digital_channel_live_data import (
    DEFAULT_DASHBOARD_MONTHS,
    GHL_ATTRIBUTION_HEAR_ABOUT,
    GHL_ATTRIBUTION_OPTIONS,
    GHL_ATTRIBUTION_TRACKER,
    GHL_ATTRIBUTED_SIGNUPS_SINCE,
    GHL_SIGNUPS_SINCE,
    GHL_DCS_SINCE,
    LIVE_DATA_REVISION,
    MEMBERSHIP_LEVELS,
    SHEETS_SIGNUPS_UNTIL,
    SHEETS_DCS_UNTIL,
    SHEET_LEADS_UNTIL,
    apply_dashboard_ghl_attribution,
    build_trend_chart_monthlies,
    channel_month_leads_total,
    clear_dashboard_disk_cache,
    clear_ghl_leads_day_cache,
    default_dashboard_since,
    load_dashboard_bundle,
    scorecard_metrics,
)

DASHBOARD_BUNDLE_REVISION = (
    f"{LIVE_DATA_REVISION}|{FUNNEL_OVER_TIME_REVISION}|{SIGNUP_COMPARISON_REVISION}"
)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

COLORS = {
    "accent": "#5DA68A",
    "accent_dark": "#264540",
    "page_bg": "#F4F8FB",
    "sidebar_bg": "#F7FBFF",
    "2023": "#4C78A8",
    "2024": "#F58518",
    "2025": "#54A24B",
    "2026": "#B279A2",
    "muted": "#6B7C93",
    "funnel_leads": "#7EC8E3",
    "funnel_dcs": "#1E5FA8",
    "funnel_signups": "#54A24B",
    "funnel_terminations": "#D64545",
}

YEAR_BAR_COLORS = {
    "2023": COLORS["2023"],
    "2024": COLORS["2024"],
    "2025": COLORS["2025"],
    "2026": COLORS["2026"],
}

MONTH_ORDER = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

CHANNEL_GOOGLE = "Google Ads"
CHANNEL_META = "FB/IG"

_QUARTERLY_RANGE_MONTHS = 9
_TITLE_LEGEND_GAP_PX = 20
_TITLE_BLOCK_PX = 22
_LEGEND_BLOCK_PX = 22
_BASE_CHART_PADDING_PX = 12
_TIER_TITLE_HEIGHT_PX = 18
_TIER_LEGEND_HEIGHT_PX = 20
_TIER_SECTION_GAP_PX = 20
_DISCOVERY_CALLS_TITLE_LEGEND_GAP_PX = 36
_TIER_CHART_LEFT = 48
_TIER_CHART_BOTTOM = 52
_TIER_CHART_RIGHT = 20


def _signups_tier_top_margin_px(*, title_legend_gap_px: int | None = None) -> int:
    """Title + gap + legend + gap above the plot."""
    plot_gap = _TIER_SECTION_GAP_PX
    title_legend_gap = (
        title_legend_gap_px if title_legend_gap_px is not None else _TIER_SECTION_GAP_PX
    )
    return (
        _TIER_TITLE_HEIGHT_PX
        + title_legend_gap
        + _TIER_LEGEND_HEIGHT_PX
        + plot_gap
    )


def _signups_tier_pair_margin(*, title_legend_gap_px: int | None = None) -> dict[str, int]:
    return dict(
        l=_TIER_CHART_LEFT,
        r=_TIER_CHART_RIGHT,
        t=_signups_tier_top_margin_px(title_legend_gap_px=title_legend_gap_px),
        b=_TIER_CHART_BOTTOM,
    )


def _signups_tier_paper_above_plot(
    tier_height: int,
    px_above_plot_top: float,
    *,
    title_legend_gap_px: int | None = None,
) -> float:
    """Paper y at ``px_above_plot_top`` pixels above the inner plot top (y=1)."""
    plot_h = _signups_tier_plot_height_px(
        tier_height, title_legend_gap_px=title_legend_gap_px
    )
    if plot_h <= 0:
        return 1.0
    return 1.0 + px_above_plot_top / plot_h


def _signups_tier_plot_height_px(
    tier_height: int, *, title_legend_gap_px: int | None = None
) -> float:
    return float(
        tier_height
        - _signups_tier_top_margin_px(title_legend_gap_px=title_legend_gap_px)
        - _TIER_CHART_BOTTOM
    )


def _signups_tier_chart_height(quarter_count: int) -> int:
    """Shared figure height for YoY and QoQ signup tier charts."""
    return max(360, 120 * max(quarter_count, 1))


def _strip_plotly_auto_titles(fig: go.Figure) -> None:
    """Remove empty layout/legend titles px.bar leaves behind (renders as 'undefined')."""
    fig.layout.pop("title", None)
    legend = fig.layout.legend
    if legend is not None and legend.title is not None:
        legend.title = None


def _apply_signups_tier_pair_layout(
    fig: go.Figure,
    *,
    tier_height: int,
    title_text: str,
    quarter_labels: list[str] | None = None,
    title_legend_gap_px: int | None = None,
) -> None:
    """
    Precise vertical stack: title → gap → legend → gap → plot.

    Title and legend sit in the top margin using paper y > 1; the plot begins
    exactly 20px below the reserved legend band.
    """
    plot_gap = _TIER_SECTION_GAP_PX
    title_legend_gap = (
        title_legend_gap_px if title_legend_gap_px is not None else _TIER_SECTION_GAP_PX
    )
    legend_h = _TIER_LEGEND_HEIGHT_PX
    title_h = _TIER_TITLE_HEIGHT_PX

    legend_bottom_px = plot_gap
    title_top_px = plot_gap + legend_h + title_legend_gap + title_h

    fig.update_layout(
        height=tier_height,
        margin=_signups_tier_pair_margin(title_legend_gap_px=title_legend_gap_px),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Roboto, sans-serif", color=COLORS["accent_dark"]),
        legend=dict(
            orientation="h",
            traceorder="normal",
            x=0.5,
            xanchor="center",
            yanchor="bottom",
            y=_signups_tier_paper_above_plot(
                tier_height,
                legend_bottom_px,
                title_legend_gap_px=title_legend_gap_px,
            ),
            bgcolor="rgba(0,0,0,0)",
            title_text="",
        ),
    )
    _strip_plotly_auto_titles(fig)
    fig.add_annotation(
        text=title_text,
        xref="paper",
        yref="paper",
        x=0.5,
        xanchor="center",
        yanchor="top",
        y=_signups_tier_paper_above_plot(
            tier_height,
            title_top_px,
            title_legend_gap_px=title_legend_gap_px,
        ),
        showarrow=False,
        font=dict(size=14, color=COLORS["accent_dark"], weight=700),
    )
    fig.update_xaxes(tickangle=-45, side="bottom")
    fig.update_yaxes(rangemode="tozero")

    if quarter_labels:
        for annotation in fig.layout.annotations or []:
            label = (annotation.text or "").strip()
            if label in quarter_labels:
                annotation.update(
                    y=0.99,
                    yref="paper",
                    yanchor="top",
                    showarrow=False,
                    font=dict(size=14, color=COLORS["accent_dark"], weight=700),
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
    """Top margin sized for title, optional legend, and gap between title and legend."""
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
    """Horizontal legend above the plot; ``plot_gap_px`` adds space below the legend."""
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


def _help_paragraphs_html(*paragraphs: str) -> str:
    """Build tooltip body from HTML paragraph strings."""
    parts = [p.strip() for p in paragraphs if p and p.strip()]
    return "".join(f"<p>{part}</p>" for part in parts)


def _help_list_html(items: list[str]) -> str:
    if not items:
        return ""
    lis = "".join(f"<li>{html_module.escape(item)}</li>" for item in items)
    return f'<ul class="dash-help-list">{lis}</ul>'


def _render_heading_with_help(
    title: str,
    tooltip_html: str,
    *,
    style: str = "section",
    aria_label: str | None = None,
) -> None:
    """Render a heading with a hover/focus ? tooltip."""
    styles: dict[str, tuple[str, str, str]] = {
        "title": ("h1", "dash-title-row", "dash-title"),
        "section": ("h2", "dash-section-row", "dash-section-heading"),
        "subsection": ("h3", "dash-subsection-row", "dash-subsection-heading"),
        "sidebar": ("h2", "dash-sidebar-row", "dash-sidebar-heading"),
        "label": ("span", "dash-label-row", "dash-label-heading"),
    }
    tag, row_cls, head_cls = styles.get(style, styles["section"])
    safe_title = html_module.escape(title)
    safe_label = html_module.escape(aria_label or f"About {title}")
    st.markdown(
        f"""
        <div class="{row_cls}">
            <{tag} class="{head_cls}">{safe_title}</{tag}>
            <span class="dash-help-wrap dash-help-wrap--{style}">
                <button type="button" class="dash-help-btn" aria-label="{safe_label}">?</button>
                <div class="dash-help-tooltip" role="tooltip">{tooltip_html}</div>
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _dashboard_methodology_tooltip_body() -> str:
    """Consolidated methodology copy for the title help tooltip."""
    return _help_paragraphs_html(
        f"""Live data from <strong>Google Ads</strong>, <strong>Meta</strong>, and
<strong>GoHighLevel</strong>. Leads through Jun 2025 use
<strong>Digital Channel Dashboard 2024-25</strong> Data tab; later months use GHL
new contacts (attributed + unallocated spread by spend, same approach as DCs).
<strong>First load</strong> for a wide date range can take a minute while GHL lead
data is pulled day-by-day (Jul 2025 onward only); <strong>repeat loads use disk
cache</strong> and are much faster. Default view is the last
<strong>{DEFAULT_DASHBOARD_MONTHS} months</strong> — widen the date range below for
full history.""",
        """<strong>Signups</strong> through Aug 2025: <strong>GRAND TOTAL New Members</strong>
from Digital Cross-Channel Tracker sheets; from Sep 2025: GHL
<strong>Committed?</strong> = Yes + <strong>Sign Up Date</strong>, with
<strong>Membership Level</strong> slicer (sheet months ignore membership filter).""",
        """Applies to the scorecard and trend charts (Spend, CPL, DCs, Signups). The
<strong>funnel</strong> chart below stays org-wide.
<strong>Signups by membership level</strong> charts use full history with their own
year/quarter filters.""",
    )


def _filters_help_html() -> str:
    return _help_paragraphs_html(
        """Channel, campaign, asset type, FB/IG type, and membership level narrow the
scorecard and trend charts (Spend, CPL, DCs, Signups). Membership level affects
GHL signups from Sep 2025 onward.""",
        """<strong>Non-paid inclusion</strong> — optional additions to paid metrics, not ad
channels. <strong>Include Organic leads</strong> adds GHL contacts without Google/Meta
attribution (blank hear-about, Word of Mouth, other non-paid values, or conflicts).
Leads only — not signups. Not channel-filtered.""",
        """<strong>GHL attribution (Sep 2025+)</strong> — <strong>How did you hear about us?</strong>
maps self-reported Google / FB/IG responses. <strong>Tracker</strong> uses tag/pixel
attribution. When both are checked, counts use deduped hear-about ∪ tracker (not
double-counted). Optional toggles spread Word of Mouth or Other signups by spend share.""",
    )


def _refresh_help_html() -> str:
    return _help_paragraphs_html(
        """<strong>Design only</strong> — reload layout and styles using cached data (no API calls).""",
        """<strong>Refresh data</strong> — reload Google Ads, Meta, and GoHighLevel while
keeping cached GHL daily lead files.""",
        """<strong>Hard refresh GHL leads</strong> — clear GHL daily lead cache first, then
reload (use if lead counts look wrong).""",
    )


def _trends_over_time_help_html(
    *,
    use_quarterly: bool,
    scorecard_lead_notes: list[str],
    strict_signup_note: str | None,
    active_attribution_note: str | None,
    show_july_cpl_note: bool,
    include_organic_leads: bool,
    loader_notes: list[str],
    funnel_notes: list[str],
) -> str:
    grain = (
        "Date range exceeds 9 months — trend charts show <strong>quarterly</strong> totals."
        if use_quarterly
        else "Trend charts show <strong>monthly</strong> totals on the x-axis."
    )
    parts = [
        grain,
        """<strong>Org-wide funnel</strong> — not affected by sidebar filters. Through Aug 30,
2025: <strong>HubSpot</strong> leads and <strong>Digital Cross-Channel Tracker</strong>
Calls completed and GRAND TOTAL signups. From """
        + f"{pd.Timestamp(GHL_SIGNUPS_SINCE).strftime('%b %Y')}: "
        """<strong>GoHighLevel</strong> for leads, discovery calls, and signups.
<strong>Terminations</strong> from <strong>Terminated Memberships 2023-2025</strong>
Consolidated Data tab (Date of Termination).""",
        """Scorecard and the four trend charts (Spend, CPL, DCs, Signups) follow sidebar
filters and active attribution.""",
    ]
    if scorecard_lead_notes:
        parts.append(" ".join(scorecard_lead_notes))
    if strict_signup_note:
        parts.append(strict_signup_note)
    if active_attribution_note:
        parts.append(active_attribution_note)
    if show_july_cpl_note:
        parts.append(
            "Jul 2025 CPL uses the average of Jun and Aug 2025 (legacy CRM import into GHL)."
        )
    if include_organic_leads:
        parts.append(
            "CPL includes <strong>Organic</strong> leads in the denominator; spend still "
            "follows channel and campaign filters."
        )
    parts.append(
        f"""<strong>DCs Over Time</strong> — discovery calls matching sidebar filters. From
{pd.Timestamp(GHL_DCS_SINCE).strftime('%b %Y')}: GoHighLevel meetings (<code>startTime</code>)
on {len(discovery_call_calendar_ids())} configured discovery-call calendars (cancelled and
no-show excluded). Pre-Sep 2025 uses tracker <strong>Calls completed</strong> (spend-weighted)."""
    )
    parts.append(
        """<strong>Signups Over Time</strong> — same totals as scorecard Signups, rolled up by
month. Pre-Sep 2025 sheet months use spend-weighted tracker splits unless hear-about mode
uses the Data tab (pre-Jul) or GHL (Jul–Aug)."""
    )
    body = _help_paragraphs_html(*parts)
    if funnel_notes:
        body += (
            "<p><strong>Funnel chart sources</strong></p>"
            + _help_list_html(list(funnel_notes))
        )
    if loader_notes:
        body += (
            "<p><strong>Loader notes</strong></p>" + _help_list_html(list(loader_notes))
        )
    return body


def _signups_by_level_help_html(
    *,
    tier_until: date,
    signup_cmp_notes: list[str],
) -> str:
    body = _help_paragraphs_html(
        """Org-wide committed signups by <strong>Membership Level</strong>.""",
        f"""Through {pd.Timestamp(SHEETS_SIGNUPS_UNTIL).strftime('%b %Y')}: <strong>Digital
Cross-Channel Tracker</strong> Both Locations tier rows. From
{pd.Timestamp(GHL_SIGNUPS_SINCE).strftime('%b %Y')}: <strong>GoHighLevel</strong> (Sign Up
Date, Committed? = Yes).""",
        f"""These charts ignore the main date range — full history through
<strong>{tier_until:%b %d, %Y}</strong>. Use <strong>Years</strong> / <strong>Quarters</strong>
multiselects; current year/quarter shown as <strong>YTD</strong> / <strong>QTD</strong> when
still open. Respects membership level filter; not affected by channel, campaign, or Word of
Mouth toggles.""",
    )
    if signup_cmp_notes:
        body += (
            "<p><strong>Data sources</strong></p>" + _help_list_html(list(signup_cmp_notes))
        )
    return body


def _discovery_calls_help_html(
    *,
    bm_until: date,
    bm_notes: list[str],
) -> str:
    body = _help_paragraphs_html(
        """Org-wide <strong>Discovery Calls</strong>: tracker <strong>Bookings (all booked
calls)</strong> through """
        + f"{pd.Timestamp(SHEETS_SIGNUPS_UNTIL).strftime('%b %Y')}, then "
        + f"""<strong>GoHighLevel meetings</strong> (calendar <code>startTime</code>, all
calendars) from {pd.Timestamp(GHL_SIGNUPS_SINCE).strftime('%b %Y')}.""",
        f"""These charts ignore the main date range — full history through
<strong>{bm_until:%b %d, %Y}</strong>. Use <strong>Years</strong> / <strong>Quarters</strong>
multiselects; current year/quarter shown as <strong>YTD</strong> / <strong>QTD</strong> when
still open. Not affected by sidebar channel, campaign, or attribution filters.""",
    )
    if bm_notes:
        body += "<p><strong>Data sources</strong></p>" + _help_list_html(list(bm_notes))
    return body


def _campaign_breakdown_help_html() -> str:
    return _help_paragraphs_html(
        """Filtered campaign rows for the selected date range and sidebar filters.""",
        """<strong>Creative allocation</strong> — spend share by asset type (Text, Video,
Image, Combo, etc.).""",
        """<strong>Spend vs clicks</strong> — correlation scatter for filtered campaigns.""",
        """Expand <strong>View filtered campaign data</strong> for the underlying table.""",
    )


def _date_range_help_html() -> str:
    return _help_paragraphs_html(
        f"Default view is the last <strong>{DEFAULT_DASHBOARD_MONTHS} months</strong>. "
        "Widen for full history.",
        """Applies to the scorecard and trend charts (Spend, CPL, DCs, Signups). The
funnel uses the same range but stays org-wide. Signups by membership level and
Discovery Calls use full history with their own year/quarter filters.""",
    )


def _render_dashboard_title() -> None:
    """Dashboard title with hover/focus methodology tooltip."""
    _render_heading_with_help(
        "Digital Channel Dashboard",
        _dashboard_methodology_tooltip_body(),
        style="title",
    )


def _inject_styles() -> None:
    st.markdown(
        f"""
        <style>
        .stApp {{
            background: {COLORS["page_bg"]} !important;
            color: {COLORS["accent_dark"]};
        }}
        [data-testid="stAppViewContainer"] {{
            background: {COLORS["page_bg"]};
        }}
        [data-testid="stHeader"] {{
            background: rgba(244, 248, 251, 0.92);
        }}
        .block-container {{
            padding-top: 1.5rem;
            max-width: 100%;
            color: {COLORS["accent_dark"]};
        }}
        [data-testid="stMetric"] {{
            background: white;
            border-radius: 12px;
            padding: 0.5rem 0.6rem;
            box-shadow: 0 1px 3px rgba(38,69,64,0.08);
            border: 1px solid rgba(93,166,138,0.15);
        }}
        [data-testid="stMetricLabel"] {{
            color: {COLORS["accent_dark"]} !important;
            font-weight: 600;
            font-size: 0.72rem;
            white-space: nowrap;
        }}
        [data-testid="stMetricValue"] {{
            color: {COLORS["accent"]} !important;
            font-size: 1.05rem;
        }}
        h1, h2, h3, h4, h5 {{
            color: {COLORS["accent_dark"]} !important;
            margin-top: 1rem !important;
        }}
        [data-testid="stCaptionContainer"] p {{
            color: {COLORS["muted"]} !important;
        }}
        [data-testid="stSidebar"] {{
            background: {COLORS["sidebar_bg"]} !important;
        }}
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {{
            color: {COLORS["accent_dark"]} !important;
        }}
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3 {{
            color: {COLORS["accent_dark"]} !important;
        }}
        [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
        [data-testid="stSidebar"] [data-testid="stWidgetLabel"],
        [data-testid="stSidebar"] [data-testid="stCheckbox"] label p,
        [data-testid="stSidebar"] [data-testid="stCheckbox"] label span,
        [data-testid="stSidebar"] [data-testid="stMultiSelect"] label p {{
            color: {COLORS["accent_dark"]} !important;
        }}
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {{
            color: {COLORS["muted"]} !important;
        }}
        .dash-title-row {{
            display: flex;
            align-items: center;
            gap: 0.65rem;
            margin: 0 0 0.35rem 0;
        }}
        .dash-section-row,
        .dash-subsection-row,
        .dash-sidebar-row,
        .dash-label-row {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
            margin: 0 0 0.5rem 0;
        }}
        .dash-title {{
            margin: 0 !important;
            padding: 0;
            font-size: 2.25rem;
            font-weight: 600;
            line-height: 1.2;
            color: {COLORS["accent_dark"]} !important;
        }}
        .dash-section-heading {{
            margin: 0 !important;
            padding: 0;
            font-size: 1.5rem;
            font-weight: 600;
            line-height: 1.25;
            color: {COLORS["accent_dark"]} !important;
        }}
        .dash-subsection-heading {{
            margin: 0 !important;
            padding: 0;
            font-size: 1.15rem;
            font-weight: 600;
            line-height: 1.25;
            color: {COLORS["accent_dark"]} !important;
        }}
        .dash-sidebar-heading {{
            margin: 0 !important;
            padding: 0;
            font-size: 1.25rem;
            font-weight: 600;
            line-height: 1.25;
            color: {COLORS["accent_dark"]} !important;
        }}
        .dash-label-heading {{
            margin: 0 !important;
            padding: 0;
            font-size: 1rem;
            font-weight: 600;
            line-height: 1.25;
            color: {COLORS["accent_dark"]} !important;
        }}
        .dash-help-wrap {{
            position: relative;
            display: inline-flex;
            align-items: center;
            flex-shrink: 0;
        }}
        .dash-help-wrap--title {{
            margin-top: 0.35rem;
        }}
        .dash-help-wrap--section .dash-help-btn,
        .dash-help-wrap--subsection .dash-help-btn {{
            width: 1.2rem;
            height: 1.2rem;
            font-size: 0.72rem;
        }}
        .dash-help-wrap--sidebar .dash-help-btn,
        .dash-help-wrap--label .dash-help-btn {{
            width: 1.1rem;
            height: 1.1rem;
            font-size: 0.68rem;
        }}
        .dash-help-btn {{
            width: 1.35rem;
            height: 1.35rem;
            border-radius: 50%;
            border: 1.5px solid {COLORS["muted"]};
            background: white;
            color: {COLORS["muted"]};
            font-size: 0.78rem;
            font-weight: 700;
            cursor: help;
            padding: 0;
            line-height: 1;
            flex-shrink: 0;
            transition: border-color 0.15s ease, color 0.15s ease, box-shadow 0.15s ease;
        }}
        .dash-help-btn:hover,
        .dash-help-btn:focus-visible {{
            border-color: {COLORS["accent"]};
            color: {COLORS["accent"]};
            outline: none;
            box-shadow: 0 0 0 3px rgba(93, 166, 138, 0.22);
        }}
        .dash-help-tooltip {{
            position: absolute;
            top: calc(100% + 10px);
            left: 0;
            z-index: 10000;
            width: min(440px, calc(100vw - 2rem));
            padding: 0.9rem 1rem;
            background: white;
            border: 1px solid rgba(93, 166, 138, 0.22);
            border-radius: 12px;
            box-shadow: 0 10px 28px rgba(38, 69, 64, 0.14);
            opacity: 0;
            visibility: hidden;
            pointer-events: none;
            transition: opacity 0.15s ease, visibility 0.15s ease;
            text-align: left;
        }}
        .dash-help-tooltip p {{
            margin: 0 0 0.7rem 0;
            color: {COLORS["accent_dark"]};
            font-size: 0.84rem;
            line-height: 1.5;
            font-weight: 400;
        }}
        .dash-help-tooltip p:last-child {{
            margin-bottom: 0;
        }}
        .dash-help-tooltip strong {{
            color: {COLORS["accent_dark"]};
            font-weight: 600;
        }}
        .dash-help-tooltip code {{
            font-size: 0.8em;
            color: {COLORS["muted"]};
        }}
        .dash-help-list {{
            margin: 0.35rem 0 0.65rem 0;
            padding-left: 1.1rem;
            color: {COLORS["accent_dark"]};
            font-size: 0.82rem;
            line-height: 1.45;
        }}
        .dash-help-list li {{
            margin-bottom: 0.35rem;
        }}
        .dash-help-list li:last-child {{
            margin-bottom: 0;
        }}
        [data-testid="stSidebar"] .dash-help-tooltip {{
            left: auto;
            right: 0;
            width: min(360px, calc(100vw - 2rem));
        }}
        .dash-help-wrap:hover .dash-help-tooltip,
        .dash-help-wrap:focus-within .dash-help-tooltip {{
            opacity: 1;
            visibility: visible;
        }}
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


def _spend_over_time_chart(spend_period_df: pd.DataFrame) -> go.Figure:
    """Spend for the selected date range."""
    x_order = spend_period_df["period_label"].tolist()
    current_y = spend_period_df["spend"].tolist()

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x_order,
            y=current_y,
            mode="lines+markers",
            name="Spend",
            line=dict(color=COLORS["accent"], width=2.5),
            marker=dict(size=7, color=COLORS["accent"]),
        )
    )

    fig.update_layout(
        height=320,
        title="Spend Over Time",
        margin=_chart_margin(has_legend=False),
        showlegend=False,
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
        margin=_chart_margin(has_legend=show_legend, bottom=bottom_margin),
        showlegend=show_legend,
        legend=_chart_legend_top(x=0.5, xanchor="center") if show_legend else {},
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
    """Org-wide monthly Leads, Discovery Calls, Terminations, and Signups."""
    if funnel_df.empty:
        return None

    plot_df = funnel_df.sort_values("month").copy()
    plot_df["month"] = pd.to_datetime(plot_df["month"])
    plot_df["period_label"] = plot_df["month"].dt.strftime("%b %Y")
    x_order = plot_df["period_label"].tolist()

    series = (
        ("leads", "Leads", COLORS["funnel_leads"]),
        ("dcs", "Discovery Calls", COLORS["funnel_dcs"]),
        ("terminations", "Terminations", COLORS["funnel_terminations"]),
        ("signups", "Signups", COLORS["funnel_signups"]),
    )
    for col, _, _ in series:
        if col not in plot_df.columns:
            plot_df[col] = 0.0

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
        title="Leads, Discovery Calls, Terminations & Signups Over Time",
        margin=_chart_margin(
            has_legend=True,
            left=48,
            right=24,
            bottom=80 if n_months > 12 else 56,
        ),
        hovermode="x unified",
        showlegend=True,
        legend=_chart_legend_top(),
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


def _year_color_map(year_labels: list[str]) -> dict[str, str]:
    colors: dict[str, str] = {}
    for label in year_labels:
        base = str(label).replace(" YTD", "").replace(" QTD", "")
        colors[label] = YEAR_BAR_COLORS.get(base, COLORS["muted"])
    return colors


def _signups_yoy_bar_chart(
    yoy_df: pd.DataFrame,
    *,
    levels: tuple[str, ...],
    chart_height: int | None = None,
    title_text: str = "Signups by tier — year over year",
    value_label: str = "Signups",
    hide_x_ticklabels: bool = False,
    title_legend_gap_px: int | None = None,
) -> go.Figure | None:
    if yoy_df.empty:
        return None

    year_order = list(dict.fromkeys(yoy_df["year_label"].tolist()))
    level_order = [level for level in levels if level in set(yoy_df["membership_level"])]
    if not level_order:
        return None

    fig = px.bar(
        yoy_df,
        x="membership_level",
        y="signups",
        color="year_label",
        barmode="group",
        labels={
            "membership_level": "Membership level",
            "signups": value_label,
            "year_label": "Year",
        },
        category_orders={
            "membership_level": level_order,
            "year_label": year_order,
        },
        color_discrete_map=_year_color_map(year_order),
    )
    tier_height = chart_height or 360
    _apply_signups_tier_pair_layout(
        fig,
        tier_height=tier_height,
        title_text=title_text,
        title_legend_gap_px=title_legend_gap_px,
    )
    fig.update_layout(
        xaxis_title="",
        yaxis_title=value_label,
        bargap=0.15,
    )
    fig.update_xaxes(
        showticklabels=not hide_x_ticklabels,
        title_text="",
    )
    fig.update_traces(hovertemplate="%{x}<br>%{fullData.name}: %{y:,0f}<extra></extra>")
    return fig


def _yoy_chart_title(yoy_df: pd.DataFrame) -> str:
    if yoy_df.empty:
        return "Signups by tier — year over year"
    years = sorted({int(y) for y in yoy_df["year"].unique()})
    if len(years) == 1:
        return f"Signups by tier — {years[0]}"
    if len(years) <= 4:
        return f"Signups by tier — {' · '.join(str(y) for y in years)}"
    return "Signups by tier — year over year"


def _qoq_chart_title(qoq_df: pd.DataFrame) -> str:
    if qoq_df.empty:
        return "Signups by tier — same quarter across years"
    quarters = sorted({int(q) for q in qoq_df["quarter"].unique()})
    labels = [f"Q{q}" for q in quarters]
    if len(quarters) == 1:
        return f"Signups by tier — {labels[0]} across years"
    if len(quarters) <= 4:
        return f"Signups by tier — {' · '.join(labels)} across years"
    return "Signups by tier — same quarter across years"


def _discovery_calls_yoy_chart_title(yoy_df: pd.DataFrame) -> str:
    return _yoy_chart_title(yoy_df).replace("Signups by tier", DISCOVERY_CALLS_LABEL)


def _discovery_calls_qoq_chart_title(qoq_df: pd.DataFrame) -> str:
    return _qoq_chart_title(qoq_df).replace("Signups by tier", DISCOVERY_CALLS_LABEL)


def _qoq_plot_years(qoq_df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Normalize QoQ year grouping so partial quarters share a bar group with full years."""
    plot_df = qoq_df.copy()
    plot_df["year_color"] = plot_df["year"].astype(int).astype(str)
    year_order = [str(y) for y in sorted({int(y) for y in plot_df["year"].unique()})]
    return plot_df, year_order


def _signups_qoq_bar_chart(
    qoq_df: pd.DataFrame,
    *,
    levels: tuple[str, ...],
    chart_height: int | None = None,
    title_text: str | None = None,
    value_label: str = "Signups",
    hide_x_ticklabels: bool = False,
    title_legend_gap_px: int | None = None,
) -> go.Figure | None:
    if qoq_df.empty:
        return None

    level_order = [level for level in levels if level in set(qoq_df["membership_level"])]
    if not level_order:
        return None

    quarter_order = sorted({int(q) for q in qoq_df["quarter"].unique()})
    plot_df, year_order = _qoq_plot_years(qoq_df)
    chart_title = title_text or _qoq_chart_title(plot_df)

    if len(quarter_order) == 1:
        fig = px.bar(
            plot_df,
            x="membership_level",
            y="signups",
            color="year_color",
            barmode="group",
            custom_data=["year_label"],
            labels={
                "membership_level": "Membership level",
                "signups": value_label,
                "year_color": "Year",
            },
            category_orders={
                "membership_level": level_order,
                "year_color": year_order,
            },
            color_discrete_map=_year_color_map(year_order),
        )
        tier_height = chart_height or 360
        _apply_signups_tier_pair_layout(
            fig,
            tier_height=tier_height,
            title_text=chart_title,
            title_legend_gap_px=title_legend_gap_px,
        )
        fig.update_layout(
            xaxis_title="",
            yaxis_title=value_label,
            bargap=0.15,
        )
        fig.update_xaxes(
            showticklabels=not hide_x_ticklabels,
            title_text="",
        )
        fig.update_traces(
            hovertemplate="%{x}<br>%{customdata[0]}: %{y:,0f}<extra></extra>"
        )
        return fig

    quarter_labels = [f"Q{q}" for q in quarter_order]
    plot_df["quarter_label"] = pd.Categorical(
        plot_df["quarter_label"],
        categories=quarter_labels,
        ordered=True,
    )

    fig = px.bar(
        plot_df,
        x="membership_level",
        y="signups",
        color="year_color",
        facet_col="quarter_label",
        barmode="group",
        custom_data=["year_label"],
        labels={
            "membership_level": "Membership level",
            "signups": value_label,
            "year_color": "Year",
            "quarter_label": "Quarter",
        },
        category_orders={
            "membership_level": level_order,
            "year_color": year_order,
            "quarter_label": quarter_labels,
        },
        color_discrete_map=_year_color_map(year_order),
    )
    tier_height = chart_height or _signups_tier_chart_height(len(quarter_labels))
    fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1].strip()))
    _apply_signups_tier_pair_layout(
        fig,
        tier_height=tier_height,
        title_text=chart_title,
        quarter_labels=quarter_labels,
        title_legend_gap_px=title_legend_gap_px,
    )
    fig.update_layout(bargap=0.12)
    for col_idx in range(1, len(quarter_labels) + 1):
        fig.update_yaxes(title_text=value_label if col_idx == 1 else "", col=col_idx)
    fig.update_xaxes(
        showticklabels=not hide_x_ticklabels,
        title_text="",
    )
    fig.update_traces(
        hovertemplate="%{x}<br>%{customdata[0]}: %{y:,0f}<extra></extra>"
    )
    return fig


@st.cache_data(ttl=86400, show_spinner=False)
def _tier_signups_by_level_monthly(
    _revision: str = SIGNUP_COMPARISON_REVISION,
    _data_until: str = "",
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    df, notes = load_tier_signups_by_level_monthly()
    return df, tuple(notes)


@st.cache_data(ttl=86400, show_spinner=False)
def _bookings_meetings_monthly(
    _revision: str = BOOKINGS_MEETINGS_COMPARISON_REVISION,
    _data_until: str = "",
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    df, notes = _bookings_meetings_mod.load_bookings_meetings_comparison_monthly()
    return df, tuple(notes)


def _reload_comparison_modules() -> None:
    """Force-reload helper modules after code changes (Streamlit keeps stale imports)."""
    global _bookings_meetings_mod, load_bookings_meetings_comparison_monthly
    global monthly_for_signup_charts, bookings_meetings_until
    global BOOKINGS_MEETINGS_COMPARISON_REVISION, BOOKINGS_MEETINGS_CATEGORY
    global DISCOVERY_CALLS_LABEL
    global _funnel_mod, FUNNEL_OVER_TIME_REVISION

    import total_new_members_yoy_chart as _tracker_chart_mod

    importlib.reload(_tracker_chart_mod)
    importlib.reload(_funnel_mod)
    FUNNEL_OVER_TIME_REVISION = _funnel_mod.FUNNEL_OVER_TIME_REVISION
    importlib.reload(_bookings_meetings_mod)
    BOOKINGS_MEETINGS_COMPARISON_REVISION = (
        _bookings_meetings_mod.BOOKINGS_MEETINGS_COMPARISON_REVISION
    )
    BOOKINGS_MEETINGS_CATEGORY = _bookings_meetings_mod.BOOKINGS_MEETINGS_CATEGORY
    DISCOVERY_CALLS_LABEL = _bookings_meetings_mod.DISCOVERY_CALLS_LABEL
    load_bookings_meetings_comparison_monthly = (
        _bookings_meetings_mod.load_bookings_meetings_comparison_monthly
    )
    monthly_for_signup_charts = _bookings_meetings_mod.monthly_for_signup_charts
    bookings_meetings_until = _bookings_meetings_mod.bookings_meetings_until
    _bookings_meetings_monthly.clear()
    _tier_signups_by_level_monthly.clear()


def _ensure_funnel_terminations(
    funnel_df: pd.DataFrame, since: date, until: date
) -> pd.DataFrame:
    """Backfill terminations when Streamlit serves a cached funnel without that column."""
    if funnel_df.empty or "terminations" in funnel_df.columns:
        return funnel_df
    term_by_month, _ = _funnel_mod.load_consolidated_terminations_monthly(
        since.isoformat(),
        until.isoformat(),
    )
    out = funnel_df.copy()
    out["terminations"] = out["month"].apply(
        lambda m: float(
            term_by_month.get(pd.Timestamp(m).to_period("M").to_timestamp(), 0)
        )
    )
    return out


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
        margin=_chart_margin(has_legend=False),
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
        margin=_chart_margin(has_legend=True, bottom=100),
        legend=_chart_legend_top(x=0.5, xanchor="center"),
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
        margin=_chart_margin(has_legend=False, extra_top=8),
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
    cpl_monthly: pd.DataFrame | None = None,
) -> dict[str, float | None]:
    """Scorecard totals with properly weighted averages for rate metrics."""
    if df.empty:
        return {k: None for k in scorecard_metrics(df).keys()}

    spend = df["spend"].sum()
    clicks = df["clicks"].sum()
    if (
        cpl_monthly is not None
        and not cpl_monthly.empty
        and "leads" in cpl_monthly.columns
    ):
        leads = float(cpl_monthly["leads"].sum())
    else:
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
def _load_dashboard(
    campaign_since: str,
    until: str,
    funnel_since: str,
    funnel_until: str,
    _revision: str = DASHBOARD_BUNDLE_REVISION,
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
    frozenset[pd.Timestamp],
    pd.DataFrame,
    pd.DataFrame,
    dict[str, dict[pd.Timestamp, float]],
    dict[pd.Timestamp, float],
    dict[pd.Timestamp, float],
    dict[pd.Timestamp, float],
    dict[pd.Timestamp, float],
    pd.DataFrame,
    pd.DataFrame,
    tuple[str, ...],
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
        _ghl_leads_org_by_month,
        ghl_signups_by_level_df,
        funnel_df,
        funnel_notes,
    ) = load_dashboard_bundle(
        campaign_since,
        until,
        funnel_since=funnel_since,
        funnel_until=funnel_until,
    )
    if not funnel_df.empty:
        funnel_df = funnel_df.copy()
        funnel_df["month"] = pd.to_datetime(funnel_df["month"])
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
        ghl_signups_by_level_df,
        funnel_df,
        tuple(funnel_notes),
    )


def main() -> None:
    st.set_page_config(
        page_title="Digital Channel Dashboard (Live)",
        layout="wide",
        page_icon="📊",
    )
    _inject_styles()

    _render_dashboard_title()

    today = date.today()
    default_until = today - timedelta(days=1)
    default_since = default_dashboard_since(until=default_until)

    _render_heading_with_help(
        "Date range",
        _date_range_help_html(),
        style="label",
    )
    date_start_col, date_end_col = st.columns(2)
    with date_start_col:
        since = st.date_input(
            "Start date",
            value=default_since,
            max_value=default_until,
        )
    with date_end_col:
        until = st.date_input(
            "End date",
            value=default_until,
            max_value=default_until,
        )
    if since > until:
        st.error("Start date must be on or before end date.")
        st.stop()

    load_label = (
        "Loading Google Ads, Meta, and GoHighLevel… "
        "(first load may take a minute; cached ranges are much faster)"
    )
    with st.spinner(load_label):
        try:
            raw_df, notes, lead_summary, conv_by_level_df, unallocated_conv_df, wom_conv_df, tracker_conv_by_level_df, tracker_unallocated_conv_df, combined_conv_by_level_df, combined_unallocated_conv_df, sheet_months, channel_month_leads, cpl_channel_month_leads, unallocated_leads_by_attr, sheet_signup_totals, ghl_signups_by_month, sheet_dcs_totals, ghl_dcs_by_month, ghl_signups_by_level_df, funnel_df, funnel_notes = _load_dashboard(
                since.isoformat(),
                until.isoformat(),
                since.isoformat(),
                until.isoformat(),
            )
        except Exception as exc:
            st.error(f"Could not load live data.\n\n{exc}")
            st.stop()

    if raw_df.empty:
        st.warning("No campaign data returned for the selected date range.")
        st.stop()

    with st.sidebar:
        _render_heading_with_help("Filters", _filters_help_html(), style="sidebar")
        channels = sorted(raw_df["channel"].dropna().unique())
        selected_channels = st.multiselect("Channel", channels, default=channels)

        st.markdown("**Non-paid inclusion**")
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

        st.markdown("**GHL attribution**")
        attribution_labels = {key: label for key, label in GHL_ATTRIBUTION_OPTIONS}
        use_hear_about = st.checkbox(
            attribution_labels[GHL_ATTRIBUTION_HEAR_ABOUT],
            value=True,
            help=(
                "Self-reported **How did you hear about us?** only — Google or FB/IG "
                "responses mapped to each channel. Signups use strict channel counts "
                "(Other excluded unless toggled below). Drives CPL, Cost per DC, and "
                "CPA when tracker is off."
            ),
        )
        use_tracker = st.checkbox(
            attribution_labels[GHL_ATTRIBUTION_TRACKER],
            value=False,
            help=(
                "All contacts with a Google tag/pixel or Meta lead tag/pixel for the "
                "channel. When both attribution sources are checked, counts use "
                "deduped hear-about ∪ tracker (not double-counted)."
            ),
        )
        if not use_hear_about and not use_tracker:
            st.warning(
                "Select at least one attribution source for GHL leads and signups."
            )

        include_wom_signups = False
        if use_hear_about:
            include_wom_signups = st.checkbox(
                "Include Word of Mouth signups",
                value=False,
                help=(
                    "Hear-about only. When on, signups whose hear-about contains "
                    "\"word of mouth\" are spread by spend share (lowers CPA). "
                    "Off by default. Pre-Sep 2025 tracker sheet months are unchanged."
                ),
            )

        include_other_signups = False
        if use_hear_about or use_tracker:
            include_other_signups = st.checkbox(
                "Include Other signups",
                value=False,
                help=(
                    "Signups only. When on, committed signups without Google/Meta "
                    "attribution (blank hear-about, Other, TikTok, etc., or "
                    "tracker-unallocated) are spread by spend share — lowers channel "
                    "CPA. Off by default so Google and FB/IG CPA reflect only strict "
                    "channel attribution."
                ),
            )

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

        st.markdown("---")
        _render_heading_with_help("Refresh", _refresh_help_html(), style="sidebar")
        if st.button(
            "Design only",
            help="Reload layout and styles only. Uses cached data — no Google Ads, Meta, or GHL calls.",
        ):
            _reload_comparison_modules()
            st.rerun()

        if st.button("Refresh data", help="Reload ads + GHL. Keeps cached GHL daily lead files."):
            clear_dashboard_disk_cache()
            _load_dashboard.clear()
            _tier_signups_by_level_monthly.clear()
            _bookings_meetings_monthly.clear()
            st.rerun()

        if st.button(
            "Hard refresh GHL leads",
            help="Clear cached GHL daily lead files and reload (use if lead counts look wrong).",
        ):
            clear_ghl_leads_day_cache()
            clear_dashboard_disk_cache()
            _load_dashboard.clear()
            _tier_signups_by_level_monthly.clear()
            _bookings_meetings_monthly.clear()
            st.rerun()

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
        include_other_signups=include_other_signups,
        sheet_signup_months=set(sheet_months),
    )

    if "month" not in raw_df.columns:
        raw_df = raw_df.copy()
        raw_df["month"] = raw_df["date"].dt.to_period("M").dt.to_timestamp()

    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()

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

    trend_monthlies = build_trend_chart_monthlies(
        df,
        channel_month_leads,
        cpl_channel_month_leads,
        unallocated_leads_by_attr,
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
        cpl_monthly=trend_monthlies.cpl,
    )

    funnel_df = _ensure_funnel_terminations(funnel_df, since, until)

    scorecard_lead_notes: list[str] = []
    if include_organic_leads:
        scorecard_lead_notes.append(
            "Leads and CPL include <strong>Organic</strong> (non–paid-attributed) contacts."
        )
    if until_month <= pd.Timestamp(SHEET_LEADS_UNTIL).to_period("M").to_timestamp():
        scorecard_lead_notes.append(
            "Selected range uses <strong>sheet lead totals</strong> (through Jun 2025) — "
            "attribution toggles do not change lead counts."
        )
    elif since_month <= pd.Timestamp(SHEET_LEADS_UNTIL).to_period("M").to_timestamp():
        scorecard_lead_notes.append(
            "Lead counts through <strong>Jun 2025</strong> come from the sheet (same total for "
            "all attribution modes); <strong>Jul 2025+</strong> follows the GHL attribution "
            "checkboxes."
        )

    strict_signup_note: str | None = None
    if (use_hear_about or use_tracker) and not include_other_signups:
        strict_signup_note = (
            "Signups use <strong>strict</strong> Google / FB/IG attribution (Other excluded). "
        )
        if use_hear_about and include_wom_signups:
            strict_signup_note += (
                "Word of mouth signups are still spread when that toggle is on. "
            )
        if (
            use_hear_about
            and since_month
            <= pd.Timestamp(SHEETS_SIGNUPS_UNTIL).to_period("M").to_timestamp()
        ):
            strict_signup_note += (
                f"Pre-{GHL_ATTRIBUTED_SIGNUPS_SINCE} hear-about signups use the "
                "<strong>Data tab</strong>; Jul–Aug 2025 use GHL hear-about; Sep 2025+ uses GHL. "
            )
        elif (
            use_tracker
            and not use_hear_about
            and since_month
            <= pd.Timestamp(SHEETS_SIGNUPS_UNTIL).to_period("M").to_timestamp()
        ):
            strict_signup_note += (
                "Pre-Sep 2025 tracker-only signups still use org-wide tracker totals split by spend."
            )

    active_attribution_note: str | None = None
    if use_hear_about or use_tracker:

        def _attrib_snapshot(
            use_hear: bool,
            use_track: bool,
            *,
            strict_signups: bool = False,
        ) -> tuple[str, str, str]:
            snap_kwargs = dict(attr_kwargs)
            if strict_signups:
                snap_kwargs["include_wom_signups"] = False
                snap_kwargs["include_other_signups"] = False
            snap = apply_dashboard_ghl_attribution(
                raw_selected.copy(),
                use_hear_about=use_hear,
                use_tracker=use_track,
                **snap_kwargs,
            )
            snap = snap.loc[mask]
            spend = float(snap["spend"].sum())
            leads = float(snap["leads"].sum())
            dcs = float(snap["dcs"].sum())
            signups = float(snap["conversions"].sum())
            cpl = _fmt_currency(spend / leads if leads else None)
            cpdc = _fmt_currency(spend / dcs if dcs else None)
            cpa = _fmt_currency(spend / signups if signups else None)
            return cpl, cpdc, cpa

        active_label = "Tracker" if use_tracker else "Hear-about"
        hear_cpl, hear_cpdc, hear_cpa = _attrib_snapshot(
            True, False, strict_signups=True
        )
        track_cpl, track_cpdc, track_cpa = _attrib_snapshot(
            False, True, strict_signups=True
        )
        active_cpl, active_cpdc, active_cpa = _attrib_snapshot(
            use_hear_about, use_tracker
        )
        override_note = (
            " (tracker overrides hear-about when both are checked)"
            if use_hear_about and use_tracker
            else ""
        )
        active_attribution_note = (
            f"Active attribution: <strong>{active_label}</strong>{override_note} — "
            f"CPL {active_cpl} · Cost per DC {active_cpdc} · CPA {active_cpa}. "
            f"Hear-about only: CPL {hear_cpl} · Cost per DC {hear_cpdc} · CPA {hear_cpa} · "
            f"Tracker only: CPL {track_cpl} · Cost per DC {track_cpdc} · CPA {track_cpa}."
        )

    show_july_cpl_note = False
    if not cpl_period_df.empty:
        july = pd.Timestamp("2025-07-01")
        show_july_cpl_note = july in set(
            pd.to_datetime(cpl_period_df["month"]).dt.to_period("M").dt.to_timestamp()
        ) or cpl_period_df["period_label"].astype(str).str.contains(
            "Q3 2025", na=False
        ).any()

    st.markdown("---")
    _render_heading_with_help(
        "Trends over time",
        _trends_over_time_help_html(
            use_quarterly=use_quarterly,
            scorecard_lead_notes=scorecard_lead_notes,
            strict_signup_note=strict_signup_note,
            active_attribution_note=active_attribution_note,
            show_july_cpl_note=show_july_cpl_note,
            include_organic_leads=include_organic_leads,
            loader_notes=notes,
            funnel_notes=list(funnel_notes),
        ),
        style="section",
    )

    funnel_chart = _funnel_over_time_chart(funnel_df)
    if funnel_chart:
        st.plotly_chart(funnel_chart, use_container_width=True)
    else:
        st.info("No funnel data for the selected date range.")

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

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.plotly_chart(
            _spend_over_time_chart(spend_period_df),
            use_container_width=True,
        )
    with c2:
        cpl_chart = _cpl_over_time_chart(cpl_period_df)
        if cpl_chart:
            st.plotly_chart(cpl_chart, use_container_width=True)
        else:
            st.info("CPL over time unavailable (no leads in the selected range).")
    with c3:
        st.plotly_chart(
            _line_chart(dcs_period_df, ["dcs"], "DCs Over Time", "DCs"),
            use_container_width=True,
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

    st.markdown("---")
    signup_levels = tuple(selected_membership_levels)
    tier_until = tier_signup_until()
    signups_by_level_df, signup_cmp_notes = _tier_signups_by_level_monthly(
        _data_until=tier_until.isoformat(),
    )
    _render_heading_with_help(
        "Signups by membership level",
        _signups_by_level_help_html(
            tier_until=tier_until,
            signup_cmp_notes=list(signup_cmp_notes),
        ),
        style="section",
    )
    yoy_col, qoq_col = st.columns(2)
    yoy_chart_height = _signups_tier_chart_height(1)
    with yoy_col:
        yoy_year_options = list(tier_year_filter_options(signups_by_level_df))
        selected_yoy_years = st.multiselect(
            "Years",
            yoy_year_options,
            default=yoy_year_options,
            help=(
                "Select one or more calendar years to compare. "
                "Current year shows as YTD when still open."
            ),
        )
        if not selected_yoy_years:
            st.info("Select at least one year to show the chart.")
        else:
            yoy_plot_df = aggregate_signups_yoy(
                signups_by_level_df,
                until=tier_until,
                levels=signup_levels,
                selected_years=tuple(int(y) for y in selected_yoy_years),
            )
            yoy_chart = _signups_yoy_bar_chart(
                yoy_plot_df,
                levels=signup_levels,
                chart_height=yoy_chart_height,
                title_text=_yoy_chart_title(yoy_plot_df),
            )
            if yoy_chart:
                st.plotly_chart(yoy_chart, use_container_width=True)
            else:
                st.info("No signup tier data for YoY comparison.")
    with qoq_col:
        qoq_quarter_options = list(tier_quarter_filter_options())
        selected_qoq_quarters = st.multiselect(
            "Quarters",
            qoq_quarter_options,
            default=qoq_quarter_options,
            help=(
                "Select one or more quarters to compare across years. "
                "Current quarter shows as QTD when still open."
            ),
        )
        if not selected_qoq_quarters:
            st.info("Select at least one quarter to show the chart.")
        else:
            qoq_plot_df = aggregate_signups_qoq(
                signups_by_level_df,
                until=tier_until,
                levels=signup_levels,
                selected_quarters=qoq_quarter_numbers(selected_qoq_quarters),
            )
            qoq_quarter_count = (
                len(set(qoq_plot_df["quarter"].unique())) if not qoq_plot_df.empty else 1
            )
            qoq_chart_height = (
                _signups_tier_chart_height(1)
                if qoq_quarter_count == 1
                else _signups_tier_chart_height(qoq_quarter_count)
            )
            qoq_chart = _signups_qoq_bar_chart(
                qoq_plot_df,
                levels=signup_levels,
                chart_height=qoq_chart_height,
                title_text=_qoq_chart_title(qoq_plot_df),
            )
            if qoq_chart:
                st.plotly_chart(qoq_chart, use_container_width=True)
            else:
                st.info("No signup tier data for quarter comparison.")

    st.markdown("---")
    bm_until = bookings_meetings_until()
    bookings_meetings_df, bm_notes = _bookings_meetings_monthly(
        _data_until=bm_until.isoformat(),
    )
    _render_heading_with_help(
        DISCOVERY_CALLS_LABEL,
        _discovery_calls_help_html(
            bm_until=bm_until,
            bm_notes=list(bm_notes),
        ),
        style="section",
    )
    bm_chart_df = monthly_for_signup_charts(bookings_meetings_df)
    bm_levels = (BOOKINGS_MEETINGS_CATEGORY,)
    bm_chart_opts = dict(
        hide_x_ticklabels=True,
        title_legend_gap_px=_DISCOVERY_CALLS_TITLE_LEGEND_GAP_PX,
    )
    bm_yoy_col, bm_qoq_col = st.columns(2)
    bm_chart_height = _signups_tier_chart_height(1)
    with bm_yoy_col:
        bm_yoy_year_options = list(tier_year_filter_options(bookings_meetings_df))
        selected_bm_yoy_years = st.multiselect(
            "Years (discovery calls)",
            bm_yoy_year_options,
            default=bm_yoy_year_options,
            help=(
                "Select one or more calendar years to compare. "
                "Current year shows as YTD when still open."
            ),
        )
        if not selected_bm_yoy_years:
            st.info("Select at least one year to show the chart.")
        else:
            bm_yoy_plot_df = aggregate_signups_yoy(
                bm_chart_df,
                until=bm_until,
                levels=bm_levels,
                selected_years=tuple(int(y) for y in selected_bm_yoy_years),
            )
            bm_yoy_chart = _signups_yoy_bar_chart(
                bm_yoy_plot_df,
                levels=bm_levels,
                chart_height=bm_chart_height,
                title_text=_discovery_calls_yoy_chart_title(bm_yoy_plot_df),
                value_label="Count",
                **bm_chart_opts,
            )
            if bm_yoy_chart:
                st.plotly_chart(bm_yoy_chart, use_container_width=True)
            else:
                st.info("No discovery call data for YoY comparison.")
    with bm_qoq_col:
        bm_qoq_quarter_options = list(tier_quarter_filter_options())
        selected_bm_qoq_quarters = st.multiselect(
            "Quarters (discovery calls)",
            bm_qoq_quarter_options,
            default=bm_qoq_quarter_options,
            help=(
                "Select one or more quarters to compare across years. "
                "Current quarter shows as QTD when still open."
            ),
        )
        if not selected_bm_qoq_quarters:
            st.info("Select at least one quarter to show the chart.")
        else:
            bm_qoq_plot_df = aggregate_signups_qoq(
                bm_chart_df,
                until=bm_until,
                levels=bm_levels,
                selected_quarters=qoq_quarter_numbers(selected_bm_qoq_quarters),
            )
            bm_qoq_quarter_count = (
                len(set(bm_qoq_plot_df["quarter"].unique()))
                if not bm_qoq_plot_df.empty
                else 1
            )
            bm_qoq_chart_height = (
                _signups_tier_chart_height(1)
                if bm_qoq_quarter_count == 1
                else _signups_tier_chart_height(bm_qoq_quarter_count)
            )
            bm_qoq_chart = _signups_qoq_bar_chart(
                bm_qoq_plot_df,
                levels=bm_levels,
                chart_height=bm_qoq_chart_height,
                title_text=_discovery_calls_qoq_chart_title(bm_qoq_plot_df),
                value_label="Count",
                **bm_chart_opts,
            )
            if bm_qoq_chart:
                st.plotly_chart(bm_qoq_chart, use_container_width=True)
            else:
                st.info("No discovery call data for quarter comparison.")

    st.markdown("---")
    _render_heading_with_help(
        "Campaign breakdown",
        _campaign_breakdown_help_html(),
        style="section",
    )

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
