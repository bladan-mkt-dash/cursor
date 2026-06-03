"""Fetch Five Journeys YouTube metrics into the 2026 cross-channel tracker."""

from __future__ import annotations

import sys

from _bootstrap import setup
from googleapiclient.errors import HttpError

setup()

from tracker_config import column_for_month
from tracker_sheets import write_column
from youtube_client import fetch_channel_month_metrics, get_credentials

YT_ROWS = {
    56: "videos_published",
    57: "views",
    58: "engaged_views",
    59: "likes",
    60: "new_subscribers",
}


def _fmt(n: int) -> str:
    return f"{n:,}"


def run_month(year: int, month: int, *, dry_run: bool = False) -> int:
    col = column_for_month(year, month)
    creds = get_credentials(allow_interactive="--auth" in sys.argv)
    print(f"Fetching Five Journeys YouTube for {year}-{month:02d} (column {col})…")
    try:
        metrics = fetch_channel_month_metrics(creds, year, month)
    except HttpError as exc:
        if exc.resp.status == 403 and "accessNotConfigured" in str(exc):
            print(
                "\nYouTube APIs are not enabled for project cursor-marketing-dashboard.\n"
                "Run:  python enable_youtube_apis.py"
            )
            return 1
        if exc.resp.status == 403:
            print(
                "\nYouTube Analytics Forbidden — use channel Owner account.\n"
                "  python auth_youtube_channel_manager.py --force"
            )
            return 1
        raise
    print(f"Channel: {metrics['channel_id']}")

    updates = {row: _fmt(metrics[key]) for row, key in YT_ROWS.items()}
    for row in range(56, 61):
        print(f"  {col}{row}: {updates[row]}")

    if dry_run:
        print("(dry-run: sheet not updated)")
        return 0

    write_column(col, updates)
    print(f"Updated YouTube rows 56–60, column {col}.")
    return 0


def main() -> int:
    from tracker_config import parse_month_arg

    year, month = parse_month_arg("2026-05")
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--month" and i < len(sys.argv) - 1:
            year, month = parse_month_arg(sys.argv[i + 1])
            break
    dry = "--dry-run" in sys.argv
    return run_month(year, month, dry_run=dry)


if __name__ == "__main__":
    raise SystemExit(main())
