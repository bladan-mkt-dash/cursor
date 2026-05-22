"""Analyze Instagram Five Journeys: content published vs profile visits."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_PATH = Path.home() / ".config" / "mcp-google-sheets" / "token.json"
SPREADSHEET_NAME = "2025 Digital Cross-Channel Tracker"
SHEET_NAME = "Monthly Tracker"
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
CONTENT_ROW = 27
VISITS_ROW = 29
MONTH_HEADER_ROW = 7
DATA_START_COL = 7  # 0-based index where Jan 2025 begins


def _credentials() -> Credentials:
    info = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    return Credentials.from_authorized_user_info(info, info["scopes"])


def _find_spreadsheet_id(drive, name: str) -> str:
    exact = (
        f"mimeType='application/vnd.google-apps.spreadsheet' "
        f"and name='{name}' and trashed=false"
    )
    files = (
        drive.files()
        .list(
            q=exact,
            fields="files(id,name)",
            pageSize=5,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
        .execute()
        .get("files", [])
    )
    if files:
        return files[0]["id"]

    fuzzy = (
        "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false "
        "and name contains 'Digital Cross-Channel Tracker'"
    )
    files = (
        drive.files()
        .list(
            q=fuzzy,
            fields="files(id,name)",
            pageSize=10,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
        .execute()
        .get("files", [])
    )
    for f in files:
        if "2025" in f["name"]:
            return f["id"]
    raise SystemExit(f"Spreadsheet not found: {name!r}")


def _row_values(sheets, spreadsheet_id: str, row: int) -> list[str]:
    result = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"'{SHEET_NAME}'!A{row}:BZ{row}")
        .execute()
    )
    rows = result.get("values", [])
    return rows[0] if rows else []


def _to_numeric(value: str) -> float | None:
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "N/A", "#N/A"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _load_metrics(sheets, spreadsheet_id: str) -> pd.DataFrame:
    months = _row_values(sheets, spreadsheet_id, MONTH_HEADER_ROW)
    content = _row_values(sheets, spreadsheet_id, CONTENT_ROW)
    visits = _row_values(sheets, spreadsheet_id, VISITS_ROW)

    records = []
    for idx in range(DATA_START_COL, max(len(months), len(content), len(visits))):
        month = months[idx].strip() if idx < len(months) else ""
        if not month or "202" not in month:
            continue
        posts = _to_numeric(content[idx] if idx < len(content) else "")
        profile_visits = _to_numeric(visits[idx] if idx < len(visits) else "")
        if posts is None and profile_visits is None:
            continue
        records.append(
            {
                "month": month,
                "content_published": posts,
                "profile_visits": profile_visits,
            }
        )

    df = pd.DataFrame(records)
    month_order = [
        "Jan 2025",
        "Feb 2025",
        "Mar 2025",
        "Apr 2025",
        "May 2025",
        "Jun 2025",
        "Jul 2025",
        "Aug 2025",
        "Sep 2025",
        "Oct 2025",
        "Nov 2025",
        "Dec 2025",
    ]
    df["month"] = pd.Categorical(df["month"], categories=month_order, ordered=True)
    return df.sort_values("month").reset_index(drop=True)


def _plot(df: pd.DataFrame, out_path: Path) -> None:
    paired = df.dropna()
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    axes[0].plot(paired["month"].astype(str), paired["content_published"], marker="o", label="Content published")
    axes[0].plot(paired["month"].astype(str), paired["profile_visits"], marker="o", label="Profile visits")
    axes[0].set_title("Instagram Five Journeys — monthly trend")
    axes[0].set_xlabel("Month")
    axes[0].tick_params(axis="x", rotation=45)
    axes[0].legend()

    axes[1].scatter(paired["content_published"], paired["profile_visits"], s=70)
    for _, row in paired.iterrows():
        axes[1].annotate(str(row["month"]), (row["content_published"], row["profile_visits"]), fontsize=8, xytext=(4, 4), textcoords="offset points")
    axes[1].set_title("Profile visits vs content published")
    axes[1].set_xlabel("# of content published")
    axes[1].set_ylabel("# of profile visits")

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> None:
    creds = _credentials()
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)

    spreadsheet_id = _find_spreadsheet_id(drive, SPREADSHEET_NAME)
    df = _load_metrics(sheets, spreadsheet_id)

    print("Source: 2025 Digital Cross-Channel Tracker / Monthly Tracker")
    print("Metrics: row 27 (# of Content Published), row 29 (# of Profile Visits)\n")
    print(df.to_string(index=False))

    paired = df.dropna()
    corr = paired["content_published"].corr(paired["profile_visits"])
    paired = paired.copy()
    paired["visits_per_post"] = paired["profile_visits"] / paired["content_published"]

    print(f"\nPearson correlation: {corr:.3f}")
    print("\nVisits per post:")
    print(paired[["month", "content_published", "profile_visits", "visits_per_post"]].to_string(index=False))

    best = paired.loc[paired["visits_per_post"].idxmax()]
    worst = paired.loc[paired["visits_per_post"].idxmin()]
    print(f"\nMost efficient month: {best['month']} ({best['visits_per_post']:.1f} visits/post)")
    print(f"Least efficient month: {worst['month']} ({worst['visits_per_post']:.1f} visits/post)")

    out_csv = OUTPUT_DIR / "instagram_five_journeys_content_vs_visits.csv"
    out_png = OUTPUT_DIR / "instagram_five_journeys_content_vs_visits.png"
    paired.to_csv(out_csv, index=False)
    _plot(paired, out_png)
    print(f"\nSaved: {out_csv}")
    print(f"Saved: {out_png}")


if __name__ == "__main__":
    main()
