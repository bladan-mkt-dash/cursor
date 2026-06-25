"""
Weekly In Review — unified leadership email (stats + interpretation + operations).

Combines:
  - work_week_in_review.py   (Sun–Fri funnel WoW)
  - pulse_weekly_report.py   (narrative, recommendations, seasonality)
  - activity_summary_report.py (milestones, completed tasks, vendor email)
  - weekly_leadership_report.py (--notes-file for manual milestones / narrative)

Run every Saturday (or any day) from Op Reports:

    python weekly_in_review_email.py
    python weekly_in_review_email.py --end 2026-06-13 --open
    python weekly_in_review_email.py --notes-file inputs/weekly_notes_2026-06-05.md

Writes: Op Reports/outputs/weekly_in_review_email_YYYY-MM-DD.html
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import webbrowser
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from _bootstrap import OP_REPORTS_DIR, PROJECT_ROOT, setup

setup()

_MWR_DIR = PROJECT_ROOT / "MWR"
if str(_MWR_DIR) not in sys.path:
    sys.path.insert(0, str(_MWR_DIR))

from activity_summary_report import load_activity_summary_report  # noqa: E402
from pulse_weekly_report import load_pulse_weekly_report  # noqa: E402
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
INPUTS_DIR = OP_REPORTS_DIR / "inputs"


@dataclass
class NotesSections:
    milestones: list[str] = field(default_factory=list)
    funnel_narrative: str = ""
    next_week: list[str] = field(default_factory=list)
    extra_ops: list[tuple[str, list[str]]] = field(default_factory=list)


@dataclass
class WeeklyInReviewEmail:
    period_start: date
    period_end: date
    prior_start: date
    prior_end: date
    opening: str
    milestones: list[tuple[str, str]]
    funnel_narrative: str
    summary_bullets: list[str]
    next_week: list[str]
    funnel_snapshot: WorkWeekSnapshot
    ops_sections: list[tuple[str, list[str]]]
    vendor_lines: list[str]
    operational_work_groups: list[tuple[str, list[str]]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _resolve_path(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else OP_REPORTS_DIR / p


def _strip_md_bold(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


def parse_notes_file(path: Path) -> NotesSections:
    """Parse optional ## sections from a notes markdown file."""
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

    notes = NotesSections()
    for key, bullets in sections.items():
        if "milestone" in key:
            notes.milestones.extend(bullets)
        elif "funnel" in key or "narrative" in key or "deep dive" in key:
            notes.funnel_narrative = " ".join(bullets)
        elif "next week" in key:
            notes.next_week.extend(bullets)
        elif bullets:
            title = key.title()
            notes.extra_ops.append((title, bullets))
    return notes


def _find_source(rows: list[SourceDeltaRow], *names: str) -> SourceDeltaRow | None:
    targets = {n.casefold() for n in names}
    for row in rows:
        if row.source.casefold() in targets:
            return row
    return None


def _funnel_one_liner(snap: WorkWeekSnapshot) -> str:
    nc_chg = pct_label(snap.new_contacts, snap.prior_new_contacts)
    spend_chg = pct_label(snap.paid_current.spend, snap.paid_prior.spend)
    paid_chg = pct_label(snap.paid_current.combined_leads, snap.paid_prior.combined_leads)
    wom_meet = _find_source(snap.meetings_by_source, "WOM", "Word of mouth")
    organic = _find_source(snap.sessions_by_channel, "Organic Search")

    growth_stages = sum(
        [
            snap.new_contacts > snap.prior_new_contacts,
            snap.paid_current.combined_leads > snap.paid_prior.combined_leads,
            snap.meetings > snap.prior_meetings,
            snap.signups_all > snap.prior_signups_all,
        ]
    )

    parts: list[str] = []
    if growth_stages >= 3:
        parts.append(
            f"Funnel mostly up WoW — contacts {nc_chg}, paid leads {paid_chg}, "
            f"meetings {pct_label(snap.meetings, snap.prior_meetings)}, "
            f"signups {pct_label(snap.signups_all, snap.prior_signups_all)} "
            f"on {spend_chg} spend."
        )
    elif snap.new_contacts >= snap.prior_new_contacts:
        parts.append(
            f"Contacts {nc_chg}; paid leads {paid_chg} on {spend_chg} spend."
        )
    else:
        parts.append(
            f"Contacts {snap.new_contacts:,} ({nc_chg}); paid leads "
            f"{snap.paid_current.combined_leads:.0f} ({paid_chg})."
        )

    if wom_meet and wom_meet.delta < 0:
        parts.append(
            f"WOM meetings down {pct_label(wom_meet.current, wom_meet.prior)} — dragging signups."
        )
    elif snap.signups_all < snap.prior_signups_all:
        parts.append(f"Signups down {pct_label(snap.signups_all, snap.prior_signups_all)}.")

    if snap.bookings < snap.prior_bookings:
        if organic and organic.delta < 0:
            parts.append(
                f"Bookings {pct_label(snap.bookings, snap.prior_bookings)}; organic sessions "
                f"{pct_label(organic.current, organic.prior)}. Top-of-funnel softness, not conversion."
            )
        else:
            parts.append(
                f"Bookings {pct_label(snap.bookings, snap.prior_bookings)} despite growth elsewhere."
            )
    return " ".join(parts)


def _summary_bullets(snap: WorkWeekSnapshot, pulse_blocks: dict[str, list[str]]) -> list[str]:
    bullets: list[str] = [_funnel_one_liner(snap)]

    wom_sig = _find_source(snap.signups_by_source, "WOM", "Word of mouth")
    if wom_sig and wom_sig.delta < 0:
        total_drop = snap.signups_all - snap.prior_signups_all
        bullets.append(
            f"Signups fell on volume and close rate. WOM drove most of the drop "
            f"({wom_sig.delta:+} committed signups"
            f"{f' = half the total decline' if total_drop and abs(wom_sig.delta) >= abs(total_drop) // 2 else ''}). "
            f"Google signups {'improved' if _find_source(snap.signups_by_source, 'Google') and _find_source(snap.signups_by_source, 'Google').delta >= 0 else 'held'} while everything else softened."
        )

    wom_meet = _find_source(snap.meetings_by_source, "WOM", "Word of mouth")
    if wom_meet and wom_meet.delta < 0:
        bullets.append(
            "Meeting volume fell mainly on referral/WOM, not paid search. "
            "Google meetings were flat-to-up while WOM dropped sharply."
        )

    if snap.paid_current.spend < snap.paid_prior.spend and snap.paid_current.combined_leads >= snap.paid_prior.combined_leads * 0.9:
        bullets.append(
            "Paid is doing more with less — lead volume held despite lower spend."
        )

    organic = _find_source(snap.sessions_by_channel, "Organic Search")
    if (
        snap.bookings < snap.prior_bookings
        and organic
        and organic.delta < 0
        and not any("organic" in b.casefold() for b in bullets)
    ):
        bullets.append(
            f"Organic search {pct_label(organic.current, organic.prior)} "
            f"({organic.prior:,} → {organic.current:,}) — watch volume into July."
        )

    for block in pulse_blocks.get("What needs attention", []):
        if "June gloom" in block or "seasonality" in block.lower():
            bullets.append(block)

    return bullets


def _auto_funnel_narrative(
    snap: WorkWeekSnapshot,
    pulse_exec: str,
    interpretation: list[str],
) -> str:
    organic = _find_source(snap.sessions_by_channel, "Organic Search")
    growth_stages = sum(
        [
            snap.new_contacts > snap.prior_new_contacts,
            snap.paid_current.combined_leads > snap.paid_prior.combined_leads,
            snap.meetings > snap.prior_meetings,
            snap.signups_all > snap.prior_signups_all,
        ]
    )
    bookings_down = snap.bookings < snap.prior_bookings
    bookings_flat = snap.bookings == snap.prior_bookings

    nc = pct_label(snap.new_contacts, snap.prior_new_contacts)
    paid = pct_label(snap.paid_current.combined_leads, snap.paid_prior.combined_leads)
    meet = pct_label(snap.meetings, snap.prior_meetings)
    sig = pct_label(snap.signups_all, snap.prior_signups_all)
    book = pct_label(snap.bookings, snap.prior_bookings)
    spend = pct_label(snap.paid_current.spend, snap.paid_prior.spend)

    sentences: list[str] = []

    if growth_stages >= 3:
        sentences.append(
            f"This was a constructive week through most of the funnel. New contacts ({nc}), "
            f"paid leads ({paid}), meetings ({meet}), and signups ({sig}) all grew week over "
            f"week on {spend} ad spend."
        )
    elif snap.new_contacts >= snap.prior_new_contacts:
        sentences.append(
            f"New contacts grew ({nc}) and paid leads moved ({paid}) on {spend} spend, "
            f"but downstream stages were mixed."
        )
    else:
        sentences.append(
            f"The funnel softened week over week, with bookings ({book}) and new contacts "
            f"({nc}) both under pressure."
        )

    if bookings_down:
        sentences.append(
            f"Bookings fell to {snap.bookings:,} ({book}). That looks more like a lag from "
            f"softer top-of-funnel traffic than a mid-funnel conversion breakdown."
        )
    elif bookings_flat and growth_stages >= 2:
        sentences.append(
            f"Bookings held flat at {snap.bookings:,} ({book}), which usually trails traffic "
            f"by one to two weeks rather than reflecting this week's funnel activity."
        )
    elif growth_stages >= 2:
        sentences.append(
            f"Bookings came in at {snap.bookings:,} ({book}), so mid-funnel conversion appears "
            f"to be holding even as more volume moves through the pipeline."
        )

    organic_soft = (
        organic
        and organic.prior > 0
        and (organic.delta <= -40 or organic.current / organic.prior <= 0.97)
    )
    organic_strong = organic and organic.prior > 0 and organic.delta >= 50

    if organic_soft:
        sentences.append(
            f"Organic search eased to {organic.current:,} sessions from {organic.prior:,} "
            f"({pct_label(organic.current, organic.prior)}) — watch that trend weekly; "
            f"traffic is the leading indicator for bookings."
        )
    elif organic_strong:
        sentences.append(
            f"Organic search strengthened ({pct_label(organic.current, organic.prior)}), "
            f"which should support bookings over the next one to two weeks if the trend holds."
        )
    elif bookings_down or growth_stages >= 2:
        sentences.append(
            "Heading into July, the priority is building traffic and awareness at the top of "
            "the pipeline rather than tuning mid-funnel mechanics."
        )

    if "June gloom" in pulse_exec and len(sentences) < 3:
        sentences.append(
            "June is historically our slowest month, so read the data in that context before "
            "making sharp changes."
        )

    text = " ".join(sentences[:3])
    if len(text.split()) > 78:
        text = " ".join(sentences[:2])
    return text


def _cross_period_lines(snap: WorkWeekSnapshot) -> list[str]:
    return [
        f"New Contacts {snap.new_contacts:,} ({pct_label(snap.new_contacts, snap.prior_new_contacts)})",
        f"↓ Paid Leads {snap.paid_current.combined_leads:.0f} ({pct_label(snap.paid_current.combined_leads, snap.paid_prior.combined_leads)})",
        f"↓ Meetings {snap.meetings:,} ({pct_label(snap.meetings, snap.prior_meetings)})",
        f"↓ Sign Ups {snap.signups_all:,} ({pct_label(snap.signups_all, snap.prior_signups_all)})",
        f"Bookings (separate) {snap.bookings:,} ({pct_label(snap.bookings, snap.prior_bookings)})",
    ]


def _stage_direction_table(snap: WorkWeekSnapshot) -> list[tuple[str, str, str]]:
    wom_meet = _find_source(snap.meetings_by_source, "WOM", "Word of mouth")
    rows: list[tuple[str, str, str]] = []

    nc_dir = "Up" if snap.new_contacts >= snap.prior_new_contacts else "Down"
    nc_driver = f"Broad intake growth ({snap.new_contacts - snap.prior_new_contacts:+})"
    rows.append(("New Contacts", nc_dir, nc_driver))

    leads_dir = "Up" if snap.paid_current.combined_leads >= snap.paid_prior.combined_leads else "Down"
    g_chg = pct_label(snap.paid_current.google_leads, snap.paid_prior.google_leads)
    m_chg = pct_label(snap.paid_current.meta_leads, snap.paid_prior.meta_leads)
    rows.append(("Leads", leads_dir, f"Google paid {g_chg}; Meta {m_chg}"))

    meet_dir = "Down" if snap.meetings < snap.prior_meetings else "Up"
    meet_driver = (
        f"WOM ({wom_meet.delta:+} meetings)"
        if wom_meet and wom_meet.delta < 0
        else "Mixed by source"
    )
    rows.append(("Meetings", meet_dir, meet_driver))

    sig_dir = "Down" if snap.signups_all < snap.prior_signups_all else "Up"
    wom_sig = _find_source(snap.signups_by_source, "WOM", "Word of mouth")
    if snap.signups_all >= snap.prior_signups_all:
        sig_driver = "Volume up across sources"
    elif wom_sig and wom_sig.delta < 0:
        sig_driver = f"WOM signups ({wom_sig.delta:+})"
    else:
        sig_driver = "Mixed by source"
    rows.append(("Sign Ups", sig_dir, sig_driver))

    book_dir = "Down" if snap.bookings < snap.prior_bookings else "Up"
    organic = _find_source(snap.sessions_by_channel, "Organic Search")
    if book_dir == "Down" and organic and organic.delta < 0:
        book_driver = (
            f"Organic traffic softer ({organic.delta:+,} sessions); bookings lag traffic"
        )
    else:
        wom_book = _find_source(snap.bookings_by_source, "WOM", "Word of mouth")
        book_driver = (
            f"WOM ({wom_book.delta:+})"
            if wom_book and wom_book.delta < 0
            else "Mixed by source"
        )
    rows.append(("Bookings", book_dir, book_driver))
    return rows


def _source_mover_table(
    rows: list[SourceDeltaRow],
    *,
    limit: int = 4,
) -> list[tuple[str, int, int, str]]:
    movers = sorted(rows, key=lambda r: abs(r.delta), reverse=True)[:limit]
    out: list[tuple[str, int, int, str]] = []
    for row in movers:
        if row.delta == 0:
            continue
        chg = pct_label(row.current, row.prior)
        delta_label = f"{row.delta:+} ({chg})" if chg not in ("n/a", "new") else f"{row.delta:+}"
        out.append((row.source, row.current, row.prior, delta_label))
    return out


def _merge_vendor_sections(
    ops_sections: list[tuple[str, list[str]]],
    vendor_lines: list[str],
) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Combine Vendor / Tech Ops (tasks) and Vendor / Partner (email) under one list."""
    merged = list(vendor_lines)
    kept: list[tuple[str, list[str]]] = []
    for title, bullets in ops_sections:
        if "vendor" in title.lower():
            merged.extend(bullets)
        else:
            kept.append((title, bullets))
    # Preserve order, drop duplicates
    seen: set[str] = set()
    deduped: list[str] = []
    for line in merged:
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(line)
    return kept, deduped


def load_weekly_in_review_email(
    *,
    start: date,
    end: date,
    notes: NotesSections | None = None,
) -> WeeklyInReviewEmail:
    notes = notes or NotesSections()
    prior_start, prior_end = prior_sunday_friday_period(start, end)
    errors: list[str] = []

    print(f"Loading funnel snapshot {start} .. {end} …")
    funnel: WorkWeekSnapshot | None = None
    try:
        funnel = load_work_week_snapshot(start=start, end=end)
    except Exception as exc:
        errors.append(f"Funnel: {exc}")

    if funnel is None:
        from work_week_in_review import PaidMediaWeek

        funnel = WorkWeekSnapshot(
            start=start,
            end=end,
            prior_start=prior_start,
            prior_end=prior_end,
            bookings=0,
            prior_bookings=0,
            meetings=0,
            prior_meetings=0,
            signups_all=0,
            prior_signups_all=0,
            signups_committed=0,
            prior_signups_committed=0,
            new_contacts=0,
            prior_new_contacts=0,
            sessions=0,
            prior_sessions=0,
            paid_current=PaidMediaWeek(0, 0, 0),
            paid_prior=PaidMediaWeek(0, 0, 0),
        )

    print("Loading activity summary …")
    try:
        activity = load_activity_summary_report(start=start, end=end)
    except Exception as exc:
        errors.append(f"Activity: {exc}")
        activity = None

    print("Loading pulse narrative …")
    try:
        pulse = load_pulse_weekly_report(end=end)
    except Exception as exc:
        errors.append(f"Pulse: {exc}")
        pulse = None

    errors.extend(funnel.errors)
    if activity:
        errors.extend(activity.errors)

    milestones = list(activity.milestones) if activity else []
    for bullet in notes.milestones:
        if " — " in bullet:
            title, detail = bullet.split(" — ", 1)
            milestones.append((title.strip(), detail.strip()))
        else:
            milestones.append((bullet, ""))

    opening = activity.net_read if activity else "Weekly marketing and operations review."
    if opening.startswith("Heavy week on"):
        pass
    elif activity:
        opening = activity.net_read

    pulse_blocks: dict[str, list[str]] = {}
    if pulse:
        for block in pulse.narrative_blocks:
            pulse_blocks[block.title] = block.bullets

    interpretation = build_interpretation(funnel)
    funnel_narrative = notes.funnel_narrative.strip()
    if not funnel_narrative:
        funnel_narrative = _auto_funnel_narrative(
            funnel,
            pulse.executive_summary if pulse else "",
            interpretation,
        )

    summary_bullets = _summary_bullets(funnel, pulse_blocks)

    next_week = list(notes.next_week)
    if not next_week and pulse:
        rec_block = pulse_blocks.get("Recommendations", [])
        next_week = rec_block[:3]

    ops_sections: list[tuple[str, list[str]]] = []
    operational_work_groups: list[tuple[str, list[str]]] = []
    if activity:
        ops_sections.extend(activity.completed_sections)
        ops_sections.extend(activity.email_sections)
        operational_work_groups = list(activity.operational_work_groups)
    ops_sections.extend(notes.extra_ops)

    ops_sections, vendor_lines = _merge_vendor_sections(ops_sections, [])

    return WeeklyInReviewEmail(
        period_start=start,
        period_end=end,
        prior_start=prior_start,
        prior_end=prior_end,
        opening=opening,
        milestones=milestones,
        funnel_narrative=funnel_narrative,
        summary_bullets=summary_bullets,
        next_week=next_week,
        funnel_snapshot=funnel,
        ops_sections=ops_sections,
        operational_work_groups=operational_work_groups,
        vendor_lines=vendor_lines,
        errors=errors,
    )


def _render_operational_work_html(groups: list[tuple[str, list[str]]]) -> str:
    if not groups:
        return ""
    parts = ["<h3>Operational Work</h3>"]
    for sub_title, bullets in groups:
        items = "".join(f"<li>{html.escape(b)}</li>" for b in bullets)
        parts.append(f"<h4>{html.escape(sub_title)}</h4><ul>{items}</ul>")
    return "".join(parts)


def _html_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{html.escape(c)}</td>" for c in row)
        body_rows.append(f"<tr>{cells}</tr>")
    return (
        f"<table><thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
    )


def render_weekly_in_review_html(report: WeeklyInReviewEmail) -> str:
    snap = report.funnel_snapshot
    title = (
        f"Weekly In Review — {report.period_start.strftime('%b %d')}–"
        f"{report.period_end.strftime('%b %d, %Y')}"
    )

    milestone_html = ""
    if report.milestones:
        items = "".join(
            f"<li><strong>{html.escape(t)}</strong>"
            f"{f' — {html.escape(d)}' if d else ''}</li>"
            for t, d in report.milestones
        )
        milestone_html = f"<h2>Milestones</h2><ul>{items}</ul>"

    summary_html = "".join(f"<p>{html.escape(b)}</p>" for b in report.summary_bullets)
    next_week_html = ""
    if report.next_week:
        items = "".join(f"<li>{html.escape(b)}</li>" for b in report.next_week)
        next_week_html = f"<p><strong>Next week:</strong></p><ul>{items}</ul>"

    cross_lines = _cross_period_lines(snap)
    cross_html = "<br>".join(html.escape(line) for line in cross_lines)

    meet_movers = _source_mover_table(snap.meetings_by_source)
    meet_table = ""
    if meet_movers:
        meet_table = (
            "<p><strong>Meetings by source (biggest movers):</strong></p>"
            + _html_table(
                ["Source", "This week", "Prior", "Change"],
                [[s, str(c), str(p), ch] for s, c, p, ch in meet_movers],
            )
        )

    sig_per_nc_cur = (
        f"{snap.signups_all / snap.new_contacts * 100:.1f}%"
        if snap.new_contacts
        else "n/a"
    )
    sig_per_nc_prior = (
        f"{snap.prior_signups_all / snap.prior_new_contacts * 100:.1f}%"
        if snap.prior_new_contacts
        else "n/a"
    )
    efficiency_html = (
        "<p><strong>Efficiency slipped downstream:</strong></p>"
        f"<ul><li>Signups per new contact: {sig_per_nc_cur} vs {sig_per_nc_prior} prior</li>"
        "<li>More volume, lower yield through the rest of the funnel</li></ul>"
        if snap.new_contacts > snap.prior_new_contacts and snap.signups_all <= snap.prior_signups_all
        else ""
    )

    stage_rows = _stage_direction_table(snap)
    stage_table = _html_table(
        ["Stage", "Direction", "Primary driver"],
        [[a, b, c] for a, b, c in stage_rows],
    )

    def _metric_section(
        heading: str,
        metric_rows: list[list[str]],
        extra: str = "",
    ) -> str:
        return (
            f"<h3>{html.escape(heading)}</h3>"
            + _html_table(["Metric", "This week", "Prior", "Δ"], metric_rows)
            + extra
        )

    nc_section = _metric_section(
        f"1. New Contacts — {'Growing' if snap.new_contacts >= snap.prior_new_contacts else 'Softening'} ({pct_label(snap.new_contacts, snap.prior_new_contacts)})",
        [
            [
                "New contacts",
                f"{snap.new_contacts:,}",
                f"{snap.prior_new_contacts:,}",
                f"{snap.new_contacts - snap.prior_new_contacts:+,} ({pct_label(snap.new_contacts, snap.prior_new_contacts)})",
            ]
        ],
    )

    leads_section = _metric_section(
        f"2. Leads (Paid: Google + Meta) — {pct_label(snap.paid_current.combined_leads, snap.paid_prior.combined_leads)}",
        [
            [
                "Paid leads",
                f"{snap.paid_current.combined_leads:.0f}",
                f"{snap.paid_prior.combined_leads:.0f}",
                f"{snap.paid_current.combined_leads - snap.paid_prior.combined_leads:+.0f} ({pct_label(snap.paid_current.combined_leads, snap.paid_prior.combined_leads)})",
            ],
            [
                "Paid spend",
                f"${snap.paid_current.spend:,.0f}",
                f"${snap.paid_prior.spend:,.0f}",
                f"${snap.paid_current.spend - snap.paid_prior.spend:+,.0f} ({pct_label(snap.paid_current.spend, snap.paid_prior.spend)})",
            ],
        ],
        _html_table(
            ["Channel", "This week", "Prior", "Change"],
            [
                [
                    "Google",
                    f"{snap.paid_current.google_leads:.0f}",
                    f"{snap.paid_prior.google_leads:.0f}",
                    pct_label(snap.paid_current.google_leads, snap.paid_prior.google_leads),
                ],
                [
                    "Meta",
                    f"{snap.paid_current.meta_leads:.0f}",
                    f"{snap.paid_prior.meta_leads:.0f}",
                    pct_label(snap.paid_current.meta_leads, snap.paid_prior.meta_leads),
                ],
            ],
        ),
    )

    show_rate_cur = (
        f"{snap.meetings / snap.bookings * 100:.1f}%"
        if snap.bookings
        else "n/a"
    )
    show_rate_prior = (
        f"{snap.prior_meetings / snap.prior_bookings * 100:.1f}%"
        if snap.prior_bookings
        else "n/a"
    )
    meetings_section = _metric_section(
        f"3. Meetings — {pct_label(snap.meetings, snap.prior_meetings)}",
        [
            [
                "Meetings held",
                f"{snap.meetings:,}",
                f"{snap.prior_meetings:,}",
                f"{snap.meetings - snap.prior_meetings:+,} ({pct_label(snap.meetings, snap.prior_meetings)})",
            ]
        ],
        (
            "<p><strong>Booking → meeting gap:</strong></p>"
            + _html_table(
                ["", "This week", "Prior"],
                [
                    ["Bookings", f"{snap.bookings:,}", f"{snap.prior_bookings:,}"],
                    ["Meetings", f"{snap.meetings:,}", f"{snap.prior_meetings:,}"],
                    ["Show rate (meetings / bookings)", show_rate_cur, show_rate_prior],
                ],
            )
        ),
    )

    cur_m2s = (
        f"{snap.signups_all / snap.meetings * 100:.1f}%"
        if snap.meetings
        else "n/a"
    )
    prior_m2s = (
        f"{snap.prior_signups_all / snap.prior_meetings * 100:.1f}%"
        if snap.prior_meetings
        else "n/a"
    )
    cur_b2s = (
        f"{snap.signups_all / snap.bookings * 100:.1f}%"
        if snap.bookings
        else "n/a"
    )
    prior_b2s = (
        f"{snap.prior_signups_all / snap.prior_bookings * 100:.1f}%"
        if snap.prior_bookings
        else "n/a"
    )
    sig_movers = _source_mover_table(snap.signups_by_source)
    signups_section = _metric_section(
        f"4. Sign Ups — {pct_label(snap.signups_all, snap.prior_signups_all)}",
        [
            [
                "Signups (all)",
                f"{snap.signups_all:,}",
                f"{snap.prior_signups_all:,}",
                f"{snap.signups_all - snap.prior_signups_all:+,} ({pct_label(snap.signups_all, snap.prior_signups_all)})",
            ],
            [
                "Signups (Committed = Yes)",
                f"{snap.signups_committed:,}",
                f"{snap.prior_signups_committed:,}",
                f"{snap.signups_committed - snap.prior_signups_committed:+,} ({pct_label(snap.signups_committed, snap.prior_signups_committed)})",
            ],
        ],
        (
            (
                "<p><strong>By source (biggest movers):</strong></p>"
                + _html_table(
                    ["Source", "This week", "Prior", "Change"],
                    [[s, str(c), str(p), ch] for s, c, p, ch in sig_movers],
                )
                if sig_movers
                else ""
            )
            + "<p><strong>Close rates:</strong></p>"
            + _html_table(
                ["Rate", "This week", "Prior"],
                [
                    ["Signups / meetings", cur_m2s, prior_m2s],
                    ["Signups / bookings", cur_b2s, prior_b2s],
                ],
            )
        ),
    )

    ops_html = ""
    has_campaigns = any(title.casefold() == "campaigns" for title, _ in report.ops_sections)
    for section_title, bullets in report.ops_sections:
        items = "".join(f"<li>{html.escape(b)}</li>" for b in bullets)
        ops_html += f"<h3>{html.escape(section_title)}</h3><ul>{items}</ul>"
        if section_title.casefold() == "campaigns" and report.operational_work_groups:
            ops_html += _render_operational_work_html(report.operational_work_groups)

    if report.operational_work_groups and not has_campaigns:
        ops_html = _render_operational_work_html(report.operational_work_groups) + ops_html

    if report.vendor_lines:
        items = "".join(f"<li>{html.escape(b)}</li>" for b in report.vendor_lines)
        ops_html += f"<h3>Vendor / Partner</h3><ul>{items}</ul>"

    errors_html = ""
    if report.errors:
        errors_html = f"<p style='color:#a00;'>{html.escape('; '.join(report.errors[:5]))}</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: Arial, sans-serif; font-size: 10pt; margin: 16px; line-height: 1.45; color: #222; max-width: 720px; }}
h2 {{ font-size: 11pt; font-weight: bold; margin: 18px 0 8px 0; border-bottom: 1px solid #ccc; padding-bottom: 4px; }}
h3 {{ font-size: 10pt; font-weight: bold; margin: 14px 0 6px 0; }}
h4 {{ font-size: 10pt; font-weight: bold; margin: 10px 0 4px 0; color: #444; }}
p {{ margin: 0 0 10px 0; }}
ul {{ margin: 0 0 10px 0; padding-left: 20px; }}
li {{ margin-bottom: 4px; }}
table {{ border-collapse: collapse; margin: 6px 0 12px 0; font-size: 10pt; width: 450px; max-width: 100%; }}
th, td {{ border: 1px solid #bbb; padding: 4px 6px; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }}
th {{ background: #eef2f6; font-weight: bold; }}
.funnel-flow {{ background: #f6f8fa; border: 1px solid #dde3ea; padding: 10px 12px; margin: 10px 0; }}
</style>
</head>
<body>

<p>{html.escape(report.opening)}</p>

{milestone_html}

<h2>Marketing &amp; Sales Funnel Report (Deep Dive)</h2>
<p>{html.escape(report.funnel_narrative)}</p>

<h2>Summary in Numbers</h2>
{summary_html}
{next_week_html}
{meet_table}
{efficiency_html}

<p><strong>Cross-period summary</strong></p>
<div class="funnel-flow">{cross_html}</div>
{stage_table}

{nc_section}
{leads_section}
{meetings_section}
{signups_section}

<h2>Marketing Operations</h2>
{ops_html}

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
    parser = argparse.ArgumentParser(description="Unified weekly in-review email report")
    parser.add_argument(
        "--start",
        type=str,
        default="",
        help="Period start YYYY-MM-DD (default: Sunday on or before --end)",
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
        help="Optional markdown notes (## Milestones, ## Funnel narrative, ## Next week)",
    )
    parser.add_argument("--open", action="store_true", help="Open HTML in browser")
    parser.add_argument("--output-dir", type=str, default="")
    args = parser.parse_args()

    end = date.fromisoformat(args.end) if args.end else default_end_date()
    if args.start:
        start = date.fromisoformat(args.start)
    else:
        start, end = sunday_friday_range(end=end)

    notes = NotesSections()
    if args.notes_file:
        notes = parse_notes_file(_resolve_path(args.notes_file))
        print(f"Loaded notes from {args.notes_file}")

    report = load_weekly_in_review_email(start=start, end=end, notes=notes)
    out_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR
    path = write_weekly_in_review_email(report, output_dir=out_dir)
    print(f"Wrote {path}")

    if args.open:
        webbrowser.open(path.resolve().as_uri())


if __name__ == "__main__":
    main()
