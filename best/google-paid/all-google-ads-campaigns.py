from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import re

import altair as alt
import pandas as pd
import streamlit as st
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

DEFAULT_GOOGLE_ADS_CUSTOMER_ID = "5504078633"
DEFAULT_GOOGLE_ADS_LOGIN_CUSTOMER_ID = "9759824543"
CAMPAIGN_NAME = "Leads-Performance Max March '26 Restart"
PROJECT_DIR = Path(__file__).resolve().parent


def _load_ads_client() -> GoogleAdsClient:
    client = GoogleAdsClient.load_from_storage(path=str(PROJECT_DIR / "google-ads.yaml"))
    client.login_customer_id = DEFAULT_GOOGLE_ADS_LOGIN_CUSTOMER_ID
    return client


def _escape_gaql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _to_date_string(d: date) -> str:
    return d.isoformat()


@st.cache_data(show_spinner=False, ttl=900)
def fetch_campaign_names() -> list[str]:
    customer_id = DEFAULT_GOOGLE_ADS_CUSTOMER_ID.replace("-", "").strip()
    query = """
        SELECT
            campaign.name
        FROM campaign
        WHERE campaign.status != 'REMOVED'
        ORDER BY campaign.name
    """
    client = _load_ads_client()
    service = client.get_service("GoogleAdsService")
    names: set[str] = set()
    try:
        stream = service.search_stream(customer_id=customer_id, query=query)
        for batch in stream:
            for row in batch.results:
                if row.campaign.name:
                    names.add(str(row.campaign.name))
    except GoogleAdsException as ex:
        msg = "\n".join(err.message for err in ex.failure.errors)
        raise RuntimeError(f"Google Ads API error:\n{msg}") from ex
    return sorted(names)


@st.cache_data(show_spinner=False, ttl=900)
def fetch_campaign_daily(campaign_name: str, start_date: date, end_date: date) -> pd.DataFrame:
    customer_id = DEFAULT_GOOGLE_ADS_CUSTOMER_ID.replace("-", "").strip()
    escaped_campaign = _escape_gaql_string(campaign_name)
    query = f"""
        SELECT
            segments.date,
            campaign.name,
            metrics.impressions,
            metrics.clicks,
            metrics.conversions,
            metrics.cost_micros
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          AND campaign.name = '{escaped_campaign}'
          AND segments.date BETWEEN '{start_date.isoformat()}' AND '{end_date.isoformat()}'
        ORDER BY segments.date
    """

    client = _load_ads_client()
    service = client.get_service("GoogleAdsService")
    rows: list[dict] = []
    try:
        stream = service.search_stream(customer_id=customer_id, query=query)
        for batch in stream:
            for row in batch.results:
                rows.append(
                    {
                        "date": pd.to_datetime(row.segments.date),
                        "impressions": int(row.metrics.impressions or 0),
                        "clicks": int(row.metrics.clicks or 0),
                        "conversions": float(row.metrics.conversions or 0),
                        "cost": float(row.metrics.cost_micros or 0) / 1_000_000.0,
                    }
                )
    except GoogleAdsException as ex:
        msg = "\n".join(err.message for err in ex.failure.errors)
        raise RuntimeError(f"Google Ads API error:\n{msg}") from ex

    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True) if rows else pd.DataFrame()


@st.cache_data(show_spinner=False, ttl=900)
def fetch_campaign_daily_limits(
    campaign_name: str, start_date: date, end_date: date
) -> pd.DataFrame:
    customer_id = DEFAULT_GOOGLE_ADS_CUSTOMER_ID.replace("-", "").strip()
    escaped_campaign = _escape_gaql_string(campaign_name)
    query = f"""
        SELECT
            segments.date,
            campaign_budget.amount_micros,
            campaign.target_cpa.target_cpa_micros,
            campaign.maximize_conversions.target_cpa_micros
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          AND campaign.name = '{escaped_campaign}'
          AND segments.date BETWEEN '{start_date.isoformat()}' AND '{end_date.isoformat()}'
        ORDER BY segments.date
    """
    client = _load_ads_client()
    service = client.get_service("GoogleAdsService")
    rows: list[dict] = []
    try:
        stream = service.search_stream(customer_id=customer_id, query=query)
        for batch in stream:
            for row in batch.results:
                rows.append(
                    {
                        "date": pd.to_datetime(row.segments.date),
                        "daily_budget_limit": float(row.campaign_budget.amount_micros or 0) / 1_000_000.0,
                        "max_cpa_limit": float(
                            row.campaign.target_cpa.target_cpa_micros
                            or row.campaign.maximize_conversions.target_cpa_micros
                            or 0
                        )
                        / 1_000_000.0,
                    }
                )
    except GoogleAdsException as ex:
        msg = "\n".join(err.message for err in ex.failure.errors)
        raise RuntimeError(f"Google Ads API error:\n{msg}") from ex

    if not rows:
        return pd.DataFrame(columns=["date", "daily_budget_limit", "max_cpa_limit"])
    d = pd.DataFrame(rows).sort_values("date").drop_duplicates(subset=["date"], keep="last")
    d["daily_budget_limit"] = d["daily_budget_limit"].replace(0, pd.NA).ffill()
    d["max_cpa_limit"] = d["max_cpa_limit"].replace(0, pd.NA).ffill()
    return d.reset_index(drop=True)


@st.cache_data(show_spinner=False, ttl=900)
def fetch_campaign_current_limits(campaign_name: str) -> tuple[float | None, float | None]:
    customer_id = DEFAULT_GOOGLE_ADS_CUSTOMER_ID.replace("-", "").strip()
    escaped_campaign = _escape_gaql_string(campaign_name)
    query = f"""
        SELECT
            campaign_budget.amount_micros,
            campaign.target_cpa.target_cpa_micros,
            campaign.maximize_conversions.target_cpa_micros
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          AND campaign.name = '{escaped_campaign}'
        LIMIT 1
    """
    client = _load_ads_client()
    service = client.get_service("GoogleAdsService")
    try:
        stream = service.search_stream(customer_id=customer_id, query=query)
        for batch in stream:
            for row in batch.results:
                budget = (
                    float(row.campaign_budget.amount_micros) / 1_000_000.0
                    if row.campaign_budget.amount_micros
                    else None
                )
                cpa = row.campaign.target_cpa.target_cpa_micros or row.campaign.maximize_conversions.target_cpa_micros
                cpa_value = float(cpa) / 1_000_000.0 if cpa else None
                return budget, cpa_value
    except GoogleAdsException as ex:
        msg = "\n".join(err.message for err in ex.failure.errors)
        raise RuntimeError(f"Google Ads API error:\n{msg}") from ex
    return None, None


@st.cache_data(show_spinner=False, ttl=900)
def fetch_campaign_identity(campaign_name: str) -> tuple[str | None, str | None]:
    customer_id = DEFAULT_GOOGLE_ADS_CUSTOMER_ID.replace("-", "").strip()
    escaped_campaign = _escape_gaql_string(campaign_name)
    query = f"""
        SELECT
            campaign.id,
            campaign_budget.resource_name
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          AND campaign.name = '{escaped_campaign}'
        LIMIT 1
    """
    client = _load_ads_client()
    service = client.get_service("GoogleAdsService")
    try:
        stream = service.search_stream(customer_id=customer_id, query=query)
        for batch in stream:
            for row in batch.results:
                return str(row.campaign.id), str(row.campaign_budget.resource_name or "")
    except GoogleAdsException as ex:
        msg = "\n".join(err.message for err in ex.failure.errors)
        raise RuntimeError(f"Google Ads API error:\n{msg}") from ex
    return None, None


@st.cache_data(show_spinner=False, ttl=900)
def fetch_campaign_limit_change_events(
    campaign_name: str, start_date: date, end_date: date, cache_version: str = "v2"
) -> pd.DataFrame:
    customer_id = DEFAULT_GOOGLE_ADS_CUSTOMER_ID.replace("-", "").strip()
    campaign_id, budget_resource_name = fetch_campaign_identity(campaign_name)
    if not campaign_id:
        return pd.DataFrame(columns=["change_dt", "resource_name", "changed_fields", "change_type"])

    campaign_resource_name = f"customers/{customer_id}/campaigns/{campaign_id}"
    # Change-event lookback is strict; keep a safe buffer within the API window.
    min_allowed_start = date.today() - timedelta(days=29)
    safe_start = max(start_date, min_allowed_start)
    safe_end = max(safe_start, end_date)
    query = f"""
        SELECT
            change_event.change_date_time,
            change_event.change_resource_name,
            change_event.change_resource_type,
            change_event.changed_fields,
            change_event.old_resource,
            change_event.new_resource
        FROM change_event
        WHERE change_event.change_date_time >= '{_to_date_string(safe_start)} 00:00:00'
          AND change_event.change_date_time <= '{_to_date_string(safe_end)} 23:59:59'
          AND change_event.change_resource_type IN ('CAMPAIGN', 'CAMPAIGN_BUDGET')
          AND change_event.resource_change_operation = 'UPDATE'
        ORDER BY change_event.change_date_time
        LIMIT 10000
    """
    client = _load_ads_client()
    service = client.get_service("GoogleAdsService")
    rows: list[dict] = []
    try:
        stream = service.search_stream(customer_id=customer_id, query=query)
        for batch in stream:
            for row in batch.results:
                changed_fields_text = str(row.change_event.changed_fields or "")
                rows.append(
                    {
                        "change_dt": pd.to_datetime(str(row.change_event.change_date_time)),
                        "resource_name": str(row.change_event.change_resource_name or ""),
                        "resource_type": str(row.change_event.change_resource_type or ""),
                        "changed_fields": changed_fields_text,
                        "old_resource": str(row.change_event.old_resource),
                        "new_resource": str(row.change_event.new_resource),
                    }
                )
    except GoogleAdsException as ex:
        msg = "\n".join(err.message for err in ex.failure.errors)
        raise RuntimeError(f"Google Ads API error:\n{msg}") from ex

    if not rows:
        return pd.DataFrame(columns=["change_dt", "resource_name", "changed_fields", "change_type"])

    out = pd.DataFrame(rows).sort_values("change_dt").reset_index(drop=True)
    out = out[
        out["resource_name"].isin(
            [campaign_resource_name, budget_resource_name] if budget_resource_name else [campaign_resource_name]
        )
    ].copy()
    if out.empty:
        return pd.DataFrame(columns=["change_dt", "resource_name", "changed_fields", "change_type"])
    lower = out["changed_fields"].str.lower()
    out["is_budget_change"] = lower.str.contains("amount_micros|campaign_budget", regex=True)
    out["is_cpa_change"] = lower.str.contains("target_cpa|maximize_conversions", regex=True)
    out["change_type"] = out.apply(
        lambda r: "Budget + CPA"
        if bool(r["is_budget_change"]) and bool(r["is_cpa_change"])
        else ("Budget" if bool(r["is_budget_change"]) else ("Target CPA" if bool(r["is_cpa_change"]) else "Other")),
        axis=1,
    )
    return out


def make_line_chart(
    df: pd.DataFrame, metric: str, title: str, color: str, y_title: str, fmt: str
) -> alt.Chart:
    return (
        alt.Chart(df)
        .mark_line(color=color, point=True)
        .encode(
            x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d")),
            y=alt.Y(f"{metric}:Q", title=y_title),
            tooltip=[
                alt.Tooltip("date:T", title="Date", format="%Y-%m-%d"),
                alt.Tooltip(f"{metric}:Q", title=y_title, format=fmt),
            ],
        )
        .properties(height=320, title=title)
    )


def make_limits_step_chart(df: pd.DataFrame) -> alt.Chart:
    d = df.copy().sort_values("date")
    d["daily_budget_limit"] = d["daily_budget_limit"].replace(0, pd.NA).ffill()
    d["max_cpa_limit"] = d["max_cpa_limit"].replace(0, pd.NA).ffill()

    budget = (
        alt.Chart(d)
        .mark_line(interpolate="step-after", color="#7E57C2", strokeWidth=3)
        .encode(
            x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d")),
            y=alt.Y("daily_budget_limit:Q", title="Daily budget limit ($)"),
            tooltip=[
                alt.Tooltip("date:T", title="Date", format="%Y-%m-%d"),
                alt.Tooltip("daily_budget_limit:Q", title="Daily budget limit", format="$,.2f"),
            ],
        )
    )
    cpa = (
        alt.Chart(d)
        .mark_line(interpolate="step-after", color="#D81B60", strokeWidth=3)
        .encode(
            x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d")),
            y=alt.Y("max_cpa_limit:Q", title="Max CPA allowance ($)"),
            tooltip=[
                alt.Tooltip("date:T", title="Date", format="%Y-%m-%d"),
                alt.Tooltip("max_cpa_limit:Q", title="Max CPA allowance", format="$,.2f"),
            ],
        )
    )
    return (
        alt.layer(budget, cpa)
        .resolve_scale(y="independent")
        .properties(
            height=360,
            title="Daily budget limit and max CPA allowance (step chart)",
        )
    )


def make_limits_change_step_chart(
    perf_df: pd.DataFrame,
    changes_df: pd.DataFrame,
    current_budget: float | None,
    current_cpa: float | None,
) -> alt.Chart | None:
    if perf_df.empty:
        return None

    d = perf_df[["date"]].copy().sort_values("date")
    d["daily_budget_level"] = 0
    d["target_cpa_level"] = 0

    if not changes_df.empty:
        budget_dates = (
            changes_df.loc[changes_df["is_budget_change"], "change_dt"].dt.normalize().drop_duplicates().tolist()
        )
        cpa_dates = (
            changes_df.loc[changes_df["is_cpa_change"], "change_dt"].dt.normalize().drop_duplicates().tolist()
        )
        d.loc[d["date"].isin(budget_dates), "daily_budget_level"] = 1
        d.loc[d["date"].isin(cpa_dates), "target_cpa_level"] = 1

    # Cumulative step-level markers for each change event sequence.
    d["daily_budget_level"] = d["daily_budget_level"].cumsum()
    d["target_cpa_level"] = d["target_cpa_level"].cumsum()

    melted = d.melt(
        id_vars=["date"],
        value_vars=["daily_budget_level", "target_cpa_level"],
        var_name="series",
        value_name="level",
    )
    label_map = {
        "daily_budget_level": "Daily budget change count",
        "target_cpa_level": "Target CPA change count",
    }
    melted["series_label"] = melted["series"].map(label_map)

    latest_budget = f"${current_budget:,.2f}" if current_budget is not None else "n/a"
    latest_cpa = f"${current_cpa:,.2f}" if current_cpa is not None else "n/a"
    return (
        alt.Chart(melted)
        .mark_line(interpolate="step-after", point=False, strokeWidth=3)
        .encode(
            x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d")),
            y=alt.Y("level:Q", title="Cumulative count of limit changes"),
            color=alt.Color(
                "series_label:N",
                title="Change type",
                scale=alt.Scale(
                    domain=["Daily budget change count", "Target CPA change count"],
                    range=["#7E57C2", "#D81B60"],
                ),
            ),
            tooltip=[
                alt.Tooltip("date:T", title="Date", format="%Y-%m-%d"),
                alt.Tooltip("series_label:N", title="Series"),
                alt.Tooltip("level:Q", title="Change count", format=","),
            ],
        )
        .properties(
            height=360,
            title=(
                "Daily budget and Target CPA edit timeline (step chart) "
                f"| Current limits: Budget {latest_budget}, Target CPA {latest_cpa}"
            ),
        )
    )


def _extract_micros_value(resource_text: str, field_name: str) -> float | None:
    pattern = rf"{re.escape(field_name)}:\s*(\d+)"
    match = re.search(pattern, resource_text or "")
    if not match:
        return None
    return float(match.group(1)) / 1_000_000.0


def extract_limit_value_events(changes_df: pd.DataFrame) -> pd.DataFrame:
    if changes_df.empty:
        return pd.DataFrame(
            columns=["change_dt", "change_type", "old_value", "new_value", "resource_name"]
        )
    d = changes_df.sort_values("change_dt").copy()
    d["budget_old"] = d["old_resource"].apply(lambda t: _extract_micros_value(t, "amount_micros"))
    d["budget_new"] = d["new_resource"].apply(lambda t: _extract_micros_value(t, "amount_micros"))
    d["cpa_old"] = d["old_resource"].apply(
        lambda t: _extract_micros_value(t, "target_cpa_micros")
    )
    d["cpa_new"] = d["new_resource"].apply(
        lambda t: _extract_micros_value(t, "target_cpa_micros")
    )

    rows: list[dict] = []
    for _, r in d.iterrows():
        if bool(r.get("is_budget_change")) and pd.notna(r.get("budget_new")):
            rows.append(
                {
                    "change_dt": r["change_dt"],
                    "change_type": "Daily budget ($)",
                    "old_value": r.get("budget_old"),
                    "new_value": r.get("budget_new"),
                    "resource_name": r.get("resource_name"),
                }
            )
        if bool(r.get("is_cpa_change")) and pd.notna(r.get("cpa_new")):
            rows.append(
                {
                    "change_dt": r["change_dt"],
                    "change_type": "Target CPA ($)",
                    "old_value": r.get("cpa_old"),
                    "new_value": r.get("cpa_new"),
                    "resource_name": r.get("resource_name"),
                }
            )
    if not rows:
        return pd.DataFrame(
            columns=["change_dt", "change_type", "old_value", "new_value", "resource_name"]
        )
    return pd.DataFrame(rows).sort_values("change_dt").reset_index(drop=True)


def make_limits_value_step_chart(
    changes_df: pd.DataFrame,
    start_date: date,
    end_date: date,
    current_budget: float | None,
    current_cpa: float | None,
) -> alt.Chart | None:
    value_events = extract_limit_value_events(changes_df)
    if value_events.empty:
        return None

    budget_changes = value_events[value_events["change_type"] == "Daily budget ($)"].copy()
    cpa_changes = value_events[value_events["change_type"] == "Target CPA ($)"].copy()

    rows: list[dict] = []
    if not budget_changes.empty:
        initial_budget = (
            budget_changes["old_value"].dropna().iloc[0]
            if budget_changes["old_value"].notna().any()
            else None
        )
        if initial_budget is not None:
            rows.append({"date": pd.to_datetime(start_date), "series_label": "Daily budget ($)", "value": initial_budget})
        for _, r in budget_changes.iterrows():
            rows.append(
                {
                    "date": pd.Timestamp(r["change_dt"]).normalize(),
                    "series_label": "Daily budget ($)",
                    "value": float(r["new_value"]),
                }
            )
        if current_budget is not None:
            rows.append({"date": pd.to_datetime(end_date), "series_label": "Daily budget ($)", "value": float(current_budget)})

    if not cpa_changes.empty:
        initial_cpa = (
            cpa_changes["old_value"].dropna().iloc[0]
            if cpa_changes["old_value"].notna().any()
            else None
        )
        if initial_cpa is not None:
            rows.append({"date": pd.to_datetime(start_date), "series_label": "Target CPA ($)", "value": initial_cpa})
        for _, r in cpa_changes.iterrows():
            rows.append(
                {
                    "date": pd.Timestamp(r["change_dt"]).normalize(),
                    "series_label": "Target CPA ($)",
                    "value": float(r["new_value"]),
                }
            )
        if current_cpa is not None:
            rows.append({"date": pd.to_datetime(end_date), "series_label": "Target CPA ($)", "value": float(current_cpa)})

    if not rows:
        return None
    points_df = pd.DataFrame(rows).sort_values(["series_label", "date"]).drop_duplicates(
        subset=["series_label", "date"], keep="last"
    )
    if points_df.empty:
        return None
    # Ensure full-day coverage across the selected range so this chart aligns with other charts.
    all_days = pd.date_range(start=start_date, end=end_date, freq="D")
    series_rows: list[pd.DataFrame] = []
    for label in ["Daily budget ($)", "Target CPA ($)"]:
        s = points_df[points_df["series_label"] == label][["date", "value"]].copy()
        if s.empty:
            continue
        full = pd.DataFrame({"date": all_days})
        full = full.merge(s, on="date", how="left").sort_values("date")
        # Carry levels across days without explicit edits.
        full["value"] = full["value"].ffill().bfill()
        full["series_label"] = label
        series_rows.append(full)
    if not series_rows:
        return None
    long_df = pd.concat(series_rows, ignore_index=True)

    return (
        alt.Chart(long_df)
        .mark_line(interpolate="step-after", strokeWidth=3)
        .encode(
            x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d")),
            y=alt.Y(
                "value:Q",
                title="Limit value ($)",
                axis=alt.Axis(format="$,.0f", tickMinStep=50),
            ),
            color=alt.Color(
                "series_label:N",
                title="Limit",
                scale=alt.Scale(
                    domain=["Daily budget ($)", "Target CPA ($)"],
                    range=["#5E35B1", "#C2185B"],
                ),
            ),
            tooltip=[
                alt.Tooltip("date:T", title="Date", format="%Y-%m-%d"),
                alt.Tooltip("series_label:N", title="Series"),
                alt.Tooltip("value:Q", title="Value", format="$,.2f"),
            ],
        )
        .properties(
            height=360,
            title="Daily budget and Target CPA levels over time ($ step chart)",
        )
    )


def main() -> None:
    st.set_page_config(page_title="Google Ads Campaign Report", layout="wide")
    # GOLD: user-validated report version marker.
    today = date.today()
    max_end_date = today - timedelta(days=2)
    default_start = date(2026, 3, 1)
    if default_start > max_end_date:
        default_start = max_end_date

    st.title("Google Ads Campaign Report")
    with st.sidebar:
        st.subheader("Filters")
        try:
            campaigns = fetch_campaign_names()
        except Exception as e:
            st.error(str(e))
            st.stop()

        campaign = st.selectbox(
            "Campaign",
            options=campaigns,
            index=campaigns.index(CAMPAIGN_NAME) if CAMPAIGN_NAME in campaigns else 0,
        )
        exclude_recent = st.checkbox(
            "Exclude yesterday and today",
            value=True,
            help="When enabled, end date is capped at day-before-yesterday.",
        )
        allowed_end = max_end_date if exclude_recent else today
        start_date = st.date_input("Start date", value=default_start, max_value=allowed_end)
        end_date = st.date_input("End date", value=allowed_end, max_value=allowed_end)

    if start_date > end_date:
        st.error("Start date must be on or before end date.")
        st.stop()

    st.subheader(campaign)

    with st.spinner("Fetching Google Ads campaign data..."):
        df = fetch_campaign_daily(campaign, start_date, end_date)
        current_budget, current_cpa = fetch_campaign_current_limits(campaign)
    change_event_floor = today - timedelta(days=29)
    change_start_date = max(start_date, change_event_floor)
    if change_start_date > end_date:
        change_start_date = end_date
    try:
        with st.spinner("Loading budget/Target CPA edit history..."):
            changes_df = fetch_campaign_limit_change_events(
                campaign, change_start_date, end_date, cache_version="v4"
            )
    except Exception as e:
        st.warning(f"Could not load change history for limits: {e}")
        changes_df = pd.DataFrame(
            columns=["change_dt", "resource_name", "changed_fields", "change_type"]
        )

    if df.empty:
        st.warning("No data returned for this campaign in the requested range.")
        st.stop()

    first_data_date = df["date"].dt.date.min()
    last_data_date = df["date"].dt.date.max()
    st.caption(
        f"Selected range: {start_date} to {end_date} | Returned data: {first_data_date} to {last_data_date}"
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Impressions", f"{int(df['impressions'].sum()):,}")
    c2.metric("Clicks", f"{int(df['clicks'].sum()):,}")
    c3.metric("Cost", f"${float(df['cost'].sum()):,.2f}")
    c4.metric("Conversions", f"{float(df['conversions'].sum()):,.1f}")

    st.altair_chart(
        make_line_chart(
            df,
            metric="impressions",
            title="Impressions by day",
            color="#4285F4",
            y_title="Impressions",
            fmt=",",
        ),
        width="stretch",
    )
    st.altair_chart(
        make_line_chart(
            df,
            metric="clicks",
            title="Clicks by day",
            color="#E65100",
            y_title="Clicks",
            fmt=",",
        ),
        width="stretch",
    )
    st.altair_chart(
        make_line_chart(
            df,
            metric="cost",
            title="Cost by day",
            color="#1E8E3E",
            y_title="Cost ($)",
            fmt="$,.2f",
        ),
        width="stretch",
    )
    st.altair_chart(
        make_line_chart(
            df,
            metric="conversions",
            title="Conversions by day",
            color="#0B57D0",
            y_title="Conversions",
            fmt=",.1f",
        ),
        width="stretch",
    )
    step_chart = make_limits_change_step_chart(df, changes_df, current_budget, current_cpa)
    if step_chart is not None:
        st.subheader("Budget & Target CPA change timeline")
        if change_start_date > start_date:
            st.warning(
                "Google Ads change history supports up to 30 days lookback. "
                f"The edit timeline below is shown for {change_start_date} to {end_date}."
            )
        st.altair_chart(step_chart, width="stretch")
        st.caption(
            "This step chart marks dates where Google Ads recorded edits to campaign budget or Target CPA. "
            "Each upward step = one additional change event. Use it to line up setting changes with performance shifts."
        )
        if not changes_df.empty:
            with st.expander("Budget/Target CPA change events (raw)"):
                show_cols = ["change_dt", "change_type", "resource_name", "changed_fields"]
                st.dataframe(changes_df[show_cols], width="stretch", hide_index=True)
        value_step_chart = make_limits_value_step_chart(
            changes_df=changes_df,
            start_date=start_date,
            end_date=end_date,
            current_budget=current_budget,
            current_cpa=current_cpa,
        )
        if value_step_chart is not None:
            st.altair_chart(value_step_chart, width="stretch")
            st.caption(
                "This chart shows actual dollar level changes parsed from Google Ads change events "
                "(old/new values), then expanded to every day in the selected range."
            )
        else:
            st.info("Could not derive daily dollar limit levels for this selected range.")
        with st.expander("Dollar-value extraction diagnostics"):
            value_events = extract_limit_value_events(changes_df)
            budget_event_count = int(changes_df["is_budget_change"].sum()) if not changes_df.empty else 0
            cpa_event_count = int(changes_df["is_cpa_change"].sum()) if not changes_df.empty else 0
            old_nonempty = (
                int(changes_df["old_resource"].fillna("").str.len().gt(0).sum())
                if not changes_df.empty
                else 0
            )
            new_nonempty = (
                int(changes_df["new_resource"].fillna("").str.len().gt(0).sum())
                if not changes_df.empty
                else 0
            )
            d1, d2, d3, d4, d5 = st.columns(5)
            d1.metric("Raw change rows", f"{len(changes_df):,}")
            d2.metric("Budget edit rows", f"{budget_event_count:,}")
            d3.metric("CPA edit rows", f"{cpa_event_count:,}")
            d4.metric("Rows with old_resource", f"{old_nonempty:,}")
            d5.metric("Rows with new_resource", f"{new_nonempty:,}")
            if value_events.empty:
                st.warning(
                    "No parsable dollar old/new values were extracted from current change-event payloads."
                )
            else:
                st.dataframe(
                    value_events[["change_dt", "change_type", "old_value", "new_value", "resource_name"]],
                    width="stretch",
                    hide_index=True,
                )
    else:
        st.info(
            "No performance rows for the selected date range, so change timeline cannot be rendered."
        )


if __name__ == "__main__":
    main()
