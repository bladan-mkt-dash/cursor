"""
Weekly In Review — leadership email (HTML).

Six sections:
  1. Executive summary (≤120 words) — funnel health, next-week priorities, team focus
  2. Milestones & achievements (3–5 auto-detected; override via --notes-file)
  3. Key channel performance — selective WoW highlights from work_week_in_review data
  4. Operations — Monday ops view (Requested / Working on / Reviewed & Approved)
  5. Tech Support — Major Tickets — Gmail threads for #TVL00011603, #TVL00011765, #TVL00011786
  6. Partnerships & vendors — new partner/vendor conversations (Gmail)

Sources:
  - work_week_in_review.py   Sun–Fri funnel snapshot vs prior Sun–Fri
  - monday_ops_view.py       team task boards
  - war_room_data.py         organic social (milestones)
  - activity_summary_report  completed Google Tasks (milestones)
  - tech_support_major_tickets.py  Valley List ticket email chains (Gmail)
  - partnerships_vendors.py  partnership/vendor email threads (Gmail)

    python weekly_in_review_email.py
    python weekly_in_review_email.py --end 2026-06-26 --open

Writes: Op Reports/outputs/weekly_in_review_email_YYYY-MM-DD.html
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import webbrowser
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from _bootstrap import OP_REPORTS_DIR, PROJECT_ROOT, setup

setup()

_MWR_DIR = PROJECT_ROOT / "MWR"
if str(_MWR_DIR) not in sys.path:
    sys.path.insert(0, str(_MWR_DIR))

from activity_summary_report import fetch_completed_tasks  # noqa: E402
from google_tasks_client import tasks_service  # noqa: E402
from monday_ops_view import MondayOpsView, load_monday_ops_view  # noqa: E402
from partnerships_vendors import (  # noqa: E402
    PartnershipsVendorsSection,
    load_partnerships_vendors,
)
from tech_support_major_tickets import (  # noqa: E402
    TechSupportMajorTickets,
    load_tech_support_major_tickets,
)
from war_room_data import OrganicSocialMetrics, load_organic_social  # noqa: E402
from work_week_in_review import (  # noqa: E402
    SourceDeltaRow,
    WorkWeekSnapshot,
    build_interpretation,
    default_end_date,
    load_work_week_snapshot,
    pct_label,
    prior_sunday_friday_period,
    sunday_friday_range,
)

OUTPUT_DIR = OP_REPORTS_DIR / "outputs"

MILESTONE_KEYWORDS = re.compile(
    r"campaign|launch|partnership|partner|brand|collateral|print|media house|rebrand",
    re.IGNORECASE,
)


@dataclass
class NotesSections:
    executive_summary: str = ""
    milestones: list[str] = field(default_factory=list)


@dataclass
class ChannelTable:
    title: str
    headers: list[str]
    rows: list[list[str]]


@dataclass
class WeeklyInReviewEmail:
    period_start: date
    period_end: date
    prior_start: date
    prior_end: date
    executive_summary: str
    executive_word_count: int
    milestones: list[str]
    channel_bullets: list[str]
    channel_tables: list[ChannelTable]
    monday_ops: MondayOpsView
    tech_support: TechSupportMajorTickets
    partnerships: PartnershipsVendorsSection
    errors: list[str] = field(default_factory=list)


def _resolve_path(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else OP_REPORTS_DIR / p


def _strip_md_bold(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


def parse_notes_file(path: Path) -> NotesSections:
    if not path.exists():
        return NotesSections()
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            current = line[3:].strip().casefold()
            sections.setdefault(current, [])
            continue
        if current and line.startswith("- "):
            sections[current].append(_strip_md_bold(line[2:].strip()))
        elif current and line and not line.startswith("#"):
            sections[current].append(_strip_md_bold(line))
    notes = NotesSections()
    for key, bullets in sections.items():
        if "executive" in key or key == "summary":
            notes.executive_summary = " ".join(bullets)
        elif "milestone" in key or "achievement" in key:
            notes.milestones.extend(bullets)
    return notes


def _limit_words(text: str, max_words: int = 120) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]).rstrip(".,;") + "…"


def _word_count(text: str) -> int:
    return len(text.split())


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.0f}"


def _summarize_team_focus(monday: MondayOpsView) -> str:
    if monday.rate_limited:
        return (
            "Team ops data was unavailable (Monday API limit); "
            "re-run when quota resets to include publishing focus."
        )

    working: list[tuple[str, int]] = []
    approved_total = 0
    approved_people: list[str] = []
    for panel in monday.panels:
        for sub in panel.subsections:
            if sub.title == "Working on" and sub.tasks:
                working.append((panel.person, len(sub.tasks)))
            if sub.title == "Reviewed & Approved" and sub.tasks:
                approved_total += len(sub.tasks)
                approved_people.append(panel.person)

    parts: list[str] = []
    if working:
        working.sort(key=lambda x: (-x[1], x[0].casefold()))
        lead = ", ".join(f"{name} ({count})" for name, count in working[:3])
        parts.append(f"active delivery centered on {lead}")
    if approved_total:
        who = ", ".join(sorted(set(approved_people), key=str.casefold))
        parts.append(
            f"{approved_total} item{'s' if approved_total != 1 else ''} cleared review "
            f"({who})"
        )

    if not parts:
        return "Monday boards show light queue activity this week."
    return "Operationally, " + "; ".join(parts) + "."


def _next_week_priorities(snap: WorkWeekSnapshot) -> list[str]:
    priorities: list[str] = []
    sessions_down = snap.sessions < snap.prior_sessions * 0.92
    signups_soft = snap.signups_all <= snap.prior_signups_all
    bookings_down = snap.bookings < snap.prior_bookings
    meetings_up = snap.meetings > snap.prior_meetings

    if sessions_down:
        priorities.append("diagnose and stabilize organic/direct traffic before scaling paid")
    if bookings_down and meetings_up:
        priorities.append("protect meeting-to-signup conversion while refilling the top of the pipe")
    elif bookings_down:
        priorities.append("recover booking volume — check intake and hear-about tagging")
    if signups_soft and not sessions_down:
        priorities.append("tighten close rate and committed-signup follow-through")

    cur_cpa = (
        snap.paid_current.spend / snap.paid_current.combined_leads
        if snap.paid_current.combined_leads
        else None
    )
    prior_cpa = (
        snap.paid_prior.spend / snap.paid_prior.combined_leads
        if snap.paid_prior.combined_leads
        else None
    )
    if cur_cpa and prior_cpa and cur_cpa > prior_cpa * 1.12:
        priorities.append("review paid creative and landing paths — CPA drifted up")

    if not priorities:
        priorities.append("maintain momentum and ship the publishing backlog")
    return priorities[:2]


def build_executive_summary(
    snap: WorkWeekSnapshot,
    monday: MondayOpsView,
    *,
    override: str = "",
) -> str:
    if override.strip():
        return _limit_words(override.strip(), 120)

    s_pct = pct_label(snap.sessions, snap.prior_sessions)
    b_pct = pct_label(snap.bookings, snap.prior_bookings)
    m_pct = pct_label(snap.meetings, snap.prior_meetings)
    su_pct = pct_label(snap.signups_all, snap.prior_signups_all)

    sessions_down = snap.sessions < snap.prior_sessions * 0.92
    signups_soft = snap.signups_all <= snap.prior_signups_all
    bookings_down = snap.bookings < snap.prior_bookings
    meetings_up = snap.meetings > snap.prior_meetings

    if sessions_down and signups_soft:
        health = (
            f"This Sun–Fri window was soft upstream: sessions {s_pct}, bookings {b_pct}, "
            f"signups {su_pct} vs the prior week."
        )
    elif not bookings_down and not signups_soft:
        health = (
            f"Funnel held or improved — bookings {b_pct}, meetings {m_pct}, signups {su_pct} "
            f"on {snap.sessions:,} sessions ({s_pct})."
        )
    elif meetings_up and bookings_down:
        health = (
            f"Mixed week: meetings rose {m_pct} but bookings {b_pct} and signups {su_pct}; "
            f"sessions {s_pct}."
        )
    else:
        health = (
            f"Bookings {b_pct}, meetings {m_pct}, signups {su_pct}, sessions {s_pct} "
            f"vs prior Sun–Fri."
        )

    prios = _next_week_priorities(snap)
    priority_sentence = (
        f"Next week: {prios[0]}"
        + (f"; {prios[1]}" if len(prios) > 1 else "")
        + "."
    )
    team = _summarize_team_focus(monday)

    return _limit_words(f"{health} {priority_sentence} {team}", 120)


def _load_completed_tasks(*, start: date, end: date) -> tuple[list[tuple[str, list[str]]], list[str]]:
    errors: list[str] = []
    try:
        tasks = fetch_completed_tasks(tasks_service(), start=start, end=end)
    except Exception as exc:
        return [], [f"Google Tasks: {exc}"]
    grouped: dict[str, list[tuple[date, str]]] = defaultdict(list)
    for task in tasks:
        grouped[task.list_name].append((task.completed_date, task.title))
    sections: list[tuple[str, list[str]]] = []
    for list_name in sorted(grouped, key=str.casefold):
        rows = sorted(grouped[list_name], key=lambda r: (r[0], r[1].casefold()))
        bullets = [f"{title} ({completed.strftime('%b %d')})" for completed, title in rows]
        sections.append((list_name, bullets))
    return sections, errors


def _load_organic(*, start: date, end: date) -> tuple[OrganicSocialMetrics | None, list[str]]:
    try:
        return load_organic_social(period_start=start, period_end=end, as_of=end), []
    except Exception as exc:
        return None, [f"Organic social: {exc}"]


def build_milestones(
    snap: WorkWeekSnapshot,
    monday: MondayOpsView,
    completed_tasks: list[tuple[str, list[str]]],
    organic: OrganicSocialMetrics | None,
    *,
    override: list[str] | None = None,
) -> list[str]:
    if override:
        return override[:5]

    scored: list[tuple[int, str]] = []

    cur_cpa = (
        snap.paid_current.spend / snap.paid_current.combined_leads
        if snap.paid_current.combined_leads
        else None
    )
    prior_cpa = (
        snap.paid_prior.spend / snap.paid_prior.combined_leads
        if snap.paid_prior.combined_leads
        else None
    )
    if cur_cpa and prior_cpa and cur_cpa < prior_cpa * 0.9:
        drop = (prior_cpa - cur_cpa) / prior_cpa * 100
        scored.append(
            (
                85,
                f"Paid efficiency win: blended CPA improved ~{drop:.0f}% "
                f"({_fmt_money(prior_cpa)} → {_fmt_money(cur_cpa)}/lead) on "
                f"{pct_label(snap.paid_current.spend, snap.paid_prior.spend)} spend.",
            )
        )
    elif cur_cpa and prior_cpa and cur_cpa > prior_cpa * 1.15:
        rise = (cur_cpa - prior_cpa) / prior_cpa * 100
        scored.append(
            (
                50,
                f"Paid CPA rose ~{rise:.0f}% WoW — worth a creative/landing-page review "
                f"({_fmt_money(prior_cpa)} → {_fmt_money(cur_cpa)}/lead).",
            )
        )

    for row in snap.signups_by_source:
        if row.source.casefold() == "google" and row.prior > 0 and row.current >= row.prior * 1.2:
            scored.append(
                (
                    78,
                    f"Google committed signups strengthened ({row.prior} → {row.current}, "
                    f"{pct_label(row.current, row.prior)}) — paid intake converting better at close.",
                )
            )

    if snap.meetings > snap.prior_meetings * 1.08:
        scored.append(
            (
                65,
                f"Meeting volume increased to {snap.meetings:,} "
                f"({pct_label(snap.meetings, snap.prior_meetings)}), keeping mid-funnel activity up.",
            )
        )

    if organic and organic.ig_reach_7d and organic.ig_engagement_7d:
        posts = organic.posts_in_period or 0
        if organic.ig_engagement_7d >= 30 or (posts >= 3 and organic.ig_reach_7d >= 800):
            scored.append(
                (
                    60,
                    f"Organic social activity: {organic.ig_reach_7d:,} IG reach, "
                    f"{organic.ig_engagement_7d:,} engagements across {posts} posts.",
                )
            )
        if organic.top_post and (organic.top_post_engagement or 0) >= 20:
            scored.append(
                (
                    58,
                    f"Standout social post: “{organic.top_post}” "
                    f"({organic.top_post_engagement:,} engagements).",
                )
            )

    approved_samples: list[str] = []
    for panel in monday.panels:
        for sub in panel.subsections:
            if sub.title == "Reviewed & Approved":
                for task in sub.tasks[:2]:
                    short = task.split("(", 1)[0].strip()
                    approved_samples.append(f"{panel.person}: {short}")
    if approved_samples:
        scored.append(
            (
                72,
                f"Publishing pipeline: {len(approved_samples)}+ deliverables cleared review "
                f"({'; '.join(approved_samples[:2])}{'…' if len(approved_samples) > 2 else ''}).",
            )
        )

    for _list_name, bullets in completed_tasks:
        for bullet in bullets:
            title = bullet.rsplit("(", 1)[0].strip()
            if MILESTONE_KEYWORDS.search(title):
                scored.append((80, f"Completed: {title}."))

    if snap.signups_all >= snap.prior_signups_all and snap.bookings >= snap.prior_bookings:
        scored.append(
            (
                55,
                f"Core funnel metrics held or improved — bookings {pct_label(snap.bookings, snap.prior_bookings)}, "
                f"signups {pct_label(snap.signups_all, snap.prior_signups_all)}.",
            )
        )

    scored.sort(key=lambda item: (-item[0], item[1]))
    seen: set[str] = set()
    results: list[str] = []
    for _, text in scored:
        key = text[:50].casefold()
        if key in seen:
            continue
        seen.add(key)
        results.append(text)
        if len(results) >= 5:
            break

    while len(results) < 3:
        fillers = [
            f"Paid generated {snap.paid_current.combined_leads:.0f} leads on "
            f"{_fmt_money(snap.paid_current.spend)} spend ({pct_label(snap.paid_current.combined_leads, snap.paid_prior.combined_leads)} vs prior).",
            f"GA4 recorded {snap.sessions:,} sessions ({pct_label(snap.sessions, snap.prior_sessions)} WoW).",
            "Review Section 3 channel tables and edit milestones manually if something strategic shipped off-dashboard.",
        ]
        for line in fillers:
            if line not in results and len(results) < 3:
                results.append(line)

    return results[:5]


def _significant_rows(
    rows: list[SourceDeltaRow],
    *,
    min_abs_delta: int = 2,
    min_pct: float = 15.0,
) -> list[SourceDeltaRow]:
    picked: list[SourceDeltaRow] = []
    for row in rows:
        pct = row.pct_change
        if row.prior == 0 and row.current > 0 and row.current >= min_abs_delta:
            picked.append(row)
        elif abs(row.delta) >= min_abs_delta and pct is not None and abs(pct) >= min_pct:
            picked.append(row)
        elif abs(row.delta) >= max(min_abs_delta * 2, 5):
            picked.append(row)
    return picked[:8]


def _delta_table(title: str, rows: list[SourceDeltaRow], *, total_cur: int, total_prior: int) -> ChannelTable | None:
    sig = _significant_rows(rows)
    if not sig:
        return None
    headers = ["Source", "This week", "Prior week", "Δ", "Change"]
    body: list[list[str]] = []
    for row in sig:
        body.append(
            [
                row.source,
                f"{row.current:,}",
                f"{row.prior:,}",
                f"{row.delta:+,}",
                pct_label(row.current, row.prior),
            ]
        )
    body.append(
        [
            "Total",
            f"{total_cur:,}",
            f"{total_prior:,}",
            f"{total_cur - total_prior:+,}",
            pct_label(total_cur, total_prior),
        ]
    )
    return ChannelTable(title=title, headers=headers, rows=body)


def build_channel_section(snap: WorkWeekSnapshot) -> tuple[list[str], list[ChannelTable]]:
    bullets: list[str] = []

    for block in build_interpretation(snap):
        for line in block.splitlines():
            cleaned = _strip_md_bold(line.strip().lstrip("- "))
            if not cleaned or cleaned.endswith(":"):
                continue
            if cleaned.casefold() in {"primary drivers", "worth watching", "what is holding"}:
                continue
            bullets.append(cleaned)

    cur_cpa = (
        snap.paid_current.spend / snap.paid_current.combined_leads
        if snap.paid_current.combined_leads
        else None
    )
    prior_cpa = (
        snap.paid_prior.spend / snap.paid_prior.combined_leads
        if snap.paid_prior.combined_leads
        else None
    )
    bullets.append(
        f"Paid: Google {snap.paid_current.google_leads:.0f} leads vs "
        f"{snap.paid_prior.google_leads:.0f} prior; Meta {snap.paid_current.meta_leads:.0f} vs "
        f"{snap.paid_prior.meta_leads:.0f}; spend {_fmt_money(snap.paid_current.spend)} "
        f"({pct_label(snap.paid_current.spend, snap.paid_prior.spend)})."
    )
    if cur_cpa and prior_cpa:
        bullets.append(
            f"Blended CPA {_fmt_money(cur_cpa)} vs {_fmt_money(prior_cpa)} prior "
            f"({pct_label(cur_cpa, prior_cpa)})."
        )

    headline = ChannelTable(
        title="Headline funnel metrics",
        headers=["Metric", "This week", "Prior week", "Change"],
        rows=[
            ["Bookings", f"{snap.bookings:,}", f"{snap.prior_bookings:,}", pct_label(snap.bookings, snap.prior_bookings)],
            ["Meetings", f"{snap.meetings:,}", f"{snap.prior_meetings:,}", pct_label(snap.meetings, snap.prior_meetings)],
            ["Signups (all)", f"{snap.signups_all:,}", f"{snap.prior_signups_all:,}", pct_label(snap.signups_all, snap.prior_signups_all)],
            ["Signups (committed)", f"{snap.signups_committed:,}", f"{snap.prior_signups_committed:,}", pct_label(snap.signups_committed, snap.prior_signups_committed)],
            ["GA4 sessions", f"{snap.sessions:,}", f"{snap.prior_sessions:,}", pct_label(snap.sessions, snap.prior_sessions)],
            ["Paid leads", f"{snap.paid_current.combined_leads:.0f}", f"{snap.paid_prior.combined_leads:.0f}", pct_label(snap.paid_current.combined_leads, snap.paid_prior.combined_leads)],
        ],
    )

    tables: list[ChannelTable] = [headline]
    for title, rows, total_cur, total_prior in [
        ("Bookings by hear-about (significant moves)", snap.bookings_by_source, snap.bookings, snap.prior_bookings),
        ("Committed signups by hear-about (significant moves)", snap.signups_by_source, snap.signups_committed, snap.prior_signups_committed),
        ("GA4 sessions by channel (significant moves)", snap.sessions_by_channel, snap.sessions, snap.prior_sessions),
    ]:
        table = _delta_table(title, rows, total_cur=total_cur, total_prior=total_prior)
        if table:
            tables.append(table)

    return bullets[:8], tables


def load_weekly_in_review_email(
    *,
    end: date,
    notes: NotesSections | None = None,
) -> WeeklyInReviewEmail:
    notes = notes or NotesSections()
    period_start, period_end = sunday_friday_range(end=end)
    prior_start, prior_end = prior_sunday_friday_period(period_start, period_end)
    errors: list[str] = []

    print(f"Loading work-week funnel {period_start} .. {period_end} …")
    snap = load_work_week_snapshot(start=period_start, end=period_end)
    errors.extend(snap.errors)

    print("Loading Monday ops boards …")
    monday = load_monday_ops_view()
    errors.extend(monday.errors)

    print("Loading organic social + completed tasks …")
    organic, organic_errors = _load_organic(start=period_start, end=period_end)
    errors.extend(organic_errors)
    completed_tasks, task_errors = _load_completed_tasks(start=period_start, end=period_end)
    errors.extend(task_errors)

    print("Loading Tech Support major ticket threads …")
    tech_support = load_tech_support_major_tickets(
        period_start=period_start,
        period_end=period_end,
    )
    errors.extend(tech_support.errors)

    print("Loading partnerships & vendor threads …")
    partnerships = load_partnerships_vendors(
        period_start=period_start,
        period_end=period_end,
    )
    errors.extend(partnerships.errors)

    executive = build_executive_summary(
        snap, monday, override=notes.executive_summary
    )
    milestones = build_milestones(
        snap,
        monday,
        completed_tasks,
        organic,
        override=notes.milestones or None,
    )
    channel_bullets, channel_tables = build_channel_section(snap)

    return WeeklyInReviewEmail(
        period_start=period_start,
        period_end=period_end,
        prior_start=prior_start,
        prior_end=prior_end,
        executive_summary=executive,
        executive_word_count=_word_count(executive),
        milestones=milestones,
        channel_bullets=channel_bullets,
        channel_tables=channel_tables,
        monday_ops=monday,
        tech_support=tech_support,
        partnerships=partnerships,
        errors=errors,
    )


def _render_table(table: ChannelTable) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in table.headers)
    body_rows = ""
    for row in table.rows:
        cells = "".join(f"<td>{html.escape(c)}</td>" for c in row)
        is_total = row and row[0].casefold() == "total"
        body_rows += f"<tr{' class=\"total-row\"' if is_total else ''}>{cells}</tr>"
    return f"""
<h3>{html.escape(table.title)}</h3>
<table>
  <thead><tr>{head}</tr></thead>
  <tbody>{body_rows}</tbody>
</table>
"""


def _render_ops_section(monday: MondayOpsView) -> str:
    parts: list[str] = []
    if monday.rate_limited:
        parts.append(
            "<p class='note'><em>Monday.com daily API limit reached — task lists are "
            "placeholders until quota resets.</em></p>"
        )

    for panel in monday.panels:
        sub_html = ""
        for sub in panel.subsections:
            if sub.tasks:
                items = "".join(f"<li>{html.escape(t)}</li>" for t in sub.tasks)
                body = f"<ul>{items}</ul>"
            else:
                body = "<p class='empty'><em>None right now.</em></p>"
            sub_html += f"""
            <div class="ops-sub">
              <h4>{html.escape(sub.title)} <span class="count">({len(sub.tasks)})</span></h4>
              {body}
            </div>
            """
        parts.append(
            f"""
        <div class="ops-person">
          <h3>{html.escape(panel.person)}</h3>
          {sub_html}
        </div>
        """
        )
    return "".join(parts)


def _render_tech_support_section(tech_support: TechSupportMajorTickets) -> str:
    if not tech_support.tickets:
        return "<p class='empty'>No major ticket threads loaded.</p>"

    parts: list[str] = []
    for ticket in tech_support.tickets:
        bullets = "".join(
            f"<li>{html.escape(b)}</li>" for b in ticket.bullets
        )
        meta = (
            f"Status: {html.escape(ticket.status)} · "
            f"{ticket.period_messages} this week · {ticket.total_messages} in thread"
        )
        if ticket.last_activity:
            meta += f" · last activity {ticket.last_activity:%b %d, %Y}"
        parts.append(
            f"""
        <div class="ticket-block">
          <h3>{html.escape(ticket.ticket_id)} — {html.escape(ticket.subject)}</h3>
          <p class="ticket-meta">{meta}</p>
          <ul>{bullets}</ul>
        </div>
        """
        )
    return "".join(parts)


def _render_partnerships_section(partnerships: PartnershipsVendorsSection) -> str:
    if not partnerships.items:
        return "<p class='empty'>No partnership or vendor threads loaded.</p>"

    parts: list[str] = []
    for item in partnerships.items:
        bullets = "".join(f"<li>{html.escape(b)}</li>" for b in item.bullets)
        meta = f"Status: {html.escape(item.status)} · {item.period_messages} this week"
        if item.last_activity:
            meta += f" · last activity {item.last_activity:%b %d, %Y}"
        parts.append(
            f"""
        <div class="ticket-block">
          <h3>{html.escape(item.name)}</h3>
          <p class="ticket-meta">{meta}</p>
          <ul>{bullets}</ul>
        </div>
        """
        )
    return "".join(parts)


def render_weekly_in_review_html(report: WeeklyInReviewEmail) -> str:
    title = (
        f"Weekly In Review — {report.period_start.strftime('%b %d')}–"
        f"{report.period_end.strftime('%b %d, %Y')}"
    )

    milestones_html = "".join(
        f"<li>{html.escape(m)}</li>" for m in report.milestones
    )
    channel_bullets_html = "".join(
        f"<li>{html.escape(b)}</li>" for b in report.channel_bullets
    )
    tables_html = "".join(_render_table(t) for t in report.channel_tables)
    ops_html = _render_ops_section(report.monday_ops)
    tech_support_html = _render_tech_support_section(report.tech_support)
    partnerships_html = _render_partnerships_section(report.partnerships)

    errors_html = ""
    if report.errors:
        errors_html = f"<p class='errors'>{html.escape('; '.join(report.errors[:4]))}</p>"

    period_note = (
        f"Sun–Fri {report.period_start.isoformat()} → {report.period_end.isoformat()} "
        f"vs prior {report.prior_start.isoformat()} → {report.prior_end.isoformat()}. "
        f"Funnel from work_week_in_review · ops from monday_ops_view · "
        f"tech tickets from Gmail · partnerships from Gmail (Help Without Hassle, Red Beard Consulting)."
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: Arial, sans-serif; font-size: 10pt; margin: 16px; line-height: 1.5; color: #222; max-width: 720px; }}
h2 {{ font-size: 11pt; font-weight: bold; margin: 22px 0 8px 0; border-bottom: 1px solid #ccc; padding-bottom: 4px; color: #264540; }}
h3 {{ font-size: 10pt; font-weight: bold; margin: 14px 0 6px 0; }}
h4 {{ font-size: 9.5pt; font-weight: bold; margin: 0 0 4px 0; color: #5DA68A; }}
p {{ margin: 0 0 12px 0; }}
ul {{ margin: 0 0 12px 0; padding-left: 20px; }}
li {{ margin-bottom: 5px; }}
.meta {{ font-size: 9pt; color: #666; margin-bottom: 16px; }}
.exec {{ font-size: 10.5pt; margin-bottom: 8px; line-height: 1.55; }}
.exec-meta {{ font-size: 8.5pt; color: #888; margin-bottom: 16px; }}
table {{ border-collapse: collapse; width: 100%; margin: 8px 0 16px 0; font-size: 9pt; }}
th, td {{ border: 1px solid #dde3ea; padding: 5px 8px; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }}
th {{ background: #f6f8fa; font-weight: bold; }}
tr.total-row td {{ font-weight: bold; background: #fafbfc; }}
.ops-person {{ margin-bottom: 18px; padding-bottom: 14px; border-bottom: 1px solid #e8ecef; }}
.ops-person:last-child {{ border-bottom: none; }}
.ops-sub {{ margin-bottom: 10px; padding-left: 4px; }}
.ticket-block {{ margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid #e8ecef; }}
.ticket-block:last-child {{ border-bottom: none; }}
.ticket-meta {{ font-size: 9pt; color: #666; margin: 0 0 8px 0; }}
.count {{ font-weight: normal; color: #888; font-size: 9pt; }}
.empty {{ margin: 0; color: #888; font-size: 9pt; }}
.note {{ font-size: 9pt; color: #666; }}
.errors {{ color: #a00; font-size: 9pt; }}
</style>
</head>
<body>

<h2>1. Executive summary</h2>
<p class="exec">{html.escape(report.executive_summary)}</p>
<p class="exec-meta">{report.executive_word_count} words · target ≤120</p>
<p class="meta">{html.escape(period_note)}</p>

<h2>2. Milestones &amp; achievements</h2>
<ul>{milestones_html}</ul>
<p class="note"><em>Auto-detected — edit manually in --notes-file (## Milestones) if needed.</em></p>

<h2>3. Key channel performance</h2>
<ul>{channel_bullets_html}</ul>
{tables_html}

<h2>4. Operations</h2>
{ops_html}

<h2>5. Tech Support — Major Tickets</h2>
{tech_support_html}
<p class="note"><em>Summarized from Gmail threads matching #TVL00011603, #TVL00011765, and #TVL00011786.</em></p>

<h2>6. Partnerships &amp; vendors</h2>
{partnerships_html}
<p class="note"><em>Summarized from Gmail (Help Without Hassle, Red Beard Consulting). Edit manually where noted.</em></p>

{errors_html}

</body>
</html>
"""


def write_weekly_in_review_email(
    report: WeeklyInReviewEmail,
    *,
    output_dir: Path | None = None,
) -> Path:
    out_dir = output_dir or OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"weekly_in_review_email_{report.period_end.isoformat()}.html"
    path.write_text(render_weekly_in_review_html(report), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Weekly in-review email — funnel + milestones + channels + ops + tech + partnerships"
    )
    parser.add_argument(
        "--end",
        type=str,
        default="",
        help="Period end YYYY-MM-DD (default: Friday on Sat runs, else today)",
    )
    parser.add_argument(
        "--notes-file",
        type=str,
        default="",
        help="Optional markdown (## Executive summary, ## Milestones)",
    )
    parser.add_argument("--open", action="store_true", help="Open HTML in browser")
    parser.add_argument("--output-dir", type=str, default="")
    args = parser.parse_args()

    end = date.fromisoformat(args.end) if args.end else default_end_date()

    notes = NotesSections()
    if args.notes_file:
        notes = parse_notes_file(_resolve_path(args.notes_file))
        print(f"Loaded notes from {args.notes_file}")

    report = load_weekly_in_review_email(end=end, notes=notes)
    out_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR
    path = write_weekly_in_review_email(report, output_dir=out_dir)
    print(f"Wrote {path}")
    print(f"  Executive summary: {report.executive_word_count} words")
    print(f"  Milestones: {len(report.milestones)}")

    if args.open:
        webbrowser.open(path.resolve().as_uri())


if __name__ == "__main__":
    main()
