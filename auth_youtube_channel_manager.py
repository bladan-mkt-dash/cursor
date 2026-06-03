"""Re-authorize YouTube with the Google account that manages the 5 Journeys channel."""

from __future__ import annotations

from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

import sys

from youtube_client import CREDENTIALS_PATH, SCOPES, YOUTUBE_TOKEN_PATH


def main() -> None:
    if "--force" in sys.argv and YOUTUBE_TOKEN_PATH.exists():
        YOUTUBE_TOKEN_PATH.unlink()
        print("Removed existing YouTube token.\n")
    if not CREDENTIALS_PATH.exists():
        raise SystemExit(f"Missing OAuth client: {CREDENTIALS_PATH}")

    print("Use --force to replace an existing token.\n")
    print("Sign in with a Google account that is an **Owner** on the 5 Journeys brand")
    print("(Managers often cannot use the Analytics API).")
    print("When Google shows a YouTube channel picker, choose **5 Journeys**,")
    print("not your personal channel.\n")
    print("If the picker never appears, use OAuth Playground instead:")
    print("  1. Add https://developers.google.com/oauthplayground to OAuth redirect URIs")
    print("  2. Authorize yt-analytics.readonly + youtube.readonly as the 5 Journeys brand")
    print("  3. Set YOUTUBE_REFRESH_TOKEN in .env and run import_youtube_refresh_token.py\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    YOUTUBE_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    print(f"\nSaved: {YOUTUBE_TOKEN_PATH}")
    print("Run:  python verify_youtube_connection.py")
    print('Then: python "EOM Updates/_fetch_youtube_may_tracker.py"')


if __name__ == "__main__":
    main()
