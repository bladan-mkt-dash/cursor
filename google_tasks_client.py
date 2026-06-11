"""Google Tasks API helpers for Marketing War Room alerts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

CONFIG_DIR = Path.home() / ".config" / "mcp-google-sheets"
CREDENTIALS_PATH = CONFIG_DIR / "credentials.json"
TASKS_TOKEN_PATH = CONFIG_DIR / "tasks_token.json"
WORKSPACE_MCP_CREDS_DIR = Path.home() / ".google_workspace_mcp" / "credentials"

TASKS_READONLY_SCOPE = "https://www.googleapis.com/auth/tasks.readonly"
SCOPES = [TASKS_READONLY_SCOPE]


class TaskAlertSeverity(str, Enum):
    OVERDUE = "Overdue"
    DUE_TODAY = "Due today"
    DUE_SOON = "Due soon"


@dataclass(frozen=True)
class TaskAlert:
    severity: TaskAlertSeverity
    title: str
    list_name: str
    due_date: date
    sort_key: date


def _load_token_info() -> dict | None:
    if not TASKS_TOKEN_PATH.exists():
        return None
    return json.loads(TASKS_TOKEN_PATH.read_text(encoding="utf-8"))


def _workspace_mcp_token() -> Credentials | None:
    if not WORKSPACE_MCP_CREDS_DIR.is_dir():
        return None
    for path in sorted(WORKSPACE_MCP_CREDS_DIR.glob("*.json")):
        try:
            creds = Credentials.from_authorized_user_file(str(path), SCOPES)
        except (ValueError, OSError):
            continue
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            return creds
    return None


def get_credentials(*, allow_interactive: bool = False) -> Credentials:
    creds = _workspace_mcp_token()
    if creds and creds.valid:
        return creds

    info = _load_token_info()
    if info:
        creds = Credentials.from_authorized_user_info(info, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TASKS_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        return creds

    if creds and creds.valid:
        return creds

    if not allow_interactive:
        raise RuntimeError(
            "Google Tasks is not authorized. Run:\n  python auth_google_tasks.py\n"
            f"Token path: {TASKS_TOKEN_PATH}"
        )

    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"Missing OAuth client file: {CREDENTIALS_PATH}\n"
            "Use the same Desktop OAuth client as Google Sheets (Google Cloud Console)."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def tasks_service(*, allow_interactive: bool = False):
    return build(
        "tasks",
        "v1",
        credentials=get_credentials(allow_interactive=allow_interactive),
        cache_discovery=False,
    )


def _parse_due(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Google Tasks due dates are calendar days stored at midnight UTC; the time
    # portion is not meaningful. Do not convert to local time or tasks appear
    # one day early (overdue) in US timezones.
    return dt.astimezone(timezone.utc).date()


def _severity_for_due(due: date, *, today: date, due_soon_days: int) -> TaskAlertSeverity | None:
    if due < today:
        return TaskAlertSeverity.OVERDUE
    if due == today:
        return TaskAlertSeverity.DUE_TODAY
    if due <= today + timedelta(days=due_soon_days):
        return TaskAlertSeverity.DUE_SOON
    return None


def _severity_rank(severity: TaskAlertSeverity) -> int:
    return {
        TaskAlertSeverity.OVERDUE: 0,
        TaskAlertSeverity.DUE_TODAY: 1,
        TaskAlertSeverity.DUE_SOON: 2,
    }[severity]


def list_tasklists(service) -> list[dict]:
    rows: list[dict] = []
    page_token: str | None = None
    while True:
        resp = service.tasklists().list(maxResults=100, pageToken=page_token).execute()
        rows.extend(resp.get("items") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return rows


def _list_matches_filter(tasklist: dict, filters: list[str]) -> bool:
    if not filters:
        return True
    list_id = (tasklist.get("id") or "").strip()
    title = (tasklist.get("title") or "").strip().casefold()
    for raw in filters:
        part = raw.strip()
        if not part:
            continue
        if part == list_id or part.casefold() == title:
            return True
        if part.casefold() in title:
            return True
    return False


def list_open_tasks(
    service,
    *,
    tasklist_id: str,
    max_tasks: int = 100,
) -> list[dict]:
    tasks: list[dict] = []
    page_token: str | None = None
    while len(tasks) < max_tasks:
        batch = min(100, max_tasks - len(tasks))
        resp = (
            service.tasks()
            .list(
                tasklist=tasklist_id,
                showCompleted=False,
                showHidden=False,
                maxResults=batch,
                pageToken=page_token,
            )
            .execute()
        )
        tasks.extend(resp.get("items") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return tasks


def fetch_task_alerts(
    service,
    *,
    list_filters: list[str] | None = None,
    due_soon_days: int = 3,
    max_per_list: int = 100,
    today: date | None = None,
) -> list[TaskAlert]:
    """
    Actionable task alerts: overdue, due today, and due within ``due_soon_days``.

    Tasks without a due date are excluded (inbox noise). Only ``needsAction`` tasks
    are included.
    """
    today = today or date.today()
    filters = [p.strip() for p in (list_filters or []) if p.strip()]
    alerts: list[TaskAlert] = []

    for tasklist in list_tasklists(service):
        if not _list_matches_filter(tasklist, filters):
            continue
        list_id = tasklist.get("id") or ""
        list_title = (tasklist.get("title") or "").strip() or list_id
        if not list_id:
            continue

        for task in list_open_tasks(service, tasklist_id=list_id, max_tasks=max_per_list):
            if (task.get("status") or "").lower() == "completed":
                continue
            due = _parse_due(task.get("due"))
            if due is None:
                continue
            severity = _severity_for_due(due, today=today, due_soon_days=due_soon_days)
            if severity is None:
                continue
            title = (task.get("title") or "").strip() or "(untitled)"
            alerts.append(
                TaskAlert(
                    severity=severity,
                    title=title,
                    list_name=list_title,
                    due_date=due,
                    sort_key=due,
                )
            )

    alerts.sort(key=lambda row: (_severity_rank(row.severity), row.sort_key, row.title.lower()))
    return alerts
