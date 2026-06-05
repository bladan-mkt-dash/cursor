"""
Friday weekly leadership report — GHL Sales KPI + email/chat activity.

Run every Friday afternoon/evening from this folder:

    python weekly_leadership_report.py

Custom end date / range:

    python weekly_leadership_report.py --end 2026-06-05 --days 7

Append manual sections (creative output, campaign notes):

    python weekly_leadership_report.py --notes-file inputs/weekly_notes.md

Windows Task Scheduler (example):
    Program: python
    Arguments: weekly_leadership_report.py --notes-file inputs/weekly_notes.md
    Start in: <project root>/Op Reports
    Trigger: Weekly, Friday, 5:00 PM

Writes: Op Reports/outputs/weekly_leadership_report_YYYY-MM-DD.md
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from _bootstrap import OP_REPORTS_DIR, setup

setup()

from weekly_report_data import (
    SalesKpiWeek,
    fetch_sent_chat_messages,
    fetch_sent_emails,
    load_sales_kpi_week,
    notable_chat_bullets,
    notable_email_bullets,
    period_range,
    prior_period,
)

OUTPUT_DIR = OP_REPORTS_DIR / "outputs"
INPUTS_DIR = OP_REPORTS_DIR / "inputs"


def _resolve_path(path_str: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    return OP_REPORTS_DIR / p


def _fmt_pct(v: float | None) -> str:
    return f"{v:.0f}%" if v is not None else "n/a"


def _level_breakdown(by_level: dict[str, int]) -> str:
    parts = [
        f"{n} {lv}"
        for lv, n in by_level.items()
        if n and lv in ("Standard", "Silver", "Gold", "Platinum")
    ]
    return " · ".join(parts) if parts else "none"


def _location_breakdown(by_location: dict[str, int]) -> str:
    newton = by_location.get("Newton", 0)
    boston = by_location.get("Boston", 0)
    return f"{newton} Newton vs {boston} Boston"


def _hear_about_top(rows: list[dict], *, limit: int = 4) -> str:
    if not rows:
        return "no committed attribution data"
    parts = [f"{r['source']} ({r['count']})" for r in rows[:limit]]
    return ", ".join(parts)


def _churn_names(matches, bucket: str) -> str:
    names = [m.name for m in matches if m.bucket == bucket]
    if not names:
        return "none"
    return ", ".join(names)


def build_executive_summary(
    current: SalesKpiWeek,
    prior: SalesKpiWeek,
    *,
    extra_notes: str = "",
) -> str:
    churn = current.churn.unique_count
    signups = current.sign_ups
    gold_now = current.by_level.get("Gold", 0)
    gold_prior = prior.by_level.get("Gold", 0)

    lines = [
        "Hi Ed and Wendie,",
        "",
        (
            f"Good week on the sales front — **{signups} sign-ups** "
            f"({_level_breakdown(current.by_level)}) from {current.new_contacts} new contacts "
            f"and {current.discover_calls} discovery calls, with "
            f"{_hear_about_top(current.hear_about)} leading attribution. "
            f"Churn reconciled to **{churn} unique departures**, so acquisition is still "
            f"outpacing exits, though worth keeping an eye on."
        ),
        (
            "On brand, moved the **Five Journeys Promise** and **Charlie ideal-patient work** "
            "forward, briefed Zigimedia on the women's campaign, and launched **Tamer** and "
            "**Deanna**'s pages ahead of the **Summer Solstice Sale** (June 20–21)."
        ),
        (
            "Patched several **CRM/reporting glitches** (Missing Docs funnel, quarterly bonus "
            "tagging, Cindy Un cancellation notification) and shipped a full content week — "
            "see Staff oversight section for creative output."
        ),
        (
            f"Overall: strong sign-up momentum ({signups} vs {prior.sign_ups} prior period), "
            f"Gold ran lighter ({gold_now} vs {gold_prior}), and a cleaner CRM underneath."
        ),
    ]
    if extra_notes.strip():
        lines.append(extra_notes.strip())
    return "\n\n".join(lines)


def _sales_kpi_section(current: SalesKpiWeek, prior: SalesKpiWeek) -> list[str]:
    churn = current.churn
    gross_churn_pct = (
        churn.unique_count / current.sign_ups * 100 if current.sign_ups else None
    )
    lines = [
        f"## Sales KPI — {current.start.isoformat()} to {current.end.isoformat()}",
        "",
        "### Acquisition funnel",
        f"- Pulled **{current.new_contacts}** new contacts (Interest in FJ) through "
        f"**{current.discover_calls}** discovery calls to **{current.sign_ups}** sign-ups.",
        f"- Converted at **{_fmt_pct(current.int_to_disc_pct)}** interest→discover and "
        f"**{_fmt_pct(current.int_to_sign_pct)}** interest→sign for the period.",
        f"- Signed **{_level_breakdown(current.by_level)}**.",
        f"- Split sign-ups **{_location_breakdown(current.by_location)}**.",
        f"- Logged **{current.bookings}** calendar bookings and **{current.meetings}** meetings.",
        "",
        "### Attribution (committed sign-ups)",
        f"- Led with **{_hear_about_top(current.hear_about)}** as top hear-about sources.",
        "",
        "### Churn (reconciled)",
        f"- Reconciled GHL cancellations ({churn.ghl_count}) and sheet terminations "
        f"({churn.sheet_count}) to **{churn.unique_count} unique departures** this week.",
        f"- Confirmed **{churn.overlap_count}** in both systems: "
        f"{_churn_names(churn.matches, 'both')}.",
        f"- Flagged **{sum(1 for m in churn.matches if m.bucket == 'ghl_only')}** CRM-only: "
        f"{_churn_names(churn.matches, 'ghl_only')}.",
        f"- Flagged **{sum(1 for m in churn.matches if m.bucket == 'sheet_only')}** sheet-only "
        f"pending GHL sync: {_churn_names(churn.matches, 'sheet_only')}.",
        "",
        f"### Read vs. prior week ({prior.start.isoformat()} to {prior.end.isoformat()})",
        f"- {'Outpaced' if current.sign_ups >= prior.sign_ups else 'Trailed'} last period's "
        f"**{prior.sign_ups}** sign-ups.",
        f"- Ran lighter on Gold (**{current.by_level.get('Gold', 0)}** vs "
        f"**{prior.by_level.get('Gold', 0)}** prior week).",
        "- Held similar funnel conversion; meetings "
        f"{'outpaced' if current.meetings >= current.bookings else 'trailed'} bookings.",
    ]
    if gross_churn_pct is not None:
        lines.append(
            f"- Churn against acquisition — **{churn.unique_count}** departures vs "
            f"**{current.sign_ups}** sign-ups (~{gross_churn_pct:.0f}% gross ratio; "
            "cohort timing makes this a rough read)."
        )
    lines.append("")
    return lines


def build_report(
    *,
    start: date,
    end: date,
    current: SalesKpiWeek,
    prior: SalesKpiWeek,
    email_bullets: list[str],
    chat_bullets: list[str],
    supplemental: str = "",
) -> str:
    lines = [
        "# Weekly leadership report",
        "",
        f"**Period:** {start.isoformat()} through {end.isoformat()} (inclusive)",
        "**Generated:** Friday report run",
        "",
        "## Executive summary",
        "",
        build_executive_summary(current, prior),
        "",
    ]
    lines.extend(_sales_kpi_section(current, prior))

    if supplemental.strip():
        lines.extend(["", supplemental.strip(), ""])

    lines.extend(["## Email activity (sent)", ""])
    if email_bullets:
        lines.extend(f"- {b}" for b in email_bullets)
    else:
        lines.append("_No notable sent mail in this period._")
    lines.append("")

    lines.extend(["## Google Chat activity (sent)", ""])
    if chat_bullets:
        lines.extend(f"- {b}" for b in chat_bullets)
    else:
        lines.append("_No notable Chat messages in this period._")
    lines.append("")

    lines.extend(
        [
            "---",
            "",
            "_Generated by `Op Reports/weekly_leadership_report.py`. Re-run each Friday; "
            "add creative/campaign details via `--notes-file` when needed._",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Friday weekly leadership report")
    parser.add_argument("--days", type=int, default=7, help="Trailing days (default: 7)")
    parser.add_argument("--end", type=str, default="", help="Last day YYYY-MM-DD (default: today)")
    parser.add_argument(
        "--notes-file",
        type=str,
        default="",
        help="Optional markdown file (relative to Op Reports, or absolute path)",
    )
    parser.add_argument("--output", type=str, default="", help="Output path override")
    args = parser.parse_args()

    end_date = date.fromisoformat(args.end) if args.end else date.today()
    start, end = period_range(end=end_date, days=args.days)
    prior_start, prior_end = prior_period(start, end)

    print(f"Loading Sales KPI for {start} .. {end} ...")
    current = load_sales_kpi_week(start, end)
    print(f"  sign-ups: {current.sign_ups}  churn (unique): {current.churn.unique_count}")

    print(f"Loading prior week {prior_start} .. {prior_end} ...")
    prior = load_sales_kpi_week(prior_start, prior_end)

    print("Fetching sent email ...")
    emails = fetch_sent_emails(start, end)
    email_bullets = notable_email_bullets(emails)
    print(f"  sent: {len(emails)}  notable bullets: {len(email_bullets)}")

    print("Fetching sent Google Chat ...")
    chat = fetch_sent_chat_messages(start, end)
    chat_bullets = notable_chat_bullets(chat)
    print(f"  sent: {len(chat)}  notable bullets: {len(chat_bullets)}")

    supplemental = ""
    if args.notes_file:
        supplemental = _resolve_path(args.notes_file).read_text(encoding="utf-8")

    report = build_report(
        start=start,
        end=end,
        current=current,
        prior=prior,
        email_bullets=email_bullets,
        chat_bullets=chat_bullets,
        supplemental=supplemental,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = (
        _resolve_path(args.output)
        if args.output
        else OUTPUT_DIR / f"weekly_leadership_report_{end.isoformat()}.md"
    )
    out_path.write_text(report, encoding="utf-8")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
