"""
Work Week In Review — Saturday morning funnel diagnostic.

Compares the trailing 7-day window to the prior 7 days across bookings,
signups, traffic, paid media, and hear-about attribution. Includes an
auto-generated interpretation of what is driving week-over-week changes.

Run every Saturday morning from this folder:

    python work_week_in_review.py

By default, when today is Saturday the report ends on Friday (yesterday) so
all seven days are complete. On other weekdays, the default end date is today.

Custom end date / range:

    python work_week_in_review.py --end 2026-06-13 --days 7

Windows Task Scheduler (example):
    Program: python
    Arguments: work_week_in_review.py
    Start in: <project root>/Op Reports
    Trigger: Weekly, Saturday, 8:00 AM

Writes: Op Reports/outputs/work_week_in_review_YYYY-MM-DD.md
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from _bootstrap import OP_REPORTS_DIR, setup

setup()

from ghl_client import (  # noqa: E402
    contact_created_utc_date_str,
    count_calendar_funnel_events,
    fetch_bookings_and_meetings_by_hear_about_us,
    fetch_committed_yes_by_hear_about_us,
    fetch_contacts_date_added_complete,
    resolve_sign_up_date_custom_field_id,
    search_contacts_custom_field_date_range,
)
from google_data import (  # noqa: E402
    get_ga4_sessions_by_date,
    get_sessions_by_session_default_channel_group,
)
from google_ads_ghl_paid_cohort import fetch_google_ads_daily  # noqa: E402
from meta_client import fetch_account_daily_insights  # noqa: E402
from weekly_report_data import period_range, prior_period  # noqa: E402

OUTPUT_DIR = OP_REPORTS_DIR / "outputs"


@dataclass
class SourceDeltaRow:
    source: str
    current: int
    prior: int

    @property
    def delta(self) -> int:
        return self.current - self.prior

    @property
    def pct_change(self) -> float | None:
        if self.prior == 0:
            return None
        return (self.current - self.prior) / self.prior * 100.0


@dataclass
class PaidMediaWeek:
    google_leads: float
    meta_leads: float
    spend: float

    @property
    def combined_leads(self) -> float:
        return self.google_leads + self.meta_leads


@dataclass
class WorkWeekSnapshot:
    start: date
    end: date
    prior_start: date
    prior_end: date
    bookings: int
    prior_bookings: int
    meetings: int
    prior_meetings: int
    signups_all: int
    prior_signups_all: int
    signups_committed: int
    prior_signups_committed: int
    new_contacts: int
    prior_new_contacts: int
    sessions: int
    prior_sessions: int
    paid_current: PaidMediaWeek
    paid_prior: PaidMediaWeek
    bookings_by_source: list[SourceDeltaRow] = field(default_factory=list)
    signups_by_source: list[SourceDeltaRow] = field(default_factory=list)
    meetings_by_source: list[SourceDeltaRow] = field(default_factory=list)
    sessions_by_channel: list[SourceDeltaRow] = field(default_factory=list)
    ga4_daily_current: dict[str, int] = field(default_factory=dict)
    ga4_daily_prior: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def default_end_date() -> date:
    """Saturday runs use Friday as the last complete day in the window."""
    today = date.today()
    if today.weekday() == 5:
        return today - timedelta(days=1)
    return today


def pct_label(current: float | int | None, prior: float | int | None) -> str:
    if current is None or prior is None:
        return "n/a"
    if prior == 0:
        return "n/a" if current == 0 else "new"
    return f"{(float(current) - float(prior)) / float(prior) * 100:+.0f}%"


def rows_to_dict(rows: list[dict] | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows or []:
        src = (row.get("source") or "(Not set)").strip() or "(Not set)"
        out[src] = int(row.get("count") or 0)
    return out


def build_source_deltas(
    current: dict[str, int],
    prior: dict[str, int],
    *,
    limit: int = 12,
) -> list[SourceDeltaRow]:
    sources = sorted(
        set(current) | set(prior),
        key=lambda s: current.get(s, 0) + prior.get(s, 0),
        reverse=True,
    )
    return [
        SourceDeltaRow(
            source=src,
            current=current.get(src, 0),
            prior=prior.get(src, 0),
        )
        for src in sources[:limit]
    ]


def paid_media_week(since: str, until: str) -> PaidMediaWeek:
    google_leads = 0.0
    google_spend = 0.0
    meta_leads = 0.0
    meta_spend = 0.0

    gdf = fetch_google_ads_daily(since, until)
    if gdf is not None and not gdf.empty:
        google_leads = float(gdf["discovery_calls"].sum())
        google_spend = float(gdf["cost"].sum())

    meta = fetch_account_daily_insights(since=since, until=until)
    meta_leads = float(meta["totals"]["leads"])
    meta_spend = float(meta["totals"]["spend"])

    return PaidMediaWeek(
        google_leads=google_leads,
        meta_leads=meta_leads,
        spend=google_spend + meta_spend,
    )


def new_contacts_total(since: str, until: str) -> int:
    contacts, _ = fetch_contacts_date_added_complete(since, until)
    counts: Counter[str] = Counter()
    for contact in contacts:
        created = contact_created_utc_date_str(contact)
        if since <= created <= until:
            counts[created] += 1
    return int(sum(counts.values()))


def load_work_week_snapshot(*, start: date, end: date) -> WorkWeekSnapshot:
    prior_start, prior_end = prior_period(start, end)
    cur_since, cur_until = start.isoformat(), end.isoformat()
    prior_since, prior_until = prior_start.isoformat(), prior_end.isoformat()
    snap = WorkWeekSnapshot(
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

    try:
        cur_funnel = count_calendar_funnel_events(cur_since, cur_until)
        prior_funnel = count_calendar_funnel_events(prior_since, prior_until)
        snap.bookings = cur_funnel.bookings
        snap.prior_bookings = prior_funnel.bookings
        snap.meetings = cur_funnel.meetings
        snap.prior_meetings = prior_funnel.meetings
    except Exception as exc:
        snap.errors.append(f"GHL calendar funnel: {exc}")

    try:
        sign_up_field = resolve_sign_up_date_custom_field_id()
        _, _, snap.signups_all = search_contacts_custom_field_date_range(
            sign_up_field, cur_since, cur_until
        )
        _, _, snap.prior_signups_all = search_contacts_custom_field_date_range(
            sign_up_field, prior_since, prior_until
        )
    except Exception as exc:
        snap.errors.append(f"GHL signups: {exc}")

    try:
        cur_committed = fetch_committed_yes_by_hear_about_us(cur_since, cur_until)
        prior_committed = fetch_committed_yes_by_hear_about_us(prior_since, prior_until)
        snap.signups_committed = int(cur_committed.get("total_committed") or 0)
        snap.prior_signups_committed = int(prior_committed.get("total_committed") or 0)
        snap.signups_by_source = build_source_deltas(
            rows_to_dict(cur_committed.get("rows")),
            rows_to_dict(prior_committed.get("rows")),
        )
    except Exception as exc:
        snap.errors.append(f"GHL committed signups: {exc}")

    try:
        cal = fetch_bookings_and_meetings_by_hear_about_us(cur_since, cur_until)
        cal_prior = fetch_bookings_and_meetings_by_hear_about_us(prior_since, prior_until)
        snap.bookings_by_source = build_source_deltas(
            rows_to_dict(cal["bookings"].get("rows")),
            rows_to_dict(cal_prior["bookings"].get("rows")),
        )
        snap.meetings_by_source = build_source_deltas(
            rows_to_dict(cal["meetings"].get("rows")),
            rows_to_dict(cal_prior["meetings"].get("rows")),
        )
    except Exception as exc:
        snap.errors.append(f"GHL hear-about breakdown: {exc}")

    try:
        cur_ch = get_sessions_by_session_default_channel_group(cur_since, cur_until)
        prior_ch = get_sessions_by_session_default_channel_group(prior_since, prior_until)
        cur_map = {
            str(r["Session_default_channel_group"]): int(r["Sessions"])
            for _, r in cur_ch.iterrows()
        }
        prior_map = {
            str(r["Session_default_channel_group"]): int(r["Sessions"])
            for _, r in prior_ch.iterrows()
        }
        snap.sessions = sum(cur_map.values())
        snap.prior_sessions = sum(prior_map.values())
        snap.sessions_by_channel = build_source_deltas(cur_map, prior_map)
    except Exception as exc:
        snap.errors.append(f"GA4 channels: {exc}")

    try:
        ga4 = get_ga4_sessions_by_date(prior_since, cur_until)
        snap.ga4_daily_current = {
            day: count for day, count in ga4.items() if cur_since <= day <= cur_until
        }
        snap.ga4_daily_prior = {
            day: count for day, count in ga4.items() if prior_since <= day <= prior_until
        }
    except Exception as exc:
        snap.errors.append(f"GA4 daily sessions: {exc}")

    try:
        snap.new_contacts = new_contacts_total(cur_since, cur_until)
        snap.prior_new_contacts = new_contacts_total(prior_since, prior_until)
    except Exception as exc:
        snap.errors.append(f"GHL new contacts: {exc}")

    try:
        snap.paid_current = paid_media_week(cur_since, cur_until)
        snap.paid_prior = paid_media_week(prior_since, prior_until)
    except Exception as exc:
        snap.errors.append(f"Paid media: {exc}")

    return snap


def _source_delta_table(rows: list[SourceDeltaRow], *, total_current: int, total_prior: int) -> list[str]:
    lines = [
        "| Source | This week | Prior week | Delta | Change |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        chg = pct_label(row.current, row.prior)
        lines.append(
            f"| {row.source} | {row.current:,} | {row.prior:,} | {row.delta:+,} | {chg} |"
        )
    lines.append(
        f"| **Total** | **{total_current:,}** | **{total_prior:,}** | "
        f"**{total_current - total_prior:+,}** | **{pct_label(total_current, total_prior)}** |"
    )
    return lines


def _rate(current_num: int, current_den: int, prior_num: int, prior_den: int) -> tuple[str, str]:
    cur = f"{current_num / current_den * 100:.1f}%" if current_den else "n/a"
    prior = f"{prior_num / prior_den * 100:.1f}%" if prior_den else "n/a"
    return cur, prior


def build_interpretation(snap: WorkWeekSnapshot) -> list[str]:
    lines: list[str] = []
    drivers: list[str] = []
    watch: list[str] = []
    healthy: list[str] = []

    booking_chg = snap.bookings - snap.prior_bookings
    signup_chg = snap.signups_all - snap.prior_signups_all
    meeting_chg = snap.meetings - snap.prior_meetings

    def find_source(rows: list[SourceDeltaRow], *names: str) -> SourceDeltaRow | None:
        targets = {n.casefold() for n in names}
        for row in rows:
            if row.source.casefold() in targets:
                return row
        return None

    wom_bookings = find_source(snap.bookings_by_source, "WOM", "Word of mouth")
    wom_signups = find_source(snap.signups_by_source, "WOM", "Word of mouth")
    google_signups = find_source(snap.signups_by_source, "Google")
    organic = find_source(snap.sessions_by_channel, "Organic Search")

    if wom_bookings and wom_bookings.delta <= -3:
        drivers.append(
            f"**Word of mouth bookings** fell by {abs(wom_bookings.delta)} "
            f"({wom_bookings.prior} → {wom_bookings.current}), accounting for much of the "
            f"overall booking change ({booking_chg:+})."
        )
    if wom_signups and wom_signups.delta <= -3:
        drivers.append(
            f"**WOM committed signups** dropped by {abs(wom_signups.delta)} "
            f"({wom_signups.prior} → {wom_signups.current}), a major driver of the signup change "
            f"({signup_chg:+})."
        )

    if snap.signups_all < snap.prior_signups_all and snap.meetings >= snap.prior_meetings * 0.85:
        cur_close, prior_close = _rate(
            snap.signups_all, snap.meetings, snap.prior_signups_all, snap.prior_meetings
        )
        if cur_close != "n/a" and prior_close != "n/a":
            drivers.append(
                f"**Close rate softened** — signups/meetings moved from {prior_close} to "
                f"{cur_close} even though meeting volume only changed {meeting_chg:+}."
            )

    if google_signups and google_signups.delta >= 0 and signup_chg < 0:
        healthy.append(
            f"**Google signups held or improved** ({google_signups.prior} → "
            f"{google_signups.current}) while overall signups were {signup_chg:+}."
        )

    paid_leads_chg = snap.paid_current.combined_leads - snap.paid_prior.combined_leads
    if abs(paid_leads_chg) <= max(3, snap.paid_prior.combined_leads * 0.15) and booking_chg < 0:
        healthy.append(
            f"**Paid leads were roughly stable** ({snap.paid_prior.combined_leads:.0f} → "
            f"{snap.paid_current.combined_leads:.0f}) with spend "
            f"{pct_label(snap.paid_current.spend, snap.paid_prior.spend)} — not primarily a paid-media issue."
        )

    if organic and organic.delta <= -100:
        watch.append(
            f"**Organic Search sessions** fell by {abs(organic.delta):,} "
            f"({organic.prior:,} → {organic.current:,}). If that persists, expect booking pressure "
            f"over the next 1–2 weeks."
        )

    not_set = find_source(snap.signups_by_source, "(Not set)")
    if not_set and not_set.delta <= -3:
        watch.append(
            f"**Untagged signups** (Not set) dropped by {abs(not_set.delta)} — check intake tagging "
            f"on referral/offline leads."
        )

    if not drivers and booking_chg >= 0 and signup_chg >= 0:
        lines.append(
            "Week-over-week funnel metrics improved or held steady. No major negative drivers "
            "flagged in attribution."
        )
    else:
        if drivers:
            lines.append("**Primary drivers**")
            lines.extend(f"- {item}" for item in drivers)
            lines.append("")
        if healthy:
            lines.append("**What is holding**")
            lines.extend(f"- {item}" for item in healthy)
            lines.append("")
        if watch:
            lines.append("**Worth watching**")
            lines.extend(f"- {item}" for item in watch)
            lines.append("")

    if not lines:
        lines.append(
            "Mixed week — review source tables below for channel-level detail. "
            "No single attribution source dominated the change."
        )

    return lines


def build_report(snap: WorkWeekSnapshot) -> str:
    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %I:%M %p %Z").lstrip("0")
    cur_b2s, prior_b2s = _rate(
        snap.signups_all, snap.bookings, snap.prior_signups_all, snap.prior_bookings
    )
    cur_m2s, prior_m2s = _rate(
        snap.signups_all, snap.meetings, snap.prior_signups_all, snap.prior_meetings
    )
    cur_s_per_k = snap.signups_all / snap.sessions * 1000 if snap.sessions else None
    prior_s_per_k = (
        snap.prior_signups_all / snap.prior_sessions * 1000 if snap.prior_sessions else None
    )
    cur_s_per_k_label = f"{cur_s_per_k:.1f}" if cur_s_per_k is not None else "n/a"
    prior_s_per_k_label = f"{prior_s_per_k:.1f}" if prior_s_per_k is not None else "n/a"

    lines = [
        "# Work Week In Review",
        "",
        f"**Period reviewed:** {snap.start.isoformat()} through {snap.end.isoformat()} (inclusive)",
        f"**Compared to:** {snap.prior_start.isoformat()} through {snap.prior_end.isoformat()}",
        f"**Generated:** {generated}",
        "",
        "## Headline metrics",
        "",
        "| Metric | This week | Prior week | Change |",
        "| --- | ---: | ---: | ---: |",
        f"| Bookings (dateAdded) | {snap.bookings:,} | {snap.prior_bookings:,} | "
        f"{pct_label(snap.bookings, snap.prior_bookings)} |",
        f"| Meetings (startTime) | {snap.meetings:,} | {snap.prior_meetings:,} | "
        f"{pct_label(snap.meetings, snap.prior_meetings)} |",
        f"| Signups (all Sign Up Date) | {snap.signups_all:,} | {snap.prior_signups_all:,} | "
        f"{pct_label(snap.signups_all, snap.prior_signups_all)} |",
        f"| Signups (Committed = Yes) | {snap.signups_committed:,} | {snap.prior_signups_committed:,} | "
        f"{pct_label(snap.signups_committed, snap.prior_signups_committed)} |",
        f"| New contacts (dateAdded) | {snap.new_contacts:,} | {snap.prior_new_contacts:,} | "
        f"{pct_label(snap.new_contacts, snap.prior_new_contacts)} |",
        f"| GA4 sessions | {snap.sessions:,} | {snap.prior_sessions:,} | "
        f"{pct_label(snap.sessions, snap.prior_sessions)} |",
        f"| Paid leads (Google + Meta) | {snap.paid_current.combined_leads:.0f} | "
        f"{snap.paid_prior.combined_leads:.0f} | "
        f"{pct_label(snap.paid_current.combined_leads, snap.paid_prior.combined_leads)} |",
        f"| Paid spend | ${snap.paid_current.spend:,.0f} | ${snap.paid_prior.spend:,.0f} | "
        f"{pct_label(snap.paid_current.spend, snap.paid_prior.spend)} |",
        "",
        "## Interpretation",
        "",
    ]
    lines.extend(build_interpretation(snap))
    lines.extend(
        [
            "## Conversion rates",
            "",
            "| Rate | This week | Prior week |",
            "| --- | ---: | ---: |",
            f"| Signups / bookings | {cur_b2s} | {prior_b2s} |",
            f"| Signups / meetings | {cur_m2s} | {prior_m2s} |",
            f"| Signups per 1k sessions | {cur_s_per_k_label} | {prior_s_per_k_label} |",
        ]
    )
    lines.extend(["", "## Bookings by hear-about source", ""])
    lines.extend(_source_delta_table(snap.bookings_by_source, total_current=snap.bookings, total_prior=snap.prior_bookings))
    lines.extend(["", "## Committed signups by hear-about source", ""])
    lines.extend(
        _source_delta_table(
            snap.signups_by_source,
            total_current=snap.signups_committed,
            total_prior=snap.prior_signups_committed,
        )
    )
    lines.extend(["", "## Meetings by hear-about source", ""])
    lines.extend(_source_delta_table(snap.meetings_by_source, total_current=snap.meetings, total_prior=snap.prior_meetings))
    lines.extend(["", "## GA4 sessions by channel", ""])
    lines.extend(_source_delta_table(snap.sessions_by_channel, total_current=snap.sessions, total_prior=snap.prior_sessions))
    lines.extend(["", "## Paid media detail", ""])
    lines.extend(
        [
            f"- Google leads: {snap.paid_current.google_leads:.0f} vs "
            f"{snap.paid_prior.google_leads:.0f} prior ({pct_label(snap.paid_current.google_leads, snap.paid_prior.google_leads)})",
            f"- Meta leads: {snap.paid_current.meta_leads:.0f} vs "
            f"{snap.paid_prior.meta_leads:.0f} prior ({pct_label(snap.paid_current.meta_leads, snap.paid_prior.meta_leads)})",
        ]
    )
    lines.extend(["", "## Daily GA4 sessions (this week)", ""])
    for day in sorted(snap.ga4_daily_current):
        lines.append(f"- {day}: {snap.ga4_daily_current[day]:,}")
    lines.extend(["", "## Daily GA4 sessions (prior week)", ""])
    for day in sorted(snap.ga4_daily_prior):
        lines.append(f"- {day}: {snap.ga4_daily_prior[day]:,}")

    if snap.errors:
        lines.extend(["", "## Data warnings", ""])
        lines.extend(f"- {err}" for err in snap.errors)

    lines.extend(
        [
            "",
            "---",
            "",
            "_Generated by `Op Reports/work_week_in_review.py`. Re-run each Saturday morning "
            "for the seven days ending Friday._",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Saturday Work Week In Review — funnel WoW diagnostic"
    )
    parser.add_argument("--days", type=int, default=7, help="Trailing days (default: 7)")
    parser.add_argument(
        "--end",
        type=str,
        default="",
        help="Last day YYYY-MM-DD (default: Friday when run on Saturday, else today)",
    )
    parser.add_argument("--output", type=str, default="", help="Output path override")
    args = parser.parse_args()

    end_date = date.fromisoformat(args.end) if args.end else default_end_date()
    start, end = period_range(end=end_date, days=args.days)
    prior_start, prior_end = prior_period(start, end)

    print("Work Week In Review")
    print(f"  Current: {start} .. {end}")
    print(f"  Prior:   {prior_start} .. {prior_end}")
    print("Loading data (GHL, GA4, Google Ads, Meta) ...")

    snap = load_work_week_snapshot(start=start, end=end)
    report = build_report(snap)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = (
        Path(args.output)
        if args.output
        else OUTPUT_DIR / f"work_week_in_review_{end.isoformat()}.md"
    )
    out_path.write_text(report, encoding="utf-8")

    print(f"  Bookings: {snap.bookings} ({pct_label(snap.bookings, snap.prior_bookings)} vs prior)")
    print(f"  Signups:  {snap.signups_all} ({pct_label(snap.signups_all, snap.prior_signups_all)} vs prior)")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
