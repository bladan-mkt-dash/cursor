"""Fetch GA4 5J Blog metrics (pagePath /blog*) for Mar–May 2026 → columns J–L, rows 108–114."""

from __future__ import annotations

import sys
from pathlib import Path

from _bootstrap import setup
from google_data import count_blog_posts_published, get_blog_stem_metrics

setup()

from tracker_config import column_for_month, month_date_range
from tracker_sheets import write_columns

ROW_BLOGS_PUBLISHED = 108
ROW_PAGEVIEWS = 109
ROW_USERS = 110
ROW_SESSIONS = 111
ROW_VIEWS_PER_USER = 112
ROW_AVG_SESSION_DURATION = 113
ROW_BOUNCE_RATE = 114

BACKFILL_MONTHS = ((2026, 3), (2026, 4), (2026, 5))


def _fmt_int(n: float) -> str:
    return f"{int(round(n)):,}"


def _fmt_ratio(n: float) -> str:
    return f"{n:.2f}"


def _fmt_bounce(rate: float) -> str:
    return f"{rate * 100:.1f}%"


def _fmt_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def _month_to_updates(start: str, end: str) -> dict[int, str]:
    ga = get_blog_stem_metrics(start, end)
    pageviews = ga["screenPageViews"]
    users = ga["activeUsers"]
    sessions = ga["sessions"]
    views_per_user = pageviews / sessions if sessions else 0.0
    blogs = count_blog_posts_published(start, end)
    return {
        ROW_BLOGS_PUBLISHED: _fmt_int(blogs),
        ROW_PAGEVIEWS: _fmt_int(pageviews),
        ROW_USERS: _fmt_int(users),
        ROW_SESSIONS: _fmt_int(sessions),
        ROW_VIEWS_PER_USER: _fmt_ratio(views_per_user),
        ROW_AVG_SESSION_DURATION: _fmt_duration(ga["averageSessionDuration"]),
        ROW_BOUNCE_RATE: _fmt_bounce(ga["bounceRate"]),
    }


def run_month(year: int, month: int, *, dry_run: bool = False) -> int:
    col = column_for_month(year, month)
    start, end = month_date_range(year, month)
    print(f"Fetching 5J Blog (/blog*) for {year}-{month:02d} ({start} .. {end})...")
    updates = _month_to_updates(start, end)
    print(
        f"  published={updates[ROW_BLOGS_PUBLISHED]} pageviews={updates[ROW_PAGEVIEWS]} "
        f"users={updates[ROW_USERS]} sessions={updates[ROW_SESSIONS]}"
    )
    for row in range(108, 115):
        print(f"  {col}{row}: {updates[row]}")

    if dry_run:
        print("(dry-run: sheet not updated)")
        return 0

    write_columns({col: updates})
    print(f"Updated 5J Blog rows 108–114, column {col}.")
    return 0


def main() -> int:
    if "--backfill-jkl" in sys.argv:
        updates_by_col: dict[str, dict[int, str]] = {}
        for year, month in BACKFILL_MONTHS:
            start, end = month_date_range(year, month)
            col = column_for_month(year, month)
            print(f"Fetching 5J Blog for {year}-{month:02d}...")
            updates_by_col[col] = _month_to_updates(start, end)
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
