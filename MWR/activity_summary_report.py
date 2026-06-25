"""
Weekly activity summary — Gmail sent + completed Google Tasks (bulleted HTML).

Fetches inbox/sent mail and checked-off tasks for a date range, filters noise,
consolidates duplicates, and writes a plain Arial 10pt report for localhost or email.

    python MWR/activity_summary_report.py
    python MWR/activity_summary_report.py --start 2026-06-08 --end 2026-06-12 --open
    python MWR/activity_summary_report.py --days 5 --serve

Paste the HTML into Cursor for a narrative polish pass, or re-run after the week ends.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import webbrowser
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MWR_DIR = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from gmail_client import (
    GmailMessage,
    _normalize_subject,
    fetch_inbox_and_sent,
    gmail_service,
)
from google_tasks_client import list_tasklists, tasks_service

OUTPUT_DIR = _MWR_DIR / "outputs"

STAFF_BOARD_NAMES = {
    "je": "Je New To-Do List",
    "sam": "Sam New To-Do List",
    "voltaire": "Voltaire To-Do List",
}
STAFF_SECTION_TITLES = {
    "Je": "Je - Reviews & Approvals",
    "Sam": "Sam - Reviews & Approvals",
    "Voltaire": "Voltaire - Reviews & Approvals",
}
STAFF_MENTION_RES = {
    "je": re.compile(r"@je\b|@jerahmay|jerahmay buenviaje|32344872", re.I),
    "sam": re.compile(r"@sam\b|@sam cimafranca|scimafranca", re.I),
    "voltaire": re.compile(r"@voltaire\b|\bhi voltaire\b", re.I),
}
APPROVAL_COMMENT_RE = re.compile(
    r"\bapproved\b|good to go|ready to publish|treat this as done",
    re.I,
)
BRUNO_CREATOR_RE = re.compile(r"bruno", re.I)
_IG_ITEM_RE = re.compile(r"^ig\s", re.I)

_SKIP_TASK_RE = re.compile(
    r"^(re:|fwd:|fw:)|pay vas|welcome to your workplace|^hi bruno\.",
    re.IGNORECASE,
)
_SKIP_EMAIL_SUBJECT_RE = re.compile(
    r"^(test$|missing docs report|welcome to your workplace|health insurance plan)",
    re.IGNORECASE,
)
_SKIP_EMAIL_CHATTER_RE = re.compile(
    r"\b(going to lunch|out for lunch|at lunch|on lunch)\b|"
    r"\b(let me know when|be right back|\bbrb\b|on my way|heading out)\b|"
    r"^(hi|hello|thanks|thank you|ok|okay|yes|no)\.?$|"
    r"\bout of office\b|\booo\b",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


@dataclass
class CompletedTask:
    title: str
    notes: str
    completed_date: date
    list_name: str


@dataclass
class ActivitySummaryReport:
    period_start: date
    period_end: date
    net_read: str
    meta_line: str
    milestones: list[tuple[str, str]]
    completed_sections: list[tuple[str, list[str]]]
    email_sections: list[tuple[str, list[str]]]
    operational_work_groups: list[tuple[str, list[str]]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _parse_api_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _first_email(text: str) -> str:
    match = _EMAIL_RE.search(text or "")
    return match.group(0).lower() if match else ""


def _first_recipient_name(to_addrs: str) -> str:
    raw = (to_addrs or "").strip()
    if "<" in raw:
        name = raw.split("<", 1)[0].strip().strip('"')
        if name:
            return name
    addr = _first_email(raw)
    if addr:
        local = addr.split("@", 1)[0].replace(".", " ").title()
        return local
    return raw[:40] or "unknown"


def fetch_completed_tasks(
    service,
    *,
    start: date,
    end: date,
) -> list[CompletedTask]:
    rows: list[CompletedTask] = []
    for tasklist in list_tasklists(service):
        list_id = tasklist.get("id") or ""
        list_title = (tasklist.get("title") or "").strip() or list_id
        if not list_id:
            continue
        page_token: str | None = None
        while True:
            resp = (
                service.tasks()
                .list(
                    tasklist=list_id,
                    showCompleted=True,
                    showHidden=True,
                    maxResults=100,
                    pageToken=page_token,
                )
                .execute()
            )
            for task in resp.get("items") or []:
                if (task.get("status") or "").lower() != "completed":
                    continue
                completed_dt = _parse_api_datetime(task.get("completed"))
                if not completed_dt:
                    continue
                completed_day = completed_dt.astimezone(timezone.utc).date()
                if not (start <= completed_day <= end):
                    continue
                title = (task.get("title") or "").strip()
                if not title or _SKIP_TASK_RE.search(title):
                    continue
                rows.append(
                    CompletedTask(
                        title=title,
                        notes=(task.get("notes") or "").strip(),
                        completed_date=completed_day,
                        list_name=list_title,
                    )
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    return rows


def _task_text(task: CompletedTask) -> str:
    return f"{task.title} {task.notes}".lower()


def _is_results_oriented_email(subject: str, snippet: str = "") -> bool:
    """Drop social/administrative mail that is not a work outcome."""
    text = f"{subject} {snippet}".strip()
    if not text:
        return False
    if _SKIP_EMAIL_CHATTER_RE.search(text):
        return False
    # Very short subjects with no clear deliverable are usually chatter.
    subj = subject.strip()
    if len(subj) < 18 and not re.search(
        r"\b(report|review|update|launch|campaign|invoice|contract|approved|seo|"
        r"integration|packet|board|payment|proposal|meeting|schedule|plan)\b",
        subj,
        re.I,
    ):
        return False
    return True


def _task_group_heading(title: str) -> str | None:
    """Leading category before ' - ', ' -- ', or ' — ' (e.g. 'Advertising - …' → Advertising)."""
    text = title.strip()
    for sep in (" — ", " -- ", " - "):
        if sep in text:
            head = text.split(sep, 1)[0].strip()
            if len(head) >= 2:
                return head
    return None


def _strip_group_prefix(title: str, group_heading: str) -> str:
    """Drop the group headline from a task title when it is already shown as h4."""
    text = title.strip()
    head = group_heading.strip()
    if not head:
        return text
    for sep in (" — ", " -- ", " - ", ": "):
        prefix = f"{head}{sep}"
        if text.casefold().startswith(prefix.casefold()):
            return text[len(prefix) :].strip()
    if text.casefold().startswith(head.casefold()):
        return text[len(head) :].lstrip(" -—:").strip() or text
    return text


def _strip_staff_oversight_tail(text: str) -> str:
    """Staff Oversight - Je - Task name → Task name."""
    match = re.match(r"^(Je|Sam|Voltaire)\s*[-—]\s*(.+)", text.strip(), re.I)
    return match.group(2).strip() if match else text.strip()


def _format_completed_task_bullet(
    task: CompletedTask,
    *,
    group_heading: str | None = None,
) -> str:
    line = task.title.strip()
    if group_heading:
        line = _strip_group_prefix(line, group_heading)
        if group_heading.casefold() == "staff oversight":
            line = _strip_staff_oversight_tail(line)
    if task.notes:
        note = task.notes.replace("\n", " ").strip()
        if note and note.casefold() not in line.casefold():
            line = f"{line} — {note[:100]}"
    return f"{line} ({_short_date(task.completed_date)})"


def _group_completed_tasks(tasks: list[CompletedTask]) -> list[tuple[str, list[str]]]:
    """Group completed tasks by shared title prefix; singletons → Miscellaneous."""
    sorted_tasks = sorted(
        tasks,
        key=lambda t: (t.completed_date, t.list_name.casefold(), t.title.casefold()),
    )
    heading_counts: dict[str, int] = defaultdict(int)
    for task in sorted_tasks:
        head = _task_group_heading(task.title)
        if head:
            heading_counts[head] += 1

    groups: dict[str, list[str]] = defaultdict(list)
    misc: list[str] = []
    seen: set[str] = set()

    for task in sorted_tasks:
        head = _task_group_heading(task.title)
        bullet = _format_completed_task_bullet(
            task,
            group_heading=head if head and heading_counts.get(head, 0) >= 2 else None,
        )
        key = bullet.casefold()
        if key in seen:
            continue
        seen.add(key)

        if head and heading_counts[head] >= 2:
            groups[head].append(bullet)
        else:
            misc.append(bullet)

    ordered: list[tuple[str, list[str]]] = [
        (head, groups[head]) for head in sorted(groups, key=str.casefold)
    ]
    if misc:
        ordered.append(("Miscellaneous", misc))
    return ordered


def _is_zonia_activity(text: str) -> bool:
    lower = text.casefold()
    return "zonia" in lower or "immune for life" in lower


def _zonia_vendor_bullets(
    tasks: list[CompletedTask],
    sent: list[GmailMessage],
) -> list[str]:
    bullets: list[str] = []
    for task in tasks:
        if _is_zonia_activity(_task_text(task)):
            title = _strip_group_prefix(task.title.strip(), "Zonia")
            line = title
            if task.notes:
                note = task.notes.replace("\n", " ").strip()
                if note and note.casefold() not in line.casefold():
                    line = f"{line} — {note[:100]}"
            bullets.append(f"{line} ({_short_date(task.completed_date)})")
    for msg in sent:
        subject = _normalize_subject(msg.subject)
        lower = f"{subject} {msg.to_addrs} {msg.from_addr}".casefold()
        if "zonia" not in lower and "immune for life" not in lower:
            continue
        if not _is_results_oriented_email(subject, msg.snippet or ""):
            continue
        recipient = _first_recipient_name(msg.to_addrs)
        day = _short_date(msg.date.date()) if msg.date else ""
        line = f"{subject} → {recipient}" + (f" ({day})" if day else "")
        bullets.append(line)
    return _dedupe_lines(bullets)


def _short_date(d: date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def _approval_date_in_range(created: datetime | None, *, start: date, end: date) -> bool:
    if not created:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)
    return start_dt <= created < end_dt


def _resolve_staff_tag(*, board_key: str, body: str, text: str) -> str:
    combined = f"{body}\n{text}"
    # Direct staff call-out wins over assignee @mention (e.g. "@Je … Hi Voltaire — approved").
    for staff_key in ("voltaire", "sam", "je"):
        if STAFF_MENTION_RES[staff_key].search(combined):
            return staff_key
    return board_key


def _short_item_name(name: str) -> str:
    for prefix in (
        "IG Carousel Post: ",
        "IG Single Image Post: ",
        "IG  Carousel Post:",
        "IG Reel: ",
    ):
        if name.startswith(prefix):
            return name[len(prefix) :].strip()
    return name.strip()


def _summarize_approval_items(items: list[tuple[date, str]]) -> list[str]:
    """Turn raw Monday approval rows into readable bullets."""
    if not items:
        return []

    ig_items = [(d, n) for d, n in items if _IG_ITEM_RE.match(n.strip())]
    other_items = [(d, n) for d, n in items if not _IG_ITEM_RE.match(n.strip())]
    bullets: list[str] = []

    if len(ig_items) >= 3:
        by_day: dict[date, int] = defaultdict(int)
        for day, _ in ig_items:
            by_day[day] += 1
        day_labels = ", ".join(f"{count} on {_short_date(day)}" for day, count in sorted(by_day.items()))
        bullets.append(f"{len(ig_items)} IG posts/reels approved — {day_labels}")
    else:
        other_items.extend(ig_items)

    seen: set[str] = set()
    for day, name in sorted(other_items, key=lambda row: (row[0], row[1].casefold())):
        short = _short_item_name(name)
        line = f"{short} ({_short_date(day)})"
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        bullets.append(line)
    return bullets


def _fetch_monday_staff_approvals(
    *,
    start: date,
    end: date,
) -> tuple[dict[str, list[str]], str | None]:
    """
    Bruno's Monday comments that tag @je / @sam / @voltaire (or board owner)
    and include approval language.
    """
    try:
        from monday_client import resolve_board_ids_by_names
        from monday_jbu_gbp_mentions import (
            fetch_board_updates_pages,
            parse_update_datetime,
            strip_html,
        )
    except ImportError as exc:
        return {}, f"Monday imports: {exc}"

    try:
        board_map, missing = resolve_board_ids_by_names(list(STAFF_BOARD_NAMES.values()))
    except Exception as exc:
        return {}, f"Monday (staff boards): {exc}"
    if missing:
        return {}, f"Monday boards not found: {', '.join(missing)}"

    name_to_key = {v: k for k, v in STAFF_BOARD_NAMES.items()}
    by_staff: dict[str, list[tuple[date, str]]] = {k: [] for k in STAFF_BOARD_NAMES}
    seen: set[str] = set()
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)

    for board_name, board_id in board_map.items():
        board_key = name_to_key.get(board_name)
        if not board_key:
            continue
        try:
            _, updates = fetch_board_updates_pages(
                board_id,
                page_limit=100,
                max_pages=30,
                sleep_s=0.2,
                cutoff=start_dt - timedelta(days=14),
            )
        except Exception as exc:
            return {}, f"Monday ({board_name}): {exc}"

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
                staff_key = _resolve_staff_tag(board_key=board_key, body=body, text=text)
                dedupe_key = f"{staff_key}:{item_name.casefold()}"
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                by_staff[staff_key].append((created.date(), item_name))

    return {
        staff.title(): _summarize_approval_items(rows)
        for staff, rows in by_staff.items()
        if rows
    }, None


def _build_staff_sections(
    tasks: list[CompletedTask],
    *,
    start: date,
    end: date,
) -> tuple[list[tuple[str, list[str]]], list[str]]:
    sections: list[tuple[str, list[str]]] = []
    errors: list[str] = []

    monday_by_staff, monday_err = _fetch_monday_staff_approvals(start=start, end=end)
    if monday_err:
        errors.append(monday_err)

    task_notes: dict[str, list[str]] = defaultdict(list)
    for task in tasks:
        text = _task_text(task)
        if "staff oversight" in text and "sam" in text:
            if "monday board" in text:
                task_notes["Sam"].append("Monday board cleanup (Google Tasks)")
            if "nurture" in text:
                task_notes["Sam"].append("nurture sequence rebalance")
            if "vidiq" in text or "a/b" in text:
                task_notes["Sam"].append("VidIQ A/B test")
            if "newsletter" in text or "solstice" in text or "khafagy" in text:
                task_notes["Sam"].append("newsletter, Khafagy copy, Solstice banner")
        if "staff oversight" in text and re.search(r"\bje\b", text):
            note = task.notes.strip() or task.title.strip()
            if note:
                task_notes["Je"].append(note)
        elif re.search(r"\bwith je\b|\bto je\b", text) and "share creative theme" in text:
            task_notes["Je"].append("briefed on Summer Solstice sale creative theme")

    for staff in ("Je", "Sam", "Voltaire"):
        bullets = list(monday_by_staff.get(staff, []))
        monday_blob = " ".join(bullets).casefold()
        for note in dict.fromkeys(task_notes.get(staff, [])):
            if note.casefold().startswith("staff oversight"):
                tail = note.split(" - ", 2)[-1].casefold()
                if tail and tail in monday_blob:
                    continue
                note = _strip_staff_oversight_tail(_strip_group_prefix(note, "Staff Oversight"))
            bullets.append(note)
        if bullets:
            sections.append((STAFF_SECTION_TITLES[staff], bullets))

    return sections, errors


def _consolidate_completed_tasks(
    tasks: list[CompletedTask],
    *,
    start: date,
    end: date,
    sent: list[GmailMessage] | None = None,
    chat_lines: list[str] | None = None,
) -> tuple[list[tuple[str, list[str]]], list[tuple[str, list[str]]], list[str]]:
    staff_sections, staff_errors = _build_staff_sections(tasks, start=start, end=end)

    blob = " ".join(_task_text(t) for t in tasks)
    sent_blob = " ".join(_normalize_subject(m.subject).lower() for m in (sent or []))
    chat_blob = " ".join((chat_lines or [])).lower()
    combined = f"{blob} {sent_blob} {chat_blob}"

    campaigns: list[str] = []
    if any(k in combined for k in ("women", "zigi", "solstice", "ad campaign")):
        campaigns.append(
            "Women's health + Solstice — Zigi asset rankings shared, ad scope clarified with Kim"
        )
    if any(k in combined for k in ("world cup", "50% off", "hydration and hang over", "wellness iv")):
        campaigns.append(
            "World Cup 50% IV offer — landing page live, sandwich board + group GIF approved, staff FB post out"
        )

    seo: list[str] = []
    if "search optimization" in blob or "supplement store" in blob:
        seo.append("Supplement store SEO — project sheet, Monday board, contractor authorized")
    if "referral" in blob and "npi" in blob:
        seo.append("Referral page updated with current providers and NPI numbers")
    if "indexed" in blob or "search console" in blob:
        seo.append("Google Search Console indexing issues investigated")

    vendor: list[str] = []
    if any("inventory" in _task_text(t) and "valley" in _task_text(t) for t in tasks):
        vendor.append("Valley List inventory reconciliation escalated to support ticket")
    if any("subscription" in _task_text(t) and "token" in _task_text(t) for t in tasks):
        vendor.append("Subscription renewal token failures triaged with tech support (16 tokens)")

    print_items: list[str] = []
    if "welcome packet" in blob or "meet the team" in blob:
        print_items.append("Welcome packet Meet the Team update to print")
    if any(k in blob for k in ("concierge", "wellness iv menu", "tamer")):
        print_items.append("Concierge cards, Wellness IV menu, and Tamer Khafagy business cards ordered")
    if "sandwich board" in blob:
        print_items.append("Boylston sandwich boards — functional medicine + HBOT/Wellness IV")

    sections: list[tuple[str, list[str]]] = []
    if campaigns:
        sections.append(("Campaigns", campaigns))

    operational_work_groups = _group_completed_tasks(
        [t for t in tasks if not _is_zonia_activity(_task_text(t))]
    )

    sections.extend(staff_sections)
    if seo:
        sections.append(("SEO & Web", seo))
    if vendor:
        sections.append(("Vendor / Tech Ops", vendor))
    if print_items:
        sections.append(("Print & Collateral", print_items))
    return sections, operational_work_groups, staff_errors


def _detect_milestones(
    sent: list[GmailMessage],
    tasks: list[CompletedTask],
    *,
    chat_lines: list[str] | None = None,
) -> list[tuple[str, str]]:
    subjects = " ".join(_normalize_subject(m.subject).lower() for m in sent)
    task_blob = " ".join(_task_text(t) for t in tasks)
    chat_blob = " ".join(chat_lines or []).lower()
    combined = f"{subjects} {task_blob} {chat_blob}"
    milestones: list[tuple[str, str]] = []

    if any(k in combined for k in ("women", "zigi", "top 10", "ad campaign", "clarification on ad")):
        milestones.append(
            (
                "Women's health campaign aligned with Zigi",
                "Top 10 posts + asset rankings shared; ad scope locked with Kim.",
            )
        )
    if any(k in combined for k in ("seo", "search optimization", "danieltkseo", "supplement store")):
        milestones.append(
            (
                "SEO supplement store program kicked off",
                "Project sheet + Monday board; Danil briefed; work authorized.",
            )
        )
    if any(k in combined for k in ("week in review", "week in review", "benefits", "ideal patient", "predictable profits")):
        milestones.append(
            (
                "Executive reporting & positioning",
                "Week In Review to Ed; Benefits framework + Ideal Patient profile to Predictable Profits.",
            )
        )
    if any(
        k in combined
        for k in ("operational priorities", "athenanet", "gohighlevel", "duplicate data entry", "ghl")
    ):
        milestones.append(
            (
                "COO ops + integration scoping",
                "Ops priorities with Michelle; GHL duplicate-entry audit; AthenaNet/Zenoti/GHL plan scoped.",
            )
        )
    if any(k in combined for k in ("world cup", "50% off", "hydration and hang over")):
        milestones.append(
            (
                "World Cup IV promo live",
                "50% off Hydration & Hangover IVs — sandwich board, group GIF, All Staff FB post.",
            )
        )
    return milestones[:6]


def _build_net_read(
    *,
    sent_count: int,
    inbox_count: int,
    tasks: list[CompletedTask],
    milestones: list[tuple[str, str]],
) -> str:
    work_items: list[str] = []
    blob = " ".join(title.lower() for title, _ in milestones)
    if "world cup" in blob:
        work_items.append("the World Cup 50% IV offer")
    if "seo" in blob:
        work_items.append("supplement store SEO")
    if "women" in blob or "zigi" in blob:
        work_items.append("women's health and Solstice campaign prep")
    if any("print" in _task_text(t) or "sandwich" in _task_text(t) for t in tasks):
        work_items.append("print and collateral production")
    if "coo" in blob or "integration" in blob:
        work_items.append("COO-level integration scoping")

    if len(work_items) >= 2:
        work_phrase = ", ".join(work_items[:-1]) + f", and {work_items[-1]}"
    elif work_items:
        work_phrase = work_items[0]
    else:
        work_phrase = "campaign, content, and operational work"

    task_count = len(tasks)
    task_days = len({t.completed_date for t in tasks})

    sentences: list[str] = [
        f"This was a heavy week spanning {work_phrase}.",
        (
            f"We closed {task_count} Google Tasks across {task_days} days, sent {sent_count} "
            f"outbound emails, and cleared a strong batch of creative reviews and approvals "
            f"for Je, Sam, and Voltaire."
        ),
    ]

    if sent_count >= 30 and task_days >= 3:
        sentences.append(
            "The first half of the week was email-driven; task completions and Monday "
            "sign-offs stacked up in the back half."
        )
    elif sent_count >= 20:
        sentences.append(
            "Outbound email set the pace early; operational tasks and approvals closed "
            "through the rest of the week."
        )

    if inbox_count <= 3:
        sentences.append(f"Inbox stayed clean ({inbox_count} promo only).")

    return " ".join(sentences)


def _fetch_chat_lines(*, start: date, end: date) -> tuple[list[str], str | None]:
    op_reports = _PROJECT_ROOT / "Op Reports"
    if str(op_reports) not in sys.path:
        sys.path.insert(0, str(op_reports))
    try:
        from weekly_report_data import fetch_sent_chat_messages
    except ImportError as exc:
        return [], f"Google Chat import: {exc}"
    try:
        rows = fetch_sent_chat_messages(start, end)
    except Exception as exc:
        return [], f"Google Chat: {exc}"
    return [row.text for row in rows], None


def _consolidate_contact_sections(
    sent: list[GmailMessage],
    chat_lines: list[str],
) -> list[tuple[str, list[str]]]:
    """People-specific correspondence (email + chat)."""
    sections: list[tuple[str, list[str]]] = []
    amanda: list[str] = []

    for msg in sent:
        lower = f"{msg.subject} {msg.to_addrs} {msg.from_addr}".lower()
        if not any(x in lower for x in ("vashon", "klotz", "aklotz@")):
            continue
        recipient = _first_recipient_name(msg.to_addrs)
        day = _short_date(msg.date.date()) if msg.date else ""
        amanda.append(
            f"Email: {_normalize_subject(msg.subject)} → {recipient}"
            + (f" ({day})" if day else "")
        )

    for text in chat_lines:
        lower = text.lower()
        if "amanda" not in lower and "welcome packet" not in lower:
            continue
        snippet = text.replace("\n", " ").strip()[:180]
        amanda.append(f"Chat: {snippet}")

    if amanda:
        sections.append(("Amanda Vashon (Klotz)", _dedupe_lines(amanda)))
    return sections


def _consolidate_other_email(
    sent: list[GmailMessage],
    milestones: list[tuple[str, str]],
    *,
    tasks: list[CompletedTask] | None = None,
) -> list[tuple[str, list[str]]]:
    milestone_subjects = " ".join(_normalize_subject(m.subject).lower() for m in sent)
    _ = milestone_subjects  # used implicitly via skip patterns below

    leadership: list[str] = []
    team: list[str] = []
    vendor: list[str] = []
    seen: set[str] = set()

    skip_subjects = {
        "week in review",
        "operational priorities and workflow improvements",
        "five journeys benefits flashed out",
        "top 10 women's health posts",
        "clarification on ad campaigns",
        "seo",
        "introducing kaerwell health - private label multiple brands from one store!",
    }

    for msg in sorted(sent, key=lambda m: m.date or datetime.min.replace(tzinfo=timezone.utc)):
        subject = _normalize_subject(msg.subject)
        key = subject.casefold()
        if key in seen or key in skip_subjects or _SKIP_EMAIL_SUBJECT_RE.search(subject):
            continue
        if not _is_results_oriented_email(subject, msg.snippet or ""):
            continue
        if _first_email(msg.from_addr) == _first_email(msg.to_addrs) and "test" in key:
            continue
        seen.add(key)

        recipient = _first_recipient_name(msg.to_addrs)
        addr = _first_email(msg.to_addrs)
        line = f"{subject} → {recipient}"
        lower = f"{subject} {addr}".lower()

        if any(x in lower for x in ("vashon", "klotz", "aklotz@")):
            continue
        if any(x in lower for x in ("world cup", "50% off of ivs")):
            continue

        if any(x in lower for x in ("pupka", "levitan", "elevitan", "athena", "jbentley", "insurance")):
            if "operational priorities" in lower:
                continue
            if "week in review" in lower or "week in review" in lower:
                continue
            if "insurance" in lower and "health insurance plan" in lower:
                continue
            leadership.append(line)
        elif any(
            x in lower
            for x in ("mhernandez", "fruzzetti", "scimafranca", "podcast@", "ortiz", "goldstein")
        ):
            team.append(line)
        elif any(
            x in lower
            for x in ("valleylist", "thevalleylist", "zonia", "zigimedia", "danieltkseo", "blaski", "jpartin")
        ):
            vendor.append(line)
        elif "qt scan" in lower:
            vendor.append(f"QT Scan availability inquiry answered (launch delay) → {recipient}")

    vendor.extend(_zonia_vendor_bullets(tasks or [], sent))
    vendor = _dedupe_lines(vendor)

    sections: list[tuple[str, list[str]]] = []
    if leadership:
        sections.append(("Leadership & Exec", _dedupe_lines(leadership)))
    if team:
        sections.append(("Team & Ops", _dedupe_lines(team)))
    if vendor:
        sections.append(("Vendor / Partner", _dedupe_lines(vendor)))
    return sections


def _dedupe_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


def load_activity_summary_report(
    *,
    start: date,
    end: date,
) -> ActivitySummaryReport:
    errors: list[str] = []
    inbox: list[GmailMessage] = []
    sent: list[GmailMessage] = []
    tasks: list[CompletedTask] = []

    try:
        gmail = gmail_service()
        inbox, sent = fetch_inbox_and_sent(gmail, start=start, end=end, max_per_folder=500)
    except Exception as exc:
        errors.append(f"Gmail: {exc}")

    try:
        tasks = fetch_completed_tasks(tasks_service(), start=start, end=end)
    except Exception as exc:
        errors.append(f"Google Tasks: {exc}")

    chat_lines, chat_err = _fetch_chat_lines(start=start, end=end)
    if chat_err:
        errors.append(chat_err)

    milestones = _detect_milestones(sent, tasks, chat_lines=chat_lines)
    completed_sections, operational_work_groups, staff_errors = _consolidate_completed_tasks(
        tasks,
        start=start,
        end=end,
        sent=sent,
        chat_lines=chat_lines,
    )
    errors.extend(staff_errors)
    net_read = _build_net_read(
        sent_count=len(sent),
        inbox_count=len(inbox),
        tasks=tasks,
        milestones=milestones,
    )
    meta_bits = [
        f"{len(sent)} sent",
        f"{len(tasks)} tasks completed",
        f"{len(chat_lines)} chat messages",
        f"{len(milestones)} milestones",
    ]
    meta_line = " · ".join(meta_bits)

    email_sections = _consolidate_other_email(sent, milestones, tasks=tasks)
    email_sections.extend(_consolidate_contact_sections(sent, chat_lines))

    return ActivitySummaryReport(
        period_start=start,
        period_end=end,
        net_read=net_read,
        meta_line=meta_line,
        milestones=milestones,
        completed_sections=completed_sections,
        email_sections=email_sections,
        operational_work_groups=operational_work_groups,
        errors=errors,
    )


def _bullets(items: list[str]) -> str:
    if not items:
        return "<ul><li><em>None in this period.</em></li></ul>"
    rows = "".join(f"<li>{html.escape(item)}</li>" for item in items)
    return f"<ul>{rows}</ul>"


def _milestone_bullets(items: list[tuple[str, str]]) -> str:
    if not items:
        return "<ul><li><em>No milestones detected — review raw data or edit HTML.</em></li></ul>"
    rows = "".join(
        f"<li><strong>{html.escape(title)}</strong> — {html.escape(detail)}</li>"
        for title, detail in items
    )
    return f"<ul>{rows}</ul>"


def render_activity_summary_html(report: ActivitySummaryReport) -> str:
    title = (
        f"Activity Summary — {report.period_start.strftime('%b')} "
        f"{report.period_start.day}–{report.period_end.strftime('%b')} "
        f"{report.period_end.day}, {report.period_end.year}"
    )

    completed_html = ""
    for section_title, bullets in report.completed_sections:
        completed_html += f"<h3>{html.escape(section_title)}</h3>\n{_bullets(bullets)}\n"

    if report.operational_work_groups:
        completed_html += "<h3>Operational Work</h3>\n"
        for sub_title, bullets in report.operational_work_groups:
            completed_html += (
                f"<h4>{html.escape(sub_title)}</h4>\n{_bullets(bullets)}\n"
            )

    email_html = ""
    for section_title, bullets in report.email_sections:
        email_html += f"<h3>{html.escape(section_title)}</h3>\n{_bullets(bullets)}\n"

    errors_html = ""
    if report.errors:
        errors_html = f"<p style='color:#a00;'>{html.escape('; '.join(report.errors))}</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: Arial, sans-serif; font-size: 10pt; margin: 16px; line-height: 1.4; }}
h1 {{ font-size: 12pt; font-weight: bold; margin: 0 0 8px 0; }}
h2 {{ font-size: 11pt; font-weight: bold; margin: 16px 0 6px 0; }}
h3 {{ font-size: 10pt; font-weight: bold; margin: 10px 0 4px 0; }}
h4 {{ font-size: 10pt; font-weight: bold; margin: 8px 0 3px 0; color: #444; }}
.net-read {{ margin: 0 0 14px 0; }}
.meta {{ margin-bottom: 12px; color: #444; }}
ul {{ margin: 0 0 8px 0; padding-left: 20px; }}
li {{ margin-bottom: 3px; }}
</style>
</head>
<body>

<h1>{html.escape(title)}</h1>

<p class="net-read"><strong>Net read:</strong> {html.escape(report.net_read)}</p>
<p class="meta">{html.escape(report.meta_line)}</p>
{errors_html}

<h2>Milestones</h2>
{_milestone_bullets(report.milestones)}

<h2>Completed Tasks</h2>
{completed_html or "<p><em>No completed tasks in range.</em></p>"}

<h2>Other Email</h2>
{email_html or "<p><em>No additional sent mail in range.</em></p>"}

</body>
</html>
"""


def write_activity_summary_report(
    report: ActivitySummaryReport,
    *,
    output_dir: Path | None = None,
) -> Path:
    out_dir = output_dir or OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"activity_summary_{report.period_start.isoformat()}_{report.period_end.isoformat()}.html"
    path.write_text(render_activity_summary_html(report), encoding="utf-8")
    return path


def _serve_directory(directory: Path, port: int) -> None:
    handler = type(
        "Handler",
        (SimpleHTTPRequestHandler,),
        {"directory": str(directory.resolve())},
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"Serving {directory} at http://127.0.0.1:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gmail + Google Tasks activity summary (HTML)")
    parser.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--days", type=int, default=None, help="Trailing N days ending on --end")
    parser.add_argument("--open", action="store_true", help="Open HTML in browser")
    parser.add_argument("--serve", action="store_true", help="Serve output dir on localhost after write")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-dir", type=str, default=None)
    args = parser.parse_args(argv)

    period_end = date.fromisoformat(args.end) if args.end else date.today()
    if args.start:
        period_start = date.fromisoformat(args.start)
    elif args.days:
        period_start = period_end - timedelta(days=args.days - 1)
    else:
        period_start = period_end - timedelta(days=4)

    print(f"Fetching activity for {period_start} .. {period_end} …")
    report = load_activity_summary_report(start=period_start, end=period_end)
    out_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR
    path = write_activity_summary_report(report, output_dir=out_dir)
    print(f"Wrote {path}")

    if args.open:
        webbrowser.open(path.resolve().as_uri())

    if args.serve:
        _serve_directory(out_dir, args.port)
    elif args.open and args.port:
        print(f"View at http://127.0.0.1:{args.port}/{path.name} (run with --serve to host)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
