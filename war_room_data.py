"""Data loaders for the Marketing War Room dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


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


@dataclass
class CommandStripMetrics:
    spend_today: float | None = None
    leads_today: float | None = None
    signups_today: int | None = None
    sessions_today: int | None = None
    bookings_today: int | None = None
    ad_spend_mtd: float | None = None
    spend_trend: TrendSeries | None = None
    leads_trend: TrendSeries | None = None
    sessions_trend: TrendSeries | None = None
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
    "Communication Plan",
)


@dataclass
class BoardTaskSummary:
    board_name: str
    open_tasks: int = 0
    due_this_week: int = 0
    overdue: int = 0


@dataclass
class TeamOpsMetrics:
    open_tasks: int | None = None
    due_this_week: int | None = None
    overdue: int | None = None
    boards: list[BoardTaskSummary] = field(default_factory=list)
    missing_boards: list[str] = field(default_factory=list)
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


def _placeholder_trend(label: str, *, dim_today: bool) -> TrendSeries:
    """Sparkline shell — wire daily series in a follow-up pass."""
    return TrendSeries(label=label, dim_today=dim_today, wired=False)


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


def _cpa(spend: float | None, leads: float | None) -> float | None:
    if spend is None or leads is None or leads <= 0:
        return None
    return spend / leads


def load_paid_media(*, as_of: date | None = None) -> PaidMediaMetrics:
    """
    Paid media panel — account-level Google Ads + Meta for the last 7 days.

    Leads = Google Ads conversions (discovery calls) + Meta lead actions.
    CPA = combined spend ÷ combined leads.
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
    - Conv. rate: bookings ÷ signups (%)
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
        float(metrics.bookings_7d) if metrics.bookings_7d is not None else None,
        float(metrics.signups_7d) if metrics.signups_7d is not None else None,
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
    from meta_organic_client import fetch_organic_social_7d

    snap = fetch_organic_social_7d(as_of=as_of)
    return OrganicSocialMetrics(
        period_since=snap.period_since,
        period_until=snap.period_until,
        ig_reach_7d=snap.ig_reach_7d,
        ig_engagement_7d=snap.ig_engagement_7d,
        follower_delta_7d=snap.follower_delta_7d,
        top_post=snap.top_post,
        top_post_engagement=snap.top_post_engagement,
        posts_in_period=snap.posts_in_period,
        page_name=snap.page_name,
        errors=list(snap.errors),
        notes=list(snap.notes),
    )


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


def _is_open_task_status(status: str) -> bool:
    from monday_client import DONE_STATUS_LABELS

    return (status or "").strip().casefold() not in DONE_STATUS_LABELS


def _due_this_week(due_iso: str, *, today: date) -> bool:
    if not due_iso:
        return False
    try:
        due = date.fromisoformat(due_iso[:10])
    except ValueError:
        return False
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    return week_start <= due <= week_end


def _summarize_board_tasks(df, *, today: date) -> tuple[int, int, int]:
    if df.empty:
        return 0, 0, 0
    open_df = df[df["status"].map(_is_open_task_status)]
    open_count = len(open_df)
    due_week = int(open_df["due_date"].map(lambda d: _due_this_week(d, today=today)).sum())
    overdue = int(open_df["overdue"].sum()) if "overdue" in open_df.columns else 0
    return open_count, due_week, overdue


def load_team_ops(*, as_of: date | None = None) -> TeamOpsMetrics:
    """Team & projects panel — scoped Monday.com boards for Sam, Je, and Communication Plan."""
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

        total_open = 0
        total_due = 0
        total_overdue = 0
        summaries: list[BoardTaskSummary] = []

        for board_name in WAR_ROOM_MONDAY_BOARD_NAMES:
            board_id = board_map.get(board_name)
            if not board_id:
                continue
            board_df = df[df["board_id"] == board_id] if not df.empty else df
            open_count, due_week, overdue = _summarize_board_tasks(board_df, today=today)
            summaries.append(
                BoardTaskSummary(
                    board_name=board_name,
                    open_tasks=open_count,
                    due_this_week=due_week,
                    overdue=overdue,
                )
            )
            total_open += open_count
            total_due += due_week
            total_overdue += overdue

        metrics.boards = summaries
        metrics.open_tasks = total_open
        metrics.due_this_week = total_due
        metrics.overdue = total_overdue
        metrics.notes.append(
            "Open = not done · Due this week = Mon–Sun calendar week · Overdue = past due, not done."
        )
    except Exception as exc:
        metrics.errors.append(f"Monday.com: {exc}")

    return metrics


def load_command_strip(*, as_of: date | None = None) -> CommandStripMetrics:
    """
    Aggregate cross-channel KPIs for the command strip.

    Sources:
      - Ad spend / leads: Google Ads + Meta (account level)
      - Signups: GHL Sign Up Date custom field
      - Bookings: GHL calendar events by dateAdded
      - Sessions: GA4 property total
    """
    today = as_of or date.today()
    month_start = today.replace(day=1)
    today_iso = today.isoformat()
    month_start_iso = month_start.isoformat()

    metrics = CommandStripMetrics()
    google_today_spend: float | None = None
    google_today_leads: float | None = None
    google_mtd_spend: float | None = None
    meta_today_spend: float | None = None
    meta_today_leads: float | None = None
    meta_mtd_spend: float | None = None

    try:
        from google_ads_ghl_paid_cohort import fetch_google_ads_totals

        google_today = fetch_google_ads_totals(today_iso, today_iso)
        google_today_spend = google_today.cost
        google_today_leads = google_today.discovery_calls

        google_mtd = fetch_google_ads_totals(month_start_iso, today_iso)
        google_mtd_spend = google_mtd.cost
    except Exception as exc:
        metrics.errors.append(f"Google Ads: {exc}")

    try:
        from meta_client import fetch_account_daily_insights

        meta = fetch_account_daily_insights(since=month_start_iso, until=today_iso)
        meta_mtd_spend = meta["totals"]["spend"]
        today_row = next(
            (row for row in meta["daily"] if row["date_start"] == today_iso),
            None,
        )
        if today_row:
            meta_today_spend = today_row["spend"]
            meta_today_leads = float(today_row["leads"])
        metrics.notes.append("Meta daily totals may lag until the day completes.")
    except Exception as exc:
        metrics.errors.append(f"Meta: {exc}")

    metrics.spend_today = _sum_optional(google_today_spend, meta_today_spend)
    metrics.leads_today = _sum_optional(google_today_leads, meta_today_leads)
    metrics.ad_spend_mtd = _sum_optional(google_mtd_spend, meta_mtd_spend)

    try:
        from ghl_client import (
            count_calendar_bookings_by_date_added,
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
            today_iso,
            today_iso,
        )
        metrics.signups_today = total_signups or len(contacts)
        if truncated:
            metrics.notes.append("GHL signups count may be incomplete (pagination cap).")

        bookings, calendar_errors = count_calendar_bookings_by_date_added(
            today_iso,
            today_iso,
        )
        metrics.bookings_today = bookings
        if calendar_errors:
            metrics.notes.append(
                f"GHL bookings: {calendar_errors} calendar(s) failed to load."
            )
    except Exception as exc:
        metrics.errors.append(f"GHL: {exc}")

    try:
        from google_data import get_ga4_sessions_total

        metrics.sessions_today = get_ga4_sessions_total("today", "today")
    except Exception as exc:
        metrics.errors.append(f"GA4: {exc}")

    metrics.spend_trend = _placeholder_trend(
        "Ad spend",
        dim_today=True,  # includes Meta; today may be incomplete
    )
    metrics.leads_trend = _placeholder_trend(
        "Leads",
        dim_today=True,  # includes Meta lead actions
    )
    metrics.sessions_trend = _placeholder_trend(
        "GA4 sessions",
        dim_today=True,  # GA4 intraday is partial
    )

    return metrics
