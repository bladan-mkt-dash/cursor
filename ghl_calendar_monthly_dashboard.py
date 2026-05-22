"""
GHL calendar monthly dashboard — booking month + meeting month tables with bar charts.

From the project directory:

    streamlit run ghl_calendar_monthly_dashboard.py

Then open (prefer 127.0.0.1 over localhost if the browser fails):

    http://127.0.0.1:8501/

July 2025 is excluded from both tables (partial month: GHL data starts ~25 Jul 2025).
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from dotenv import load_dotenv

from _ghl_calendar_pageviews_report import (
    GA4_END_DATE,
    GA4_START_DATE,
    HOME_PATH,
    discover_embed_pages,
    ga4_monthly_views,
)

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

EXCLUDE_MONTH = "2025-07"
GHL_API_VERSION = "2021-04-15"
GHL_BASE = "https://services.leadconnectorhq.com"
GHL_START = datetime(2025, 7, 25, tzinfo=timezone.utc)

COLORS = {
    "includes_home": "#5DA68A",
    "excludes_home": "#4C78A8",
    "bookings": "#F58518",
    "confirmed": "#5DA68A",
    "cancelled": "#E45756",
    "noshow": "#B279A2",
    "total": "#264540",
}


def _month_label(ym: str) -> str:
    return datetime.strptime(ym, "%Y-%m").strftime("%b %Y")


def _ghl_headers() -> dict[str, str]:
    token = (
        os.getenv("GHL_ACCESS_TOKEN")
        or os.getenv("GHL_PRIVATE_INTEGRATION_TOKEN")
        or os.getenv("GHL_API_KEY")
        or ""
    ).strip()
    if not token:
        raise ValueError(
            "Set GHL_ACCESS_TOKEN, GHL_PRIVATE_INTEGRATION_TOKEN, or GHL_API_KEY in .env"
        )
    return {
        "Authorization": f"Bearer {token}",
        "Version": GHL_API_VERSION,
        "Accept": "application/json",
    }


def _confirmed_count(status_counter: Counter) -> int:
    return sum(
        status_counter.get(s, 0)
        for s in ("confirmed", "showed", "completed", "active", "new")
    )


@st.cache_data(ttl=3600, show_spinner=False)
def load_embed_paths() -> list[str]:
    pages = discover_embed_pages()
    return [p["path"] for p in pages]


@st.cache_data(ttl=3600, show_spinner=False)
def load_ga4_views(embed_paths: tuple[str, ...]) -> tuple[dict[str, int], dict[str, int]]:
    paths = list(embed_paths)
    excludes = ga4_monthly_views(paths)
    includes = ga4_monthly_views(paths + [HOME_PATH])
    return includes, excludes


@st.cache_data(ttl=3600, show_spinner=False)
def load_ghl_appointments() -> dict:
    loc = (os.getenv("GHL_LOCATION_ID") or "").strip()
    if not loc:
        raise ValueError("Set GHL_LOCATION_ID in .env")

    headers = _ghl_headers()
    end = datetime.now(timezone.utc)
    start_ms = str(int(GHL_START.timestamp() * 1000))
    end_ms = str(int(end.timestamp() * 1000))

    r = requests.get(
        f"{GHL_BASE}/calendars/",
        params={"locationId": loc},
        headers=headers,
        timeout=60,
    )
    r.raise_for_status()
    calendars = r.json().get("calendars") or []

    seen_ids: set[str] = set()
    events: list[dict] = []
    api_errors = 0
    for cal in calendars:
        r2 = requests.get(
            f"{GHL_BASE}/calendars/events",
            params={
                "locationId": loc,
                "calendarId": cal["id"],
                "startTime": start_ms,
                "endTime": end_ms,
            },
            headers=headers,
            timeout=90,
        )
        if not r2.ok:
            api_errors += 1
            continue
        for ev in r2.json().get("events") or []:
            eid = ev.get("id")
            if eid and str(eid) not in seen_ids:
                seen_ids.add(str(eid))
                events.append(ev)

    by_meeting_month: Counter[str] = Counter()
    by_status_month: dict[str, Counter] = defaultdict(Counter)
    by_booked_month: Counter[str] = Counter()
    deleted_count = 0

    for ev in events:
        if ev.get("deleted"):
            deleted_count += 1
            continue
        start_time = ev.get("startTime")
        if not start_time:
            continue
        meeting_month = str(start_time)[:7]
        by_meeting_month[meeting_month] += 1
        status = ev.get("appointmentStatus") or "unknown"
        by_status_month[meeting_month][status] += 1
        date_added = ev.get("dateAdded")
        if date_added:
            by_booked_month[str(date_added)[:7]] += 1

    return {
        "calendar_count": len(calendars),
        "event_count": len(events),
        "api_errors": api_errors,
        "deleted_count": deleted_count,
        "by_meeting_month": dict(by_meeting_month),
        "by_status_month": {k: dict(v) for k, v in by_status_month.items()},
        "by_booked_month": dict(by_booked_month),
    }


def _months_after_exclusion(*month_sets: dict) -> list[str]:
    months: set[str] = set()
    for data in month_sets:
        months.update(data.keys())
    return sorted(m for m in months if m >= "2025-07" and m != EXCLUDE_MONTH)


def build_booking_month_df(
    views_includes: dict[str, int],
    views_excludes: dict[str, int],
    bookings_by_month: dict[str, int],
) -> pd.DataFrame:
    rows = []
    for ym in _months_after_exclusion(views_includes, views_excludes, bookings_by_month):
        rows.append(
            {
                "month_key": ym,
                "Month": _month_label(ym),
                "Page views (Includes Home)": views_includes.get(ym, 0),
                "Page views (Excludes Home)": views_excludes.get(ym, 0),
                "Bookings": bookings_by_month.get(ym, 0),
            }
        )
    return pd.DataFrame(rows)


def build_meeting_month_df(ghl: dict) -> pd.DataFrame:
    by_meeting = ghl["by_meeting_month"]
    by_status = ghl["by_status_month"]
    rows = []
    for ym in _months_after_exclusion(by_meeting):
        sc = Counter(by_status.get(ym, {}))
        confirmed = _confirmed_count(sc)
        cancelled = sc.get("cancelled", 0)
        noshow = sc.get("noshow", 0)
        total = by_meeting.get(ym, 0)
        rows.append(
            {
                "month_key": ym,
                "Month": _month_label(ym),
                "Total": total,
                "confirmed": confirmed,
                "cancelled": cancelled,
                "noshow": noshow,
            }
        )
    return pd.DataFrame(rows)


def _booking_pageviews_chart(df: pd.DataFrame) -> go.Figure:
    long_df = df.melt(
        id_vars=["Month"],
        value_vars=["Page views (Includes Home)", "Page views (Excludes Home)"],
        var_name="Metric",
        value_name="Page views",
    )
    fig = px.bar(
        long_df,
        x="Month",
        y="Page views",
        color="Metric",
        barmode="group",
        title="Monthly page views (GHL embed pages)",
        color_discrete_map={
            "Page views (Includes Home)": COLORS["includes_home"],
            "Page views (Excludes Home)": COLORS["excludes_home"],
        },
        category_orders={"Month": df["Month"].tolist()},
    )
    fig.update_layout(
        xaxis_title=None,
        yaxis_title="Page views",
        legend_title=None,
        margin=dict(t=50, b=40),
    )
    return fig


def _booking_counts_chart(df: pd.DataFrame) -> go.Figure:
    fig = px.bar(
        df,
        x="Month",
        y="Bookings",
        title="Monthly bookings (GHL dateAdded)",
        color_discrete_sequence=[COLORS["bookings"]],
        category_orders={"Month": df["Month"].tolist()},
    )
    fig.update_layout(xaxis_title=None, yaxis_title="Bookings", margin=dict(t=50, b=40))
    return fig


def _meeting_status_chart(df: pd.DataFrame) -> go.Figure:
    long_df = df.melt(
        id_vars=["Month"],
        value_vars=["confirmed", "cancelled", "noshow"],
        var_name="Status",
        value_name="Count",
    )
    fig = px.bar(
        long_df,
        x="Month",
        y="Count",
        color="Status",
        barmode="stack",
        title="Appointments by meeting month and status",
        color_discrete_map={
            "confirmed": COLORS["confirmed"],
            "cancelled": COLORS["cancelled"],
            "noshow": COLORS["noshow"],
        },
        category_orders={"Month": df["Month"].tolist()},
    )
    fig.update_layout(
        xaxis_title=None,
        yaxis_title="Appointments",
        legend_title=None,
        margin=dict(t=50, b=40),
    )
    return fig


def _format_table(df: pd.DataFrame, numeric_cols: list[str]) -> pd.DataFrame:
    out = df.drop(columns=["month_key"], errors="ignore").copy()
    for col in numeric_cols:
        out[col] = out[col].map(lambda x: f"{int(x):,}")
    return out


def main() -> None:
    st.set_page_config(
        page_title="GHL Calendar Monthly Dashboard",
        layout="wide",
    )
    st.title("GHL Calendar Monthly Dashboard")
    st.caption(
        f"GHL appointments from **25 Jul 2025** through today across all calendars. "
        f"GA4 page views: **{GA4_START_DATE}** – **{GA4_END_DATE}**. "
        f"**{EXCLUDE_MONTH} ({_month_label(EXCLUDE_MONTH)}) is excluded** from both tables "
        "(partial month / outlier)."
    )

    try:
        with st.spinner("Discovering GHL embed pages on fivejourneys.com…"):
            embed_paths = load_embed_paths()
        with st.spinner("Fetching GA4 monthly page views…"):
            views_includes, views_excludes = load_ga4_views(tuple(embed_paths))
        with st.spinner("Fetching GHL calendar appointments (42 calendars)…"):
            ghl = load_ghl_appointments()
    except Exception as e:
        st.error(str(e))
        st.stop()

    booking_df = build_booking_month_df(
        views_includes, views_excludes, ghl["by_booked_month"]
    )
    meeting_df = build_meeting_month_df(ghl)

    st.markdown("---")
    st.subheader("By Booking Month")
    st.caption(
        "Page views = GA4 `screenPageViews` summed across "
        f"**{len(embed_paths)}** embed pages "
        f"(Includes Home adds `{HOME_PATH}`). "
        "Bookings = GHL appointments by **dateAdded**."
    )
    st.dataframe(
        _format_table(
            booking_df,
            ["Page views (Includes Home)", "Page views (Excludes Home)", "Bookings"],
        ),
        use_container_width=True,
        hide_index=True,
    )
    col_a, col_b = st.columns(2)
    with col_a:
        st.plotly_chart(_booking_pageviews_chart(booking_df), use_container_width=True)
    with col_b:
        st.plotly_chart(_booking_counts_chart(booking_df), use_container_width=True)

    st.markdown("---")
    st.subheader("By Meeting Month")
    st.caption(
        "Appointments grouped by scheduled **startTime**. "
        "**confirmed** includes confirmed, showed, completed, active, and new statuses."
    )
    st.dataframe(
        _format_table(meeting_df, ["Total", "confirmed", "cancelled", "noshow"]),
        use_container_width=True,
        hide_index=True,
    )
    st.plotly_chart(_meeting_status_chart(meeting_df), use_container_width=True)

    with st.expander("Data notes"):
        st.markdown(
            f"- **Calendars:** {ghl['calendar_count']} · **Unique appointments:** {ghl['event_count']:,}\n"
            f"- **Excluded month:** {_month_label(EXCLUDE_MONTH)} (partial GHL window from 25 Jul)\n"
            f"- **Deleted events skipped:** {ghl['deleted_count']:,}\n"
            f"- **Calendar API errors:** {ghl['api_errors']}"
        )


if __name__ == "__main__":
    main()
