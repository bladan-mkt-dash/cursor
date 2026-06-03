"""Google Sheets read/write helpers for the cross-channel tracker."""

from __future__ import annotations

import json
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from tracker_config import SHEET, SPREADSHEET_ID


def _sheets_service():
    token_path = Path.home() / ".config" / "mcp-google-sheets" / "token.json"
    info = json.loads(token_path.read_text(encoding="utf-8"))
    creds = Credentials.from_authorized_user_info(info, info["scopes"])
    return build("sheets", "v4", credentials=creds)


def read_cell(row: int, column: str) -> str | None:
    sheets = _sheets_service()
    vals = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=SPREADSHEET_ID, range=f"'{SHEET}'!{column}{row}")
        .execute()
        .get("values", [[]])
    )
    if not vals or not vals[0]:
        return None
    return str(vals[0][0]).strip()


def read_int_cell(row: int, column: str) -> int | None:
    raw = read_cell(row, column)
    if raw is None or raw in ("", "-"):
        return None
    try:
        return int(float(raw.replace(",", "")))
    except ValueError:
        return None


def write_column(column: str, updates: dict[int, str]) -> None:
    sheets = _sheets_service()
    data = [
        {"range": f"'{SHEET}'!{column}{row}", "values": [[value]]}
        for row, value in sorted(updates.items())
    ]
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()


def write_columns(updates_by_col: dict[str, dict[int, str]]) -> None:
    sheets = _sheets_service()
    data = [
        {"range": f"'{SHEET}'!{col}{row}", "values": [[value]]}
        for col, row_values in sorted(updates_by_col.items())
        for row, value in sorted(row_values.items())
    ]
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()
