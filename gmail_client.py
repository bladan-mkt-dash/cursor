"""Gmail API helpers for weekly inbox/sent digests."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

CONFIG_DIR = Path.home() / ".config" / "mcp-google-sheets"
CREDENTIALS_PATH = CONFIG_DIR / "credentials.json"
GMAIL_TOKEN_PATH = CONFIG_DIR / "gmail_token.json"

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES = [GMAIL_READONLY_SCOPE]

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


@dataclass(frozen=True)
class GmailMessage:
    message_id: str
    thread_id: str
    folder: str
    subject: str
    from_addr: str
    to_addrs: str
    date: datetime | None
    snippet: str
    label_ids: tuple[str, ...]


def _load_token_info() -> dict | None:
    if not GMAIL_TOKEN_PATH.exists():
        return None
    return json.loads(GMAIL_TOKEN_PATH.read_text(encoding="utf-8"))


def get_credentials(*, allow_interactive: bool = False) -> Credentials:
    """Return valid Gmail credentials; optionally run browser OAuth."""
    creds: Credentials | None = None
    info = _load_token_info()
    if info:
        creds = Credentials.from_authorized_user_info(info, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        GMAIL_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        return creds

    if creds and creds.valid:
        return creds

    if not allow_interactive:
        raise RuntimeError(
            f"Gmail is not authorized. Run:\n  python auth_google_gmail.py\n"
            f"Token path: {GMAIL_TOKEN_PATH}"
        )

    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"Missing OAuth client file: {CREDENTIALS_PATH}\n"
            "Use the same Desktop OAuth client as Google Sheets (Google Cloud Console)."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    GMAIL_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def gmail_service(*, allow_interactive: bool = False):
    return build("gmail", "v1", credentials=get_credentials(allow_interactive=allow_interactive))


def week_range(
    *,
    days: int = 7,
    end: date | None = None,
) -> tuple[date, date]:
    """Inclusive calendar range ending on ``end`` (default: today)."""
    if days < 1:
        raise ValueError("days must be at least 1")
    end_date = end or date.today()
    start_date = end_date - timedelta(days=days - 1)
    return start_date, end_date


def gmail_search_dates(start: date, end: date) -> str:
    """Gmail ``after`` / ``before`` query for an inclusive date range."""
    after = start - timedelta(days=1)
    before = end + timedelta(days=1)
    return f"after:{after:%Y/%m/%d} before:{before:%Y/%m/%d}"


def _header_map(payload: dict) -> dict[str, str]:
    headers: dict[str, str] = {}
    for block in payload.get("headers", []):
        name = (block.get("name") or "").strip()
        value = (block.get("value") or "").strip()
        if name:
            headers[name.lower()] = value
    return headers


def _parse_internal_date(ms: str | None) -> datetime | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def _parse_header_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError, IndexError):
        return None


def _first_email(text: str) -> str:
    match = _EMAIL_RE.search(text or "")
    return match.group(0).lower() if match else (text or "").strip()[:80]


def _normalize_subject(subject: str) -> str:
    s = (subject or "").strip()
    while True:
        lowered = s.lower()
        if lowered.startswith("re:"):
            s = s[3:].strip()
            continue
        if lowered.startswith("fwd:") or lowered.startswith("fw:"):
            s = s[4:].strip()
            continue
        break
    return s or "(no subject)"


def list_message_ids(
    service,
    *,
    query: str,
    max_messages: int = 500,
) -> list[str]:
    ids: list[str] = []
    page_token: str | None = None
    while len(ids) < max_messages:
        batch_size = min(100, max_messages - len(ids))
        result = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=batch_size, pageToken=page_token)
            .execute()
        )
        for item in result.get("messages", []):
            mid = item.get("id")
            if mid:
                ids.append(mid)
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return ids


def fetch_message(
    service,
    message_id: str,
    *,
    folder: str,
) -> GmailMessage:
    msg = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        )
        .execute()
    )
    headers = _header_map(msg.get("payload", {}))
    header_date = _parse_header_date(headers.get("date", ""))
    internal_date = _parse_internal_date(msg.get("internalDate"))
    when = header_date or internal_date

    return GmailMessage(
        message_id=message_id,
        thread_id=msg.get("threadId", ""),
        folder=folder,
        subject=headers.get("subject", "").strip() or "(no subject)",
        from_addr=_first_email(headers.get("from", "")),
        to_addrs=headers.get("to", "").strip(),
        date=when,
        snippet=(msg.get("snippet") or "").replace("\n", " ").strip(),
        label_ids=tuple(msg.get("labelIds", [])),
    )


def fetch_messages_for_query(
    service,
    *,
    base_query: str,
    folder: str,
    start: date,
    end: date,
    max_messages: int = 500,
) -> list[GmailMessage]:
    date_q = gmail_search_dates(start, end)
    query = f"{base_query} {date_q}".strip()
    ids = list_message_ids(service, query=query, max_messages=max_messages)
    return [fetch_message(service, mid, folder=folder) for mid in ids]


def fetch_inbox_and_sent(
    service,
    *,
    start: date,
    end: date,
    max_per_folder: int = 500,
) -> tuple[list[GmailMessage], list[GmailMessage]]:
    inbox = fetch_messages_for_query(
        service,
        base_query="in:inbox",
        folder="inbox",
        start=start,
        end=end,
        max_messages=max_per_folder,
    )
    sent = fetch_messages_for_query(
        service,
        base_query="in:sent",
        folder="sent",
        start=start,
        end=end,
        max_messages=max_per_folder,
    )
    return inbox, sent


def top_addresses(messages: Iterable[GmailMessage], *, field: str, limit: int = 12) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for msg in messages:
        if field == "from":
            counter[msg.from_addr] += 1
        elif field == "to":
            for addr in _EMAIL_RE.findall(msg.to_addrs):
                counter[addr.lower()] += 1
    return counter.most_common(limit)


def active_threads(messages: Iterable[GmailMessage], *, min_messages: int = 3) -> list[tuple[str, int, str]]:
    by_thread: dict[str, list[GmailMessage]] = defaultdict(list)
    for msg in messages:
        if msg.thread_id:
            by_thread[msg.thread_id].append(msg)

    rows: list[tuple[str, int, str]] = []
    for thread_id, msgs in by_thread.items():
        if len(msgs) < min_messages:
            continue
        subjects = {_normalize_subject(m.subject) for m in msgs}
        subject = sorted(subjects, key=len)[0]
        rows.append((subject, len(msgs), thread_id))

    rows.sort(key=lambda r: (-r[1], r[0].lower()))
    return rows[:20]


def format_datetime(dt: datetime | None) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")

