"""Grouped bar chart: TOTAL New Members MoM comparison across 2023-2026."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

TRACKER_CHART_REVISION = "2026-06-24-bookings-row-v1"

TOKEN_PATH = Path.home() / ".config" / "mcp-google-sheets" / "token.json"
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
DEFAULT_SHEET = "Monthly Tracker"
MONTH_HEADER_ROW = 7
CHART_YEARS = (2023, 2024, 2025, 2026)
CHART_COLORS = ["#4C78A8", "#F58518", "#54A24B", "#B279A2"]

TRACKERS = {
    "2023": {"year": 2023, "name": "2023 Digital Cross-Channel Tracker"},
    "2024": {"year": 2024, "name": "2024 Digital Cross-Channel Tracker"},
    "2025": {
        "year": 2025,
        "name": "2025 Digital Cross-Channel Tracker",
        "id": "1oPIba48QuaDhfUP0l6JvoIAQYHQDPgU2JanerfsmYJM",
    },
    "2026": {"year": 2026, "name": "2026 Digital Cross-Channel Tracker"},
}

OUT_CSV = OUTPUT_DIR / "total_new_members_mom_2023_2024_2025.csv"
OUT_PNG = OUTPUT_DIR / "total_new_members_mom_2023_2024_2025.png"
OUT_HTML = OUTPUT_DIR / "total_new_members_report.html"

_TRACKER_GRID_RANGE = "A1:BZ250"
_TRACKER_CACHE_DIR = Path(__file__).resolve().parent / ".cache" / "tracker_sheets"
_MEMORY_GRIDS: dict[tuple[str, str], list[list[str]]] = {}
_SHEET_NAME_CACHE: dict[str, str] = {}


def _credentials() -> Credentials:
    info = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    return Credentials.from_authorized_user_info(info, info["scopes"])


def _resolve_tracker_spreadsheet(drive, meta: dict) -> dict:
    """Return tracker file metadata, using a pinned spreadsheet id when configured."""
    pinned = meta.get("id")
    if pinned:
        return {"id": pinned, "name": meta["name"]}
    return _find_spreadsheet(drive, meta["name"])


def _find_spreadsheet(drive, name: str) -> dict:
    exact = (
        f"mimeType='application/vnd.google-apps.spreadsheet' "
        f"and trashed=false and name='{name}'"
    )
    files = (
        drive.files()
        .list(
            q=exact,
            fields="files(id,name)",
            pageSize=10,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
        .execute()
        .get("files", [])
    )
    if files:
        return files[0]

    fuzzy = (
        f"mimeType='application/vnd.google-apps.spreadsheet' and trashed=false and "
        f"name contains '{name}'"
    )
    files = (
        drive.files()
        .list(
            q=fuzzy,
            fields="files(id,name)",
            pageSize=20,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
        .execute()
        .get("files", [])
    )
    if not files:
        drives = drive.drives().list(pageSize=50).execute().get("drives", [])
        for shared in drives:
            files = (
                drive.files()
                .list(
                    q=fuzzy,
                    corpora="drive",
                    driveId=shared["id"],
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                    fields="files(id,name)",
                    pageSize=20,
                )
                .execute()
                .get("files", [])
            )
            if files:
                break
    if not files:
        raise SystemExit(f"Spreadsheet not found: {name!r}")
    return files[0]


def _execute_with_retry(execute_fn: Callable[[], Any], *, max_attempts: int = 6) -> Any:
    """Retry Google API calls on HTTP 429 (Sheets read quota)."""
    delay = 2.0
    last_exc: HttpError | None = None
    for attempt in range(max_attempts):
        try:
            return execute_fn()
        except HttpError as exc:
            last_exc = exc
            if exc.resp.status == 429 and attempt < max_attempts - 1:
                time.sleep(min(60.0, delay))
                delay *= 2.0
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Sheets API retry exhausted")


def _tracker_workbook_year(spreadsheet_id: str) -> int | None:
    for meta in TRACKERS.values():
        if meta.get("id") == spreadsheet_id:
            return int(meta["year"])
    return None


def _tracker_disk_cache_path(spreadsheet_id: str, sheet_name: str) -> Path:
    slug = "".join(ch if ch.isalnum() else "_" for ch in sheet_name)
    return _TRACKER_CACHE_DIR / f"{spreadsheet_id}_{slug}.json"


def _tracker_disk_cache_fresh(path: Path, *, spreadsheet_id: str) -> bool:
    if not path.is_file():
        return False
    age_hours = (time.time() - path.stat().st_mtime) / 3600.0
    workbook_year = _tracker_workbook_year(spreadsheet_id)
    if workbook_year is not None and workbook_year < date.today().year:
        return age_hours < 24 * 7
    return age_hours < 6


def clear_tracker_sheet_cache() -> None:
    """Drop in-memory and on-disk Monthly Tracker grid caches."""
    _MEMORY_GRIDS.clear()
    _SHEET_NAME_CACHE.clear()
    if _TRACKER_CACHE_DIR.is_dir():
        for path in _TRACKER_CACHE_DIR.rglob("*"):
            if path.is_file():
                path.unlink(missing_ok=True)


def _get_tracker_grid(
    sheets, spreadsheet_id: str, sheet_name: str
) -> list[list[str]]:
    """One cached read per workbook instead of dozens of cell-range requests."""
    key = (spreadsheet_id, sheet_name)
    if key in _MEMORY_GRIDS:
        return _MEMORY_GRIDS[key]

    cache_path = _tracker_disk_cache_path(spreadsheet_id, sheet_name)
    if _tracker_disk_cache_fresh(cache_path, spreadsheet_id=spreadsheet_id):
        try:
            grid = json.loads(cache_path.read_text(encoding="utf-8"))
            _MEMORY_GRIDS[key] = grid
            return grid
        except Exception:
            pass

    result = _execute_with_retry(
        lambda: sheets.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'!{_TRACKER_GRID_RANGE}",
        )
        .execute()
    )
    grid = result.get("values", [])
    _TRACKER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(grid), encoding="utf-8")
    _MEMORY_GRIDS[key] = grid
    return grid


def _grid_row(grid: list[list[str]], row: int) -> list[str]:
    if row < 1 or row > len(grid):
        return []
    return grid[row - 1]


def _grid_col_c_label(grid: list[list[str]], row: int) -> str:
    row_vals = _grid_row(grid, row)
    if len(row_vals) < 3:
        return ""
    return str(row_vals[2]).strip()


def _resolve_sheet_name(sheets, spreadsheet_id: str, preferred: str) -> str:
    if spreadsheet_id in _SHEET_NAME_CACHE:
        return _SHEET_NAME_CACHE[spreadsheet_id]

    meta = _execute_with_retry(
        lambda: sheets.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title")
        .execute()
    )
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if preferred in titles:
        resolved = preferred
    else:
        resolved = None
        for title in titles:
            if "monthly" in title.lower() and "tracker" in title.lower():
                resolved = title
                break
        if resolved is None:
            raise SystemExit(
                f"No monthly tracker sheet found in {spreadsheet_id}. Tabs: {titles}"
            )

    _SHEET_NAME_CACHE[spreadsheet_id] = resolved
    return resolved


def _row_values(sheets, spreadsheet_id: str, sheet_name: str, row: int) -> list[str]:
    grid = _get_tracker_grid(sheets, spreadsheet_id, sheet_name)
    return _grid_row(grid, row)


def _header_row(sheets, spreadsheet_id: str, sheet_name: str) -> list[str]:
    grid = _get_tracker_grid(sheets, spreadsheet_id, sheet_name)
    return _grid_row(grid, MONTH_HEADER_ROW)


def _find_total_new_members_row(sheets, spreadsheet_id: str, sheet_name: str) -> int:
    grid = _get_tracker_grid(sheets, spreadsheet_id, sheet_name)
    total_row: int | None = None
    grand_total_row: int | None = None
    candidates: list[tuple[int, str]] = []
    for i in range(1, min(len(grid), 250) + 1):
        label = _grid_col_c_label(grid, i)
        if not label:
            continue
        norm = label.lower()
        if "average" in norm:
            continue
        if label == "TOTAL New Members":
            total_row = i
        elif label == "GRAND TOTAL New Members":
            grand_total_row = i
        elif label.startswith("TOTAL New Members") and "Boston" not in label and "Newton" not in label:
            candidates.append((i, label))
    if total_row is not None:
        return total_row
    if grand_total_row is not None:
        return grand_total_row
    if candidates:
        return candidates[0][0]
    raise SystemExit(f"Could not find TOTAL New Members row in {spreadsheet_id}")


def _find_calls_completed_row(sheets, spreadsheet_id: str, sheet_name: str) -> int:
    grid = _get_tracker_grid(sheets, spreadsheet_id, sheet_name)
    for i in range(1, min(len(grid), 250) + 1):
        if _grid_col_c_label(grid, i) == "Calls completed":
            return i
    raise SystemExit(f"Could not find Calls completed row in {spreadsheet_id}")


def _find_bookings_all_booked_calls_row(
    sheets, spreadsheet_id: str, sheet_name: str
) -> int:
    grid = _get_tracker_grid(sheets, spreadsheet_id, sheet_name)
    for i in range(1, min(len(grid), 250) + 1):
        if _grid_col_c_label(grid, i) == "Bookings (all booked calls)":
            return i
    raise SystemExit(
        f"Could not find Bookings (all booked calls) row in {spreadsheet_id}"
    )


def _to_numeric(value: str) -> float | None:
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "N/A", "#N/A", "#DIV/0!"}:
        return None
    if text.startswith("+") and "%" in text:
        return None
    if text.endswith("%"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _month_num(label: str) -> int | None:
    text = label.strip().lower()
    mapping = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    for key, num in mapping.items():
        if text.startswith(key):
            return num
    return None


def _load_row_year_series(
    sheets, spreadsheet_id: str, sheet_name: str, year: int, row_num: int
) -> pd.Series:
    headers = _header_row(sheets, spreadsheet_id, sheet_name)
    values = _row_values(sheets, spreadsheet_id, sheet_name, row_num)

    month_values: dict[int, float] = {}
    for idx in range(len(headers)):
        month_label = str(headers[idx]).strip()
        if not month_label or str(year) not in month_label:
            continue
        month_num = _month_num(month_label)
        if month_num is None:
            continue
        val = _to_numeric(values[idx] if idx < len(values) else "")
        if val is None:
            continue
        month_values[month_num] = val

    return pd.Series(month_values, name=str(year)).sort_index()


def _load_year_series(
    sheets, spreadsheet_id: str, sheet_name: str, year: int
) -> pd.Series:
    row_num = _find_total_new_members_row(sheets, spreadsheet_id, sheet_name)
    return _load_row_year_series(sheets, spreadsheet_id, sheet_name, year, row_num)


def _load_calls_completed_year_series(
    sheets, spreadsheet_id: str, sheet_name: str, year: int
) -> pd.Series:
    row_num = _find_calls_completed_row(sheets, spreadsheet_id, sheet_name)
    return _load_row_year_series(sheets, spreadsheet_id, sheet_name, year, row_num)


def _load_bookings_year_series(
    sheets, spreadsheet_id: str, sheet_name: str, year: int
) -> pd.Series:
    row_num = _find_bookings_all_booked_calls_row(sheets, spreadsheet_id, sheet_name)
    return _load_row_year_series(sheets, spreadsheet_id, sheet_name, year, row_num)


def _plot(df: pd.DataFrame, out_path: Path) -> None:
    import matplotlib.pyplot as plt

    months = df.index.tolist()
    x = np.arange(len(months))
    n_years = len(CHART_YEARS)
    width = 0.8 / n_years

    fig, ax = plt.subplots(figsize=(14, 7))
    for i, year in enumerate(CHART_YEARS):
        offset = (i - (n_years - 1) / 2) * width
        vals = df[str(year)].values.astype(float)
        bars = ax.bar(
            x + offset,
            np.nan_to_num(vals, nan=0.0),
            width,
            label=str(year),
            color=CHART_COLORS[i],
        )
        for bar, val in zip(bars, vals):
            if pd.notna(val):
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 2,
                    f"{int(val)}",
                    ha="center",
                    va="bottom",
                    fontsize=7,
                )

    year_label = f"{CHART_YEARS[0]} vs {CHART_YEARS[1]} vs {CHART_YEARS[2]} vs {CHART_YEARS[3]}"
    ax.set_title(f"TOTAL New Members - Month over Month Comparison ({year_label})")
    ax.set_xlabel("Month")
    ax.set_ylabel("TOTAL New Members")
    ax.set_xticks(x)
    ax.set_xticklabels(months)
    ax.legend(title="Year")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _write_html(df: pd.DataFrame, out_path: Path) -> None:
    rows = []
    for month, row in df.iterrows():
        cells = [f"<td>{month}</td>"]
        for year in CHART_YEARS:
            val = row[str(year)]
            text = "" if pd.isna(val) else str(int(val))
            cells.append(f"<td>{text}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")

    year_headers = "".join(f"<th>{year}</th>" for year in CHART_YEARS)
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>TOTAL New Members — 2023–2026</title>
    <style>
      :root {{
        font-family: system-ui, Segoe UI, Roboto, sans-serif;
        background: #0e1117;
        color: #fafafa;
      }}
      body {{ max-width: 1200px; margin: 0 auto; padding: 1.5rem 1rem 3rem; }}
      h1 {{ font-size: 1.55rem; margin: 0 0 0.35rem; }}
      .sub {{ color: #a0a8b0; margin: 0 0 1.25rem; line-height: 1.45; }}
      .panel {{
        background: #161b22;
        border: 1px solid #30363d;
        border-radius: 10px;
        padding: 1rem 1.1rem;
        margin-bottom: 1.25rem;
      }}
      img {{ width: 100%; height: auto; border-radius: 8px; background: #fff; }}
      table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
      th, td {{ text-align: right; padding: 0.55rem 0.65rem; border-bottom: 1px solid #30363d; }}
      th:first-child, td:first-child {{ text-align: left; }}
      th {{ color: #8b949e; font-weight: 500; }}
      a {{ color: #58a6ff; }}
      .links {{ display: flex; gap: 1rem; margin-bottom: 1rem; font-size: 0.9rem; }}
    </style>
  </head>
  <body>
    <h1>TOTAL New Members</h1>
    <p class="sub">
      Month-over-month comparison from the Monthly Tracker sheet on the
      2023–2026 Digital Cross-Channel trackers. 2026 includes available months only.
    </p>
    <div class="links">
      <a href="./total_new_members_mom_2023_2024_2025.png">Download chart PNG</a>
      <a href="./total_new_members_mom_2023_2024_2025.csv">Download CSV</a>
    </div>
    <div class="panel">
      <img src="./total_new_members_mom_2023_2024_2025.png" alt="TOTAL New Members chart" />
    </div>
    <div class="panel">
      <table>
        <thead><tr><th>Month</th>{year_headers}</tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
  </body>
</html>
"""
    out_path.write_text(html, encoding="utf-8")


def build_report() -> tuple[pd.DataFrame, list[str], Path, Path]:
    creds = _credentials()
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)

    series_by_year: dict[str, pd.Series] = {}
    sources: list[str] = []

    for meta in TRACKERS.values():
        file_info = _resolve_tracker_spreadsheet(drive, meta)
        year = meta["year"]
        sheet_name = _resolve_sheet_name(sheets, file_info["id"], DEFAULT_SHEET)
        row_num = _find_total_new_members_row(sheets, file_info["id"], sheet_name)
        series = _load_year_series(sheets, file_info["id"], sheet_name, year)
        series_by_year[str(year)] = series
        sources.append(
            f"{year}: {file_info['name']} / {sheet_name} / row {row_num} ({len(series)} months)"
        )

    month_names = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    df = pd.DataFrame(index=month_names)
    for year in CHART_YEARS:
        s = series_by_year[str(year)]
        df[str(year)] = [s.get(i + 1, np.nan) for i in range(12)]

    df.to_csv(OUT_CSV)
    _plot(df, OUT_PNG)
    _write_html(df, OUT_HTML)
    return df, sources, OUT_PNG, OUT_CSV


def main() -> None:
    df, sources, out_png, out_csv = build_report()
    print("Sources:")
    for line in sources:
        print(f"  - {line}")
    print("\nData:")
    print(df.to_string())
    print(f"\nSaved: {out_csv}")
    print(f"Saved: {out_png}")
    print(f"Saved: {OUT_HTML}")


if __name__ == "__main__":
    main()
