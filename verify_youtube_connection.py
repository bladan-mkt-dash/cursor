"""Verify YouTube OAuth can read 5 Journeys channel analytics."""

from __future__ import annotations

from datetime import date

from googleapiclient.errors import HttpError

from youtube_client import (
    channel_id,
    fetch_channel_month_analytics,
    get_credentials,
    resolve_channel_id,
    _youtube_data,
)


def main() -> None:
    creds = get_credentials()
    channel = resolve_channel_id(creds)
    yt = _youtube_data(creds)
    info = (
        yt.channels()
        .list(part="snippet,statistics", id=channel)
        .execute()
        .get("items", [{}])[0]
    )
    title = info.get("snippet", {}).get("title", "?")
    print(f"Channel: {title} ({channel})")
    if channel != channel_id():
        print(f"  (configured default: {channel_id()})")

    mine = yt.channels().list(part="snippet", mine=True, maxResults=10).execute()
    managed = [c["snippet"]["title"] for c in mine.get("items", [])]
    print(f"Authenticated user's channels (mine): {managed or '(none)'}")

    try:
        metrics = fetch_channel_month_analytics(
            creds, channel, date(2026, 5, 1), date(2026, 5, 31)
        )
        print("May 2026 analytics OK:", metrics)
    except HttpError as exc:
        print("Analytics FAILED:", exc)
        print(
            "\nRe-run with the Studio manager account:\n"
            "  python auth_youtube_channel_manager.py"
        )
        raise SystemExit(1) from exc

    print('\nOK — run:  python "EOM Updates/_fetch_youtube_may_tracker.py"')


if __name__ == "__main__":
    main()
