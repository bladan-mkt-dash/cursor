"""Patch Q2 2025 new-member counts on the 2025 Digital Cross-Channel Tracker."""

from __future__ import annotations

from collections import Counter

import pandas as pd
from googleapiclient.discovery import build

from total_new_members_yoy_chart import (
    DEFAULT_SHEET,
    _credentials,
    _header_row,
    _month_num,
    _resolve_sheet_name,
    _row_values,
    _to_numeric,
)

TRACKER_ID = "1oPIba48QuaDhfUP0l6JvoIAQYHQDPgU2JanerfsmYJM"
ALL_CONTACTS_ID = "18YwAZoROBfA88KPT_M6m2jA6v9xDS7aHOzOXJTyO2LM"
SIGNUPS_TAB = "All Sign Ups with Notes"

Q2_MONTHS = {
    4: "2025-04",
    5: "2025-05",
    6: "2025-06",
}

# Both Locations tier rows on 2025 Monthly Tracker (from april_new_members_tier_yoy.py).
TIER_ROWS = {
    "New Standard Members": 202,
    "New Silver Members": 203,
    "New Gold Members": 204,
    "New Platinum Members": 205,
}
GRAND_TOTAL_ROW = 207

LEVEL_TO_TIER = {
    "Standard": "New Standard Members",
    "Silver": "New Silver Members",
    "Gold": "New Gold Members",
    "Platinum": "New Platinum Members",
}


def _get_values(spreadsheet_id: str, tab: str, cell_range: str) -> list[list[str]]:
    sheets = build("sheets", "v4", credentials=_credentials())
    result = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"'{tab}'!{cell_range}")
        .execute()
    )
    return result.get("values", [])


def _hubspot_q2_by_month_and_tier() -> tuple[dict[int, int], dict[int, dict[str, int]]]:
    rows = _get_values(ALL_CONTACTS_ID, SIGNUPS_TAB, "A1:ZZ")
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    headers = [str(h).strip() for h in rows[0]]
    df = pd.DataFrame(rows[1:], columns=headers)
    hs = pd.to_datetime(df["HS Create Date"], errors="coerce")
    q2 = df.loc[hs.between("2025-04-01", "2025-06-30")].copy()
    q2["month_num"] = hs.loc[q2.index].dt.month

    totals: dict[int, int] = {}
    tiers: dict[int, dict[str, int]] = {m: Counter() for m in Q2_MONTHS}
    for month_num in Q2_MONTHS:
        month_df = q2.loc[q2["month_num"] == month_num]
        totals[month_num] = len(month_df)
        for level, count in (
            month_df["Membership Level"].fillna("(other)").astype(str).value_counts().items()
        ):
            tiers[month_num][level] = int(count)

    tier_rows: dict[int, dict[str, int]] = {}
    for month_num, counter in tiers.items():
        mapped: dict[str, int] = {tier: 0 for tier in TIER_ROWS}
        other = 0
        for level, count in counter.items():
            tier = LEVEL_TO_TIER.get(level)
            if tier:
                mapped[tier] += count
            else:
                other += count
        mapped["(other levels)"] = other
        tier_rows[month_num] = mapped
    return totals, tier_rows


def _month_columns(sheets, spreadsheet_id: str, sheet_name: str) -> dict[int, int]:
    headers = _header_row(sheets, spreadsheet_id, sheet_name)
    out: dict[int, int] = {}
    for idx, label in enumerate(headers):
        text = str(label).strip()
        if "2025" not in text:
            continue
        month_num = _month_num(text)
        if month_num in Q2_MONTHS:
            out[month_num] = idx
    return out


def _col_letter(idx: int) -> str:
    """0-based column index to A1 letter(s)."""
    n = idx + 1
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _read_current(
    sheets, spreadsheet_id: str, sheet_name: str, month_cols: dict[int, int]
) -> dict[str, dict[int, float | None]]:
    labels = list(TIER_ROWS) + ["GRAND TOTAL New Members"]
    rows = {**TIER_ROWS, "GRAND TOTAL New Members": GRAND_TOTAL_ROW}
    current: dict[str, dict[int, float | None]] = {}
    col_c = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"'{sheet_name}'!C1:C250")
        .execute()
        .get("values", [])
    )
    row_by_label: dict[str, int] = {}
    for i, row in enumerate(col_c, start=1):
        label = str(row[0]).strip() if row else ""
        if label in rows:
            row_by_label[label] = i
    for label in labels:
        row_num = row_by_label.get(label) or rows.get(label)
        if not row_num:
            continue
        values = _row_values(sheets, spreadsheet_id, sheet_name, row_num)
        current[label] = {}
        for month_num, col_idx in month_cols.items():
            raw = values[col_idx] if col_idx < len(values) else ""
            current[label][month_num] = _to_numeric(raw)
    return current


def main(dry_run: bool = False) -> None:
    totals, tier_rows = _hubspot_q2_by_month_and_tier()
    sheets = build("sheets", "v4", credentials=_credentials())
    sheet_name = _resolve_sheet_name(sheets, TRACKER_ID, DEFAULT_SHEET)
    month_cols = _month_columns(sheets, TRACKER_ID, sheet_name)
    if set(month_cols) != set(Q2_MONTHS):
        raise SystemExit(f"Missing Q2 month columns; found {month_cols}")

    current = _read_current(sheets, TRACKER_ID, sheet_name, month_cols)
    updates: dict[str, list] = {}

    for month_num, total in totals.items():
        col = _col_letter(month_cols[month_num])
        updates[f"{col}{GRAND_TOTAL_ROW}"] = [[total]]
        for tier_label, row_num in TIER_ROWS.items():
            val = tier_rows[month_num].get(tier_label, 0)
            updates[f"{col}{row_num}"] = [[val]]

    print("=== Proposed Q2 2025 signup updates (HubSpot all-contacts) ===")
    for month_num in sorted(Q2_MONTHS):
        print(
            f"{Q2_MONTHS[month_num]}: "
            f"GRAND TOTAL {current.get('GRAND TOTAL New Members', {}).get(month_num)} -> {totals[month_num]}"
        )
        for tier_label in TIER_ROWS:
            old = current.get(tier_label, {}).get(month_num)
            new = tier_rows[month_num].get(tier_label, 0)
            other = tier_rows[month_num].get("(other levels)", 0)
            print(f"  {tier_label}: {old} -> {new}")
        if tier_rows[month_num].get("(other levels)", 0):
            print(
                f"  (other levels, not mapped to tracker rows): "
                f"{tier_rows[month_num]['(other levels)']}"
            )

    if dry_run:
        print("Dry run only; no sheet writes.")
        return

    body = {
        "valueInputOption": "USER_ENTERED",
        "data": [
            {"range": f"'{sheet_name}'!{cell}", "values": values}
            for cell, values in updates.items()
        ],
    }
    result = (
        sheets.spreadsheets()
        .values()
        .batchUpdate(spreadsheetId=TRACKER_ID, body=body)
        .execute()
    )
    print(f"Updated {result.get('totalUpdatedCells', 0)} cells on {sheet_name}.")


if __name__ == "__main__":
    import sys

    main(dry_run="--dry-run" in sys.argv)
