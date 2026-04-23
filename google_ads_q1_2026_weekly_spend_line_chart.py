"""
Streamlit: Q1 2026 — Google Ads weekly spend + GHL Committed? = Yes by sign-up week,
optionally filtered to Google Ads / GTM / Ad Manager path tags (same app).

Run from the project directory (where google-ads.yaml and .env live):

    streamlit run google_ads_q1_2026_weekly_spend_line_chart.py

Requires: google-ads.yaml; GHL token + GHL_LOCATION_ID (and field IDs if not auto-resolved).
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from streamlit.errors import StreamlitAPIException
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

from ghl_client import contact_custom_field_value, fetch_signup_date_range_committed_yes_contacts

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

DEFAULT_GOOGLE_ADS_CUSTOMER_ID = "5504078633"
DEFAULT_GOOGLE_ADS_LOGIN_CUSTOMER_ID = "9759824543"

Q1_2026_START = date(2026, 1, 1)
Q1_2026_END = date(2026, 3, 31)


def _google_ads_customer_id() -> str:
    return (
        os.getenv("GOOGLE_ADS_CUSTOMER_ID") or DEFAULT_GOOGLE_ADS_CUSTOMER_ID
    ).strip().replace("-", "")


def _google_ads_login_customer_id() -> str:
    return (
        os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or DEFAULT_GOOGLE_ADS_LOGIN_CUSTOMER_ID
    ).strip().replace("-", "")


def _fetch_google_ads_daily(since: str, until: str) -> pd.DataFrame:
    """Daily spend (account campaign roll-up)."""
    customer_id = _google_ads_customer_id()
    query = f"""
        SELECT
            segments.date,
            metrics.cost_micros
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
                    }
                )
    except GoogleAdsException as ex:
        msg = "\n".join(err.message for err in ex.failure.errors)
        raise RuntimeError(msg) from ex

    if not rows:
        return pd.DataFrame(columns=["date", "spend"])

    df = pd.DataFrame(rows)
    agg = (
        df.groupby("date", as_index=False)
        .agg({"cost_micros": "sum"})
        .sort_values("date")
    )
    agg["spend"] = agg["cost_micros"] / 1_000_000.0
    return agg.drop(columns=["cost_micros"])


def _week_range_calendar(d: date) -> tuple[date, date]:
    """Monday–Sunday week containing ``d`` (``date.weekday()``: Mon=0)."""
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _fetch_bounds_for_full_weeks_covering_q1() -> tuple[str, str]:
    """Extend Q1 to the enclosing Mon–Sun range so weekly totals are full weeks."""
    m0, _ = _week_range_calendar(Q1_2026_START)
    _, s1 = _week_range_calendar(Q1_2026_END)
    return m0.isoformat(), s1.isoformat()


def _daily_to_weekly_q1(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame(columns=["week_start", "spend"])

    d = daily.copy()
    d["day"] = d["date"].dt.normalize()
    d["week_start"] = d["day"] - pd.to_timedelta(d["day"].dt.weekday, unit="D")

    weekly = (
        d.groupby("week_start", as_index=False)
        .agg({"spend": "sum"})
        .sort_values("week_start")
    )

    def _intersects_q1(ws: pd.Timestamp) -> bool:
        mon = ws.date()
        sun = mon + timedelta(days=6)
        return mon <= Q1_2026_END and sun >= Q1_2026_START

    weekly = weekly[weekly["week_start"].map(_intersects_q1)].reset_index(drop=True)
    return weekly


def _q1_overlapping_week_monday_dates() -> list[date]:
    """Monday starts for each calendar week that intersects Q1 2026."""
    mon = _week_range_calendar(Q1_2026_START)[0]
    out: list[date] = []
    while True:
        sun = mon + timedelta(days=6)
        if mon > Q1_2026_END:
            break
        if sun >= Q1_2026_START:
            out.append(mon)
        mon += timedelta(days=7)
    return out


def _signup_raw_to_week_monday_date(signup_raw: str) -> date | None:
    """
    Parse the **Sign Up Date** custom-field string and return the Monday (calendar
    week, Mon=0) of that calendar day in local date form — avoids tz-aware merge
    mismatches with the Q1 week grid.
    """
    raw = (signup_raw or "").strip()
    if not raw:
        return None
    ts = pd.to_datetime(raw, errors="coerce", utc=True)
    if pd.isna(ts):
        return None
    # One calendar day in UTC is stable for “which week” bucketing.
    d = pd.Timestamp(ts).date()
    return d - timedelta(days=d.weekday())


def _ghl_committed_yes_weekly_counts(
    contacts: list[dict],
    sign_up_field_id: str,
    week_monday_dates: list[date],
) -> pd.DataFrame:
    """
    Count contacts per Monday week using **Sign Up Date** only (cohort is already
    **Sign Up Date** in Q1 + **Committed?** = Yes from the API helper).
    """
    base = pd.DataFrame({"week_monday": week_monday_dates})
    if not (sign_up_field_id or "").strip():
        base["week_start"] = pd.to_datetime(base["week_monday"])
        base["records"] = 0
        return base.drop(columns=["week_monday"])

    if not contacts:
        base["week_start"] = pd.to_datetime(base["week_monday"])
        base["records"] = 0
        return base.drop(columns=["week_monday"])

    rows: list[date] = []
    for c in contacts:
        raw = contact_custom_field_value(c, sign_up_field_id)
        mon_d = _signup_raw_to_week_monday_date(raw)
        if mon_d is not None:
            rows.append(mon_d)

    if not rows:
        base["week_start"] = pd.to_datetime(base["week_monday"])
        base["records"] = 0
        return base.drop(columns=["week_monday"])

    counts = (
        pd.DataFrame({"week_monday": rows})
        .groupby("week_monday", sort=False)
        .size()
        .reset_index(name="records")
    )
    out = base.merge(counts, on="week_monday", how="left")
    out["records"] = out["records"].fillna(0).astype(int)
    out["week_start"] = pd.to_datetime(out["week_monday"])
    return out.drop(columns=["week_monday"])


def _tag_display_name(tag_item: object) -> str:
    if isinstance(tag_item, str):
        return tag_item
    if isinstance(tag_item, dict):
        return str((tag_item or {}).get("name") or "")
    return ""


def _contact_has_google_ads_or_ad_manager_tag(contact: dict) -> bool:
    """
    True if the contact has any tag whose name suggests Google Ads, GTM,
    Tag Manager, or **Google Ad Manager** (substring match, case-insensitive).
    Aligns with the conversion report path-tag idea plus **ad manager** wording.
    """
    for t in contact.get("tags") or []:
        name = _tag_display_name(t)
        if not name:
            continue
        s = name.lower()
        if (
            "google" in s
            or "g-ad" in s
            or "gtm" in s
            or "tag manager" in s
            or "ad manager" in s
        ):
            return True
    return False


def _ghl_weekly_display(
    contacts: list[dict], sign_up_field_id: str, week_monday_dates: list[date]
) -> pd.DataFrame:
    out = _ghl_committed_yes_weekly_counts(contacts, sign_up_field_id, week_monday_dates)
    out["week_start_str"] = out["week_start"].dt.strftime("%Y-%m-%d")
    return out


def _ghl_weekly_line_chart(df: pd.DataFrame, *, title: str, color: str) -> alt.Chart:
    return (
        alt.Chart(df)
        .mark_line(point=True, color=color, strokeWidth=2)
        .encode(
            x=alt.X(
                "week_start:T",
                title="Week (Monday)",
                axis=alt.Axis(format="%b %d", labelAngle=-45),
            ),
            y=alt.Y("records:Q", title="Records (count)"),
            tooltip=[
                alt.Tooltip("week_start_str:N", title="Week starting"),
                alt.Tooltip("records:Q", title="Records", format=","),
            ],
        )
        .properties(height=380, title=title)
    )


def main() -> None:
    try:
        st.set_page_config(
            page_title="Q1 2026 — Ads spend & GHL Committed (weekly)",
            layout="wide",
        )
    except StreamlitAPIException:
        pass

    st.title("Q1 2026 — weekly Google Ads spend & GHL Committed = Yes")
    st.caption(
        "**Google Ads:** spend by calendar week (Mon–Sun), from the Ads API. "
        "**GoHighLevel:** **Sign Up Date** in Q1 2026 + **Committed?** = **Yes**, by sign-up week; "
        "a second GHL chart applies the same rules plus a **path tag** filter (Google Ads / GTM / "
        "**Google Ad Manager** style tag names). "
        "Same Streamlit app / port (`streamlit run` this file)."
    )

    week_mondays = _q1_overlapping_week_monday_dates()

    # --- Google Ads ---
    st.header("Google Ads — spend by week")
    st.caption(
        "`campaign` · `metrics.cost_micros` · weeks overlapping Q1. "
        "API window is extended to full Mon–Sun weeks around the quarter."
    )
    since, until = _fetch_bounds_for_full_weeks_covering_q1()
    st.markdown(
        f"**Customer** `{_google_ads_customer_id()}` · **login (MCC)** `{_google_ads_login_customer_id()}` · "
        f"**API range** {since} → {until}"
    )

    daily = pd.DataFrame()
    with st.spinner("Fetching Google Ads…"):
        try:
            daily = _fetch_google_ads_daily(since, until)
        except Exception as e:
            st.error(str(e))

    if daily.empty:
        st.info("No Google Ads rows for this range (or error above).")
    else:
        weekly = _daily_to_weekly_q1(daily)
        if weekly.empty:
            st.info("No weekly spend buckets intersect Q1 2026.")
        else:
            total_q1 = float(
                daily.loc[
                    (daily["date"].dt.date >= Q1_2026_START)
                    & (daily["date"].dt.date <= Q1_2026_END),
                    "spend",
                ].sum()
            )
            st.metric("Q1 2026 spend (days in quarter)", f"${total_q1:,.2f}")

            weekly_display = weekly.copy()
            weekly_display["week_start_str"] = weekly_display["week_start"].dt.strftime(
                "%Y-%m-%d"
            )

            spend_chart = (
                alt.Chart(weekly_display)
                .mark_line(point=True, color="#1a73e8", strokeWidth=2)
                .encode(
                    x=alt.X(
                        "week_start:T",
                        title="Week (Monday)",
                        axis=alt.Axis(format="%b %d", labelAngle=-45),
                    ),
                    y=alt.Y("spend:Q", title="Spend ($)"),
                    tooltip=[
                        alt.Tooltip("week_start_str:N", title="Week starting"),
                        alt.Tooltip("spend:Q", title="Spend", format="$,.2f"),
                    ],
                )
                .properties(height=380, title="Weekly spend")
            )
            st.altair_chart(spend_chart, width="stretch")

            with st.expander("Google Ads — weekly table"):
                st.dataframe(
                    weekly_display[["week_start_str", "spend"]].rename(
                        columns={"week_start_str": "Week (Mon)", "spend": "Spend ($)"}
                    ),
                    width="stretch",
                    hide_index=True,
                )

    st.divider()

    # --- GoHighLevel ---
    st.header("GoHighLevel — Committed? = Yes by sign-up week")
    st.caption(
        "Uses the **Sign Up Date** custom field together with **Committed?** = **Yes**: "
        "only contacts whose sign-up is in **"
        f"{Q1_2026_START.isoformat()}**–**{Q1_2026_END.isoformat()}** and who are committed "
        "are loaded (`fetch_signup_date_range_committed_yes_contacts`). "
        "The line chart counts them by the **Monday calendar week** of that **Sign Up Date**."
    )

    q1_since = Q1_2026_START.isoformat()
    q1_until = Q1_2026_END.isoformat()

    with st.spinner("Fetching GoHighLevel…"):
        try:
            ghl = fetch_signup_date_range_committed_yes_contacts(q1_since, q1_until)
        except Exception as e:
            st.error(str(e))
            ghl = None

    if ghl is not None:
        contacts = ghl["contacts"]
        sid = str(ghl.get("sign_up_date_field_id") or "")

        if ghl.get("truncated_pages"):
            st.warning(
                "GHL search hit the pagination cap; weekly counts may be incomplete. "
                "Raise `max_pages` in `search_contacts_custom_field_date_range` if needed."
            )

        st.metric("Committed = Yes (sign-up in Q1 2026, total)", f"{len(contacts):,}")
        excl = int(ghl.get("excluded_not_committed_yes") or 0)
        if excl:
            st.caption(
                f"{excl:,} contact(s) had sign-up in range but **Committed?** was not **Yes** (excluded)."
            )

        ghl_weekly = _ghl_weekly_display(contacts, sid, week_mondays)

        bucketed = int(ghl_weekly["records"].sum())
        if bucketed != len(contacts):
            st.warning(
                f"Weekly counts sum to **{bucketed:,}**, but **{len(contacts):,}** contacts "
                "were returned — some **Sign Up Date** values could not be parsed for week grouping."
            )

        st.altair_chart(
            _ghl_weekly_line_chart(
                ghl_weekly,
                title="Committed? = Yes — weekly count (Sign Up Date in Q1)",
                color="#0f9d58",
            ),
            width="stretch",
        )

        with st.expander("GoHighLevel — weekly table (all Committed = Yes)"):
            st.dataframe(
                ghl_weekly[["week_start_str", "records"]].rename(
                    columns={"week_start_str": "Week (Mon)", "records": "Records"}
                ),
                width="stretch",
                hide_index=True,
            )

        st.divider()
        st.subheader("Committed = Yes + Google Ads / Google Ad Manager path tag")
        st.caption(
            "Same cohort as above, then restricted to contacts with at least one **tag** whose "
            "name contains **google**, **g-ad**, **gtm**, **tag manager**, or **ad manager** "
            "(covers Google Ads / GTM / **Google Ad Manager** style labels)."
        )

        contacts_ads_tag = [c for c in contacts if _contact_has_google_ads_or_ad_manager_tag(c)]
        st.metric(
            "Committed = Yes + path tag (sign-up Q1 2026)",
            f"{len(contacts_ads_tag):,}",
        )

        ghl_weekly_tag = _ghl_weekly_display(contacts_ads_tag, sid, week_mondays)
        bucketed_tag = int(ghl_weekly_tag["records"].sum())
        if bucketed_tag != len(contacts_ads_tag):
            st.warning(
                f"Tagged cohort: weekly counts sum to **{bucketed_tag:,}**, but **{len(contacts_ads_tag):,}** "
                "contacts matched — some **Sign Up Date** values could not be parsed for week grouping."
            )

        st.altair_chart(
            _ghl_weekly_line_chart(
                ghl_weekly_tag,
                title="Committed? = Yes + path tag — weekly count (Sign Up Date in Q1)",
                color="#e65100",
            ),
            width="stretch",
        )

        with st.expander("GoHighLevel — weekly table (Committed = Yes + path tag)"):
            st.dataframe(
                ghl_weekly_tag[["week_start_str", "records"]].rename(
                    columns={"week_start_str": "Week (Mon)", "records": "Records"}
                ),
                width="stretch",
                hide_index=True,
            )


if __name__ == "__main__":
    main()
