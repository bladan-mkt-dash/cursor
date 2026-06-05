"""Quick check that Google Chat API access works (workspace / MCP scopes)."""

from __future__ import annotations

# Bump when token-loading logic changes (war_room_data reloads this module).
CHAT_AUTH_REVISION = "2026-06-05-scope-fix-v1"

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

CONFIG_DIR = Path.home() / ".config" / "mcp-google-sheets"
CREDENTIALS_PATH = CONFIG_DIR / "credentials.json"
CHAT_TOKEN_PATH = CONFIG_DIR / "chat_token.json"
WORKSPACE_MCP_CREDS_DIR = Path.home() / ".google_workspace_mcp" / "credentials"

# Scopes requested when running auth_google_chat.py (must be on OAuth consent screen).
CHAT_AUTH_SCOPES = [
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.users.readstate.readonly",
]

_CHAT_REQUIRED_SCOPES = frozenset(
    {
        "https://www.googleapis.com/auth/chat.spaces.readonly",
        "https://www.googleapis.com/auth/chat.messages.readonly",
    }
)


def _load_mcp_oauth_from_json() -> tuple[str, str] | None:
    mcp_path = _PROJECT_DIR / ".cursor" / "mcp.json"
    if not mcp_path.exists():
        return None
    data = json.loads(mcp_path.read_text(encoding="utf-8"))
    env = (data.get("mcpServers") or {}).get("google-workspace", {}).get("env") or {}
    client_id = (env.get("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
    client_secret = (env.get("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()
    if client_id and client_secret:
        return client_id, client_secret
    return None


def _token_has_chat_access(creds: Credentials) -> bool:
    scopes = set(creds.scopes or [])
    return _CHAT_REQUIRED_SCOPES.issubset(scopes)


def _load_credentials_from_file(
    path: Path,
    *,
    persist_refresh: bool = False,
) -> Credentials | None:
    """
    Load OAuth credentials using the scopes stored in the token file.

    Passing a different scope list to ``from_authorized_user_file`` causes
    ``invalid_scope`` errors on refresh when the file was issued with fewer
    scopes (e.g. workspace-mcp tokens).
    """
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        creds = Credentials.from_authorized_user_info(info)
    except (ValueError, OSError, json.JSONDecodeError):
        return None
    if not _token_has_chat_access(creds):
        return None
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            if persist_refresh:
                path.write_text(creds.to_json(), encoding="utf-8")
        except Exception:
            return None
    return creds if creds.valid else None


def _workspace_mcp_token() -> Credentials | None:
    if not WORKSPACE_MCP_CREDS_DIR.is_dir():
        return None
    for path in sorted(WORKSPACE_MCP_CREDS_DIR.glob("*.json")):
        if path.name == "oauth_states.json":
            continue
        creds = _load_credentials_from_file(path)
        if creds:
            return creds
    return None


def _chat_token(allow_interactive: bool) -> Credentials:
    if CHAT_TOKEN_PATH.exists():
        creds = _load_credentials_from_file(CHAT_TOKEN_PATH, persist_refresh=True)
        if creds:
            return creds

    if not allow_interactive:
        raise RuntimeError(
            "No valid Chat token found.\n"
            f"  - workspace-mcp creds: {WORKSPACE_MCP_CREDS_DIR} (empty or missing Chat scopes)\n"
            f"  - script token: {CHAT_TOKEN_PATH}\n"
            "Fix: run  python auth_google_chat.py\n"
            "  (or restart google-workspace MCP and complete OAuth in the browser)"
        )

    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(f"Missing OAuth client: {CREDENTIALS_PATH}")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_PATH), CHAT_AUTH_SCOPES
    )
    creds = flow.run_local_server(port=0, open_browser=True)
    CHAT_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def get_chat_credentials(*, allow_interactive: bool = False) -> Credentials:
    creds = _workspace_mcp_token()
    if creds and creds.valid:
        return creds
    return _chat_token(allow_interactive)


def main() -> int:
    print("Google Chat connection check")
    print(f"  mcp.json google-workspace tools: gmail, calendar, tasks, chat")
    print(f"  WORKSPACE_MCP_READ_ONLY: true (Chat read-only expected)")
    print()

    mcp_oauth = _load_mcp_oauth_from_json()
    sheets_client = "?"
    if CREDENTIALS_PATH.exists():
        installed = json.loads(CREDENTIALS_PATH.read_text()).get("installed", {})
        sheets_client = (installed.get("client_id") or "?")[:24] + "..."
    mcp_client = (mcp_oauth[0][:24] + "...") if mcp_oauth else "not found"
    print(f"  OAuth client in credentials.json: {sheets_client}")
    print(f"  OAuth client in .cursor/mcp.json:   {mcp_client}")
    if mcp_oauth and sheets_client != "?" and mcp_client != sheets_client:
        print("  NOTE: These client IDs differ. Cursor workspace-mcp uses mcp.json;")
        print("        Python scripts use credentials.json unless you align them.")
    print()

    ws_files = list(WORKSPACE_MCP_CREDS_DIR.glob("*.json")) if WORKSPACE_MCP_CREDS_DIR.is_dir() else []
    print(f"  workspace-mcp token files: {len(ws_files)} under {WORKSPACE_MCP_CREDS_DIR}")
    print(f"  chat_token.json: {'yes' if CHAT_TOKEN_PATH.exists() else 'no'}")
    print()

    try:
        creds = get_chat_credentials(allow_interactive="--auth" in sys.argv)
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"NOT READY: {exc}")
        return 1

    service = build("chat", "v1", credentials=creds, cache_discovery=False)
    resp = service.spaces().list(pageSize=10).execute()
    spaces = resp.get("spaces") or []
    print(f"OK: Chat API authenticated — {len(spaces)} space(s) returned (first page, max 10)")
    for sp in spaces[:5]:
        name = sp.get("displayName") or sp.get("name") or "?"
        print(f"    - {name}")
    if len(spaces) > 5:
        print(f"    … and {len(spaces) - 5} more on this page")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
