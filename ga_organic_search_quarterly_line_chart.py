"""Streamlit: GA4 quarterly traffic by channel + Q4 2025 vs Q1 2026 lift table."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

from google_data import (
    compare_paid_other_by_session_source_medium,
    compare_session_default_channel_sessions,
    get_organic_and_paid_search_sessions_by_quarter,
)

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

# GA4 intraday totals are incomplete; full-trend mode ends here (UTC).
CHART_LAG = timedelta(hours=48)

# Q2 YoY compare: full calendar quarters, no lag trim.
Q2_COMPARE_YEARS = (2023, 2024, 2025, 2026)
Q2_COMPARE_THROUGH = date(2026, 6, 30)


def _chart_through_date(*, apply_lag: bool) -> date:
    """Last calendar date included in the trend chart."""
    if not apply_lag:
        return Q2_COMPARE_THROUGH
    return (datetime.now(timezone.utc) - CHART_LAG).date()

# GA4 ``sessionDefaultChannelGroup`` values treated as paid (aggregated on the chart).
GA4_PAID_SESSION_DEFAULT_CHANNELS = frozenset(
    {
        "Paid Search",
        "Paid Social",
        "Paid Video",
        "Display",
        "Paid Shopping",
        "Cross-network",
        "Paid Other",
    }
)

# Side table: paid channels that explain YoY session change (session default group).
LIFT_TABLE_CHANNELS = ("Cross-network", "Display", "Paid Other", "Paid Search")

_FULL_TREND_LIFT = (
    "2025-10-01",
    "2025-12-31",
    "2026-01-01",
    "2026-03-31",
    "Q4 2025 → Q1 2026",
    "Q4 2025",
    "Q1 2026",
)
_Q2_YOY_LIFT = (
    "2025-04-01",
    "2025-06-30",
    "2026-04-01",
    "2026-06-30",
    "Q2 2025 → Q2 2026",
    "Q2 2025",
    "Q2 2026",
)

_CHART_LABELS = {
    "Organic Search": "Organic search",
    "Paid traffic": "Paid traffic",
    "Direct": "Direct traffic",
    "All sources": "All sources",
}

_CHART_COLOR_MAP = {
    "Organic search": "#1B5E20",
    "Paid traffic": "#B71C1C",
    "Direct traffic": "#E65100",
    "All sources": "#1565C0",
}


def _sessions_df_for_chart_aggregate_paid(df: pd.DataFrame) -> pd.DataFrame:
    """One **Paid traffic** row per quarter (sum of all paid default channel groups)."""
    if df.empty:
        return df
    d = df.copy()
    d["Channel"] = d["Channel"].astype(str)
    is_paid = d["Channel"].isin(GA4_PAID_SESSION_DEFAULT_CHANNELS)
    paid = d.loc[is_paid]
    other = d.loc[~is_paid]
    if paid.empty:
        return d
    keys = ["Year", "Quarter_num", "Quarter_label", "Period_start", "Period_end"]
    paid_sum = paid.groupby(keys, as_index=False).agg(
        {"Sessions": "sum", "Total_users": "sum"}
    )
    paid_sum["Channel"] = "Paid traffic"
    out = pd.concat([other, paid_sum], ignore_index=True)
    ch_order = ["Organic Search", "Paid traffic", "Direct", "All sources"]
    out["Channel"] = pd.Categorical(
        out["Channel"],
        categories=ch_order,
        ordered=True,
    )
    return out.sort_values(["Year", "Quarter_num", "Channel"]).reset_index(drop=True)


st.set_page_config(
    page_title="GA4 Traffic — Quarterly",
    layout="wide",
)

_view_default = 1 if st.query_params.get("mode") == "q2" else 0
view_mode = st.sidebar.radio(
    "Chart range",
    options=("full_trend", "q2_yoy"),
    format_func=lambda k: {
        "full_trend": "Full trend (2023 Q1 → current)",
        "q2_yoy": "Q2 trend (2023–2026, full quarters)",
    }[k],
    index=_view_default,
    key="ga_traffic_view_mode",
)
q2_yoy = view_mode == "q2_yoy"
apply_lag = not q2_yoy
chart_through = _chart_through_date(apply_lag=apply_lag)

if q2_yoy:
    lift_cfg = _Q2_YOY_LIFT
    q2_year_span = f"{Q2_COMPARE_YEARS[0]}–{Q2_COMPARE_YEARS[-1]}"
    page_title = f"Q2 traffic trend {q2_year_span} (GA4)"
    chart_caption = (
        "Chart: **Organic search**, **Paid traffic**, **Direct**, and **All sources** — "
        f"**Q2 only** for **{q2_year_span}** "
        f"(Apr 1 – Jun 30 each year, through **{chart_through.isoformat()}**, "
        "**no 48-hour exclusion**). Side table: latest Q2 YoY session **change** "
        f"({lift_cfg[5]} → {lift_cfg[7]}) by paid channel."
    )
else:
    lift_cfg = _FULL_TREND_LIFT
    page_title = "Traffic by quarter and Q4 2025 → Q1 2026 channel lift (GA4)"
    chart_caption = (
        "Chart: **Organic search**, **Paid traffic** (all GA4 paid default channel groups combined), "
        "**Direct**, and **All sources** — quarters from **2023 Q1** through the current quarter. "
        f"Data through **{chart_through.isoformat()}** (last **48 hours** excluded for GA4 completeness). "
        "Side table: Q4 → Q1 session **change** by channel (**Cross-network**, **Display**, **Paid Other**, "
        "**Paid Search**). Expandable table: quarterly **breakdown** by channel. "
        "Latest chart point may be a **partial quarter**."
    )

(
    lift_earlier_start,
    lift_earlier_end,
    lift_later_start,
    lift_later_end,
    lift_title,
    lift_earlier_label,
    lift_later_label,
) = lift_cfg

st.title(page_title)
st.caption(chart_caption)

with st.spinner("Fetching GA4…"):
    try:
        if q2_yoy:
            df = get_organic_and_paid_search_sessions_by_quarter(
                first_year=Q2_COMPARE_YEARS[0],
                first_quarter=2,
                through=chart_through,
            )
            df = df[df["Quarter_num"] == 2].copy()
            df = df[df["Year"].isin(Q2_COMPARE_YEARS)].copy()
        else:
            df = get_organic_and_paid_search_sessions_by_quarter(
                first_year=2023,
                first_quarter=1,
                through=chart_through,
            )
        lift_full = compare_session_default_channel_sessions(
            earlier_start=lift_earlier_start,
            earlier_end=lift_earlier_end,
            later_start=lift_later_start,
            later_end=lift_later_end,
        )
        paid_other_by_sm = compare_paid_other_by_session_source_medium(
            earlier_start=lift_earlier_start,
            earlier_end=lift_earlier_end,
            later_start=lift_later_start,
            later_end=lift_later_end,
        )
    except Exception as e:
        st.error(str(e))
        st.stop()

if df.empty:
    st.info("No quarterly rows returned from GA4.")
    st.stop()

df_chart = _sessions_df_for_chart_aggregate_paid(df)
df_chart = df_chart.assign(Series=df_chart["Channel"].map(_CHART_LABELS))

last_meta = df_chart.drop_duplicates(subset=["Year", "Quarter_num"]).iloc[-1]
_natural_q_end = pd.Period(
    f"{int(last_meta['Year'])}Q{int(last_meta['Quarter_num'])}",
    freq="Q-DEC",
).end_time.date()
is_partial = str(last_meta["Period_end"]) != _natural_q_end.isoformat()
if is_partial:
    partial_note = (
        f"Chart excludes the most recent 48 hours (through {chart_through.isoformat()})."
        if apply_lag
        else f"Expected full quarter through {_natural_q_end.isoformat()}."
    )
    st.warning(
        f"Latest period **{last_meta['Quarter_label']}** is partial "
        f"({last_meta['Period_start']} → {last_meta['Period_end']}). "
        f"{partial_note}"
    )

quarter_order = (
    df_chart.drop_duplicates(subset=["Quarter_label"])[["Year", "Quarter_num", "Quarter_label"]]
    .sort_values(["Year", "Quarter_num"])["Quarter_label"]
    .tolist()
)

fig = px.line(
    df_chart,
    x="Quarter_label",
    y="Sessions",
    color="Series",
    markers=True,
    labels={"Quarter_label": "Quarter", "Sessions": "Sessions", "Series": "Channel"},
    color_discrete_map=_CHART_COLOR_MAP,
)
fig.update_traces(line=dict(width=2.25), marker=dict(size=7))
fig.update_layout(
    height=520,
    hovermode="x unified",
    yaxis_title="Sessions",
    xaxis_title=None,
    legend_title_text="",
    legend=dict(
        orientation="v",
        yanchor="top",
        y=1,
        xanchor="left",
        x=1.02,
    ),
    margin=dict(r=160),
)
fig.update_xaxes(type="category", categoryorder="array", categoryarray=quarter_order)

chart_col, table_col = st.columns([1.65, 1], gap="large")
with chart_col:
    st.subheader("Sessions by quarter")
    st.plotly_chart(fig, width="stretch")

with table_col:
    st.subheader(f"{lift_title} (why sessions moved)")
    st.caption("Traffic acquisition · `sessionDefaultChannelGroup`")
    if lift_full.empty:
        st.info("No comparison rows returned from GA4.")
    else:
        lift_slice = lift_full[
            lift_full["Session_default_channel_group"].isin(LIFT_TABLE_CHANNELS)
        ].copy()
        lift_slice = lift_slice.sort_values("Session_delta", ascending=False)
        prior_pct_help = (
            f"Percent change vs that channel’s {lift_earlier_label} sessions "
            "(not share of site total)."
        )
        show = pd.DataFrame(
            {
                "Channel": lift_slice["Session_default_channel_group"],
                lift_earlier_label: lift_slice["Sessions_earlier"],
                lift_later_label: lift_slice["Sessions_later"],
                "Δ sessions": lift_slice["Session_delta"],
                f"Δ % vs {lift_earlier_label} (%)": lift_slice[
                    "Session_delta_pct_prior"
                ],
            }
        )
        st.dataframe(
            show,
            width="stretch",
            hide_index=True,
            column_config={
                "Channel": st.column_config.TextColumn("Channel"),
                lift_earlier_label: st.column_config.NumberColumn(
                    lift_earlier_label, format="%d"
                ),
                lift_later_label: st.column_config.NumberColumn(
                    lift_later_label, format="%d"
                ),
                "Δ sessions": st.column_config.NumberColumn("Δ sessions", format="%d"),
                f"Δ % vs {lift_earlier_label} (%)": st.column_config.NumberColumn(
                    f"Δ % vs {lift_earlier_label} (%)",
                    format="%.1f",
                    help=prior_pct_help,
                ),
            },
        )
        net_four = int(show["Δ sessions"].sum())
        prop_d = int(lift_full["Sessions_later"].sum()) - int(
            lift_full["Sessions_earlier"].sum()
        )
        st.caption(
            f"**Combined Δ** from these four channels: **{net_four:+,}** sessions. "
            f"**Net Δ (all channels in GA4):** **{prop_d:+,}** sessions."
        )

st.divider()
st.subheader("Paid Other — what it is, and what’s inside it")
st.markdown(
    """
**Paid Other** is a GA4 **default channel group** for **paid** sessions that don’t get
sorted into **Paid Search**, **Paid Social**, **Display**, **Paid Shopping**,
**Paid Video**, or **Cross-network** under Google’s channel rules. In practice it often
holds **leftover paid sources** (uncommon mediums, some partner / app flows, or traffic
that doesn’t match the other paid buckets). It is **not** “misc organic” — it is still
classified as **paid** in GA4.

The table below breaks **only** sessions counted as **Paid Other** into
`sessionSourceMedium` (e.g. `example / cpc`), for the **same** windows as the lift
table: **"""
    + lift_earlier_label
    + """** vs **"""
    + lift_later_label
    + """**.
"""
)

if paid_other_by_sm.empty:
    st.info("No **Paid Other** rows returned for these periods (or no data in GA4).")
else:
    po_show = pd.DataFrame(
        {
            "Source / medium": paid_other_by_sm["Session_source_medium"],
            lift_earlier_label: paid_other_by_sm["Sessions_earlier"],
            lift_later_label: paid_other_by_sm["Sessions_later"],
            "Δ sessions": paid_other_by_sm["Session_delta"],
            f"Δ % vs {lift_earlier_label} (%)": paid_other_by_sm[
                "Session_delta_pct_prior"
            ],
        }
    )
    st.dataframe(
        po_show,
        width="stretch",
        hide_index=True,
        column_config={
            "Source / medium": st.column_config.TextColumn(
                "Source / medium",
                help="GA4 `sessionSourceMedium` for traffic in the Paid Other channel only.",
            ),
            lift_earlier_label: st.column_config.NumberColumn(
                lift_earlier_label, format="%d"
            ),
            lift_later_label: st.column_config.NumberColumn(
                lift_later_label, format="%d"
            ),
            "Δ sessions": st.column_config.NumberColumn("Δ sessions", format="%d"),
            f"Δ % vs {lift_earlier_label} (%)": st.column_config.NumberColumn(
                f"Δ % vs {lift_earlier_label} (%)",
                format="%.1f",
                help=f"Percent change vs that row’s {lift_earlier_label} sessions.",
            ),
        },
    )
    st.caption(
        f"**Paid Other** session totals in this breakdown: {lift_earlier_label} "
        f"**{int(paid_other_by_sm['Sessions_earlier'].sum()):,}** → "
        f"{lift_later_label} **{int(paid_other_by_sm['Sessions_later'].sum()):,}** "
        f"(Δ **{int(paid_other_by_sm['Session_delta'].sum()):+,}**)."
    )

with st.expander("Quarterly table (channel breakdown)"):
    st.caption(
        "Per-quarter **sessions** and **users** by default channel (paid groups **not** aggregated here)."
    )
    wide = df.pivot_table(
        index=["Quarter_label", "Period_start", "Period_end"],
        columns="Channel",
        values=["Sessions", "Total_users"],
        aggfunc="first",
    )
    wide.columns = [f"{m}_{ch.replace(' ', '_')}" for m, ch in wide.columns]
    wide = wide.reset_index().sort_values(
        ["Period_start"],
    )
    st.dataframe(wide, width="stretch", hide_index=True)
