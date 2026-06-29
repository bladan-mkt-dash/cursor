"""Q2 2026 Gold & Platinum members by First Appointment Date (GoHighLevel)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from ghl_client import (
    _field_name_fingerprint,
    _ghl_get_json,
    _location_id,
    contact_created_utc_date_str,
    contact_custom_field_value,
    fetch_location_custom_fields,
    resolve_cancellation_date_custom_field_id,
    resolve_membership_cancelled_custom_field_id,
    resolve_membership_level_custom_field_id,
    resolve_sign_up_date_custom_field_id,
    search_contacts_custom_field_date_range,
)

SINCE = "2026-04-01"
UNTIL = "2026-06-30"
TARGET_LEVELS = frozenset({"gold", "platinum"})
FIRST_APPOINTMENT_DATE_ALIASES = ("First Appointment Date",)
SALES_REP_FIELD_ID = "9DhTm0QrESdjys1wWE0g"
OUTPUT = (
    Path(__file__).resolve().parent
    / "ghl_q2_2026_first_appt_gold_platinum_ledger.csv"
)


def _resolve_first_appointment_date_field_id() -> str | None:
    fps = frozenset(_field_name_fingerprint(a) for a in FIRST_APPOINTMENT_DATE_ALIASES)
    for f in fetch_location_custom_fields():
        nm = f.get("name") or f.get("fieldName") or ""
        if _field_name_fingerprint(str(nm)) in fps:
            fid = f.get("id") or f.get("fieldKey") or f.get("key")
            if fid:
                return str(fid)
    return None


def _format_date(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        if raw.endswith("Z"):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(raw)
        return dt.date().isoformat()
    except ValueError:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(raw[:10], fmt).date().isoformat()
            except ValueError:
                continue
    return raw


def _load_user_names() -> dict[str, str]:
    loc = _location_id()
    data = _ghl_get_json(f"/users/?locationId={loc}")
    out: dict[str, str] = {}
    for u in data.get("users") or []:
        uid = str(u.get("id") or "")
        if not uid:
            continue
        name = (u.get("name") or "").strip()
        if not name:
            name = f"{u.get('firstName') or ''} {u.get('lastName') or ''}".strip()
        out[uid] = name
    return out


def _normalize_level(raw: str) -> str:
    v = (raw or "").strip()
    for level in ("Platinum", "Gold", "Silver", "Standard"):
        if v.casefold() == level.casefold():
            return level
    return v


def main() -> None:
    fa_field = _resolve_first_appointment_date_field_id()
    if not fa_field:
        raise ValueError("First Appointment Date field not resolved")

    ml_field = resolve_membership_level_custom_field_id() or ""
    su_field = resolve_sign_up_date_custom_field_id() or ""
    mc_field = resolve_membership_cancelled_custom_field_id() or ""
    cd_field = resolve_cancellation_date_custom_field_id() or ""
    users = _load_user_names()

    contacts, truncated, total_reported = search_contacts_custom_field_date_range(
        fa_field, SINCE, UNTIL, max_pages=500
    )

    rows: list[dict[str, str]] = []
    for c in contacts:
        level = _normalize_level(contact_custom_field_value(c, ml_field) if ml_field else "")
        if level.casefold() not in TARGET_LEVELS:
            continue

        fa_raw = contact_custom_field_value(c, fa_field)
        fa_date = _format_date(fa_raw)
        if not fa_date or fa_date < SINCE or fa_date > UNTIL:
            continue

        owner_id = c.get("assignedTo")
        assignee = users.get(str(owner_id), "") if owner_id else ""

        rows.append(
            {
                "Contact First Name": (c.get("firstName") or "").strip(),
                "Contact Last Name": (c.get("lastName") or "").strip(),
                "First Appointment Date": fa_date,
                "Membership Level": level,
                "Assignee": assignee,
                "Sales Rep": contact_custom_field_value(c, SALES_REP_FIELD_ID).strip(),
                "Sign Up Date": _format_date(
                    contact_custom_field_value(c, su_field) if su_field else ""
                ),
                "Membership Cancelled": (
                    contact_custom_field_value(c, mc_field) if mc_field else ""
                ),
                "Membership Cancellation Date": _format_date(
                    contact_custom_field_value(c, cd_field) if cd_field else ""
                ),
                "dateAdded": contact_created_utc_date_str(c) or "",
                "Email": (c.get("email") or "").strip(),
                "Contact ID": str(c.get("id") or ""),
            }
        )

    rows.sort(
        key=lambda r: (
            r["First Appointment Date"],
            r["Contact Last Name"],
            r["Contact First Name"],
        )
    )
    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT, index=False)

    print(f"First Appointment Date range: {SINCE} through {UNTIL}")
    print(
        f"Contacts with first appointment in range: {len(contacts):,} "
        f"(API total: {total_reported:,}, truncated: {truncated})"
    )
    print(f"Gold / Platinum in cohort: {len(rows):,}")
    print(f"Wrote: {OUTPUT}")
    print()
    if df.empty:
        print("No Gold or Platinum members matched this first-appointment window.")
    else:
        print(df.to_string(index=False))
    if truncated:
        print()
        print("WARNING: Search hit pagination cap; counts may be incomplete.")


if __name__ == "__main__":
    main()
