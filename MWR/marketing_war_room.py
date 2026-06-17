"""
Marketing Pulse — single-screen operational pulse.

Run (from this folder):
  streamlit run marketing_war_room.py

Or from the repo root:
  streamlit run MWR/marketing_war_room.py

Build in stages (suggested order):
  1. Layout shell, styling, refresh controls
  2. Command strip — top-line KPIs across all channels
  3. Paid media — Google Ads + Meta (spend, leads, CPL)
  4. CRM & funnel — GHL signups, bookings, conversions
  5. Website & traffic — GA4 sessions, channels, embed pages
  6. Organic social — Instagram / Meta engagement
  7. Content & SEO — blog traffic, organic search trends
  8. Team & projects — Monday.com workflow status (last 7 days)
  9. Discovery Call & Conversion Drivers — GHL hear-about bar charts
  10. Needs response — marketing-only Gmail + Google Chat queue
  11. Alerts — thresholds, anomalies, stale-data warnings

Needs response scope (marketing-only, not a full inbox):
  - Gmail: unread in a dedicated label/filter (e.g. Marketing/Action)
  - Google Chat: unread @mentions or starred items in named marketing spaces
  - Surface counts + oldest-waiting age + top few items; no general mail/DMs
"""

from __future__ import annotations

import html
import pickle
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, TypeVar

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

# Bump WAR_ROOM_LOADER_VERSION when war_room_data loaders change (cache bust).
from war_room_data import (
    AlertsMetrics,
    CommandStripMetrics,
    ContentSeoMetrics,
    ConversionDriversMetrics,
    CrmFunnelMetrics,
    HearAboutCountRow,
    NeedsResponseMetrics,
    OrganicSocialMetrics,
    PaidMediaMetrics,
    BoardTaskSummary,
    TeamOpsMetrics,
    format_team_status_label,
    status_count_for,
    TEAM_OPS_SUMMARY_BUCKETS,
    TrendSeries,
    WebsiteTrafficMetrics,
    load_alerts,
    load_command_strip,
    load_content_seo,
    load_conversion_drivers,
    load_crm_funnel,
    load_needs_response,
    load_organic_social,
    load_paid_media,
    load_team_ops,
    load_website_traffic,
)
from ghl_client import HEAR_ABOUT_US_FIELD_NAME  # noqa: E402 — after war_room_data reloads ghl_client

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Bump when loader logic changes — invalidates @st.cache_data without a server restart.
WAR_ROOM_LOADER_VERSION = "2026-06-17-organic-not-set-label-v24"

SPARKLINE_HEIGHT_PX = 44

COLORS = {
    "accent": "#5DA68A",
    "accent_dark": "#264540",
    "muted": "#6B7C93",
    "warning": "#F58518",
    "danger": "#E45756",
    "panel_bg": "#FFFFFF",
    "page_bg": "#F4F8FB",
}

TRAFFIC_PIE_COLORS = (
    "#5DA68A",
    "#264540",
    "#6B7C93",
    "#4A90A4",
    "#F58518",
    "#E45756",
)

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------


def _inject_styles() -> None:
    panel_keys = (
        "war-room-command-strip",
        "war-room-conversion-drivers",
        "war-room-paid-media",
        "war-room-crm-funnel",
        "war-room-website-traffic",
        "war-room-organic-social",
        "war-room-content-seo",
        "war-room-team-ops",
        "war-room-needs-response",
        "war-room-alerts",
    )
    panel_rules = "\n".join(
        f"""
        div.st-key-{key}[data-testid="stVerticalBlock"] {{
            background: {COLORS["panel_bg"]} !important;
            border: 1px solid rgba(93, 166, 138, 0.35) !important;
            border-radius: 12px !important;
            box-shadow: 0 1px 3px rgba(38, 69, 64, 0.08) !important;
            padding: 0.85rem 1rem !important;
            margin-bottom: 0.25rem;
            overflow: visible !important;
        }}"""
        for key in panel_keys
    )
    st.markdown(
        f"""
        <style>
        .stApp {{ background: {COLORS["page_bg"]}; }}
        .block-container {{
            padding-top: 1rem;
            padding-bottom: 0.5rem;
            max-width: 1680px;
        }}
        /* Two-column layout: main analytics (2/3) + ops sidebar (1/3), each scrolls independently */
        div.st-key-war-room-scroll-main[data-testid="stVerticalBlock"],
        div.st-key-war-room-scroll-side[data-testid="stVerticalBlock"] {{
            max-height: calc(100vh - 5.5rem);
            overflow-y: auto;
            overflow-x: hidden;
            padding-right: 0.35rem;
            scrollbar-gutter: stable;
        }}
        div.st-key-war-room-scroll-main[data-testid="stVerticalBlock"] {{
            padding-left: 0.1rem;
        }}
        div.st-key-war-room-scroll-main[data-testid="stVerticalBlock"]::-webkit-scrollbar,
        div.st-key-war-room-scroll-side[data-testid="stVerticalBlock"]::-webkit-scrollbar {{
            width: 7px;
        }}
        div.st-key-war-room-scroll-main[data-testid="stVerticalBlock"]::-webkit-scrollbar-thumb,
        div.st-key-war-room-scroll-side[data-testid="stVerticalBlock"]::-webkit-scrollbar-thumb {{
            background: rgba(93, 166, 138, 0.35);
            border-radius: 4px;
        }}
        div.st-key-war-room-scroll-main[data-testid="stVerticalBlock"]::-webkit-scrollbar-track,
        div.st-key-war-room-scroll-side[data-testid="stVerticalBlock"]::-webkit-scrollbar-track {{
            background: rgba(107, 124, 147, 0.08);
            border-radius: 4px;
        }}
        [data-testid="stMetric"] {{
            background: white;
            border-radius: 10px;
            padding: 0.45rem 0.6rem;
            box-shadow: 0 1px 2px rgba(38,69,64,0.06);
            border: 1px solid rgba(93,166,138,0.12);
            min-width: 0;
            overflow: hidden;
        }}
        [data-testid="stMetricLabel"] {{
            color: {COLORS["accent_dark"]} !important;
            font-weight: 600;
            font-size: 0.72rem;
            line-height: 1.25;
        }}
        [data-testid="stMetricValue"],
        [data-testid="stMetricValue"] > div {{
            color: {COLORS["accent"]} !important;
            font-size: 1rem !important;
            line-height: 1.2 !important;
            font-weight: 600;
            overflow-wrap: anywhere;
            word-break: break-word;
        }}
        [data-testid="stMetricDelta"],
        [data-testid="stMetricDelta"] > div {{
            font-size: 0.68rem !important;
            line-height: 1.2 !important;
        }}
        [data-testid="stCaptionContainer"] p,
        [data-testid="stCaptionContainer"] {{
            color: {COLORS["muted"]} !important;
        }}
        .war-room-header {{
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 1rem;
            margin-bottom: 0.75rem;
        }}
        .war-room-header h1 {{
            color: {COLORS["accent_dark"]};
            margin: 0;
            font-size: 1.75rem;
        }}
        .war-room-status {{
            color: {COLORS["muted"]};
            font-size: 0.85rem;
            text-align: right;
        }}
        .war-room-panel-header {{
            margin: 0.75rem 0 0.12rem 0;
            padding: 0;
        }}
        .war-room-panel-heading {{
            display: flex;
            flex-direction: column;
            gap: 0;
            margin: 0;
            padding: 0;
        }}
        .war-room-panel-title {{
            color: {COLORS["accent_dark"]};
            font-size: 1.15rem;
            font-weight: 700;
            line-height: 1.05;
            margin: 0;
            padding: 0;
        }}
        .war-room-panel-caption {{
            color: {COLORS["muted"]};
            font-size: calc(0.82rem + 2pt);
            line-height: 1.1;
            margin: 0;
            padding: 0;
        }}
        .war-room-panel-caption--source {{
            margin-top: 0.12rem;
        }}
        .war-room-panel-caption--dates {{
            margin-top: 0.14rem;
            font-size: calc(0.62rem + 2pt);
            line-height: 1.15;
            letter-spacing: 0.01em;
        }}
        /* Streamlit injects large margins on markdown headings/paragraphs — collapse them */
        [data-testid="stMarkdownContainer"]:has(.war-room-panel-header),
        [data-testid="stMarkdown"]:has(.war-room-panel-header),
        [data-testid="stElementContainer"]:has(.war-room-panel-header) {{
            margin: 0 !important;
            padding: 0 !important;
        }}
        [data-testid="stMarkdownContainer"]:has(.war-room-panel-header) [data-testid="stMarkdownContainer"],
        [data-testid="stMarkdownContainer"]:has(.war-room-panel-header) p,
        [data-testid="stMarkdownContainer"]:has(.war-room-panel-header) h1,
        [data-testid="stMarkdownContainer"]:has(.war-room-panel-header) h2,
        [data-testid="stMarkdownContainer"]:has(.war-room-panel-header) h3,
        [data-testid="stMarkdownContainer"]:has(.war-room-panel-header) h4,
        .war-room-panel-header .war-room-panel-title,
        .war-room-panel-header .war-room-panel-caption {{
            margin: 0 !important;
            padding: 0 !important;
        }}
        .war-room-panel-header .war-room-panel-caption--source {{
            margin-top: 0.12rem !important;
        }}
        .war-room-panel-header .war-room-panel-caption--dates {{
            margin-top: 0.14rem !important;
        }}
        .war-room-placeholder {{
            color: {COLORS["muted"]};
            font-size: 0.82rem;
            font-style: italic;
            margin: 0.25rem 0 0.5rem 0;
        }}
        .war-room-sparkline-placeholder {{
            background: rgba(107, 124, 147, 0.08);
            border: 1px dashed rgba(107, 124, 147, 0.35);
            border-radius: 6px;
            color: {COLORS["muted"]};
            font-size: 0.68rem;
            height: {SPARKLINE_HEIGHT_PX}px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-top: 0.2rem;
        }}
        div[data-testid="stPlotlyChart"] {{
            margin-top: 0.15rem;
            margin-bottom: 0;
        }}
        div[data-testid="stPlotlyChart"] iframe {{
            max-height: {SPARKLINE_HEIGHT_PX}px !important;
        }}
        /* Command-strip KPI: one bordered card holds metric + sparkline */
        div[class*="st-key-war-room-kpi-"][data-testid="stVerticalBlockBorderWrapper"] {{
            border: 1px solid rgba(93, 166, 138, 0.12) !important;
            border-radius: 10px !important;
            background: {COLORS["panel_bg"]};
            box-shadow: 0 1px 2px rgba(38, 69, 64, 0.06) !important;
            padding: 0.35rem 0.5rem 0.3rem 0.5rem !important;
            min-width: 0;
        }}
        div[class*="st-key-war-room-kpi-"] [data-testid="stHorizontalBlock"] {{
            gap: 0.35rem !important;
            align-items: center !important;
        }}
        div[class*="st-key-war-room-kpi-"] [data-testid="stMetric"] {{
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            padding: 0 !important;
            margin: 0 !important;
        }}
        div[class*="st-key-war-room-kpi-"] div[data-testid="stPlotlyChart"] {{
            margin: 0 !important;
            padding: 0 !important;
        }}
        div[class*="st-key-war-room-kpi-"] .war-room-sparkline-placeholder {{
            margin: 0;
            border: none;
            background: transparent;
        }}
        /* Conversion drivers: metric + source bar chart in one bordered card */
        div[class*="st-key-war-room-conversion-"][data-testid="stVerticalBlockBorderWrapper"] {{
            border: 1px solid rgba(93, 166, 138, 0.12) !important;
            border-radius: 10px !important;
            background: {COLORS["panel_bg"]} !important;
            box-shadow: 0 1px 2px rgba(38, 69, 64, 0.06) !important;
            padding: 0.35rem 0.5rem 0.45rem 0.5rem !important;
            min-width: 0;
            overflow: visible !important;
        }}
        .war-room-conversion-headline {{
            margin: 0 0 0.15rem 0;
            font-size: 0.95rem;
            line-height: 1.35;
            color: {COLORS["accent_dark"]};
        }}
        .war-room-conversion-label {{
            font-weight: 600;
        }}
        .war-room-conversion-value {{
            font-weight: 700;
            font-size: 1.05rem;
            color: {COLORS["accent"]};
        }}
        .war-room-conversion-delta {{
            display: inline-block;
            margin: 0 0 0.25rem 0;
            padding: 0.12rem 0.42rem;
            border-radius: 0.35rem;
            font-size: 0.68rem;
            font-weight: 600;
            line-height: 1.2;
        }}
        .war-room-conversion-delta--up {{
            background: rgba(93, 166, 138, 0.2);
            color: #2d6b52;
        }}
        .war-room-conversion-delta--down {{
            background: rgba(228, 87, 86, 0.18);
            color: {COLORS["danger"]};
        }}
        .war-room-conversion-delta--flat {{
            background: rgba(107, 124, 147, 0.14);
            color: {COLORS["muted"]};
        }}
        div[class*="st-key-war-room-conversion-"] div[data-testid="stPlotlyChart"] {{
            margin: 0 !important;
            padding: 0 !important;
        }}
        div[class*="st-key-war-room-conversion-"] div[data-testid="stPlotlyChart"] {{
            min-height: 140px;
        }}
        div[class*="st-key-war-room-conversion-"] div[data-testid="stPlotlyChart"] iframe {{
            max-height: none !important;
            min-height: 140px !important;
        }}
        div.st-key-war-room-team-ops[data-testid="stVerticalBlock"] {{
            padding: 0.65rem 0.75rem !important;
        }}
        .war-room-board-list {{
            display: flex;
            flex-direction: column;
        }}
        .war-room-board-block {{
            padding-bottom: 0.45rem;
            border-bottom: 1px solid rgba(93, 166, 138, 0.14);
            overflow: visible;
        }}
        .war-room-board-list .war-room-board-block:not(:first-child) {{
            margin-top: 10px;
        }}
        .war-room-board-block:last-child {{
            padding-bottom: 0;
            border-bottom: none;
        }}
        .war-room-board-name {{
            color: {COLORS["accent_dark"]};
            font-size: 0.8rem;
            font-weight: 700;
            line-height: 1.2;
            margin: 0 0 0.3rem 0;
        }}
        .war-room-board-empty {{
            color: {COLORS["muted"]};
            font-size: 0.72rem;
            font-style: italic;
            margin: 0;
        }}
        .war-room-board-statuses {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.28rem;
            overflow: visible;
        }}
        .war-room-board-status-rows .war-room-board-statuses + .war-room-board-statuses {{
            margin-top: 0.28rem;
        }}
        .war-room-board-status {{
            flex: 1 1 calc(50% - 0.28rem);
            min-width: 0;
            box-sizing: border-box;
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            gap: 0.2rem;
            background: {COLORS["panel_bg"]};
            border: 1px solid rgba(93, 166, 138, 0.12);
            border-radius: 6px;
            padding: 0.22rem 0.38rem;
            box-shadow: 0 1px 2px rgba(38, 69, 64, 0.04);
            position: relative;
        }}
        .war-room-board-status--has-tip {{
            cursor: help;
        }}
        .war-room-board-status--has-tip:hover {{
            border-color: rgba(93, 166, 138, 0.45);
            box-shadow: 0 2px 6px rgba(38, 69, 64, 0.1);
        }}
        .war-room-board-status-tip {{
            display: none;
            position: absolute;
            top: calc(100% + 4px);
            left: 0;
            z-index: 20;
            min-width: 11rem;
            max-width: 16rem;
            max-height: 9.5rem;
            overflow-y: auto;
            padding: 0.38rem 0.45rem;
            background: {COLORS["panel_bg"]};
            border: 1px solid rgba(93, 166, 138, 0.35);
            border-radius: 8px;
            box-shadow: 0 6px 16px rgba(38, 69, 64, 0.16);
            pointer-events: none;
        }}
        .war-room-board-status--has-tip:hover .war-room-board-status-tip {{
            display: block;
        }}
        .war-room-board-status-tip ul {{
            margin: 0;
            padding-left: 1rem;
        }}
        .war-room-board-status-tip li {{
            color: {COLORS["accent_dark"]};
            font-size: 0.66rem;
            line-height: 1.35;
            margin: 0.12rem 0;
        }}
        .war-room-board-status-tip-more {{
            color: {COLORS["muted"]} !important;
            font-style: italic;
            list-style: none;
            margin-left: -1rem;
        }}
        .war-room-board-status-label {{
            color: {COLORS["accent_dark"]};
            font-size: calc(0.66rem + 1pt);
            font-weight: 600;
            line-height: 1.2;
            overflow-wrap: anywhere;
        }}
        .war-room-board-status-value {{
            color: {COLORS["accent"]};
            font-size: 0.78rem;
            font-weight: 700;
            line-height: 1.2;
            flex-shrink: 0;
        }}
        div.st-key-war-room-paid-media [data-testid="stHorizontalBlock"],
        div.st-key-war-room-crm-funnel [data-testid="stHorizontalBlock"],
        div.st-key-war-room-website-traffic [data-testid="stHorizontalBlock"] {{
            gap: 0.3rem !important;
        }}
        div.st-key-war-room-paid-media [data-testid="stMetric"],
        div.st-key-war-room-crm-funnel [data-testid="stMetric"],
        div.st-key-war-room-website-traffic [data-testid="stMetric"] {{
            padding: 0.32rem 0.38rem !important;
            overflow: visible !important;
        }}
        div.st-key-war-room-paid-media [data-testid="stMetricLabel"],
        div.st-key-war-room-crm-funnel [data-testid="stMetricLabel"],
        div.st-key-war-room-website-traffic [data-testid="stMetricLabel"] {{
            font-size: 0.6rem !important;
            line-height: 1.15 !important;
        }}
        div.st-key-war-room-paid-media [data-testid="stMetricValue"],
        div.st-key-war-room-paid-media [data-testid="stMetricValue"] > div,
        div.st-key-war-room-crm-funnel [data-testid="stMetricValue"],
        div.st-key-war-room-crm-funnel [data-testid="stMetricValue"] > div,
        div.st-key-war-room-website-traffic [data-testid="stMetricValue"],
        div.st-key-war-room-website-traffic [data-testid="stMetricValue"] > div {{
            font-size: 0.92rem !important;
            line-height: 1.2 !important;
            font-weight: 700 !important;
        }}
        .war-room-pie-legend {{
            display: grid;
            grid-template-columns: auto auto;
            gap: 0.2rem 0.85rem;
            width: fit-content;
            max-width: 88%;
            margin: 0 auto 0.35rem auto;
            padding-top: 0;
            padding-bottom: 0.75rem;
            transform: translateY(-5px);
        }}
        .war-room-pie-legend-col {{
            display: flex;
            flex-direction: column;
            gap: 0.22rem;
            min-width: 0;
        }}
        .war-room-pie-legend-item {{
            display: flex;
            align-items: center;
            gap: 0.32rem;
            min-width: 0;
        }}
        .war-room-pie-swatch {{
            width: 8px;
            height: 8px;
            border-radius: 2px;
            flex-shrink: 0;
        }}
        .war-room-pie-legend-label {{
            color: {COLORS["accent_dark"]};
            font-size: 0.68rem;
            font-weight: 500;
            line-height: 1.2;
            overflow-wrap: anywhere;
        }}
        div.st-key-war-room-paid-media [data-testid="stPopover"] button,
        div.st-key-war-room-crm-funnel [data-testid="stPopover"] button,
        div.st-key-war-room-website-traffic [data-testid="stPopover"] button,
        div.st-key-war-room-organic-social [data-testid="stPopover"] button,
        div.st-key-war-room-content-seo [data-testid="stPopover"] button {{
            color: {COLORS["muted"]} !important;
            font-size: 0.78rem !important;
            padding: 0.1rem 0.35rem !important;
            min-height: 0 !important;
            border: none !important;
            background: transparent !important;
            box-shadow: none !important;
        }}
        div.st-key-war-room-paid-media [data-testid="stPopover"] button:hover,
        div.st-key-war-room-crm-funnel [data-testid="stPopover"] button:hover,
        div.st-key-war-room-website-traffic [data-testid="stPopover"] button:hover,
        div.st-key-war-room-organic-social [data-testid="stPopover"] button:hover,
        div.st-key-war-room-content-seo [data-testid="stPopover"] button:hover {{
            color: {COLORS["accent_dark"]} !important;
            background: rgba(93, 166, 138, 0.1) !important;
        }}
        div.st-key-war-room-alerts-overdue-metric [data-testid="stMetricLabel"],
        div.st-key-war-room-alerts-overdue-metric [data-testid="stMetricValue"],
        div.st-key-war-room-alerts-overdue-metric [data-testid="stMetricValue"] > div {{
            color: {COLORS["danger"]} !important;
        }}
        .war-room-alert-overdue {{
            color: {COLORS["danger"]};
            font-size: 0.9rem;
            line-height: 1.45;
            margin: 0.35rem 0;
        }}
        .war-room-alert-overdue strong,
        .war-room-alert-overdue em {{
            color: {COLORS["danger"]};
        }}
        {panel_rules}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _metric_row(items: list[tuple[str, str]], *, deltas: list[str | None] | None = None) -> None:
    cols = st.columns(len(items))
    for i, (label, value) in enumerate(items):
        delta = deltas[i] if deltas else None
        cols[i].metric(label, value, delta=delta)


def _placeholder_note(text: str) -> None:
    st.markdown(f'<p class="war-room-placeholder">{text}</p>', unsafe_allow_html=True)


def _partition_panel_notes(
    notes: list[str],
    *,
    footnote_markers: tuple[str, ...],
) -> tuple[tuple[str, ...], list[str]]:
    """Move matching notes into popover footnotes; return the rest for inline display."""
    footnotes: list[str] = []
    remaining: list[str] = []
    for note in notes:
        if any(marker in note for marker in footnote_markers):
            footnotes.append(note)
        else:
            remaining.append(note)
    return tuple(footnotes), remaining


def _render_metric_definitions_popover(
    *lines: str,
    footnotes: tuple[str, ...] = (),
) -> None:
    """Compact ℹ️ popover — metric definitions on click."""
    if not lines and not footnotes:
        return
    _spacer, info = st.columns([0.86, 0.14])
    _spacer.write("")
    with info:
        with st.popover("ℹ️", help="Metric definitions"):
            parts: list[str] = []
            if lines:
                parts.append("\n\n".join(lines))
            if footnotes:
                if parts:
                    parts.append("---")
                parts.append("\n\n".join(f"*{note}*" for note in footnotes))
            st.markdown("\n\n".join(parts))


def _panel_period_caption(source: str, period_since: str, period_until: str) -> str:
    """Panel subtitle: ``source (7 days)`` with date range on the next line."""
    line_one = f"{source} (7 days)"
    if period_since and period_until:
        return f"{line_one}\n{period_since} – {period_until}"
    return line_one


def _panel_header(title: str, caption: str) -> None:
    lines = [line.strip() for line in caption.split("\n") if line.strip()]
    has_date_line = len(lines) >= 2
    if has_date_line:
        source_line = lines[0]
        date_line = lines[1]
        body_html = (
            f'<div class="war-room-panel-heading">'
            f'<div class="war-room-panel-title">{html.escape(title)}</div>'
            f'<div class="war-room-panel-caption war-room-panel-caption--source">'
            f"{html.escape(source_line)}</div>"
            f"</div>"
            f'<div class="war-room-panel-caption war-room-panel-caption--dates">'
            f"{html.escape(date_line)}</div>"
        )
    else:
        caption_html = "".join(
            f'<div class="war-room-panel-caption">{html.escape(line)}</div>'
            for line in lines
        )
        body_html = (
            f'<div class="war-room-panel-heading">'
            f'<div class="war-room-panel-title">{html.escape(title)}</div>'
            f"{caption_html}"
            f"</div>"
        )
    st.markdown(
        f'<div class="war-room-panel-header">{body_html}</div>',
        unsafe_allow_html=True,
    )


@contextmanager
def _panel(title: str, caption: str, key: str) -> Iterator[None]:
    _panel_header(title, caption)
    with st.container(border=True, key=key):
        yield


# ---------------------------------------------------------------------------
# Data loaders — wire these up in later stages
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300, show_spinner=False)
def _load_conversion_drivers(
    _loader_version: str = WAR_ROOM_LOADER_VERSION,
) -> ConversionDriversMetrics:
    """Discovery Call & Conversion Drivers — GHL hear-about breakdowns (7 days)."""
    return _cache_safe_metrics(load_conversion_drivers())


@st.cache_data(ttl=300, show_spinner=False)
def _load_command_strip(_loader_version: str = WAR_ROOM_LOADER_VERSION) -> CommandStripMetrics:
    """Stage 2: aggregate top-line KPIs from all connected sources."""
    return _cache_safe_metrics(load_command_strip())


@st.cache_data(ttl=300, show_spinner=False)
def _load_paid_media() -> PaidMediaMetrics:
    """Stage 3: Google Ads + Meta account roll-up, last 7 days."""
    return _cache_safe_metrics(load_paid_media())


@st.cache_data(ttl=300, show_spinner=False)
def _load_crm_funnel() -> CrmFunnelMetrics:
    """Stage 4: GHL signups, bookings, meetings, last 7 days."""
    return _cache_safe_metrics(load_crm_funnel())


@st.cache_data(ttl=300, show_spinner=False)
def _load_website_traffic() -> WebsiteTrafficMetrics:
    """Stage 5: GA4 sessions, users, top channel, embed page views (7 days)."""
    return _cache_safe_metrics(load_website_traffic())


@st.cache_data(ttl=300, show_spinner=False)
def _load_organic_social() -> OrganicSocialMetrics:
    """Stage 6: Instagram organic reach, engagement, followers (7 days)."""
    return _cache_safe_metrics(load_organic_social())


@st.cache_data(ttl=300, show_spinner=False)
def _load_content_seo() -> ContentSeoMetrics:
    """Stage 7: GA4 organic search, blog pageviews, top landing page (7 days)."""
    return _cache_safe_metrics(load_content_seo())


@st.cache_data(ttl=300, show_spinner=False)
def _load_team_ops() -> TeamOpsMetrics:
    """Stage 8: Monday.com — Sam, Je, Voltaire, Lead Paramedic, We Have SEO."""
    return _cache_safe_metrics(load_team_ops())


@st.cache_data(ttl=300, show_spinner=False)
def _load_needs_response(_loader_version: str = WAR_ROOM_LOADER_VERSION) -> NeedsResponseMetrics:
    """Marketing-only inbound — Gmail label queue + Google Chat @mentions."""
    return _cache_safe_metrics(load_needs_response())


def _fmt(value: str | None, *, prefix: str = "", suffix: str = "") -> str:
    if value is None:
        return "—"
    return f"{prefix}{value}{suffix}"


def _fmt_currency(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}"


def _fmt_currency_compact(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:,.0f}"


def _fmt_count(value: float | int | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}"
    return f"{int(round(value)):,}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}%"


def _fmt_vs_prior_avg(pct: float | None) -> str | None:
    if pct is None:
        return None
    return f"{pct:+.0f}% vs prior 7d"


def _fmt_vs_prior_mtd(pct: float | None) -> str | None:
    if pct is None:
        return None
    return f"{pct:+.0f}% vs prior MTD"


def _fmt_vs_prior_ytd(pct: float | None) -> str | None:
    if pct is None:
        return None
    return f"{pct:+.0f}% vs prior YTD"


def _is_spend_sparkline(label: str, trend: TrendSeries | None) -> bool:
    if "ad spend" in label.casefold():
        return True
    if trend is not None and trend.invert_spark_color:
        return True
    if trend is not None and "ad spend" in (trend.label or "").casefold():
        return True
    return False


def _sparkline_visible_values(trend: TrendSeries) -> list[float]:
    values = [point.value for point in trend.points[-7:]]
    if trend.dim_today and len(values) >= 2:
        return values[:-1]
    return values


def _sparkline_direction(trend: TrendSeries) -> int:
    """Visual slope of the solid sparkline: last visible point vs first."""
    values = _sparkline_visible_values(trend)
    if len(values) < 2:
        return 0
    if values[-1] > values[0]:
        return 1
    if values[-1] < values[0]:
        return -1
    return 0


def _sparkline_trend_color(
    trend: TrendSeries,
    *,
    invert: bool,
    delta_pct: float | None = None,
) -> str:
    """Spend: higher vs prior = red, lower = green. Others: the opposite."""
    if len(trend.points) < 2:
        return COLORS["muted"]

    pct = delta_pct if delta_pct is not None else trend.vs_prior_avg_pct
    if pct is not None:
        if pct > 0:
            return COLORS["danger"] if invert else COLORS["accent"]
        if pct < 0:
            return COLORS["accent"] if invert else COLORS["danger"]
        return COLORS["muted"]

    direction = _sparkline_direction(trend)
    if direction == 0:
        return COLORS["muted"]
    if invert:
        return COLORS["accent"] if direction < 0 else COLORS["danger"]
    return COLORS["accent"] if direction > 0 else COLORS["danger"]


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _sparkline_figure(
    trend: TrendSeries,
    *,
    invert: bool,
    delta_pct: float | None = None,
) -> go.Figure | None:
    if not trend.wired or len(trend.points) < 2:
        return None

    points = trend.points[-7:]
    x = [p.date for p in points]
    y = [p.value for p in points]
    line_color = _sparkline_trend_color(trend, invert=invert, delta_pct=delta_pct)
    fig = go.Figure()

    if trend.dim_today and len(points) >= 2:
        fig.add_trace(
            go.Scatter(
                x=x[:-1],
                y=y[:-1],
                mode="lines",
                line=dict(color=line_color, width=2),
                hoverinfo="skip",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=x[-2:],
                y=y[-2:],
                mode="lines+markers",
                line=dict(color=_hex_to_rgba(line_color, 0.45), width=2, dash="dot"),
                marker=dict(size=4, color=_hex_to_rgba(line_color, 0.45)),
                hoverinfo="skip",
            )
        )
    else:
        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                mode="lines",
                line=dict(color=line_color, width=2),
                hoverinfo="skip",
            )
        )

    fig.update_layout(
        height=SPARKLINE_HEIGHT_PX,
        margin=dict(l=2, r=2, t=2, b=2),
        showlegend=False,
        xaxis=dict(visible=False, fixedrange=True),
        yaxis=dict(visible=False, fixedrange=True, rangemode="tozero"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False,
    )
    return fig


def _slugify_label(label: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in label.lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "metric"


def _kpi_container_key(label: str) -> str:
    return f"war-room-kpi-{_slugify_label(label)}"


def _inject_spend_kpi_backgrounds(registry: dict[str, float | None]) -> None:
    """Tint spend KPI cards after render — above prior = red, below = green."""
    rules: list[str] = []
    for container_key, delta_pct in registry.items():
        if delta_pct is None or delta_pct == 0:
            continue
        if delta_pct > 0:
            bg = "rgba(228, 87, 86, 0.24)"
            border = "rgba(228, 87, 86, 0.45)"
        else:
            bg = "rgba(93, 166, 138, 0.28)"
            border = "rgba(93, 166, 138, 0.48)"
        rules.append(
            f'div[class*="st-key-{container_key}"][data-testid="stVerticalBlockBorderWrapper"] '
            f"{{ background: {bg} !important; border-color: {border} !important; }}"
        )
    if rules:
        st.markdown(f"<style>{''.join(rules)}</style>", unsafe_allow_html=True)


def _conversion_kpi_container_key(label: str) -> str:
    return f"war-room-conversion-{_slugify_label(label)}"


def _render_sparkline(
    trend: TrendSeries,
    *,
    invert: bool,
    metric_label: str,
    delta_pct: float | None = None,
) -> None:
    fig = _sparkline_figure(trend, invert=invert, delta_pct=delta_pct)
    if fig is None:
        st.markdown(
            '<div class="war-room-sparkline-placeholder">7d trend · wiring pending</div>',
            unsafe_allow_html=True,
        )
        return
    st.plotly_chart(
        fig,
        use_container_width=True,
        height=SPARKLINE_HEIGHT_PX,
        config={"displayModeBar": False},
        key=f"{_kpi_container_key(metric_label)}-spark",
    )


def _render_trend_metric(
    label: str,
    value: str,
    trend: TrendSeries | None,
    *,
    delta: str | None = None,
    delta_pct: float | None = None,
    invert_sparkline: bool | None = None,
    spend_tone_registry: dict[str, float | None] | None = None,
) -> None:
    """Single KPI card: headline metric and sparkline share one bordered box."""
    if invert_sparkline is None:
        invert_sparkline = _is_spend_sparkline(label, trend)
    container_key = _kpi_container_key(label)
    effective_delta_pct = delta_pct
    if effective_delta_pct is None and trend:
        effective_delta_pct = trend.vs_prior_avg_pct
    if invert_sparkline and spend_tone_registry is not None:
        spend_tone_registry[container_key] = effective_delta_pct
    with st.container(border=True, key=container_key):
        col_metric, col_spark = st.columns(
            [1.2, 0.9],
            gap="small",
            vertical_alignment="center",
        )
        with col_metric:
            if trend and delta_pct is None:
                delta_pct = trend.vs_prior_avg_pct
            if delta is None and delta_pct is not None:
                delta = _fmt_vs_prior_avg(delta_pct)
            elif delta is None and trend:
                delta = _fmt_vs_prior_avg(trend.vs_prior_avg_pct)
            metric_delta_color = "inverse" if invert_sparkline else "normal"
            st.metric(label, value, delta=delta, delta_color=metric_delta_color)
        with col_spark:
            if trend:
                _render_sparkline(
                    trend,
                    invert=invert_sparkline,
                    metric_label=label,
                    delta_pct=delta_pct,
                )
            else:
                st.markdown(
                    '<div class="war-room-sparkline-placeholder">7d trend · wiring pending</div>',
                    unsafe_allow_html=True,
                )


def _format_hear_about_source_label(source: str) -> str:
    text = (source or "").strip()
    if not text or text.casefold() == "(not set)":
        return "Organic"
    fold = text.casefold()
    if "word of mouth" in fold:
        return "WOM"
    if fold.startswith("3rd party"):
        return "3rd party"
    compact = fold.replace(" ", "")
    if "chatgpt" in compact and "ai" in fold:
        return "LLMs"
    if "tiktok" in fold and ("linkedin" in fold or "linedin" in fold or "other social" in fold):
        return "Other Social"
    return text


def _horizontal_bar_figure(
    rows: list[HearAboutCountRow],
    *,
    title: str,
) -> go.Figure | None:
    if not rows:
        return None
    ordered = sorted(rows, key=lambda r: r.count)
    fig = go.Figure(
        go.Bar(
            x=[r.count for r in ordered],
            y=[_format_hear_about_source_label(r.source) for r in ordered],
            orientation="h",
            marker=dict(color=COLORS["accent"]),
            hovertemplate=(
                "<b>%{y}</b><br>Count: %{x:,}<extra></extra>"
            ),
            hoverlabel=dict(
                bgcolor="#FFFFFF",
                bordercolor=COLORS["accent"],
                font=dict(size=12, color=COLORS["accent_dark"]),
            ),
        )
    )
    bar_height = 28
    fig.update_layout(
        title=dict(text=title, font=dict(size=13, color=COLORS["accent_dark"])),
        height=max(160, min(380, bar_height * len(ordered) + 56)),
        margin=dict(l=4, r=16, t=36, b=8),
        showlegend=False,
        xaxis=dict(title="", fixedrange=True, showgrid=True, gridcolor="rgba(107,124,147,0.15)"),
        yaxis=dict(title="", automargin=True, fixedrange=True),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False,
    )
    return fig


def _conversion_delta_badge(delta_pct: float | None) -> str:
    if delta_pct is None:
        return ""
    if delta_pct > 0:
        tone = "up"
        arrow = "↑ "
    elif delta_pct < 0:
        tone = "down"
        arrow = "↓ "
    else:
        tone = "flat"
        arrow = ""
    text = f"{arrow}{delta_pct:+.0f}% vs prior 7d"
    return (
        f'<span class="war-room-conversion-delta war-room-conversion-delta--{tone}">'
        f"{html.escape(text)}</span>"
    )


def _render_conversion_driver_headline(
    label: str,
    value: str,
    *,
    delta_pct: float | None,
) -> None:
    st.markdown(
        (
            f'<p class="war-room-conversion-headline">'
            f'<span class="war-room-conversion-label">{html.escape(label)}:</span> '
            f'<span class="war-room-conversion-value">{html.escape(value)}</span>'
            f"</p>{_conversion_delta_badge(delta_pct)}"
        ),
        unsafe_allow_html=True,
    )


def _render_hear_about_bar_chart(
    rows: list[HearAboutCountRow],
    *,
    title: str,
    total: int | None,
    show_total: bool = True,
) -> None:
    fig = _horizontal_bar_figure(rows, title=title)
    if fig is None:
        st.caption("No records in this window.")
        return
    chart_height = int(fig.layout.height or 160)
    st.plotly_chart(
        fig,
        use_container_width=True,
        height=chart_height,
        config={"displayModeBar": False},
    )
    if show_total and total is not None:
        st.caption(f"Total: {total:,}")


def _traffic_legend_html(rows: list[HearAboutCountRow]) -> str:
    if not rows:
        return ""
    split_at = (len(rows) + 1) // 2
    columns = [rows[:split_at], rows[split_at:]]

    def _column_html(col_rows: list[HearAboutCountRow], color_offset: int) -> str:
        items = []
        for index, row in enumerate(col_rows):
            color = TRAFFIC_PIE_COLORS[(color_offset + index) % len(TRAFFIC_PIE_COLORS)]
            label = html.escape(f"{row.source} ({row.count:,})")
            items.append(
                f'<div class="war-room-pie-legend-item">'
                f'<span class="war-room-pie-swatch" style="background:{color};"></span>'
                f'<span class="war-room-pie-legend-label">{label}</span>'
                f"</div>"
            )
        return f'<div class="war-room-pie-legend-col">{"".join(items)}</div>'

    return (
        f'<div class="war-room-pie-legend">'
        f"{_column_html(columns[0], 0)}"
        f"{_column_html(columns[1], split_at)}"
        f"</div>"
    )


def _traffic_contributor_hover_customdata(row: HearAboutCountRow) -> tuple[str, str]:
    if row.prior_count is None:
        return ("Prior 7d: unavailable", "")
    prior_line = f"Prior 7d: {row.prior_count:,} sessions"
    if row.vs_prior_pct is None:
        if row.prior_count == 0 and row.count > 0:
            delta_line = "vs prior 7d: new"
        else:
            delta_line = "vs prior 7d: —"
    else:
        delta_line = f"vs prior 7d: {row.vs_prior_pct:+.0f}%"
    return (prior_line, delta_line)


def _traffic_contributors_pie_figure(rows: list[HearAboutCountRow]) -> go.Figure | None:
    if not rows:
        return None
    labels = [row.source for row in rows]
    values = [row.count for row in rows]
    customdata = [_traffic_contributor_hover_customdata(row) for row in rows]
    fig = go.Figure(
        go.Pie(
            labels=labels,
            values=values,
            customdata=customdata,
            hole=0.38,
            sort=False,
            direction="clockwise",
            domain=dict(x=[0.14, 0.86], y=[0.12, 0.88]),
            textinfo="percent",
            textposition="outside",
            texttemplate="%{percent:.1%}",
            textfont=dict(size=11, color=COLORS["accent_dark"]),
            marker=dict(
                colors=TRAFFIC_PIE_COLORS[: len(rows)],
                line=dict(color="#FFFFFF", width=1.5),
            ),
            hovertemplate=(
                "<b>%{label}</b><br>"
                "%{value:,} sessions · %{percent}<br>"
                "%{customdata[0]}<br>"
                "%{customdata[1]}"
                "<extra></extra>"
            ),
            showlegend=False,
        )
    )
    fig.update_layout(
        height=260,
        margin=dict(l=18, r=18, t=14, b=14),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def _render_traffic_contributors_card(
    rows: list[HearAboutCountRow],
    *,
    total_sessions: int | None,
    delta_pct: float | None = None,
) -> None:
    """Traffic contributors card — GA4 top channel groups as a pie chart."""
    with st.container(border=True, key=_conversion_kpi_container_key("Traffic contributors (7d)")):
        _render_conversion_driver_headline(
            "Traffic contributors (7d)",
            _fmt_count(total_sessions),
            delta_pct=delta_pct,
        )
        fig = _traffic_contributors_pie_figure(rows)
        if fig is None:
            st.caption("No GA4 channel data in this window.")
            return
        st.plotly_chart(
            fig,
            use_container_width=True,
            height=260,
            config={"displayModeBar": False},
        )
        st.markdown(_traffic_legend_html(rows), unsafe_allow_html=True)


def _render_conversion_driver_card(
    label: str,
    value: str,
    *,
    delta_pct: float | None,
    rows: list[HearAboutCountRow],
    chart_title: str,
    total: int | None,
) -> None:
    """Single conversion-driver card: compact headline and source bar chart in one box."""
    with st.container(border=True, key=_conversion_kpi_container_key(label)):
        _render_conversion_driver_headline(label, value, delta_pct=delta_pct)
        _render_hear_about_bar_chart(
            rows,
            title=chart_title,
            total=total,
            show_total=False,
        )


def _clear_caches() -> None:
    _load_command_strip.clear()
    _load_conversion_drivers.clear()
    _load_paid_media.clear()
    _load_crm_funnel.clear()
    _load_website_traffic.clear()
    _load_organic_social.clear()
    _load_content_seo.clear()
    _load_team_ops.clear()
    _load_needs_response.clear()
    _load_alerts.clear()


MetricsT = TypeVar("MetricsT")


def _cache_safe_metrics(metrics: MetricsT) -> MetricsT:
    """Deep-copy via pickle so Streamlit cache and session snapshots can serialize."""
    return pickle.loads(pickle.dumps(metrics))


def _store_data_snapshot(snapshots: dict[str, object], key: str, data: object) -> None:
    snapshots[key] = pickle.dumps(data)


def _load_data_snapshot(snapshots: dict[str, object], key: str) -> object | None:
    raw = snapshots.get(key)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return pickle.loads(raw)
    return raw


_DATA_SOURCE_SPECS: tuple[tuple[str, str, str], ...] = (
    ("command_strip", "Command strip", "Google Ads, Meta, GHL, GA4"),
    ("conversion_drivers", "Conversion drivers", "GA4, GHL (hear-about lookups)"),
    ("paid_media", "Paid media", "Google Ads, Meta"),
    ("crm_funnel", "CRM & funnel", "GoHighLevel"),
    ("website_traffic", "Website & traffic", "GA4"),
    ("organic_social", "Organic social", "Meta / Instagram"),
    ("content_seo", "Content & SEO", "GA4"),
    ("alerts", "Alerts", "Google Tasks"),
    ("team_ops", "Team & projects", "Monday.com"),
)

_DATA_SOURCE_KEYS: tuple[str, ...] = tuple(key for key, _, _ in _DATA_SOURCE_SPECS)


def _init_data_source_prefs() -> None:
    if "war_room_data_enabled" not in st.session_state:
        st.session_state.war_room_data_enabled = {key: True for key in _DATA_SOURCE_KEYS}
    if "war_room_data_snapshots" not in st.session_state:
        st.session_state.war_room_data_snapshots = {}


def _data_source_enabled(key: str) -> bool:
    return bool(st.session_state.war_room_data_enabled.get(key, True))


def _set_all_data_sources(enabled: bool) -> None:
    for key in _DATA_SOURCE_KEYS:
        st.session_state.war_room_data_enabled[key] = enabled
        st.session_state.pop(f"war_room_src_{key}", None)


def _render_data_source_controls() -> None:
    _init_data_source_prefs()
    enabled_map: dict[str, bool] = st.session_state.war_room_data_enabled
    skipped_count = sum(1 for key in _DATA_SOURCE_KEYS if not enabled_map.get(key, True))

    with st.expander("Load data sources", expanded=skipped_count > 0):
        st.caption(
            "Uncheck sources to skip API calls on refresh. Skipped panels keep "
            "the last loaded snapshot so layout work stays fast."
        )
        preset_col_a, preset_col_b = st.columns(2)
        with preset_col_a:
            if st.button("Layout dev (skip all)", use_container_width=True):
                _set_all_data_sources(False)
                st.rerun()
        with preset_col_b:
            if st.button("Enable all", use_container_width=True):
                _set_all_data_sources(True)
                st.rerun()

        for key, label, apis in _DATA_SOURCE_SPECS:
            enabled_map[key] = st.checkbox(
                label,
                value=enabled_map.get(key, True),
                help=f"APIs: {apis}",
                key=f"war_room_src_{key}",
            )
        st.session_state.war_room_data_enabled = enabled_map


def _resolve_data_source(
    key: str,
    loader: Callable[[], MetricsT],
    empty_factory: Callable[[], MetricsT],
) -> MetricsT:
    """Load live data, or reuse a snapshot when the source is skipped."""
    _init_data_source_prefs()
    snapshots: dict[str, object] = st.session_state.war_room_data_snapshots

    if _data_source_enabled(key):
        data = loader()
        _store_data_snapshot(snapshots, key, data)
        return data

    snapshot = _load_data_snapshot(snapshots, key)
    if snapshot is not None:
        return snapshot  # type: ignore[return-value]

    return empty_factory()


_DATA_SOURCE_LABELS: dict[str, str] = {key: label for key, label, _ in _DATA_SOURCE_SPECS}


@st.cache_data(ttl=300, show_spinner=False)
def _load_alerts(_loader_version: str = WAR_ROOM_LOADER_VERSION) -> AlertsMetrics:
    """Google Tasks — overdue, due today, and due soon."""
    return _cache_safe_metrics(load_alerts())


_WAR_ROOM_LOAD_SPECS: tuple[
    tuple[str, Callable[[], object], Callable[[], object]], ...
] = (
    ("command_strip", _load_command_strip, CommandStripMetrics),
    ("conversion_drivers", _load_conversion_drivers, ConversionDriversMetrics),
    ("paid_media", _load_paid_media, PaidMediaMetrics),
    ("crm_funnel", _load_crm_funnel, CrmFunnelMetrics),
    ("website_traffic", _load_website_traffic, WebsiteTrafficMetrics),
    ("organic_social", _load_organic_social, OrganicSocialMetrics),
    ("content_seo", _load_content_seo, ContentSeoMetrics),
    ("alerts", _load_alerts, AlertsMetrics),
    ("team_ops", _load_team_ops, TeamOpsMetrics),
)


def _load_war_room_metrics(status_slot: object | None = None) -> dict[str, object]:
    """Fetch enabled sources in parallel; reuse snapshots for skipped sources."""
    _init_data_source_prefs()
    snapshots: dict[str, object] = st.session_state.war_room_data_snapshots
    results: dict[str, object] = {}
    pending: list[tuple[str, Callable[[], object], Callable[[], object]]] = []

    for key, loader, empty_factory in _WAR_ROOM_LOAD_SPECS:
        if _data_source_enabled(key):
            pending.append((key, loader, empty_factory))
            continue
        snapshot = _load_data_snapshot(snapshots, key)
        results[key] = snapshot if snapshot is not None else empty_factory()

    if not pending:
        return results

    workers = min(len(pending), 8)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(loader): (key, empty_factory)
            for key, loader, empty_factory in pending
        }
        for future in as_completed(future_map):
            key, empty_factory = future_map[future]
            try:
                data = future.result()
            except Exception:
                data = empty_factory()
            _store_data_snapshot(snapshots, key, data)
            results[key] = data
            if status_slot is not None:
                label = _DATA_SOURCE_LABELS.get(key, key)
                status_slot.write(f"Loaded {label}")

    return results


# ---------------------------------------------------------------------------
# Panel renderers
# ---------------------------------------------------------------------------


def _render_header(last_refresh: datetime) -> None:
    local = last_refresh.astimezone() if last_refresh.tzinfo else last_refresh
    ts = local.strftime("%I:%M:%S %p %Z").lstrip("0")
    st.markdown(
        f"""
        <div class="war-room-header">
            <h1>Marketing Pulse</h1>
            <div class="war-room-status">
                Last refresh · {ts}<br>
                2-column pulse · 9 panels live
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_command_strip(data: CommandStripMetrics) -> None:
    source = "Cross-channel snapshot + MTD + YTD · Google Ads + Meta + GHL + GA4"
    if data.period_since and data.period_until:
        caption = f"{source}\n{data.period_since} – {data.period_until}"
    else:
        caption = f"{source} (last 7 days)"
    with _panel(
        "Command strip",
        caption,
        "war-room-command-strip",
    ):
        st.caption(
            "7-day KPIs compare vs the prior 7 days. Ad spend (MTD) compares vs the same "
            "calendar days last month; Ad spend (YTD) vs the same span last year. Spend cards "
            "turn red when above prior and green when below. Today is dimmed on paid/GA4 series "
            "(intraday may be incomplete)."
        )

        spend_tones: dict[str, float | None] = {}
        row_mtd, row_ytd = st.columns(2, gap="small")
        with row_mtd:
            _render_trend_metric(
                "Ad spend (MTD)",
                _fmt_currency(data.ad_spend_mtd),
                data.ad_spend_mtd_trend,
                delta=_fmt_vs_prior_mtd(data.ad_spend_mtd_vs_prior_pct),
                delta_pct=data.ad_spend_mtd_vs_prior_pct,
                invert_sparkline=True,
                spend_tone_registry=spend_tones,
            )
        with row_ytd:
            _render_trend_metric(
                "Ad spend (YTD)",
                _fmt_currency(data.ad_spend_ytd),
                data.ad_spend_ytd_trend,
                delta=_fmt_vs_prior_ytd(data.ad_spend_ytd_vs_prior_pct),
                delta_pct=data.ad_spend_ytd_vs_prior_pct,
                invert_sparkline=True,
                spend_tone_registry=spend_tones,
            )

        row_spend, row_sessions, row_contacts, row_leads = st.columns(4, gap="small")
        with row_spend:
            _render_trend_metric(
                "Ad spend (7d)",
                _fmt_currency(data.spend_7d),
                data.spend_trend,
                invert_sparkline=True,
                spend_tone_registry=spend_tones,
            )
        with row_sessions:
            _render_trend_metric(
                "GA4 sessions (7d)",
                _fmt_count(data.sessions_7d),
                data.sessions_trend,
                invert_sparkline=False,
            )
        with row_contacts:
            _render_trend_metric(
                "New contacts (7d)",
                _fmt_count(data.new_contacts_7d),
                data.new_contacts_trend,
                invert_sparkline=False,
            )
        with row_leads:
            _render_trend_metric(
                "Leads (7d)",
                _fmt_count(data.leads_7d),
                data.leads_trend,
                invert_sparkline=False,
            )

        _inject_spend_kpi_backgrounds(spend_tones)

        if data.notes:
            for note in data.notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


def _render_conversion_drivers(
    data: ConversionDriversMetrics,
    command: CommandStripMetrics,
) -> None:
    with _panel(
        "Discovery Call & Conversion Drivers",
        _panel_period_caption(
            f"GA4 + GoHighLevel · {HEAR_ABOUT_US_FIELD_NAME}",
            data.period_since,
            data.period_until,
        ),
        "war-room-conversion-drivers",
    ):
        st.caption(
            "Traffic = GA4 default channel group (top 5 + Other) · "
            "Bookings = calendar events by date added · "
            "Meetings = calendar events by start time · "
            "Signups = Sign Up Date in range with Committed? = Yes · "
            "deltas vs prior 7d (traffic, bookings, meetings & signups)"
        )
        row_top = st.columns(2, gap="medium")
        row_bottom = st.columns(2, gap="medium")
        bookings_count = (
            data.total_bookings
            if data.total_bookings is not None
            else command.bookings_7d
        )
        meetings_count = (
            data.total_meetings
            if data.total_meetings is not None
            else command.meetings_7d
        )
        signups_count = (
            data.total_committed
            if data.total_committed is not None
            else command.signups_7d
        )
        with row_top[0]:
            _render_traffic_contributors_card(
                data.traffic_contributors,
                total_sessions=data.total_sessions_7d,
                delta_pct=data.total_sessions_7d_vs_prior_pct,
            )
        with row_top[1]:
            _render_conversion_driver_card(
                "Bookings (7d)",
                _fmt_count(bookings_count),
                delta_pct=command.bookings_7d_vs_prior_pct,
                rows=data.bookings_by_source,
                chart_title="DC Bookings by Source",
                total=bookings_count,
            )
        with row_bottom[0]:
            _render_conversion_driver_card(
                "Meetings (7d)",
                _fmt_count(meetings_count),
                delta_pct=command.meetings_7d_vs_prior_pct,
                rows=data.meetings_by_source,
                chart_title="DC Meetings by Source",
                total=meetings_count,
            )
        with row_bottom[1]:
            _render_conversion_driver_card(
                "Signups (7d)",
                _fmt_count(signups_count),
                delta_pct=command.signups_7d_vs_prior_pct,
                rows=data.committed_by_source,
                chart_title="Signups by Source",
                total=signups_count,
            )
        if data.notes:
            for note in data.notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


def _render_paid_media(data: PaidMediaMetrics) -> None:
    with _panel(
        "Paid media",
        _panel_period_caption("Google Ads + Meta", data.period_since, data.period_until),
        "war-room-paid-media",
    ):
        _metric_row(
            [
                ("Google", _fmt_currency_compact(data.google_spend_7d)),
                ("Meta", _fmt_currency_compact(data.meta_spend_7d)),
            ]
        )
        _metric_row(
            [
                ("Leads", _fmt_count(data.leads_7d)),
                ("CPL", _fmt_currency_compact(data.cpa_7d)),
            ]
        )
        footnotes, remaining_notes = _partition_panel_notes(
            data.notes,
            footnote_markers=("Meta includes today",),
        )
        _render_metric_definitions_popover(
            "**Leads** — Google Ads conversions + Meta lead actions.",
            "**CPL** — combined Google + Meta spend ÷ leads.",
            footnotes=footnotes,
        )
        if remaining_notes:
            for note in remaining_notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


def _render_crm_funnel(data: CrmFunnelMetrics) -> None:
    with _panel(
        "CRM & funnel",
        _panel_period_caption("GoHighLevel", data.period_since, data.period_until),
        "war-room-crm-funnel",
    ):
        _metric_row(
            [
                ("Signups", _fmt_count(data.signups_7d)),
                ("Conv %", _fmt_pct(data.conversion_rate)),
            ]
        )
        _metric_row(
            [
                ("Meetings", _fmt_count(data.meetings_7d)),
                ("Bookings", _fmt_count(data.bookings_7d)),
            ]
        )
        _render_metric_definitions_popover(
            "**Signups** — contacts with **Sign Up Date** in the 7-day window.",
            "**Bookings** — calendar appointments by **dateAdded** (when scheduled).",
            "**Meetings** — calendar appointments by **startTime** (when they occur).",
            "**Conv %** — signups ÷ meetings held in the 7-day window.",
            "Conv % above 100% means more signups than meetings in this window.",
        )
        if data.notes:
            for note in data.notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


def _render_website_traffic(data: WebsiteTrafficMetrics) -> None:
    with _panel(
        "Website & traffic",
        _panel_period_caption("GA4", data.period_since, data.period_until),
        "war-room-website-traffic",
    ):
        _metric_row(
            [
                ("Sessions", _fmt_count(data.sessions_7d)),
                ("Users", _fmt_count(data.users_7d)),
            ]
        )
        _metric_row(
            [
                ("Top channel", _fmt(data.top_channel)),
                ("Embed", _fmt_count(data.embed_pageviews_7d)),
            ]
        )
        footnotes, remaining_notes = _partition_panel_notes(
            data.notes,
            footnote_markers=("GA4 includes today", "Embed views ="),
        )
        _render_metric_definitions_popover(
            "**Top channel** — highest GA4 `sessionDefaultChannelGroup` by sessions.",
            "**Embed** — pageviews on GHL booking embed pages (home excluded).",
            footnotes=footnotes,
        )
        if remaining_notes:
            for note in remaining_notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


def _fmt_delta(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{value:+,}"


def _render_organic_social(data: OrganicSocialMetrics) -> None:
    page = f" · {data.page_name}" if data.page_name else ""
    with _panel(
        "Organic social",
        _panel_period_caption(f"Instagram{page}", data.period_since, data.period_until),
        "war-room-organic-social",
    ):
        _metric_row(
            [
                ("IG reach", _fmt_count(data.ig_reach_7d)),
                ("Engagement", _fmt_count(data.ig_engagement_7d)),
                ("Followers Δ", _fmt_delta(data.follower_delta_7d)),
            ]
        )
        organic_footnotes: list[str] = []
        top_label = _fmt(data.top_post)
        if data.top_post and top_label != "—":
            if data.top_post_engagement is not None:
                organic_footnotes.append(
                    f"Top post ({data.top_post_engagement:,} eng.): {top_label}"
                )
            else:
                organic_footnotes.append(f"Top post: {top_label}")
        if data.posts_in_period is not None:
            organic_footnotes.append(f"{data.posts_in_period} post(s) published in window.")
        note_footnotes, remaining_notes = _partition_panel_notes(
            data.notes,
            footnote_markers=("Follower Δ uses", "Engagement ="),
        )
        _render_metric_definitions_popover(
            "**IG reach** — sum of daily Instagram account reach in the 7-day window.",
            "**Engagement** — likes + comments on posts published in the 7-day window.",
            "**Followers Δ** — net change in Instagram `follower_count` snapshots.",
            footnotes=tuple(organic_footnotes) + note_footnotes,
        )
        if remaining_notes:
            for note in remaining_notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


def _render_content_seo(data: ContentSeoMetrics) -> None:
    with _panel(
        "Content & SEO",
        _panel_period_caption("GA4", data.period_since, data.period_until),
        "war-room-content-seo",
    ):
        _metric_row(
            [
                ("Organic sessions", _fmt_count(data.organic_sessions_7d)),
                ("Blog pageviews", _fmt_count(data.blog_pageviews_7d)),
            ]
        )
        seo_footnotes: list[str] = []
        landing = _fmt(data.top_landing_page)
        if landing != "—":
            if data.top_landing_sessions is not None:
                seo_footnotes.append(
                    f"Top landing page ({data.top_landing_sessions:,} sessions): {landing}"
                )
            else:
                seo_footnotes.append(f"Top landing page: {landing}")
        note_footnotes, remaining_notes = _partition_panel_notes(
            data.notes,
            footnote_markers=("Blog pageviews matched", "GA4 includes today"),
        )
        _render_metric_definitions_popover(
            "**Organic sessions** — GA4 Organic Search channel sessions.",
            "**Blog pageviews** — WordPress blog post pageviews.",
            "**Top landing** — highest session entry page in the 7-day window.",
            footnotes=tuple(seo_footnotes) + note_footnotes,
        )
        if remaining_notes:
            for note in remaining_notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


def _board_display_name(board_name: str) -> str:
    for suffix in (" New To-Do List", " To-Do List"):
        if board_name.endswith(suffix):
            return board_name[: -len(suffix)]
    return board_name


def _board_status_tooltip_html(task_names: list[str], *, max_show: int = 20) -> str:
    if not task_names:
        return ""
    shown = task_names[:max_show]
    items = "".join(f"<li>{html.escape(name)}</li>" for name in shown)
    if len(task_names) > max_show:
        items += (
            f'<li class="war-room-board-status-tip-more">'
            f"+ {len(task_names) - max_show} more</li>"
        )
    return f'<div class="war-room-board-status-tip"><ul>{items}</ul></div>'


def _board_status_chip(label: str, count: int, task_names: list[str] | None = None) -> str:
    safe_label = html.escape(label)
    tip_class = " war-room-board-status--has-tip" if task_names else ""
    tooltip = _board_status_tooltip_html(task_names or [])
    return (
        f'<div class="war-room-board-status{tip_class}">'
        f'<span class="war-room-board-status-label">{safe_label}</span>'
        f'<span class="war-room-board-status-value">{count:,}</span>'
        f"{tooltip}"
        f"</div>"
    )


def _board_status_html(board: BoardTaskSummary) -> str:
    display_name = html.escape(_board_display_name(board.board_name))
    if not board.by_status:
        return (
            f'<div class="war-room-board-block">'
            f'<div class="war-room-board-name">{display_name}</div>'
            f'<p class="war-room-board-empty">No open tasks</p>'
            f"</div>"
        )

    open_count = sum(row.count for row in board.by_status)
    bucket_tasks = board.tasks_by_bucket
    row_one = [
        _board_status_chip(
            format_team_status_label(status),
            status_count_for(board.by_status, status),
            bucket_tasks.get(status, []),
        )
        for status in TEAM_OPS_SUMMARY_BUCKETS[:3]
    ]
    ready_status = TEAM_OPS_SUMMARY_BUCKETS[3]
    row_two = [
        _board_status_chip(
            format_team_status_label(ready_status),
            status_count_for(board.by_status, ready_status),
            bucket_tasks.get(ready_status, []),
        ),
        _board_status_chip("Open", open_count, bucket_tasks.get("Open", [])),
    ]
    return (
        f'<div class="war-room-board-block">'
        f'<div class="war-room-board-name">{display_name}</div>'
        f'<div class="war-room-board-status-rows">'
        f'<div class="war-room-board-statuses">{"".join(row_one)}</div>'
        f'<div class="war-room-board-statuses">{"".join(row_two)}</div>'
        f"</div></div>"
    )


def _render_team_ops(data: TeamOpsMetrics) -> None:
    with _panel(
        "Team & projects",
        "Monday.com · open queue · current status",
        "war-room-team-ops",
    ):
        _metric_row(
            [
                ("Requested", _fmt_count(data.total_requested)),
                ("Working On It", _fmt_count(data.total_working)),
                ("In Review", _fmt_count(data.total_in_review)),
            ]
        )
        _metric_row(
            [
                ("Ready for Publishing", _fmt_count(data.total_ready_for_publishing)),
                ("Open", _fmt_count(data.total_open)),
            ]
        )
        if data.boards:
            blocks = "".join(_board_status_html(board) for board in data.boards)
            st.markdown(
                f'<div class="war-room-board-list">{blocks}</div>',
                unsafe_allow_html=True,
            )
        if data.notes:
            for note in data.notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


def _render_needs_response(data: NeedsResponseMetrics) -> None:
    with _panel(
        "Needs response",
        "Marketing-only · Gmail label queue + Google Chat spaces (not general inbox)",
        "war-room-needs-response",
    ):
        _metric_row(
            [
                ("Gmail (marketing)", _fmt_count(data.gmail_count)),
                ("Google Chat", _fmt_count(data.chat_count)),
                ("Oldest waiting", _fmt(data.oldest_wait)),
            ]
        )
        if data.items:
            for item in data.items[:5]:
                st.markdown(
                    f"**{item.source}** · {item.sender} · _{item.age}_  \n{item.preview}"
                )
        else:
            st.caption("No pending marketing requests in queue.")
        if data.notes:
            for note in data.notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


def _render_alerts(data: AlertsMetrics) -> None:
    with _panel(
        "Alerts",
        "Google Tasks · overdue and upcoming due dates",
        "war-room-alerts",
    ):
        cols = st.columns(3)
        with cols[0]:
            with st.container(key="war-room-alerts-overdue-metric"):
                st.metric("Overdue", _fmt_count(data.overdue_count))
        with cols[1]:
            st.metric("Due today", _fmt_count(data.due_today_count))
        with cols[2]:
            st.metric("Due soon", _fmt_count(data.due_soon_count))
        if data.items:
            for item in data.items[:8]:
                title = html.escape(item.title)
                due_label = html.escape(item.due_label)
                list_name = html.escape(item.list_name)
                if item.severity == "Overdue":
                    st.markdown(
                        f'<p class="war-room-alert-overdue">'
                        f"<strong>{html.escape(item.severity)}</strong> · {title} · "
                        f"<em>{due_label}</em><br>{list_name}</p>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"**{item.severity}** · {item.title} · _{item.due_label}_  \n"
                        f"{item.list_name}"
                    )
        elif not data.errors:
            st.caption("No overdue or upcoming tasks with due dates.")
        if data.notes:
            for note in data.notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="Marketing Pulse",
        layout="wide",
        page_icon="🎯",
        initial_sidebar_state="collapsed",
    )
    _inject_styles()

    with st.sidebar:
        st.header("Marketing Pulse controls")
        _render_data_source_controls()
        refresh_minutes = st.selectbox("Auto-refresh interval", [0, 5, 15, 30], index=0, format_func=lambda m: "Off" if m == 0 else f"{m} min")
        if st.button("Refresh now", type="primary", use_container_width=True):
            _clear_caches()
            st.rerun()
        st.caption(
            "Refresh now fetches live data for enabled sources. Skipped sources "
            "keep the last snapshot until you turn them back on."
        )

    last_refresh = datetime.now(timezone.utc)
    _render_header(last_refresh)

    col_main, col_side = st.columns([2, 1], gap="medium")

    with st.status("Loading Marketing Pulse…", expanded=True) as load_status:
        metrics = _load_war_room_metrics(status_slot=load_status)
        load_status.update(label="Marketing Pulse data loaded", state="complete", expanded=False)

    command = metrics["command_strip"]  # type: ignore[assignment]
    conversion = metrics["conversion_drivers"]  # type: ignore[assignment]
    paid = metrics["paid_media"]  # type: ignore[assignment]
    crm = metrics["crm_funnel"]  # type: ignore[assignment]
    traffic = metrics["website_traffic"]  # type: ignore[assignment]
    organic = metrics["organic_social"]  # type: ignore[assignment]
    content = metrics["content_seo"]  # type: ignore[assignment]
    alerts = metrics["alerts"]  # type: ignore[assignment]
    team = metrics["team_ops"]  # type: ignore[assignment]

    with col_main:
        with st.container(key="war-room-scroll-main"):
            _render_command_strip(command)
            _render_conversion_drivers(conversion, command)

            col_paid, col_crm, col_traffic = st.columns(3, gap="small")
            with col_paid:
                _render_paid_media(paid)
            with col_crm:
                _render_crm_funnel(crm)
            with col_traffic:
                _render_website_traffic(traffic)

            col_organic, col_content = st.columns(2, gap="small")
            with col_organic:
                _render_organic_social(organic)
            with col_content:
                _render_content_seo(content)

    with col_side:
        with st.container(key="war-room-scroll-side"):
            _render_alerts(alerts)
            _render_team_ops(team)

    if refresh_minutes > 0:
        st.caption(f"Auto-refresh every {refresh_minutes} min — not active yet.")


if __name__ == "__main__":
    main()
