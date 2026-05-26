"""Load acquisition + retention metrics for the combined dashboard."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ghl_client import (
    _bearer_token,
    _location_id,
    contact_custom_field_value,
    resolve_membership_level_custom_field_id,
    resolve_sign_up_date_custom_field_id,
    search_contacts_custom_field_date_range,
    search_contacts_date_added_range,
)

LEVELS = ("Standard", "Gold", "Silver", "Platinum", "n/a")
GHL_BASE = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-04-15"
CONFIRMED_STATUSES = frozenset({"confirmed", "showed", "completed", "active", "new"})
SPREADSHEET_ID = "18fDtd3xEHHXC6sCeRFFadSwcshk4SJqFUG6aV006DhU"
TOKEN_PATH = Path.home() / ".config" / "mcp-google-sheets" / "token.json"

_consolidated_cache: dict[str, dict] | None = None
_confirmed_ids_cache: dict[str, set[str]] = {}

PERIOD_PRESETS = {
    "sept-apr-2026": ("Sept 2025 – Apr 2026", date(2025, 9, 1), date(2026, 4, 30)),
    "jan-apr-2026": ("Jan – Apr 2026", date(2026, 1, 1), date(2026, 4, 30)),
    "sept-dec-2025": ("Sept – Dec 2025", date(2025, 9, 1), date(2025, 12, 31)),
}


def parse_date(val) -> date | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.upper() in {"FALSE", "TRUE", "N/A", "#N/A", "-"}:
        return None
    ts = pd.to_datetime(s, errors="coerce")
    return None if pd.isna(ts) else ts.date()


def in_range(d: date | None, start: date, end: date) -> bool:
    return d is not None and start <= d <= end


def norm_level(raw: str) -> str:
    v = (raw or "").strip()
    for level in ("Standard", "Gold", "Silver", "Platinum"):
        if v.casefold() == level.casefold():
            return level
    return "n/a" if not v else v


def norm_name(raw: str) -> str:
    s = unicodedata.normalize("NFKD", (raw or "").strip())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9\s]", " ", s.casefold())
    return re.sub(r"\s+", " ", s).strip()


def contact_name(c: dict) -> str:
    fn = (c.get("firstName") or c.get("firstNameRaw") or "").strip()
    ln = (c.get("lastName") or c.get("lastNameRaw") or "").strip()
    if fn or ln:
        return f"{fn} {ln}".strip()
    return (c.get("contactName") or c.get("name") or "").strip()


def _load_confirmed_contact_ids(since: date) -> set[str]:
    key = since.isoformat()
    if key in _confirmed_ids_cache:
        return _confirmed_ids_cache[key]
    loc = _location_id()
    headers = {
        "Authorization": f"Bearer {_bearer_token()}",
        "Version": GHL_API_VERSION,
        "Accept": "application/json",
    }
    start_ms = str(int(datetime(since.year, since.month, since.day, tzinfo=timezone.utc).timestamp() * 1000))
    end_ms = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    r = requests.get(f"{GHL_BASE}/calendars/", params={"locationId": loc}, headers=headers, timeout=60)
    r.raise_for_status()
    confirmed: set[str] = set()
    seen: set[str] = set()
    for cal in r.json().get("calendars") or []:
        r2 = requests.get(
            f"{GHL_BASE}/calendars/events",
            params={"locationId": loc, "calendarId": cal["id"], "startTime": start_ms, "endTime": end_ms},
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
            status = (ev.get("appointmentStatus") or ev.get("appoinmentStatus") or "unknown").casefold()
            cid = ev.get("contactId")
            if cid and status in CONFIRMED_STATUSES:
                confirmed.add(str(cid))
    _confirmed_ids_cache[key] = confirmed
    return confirmed


def load_consolidated_by_name() -> dict[str, dict]:
    global _consolidated_cache
    if _consolidated_cache is not None:
        return _consolidated_cache
    info = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    sheets = build("sheets", "v4", credentials=Credentials.from_authorized_user_info(info, info["scopes"]))
    rows = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range="Consolidated Data"
    ).execute().get("values", [])
    header = rows[0]
    col = {re.sub(r"\s+", " ", h.replace("\n", " ")).strip(): i for i, h in enumerate(header)}
    out: dict[str, dict] = {}
    for r in rows[1:]:
        name = str(r[col["Patient"]]).strip() if len(r) > col["Patient"] else ""
        if not name:
            continue
        key = norm_name(name)
        out[key] = {
            "name": name,
            "start_date": parse_date(r[col["Start Date (1st Appt)"]] if len(r) > col["Start Date (1st Appt)"] else None),
            "termination_date": parse_date(r[col["Date of Termination"]] if len(r) > col["Date of Termination"] else None),
            "level": norm_level(r[col["Membership Level"]] if len(r) > col["Membership Level"] else ""),
        }
    _consolidated_cache = out
    return out


def _sign_up_month(contact: dict, field_id: str) -> str | None:
    raw = contact_custom_field_value(contact, field_id)
    if not raw:
        return None
    ts = pd.to_datetime(raw, errors="coerce", utc=True)
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts).strftime("%Y-%m")


def build_acquisition(start: date, end: date) -> dict:
    since, until = start.isoformat(), end.isoformat()
    ml_field = resolve_membership_level_custom_field_id()
    su_field = resolve_sign_up_date_custom_field_id()

    created, _, _ = search_contacts_date_added_range(since, until, max_pages=500)
    signups_raw, _, _ = search_contacts_custom_field_date_range(su_field, since, until, max_pages=500)
    confirmed_ids = _load_confirmed_contact_ids(start)

    interest: Counter[str] = Counter()
    discover: Counter[str] = Counter()
    signups: Counter[str] = Counter()
    monthly: dict[str, Counter[str]] = defaultdict(Counter)

    for c in created:
        lv = norm_level(contact_custom_field_value(c, ml_field) if ml_field else "")
        if lv not in LEVELS:
            lv = "n/a"
        interest[lv] += 1
        if str(c.get("id") or "") in confirmed_ids:
            discover[lv] += 1

    for c in signups_raw:
        lv = norm_level(contact_custom_field_value(c, ml_field) if ml_field else "")
        if lv not in LEVELS:
            lv = "n/a"
        signups[lv] += 1
        mo = _sign_up_month(c, su_field)
        if mo:
            monthly[mo][lv] += 1

    total_interest = sum(interest.values())
    total_discover = sum(discover.values())
    total_signups = sum(signups.values())

    by_level = []
    for lv in LEVELS:
        su = signups[lv]
        intr = interest[lv]
        disc = discover[lv]
        by_level.append(
            {
                "level": lv,
                "interest": intr,
                "discover_calls": disc,
                "sign_ups": su,
                # Per-level counts kept for reference; conversion rates use period totals.
                "int_to_disc_pct": (total_discover / total_interest * 100) if total_interest else None,
                "disc_to_sign_pct": (su / total_discover * 100) if total_discover else None,
                "int_to_sign_pct": (su / total_interest * 100) if total_interest else None,
            }
        )

    months = sorted(monthly.keys())
    monthly_rows = []
    for ym in months:
        label = datetime.strptime(ym, "%Y-%m").strftime("%b %Y")
        row = {"month": label, "ym": ym}
        for lv in LEVELS:
            row[lv] = monthly[ym][lv]
        row["total"] = sum(monthly[ym].values())
        monthly_rows.append(row)

    return {
        "total_interest": total_interest,
        "total_discover": total_discover,
        "total_sign_ups": total_signups,
        "by_level": by_level,
        "monthly_signups": monthly_rows,
    }


def build_retention(start: date, end: date, consolidated: dict[str, dict]) -> dict:
    su_field = resolve_sign_up_date_custom_field_id()
    ml_field = resolve_membership_level_custom_field_id()
    contacts, truncated, _ = search_contacts_custom_field_date_range(
        su_field, start.isoformat(), end.isoformat(), max_pages=500
    )

    stats = {lv: Counter() for lv in LEVELS}
    term_all = sum(
        1 for row in consolidated.values()
        if row["termination_date"] and in_range(row["termination_date"], start, end)
    )

    for c in contacts:
        lv = norm_level(contact_custom_field_value(c, ml_field) if ml_field else "")
        if lv not in LEVELS:
            lv = "n/a"
        key = norm_name(contact_name(c))
        stats[lv]["sign_ups"] += 1

        row = consolidated.get(key)
        if not row:
            stats[lv]["no_match"] += 1
            continue

        stats[lv]["exact_match"] += 1
        if row["termination_date"] and in_range(row["termination_date"], start, end):
            stats[lv]["churned"] += 1
        else:
            stats[lv]["retained_exact"] += 1

    by_level = []
    totals = Counter()
    for lv in LEVELS:
        s = stats[lv]
        su = s["sign_ups"]
        ex = s["exact_match"]
        ch = s["churned"]
        nm = s["no_match"]
        headline_ret = ((su - ch) / su * 100) if su else None
        exact_ret = (s["retained_exact"] / ex * 100) if ex else None
        match_rate = (ex / su * 100) if su else None
        for k, v in s.items():
            totals[k] += v
        by_level.append(
            {
                "level": lv,
                "sign_ups": su,
                "exact_match": ex,
                "match_rate_pct": match_rate,
                "churned": ch,
                "headline_retention_pct": headline_ret,
                "exact_match_retention_pct": exact_ret,
                "unmatched_assumed_retained": nm,
            }
        )

    t_su = totals["sign_ups"]
    t_ch = totals["churned"]
    t_ex = totals["exact_match"]
    return {
        "truncated": truncated,
        "terminations_all": term_all,
        "total_sign_ups": t_su,
        "total_exact_match": t_ex,
        "total_churned": t_ch,
        "headline_retention_pct": ((t_su - t_ch) / t_su * 100) if t_su else None,
        "exact_match_retention_pct": ((t_ex - t_ch) / t_ex * 100) if t_ex else None,
        "match_rate_pct": (t_ex / t_su * 100) if t_su else None,
        "by_level": by_level,
    }


def build_report(preset_key: str = "sept-apr-2026") -> dict:
    label, start, end = PERIOD_PRESETS[preset_key]
    consolidated = load_consolidated_by_name()
    acquisition = build_acquisition(start, end)
    retention = build_retention(start, end, consolidated)
    return {
        "preset": preset_key,
        "label": label,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "acquisition": acquisition,
        "retention": retention,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def clear_caches() -> None:
    global _consolidated_cache
    _consolidated_cache = None
    _confirmed_ids_cache.clear()


def build_all_presets() -> dict[str, dict]:
    consolidated = load_consolidated_by_name()
    out = {}
    for key, (label, start, end) in PERIOD_PRESETS.items():
        out[key] = {
            "preset": key,
            "label": label,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "acquisition": build_acquisition(start, end),
            "retention": build_retention(start, end, consolidated),
        }
    return out
