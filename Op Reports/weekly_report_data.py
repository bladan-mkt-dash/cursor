"""Data loaders for the Friday weekly leadership report."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from _bootstrap import setup

setup()

from acquisition_retention_data import (
    build_acquisition,
    contact_name,
    in_range,
    load_consolidated_by_name,
    norm_name,
)
from ghl_client import (
    contact_custom_field_value,
    count_calendar_funnel_events,
    fetch_committed_yes_by_hear_about_us,
    fetch_cancellation_date_range_membership_cancelled_true_contacts,
    fetch_signup_date_range_committed_yes_contacts,
    resolve_cancellation_date_custom_field_id,
    resolve_membership_level_custom_field_id,
    resolve_sign_up_date_custom_field_id,
    search_contacts_custom_field_date_range,
    search_contacts_date_added_range,
)
from gmail_client import (
    GmailMessage,
    _normalize_subject,
    fetch_messages_for_query,
    gmail_service,
    week_range,
)
from google_chat_client import _parse_rfc3339, chat_service, list_all_spaces

LOCATION_FIELD_ID = "U4qminML7Yiync8OaxNz"
LEVELS = ("Standard", "Silver", "Gold", "Platinum")
ME_USER_PREFIX = "users/"


@dataclass
class ChurnMatch:
    name: str
    ghl_cancel_date: str | None
    sheet_term_date: str | None
    level: str
    bucket: str  # both | ghl_only | sheet_only


@dataclass
class ChurnReconciliation:
    period_start: date
    period_end: date
    ghl_count: int
    sheet_count: int
    unique_count: int
    overlap_count: int
    matches: list[ChurnMatch] = field(default_factory=list)


@dataclass
class SalesKpiWeek:
    start: date
    end: date
    new_contacts: int
    discover_calls: int
    sign_ups: int
    int_to_disc_pct: float | None
    disc_to_sign_pct: float | None
    int_to_sign_pct: float | None
    by_level: dict[str, int]
    by_location: dict[str, int]
    bookings: int
    meetings: int
    hear_about: list[dict[str, int | str]]
    churn: ChurnReconciliation


def period_range(*, end: date | None = None, days: int = 7) -> tuple[date, date]:
    return week_range(days=days, end=end)


def prior_period(start: date, end: date) -> tuple[date, date]:
    span = (end - start).days + 1
    prior_end = start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=span - 1)
    return prior_start, prior_end


def _norm_level(raw: str) -> str:
    v = (raw or "").strip()
    for level in LEVELS:
        if v.casefold() == level.casefold():
            return level
    return v or "Other"


def _norm_loc(raw: str) -> str:
    s = (raw or "").strip().casefold()
    if "boston" in s:
        return "Boston"
    if "newton" in s:
        return "Newton"
    return "Other"


def _pct(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den <= 0:
        return None
    return num / den * 100.0


def _fuzzy_name_key(name: str) -> str:
    parts = norm_name(name).split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1]}"
    return norm_name(name)


def reconcile_churn(start: date, end: date) -> ChurnReconciliation:
    since, until = start.isoformat(), end.isoformat()
    cancel_field = resolve_cancellation_date_custom_field_id()

    ghl = fetch_cancellation_date_range_membership_cancelled_true_contacts(since, until)
    ghl_rows: list[dict] = []
    for c in ghl["contacts"]:
        ghl_rows.append(
            {
                "key": norm_name(contact_name(c)),
                "fuzzy": _fuzzy_name_key(contact_name(c)),
                "name": contact_name(c),
                "cancel": (contact_custom_field_value(c, cancel_field or "") or "")[:10],
            }
        )

    consolidated = load_consolidated_by_name()
    sheet_rows: list[dict] = []
    for key, row in consolidated.items():
        td = row["termination_date"]
        if td and in_range(td, start, end):
            sheet_rows.append(
                {
                    "key": key,
                    "fuzzy": _fuzzy_name_key(row["name"]),
                    "name": row["name"],
                    "term": td.isoformat(),
                    "level": row["level"],
                }
            )

    used_sheet: set[str] = set()
    matches: list[ChurnMatch] = []

    for g in ghl_rows:
        partner = None
        for s in sheet_rows:
            if s["key"] in used_sheet:
                continue
            if (
                s["key"] == g["key"]
                or s["fuzzy"] == g["fuzzy"]
                or s["key"] in g["key"]
                or g["key"] in s["key"]
            ):
                partner = s
                used_sheet.add(s["key"])
                break
        if partner:
            matches.append(
                ChurnMatch(
                    name=partner["name"],
                    ghl_cancel_date=g["cancel"] or None,
                    sheet_term_date=partner["term"],
                    level=partner["level"],
                    bucket="both",
                )
            )
        else:
            matches.append(
                ChurnMatch(
                    name=g["name"],
                    ghl_cancel_date=g["cancel"] or None,
                    sheet_term_date=None,
                    level="",
                    bucket="ghl_only",
                )
            )

    for s in sheet_rows:
        if s["key"] not in used_sheet:
            matches.append(
                ChurnMatch(
                    name=s["name"],
                    ghl_cancel_date=None,
                    sheet_term_date=s["term"],
                    level=s["level"],
                    bucket="sheet_only",
                )
            )

    overlap = sum(1 for m in matches if m.bucket == "both")
    unique = len(matches)
    return ChurnReconciliation(
        period_start=start,
        period_end=end,
        ghl_count=len(ghl_rows),
        sheet_count=len(sheet_rows),
        unique_count=unique,
        overlap_count=overlap,
        matches=sorted(matches, key=lambda m: (m.bucket, m.name.lower())),
    )


def load_sales_kpi_week(start: date, end: date) -> SalesKpiWeek:
    since, until = start.isoformat(), end.isoformat()
    acq = build_acquisition(start, end)
    churn = reconcile_churn(start, end)

    ml_field = resolve_membership_level_custom_field_id()
    committed = fetch_signup_date_range_committed_yes_contacts(since, until)
    by_level = Counter()
    by_loc = Counter()
    for c in committed["contacts"]:
        by_level[_norm_level(contact_custom_field_value(c, ml_field) if ml_field else "")] += 1
        by_loc[_norm_loc(contact_custom_field_value(c, LOCATION_FIELD_ID))] += 1

    hear = fetch_committed_yes_by_hear_about_us(since, until)
    funnel = count_calendar_funnel_events(since, until)

    ti = acq["total_interest"]
    td = acq["total_discover"]
    ts = acq["total_sign_ups"]
    signups_by_level = {lv: 0 for lv in LEVELS}
    for row in acq["by_level"]:
        if row["level"] in signups_by_level:
            signups_by_level[row["level"]] = row["sign_ups"]

    return SalesKpiWeek(
        start=start,
        end=end,
        new_contacts=ti,
        discover_calls=td,
        sign_ups=ts,
        int_to_disc_pct=_pct(float(td), float(ti)),
        disc_to_sign_pct=_pct(float(ts), float(td)),
        int_to_sign_pct=_pct(float(ts), float(ti)),
        by_level=signups_by_level,
        by_location=dict(by_loc),
        bookings=funnel.bookings,
        meetings=funnel.meetings,
        hear_about=hear.get("rows") or [],
        churn=churn,
    )


def _resolve_chat_user_id(service) -> str:
    for sp in list_all_spaces(service)[:10]:
        resource = sp.get("name") or ""
        if not resource.startswith("spaces/"):
            continue
        sid = resource.split("/", 1)[-1]
        try:
            state = (
                service.users()
                .spaces()
                .getSpaceReadState(name=f"users/me/spaces/{sid}/spaceReadState")
                .execute()
            )
            parts = (state.get("name") or "").split("/")
            if len(parts) >= 2 and parts[0] == "users":
                return parts[1]
        except Exception:
            continue
    return "110731201507276994142"


@dataclass
class ChatMessageRow:
    when: datetime | None
    space: str
    text: str


def fetch_sent_chat_messages(start: date, end: date) -> list[ChatMessageRow]:
    service = chat_service()
    me_id = _resolve_chat_user_id(service)
    me = f"{ME_USER_PREFIX}{me_id}"
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)
    start_filter = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    rows: list[ChatMessageRow] = []
    for sp in list_all_spaces(service):
        resource = sp.get("name") or ""
        if not resource.startswith("spaces/"):
            continue
        display = (sp.get("displayName") or "").strip() or resource
        page_token = None
        while True:
            kwargs: dict = {
                "parent": resource,
                "filter": f'createTime > "{start_filter}"',
                "pageSize": 100,
            }
            if page_token:
                kwargs["pageToken"] = page_token
            try:
                resp = service.spaces().messages().list(**kwargs).execute()
            except Exception:
                break
            for msg in resp.get("messages") or []:
                if (msg.get("sender") or {}).get("name") != me:
                    continue
                ct = _parse_rfc3339(msg.get("createTime"))
                if ct and ct > end_dt:
                    continue
                text = (msg.get("text") or "").replace("\n", " ").strip()
                if not text or text in {"👍", "👍🏻", "👍🏼"}:
                    continue
                if len(text) < 12:
                    continue
                rows.append(ChatMessageRow(when=ct, space=display, text=text[:500]))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    min_dt = datetime.min.replace(tzinfo=timezone.utc)
    rows.sort(key=lambda r: r.when or min_dt, reverse=True)
    return rows


def fetch_sent_emails(start: date, end: date) -> list[GmailMessage]:
    service = gmail_service()
    return fetch_messages_for_query(
        service,
        base_query="in:sent",
        folder="sent",
        start=start,
        end=end,
        max_messages=500,
    )


def _first_to(msg: GmailMessage) -> str:
    match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", msg.to_addrs)
    return match.group(0).lower() if match else msg.to_addrs[:60]


def notable_email_bullets(messages: list[GmailMessage], *, limit: int = 35) -> list[str]:
    """Journal-style bullets from sent mail (action verb, no leading I)."""
    skip_subjects = {
        "sales kpis - last month",
        "sales kpis - this week",
        "missing docs report",
        "jason update",
        "back up screenshots for this week.",
    }
    min_dt = datetime.min.replace(tzinfo=timezone.utc)
    sorted_msgs = sorted(messages, key=lambda m: m.date or min_dt, reverse=True)

    bullets: list[str] = []
    seen_subjects: set[str] = set()
    for msg in sorted_msgs:
        subj = _normalize_subject(msg.subject).casefold()
        if subj in skip_subjects or subj in seen_subjects:
            continue
        if re.search(r"scheduled report for", subj):
            continue
        seen_subjects.add(subj)
        peer = _first_to(msg)
        snippet = (msg.snippet or "").replace("&#39;", "'")[:140].strip()
        line = f"Emailed **{_normalize_subject(msg.subject)}** ({peer})"
        if snippet:
            line += f" — {snippet}"
        bullets.append(line)
        if len(bullets) >= limit:
            break
    return bullets


def notable_chat_bullets(messages: list[ChatMessageRow], *, limit: int = 20) -> list[str]:
    bullets: list[str] = []
    for row in messages[:limit]:
        space = row.space if row.space and not row.space.startswith("spaces/") else "Chat"
        text = row.text[:200]
        bullets.append(f"Posted in **{space}**: {text}")
    return bullets
