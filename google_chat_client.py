"""Google Chat API helpers for Marketing War Room (read-only)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from googleapiclient.discovery import build

from verify_google_chat_connection import get_chat_credentials

ME_USER = "users/me"


@dataclass(frozen=True)
class ChatPendingMessage:
    space_display_name: str
    space_resource: str
    sender_name: str
    preview: str
    create_time: datetime | None


def chat_service(*, allow_interactive: bool = False):
    creds = get_chat_credentials(allow_interactive=allow_interactive)
    return build("chat", "v1", credentials=creds, cache_discovery=False)


def list_all_spaces(service) -> list[dict]:
    spaces: list[dict] = []
    page_token: str | None = None
    while True:
        resp = (
            service.spaces()
            .list(pageSize=100, pageToken=page_token)
            .execute()
        )
        spaces.extend(resp.get("spaces") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return spaces


def _parse_rfc3339(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _space_matches_filters(space: dict, filters: list[str]) -> bool:
    if not filters:
        return bool((space.get("displayName") or "").strip())
    display = (space.get("displayName") or "").casefold()
    return any(part.strip().casefold() in display for part in filters if part.strip())


def _sender_label(message: dict) -> str:
    sender = message.get("sender") or {}
    display = (sender.get("displayName") or "").strip()
    if display:
        return display
    name = (sender.get("name") or "").strip()
    if name.startswith("users/"):
        return name.split("/", 1)[-1]
    return name or "Unknown"


def _message_mentions_me(message: dict, *, me_user_id: str | None) -> bool:
    me_ids = {ME_USER}
    if me_user_id:
        me_ids.add(f"users/{me_user_id}")
    for ann in message.get("annotations") or []:
        if ann.get("type") != "USER_MENTION":
            continue
        user = (ann.get("userMention") or {}).get("user") or {}
        if user.get("name") in me_ids:
            return True
    return False


def _me_user_id_from_read_state(service, space_id: str) -> str | None:
    try:
        state = (
            service.users()
            .spaces()
            .getSpaceReadState(name=f"users/me/spaces/{space_id}/spaceReadState")
            .execute()
        )
    except Exception:
        return None
    name = (state.get("name") or "").strip()
    # users/{id}/spaces/{space}/spaceReadState
    parts = name.split("/")
    if len(parts) >= 2 and parts[0] == "users":
        return parts[1]
    return None


def fetch_unread_mentions(
    service,
    *,
    space_display_names: list[str] | None = None,
    max_spaces: int = 50,
    max_per_space: int = 25,
) -> list[ChatPendingMessage]:
    """
    Unread @mentions in Chat spaces.

    When ``space_display_names`` is empty, scans spaces that have a display name
    (named rooms, not unnamed DMs). When set, only spaces whose display name
    contains any filter substring (case-insensitive).
    """
    filters = [p.strip() for p in (space_display_names or []) if p.strip()]
    spaces = list_all_spaces(service)
    targets = [sp for sp in spaces if _space_matches_filters(sp, filters)]
    if filters:
        targets = targets[:max_spaces]
    else:
        targets = targets[:max_spaces]

    me_user_id: str | None = None
    pending: list[ChatPendingMessage] = []

    for sp in targets:
        resource = sp.get("name") or ""
        if not resource.startswith("spaces/"):
            continue
        space_id = resource.split("/", 1)[-1]
        if me_user_id is None:
            me_user_id = _me_user_id_from_read_state(service, space_id)

        try:
            read_state = (
                service.users()
                .spaces()
                .getSpaceReadState(name=f"users/me/spaces/{space_id}/spaceReadState")
                .execute()
            )
            last_read = read_state.get("lastReadTime") or "1970-01-01T00:00:00Z"
            resp = (
                service.spaces()
                .messages()
                .list(
                    parent=resource,
                    filter=f'createTime > "{last_read}"',
                    pageSize=max_per_space,
                )
                .execute()
            )
        except Exception:
            continue

        display = (sp.get("displayName") or "").strip() or resource
        for message in resp.get("messages") or []:
            if (message.get("sender") or {}).get("name") == ME_USER:
                continue
            if not _message_mentions_me(message, me_user_id=me_user_id):
                continue
            text = (message.get("text") or "").replace("\n", " ").strip()
            pending.append(
                ChatPendingMessage(
                    space_display_name=display,
                    space_resource=resource,
                    sender_name=_sender_label(message),
                    preview=text[:160] if text else "(attachment or card)",
                    create_time=_parse_rfc3339(message.get("createTime")),
                )
            )

    pending.sort(
        key=lambda row: row.create_time or datetime.min.replace(tzinfo=timezone.utc)
    )
    return pending
