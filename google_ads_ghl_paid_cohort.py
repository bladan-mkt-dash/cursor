"""
Google Ads account metrics + GoHighLevel paid-Google new-patient cohort.

Reusable building blocks for period comparisons and larger reports.

GHL **paid Google new patients**:
  - Sign Up Date in range
  - Committed? = Yes
  - Google Ads path tag (google / g-ad / gtm / tag manager)
  - How did you hear about us? classifies as Google

**Discovery calls** = Google Ads ``metrics.conversions`` (account roll-up).

Requires ``google-ads.yaml`` and ``.env`` (GHL_*).
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
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

DEFAULT_GOOGLE_ADS_CUSTOMER_ID = "5504078633"
DEFAULT_GOOGLE_ADS_LOGIN_CUSTOMER_ID = "9759824543"

# Default comparison for ``run_google_ads_ghl_period_comparison.py``.
APRIL_2026 = (date(2026, 4, 1), date(2026, 4, 30), "April 2026")
MAY_2026_MTD = (date(2026, 5, 1), date.today(), "May 2026 MTD")


def _google_ads_customer_id() -> str:
    return (
        os.getenv("GOOGLE_ADS_CUSTOMER_ID") or DEFAULT_GOOGLE_ADS_CUSTOMER_ID
    ).strip().replace("-", "")


def _google_ads_login_customer_id() -> str:
    return (
        os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or DEFAULT_GOOGLE_ADS_LOGIN_CUSTOMER_ID
    ).strip().replace("-", "")


def _google_ads_yaml_path() -> Path:
    p = _PROJECT_DIR / "google-ads.yaml"
    if not p.is_file():
        raise FileNotFoundError(f"Missing google-ads.yaml at {p}")
    return p


def is_google_ads_path_tag(name: str) -> bool:
    s = (name or "").lower()
    return "google" in s or "g-ad" in s or "gtm" in s or "tag manager" in s


def contact_has_google_ads_path_tag(contact: dict) -> bool:
    for t in contact.get("tags") or []:
        tag_name = t if isinstance(t, str) else str((t or {}).get("name") or "")
        if tag_name and is_google_ads_path_tag(tag_name):
            return True
    return False


def pct_change(new: float, old: float) -> float | None:
    if old == 0:
        return None
    return (new - old) / old * 100.0


def format_pct_change(new: float, old: float) -> str:
    p = pct_change(new, old)
    return "N/A" if p is None else f"{p:+.1f}%"


@dataclass(frozen=True)
class PeriodSpec:
    label: str
    since: date
    until: date

    @property
    def since_iso(self) -> str:
        return self.since.isoformat()

    @property
    def until_iso(self) -> str:
        return self.until.isoformat()

    @property
    def calendar_days(self) -> int:
        return (self.until - self.since).days + 1


@dataclass
class GoogleAdsTotals:
    days_with_spend_data: int
    impressions: int
    clicks: int
    discovery_calls: float
    cost: float

    @property
    def cpa_discovery_call(self) -> float | None:
        if self.discovery_calls <= 0:
            return None
        return self.cost / self.discovery_calls


@dataclass
class GhlPaidGoogleCohort:
    path_tag_committed: int
    paid_google_new_patients: int
    truncated: bool


@dataclass
class PeriodMetrics:
    period: PeriodSpec
    ads: GoogleAdsTotals
    ghl: GhlPaidGoogleCohort

    @property
    def impressions_per_day(self) -> float:
        return self.ads.impressions / self.period.calendar_days

    @property
    def clicks_per_day(self) -> float:
        return self.ads.clicks / self.period.calendar_days

    @property
    def discovery_calls_per_day(self) -> float:
        return self.ads.discovery_calls / self.period.calendar_days

    @property
    def new_patients_per_day(self) -> float:
        return self.ghl.paid_google_new_patients / self.period.calendar_days

    @property
    def cost_per_day(self) -> float:
        return self.ads.cost / self.period.calendar_days

    @property
    def cpa_new_patient(self) -> float | None:
        if self.ghl.paid_google_new_patients <= 0:
            return None
        return self.ads.cost / self.ghl.paid_google_new_patients

    @property
    def patients_per_discovery_call_pct(self) -> float | None:
        if self.ads.discovery_calls <= 0:
            return None
        return (
            self.ghl.paid_google_new_patients / self.ads.discovery_calls * 100.0
        )


def fetch_google_ads_totals(since: str, until: str) -> GoogleAdsTotals:
    """Account-level impressions, clicks, conversions, cost for inclusive date range."""
    query = f"""
        SELECT
            segments.date,
            metrics.cost_micros,
            metrics.impressions,
            metrics.clicks,
            metrics.conversions
        FROM campaign
        WHERE segments.date BETWEEN '{since}' AND '{until}'
          AND campaign.status != 'REMOVED'
    """
    client = GoogleAdsClient.load_from_storage(path=str(_google_ads_yaml_path()))
    client.login_customer_id = _google_ads_login_customer_id()
    ga = client.get_service("GoogleAdsService")
    daily: dict[str, dict] = defaultdict(
        lambda: {"cost_micros": 0, "impressions": 0, "clicks": 0, "conversions": 0.0}
    )
    try:
        stream = ga.search_stream(
            customer_id=_google_ads_customer_id(), query=query
        )
        for batch in stream:
            for row in batch.results:
                d = row.segments.date
                daily[d]["cost_micros"] += row.metrics.cost_micros or 0
                daily[d]["impressions"] += row.metrics.impressions or 0
                daily[d]["clicks"] += row.metrics.clicks or 0
                daily[d]["conversions"] += float(row.metrics.conversions or 0)
    except GoogleAdsException as ex:
        msg = "\n".join(err.message for err in ex.failure.errors)
        raise RuntimeError(f"Google Ads API error:\n{msg}") from ex

    totals = GoogleAdsTotals(
        days_with_spend_data=len(daily),
        impressions=0,
        clicks=0,
        discovery_calls=0.0,
        cost=0.0,
    )
    for v in daily.values():
        totals.impressions += v["impressions"]
        totals.clicks += v["clicks"]
        totals.discovery_calls += v["conversions"]
        totals.cost += v["cost_micros"] / 1_000_000.0
    return totals


def fetch_google_ads_daily(since: str, until: str) -> pd.DataFrame:
    """Daily account roll-up: date, impressions, clicks, discovery_calls, cost."""
    query = f"""
        SELECT
            segments.date,
            metrics.cost_micros,
            metrics.impressions,
            metrics.clicks,
            metrics.conversions
        FROM campaign
        WHERE segments.date BETWEEN '{since}' AND '{until}'
          AND campaign.status != 'REMOVED'
    """
    client = GoogleAdsClient.load_from_storage(path=str(_google_ads_yaml_path()))
    client.login_customer_id = _google_ads_login_customer_id()
    ga = client.get_service("GoogleAdsService")
    rows: list[dict] = []
    try:
        stream = ga.search_stream(
            customer_id=_google_ads_customer_id(), query=query
        )
        for batch in stream:
            for row in batch.results:
                rows.append(
                    {
                        "date": row.segments.date,
                        "impressions": int(row.metrics.impressions or 0),
                        "clicks": int(row.metrics.clicks or 0),
                        "discovery_calls": float(row.metrics.conversions or 0),
                        "cost_micros": int(row.metrics.cost_micros or 0),
                    }
                )
    except GoogleAdsException as ex:
        msg = "\n".join(err.message for err in ex.failure.errors)
        raise RuntimeError(f"Google Ads API error:\n{msg}") from ex

    if not rows:
        return pd.DataFrame(
            columns=[
                "date",
                "impressions",
                "clicks",
                "discovery_calls",
                "cost",
            ]
        )

    df = pd.DataFrame(rows)
    agg = (
        df.groupby("date", as_index=False)
        .agg(
            {
                "impressions": "sum",
                "clicks": "sum",
                "discovery_calls": "sum",
                "cost_micros": "sum",
            }
        )
        .sort_values("date")
    )
    agg["cost"] = agg["cost_micros"] / 1_000_000.0
    agg["date"] = pd.to_datetime(agg["date"])
    return agg.drop(columns=["cost_micros"])


def fetch_ghl_paid_google_cohort(since: str, until: str) -> GhlPaidGoogleCohort:
    hear_id = resolve_hear_about_us_custom_field_id(None)
    data = fetch_signup_date_range_committed_yes_contacts(since, until)
    path_tag = 0
    paid_google = 0
    for c in data["contacts"]:
        if not contact_has_google_ads_path_tag(c):
            continue
        path_tag += 1
        raw = contact_custom_field_value(c, hear_id)
        if classify_hear_about_wom_vs_google(raw) == "Google":
            paid_google += 1
    return GhlPaidGoogleCohort(
        path_tag_committed=path_tag,
        paid_google_new_patients=paid_google,
        truncated=bool(data.get("truncated_pages")),
    )


def load_period_metrics(period: PeriodSpec) -> PeriodMetrics:
    since, until = period.since_iso, period.until_iso
    return PeriodMetrics(
        period=period,
        ads=fetch_google_ads_totals(since, until),
        ghl=fetch_ghl_paid_google_cohort(since, until),
    )


def build_period_totals_dataframe(metrics: list[PeriodMetrics]) -> pd.DataFrame:
    """One row per period with totals and CPA fields."""
    rows: list[dict] = []
    for m in metrics:
        row: dict = {
            "period": m.period.label,
            "since": m.period.since_iso,
            "until": m.period.until_iso,
            "calendar_days": m.period.calendar_days,
            "impressions": m.ads.impressions,
            "clicks": m.ads.clicks,
            "discovery_calls": round(m.ads.discovery_calls, 2),
            "ghl_path_tag_cohort": m.ghl.path_tag_committed,
            "ghl_paid_google_new_patients": m.ghl.paid_google_new_patients,
            "cost": round(m.ads.cost, 2),
            "cpa_discovery_call": round(m.ads.cpa_discovery_call, 2)
            if m.ads.cpa_discovery_call is not None
            else None,
            "cpa_new_patient": round(m.cpa_new_patient, 2)
            if m.cpa_new_patient is not None
            else None,
            "patients_per_discovery_call_pct": round(
                m.patients_per_discovery_call_pct, 1
            )
            if m.patients_per_discovery_call_pct is not None
            else None,
            "ghl_truncated": m.ghl.truncated,
        }
        rows.append(row)
    return pd.DataFrame(rows)


def build_daily_pace_dataframe(
    metrics: list[PeriodMetrics],
    *,
    baseline_index: int = 0,
) -> pd.DataFrame:
    """Daily averages per period with ``pct_change_vs_baseline`` on key metrics."""
    rows: list[dict] = []
    base = metrics[baseline_index] if metrics else None

    def _pace(m: PeriodMetrics) -> dict:
        return {
            "impressions_per_day": m.impressions_per_day,
            "clicks_per_day": m.clicks_per_day,
            "discovery_calls_per_day": m.discovery_calls_per_day,
            "new_patients_per_day": m.new_patients_per_day,
            "cost_per_day": m.cost_per_day,
            "cpa_discovery_call": m.ads.cpa_discovery_call,
            "cpa_new_patient": m.cpa_new_patient,
        }

    base_pace = _pace(base) if base else {}

    for m in metrics:
        p = _pace(m)
        row = {
            "period": m.period.label,
            "since": m.period.since_iso,
            "until": m.period.until_iso,
            "calendar_days": m.period.calendar_days,
            **{k: round(v, 4) if isinstance(v, float) else v for k, v in p.items()},
        }
        if base is not None and m is not base:
            row["impressions_per_day_chg_pct"] = format_pct_change(
                p["impressions_per_day"], base_pace["impressions_per_day"]
            )
            row["clicks_per_day_chg_pct"] = format_pct_change(
                p["clicks_per_day"], base_pace["clicks_per_day"]
            )
            row["discovery_calls_per_day_chg_pct"] = format_pct_change(
                p["discovery_calls_per_day"], base_pace["discovery_calls_per_day"]
            )
            row["new_patients_per_day_chg_pct"] = format_pct_change(
                p["new_patients_per_day"], base_pace["new_patients_per_day"]
            )
            row["cost_per_day_chg_pct"] = format_pct_change(
                p["cost_per_day"], base_pace["cost_per_day"]
            )
            if p["cpa_discovery_call"] and base_pace["cpa_discovery_call"]:
                row["cpa_discovery_call_chg_pct"] = format_pct_change(
                    p["cpa_discovery_call"], base_pace["cpa_discovery_call"]
                )
            if p["cpa_new_patient"] and base_pace["cpa_new_patient"]:
                row["cpa_new_patient_chg_pct"] = format_pct_change(
                    p["cpa_new_patient"], base_pace["cpa_new_patient"]
                )
        rows.append(row)
    return pd.DataFrame(rows)


def build_comparison_summary_dataframe(
    earlier: PeriodMetrics,
    later: PeriodMetrics,
) -> pd.DataFrame:
    """Side-by-side totals with percent change (later vs earlier)."""

    def _row(metric: str, ev: float, lv: float, *, money: bool = False) -> dict:
        return {
            "metric": metric,
            "earlier_period": earlier.period.label,
            "earlier_value": round(ev, 2) if money else ev,
            "later_period": later.period.label,
            "later_value": round(lv, 2) if money else lv,
            "pct_change": format_pct_change(lv, ev),
        }

    rows = [
        _row("Impressions", earlier.ads.impressions, later.ads.impressions),
        _row("Clicks", earlier.ads.clicks, later.ads.clicks),
        _row(
            "Discovery Calls",
            earlier.ads.discovery_calls,
            later.ads.discovery_calls,
        ),
        _row(
            "New Patients (GHL paid Google)",
            earlier.ghl.paid_google_new_patients,
            later.ghl.paid_google_new_patients,
        ),
        _row("Cost", earlier.ads.cost, later.ads.cost, money=True),
    ]
    if earlier.ads.cpa_discovery_call and later.ads.cpa_discovery_call:
        rows.append(
            _row(
                "CPA per Discovery Call",
                earlier.ads.cpa_discovery_call,
                later.ads.cpa_discovery_call,
                money=True,
            )
        )
    if earlier.cpa_new_patient and later.cpa_new_patient:
        rows.append(
            _row(
                "CPA per New Patient",
                earlier.cpa_new_patient,
                later.cpa_new_patient,
                money=True,
            )
        )
    return pd.DataFrame(rows)


def build_weekly_breakdown(
    period: PeriodSpec,
) -> pd.DataFrame:
    """
    ISO-week buckets (Mon–Sun) clipped to ``period``; Ads daily + GHL counts per week.

    GHL uses sign-up date in each week window (committed yes, path tag, hear-about Google).
    """
    ads_daily = fetch_google_ads_daily(period.since_iso, period.until_iso)
    weeks: list[tuple[date, date]] = []
    cursor = period.since
    while cursor <= period.until:
        week_start = cursor - timedelta(days=cursor.weekday())
        week_end = week_start + timedelta(days=6)
        eff_start = max(week_start, period.since)
        eff_end = min(week_end, period.until)
        if eff_start <= eff_end:
            weeks.append((eff_start, eff_end))
        cursor = week_end + timedelta(days=1)

    rows: list[dict] = []
    for w_start, w_end in weeks:
        since_s, until_s = w_start.isoformat(), w_end.isoformat()
        ghl = fetch_ghl_paid_google_cohort(since_s, until_s)
        if ads_daily.empty:
            imp = clk = 0
            disc = 0.0
            cost = 0.0
        else:
            mask = (ads_daily["date"].dt.date >= w_start) & (
                ads_daily["date"].dt.date <= w_end
            )
            chunk = ads_daily.loc[mask]
            imp = int(chunk["impressions"].sum())
            clk = int(chunk["clicks"].sum())
            disc = float(chunk["discovery_calls"].sum())
            cost = float(chunk["cost"].sum())

        cal_days = (w_end - w_start).days + 1
        rows.append(
            {
                "period_label": period.label,
                "week_start": since_s,
                "week_end": until_s,
                "calendar_days": cal_days,
                "impressions": imp,
                "clicks": clk,
                "discovery_calls": round(disc, 2),
                "ghl_paid_google_new_patients": ghl.paid_google_new_patients,
                "ghl_path_tag_cohort": ghl.path_tag_committed,
                "cost": round(cost, 2),
                "cpa_discovery_call": round(cost / disc, 2) if disc else None,
                "cpa_new_patient": round(cost / ghl.paid_google_new_patients, 2)
                if ghl.paid_google_new_patients
                else None,
                "ghl_truncated": ghl.truncated,
            }
        )
    return pd.DataFrame(rows)


def default_april_may_2026_periods() -> list[PeriodSpec]:
    a0, a1, al = APRIL_2026
    m0, m1, ml = MAY_2026_MTD
    return [
        PeriodSpec(label=al, since=a0, until=a1),
        PeriodSpec(label=ml, since=m0, until=m1),
    ]


def export_comparison_csvs(
    metrics: list[PeriodMetrics],
    out_dir: Path | None = None,
    *,
    prefix: str = "google_ads_ghl_paid_cohort",
) -> dict[str, Path]:
    """Write totals, daily pace, comparison, and weekly (last period) CSVs."""
    out = out_dir or (_PROJECT_DIR / "outputs")
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    totals = build_period_totals_dataframe(metrics)
    p_totals = out / f"{prefix}_period_totals.csv"
    totals.to_csv(p_totals, index=False)
    written["period_totals"] = p_totals

    pace = build_daily_pace_dataframe(metrics)
    p_pace = out / f"{prefix}_daily_pace.csv"
    pace.to_csv(p_pace, index=False)
    written["daily_pace"] = p_pace

    if len(metrics) >= 2:
        comp = build_comparison_summary_dataframe(metrics[0], metrics[1])
        p_comp = out / f"{prefix}_comparison.csv"
        comp.to_csv(p_comp, index=False)
        written["comparison"] = p_comp

    weekly = build_weekly_breakdown(metrics[-1].period)
    p_weekly = out / f"{prefix}_weekly_{metrics[-1].period.since:%Y%m}.csv"
    weekly.to_csv(p_weekly, index=False)
    written["weekly_last_period"] = p_weekly

    return written
