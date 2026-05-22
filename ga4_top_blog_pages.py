"""
GA4: top 20 Five Journeys blog **posts** by page views (canonical article URLs).

Posts are listed at https://fivejourneys.com/blog/ but GA4 tracks them at
https://fivejourneys.com/{post-slug}/ — not /blog/2/, which is only the blog
index page 2.

Uses ``GOOGLE_APPLICATION_CREDENTIALS`` and ``GA4_PROPERTY_ID`` from ``.env``.
Override the date window with ``GA4_BLOG_START_DATE`` / ``GA4_BLOG_END_DATE``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from google_data import get_top_blog_posts_by_views

_PROJECT_DIR = Path(__file__).resolve().parent
_CREDENTIALS_FILE = _PROJECT_DIR / "ga_credentials.json"
_OUTPUT_CSV = _PROJECT_DIR / "ga4_top_20_blog_pages_by_views.csv"

TOP_N = 20


def main() -> int:
    load_dotenv(_PROJECT_DIR / ".env")

    if _CREDENTIALS_FILE.is_file():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(
            _CREDENTIALS_FILE.resolve()
        )

    start_date = os.getenv("GA4_BLOG_START_DATE", "365daysAgo").strip()
    end_date = os.getenv("GA4_BLOG_END_DATE", "today").strip()

    try:
        df = get_top_blog_posts_by_views(
            limit=TOP_N,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1

    if df.empty:
        print(f"No blog posts with GA4 views ({start_date} to {end_date}).")
        return 0

    df.to_csv(_OUTPUT_CSV, index=False)
    print(
        f"Top {len(df)} blog posts by views ({start_date} to {end_date})\n"
        f"(canonical article URLs from WordPress, not /blog/2/ pagination)\n"
        f"Wrote {_OUTPUT_CSV}\n"
    )

    pd.set_option("display.max_colwidth", 80)
    pd.set_option("display.width", 220)
    print(df.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
