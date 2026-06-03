"""Enable YouTube APIs on cursor-marketing-dashboard (requires project Owner/Editor)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

CONFIG_DIR = Path.home() / ".config" / "mcp-google-sheets"
CREDENTIALS_PATH = CONFIG_DIR / "credentials.json"
CLOUD_TOKEN_PATH = CONFIG_DIR / "cloud_token.json"
PROJECT = "996528452668"
APIS = ["youtube.googleapis.com", "youtubeanalytics.googleapis.com"]
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def main() -> None:
    if not CREDENTIALS_PATH.exists():
        raise SystemExit(f"Missing {CREDENTIALS_PATH}")

    print("Sign in with a Google account that can enable APIs on this project...")
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    CLOUD_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")

    svc = build("serviceusage", "v1", credentials=creds, cache_discovery=False)
    for api in APIS:
        name = f"projects/{PROJECT}/services/{api}"
        print(f"Enabling {api}...")
        op = svc.services().enable(name=name).execute()
        op_name = op.get("name")
        if op_name:
            for _ in range(30):
                status = svc.operations().get(name=op_name).execute()
                if status.get("done"):
                    break
                time.sleep(2)
        print(f"  {api}: requested")

    print('\nDone. Wait ~1 minute, then run:  python "EOM Updates/_fetch_youtube_may_tracker.py"')


if __name__ == "__main__":
    main()
