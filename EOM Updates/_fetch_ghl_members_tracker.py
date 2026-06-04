"""Fetch GHL new members by tier/location and write Boston + Newton rows on 2026 tracker."""

from __future__ import annotations

import calendar
import sys
from collections import Counter, defaultdict
from pathlib import Path

from _bootstrap import setup
from ghl_client import contact_custom_field_value, fetch_signup_date_range_committed_yes_contacts

setup()

from tracker_config import active_layout, column_for_month
from tracker_sheets import write_columns

# GHL **Location Preference** (contact.location_preference)
LOCATION_FIELD_ID = "U4qminML7Yiync8OaxNz"

TIERS = ("Standard", "Silver", "Gold", "Platinum")


def _tier_rows() -> dict[str, dict[str, int]]:
    lay = active_layout()
    return {"Boston": lay.ghl_boston, "Newton": lay.ghl_newton}


def _total_rows() -> dict[str, int]:
    lay = active_layout()
    return {"Boston": lay.ghl_boston_total, "Newton": lay.ghl_newton_total}


def _avg_rows() -> dict[str, int]:
    lay = active_layout()
    return {"Boston": lay.ghl_boston_avg, "Newton": lay.ghl_newton_avg}

# Legacy multi-month backfill (Mar–May 2026)
BACKFILL_MONTHS = ((2026, 3), (2026, 4), (2026, 5))


def _norm_loc(raw: str) -> str:
    s = (raw or "").strip().casefold()
    if "boston" in s:
        return "Boston"
    if "newton" in s:
        return "Newton"
    return "Other"


def _norm_tier(raw: str) -> str | None:
    s = (raw or "").strip().casefold()
    for tier in TIERS:
        if tier.lower() in s or s == tier.lower():
            return tier
    return None


def _avg_per_week(total: int, year: int, month: int, *, location: str) -> str:
    days = calendar.monthrange(year, month)[1]
    if location == "Boston":
        # Matches existing sheet convention (e.g. 14 members / 8 = 1.75 for Jan).
        weeks = 8.0
    else:
        weeks = days / 7.0
    if weeks <= 0:
        return ""
    return f"{total / weeks:.2f}"


def _fmt_tier(value: int) -> str:
    return str(value) if value else ""


def fetch_month_counts(year: int, month: int) -> dict[str, Counter[str]]:
    last = calendar.monthrange(year, month)[1]
    since = f"{year}-{month:02d}-01"
    until = f"{year}-{month:02d}-{last:02d}"
    data = fetch_signup_date_range_committed_yes_contacts(since, until)
    mid = data["membership_level_field_id"]
    by_loc: dict[str, Counter[str]] = defaultdict(Counter)
    for contact in data["contacts"]:
        loc = _norm_loc(contact_custom_field_value(contact, LOCATION_FIELD_ID))
        tier = _norm_tier(contact_custom_field_value(contact, mid) if mid else "")
        if tier and loc in ("Boston", "Newton"):
            by_loc[loc][tier] += 1
    if data.get("truncated_pages"):
        print(
            f"WARNING: GHL pagination truncated for {since}..{until}; counts may be low.",
            file=sys.stderr,
        )
    return dict(by_loc)


def build_col_updates(year: int, month: int) -> dict[int, str]:
    counts = fetch_month_counts(year, month)
    col_updates: dict[int, str] = {}
    tier_rows = _tier_rows()
    total_rows = _total_rows()
    avg_rows = _avg_rows()
    for location in ("Boston", "Newton"):
        tier_counts = counts.get(location, Counter())
        total = sum(tier_counts.get(t, 0) for t in TIERS)
        for tier, row in tier_rows[location].items():
            col_updates[row] = _fmt_tier(tier_counts.get(tier, 0))
        col_updates[total_rows[location]] = str(total) if total else ""
        col_updates[avg_rows[location]] = (
            _avg_per_week(total, year, month, location=location) if total else ""
        )
    return col_updates


def run_month(year: int, month: int, *, dry_run: bool = False) -> int:
    col = column_for_month(year, month)
    print(f"Fetching GHL members for {year}-{month:02d} (column {col})...")
    col_updates = build_col_updates(year, month)
    total_rows = _total_rows()
    b_total = col_updates.get(total_rows["Boston"], "")
    n_total = col_updates.get(total_rows["Newton"], "")
    print(f"  Boston total={b_total}, Newton total={n_total}")
    for row in sorted(col_updates):
        print(f"    {col}{row}: {col_updates[row]}")

    if dry_run:
        print("(dry-run: sheet not updated)")
        return 0

    write_columns({col: col_updates})
    print(f"Updated GHL rows 185-198, column {col}.")
    return 0


def main() -> int:
    if "--backfill-jkl" in sys.argv:
        updates_by_col: dict[str, dict[int, str]] = {}
        for year, month in BACKFILL_MONTHS:
            col = column_for_month(year, month)
            print(f"Fetching GHL members for {year}-{month:02d}...")
            updates_by_col[col] = build_col_updates(year, month)
        if "--dry-run" in sys.argv:
            print("(dry-run)")
            return 0
        write_columns(updates_by_col)
        print("Backfill complete (columns J–L).")
        return 0

    from tracker_config import parse_month_arg

    year, month = parse_month_arg("2026-05")
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--month" and i < len(sys.argv) - 1:
            year, month = parse_month_arg(sys.argv[i + 1])
            break
    return run_month(year, month, dry_run="--dry-run" in sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
