"""
Find Monday.com item comments that @mention jbuenviaje and reference Google Business Profile.

Uses batched board queries (items_page + updates per page) to minimize API calls.

Setup:
  MONDAY_API_TOKEN in .env
  python monday_jbu_gbp_mentions.py
  python monday_jbu_gbp_mentions.py --board-id 4125103989   # single board
  python monday_jbu_gbp_mentions.py --board-id 4125103989 --months 3
  python monday_jbu_gbp_mentions.py --resume-from-board 6     # skip first N boards
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from monday_client import DEFAULT_PAGE_LIMIT, graphql_query, list_boards

_PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = _PROJECT_DIR / "monday_jbu_gbp_mentions.json"
DEFAULT_PROGRESS = _PROJECT_DIR / "monday_jbu_gbp_scan_progress.json"

JBU_MENTION_ID = "32344872"
JBU_EMAIL = "jbuenviaje@fivejourneys.com"
MONDAY_BASE = "https://fivejourneys-team.monday.com"

GBP_PATTERNS = [
    r"google\s+business\s+profile",
    r"google\s+business\s+profiles",
    r"google\s+business",
    r"\bgbp\b",
    r"google\s+my\s+business",
    r"gmb\s+profile",
    r"business\s+profile\s+on\s+google",
]
GBP_RE = re.compile("|".join(f"({p})" for p in GBP_PATTERNS), re.I)
MENTION_RE = re.compile(
    rf'data-mention-id="{JBU_MENTION_ID}"|@jbuenviaje|@jerahmay\s+buenviaje|users/{JBU_MENTION_ID}',
    re.I,
)

JE_NEW_TODO_BOARD_ID = "4125103989"

BOARD_UPDATES_QUERY = """
query ($board_ids: [ID!], $limit: Int!, $page: Int!) {
  boards(ids: $board_ids) {
    id
    name
    updates(limit: $limit, page: $page) {
      id
      body
      text_body
      created_at
      creator { id name }
      item { id name }
      replies {
        id
        body
        text_body
        created_at
        creator { id name }
      }
    }
  }
}
"""

ITEMS_WITH_UPDATES_QUERY = """
query ($board_ids: [ID!], $limit: Int!, $cursor: String) {
  boards(ids: $board_ids) {
    id
    name
    items_page(limit: $limit, cursor: $cursor) {
      cursor
      items {
        id
        name
        updates(limit: 100) {
          id
          body
          text_body
          created_at
          creator { id name }
          replies {
            id
            body
            text_body
            created_at
            creator { id name }
          }
        }
      }
    }
  }
}
"""


def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def has_jbu_mention(body: str, text_body: str) -> bool:
    return bool(MENTION_RE.search((body or "") + "\n" + (text_body or "")))


def has_gbp_reference(body: str, text_body: str, item_name: str = "") -> bool:
    combined = strip_html(body or "") + " " + (text_body or "") + " " + (item_name or "")
    return bool(GBP_RE.search(combined))


def item_url(board_id: str, item_id: str) -> str:
    return f"{MONDAY_BASE}/boards/{board_id}/pulses/{item_id}"


def parse_update_datetime(raw: str | None) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw[:19] if "UTC" not in fmt else raw, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def cutoff_from_months(months: int | None) -> datetime | None:
    if months is None or months <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(days=months * 30)


def within_date_window(created_at: str | None, cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    dt = parse_update_datetime(created_at)
    if dt is None:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= cutoff


def scan_update(
    update: dict[str, Any],
    *,
    item: dict[str, Any],
    board_id: str,
    board_name: str,
    is_reply: bool,
    parent_update_id: str | None,
    cutoff: datetime | None = None,
) -> dict[str, Any] | None:
    if not within_date_window(update.get("created_at"), cutoff):
        return None
    body = update.get("body") or ""
    text_body = update.get("text_body") or ""
    item_name = item.get("name") or ""
    if not has_jbu_mention(body, text_body):
        return None
    if not has_gbp_reference(body, text_body, item_name):
        return None
    item_id = str(item.get("id") or "")
    return {
        "board_id": board_id,
        "board_name": board_name,
        "item_id": item_id,
        "item_name": item_name,
        "item_url": item_url(board_id, item_id),
        "update_id": str(update.get("id") or ""),
        "is_reply": is_reply,
        "parent_update_id": parent_update_id,
        "created_at": update.get("created_at"),
        "creator": (update.get("creator") or {}).get("name"),
        "comment_text": (text_body or strip_html(body))[:4000],
    }


def fetch_board_items_with_updates(
    board_id: str,
    *,
    page_limit: int = DEFAULT_PAGE_LIMIT,
    sleep_s: float = 0.35,
) -> tuple[str, list[dict[str, Any]]]:
    """Return (board_name, items with updates)."""
    board_name = f"Board {board_id}"
    all_items: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        variables: dict[str, Any] = {
            "board_ids": [str(board_id)],
            "limit": page_limit,
        }
        if cursor:
            variables["cursor"] = cursor

        data = graphql_query(ITEMS_WITH_UPDATES_QUERY, variables)
        boards = data.get("boards") or []
        if not boards:
            break

        board = boards[0]
        board_name = board.get("name") or board_name
        page = board.get("items_page") or {}
        batch = page.get("items") or []
        all_items.extend(batch)
        cursor = page.get("cursor")
        if not cursor or not batch:
            break
        if sleep_s:
            time.sleep(sleep_s)

    return board_name, all_items


def fetch_board_updates_pages(
    board_id: str,
    *,
    page_limit: int = 100,
    max_pages: int = 500,
    sleep_s: float = 0.35,
    cutoff: datetime | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Paginate board-level updates (item comments). Stops early when past cutoff."""
    board_name = f"Board {board_id}"
    all_updates: list[dict[str, Any]] = []

    for page in range(1, max_pages + 1):
        data = graphql_query(
            BOARD_UPDATES_QUERY,
            {"board_ids": [str(board_id)], "limit": page_limit, "page": page},
        )
        boards = data.get("boards") or []
        if not boards:
            break
        board = boards[0]
        board_name = board.get("name") or board_name
        batch = board.get("updates") or []
        if not batch:
            break

        if cutoff is not None:
            in_window = [
                u
                for u in batch
                if isinstance(u, dict) and within_date_window(u.get("created_at"), cutoff)
            ]
            all_updates.extend(in_window)
            oldest = min(
                (parse_update_datetime(u.get("created_at")) for u in batch if isinstance(u, dict)),
                default=None,
                key=lambda d: d or datetime.min.replace(tzinfo=timezone.utc),
            )
            if oldest and oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=timezone.utc)
            if oldest and oldest < cutoff:
                break
        else:
            all_updates.extend(batch)

        if len(batch) < page_limit:
            break
        if sleep_s:
            time.sleep(sleep_s)

    return board_name, all_updates


def scan_board(
    board_id: str,
    board_name: str | None = None,
    *,
    cutoff: datetime | None = None,
    use_board_updates: bool = True,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []

    if use_board_updates:
        resolved_name, updates = fetch_board_updates_pages(board_id, cutoff=cutoff)
        if board_name:
            resolved_name = board_name
        for upd in updates:
            if not isinstance(upd, dict):
                continue
            item = upd.get("item") or {}
            if not item.get("id"):
                continue
            row = scan_update(
                upd,
                item=item,
                board_id=str(board_id),
                board_name=resolved_name,
                is_reply=False,
                parent_update_id=None,
                cutoff=cutoff,
            )
            if row:
                matches.append(row)
            for rep in upd.get("replies") or []:
                if not isinstance(rep, dict):
                    continue
                row = scan_update(
                    rep,
                    item=item,
                    board_id=str(board_id),
                    board_name=resolved_name,
                    is_reply=True,
                    parent_update_id=str(upd.get("id") or ""),
                    cutoff=cutoff,
                )
                if row:
                    matches.append(row)
        return matches

    resolved_name, items = fetch_board_items_with_updates(board_id)
    if board_name:
        resolved_name = board_name

    for item in items:
        if not isinstance(item, dict):
            continue
        for upd in item.get("updates") or []:
            if not isinstance(upd, dict):
                continue
            row = scan_update(
                upd,
                item=item,
                board_id=str(board_id),
                board_name=resolved_name,
                is_reply=False,
                parent_update_id=None,
                cutoff=cutoff,
            )
            if row:
                matches.append(row)
            for rep in upd.get("replies") or []:
                if not isinstance(rep, dict):
                    continue
                row = scan_update(
                    rep,
                    item=item,
                    board_id=str(board_id),
                    board_name=resolved_name,
                    is_reply=True,
                    parent_update_id=str(upd.get("id") or ""),
                    cutoff=cutoff,
                )
                if row:
                    matches.append(row)
    return matches


def load_progress() -> dict[str, Any]:
    if DEFAULT_PROGRESS.exists():
        return json.loads(DEFAULT_PROGRESS.read_text())
    return {"completed_board_ids": [], "matches": []}


def save_progress(progress: dict[str, Any]) -> None:
    DEFAULT_PROGRESS.write_text(json.dumps(progress, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board-id", help="Scan a single board ID only")
    parser.add_argument(
        "--resume-from-board",
        type=int,
        default=0,
        help="Skip the first N boards (1-based index in active board list)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="JSON output path",
    )
    parser.add_argument("--no-sleep", action="store_true", help="Disable delay between pages")
    parser.add_argument(
        "--months",
        type=int,
        default=None,
        help="Only include comments from the last N months (approx. 30 days each)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore saved progress and start with an empty match list",
    )
    args = parser.parse_args()

    sleep_s = 0.0 if args.no_sleep else 0.35
    cutoff = cutoff_from_months(args.months)

    if args.board_id:
        boards = [{"id": args.board_id, "name": None}]
    else:
        all_boards = list_boards(limit=100)
        boards = [
            b
            for b in all_boards
            if not (b.get("name") or "").startswith("Subitems of")
            and (b.get("state") or "").casefold() == "active"
        ]

    skip = max(0, args.resume_from_board - 1)
    if skip:
        boards = boards[skip:]

    progress = load_progress()
    if args.fresh or args.board_id:
        all_matches = []
        completed: set[str] = set()
    else:
        all_matches = list(progress.get("matches") or [])
        completed = set(str(x) for x in progress.get("completed_board_ids") or [])

    print(f"Scanning {len(boards)} board(s) for @jbuenviaje + Google Business Profile mentions…")
    print(f"Mention user: Jerahmay Buenviaje ({JBU_EMAIL}, id {JBU_MENTION_ID})")
    if cutoff:
        print(
            f"Date window: comments since "
            f"{cutoff.date().isoformat()} (last {args.months} month(s))\n"
        )
    else:
        print()

    for i, board in enumerate(boards, start=1 + skip):
        bid = str(board["id"])
        bname = board.get("name") or bid
        if bid in completed and not args.board_id:
            print(f"[{i}] SKIP (done) {bname}")
            continue

        print(f"[{i}] {bname} ({bid})…", flush=True)
        try:
            matches = scan_board(bid, bname, cutoff=cutoff)
            all_matches.extend(matches)
            completed.add(bid)
            progress["completed_board_ids"] = sorted(completed)
            progress["matches"] = all_matches
            save_progress(progress)
            print(f"    → {len(matches)} match(es) on this board", flush=True)
        except RuntimeError as exc:
            if "DAILY_LIMIT_EXCEEDED" in str(exc) or "429" in str(exc):
                print(f"\nAPI daily limit reached. Progress saved to {DEFAULT_PROGRESS}")
                print(f"Re-run tomorrow: python monday_jbu_gbp_mentions.py --resume-from-board {i}")
                break
            print(f"    ERROR: {exc}", flush=True)
            return 1

    unique: dict[str, dict[str, Any]] = {}
    for m in all_matches:
        key = f"{m.get('update_id')}:{m.get('item_id')}"
        unique[key] = m
    final = sorted(unique.values(), key=lambda x: x.get("created_at") or "", reverse=True)

    args.output.write_text(json.dumps(final, indent=2))
    print(f"\n=== {len(final)} matching comment(s) ===\n")
    for m in final:
        print(f"Board: {m['board_name']}")
        print(f"Task:  {m['item_name']}")
        print(f"Date:  {m.get('created_at')} | By: {m.get('creator')} | Reply: {m.get('is_reply')}")
        print(f"URL:   {m['item_url']}")
        print(f"Comment: {(m.get('comment_text') or '')[:500]}")
        print("-" * 60)

    print(f"\nSaved: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
