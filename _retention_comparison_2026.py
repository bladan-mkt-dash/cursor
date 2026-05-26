"""Retention + sign-ups comparison: Jan-Apr 2026 vs Sept-Dec 2025."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime
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

SPREADSHEET_ID = "18fDtd3xEHHXC6sCeRFFadSwcshk4SJqFUG6aV006DhU"
SHEET = "Consolidated Data"
TOKEN_PATH = Path.home() / ".config" / "mcp-google-sheets" / "token.json"
LEVELS = ("Standard", "Gold", "Silver", "Platinum", "n/a")

PERIODS = {
    "Jan–Apr 2026": (date(2026, 1, 1), date(2026, 4, 30)),
    "Sept–Dec 2025": (date(2025, 9, 1), date(2025, 12, 31)),
    "Sept–Apr 2026 (8 mo)": (date(2025, 9, 1), date(2026, 4, 30)),
}


def _credentials() -> Credentials:
    info = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    return Credentials.from_authorized_user_info(info, info["scopes"])


def _parse_date(val) -> date | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.upper() in {"FALSE", "TRUE", "N/A", "#N/A", "-"}:
        return None
    ts = pd.to_datetime(s, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date()


def _normalize_level(raw: str) -> str:
    v = (raw or "").strip()
    for level in LEVELS:
        if level != "n/a" and v.casefold() == level.casefold():
            return level
    return "n/a" if not v else v


def load_consolidated() -> pd.DataFrame:
    sheets = build("sheets", "v4", credentials=_credentials())
    rows = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=SPREADSHEET_ID, range=f"'{SHEET}'")
        .execute()
        .get("values", [])
    )
    header = rows[0]
    col = {re.sub(r"\s+", " ", h.replace("\n", " ")).strip(): i for i, h in enumerate(header)}
    si = col["Start Date (1st Appt)"]
    ti = col["Date of Termination"]
    li = col["Membership Level"]

    records = []
    for row in rows[1:]:
        if len(row) <= col["Patient"] or not str(row[col["Patient"]]).strip():
            continue
        records.append(
            {
                "start_date": _parse_date(row[si] if len(row) > si else None),
                "termination_date": _parse_date(row[ti] if len(row) > ti else None),
                "membership_level": _normalize_level(row[li] if len(row) > li else ""),
            }
        )
    return pd.DataFrame(records)


def in_range(d: date | None, start: date, end: date) -> bool:
    return d is not None and start <= d <= end


def ghl_signups(start: date, end: date) -> dict:
    su_field = resolve_sign_up_date_custom_field_id()
    ml_field = resolve_membership_level_custom_field_id()
    contacts, truncated, total = search_contacts_custom_field_date_range(
        su_field, start.isoformat(), end.isoformat(), max_pages=500
    )
    by_level: Counter[str] = Counter()
    for c in contacts:
        lvl = _normalize_level(
            contact_custom_field_value(c, ml_field) if ml_field else ""
        )
        if lvl not in LEVELS:
            lvl = "n/a"
        by_level[lvl] += 1
    core = ("Standard", "Gold", "Silver", "Platinum")
    return {
        "total": len(contacts),
        "by_level": dict(by_level),
        "core_total": sum(by_level.get(l, 0) for l in core),
        "truncated": truncated,
        "api_total": total,
    }


def sheet_metrics(df: pd.DataFrame, start: date, end: date) -> dict:
    cohort = df[df["start_date"].apply(lambda d: in_range(d, start, end))]
    term_all = df[df["termination_date"].apply(lambda d: in_range(d, start, end))]

    started = len(cohort)
    term_in_cohort = int(
        cohort["termination_date"].apply(lambda d: in_range(d, start, end)).sum()
    )
    retained_eop = int(
        cohort.apply(
            lambda r: r["termination_date"] is None or r["termination_date"] > end,
            axis=1,
        ).sum()
    )
    retention_through = ((started - term_in_cohort) / started * 100) if started else 0
    retained_pct = (retained_eop / started * 100) if started else 0

    tenures = []
    for _, r in cohort.iterrows():
        if in_range(r["termination_date"], start, end) and r["start_date"] and r["termination_date"]:
            if r["termination_date"] >= r["start_date"]:
                tenures.append((r["termination_date"] - r["start_date"]).days)

    by_level: dict[str, dict] = {}
    for level in LEVELS:
        sub = cohort[cohort["membership_level"] == level]
        n = len(sub)
        if n == 0 and level not in ("Standard", "n/a"):
            continue
        ti = int(sub["termination_date"].apply(lambda d: in_range(d, start, end)).sum()) if n else 0
        by_level[level] = {
            "started": n,
            "term_in_period": ti,
            "retention_through": ((n - ti) / n * 100) if n else 0,
        }

    term_by_level = Counter(term_all["membership_level"])

    return {
        "started": started,
        "terminations_all": len(term_all),
        "term_in_cohort": term_in_cohort,
        "churn_rate_cohort": (term_in_cohort / started * 100) if started else 0,
        "retention_through": retention_through,
        "retained_eop": retained_eop,
        "retained_eop_pct": retained_pct,
        "median_tenure_days": float(pd.Series(tenures).median()) if tenures else None,
        "avg_tenure_days": float(pd.Series(tenures).mean()) if tenures else None,
        "by_level": by_level,
        "term_by_level": dict(term_by_level),
    }


def delta(a: float | None, b: float | None, *, pp: bool = True) -> str:
    if a is None or b is None:
        return "—"
    d = a - b
    sign = "+" if d > 0 else ""
    suffix = " pp" if pp else ""
    return f"{sign}{d:.1f}{suffix}"


def print_period(name: str, ghl: dict, sheet: dict) -> None:
    print(f"\n{'=' * 78}")
    print(name)
    print(f"{'=' * 78}")
    print("\nGoHighLevel — Sign Ups (Sign Up Date in period)")
    print(f"  Total sign-ups: {ghl['total']:,}")
    for lvl in ("Standard", "Gold", "Silver", "Platinum", "n/a"):
        n = ghl["by_level"].get(lvl, 0)
        if n:
            print(f"    {lvl}: {n:,}")
    if ghl["truncated"]:
        print("  WARNING: GHL pagination cap hit")

    print("\nConsolidated Data — Cohort (Start Date 1st Appt in period)")
    print(f"  Members started:              {sheet['started']:,}")
    print(f"  Terminated during period:     {sheet['term_in_cohort']:,}  ({sheet['churn_rate_cohort']:.1f}% churn)")
    print(f"  Retention through period:     {sheet['retention_through']:.1f}%")
    print(f"  Retained at period end:       {sheet['retained_eop']:,}  ({sheet['retained_eop_pct']:.1f}%)")
    if sheet["median_tenure_days"] is not None:
        print(f"  Median tenure to termination: {sheet['median_tenure_days']:.0f} days")
        print(f"  Avg tenure to termination:    {sheet['avg_tenure_days']:.0f} days")

    print(f"\n  All terminations in period (any start date): {sheet['terminations_all']:,}")

    print("\n  By membership level (cohort):")
    print(f"  {'Level':<12} {'Started':>8} {'Term in period':>14} {'Retention %':>12}")
    print("  " + "-" * 50)
    for lvl in LEVELS:
        bl = sheet["by_level"].get(lvl)
        if not bl or bl["started"] == 0:
            continue
        print(
            f"  {lvl:<12} {bl['started']:>8,} {bl['term_in_period']:>14,} "
            f"{bl['retention_through']:>11.1f}%"
        )


def main() -> None:
    df = load_consolidated()
    results = {}
    for name, (start, end) in PERIODS.items():
        results[name] = {
            "ghl": ghl_signups(start, end),
            "sheet": sheet_metrics(df, start, end),
        }

    for name in PERIODS:
        print_period(name, results[name]["ghl"], results[name]["sheet"])

    j = results["Jan–Apr 2026"]["sheet"]
    s = results["Sept–Dec 2025"]["sheet"]
    jg = results["Jan–Apr 2026"]["ghl"]
    sg = results["Sept–Dec 2025"]["ghl"]
    full = results["Sept–Apr 2026 (8 mo)"]["sheet"]
    fullg = results["Sept–Apr 2026 (8 mo)"]["ghl"]

    print(f"\n{'=' * 78}")
    print("COMPARISON: Jan–Apr 2026 vs Sept–Dec 2025 (prior 4 months)")
    print(f"{'=' * 78}")
    print(f"\n{'Metric':<40} {'Sept–Dec 2025':>14} {'Jan–Apr 2026':>14} {'Change':>12}")
    print("-" * 82)
    rows = [
        ("GHL sign-ups", sg["total"], jg["total"], False),
        ("Consolidated — started (1st Appt)", s["started"], j["started"], False),
        ("Terminations (cohort, in period)", s["term_in_cohort"], j["term_in_cohort"], False),
        ("Cohort churn rate", s["churn_rate_cohort"], j["churn_rate_cohort"], True),
        ("Retention through period", s["retention_through"], j["retention_through"], True),
        ("Retained at period end %", s["retained_eop_pct"], j["retained_eop_pct"], True),
        ("All terminations (any start)", s["terminations_all"], j["terminations_all"], False),
        ("Median tenure to term (days)", s["median_tenure_days"], j["median_tenure_days"], False),
    ]
    for label, v1, v2, is_pct in rows:
        if is_pct:
            print(f"{label:<40} {v1:>13.1f}% {v2:>13.1f}% {delta(v2, v1):>12}")
        elif isinstance(v1, float) and v1 is not None:
            print(f"{label:<40} {v1:>14.1f} {v2:>14.1f} {delta(v2, v1, pp=False):>12}")
        else:
            v1s = f"{v1:,}" if v1 is not None else "—"
            v2s = f"{v2:,}" if v2 is not None else "—"
            ch = ""
            if v1 is not None and v2 is not None and isinstance(v1, int):
                d = v2 - v1
                ch = f"{'+' if d > 0 else ''}{d:,}"
            print(f"{label:<40} {v1s:>14} {v2s:>14} {ch:>12}")

    print(f"\n{'=' * 78}")
    print("VERDICT")
    print(f"{'=' * 78}")
    ret_chg = j["retention_through"] - s["retention_through"]
    churn_chg = j["churn_rate_cohort"] - s["churn_rate_cohort"]
    if ret_chg > 0:
        direction = "UP"
        detail = f"Retention through period rose {ret_chg:.1f} pp ({s['retention_through']:.1f}% → {j['retention_through']:.1f}%)"
    elif ret_chg < 0:
        direction = "DOWN"
        detail = f"Retention through period fell {abs(ret_chg):.1f} pp ({s['retention_through']:.1f}% → {j['retention_through']:.1f}%)"
    else:
        direction = "UNCHANGED"
        detail = "Retention through period unchanged"

    print(f"  Retention trend (4-mo vs prior 4-mo): {direction}")
    print(f"  {detail}")
    print(f"  Cohort churn: {s['churn_rate_cohort']:.1f}% → {j['churn_rate_cohort']:.1f}% ({delta(-churn_chg, 0, pp=False)} improvement)" if churn_chg < 0 else f"  Cohort churn: {s['churn_rate_cohort']:.1f}% → {j['churn_rate_cohort']:.1f}% ({'+' if churn_chg>0 else ''}{churn_chg:.1f} pp worse)")

    print(f"\n  Context vs full 8-month window (Sept 2025 – Apr 2026):")
    print(f"    Sign-ups: {fullg['total']:,} total  |  Jan–Apr 2026 = {jg['total']:,} ({jg['total']/fullg['total']*100 if fullg['total'] else 0:.0f}% of period)")
    print(f"    Retention: {full['retention_through']:.1f}% (8 mo)  vs  {j['retention_through']:.1f}% (Jan–Apr 2026 cohort only)")

    print("\n  NOTE: Consolidated Data is a termination log (no active/no-term rows).")
    print("  GHL sign-ups include all active members; sheet cohort is a subset.")


if __name__ == "__main__":
    main()
