"""
Monday Marketing Ops — team task view by workflow stage.

Five panels (Je, Sam, Voltaire, Amanda, We Have SEO), each with:
  1. Requested
  2. Working on
  3. Reviewed & Approved

Uses one board lookup + one items fetch for all configured boards.

    python "Op Reports/monday_ops_view.py"
    python "Op Reports/monday_ops_view.py" --serve --port 8855 --open

Writes: Op Reports/outputs/monday_ops_view.html
"""

from __future__ import annotations

import argparse
import html
import os
import sys
import webbrowser
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from _bootstrap import OP_REPORTS_DIR, PROJECT_ROOT, setup

setup()

_MWR_DIR = PROJECT_ROOT / "MWR"
if str(_MWR_DIR) not in sys.path:
    sys.path.insert(0, str(_MWR_DIR))

OUTPUT_DIR = OP_REPORTS_DIR / "outputs"

SECTION_REQUESTED = "Requested"
SECTION_WORKING = "Working on"
SECTION_APPROVED = "Reviewed & Approved"

WORKING_BUCKETS = ("Working On It", "In Review")
APPROVED_BUCKETS = ("Ready for Publishing",)

# person label, Monday board name, optional staff_key for Bruno approval comments
OPS_PANELS: tuple[dict[str, str | None], ...] = (
    {"person": "Je", "board": "Je New To-Do List", "staff_key": "je"},
    {"person": "Sam", "board": "Sam New To-Do List", "staff_key": "sam"},
    {"person": "Voltaire", "board": "Voltaire To-Do List", "staff_key": "voltaire"},
    {
        "person": "Amanda",
        "board": os.getenv("MONDAY_AMANDA_BOARD_NAME", "Amanda New To-Do List"),
        "staff_key": None,
    },
    {"person": "We Have SEO", "board": "We Have SEO", "staff_key": None},
)


@dataclass
class OpsSubsection:
    title: str
    tasks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class PersonOpsPanel:
    person: str
    board_name: str
    subsections: list[OpsSubsection]
    board_missing: bool = False


@dataclass
class MondayOpsView:
    generated_at: datetime
    panels: list[PersonOpsPanel]
    errors: list[str] = field(default_factory=list)
    truncated_boards: list[str] = field(default_factory=list)
    rate_limited: bool = False


def _empty_subsections(*, notes: list[str] | None = None) -> list[OpsSubsection]:
    note = notes or []
    return [
        OpsSubsection(SECTION_REQUESTED),
        OpsSubsection(SECTION_WORKING),
        OpsSubsection(SECTION_APPROVED, notes=note),
    ]


def _task_lines_for_buckets(df, buckets: tuple[str, ...]) -> list[str]:
    from war_room_data import canonical_team_ops_bucket, format_team_status_label

    if df is None or df.empty:
        return []

    lines: list[str] = []
    seen: set[str] = set()
    for _, row in df.iterrows():
        raw_status = str(row.get("status") or "")
        bucket = canonical_team_ops_bucket(raw_status)
        if not bucket or bucket not in buckets:
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        status_label = format_team_status_label(raw_status)
        line = name if status_label.casefold() == bucket.casefold() else f"{name} ({status_label})"
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return sorted(lines, key=str.casefold)


def _recent_review_approvals(
    *,
    board_id: str,
    staff_key: str,
    days: int = 21,
) -> tuple[list[str], str | None]:
    try:
        from activity_summary_report import (
            APPROVAL_COMMENT_RE,
            BRUNO_CREATOR_RE,
            _approval_date_in_range,
            _resolve_staff_tag,
            _summarize_approval_items,
        )
        from monday_jbu_gbp_mentions import (
            fetch_board_updates_pages,
            parse_update_datetime,
            strip_html,
        )
    except ImportError as exc:
        return [], str(exc)

    end = date.today()
    start = end - timedelta(days=days)
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    rows: list[tuple[date, str]] = []
    seen: set[str] = set()

    try:
        _, updates = fetch_board_updates_pages(
            board_id,
            page_limit=100,
            max_pages=30,
            sleep_s=0.2,
            cutoff=start_dt,
        )
    except Exception as exc:
        return [], f"Monday updates ({staff_key}): {exc}"

    for upd in updates:
        if not isinstance(upd, dict):
            continue
        item = upd.get("item") or {}
        item_name = str(item.get("name") or "").strip()
        if not item_name:
            continue
        for comment in [upd, *(upd.get("replies") or [])]:
            if not isinstance(comment, dict):
                continue
            creator = str((comment.get("creator") or {}).get("name") or "")
            if not BRUNO_CREATOR_RE.search(creator):
                continue
            created = parse_update_datetime(comment.get("created_at"))
            if not _approval_date_in_range(created, start=start, end=end):
                continue
            body = str(comment.get("body") or "")
            text = strip_html(body + "\n" + str(comment.get("text_body") or ""))
            if not APPROVAL_COMMENT_RE.search(text):
                continue
            resolved = _resolve_staff_tag(board_key=staff_key, body=body, text=text)
            if resolved != staff_key:
                continue
            dedupe_key = item_name.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            rows.append((created.date(), item_name))

    if not rows:
        return [], None
    return _summarize_approval_items(rows), None


def _merge_approved_tasks(
    status_tasks: list[str],
    comment_tasks: list[str],
) -> list[str]:
    approved = list(status_tasks)
    seen = {t.casefold() for t in approved}
    for line in comment_tasks:
        short = line.split(" (", 1)[0].strip()
        if short.casefold() in seen:
            continue
        seen.add(short.casefold())
        approved.append(line)
    return approved


def _build_panel_subsections(
    df,
    *,
    board_id: str | None,
    staff_key: str | None,
    rate_limited: bool,
    errors: list[str],
) -> list[OpsSubsection]:
    requested = _task_lines_for_buckets(df, (SECTION_REQUESTED,))
    working = _task_lines_for_buckets(df, WORKING_BUCKETS)
    approved_status = _task_lines_for_buckets(df, APPROVED_BUCKETS)
    approval_notes: list[str] = []

    recent_approvals: list[str] = []
    if board_id and staff_key and not rate_limited:
        recent_approvals, approval_err = _recent_review_approvals(
            board_id=board_id,
            staff_key=staff_key,
        )
        if approval_err:
            errors.append(approval_err)

    approved_tasks = _merge_approved_tasks(approved_status, recent_approvals)

    if recent_approvals and approved_status:
        approval_notes.append(
            "Ready for Publishing tasks plus Bruno review approvals (last 21 days)."
        )
    elif recent_approvals and not approved_status:
        approval_notes.append(
            f"{len(recent_approvals)} recent review approval(s) from Monday comments."
        )

    return [
        OpsSubsection(SECTION_REQUESTED, requested),
        OpsSubsection(SECTION_WORKING, working),
        OpsSubsection(SECTION_APPROVED, approved_tasks, notes=approval_notes),
    ]


def load_monday_ops_view() -> MondayOpsView:
    errors: list[str] = []
    truncated_boards: list[str] = []
    rate_limited = False

    board_names = [str(p["board"]) for p in OPS_PANELS]
    board_map: dict[str, str] = {}
    missing_boards: list[str] = []

    try:
        from monday_client import fetch_items_from_boards, resolve_board_ids_by_names

        board_map, missing_boards = resolve_board_ids_by_names(board_names)
    except RuntimeError as exc:
        msg = str(exc)
        errors.append(f"Monday.com: {exc}")
        rate_limited = "DAILY_LIMIT" in msg

    if missing_boards:
        errors.extend(f"Board not found: {name}" for name in missing_boards)

    all_df = None
    if board_map and not rate_limited:
        try:
            from monday_client import fetch_items_from_boards
            from war_room_data import _open_team_ops_tasks

            board_ids = list(board_map.values())
            name_by_id = {bid: name for name, bid in board_map.items()}
            raw_df, truncated_map = fetch_items_from_boards(
                board_ids,
                board_names=name_by_id,
            )
            for bid, hit_cap in truncated_map.items():
                if hit_cap:
                    truncated_boards.append(name_by_id.get(str(bid), str(bid)))
            all_df = _open_team_ops_tasks(raw_df) if not raw_df.empty else raw_df
        except RuntimeError as exc:
            msg = str(exc)
            errors.append(f"Monday.com items: {exc}")
            rate_limited = rate_limited or "DAILY_LIMIT" in msg

    global_note: list[str] = []
    if rate_limited:
        global_note.append(
            "Monday.com daily API limit reached — all lists show placeholders until the quota resets."
        )

    panels: list[PersonOpsPanel] = []
    for spec in OPS_PANELS:
        person = str(spec["person"])
        board_name = str(spec["board"])
        staff_key = spec.get("staff_key")

        board_id = board_map.get(board_name)
        board_missing = board_name in missing_boards or board_id is None

        panel_df = None
        if all_df is not None and not all_df.empty and board_id:
            panel_df = all_df[all_df["board_id"] == str(board_id)]

        notes = list(global_note) if rate_limited else []
        if board_missing and not rate_limited:
            notes.append(f"Board not resolved: {board_name}")

        subsections = (
            _build_panel_subsections(
                panel_df,
                board_id=board_id,
                staff_key=str(staff_key) if staff_key else None,
                rate_limited=rate_limited,
                errors=errors,
            )
            if board_id and not rate_limited
            else _empty_subsections(notes=notes)
        )

        panels.append(
            PersonOpsPanel(
                person=person,
                board_name=board_name,
                subsections=subsections,
                board_missing=board_missing,
            )
        )

    return MondayOpsView(
        generated_at=datetime.now(),
        panels=panels,
        errors=errors,
        truncated_boards=truncated_boards,
        rate_limited=rate_limited,
    )


def render_monday_ops_html(view: MondayOpsView) -> str:
    panels_html = ""
    for panel in view.panels:
        subsections_html = ""
        for sub in panel.subsections:
            if sub.tasks:
                items = "".join(f"<li>{html.escape(t)}</li>" for t in sub.tasks)
                body = f"<ul>{items}</ul>"
            else:
                body = "<p class='empty'><em>None right now.</em></p>"
            notes = ""
            if sub.notes:
                notes = "".join(
                    f"<p class='note'>{html.escape(n)}</p>" for n in sub.notes
                )
            subsections_html += f"""
            <div class="subsection">
              <h3>{html.escape(sub.title)}</h3>
              <p class="count">{len(sub.tasks)} task{'s' if len(sub.tasks) != 1 else ''}</p>
              {body}
              {notes}
            </div>
            """

        missing_note = ""
        if panel.board_missing and not view.rate_limited:
            missing_note = "<p class='note'>Board name not found on Monday — check configuration.</p>"

        panels_html += f"""
        <article class="person-panel">
          <h2>{html.escape(panel.person)}</h2>
          <p class="board-meta">Monday board: {html.escape(panel.board_name)}</p>
          {missing_note}
          {subsections_html}
        </article>
        """

    errors_html = ""
    if view.errors:
        unique_errors = list(dict.fromkeys(view.errors))
        errors_html = f"<p class='errors'>{html.escape('; '.join(unique_errors[:3]))}</p>"
    trunc_html = ""
    if view.truncated_boards:
        trunc_html = (
            "<p class='note'>Pagination cap on: "
            + html.escape(", ".join(view.truncated_boards))
            + "</p>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Monday Marketing Ops</title>
<style>
body {{ font-family: Arial, sans-serif; font-size: 10pt; margin: 20px; line-height: 1.45; color: #222; max-width: 760px; }}
h1 {{ font-size: 15pt; margin: 0 0 4px 0; }}
.page-meta {{ color: #666; font-size: 9pt; margin-bottom: 24px; }}
.person-panel {{ margin-bottom: 32px; padding-bottom: 20px; border-bottom: 2px solid #d8e8e2; }}
.person-panel:last-child {{ border-bottom: none; }}
h2 {{ font-size: 13pt; margin: 0 0 4px 0; color: #264540; }}
.board-meta {{ font-size: 9pt; color: #888; margin: 0 0 12px 0; }}
.subsection {{ margin-bottom: 16px; padding-left: 4px; }}
h3 {{ font-size: 10.5pt; margin: 0 0 2px 0; color: #5DA68A; }}
.count {{ font-size: 9pt; color: #888; margin: 0 0 6px 0; }}
ul {{ margin: 0; padding-left: 20px; }}
li {{ margin-bottom: 5px; }}
.empty {{ margin: 0; color: #888; font-size: 9.5pt; }}
.note {{ font-size: 9pt; color: #666; margin: 6px 0 0 0; }}
.errors {{ color: #a00; font-size: 9pt; }}
</style>
</head>
<body>
<h1>Monday Marketing Ops</h1>
<p class="page-meta">Generated {view.generated_at.strftime('%Y-%m-%d %H:%M')} · {len(view.panels)} team panels · 3 workflow stages each</p>
{trunc_html}
{errors_html}
{panels_html}
</body>
</html>
"""


def write_monday_ops_view(view: MondayOpsView) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "monday_ops_view.html"
    path.write_text(render_monday_ops_html(view), encoding="utf-8")
    return path


def _serve_directory(directory: Path, port: int) -> None:
    directory = directory.resolve()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving {directory} at http://127.0.0.1:{port}/monday_ops_view.html")
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Monday Marketing Ops team task view")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8855)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()

    print("Loading Monday boards (single batch)…")
    view = load_monday_ops_view()
    path = write_monday_ops_view(view)
    print(f"Wrote {path}")
    for panel in view.panels:
        counts = ", ".join(f"{s.title}: {len(s.tasks)}" for s in panel.subsections)
        print(f"  {panel.person}: {counts}")

    if args.open:
        webbrowser.open(path.resolve().as_uri())

    if args.serve:
        _serve_directory(OUTPUT_DIR, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
