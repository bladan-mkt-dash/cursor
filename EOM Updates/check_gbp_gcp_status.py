"""Check Google Business Profile API enablement and quota hints on cursor-marketing-dashboard."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from _bootstrap import setup

setup()

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

PROJECT_ID = "996528452668"
PROJECT_NAME = "cursor-marketing-dashboard"
TOKEN_PATH = Path.home() / ".config" / "mcp-google-sheets" / "cloud_token.json"

# Eight GBP APIs per https://developers.google.com/my-business/content/basic-setup
GBP_APIS: list[tuple[str, str]] = [
    ("businessprofileperformance.googleapis.com", "Business Profile Performance API"),
    ("mybusinessaccountmanagement.googleapis.com", "My Business Account Management API"),
    ("mybusinessbusinessinformation.googleapis.com", "My Business Business Information API"),
    ("mybusinesslodging.googleapis.com", "My Business Lodging API"),
    ("mybusinessplaceactions.googleapis.com", "My Business Place Actions API"),
    ("mybusinessnotifications.googleapis.com", "My Business Notifications API"),
    ("mybusinessverifications.googleapis.com", "My Business Verifications API"),
    ("mybusinessqanda.googleapis.com", "My Business Q&A API"),
    ("mybusiness.googleapis.com", "Google My Business API (legacy)"),
]

CONSOLE_QUOTAS = (
    "https://console.cloud.google.com/apis/api/businessprofileperformance.googleapis.com/quotas"
    f"?project={PROJECT_ID}"
)
CONSOLE_LIBRARY = f"https://console.cloud.google.com/apis/library?project={PROJECT_ID}"
CONSOLE_GBP_APPLY = "https://developers.google.com/my-business/content/prereqs"


def _load_creds() -> Credentials:
    if not TOKEN_PATH.exists():
        raise SystemExit(
            f"Missing {TOKEN_PATH}\n"
            "Run: python _enable_youtube_gcp.py  (sign in with a project Owner/Editor)"
        )
    info = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    return Credentials.from_authorized_user_info(info, info.get("scopes", []))


def main() -> int:
    creds = _load_creds()
    usage = build("serviceusage", "v1", credentials=creds, cache_discovery=False)

    print(f"Project: {PROJECT_NAME} ({PROJECT_ID})\n")
    print("Google Business Profile APIs (enablement state):\n")
    print(f"{'Service':<52} {'State':<14} Label")
    print("-" * 95)

    enabled = 0
    states: dict[str, str] = {}
    for api_id, label in GBP_APIS:
        name = f"projects/{PROJECT_ID}/services/{api_id}"
        try:
            s = usage.services().get(name=name).execute()
            state = str(s.get("state") or "?")
        except Exception as exc:
            state = f"error ({exc})"
        states[api_id] = state
        if state == "ENABLED":
            enabled += 1
        print(f"{api_id:<52} {state:<14} {label}")

    print(f"\nEnabled: {enabled} / {len(GBP_APIS)}")

    # Any other business-related enabled services
    print("\nOther ENABLED services matching 'business' or 'mybusiness':")
    page_token = None
    extra: list[str] = []
    known = {a[0] for a in GBP_APIS}
    while True:
        resp = (
            usage.services()
            .list(
                parent=f"projects/{PROJECT_ID}",
                filter="state:ENABLED",
                pageSize=200,
                pageToken=page_token,
            )
            .execute()
        )
        for s in resp.get("services", []):
            sid = (s.get("config", {}) or {}).get("name") or s.get("name", "")
            short = sid.split("/")[-1] if sid else ""
            if not short:
                continue
            low = short.lower()
            if ("business" in low or "mybusiness" in low) and short not in known:
                extra.append(short)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    if extra:
        for m in sorted(set(extra)):
            print(f"  {m}")
    else:
        print("  (none)")

    # Quota via Service Usage consumerQuotaMetrics (Performance API)
    print("\n--- API access approval (quota) ---")
    perf = "businessprofileperformance.googleapis.com"
    try:
        usage_beta = build("serviceusage", "v1beta1", credentials=creds, cache_discovery=False)
        parent = f"projects/{PROJECT_ID}/services/{perf}"
        listed = usage_beta.services().consumerQuotaMetrics().list(parent=parent, view="FULL").execute()
        metrics = listed.get("metrics") or []
        if not metrics:
            print("No quota metrics (API likely not approved yet).")
        for m in metrics:
            display = m.get("displayName") or m.get("metric", "")
            for limit in m.get("consumerQuotaLimits") or []:
                unit = limit.get("unit", "")
                for bucket in limit.get("quotaBuckets") or []:
                    eff = bucket.get("effectiveLimit")
                    default = bucket.get("defaultLimit")
                    print(f"  {display} ({unit}): effectiveLimit={eff!r}, defaultLimit={default!r}")
                    if eff is None and default is None:
                        print(
                            "  Likely NOT approved yet (Google docs: 0 QPM until access is granted; "
                            "300 QPM after approval)."
                        )
                    elif eff == "300" or default == "300":
                        print("  Looks APPROVED for basic access (300 QPM).")
    except Exception as exc:
        print(f"Could not read quotas via API ({exc}).")
        print("Check manually in Cloud Console (see links below).")

    print("\n--- Interpretation ---")
    if enabled == 0:
        print(
            "GBP APIs are NOT enabled on this project yet.\n"
            "After Google approves API access, enable all eight APIs in API Library."
        )
    elif enabled > 0 and enabled < len(GBP_APIS):
        print("Some GBP APIs are enabled; enable the rest after access approval.")
    else:
        print("All listed GBP APIs show ENABLED.")

    print(
        "\nApproval signal (per Google docs):\n"
        "  - Quota 0 QPM  = project NOT approved for GBP API access yet\n"
        "  - Quota 300 QPM = approved for basic access\n"
        "Open Quotas page and look for 'Queries per minute' on Performance API."
    )
    print(f"\nConsole links:\n  Quotas: {CONSOLE_QUOTAS}\n  API Library: {CONSOLE_LIBRARY}")
    print(f"  Apply for access: {CONSOLE_GBP_APPLY}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
