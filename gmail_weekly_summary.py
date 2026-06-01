"""
Weekly Gmail digest — inbox + sent for a date range.

Setup:
  1. Google Cloud: enable Gmail API; add gmail.readonly scope on OAuth consent screen
  2. python auth_google_gmail.py
  3. python verify_gmail_connection.py

Usage:
  python gmail_weekly_summary.py
  python gmail_weekly_summary.py --days 7 --end 2026-05-23
  python gmail_weekly_summary.py --previous-calendar-week

Writes markdown to outputs/gmail_weekly_YYYY-MM-DD.md
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from gmail_client import (
    GmailMessage,
    _normalize_subject,
    active_threads,
    fetch_inbox_and_sent,
    format_datetime,
    gmail_service,
    top_addresses,
    week_range,
)

PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / "outputs"


def _previous_calendar_week(today: date | None = None) -> tuple[date, date]:
    """Monday–Sunday of the week before the week containing ``today``."""
    today = today or date.today()
    this_monday = today - timedelta(days=today.weekday())
    last_sunday = this_monday - timedelta(days=1)
    last_monday = last_sunday - timedelta(days=6)
    return last_monday, last_sunday


def _section_messages(title: str, messages: list[GmailMessage], *, limit: int = 40) -> list[str]:
    lines = [f"### {title}", ""]
    if not messages:
        lines.append("_No messages in this period._")
        lines.append("")
        return lines

    min_dt = datetime.min.replace(tzinfo=timezone.utc)
    sorted_msgs = sorted(messages, key=lambda m: m.date or min_dt, reverse=True)
    for msg in sorted_msgs[:limit]:
        when = format_datetime(msg.date)
        peer = msg.from_addr if msg.folder == "inbox" else _first_to(msg)
        lines.append(f"- **{when}** — {_normalize_subject(msg.subject)}")
        lines.append(f"  - {msg.folder}: `{peer}`")
        if msg.snippet:
            lines.append(f"  - _{msg.snippet[:160]}{'…' if len(msg.snippet) > 160 else ''}_")
    if len(sorted_msgs) > limit:
        lines.append(f"- _…and {len(sorted_msgs) - limit} more (increase --list-limit to show)_")
    lines.append("")
    return lines


def _first_to(msg: GmailMessage) -> str:
    match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", msg.to_addrs)
    return match.group(0).lower() if match else msg.to_addrs[:60]


def _subject_clusters(messages: list[GmailMessage]) -> list[str]:
    clusters: dict[str, list[GmailMessage]] = defaultdict(list)
    for msg in messages:
        clusters[_normalize_subject(msg.subject)].append(msg)

    rows = sorted(clusters.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))
    lines = ["| Subject | Count |", "| --- | ---: |"]
    for subject, msgs in rows[:25]:
        safe = subject.replace("|", "\\|")
        lines.append(f"| {safe} | {len(msgs)} |")
    lines.append("")
    return lines


def build_report(
    *,
    start: date,
    end: date,
    inbox: list[GmailMessage],
    sent: list[GmailMessage],
    list_limit: int,
) -> str:
    all_msgs = inbox + sent
    inbox_threads = active_threads(inbox)
    sent_threads = active_threads(sent)

    lines = [
        f"# Gmail weekly summary",
        "",
        f"**Period:** {start.isoformat()} through {end.isoformat()} (inclusive)",
        "",
        "## Overview",
        "",
        f"| | Count |",
        f"| --- | ---: |",
        f"| Inbox received | {len(inbox)} |",
        f"| Sent | {len(sent)} |",
        f"| Combined | {len(all_msgs)} |",
        "",
    ]

    if inbox:
        lines.append("### Top senders (inbox)")
        lines.append("")
        for addr, count in top_addresses(inbox, field="from"):
            lines.append(f"- `{addr}` — {count}")
        lines.append("")

    if sent:
        lines.append("### Top recipients (sent)")
        lines.append("")
        for addr, count in top_addresses(sent, field="to"):
            lines.append(f"- `{addr}` — {count}")
        lines.append("")

    lines.extend(["## Subject themes", ""])
    if inbox:
        lines.append("### Inbox")
        lines.append("")
        lines.extend(_subject_clusters(inbox))
    if sent:
        lines.append("### Sent")
        lines.append("")
        lines.extend(_subject_clusters(sent))

    if inbox_threads or sent_threads:
        lines.extend(["## Active threads (3+ messages)", ""])
        if inbox_threads:
            lines.append("### Inbox")
            lines.append("")
            for subject, count, _tid in inbox_threads[:15]:
                lines.append(f"- **{subject}** — {count} messages")
            lines.append("")
        if sent_threads:
            lines.append("### Sent")
            lines.append("")
            for subject, count, _tid in sent_threads[:15]:
                lines.append(f"- **{subject}** — {count} messages")
            lines.append("")

    lines.extend(_section_messages("Recent inbox", inbox, limit=list_limit))
    lines.extend(_section_messages("Recent sent", sent, limit=list_limit))

    lines.extend(
        [
            "---",
            "",
            "_Generated by `gmail_weekly_summary.py`. "
            "Paste this file into Cursor for a narrative summary, or re-run at end of week._",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly Gmail inbox + sent digest")
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of calendar days in the range (default: 7)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default="",
        help="Last day of range (YYYY-MM-DD). Default: today",
    )
    parser.add_argument(
        "--previous-calendar-week",
        action="store_true",
        help="Use Mon–Sun of the previous calendar week instead of trailing N days",
    )
    parser.add_argument(
        "--max-per-folder",
        type=int,
        default=500,
        help="Max messages to fetch per folder (default: 500)",
    )
    parser.add_argument(
        "--list-limit",
        type=int,
        default=40,
        help="How many messages to list per folder in the report (default: 40)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Output markdown path (default: outputs/gmail_weekly_ENDDATE.md)",
    )
    args = parser.parse_args()

    if args.previous_calendar_week:
        start, end = _previous_calendar_week()
    else:
        end_date = date.fromisoformat(args.end) if args.end else None
        start, end = week_range(days=args.days, end=end_date)

    print(f"Fetching Gmail for {start} .. {end} ...")
    service = gmail_service()
    inbox, sent = fetch_inbox_and_sent(
        service,
        start=start,
        end=end,
        max_per_folder=args.max_per_folder,
    )
    print(f"  inbox: {len(inbox)}  sent: {len(sent)}")

    report = build_report(
        start=start,
        end=end,
        inbox=inbox,
        sent=sent,
        list_limit=args.list_limit,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.output) if args.output else OUTPUT_DIR / f"gmail_weekly_{end.isoformat()}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
