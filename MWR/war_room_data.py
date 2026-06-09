"""Data loaders for the Marketing War Room dashboard."""

from __future__ import annotations

# Bump when exports or loaders change — marketing_war_room.py reloads this module
# when the revision differs (Streamlit caches imports across reruns).
WAR_ROOM_DATA_REVISION = "2026-06-09-team-ops-status-labels-v13"

import calendar
import importlib
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Streamlit keeps imported modules in memory across reruns; reload when helpers
# were added to ghl_client.py after the server started.
import ghl_client as _ghl_client

_GHL_CLIENT_REVISION = "2026-06-09-calendar-hear-about-batch-v2"
if (
    not hasattr(_ghl_client, "fetch_bookings_by_hear_about_us")
    or not hasattr(_ghl_client, "fetch_meetings_by_hear_about_us")
    or not hasattr(_ghl_client, "fetch_bookings_and_meetings_by_hear_about_us")
    or getattr(_ghl_client, "GHL_CLIENT_REVISION", None) != _GHL_CLIENT_REVISION
):
    _ghl_client = importlib.reload(_ghl_client)

from ghl_client import (  # noqa: E402
    fetch_bookings_and_meetings_by_hear_about_us,
    fetch_bookings_by_hear_about_us,
    fetch_committed_yes_by_hear_about_us,
    fetch_meetings_by_hear_about_us,
)


@dataclass
class TrendPoint:
    date: str  # YYYY-MM-DD
    value: float


@dataclass
class TrendSeries:
    """Seven-day daily series for command-strip sparklines."""

    label: str
    points: list[TrendPoint] = field(default_factory=list)
    dim_today: bool = False
    vs_prior_avg_pct: float | None = None
    wired: bool = False
    invert_spark_color: bool = False


@dataclass
class HearAboutCountRow:
    source: str
    count: int


@dataclass
class ConversionDriversMetrics:
    period_since: str = ""
    period_until: str = ""
    traffic_contributors: list[HearAboutCountRow] = field(default_factory=list)
    total_sessions_7d: int | None = None
    bookings_by_source: list[HearAboutCountRow] = field(default_factory=list)
    meetings_by_source: list[HearAboutCountRow] = field(default_factory=list)
    committed_by_source: list[HearAboutCountRow] = field(default_factory=list)
    total_bookings: int | None = None
    total_meetings: int | None = None
    total_committed: int | None = None
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class CommandStripMetrics:
    period_since: str = ""
    period_until: str = ""
    spend_7d: float | None = None
    leads_7d: float | None = None
    signups_7d: int | None = None
    sessions_7d: int | None = None
    bookings_7d: int | None = None
    meetings_7d: int | None = None
    new_contacts_7d: int | None = None
    ad_spend_mtd: float | None = None
    ad_spend_ytd: float | None = None
    signups_7d_vs_prior_pct: float | None = None
    bookings_7d_vs_prior_pct: float | None = None
    meetings_7d_vs_prior_pct: float | None = None
    ad_spend_mtd_vs_prior_pct: float | None = None
    ad_spend_ytd_vs_prior_pct: float | None = None
    spend_trend: TrendSeries | None = None
    leads_trend: TrendSeries | None = None
    sessions_trend: TrendSeries | None = None
    new_contacts_trend: TrendSeries | None = None
    ad_spend_mtd_trend: TrendSeries | None = None
    ad_spend_ytd_trend: TrendSeries | None = None
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class PaidMediaMetrics:
    period_since: str = ""
    period_until: str = ""
    google_spend_7d: float | None = None
    meta_spend_7d: float | None = None
    leads_7d: float | None = None
    cpa_7d: float | None = None
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class CrmFunnelMetrics:
    period_since: str = ""
    period_until: str = ""
    signups_7d: int | None = None
    bookings_7d: int | None = None
    meetings_7d: int | None = None
    conversion_rate: float | None = None
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class WebsiteTrafficMetrics:
    period_since: str = ""
    period_until: str = ""
    sessions_7d: int | None = None
    users_7d: int | None = None
    top_channel: str | None = None
    embed_pageviews_7d: int | None = None
    embed_page_count: int | None = None
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class OrganicSocialMetrics:
    period_since: str = ""
    period_until: str = ""
    ig_reach_7d: int | None = None
    ig_engagement_7d: int | None = None
    follower_delta_7d: int | None = None
    top_post: str | None = None
    top_post_engagement: int | None = None
    posts_in_period: int | None = None
    page_name: str | None = None
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ContentSeoMetrics:
    period_since: str = ""
    period_until: str = ""
    organic_sessions_7d: int | None = None
    blog_pageviews_7d: int | None = None
    top_landing_page: str | None = None
    top_landing_sessions: int | None = None
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


WAR_ROOM_MONDAY_BOARD_NAMES: tuple[str, ...] = (
    "Sam New To-Do List",
    "Je New To-Do List",
    "Voltaire To-Do List",
    "Lead Paramedic",
    "We Have SEO",
)

# Je's board is organized into month groups; her queue view uses the current month only.
WAR_ROOM_MONDAY_CURRENT_MONTH_GROUP_BOARDS: frozenset[str] = frozenset(
    {"Je New To-Do List"}
)


@dataclass
class StatusCountRow:
    status: str
    count: int


TEAM_OPS_STATUS_ORDER: tuple[str, ...] = (
    "Requested",
    "Working On It",
    "In Review",
    "For approval",
    "Initiated",
    "Ready for Publishing",
    "Approved",
    "Done/Published",
    "Done",
)

TEAM_OPS_CLOSED_STATUSES: frozenset[str] = frozenset(
    {
        "done",
        "complete",
        "completed",
        "finished",
        "closed",
        "won't do",
        "wont do",
        "cancelled",
        "canceled",
        "done/published",
    }
)


@dataclass
class BoardTaskSummary:
    board_name: str
    requested: int = 0
    scope_label: str = ""
    by_status: list[StatusCountRow] = field(default_factory=list)


@dataclass
class TeamOpsMetrics:
    period_since: str = ""
    period_until: str = ""
    boards: list[BoardTaskSummary] = field(default_factory=list)
    missing_boards: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


DEFAULT_WAR_ROOM_GMAIL_LABEL = "Marketing/Action"


@dataclass
class NeedsResponseItem:
    source: str
    sender: str
    preview: str
    age: str
    when: datetime | None = None


@dataclass
class NeedsResponseMetrics:
    gmail_count: int | None = None
    chat_count: int | None = None
    oldest_wait: str | None = None
    items: list[NeedsResponseItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class TaskAlertItem:
    severity: str
    title: str
    list_name: str
    due_label: str


@dataclass
class AlertsMetrics:
    overdue_count: int | None = None
    due_today_count: int | None = None
    due_soon_count: int | None = None
    items: list[TaskAlertItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def compute_vs_prior_avg(
    today_value: float | None,
    points: list[TrendPoint],
    *,
    dim_today: bool,
) -> float | None:
    """
    Compare ``today_value`` to the mean of prior complete days in the series.

    When ``dim_today`` is True, the average uses the six days before today only
    (today excluded as potentially incomplete).
    """
    if today_value is None or len(points) < 2:
        return None

    if dim_today:
        prior = points[:-1][-6:]
    else:
        prior = points[-7:]

    if not prior:
        return None

    avg = sum(p.value for p in prior) / len(prior)
    if avg == 0:
        return None
    return (today_value - avg) / avg * 100.0


def _placeholder_trend(
    label: str,
    *,
    dim_today: bool,
    invert_spark_color: bool = False,
) -> TrendSeries:
    """Fallback when daily series could not be loaded."""
    return TrendSeries(
        label=label,
        dim_today=dim_today,
        wired=False,
        invert_spark_color=invert_spark_color,
    )


def _google_mtd_spend(df) -> float | None:
    if df is None or df.empty or "cost" not in df.columns:
        return None
    return float(df["cost"].sum())


def _sum_optional(*values: float | int | None) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return float(sum(present))


def _last_n_days_range(*, as_of: date, days: int = 7) -> tuple[str, str]:
    """Inclusive calendar range covering ``days`` days ending on ``as_of``."""
    if days < 1:
        raise ValueError("days must be at least 1")
    start = as_of - timedelta(days=days - 1)
    return start.isoformat(), as_of.isoformat()


def _seven_day_dates(as_of: date) -> list[str]:
    """Ordered ISO dates for the seven-day window ending on ``as_of``."""
    return [(as_of - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)]


def _prior_seven_day_dates(as_of: date) -> list[str]:
    """Ordered ISO dates for the seven-day window immediately before the current 7d window."""
    return [(as_of - timedelta(days=offset)).isoformat() for offset in range(13, 6, -1)]


def _sum_for_dates(values_by_date: dict[str, float], dates: list[str]) -> float:
    return sum(float(values_by_date.get(day, 0.0)) for day in dates)


def _calendar_dates_inclusive(start: date, end: date) -> list[str]:
    days: list[str] = []
    cursor = start
    while cursor <= end:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def _cumulative_by_date(dates: list[str], daily: dict[str, float]) -> dict[str, float]:
    running = 0.0
    out: dict[str, float] = {}
    for day in dates:
        running += float(daily.get(day, 0.0))
        out[day] = running
    return out


def _pct_vs_prior_period(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / prior * 100.0


def _prior_month_mtd_range(as_of: date) -> tuple[str, str]:
    """Same day-of-month span in the previous calendar month (for MTD comparisons)."""
    if as_of.month == 1:
        prior_start = date(as_of.year - 1, 12, 1)
    else:
        prior_start = date(as_of.year, as_of.month - 1, 1)
    last_day = calendar.monthrange(prior_start.year, prior_start.month)[1]
    prior_end = prior_start.replace(day=min(as_of.day, last_day))
    return prior_start.isoformat(), prior_end.isoformat()


def _prior_year_ytd_range(as_of: date) -> tuple[str, str]:
    """Jan 1 through the same calendar day in the prior year (for YTD comparisons)."""
    prior_start = date(as_of.year - 1, 1, 1)
    last_day = calendar.monthrange(prior_start.year, as_of.month)[1]
    prior_end = date(as_of.year - 1, as_of.month, min(as_of.day, last_day))
    return prior_start.isoformat(), prior_end.isoformat()


def _iso_date_key(value) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def _sum_daily_dicts(dates: list[str], *series: dict[str, float]) -> dict[str, float]:
    return {day: sum(float(values.get(day, 0.0)) for values in series) for day in dates}


def _google_daily_dict(df, *, value_col: str, dates: list[str]) -> dict[str, float]:
    out = {day: 0.0 for day in dates}
    if df is None or df.empty:
        return out
    for _, row in df.iterrows():
        day = _iso_date_key(row["date"])
        if day in out:
            out[day] += float(row[value_col])
    return out


def _meta_daily_dict(daily_rows: list[dict], *, value_key: str, dates: list[str]) -> dict[str, float]:
    out = {day: 0.0 for day in dates}
    for row in daily_rows:
        day = (row.get("date_start") or "")[:10]
        if day in out:
            out[day] += float(row.get(value_key) or 0)
    return out


def _ga4_daily_dict(raw: dict[str, int], dates: list[str]) -> dict[str, float]:
    return {day: float(raw.get(day, 0)) for day in dates}


def _build_trend_series(
    label: str,
    dates: list[str],
    values_by_date: dict[str, float],
    *,
    today_value: float | None,
    dim_today: bool,
    invert_spark_color: bool = False,
) -> TrendSeries:
    points = [TrendPoint(date=day, value=float(values_by_date.get(day, 0.0))) for day in dates]
    if dates and today_value is not None:
        points[-1] = TrendPoint(date=dates[-1], value=float(today_value))
    wired = len(points) >= 2 and any(point.value > 0 for point in points)
    return TrendSeries(
        label=label,
        points=points,
        dim_today=dim_today,
        vs_prior_avg_pct=compute_vs_prior_avg(today_value, points, dim_today=dim_today),
        wired=wired,
        invert_spark_color=invert_spark_color,
    )


def _cpa(spend: float | None, leads: float | None) -> float | None:
    if spend is None or leads is None or leads <= 0:
        return None
    return spend / leads


def load_paid_media(*, as_of: date | None = None) -> PaidMediaMetrics:
    """
    Paid media panel — account-level Google Ads + Meta for the last 7 days.

    Leads = Google Ads conversions (discovery calls) + Meta lead actions.
    CPL = combined spend ÷ combined leads.
    """
    today = as_of or date.today()
    since, until = _last_n_days_range(as_of=today, days=7)
    metrics = PaidMediaMetrics(period_since=since, period_until=until)

    google_spend: float | None = None
    google_leads: float | None = None
    meta_spend: float | None = None
    meta_leads: float | None = None

    try:
        from google_ads_ghl_paid_cohort import fetch_google_ads_totals

        google = fetch_google_ads_totals(since, until)
        google_spend = google.cost
        google_leads = google.discovery_calls
    except Exception as exc:
        metrics.errors.append(f"Google Ads: {exc}")

    try:
        from meta_client import fetch_account_daily_insights

        meta = fetch_account_daily_insights(since=since, until=until)
        meta_spend = meta["totals"]["spend"]
        meta_leads = float(meta["totals"]["leads"])
        metrics.notes.append("Meta includes today; daily totals may lag until complete.")
    except Exception as exc:
        metrics.errors.append(f"Meta: {exc}")

    metrics.google_spend_7d = google_spend
    metrics.meta_spend_7d = meta_spend
    metrics.leads_7d = _sum_optional(google_leads, meta_leads)
    metrics.cpa_7d = _cpa(
        _sum_optional(google_spend, meta_spend),
        metrics.leads_7d,
    )
    return metrics


def _conversion_rate_pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator * 100.0


def load_crm_funnel(*, as_of: date | None = None) -> CrmFunnelMetrics:
    """
    CRM & funnel panel — GHL signups, bookings, and meetings for the last 7 days.

    - Signups: **Sign Up Date** custom field in range
    - Bookings: calendar appointments by **dateAdded**
    - Meetings: calendar appointments by **startTime**
    - Conv. rate: signups ÷ meetings (%)
    """
    today = as_of or date.today()
    since, until = _last_n_days_range(as_of=today, days=7)
    metrics = CrmFunnelMetrics(period_since=since, period_until=until)

    try:
        from ghl_client import (
            count_calendar_funnel_events,
            resolve_sign_up_date_custom_field_id,
            search_contacts_custom_field_date_range,
        )

        sign_up_field_id = resolve_sign_up_date_custom_field_id()
        if not sign_up_field_id:
            raise ValueError(
                "Could not resolve Sign Up Date field. Set GHL_SIGN_UP_DATE_FIELD_ID in .env."
            )

        contacts, truncated, total_signups = search_contacts_custom_field_date_range(
            sign_up_field_id,
            since,
            until,
        )
        metrics.signups_7d = total_signups or len(contacts)
        if truncated:
            metrics.notes.append("Signups count may be incomplete (pagination cap).")

        funnel = count_calendar_funnel_events(since, until)
        metrics.bookings_7d = funnel.bookings
        metrics.meetings_7d = funnel.meetings
        if funnel.calendar_api_errors:
            metrics.notes.append(
                f"Calendar funnel: {funnel.calendar_api_errors} calendar(s) failed to load."
            )
    except Exception as exc:
        metrics.errors.append(f"GHL: {exc}")

    metrics.conversion_rate = _conversion_rate_pct(
        float(metrics.signups_7d) if metrics.signups_7d is not None else None,
        float(metrics.meetings_7d) if metrics.meetings_7d is not None else None,
    )
    return metrics


_embed_paths_cache: tuple[str, ...] | None = None


def _get_embed_page_paths() -> tuple[str, ...]:
    """Discover GHL booking embed paths (cached for process lifetime)."""
    global _embed_paths_cache
    if _embed_paths_cache is None:
        from _ghl_calendar_pageviews_report import discover_embed_pages

        _embed_paths_cache = tuple(p["path"] for p in discover_embed_pages())
    return _embed_paths_cache


def load_website_traffic(*, as_of: date | None = None) -> WebsiteTrafficMetrics:
    """
    Website & traffic panel — GA4 property totals and embed-page views (last 7 days).

    - Sessions / users: property-wide traffic acquisition totals
    - Top channel: highest ``sessionDefaultChannelGroup`` by sessions
    - Embed views: ``screenPageViews`` on GHL calendar embed pages (excludes home)
    """
    today = as_of or date.today()
    since, until = _last_n_days_range(as_of=today, days=7)
    metrics = WebsiteTrafficMetrics(period_since=since, period_until=until)

    try:
        from google_data import (
            get_ga4_traffic_totals,
            get_sessions_by_session_default_channel_group,
        )

        sessions, users = get_ga4_traffic_totals(since, until)
        metrics.sessions_7d = sessions
        metrics.users_7d = users

        channels = get_sessions_by_session_default_channel_group(since, until)
        if not channels.empty:
            metrics.top_channel = str(channels.iloc[0]["Session_default_channel_group"])

        metrics.notes.append("GA4 includes today; intraday totals may be partial.")
    except Exception as exc:
        metrics.errors.append(f"GA4 traffic: {exc}")

    try:
        from google_data import get_ga4_screen_page_views_for_paths

        embed_paths = list(_get_embed_page_paths())
        metrics.embed_page_count = len(embed_paths)
        metrics.embed_pageviews_7d = get_ga4_screen_page_views_for_paths(
            embed_paths,
            since,
            until,
        )
        metrics.notes.append(
            f"Embed views = {len(embed_paths)} GHL booking pages (home excluded)."
        )
    except Exception as exc:
        metrics.errors.append(f"GA4 embed pages: {exc}")

    return metrics


def load_organic_social(*, as_of: date | None = None) -> OrganicSocialMetrics:
    """Organic social panel — Instagram reach, engagement, followers, top post (7 days)."""
    today = as_of or date.today()
    since, until = _last_n_days_range(as_of=today, days=7)
    metrics = OrganicSocialMetrics(period_since=since, period_until=until)

    try:
        from meta_organic_client import fetch_organic_social_7d

        snap = fetch_organic_social_7d(as_of=as_of)
        metrics.period_since = snap.period_since
        metrics.period_until = snap.period_until
        metrics.ig_reach_7d = snap.ig_reach_7d
        metrics.ig_engagement_7d = snap.ig_engagement_7d
        metrics.follower_delta_7d = snap.follower_delta_7d
        metrics.top_post = snap.top_post
        metrics.top_post_engagement = snap.top_post_engagement
        metrics.posts_in_period = snap.posts_in_period
        metrics.page_name = snap.page_name
        metrics.errors.extend(snap.errors)
        metrics.notes.extend(snap.notes)
    except Exception as exc:
        metrics.errors.append(f"Instagram organic: {exc}")

    return metrics


def load_content_seo(*, as_of: date | None = None) -> ContentSeoMetrics:
    """
    Content & SEO panel — organic search sessions, blog views, top landing page (7 days).
    """
    today = as_of or date.today()
    since, until = _last_n_days_range(as_of=today, days=7)
    metrics = ContentSeoMetrics(period_since=since, period_until=until)

    try:
        from google_data import get_organic_search_sessions

        metrics.organic_sessions_7d = get_organic_search_sessions(since, until)
    except Exception as exc:
        metrics.errors.append(f"GA4 organic search: {exc}")

    try:
        from google_data import get_blog_pageviews_total

        metrics.blog_pageviews_7d = get_blog_pageviews_total(since, until)
        metrics.notes.append("Blog pageviews matched to WordPress post slugs.")
    except Exception as exc:
        metrics.errors.append(f"GA4 blog: {exc}")

    try:
        from google_data import get_top_landing_page_by_sessions

        landing_page, landing_sessions = get_top_landing_page_by_sessions(since, until)
        metrics.top_landing_page = landing_page
        metrics.top_landing_sessions = landing_sessions
        metrics.notes.append("GA4 includes today; intraday totals may be partial.")
    except Exception as exc:
        metrics.errors.append(f"GA4 landing pages: {exc}")

    return metrics


def _is_open_team_ops_status(status: str) -> bool:
    return (status or "").strip().casefold() not in TEAM_OPS_CLOSED_STATUSES


def _open_team_ops_tasks(df):
    if df.empty or "status" not in df.columns:
        return df
    return df[df["status"].map(_is_open_team_ops_status)]


def _current_month_group_title(*, as_of: date) -> str:
    return f"{as_of.strftime('%B')} {as_of.year}"


def _filter_board_scope(df, board_name: str, *, as_of: date) -> tuple[object, str]:
    """Return board rows, optionally scoped to the current month group."""
    if df.empty:
        return df, ""
    if board_name not in WAR_ROOM_MONDAY_CURRENT_MONTH_GROUP_BOARDS:
        return df, ""
    if "group_title" not in df.columns:
        return df, ""

    group_title = _current_month_group_title(as_of=as_of)
    scoped = df[df["group_title"] == group_title]
    return scoped, group_title


def format_team_status_label(status: str) -> str:
    """Return the workflow Status column label verbatim (no display aliases)."""
    return (status or "").strip() or "No status"


def status_count_for(rows: list[StatusCountRow], status: str) -> int:
    target = (status or "").strip().casefold()
    for row in rows:
        if row.status.strip().casefold() == target:
            return row.count
    return 0


def _status_sort_key(status: str) -> tuple[int, str]:
    label = (status or "").strip()
    order = {name.casefold(): idx for idx, name in enumerate(TEAM_OPS_STATUS_ORDER)}
    return (order.get(label.casefold(), 100), label.casefold())


def _count_tasks_by_status(df) -> list[StatusCountRow]:
    if df.empty or "status" not in df.columns:
        return []
    counts = df["status"].value_counts()
    rows = [StatusCountRow(status=str(label), count=int(count)) for label, count in counts.items()]
    return sorted(rows, key=lambda row: _status_sort_key(row.status))


def load_team_ops(*, as_of: date | None = None) -> TeamOpsMetrics:
    """Team & projects — open Monday.com tasks by current workflow Status per board."""
    today = as_of or date.today()
    metrics = TeamOpsMetrics()

    try:
        from monday_client import fetch_items_from_boards, resolve_board_ids_by_names

        board_map, missing = resolve_board_ids_by_names(list(WAR_ROOM_MONDAY_BOARD_NAMES))
        metrics.missing_boards = missing
        if missing:
            metrics.errors.append(f"Monday boards not found: {', '.join(missing)}")

        if not board_map:
            metrics.errors.append("Monday: no scoped boards resolved.")
            return metrics

        board_ids = list(board_map.values())
        name_by_id = {board_id: name for name, board_id in board_map.items()}
        df, truncated = fetch_items_from_boards(board_ids, board_names=name_by_id)

        if any(truncated.values()):
            metrics.notes.append("Some boards hit pagination cap; counts may be incomplete.")

        open_df = _open_team_ops_tasks(df)

        summaries: list[BoardTaskSummary] = []
        for board_name in WAR_ROOM_MONDAY_BOARD_NAMES:
            board_id = board_map.get(board_name)
            if not board_id:
                continue
            board_df = open_df[open_df["board_id"] == board_id] if not open_df.empty else open_df
            board_df, scope_label = _filter_board_scope(board_df, board_name, as_of=today)
            by_status = _count_tasks_by_status(board_df)
            summaries.append(
                BoardTaskSummary(
                    board_name=board_name,
                    requested=status_count_for(by_status, "Requested"),
                    scope_label=scope_label,
                    by_status=by_status,
                )
            )

        metrics.boards = summaries
        metrics.notes.append(
            "Open tasks by current workflow Status (excludes Done/Published) · "
            "Je scoped to current month group · Status column, not Priority."
        )
    except Exception as exc:
        metrics.errors.append(f"Monday.com: {exc}")

    return metrics


def load_command_strip(*, as_of: date | None = None) -> CommandStripMetrics:
    """
    Aggregate cross-channel KPIs for the command strip.

    Headline metrics use the same rolling 7-day window as the sparklines.
    Sources:
      - Ad spend / leads: Google Ads + Meta (account level)
      - Signups: GHL Sign Up Date custom field
      - Bookings: GHL calendar events by dateAdded
      - New contacts: GHL contacts by ``dateAdded``
      - Sessions: GA4 property total
    """
    today = as_of or date.today()
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    month_start_iso = month_start.isoformat()
    year_start_iso = year_start.isoformat()
    today_iso = today.isoformat()
    trend_since, trend_until = _last_n_days_range(as_of=today, days=7)
    prior_since, prior_until = _last_n_days_range(as_of=today - timedelta(days=7), days=7)
    prior_mtd_since, prior_mtd_until = _prior_month_mtd_range(today)
    prior_ytd_since, prior_ytd_until = _prior_year_ytd_range(today)
    trend_dates = _seven_day_dates(today)
    prior_dates = _prior_seven_day_dates(today)
    compare_dates = prior_dates + trend_dates

    metrics = CommandStripMetrics(period_since=trend_since, period_until=trend_until)
    google_mtd_spend: float | None = None
    meta_mtd_spend: float | None = None
    google_ytd_spend: float | None = None
    meta_ytd_spend: float | None = None
    google_prior_mtd_spend: float | None = None
    meta_prior_mtd_spend: float | None = None
    google_prior_ytd_spend: float | None = None
    meta_prior_ytd_spend: float | None = None
    google_daily = None
    google_month_daily = None
    google_ytd_daily = None
    meta_daily: list[dict] = []
    meta_month_daily: list[dict] = []
    meta_ytd_daily: list[dict] = []
    ga4_sessions_by_date: dict[str, int] = {}

    try:
        from google_ads_ghl_paid_cohort import fetch_google_ads_daily

        google_daily = fetch_google_ads_daily(prior_since, today_iso)
        google_month_daily = fetch_google_ads_daily(month_start_iso, today_iso)
        google_ytd_daily = fetch_google_ads_daily(year_start_iso, today_iso)
        google_mtd_spend = _google_mtd_spend(google_month_daily)
        google_ytd_spend = _google_mtd_spend(google_ytd_daily)
        google_prior_mtd_spend = _google_mtd_spend(
            fetch_google_ads_daily(prior_mtd_since, prior_mtd_until)
        )
        google_prior_ytd_spend = _google_mtd_spend(
            fetch_google_ads_daily(prior_ytd_since, prior_ytd_until)
        )
    except Exception as exc:
        metrics.errors.append(f"Google Ads: {exc}")

    try:
        from meta_client import fetch_account_daily_insights

        meta = fetch_account_daily_insights(since=prior_since, until=today_iso)
        meta_daily = meta["daily"]
        meta_mtd = fetch_account_daily_insights(since=month_start_iso, until=today_iso)
        meta_month_daily = meta_mtd["daily"]
        meta_mtd_spend = meta_mtd["totals"]["spend"]
        meta_ytd = fetch_account_daily_insights(since=year_start_iso, until=today_iso)
        meta_ytd_daily = meta_ytd["daily"]
        meta_ytd_spend = meta_ytd["totals"]["spend"]
        meta_prior_mtd = fetch_account_daily_insights(
            since=prior_mtd_since,
            until=prior_mtd_until,
        )
        meta_prior_mtd_spend = meta_prior_mtd["totals"]["spend"]
        meta_prior_ytd = fetch_account_daily_insights(
            since=prior_ytd_since,
            until=prior_ytd_until,
        )
        meta_prior_ytd_spend = meta_prior_ytd["totals"]["spend"]
        metrics.notes.append("Meta daily totals may lag until the day completes.")
    except Exception as exc:
        metrics.errors.append(f"Meta: {exc}")

    metrics.ad_spend_mtd = _sum_optional(google_mtd_spend, meta_mtd_spend)
    metrics.ad_spend_ytd = _sum_optional(google_ytd_spend, meta_ytd_spend)
    metrics.ad_spend_mtd_vs_prior_pct = _pct_vs_prior_period(
        metrics.ad_spend_mtd,
        _sum_optional(google_prior_mtd_spend, meta_prior_mtd_spend),
    )
    metrics.ad_spend_ytd_vs_prior_pct = _pct_vs_prior_period(
        metrics.ad_spend_ytd,
        _sum_optional(google_prior_ytd_spend, meta_prior_ytd_spend),
    )

    sign_up_field_id: str | None = None
    try:
        from ghl_client import resolve_sign_up_date_custom_field_id

        sign_up_field_id = resolve_sign_up_date_custom_field_id()
        if not sign_up_field_id:
            raise ValueError(
                "Could not resolve Sign Up Date field. Set GHL_SIGN_UP_DATE_FIELD_ID in .env."
            )
    except Exception as exc:
        metrics.errors.append(f"GHL signups: {exc}")

    if sign_up_field_id:
        try:
            from ghl_client import search_contacts_custom_field_date_range

            contacts, truncated, total_signups = search_contacts_custom_field_date_range(
                sign_up_field_id,
                trend_since,
                trend_until,
            )
            metrics.signups_7d = total_signups or len(contacts)
            if truncated:
                metrics.notes.append("GHL signups count may be incomplete (pagination cap).")
        except Exception as exc:
            metrics.errors.append(f"GHL signups: {exc}")

    current_meetings: int | None = None
    try:
        from ghl_client import count_calendar_funnel_events

        funnel = count_calendar_funnel_events(trend_since, trend_until)
        metrics.bookings_7d = funnel.bookings
        metrics.meetings_7d = funnel.meetings
        current_meetings = funnel.meetings
        if funnel.calendar_api_errors:
            metrics.notes.append(
                f"GHL calendar: {funnel.calendar_api_errors} calendar(s) failed to load."
            )
    except Exception as exc:
        metrics.errors.append(f"GHL bookings/meetings: {exc}")

    prior_signups: int | None = None
    prior_bookings: int | None = None
    prior_meetings: int | None = None
    if sign_up_field_id:
        try:
            from ghl_client import search_contacts_custom_field_date_range

            prior_contacts, prior_truncated, prior_signups = (
                search_contacts_custom_field_date_range(
                    sign_up_field_id,
                    prior_since,
                    prior_until,
                )
            )
            prior_signups = prior_signups or len(prior_contacts)
            if prior_truncated:
                metrics.notes.append(
                    "Prior-period GHL signups may be incomplete (pagination cap)."
                )
        except Exception as exc:
            metrics.errors.append(f"GHL prior signups: {exc}")

    try:
        from ghl_client import count_calendar_funnel_events

        prior_funnel = count_calendar_funnel_events(prior_since, prior_until)
        prior_bookings = prior_funnel.bookings
        prior_meetings = prior_funnel.meetings
        if prior_funnel.calendar_api_errors:
            metrics.notes.append(
                f"Prior-period GHL calendar: {prior_funnel.calendar_api_errors} calendar(s) failed."
            )
    except Exception as exc:
        metrics.errors.append(f"GHL prior bookings/meetings: {exc}")

    metrics.signups_7d_vs_prior_pct = _pct_vs_prior_period(
        float(metrics.signups_7d) if metrics.signups_7d is not None else None,
        float(prior_signups) if prior_signups is not None else None,
    )
    metrics.bookings_7d_vs_prior_pct = _pct_vs_prior_period(
        float(metrics.bookings_7d) if metrics.bookings_7d is not None else None,
        float(prior_bookings) if prior_bookings is not None else None,
    )
    metrics.meetings_7d_vs_prior_pct = _pct_vs_prior_period(
        float(current_meetings) if current_meetings is not None else None,
        float(prior_meetings) if prior_meetings is not None else None,
    )

    new_contacts_by_date: dict[str, float] = {day: 0.0 for day in compare_dates}
    try:
        from ghl_client import contact_created_utc_date_str, fetch_contacts_date_added_complete

        new_contacts, new_contacts_truncated = fetch_contacts_date_added_complete(
            prior_since,
            trend_until,
        )
        for contact in new_contacts:
            created = contact_created_utc_date_str(contact)
            if created in new_contacts_by_date:
                new_contacts_by_date[created] += 1.0
        metrics.new_contacts_7d = int(_sum_for_dates(new_contacts_by_date, trend_dates))
        if new_contacts_truncated:
            metrics.notes.append(
                "GHL new contacts count may be incomplete (pagination cap)."
            )
    except Exception as exc:
        metrics.errors.append(f"GHL new contacts: {exc}")

    try:
        from google_data import get_ga4_sessions_by_date

        ga4_sessions_by_date = get_ga4_sessions_by_date(prior_since, trend_until)
    except Exception as exc:
        metrics.errors.append(f"GA4: {exc}")

    google_spend = _google_daily_dict(google_daily, value_col="cost", dates=compare_dates)
    google_leads = _google_daily_dict(
        google_daily,
        value_col="discovery_calls",
        dates=compare_dates,
    )
    meta_spend = _meta_daily_dict(meta_daily, value_key="spend", dates=compare_dates)
    meta_leads = _meta_daily_dict(meta_daily, value_key="leads", dates=compare_dates)
    spend_by_date = _sum_daily_dicts(trend_dates, google_spend, meta_spend)
    leads_by_date = _sum_daily_dicts(trend_dates, google_leads, meta_leads)
    sessions_by_date = _ga4_daily_dict(ga4_sessions_by_date, trend_dates)
    spend_all = _sum_daily_dicts(compare_dates, google_spend, meta_spend)

    metrics.spend_7d = _sum_for_dates(spend_by_date, trend_dates)
    metrics.leads_7d = _sum_for_dates(leads_by_date, trend_dates)
    metrics.sessions_7d = int(_sum_for_dates(sessions_by_date, trend_dates))

    prior_spend_7d = _sum_for_dates(spend_all, prior_dates)
    prior_leads_7d = _sum_for_dates(
        _sum_daily_dicts(prior_dates, google_leads, meta_leads),
        prior_dates,
    )
    prior_sessions_7d = int(
        _sum_for_dates(_ga4_daily_dict(ga4_sessions_by_date, prior_dates), prior_dates)
    )

    metrics.spend_trend = _build_trend_series(
        "Ad spend",
        trend_dates,
        spend_by_date,
        today_value=None,
        dim_today=True,
        invert_spark_color=True,
    )
    metrics.leads_trend = _build_trend_series(
        "Leads",
        trend_dates,
        leads_by_date,
        today_value=None,
        dim_today=True,
    )
    metrics.sessions_trend = _build_trend_series(
        "GA4 sessions",
        trend_dates,
        sessions_by_date,
        today_value=None,
        dim_today=True,
    )
    if metrics.spend_trend:
        metrics.spend_trend.vs_prior_avg_pct = _pct_vs_prior_period(
            metrics.spend_7d, prior_spend_7d
        )
    if metrics.leads_trend:
        metrics.leads_trend.vs_prior_avg_pct = _pct_vs_prior_period(
            metrics.leads_7d, prior_leads_7d
        )
    if metrics.sessions_trend:
        metrics.sessions_trend.vs_prior_avg_pct = _pct_vs_prior_period(
            float(metrics.sessions_7d), float(prior_sessions_7d)
        )

    prior_new_contacts_7d = int(_sum_for_dates(new_contacts_by_date, prior_dates))
    metrics.new_contacts_trend = _build_trend_series(
        "New contacts",
        trend_dates,
        new_contacts_by_date,
        today_value=None,
        dim_today=False,
    )
    if metrics.new_contacts_trend:
        metrics.new_contacts_trend.vs_prior_avg_pct = _pct_vs_prior_period(
            float(metrics.new_contacts_7d) if metrics.new_contacts_7d is not None else None,
            float(prior_new_contacts_7d),
        )

    month_dates = _calendar_dates_inclusive(month_start, today)
    google_month_spend = _google_daily_dict(
        google_month_daily, value_col="cost", dates=month_dates
    )
    meta_month_spend = _meta_daily_dict(
        meta_month_daily, value_key="spend", dates=month_dates
    )
    mtd_daily_spend = _sum_daily_dicts(month_dates, google_month_spend, meta_month_spend)
    mtd_cumulative = _cumulative_by_date(month_dates, mtd_daily_spend)
    metrics.ad_spend_mtd_trend = _build_trend_series(
        "Ad spend MTD",
        month_dates,
        mtd_cumulative,
        today_value=metrics.ad_spend_mtd,
        dim_today=True,
        invert_spark_color=True,
    )
    if metrics.ad_spend_mtd_trend:
        metrics.ad_spend_mtd_trend.vs_prior_avg_pct = metrics.ad_spend_mtd_vs_prior_pct

    year_dates = _calendar_dates_inclusive(year_start, today)
    google_year_spend = _google_daily_dict(
        google_ytd_daily, value_col="cost", dates=year_dates
    )
    meta_year_spend = _meta_daily_dict(
        meta_ytd_daily, value_key="spend", dates=year_dates
    )
    ytd_daily_spend = _sum_daily_dicts(year_dates, google_year_spend, meta_year_spend)
    ytd_cumulative = _cumulative_by_date(year_dates, ytd_daily_spend)
    metrics.ad_spend_ytd_trend = _build_trend_series(
        "Ad spend YTD",
        year_dates,
        ytd_cumulative,
        today_value=metrics.ad_spend_ytd,
        dim_today=True,
        invert_spark_color=True,
    )
    if metrics.ad_spend_ytd_trend:
        metrics.ad_spend_ytd_trend.vs_prior_avg_pct = metrics.ad_spend_ytd_vs_prior_pct

    if not metrics.spend_trend.wired:
        metrics.spend_trend = _placeholder_trend(
            "Ad spend", dim_today=True, invert_spark_color=True
        )
    if not metrics.leads_trend.wired:
        metrics.leads_trend = _placeholder_trend("Leads", dim_today=True)
    if not metrics.sessions_trend.wired:
        metrics.sessions_trend = _placeholder_trend("GA4 sessions", dim_today=True)
    if not metrics.new_contacts_trend.wired:
        metrics.new_contacts_trend = _placeholder_trend("New contacts", dim_today=False)
    if not metrics.ad_spend_mtd_trend.wired:
        metrics.ad_spend_mtd_trend = _placeholder_trend(
            "Ad spend MTD", dim_today=True, invert_spark_color=True
        )
    if not metrics.ad_spend_ytd_trend or not metrics.ad_spend_ytd_trend.wired:
        metrics.ad_spend_ytd_trend = _placeholder_trend(
            "Ad spend YTD", dim_today=True, invert_spark_color=True
        )

    return metrics


def _format_hear_about_source_label(source: str) -> str:
    text = (source or "").strip()
    if not text:
        return "(Not set)"
    fold = text.casefold()
    if "word of mouth" in fold:
        return "WOM"
    if fold.startswith("3rd party"):
        return "3rd party"
    compact = fold.replace(" ", "")
    if "chatgpt" in compact and "ai" in fold:
        return "LLMs"
    if "tiktok" in fold and ("linkedin" in fold or "linedin" in fold or "other social" in fold):
        return "Other Social"
    return text


def _rows_from_ghl_payload(rows: list[dict]) -> list[HearAboutCountRow]:
    return [
        HearAboutCountRow(
            source=_format_hear_about_source_label(str(row.get("source") or "")),
            count=int(row.get("count") or 0),
        )
        for row in rows
    ]


def load_conversion_drivers(*, as_of: date | None = None) -> ConversionDriversMetrics:
    """
    Discovery Call & Conversion Drivers — GA4 traffic contributors, GHL bookings,
    meetings, and committed members grouped by **How did you hear about us?**
    (last 7 days).
    """
    today = as_of or date.today()
    since, until = _last_n_days_range(as_of=today, days=7)
    metrics = ConversionDriversMetrics(period_since=since, period_until=until)

    try:
        from google_data import get_sessions_by_session_default_channel_group

        channels = get_sessions_by_session_default_channel_group(since, until)
        if channels.empty:
            metrics.notes.append("GA4 returned no channel rows for traffic contributors.")
        else:
            metrics.total_sessions_7d = int(channels["Sessions"].sum())
            top5 = channels.head(5)
            rows: list[HearAboutCountRow] = [
                HearAboutCountRow(
                    source=str(row["Session_default_channel_group"]),
                    count=int(row["Sessions"]),
                )
                for _, row in top5.iterrows()
            ]
            other_sessions = int(channels.iloc[5:]["Sessions"].sum()) if len(channels) > 5 else 0
            if other_sessions > 0:
                rows.append(HearAboutCountRow(source="Other", count=other_sessions))
            metrics.traffic_contributors = rows
    except Exception as exc:
        metrics.errors.append(f"GA4 traffic: {exc}")

    try:
        calendar = fetch_bookings_and_meetings_by_hear_about_us(since, until)
        bookings = calendar.get("bookings") or {}
        meetings = calendar.get("meetings") or {}

        metrics.bookings_by_source = _rows_from_ghl_payload(bookings.get("rows") or [])
        metrics.total_bookings = int(bookings.get("total") or 0)
        metrics.meetings_by_source = _rows_from_ghl_payload(meetings.get("rows") or [])
        metrics.total_meetings = int(meetings.get("total") or 0)

        if calendar.get("calendar_api_errors"):
            metrics.notes.append(
                f"Calendar: {calendar['calendar_api_errors']} calendar(s) failed to load."
            )
        missing_booking_links = int(bookings.get("missing_contact_link") or 0)
        missing_meeting_links = int(meetings.get("missing_contact_link") or 0)
        if missing_booking_links:
            metrics.notes.append(
                f"{missing_booking_links} booking(s) had no linked contact."
            )
        if missing_meeting_links:
            metrics.notes.append(
                f"{missing_meeting_links} meeting(s) had no linked contact."
            )
        booking_lookup_failures = int(bookings.get("contact_lookup_failures") or 0)
        meeting_lookup_failures = int(meetings.get("contact_lookup_failures") or 0)
        if booking_lookup_failures or meeting_lookup_failures:
            metrics.notes.append(
                f"{booking_lookup_failures + meeting_lookup_failures} contact(s) "
                "could not be loaded from GHL."
            )
    except Exception as exc:
        metrics.errors.append(f"GHL bookings/meetings: {exc}")

    try:
        committed = fetch_committed_yes_by_hear_about_us(since, until)
        metrics.committed_by_source = _rows_from_ghl_payload(committed.get("rows") or [])
        metrics.total_committed = int(committed.get("total_committed") or 0)
        if committed.get("truncated_pages"):
            metrics.notes.append(
                "Committed cohort may be incomplete (GHL pagination cap on sign-up search)."
            )
        excluded = int(committed.get("excluded_not_committed_yes") or 0)
        if excluded:
            metrics.notes.append(
                f"{excluded} contact(s) with sign-up in range excluded (Committed? ≠ Yes)."
            )
        metrics.notes.append(
            "Signups by source = Sign Up Date in range and Committed? = Yes."
        )
    except Exception as exc:
        metrics.errors.append(f"GHL committed: {exc}")

    return metrics


def _format_wait_age(when: datetime | None, *, now: datetime | None = None) -> str:
    if when is None:
        return "—"
    current = now or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delta = current - when.astimezone(timezone.utc)
    if delta.days >= 1:
        return f"{delta.days}d"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours}h"
    minutes = max(1, delta.seconds // 60)
    return f"{minutes}m"


def _parse_chat_space_names(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def load_needs_response() -> NeedsResponseMetrics:
    """
    Marketing-only inbound queue — Gmail label + Google Chat @mentions.

    Environment:
      WAR_ROOM_GMAIL_LABEL — unread messages in this label (default Marketing/Action)
      WAR_ROOM_GMAIL_QUERY — optional full Gmail query (overrides label)
      WAR_ROOM_CHAT_SPACES — comma-separated display-name substrings; empty = named spaces
    """
    metrics = NeedsResponseMetrics()
    now = datetime.now(timezone.utc)
    queue: list[NeedsResponseItem] = []

    gmail_label = (os.getenv("WAR_ROOM_GMAIL_LABEL") or DEFAULT_WAR_ROOM_GMAIL_LABEL).strip()
    gmail_query = (os.getenv("WAR_ROOM_GMAIL_QUERY") or "").strip()
    chat_spaces = _parse_chat_space_names(os.getenv("WAR_ROOM_CHAT_SPACES"))

    try:
        from gmail_client import (
            fetch_unread_by_label,
            fetch_unread_by_query,
            gmail_service,
        )

        service = gmail_service()
        if gmail_query:
            gmail_messages = fetch_unread_by_query(service, query=gmail_query)
            metrics.notes.append(f"Gmail queue: custom query ({gmail_query}).")
        else:
            gmail_messages = fetch_unread_by_label(service, label=gmail_label)
            metrics.notes.append(f"Gmail queue: unread in label “{gmail_label}”.")

        metrics.gmail_count = len(gmail_messages)
        for msg in gmail_messages:
            preview = msg.subject
            if msg.snippet:
                preview = f"{msg.subject} — {msg.snippet[:120]}"
            queue.append(
                NeedsResponseItem(
                    source="Gmail",
                    sender=msg.from_addr or "—",
                    preview=preview,
                    age=_format_wait_age(msg.date, now=now),
                    when=msg.date,
                )
            )
    except Exception as exc:
        metrics.errors.append(f"Gmail: {exc}")

    try:
        import importlib

        import google_chat_client as _google_chat_client
        import verify_google_chat_connection as _chat_auth

        _CHAT_AUTH_REVISION = "2026-06-05-scope-fix-v1"
        if getattr(_chat_auth, "CHAT_AUTH_REVISION", None) != _CHAT_AUTH_REVISION:
            _chat_auth = importlib.reload(_chat_auth)
            _google_chat_client = importlib.reload(_google_chat_client)

        from google_chat_client import fetch_unread_mentions, chat_service

        chat = chat_service()
        chat_rows = fetch_unread_mentions(chat, space_display_names=chat_spaces or None)
        metrics.chat_count = len(chat_rows)
        if chat_spaces:
            metrics.notes.append(
                f"Chat: unread @mentions in spaces matching {', '.join(chat_spaces)}."
            )
        else:
            metrics.notes.append("Chat: unread @mentions in named spaces (set WAR_ROOM_CHAT_SPACES to narrow).")

        for row in chat_rows:
            queue.append(
                NeedsResponseItem(
                    source=f"Chat · {row.space_display_name}",
                    sender=row.sender_name,
                    preview=row.preview,
                    age=_format_wait_age(row.create_time, now=now),
                    when=row.create_time,
                )
            )
    except Exception as exc:
        metrics.errors.append(f"Google Chat: {exc}")

    queue.sort(
        key=lambda item: item.when or datetime.min.replace(tzinfo=timezone.utc)
    )
    metrics.items = queue

    oldest = next((item for item in queue if item.when), None)
    metrics.oldest_wait = oldest.age if oldest else None

    return metrics


def _format_task_due_label(due: date, *, severity: str, today: date) -> str:
    if severity == "Overdue":
        days = max(1, (today - due).days)
        return f"{days}d overdue"
    return f"{due.strftime('%b')} {due.day}"


def _parse_tasks_list_filters(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def load_alerts(*, as_of: date | None = None) -> AlertsMetrics:
    """
    Google Tasks alerts — overdue, due today, and due soon (tasks with due dates only).

    Environment:
      WAR_ROOM_TASKS_LISTS — optional comma-separated task list titles or IDs to include
      WAR_ROOM_TASKS_DUE_SOON_DAYS — days ahead for “due soon” (default 3)
    """
    today = as_of or date.today()
    metrics = AlertsMetrics()
    list_filters = _parse_tasks_list_filters(os.getenv("WAR_ROOM_TASKS_LISTS"))
    try:
        due_soon_days = int(os.getenv("WAR_ROOM_TASKS_DUE_SOON_DAYS") or "3")
    except ValueError:
        due_soon_days = 3

    try:
        from google_tasks_client import TaskAlertSeverity, fetch_task_alerts, tasks_service

        service = tasks_service()
        rows = fetch_task_alerts(
            service,
            list_filters=list_filters or None,
            due_soon_days=due_soon_days,
            today=today,
        )
        metrics.overdue_count = sum(
            1 for row in rows if row.severity == TaskAlertSeverity.OVERDUE
        )
        metrics.due_today_count = sum(
            1 for row in rows if row.severity == TaskAlertSeverity.DUE_TODAY
        )
        metrics.due_soon_count = sum(
            1 for row in rows if row.severity == TaskAlertSeverity.DUE_SOON
        )
        metrics.items = [
            TaskAlertItem(
                severity=row.severity.value,
                title=row.title,
                list_name=row.list_name,
                due_label=_format_task_due_label(
                    row.due_date,
                    severity=row.severity.value,
                    today=today,
                ),
            )
            for row in rows
        ]
        if list_filters:
            metrics.notes.append(
                f"Tasks: lists matching {', '.join(list_filters)} · due within {due_soon_days}d."
            )
        else:
            metrics.notes.append(
                f"Tasks: all lists · overdue, due today, or due within {due_soon_days}d (due date required)."
            )
    except Exception as exc:
        metrics.errors.append(f"Google Tasks: {exc}")

    return metrics
