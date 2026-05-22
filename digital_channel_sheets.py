"""Load and parse Digital Channel Dashboard Google Sheets data."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_PATH = Path.home() / ".config" / "mcp-google-sheets" / "token.json"
SPREADSHEET_ID = "17hiPmxzwaEUVlE5rEce4b7q8e1M0_iIqAq7g3-E_US0"
SPREADSHEET_NAME = "Digital Channel Dashboard 2024-25"

DATA_COLUMNS = [
    "date",
    "channel",
    "campaign",
    "creative_type",
    "creative",
    "fb_ig_type",
    "spend",
    "spend_month",
    "reach",
    "impressions",
    "clicks",
    "cpc",
    "leads",
    "cpl",
    "dcs",
    "cpdc",
    "conversions",
    "lead_to_patient_pct",
    "cac",
]

INVALID_VALUES = {"", "-", "N/A", "#N/A", "#DIV/0!", "n/a"}


def _credentials() -> Credentials:
    info = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    return Credentials.from_authorized_user_info(info, info["scopes"])


def _sheets_service():
    return build("sheets", "v4", credentials=_credentials())


def _get_values(sheets, spreadsheet_id: str, sheet: str, cell_range: str | None = None) -> list[list]:
    range_name = f"'{sheet}'!{cell_range}" if cell_range else f"'{sheet}'"
    result = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_name)
        .execute()
    )
    return result.get("values", [])


def parse_number(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in INVALID_VALUES:
        return None
    if text.endswith("%"):
        try:
            return float(text.replace("%", "").replace(",", "")) / 100.0
        except ValueError:
            return None
    text = text.replace("$", "").replace(",", "").replace("+", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_currency(value) -> float | None:
    return parse_number(value)


def parse_percent(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in INVALID_VALUES:
        return None
    if text.endswith("%"):
        try:
            return float(text.replace("%", "").replace(",", ""))
        except ValueError:
            return None
    num = parse_number(value)
    if num is None:
        return None
    return num * 100 if num <= 1 else num


def load_campaign_data(spreadsheet_id: str = SPREADSHEET_ID) -> pd.DataFrame:
    sheets = _sheets_service()
    rows = _get_values(sheets, spreadsheet_id, "Data")
    if not rows:
        return pd.DataFrame(columns=DATA_COLUMNS)

    header = rows[0]
    data_rows = rows[1:]
    width = len(DATA_COLUMNS)
    records: list[dict] = []

    for row in data_rows:
        padded = row + [""] * (width - len(row))
        record = dict(zip(DATA_COLUMNS, padded[:width]))
        records.append(record)

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()

    numeric_map = {
        "spend": parse_currency,
        "spend_month": parse_currency,
        "reach": parse_number,
        "impressions": parse_number,
        "clicks": parse_number,
        "cpc": parse_currency,
        "leads": parse_number,
        "cpl": parse_currency,
        "dcs": parse_number,
        "cpdc": parse_currency,
        "conversions": parse_number,
        "lead_to_patient_pct": parse_percent,
        "cac": parse_currency,
    }
    for col, parser in numeric_map.items():
        df[col] = df[col].apply(parser)

    for col in ("channel", "campaign", "creative_type", "creative", "fb_ig_type"):
        df[col] = df[col].astype(str).str.strip()

    return df.dropna(subset=["date"])


def _parse_wide_comparison(rows: list[list]) -> tuple[list[str], dict[str, dict[str, float]]]:
    """Parse the wide YoY table (headers row + metric rows)."""
    header_row_idx = None
    for i, row in enumerate(rows):
        labels = [str(c).strip() for c in row if str(c).strip()]
        if len(labels) >= 4 and re.match(r"^[A-Za-z]{3} \d{4}$", labels[0]):
            header_row_idx = i
            break

    if header_row_idx is None:
        return [], {}

    header = [str(c).strip() for c in rows[header_row_idx]]
    period_cols = [(idx, label) for idx, label in enumerate(header) if re.match(r"^[A-Za-z]{3} \d{4}$", label)]
    metrics: dict[str, dict[str, float]] = {}

    for row in rows[header_row_idx + 1 :]:
        if len(row) < 4:
            continue
        metric = str(row[2]).strip() if len(row) > 2 else ""
        if not metric:
            continue
        metrics[metric] = {}
        for idx, period in period_cols:
            if idx < len(row):
                val = parse_number(row[idx])
                if val is not None:
                    metrics[metric][period] = val

    periods = [label for _, label in period_cols]
    return periods, metrics


def _parse_long_comparison(rows: list[list]) -> pd.DataFrame:
    """Parse the long-format comparison table used for column charts."""
    start_idx = None
    for i, row in enumerate(rows):
        cells = [str(c).strip() for c in row]
        if "Booking Page Views (Traffic)" in cells and "Bookings (all booked calls)" in cells:
            start_idx = i
            break

    if start_idx is None:
        return pd.DataFrame()

    headers = [str(c).strip() for c in rows[start_idx]]
    records: list[dict] = []
    for row in rows[start_idx + 1 :]:
        if len(row) < 4:
            continue
        period = str(row[2]).strip()
        if not re.match(r"^[A-Za-z]{3} \d{4}$", period):
            continue
        record = {"period": period}
        for idx, header in enumerate(headers[3:], start=3):
            if idx >= len(row) or not header:
                continue
            val = parse_number(row[idx])
            if val is not None:
                record[header] = val
        records.append(record)

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["month"] = df["period"].str.split().str[0]
    df["year"] = df["period"].str.split().str[1].astype(int)
    return df


def load_comparison_data(spreadsheet_id: str = SPREADSHEET_ID) -> dict:
    sheets = _sheets_service()
    rows = _get_values(sheets, spreadsheet_id, "Comparison Data")
    periods, metrics = _parse_wide_comparison(rows)
    long_df = _parse_long_comparison(rows)
    return {"periods": periods, "metrics": metrics, "long": long_df}


def monthly_campaign_summary(df: pd.DataFrame) -> pd.DataFrame:
    agg = (
        df.groupby("month", as_index=False)
        .agg(
            spend=("spend", "sum"),
            clicks=("clicks", "sum"),
            leads=("leads", "sum"),
            dcs=("dcs", "sum"),
            conversions=("conversions", "sum"),
        )
        .sort_values("month")
    )
    return agg


def scorecard_metrics(df: pd.DataFrame) -> dict[str, float | None]:
    return {
        "spend": df["spend"].sum(),
        "clicks": df["clicks"].sum(),
        "cpc": df["cpc"].mean(),
        "leads": df["leads"].sum(),
        "cpl": df["cpl"].mean(),
        "dcs": df["dcs"].sum(),
        "cpdc": df["cpdc"].mean(),
        "conversions": df["conversions"].sum(),
        "cac": df["cac"].mean(),
        "lead_to_patient_pct": df["lead_to_patient_pct"].mean(),
    }
