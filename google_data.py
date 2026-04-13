import os
from datetime import date
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Filter,
    FilterExpression,
    FilterExpressionList,
    Metric,
    RunReportRequest,
)

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

# When ``GA4_BRAND_TERMS`` is unset, match Five Journeys–style branded traffic.
_DEFAULT_BRAND_SUBSTRINGS = ("five journeys", "fivejourneys")


def _strip_env(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().strip('"').strip("'")


def _channel_filter(channel: str) -> FilterExpression:
    return FilterExpression(
        filter=Filter(
            field_name="sessionDefaultChannelGroup",
            string_filter=Filter.StringFilter(
                match_type=Filter.StringFilter.MatchType.EXACT,
                value=channel,
            ),
        )
    )


def _and_exprs(*expressions: FilterExpression) -> FilterExpression:
    return FilterExpression(
        and_group=FilterExpressionList(expressions=list(expressions))
    )


def _or_exprs(*expressions: FilterExpression) -> FilterExpression:
    return FilterExpression(
        or_group=FilterExpressionList(expressions=list(expressions))
    )


def _host_contains_filter(substring: str) -> FilterExpression:
    return FilterExpression(
        filter=Filter(
            field_name="hostName",
            string_filter=Filter.StringFilter(
                match_type=Filter.StringFilter.MatchType.CONTAINS,
                value=substring,
            ),
        )
    )


def _parse_brand_terms(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def _text_matches_brand_terms(text: str, terms: list[str]) -> bool:
    hay = (text or "").casefold()
    return any(t.casefold() in hay for t in terms)


def _run_report_paginated(
    client: BetaAnalyticsDataClient,
    *,
    property_id: str,
    dimensions: list[Dimension],
    metrics: list[Metric],
    start_date: str,
    end_date: str,
    dimension_filter: FilterExpression | None = None,
    page_size: int = 10_000,
) -> list:
    rows_out: list = []
    offset = 0
    while True:
        req_kwargs: dict = {
            "property": f"properties/{property_id}",
            "dimensions": dimensions,
            "metrics": metrics,
            "date_ranges": [DateRange(start_date=start_date, end_date=end_date)],
            "offset": offset,
            "limit": page_size,
        }
        if dimension_filter is not None:
            req_kwargs["dimension_filter"] = dimension_filter
        request = RunReportRequest(**req_kwargs)
        response = client.run_report(request)
        batch = list(response.rows)
        rows_out.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows_out


def _split_branded_sessions(rows, *, terms: list[str]) -> tuple[int, int]:
    branded = 0
    non_branded = 0
    for row in rows:
        dim_val = (
            row.dimension_values[0].value if row.dimension_values else ""
        ) or ""
        sessions = int(row.metric_values[0].value)
        if _text_matches_brand_terms(dim_val, terms):
            branded += sessions
        else:
            non_branded += sessions
    return branded, non_branded


def _effective_brand_terms(brand_terms: str | None) -> list[str]:
    parsed = _parse_brand_terms(brand_terms or os.getenv("GA4_BRAND_TERMS"))
    if parsed:
        return parsed
    return list(_DEFAULT_BRAND_SUBSTRINGS)


def _ensure_ga_credentials() -> None:
    raw = _strip_env(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
    if not raw:
        raise ValueError(
            "Set GOOGLE_APPLICATION_CREDENTIALS in .env (path to the service account JSON file)"
        )
    path = Path(raw)
    if not path.is_absolute():
        path = _PROJECT_DIR / path
    if not path.is_file():
        raise FileNotFoundError(f"Credentials file not found: {path}")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(path)


def get_ga4_data():
    _ensure_ga_credentials()
    client = BetaAnalyticsDataClient()
    property_id = _strip_env(os.getenv("GA4_PROPERTY_ID"))
    if not property_id:
        raise ValueError("Set GA4_PROPERTY_ID in .env")

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="date")],
        metrics=[Metric(name="sessions"), Metric(name="totalUsers")],
        date_ranges=[DateRange(start_date="30daysAgo", end_date="today")],
    )

    response = client.run_report(request)

    data = []
    for row in response.rows:
        data.append(
            {
                "Date": row.dimension_values[0].value,
                "GA4_Sessions": int(row.metric_values[0].value),
                "GA4_Total_Users": int(row.metric_values[1].value),
            }
        )

    df = pd.DataFrame(data)
    if df.empty:
        return df

    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    df = df.sort_values(by="Date").reset_index(drop=True)

    return df


def get_march_sessions_yoy(
    years: tuple[int, ...] = (2026, 2025, 2024, 2023),
) -> pd.DataFrame:
    """
    Total GA4 **sessions** and **new users** for March 1–31 for each calendar year
    (one API call per year; both metrics in the same request).

    Requires ``GOOGLE_APPLICATION_CREDENTIALS`` and ``GA4_PROPERTY_ID`` in ``.env``.
    """
    _ensure_ga_credentials()
    property_id = _strip_env(os.getenv("GA4_PROPERTY_ID"))
    if not property_id:
        raise ValueError("Set GA4_PROPERTY_ID in .env")

    client = BetaAnalyticsDataClient()
    records: list[dict[str, object]] = []
    for year in sorted(set(years)):
        start_date = f"{year}-03-01"
        end_date = f"{year}-03-31"
        request = RunReportRequest(
            property=f"properties/{property_id}",
            metrics=[
                Metric(name="sessions"),
                Metric(name="newUsers"),
            ],
            date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        )
        response = client.run_report(request)
        sessions = 0
        new_users = 0
        if response.rows:
            sessions = int(response.rows[0].metric_values[0].value)
            new_users = int(response.rows[0].metric_values[1].value)
        records.append(
            {
                "Year": year,
                "March_Start": start_date,
                "March_End": end_date,
                "Sessions": sessions,
                "New_Users": new_users,
            }
        )

    df = pd.DataFrame(records).sort_values("Year", ascending=False).reset_index(drop=True)
    return df


def get_march_yoy_by_session_source_medium(
    years: tuple[int, ...] = (2026, 2025, 2024, 2023),
) -> pd.DataFrame:
    """
    March 1–31 **sessions** and **new users** broken down by GA4 dimension
    ``sessionSourceMedium`` (e.g. ``google / organic``, ``google / cpc``).

    One paginated report per calendar year. Requires credentials and
    ``GA4_PROPERTY_ID`` in ``.env``.
    """
    _ensure_ga_credentials()
    property_id = _strip_env(os.getenv("GA4_PROPERTY_ID"))
    if not property_id:
        raise ValueError("Set GA4_PROPERTY_ID in .env")

    client = BetaAnalyticsDataClient()
    dims = [Dimension(name="sessionSourceMedium")]
    metrics = [Metric(name="sessions"), Metric(name="newUsers")]
    records: list[dict[str, object]] = []

    for year in sorted(set(years)):
        start_date = f"{year}-03-01"
        end_date = f"{year}-03-31"
        rows = _run_report_paginated(
            client,
            property_id=property_id,
            dimensions=dims,
            metrics=metrics,
            start_date=start_date,
            end_date=end_date,
            dimension_filter=None,
        )
        for row in rows:
            sm = (
                row.dimension_values[0].value if row.dimension_values else ""
            ) or "(not set)"
            records.append(
                {
                    "Year": year,
                    "Source_medium": sm,
                    "Sessions": int(row.metric_values[0].value),
                    "New_Users": int(row.metric_values[1].value),
                }
            )

    df = pd.DataFrame(records)
    if df.empty:
        return df
    return df.sort_values(["Year", "Sessions"], ascending=[True, False]).reset_index(
        drop=True
    )


def get_march_search_branded_sessions_yoy(
    years: tuple[int, ...] = (2026, 2025, 2024, 2023),
    *,
    brand_terms: str | None = None,
    site_host_match: str | None = None,
) -> pd.DataFrame:
    """
    March 1–31 **sessions** from **search** (Organic + Paid only), on a matching host,
    split into branded vs non-branded.

    - **Scope:** ``sessionDefaultChannelGroup`` is **Paid Search** or **Organic Search**,
      and ``hostName`` contains ``site_host_match`` (default from ``GA4_SITE_HOST_MATCH``,
      else ``fivejourneys`` so ``www.fivejourneys.com`` matches).
    - **Paid search:** classified using ``sessionGoogleAdsKeyword`` (the search term).
    - **Organic search:** Google hides almost all queries as ``(not provided)`` in GA4.
      Those sessions are split using ``pageTitle`` as a **proxy** (title contains brand
      substrings → branded).

    Override brand substrings with ``GA4_BRAND_TERMS`` in ``.env`` (comma-separated),
    or pass ``brand_terms``. If unset, defaults to **Five Journeys** / **fivejourneys**.
    """
    terms = _effective_brand_terms(brand_terms)
    host_raw = _strip_env(site_host_match or os.getenv("GA4_SITE_HOST_MATCH"))
    host_sub = host_raw if host_raw else "fivejourneys"

    _ensure_ga_credentials()
    property_id = _strip_env(os.getenv("GA4_PROPERTY_ID"))
    if not property_id:
        raise ValueError("Set GA4_PROPERTY_ID in .env")

    client = BetaAnalyticsDataClient()
    paid_filter = _and_exprs(
        _host_contains_filter(host_sub),
        _channel_filter("Paid Search"),
    )
    organic_filter = _and_exprs(
        _host_contains_filter(host_sub),
        _channel_filter("Organic Search"),
    )
    metric = [Metric(name="sessions")]

    records: list[dict[str, object]] = []
    for year in sorted(set(years)):
        start_date = f"{year}-03-01"
        end_date = f"{year}-03-31"

        paid_rows = _run_report_paginated(
            client,
            property_id=property_id,
            dimensions=[Dimension(name="sessionGoogleAdsKeyword")],
            metrics=metric,
            start_date=start_date,
            end_date=end_date,
            dimension_filter=paid_filter,
        )
        b_paid, nb_paid = _split_branded_sessions(paid_rows, terms=terms)

        organic_rows = _run_report_paginated(
            client,
            property_id=property_id,
            dimensions=[Dimension(name="pageTitle")],
            metrics=metric,
            start_date=start_date,
            end_date=end_date,
            dimension_filter=organic_filter,
        )
        b_org, nb_org = _split_branded_sessions(organic_rows, terms=terms)

        records.append(
            {
                "Year": year,
                "March_Start": start_date,
                "March_End": end_date,
                "Branded": b_paid + b_org,
                "Non_branded": nb_paid + nb_org,
                "Paid_branded": b_paid,
                "Paid_non_branded": nb_paid,
                "Organic_branded_page_title": b_org,
                "Organic_non_branded_page_title": nb_org,
            }
        )

    df = pd.DataFrame(records).sort_values("Year", ascending=False).reset_index(drop=True)
    return df


def get_q1_traffic_by_session_default_channel_group(
    years: tuple[int, ...] = (2024, 2025, 2026),
) -> pd.DataFrame:
    """
    Q1 (Jan 1–Mar 31) **sessions** and **total users** by GA4 dimension
    ``sessionDefaultChannelGroup`` (Traffic acquisition — session default channel group).

    Default ``years`` is 2024–2026. One paginated report per calendar year.
    Requires credentials and ``GA4_PROPERTY_ID``.
    """
    _ensure_ga_credentials()
    property_id = _strip_env(os.getenv("GA4_PROPERTY_ID"))
    if not property_id:
        raise ValueError("Set GA4_PROPERTY_ID in .env")

    client = BetaAnalyticsDataClient()
    dims = [Dimension(name="sessionDefaultChannelGroup")]
    metrics = [Metric(name="sessions"), Metric(name="totalUsers")]
    records: list[dict[str, object]] = []

    for year in sorted(set(years)):
        start_date = f"{year}-01-01"
        end_date = f"{year}-03-31"
        rows = _run_report_paginated(
            client,
            property_id=property_id,
            dimensions=dims,
            metrics=metrics,
            start_date=start_date,
            end_date=end_date,
            dimension_filter=None,
        )
        for row in rows:
            ch = (
                row.dimension_values[0].value if row.dimension_values else ""
            ) or "(not set)"
            records.append(
                {
                    "Year": year,
                    "Q1_Start": start_date,
                    "Q1_End": end_date,
                    "Session_default_channel_group": ch,
                    "Sessions": int(row.metric_values[0].value),
                    "Total_users": int(row.metric_values[1].value),
                }
            )

    df = pd.DataFrame(records)
    if df.empty:
        return df
    return df.sort_values(
        ["Year", "Sessions"], ascending=[True, False]
    ).reset_index(drop=True)


def get_sessions_by_session_default_channel_group(
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """
    **Sessions** and **total users** by ``sessionDefaultChannelGroup`` (Traffic
    acquisition) for ``start_date`` through ``end_date`` (inclusive, ISO
    ``YYYY-MM-DD``). One paginated report. Requires credentials and
    ``GA4_PROPERTY_ID``.
    """
    _ensure_ga_credentials()
    property_id = _strip_env(os.getenv("GA4_PROPERTY_ID"))
    if not property_id:
        raise ValueError("Set GA4_PROPERTY_ID in .env")

    client = BetaAnalyticsDataClient()
    rows = _run_report_paginated(
        client,
        property_id=property_id,
        dimensions=[Dimension(name="sessionDefaultChannelGroup")],
        metrics=[Metric(name="sessions"), Metric(name="totalUsers")],
        start_date=start_date,
        end_date=end_date,
        dimension_filter=None,
    )
    records: list[dict[str, object]] = []
    for row in rows:
        ch = (
            row.dimension_values[0].value if row.dimension_values else ""
        ) or "(not set)"
        records.append(
            {
                "Session_default_channel_group": ch,
                "Sessions": int(row.metric_values[0].value),
                "Total_users": int(row.metric_values[1].value),
            }
        )
    df = pd.DataFrame(records)
    if df.empty:
        return df
    return df.sort_values("Sessions", ascending=False).reset_index(drop=True)


def compare_session_default_channel_sessions(
    *,
    earlier_start: str,
    earlier_end: str,
    later_start: str,
    later_end: str,
) -> pd.DataFrame:
    """
    Merge two channel-group reports and compute **session** deltas
    (later minus earlier) per ``Session_default_channel_group``.
    """
    earlier = get_sessions_by_session_default_channel_group(
        earlier_start, earlier_end
    )
    later = get_sessions_by_session_default_channel_group(later_start, later_end)
    if earlier.empty and later.empty:
        return pd.DataFrame()

    earlier = earlier.rename(
        columns={
            "Sessions": "Sessions_earlier",
            "Total_users": "Total_users_earlier",
        }
    )
    later = later.rename(
        columns={
            "Sessions": "Sessions_later",
            "Total_users": "Total_users_later",
        }
    )
    merged = pd.merge(
        earlier,
        later,
        on="Session_default_channel_group",
        how="outer",
    ).fillna(0)
    for col in (
        "Sessions_earlier",
        "Sessions_later",
        "Total_users_earlier",
        "Total_users_later",
    ):
        merged[col] = merged[col].astype(int)

    merged["Session_delta"] = merged["Sessions_later"] - merged["Sessions_earlier"]
    merged["User_delta"] = merged["Total_users_later"] - merged["Total_users_earlier"]
    merged["Session_delta_pct_prior"] = (
        merged["Session_delta"]
        / merged["Sessions_earlier"].replace(0, pd.NA)
        * 100.0
    )
    return merged.sort_values("Session_delta", ascending=False).reset_index(drop=True)


def get_paid_other_sessions_by_session_source_medium(
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """
    **Paid Other** default channel only: **sessions** and **total users** by
    ``sessionSourceMedium`` (Traffic acquisition). One paginated report with
    ``sessionDefaultChannelGroup`` = **Paid Other**.
    """
    _ensure_ga_credentials()
    property_id = _strip_env(os.getenv("GA4_PROPERTY_ID"))
    if not property_id:
        raise ValueError("Set GA4_PROPERTY_ID in .env")

    client = BetaAnalyticsDataClient()
    rows = _run_report_paginated(
        client,
        property_id=property_id,
        dimensions=[Dimension(name="sessionSourceMedium")],
        metrics=[Metric(name="sessions"), Metric(name="totalUsers")],
        start_date=start_date,
        end_date=end_date,
        dimension_filter=_channel_filter("Paid Other"),
    )
    records: list[dict[str, object]] = []
    for row in rows:
        sm = (
            row.dimension_values[0].value if row.dimension_values else ""
        ) or "(not set)"
        records.append(
            {
                "Session_source_medium": sm,
                "Sessions": int(row.metric_values[0].value),
                "Total_users": int(row.metric_values[1].value),
            }
        )
    df = pd.DataFrame(records)
    if df.empty:
        return df
    return df.sort_values("Sessions", ascending=False).reset_index(drop=True)


def compare_paid_other_by_session_source_medium(
    *,
    earlier_start: str,
    earlier_end: str,
    later_start: str,
    later_end: str,
) -> pd.DataFrame:
    """
    Merge **Paid Other** ``sessionSourceMedium`` breakdowns for two date ranges;
    **Session_delta** = later minus earlier sessions per row.
    """
    earlier = get_paid_other_sessions_by_session_source_medium(
        earlier_start, earlier_end
    )
    later = get_paid_other_sessions_by_session_source_medium(later_start, later_end)
    if earlier.empty and later.empty:
        return pd.DataFrame()

    earlier = earlier.rename(
        columns={
            "Sessions": "Sessions_earlier",
            "Total_users": "Total_users_earlier",
        }
    )
    later = later.rename(
        columns={
            "Sessions": "Sessions_later",
            "Total_users": "Total_users_later",
        }
    )
    merged = pd.merge(
        earlier,
        later,
        on="Session_source_medium",
        how="outer",
    ).fillna(0)
    for col in (
        "Sessions_earlier",
        "Sessions_later",
        "Total_users_earlier",
        "Total_users_later",
    ):
        merged[col] = merged[col].astype(int)

    merged["Session_delta"] = merged["Sessions_later"] - merged["Sessions_earlier"]
    merged["User_delta"] = merged["Total_users_later"] - merged["Total_users_earlier"]
    merged["Session_delta_pct_prior"] = (
        merged["Session_delta"]
        / merged["Sessions_earlier"].replace(0, pd.NA)
        * 100.0
    )
    return merged.sort_values("Sessions_later", ascending=False).reset_index(
        drop=True
    )


def get_organic_and_paid_search_sessions_by_quarter(
    *,
    first_year: int = 2023,
    first_quarter: int = 1,
    through: str | date | None = None,
    include_all_sources: bool = True,
) -> pd.DataFrame:
    """
    **Organic Search**, **Direct**, and GA4 **paid** default channel groups
    (**Paid Search**, **Paid Social**, **Paid Video**, **Display**, **Paid Shopping**,
    **Cross-network**, **Paid Other**) — ``sessionDefaultChannelGroup`` exact
    matches — **sessions** and **total users** per calendar quarter.

    When ``include_all_sources`` is True (default), also adds **All sources**:
    property-wide **sessions** and **total users** for the same quarter windows
    (no channel filter — Traffic acquisition totals).

    Returns **long** format: one row per quarter per listed channel plus optionally
    **All sources**. Missing channel rows in a quarter are filled with zeros.

    The last quarter may be **partial** (from quarter start through ``through``,
    default today). Requires ``GOOGLE_APPLICATION_CREDENTIALS`` and
    ``GA4_PROPERTY_ID``.
    """
    if first_quarter not in (1, 2, 3, 4):
        raise ValueError("first_quarter must be 1, 2, 3, or 4")

    if isinstance(through, str):
        end_cap = date.fromisoformat(through)
    elif isinstance(through, date):
        end_cap = through
    elif through is None:
        end_cap = date.today()
    else:
        raise TypeError("through must be a date, ISO date string, or None")

    _ensure_ga_credentials()
    property_id = _strip_env(os.getenv("GA4_PROPERTY_ID"))
    if not property_id:
        raise ValueError("Set GA4_PROPERTY_ID in .env")

    channel_slice = _or_exprs(
        _channel_filter("Organic Search"),
        _channel_filter("Direct"),
        _channel_filter("Paid Search"),
        _channel_filter("Paid Social"),
        _channel_filter("Paid Video"),
        _channel_filter("Display"),
        _channel_filter("Paid Shopping"),
        _channel_filter("Cross-network"),
        _channel_filter("Paid Other"),
    )
    client = BetaAnalyticsDataClient()
    metrics = [Metric(name="sessions"), Metric(name="totalUsers")]
    dims = [Dimension(name="sessionDefaultChannelGroup")]
    channels = (
        "Organic Search",
        "Paid Search",
        "Paid Social",
        "Paid Video",
        "Display",
        "Paid Shopping",
        "Cross-network",
        "Paid Other",
        "Direct",
    )
    records: list[dict[str, object]] = []

    p = pd.Period(f"{first_year}Q{first_quarter}", freq="Q-DEC")
    end_p = pd.Period(pd.Timestamp(end_cap), freq="Q-DEC")

    while p <= end_p:
        q_start = p.start_time.date()
        q_end_natural = p.end_time.date()
        if p == end_p:
            eff_end = min(q_end_natural, end_cap)
        else:
            eff_end = q_end_natural
        if eff_end < q_start:
            break

        start_iso = q_start.isoformat()
        end_iso = eff_end.isoformat()
        rows = _run_report_paginated(
            client,
            property_id=property_id,
            dimensions=dims,
            metrics=metrics,
            start_date=start_iso,
            end_date=end_iso,
            dimension_filter=channel_slice,
        )
        by_channel: dict[str, tuple[int, int]] = {}
        for row in rows:
            ch = (
                row.dimension_values[0].value if row.dimension_values else ""
            ) or "(not set)"
            by_channel[ch] = (
                int(row.metric_values[0].value),
                int(row.metric_values[1].value),
            )

        y = int(p.year)
        qn = int(p.quarter)
        for ch in channels:
            sess, users = by_channel.get(ch, (0, 0))
            records.append(
                {
                    "Year": y,
                    "Quarter_num": qn,
                    "Quarter_label": f"{y} Q{qn}",
                    "Period_start": start_iso,
                    "Period_end": end_iso,
                    "Channel": ch,
                    "Sessions": sess,
                    "Total_users": users,
                }
            )

        if include_all_sources:
            total_rows = _run_report_paginated(
                client,
                property_id=property_id,
                dimensions=[],
                metrics=metrics,
                start_date=start_iso,
                end_date=end_iso,
                dimension_filter=None,
            )
            all_sess = 0
            all_users = 0
            if total_rows:
                all_sess = int(total_rows[0].metric_values[0].value)
                all_users = int(total_rows[0].metric_values[1].value)
            records.append(
                {
                    "Year": y,
                    "Quarter_num": qn,
                    "Quarter_label": f"{y} Q{qn}",
                    "Period_start": start_iso,
                    "Period_end": end_iso,
                    "Channel": "All sources",
                    "Sessions": all_sess,
                    "Total_users": all_users,
                }
            )

        p += 1

    df = pd.DataFrame(records)
    if df.empty:
        return df
    ch_order = pd.CategoricalDtype(
        [
            "Organic Search",
            "Paid Search",
            "Paid Social",
            "Paid Video",
            "Display",
            "Paid Shopping",
            "Cross-network",
            "Paid Other",
            "Direct",
            "All sources",
        ],
        ordered=True,
    )
    df = df.astype({"Channel": ch_order})
    return df.sort_values(["Year", "Quarter_num", "Channel"]).reset_index(
        drop=True
    )


def get_organic_search_sessions_by_quarter(
    *,
    first_year: int = 2023,
    first_quarter: int = 1,
    through: str | date | None = None,
) -> pd.DataFrame:
    """
    **Organic Search** (``sessionDefaultChannelGroup`` exact **Organic Search**)
    **sessions** and **total users** per calendar quarter from ``first_year`` Q
    ``first_quarter`` through the quarter containing ``through`` (default: today).

    The last row may be a **partial quarter** (from quarter start through
    ``through``). Requires ``GOOGLE_APPLICATION_CREDENTIALS`` and
    ``GA4_PROPERTY_ID``.
    """
    df = get_organic_and_paid_search_sessions_by_quarter(
        first_year=first_year,
        first_quarter=first_quarter,
        through=through,
        include_all_sources=False,
    )
    if df.empty:
        return df
    out = df[df["Channel"] == "Organic Search"].drop(columns=["Channel"])
    return out.reset_index(drop=True)


if __name__ == "__main__":
    print(get_ga4_data())
