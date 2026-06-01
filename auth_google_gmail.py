"""One-time OAuth login for Gmail read-only access (weekly inbox/sent summary)."""

from __future__ import annotations

from gmail_client import CREDENTIALS_PATH, GMAIL_TOKEN_PATH, get_credentials


def main() -> None:
    if not CREDENTIALS_PATH.exists():
        raise SystemExit(
            f"Missing OAuth client file: {CREDENTIALS_PATH}\n"
            "Create a Desktop app OAuth client in Google Cloud Console "
            "(same project as Google Sheets: cursor-marketing-dashboard)."
        )

    print("Opening browser for Gmail sign-in (read-only)...")
    print("If access is blocked, in Google Cloud Console:")
    print("  1. Enable the Gmail API for your project")
    print("  2. OAuth consent screen -> add your account as a Test user")
    print("  3. Data access -> add scope: .../auth/gmail.readonly")
    print()

    get_credentials(allow_interactive=True)
    print(f"\nSaved Gmail token to: {GMAIL_TOKEN_PATH}")
    print("Run:  python verify_gmail_connection.py")
    print("Then: python gmail_weekly_summary.py")


if __name__ == "__main__":
    main()
