"""Streamlit: GA4 March YoY — sessions and new users (service account JSON from .env)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

from google_data import (
    get_march_search_branded_sessions_yoy,
    get_march_yoy_by_session_source_medium,
)

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

MARCH_YOY_YEARS = (2026, 2025, 2024, 2023)
# Stacked bars: show the N largest source/medium combos by total sessions; remainder → "Other".
TOP_SOURCE_MEDIA_FOR_CHART = 10

# Always rolled into "Other" before the top-N cutoff (match is case-insensitive, trimmed).
_SOURCE_MEDIUM_ALWAYS_OTHER_CASEFOLD = frozenset(
    {
        "(not set)",
        "duckduckgo / organic",
        "guckduckgo / organic",  # common misspelling / data quirk
        "yahoo / organic",
        "bing / organic",
        "ecosia.org / organic",
    }
)

# HubSpot + generic “email / email marketing” → one stack segment (case-insensitive).
_EMAIL_MARKETING_SOURCE_MEDIA_CASEFOLD = frozenset(
    {
        "hs_automation / email",
        "hs_email / email",
        "hs email / email",
        "email / email marketing",
    }
)
EMAIL_EMAIL_MARKETING_CHART_LABEL = "Email / email marketing"

REFERRAL_CHART_LABEL = "Referral"

DIRECT_TRAFFIC_CHART_LABEL = "Direct Traffic"

SOURCE_NOT_AVAILABLE_CHART_LABEL = "Source Not Available"

GOOGLE_PAID_CHART_LABEL = "Google Paid"
GOOGLE_ORGANIC_CHART_LABEL = "Google Organic"
FACEBOOK_ORGANIC_CHART_LABEL = "Facebook Organic"

# Legacy AdWords label → same bucket as ``google / cpc`` (both → Google Paid; case-insensitive).
_ADWORDS_PPC_LUMP_INTO_GOOGLE_PAID_CASEFOLD = frozenset({"adwords / ppc"})

# Meta Audience Network (GA4 source ``an``) — separate from in-feed Meta paid.
META_AUDIENCE_NETWORK_CHART_LABEL = "Meta Audience Network"
META_AUDIENCE_NETWORK_CHART_COLOR = "#0D9488"

# Single stack segment + color for Meta ads (Facebook / paid, Instagram / cpc, etc.)
FACEBOOK_PAID_CHART_LABEL = "Facebook (paid)"
FACEBOOK_PAID_CHART_COLOR = "#1877F2"

_PAID_MEDIUM_EXACT = frozenset(
    {"paid", "cpc", "cpp", "cpm", "cpi", "paidsocial", "paid_social"}
)


def _is_facebook_family_paid_source_medium(raw: str) -> bool:
    """True for facebook / fb / meta / instagram sources with a paid-style medium."""
    s = (raw or "").strip()
    if " / " not in s:
        return False
    left, right = s.split(" / ", 1)
    src_cf = left.strip().casefold()
    med_cf = right.strip().casefold()

    if med_cf in ("referral", "organic", "(none)"):
        return False
    if not (
        src_cf in ("facebook", "fb", "meta", "instagram")
        or "facebook" in src_cf
    ):
        return False
    if med_cf in _PAID_MEDIUM_EXACT:
        return True
    if any(t in med_cf for t in ("cpc", "cpp", "cpm", "paid", "social")):
        # e.g. paid_social, cpc aliases; exclude plain "social" organic if any
        if med_cf == "social":
            return False
        return True
    return False


def _is_meta_audience_network_paid_source_medium(raw: str) -> bool:
    """True for GA4 source ``an`` (Meta Audience Network) with a paid-style medium."""
    s = (raw or "").strip()
    if " / " not in s:
        return False
    left, right = s.split(" / ", 1)
    if left.strip().casefold() != "an":
        return False
    med_cf = right.strip().casefold()
    if med_cf in ("referral", "organic", "(none)"):
        return False
    if med_cf in _PAID_MEDIUM_EXACT:
        return True
    if any(t in med_cf for t in ("cpc", "cpp", "cpm", "paid", "social")):
        if med_cf == "social":
            return False
        return True
    return False


def _is_referral_source_medium(raw: str) -> bool:
    """True when GA4 ``sessionSourceMedium`` is ``<source> / referral`` (medium is referral)."""
    s = (raw or "").strip()
    if " / " not in s:
        return False
    _left, right = s.split(" / ", 1)
    return right.strip().casefold() == "referral"


def _is_data_not_available_source_medium(raw: str) -> bool:
    """True when the session **source** is GA4's ``(data not available)`` (with any medium)."""
    s = (raw or "").strip()
    k = s.casefold()
    if k == "(data not available)":
        return True
    if " / " in s:
        return s.split(" / ", 1)[0].strip().casefold() == "(data not available)"
    return False


def _bucket_source_medium_for_chart(raw: str) -> str:
    s = (raw or "").strip()
    k = s.casefold()
    if not k or k in _SOURCE_MEDIUM_ALWAYS_OTHER_CASEFOLD:
        return "Other"
    if k in _EMAIL_MARKETING_SOURCE_MEDIA_CASEFOLD:
        return EMAIL_EMAIL_MARKETING_CHART_LABEL
    if k == "(direct) / (none)":
        return DIRECT_TRAFFIC_CHART_LABEL
    if _is_data_not_available_source_medium(s):
        return SOURCE_NOT_AVAILABLE_CHART_LABEL
    if _is_referral_source_medium(s):
        return REFERRAL_CHART_LABEL
    if _is_meta_audience_network_paid_source_medium(s):
        return META_AUDIENCE_NETWORK_CHART_LABEL
    if k == "facebook / social":
        return FACEBOOK_ORGANIC_CHART_LABEL
    if _is_facebook_family_paid_source_medium(s):
        return FACEBOOK_PAID_CHART_LABEL
    if k in _ADWORDS_PPC_LUMP_INTO_GOOGLE_PAID_CASEFOLD or k == "google / cpc":
        return GOOGLE_PAID_CHART_LABEL
    if k == "google / organic":
        return GOOGLE_ORGANIC_CHART_LABEL
    return s


def _collapse_source_medium_for_stack(
    detail: pd.DataFrame, *, value_col: str, top_n: int
) -> pd.DataFrame:
    if detail.empty:
        return detail
    d = detail.copy()
    d["__bucket__"] = d["Source_medium"].map(_bucket_source_medium_for_chart)
    d = d.groupby(["Year", "__bucket__"], as_index=False)[value_col].sum()

    forced_other = d[d["__bucket__"] == "Other"]
    main = d[d["__bucket__"] != "Other"]

    rank = (
        main.groupby("__bucket__", as_index=False)[value_col]
        .sum()
        .sort_values(value_col, ascending=False)
    )
    top_set = set(rank.head(top_n)["__bucket__"])
    main["__bucket2__"] = main["__bucket__"].where(
        main["__bucket__"].isin(top_set), "Other"
    )
    main = main.groupby(["Year", "__bucket2__"], as_index=False)[value_col].sum()
    main = main.rename(columns={"__bucket2__": "__bucket__"})

    out = pd.concat([main, forced_other], ignore_index=True)
    out = out.groupby(["Year", "__bucket__"], as_index=False)[value_col].sum()
    return out.rename(columns={"__bucket__": "Source / medium"})


def _stack_category_order_by_mean_desc(
    stack_df: pd.DataFrame,
    *,
    value_col: str,
    cat_col: str = "Source / medium",
) -> list[str]:
    """Categories ordered by mean ``value_col`` across years (highest average first)."""
    if stack_df.empty or value_col not in stack_df.columns or cat_col not in stack_df.columns:
        return []
    means = (
        stack_df.groupby(cat_col, observed=True)[value_col]
        .mean()
        .sort_values(ascending=False)
    )
    return list(means.index)


st.set_page_config(page_title="GA4 — March YoY", layout="wide")
st.title("Google Analytics 4 — March (year over year)")
st.caption(
    "Uses `GOOGLE_APPLICATION_CREDENTIALS` (service account JSON) and `GA4_PROPERTY_ID` from `.env`. "
    "**March 1–31** each year. Sessions and new users are **stacked bars** by GA4 **`sessionSourceMedium`** "
    f"(e.g. raw `google / organic` is shown as **{GOOGLE_ORGANIC_CHART_LABEL}**). Each chart keeps the top **{TOP_SOURCE_MEDIA_FOR_CHART}** "
    "source/medium values ranked by **that chart’s metric**; remaining combinations are **Other**. "
    "Stack segments and legend follow **mean** of that metric across the years shown (**highest → lowest**). "
    "**Other** also includes `(not set)`, `DuckDuckGo / organic`, "
    "`Yahoo / organic`, `Bing / organic`, and `ecosia.org / organic`. "
    f"Raw `(direct) / (none)` is **{DIRECT_TRAFFIC_CHART_LABEL}**. "
    f"Rows whose source is `(data not available)` (e.g. `(data not available) / …`) are **{SOURCE_NOT_AVAILABLE_CHART_LABEL}**. "
    f"Any source/medium whose medium is **referral** (e.g. `example.com / referral`) is combined as **{REFERRAL_CHART_LABEL}**. "
    f"**{EMAIL_EMAIL_MARKETING_CHART_LABEL}** combines `hs_automation / email`, `hs_email / email`, "
    "`hs email / email`, and `email / email marketing` (any casing). "
    f"**{META_AUDIENCE_NETWORK_CHART_LABEL}** is used for `an / paid` and other paid-style "
    f"`an / …` Audience Network traffic. All other **Meta in-feed paid** variants "
    f"(e.g. `Facebook / paid`, `Instagram / cpc`) are **{FACEBOOK_PAID_CHART_LABEL}** "
    "(distinct colors in both charts). "
    f"Raw `google / cpc` and `adwords / ppc` are **{GOOGLE_PAID_CHART_LABEL}**; "
    f"raw `facebook / social` is **{FACEBOOK_ORGANIC_CHART_LABEL}** (any casing)."
)

with st.spinner("Fetching GA4 March data by source / medium…"):
    try:
        df_detail = get_march_yoy_by_session_source_medium(years=MARCH_YOY_YEARS)
    except Exception as e:
        st.error(str(e))
        st.stop()

if df_detail.empty:
    st.info("No rows returned.")
    st.stop()

df = (
    df_detail.groupby("Year", as_index=False)
    .agg(Sessions=("Sessions", "sum"), New_Users=("New_Users", "sum"))
    .assign(
        March_Start=lambda x: x["Year"].astype(str) + "-03-01",
        March_End=lambda x: x["Year"].astype(str) + "-03-31",
    )
    .sort_values("Year", ascending=False)
    .reset_index(drop=True)
)

stack_sessions = _collapse_source_medium_for_stack(
    df_detail,
    value_col="Sessions",
    top_n=TOP_SOURCE_MEDIA_FOR_CHART,
)
stack_new = _collapse_source_medium_for_stack(
    df_detail,
    value_col="New_Users",
    top_n=TOP_SOURCE_MEDIA_FOR_CHART,
)

_order_sessions = _stack_category_order_by_mean_desc(
    stack_sessions, value_col="Sessions"
)
_order_new_users = _stack_category_order_by_mean_desc(
    stack_new, value_col="New_Users"
)

st.subheader("Sessions by source / medium")
fig_sessions = px.bar(
    stack_sessions.sort_values("Year"),
    x="Year",
    y="Sessions",
    color="Source / medium",
    barmode="stack",
    labels={"Sessions": "Sessions", "Year": "Year"},
    category_orders=(
        {"Source / medium": _order_sessions} if _order_sessions else {}
    ),
    color_discrete_map={
        FACEBOOK_PAID_CHART_LABEL: FACEBOOK_PAID_CHART_COLOR,
        META_AUDIENCE_NETWORK_CHART_LABEL: META_AUDIENCE_NETWORK_CHART_COLOR,
    },
)
fig_sessions.update_layout(xaxis_type="category", yaxis_title="Sessions")
st.plotly_chart(fig_sessions, use_container_width=True)

st.subheader("New users by source / medium")
fig_new = px.bar(
    stack_new.sort_values("Year"),
    x="Year",
    y="New_Users",
    color="Source / medium",
    barmode="stack",
    labels={"New_Users": "New users", "Year": "Year"},
    category_orders=(
        {"Source / medium": _order_new_users} if _order_new_users else {}
    ),
    color_discrete_map={
        FACEBOOK_PAID_CHART_LABEL: FACEBOOK_PAID_CHART_COLOR,
        META_AUDIENCE_NETWORK_CHART_LABEL: META_AUDIENCE_NETWORK_CHART_COLOR,
    },
)
fig_new.update_layout(xaxis_type="category", yaxis_title="New users")
st.plotly_chart(fig_new, use_container_width=True)

with st.expander("Full March breakdown by source / medium (all rows)"):
    st.dataframe(
        df_detail.rename(
            columns={"Source_medium": "Source / medium", "New_Users": "New users"}
        ),
        use_container_width=True,
        hide_index=True,
    )

st.subheader("Search sessions — branded vs non-branded (www.fivejourneys.com)")
st.caption(
    "**Sessions only** from **Organic Search** and **Paid Search**, where `hostName` contains "
    "`fivejourneys` (override with **`GA4_SITE_HOST_MATCH`** in `.env`). "
    "**Not** a paid-vs-organic chart — both channels are combined into branded vs non-branded.\n\n"
    "- **Paid search:** `sessionGoogleAdsKeyword` (the search term).\n"
    "- **Organic search:** Google does not expose the query for most traffic (`(not provided)`). "
    "Organic rows use **`pageTitle`** as a proxy: title contains your brand phrases → **branded**.\n\n"
    "Brand phrases default to **five journeys** / **fivejourneys**; set **`GA4_BRAND_TERMS`** "
    "(comma-separated) to override."
)

df_brand = None
brand_fetch_error: str | None = None
with st.spinner("Fetching branded vs non-branded search sessions…"):
    try:
        df_brand = get_march_search_branded_sessions_yoy(years=MARCH_YOY_YEARS)
    except Exception as e:
        brand_fetch_error = str(e)

if brand_fetch_error:
    st.warning(f"Could not load branded search breakdown: {brand_fetch_error}")
elif df_brand is None or df_brand.empty:
    st.info("No branded search data returned.")
else:
    brand_chart_df = df_brand.sort_values("Year", ascending=True)
    long_brand = brand_chart_df.melt(
        id_vars=["Year"],
        value_vars=["Branded", "Non_branded"],
        var_name="Segment",
        value_name="Sessions",
    )
    long_brand["Segment"] = long_brand["Segment"].map(
        {"Branded": "Branded", "Non_branded": "Non-branded"}
    )
    fig_brand = px.bar(
        long_brand,
        x="Year",
        y="Sessions",
        color="Segment",
        barmode="group",
        labels={"Sessions": "Sessions", "Year": "Year"},
        color_discrete_map={"Branded": "#9467bd", "Non-branded": "#17becf"},
    )
    fig_brand.update_layout(xaxis_type="category", yaxis_title="Sessions")
    st.plotly_chart(fig_brand, use_container_width=True)

    st.dataframe(
        df_brand[
            [
                "Year",
                "March_Start",
                "March_End",
                "Branded",
                "Non_branded",
            ]
        ].rename(
            columns={
                "March_Start": "March start",
                "March_End": "March end",
                "Branded": "Branded (sessions)",
                "Non_branded": "Non-branded (sessions)",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("How the totals are built (paid keyword vs organic page title)"):
        st.dataframe(
            df_brand.rename(
                columns={
                    "March_Start": "March start",
                    "March_End": "March end",
                    "Paid_branded": "Paid — branded (keyword)",
                    "Paid_non_branded": "Paid — non-branded (keyword)",
                    "Organic_branded_page_title": "Organic — branded (page title proxy)",
                    "Organic_non_branded_page_title": "Organic — non-branded (page title proxy)",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

st.subheader("Summary table")
st.dataframe(
    df[["Year", "March_Start", "March_End", "Sessions", "New_Users"]].rename(
        columns={
            "Year": "Year",
            "March_Start": "March start",
            "March_End": "March end",
            "Sessions": "Sessions",
            "New_Users": "New users",
        }
    ),
    use_container_width=True,
    hide_index=True,
)

st.caption(
    "**New users** is GA4 metric `newUsers` for the same March windows. "
    "March in the current or future year may be partial until the month ends. "
    "Ensure the service account has **Viewer** access on the GA4 property."
)
