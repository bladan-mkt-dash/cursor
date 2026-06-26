"""
Period in review — live dashboard trends, Marketing War Room, and completed Google Tasks.

Combines:
  - Digital Channel Live Dashboard → Trends over time section only (8850 defaults)
  - Marketing War Room panels (8851)
  - Completed Google Tasks checked off in the date range

    python "Op Reports/period_in_review_report.py" --start 2026-06-20 --end 2026-06-25 --serve
"""

from __future__ import annotations

import argparse
import html
import sys
import webbrowser
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd

from _bootstrap import OP_REPORTS_DIR, PROJECT_ROOT, setup

setup()

_DC_LIVE_DIR = PROJECT_ROOT / "DC-Live-Dash"
_MWR_DIR = PROJECT_ROOT / "MWR"
for path in (_DC_LIVE_DIR, _MWR_DIR):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from activity_summary_report import CompletedTask, fetch_completed_tasks  # noqa: E402
from digital_channel_live_data import (  # noqa: E402
    MEMBERSHIP_LEVELS,
    apply_dashboard_ghl_attribution,
    build_trend_chart_monthlies,
    load_dashboard_bundle,
)
from google_tasks_client import tasks_service  # noqa: E402
from war_room_data import (  # noqa: E402
    CommandStripMetrics,
    ConversionDriversMetrics,
    CrmFunnelMetrics,
    PaidMediaMetrics,
    WebsiteTrafficMetrics,
    OrganicSocialMetrics,
    ContentSeoMetrics,
    TeamOpsMetrics,
    AlertsMetrics,
    load_alerts,
    load_command_strip,
    load_content_seo,
    load_conversion_drivers,
    load_crm_funnel,
    load_organic_social,
    load_paid_media,
    load_team_ops,
    load_website_traffic,
)

OUTPUT_DIR = OP_REPORTS_DIR / "outputs"


@dataclass
class DcTrendsSnapshot:
    scorecard: dict[str, float | None]
    funnel: dict[str, float]
    trend_rows: list[dict[str, object]]
    attribution: str
    funnel_note: str
    loader_notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class PeriodInReviewReport:
    period_start: date
    period_end: date
    dc_trends: DcTrendsSnapshot
    command: CommandStripMetrics
    conversion: ConversionDriversMetrics
    paid: PaidMediaMetrics
    crm: CrmFunnelMetrics
    traffic: WebsiteTrafficMetrics
    organic: OrganicSocialMetrics
    content: ContentSeoMetrics
    team: TeamOpsMetrics
    alerts: AlertsMetrics
    completed_tasks: list[CompletedTask]
    errors: list[str] = field(default_factory=list)


def _fmt_currency(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}"


def _fmt_int(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.0f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}%"


def _fmt_pct_change(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f}%"


def _time_period_summary(
    monthly: pd.DataFrame, since: date, until: date
) -> pd.DataFrame:
    if monthly.empty:
        return monthly
    out = monthly.copy()
    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()
    out = out[(out["month"] >= since_month) & (out["month"] <= until_month)]
    out = out.copy()
    out["period_label"] = out["month"].dt.strftime("%b %Y")
    return out


def _weighted_scorecard_metrics(
    df: pd.DataFrame,
    *,
    cpl_monthly: pd.DataFrame,
) -> dict[str, float | None]:
    if df.empty:
        return {
            "spend": None,
            "clicks": None,
            "cpc": None,
            "leads": None,
            "cpl": None,
            "dcs": None,
            "cpdc": None,
            "conversions": None,
            "cac": None,
            "lead_to_patient_pct": None,
        }
    spend = float(df["spend"].sum())
    clicks = float(df["clicks"].sum())
    if not cpl_monthly.empty and "leads" in cpl_monthly.columns:
        leads = float(cpl_monthly["leads"].sum())
    else:
        leads = float(df["leads"].sum())
    dcs = float(df["dcs"].sum())
    conversions = float(df["conversions"].sum())
    return {
        "spend": spend,
        "clicks": clicks,
        "cpc": spend / clicks if clicks else None,
        "leads": leads,
        "cpl": spend / leads if leads else None,
        "dcs": dcs,
        "cpdc": spend / dcs if dcs else None,
        "conversions": conversions,
        "cac": spend / conversions if conversions else None,
        "lead_to_patient_pct": (conversions / dcs * 100.0) if dcs else None,
    }


def load_dc_trends_snapshot(start: date, end: date) -> DcTrendsSnapshot:
    """Trends over time metrics using dashboard defaults (hear-about, all channels)."""
    errors: list[str] = []
    since_iso = start.isoformat()
    until_iso = end.isoformat()

    try:
        (
            raw_df,
            notes,
            _lead_summary,
            conv_by_level_df,
            unallocated_conv_df,
            wom_conv_df,
            tracker_conv_by_level_df,
            tracker_unallocated_conv_df,
            combined_conv_by_level_df,
            combined_unallocated_conv_df,
            sheet_months,
            channel_month_leads,
            cpl_channel_month_leads,
            unallocated_leads_by_attr,
            _sheet_signup_totals,
            _ghl_signups_by_month,
            _sheet_dcs_totals,
            _ghl_dcs_by_month,
            _ghl_leads_org_by_month,
            _ghl_signups_by_level_df,
            funnel_df,
            funnel_notes,
        ) = load_dashboard_bundle(
            since_iso,
            until_iso,
            funnel_since=since_iso,
            funnel_until=until_iso,
        )
    except Exception as exc:
        return DcTrendsSnapshot(
            scorecard={},
            funnel={},
            trend_rows=[],
            attribution="Hear-about",
            funnel_note="",
            errors=[f"Digital Channel Live: {exc}"],
        )

    if raw_df.empty:
        return DcTrendsSnapshot(
            scorecard={},
            funnel={},
            trend_rows=[],
            attribution="Hear-about",
            funnel_note="No campaign data for the selected range.",
            loader_notes=list(notes),
            errors=["No campaign data returned for the selected date range."],
        )

    use_hear_about = True
    use_tracker = False
    selected_channels = sorted(raw_df["channel"].dropna().unique())
    selected_campaigns = sorted(raw_df["campaign"].dropna().unique())
    selected_creatives = sorted(raw_df["creative_type"].dropna().unique())
    selected_meta_types = sorted(
        raw_df.loc[raw_df["channel"] == "FB/IG", "fb_ig_type"].dropna().unique()
    )
    selected_meta_types = [t for t in selected_meta_types if str(t).strip()]
    selected_levels = list(MEMBERSHIP_LEVELS)

    attr_kwargs = dict(
        conv_by_level_df=conv_by_level_df,
        tracker_conv_by_level_df=tracker_conv_by_level_df,
        combined_conv_by_level_df=combined_conv_by_level_df,
        unallocated_conv_df=unallocated_conv_df,
        tracker_unallocated_conv_df=tracker_unallocated_conv_df,
        combined_unallocated_conv_df=combined_unallocated_conv_df,
        wom_conv_df=wom_conv_df,
        include_wom_signups=False,
        include_other_signups=False,
        sheet_signup_months=set(sheet_months),
        selected_levels=selected_levels,
    )

    filtered = apply_dashboard_ghl_attribution(
        raw_df,
        use_hear_about=use_hear_about,
        use_tracker=use_tracker,
        **attr_kwargs,
    )
    mask = (
        filtered["channel"].isin(selected_channels)
        & (filtered["campaign"].isin(selected_campaigns))
        & (filtered["creative_type"].isin(selected_creatives))
    )
    if selected_meta_types:
        meta_type_mask = (filtered["channel"] != "FB/IG") | (
            filtered["fb_ig_type"].isin(selected_meta_types)
        )
        mask &= meta_type_mask

    df = filtered.loc[mask].copy()
    if "month" not in df.columns:
        df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()

    trend_monthlies = build_trend_chart_monthlies(
        df,
        channel_month_leads,
        cpl_channel_month_leads,
        unallocated_leads_by_attr,
        selected_channels=selected_channels,
        use_hear_about=use_hear_about,
        use_tracker=use_tracker,
        include_organic=False,
    )

    spend_period = _time_period_summary(trend_monthlies.spend, start, end)
    cpl_period = _time_period_summary(trend_monthlies.cpl, start, end)
    dcs_period = _time_period_summary(trend_monthlies.dcs, start, end)
    signups_period = _time_period_summary(trend_monthlies.signups, start, end)

    since_month = pd.Timestamp(start).to_period("M").to_timestamp()
    until_month = pd.Timestamp(end).to_period("M").to_timestamp()
    cpl_for_scorecard = trend_monthlies.cpl.copy()
    if not cpl_for_scorecard.empty:
        cpl_for_scorecard = cpl_for_scorecard[
            (cpl_for_scorecard["month"] >= since_month)
            & (cpl_for_scorecard["month"] <= until_month)
        ]

    scores = _weighted_scorecard_metrics(df, cpl_monthly=cpl_for_scorecard)

    funnel_totals = {"leads": 0.0, "dcs": 0.0, "signups": 0.0, "terminations": 0.0}
    if not funnel_df.empty:
        if "terminations" not in funnel_df.columns:
            try:
                from funnel_over_time_data import load_consolidated_terminations_monthly

                term_by_month, _ = load_consolidated_terminations_monthly(
                    since_iso, until_iso
                )
                funnel_df = funnel_df.copy()
                funnel_df["terminations"] = funnel_df["month"].apply(
                    lambda m: float(
                        term_by_month.get(
                            pd.Timestamp(m).to_period("M").to_timestamp(), 0
                        )
                    )
                )
            except Exception as exc:
                errors.append(f"Funnel terminations: {exc}")

        for col in funnel_totals:
            if col in funnel_df.columns:
                funnel_totals[col] = float(funnel_df[col].sum())

    span_days = (end - start).days + 1
    same_month = start.replace(day=1) == end.replace(day=1)
    funnel_note = (
        "Funnel chart is org-wide and monthly — values reflect full calendar month(s) "
        "touching the range, not daily proration."
        if same_month and span_days < 28
        else "Funnel chart is org-wide (all channels, not filtered by attribution toggles)."
    )

    trend_rows: list[dict[str, object]] = []
    for label, frame in (
        ("Spend", spend_period),
        ("CPL", cpl_period),
        ("DCs", dcs_period),
        ("Signups", signups_period),
    ):
        if frame.empty:
            continue
        for _, row in frame.iterrows():
            entry: dict[str, object] = {
                "metric": label,
                "period": str(row.get("period_label", "")),
            }
            if label == "Spend":
                entry["value"] = _fmt_currency(float(row.get("spend", 0)))
            elif label == "CPL":
                spend_val = float(row.get("spend", 0))
                leads_val = float(row.get("leads", 0))
                cpl_val = spend_val / leads_val if leads_val else None
                entry["value"] = _fmt_currency(cpl_val)
                entry["detail"] = f"spend {_fmt_currency(spend_val)}, leads {_fmt_int(leads_val)}"
            elif label == "DCs":
                entry["value"] = _fmt_int(row.get("dcs", 0))
            else:
                entry["value"] = _fmt_int(row.get("conversions", row.get("signups", 0)))
            trend_rows.append(entry)

    return DcTrendsSnapshot(
        scorecard=scores,
        funnel=funnel_totals,
        trend_rows=trend_rows,
        attribution="Hear-about (Google / FB/IG self-reported)",
        funnel_note=funnel_note,
        loader_notes=list(notes) + list(funnel_notes),
        errors=errors,
    )


def load_period_in_review_report(*, start: date, end: date) -> PeriodInReviewReport:
    errors: list[str] = []
    period_kwargs = dict(period_start=start, period_end=end, as_of=end)

    dc_trends = load_dc_trends_snapshot(start, end)
    errors.extend(dc_trends.errors)

    command = load_command_strip(**period_kwargs)
    conversion = load_conversion_drivers(**period_kwargs)
    paid = load_paid_media(**period_kwargs)
    crm = load_crm_funnel(**period_kwargs)
    traffic = load_website_traffic(**period_kwargs)
    organic = load_organic_social(**period_kwargs)
    content = load_content_seo(**period_kwargs)
    team = load_team_ops(as_of=end)
    alerts = load_alerts(as_of=end)

    completed: list[CompletedTask] = []
    try:
        completed = fetch_completed_tasks(tasks_service(), start=start, end=end)
    except Exception as exc:
        errors.append(f"Google Tasks: {exc}")

    for block in (command, conversion, paid, crm, traffic, organic, content, team, alerts):
        errors.extend(getattr(block, "errors", []))

    return PeriodInReviewReport(
        period_start=start,
        period_end=end,
        dc_trends=dc_trends,
        command=command,
        conversion=conversion,
        paid=paid,
        crm=crm,
        traffic=traffic,
        organic=organic,
        content=content,
        team=team,
        alerts=alerts,
        completed_tasks=completed,
        errors=errors,
    )


def _tasks_by_list(tasks: list[CompletedTask]) -> list[tuple[str, list[str]]]:
    grouped: dict[str, list[tuple[date, str]]] = defaultdict(list)
    for task in tasks:
        grouped[task.list_name].append((task.completed_date, task.title))
    sections: list[tuple[str, list[str]]] = []
    for list_name in sorted(grouped, key=str.casefold):
        rows = sorted(grouped[list_name], key=lambda row: (row[0], row[1].casefold()))
        bullets = [
            f"{title} ({completed.strftime('%b %d')})"
            for completed, title in rows
        ]
        sections.append((list_name, bullets))
    return sections


def _metric_table(rows: list[tuple[str, str]]) -> str:
    cells = "".join(
        f"<tr><th>{html.escape(label)}</th><td>{value}</td></tr>"
        for label, value in rows
    )
    return f"<table class='metrics'>{cells}</table>"


def _bullets(items: list[str]) -> str:
    if not items:
        return "<p><em>None in this period.</em></p>"
    rows = "".join(f"<li>{html.escape(item)}</li>" for item in items)
    return f"<ul>{rows}</ul>"


def _hear_about_table(rows) -> str:
    if not rows:
        return "<p><em>No rows.</em></p>"
    body = "".join(
        f"<tr><td>{html.escape(row.source)}</td>"
        f"<td>{row.count}</td>"
        f"<td>{_fmt_pct_change(row.vs_prior_pct)}</td></tr>"
        for row in rows
    )
    return (
        "<table class='metrics'>"
        "<tr><th>Source</th><th>Count</th><th>vs prior</th></tr>"
        f"{body}</table>"
    )


def render_period_in_review_html(report: PeriodInReviewReport) -> str:
    title = (
        f"Period in Review — {report.period_start.strftime('%b %d')}–"
        f"{report.period_end.strftime('%b %d, %Y')}"
    )
    dc = report.dc_trends
    scores = dc.scorecard

    scorecard_rows = [
        ("Spend", _fmt_currency(scores.get("spend"))),
        ("Clicks", _fmt_int(scores.get("clicks"))),
        ("Cost per click", _fmt_currency(scores.get("cpc"))),
        ("Leads", _fmt_int(scores.get("leads"))),
        ("Cost per lead", _fmt_currency(scores.get("cpl"))),
        ("DCs", _fmt_int(scores.get("dcs"))),
        ("Avg. $ per DC", _fmt_currency(scores.get("cpdc"))),
        ("Signups", _fmt_int(scores.get("conversions"))),
        ("Avg. CPA", _fmt_currency(scores.get("cac"))),
        ("Signup to DC %", _fmt_pct(scores.get("lead_to_patient_pct"))),
    ]

    funnel_rows = [
        ("Leads", _fmt_int(dc.funnel.get("leads"))),
        ("DCs", _fmt_int(dc.funnel.get("dcs"))),
        ("Signups", _fmt_int(dc.funnel.get("signups"))),
        ("Terminations", _fmt_int(dc.funnel.get("terminations"))),
    ]

    trend_html = ""
    if dc.trend_rows:
        body = "".join(
            f"<tr><td>{html.escape(str(row['metric']))}</td>"
            f"<td>{html.escape(str(row['period']))}</td>"
            f"<td>{row['value']}</td>"
            f"<td>{html.escape(str(row.get('detail', '')))}</td></tr>"
            for row in dc.trend_rows
        )
        trend_html = (
            "<table class='metrics'>"
            "<tr><th>Chart</th><th>Period</th><th>Value</th><th>Detail</th></tr>"
            f"{body}</table>"
        )

    cmd = report.command
    command_rows = [
        ("Ad spend", _fmt_currency(cmd.spend_7d)),
        ("Leads (ads)", _fmt_int(cmd.leads_7d)),
        ("Signups (GHL)", _fmt_int(cmd.signups_7d)),
        ("Bookings", _fmt_int(cmd.bookings_7d)),
        ("Meetings", _fmt_int(cmd.meetings_7d)),
        ("New contacts", _fmt_int(cmd.new_contacts_7d)),
        ("GA4 sessions", _fmt_int(cmd.sessions_7d)),
        ("Ad spend MTD", _fmt_currency(cmd.ad_spend_mtd)),
        ("Ad spend YTD", _fmt_currency(cmd.ad_spend_ytd)),
    ]

    paid = report.paid
    paid_rows = [
        ("Google spend", _fmt_currency(paid.google_spend_7d)),
        ("Meta spend", _fmt_currency(paid.meta_spend_7d)),
        ("Combined leads", _fmt_int(paid.leads_7d)),
        ("CPA (spend ÷ leads)", _fmt_currency(paid.cpa_7d)),
    ]

    crm = report.crm
    crm_rows = [
        ("Signups", _fmt_int(crm.signups_7d)),
        ("Bookings", _fmt_int(crm.bookings_7d)),
        ("Meetings", _fmt_int(crm.meetings_7d)),
        ("Conv. rate (signups ÷ meetings)", _fmt_pct(crm.conversion_rate)),
    ]

    traffic = report.traffic
    traffic_rows = [
        ("Sessions", _fmt_int(traffic.sessions_7d)),
        ("Users", _fmt_int(traffic.users_7d)),
        ("Top channel", html.escape(traffic.top_channel or "—")),
        ("Embed pageviews", _fmt_int(traffic.embed_pageviews_7d)),
    ]

    organic = report.organic
    organic_rows = [
        ("IG reach", _fmt_int(organic.ig_reach_7d)),
        ("IG engagement", _fmt_int(organic.ig_engagement_7d)),
        ("Follower delta", _fmt_int(organic.follower_delta_7d)),
        ("Posts in period", _fmt_int(organic.posts_in_period)),
        ("Top post", html.escape(organic.top_post or "—")),
    ]

    content = report.content
    content_rows = [
        ("Organic search sessions", _fmt_int(content.organic_sessions_7d)),
        ("Blog pageviews", _fmt_int(content.blog_pageviews_7d)),
        ("Top landing page", html.escape(content.top_landing_page or "—")),
        ("Landing sessions", _fmt_int(content.top_landing_sessions)),
    ]

    conv = report.conversion
    team = report.team
    alerts = report.alerts

    pay_period_html = ""
    if team.pay_period_hours:
        pay_rows = "".join(
            f"<tr><td>{html.escape(row.person)}</td>"
            f"<td>{row.hours:.1f}</td>"
            f"<td>{row.task_count}</td></tr>"
            for row in team.pay_period_hours
        )
        pay_period_html = (
            f"<p>Pay period {html.escape(team.pay_period_start or '')} → "
            f"{html.escape(team.pay_period_end or '')} · "
            f"total {team.pay_period_total_hours:.1f}h</p>"
            "<table class='metrics'>"
            "<tr><th>Person</th><th>Hours</th><th>Tasks</th></tr>"
            f"{pay_rows}</table>"
        )

    team_board_html = ""
    for board in team.boards:
        open_count = len(board.tasks_by_bucket.get("Open", []))
        team_board_html += (
            f"<p><strong>{html.escape(board.board_name)}</strong> — "
            f"{open_count} open · {board.requested} requested</p>"
        )

    alert_items = [
        f"{item.severity}: {item.title} ({item.list_name}, {item.due_label})"
        for item in alerts.items[:12]
    ]

    tasks_html = ""
    for list_name, bullets in _tasks_by_list(report.completed_tasks):
        tasks_html += f"<h3>{html.escape(list_name)}</h3>\n{_bullets(bullets)}\n"

    notes: list[str] = []
    notes.append(
        f"Digital Channel Live — Trends over time only; attribution: {dc.attribution}. "
        f"{dc.funnel_note}"
    )
    notes.append(
        f"Marketing War Room metrics use {cmd.period_since} → {cmd.period_until} "
        "(same window as localhost :8851 when period controls match)."
    )
    notes.extend(cmd.notes)
    notes.extend(conv.notes)
    notes.extend(organic.notes)

    errors_html = ""
    if report.errors:
        errors_html = (
            "<p class='errors'>"
            + html.escape("; ".join(dict.fromkeys(report.errors)))
            + "</p>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: Arial, sans-serif; font-size: 10pt; margin: 20px; line-height: 1.45; color: #222; }}
h1 {{ font-size: 14pt; margin: 0 0 4px 0; }}
h2 {{ font-size: 11pt; margin: 20px 0 8px 0; border-bottom: 1px solid #ccc; padding-bottom: 4px; }}
h3 {{ font-size: 10pt; margin: 12px 0 4px 0; }}
.meta {{ color: #555; margin-bottom: 16px; }}
.metrics {{ border-collapse: collapse; margin: 8px 0 12px 0; }}
.metrics th, .metrics td {{ border: 1px solid #ddd; padding: 4px 10px; text-align: left; }}
.metrics th {{ background: #f5f5f5; font-weight: bold; }}
.notes {{ font-size: 9pt; color: #666; margin-top: 24px; }}
.errors {{ color: #a00; }}
ul {{ margin: 4px 0 12px 0; padding-left: 20px; }}
.two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
@media (max-width: 900px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<h1>{html.escape(title)}</h1>
<p class="meta">Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · {len(report.completed_tasks)} completed tasks · sourced from live loaders (not scraped HTML)</p>
{errors_html}

<h2>Digital Channel Live — Trends over time</h2>
<p><em>Matches localhost :8850 Trends section defaults (all channels, hear-about attribution).</em></p>
<h3>Scorecard</h3>
{_metric_table(scorecard_rows)}
<h3>Org-wide funnel</h3>
<p class="meta">{html.escape(dc.funnel_note)}</p>
{_metric_table(funnel_rows)}
<h3>Trend charts (period breakdown)</h3>
{trend_html or '<p><em>No trend rows for this range.</em></p>'}

<h2>Marketing War Room</h2>
<p><em>Matches localhost :8851 panels for {html.escape(cmd.period_since)} → {html.escape(cmd.period_until)}.</em></p>
<h3>Command strip</h3>
{_metric_table(command_rows)}
<h3>Conversion drivers</h3>
<p>Sessions: {_fmt_int(conv.total_sessions_7d)} ({_fmt_pct_change(conv.total_sessions_7d_vs_prior_pct)} vs prior)</p>
<p>Bookings: {_fmt_int(conv.total_bookings)} · Meetings: {_fmt_int(conv.total_meetings)} · Committed signups: {_fmt_int(conv.total_committed)}</p>
<h4>Traffic contributors</h4>
{_hear_about_table(conv.traffic_contributors)}
<h4>Bookings by hear-about</h4>
{_hear_about_table(conv.bookings_by_source)}
<h4>Meetings by hear-about</h4>
{_hear_about_table(conv.meetings_by_source)}
<h4>Committed signups by hear-about</h4>
{_hear_about_table(conv.committed_by_source)}

<div class="two-col">
<div>
<h3>Paid media</h3>
{_metric_table(paid_rows)}
<h3>CRM funnel</h3>
{_metric_table(crm_rows)}
<h3>Website traffic</h3>
{_metric_table(traffic_rows)}
</div>
<div>
<h3>Organic social</h3>
{_metric_table(organic_rows)}
<h3>Content &amp; SEO</h3>
{_metric_table(content_rows)}
<h3>Team &amp; projects (snapshot)</h3>
{team_board_html or '<p><em>No board data.</em></p>'}
{pay_period_html}
<h3>Task alerts (as of {html.escape(report.period_end.isoformat())})</h3>
<p>Overdue {alerts.overdue_count} · Due today {alerts.due_today_count} · Due soon {alerts.due_soon_count}</p>
{_bullets(alert_items)}
</div>
</div>

<h2>Completed Google Tasks</h2>
<p class="meta">Tasks checked off {report.period_start.isoformat()} through {report.period_end.isoformat()}.</p>
{tasks_html or '<p><em>No completed tasks in this period.</em></p>'}

<div class="notes">
<p><strong>Notes</strong></p>
<ul>
{"".join(f"<li>{html.escape(note)}</li>" for note in notes if note)}
</ul>
</div>
</body>
</html>"""


def _default_output_path(start: date, end: date) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / f"period_in_review_{start.isoformat()}_{end.isoformat()}.html"


def _serve_directory(directory: Path, port: int) -> None:
    directory = directory.resolve()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving {directory} at http://127.0.0.1:{port}/")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Period in review report")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 6, 20))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 6, 25))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--serve", action="store_true", help="Serve outputs folder on localhost")
    parser.add_argument("--port", type=int, default=8853)
    parser.add_argument("--open", action="store_true", help="Open report in browser after write")
    args = parser.parse_args()

    if args.start > args.end:
        raise SystemExit("Start date must be on or before end date.")

    print(f"Loading period in review {args.start} through {args.end}...")
    report = load_period_in_review_report(start=args.start, end=args.end)
    html_text = render_period_in_review_html(report)

    out_path = args.output or _default_output_path(args.start, args.end)
    out_path.write_text(html_text, encoding="utf-8")
    print(f"Wrote {out_path}")

    if args.open:
        webbrowser.open(out_path.as_uri())

    if args.serve:
        _serve_directory(OUTPUT_DIR, args.port)


if __name__ == "__main__":
    main()
