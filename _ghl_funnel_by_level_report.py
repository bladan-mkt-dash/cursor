"""Combined funnel: Interest in FJ, Discover Calls, Sign Ups by membership level."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

import pandas as pd
import requests

from ghl_client import (
    _bearer_token,
    _location_id,
    contact_custom_field_value,
    resolve_membership_level_custom_field_id,
    resolve_sign_up_date_custom_field_id,
    search_contacts_custom_field_date_range,
    search_contacts_date_added_range,
)

SINCE = "2025-09-01"
UNTIL = "2026-04-30"
LEVELS = ("Standard", "Gold", "Silver", "Platinum", "n/a")

GHL_BASE = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-04-15"
CONFIRMED_STATUSES = frozenset({"confirmed", "showed", "completed", "active", "new"})


def _normalize_level(raw: str) -> str:
    v = (raw or "").strip()
    if not v:
        return "n/a"
    for level in ("Standard", "Gold", "Silver", "Platinum"):
        if v.casefold() == level.casefold():
            return level
    return v


def _sign_up_month(contact: dict, sign_up_field_id: str) -> str | None:
    raw = contact_custom_field_value(contact, sign_up_field_id)
    if not raw:
        return None
    ts = pd.to_datetime(raw, errors="coerce", utc=True)
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts).strftime("%Y-%m")


def _load_confirmed_contact_ids() -> set[str]:
    loc = _location_id()
    headers = {
        "Authorization": f"Bearer {_bearer_token()}",
        "Version": GHL_API_VERSION,
        "Accept": "application/json",
    }
    start_dt = datetime(2025, 9, 1, tzinfo=timezone.utc)
    start_ms = str(int(start_dt.timestamp() * 1000))
    end_ms = str(int(datetime.now(timezone.utc).timestamp() * 1000))

    r = requests.get(
        f"{GHL_BASE}/calendars/",
        params={"locationId": loc},
        headers=headers,
        timeout=60,
    )
    r.raise_for_status()

    confirmed: set[str] = set()
    seen: set[str] = set()
    for cal in r.json().get("calendars") or []:
        r2 = requests.get(
            f"{GHL_BASE}/calendars/events",
            params={
                "locationId": loc,
                "calendarId": cal["id"],
                "startTime": start_ms,
                "endTime": end_ms,
            },
            headers=headers,
            timeout=90,
        )
        if not r2.ok:
            continue
        for ev in r2.json().get("events") or []:
            eid = ev.get("id")
            if not eid or str(eid) in seen or ev.get("deleted"):
                continue
            seen.add(str(eid))
            status = (
                ev.get("appointmentStatus") or ev.get("appoinmentStatus") or "unknown"
            ).casefold()
            cid = ev.get("contactId")
            if cid and status in CONFIRMED_STATUSES:
                confirmed.add(str(cid))
    return confirmed


def main() -> None:
    ml_field = resolve_membership_level_custom_field_id()
    su_field = resolve_sign_up_date_custom_field_id()

    created_contacts, _, _ = search_contacts_date_added_range(SINCE, UNTIL, max_pages=500)
    signup_contacts, _, _ = search_contacts_custom_field_date_range(
        su_field, SINCE, UNTIL, max_pages=500
    )
    confirmed_ids = _load_confirmed_contact_ids()

    interest: Counter[str] = Counter()
    discover: Counter[str] = Counter()
    signups: Counter[str] = Counter()
    by_month: dict[str, Counter[str]] = defaultdict(Counter)
    other: Counter[str] = Counter()

    for c in created_contacts:
        level = _normalize_level(
            contact_custom_field_value(c, ml_field) if ml_field else ""
        )
        bucket = level if level in LEVELS else None
        if bucket:
            interest[bucket] += 1
            if str(c.get("id") or "") in confirmed_ids:
                discover[bucket] += 1
        else:
            other[level] += 1

    for c in signup_contacts:
        level = _normalize_level(
            contact_custom_field_value(c, ml_field) if ml_field else ""
        )
        if level in LEVELS:
            signups[level] += 1
            month = _sign_up_month(c, su_field)
            if month:
                by_month[month][level] += 1
        else:
            other[level] += 1

    total_interest = sum(interest[l] for l in LEVELS)
    total_discover = sum(discover[l] for l in LEVELS)
    total_signups = sum(signups[l] for l in LEVELS)

    print(f"Period: {SINCE} through {UNTIL}")
    print("Interest in FJ = new contacts created (dateAdded)")
    print("Discover Calls = confirmed calendar appointment")
    print("Sign Ups = Sign Up Date in range")
    print()

    w = 14
    print("=" * 88)
    print("FUNNEL BY MEMBERSHIP LEVEL")
    print("=" * 88)
    print(
        f"{'Interest in FJ':>{w}} {'Discover Calls':>{w}} "
        f"{'Membership Level':<16} {'Sign Ups':>10}"
    )
    print("-" * 88)

    for i, level in enumerate(LEVELS):
        if i == 0:
            int_cell = f"{total_interest:>{w},}"
            disc_cell = f"{total_discover:>{w},}"
        else:
            int_cell = f"{'':>{w}}"
            disc_cell = f"{'':>{w}}"
        print(
            f"{int_cell} {disc_cell} {level:<16} {signups[level]:>10,}  "
            f"({interest[level]:,} new · {discover[level]:,} calls)"
        )

    print("-" * 88)
    print(
        f"{total_interest:>{w},} {total_discover:>{w},} {'TOTAL':<16} {total_signups:>10,}"
    )
    print()
    print("Per-level detail (Interest in FJ | Discover Calls | Sign Ups):")
    print(f"{'Membership Level':<16} {'Interest in FJ':>14} {'Discover Calls':>14} {'Sign Ups':>10}")
    print("-" * 58)
    for level in LEVELS:
        print(
            f"{level:<16} {interest[level]:>14,} {discover[level]:>14,} {signups[level]:>10,}"
        )
    print("-" * 58)
    print(
        f"{'TOTAL':<16} {total_interest:>14,} {total_discover:>14,} {total_signups:>10,}"
    )

    if other:
        print()
        print("Outside core levels (Nutrition Only, etc.):")
        for lvl, n in other.most_common():
            print(f"  {lvl}: {n:,}")

    months = sorted(by_month.keys())
    if months:
        print()
        print("=" * 88)
        print("MONTHLY SIGN-UPS BY MEMBERSHIP LEVEL")
        print("=" * 88)
        hdr = f"{'Month':<10}" + "".join(f"{l:>12}" for l in LEVELS) + f"{'Total':>12}"
        print(hdr)
        print("-" * 88)
        for ym in months:
            label = datetime.strptime(ym, "%Y-%m").strftime("%b %Y")
            row_total = sum(by_month[ym][l] for l in LEVELS)
            cells = "".join(f"{by_month[ym][l]:>12,}" for l in LEVELS)
            print(f"{label:<10}{cells}{row_total:>12,}")
        print("-" * 88)
        cells = "".join(f"{signups[l]:>12,}" for l in LEVELS)
        print(f"{'TOTAL':<10}{cells}{total_signups:>12,}")


if __name__ == "__main__":
    main()
