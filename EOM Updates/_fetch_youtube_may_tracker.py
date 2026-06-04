"""Fetch Five Journeys YouTube metrics into the cross-channel tracker."""

from __future__ import annotations

import sys

from _bootstrap import setup
from googleapiclient.errors import HttpError

setup()

from tracker_config import active_layout, column_for_month
from tracker_sheets import write_column
from youtube_client import fetch_channel_month_metrics, get_credentials


def _fmt(n: int) -> str:
    return f"{n:,}"


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


def _build_updates(metrics: dict) -> dict[int, str]:
    yt = active_layout().youtube
    lay = active_layout()
    views_key = lay.youtube_views_key
    updates: dict[int, str] = {
        yt.videos_published: _fmt(int(metrics["videos_published"])),
        yt.views: _fmt(int(metrics.get(views_key, metrics.get("views", 0)))),
        yt.new_subscribers: _fmt(int(metrics["new_subscribers"])),
    }
    if yt.engaged_views is not None:
        updates[yt.engaged_views] = _fmt(int(metrics.get("engaged_views", 0)))
    if yt.likes is not None:
        updates[yt.likes] = _fmt(int(metrics.get("likes", 0)))
    if yt.avg_watch_duration is not None:
        updates[yt.avg_watch_duration] = _fmt_avg_watch(
            float(metrics.get("avg_view_seconds", 0))
        )
    if yt.watch_hours is not None:
        updates[yt.watch_hours] = _fmt_watch_hours(
            float(metrics.get("watch_minutes", 0))
        )
    return updates


def run_month(year: int, month: int, *, dry_run: bool = False) -> int:
    col = column_for_month(year, month)
    yt = active_layout().youtube
    creds = get_credentials(allow_interactive="--auth" in sys.argv)
    print(f"Fetching Five Journeys YouTube for {year}-{month:02d} (column {col})…")
    try:
        metrics = fetch_channel_month_metrics(
            creds, year, month, legacy_layout=yt.legacy
        )
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

    updates = _build_updates(metrics)
    for row in sorted(updates):
        print(f"  {col}{row}: {updates[row]}")

    if dry_run:
        print("(dry-run: sheet not updated)")
        return 0

    write_column(col, updates)
    print(f"Updated YouTube ({len(updates)} cells), column {col}.")
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
