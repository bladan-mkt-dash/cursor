"""
Update the Digital Cross-Channel Tracker (Monthly Tracker sheet) from APIs.

Run one month (e.g. end of June), from project root:
  python "EOM Updates/run_cross_channel_tracker.py" --tracker 2026 --month 2026-06
  python "EOM Updates/run_cross_channel_tracker.py" --tracker 2025 --from 2025-04 --to 2025-12

Preview without writing:
  python "EOM Updates/run_cross_channel_tracker.py" --tracker 2025 --month 2025-04 --dry-run
"""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Callable

from _bootstrap import setup

setup()

from tracker_config import (
    active_layout,
    column_for_month,
    configure_tracker,
    iter_months,
    parse_month_arg,
)

# name -> (module, description, rows hint)
SOURCES: dict[str, tuple[str, str]] = {
    "ghl_members": (
        "_fetch_ghl_members_tracker",
        "GHL new members Boston/Newton (rows 185-198)",
    ),
    "woocommerce": (
        "_fetch_woocommerce_tracker",
        "GA4 eShop + WooCommerce (rows 147-160)",
    ),
    "ga_website": (
        "_fetch_ga_website_tracker",
        "GA4 5J Website + channels (rows 83-101)",
    ),
    "ga_blog": ("_fetch_ga_blog_tracker", "GA4 5J Blog /blog* (rows 108-114)"),
    "meta_fj": (
        "_fetch_may_meta_tracker",
        "Meta FB + IG Five Journeys (rows 8-17, 32-41)",
    ),
    "meta_wt_ig": (
        "_fetch_wt_ig_may_tracker",
        "Instagram @wendietrubowmd (rows 44-53; may need --allow-discovery)",
    ),
    "youtube": ("_fetch_youtube_may_tracker", "YouTube Five Journeys (rows 56-60)"),
}

DEFAULT_ORDER = [
    "ghl_members",
    "woocommerce",
    "ga_website",
    "ga_blog",
    "meta_fj",
    "meta_wt_ig",
    "youtube",
]


def _load_run_month(module_name: str) -> Callable[..., int]:
    mod = importlib.import_module(module_name)
    fn = getattr(mod, "run_month", None)
    if fn is None:
        raise RuntimeError(f"{module_name} has no run_month(); update the fetch module.")
    return fn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tracker",
        type=int,
        default=2026,
        choices=(2024, 2025, 2026),
        help="Tracker workbook year (2024, 2025, or 2026 spreadsheet)",
    )
    parser.add_argument(
        "--month",
        default="",
        help="Single month, e.g. 2026-06 or 6",
    )
    parser.add_argument(
        "--from",
        dest="month_from",
        default="",
        help="Start month for range backfill (with --to), e.g. 2025-04",
    )
    parser.add_argument(
        "--to",
        dest="month_to",
        default="",
        help="End month for range backfill, e.g. 2025-12",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print values; do not write to the sheet",
    )
    parser.add_argument(
        "--only",
        default="",
        help=f"Comma-separated sources (default: all). Choices: {','.join(SOURCES)}",
    )
    parser.add_argument(
        "--allow-discovery",
        action="store_true",
        help="Pass through to meta_wt_ig for partial @wendietrubowmd metrics",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on first source error (default: continue and report)",
    )
    args = parser.parse_args(argv)

    configure_tracker(args.tracker)
    layout = active_layout()

    if args.month_from.strip() and args.month_to.strip():
        y0, m0 = parse_month_arg(args.month_from)
        y1, m1 = parse_month_arg(args.month_to)
        months = list(iter_months(y0, m0, y1, m1))
    elif args.month.strip():
        year, month = parse_month_arg(args.month)
        months = [(year, month)]
    else:
        year, month = parse_month_arg("5")
        months = [(year, month)]

    if args.only.strip():
        names = [s.strip() for s in args.only.split(",") if s.strip()]
        unknown = [n for n in names if n not in SOURCES]
        if unknown:
            print(f"Unknown --only source(s): {unknown}", file=sys.stderr)
            print(f"Valid: {', '.join(SOURCES)}", file=sys.stderr)
            return 1
    else:
        names = [n for n in DEFAULT_ORDER if n != "meta_fj" or layout.meta_fj_supported]
        names = [n for n in names if n != "meta_wt_ig" or layout.meta_wt_supported]
        if args.tracker == 2024:
            # GHL member rows already populated on the 2024 workbook.
            names = [n for n in names if n != "ghl_members"]

    if not layout.meta_fj_supported:
        print(
            "Note: Meta Five Journeys fetch skipped (2025 sheet uses a legacy row layout)."
        )
    if not layout.meta_wt_supported:
        print(
            "Note: Instagram @wendietrubowmd fetch skipped (2025 sheet uses a legacy row layout)."
        )
    print(
        f"{args.tracker} Digital Cross-Channel Tracker — "
        f"{len(months)} month(s){' (dry-run)' if args.dry_run else ''}\n"
    )

    failed_total = 0
    for year, month in months:
        col = column_for_month(year, month)
        print(f"######## {year}-{month:02d} → column {col} ########\n")
        results: list[tuple[str, int, str]] = []
        for name in names:
            module_name, desc = SOURCES[name]
            print(f"=== {name}: {desc} ===")
            try:
                run_month = _load_run_month(module_name)
                if name == "meta_wt_ig" and args.allow_discovery:
                    rc = run_month(
                        year, month, dry_run=args.dry_run, allow_discovery=True
                    )
                else:
                    rc = run_month(year, month, dry_run=args.dry_run)
            except Exception as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                rc = 1
            results.append((name, rc, desc))
            print()
            if rc != 0 and args.fail_fast:
                failed_total += 1
                print("--- Summary (stopped) ---")
                for n, r, d in results:
                    print(f"  [{'ok' if r == 0 else 'FAILED'}] {n}: {d}")
                return 1

        print(f"--- Summary {year}-{month:02d} ---")
        for name, rc, desc in results:
            status = "ok" if rc == 0 else "FAILED"
            if rc != 0:
                failed_total += 1
            print(f"  [{status}] {name}: {desc}")
        print()

    if failed_total:
        print(f"\n{failed_total} source-month step(s) failed.", file=sys.stderr)
        return 1
    print("\nAll source-month steps completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
