"""List Pages and linked Instagram business accounts via GET /me/accounts (debug / discovery)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

GRAPH_API_VERSION = "v21.0"


def main() -> int:
    # Prefer a System User token in .env; fall back to your user token.
    token = (
        (os.getenv("META_SYSTEM_USER_TOKEN") or os.getenv("META_USER_ACCESS_TOKEN") or "")
        .strip()
    )
    if not token:
        print(
            "Set META_SYSTEM_USER_TOKEN or META_USER_ACCESS_TOKEN in .env.",
            file=sys.stderr,
        )
        return 1

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/accounts"
    params = {
        "fields": "name,instagram_business_account",
        "access_token": token,
    }

    print("Fetching data from Meta...")
    response = requests.get(url, params=params, timeout=30)
    print(json.dumps(response.json(), indent=2))
    return 0 if response.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
