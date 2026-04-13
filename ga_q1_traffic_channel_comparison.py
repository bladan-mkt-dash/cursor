"""
GA4 comparative report: Q1 website traffic by session default channel group
(Traffic acquisition), years 2024–2026.

Authentication: sets ``GOOGLE_APPLICATION_CREDENTIALS`` to this project’s
``ga_credentials.json``. Property ID: ``GA4_PROPERTY_ID`` in ``.env`` (or env).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from google_data import get_q1_traffic_by_session_default_channel_group

_PROJECT_DIR = Path(__file__).resolve().parent
_CREDENTIALS_FILE = _PROJECT_DIR / "ga_credentials.json"
_OUTPUT_CSV = _PROJECT_DIR / "q1_traffic_by_session_channel_group_comparison.csv"

YEARS = (2024, 2025, 2026)


def main() -> int:
    load_dotenv(_PROJECT_DIR / ".env")

    if not _CREDENTIALS_FILE.is_file():
        print(f"Missing credentials file: {_CREDENTIALS_FILE}", file=sys.stderr)
        return 1

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_CREDENTIALS_FILE.resolve())

    try:
        detail = get_q1_traffic_by_session_default_channel_group(years=YEARS)
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1

    if detail.empty:
        print("No rows returned from GA4.")
        return 0

    detail.to_csv(_OUTPUT_CSV, index=False)
    print(f"Wrote detail rows to {_OUTPUT_CSV}\n")

    # Wide pivots for comparison (channel × year)
    pivot_sessions = detail.pivot_table(
        index="Session_default_channel_group",
        columns="Year",
        values="Sessions",
        aggfunc="sum",
        fill_value=0,
    ).reindex(columns=list(YEARS), fill_value=0)
    pivot_sessions = pivot_sessions.sort_values(
        by=list(YEARS), ascending=False
    )

    pivot_users = detail.pivot_table(
        index="Session_default_channel_group",
        columns="Year",
        values="Total_users",
        aggfunc="sum",
        fill_value=0,
    ).reindex(columns=list(YEARS), fill_value=0)
    pivot_users = pivot_users.loc[pivot_sessions.index]

    totals_sessions = pivot_sessions.sum(axis=0)
    totals_users = pivot_users.sum(axis=0)

    pd.set_option("display.max_rows", 200)
    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda x: f"{x:,.0f}")

    print("=== Q1 sessions by session default channel group (GA4 Traffic acquisition) ===")
    print(pivot_sessions.to_string())
    print("\n--- Q1 sessions total (all channels) ---")
    print(totals_sessions.to_string())
    print()

    print("=== Q1 total users by session default channel group ===")
    print(pivot_users.to_string())
    print("\n--- Q1 total users (all channels) ---")
    print(totals_users.to_string())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
