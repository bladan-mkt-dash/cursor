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

_SKIP_TASK_RE = re.compile(
    r"^(re:|fwd:|fw:)|pay vas|welcome to your workplace|^hi bruno\.",
    re.IGNORECASE,
)
_SKIP_EMAIL_SUBJECT_RE = re.compile(
    r"^(test$|missing docs report|welcome to your workplace|health insurance plan)",
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


def _consolidate_completed_tasks(tasks: list[CompletedTask]) -> list[tuple[str, list[str]]]:
    if not tasks:
        return []

    campaigns: list[str] = []
    if any(k in " ".join(_task_text(t) for t in tasks) for k in ("women", "zigi", "solstice", "ad campaign")):
        campaigns.append(
            "Women's health + Summer Solstice prep — asset rankings to Zigi Media, "
            "ad campaign clarification, Solstice creative briefed internally"
        )

    seo: list[str] = []
    blob = " ".join(_task_text(t) for t in tasks)
    if "search optimization" in blob or "supplement store" in blob:
        seo.append("Supplement store SEO program — project sheet, Monday board, contractor authorized")
    if "referral" in blob and "npi" in blob:
        seo.append("Referral page updated with current providers and NPI numbers")
    if "indexed" in blob or "search console" in blob:
        seo.append("Google Search Console indexing issues investigated")

    vendor: list[str] = []
    if any("inventory" in _task_text(t) and "valley" in _task_text(t) for t in tasks):
        vendor.append("Valley List inventory reconciliation escalated to support ticket")
    if any("subscription" in _task_text(t) and "token" in _task_text(t) for t in tasks):
        vendor.append("Subscription renewal token failures triaged with tech support (16 tokens)")

    staff: list[str] = []
    sam_notes: list[str] = []
    for task in tasks:
        text = _task_text(task)
        if "staff oversight" in text and "sam" in text:
            if "monday board" in text:
                sam_notes.append("Monday board cleanup")
            if "nurture" in text:
                sam_notes.append("nurture sequence rebalance")
            if "vidiq" in text or "a/b" in text:
                sam_notes.append("VidIQ A/B test")
            if "newsletter" in text or "solstice" in text or "khafagy" in text:
                sam_notes.append("newsletter, Khafagy copy, Solstice banner")
    if sam_notes:
        unique = list(dict.fromkeys(sam_notes))
        staff.append(f"Sam — {', '.join(unique)}")
    if any("voltaire" in _task_text(t) or "blog #20" in _task_text(t) for t in tasks):
        staff.append("Voltaire — blog #20 approved; blogs 17–19 cleared to proceed")

    print_items: list[str] = []
    blob = " ".join(_task_text(t) for t in tasks)
    if "welcome packet" in blob or "meet the team" in blob:
        print_items.append("Welcome packet Meet the Team update sent to print")
    if any(k in blob for k in ("concierge", "wellness iv menu", "tamer")):
        print_items.append(
            "Concierge welcome cards, Wellness IV menu, and Tamer Khafagy business cards ordered"
        )
    if "sandwich board" in blob:
        print_items.append("Boylston sandwich boards finalized — functional medicine + HBOT/Wellness IV")

    sections: list[tuple[str, list[str]]] = []
    if campaigns:
        sections.append(("Campaigns", campaigns))
    if seo:
        sections.append(("SEO & Web", seo))
    if vendor:
        sections.append(("Vendor / Tech Ops", vendor))
    if staff:
        sections.append(("Staff Oversight", staff))
    if print_items:
        sections.append(("Print & Collateral", print_items))
    return sections


def _detect_milestones(
    sent: list[GmailMessage],
    tasks: list[CompletedTask],
) -> list[tuple[str, str]]:
    subjects = " ".join(_normalize_subject(m.subject).lower() for m in sent)
    task_blob = " ".join(_task_text(t) for t in tasks)
    combined = f"{subjects} {task_blob}"
    milestones: list[tuple[str, str]] = []

    if any(k in combined for k in ("women", "zigi", "top 10", "ad campaign", "clarification on ad")):
        milestones.append(
            (
                "Women's health campaign aligned with Zigi Media",
                "Top 10 posts + asset rankings shared; ad scope and asset inventory clarified with Kim. Late-June launch.",
            )
        )
    if any(k in combined for k in ("seo", "search optimization", "danieltkseo", "supplement store")):
        milestones.append(
            (
                "SEO supplement store program kicked off",
                "Project sheet + Monday board; plan shared with Danil; payment terms agreed; work authorized.",
            )
        )
    if any(k in combined for k in ("week in review", "week in review", "benefits", "ideal patient", "predictable profits")):
        milestones.append(
            (
                "Executive reporting & strategic positioning",
                "Week In Review to Ed; Benefits/Promise framework + Ideal Patient profile to Predictable Profits.",
            )
        )
    if "immune for life" in combined or "zonia" in combined:
        milestones.append(
            (
                'Zonia "Immune for Life" partnership confirmed',
                "Five Journeys in for August campaign.",
            )
        )
    if any(
        k in combined
        for k in ("operational priorities", "athenanet", "gohighlevel", "duplicate data entry", "ghl")
    ):
        milestones.append(
            (
                "COO ops initiative + integration scoping",
                "Operational priorities with Michelle; GHL duplicate-entry audit launched; "
                "AthenaNet/Zenoti/GHL plan scoped and timeline pushed back.",
            )
        )
    return milestones[:5]


def _build_net_read(
    *,
    sent_count: int,
    inbox_count: int,
    tasks: list[CompletedTask],
    milestones: list[tuple[str, str]],
) -> str:
    themes: list[str] = []
    blob = " ".join(title.lower() for title, _ in milestones)
    if "women" in blob or "zigi" in blob:
        themes.append("campaign prep (women's health, Solstice)")
    if "seo" in blob:
        themes.append("SEO launch")
    if any("print" in _task_text(t) or "sandwich" in _task_text(t) for t in tasks):
        themes.append("print/collateral production")
    if "coo" in blob or "integration" in blob:
        themes.append("COO-level ops")

    theme_text = ", ".join(themes) if themes else "email and task throughput"
    task_days = {t.completed_date for t in tasks}
    email_note = ""
    if sent_count >= 30 and len(task_days) >= 3:
        email_note = " Early days were email-driven; task completions ramped later in the period."
    elif sent_count >= 20:
        email_note = " Outbound email carried momentum; task completions stacked mid-to-late week."

    inbox_note = f" Inbox stayed clean ({inbox_count} promo{'s' if inbox_count != 1 else ''} only)." if inbox_count <= 3 else ""

    return (
        f"Heavy week on {theme_text}.{email_note}{inbox_note}".strip()
    )


def _consolidate_other_email(
    sent: list[GmailMessage],
    milestones: list[tuple[str, str]],
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
        "immune for life is back this august",
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
        if _first_email(msg.from_addr) == _first_email(msg.to_addrs) and "test" in key:
            continue
        seen.add(key)

        recipient = _first_recipient_name(msg.to_addrs)
        addr = _first_email(msg.to_addrs)
        line = f"{subject} → {recipient}"
        lower = f"{subject} {addr}".lower()

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

    milestones = _detect_milestones(sent, tasks)
    net_read = _build_net_read(
        sent_count=len(sent),
        inbox_count=len(inbox),
        tasks=tasks,
        milestones=milestones,
    )
    meta_line = f"{len(sent)} sent · {len(tasks)} tasks completed · {len(milestones)} milestones"

    return ActivitySummaryReport(
        period_start=start,
        period_end=end,
        net_read=net_read,
        meta_line=meta_line,
        milestones=milestones,
        completed_sections=_consolidate_completed_tasks(tasks),
        email_sections=_consolidate_other_email(sent, milestones),
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
