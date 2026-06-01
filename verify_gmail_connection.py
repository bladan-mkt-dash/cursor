"""Quick check that Gmail OAuth token works."""

from __future__ import annotations

from gmail_client import gmail_service


def main() -> None:
    service = gmail_service()
    profile = service.users().getProfile(userId="me").execute()
    email = profile.get("emailAddress", "(unknown)")
    total = profile.get("messagesTotal", "?")
    print(f"OK: Gmail authenticated as {email}")
    print(f"    Approximate mailbox size: {total} messages")


if __name__ == "__main__":
    main()
