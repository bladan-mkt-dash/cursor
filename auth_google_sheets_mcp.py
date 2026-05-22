"""One-time OAuth login for the Cursor Google Sheets MCP server."""

from __future__ import annotations

from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

CONFIG_DIR = Path.home() / ".config" / "mcp-google-sheets"
CREDENTIALS_PATH = CONFIG_DIR / "credentials.json"
TOKEN_PATH = CONFIG_DIR / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def main() -> None:
    if not CREDENTIALS_PATH.exists():
        raise SystemExit(
            f"Missing OAuth client file: {CREDENTIALS_PATH}\n"
            "Create it from Google Cloud Console (Desktop app OAuth client)."
        )

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    print("Opening browser for Google sign-in...")
    print("If you see 'Access blocked' or 'access_denied', check Google Cloud Console:")
    print("  1. OAuth consent screen -> add your Google account as a Test user")
    print("  2. Data access -> add scopes for Google Drive + Google Sheets")
    print("  3. Use the same project: cursor-marketing-dashboard")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    try:
        credentials = flow.run_local_server(port=0, open_browser=True)
    except Exception as exc:
        raise SystemExit(
            f"OAuth login failed: {exc}\n"
            "Complete the Google Cloud consent-screen setup above, then run this script again."
        ) from exc

    TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")
    print(f"\nSaved token to: {TOKEN_PATH}")
    print("Restart Cursor, then use Agent mode to access Google Sheets/Drive.")


if __name__ == "__main__":
    main()
