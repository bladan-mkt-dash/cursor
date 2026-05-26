"""Retention as % of GHL sign-ups across multiple periods."""

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
    "Sept–Dec 2025": (date(2025, 9, 1), date(2025, 12, 31)),
    "Jan–Apr 2026": (date(2026, 1, 1), date(2026, 4, 30)),
    "Sept 2025 – Apr 2026": (date(2025, 9, 1), date(2026, 4, 30)),
}


def parse_date(val) -> date | None:
    if not val or str(val).strip().upper() in {"FALSE", "TRUE", "", "N/A"}:
        return None
    ts = pd.to_datetime(val, errors="coerce")
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


def load_consolidated() -> list[dict]:
    info = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    sheets = build("sheets", "v4", credentials=Credentials.from_authorized_user_info(info, info["scopes"]))
    rows = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=SPREADSHEET_ID, range="Consolidated Data")
        .execute()
        .get("values", [])
    )
    header = rows[0]
    col = {re.sub(r"\s+", " ", h.replace("\n", " ")).strip(): i for i, h in enumerate(header)}
    pi = col["Patient"]
    si = col["Start Date (1st Appt)"]
    ti = col["Date of Termination"]
    li = col["Membership Level"]
    out = []
    for r in rows[1:]:
        name = str(r[pi]).strip() if len(r) > pi else ""
        if not name:
            continue
        out.append(
            {
                "name_key": norm_name(name),
                "start_date": parse_date(r[si] if len(r) > si else None),
                "termination_date": parse_date(r[ti] if len(r) > ti else None),
                "level": norm_level(r[li] if len(r) > li else ""),
            }
        )
    return out


def load_ghl_signups(start: date, end: date) -> tuple[list[dict], bool]:
    su = resolve_sign_up_date_custom_field_id()
    ml = resolve_membership_level_custom_field_id()
    contacts, truncated, _ = search_contacts_custom_field_date_range(
        su, start.isoformat(), end.isoformat(), max_pages=500
    )
    out = []
    for c in contacts:
        level = norm_level(contact_custom_field_value(c, ml) if ml else "")
        if level not in LEVELS:
            level = "n/a"
        out.append(
            {
                "name_key": norm_name(contact_name(c)),
                "level": level,
                "sign_up_date": parse_date(contact_custom_field_value(c, su) if su else None),
            }
        )
    return out, truncated


def is_plausible_match(start: date, end: date, sign_up: date | None, row: dict) -> bool:
    term = row["termination_date"]
    appt = row["start_date"]
    if term and term >= start:
        return True
    if appt and in_range(appt, start, end):
        return True
    lookback = date(start.year - 1, start.month, 1) if start.month > 1 else date(start.year - 1, 12, 1)
    if appt and appt >= lookback and term and term >= start:
        return True
    return False


def compute_period(
    name: str,
    start: date,
    end: date,
    consolidated: list[dict],
) -> dict:
    signups, truncated = load_ghl_signups(start, end)
    by_name: dict[str, list[dict]] = {}
    for row in consolidated:
        k = row["name_key"]
        if k:
            by_name.setdefault(k, []).append(row)

    stats: dict[str, Counter] = {lv: Counter() for lv in LEVELS}
    for s in signups:
        lv = s["level"]
        stats[lv]["sign_ups"] += 1
        candidates = by_name.get(s["name_key"], [])
        match = next(
            (r for r in candidates if is_plausible_match(start, end, s["sign_up_date"], r)),
            None,
        )
        if not match:
            stats[lv]["not_matched"] += 1
            continue
        if match["termination_date"] and in_range(match["termination_date"], start, end):
            stats[lv]["churned"] += 1

    by_level = {}
    totals = Counter()
    for lv in LEVELS:
        su = stats[lv]["sign_ups"]
        churn = stats[lv]["churned"]
        ret = su - churn
        pct = (ret / su * 100) if su else None
        row = {
            "sign_ups": su,
            "churned": churn,
            "retained": ret,
            "retention_pct": pct,
            "not_matched": stats[lv]["not_matched"],
        }
        by_level[lv] = row
        totals["sign_ups"] += su
        totals["churned"] += churn
        totals["not_matched"] += stats[lv]["not_matched"]

    totals["retained"] = totals["sign_ups"] - totals["churned"]
    totals["retention_pct"] = (
        totals["retained"] / totals["sign_ups"] * 100 if totals["sign_ups"] else None
    )
    return {
        "name": name,
        "start": start,
        "end": end,
        "by_level": by_level,
        "totals": dict(totals),
        "truncated": truncated,
    }


def print_period(result: dict) -> None:
    print(f"\n{'=' * 88}")
    print(f"{result['name']}  ({result['start'].isoformat()} to {result['end'].isoformat()})")
    if result["truncated"]:
        print("WARNING: GHL sign-up search hit pagination cap")
    print(f"{'=' * 88}")
    print(
        f"{'Level':<12} {'Sign-ups':>9} {'Churned':>8} {'Retained':>9} "
        f"{'Retention %':>12} {'Not matched':>12}"
    )
    print("-" * 88)
    for lv in LEVELS:
        r = result["by_level"][lv]
        if r["sign_ups"] == 0 and lv not in ("Standard", "Gold", "Silver", "Platinum", "n/a"):
            continue
        pct = f"{r['retention_pct']:.1f}%" if r["retention_pct"] is not None else "-"
        print(
            f"{lv:<12} {r['sign_ups']:>9,} {r['churned']:>8,} {r['retained']:>9,} "
            f"{pct:>12} {r['not_matched']:>12,}"
        )
    t = result["totals"]
    print("-" * 88)
    print(
        f"{'TOTAL':<12} {t['sign_ups']:>9,} {t['churned']:>8,} {t['retained']:>9,} "
        f"{t['retention_pct']:>11.1f}% {t['not_matched']:>12,}"
    )


def print_comparison(results: list[dict]) -> None:
    print(f"\n{'=' * 88}")
    print("COMPARISON — Retention % of sign-ups by period")
    print(f"{'=' * 88}")

    headers = [r["name"] for r in results]
    short = ["Sept-Dec 2025", "Jan-Apr 2026", "Sept-Apr 2026"]

    print(f"\n{'Level':<12} {'Metric':<10}", end="")
    for h in short:
        print(f"{h:>18}", end="")
    print()
    print("-" * 88)

    for lv in LEVELS:
        has_data = any(r["by_level"][lv]["sign_ups"] > 0 for r in results)
        if not has_data:
            continue
        print(f"{lv:<12} {'Sign-ups':<10}", end="")
        for r in results:
            print(f"{r['by_level'][lv]['sign_ups']:>18,}", end="")
        print()
        print(f"{'':12} {'Churned':<10}", end="")
        for r in results:
            print(f"{r['by_level'][lv]['churned']:>18,}", end="")
        print()
        print(f"{'':12} {'Retention':<10}", end="")
        for r in results:
            pct = r["by_level"][lv]["retention_pct"]
            s = f"{pct:.1f}%" if pct is not None else "-"
            print(f"{s:>18}", end="")
        print()
        print("-" * 88)

    print(f"{'TOTAL':<12} {'Sign-ups':<10}", end="")
    for r in results:
        print(f"{r['totals']['sign_ups']:>18,}", end="")
    print()
    print(f"{'':12} {'Churned':<10}", end="")
    for r in results:
        print(f"{r['totals']['churned']:>18,}", end="")
    print()
    print(f"{'':12} {'Retention':<10}", end="")
    for r in results:
        print(f"{r['totals']['retention_pct']:>17.1f}%", end="")
    print()
    print()

    s, j, f = results[0]["totals"]["retention_pct"], results[1]["totals"]["retention_pct"], results[2]["totals"]["retention_pct"]
    chg = j - s
    print("Trend:")
    print(f"  Sept-Dec 2025:     {s:.1f}% retention ({results[0]['totals']['churned']} churned / {results[0]['totals']['sign_ups']} sign-ups)")
    print(f"  Jan-Apr 2026:      {j:.1f}% retention ({results[1]['totals']['churned']} churned / {results[1]['totals']['sign_ups']} sign-ups)")
    print(f"  Sept 2025-Apr 2026: {f:.1f}% retention ({results[2]['totals']['churned']} churned / {results[2]['totals']['sign_ups']} sign-ups)")
    if chg > 0:
        print(f"  Jan-Apr 2026 vs Sept-Dec 2025: retention UP {chg:.1f} pp")
    elif chg < 0:
        print(f"  Jan-Apr 2026 vs Sept-Dec 2025: retention DOWN {abs(chg):.1f} pp")
    else:
        print("  Jan-Apr 2026 vs Sept-Dec 2025: retention unchanged")


def main() -> None:
    consolidated = load_consolidated()
    results = [
        compute_period(pname, start, end, consolidated)
        for pname, (start, end) in PERIODS.items()
    ]

    print("Retention % = (Sign-ups - Churned in period) / Sign-ups x 100")
    print("Sign-ups: GHL Sign Up Date in period | Churned: name-matched termination in period")

    for r in results:
        print_period(r)
    print_comparison(results)


if __name__ == "__main__":
    main()
