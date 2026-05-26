"""Exact name matches by membership level."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ghl_client import (
    contact_custom_field_value,
    resolve_membership_level_custom_field_id,
    resolve_sign_up_date_custom_field_id,
    search_contacts_custom_field_date_range,
)

LEVELS = ("Standard", "Gold", "Silver", "Platinum", "n/a")
SPREADSHEET_ID = "18fDtd3xEHHXC6sCeRFFadSwcshk4SJqFUG6aV006DhU"
TOKEN_PATH = Path.home() / ".config" / "mcp-google-sheets" / "token.json"

PERIODS = {
    "Sept-Dec 2025": (date(2025, 9, 1), date(2025, 12, 31)),
    "Jan-Apr 2026": (date(2026, 1, 1), date(2026, 4, 30)),
    "Sept 2025-Apr 2026": (date(2025, 9, 1), date(2026, 4, 30)),
}


def norm_name(raw: str) -> str:
    s = unicodedata.normalize("NFKD", (raw or "").strip())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9\s]", " ", s.casefold())
    return re.sub(r"\s+", " ", s).strip()


def norm_level(raw: str) -> str:
    v = (raw or "").strip()
    for level in ("Standard", "Gold", "Silver", "Platinum"):
        if v.casefold() == level.casefold():
            return level
    return "n/a" if not v else v


def contact_name(c: dict) -> str:
    fn = (c.get("firstName") or "").strip()
    ln = (c.get("lastName") or "").strip()
    if fn or ln:
        return f"{fn} {ln}".strip()
    return (c.get("contactName") or c.get("name") or "").strip()


def parse_date(val):
    if not val:
        return None
    t = pd.to_datetime(val, errors="coerce")
    return None if pd.isna(t) else t.date()


def in_range(d, start, end):
    return d is not None and start <= d <= end


def load_consolidated():
    info = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    sheets = build("sheets", "v4", credentials=Credentials.from_authorized_user_info(info, info["scopes"]))
    rows = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range="Consolidated Data"
    ).execute()["values"]
    header = rows[0]
    col = {re.sub(r"\s+", " ", h.replace("\n", " ")).strip(): i for i, h in enumerate(header)}
    out = {}
    for r in rows[1:]:
        name = str(r[col["Patient"]]).strip() if len(r) > col["Patient"] else ""
        if not name:
            continue
        key = norm_name(name)
        out[key] = {
            "name": name,
            "term": parse_date(r[col["Date of Termination"]] if len(r) > col["Date of Termination"] else None),
            "level": norm_level(r[col["Membership Level"]] if len(r) > col["Membership Level"] else ""),
        }
    return out


def analyze_period(label, start, end, consolidated):
    su = resolve_sign_up_date_custom_field_id()
    ml = resolve_membership_level_custom_field_id()
    contacts, _, _ = search_contacts_custom_field_date_range(
        su, start.isoformat(), end.isoformat(), max_pages=500
    )

    stats = {lv: Counter() for lv in LEVELS}
    matched_examples = {lv: [] for lv in LEVELS}

    for c in contacts:
        lv = norm_level(contact_custom_field_value(c, ml) if ml else "")
        if lv not in LEVELS:
            lv = "n/a"
        ghl_name = contact_name(c)
        key = norm_name(ghl_name)
        stats[lv]["sign_ups"] += 1

        row = consolidated.get(key)
        if not row:
            stats[lv]["no_exact_match"] += 1
            continue

        stats[lv]["exact_match"] += 1
        if row["term"] and in_range(row["term"], start, end):
            stats[lv]["churned"] += 1
            if len(matched_examples[lv]) < 3:
                matched_examples[lv].append((ghl_name, row["name"], "churned", row["term"]))
        else:
            stats[lv]["retained"] += 1
            if len(matched_examples[lv]) < 3:
                matched_examples[lv].append((ghl_name, row["name"], "retained", row["term"]))

    print(f"\n{'=' * 92}")
    print(f"{label}  ({start.isoformat()} to {end.isoformat()})")
    print("Exact match only — normalized full name must match exactly between GHL and Consolidated Data")
    print(f"{'=' * 92}")
    print(
        f"{'Level':<12} {'Sign-ups':>9} {'Exact match':>12} {'Match %':>8} "
        f"{'Churned':>8} {'Retained':>9} {'Retention*':>11}"
    )
    print("-" * 92)

    totals = Counter()
    for lv in LEVELS:
        s = stats[lv]
        su_n = s["sign_ups"]
        if su_n == 0 and lv not in ("Standard", "Gold", "Silver", "Platinum", "n/a"):
            continue
        ex = s["exact_match"]
        ch = s["churned"]
        ret = s["retained"]
        match_pct = (ex / su_n * 100) if su_n else 0
        ret_pct = (ret / ex * 100) if ex else None
        ret_s = f"{ret_pct:.1f}%" if ret_pct is not None else "-"
        for k, v in s.items():
            totals[k] += v
        print(
            f"{lv:<12} {su_n:>9,} {ex:>12,} {match_pct:>7.1f}% "
            f"{ch:>8,} {ret:>9,} {ret_s:>11}"
        )

    print("-" * 92)
    t_ex = totals["exact_match"]
    t_ret_pct = (totals["retained"] / t_ex * 100) if t_ex else 0
    t_match_pct = (t_ex / totals["sign_ups"] * 100) if totals["sign_ups"] else 0
    print(
        f"{'TOTAL':<12} {totals['sign_ups']:>9,} {t_ex:>12,} {t_match_pct:>7.1f}% "
        f"{totals['churned']:>8,} {totals['retained']:>9,} {t_ret_pct:>10.1f}%"
    )
    print("* Retention among exact matches only (not overall sign-ups)")


def main():
    consolidated = load_consolidated()
    for label, (start, end) in PERIODS.items():
        analyze_period(label, start, end, consolidated)


if __name__ == "__main__":
    main()
