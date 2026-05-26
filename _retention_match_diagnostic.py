"""Diagnose name-matching quality for retention analysis."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ghl_client import (
    contact_custom_field_value,
    resolve_sign_up_date_custom_field_id,
    search_contacts_custom_field_date_range,
)

START = date(2026, 1, 1)
END = date(2026, 4, 30)
SPREADSHEET_ID = "18fDtd3xEHHXC6sCeRFFadSwcshk4SJqFUG6aV006DhU"
TOKEN_PATH = Path.home() / ".config" / "mcp-google-sheets" / "token.json"


def norm_name(raw: str) -> str:
    s = unicodedata.normalize("NFKD", (raw or "").strip())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9\s]", " ", s.casefold())
    return re.sub(r"\s+", " ", s).strip()


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


def split_name(key: str) -> tuple[str, str]:
    parts = key.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def main() -> None:
    info = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    sheets = build("sheets", "v4", credentials=Credentials.from_authorized_user_info(info, info["scopes"]))
    rows = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range="Consolidated Data"
    ).execute()["values"]
    header = rows[0]
    col = {re.sub(r"\s+", " ", h.replace("\n", " ")).strip(): i for i, h in enumerate(header)}

    consolidated = []
    for r in rows[1:]:
        name = str(r[col["Patient"]]).strip() if len(r) > col["Patient"] else ""
        if not name:
            continue
        term = parse_date(r[col["Date of Termination"]] if len(r) > col["Date of Termination"] else None)
        consolidated.append({"name": name, "key": norm_name(name), "term": term})

    cons_keys = {c["key"] for c in consolidated}
    cons_by_last: dict[str, list] = {}
    for c in consolidated:
        _, ln = split_name(c["key"])
        if ln:
            cons_by_last.setdefault(ln, []).append(c)

    su = resolve_sign_up_date_custom_field_id()
    contacts, _, _ = search_contacts_custom_field_date_range(
        su, START.isoformat(), END.isoformat(), max_pages=500
    )

    exact = fuzzy_last = email_only = no_match = 0
    unmatched_samples = []
    near_miss_samples = []
    termed_in_period_unmatched = []

    for c in contacts:
        ghl_name = contact_name(c)
        key = norm_name(ghl_name)
        email = (c.get("email") or "").strip().casefold()
        fn, ln = split_name(key)

        if key in cons_keys:
            exact += 1
            continue

        # last name match, different first (nickname/misspelling candidate)
        cands = cons_by_last.get(ln, []) if ln else []
        if cands:
            best = max(cands, key=lambda x: SequenceMatcher(None, fn, split_name(x["key"])[0]).ratio())
            ratio = SequenceMatcher(None, fn, split_name(best["key"])[0]).ratio()
            if ln and ratio < 1.0:
                near_miss_samples.append((ghl_name, best["name"], ratio, best["term"]))
            fuzzy_last += 1
            if best["term"] and START <= best["term"] <= END:
                termed_in_period_unmatched.append((ghl_name, best["name"], "last_name_only"))
            continue

        no_match += 1
        if len(unmatched_samples) < 15:
            unmatched_samples.append((ghl_name, email or "(no email)"))

    total = len(contacts)
    churn_in_period = sum(
        1 for c in consolidated if c["term"] and START <= c["term"] <= END
    )

    print("MATCHING DIAGNOSTIC - Jan-Apr 2026 sign-ups")
    print("=" * 70)
    print(f"GHL sign-ups: {total}")
    print(f"Exact normalized name match: {exact} ({exact/total*100:.1f}%)")
    print(f"Same last name, different first (NOT counted as churn): {fuzzy_last}")
    print(f"No last-name match at all: {no_match} ({no_match/total*100:.1f}%)")
    print()
    print("CURRENT METHOD ASSUMPTION:")
    print(f"  Unmatched sign-ups counted as RETAINED: {total - exact} ({(total-exact)/total*100:.1f}%)")
    print(f"  Only exact matches can be counted as churned: max {exact}")
    print(f"  Actual churned counted in report: 12")
    print(f"  Terminations in Consolidated Data (Jan-Apr, all members): {churn_in_period}")
    print()
    print("RETENTION BIAS ESTIMATE:")
    print(f"  Reported retention: 96.4% (12 churned / 334 sign-ups)")
    print(f"  If all {churn_in_period} sheet terminations were sign-ups: max churn ~{churn_in_period}")
    print(f"  Floor retention (worst case): {(total-churn_in_period)/total*100:.1f}%")
    print()
    print("Near-miss examples (GHL name -> Sheet name, first-name similarity):")
    for g, s, r, t in sorted(near_miss_samples, key=lambda x: -x[2])[:12]:
        term_s = t.isoformat() if t else "?"
        print(f"  {g!r} -> {s!r}  (first-name match {r:.0%}, term {term_s})")
    print()
    print("Unmatched samples (no shared last name):")
    for g, e in unmatched_samples[:10]:
        print(f"  {g!r}  email: {e}")


if __name__ == "__main__":
    main()
