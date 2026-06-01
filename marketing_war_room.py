"""
Marketing War Room — single-screen operational pulse.

Run:
  streamlit run marketing_war_room.py

Build in stages (suggested order):
  1. Layout shell, styling, refresh controls
  2. Command strip — top-line KPIs across all channels
  3. Paid media — Google Ads + Meta (spend, leads, CPA)
  4. CRM & funnel — GHL signups, bookings, conversions
  5. Website & traffic — GA4 sessions, channels, embed pages
  6. Organic social — Instagram / Meta engagement
  7. Content & SEO — blog traffic, organic search trends
  8. Team & projects — Monday.com board activity          ← you are here
  9. Needs response — marketing-only Gmail + Google Chat queue
  10. Alerts — thresholds, anomalies, stale-data warnings

Needs response scope (marketing-only, not a full inbox):
  - Gmail: unread in a dedicated label/filter (e.g. Marketing/Action)
  - Google Chat: unread @mentions or starred items in named marketing spaces
  - Surface counts + oldest-waiting age + top few items; no general mail/DMs
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from war_room_data import (
    CommandStripMetrics,
    ContentSeoMetrics,
    CrmFunnelMetrics,
    OrganicSocialMetrics,
    PaidMediaMetrics,
    TeamOpsMetrics,
    TrendSeries,
    WebsiteTrafficMetrics,
    load_command_strip,
    load_content_seo,
    load_crm_funnel,
    load_organic_social,
    load_paid_media,
    load_team_ops,
    load_website_traffic,
)

load_dotenv(Path(__file__).resolve().parent / ".env")

COLORS = {
    "accent": "#5DA68A",
    "accent_dark": "#264540",
    "muted": "#6B7C93",
    "warning": "#F58518",
    "danger": "#E45756",
    "panel_bg": "#FFFFFF",
    "page_bg": "#F4F8FB",
}

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------


def _inject_styles() -> None:
    st.markdown(
        f"""
        <style>
        .stApp {{ background: {COLORS["page_bg"]}; }}
        .block-container {{
            padding-top: 0.75rem;
            padding-bottom: 0.75rem;
            max-width: 1600px;
        }}
        [data-testid="stMetric"] {{
            background: white;
            border-radius: 10px;
            padding: 0.5rem 0.75rem;
            box-shadow: 0 1px 2px rgba(38,69,64,0.06);
            border: 1px solid rgba(93,166,138,0.12);
        }}
        [data-testid="stMetricLabel"] {{
            color: {COLORS["accent_dark"]};
            font-weight: 600;
            font-size: 0.78rem;
        }}
        [data-testid="stMetricValue"] {{
            color: {COLORS["accent"]};
            font-size: 1.35rem;
        }}
        div[data-testid="stVerticalBlockBorderWrapper"] {{
            background: {COLORS["panel_bg"]};
            border-radius: 12px;
            border-color: rgba(93,166,138,0.22) !important;
            box-shadow: 0 1px 3px rgba(38,69,64,0.05);
        }}
        .war-room-header {{
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 1rem;
            margin-bottom: 0.5rem;
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
        .war-room-placeholder {{
            color: {COLORS["muted"]};
            font-size: 0.82rem;
            font-style: italic;
            margin: 0.25rem 0 0.5rem 0;
        }}
        .war-room-sparkline-placeholder {{
            background: rgba(107, 124, 147, 0.08);
            border: 1px dashed rgba(107, 124, 147, 0.35);
            border-radius: 8px;
            color: {COLORS["muted"]};
            font-size: 0.72rem;
            height: 56px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-top: 0.35rem;
        }}
        .war-room-trend-label {{
            color: {COLORS["muted"]};
            font-size: 0.72rem;
            margin-top: 0.15rem;
        }}
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


def _panel_header(title: str, caption: str) -> None:
    st.subheader(title)
    st.caption(caption)


# ---------------------------------------------------------------------------
# Data loaders — wire these up in later stages
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300, show_spinner=False)
def _load_command_strip() -> CommandStripMetrics:
    """Stage 2: aggregate top-line KPIs from all connected sources."""
    return load_command_strip()


@st.cache_data(ttl=300, show_spinner=False)
def _load_paid_media() -> PaidMediaMetrics:
    """Stage 3: Google Ads + Meta account roll-up, last 7 days."""
    return load_paid_media()


@st.cache_data(ttl=300, show_spinner=False)
def _load_crm_funnel() -> CrmFunnelMetrics:
    """Stage 4: GHL signups, bookings, meetings, last 7 days."""
    return load_crm_funnel()


@st.cache_data(ttl=300, show_spinner=False)
def _load_website_traffic() -> WebsiteTrafficMetrics:
    """Stage 5: GA4 sessions, users, top channel, embed page views (7 days)."""
    return load_website_traffic()


@st.cache_data(ttl=300, show_spinner=False)
def _load_organic_social() -> OrganicSocialMetrics:
    """Stage 6: Instagram organic reach, engagement, followers (7 days)."""
    return load_organic_social()


@st.cache_data(ttl=300, show_spinner=False)
def _load_content_seo() -> ContentSeoMetrics:
    """Stage 7: GA4 organic search, blog pageviews, top landing page (7 days)."""
    return load_content_seo()


@st.cache_data(ttl=300, show_spinner=False)
def _load_team_ops() -> TeamOpsMetrics:
    """Stage 8: Monday.com — Sam, Je, and Communication Plan boards."""
    return load_team_ops()


@st.cache_data(ttl=300, show_spinner=False)
def _load_needs_response() -> dict:
    """Stage 9: marketing-only inbound — Gmail API + Google Chat API.

    Configure via .env when wiring:
      WAR_ROOM_GMAIL_LABEL=Marketing/Action
      WAR_ROOM_CHAT_SPACES=Marketing Team,Agency Updates   (comma-separated names)
    """
    return {
        "gmail_count": None,
        "chat_count": None,
        "oldest_wait": None,
        "items": [],  # list[{"source", "from", "preview", "age"}]
    }


def _fmt(value: str | None, *, prefix: str = "", suffix: str = "") -> str:
    if value is None:
        return "—"
    return f"{prefix}{value}{suffix}"


def _fmt_currency(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}"


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
    return f"{pct:+.0f}% vs 6d avg"


def _sparkline_figure(trend: TrendSeries) -> go.Figure | None:
    if not trend.wired or len(trend.points) < 2:
        return None

    points = trend.points[-7:]
    x = [p.date for p in points]
    y = [p.value for p in points]
    fig = go.Figure()

    if trend.dim_today and len(points) >= 2:
        fig.add_trace(
            go.Scatter(
                x=x[:-1],
                y=y[:-1],
                mode="lines",
                line=dict(color=COLORS["accent"], width=2),
                hoverinfo="skip",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=x[-1:],
                y=y[-1:],
                mode="lines+markers",
                line=dict(color="rgba(93,166,138,0.35)", width=2, dash="dot"),
                marker=dict(size=5, color="rgba(93,166,138,0.35)"),
                hoverinfo="skip",
            )
        )
    else:
        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                mode="lines",
                line=dict(color=COLORS["accent"], width=2),
                hoverinfo="skip",
            )
        )

    fig.update_layout(
        height=56,
        margin=dict(l=2, r=2, t=2, b=2),
        showlegend=False,
        xaxis=dict(visible=False, fixedrange=True),
        yaxis=dict(visible=False, fixedrange=True),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False,
    )
    return fig


def _render_sparkline(trend: TrendSeries) -> None:
    fig = _sparkline_figure(trend)
    if fig is None:
        st.markdown(
            '<div class="war-room-sparkline-placeholder">7d trend · wiring pending</div>',
            unsafe_allow_html=True,
        )
        return
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _render_trend_metric(
    label: str,
    value: str,
    trend: TrendSeries | None,
    *,
    trend_caption: str,
) -> None:
    delta = _fmt_vs_prior_avg(trend.vs_prior_avg_pct) if trend else None
    st.metric(label, value, delta=delta, delta_color="normal")
    if trend:
        st.markdown(f'<p class="war-room-trend-label">{trend_caption}</p>', unsafe_allow_html=True)
        _render_sparkline(trend)
    else:
        st.markdown(
            '<div class="war-room-sparkline-placeholder">7d trend · wiring pending</div>',
            unsafe_allow_html=True,
        )


def _clear_caches() -> None:
    _load_command_strip.clear()
    _load_paid_media.clear()
    _load_crm_funnel.clear()
    _load_website_traffic.clear()
    _load_organic_social.clear()
    _load_content_seo.clear()
    _load_team_ops.clear()
    _load_needs_response.clear()


# ---------------------------------------------------------------------------
# Panel renderers
# ---------------------------------------------------------------------------


def _render_header(last_refresh: datetime) -> None:
    local = last_refresh.astimezone() if last_refresh.tzinfo else last_refresh
    ts = local.strftime("%I:%M:%S %p %Z").lstrip("0")
    st.markdown(
        f"""
        <div class="war-room-header">
            <h1>Marketing War Room</h1>
            <div class="war-room-status">
                Last refresh · {ts}<br>
                Single-screen pulse · 8 of 10 panels live
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_command_strip(data: CommandStripMetrics) -> None:
    with st.container(border=True):
        _panel_header(
            "Command strip",
            "Cross-channel snapshot · today + MTD · Google Ads + Meta + GHL + GA4",
        )

        st.caption(
            "Trend row · 7-day sparklines on spend, leads, and sessions. "
            "Today is dimmed on Meta- and GA4-backed series (intraday may be incomplete)."
        )

        trend_spend, trend_leads, trend_sessions = st.columns(3, gap="small")
        with trend_spend:
            _render_trend_metric(
                "Ad spend (today)",
                _fmt_currency(data.spend_today),
                data.spend_trend,
                trend_caption="7d trend · Google Ads + Meta · today dimmed",
            )
        with trend_leads:
            _render_trend_metric(
                "Leads (today)",
                _fmt_count(data.leads_today),
                data.leads_trend,
                trend_caption="7d trend · Google Ads + Meta · today dimmed",
            )
        with trend_sessions:
            _render_trend_metric(
                "GA4 sessions (today)",
                _fmt_count(data.sessions_today),
                data.sessions_trend,
                trend_caption="7d trend · GA4 · today dimmed",
            )

        st.caption("Snapshot row · today counts without trend (sparse daily signal).")
        snap_a, snap_b, snap_c = st.columns(3, gap="small")
        with snap_a:
            st.metric("GHL signups (today)", _fmt_count(data.signups_today))
        with snap_b:
            st.metric("Bookings (today)", _fmt_count(data.bookings_today))
        with snap_c:
            st.metric("Ad spend (MTD)", _fmt_currency(data.ad_spend_mtd))

        if data.notes:
            for note in data.notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


def _render_paid_media(data: PaidMediaMetrics) -> None:
    with st.container(border=True):
        _panel_header(
            "Paid media",
            f"Google Ads + Meta · {data.period_since} – {data.period_until} (7 days)",
        )
        _metric_row(
            [
                ("Google spend", _fmt_currency(data.google_spend_7d)),
                ("Meta spend", _fmt_currency(data.meta_spend_7d)),
                ("Leads", _fmt_count(data.leads_7d)),
                ("CPA", _fmt_currency(data.cpa_7d)),
            ]
        )
        st.caption(
            "Leads = Google Ads conversions + Meta lead actions · "
            "CPA = combined spend ÷ leads"
        )
        if data.notes:
            for note in data.notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


def _render_crm_funnel(data: CrmFunnelMetrics) -> None:
    with st.container(border=True):
        _panel_header(
            "CRM & funnel",
            f"GoHighLevel · {data.period_since} – {data.period_until} (7 days)",
        )
        _metric_row(
            [
                ("Signups", _fmt_count(data.signups_7d)),
                ("Bookings", _fmt_count(data.bookings_7d)),
                ("Meetings", _fmt_count(data.meetings_7d)),
                ("Conv. rate", _fmt_pct(data.conversion_rate)),
            ]
        )
        st.caption(
            "Signups = Sign Up Date · Bookings = appointment dateAdded · "
            "Meetings = appointment startTime · "
            "Conv. rate = bookings ÷ signups (same 7-day window, not cohort-matched)"
        )
        if (
            data.conversion_rate is not None
            and data.conversion_rate > 100
            and data.signups_7d
            and data.bookings_7d
        ):
            st.caption(
                "Conv. rate above 100% is normal here — bookings often tie to contacts "
                "who signed up outside this window."
            )
        if data.notes:
            for note in data.notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


def _render_website_traffic(data: WebsiteTrafficMetrics) -> None:
    with st.container(border=True):
        _panel_header(
            "Website & traffic",
            f"GA4 · {data.period_since} – {data.period_until} (7 days)",
        )
        _metric_row(
            [
                ("Sessions", _fmt_count(data.sessions_7d)),
                ("Users", _fmt_count(data.users_7d)),
                ("Top channel", _fmt(data.top_channel)),
                ("Embed views", _fmt_count(data.embed_pageviews_7d)),
            ]
        )
        st.caption(
            "Top channel = highest sessionDefaultChannelGroup by sessions · "
            "Embed views = GHL booking embed pages (home excluded)"
        )
        if data.notes:
            for note in data.notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


def _fmt_delta(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{value:+,}"


def _render_organic_social(data: OrganicSocialMetrics) -> None:
    with st.container(border=True):
        page = f" · {data.page_name}" if data.page_name else ""
        _panel_header(
            "Organic social",
            f"Instagram{page} · {data.period_since} – {data.period_until} (7 days)",
        )
        _metric_row(
            [
                ("IG reach", _fmt_count(data.ig_reach_7d)),
                ("Engagement", _fmt_count(data.ig_engagement_7d)),
                ("Followers Δ", _fmt_delta(data.follower_delta_7d)),
            ]
        )
        top_label = _fmt(data.top_post)
        if data.top_post_engagement is not None:
            st.caption(f"Top post ({data.top_post_engagement:,} eng.): {top_label}")
        else:
            st.caption(f"Top post: {top_label}")
        if data.posts_in_period is not None:
            st.caption(f"{data.posts_in_period} post(s) published in window.")
        if data.notes:
            for note in data.notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


def _render_content_seo(data: ContentSeoMetrics) -> None:
    with st.container(border=True):
        _panel_header(
            "Content & SEO",
            f"GA4 · {data.period_since} – {data.period_until} (7 days)",
        )
        _metric_row(
            [
                ("Organic sessions", _fmt_count(data.organic_sessions_7d)),
                ("Blog pageviews", _fmt_count(data.blog_pageviews_7d)),
            ]
        )
        landing = _fmt(data.top_landing_page)
        if data.top_landing_sessions is not None:
            st.caption(f"Top landing page ({data.top_landing_sessions:,} sessions): {landing}")
        else:
            st.caption(f"Top landing page: {landing}")
        st.caption(
            "Organic sessions = Organic Search channel · "
            "Blog pageviews = WordPress blog posts · Top landing = highest session entry page"
        )
        if data.notes:
            for note in data.notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


def _render_team_ops(data: TeamOpsMetrics) -> None:
    with st.container(border=True):
        _panel_header(
            "Team & projects",
            "Monday.com · Sam New To-Do List · Je New To-Do List · Communication Plan",
        )
        _metric_row(
            [
                ("Open tasks", _fmt_count(data.open_tasks)),
                ("Due this week", _fmt_count(data.due_this_week)),
                ("Overdue", _fmt_count(data.overdue)),
            ]
        )
        for board in data.boards:
            st.caption(
                f"{board.board_name}: {board.open_tasks} open · "
                f"{board.due_this_week} due this week · {board.overdue} overdue"
            )
        if data.notes:
            for note in data.notes:
                st.caption(note)
        if data.errors:
            for err in data.errors:
                st.warning(err)


def _render_needs_response(data: dict) -> None:
    with st.container(border=True):
        _panel_header(
            "Needs response",
            "Marketing-only · Gmail label queue + Google Chat spaces (not general inbox)",
        )
        _metric_row(
            [
                ("Gmail (marketing)", _fmt(data["gmail_count"])),
                ("Google Chat", _fmt(data["chat_count"])),
                ("Oldest waiting", _fmt(data["oldest_wait"])),
            ]
        )
        items: list = data.get("items") or []
        if items:
            for item in items[:5]:
                st.markdown(
                    f"**{item.get('source', '—')}** · {item.get('from', '—')} · "
                    f"_{item.get('age', '—')}_  \n{item.get('preview', '')}"
                )
        else:
            st.caption("No pending marketing requests in queue.")
        _placeholder_note(
            "Stage 9 — Gmail API (scoped label) + Chat API (named spaces / @mentions)."
        )


def _render_alerts() -> None:
    with st.container(border=True):
        _panel_header("Alerts", "Thresholds, anomalies, stale feeds")
        st.info("No alerts configured yet.")
        _placeholder_note("Stage 10 — add rules (e.g. CPA spike, booking drop, overdue tasks).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="Marketing War Room",
        layout="wide",
        page_icon="🎯",
        initial_sidebar_state="collapsed",
    )
    _inject_styles()

    with st.sidebar:
        st.header("War Room controls")
        refresh_minutes = st.selectbox("Auto-refresh interval", [0, 5, 15, 30], index=0, format_func=lambda m: "Off" if m == 0 else f"{m} min")
        if st.button("Refresh now", type="primary", use_container_width=True):
            _clear_caches()
            st.rerun()
        st.caption("Caches clear on manual refresh. Wire auto-refresh in a later stage.")

    last_refresh = datetime.now(timezone.utc)
    _render_header(last_refresh)

    with st.spinner("Loading command strip…"):
        command = _load_command_strip()
    _render_command_strip(command)

    col_left, col_mid, col_right = st.columns(3, gap="small")
    with st.spinner("Loading panels…"):
        paid = _load_paid_media()
        crm = _load_crm_funnel()
        traffic = _load_website_traffic()
    with col_left:
        _render_paid_media(paid)
    with col_mid:
        _render_crm_funnel(crm)
    with col_right:
        _render_website_traffic(traffic)

    col_a, col_b, col_c = st.columns(3, gap="small")
    with st.spinner("Loading lower panels…"):
        organic = _load_organic_social()
        content = _load_content_seo()
        team = _load_team_ops()
    with col_a:
        _render_organic_social(organic)
    with col_b:
        _render_content_seo(content)
    with col_c:
        _render_team_ops(team)

    col_needs, col_alerts = st.columns([3, 2], gap="small")
    with col_needs:
        _render_needs_response(_load_needs_response())
    with col_alerts:
        _render_alerts()

    if refresh_minutes > 0:
        st.caption(f"Auto-refresh every {refresh_minutes} min — not active yet.")


if __name__ == "__main__":
    main()
