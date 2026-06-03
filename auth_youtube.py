"""One-time OAuth login for YouTube Data + Analytics (read-only)."""

from __future__ import annotations

from youtube_client import CREDENTIALS_PATH, YOUTUBE_TOKEN_PATH, get_credentials


def main() -> None:
    if not CREDENTIALS_PATH.exists():
        raise SystemExit(
            f"Missing OAuth client file: {CREDENTIALS_PATH}\n"
            "Create a Desktop app OAuth client in Google Cloud Console "
            "(same project as Google Sheets: cursor-marketing-dashboard)."
        )

    print("Opening browser for YouTube sign-in (read-only analytics)...")
    print("If access is blocked, in Google Cloud Console:")
    print("  1. Enable YouTube Data API v3 and YouTube Analytics API")
    print("  2. OAuth consent screen -> add your Google account as a Test user")
    print("  3. Data access -> add scopes:")
    print("       youtube.readonly")
    print("       yt-analytics.readonly")
    print("  4. Sign in with the Google account that owns the Five Journeys channel")
    print()

    get_credentials(allow_interactive=True)
    print(f"\nSaved YouTube token to: {YOUTUBE_TOKEN_PATH}")
    print('Run:  python "EOM Updates/_fetch_youtube_may_tracker.py"')


if __name__ == "__main__":
    main()
