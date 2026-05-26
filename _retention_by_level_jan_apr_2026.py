"""Retention by membership level: Jan 1 - Apr 30, 2026."""

from __future__ import annotations

import json
import re
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

START = date(2026, 1, 1)
END = date(2026, 4, 30)
LEVELS = ("Standard", "Gold", "Silver", "Platinum", "n/a")
SPREADSHEET_ID = "18fDtd3xEHHXC6sCeRFFadSwcshk4SJqFUG6aV006DhU"
TOKEN_PATH = Path.home() / ".config" / "mcp-google-sheets" / "token.json"


def parse_date(val) -> date | None:
    if not val or str(val).strip().upper() in {"FALSE", "TRUE", "", "N/A"}:
        return None
    ts = pd.to_datetime(val, errors="coerce")
    return None if pd.isna(ts) else ts.date()


def in_range(d: date | None) -> bool:
    return d is not None and START <= d <= END


def norm_level(raw: str) -> str:
    v = (raw or "").strip()
    for level in ("Standard", "Gold", "Silver", "Platinum"):
        if v.casefold() == level.casefold():
            return level
    return "n/a" if not v else v


def load_cohort() -> list[dict]:
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
    si, ti, li = col["Start Date (1st Appt)"], col["Date of Termination"], col["Membership Level"]
    out = []
    for r in rows[1:]:
        st = parse_date(r[si] if len(r) > si else None)
        te = parse_date(r[ti] if len(r) > ti else None)
        if not in_range(st):
            continue
        out.append({"level": norm_level(r[li] if len(r) > li else ""), "start": st, "term": te})
    return out


def load_terminations() -> Counter[str]:
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
    ti, li = col["Date of Termination"], col["Membership Level"]
    counts: Counter[str] = Counter()
    for r in rows[1:]:
        te = parse_date(r[ti] if len(r) > ti else None)
        if in_range(te):
            counts[norm_level(r[li] if len(r) > li else "")] += 1
    return counts


def ghl_signups() -> Counter[str]:
    su = resolve_sign_up_date_custom_field_id()
    ml = resolve_membership_level_custom_field_id()
    contacts, _, _ = search_contacts_custom_field_date_range(
        su, START.isoformat(), END.isoformat(), max_pages=500
    )
    counts: Counter[str] = Counter()
    for c in contacts:
        lv = norm_level(contact_custom_field_value(c, ml) if ml else "")
        if lv not in LEVELS:
            lv = "n/a"
        counts[lv] += 1
    return counts


def main() -> None:
    cohort = load_cohort()
    term_all = load_terminations()
    ghl = ghl_signups()

    print("Period: Jan 1 - Apr 30, 2026")
    print("Retention = cohort members (Start Date 1st Appt in period) not terminated in period")
    print("=" * 95)
    hdr = (
        f"{'Level':<12} {'GHL Sign-ups':>12} {'Started':>9} {'Term (cohort)':>14} "
        f"{'Retained':>9} {'Churn %':>9} {'Retention %':>12} {'All Terms':>10}"
    )
    print(hdr)
    print("-" * 95)

    totals = {"ghl": 0, "started": 0, "term_c": 0, "retained": 0, "all_term": 0}
    rows_out = []
    for lv in LEVELS:
        sub = [x for x in cohort if x["level"] == lv]
        started = len(sub)
        term_c = sum(1 for x in sub if in_range(x["term"]))
        retained = started - term_c
        retained_eop = sum(1 for x in sub if x["term"] is None or x["term"] > END)
        all_term = term_all.get(lv, 0)
        churn = (term_c / started * 100) if started else None
        ret = (retained / started * 100) if started else None
        ret_eop = (retained_eop / started * 100) if started else None
        g = ghl.get(lv, 0)

        totals["ghl"] += g
        totals["started"] += started
        totals["term_c"] += term_c
        totals["retained"] += retained
        totals["all_term"] += all_term

        rows_out.append((lv, g, started, term_c, retained, retained_eop, churn, ret, ret_eop, all_term))
        churn_s = f"{churn:.1f}%" if churn is not None else "-"
        ret_s = f"{ret:.1f}%" if ret is not None else "-"
        print(
            f"{lv:<12} {g:>12,} {started:>9,} {term_c:>14,} {retained:>9,} "
            f"{churn_s:>9} {ret_s:>12} {all_term:>10,}"
        )

    print("-" * 95)
    t_churn = totals["term_c"] / totals["started"] * 100 if totals["started"] else 0
    t_ret = totals["retained"] / totals["started"] * 100 if totals["started"] else 0
    print(
        f"{'TOTAL':<12} {totals['ghl']:>12,} {totals['started']:>9,} {totals['term_c']:>14,} "
        f"{totals['retained']:>9,} {t_churn:>8.1f}% {t_ret:>11.1f}% {totals['all_term']:>10,}"
    )

    print()
    print("Retained at Apr 30, 2026 (termination date after period end):")
    print(f"{'Level':<12} {'Started':>9} {'Retained EOP':>13} {'Retention EOP %':>16}")
    print("-" * 55)
    for lv, _, started, _, _, retained_eop, _, _, ret_eop, _ in rows_out:
        if started == 0:
            continue
        print(f"{lv:<12} {started:>9,} {retained_eop:>13,} {ret_eop:>15.1f}%")

    print()
    print("Median tenure to termination (cohort members who churned in period):")
    print(f"{'Level':<12} {'Count':>8} {'Median days':>12} {'Avg days':>10}")
    print("-" * 45)
    for lv in LEVELS:
        days = []
        for x in cohort:
            if x["level"] != lv or not in_range(x["term"]) or not x["start"] or not x["term"]:
                continue
            if x["term"] >= x["start"]:
                days.append((x["term"] - x["start"]).days)
        if days:
            s = pd.Series(days)
            print(f"{lv:<12} {len(days):>8,} {s.median():>12.0f} {s.mean():>10.0f}")

    print()
    print("Monthly cohort starts by level:")
    months = ["2026-01", "2026-02", "2026-03", "2026-04"]
    lvl_cols = ("Standard", "Gold", "Silver", "Platinum", "n/a")
    print(f"{'Month':<10}" + "".join(f"{L:>12}" for L in lvl_cols) + f"{'Total':>10}")
    print("-" * 72)
    for ym in months:
        label = pd.Timestamp(ym + "-01").strftime("%b %Y")
        counts = [sum(1 for x in cohort if x["level"] == L and x["start"].strftime("%Y-%m") == ym) for L in lvl_cols]
        print(f"{label:<10}" + "".join(f"{c:>12,}" for c in counts) + f"{sum(counts):>10,}")


if __name__ == "__main__":
    main()
