"""Monday.com GraphQL API client for boards, workspaces, and task items."""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

API_URL = "https://api.monday.com/v2"
DEFAULT_PAGE_LIMIT = 100
MAX_PAGES = 500

DONE_STATUS_LABELS = frozenset(
    {
        "done",
        "complete",
        "completed",
        "finished",
        "closed",
        "won't do",
        "wont do",
        "cancelled",
        "canceled",
    }
)


def _api_token() -> str:
    return (os.getenv("MONDAY_API_TOKEN") or "").strip()


def _request_headers() -> dict[str, str]:
    token = _api_token()
    if not token:
        raise ValueError(
            "Set MONDAY_API_TOKEN in .env (Personal API token from "
            "monday.com -> Profile -> Developers -> My Access Tokens)"
        )
    return {
        "Authorization": token,
        "Content-Type": "application/json",
        "API-Version": "2024-10",
    }


def graphql_query(
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    timeout: int = 60,
) -> dict[str, Any]:
    """Run a GraphQL query against Monday.com API v2."""
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables

    response = requests.post(
        API_URL,
        json=payload,
        headers=_request_headers(),
        timeout=timeout,
    )
    if not response.ok:
        detail = response.text
        try:
            err = response.json()
            if isinstance(err, dict):
                detail = err.get("error_message") or err.get("message") or str(err)
        except ValueError:
            pass
        raise RuntimeError(f"Monday.com API error {response.status_code}: {detail}")

    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Monday.com API returned an unexpected response shape")

    errors = data.get("errors")
    if errors:
        messages = []
        for err in errors:
            if isinstance(err, dict):
                messages.append(err.get("message") or str(err))
            else:
                messages.append(str(err))
        raise RuntimeError("Monday.com GraphQL error: " + "; ".join(messages))

    inner = data.get("data")
    return inner if isinstance(inner, dict) else {}


def list_workspaces(*, limit: int = 100) -> list[dict[str, Any]]:
    """Return workspaces visible to the API token."""
    query = """
    query ($limit: Int!) {
      workspaces(limit: $limit) {
        id
        name
        kind
        description
      }
    }
    """
    data = graphql_query(query, {"limit": limit})
    workspaces = data.get("workspaces")
    if not isinstance(workspaces, list):
        return []
    return [w for w in workspaces if isinstance(w, dict)]


def list_boards(
    *,
    workspace_ids: list[str] | None = None,
    limit: int = 100,
    page: int = 1,
) -> list[dict[str, Any]]:
    """
    Return boards (projects) visible to the API token.

    Optionally filter by ``workspace_ids``.
    """
    if workspace_ids:
        query = """
        query ($workspace_ids: [ID], $limit: Int!, $page: Int!) {
          boards(workspace_ids: $workspace_ids, limit: $limit, page: $page) {
            id
            name
            state
            board_kind
            workspace_id
            description
          }
        }
        """
        variables: dict[str, Any] = {
            "workspace_ids": [str(w) for w in workspace_ids],
            "limit": limit,
            "page": page,
        }
    else:
        query = """
        query ($limit: Int!, $page: Int!) {
          boards(limit: $limit, page: $page) {
            id
            name
            state
            board_kind
            workspace_id
            description
          }
        }
        """
        variables = {"limit": limit, "page": page}

    data = graphql_query(query, variables)
    boards = data.get("boards")
    if not isinstance(boards, list):
        return []
    return [b for b in boards if isinstance(b, dict)]


def get_board_columns(board_id: str) -> list[dict[str, Any]]:
    """Return column definitions for a board."""
    query = """
    query ($board_ids: [ID!]) {
      boards(ids: $board_ids) {
        id
        name
        columns {
          id
          title
          type
          settings_str
        }
      }
    }
    """
    data = graphql_query(query, {"board_ids": [str(board_id)]})
    boards = data.get("boards") or []
    if not boards or not isinstance(boards[0], dict):
        return []
    columns = boards[0].get("columns")
    if not isinstance(columns, list):
        return []
    return [c for c in columns if isinstance(c, dict)]


def _pick_column_id(columns: list[dict[str, Any]], column_type: str) -> str | None:
    for col in columns:
        if (col.get("type") or "").casefold() == column_type.casefold():
            cid = col.get("id")
            if cid:
                return str(cid)
    return None


def _pick_column_id_by_title(columns: list[dict[str, Any]], *needles: str) -> str | None:
    for col in columns:
        title = (col.get("title") or "").casefold()
        if any(n.casefold() in title for n in needles):
            cid = col.get("id")
            if cid:
                return str(cid)
    return None


def build_column_map(columns: list[dict[str, Any]]) -> dict[str, str | None]:
    """
    Map logical roles (status, assignee, due_date) to column ids on a board.

    Uses column type first, then common title patterns for due dates.
    """
    status_id = _pick_column_id(columns, "status")
    assignee_id = _pick_column_id(columns, "people") or _pick_column_id(columns, "person")
    due_id = (
        _pick_column_id_by_title(columns, "due date", "deadline", "due")
        or _pick_column_id(columns, "date")
    )
    return {
        "status": status_id,
        "assignee": assignee_id,
        "due_date": due_id,
    }


def parse_column_value(column_value: dict[str, Any]) -> str:
    """Extract a display string from a Monday column_values entry."""
    text = (column_value.get("text") or "").strip()
    if text:
        return text

    raw = column_value.get("value")
    if raw in (None, "", "null"):
        return ""

    parsed: Any = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip()

    if not isinstance(parsed, dict):
        return str(parsed).strip()

    col_type = (column_value.get("type") or "").casefold()

    if col_type == "date":
        d = parsed.get("date") or parsed.get("time")
        return str(d).strip() if d else ""

    if col_type in ("people", "person"):
        names: list[str] = []
        for entry in parsed.get("personsAndTeams") or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("kind") == "person":
                name = entry.get("name") or entry.get("id")
                if name:
                    names.append(str(name))
        return ", ".join(names)

    if col_type == "status":
        label = parsed.get("label") or parsed.get("index")
        return str(label).strip() if label is not None else ""

    for key in ("label", "text", "value", "name"):
        val = parsed.get(key)
        if val not in (None, ""):
            return str(val).strip()
    return ""


def _parse_due_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _is_done_status(status: str) -> bool:
    return (status or "").strip().casefold() in DONE_STATUS_LABELS


def item_to_row(
    item: dict[str, Any],
    *,
    board_id: str,
    board_name: str,
    column_map: dict[str, str | None],
    column_titles: dict[str, str],
) -> dict[str, Any]:
    """Convert a Monday item payload into a flat, DataFrame-friendly row."""
    values_by_id: dict[str, dict[str, Any]] = {}
    for cv in item.get("column_values") or []:
        if isinstance(cv, dict) and cv.get("id"):
            values_by_id[str(cv["id"])] = cv

    def _value_for(role: str) -> str:
        col_id = column_map.get(role)
        if not col_id:
            return ""
        cv = values_by_id.get(col_id)
        return parse_column_value(cv) if cv else ""

    status = _value_for("status")
    assignee = _value_for("assignee")
    due_raw = _value_for("due_date")
    due_dt = _parse_due_date(due_raw)
    today = date.today()
    overdue = bool(
        due_dt
        and due_dt < today
        and not _is_done_status(status)
    )

    return {
        "item_id": str(item.get("id") or ""),
        "name": (item.get("name") or "").strip(),
        "board_id": str(board_id),
        "board_name": board_name,
        "assignee": assignee or "Unassigned",
        "status": status or "No status",
        "due_date": due_dt.isoformat() if due_dt else "",
        "due_date_raw": due_raw,
        "overdue": overdue,
        "updated_at": (item.get("updated_at") or "").strip(),
        "state": (item.get("state") or "active").strip(),
        "status_column": column_titles.get(column_map.get("status") or "", ""),
        "assignee_column": column_titles.get(column_map.get("assignee") or "", ""),
        "due_date_column": column_titles.get(column_map.get("due_date") or "", ""),
    }


def fetch_board_items_page(
    board_id: str,
    *,
    limit: int = DEFAULT_PAGE_LIMIT,
    cursor: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    """
    Fetch one page of items for a board.

    Returns (board_payload, next_cursor).
    """
    if cursor:
        query = """
        query ($cursor: String!, $limit: Int!) {
          next_items_page(cursor: $cursor, limit: $limit) {
            cursor
            items {
              id
              name
              state
              updated_at
              column_values {
                id
                type
                text
                value
              }
            }
          }
        }
        """
        data = graphql_query(query, {"cursor": cursor, "limit": limit})
        page = data.get("next_items_page") or {}
        return {"items_page": page}, page.get("cursor")
    query = """
    query ($board_ids: [ID!], $limit: Int!) {
      boards(ids: $board_ids) {
        id
        name
        columns {
          id
          title
          type
        }
        items_page(limit: $limit) {
          cursor
          items {
            id
            name
            state
            updated_at
            column_values {
              id
              type
              text
              value
            }
          }
        }
      }
    }
    """
    data = graphql_query(query, {"board_ids": [str(board_id)], "limit": limit})
    boards = data.get("boards") or []
    if not boards:
        return {}, None
    board = boards[0]
    page = board.get("items_page") or {}
    return board, page.get("cursor")


def fetch_board_items(
    board_id: str,
    *,
    board_name: str | None = None,
    page_limit: int = DEFAULT_PAGE_LIMIT,
    max_pages: int = MAX_PAGES,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Fetch all items from a board with cursor pagination.

    Returns (rows, truncated) where truncated is True if max_pages was hit.
    """
    board, cursor = fetch_board_items_page(board_id, limit=page_limit)
    if not board:
        return [], False

    resolved_name = board_name or (board.get("name") or f"Board {board_id}")
    columns = [c for c in (board.get("columns") or []) if isinstance(c, dict)]
    column_map = build_column_map(columns)
    column_titles = {str(c["id"]): str(c.get("title") or "") for c in columns if c.get("id")}

    rows: list[dict[str, Any]] = []
    page = board.get("items_page") or {}
    items = page.get("items") or []
    for item in items:
        if isinstance(item, dict):
            rows.append(
                item_to_row(
                    item,
                    board_id=str(board_id),
                    board_name=resolved_name,
                    column_map=column_map,
                    column_titles=column_titles,
                )
            )

    pages = 1
    while cursor and pages < max_pages:
        page_data, cursor = fetch_board_items_page(
            board_id, limit=page_limit, cursor=cursor
        )
        page_payload = page_data.get("items_page") or {}
        batch = page_payload.get("items") or []
        for item in batch:
            if isinstance(item, dict):
                rows.append(
                    item_to_row(
                        item,
                        board_id=str(board_id),
                        board_name=resolved_name,
                        column_map=column_map,
                        column_titles=column_titles,
                    )
                )
        pages += 1
        if not batch:
            break

    truncated = bool(cursor and pages >= max_pages)
    return rows, truncated


def fetch_items_from_boards(
    board_ids: list[str],
    *,
    board_names: dict[str, str] | None = None,
    page_limit: int = DEFAULT_PAGE_LIMIT,
    max_pages: int = MAX_PAGES,
) -> tuple[pd.DataFrame, dict[str, bool]]:
    """
    Fetch items from multiple boards and return a combined DataFrame.

    ``board_names`` maps board id → display name (optional).
    Second return value maps board id → truncated flag.
    """
    if not board_ids:
        return pd.DataFrame(), {}

    names = board_names or {}
    all_rows: list[dict[str, Any]] = []
    truncated_by_board: dict[str, bool] = {}

    for bid in board_ids:
        bid_str = str(bid)
        rows, truncated = fetch_board_items(
            bid_str,
            board_name=names.get(bid_str),
            page_limit=page_limit,
            max_pages=max_pages,
        )
        all_rows.extend(rows)
        truncated_by_board[bid_str] = truncated

    if not all_rows:
        return pd.DataFrame(
            columns=[
                "item_id",
                "name",
                "board_id",
                "board_name",
                "assignee",
                "status",
                "due_date",
                "due_date_raw",
                "overdue",
                "updated_at",
                "state",
            ]
        ), truncated_by_board

    df = pd.DataFrame(all_rows)
    if "due_date" in df.columns:
        df["due_date_sort"] = pd.to_datetime(df["due_date"], errors="coerce")
        df = df.sort_values(["board_name", "due_date_sort", "name"], na_position="last")
        df = df.drop(columns=["due_date_sort"])
    else:
        df = df.sort_values(["board_name", "name"])
    return df.reset_index(drop=True), truncated_by_board


def summarize_items(df: pd.DataFrame) -> dict[str, Any]:
    """Compute summary stats for a tasks DataFrame."""
    if df.empty:
        return {
            "total": 0,
            "by_status": {},
            "by_assignee": {},
            "overdue_count": 0,
            "unassigned_count": 0,
        }

    by_status = df["status"].value_counts().to_dict() if "status" in df.columns else {}
    by_assignee = df["assignee"].value_counts().to_dict() if "assignee" in df.columns else {}
    overdue_count = int(df["overdue"].sum()) if "overdue" in df.columns else 0
    unassigned = 0
    if "assignee" in df.columns:
        unassigned = int((df["assignee"] == "Unassigned").sum())

    return {
        "total": len(df),
        "by_status": {str(k): int(v) for k, v in by_status.items()},
        "by_assignee": {str(k): int(v) for k, v in by_assignee.items()},
        "overdue_count": overdue_count,
        "unassigned_count": unassigned,
    }
