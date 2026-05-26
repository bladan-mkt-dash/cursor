"""Retention analysis from Consolidated Data sheet."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SPREADSHEET_ID = "18fDtd3xEHHXC6sCeRFFadSwcshk4SJqFUG6aV006DhU"
SHEET = "Consolidated Data"
TOKEN_PATH = Path.home() / ".config" / "mcp-google-sheets" / "token.json"

PERIOD_START = date(2025, 9, 1)
PERIOD_END = date(2026, 4, 30)
LEVELS = ("Standard", "Gold", "Silver", "Platinum")


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
        if v.casefold() == level.casefold():
            return level
    if not v:
        return "n/a"
    return v


def load_data() -> pd.DataFrame:
    sheets = build("sheets", "v4", credentials=_credentials())
    result = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=SPREADSHEET_ID, range=f"'{SHEET}'")
        .execute()
    )
    rows = result.get("values", [])
    if not rows:
        raise ValueError("No data returned")

    header = rows[0]
    # Normalize header names
    col_map = {}
    for i, h in enumerate(header):
        key = re.sub(r"\s+", " ", str(h).replace("\n", " ")).strip()
        col_map[key] = i

    def col(name: str) -> int:
        for k, i in col_map.items():
            if name.lower() in k.lower():
                return i
        raise KeyError(name)

    idx_patient = col("Patient")
    idx_start = col("Start Date")
    idx_term = col("Date of Termination")
    idx_level = col("Membership Level")
    idx_home = col("Membership Home") if any("Membership Home" in k for k in col_map) else None
    idx_days = col("Total Days") if any("Total Days" in k for k in col_map) else None

    records = []
    for row in rows[1:]:
        if len(row) <= idx_patient or not str(row[idx_patient]).strip():
            continue
        start = _parse_date(row[idx_start] if len(row) > idx_start else None)
        term = _parse_date(row[idx_term] if len(row) > idx_term else None)
        level = _normalize_level(row[idx_level] if len(row) > idx_level else "")
        home = (
            str(row[idx_home]).strip()
            if idx_home is not None and len(row) > idx_home
            else ""
        )
        records.append(
            {
                "patient": str(row[idx_patient]).strip(),
                "start_date": start,
                "termination_date": term,
                "membership_level": level,
                "membership_home": home,
            }
        )
    return pd.DataFrame(records)


def in_period(d: date | None) -> bool:
    return d is not None and PERIOD_START <= d <= PERIOD_END


def retained_at_period_end(start: date, term: date | None) -> bool:
    """Member started in cohort period and had not terminated by period end."""
    if term is None:
        return True
    return term > PERIOD_END


def main(df: pd.DataFrame) -> None:
    print(f"Loaded {len(df):,} rows from '{SHEET}'")
    print(f"Period: {PERIOD_START.isoformat()} through {PERIOD_END.isoformat()}")
    print()

    # Cohort: Start Date (1st Appt) in period
    cohort = df[df["start_date"].apply(in_period)].copy()
    cohort["retained"] = cohort.apply(
        lambda r: retained_at_period_end(r["start_date"], r["termination_date"]),
        axis=1,
    )
    cohort["terminated_in_period"] = cohort["termination_date"].apply(in_period)

    total_started = len(cohort)
    total_retained = int(cohort["retained"].sum())
    total_churned_in_period = int(cohort["terminated_in_period"].sum())
    total_churned_by_end = total_started - total_retained

    print("=" * 72)
    print("COHORT RETENTION (Start Date in period)")
    print("=" * 72)
    print(f"Members started (1st Appt):     {total_started:>6,}")
    print(f"Still active at {PERIOD_END}:       {total_retained:>6,}")
    print(f"Terminated on/before period end: {total_churned_by_end:>6,}")
    print(f"Terminated during period:        {total_churned_in_period:>6,}")
    if total_started:
        print(f"Retention rate (end of period):  {total_retained/total_started*100:>6.1f}%")
        print(f"Churn rate (terminated in period): {total_churned_in_period/total_started*100:>4.1f}%")
    print()

    # By membership level
    print("=" * 72)
    print("RETENTION BY MEMBERSHIP LEVEL")
    print("=" * 72)
    print(
        f"{'Level':<12} {'Started':>8} {'Retained':>9} {'Churned':>8} "
        f"{'Term in period':>14} {'Retention %':>12}"
    )
    print("-" * 72)
    level_rows = []
    for level in (*LEVELS, "n/a"):
        sub = cohort[cohort["membership_level"] == level]
        n = len(sub)
        if n == 0 and level != "Standard":
            continue
        ret = int(sub["retained"].sum()) if n else 0
        churn_end = n - ret
        term_p = int(sub["terminated_in_period"].sum()) if n else 0
        pct = (ret / n * 100) if n else 0
        level_rows.append((level, n, ret, churn_end, term_p, pct))
        print(
            f"{level:<12} {n:>8,} {ret:>9,} {churn_end:>8,} "
            f"{term_p:>14,} {pct:>11.1f}%"
        )

    other = cohort[~cohort["membership_level"].isin((*LEVELS, "n/a"))]
    if len(other):
        n = len(other)
        ret = int(other["retained"].sum())
        term_p = int(other["terminated_in_period"].sum())
        print(
            f"{'Other':<12} {n:>8,} {ret:>9,} {n-ret:>8,} "
            f"{term_p:>14,} {(ret/n*100 if n else 0):>11.1f}%"
        )

    print("-" * 72)
    print(
        f"{'TOTAL':<12} {total_started:>8,} {total_retained:>9,} "
        f"{total_churned_by_end:>8,} {total_churned_in_period:>14,} "
        f"{(total_retained/total_started*100 if total_started else 0):>11.1f}%"
    )
    print()

    # Monthly cohort retention
    cohort["start_month"] = cohort["start_date"].apply(
        lambda d: d.strftime("%Y-%m") if d else None
    )
    print("=" * 72)
    print("MONTHLY COHORT RETENTION (by Start Date month)")
    print("=" * 72)
    print(
        f"{'Month':<10} {'Started':>8} {'Retained':>9} {'Churned':>8} {'Retention %':>12}"
    )
    print("-" * 72)
    for ym in sorted(cohort["start_month"].dropna().unique()):
        sub = cohort[cohort["start_month"] == ym]
        n = len(sub)
        ret = int(sub["retained"].sum())
        label = datetime.strptime(ym, "%Y-%m").strftime("%b %Y")
        print(
            f"{label:<10} {n:>8,} {ret:>9,} {n-ret:>8,} "
            f"{(ret/n*100 if n else 0):>11.1f}%"
        )
    print()

    # Terminations in period (regardless of start date) — context
    termed = df[df["termination_date"].apply(in_period)]
    print("=" * 72)
    print("TERMINATIONS IN PERIOD (all members, any start date)")
    print("=" * 72)
    print(f"Total terminations in period: {len(termed):,}")
    by_level = Counter(termed["membership_level"])
    for level in (*LEVELS, "n/a"):
        if by_level.get(level, 0):
            print(f"  {level}: {by_level[level]:,}")
    for lvl, n in by_level.items():
        if lvl not in (*LEVELS, "n/a"):
            print(f"  {lvl}: {n:,}")

    # Average tenure for cohort who churned in period
    churned_cohort = cohort[cohort["terminated_in_period"]]
    if len(churned_cohort):
        tenures = (
            churned_cohort["termination_date"] - churned_cohort["start_date"]
        ).apply(lambda x: x.days)
        print()
        print(f"Avg tenure at termination (cohort churned in period): {tenures.mean():.0f} days")
        print(f"Median tenure: {tenures.median():.0f} days")


def debug_sheet_composition(df: pd.DataFrame) -> None:
    cohort = df[df["start_date"].apply(in_period)]
    no_term = cohort["termination_date"].isna().sum()
    term_in = cohort["termination_date"].apply(in_period).sum()
    term_after = (
        (cohort["termination_date"].notna())
        & (cohort["termination_date"] > PERIOD_END)
    ).sum()
    all_no_term = df["termination_date"].isna().sum()
    print("=" * 72)
    print("DATA COMPOSITION CHECK")
    print("=" * 72)
    print(f"Total rows in sheet:              {len(df):,}")
    print(f"Rows with blank termination date:   {all_no_term:,}")
    print(f"Cohort started in period:           {len(cohort):,}")
    print(f"  Blank termination in cohort:      {no_term:,}")
    print(f"  Terminated in period (cohort):     {term_in:,}")
    print(f"  Terminated after period (cohort): {term_after:,}")
    print()


if __name__ == "__main__":
    df = load_data()
    debug_sheet_composition(df)
    main(df)
