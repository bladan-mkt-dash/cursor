"""
Scoped backfill for 2023 Digital Cross-Channel Tracker — only these cells:

  YouTube rows 59–63, columns H–S (Jan–Dec 2023)
  5J Website rows 89–92, column S (Dec 2023 only)
  5J Blog rows 108–113, column S (Dec 2023 only)

Row 107 is the "5J Blog" section header and is not written.

Run from project root:
  python "EOM Updates/backfill_2023_scoped.py"
  python "EOM Updates/backfill_2023_scoped.py" --dry-run
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
from pathlib import Path

from _bootstrap import setup

setup()

from google_data import (
    _ensure_ga_credentials,
    _strip_env,
    count_blog_posts_published,
    get_blog_stem_metrics,
)
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from youtube_client import fetch_channel_month_metrics, get_credentials

from _fetch_ga_website_tracker import (
    _fetch_month as _website_fetch_month,
    _fmt_bounce,
    _fmt_duration,
    _fmt_ratio,
)

SPREADSHEET_ID = "1vwj-ckQauOn22pxlXdt6kh63OVvkFjQeFBqdJ0k2QZg"
SHEET = "Monthly Tracker"
TRACKER_YEAR = 2023
JAN_COLUMN = "H"

YT_ROWS = (59, 60, 61, 62, 63)
BLOG_DEC_ROWS = (108, 109, 110, 111, 112, 113)
WEBSITE_DEC_ROWS = (89, 90, 91, 92)


def _sheets_service():
    token_path = Path.home() / ".config" / "mcp-google-sheets" / "token.json"
    info = json.loads(token_path.read_text(encoding="utf-8"))
    creds = Credentials.from_authorized_user_info(info, info["scopes"])
    return build("sheets", "v4", credentials=creds)


def write_cells(updates_by_col: dict[str, dict[int, str]]) -> None:
    sheets = _sheets_service()
    data = [
        {"range": f"'{SHEET}'!{col}{row}", "values": [[value]]}
        for col, row_values in sorted(updates_by_col.items())
        for row, value in sorted(row_values.items())
    ]
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()


def column_for_month(month: int) -> str:
    base = ord(JAN_COLUMN) - ord("A")
    return chr(ord("A") + base + month - 1)


def month_date_range(month: int) -> tuple[str, str]:
    last = calendar.monthrange(TRACKER_YEAR, month)[1]
    return f"{TRACKER_YEAR}-{month:02d}-01", f"{TRACKER_YEAR}-{month:02d}-{last:02d}"


def _fmt_int(n: int | float) -> str:
    return f"{int(round(n)):,}"


def _fmt_avg_watch(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    minutes, rem = divmod(s, 60)
    if rem:
        return f"{minutes}m {rem}s"
    return f"{minutes}m"


def _fmt_watch_hours(minutes: float) -> str:
    hours = minutes / 60.0
    if hours < 10 and abs(hours - round(hours, 1)) < 0.05:
        return f"{hours:.1f}h"
    if abs(hours - round(hours)) < 0.05:
        return str(int(round(hours)))
    return f"{hours:.1f}"


def _fmt_blog_bounce(rate: float) -> str:
    return f"{rate * 100:.1f}%"


def _fmt_blog_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def _youtube_updates(year: int, month: int, creds) -> dict[int, str]:
    metrics = fetch_channel_month_metrics(creds, year, month, legacy_layout=True)
    return {
        59: _fmt_int(metrics["videos_published"]),
        60: _fmt_int(metrics["new_subscribers"]),
        61: _fmt_int(metrics.get("engaged_views", metrics.get("views", 0))),
        62: _fmt_avg_watch(float(metrics.get("avg_view_seconds", 0))),
        63: _fmt_watch_hours(float(metrics.get("watch_minutes", 0))),
    }


def _website_dec_updates(start: str, end: str) -> dict[int, str]:
    property_id = _strip_env(os.getenv("GA4_PROPERTY_ID"))
    m = _website_fetch_month(property_id, start, end)
    return {
        89: _fmt_ratio(m.sessions_per_user),
        90: _fmt_ratio(m.views_per_session),
        91: _fmt_duration(m.avg_session_seconds),
        92: _fmt_bounce(m.bounce_rate),
    }


def _blog_dec_updates(start: str, end: str) -> dict[int, str]:
    ga = get_blog_stem_metrics(start, end)
    pageviews = ga["screenPageViews"]
    sessions = ga["sessions"]
    views_per_user = pageviews / sessions if sessions else 0.0
    try:
        blogs = count_blog_posts_published(start, end)
    except Exception as exc:
        print(f"  (blog published count unavailable: {exc})", file=sys.stderr)
        blogs = 0
    return {
        108: _fmt_int(blogs),
        109: _fmt_int(pageviews),
        110: _fmt_int(ga["activeUsers"]),
        111: _fmt_int(sessions),
        112: f"{views_per_user:.2f}",
        113: _fmt_blog_bounce(ga["bounceRate"]),
    }


def run(*, dry_run: bool = False) -> int:
    _ensure_ga_credentials()
    updates_by_col: dict[str, dict[int, str]] = {}

    print("=== YouTube rows 59–63, Jan–Dec 2023 (H–S) ===")
    creds = get_credentials(allow_interactive=False)
    yt_failed = 0
    for month in range(1, 13):
        col = column_for_month(month)
        print(f"  {TRACKER_YEAR}-{month:02d} → column {col}")
        try:
            updates_by_col[col] = _youtube_updates(TRACKER_YEAR, month, creds)
        except Exception as exc:
            print(f"    FAIL: {exc}", file=sys.stderr)
            yt_failed += 1
            continue
        for row in YT_ROWS:
            print(f"    {col}{row}: {updates_by_col[col][row]}")

    dec_col = column_for_month(12)
    start, end = month_date_range(12)

    print(f"\n=== 5J Website rows 89–92, Dec 2023 (column {dec_col}) ===")
    try:
        web = _website_dec_updates(start, end)
        updates_by_col.setdefault(dec_col, {}).update(web)
        for row in WEBSITE_DEC_ROWS:
            print(f"  {dec_col}{row}: {web[row]}")
    except Exception as exc:
        print(f"  FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"\n=== 5J Blog rows 108–113, Dec 2023 (column {dec_col}) ===")
    try:
        blog = _blog_dec_updates(start, end)
        updates_by_col.setdefault(dec_col, {}).update(blog)
        for row in BLOG_DEC_ROWS:
            print(f"  {dec_col}{row}: {blog[row]}")
    except Exception as exc:
        print(f"  FAIL: {exc}", file=sys.stderr)
        return 1

    n_cells = sum(len(v) for v in updates_by_col.values())
    print(f"\nTotal cells to write: {n_cells}")

    if dry_run:
        print("(dry-run: sheet not updated)")
        return 0 if yt_failed == 0 else 1

    write_cells(updates_by_col)
    print(f"Updated 2023 Digital Cross-Channel Tracker ({n_cells} cells).")
    if yt_failed:
        print(f"Warning: {yt_failed} YouTube month(s) failed.", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    return run(dry_run=parser.parse_args().dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
