"""Tech Support major tickets — Gmail thread fetch and weekly summary."""

from __future__ import annotations

import html as html_module
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from gmail_client import (
    GmailMessage,
    _normalize_subject,
    fetch_messages_by_subject_contains,
    format_datetime,
    gmail_service,
)

MAJOR_TECH_TICKET_IDS: tuple[str, ...] = (
    "#TVL00011603",
    "#TVL00011765",
    "#TVL00011786",
)

_VALLEY_SUPPORT = "support@thevalleylist.com"
_RESOLVED_RE = re.compile(
    r"close this ticket|feel free to close|moved to live|should be good|"
    r"no open invoices|don't think we need to reach out|inventory may have updated",
    re.IGNORECASE,
)
_IN_PROGRESS_RE = re.compile(
    r"still looking|investigating|please give me more time|checking back|"
    r"keep an eye|i'll keep an eye|open another ticket",
    re.IGNORECASE,
)
_ACCOMPLISHMENT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(\d+)\s+products?\s+have\s+been\s+synced", re.I),
        "{n} WooCommerce products synced to Zenoti and integration moved to live (Jun 23).",
    ),
    (
        re.compile(r"successfully established the connection", re.I),
        "Valley List established the Woo/Zenoti API connection and retrieved Center ID requirements.",
    ),
    (
        re.compile(r"velocity fraud filter", re.I),
        "Root cause identified: Authorize.net daily velocity fraud filter held authorized-but-uncaptured charges during the online sale.",
    ),
    (
        re.compile(r"change the order status manually", re.I),
        "Team advised manually moving on-hold orders to Completed so sale revenue reports correctly.",
    ),
    (
        re.compile(r"Products Analytics report can include sales from orders that are not yet completed", re.I),
        "Clarified Woo reporting gap: Products Analytics includes on-hold/processing orders; Revenue and Order reports count completed orders only.",
    ),
    (
        re.compile(r"further changes to resolve this.*open invoice", re.I | re.DOTALL),
        "Valley List shipped a fix for Zenoti open-invoice buildup from WooCommerce reconciliation.",
    ),
    (
        re.compile(r"don't see any open invoices that were created on 6/24", re.I),
        "Confirmed Jun 24 open-invoice issue cleared in Zenoti after the reconciliation fix.",
    ),
    (
        re.compile(r"inventory may have updated automatically", re.I),
        "Partial refund test (order 104621) showed inventory updating automatically in Woo/Zenoti.",
    ),
)


@dataclass
class TechSupportTicketSummary:
    ticket_id: str
    subject: str
    status: str
    total_messages: int
    period_messages: int
    bullets: list[str] = field(default_factory=list)
    last_activity: date | None = None


@dataclass
class TechSupportMajorTickets:
    tickets: list[TechSupportTicketSummary] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _message_date(msg: GmailMessage) -> date | None:
    if not msg.date:
        return None
    return msg.date.date()


def _in_period(msg: GmailMessage, start: date, end: date) -> bool:
    d = _message_date(msg)
    return d is not None and start <= d <= end


def _clean_snippet(snippet: str) -> str:
    text = html_module.unescape((snippet or "").replace("\n", " "))
    text = re.sub(r"^New reply for the ticket #TVL\d+\s*", "", text, flags=re.I)
    text = re.sub(r"^Ticket created #TVL\d+\s*", "", text, flags=re.I)
    text = re.sub(r"^Hi [A-Za-z]+,?\s*", "", text)
    text = re.sub(r"#TVL\d+\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _sender_label(from_addr: str) -> str:
    addr = (from_addr or "").casefold()
    if addr == _VALLEY_SUPPORT:
        return "Valley List"
    if "tfelt@" in addr:
        return "Tucker"
    if "bladan@" in addr:
        return "Bruno"
    return from_addr.split("@")[0] if "@" in from_addr else from_addr


def _pick_main_thread(messages: list[GmailMessage]) -> list[GmailMessage]:
    by_thread: dict[str, list[GmailMessage]] = defaultdict(list)
    for msg in messages:
        by_thread[msg.thread_id].append(msg)
    if not by_thread:
        return []
    main = max(by_thread.values(), key=len)
    min_dt = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(main, key=lambda m: m.date or min_dt)


def _ticket_subject(messages: list[GmailMessage], ticket_id: str) -> str:
    subjects = [_normalize_subject(m.subject) for m in messages]
    for subj in subjects:
        cleaned = re.sub(r"\s*" + re.escape(ticket_id) + r"\s*$", "", subj).strip()
        if cleaned and cleaned.casefold() != "ticket created":
            return cleaned
    return subjects[0] if subjects else ticket_id


def _infer_status(messages: list[GmailMessage], *, period_start: date, period_end: date) -> str:
    period_msgs = [m for m in messages if _in_period(m, period_start, period_end)]
    scan = period_msgs or messages[-3:]
    combined = " ".join(_clean_snippet(m.snippet) for m in scan)
    if _RESOLVED_RE.search(combined):
        return "Resolved / monitoring"
    if _IN_PROGRESS_RE.search(combined):
        return "In progress"
    return "Open"


def _extract_accomplishments(messages: list[GmailMessage]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    combined = " ".join(_clean_snippet(m.snippet) for m in messages)
    for pattern, template in _ACCOMPLISHMENT_PATTERNS:
        match = pattern.search(combined)
        if not match:
            continue
        if "{n}" in template:
            line = template.format(n=match.group(1))
        else:
            line = template
        key = line.casefold()
        if key not in seen:
            seen.add(key)
            found.append(line)
    return found


def _period_activity_bullets(
    messages: list[GmailMessage],
    *,
    period_start: date,
    period_end: date,
) -> list[str]:
    period_msgs = [m for m in messages if _in_period(m, period_start, period_end)]
    if not period_msgs:
        return []

    bullets: list[str] = []
    support_in_period = [m for m in period_msgs if m.from_addr == _VALLEY_SUPPORT]
    internal_in_period = [m for m in period_msgs if m.from_addr != _VALLEY_SUPPORT]

    if support_in_period:
        latest_support = support_in_period[-1]
        snippet = _clean_snippet(latest_support.snippet)
        if snippet:
            bullets.append(
                f"Latest from Valley List ({format_datetime(latest_support.date)[:10]}): "
                f"{snippet[:220]}{'…' if len(snippet) > 220 else ''}"
            )

    for msg in internal_in_period[-2:]:
        snippet = _clean_snippet(msg.snippet)
        if not snippet or len(snippet) < 20:
            continue
        bullets.append(
            f"{_sender_label(msg.from_addr)} ({format_datetime(msg.date)[:10]}): "
            f"{snippet[:200]}{'…' if len(snippet) > 200 else ''}"
        )

    return bullets[:3]


def summarize_ticket_thread(
    ticket_id: str,
    messages: list[GmailMessage],
    *,
    period_start: date,
    period_end: date,
) -> TechSupportTicketSummary:
    thread = _pick_main_thread(messages)
    subject = _ticket_subject(thread, ticket_id)
    period_count = sum(1 for m in thread if _in_period(m, period_start, period_end))
    last_activity = max(
        (d for m in thread if (d := _message_date(m)) is not None),
        default=None,
    )

    bullets: list[str] = []
    bullets.extend(_extract_accomplishments(thread))

    period_bullets = _period_activity_bullets(
        thread, period_start=period_start, period_end=period_end
    )
    for line in period_bullets:
        if line not in bullets:
            bullets.append(line)

    if period_count == 0:
        bullets.append(
            f"No new email activity this Sun–Fri ({period_start:%b %d}–{period_end:%b %d}); "
            f"last thread update {last_activity:%b %d, %Y}."
            if last_activity
            else "No new email activity this Sun–Fri."
        )
    else:
        bullets.insert(
            0,
            f"{period_count} email{'s' if period_count != 1 else ''} this week "
            f"({period_start:%b %d}–{period_end:%b %d}) · {len(thread)} messages total in thread.",
        )

    if not bullets:
        bullets.append("Thread located in Gmail; review manually for details.")

    # Cap bullets per ticket for email readability
    deduped: list[str] = []
    seen: set[str] = set()
    for line in bullets:
        key = line[:80].casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(line)

    return TechSupportTicketSummary(
        ticket_id=ticket_id,
        subject=subject,
        status=_infer_status(thread, period_start=period_start, period_end=period_end),
        total_messages=len(thread),
        period_messages=period_count,
        bullets=deduped[:5],
        last_activity=last_activity,
    )


def load_tech_support_major_tickets(
    *,
    period_start: date,
    period_end: date,
    ticket_ids: tuple[str, ...] = MAJOR_TECH_TICKET_IDS,
) -> TechSupportMajorTickets:
    result = TechSupportMajorTickets()
    try:
        service = gmail_service()
    except Exception as exc:
        result.errors.append(f"Gmail: {exc}")
        return result

    for ticket_id in ticket_ids:
        try:
            messages = fetch_messages_by_subject_contains(
                service, subject_token=ticket_id, max_messages=100
            )
        except Exception as exc:
            result.errors.append(f"{ticket_id}: {exc}")
            continue

        if not messages:
            result.tickets.append(
                TechSupportTicketSummary(
                    ticket_id=ticket_id,
                    subject="(no Gmail thread found)",
                    status="Unknown",
                    total_messages=0,
                    period_messages=0,
                    bullets=[
                        f"No messages found in Gmail with subject containing {ticket_id}."
                    ],
                )
            )
            continue

        result.tickets.append(
            summarize_ticket_thread(
                ticket_id,
                messages,
                period_start=period_start,
                period_end=period_end,
            )
        )

    return result
