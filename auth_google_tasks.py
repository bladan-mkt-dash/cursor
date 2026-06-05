"""One-time OAuth login for Google Tasks read-only (War Room alerts)."""

from __future__ import annotations

from google_tasks_client import CREDENTIALS_PATH, TASKS_TOKEN_PATH, get_credentials


def main() -> None:
    if not CREDENTIALS_PATH.exists():
        raise SystemExit(
            f"Missing OAuth client file: {CREDENTIALS_PATH}\n"
            "Create a Desktop app OAuth client in Google Cloud Console "
            "(same project as Google Sheets: cursor-marketing-dashboard)."
        )

    print("Opening browser for Google Tasks sign-in (read-only)...")
    print("If access is blocked, in Google Cloud Console:")
    print("  1. Enable the Google Tasks API for your project")
    print("  2. OAuth consent screen -> add your account as a Test user")
    print("  3. Data access -> add scope: .../auth/tasks.readonly")
    print()

    get_credentials(allow_interactive=True)
    print(f"\nSaved Tasks token to: {TASKS_TOKEN_PATH}")
    print("Run:  python verify_google_tasks_connection.py")


if __name__ == "__main__":
    main()
