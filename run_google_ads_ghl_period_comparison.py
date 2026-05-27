"""
Compare Google Ads + GHL paid-Google cohort across two or more date ranges.

Default: April 2026 (full month) vs May 2026 month-to-date (through today).

Run from the project directory::

    python run_google_ads_ghl_period_comparison.py

Custom periods::

    python run_google_ads_ghl_period_comparison.py \\
        --period "April 2026" 2026-04-01 2026-04-30 \\
        --period "May 2026" 2026-05-01 2026-05-27

Writes CSVs under ``outputs/`` (see ``google_ads_ghl_paid_cohort.export_comparison_csvs``).
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from google_ads_ghl_paid_cohort import (
    PeriodMetrics,
    PeriodSpec,
    build_comparison_summary_dataframe,
    build_daily_pace_dataframe,
    build_period_totals_dataframe,
    build_weekly_breakdown,
    default_april_may_2026_periods,
    export_comparison_csvs,
    load_period_metrics,
)

_PROJECT_DIR = Path(__file__).resolve().parent


def _parse_periods(raw: list[list[str]] | None) -> list[PeriodSpec]:
    if not raw:
        return default_april_may_2026_periods()
    periods: list[PeriodSpec] = []
    for group in raw:
        if len(group) != 3:
            raise SystemExit(
                "Each --period needs: LABEL YYYY-MM-DD YYYY-MM-DD"
            )
        label, s, u = group[0], group[1], group[2]
        since = date.fromisoformat(s)
        until = date.fromisoformat(u)
        if since > until:
            raise SystemExit(f"Invalid range for {label}: {s} > {u}")
        periods.append(PeriodSpec(label=label, since=since, until=until))
    return periods


def _print_section(title: str, df: pd.DataFrame) -> None:
    print(f"\n=== {title} ===\n")
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    print(df.to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Google Ads vs GHL paid-Google cohort period comparison."
    )
    parser.add_argument(
        "--period",
        nargs=3,
        action="append",
        metavar=("LABEL", "SINCE", "UNTIL"),
        help="Period label and inclusive ISO dates. Repeat for multiple periods.",
    )
    parser.add_argument(
        "--no-weekly",
        action="store_true",
        help="Skip weekly breakdown table and weekly CSV.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_PROJECT_DIR / "outputs",
        help="Directory for CSV exports (default: outputs/).",
    )
    parser.add_argument(
        "--prefix",
        default="google_ads_ghl_paid_cohort",
        help="Filename prefix for exported CSVs.",
    )
    args = parser.parse_args()

    periods = _parse_periods(args.period)

    print("Google Ads + GHL paid-Google cohort comparison")
    print("GHL: Sign Up Date in range, Committed=Yes, path tag, hear-about Google")
    print("Discovery calls: Google Ads conversions\n")

    metrics: list[PeriodMetrics] = []
    for p in periods:
        print(f"Loading {p.label} ({p.since_iso} to {p.until_iso})...")
        m = load_period_metrics(p)
        metrics.append(m)
        if m.ghl.truncated:
            print(f"  WARNING: GHL pagination truncated for {p.label}")

    _print_section("Period totals", build_period_totals_dataframe(metrics))
    _print_section("Daily pace", build_daily_pace_dataframe(metrics))

    if len(metrics) >= 2:
        _print_section(
            f"Comparison ({metrics[1].period.label} vs {metrics[0].period.label})",
            build_comparison_summary_dataframe(metrics[0], metrics[1]),
        )

    written = export_comparison_csvs(
        metrics, args.output_dir, prefix=args.prefix
    )
    if args.no_weekly and "weekly_last_period" in written:
        written["weekly_last_period"].unlink(missing_ok=True)
        del written["weekly_last_period"]
    elif not args.no_weekly:
        _print_section(
            f"Weekly - {metrics[-1].period.label}",
            build_weekly_breakdown(metrics[-1].period),
        )

    print("\nExported CSVs:")
    for key, path in sorted(written.items()):
        print(f"  {key}: {path}")


if __name__ == "__main__":
    main()
