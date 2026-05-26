"""Sign-ups by membership level (Gold, Platinum, Silver, Standard)."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

import pandas as pd

from ghl_client import (
    contact_custom_field_value,
    resolve_membership_level_custom_field_id,
    resolve_sign_up_date_custom_field_id,
    search_contacts_custom_field_date_range,
)

SINCE = "2025-09-01"
UNTIL = "2026-04-30"
LEVELS = ("Standard", "Gold", "Silver", "Platinum")


def _normalize_level(raw: str) -> str:
    v = (raw or "").strip()
    for level in LEVELS:
        if v.casefold() == level.casefold():
            return level
    return v or "(No membership level)"


def _sign_up_month(contact: dict, sign_up_field_id: str) -> str | None:
    raw = contact_custom_field_value(contact, sign_up_field_id)
    if not raw:
        return None
    ts = pd.to_datetime(raw, errors="coerce", utc=True)
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts).strftime("%Y-%m")


def main() -> None:
    su_field = resolve_sign_up_date_custom_field_id()
    ml_field = resolve_membership_level_custom_field_id()
    if not su_field:
        raise ValueError("Sign Up Date field not resolved")

    contacts, truncated, total_reported = search_contacts_custom_field_date_range(
        su_field, SINCE, UNTIL, max_pages=500
    )

    by_level: Counter[str] = Counter()
    by_month_level: dict[str, Counter[str]] = defaultdict(Counter)
    other_levels: Counter[str] = Counter()

    for c in contacts:
        level = _normalize_level(
            contact_custom_field_value(c, ml_field) if ml_field else ""
        )
        month = _sign_up_month(c, su_field)
        if level in LEVELS:
            by_level[level] += 1
            if month:
                by_month_level[month][level] += 1
        else:
            other_levels[level] += 1

    core_total = sum(by_level[l] for l in LEVELS)
    all_total = len(contacts)

    print(f"Sign Up Date range: {SINCE} through {UNTIL}")
    print(f"Contacts loaded: {all_total:,} (API total: {total_reported:,}, truncated: {truncated})")
    print()

    print("=" * 60)
    print("SIGN-UPS BY MEMBERSHIP LEVEL")
    print("=" * 60)
    print(f"{'Level':<12} {'Count':>8} {'% of core 4':>12} {'% of all sign-ups':>18}")
    print("-" * 60)
    for level in LEVELS:
        n = by_level[level]
        core_pct = (n / core_total * 100) if core_total else 0
        all_pct = (n / all_total * 100) if all_total else 0
        print(f"{level:<12} {n:>8,} {core_pct:>11.1f}% {all_pct:>17.1f}%")
    print("-" * 60)
    print(f"{'Core 4 total':<12} {core_total:>8,} {100.0:>11.1f}% {(core_total/all_total*100) if all_total else 0:>17.1f}%")
    print()

    if other_levels:
        print("Other membership levels (excluded from core 4 breakdown):")
        for level, n in other_levels.most_common():
            print(f"  {level}: {n:,}")
        print()

    months = sorted(by_month_level.keys())
    if months:
        print("=" * 72)
        print("MONTHLY SIGN-UPS BY MEMBERSHIP LEVEL")
        print("=" * 72)
        hdr = f"{'Month':<10}" + "".join(f"{l:>12}" for l in LEVELS) + f"{'Total':>12}"
        print(hdr)
        print("-" * 72)
        month_totals: Counter[str] = Counter()
        for ym in months:
            label = datetime.strptime(ym, "%Y-%m").strftime("%b %Y")
            row_total = sum(by_month_level[ym][l] for l in LEVELS)
            month_totals[ym] = row_total
            cells = "".join(f"{by_month_level[ym][l]:>12,}" for l in LEVELS)
            print(f"{label:<10}{cells}{row_total:>12,}")
        print("-" * 72)
        grand = sum(month_totals.values())
        cells = "".join(f"{by_level[l]:>12,}" for l in LEVELS)
        print(f"{'TOTAL':<10}{cells}{grand:>12,}")
        print()

        print("Monthly share of core-4 sign-ups:")
        print(f"{'Month':<10}" + "".join(f"{l:>12}" for l in LEVELS))
        print("-" * 72)
        for ym in months:
            label = datetime.strptime(ym, "%Y-%m").strftime("%b %Y")
            row_total = month_totals[ym] or 1
            cells = "".join(
                f"{by_month_level[ym][l] / row_total * 100:>11.1f}%" for l in LEVELS
            )
            print(f"{label:<10}{cells}")

    if truncated:
        print()
        print("WARNING: Search hit pagination cap; counts may be incomplete.")


if __name__ == "__main__":
    main()
