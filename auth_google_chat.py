"""One-time OAuth login for Google Chat read-only (verify / scripts)."""

from __future__ import annotations

from verify_google_chat_connection import CHAT_TOKEN_PATH, get_chat_credentials


def main() -> None:
    print("Opening browser for Google Chat sign-in (read-only scopes)…")
    print("In Google Cloud Console, ensure Google Chat API is enabled and scopes are on")
    print("the OAuth consent screen for this OAuth client.")
    print()
    get_chat_credentials(allow_interactive=True)
    print(f"\nSaved Chat token to: {CHAT_TOKEN_PATH}")
    print("Run:  python verify_google_chat_connection.py")


if __name__ == "__main__":
    main()
