"""One-off: analyze Q2 2025 signups from HubSpot/GHL migration sheets."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

import pandas as pd
from googleapiclient.discovery import build

from total_new_members_yoy_chart import (
    DEFAULT_SHEET,
    TRACKERS,
    _credentials,
    _find_spreadsheet,
    _load_year_series,
    _resolve_sheet_name,
)

ALL_CONTACTS_ID = "18YwAZoROBfA88KPT_M6m2jA6v9xDS7aHOzOXJTyO2LM"
ACTIVE_PATIENT_ID = "1pOJ24CQly3UQ_1NhPr6s0FlKgQrdOYSb5xkV82sro9I"

Q2_START = pd.Timestamp("2025-04-01")
Q2_END = pd.Timestamp("2025-06-30")


def _get_values(spreadsheet_id: str, tab: str, cell_range: str = "A:AZ") -> list[list[str]]:
    sheets = build("sheets", "v4", credentials=_credentials())
    result = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"'{tab}'!{cell_range}")
        .execute()
    )
    return result.get("values", [])


def _rows_to_df(rows: list[list[str]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    width = max(len(r) for r in rows)
    padded = [r + [""] * (width - len(r)) for r in rows]
    headers = [str(h).strip() for h in padded[0]]
    df = pd.DataFrame(padded[1:], columns=headers)
    return df.loc[:, ~df.columns.duplicated()]


def _parse_date_series(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=False)


def _find_date_columns(df: pd.DataFrame) -> list[str]:
    patterns = (
        r"sign.?up",
        r"signup",
        r"join",
        r"start",
        r"created",
        r"became",
        r"member",
        r"date",
    )
    out: list[str] = []
    for col in df.columns:
        norm = col.casefold()
        if any(re.search(p, norm) for p in patterns):
            out.append(col)
    return out


def _count_by_month(dates: pd.Series) -> dict[str, int]:
    valid = dates.dropna()
    valid = valid[(valid >= Q2_START) & (valid <= Q2_END)]
    counts = Counter(valid.dt.to_period("M").astype(str))
    return dict(sorted(counts.items()))


def analyze_all_signups_with_notes() -> dict:
    rows = _get_values(ALL_CONTACTS_ID, "All Sign Ups with Notes")
    df = _rows_to_df(rows)
    date_cols = _find_date_columns(df)
    results: dict = {
        "tab": "All Sign Ups with Notes",
        "rows": len(df),
        "columns": list(df.columns),
        "date_cols": date_cols,
    }
    for col in date_cols:
        parsed = _parse_date_series(df[col])
        by_month = _count_by_month(parsed)
        if by_month:
            results[col] = {
                "q2_total": int(parsed.between(Q2_START, Q2_END).sum()),
                "by_month": by_month,
            }
    # Membership level breakdown for Q2 HS Create Date signups
    if "HS Create Date" in df.columns:
        hs = _parse_date_series(df["HS Create Date"])
        q2_mask = hs.between(Q2_START, Q2_END)
        q2_df = df.loc[q2_mask].copy()
        if "Membership Level" in q2_df.columns:
            results["q2_membership_level"] = (
                q2_df["Membership Level"].fillna("(blank)").value_counts().to_dict()
            )
        if "Membership Type" in q2_df.columns:
            results["q2_membership_type"] = (
                q2_df["Membership Type"].fillna("(blank)").value_counts().to_dict()
            )
        if "Lifecycle Stage" in q2_df.columns:
            results["q2_lifecycle_stage"] = (
                q2_df["Lifecycle Stage"].fillna("(blank)").value_counts().to_dict()
            )
        if "Converted?" in q2_df.columns:
            results["q2_converted"] = (
                q2_df["Converted?"].fillna("(blank)").value_counts().to_dict()
            )
        if "Contact Type" in q2_df.columns:
            results["q2_contact_type"] = (
                q2_df["Contact Type"].fillna("(blank)").value_counts().to_dict()
            )
    return results


def analyze_import_all_contacts() -> dict:
    rows = _get_values(ALL_CONTACTS_ID, "Import All Contacts")
    df = _rows_to_df(rows)
    results: dict = {
        "tab": "Import All Contacts",
        "rows": len(df),
        "columns": list(df.columns)[:25],
    }
    if "HS Created Date" in df.columns:
        hs = _parse_date_series(df["HS Created Date"])
        q2_mask = hs.between(Q2_START, Q2_END)
        results["HS Created Date"] = {
            "q2_total_all_contacts": int(q2_mask.sum()),
            "by_month": _count_by_month(hs),
        }
        if "Membership Level" in df.columns:
            members = df.loc[q2_mask & df["Membership Level"].astype(str).str.strip().ne("")]
            results["q2_with_membership_level"] = {
                "total": len(members),
                "by_month": _count_by_month(_parse_date_series(members["HS Created Date"])),
                "levels": members["Membership Level"].value_counts().to_dict(),
            }
        if "Lifecycle Stage" in df.columns:
            results["q2_lifecycle_stage"] = (
                df.loc[q2_mask, "Lifecycle Stage"].fillna("(blank)").value_counts().to_dict()
            )
    return results


def count_all_signups_by_year() -> dict:
    rows = _get_values(ALL_CONTACTS_ID, "All Sign Ups with Notes")
    df = _rows_to_df(rows)
    hs = _parse_date_series(df["HS Create Date"])
    valid = hs.dropna()
    by_month = Counter(valid.dt.to_period("M").astype(str))
    y2025 = {k: v for k, v in sorted(by_month.items()) if k.startswith("2025")}
    return {
        "total_rows": len(df),
        "unique_emails": df["Email"].str.strip().str.casefold().nunique() if "Email" in df.columns else None,
        "q2_unique_emails": df.loc[hs.between(Q2_START, Q2_END), "Email"].str.strip().str.casefold().nunique()
        if "Email" in df.columns
        else None,
        "2025_by_month": y2025,
        "2025_total": sum(y2025.values()),
        "q2_total": int(hs.between(Q2_START, Q2_END).sum()),
    }


def tracker_location_rows_q2() -> dict:
    from total_new_members_yoy_chart import _header_row, _row_values, _month_num, _to_numeric

    creds = _credentials()
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    meta = TRACKERS["2025"]
    file_info = _find_spreadsheet(drive, meta["name"])
    sheet_name = _resolve_sheet_name(sheets, file_info["id"], DEFAULT_SHEET)
    sid = file_info["id"]
    col_c = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=sid, range=f"'{sheet_name}'!C1:C250")
        .execute()
        .get("values", [])
    )
    headers = _header_row(sheets, sid, sheet_name)
    labels = (
        "TOTAL New Members",
        "GRAND TOTAL New Members",
        "Both Locations",
        "Boston",
        "Newton",
        "TOTAL New Members Boston",
        "TOTAL New Members Newton",
    )
    out: dict = {}
    for i, row in enumerate(col_c, start=1):
        label = str(row[0]).strip() if row else ""
        if label not in labels and not label.startswith("TOTAL New Members"):
            continue
        if label not in labels and "New Members" not in label:
            continue
        values = _row_values(sheets, sid, sheet_name, i)
        months: dict[str, float] = {}
        for idx, h in enumerate(headers):
            month_label = str(h).strip()
            if "2025" not in month_label:
                continue
            mnum = _month_num(month_label)
            if mnum not in (4, 5, 6):
                continue
            val = _to_numeric(values[idx] if idx < len(values) else "")
            if val is not None:
                months[f"2025-{mnum:02d}"] = val
        if months:
            out[label] = {"by_month": months, "q2": sum(months.values())}
    return out


def tracker_grand_total_q2_2025() -> dict:
    from total_new_members_yoy_chart import _find_total_new_members_row, _load_row_year_series

    creds = _credentials()
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    meta = TRACKERS["2025"]
    file_info = _find_spreadsheet(drive, meta["name"])
    sheet_name = _resolve_sheet_name(sheets, file_info["id"], DEFAULT_SHEET)
    sid = file_info["id"]

    total_row = _find_total_new_members_row(sheets, sid, sheet_name)
    col_c = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=sid, range=f"'{sheet_name}'!C1:C250")
        .execute()
        .get("values", [])
    )
    grand_row = None
    for i, row in enumerate(col_c, start=1):
        if row and str(row[0]).strip() == "GRAND TOTAL New Members":
            grand_row = i
            break

    def series_for(row_num: int) -> dict:
        s = _load_row_year_series(sheets, sid, sheet_name, 2025, row_num)
        return {
            "2025-04": float(s.get(4, 0)),
            "2025-05": float(s.get(5, 0)),
            "2025-06": float(s.get(6, 0)),
        }

    total = series_for(total_row)
    out = {"TOTAL New Members row": total, "TOTAL_q2": sum(total.values())}
    if grand_row and grand_row != total_row:
        grand = series_for(grand_row)
        out["GRAND TOTAL New Members row"] = grand
        out["GRAND_q2"] = sum(grand.values())
    return out


def analyze_active_patient_snapshot() -> dict:
    rows = _get_values(ACTIVE_PATIENT_ID, "All Members 06 25 2025", "A:F")
    df = _rows_to_df(rows)
    level_counts = df["Membership Level"].value_counts(dropna=False).to_dict() if "Membership Level" in df.columns else {}
    return {
        "tab": "All Members 06 25 2025",
        "snapshot_date": "2025-06-25",
        "total_rows": len(df),
        "membership_level_counts": level_counts,
        "note": "Snapshot has no signup date column; useful as membership inventory only.",
    }


def tracker_q2_2025() -> dict:
    creds = _credentials()
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    meta = TRACKERS["2025"]
    file_info = _find_spreadsheet(drive, meta["name"])
    sheet_name = _resolve_sheet_name(sheets, file_info["id"], DEFAULT_SHEET)
    series = _load_year_series(sheets, file_info["id"], sheet_name, 2025)
    months = {4: "2025-04", 5: "2025-05", 6: "2025-06"}
    by_month = {label: float(series.get(m, 0)) for m, label in months.items()}
    return {
        "source": "2025 Digital Cross-Channel Tracker / TOTAL New Members",
        "by_month": by_month,
        "q2_total": sum(by_month.values()),
    }


def main() -> None:
    import json

    out = {
        "tracker": tracker_q2_2025(),
        "tracker_rows": tracker_grand_total_q2_2025(),
        "tracker_location_rows": tracker_location_rows_q2(),
        "all_signups_year": count_all_signups_by_year(),
        "all_signups": analyze_all_signups_with_notes(),
        "import_all": analyze_import_all_contacts(),
        "active_patient": analyze_active_patient_snapshot(),
    }
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
