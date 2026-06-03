"""Open Google Cloud + OAuth Playground pages for 5 Journeys brand-channel API access."""

from __future__ import annotations

import json
import webbrowser
from pathlib import Path

PROJECT = "996528452668"
CONFIG = Path.home() / ".config" / "mcp-google-sheets"
CREDENTIALS = CONFIG / "credentials.json"


def main() -> None:
    client_id = json.loads(CREDENTIALS.read_text(encoding="utf-8"))["installed"]["client_id"]
    print("5 Journeys YouTube Analytics requires OAuth as the **brand channel**, not a personal profile.\n")
    print("Step 1 — Create a WEB OAuth client (Desktop clients have no redirect URI field):")
    print(f"  https://console.cloud.google.com/apis/credentials?project={PROJECT}\n")
    print("  Click + Create credentials -> OAuth client ID")
    print("  Application type: Web application")
    print("  Name: YouTube OAuth Playground (or any name)")
    print("  Authorized redirect URIs, add:")
    print("    https://developers.google.com/oauthplayground")
    print("  Save, then copy that client's Client ID and Client secret.\n")
    print("  Put them in .env as:")
    print("    YOUTUBE_OAUTH_CLIENT_ID=...")
    print("    YOUTUBE_OAUTH_CLIENT_SECRET=...")
    print("  Use those (not the Desktop client) in OAuth Playground gear settings.\n")
    webbrowser.open(f"https://console.cloud.google.com/apis/credentials?project={PROJECT}")

    print("Step 2 — OAuth Playground (choose 5 Journeys brand when asked):")
    playground = "https://developers.google.com/oauthplayground/"
    print(f"  {playground}\n")
    print("  Scopes to authorize:")
    print("    https://www.googleapis.com/auth/youtube.readonly")
    print("    https://www.googleapis.com/auth/yt-analytics.readonly\n")
    webbrowser.open(playground)

    print("Step 3 — Copy the refresh token into .env:")
    print("  YOUTUBE_REFRESH_TOKEN=<paste refresh token>")
    print("  (plus YOUTUBE_OAUTH_CLIENT_ID/SECRET if you created a Web client)\n")
    print("Step 4 — Import and fetch:")
    print("  python import_youtube_refresh_token.py")
    print("  python verify_youtube_connection.py")
    print('  python "EOM Updates/_fetch_youtube_may_tracker.py"')


if __name__ == "__main__":
    main()
