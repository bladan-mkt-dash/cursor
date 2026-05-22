"""Quick check that Monday.com API token works and list available boards.

Setup:
  1. monday.com → profile (avatar) → Developers → My Access Tokens → Generate
  2. Add to project .env:  MONDAY_API_TOKEN=your_token_here
  3. Run:  python verify_monday_connection.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")


def main() -> int:
    token = (os.getenv("MONDAY_API_TOKEN") or "").strip()
    if not token:
        print("MISSING: MONDAY_API_TOKEN is not set in .env")
        print()
        print("How to fix:")
        print("  1. monday.com -> Profile -> Developers -> My Access Tokens -> Generate")
        print("  2. Add to .env:  MONDAY_API_TOKEN=your_token_here")
        print("  3. Re-run:  python verify_monday_connection.py")
        return 1

    try:
        from monday_client import list_boards, list_workspaces
    except ImportError as exc:
        print(f"Import error: {exc}")
        return 1

    try:
        workspaces = list_workspaces()
        print(f"OK: authenticated — {len(workspaces)} workspace(s)")
        for ws in workspaces:
            print(f"  Workspace {ws.get('id')}: {ws.get('name')}")

        boards = list_boards(limit=100)
        active = [b for b in boards if (b.get("state") or "").casefold() == "active"]
        print(f"\nBoards: {len(boards)} total ({len(active)} active)")
        for board in boards[:25]:
            state = board.get("state") or "?"
            print(
                f"  [{state}] {board.get('id')}: {board.get('name')} "
                f"(workspace {board.get('workspace_id')})"
            )
        if len(boards) > 25:
            print(f"  … and {len(boards) - 25} more")

        print("\nUse board IDs in the Monday Team Activity dashboard sidebar filter.")
        return 0
    except ValueError as exc:
        print(f"Config error: {exc}")
        return 1
    except RuntimeError as exc:
        print(f"API error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
