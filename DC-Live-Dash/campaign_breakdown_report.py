"""
Campaign Breakdown report on localhost — live Google Ads, Meta, and GoHighLevel data.

    streamlit run DC-Live-Dash/campaign_breakdown_report.py --server.port 8854

Open (after the command above is running):

    http://127.0.0.1:8854/

Localhost port map (avoid collisions):
  8850 — Digital Channel Live Dashboard
  8851 — Marketing Pulse
  8852 — Op Reports outputs static server (if you use one)
  8853 — Period in Review (--serve)
  8854 — Campaign Breakdown (this app)
  8855 — Monday ops view (--serve)
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DC_LIVE_DIR = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_DC_LIVE_DIR) not in sys.path:
    sys.path.insert(0, str(_DC_LIVE_DIR))

from campaign_breakdown_ui import (
    campaign_breakdown_filters_help_html,
    campaign_breakdown_title_help_html,
    render_campaign_breakdown,
)
from digital_channel_live_data import (
    DEFAULT_DASHBOARD_MONTHS,
    GHL_ATTRIBUTION_HEAR_ABOUT,
    GHL_ATTRIBUTION_OPTIONS,
    GHL_ATTRIBUTION_TRACKER,
    LIVE_DATA_REVISION,
    MEMBERSHIP_LEVELS,
    apply_dashboard_ghl_attribution,
    clear_dashboard_disk_cache,
    clear_ghl_leads_day_cache,
    default_dashboard_since,
    load_dashboard_bundle,
)

load_dotenv(_PROJECT_ROOT / ".env")

DASHBOARD_BUNDLE_REVISION = LIVE_DATA_REVISION


def _help_paragraphs_html(*paragraphs: str) -> str:
    parts = [p.strip() for p in paragraphs if p and p.strip()]
    return "".join(f"<p>{part}</p>" for part in parts)


def _date_range_help_html() -> str:
    return _help_paragraphs_html(
        f"Default view is the last <strong>{DEFAULT_DASHBOARD_MONTHS} months</strong>. "
        "Widen for full history.",
        "Applies to the campaign charts and table below.",
    )


def _refresh_help_html() -> str:
    return _help_paragraphs_html(
        """<strong>Design only</strong> — reload layout and styles using cached data (no API calls).""",
        """<strong>Refresh data</strong> — reload Google Ads, Meta, and GoHighLevel while
keeping cached GHL daily lead files.""",
        """<strong>Hard refresh GHL leads</strong> — clear GHL daily lead cache first, then
reload (use if lead counts look wrong).""",
    )


def _inject_styles() -> None:
    from digital_channel_live_dashboard import _inject_styles as inject

    inject()


def _render_heading_with_help(*args, **kwargs) -> None:
    from digital_channel_live_dashboard import _render_heading_with_help as render

    render(*args, **kwargs)


@st.cache_data(ttl=86400, show_spinner=False)
def _load_dashboard(
    campaign_since: str,
    until: str,
    _revision: str = DASHBOARD_BUNDLE_REVISION,
):
    (
        df,
        notes,
        _lead_summary,
        conv_by_level,
        unallocated_conv,
        wom_conv,
        tracker_conv_by_level,
        tracker_unallocated,
        combined_conv_by_level,
        combined_unallocated,
        sheet_months,
        _channel_month_leads,
        _cpl_channel_month_leads,
        _unallocated_leads_by_attr,
        _sheet_signup_totals,
        _ghl_signups_by_month,
        _sheet_dcs_totals,
        _ghl_dcs_by_month,
        _ghl_leads_org_by_month,
        _ghl_signups_by_level_df,
        _funnel_df,
        _funnel_notes,
    ) = load_dashboard_bundle(
        campaign_since,
        until,
        funnel_since=campaign_since,
        funnel_until=until,
    )
    return (
        df,
        tuple(notes),
        conv_by_level,
        unallocated_conv,
        wom_conv,
        tracker_conv_by_level,
        tracker_unallocated,
        combined_conv_by_level,
        combined_unallocated,
        frozenset(sheet_months),
    )


def main() -> None:
    st.set_page_config(
        page_title="Campaign Breakdown (Live)",
        layout="wide",
        page_icon="📊",
    )
    _inject_styles()

    _render_heading_with_help(
        "Campaign Breakdown",
        campaign_breakdown_title_help_html(default_months=DEFAULT_DASHBOARD_MONTHS),
        style="title",
    )

    today = date.today()
    default_until = today - timedelta(days=1)
    default_since = default_dashboard_since(until=default_until)

    _render_heading_with_help(
        "Date range",
        _date_range_help_html(),
        style="label",
    )
    date_start_col, date_end_col = st.columns(2)
    with date_start_col:
        since = st.date_input(
            "Start date",
            value=default_since,
            max_value=default_until,
        )
    with date_end_col:
        until = st.date_input(
            "End date",
            value=default_until,
            max_value=default_until,
        )
    if since > until:
        st.error("Start date must be on or before end date.")
        st.stop()

    load_label = (
        "Loading Google Ads, Meta, and GoHighLevel… "
        "(first load may take a minute; cached ranges are much faster)"
    )
    with st.spinner(load_label):
        try:
            (
                raw_df,
                notes,
                conv_by_level_df,
                unallocated_conv_df,
                wom_conv_df,
                tracker_conv_by_level_df,
                tracker_unallocated_conv_df,
                combined_conv_by_level_df,
                combined_unallocated_conv_df,
                sheet_months,
            ) = _load_dashboard(since.isoformat(), until.isoformat())
        except Exception as exc:
            st.error(f"Could not load live data.\n\n{exc}")
            st.stop()

    if raw_df.empty:
        st.warning("No campaign data returned for the selected date range.")
        st.stop()

    with st.sidebar:
        _render_heading_with_help(
            "Filters",
            campaign_breakdown_filters_help_html(),
            style="sidebar",
        )
        channels = sorted(raw_df["channel"].dropna().unique())
        selected_channels = st.multiselect("Channel", channels, default=channels)

        st.markdown("**GHL attribution**")
        attribution_labels = {key: label for key, label in GHL_ATTRIBUTION_OPTIONS}
        use_hear_about = st.checkbox(
            attribution_labels[GHL_ATTRIBUTION_HEAR_ABOUT],
            value=True,
            help=(
                "Self-reported **How did you hear about us?** only — Google or FB/IG "
                "responses mapped to each channel."
            ),
        )
        use_tracker = st.checkbox(
            attribution_labels[GHL_ATTRIBUTION_TRACKER],
            value=False,
            help=(
                "All contacts with a Google tag/pixel or Meta lead tag/pixel for the "
                "channel. When both attribution sources are checked, counts use "
                "deduped hear-about ∪ tracker (not double-counted)."
            ),
        )
        if not use_hear_about and not use_tracker:
            st.warning(
                "Select at least one attribution source for GHL leads and signups."
            )

        include_wom_signups = False
        if use_hear_about:
            include_wom_signups = st.checkbox(
                "Include Word of Mouth signups",
                value=False,
                help=(
                    "Hear-about only. When on, signups whose hear-about contains "
                    "\"word of mouth\" are spread by spend share."
                ),
            )

        include_other_signups = False
        if use_hear_about or use_tracker:
            include_other_signups = st.checkbox(
                "Include Other signups",
                value=False,
                help=(
                    "Signups only. When on, committed signups without Google/Meta "
                    "attribution are spread by spend share."
                ),
            )

        channel_mask = raw_df["channel"].isin(selected_channels)
        campaign_pool = sorted(raw_df.loc[channel_mask, "campaign"].dropna().unique())
        selected_campaigns = st.multiselect(
            "Campaign",
            campaign_pool,
            placeholder="All campaigns",
        )
        if not selected_campaigns:
            selected_campaigns = campaign_pool

        creative_pool = sorted(
            raw_df.loc[channel_mask, "creative_type"].dropna().unique()
        )
        selected_creatives = st.multiselect(
            "Asset type",
            creative_pool,
            default=creative_pool,
            help="Matches Sheet slicer: Text, Video, Image, Combo, etc.",
        )

        meta_types = sorted(
            raw_df.loc[raw_df["channel"] == "FB/IG", "fb_ig_type"]
            .dropna()
            .unique()
        )
        meta_types = [t for t in meta_types if str(t).strip()]
        selected_meta_types = st.multiselect(
            "FB/IG type",
            meta_types,
            default=meta_types,
            help="Meta campaign objective bucket (Lead Gen, Traffic, etc.).",
        )

        membership_options = list(MEMBERSHIP_LEVELS)
        selected_membership_levels = st.multiselect(
            "Membership level",
            membership_options,
            default=membership_options,
            help=(
                "Signups from Sep 2025: GHL Committed? = Yes, Sign Up Date in range, "
                "filtered by Membership Level. Pre-Sep 2025 uses tracker sheet totals."
            ),
        )
        if not selected_membership_levels:
            selected_membership_levels = membership_options

        st.markdown("---")
        _render_heading_with_help("Refresh", _refresh_help_html(), style="sidebar")
        if st.button(
            "Design only",
            help="Reload layout and styles only. Uses cached data — no API calls.",
        ):
            st.rerun()

        if st.button(
            "Refresh data",
            help="Reload ads + GHL. Keeps cached GHL daily lead files.",
        ):
            clear_dashboard_disk_cache()
            _load_dashboard.clear()
            st.rerun()

        if st.button(
            "Hard refresh GHL leads",
            help="Clear cached GHL daily lead files and reload (use if lead counts look wrong).",
        ):
            clear_ghl_leads_day_cache()
            clear_dashboard_disk_cache()
            _load_dashboard.clear()
            st.rerun()

    attr_kwargs = dict(
        conv_by_level_df=conv_by_level_df,
        tracker_conv_by_level_df=tracker_conv_by_level_df,
        combined_conv_by_level_df=combined_conv_by_level_df,
        selected_levels=selected_membership_levels,
        unallocated_conv_df=unallocated_conv_df,
        tracker_unallocated_conv_df=tracker_unallocated_conv_df,
        combined_unallocated_conv_df=combined_unallocated_conv_df,
        wom_conv_df=wom_conv_df,
        include_wom_signups=include_wom_signups,
        include_other_signups=include_other_signups,
        sheet_signup_months=set(sheet_months),
    )

    if "month" not in raw_df.columns:
        raw_df = raw_df.copy()
        raw_df["month"] = raw_df["date"].dt.to_period("M").dt.to_timestamp()

    since_month = pd.Timestamp(since).to_period("M").to_timestamp()
    until_month = pd.Timestamp(until).to_period("M").to_timestamp()

    raw_selected = raw_df[
        (raw_df["month"] >= since_month) & (raw_df["month"] <= until_month)
    ].copy()

    filtered = apply_dashboard_ghl_attribution(
        raw_selected,
        use_hear_about=use_hear_about,
        use_tracker=use_tracker,
        **attr_kwargs,
    )

    mask = (
        filtered["channel"].isin(selected_channels)
        & (filtered["campaign"].isin(selected_campaigns))
        & (filtered["creative_type"].isin(selected_creatives))
    )
    if meta_types:
        meta_type_mask = (filtered["channel"] != "FB/IG") | (
            filtered["fb_ig_type"].isin(selected_meta_types)
        )
        mask &= meta_type_mask

    df = filtered.loc[mask].copy()

    if notes:
        with st.expander("Loader notes", expanded=False):
            for note in notes:
                st.markdown(note)

    render_campaign_breakdown(
        df,
        render_heading_with_help=_render_heading_with_help,
        show_section_divider=False,
    )


if __name__ == "__main__":
    main()
