"""April (or any month) new-member tier comparison: Sheets 2023–2025 + GHL for target year.

Fetches **Both Locations** tier rows from Digital Cross-Channel Trackers and
GoHighLevel sign-ups (Committed? = Yes, known Standard/Silver/Gold/Platinum).

Usage:
    python april_new_members_tier_yoy.py
    python april_new_members_tier_yoy.py --year 2026 --month 4 --write-sheet
"""

from __future__ import annotations

import argparse
import calendar
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ghl_client import (
    contact_custom_field_value,
    fetch_signup_date_range_committed_yes_contacts,
)

TOKEN_PATH = Path.home() / ".config" / "mcp-google-sheets" / "token.json"
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
SHEET_TAB = "Monthly Tracker"
HEADER_ROW = 7
APRIL_COL_INDEX = 10  # column K when months start at G (index 7)

TRACKERS = {
    2023: "2023 Digital Cross-Channel Tracker",
    2024: "2024 Digital Cross-Channel Tracker",
    2025: "2025 Digital Cross-Channel Tracker",
    2026: "2026 Digital Cross-Channel Tracker",
}

TIERS = ("Standard", "Silver", "Gold", "Platinum")
TIER_LABELS = {
    "Standard": "New Standard Members",
    "Silver": "New Silver Members",
    "Gold": "New Gold Members",
    "Platinum": "New Platinum Members",
}

# Both Locations block (year-specific row numbers)
BOTH_LOCATIONS_ROWS = {
    2023: {"Standard": 176, "Silver": 177, "Gold": 178, "Platinum": 179},
    2024: {"Standard": 200, "Silver": 201, "Gold": 202, "Platinum": 203},
    2025: {"Standard": 202, "Silver": 203, "Gold": 204, "Platinum": 205},
    2026: {"Standard": 202, "Silver": 203, "Gold": 204, "Platinum": 205},
}
GRAND_TOTAL_ROW = {2023: 180, 2024: 205, 2025: 207, 2026: 207}


def _credentials() -> Credentials:
    info = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    return Credentials.from_authorized_user_info(info, info["scopes"])


def _month_num(label: str) -> int | None:
    text = label.strip().lower()
    mapping = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    for key, num in mapping.items():
        if text.startswith(key):
            return num
    return None


def _find_spreadsheet(drive, name: str) -> dict:
    q = (
        f"mimeType='application/vnd.google-apps.spreadsheet' "
        f"and trashed=false and name='{name}'"
    )
    files = (
        drive.files()
        .list(
            q=q,
            fields="files(id,name)",
            pageSize=5,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
        .execute()
        .get("files", [])
    )
    if not files:
        raise SystemExit(f"Spreadsheet not found: {name!r}")
    return files[0]


def _resolve_tab(sheets, spreadsheet_id: str) -> str:
    meta = (
        sheets.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="sheets.properties")
        .execute()
    )
    for sh in meta.get("sheets", []):
        title = sh["properties"]["title"]
        if title == SHEET_TAB:
            return title
    for sh in meta.get("sheets", []):
        title = sh["properties"]["title"]
        if "monthly" in title.lower() and "tracker" in title.lower():
            return title
    raise SystemExit(f"No Monthly Tracker tab in {spreadsheet_id}")


def _header_row(sheets, spreadsheet_id: str, tab: str) -> list[str]:
    result = (
        sheets.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab}'!A{HEADER_ROW}:BZ{HEADER_ROW}",
        )
        .execute()
    )
    rows = result.get("values", [])
    return rows[0] if rows else []


def _find_month_col(headers: list[str], year: int, month: int) -> int | None:
    for idx, label in enumerate(headers):
        text = str(label).strip()
        if text and str(year) in text and _month_num(text) == month:
            return idx
    return None


def _cell(row: list, col: int | None) -> str:
    if col is None or col >= len(row):
        return ""
    return str(row[col]).strip()


def _sheet_tier_counts(
    sheets, drive, *, month: int
) -> dict[int, dict[str, str]]:
    """Return {year: {tier: value_str}} from tracker sheets."""
    out: dict[int, dict[str, str]] = {}
    for year, name in TRACKERS.items():
        file_info = _find_spreadsheet(drive, name)
        sid = file_info["id"]
        tab = _resolve_tab(sheets, sid)
        headers = _header_row(sheets, sid, tab)
        col = _find_month_col(headers, year, month)
        tier_rows = BOTH_LOCATIONS_ROWS[year]
        max_row = max(tier_rows.values())
        block = (
            sheets.spreadsheets()
            .values()
            .get(spreadsheetId=sid, range=f"'{tab}'!A1:BZ{max_row}")
            .execute()
            .get("values", [])
        )
        year_vals: dict[str, str] = {}
        for tier, row_num in tier_rows.items():
            row = block[row_num - 1] if row_num - 1 < len(block) else []
            year_vals[tier] = _cell(row, col) or "—"
        out[year] = year_vals
    return out


def _norm_ghl_level(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s or s.casefold() in {"(blank)", "blank", "n/a", "none", "unknown", "-"}:
        return None
    cf = s.casefold()
    for tier in TIERS:
        if tier.lower() in cf or cf == tier.lower():
            return tier
    return "__other__"


def _ghl_tier_counts(year: int, month: int) -> tuple[dict[str, int], dict[str, int]]:
    last_day = calendar.monthrange(year, month)[1]
    since = date(year, month, 1).isoformat()
    until = date(year, month, last_day).isoformat()
    data = fetch_signup_date_range_committed_yes_contacts(since, until)
    mid = data.get("membership_level_field_id") or ""
    tier_counts: Counter[str] = Counter()
    extras: Counter[str] = Counter()
    for contact in data["contacts"]:
        raw = contact_custom_field_value(contact, mid).strip() if mid else ""
        level = _norm_ghl_level(raw)
        if level in TIERS:
            tier_counts[level] += 1
        elif level == "__other__":
            extras[raw or "(empty)"] += 1
        else:
            extras["(blank)"] += 1
    meta = {
        "total_committed": len(data["contacts"]),
        "excluded_not_committed": int(data.get("excluded_not_committed_yes") or 0),
        "truncated": bool(data.get("truncated_pages")),
    }
    return dict(tier_counts), {**dict(extras), **{f"_meta_{k}": v for k, v in meta.items()}}


def _col_letter(index: int) -> str:
    n = index + 1
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(rem + ord("A")) + letters
    return letters


def _write_ghl_to_sheet(
    sheets,
    drive,
    *,
    year: int,
    month: int,
    tier_counts: dict[str, int],
) -> list[str]:
    name = TRACKERS[year]
    file_info = _find_spreadsheet(drive, name)
    sid = file_info["id"]
    tab = _resolve_tab(sheets, sid)
    headers = _header_row(sheets, sid, tab)
    col = _find_month_col(headers, year, month)
    if col is None:
        raise SystemExit(f"No month column for {year}-{month:02d} in {name}")
    letter = _col_letter(col)
    tier_rows = BOTH_LOCATIONS_ROWS[year]
    updates = []
    data = []
    for tier in TIERS:
        row_num = tier_rows[tier]
        value = tier_counts.get(tier, 0)
        cell = f"{letter}{row_num}"
        data.append({"range": f"'{tab}'!{cell}", "values": [[value]]})
        updates.append(f"{cell} ({tier}) = {value}")
    grand_row = GRAND_TOTAL_ROW[year]
    grand_total = sum(tier_counts.get(t, 0) for t in TIERS)
    grand_cell = f"{letter}{grand_row}"
    data.append({"range": f"'{tab}'!{grand_cell}", "values": [[grand_total]]})
    updates.append(f"{grand_cell} (GRAND TOTAL) = {grand_total}")
    body = {"valueInputOption": "USER_ENTERED", "data": data}
    sheets.spreadsheets().values().batchUpdate(spreadsheetId=sid, body=body).execute()
    return updates


def build_report(*, year: int, month: int, write_sheet: bool) -> Path:
    month_name = calendar.month_name[month]
    creds = _credentials()
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)

    sheet_data = _sheet_tier_counts(sheets, drive, month=month)
    ghl_counts, ghl_meta = _ghl_tier_counts(year, month)

    # Populate target year column from GHL
    combined: dict[int, dict[str, str]] = {}
    for y in TRACKERS:
        combined[y] = dict(sheet_data.get(y, {}))
    for tier in TIERS:
        combined[year][tier] = str(ghl_counts.get(tier, 0))

    rows = []
    for tier in TIERS:
        row = {"Tier": tier}
        total = 0
        for y in sorted(TRACKERS):
            val = combined[y].get(tier, "—")
            row[str(y)] = val
            try:
                total += int(str(val).replace(",", ""))
            except ValueError:
                pass
        row["Notes"] = ""
        rows.append(row)

    # Totals row
    totals = {"Tier": "TOTAL"}
    for y in sorted(TRACKERS):
        nums = []
        for tier in TIERS:
            v = combined[y].get(tier, "")
            try:
                nums.append(int(str(v).replace(",", "")))
            except ValueError:
                pass
        totals[str(y)] = str(sum(nums)) if nums else "—"
    totals["Notes"] = f"GHL {year}: {ghl_meta.get('_meta_total_committed', '?')} committed"
    rows.append(totals)

    df = pd.DataFrame(rows)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"new_members_tier_{month_name.lower()}_{year}"
    csv_path = OUTPUT_DIR / f"{stem}.csv"
    html_path = OUTPUT_DIR / f"{stem}.html"
    df.to_csv(csv_path, index=False)

    other = {k: v for k, v in ghl_meta.items() if not k.startswith("_meta_")}
    meta_lines = [
        f"<li>Sign Up Date: {year}-{month:02d}-01 – "
        f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]}</li>",
        f"<li>Committed? = Yes</li>",
        f"<li>GHL committed contacts: {ghl_meta.get('_meta_total_committed', '—')}</li>",
    ]
    if other:
        meta_lines.append(f"<li>Other membership levels: {other}</li>")

    html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8" />
<title>{month_name} new members by tier — {year} GHL + sheet YoY</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ccc; padding: 0.5rem 0.75rem; text-align: right; }}
  th:first-child, td:first-child {{ text-align: left; }}
  th {{ background: #f4f4f4; }}
  tr:last-child {{ font-weight: 600; background: #fafafa; }}
  .sub {{ color: #555; font-size: 0.95rem; }}
</style>
</head><body>
<h1>{month_name} — new members by tier</h1>
<p class="sub">Years 2023–2025 from Digital Cross-Channel Tracker (Both Locations).
<strong>{year}</strong> from GoHighLevel. Generated {date.today().isoformat()}.</p>
<ul class="sub">{''.join(meta_lines)}</ul>
{df.to_html(index=False, border=0)}
<p class="sub"><a href="{csv_path.name}">Download CSV</a></p>
</body></html>"""
    html_path.write_text(html, encoding="utf-8")

    sheet_updates: list[str] = []
    if write_sheet:
        sheet_updates = _write_ghl_to_sheet(
            sheets, drive, year=year, month=month, tier_counts=ghl_counts
        )

    print(df.to_string(index=False))
    print(f"\nSaved: {csv_path}")
    print(f"Saved: {html_path}")
    if sheet_updates:
        print(f"\nUpdated {TRACKERS[year]}:")
        for line in sheet_updates:
            print(f"  {line}")
    if ghl_meta.get("_meta_truncated"):
        print("\nWARNING: GHL pagination truncated; counts may be incomplete.")

    return csv_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=date.today().year)
    parser.add_argument("--month", type=int, default=4, help="Month number (default: 4 = April)")
    parser.add_argument(
        "--write-sheet",
        action="store_true",
        help="Write GHL tier counts into the target year tracker (Both Locations rows)",
    )
    args = parser.parse_args()
    build_report(year=args.year, month=args.month, write_sheet=args.write_sheet)


if __name__ == "__main__":
    main()
