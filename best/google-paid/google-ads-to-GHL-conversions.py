"""
Single-page Streamlit report: Google Ads performance + GoHighLevel conversion cohorts.

Run from the project directory (where google-ads.yaml and .env live):

    streamlit run google_ads_ghl_conversion_report.py

Requires: google-ads.yaml (Google Ads API), .env with GHL_* for LeadConnector.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from streamlit.errors import StreamlitAPIException
from streamlit.runtime.scriptrunner import get_script_run_ctx
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

from ghl_client import (
    classify_hear_about_wom_vs_google,
    contact_custom_field_value,
    fetch_signup_date_range_committed_yes_contacts,
    resolve_hear_about_us_custom_field_id,
)

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

# Defaults match google_ads_verify_connection.py; override with env if set.
DEFAULT_GOOGLE_ADS_CUSTOMER_ID = "5504078633"
DEFAULT_GOOGLE_ADS_LOGIN_CUSTOMER_ID = "9759824543"


def _google_ads_customer_id() -> str:
    return (
        os.getenv("GOOGLE_ADS_CUSTOMER_ID") or DEFAULT_GOOGLE_ADS_CUSTOMER_ID
    ).strip().replace("-", "")


def _google_ads_login_customer_id() -> str:
    return (
        os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or DEFAULT_GOOGLE_ADS_LOGIN_CUSTOMER_ID
    ).strip().replace("-", "")


def _is_google_ads_tag(name: str) -> bool:
    s = name.lower()
    return "google" in s or "g-ad" in s or "gtm" in s or "tag manager" in s


def _contact_has_google_ads_path_tag(contact: dict) -> bool:
    for t in contact.get("tags") or []:
        if isinstance(t, str):
            name = t
        else:
            name = str((t or {}).get("name") or "")
        if name and _is_google_ads_tag(name):
            return True
    return False


def _fetch_google_ads_daily(since: str, until: str) -> pd.DataFrame:
    """Daily spend, impressions, clicks, conversions (account campaign roll-up)."""
    customer_id = _google_ads_customer_id()
    query = f"""
        SELECT
            segments.date,
            metrics.cost_micros,
            metrics.impressions,
            metrics.clicks,
            metrics.conversions,
            metrics.all_conversions
        FROM campaign
        WHERE segments.date BETWEEN '{since}' AND '{until}'
          AND campaign.status != 'REMOVED'
    """
    client = GoogleAdsClient.load_from_storage(path=str(_PROJECT_DIR / "google-ads.yaml"))
    client.login_customer_id = _google_ads_login_customer_id()
    ga_service = client.get_service("GoogleAdsService")

    rows: list[dict] = []
    try:
        stream = ga_service.search_stream(customer_id=customer_id, query=query)
        for batch in stream:
            for row in batch.results:
                rows.append(
                    {
                        "date": pd.to_datetime(row.segments.date),
                        "cost_micros": row.metrics.cost_micros,
                        "impressions": row.metrics.impressions,
                        "clicks": row.metrics.clicks,
                        "conversions": float(row.metrics.conversions or 0),
                        "all_conversions": float(row.metrics.all_conversions or 0),
                    }
                )
    except GoogleAdsException as ex:
        msg = "\n".join(err.message for err in ex.failure.errors)
        raise RuntimeError(msg) from ex

    if not rows:
        return pd.DataFrame(
            columns=[
                "date",
                "spend",
                "impressions",
                "clicks",
                "conversions",
                "all_conversions",
            ]
        )

    df = pd.DataFrame(rows)
    agg = (
        df.groupby("date", as_index=False)
        .agg(
            {
                "cost_micros": "sum",
                "impressions": "sum",
                "clicks": "sum",
                "conversions": "sum",
                "all_conversions": "sum",
            }
        )
        .sort_values("date")
    )
    agg["spend"] = agg["cost_micros"] / 1_000_000.0
    return agg.drop(columns=["cost_micros"])


def _hear_about_bucket(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return "Blank / missing"
    ch = classify_hear_about_wom_vs_google(s)
    if ch == "Google":
        return "Google (field)"
    return "Other / WOM"


# GHL path-tag cohort time series: legend order and colors (Altair scale.domain / range).
_GHL_COHORT_TS_SERIES_ORDER = ("Path tag cohort", "Hear-about: Google", "Word of mouth")
_GHL_COHORT_TS_SERIES_COLORS = {
    "Path tag cohort": "#34a853",
    "Hear-about: Google": "#1a73e8",
    "Word of mouth": "#9c27b0",
}


def main() -> None:
    # Must be first Streamlit call, and only once per browser session (reruns would raise).
    try:
        st.set_page_config(
            page_title="Google Ads + GHL conversions",
            layout="wide",
            initial_sidebar_state="expanded",
        )
    except StreamlitAPIException:
        pass

    st.title("Google Ads + GoHighLevel — conversion report")
    st.caption(
        "Google Ads: `google-ads.yaml` (spend, traffic, conversions). "
        "GHL: `.env` (`GHL_ACCESS_TOKEN` or private token, `GHL_LOCATION_ID`). "
        "GHL cohort uses **Sign Up Date** in range and **Committed?** = **Yes**; "
        "Google Ads path = tags matching google / g-ad / gtm / tag manager (e.g. **dc thru g-ad**)."
    )

    today = date.today()
    default_start = date(2026, 1, 1)
    default_end = date(2026, 3, 31)
    if today.year == 2026 and today < default_end:
        default_end = min(today, default_end)

    with st.sidebar:
        st.subheader("Date range")
        since_d = st.date_input("Start", value=default_start)
        until_d = st.date_input("End", value=default_end)
        st.divider()
        st.markdown(
            "**Google Ads** customer (10 digits, no dashes): "
            f"`{_google_ads_customer_id()}` · login MCC: `{_google_ads_login_customer_id()}`"
        )

    if since_d > until_d:
        st.error("Start date must be on or before end date.")
        st.stop()

    since = since_d.isoformat()
    until = until_d.isoformat()

    # --- Google Ads ---
    st.header("Google Ads")
    try:
        ads_daily = _fetch_google_ads_daily(since, until)
    except Exception as e:
        st.error(f"Google Ads API: {e}")
        ads_daily = pd.DataFrame()

    if ads_daily.empty:
        st.info("No Google Ads rows for this range (or API error above).")
        total_spend = total_imp = total_clicks = total_conv = 0.0
    else:
        total_spend = float(ads_daily["spend"].sum())
        total_imp = int(ads_daily["impressions"].sum())
        total_clicks = int(ads_daily["clicks"].sum())
        total_conv = float(ads_daily["conversions"].sum())

    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Spend (account)", f"${total_spend:,.2f}")
    a2.metric("Impressions", f"{total_imp:,}")
    a3.metric("Clicks", f"{total_clicks:,}")
    a4.metric("Conversions (Ads)", f"{total_conv:,.1f}")

    if not ads_daily.empty:
        ads_sorted = ads_daily.sort_values("date")
        spend_chart = (
            alt.Chart(ads_sorted)
            .mark_line(color="#1a73e8", point=True)
            .encode(
                x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d")),
                y=alt.Y("spend:Q", title="Spend ($)"),
                tooltip=[
                    alt.Tooltip("date:T", title="Date", format="%Y-%m-%d"),
                    alt.Tooltip("spend:Q", title="Spend", format="$,.2f"),
                ],
            )
            .properties(height=320, title="Google Ads — spend by day")
        )
        st.altair_chart(spend_chart, width="stretch")

        imp_chart = (
            alt.Chart(ads_sorted)
            .mark_line(color="#4285f4", point=True)
            .encode(
                x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d")),
                y=alt.Y("impressions:Q", title="Impressions"),
                tooltip=[
                    alt.Tooltip("date:T", title="Date", format="%Y-%m-%d"),
                    alt.Tooltip("impressions:Q", title="Impressions", format=","),
                ],
            )
            .properties(height=280, title="Google Ads — impressions by day")
        )
        st.altair_chart(imp_chart, width="stretch")

        clk_chart = (
            alt.Chart(ads_sorted)
            .mark_line(color="#e65100", point=True)
            .encode(
                x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d")),
                y=alt.Y("clicks:Q", title="Clicks"),
                tooltip=[
                    alt.Tooltip("date:T", title="Date", format="%Y-%m-%d"),
                    alt.Tooltip("clicks:Q", title="Clicks", format=","),
                ],
            )
            .properties(height=280, title="Google Ads — clicks by day")
        )
        st.altair_chart(clk_chart, width="stretch")

    st.divider()

    # --- GHL ---
    _ghl_hdr, _ghl_cx1, _ghl_cx2, _ghl_cx3 = st.columns([2.65, 1.0, 1.05, 1.05])
    with _ghl_hdr:
        st.header("GoHighLevel — cohorts & hear-about")
    with _ghl_cx1:
        ghl_show_path = st.checkbox(
            "Path tag cohort",
            value=True,
            key="ghl_cohort_line_path",
            help="Show or hide the line for all path-tag contacts (parsable sign-up date).",
        )
    with _ghl_cx2:
        ghl_show_google = st.checkbox(
            "Hear-about: Google",
            value=True,
            key="ghl_cohort_line_google",
            help="Show or hide the line for hear-about classified as Google.",
        )
    with _ghl_cx3:
        ghl_show_wom = st.checkbox(
            "Word of mouth",
            value=True,
            key="ghl_cohort_line_wom",
            help="Show or hide the line for hear-about classified as Word of mouth.",
        )
    try:
        hear_id = resolve_hear_about_us_custom_field_id(None)
        signup_committed = fetch_signup_date_range_committed_yes_contacts(
            since, until, location_id=None
        )
    except Exception as e:
        st.error(f"GHL: {e}")
        st.stop()

    contacts = signup_committed["contacts"]
    with_tag = [c for c in contacts if _contact_has_google_ads_path_tag(c)]

    hear_google = hear_blank = hear_other = 0
    for c in with_tag:
        raw = contact_custom_field_value(c, hear_id)
        b = _hear_about_bucket(raw)
        if b == "Google (field)":
            hear_google += 1
        elif b == "Blank / missing":
            hear_blank += 1
        else:
            hear_other += 1

    google_channel_assumed = hear_google + hear_blank

    g1, g2, g3, g4, g5 = st.columns(5)
    g1.metric("Sign-up in range + Committed = Yes", f"{len(contacts):,}")
    g2.metric("… also Google Ads path tag", f"{len(with_tag):,}")
    g3.metric("Hear-about: Google", f"{hear_google:,}")
    g4.metric("Hear-about: blank", f"{hear_blank:,}")
    g5.metric("Google channel (Google + blank)", f"{google_channel_assumed:,}")

    if signup_committed.get("truncated_pages"):
        st.warning("GHL sign-up search hit pagination cap; counts may be incomplete.")

    # CPA row: same 5 columns as metrics — g2 path tag count, g3 Google field, g5 Google+blank hear-about
    _cpa0, cpa_path_tag_col, cpa_google_only_col, _cpa3, cpa_google_blank_col = st.columns(5)
    n_path_tag = len(with_tag)
    if n_path_tag > 0 and total_spend > 0:
        cpa_path_tag = total_spend / n_path_tag
        cpa_path_tag_col.metric(
            "Implied CPA (account spend ÷ Google Ads path tag cohort)",
            f"${cpa_path_tag:,.2f}",
            help="Total Google Ads account spend for the date range divided by **… also Google Ads path tag** "
            "(Committed = Yes, Sign Up Date in range, and a Google Ads path tag).",
        )
    if hear_google > 0 and total_spend > 0:
        cpa_google_only = total_spend / hear_google
        cpa_google_only_col.metric(
            "Implied CPA (spend ÷ Hear-about: Google only, tag cohort)",
            f"${cpa_google_only:,.2f}",
            help="Same account spend as above, divided only by contacts whose **How did you hear about us?** "
            "classifies as **Google (field)** (same count as **Hear-about: Google** in the row above).",
        )
    if google_channel_assumed > 0 and total_spend > 0:
        cpa_google_blank = total_spend / google_channel_assumed
        cpa_google_blank_col.metric(
            "Implied CPA (account spend ÷ Google + blank hear-about, tag cohort)",
            f"${cpa_google_blank:,.2f}",
            help="Uses total Google Ads spend for the range and the hear-about **Google (field) + blank** count "
            "among contacts with Sign Up Date in range, Committed = Yes, and Google Ads path tag "
            "(same as **Google channel (Google + blank)** above).",
        )

    # Daily: path-tag cohort + hear-about Google / WOM (classify_hear_about_wom_vs_google)
    sid = signup_committed.get("sign_up_date_field_id") or ""
    day_rows: list[dict] = []
    for c in with_tag:
        raw = contact_custom_field_value(c, sid).strip() if sid else ""
        if not raw:
            continue
        try:
            d = pd.to_datetime(raw)
            if pd.isna(d):
                continue
            signup_day = pd.Timestamp(d).normalize()
        except (ValueError, TypeError):
            continue
        day_rows.append({"signup_day": signup_day, "series": "Path tag cohort"})
        hear_raw = (contact_custom_field_value(c, hear_id) or "").strip()
        hear_ch = classify_hear_about_wom_vs_google(hear_raw)
        if hear_ch == "Word of mouth":
            day_rows.append({"signup_day": signup_day, "series": "Word of mouth"})
        elif hear_ch == "Google":
            day_rows.append({"signup_day": signup_day, "series": "Hear-about: Google"})
    if day_rows:
        ghl_d = (
            pd.DataFrame(day_rows)
            .groupby(["signup_day", "series"], as_index=False)
            .agg(n=("signup_day", "count"))
        )
        ghl_d["sign_up"] = pd.to_datetime(ghl_d["signup_day"]).dt.strftime("%Y-%m-%d")
        day_order = sorted(ghl_d["sign_up"].unique())
        ghl_d = ghl_d.sort_values(["sign_up", "series"])
        active_series: list[str] = []
        if ghl_show_path:
            active_series.append("Path tag cohort")
        if ghl_show_google:
            active_series.append("Hear-about: Google")
        if ghl_show_wom:
            active_series.append("Word of mouth")
        plot_df = ghl_d[ghl_d["series"].isin(active_series)].copy()
        present = set(plot_df["series"].unique())
        dom = [s for s in _GHL_COHORT_TS_SERIES_ORDER if s in present]
        dom += sorted(present - set(dom))
        color_range = [_GHL_COHORT_TS_SERIES_COLORS.get(s, "#5f6368") for s in dom]
        if plot_df.empty:
            st.info("Turn on at least one line using the checkboxes next to the section title.")
        else:
            ghl_chart = (
                alt.Chart(plot_df)
                .mark_line(point=True)
                .encode(
                    x=alt.X(
                        "sign_up:O",
                        title="Sign-up date",
                        sort=day_order,
                        axis=alt.Axis(labelAngle=-40),
                    ),
                    y=alt.Y("n:Q", title="Contacts"),
                    color=alt.Color(
                        "series:N",
                        legend=alt.Legend(title=""),
                        scale=alt.Scale(domain=dom, range=color_range),
                    ),
                    tooltip=[
                        alt.Tooltip("sign_up:O", title="Date"),
                        alt.Tooltip("series:N", title=""),
                        alt.Tooltip("n:Q", title="Contacts", format=","),
                    ],
                )
                .properties(
                    height=320,
                    title="GHL — path tag cohort & hear-about (Google / WOM) by sign-up day",
                )
            )
            st.caption(
                "All lines: **Committed = Yes**, Google Ads path tag, parsable **Sign Up Date**. "
                "**Hear-about: Google** / **Word of mouth** use `classify_hear_about_wom_vs_google` "
                '(field text contains "google" or "word of mouth", case-insensitive; WOM checked first). '
                "The x-axis is one label per calendar day (no duplicate temporal ticks). "
                "Use the checkboxes next to the title to show or hide each line."
            )
            st.altair_chart(ghl_chart, width="stretch")

    hear_df = pd.DataFrame(
        {
            "Bucket": ["Google (field)", "Blank / missing", "Other / WOM"],
            "Contacts": [hear_google, hear_blank, hear_other],
        }
    )
    pie = (
        alt.Chart(hear_df)
        .mark_arc(innerRadius=40)
        .encode(
            theta=alt.Theta("Contacts:Q", title=""),
            color=alt.Color("Bucket:N", legend=alt.Legend(title="Hear about us")),
            tooltip=[
                alt.Tooltip("Bucket:N", title=""),
                alt.Tooltip("Contacts:Q", title="Contacts", format=","),
            ],
        )
        .properties(height=340, title="Hear about us — among Google Ads path tag cohort")
    )
    st.altair_chart(pie, width="stretch")


# Under `streamlit run`, the script context exists; under plain `python` / import, it does not.
if get_script_run_ctx() is not None:
    main()
elif __name__ == "__main__":
    import sys

    print(
        "This is a Streamlit app. Start it with:\n"
        "  streamlit run google_ads_ghl_conversion_report.py",
        file=sys.stderr,
    )
    sys.exit(1)
