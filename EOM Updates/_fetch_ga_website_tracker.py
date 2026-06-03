"""Fetch GA4 5J Website metrics for Mar–May 2026 and write columns J–L on 2026 tracker."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from _bootstrap import setup
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)

from google_data import _ensure_ga_credentials, _run_report_paginated, _strip_env

setup()

from tracker_config import column_for_month, month_date_range
from tracker_sheets import write_columns

# 5J Website section (Monthly Tracker) — row numbers from sheet layout
ROW_UNIQUE_PAGEVIEWS = 83
ROW_USERS = 84
ROW_SESSIONS = 85
ROW_SESSIONS_PER_USER = 86
ROW_VIEWS_PER_SESSION = 87
ROW_SCROLLED_USERS = 88
ROW_AVG_SESSION_DURATION = 89
ROW_BOUNCE_RATE = 90
# ROW 91 = traffic section header (skip)
ROW_ORGANIC_SEARCH = 92
ROW_DIRECT = 93
ROW_DISPLAY = 94
ROW_CROSS_NETWORK = 95
ROW_EMAIL = 96
ROW_REFERRAL = 97
ROW_PAID_SOCIAL = 98
ROW_ORGANIC_SOCIAL = 99
ROW_PAID_SEARCH = 100
ROW_PAID_ORGANIC_OTHER = 101

# Rows 102–107 / 115 were accidentally written by an earlier row-offset bug; clear J–L.
# Rows 108–114 are 5J Blog (see _fetch_ga_blog_tracker.py) — do not clear here.
CLEAR_JKL_ROWS = tuple(range(102, 108)) + (115,)

BACKFILL_MONTHS = ((2026, 3), (2026, 4), (2026, 5))

# Sheet label -> GA4 sessionDefaultChannelGroup (exact)
CHANNEL_MAP: dict[int, str] = {
    ROW_ORGANIC_SEARCH: "Organic Search",
    ROW_DIRECT: "Direct",
    ROW_DISPLAY: "Display",
    ROW_CROSS_NETWORK: "Cross-network",
    ROW_EMAIL: "Email",
    ROW_REFERRAL: "Referral",
    ROW_PAID_SOCIAL: "Paid Social",
    ROW_ORGANIC_SOCIAL: "Organic Social",
    ROW_PAID_SEARCH: "Paid Search",
    ROW_PAID_ORGANIC_OTHER: "Paid Other",
}


@dataclass
class MonthMetrics:
    unique_pageviews: int
    users: int
    sessions: int
    sessions_per_user: float
    views_per_session: float
    scrolled_users: int
    avg_session_seconds: float
    bounce_rate: float
    channel_sessions: dict[str, int]


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _fmt_ratio(n: float) -> str:
    return f"{n:.2f}"


def _fmt_bounce(rate: float) -> str:
    return f"{rate * 100:.2f}%"


def _fmt_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def _fetch_month(property_id: str, start: str, end: str) -> MonthMetrics:
    client = BetaAnalyticsDataClient()
    totals_req = RunReportRequest(
        property=f"properties/{property_id}",
        metrics=[
            Metric(name="screenPageViews"),
            Metric(name="activeUsers"),
            Metric(name="sessions"),
            Metric(name="sessionsPerUser"),
            Metric(name="screenPageViewsPerSession"),
            Metric(name="scrolledUsers"),
            Metric(name="averageSessionDuration"),
            Metric(name="bounceRate"),
        ],
        date_ranges=[DateRange(start_date=start, end_date=end)],
    )
    totals_resp = client.run_report(totals_req)
    if not totals_resp.rows:
        raise RuntimeError(f"No GA4 totals for {start}..{end}")

    mv = totals_resp.rows[0].metric_values
    channel_rows = _run_report_paginated(
        client,
        property_id=property_id,
        dimensions=[Dimension(name="sessionDefaultChannelGroup")],
        metrics=[Metric(name="sessions")],
        start_date=start,
        end_date=end,
    )
    channel_sessions: dict[str, int] = {}
    for row in channel_rows:
        ch = (row.dimension_values[0].value if row.dimension_values else "") or ""
        channel_sessions[ch] = int(row.metric_values[0].value)

    return MonthMetrics(
        unique_pageviews=int(mv[0].value),
        users=int(mv[1].value),
        sessions=int(mv[2].value),
        sessions_per_user=float(mv[3].value),
        views_per_session=float(mv[4].value),
        scrolled_users=int(mv[5].value),
        avg_session_seconds=float(mv[6].value),
        bounce_rate=float(mv[7].value),
        channel_sessions=channel_sessions,
    )


def _month_to_updates(metrics: MonthMetrics) -> dict[int, str]:
    updates: dict[int, str] = {
        ROW_UNIQUE_PAGEVIEWS: _fmt_int(metrics.unique_pageviews),
        ROW_USERS: _fmt_int(metrics.users),
        ROW_SESSIONS: _fmt_int(metrics.sessions),
        ROW_SESSIONS_PER_USER: _fmt_ratio(metrics.sessions_per_user),
        ROW_VIEWS_PER_SESSION: _fmt_ratio(metrics.views_per_session),
        ROW_SCROLLED_USERS: _fmt_int(metrics.scrolled_users),
        ROW_AVG_SESSION_DURATION: _fmt_duration(metrics.avg_session_seconds),
        ROW_BOUNCE_RATE: _fmt_bounce(metrics.bounce_rate),
    }
    for row, ga_channel in CHANNEL_MAP.items():
        updates[row] = _fmt_int(metrics.channel_sessions.get(ga_channel, 0))
    return updates


def run_month(year: int, month: int, *, dry_run: bool = False) -> int:
    _ensure_ga_credentials()
    property_id = _strip_env(__import__("os").getenv("GA4_PROPERTY_ID"))
    if not property_id:
        raise SystemExit("Set GA4_PROPERTY_ID in .env")

    col = column_for_month(year, month)
    start, end = month_date_range(year, month)
    print(f"Fetching GA4 5J Website for {year}-{month:02d} ({start} .. {end})...")
    metrics = _fetch_month(property_id, start, end)
    updates = _month_to_updates(metrics)
    print(
        f"  pageviews={metrics.unique_pageviews:,} users={metrics.users:,} "
        f"sessions={metrics.sessions:,}"
    )
    for row in range(ROW_UNIQUE_PAGEVIEWS, ROW_PAID_ORGANIC_OTHER + 1):
        if row == 91:
            continue
        print(f"  {col}{row}: {updates[row]}")

    if dry_run:
        print("(dry-run: sheet not updated)")
        return 0

    write_columns({col: updates})
    print(f"Updated 5J Website rows 83–101, column {col}.")
    return 0


def main() -> int:
    if "--backfill-jkl" in sys.argv:
        _ensure_ga_credentials()
        property_id = _strip_env(__import__("os").getenv("GA4_PROPERTY_ID"))
        if not property_id:
            raise SystemExit("Set GA4_PROPERTY_ID in .env")
        updates_by_col: dict[str, dict[int, str]] = {}
        for year, month in BACKFILL_MONTHS:
            start, end = month_date_range(year, month)
            col = column_for_month(year, month)
            print(f"Fetching GA4 5J Website for {year}-{month:02d}...")
            updates_by_col[col] = _month_to_updates(_fetch_month(property_id, start, end))
        if "--dry-run" in sys.argv:
            return 0
        write_columns(updates_by_col)
        print("Backfill complete (columns J–L).")
        return 0

    from tracker_config import parse_month_arg

    year, month = parse_month_arg("2026-05")
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--month" and i < len(sys.argv) - 1:
            year, month = parse_month_arg(sys.argv[i + 1])
            break
    return run_month(year, month, dry_run="--dry-run" in sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
