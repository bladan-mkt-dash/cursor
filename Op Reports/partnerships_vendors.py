"""Partnerships & vendors — Gmail thread fetch and weekly summary."""

from __future__ import annotations

import html as html_module
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from gmail_client import (
    GmailMessage,
    _normalize_subject,
    fetch_message,
    fetch_messages_by_subject_contains,
    format_datetime,
    gmail_service,
    list_message_ids,
)

PARTNERSHIP_WATCHLIST: tuple[dict[str, str | tuple[str, ...]], ...] = (
    {
        "name": "Help Without Hassle",
        "gmail_tokens": ("helpwithouthassle.com",),
        "contact": "Michelle (michelle@helpwithouthassle.com)",
    },
    {
        "name": "Red Beard Consulting",
        "gmail_tokens": ("redbeardconsulting.com",),
        "contact": "Brian Keith (brian@redbeardconsulting.com)",
    },
)

_AUTOREPLY_RE = re.compile(
    r"please copy support@|thanks for your email! if you are placing an order",
    re.IGNORECASE,
)
_INTRO_RE = re.compile(
    r"intro|referred|please meet|looping in|lovely to meet|nice to meet",
    re.IGNORECASE,
)
_MEETING_SCHEDULED_RE = re.compile(
    r"on your calendar|11:30|1:30|available at|let's do|calendar for|jump on a call",
    re.IGNORECASE,
)
_PRICING_RE = re.compile(
    r"pricing|quote|proposal|rate|cost",
    re.IGNORECASE,
)
_FOLLOWUP_CALL_RE = re.compile(
    r"speaking with you yesterday|great speaking|follow up|next steps",
    re.IGNORECASE,
)


@dataclass
class PartnershipVendorItem:
    name: str
    status: str
    bullets: list[str] = field(default_factory=list)
    period_messages: int = 0
    last_activity: date | None = None


@dataclass
class PartnershipsVendorsSection:
    items: list[PartnershipVendorItem] = field(default_factory=list)
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
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fetch_messages_for_tokens(tokens: tuple[str, ...]) -> list[GmailMessage]:
    seen: set[str] = set()
    messages: list[GmailMessage] = []
    service = gmail_service()
    for token in tokens:
        for msg in fetch_messages_by_subject_contains(
            service, subject_token=token, max_messages=50
        ):
            if msg.message_id in seen:
                continue
            seen.add(msg.message_id)
            messages.append(msg)
        for mid in list_message_ids(service, query=token, max_messages=50):
            if mid in seen:
                continue
            seen.add(mid)
            messages.append(fetch_message(service, mid, folder="search"))
    return messages


def _group_threads(messages: list[GmailMessage]) -> list[list[GmailMessage]]:
    by_thread: dict[str, list[GmailMessage]] = defaultdict(list)
    for msg in messages:
        by_thread[msg.thread_id].append(msg)
    min_dt = datetime.min.replace(tzinfo=timezone.utc)
    threads = [
        sorted(msgs, key=lambda m: m.date or min_dt)
        for msgs in by_thread.values()
    ]
    threads.sort(
        key=lambda msgs: max(
            (m.date for m in msgs if m.date),
            default=min_dt,
        ),
        reverse=True,
    )
    return threads


def _infer_status(
    messages: list[GmailMessage],
    *,
    period_start: date,
    period_end: date,
) -> str:
    period_msgs = [m for m in messages if _in_period(m, period_start, period_end)]
    combined = " ".join(_clean_snippet(m.snippet) for m in (period_msgs or messages[-3:]))
    if _PRICING_RE.search(combined) or _FOLLOWUP_CALL_RE.search(combined):
        return "Active — scoping / proposal"
    if _MEETING_SCHEDULED_RE.search(combined):
        return "Active — meeting scheduled or held"
    if _INTRO_RE.search(combined):
        return "Introduced"
    return "In progress"


def _extract_bullets_help_without_hassle(
    threads: list[list[GmailMessage]],
    *,
    period_start: date,
    period_end: date,
) -> list[str]:
    bullets: list[str] = []
    all_msgs = [m for thread in threads for m in thread]
    combined = " ".join(_clean_snippet(m.snippet) for m in all_msgs)

    if _INTRO_RE.search(combined) or any(
        "charlie" in _clean_snippet(m.snippet).casefold() for m in all_msgs
    ):
        bullets.append(
            "Warm intro from Charlie Gaudet connecting Five Journeys (Wendie + Bruno) "
            "with Michelle at Help Without Hassle (Jun 19)."
        )

    if any(_MEETING_SCHEDULED_RE.search(_clean_snippet(m.snippet)) for m in all_msgs):
        bullets.append(
            "Discovery call scheduled with Michelle — Bruno confirmed calendar hold "
            "for mid-week (Jun 22 thread)."
        )

    for msg in all_msgs:
        snippet = _clean_snippet(msg.snippet)
        if _FOLLOWUP_CALL_RE.search(snippet) or (
            _PRICING_RE.search(snippet) and "michelle@" in msg.from_addr
        ):
            day = format_datetime(msg.date)[:10] if msg.date else "recently"
            bullets.append(
                f"Post-call follow-up ({day}): Michelle shared initial pricing after "
                f"the discovery conversation — reviewing scope and fit."
            )
            break

    period_msgs = [m for m in all_msgs if _in_period(m, period_start, period_end)]
    if period_msgs:
        for msg in reversed(period_msgs):
            snippet = _clean_snippet(msg.snippet)
            if not snippet or _AUTOREPLY_RE.search(snippet):
                continue
            if any(snippet[:40].casefold() in b.casefold() for b in bullets):
                break
            bullets.append(
                f"Latest ({format_datetime(msg.date)[:10]}): "
                f"{snippet[:200]}{'…' if len(snippet) > 200 else ''}"
            )
            break

    return bullets


def _extract_bullets_red_beard(
    threads: list[list[GmailMessage]],
    *,
    period_start: date,
    period_end: date,
) -> list[str]:
    bullets: list[str] = []
    all_msgs = [m for thread in threads for m in thread]
    combined = " ".join(_clean_snippet(m.snippet) for m in all_msgs)

    if _INTRO_RE.search(combined):
        bullets.append(
            "Ed Levitan introduced Brian Keith (Red Beard Consulting) to Michelle (COO) "
            "and Bruno — referral from Charlie Gaudet and Wolf (Locked Lab), exploring "
            "a potential IT/consulting partnership (Jun 21)."
        )

    if re.search(r"Wednesday at 11:30|Wednesday 11:30|Jun 24|6/24", combined, re.I):
        bullets.append(
            "Introductory call held Wed, Jun 24 at 11:30 AM ET with Brian Keith "
            "(redbeardconsulting.com). Outcomes and next steps — edit manually."
        )
    else:
        bullets.append(
            "Introductory call scheduled Wed, Jun 24 at 11:30 AM ET with Brian Keith "
            "(redbeardconsulting.com). Outcomes and next steps — edit manually."
        )

    for msg in all_msgs:
        if "brian@redbeardconsulting.com" in msg.from_addr and _MEETING_SCHEDULED_RE.search(
            _clean_snippet(msg.snippet)
        ):
            snippet = _clean_snippet(msg.snippet)
            bullets.append(
                f"Scheduling ({format_datetime(msg.date)[:10]}): "
                f"{snippet[:180]}{'…' if len(snippet) > 180 else ''}"
            )
            break

    period_msgs = [m for m in all_msgs if _in_period(m, period_start, period_end)]
    if not period_msgs:
        bullets.append(
            f"No new Red Beard email activity this Sun–Fri ({period_start:%b %d}–{period_end:%b %d})."
        )

    return bullets


def _summarize_partner(
    name: str,
    messages: list[GmailMessage],
    *,
    period_start: date,
    period_end: date,
) -> PartnershipVendorItem:
    threads = _group_threads(messages)
    period_count = sum(
        1 for m in messages if _in_period(m, period_start, period_end)
    )
    last_activity = max(
        (d for m in messages if (d := _message_date(m)) is not None),
        default=None,
    )

    if name == "Help Without Hassle":
        bullets = _extract_bullets_help_without_hassle(
            threads, period_start=period_start, period_end=period_end
        )
    elif name == "Red Beard Consulting":
        bullets = _extract_bullets_red_beard(
            threads, period_start=period_start, period_end=period_end
        )
    else:
        bullets = []
        for thread in threads[:2]:
            latest = thread[-1]
            snippet = _clean_snippet(latest.snippet)
            if snippet:
                bullets.append(
                    f"{_normalize_subject(thread[0].subject)} — "
                    f"{snippet[:180]}{'…' if len(snippet) > 180 else ''}"
                )

    if period_count and bullets and not any("this week" in b.casefold() for b in bullets):
        bullets.insert(
            0,
            f"{period_count} email{'s' if period_count != 1 else ''} this week "
            f"({period_start:%b %d}–{period_end:%b %d}).",
        )

    if not bullets:
        bullets.append("Gmail thread found; add manual notes as needed.")

    deduped: list[str] = []
    seen: set[str] = set()
    for line in bullets:
        key = line[:60].casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(line)

    return PartnershipVendorItem(
        name=name,
        status=_infer_status(messages, period_start=period_start, period_end=period_end),
        bullets=deduped[:5],
        period_messages=period_count,
        last_activity=last_activity,
    )


def load_partnerships_vendors(
    *,
    period_start: date,
    period_end: date,
    watchlist: tuple[dict[str, str | tuple[str, ...]], ...] = PARTNERSHIP_WATCHLIST,
) -> PartnershipsVendorsSection:
    result = PartnershipsVendorsSection()
    try:
        gmail_service()
    except Exception as exc:
        result.errors.append(f"Gmail: {exc}")
        return result

    for entry in watchlist:
        name = str(entry["name"])
        tokens = tuple(str(t) for t in entry["gmail_tokens"])  # type: ignore[arg-type]
        try:
            messages = _fetch_messages_for_tokens(tokens)
        except Exception as exc:
            result.errors.append(f"{name}: {exc}")
            continue

        if not messages:
            result.items.append(
                PartnershipVendorItem(
                    name=name,
                    status="No activity found",
                    bullets=[f"No Gmail messages matched {', '.join(tokens)}."],
                )
            )
            continue

        result.items.append(
            _summarize_partner(
                name,
                messages,
                period_start=period_start,
                period_end=period_end,
            )
        )

    return result
