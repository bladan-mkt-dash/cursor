"""Quick check that Google Sheets/Drive OAuth token works."""

from __future__ import annotations

import json
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_PATH = Path.home() / ".config" / "mcp-google-sheets" / "token.json"


def main() -> None:
    info = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    creds = Credentials.from_authorized_user_info(info, info["scopes"])
    drive = build("drive", "v3", credentials=creds)
    result = (
        drive.files()
        .list(
            q="mimeType='application/vnd.google-apps.spreadsheet'",
            pageSize=5,
            fields="files(id,name)",
        )
        .execute()
    )
    files = result.get("files", [])
    print(f"OK: authenticated; found {len(files)} spreadsheet(s)")
    for f in files:
        print(f"  - {f['name']}")


if __name__ == "__main__":
    main()
