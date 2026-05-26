"""One-off: contacts created -> meeting confirmed -> sign up date by membership level."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import requests

from ghl_client import (
    _bearer_token,
    _location_id,
    contact_custom_field_value,
    resolve_membership_level_custom_field_id,
    resolve_sign_up_date_custom_field_id,
    search_contacts_date_added_range,
)

GHL_BASE = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-04-15"
CONFIRMED_STATUSES = frozenset({"confirmed", "showed", "completed", "active", "new"})

SINCE = "2025-09-01"
UNTIL = "2026-04-30"


def _ghl_headers() -> dict[str, str]:
    token = _bearer_token()
    if not token:
        raise ValueError("No GHL token in .env")
    return {
        "Authorization": f"Bearer {token}",
        "Version": GHL_API_VERSION,
        "Accept": "application/json",
    }


def load_confirmed_contact_ids(start_dt: datetime) -> tuple[set[str], int]:
    loc = _location_id()
    headers = _ghl_headers()
    start_ms = str(int(start_dt.timestamp() * 1000))
    end_ms = str(int(datetime.now(timezone.utc).timestamp() * 1000))

    r = requests.get(
        f"{GHL_BASE}/calendars/",
        params={"locationId": loc},
        headers=headers,
        timeout=60,
    )
    r.raise_for_status()
    calendars = r.json().get("calendars") or []

    confirmed_by_contact: set[str] = set()
    seen: set[str] = set()
    for cal in calendars:
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
                confirmed_by_contact.add(str(cid))
    return confirmed_by_contact, len(calendars)


def main() -> None:
    loc = _location_id()
    ml_field = resolve_membership_level_custom_field_id(loc)
    su_field = resolve_sign_up_date_custom_field_id(loc)

    contacts, truncated, total_reported = search_contacts_date_added_range(
        SINCE, UNTIL, max_pages=500
    )
    start_dt = datetime(2025, 9, 1, tzinfo=timezone.utc)
    confirmed_contacts, cal_count = load_confirmed_contact_ids(start_dt)

    by_level: dict[str, dict[str, int]] = defaultdict(
        lambda: {"created": 0, "meeting_confirmed": 0, "sign_up_date": 0}
    )

    for c in contacts:
        cid = str(c.get("id") or "")
        ml = (
            contact_custom_field_value(c, ml_field).strip()
            if ml_field
            else ""
        ) or "(No membership level)"
        su = contact_custom_field_value(c, su_field).strip() if su_field else ""
        by_level[ml]["created"] += 1
        if cid in confirmed_contacts:
            by_level[ml]["meeting_confirmed"] += 1
        if su:
            by_level[ml]["sign_up_date"] += 1

    rows = sorted(by_level.items(), key=lambda x: -x[1]["created"])
    totals = {"created": 0, "meeting_confirmed": 0, "sign_up_date": 0}
    for _, v in rows:
        for k in totals:
            totals[k] += v[k]

    print(f"Location: {loc}")
    print(f"Membership Level field: {ml_field}")
    print(f"Sign Up Date field: {su_field}")
    print(f"Calendars scanned: {cal_count}")
    print(
        f"Contacts fetched: {len(contacts):,} (API total: {total_reported:,}, "
        f"truncated: {truncated})"
    )
    print()
    print(f"Period: contacts created {SINCE} through {UNTIL} (UTC dateAdded)")
    print("=" * 90)
    hdr = f"{'Membership Level':<35} {'Created':>10} {'Meeting Confirmed':>18} {'Sign Up Date':>14}"
    print(hdr)
    print("-" * 90)
    for level, v in rows:
        print(
            f"{level[:35]:<35} {v['created']:>10,} "
            f"{v['meeting_confirmed']:>18,} {v['sign_up_date']:>14,}"
        )
    print("-" * 90)
    print(
        f"{'TOTAL':<35} {totals['created']:>10,} "
        f"{totals['meeting_confirmed']:>18,} {totals['sign_up_date']:>14,}"
    )
    if totals["created"]:
        print()
        mc_pct = totals["meeting_confirmed"] / totals["created"] * 100
        su_pct = totals["sign_up_date"] / totals["created"] * 100
        print(f"Overall: Created -> Meeting Confirmed: {mc_pct:.1f}%")
        print(f"Overall: Created -> Sign Up Date: {su_pct:.1f}%")
        if totals["meeting_confirmed"]:
            su_mc = totals["sign_up_date"] / totals["meeting_confirmed"] * 100
            print(f"Overall: Meeting Confirmed -> Sign Up Date: {su_mc:.1f}%")
    if truncated:
        print()
        print("WARNING: Contact search hit pagination cap; counts may be incomplete.")


if __name__ == "__main__":
    main()
