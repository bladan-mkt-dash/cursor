"""
Update the 2026 Digital Cross-Channel Tracker (Monthly Tracker sheet) from APIs.

Run one month (e.g. end of June), from project root:
  python "EOM Updates/run_cross_channel_tracker.py" --month 2026-06
  python run_cross_channel_tracker.py --month 2026-06

Preview without writing:
  python "EOM Updates/run_cross_channel_tracker.py" --month 2026-06 --dry-run
"""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Callable

from _bootstrap import setup

setup()

from tracker_config import column_for_month, parse_month_arg

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
        "--month",
        default="2026-05",
        help="Month to update, e.g. 2026-06 or 6 (default: 2026-05)",
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

    year, month = parse_month_arg(args.month)
    col = column_for_month(year, month)
    print(
        f"2026 Digital Cross-Channel Tracker - {year}-{month:02d} -> column {col}"
        f"{' (dry-run)' if args.dry_run else ''}\n"
    )

    if args.only.strip():
        names = [s.strip() for s in args.only.split(",") if s.strip()]
        unknown = [n for n in names if n not in SOURCES]
        if unknown:
            print(f"Unknown --only source(s): {unknown}", file=sys.stderr)
            print(f"Valid: {', '.join(SOURCES)}", file=sys.stderr)
            return 1
    else:
        names = DEFAULT_ORDER

    extra: list[str] = []
    if args.allow_discovery:
        extra.append("--allow-discovery")

    results: list[tuple[str, int, str]] = []
    for name in names:
        module_name, desc = SOURCES[name]
        print(f"=== {name}: {desc} ===")
        try:
            run_month = _load_run_month(module_name)
            if name == "meta_wt_ig" and args.allow_discovery:
                rc = run_month(year, month, dry_run=args.dry_run, allow_discovery=True)
            else:
                rc = run_month(year, month, dry_run=args.dry_run)
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            rc = 1
        results.append((name, rc, desc))
        print()
        if rc != 0 and args.fail_fast:
            break

    print("--- Summary ---")
    failed = 0
    for name, rc, desc in results:
        status = "ok" if rc == 0 else "FAILED"
        if rc != 0:
            failed += 1
        print(f"  [{status}] {name}: {desc}")

    if failed:
        print(f"\n{failed} source(s) failed.", file=sys.stderr)
        return 1
    print("\nAll sources completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
