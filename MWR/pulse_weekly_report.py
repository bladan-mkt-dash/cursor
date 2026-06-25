"""
Marketing Pulse — quick weekly ownership report (bulleted HTML for email).

Designed to run in under a minute by fetching only core funnel metrics in
parallel — not the full Marketing Pulse dashboard.

    python MWR/pulse_weekly_report.py --open
    python MWR/pulse_weekly_report.py --end 2026-06-12

Email: open the HTML → Select All → Copy → paste into Gmail/Outlook.
"""

from __future__ import annotations

import argparse
import html
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MWR_DIR = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_MWR_DIR) not in sys.path:
    sys.path.insert(0, str(_MWR_DIR))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

OUTPUT_DIR = _MWR_DIR / "outputs"

COLORS = {
    "accent": "#5DA68A",
    "accent_dark": "#264540",
    "muted": "#6B7C93",
    "danger": "#E45756",
    "panel_bg": "#F4F8FB",
    "card_bg": "#FFFFFF",
}

# From GHL history (Jul 2025+); avoids slow extra API calls each run.
_MAY_2026_BOOKINGS = 231


@dataclass
class NarrativeBlock:
    title: str
    bullets: list[str] = field(default_factory=list)


@dataclass
class WeeklySnapshot:
    period_start: date
    period_end: date
    bookings: int = 0
    prior_bookings: int = 0
    meetings: int = 0
    prior_meetings: int = 0
    signups: int = 0
    prior_signups: int = 0
    committed: int = 0
    sessions: int = 0
    prior_sessions: int = 0
    google_bookings: int = 0
    wom_bookings: int = 0
    wom_committed: int = 0
    google_committed: int = 0
    not_set_committed: int = 0
    spend_7d: float = 0.0
    leads_7d: float = 0.0
    google_spend: float = 0.0
    meta_spend: float = 0.0
    errors: list[str] = field(default_factory=list)


@dataclass
class PulseWeeklyReport:
    period_start: date
    period_end: date
    headline: str
    executive_summary: str
    metric_line: str
    narrative_blocks: list[NarrativeBlock] = field(default_factory=list)
    data_errors: list[str] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)


def _fmt_count(v: int | float | None) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return f"{v:,}"


def _fmt_money(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"${v:,.0f}"


def _pct_change(current: float | int | None, prior: float | int | None) -> str | None:
    if current is None or prior is None or prior == 0:
        return None
    pct = (float(current) - float(prior)) / float(prior) * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.0f}% vs prior 7d"


def _period_range(*, end: date, days: int = 7) -> tuple[date, date]:
    return end - timedelta(days=days - 1), end


def _source_count(rows: list[dict] | None, source: str) -> int:
    for row in rows or []:
        if (row.get("source") or "").strip() == source:
            return int(row.get("count") or 0)
    return 0


def load_weekly_snapshot(*, end: date | None = None) -> WeeklySnapshot:
    """Fetch core weekly metrics in parallel (GHL + paid + GA4 only)."""
    from ghl_client import (
        count_calendar_funnel_events,
        fetch_bookings_and_meetings_by_hear_about_us,
        fetch_committed_yes_by_hear_about_us,
        resolve_sign_up_date_custom_field_id,
        search_contacts_custom_field_date_range,
    )
    from google_ads_ghl_paid_cohort import fetch_google_ads_daily
    from google_data import get_sessions_by_session_default_channel_group
    from meta_client import fetch_account_daily_insights

    period_end = end or date.today()
    period_start, period_end = _period_range(end=period_end, days=7)
    prior_start = period_start - timedelta(days=7)
    prior_end = period_end - timedelta(days=7)
    cur_since, cur_until = period_start.isoformat(), period_end.isoformat()
    prior_since, prior_until = prior_start.isoformat(), prior_end.isoformat()

    snap = WeeklySnapshot(period_start=period_start, period_end=period_end)
    committed_rows: list[dict] = []
    booking_rows: list[dict] = []

    def _funnel(since: str, until: str):
        return count_calendar_funnel_events(since, until)

    def _signups(since: str, until: str) -> int:
        field_id = resolve_sign_up_date_custom_field_id()
        _, _, total = search_contacts_custom_field_date_range(field_id, since, until)
        return int(total or 0)

    def _sessions(since: str, until: str) -> int:
        df = get_sessions_by_session_default_channel_group(since, until)
        return int(df["Sessions"].sum()) if df is not None and not df.empty else 0

    def _paid(since: str, until: str) -> tuple[float, float, float, float]:
        google_spend = google_leads = 0.0
        gdf = fetch_google_ads_daily(since, until)
        if gdf is not None and not gdf.empty:
            google_leads = float(gdf["discovery_calls"].sum())
            google_spend = float(gdf["cost"].sum())
        meta = fetch_account_daily_insights(since=since, until=until)
        meta_leads = float(meta["totals"]["leads"])
        meta_spend = float(meta["totals"]["spend"])
        return google_spend, meta_spend, google_leads + meta_leads, google_spend + meta_spend

    tasks = {
        "cur_funnel": lambda: _funnel(cur_since, cur_until),
        "prior_funnel": lambda: _funnel(prior_since, prior_until),
        "cur_signups": lambda: _signups(cur_since, cur_until),
        "prior_signups": lambda: _signups(prior_since, prior_until),
        "cur_sessions": lambda: _sessions(cur_since, cur_until),
        "prior_sessions": lambda: _sessions(prior_since, prior_until),
        "cur_paid": lambda: _paid(cur_since, cur_until),
        "committed": lambda: fetch_committed_yes_by_hear_about_us(cur_since, cur_until),
        "hear_about": lambda: fetch_bookings_and_meetings_by_hear_about_us(
            cur_since, cur_until
        ),
    }

    results: dict[str, object] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                snap.errors.append(f"{name}: {exc}")

    if "cur_funnel" in results:
        f = results["cur_funnel"]
        snap.bookings = f.bookings
        snap.meetings = f.meetings
    if "prior_funnel" in results:
        f = results["prior_funnel"]
        snap.prior_bookings = f.bookings
        snap.prior_meetings = f.meetings
    if "cur_signups" in results:
        snap.signups = results["cur_signups"]
    if "prior_signups" in results:
        snap.prior_signups = results["prior_signups"]
    if "cur_sessions" in results:
        snap.sessions = results["cur_sessions"]
    if "prior_sessions" in results:
        snap.prior_sessions = results["prior_sessions"]
    if "cur_paid" in results:
        g, m, leads, spend = results["cur_paid"]
        snap.google_spend, snap.meta_spend = g, m
        snap.leads_7d, snap.spend_7d = leads, spend
    if "committed" in results:
        payload = results["committed"]
        snap.committed = int(payload.get("total_committed") or 0)
        committed_rows = payload.get("rows") or []
        snap.not_set_committed = _source_count(committed_rows, "(Not set)")
        snap.wom_committed = _source_count(committed_rows, "WOM")
        snap.google_committed = _source_count(committed_rows, "Google")
    if "hear_about" in results:
        payload = results["hear_about"]
        booking_rows = (payload.get("bookings") or {}).get("rows") or []
        snap.google_bookings = _source_count(booking_rows, "Google")
        snap.wom_bookings = _source_count(booking_rows, "WOM")

    return snap


def load_pulse_weekly_report(*, end: date | None = None) -> PulseWeeklyReport:
    snap = load_weekly_snapshot(end=end)
    period_start, period_end = snap.period_start, snap.period_end

    cpa = snap.spend_7d / snap.leads_7d if snap.leads_7d else None
    cpa_display = f"~{_fmt_money(cpa)}" if cpa else "n/a"
    conv = (
        f"{snap.signups / snap.meetings * 100:.0f}%"
        if snap.meetings
        else "n/a"
    )

    bookings_chg = _pct_change(snap.bookings, snap.prior_bookings)
    meetings_chg = _pct_change(snap.meetings, snap.prior_meetings)
    signups_chg = _pct_change(snap.signups, snap.prior_signups)
    sessions_chg = _pct_change(snap.sessions, snap.prior_sessions)

    if snap.bookings < snap.prior_bookings and snap.prior_bookings > 0:
        booking_note = (
            f"Bookings {_fmt_count(snap.bookings)} ({bookings_chg or 'down'}); "
            f"prior week {_fmt_count(snap.prior_bookings)}."
        )
    else:
        booking_note = (
            f"Bookings {_fmt_count(snap.bookings)} ({bookings_chg or 'steady'})."
        )

    seasonality: list[str] = []
    if period_end.month == 6:
        seasonality = [
            "June is historically our slowest month (\"June gloom\") — factor "
            "seasonality before overreacting.",
            f"May {period_end.year} finished at {_fmt_count(_MAY_2026_BOOKINGS)} "
            "bookings (peak in recent GHL data) — tough baseline entering June.",
        ]

    working_bullets = [
        (
            f"{_fmt_count(snap.sessions)} sessions ({sessions_chg or 'steady'}); "
            f"{_fmt_count(snap.leads_7d)} paid leads at {cpa_display}/lead; "
            f"{_fmt_money(snap.spend_7d)} ad spend."
        ),
        (
            f"{conv} meeting→signup; {snap.committed} committed members; "
            f"WOM ({snap.wom_committed}) + Google ({snap.google_committed}) close best."
        ),
        (
            f"Bookings by source: Google ({snap.google_bookings}), "
            f"WOM ({snap.wom_bookings})."
        ),
    ]

    attention_bullets = list(seasonality)
    attention_bullets.append(booking_note)
    if meetings_chg and snap.meetings < snap.prior_meetings:
        attention_bullets.append(
            f"Meetings {_fmt_count(snap.meetings)} ({meetings_chg}); "
            f"signups {_fmt_count(snap.signups)} ({signups_chg or 'steady'})."
        )
    elif signups_chg and snap.signups < snap.prior_signups:
        attention_bullets.append(
            f"Signups {_fmt_count(snap.signups)} ({signups_chg}); "
            f"meetings {_fmt_count(snap.meetings)} ({meetings_chg or 'steady'})."
        )
    if snap.not_set_committed >= 3:
        attention_bullets.append(
            f"{snap.not_set_committed} committed signups missing hear-about source."
        )

    recommendations_bullets = [
        "Push organic: 2+ SEO/blog posts + IG tied to top search terms.",
        (
            f"Test ~10–15% Google ad bump (~$200–300/wk) at {cpa_display} CPA — "
            "2-week trial, judge on bookings."
        ),
        "Shore up top-of-funnel before July — traffic first, not mid-funnel fixes.",
        "5+ IG posts tied to top SEO/blog content.",
        'Tighten GHL hear-about field — cut "(Not set)" before scaling spend.',
    ]

    executive = (
        f"Marketing Pulse · week ending {period_end.strftime('%B %d')}. "
        f"Bookings {_fmt_count(snap.bookings)} ({bookings_chg or 'steady'}). "
        "When bookings lag while other stages hold, look upstream — organic traffic and "
        "awareness before mid-funnel tweaks."
    )

    metric_line = (
        f"Sessions {_fmt_count(snap.sessions)} · Bookings {_fmt_count(snap.bookings)} · "
        f"Meetings {_fmt_count(snap.meetings)} · Signups {_fmt_count(snap.signups)} · "
        f"Spend {_fmt_money(snap.spend_7d)} · CPA {cpa_display}"
    )

    headline = (
        f"Marketing Pulse · {period_start.strftime('%b %d')}–"
        f"{period_end.strftime('%b %d, %Y')}"
    )

    footnotes = [
        "7-day window ending on report date. Quick report — core GHL, paid, GA4 only.",
        "Re-run: python MWR/pulse_weekly_report.py --open",
    ]
    if snap.errors:
        footnotes.append(f"Data gaps: {'; '.join(snap.errors[:3])}")

    return PulseWeeklyReport(
        period_start=period_start,
        period_end=period_end,
        headline=headline,
        executive_summary=executive,
        metric_line=metric_line,
        narrative_blocks=[
            NarrativeBlock("What's working", working_bullets),
            NarrativeBlock("What needs attention", attention_bullets),
            NarrativeBlock("Recommendations", recommendations_bullets),
        ],
        data_errors=snap.errors,
        footnotes=footnotes,
    )


def _narrative_blocks_html(blocks: list[NarrativeBlock]) -> str:
    parts: list[str] = []
    for block in blocks:
        items = "".join(
            f"<li style='margin-bottom:8px;font-size:14px;line-height:1.55;"
            f"color:{COLORS['accent_dark']};'>{html.escape(b)}</li>"
            for b in block.bullets
        )
        parts.append(
            f"""
            <div style="margin-bottom:20px;">
              <div style="font-size:14px;font-weight:700;margin-bottom:8px;color:{COLORS['accent']};">
                {html.escape(block.title)}
              </div>
              <ul style="margin:0;padding-left:20px;">{items}</ul>
            </div>
            """
        )
    return "".join(parts)


def render_pulse_weekly_html(report: PulseWeeklyReport) -> str:
    errors_html = ""
    if report.data_errors:
        errors_html = (
            f"<p style='color:{COLORS['danger']};font-size:12px;'>"
            f"{html.escape('; '.join(report.data_errors[:5]))}</p>"
        )
    footnotes_html = "".join(
        f"<li style='margin-bottom:4px;'>{html.escape(n)}</li>"
        for n in report.footnotes
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(report.headline)}</title>
</head>
<body style="margin:0;padding:24px 16px;background:{COLORS['panel_bg']};
             font-family:Helvetica,Arial,sans-serif;color:{COLORS['accent_dark']};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="max-width:640px;margin:0 auto;background:{COLORS['card_bg']};
                border-radius:12px;border:1px solid #d8e8e2;">
    <tr>
      <td style="padding:22px 24px;border-bottom:3px solid {COLORS['accent']};">
        <div style="font-size:22px;font-weight:700;">Marketing Pulse</div>
        <div style="font-size:13px;color:{COLORS['muted']};margin-top:4px;">
          {html.escape(report.headline)}
        </div>
      </td>
    </tr>
    <tr>
      <td style="padding:20px 24px;font-size:15px;line-height:1.55;">
        {html.escape(report.executive_summary)}
      </td>
    </tr>
    <tr>
      <td style="padding:0 24px 16px;font-size:12px;color:{COLORS['muted']};">
        {html.escape(report.metric_line)}
      </td>
    </tr>
    <tr>
      <td style="padding:8px 24px 24px;">
        {_narrative_blocks_html(report.narrative_blocks)}
        {errors_html}
        <ul style="margin:16px 0 0;padding-left:18px;font-size:11px;color:{COLORS['muted']};">
          {footnotes_html}
        </ul>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def write_pulse_weekly_report(
    report: PulseWeeklyReport,
    *,
    output_dir: Path | None = None,
) -> Path:
    out_dir = output_dir or OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"marketing_pulse_weekly_{report.period_end.isoformat()}.html"
    path.write_text(render_pulse_weekly_html(report), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Marketing Pulse quick weekly report")
    parser.add_argument("--end", type=str, default=None, help="Period end (YYYY-MM-DD)")
    parser.add_argument("--open", action="store_true", help="Open HTML in browser")
    parser.add_argument("--output-dir", type=str, default=None)
    args = parser.parse_args(argv)

    period_end = date.fromisoformat(args.end) if args.end else date.today()
    print(f"Loading core metrics for week ending {period_end}…")
    report = load_pulse_weekly_report(end=period_end)
    out_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR
    path = write_pulse_weekly_report(report, output_dir=out_dir)
    print(f"Wrote {path}")

    if args.open:
        webbrowser.open(path.resolve().as_uri())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
