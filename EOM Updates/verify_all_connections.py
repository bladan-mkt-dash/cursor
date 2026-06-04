"""Quick connectivity check for all EOM tracker data sources."""

from __future__ import annotations

import os
import sys

from _bootstrap import setup

setup()


def _ok(label: str) -> None:
    print(f"  OK  {label}")


def _fail(label: str, detail: str = "") -> int:
    print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))
    return 1


def main() -> int:
    failures = 0
    print("=== Environment (.env) ===")
    for key in (
        "GA4_PROPERTY_ID",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GHL_ACCESS_TOKEN",
        "GHL_LOCATION_ID",
        "META_SYSTEM_USER_TOKEN",
        "YOUTUBE_REFRESH_TOKEN",
        "WOOCOMMERCE_URL",
        "MONDAY_API_TOKEN",
    ):
        val = (os.getenv(key) or "").strip()
        if val:
            _ok(key)
        else:
            failures += _fail(key, "missing or empty")

    print("\n=== Google Sheets OAuth ===")
    try:
        from tracker_sheets import _sheets_service

        _sheets_service()
        _ok("Sheets API token")
    except Exception as exc:
        failures += _fail("Sheets API", str(exc)[:120])

    print("\n=== GA4 ===")
    try:
        from google_data import _ensure_ga_credentials, _strip_env

        _ensure_ga_credentials()
        pid = _strip_env(os.getenv("GA4_PROPERTY_ID"))
        if not pid:
            failures += _fail("GA4_PROPERTY_ID")
        else:
            from google.analytics.data_v1beta import BetaAnalyticsDataClient
            from google.analytics.data_v1beta.types import DateRange, Metric, RunReportRequest

            client = BetaAnalyticsDataClient()
            client.run_report(
                RunReportRequest(
                    property=f"properties/{pid}",
                    metrics=[Metric(name="sessions")],
                    date_ranges=[DateRange(start_date="yesterday", end_date="yesterday")],
                )
            )
            _ok(f"GA4 property {pid}")
    except Exception as exc:
        failures += _fail("GA4", str(exc)[:120])

    print("\n=== GHL ===")
    try:
        from ghl_client import _bearer_token, _location_id

        if not _bearer_token():
            failures += _fail("GHL token")
        elif not _location_id():
            failures += _fail("GHL_LOCATION_ID")
        else:
            _ok("GHL credentials")
    except Exception as exc:
        failures += _fail("GHL", str(exc)[:120])

    print("\n=== Meta ===")
    tok = (
        os.getenv("META_SYSTEM_USER_TOKEN")
        or os.getenv("META_USER_ACCESS_TOKEN")
        or os.getenv("META_ACCESS_TOKEN")
        or ""
    ).strip()
    if tok:
        _ok("Meta token present")
    else:
        failures += _fail("Meta token")

    print("\n=== YouTube ===")
    try:
        from youtube_client import get_credentials, resolve_channel_id

        creds = get_credentials(allow_interactive=False)
        ch = resolve_channel_id(creds)
        _ok(f"YouTube channel {ch}")
    except Exception as exc:
        failures += _fail(
            "YouTube",
            str(exc)[:120] + " (set YOUTUBE_* in .env or run python auth_youtube.py)",
        )

    print("\n=== WooCommerce ===")
    try:
        from woocommerce_client import woo_credentials_configured

        if woo_credentials_configured():
            _ok("WooCommerce keys")
        else:
            failures += _fail("WooCommerce", "keys missing — GA4 eShop only")
    except Exception as exc:
        failures += _fail("WooCommerce", str(exc)[:120])

    print("\n=== Google Ads (google-ads.yaml) ===")
    from pathlib import Path

    if (Path(__file__).resolve().parent.parent / "google-ads.yaml").exists():
        _ok("google-ads.yaml found")
    else:
        failures += _fail("google-ads.yaml", "not in project root")

    print("\n=== GBP ===")
    print(
        "  —  No EOM fetch script for Google Business Profile rows; "
        "fill manually or add a fetcher."
    )

    if failures:
        print(f"\n{failures} check(s) failed.")
        return 1
    print("\nAll automated sources reachable (GBP excluded).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
