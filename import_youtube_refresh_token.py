"""Import a YouTube refresh token (e.g. from OAuth Playground) into youtube_token.json."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from youtube_client import CONFIG_DIR, CREDENTIALS_PATH, SCOPES, YOUTUBE_TOKEN_PATH

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")


def main() -> None:
    refresh = (os.getenv("YOUTUBE_REFRESH_TOKEN") or "").strip()
    if not refresh:
        raise SystemExit(
            "Set YOUTUBE_REFRESH_TOKEN in .env (from OAuth Playground after choosing "
            "the **5 Journeys** brand account)."
        )
    client_id = (os.getenv("YOUTUBE_OAUTH_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("YOUTUBE_OAUTH_CLIENT_SECRET") or "").strip()
    token_uri = "https://oauth2.googleapis.com/token"

    if not client_id or not client_secret:
        if not CREDENTIALS_PATH.exists():
            raise SystemExit(
                f"Missing {CREDENTIALS_PATH}\n"
                "Or set YOUTUBE_OAUTH_CLIENT_ID and YOUTUBE_OAUTH_CLIENT_SECRET in .env "
                "(from a Web application OAuth client used in OAuth Playground)."
            )
        installed = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))["installed"]
        client_id = installed["client_id"]
        client_secret = installed["client_secret"]
        token_uri = installed["token_uri"]

    creds = Credentials(
        None,
        refresh_token=refresh,
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    YOUTUBE_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    print(f"Saved token to {YOUTUBE_TOKEN_PATH}")
    print("Run:  python verify_youtube_connection.py")


if __name__ == "__main__":
    main()
