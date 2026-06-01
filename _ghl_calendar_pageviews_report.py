"""GHL calendar embed pages on fivejourneys.com + GA4 monthly views + GHL bookings."""
from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

BASE = "https://fivejourneys.com"
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
EMBED_CACHE_PATH = _PROJECT_DIR / "embed_pages_cache.json"
EMBED_CACHE_TTL = timedelta(hours=24)
SITEMAP_CANDIDATES = (
    f"{BASE}/sitemap_index.xml",
    f"{BASE}/sitemap.xml",
    f"{BASE}/wp-sitemap.xml",
    f"{BASE}/page-sitemap.xml",
)
FOOTER_BOOKING_WIDGET = "vqsYIK9VZZvgZb8vrhP8"
IFRAME_BOOKING = re.compile(
    r'<iframe[^>]+(?:src|data-src|nitro-lazy-src)=["\']([^"\']*widget/booking[^"\']*)["\']',
    re.I,
)

HOME_PATH = "/"
GA4_START_DATE = "2025-07-01"
GA4_END_DATE = "2026-05-22"

BOOKINGS_BY_MONTH = {
    "2025-07": 23,
    "2025-08": 141,
    "2025-09": 187,
    "2025-10": 200,
    "2025-11": 139,
    "2025-12": 129,
    "2026-01": 136,
    "2026-02": 160,
    "2026-03": 195,
    "2026-04": 171,
    "2026-05": 161,
}


def _looks_like_xml(content: bytes) -> bool:
    head = content.lstrip()[:200]
    return head.startswith(b"<?xml") or head.startswith(b"<")


def _parse_xml(content: bytes, *, source: str) -> ET.Element:
    if not content.strip():
        raise ValueError(f"Empty response from {source}")
    if not _looks_like_xml(content):
        preview = content.lstrip()[:80].decode("utf-8", errors="replace")
        raise ValueError(
            f"Expected XML from {source}, got HTML or other content: {preview!r}…"
        )
    try:
        return ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML from {source}: {exc}") from exc


def _fetch_sitemap_document(session: requests.Session, url: str) -> ET.Element:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return _parse_xml(response.content, source=url)


def fetch_urls_via_wp_json(session: requests.Session) -> list[str]:
    """Fallback when sitemap endpoints return NitroPack HTML instead of XML."""
    urls: set[str] = set()
    for post_type in ("pages", "posts"):
        page = 1
        total_pages = 1
        while page <= total_pages:
            response = session.get(
                f"{BASE}/wp-json/wp/v2/{post_type}",
                params={"per_page": 100, "page": page, "_fields": "link"},
                headers={"Accept": "application/json"},
                timeout=30,
            )
            if response.status_code in {400, 404}:
                break
            response.raise_for_status()
            try:
                batch = response.json()
            except ValueError as exc:
                raise ValueError(
                    f"WordPress API returned non-JSON for {post_type} page {page}: {exc}"
                ) from exc
            if not batch:
                break
            for item in batch:
                link = (item.get("link") or "").strip().rstrip("/")
                if link.startswith(BASE):
                    urls.add(link)
            total_pages = max(total_pages, int(response.headers.get("X-WP-TotalPages", page)))
            page += 1
    if not urls:
        raise ValueError("Could not discover site URLs from sitemap or WordPress API")
    return sorted(urls)


def fetch_sitemap_urls(session: requests.Session) -> list[str]:
    for index_url in SITEMAP_CANDIDATES:
        try:
            root = _fetch_sitemap_document(session, index_url)
        except Exception:
            continue

        urls: set[str] = set()
        locs = root.findall(".//sm:loc", NS)
        if not locs:
            locs = [el for el in root.iter() if el.tag.endswith("loc")]
        if not locs:
            continue

        first = (locs[0].text or "").strip()
        if first.endswith(".xml"):
            for sm in locs:
                sm_url = (sm.text or "").strip()
                if not sm_url.endswith(".xml"):
                    continue
                try:
                    sroot = _fetch_sitemap_document(session, sm_url)
                except Exception:
                    continue
                for loc in sroot.findall(".//sm:loc", NS) or [
                    el for el in sroot.iter() if el.tag.endswith("loc")
                ]:
                    u = (loc.text or "").strip().rstrip("/")
                    if u.startswith(BASE):
                        urls.add(u)
        else:
            for loc in locs:
                u = (loc.text or "").strip().rstrip("/")
                if u.startswith(BASE):
                    urls.add(u)

        if urls:
            return sorted(urls)

    return fetch_urls_via_wp_json(session)


def _page_fetch_url(url: str) -> str:
    return f"{url}?nonitro=1" if "?" not in url else f"{url}&nonitro=1"


def _load_embed_cache() -> list[dict] | None:
    if not EMBED_CACHE_PATH.is_file():
        return None
    try:
        payload = json.loads(EMBED_CACHE_PATH.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(payload["cached_at"])
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - cached_at > EMBED_CACHE_TTL:
            return None
        pages = payload.get("pages")
        return pages if isinstance(pages, list) and pages else None
    except Exception:
        return None


def _save_embed_cache(pages: list[dict]) -> None:
    payload = {
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "pages": pages,
    }
    EMBED_CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def classify_page(html: str) -> dict | None:
    iframe_srcs = IFRAME_BOOKING.findall(html)
    if not iframe_srcs:
        return None

    widget_ids = sorted(
        {
            src.split("/widget/booking/")[-1].split("?")[0]
            for src in iframe_srcs
        }
    )
    return {
        "iframe_srcs": iframe_srcs,
        "widget_ids": widget_ids,
        "embed_kind": "iframe",
    }


def discover_embed_pages(*, force_refresh: bool = False) -> list[dict]:
    if not force_refresh:
        cached = _load_embed_cache()
        if cached:
            return cached

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": BROWSER_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    urls = fetch_sitemap_urls(session)
    found: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {
            ex.submit(session.get, _page_fetch_url(u), timeout=25): u for u in urls
        }
        for fut in as_completed(futs):
            url = futs[fut]
            try:
                r = fut.result()
                r.raise_for_status()
                info = classify_page(r.text)
            except Exception:
                continue
            if info:
                path = urlparse(url).path or "/"
                found.append({"url": url, "path": path, **info})

    result = sorted(found, key=lambda x: x["path"])
    if result:
        _save_embed_cache(result)
    return result


def ga4_monthly_views(page_paths: list[str]) -> dict[str, int]:
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Filter,
        FilterExpression,
        FilterExpressionList,
        Metric,
        RunReportRequest,
    )

    from google_data import _ensure_ga_credentials, _run_report_paginated, _strip_env

    cred_path = _PROJECT_DIR / "ga_credentials.json"
    if cred_path.is_file():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(cred_path.resolve())
    _ensure_ga_credentials()

    property_id = _strip_env(os.getenv("GA4_PROPERTY_ID"))
    if not property_id:
        raise ValueError("Set GA4_PROPERTY_ID in .env")

    # Normalize paths for exact match (GA4 often uses trailing slash)
    path_exprs = []
    for p in page_paths:
        p = p if p.startswith("/") else f"/{p}"
        path_exprs.append(
            FilterExpression(
                filter=Filter(
                    field_name="pagePath",
                    string_filter=Filter.StringFilter(
                        match_type=Filter.StringFilter.MatchType.EXACT,
                        value=p,
                    ),
                )
            )
        )
        if not p.endswith("/"):
            path_exprs.append(
                FilterExpression(
                    filter=Filter(
                        field_name="pagePath",
                        string_filter=Filter.StringFilter(
                            match_type=Filter.StringFilter.MatchType.EXACT,
                            value=p + "/",
                        ),
                    )
                )
            )

    dim_filter = FilterExpression(or_group=FilterExpressionList(expressions=path_exprs))
    client = BetaAnalyticsDataClient()
    rows = _run_report_paginated(
        client,
        property_id=property_id,
        dimensions=[Dimension(name="yearMonth"), Dimension(name="pagePath")],
        metrics=[Metric(name="screenPageViews")],
        start_date=GA4_START_DATE,
        end_date=GA4_END_DATE,
        dimension_filter=dim_filter,
    )

    by_month: Counter[str] = Counter()
    for row in rows:
        ym = row.dimension_values[0].value  # YYYYMM
        month = f"{ym[:4]}-{ym[4:6]}"
        views = int(row.metric_values[0].value)
        by_month[month] += views
    return dict(by_month)


def main() -> int:
    print("Discovering GHL calendar embed pages...", file=sys.stderr)
    pages = discover_embed_pages()
    print(f"Found {len(pages)} pages with embedded/unique GHL booking calendars\n", file=sys.stderr)

    print("=== GHL CALENDAR EMBED PAGES (iframe) ===")
    for p in pages:
        ids = ",".join(p["widget_ids"])
        print(f"{p['path']}\t{ids}")

    embed_paths = [p["path"] for p in pages]
    print("\nFetching GA4 monthly page views (Jul 2025 – May 2026)...", file=sys.stderr)
    try:
        views_excludes_home = ga4_monthly_views(embed_paths)
        views_includes_home = ga4_monthly_views(embed_paths + [HOME_PATH])
    except Exception as e:
        print(f"GA4 error: {e}", file=sys.stderr)
        return 1

    months = sorted(
        set(BOOKINGS_BY_MONTH) | set(views_excludes_home) | set(views_includes_home)
    )
    print("\n=== BY BOOKING MONTH ===")
    print(
        f"{'Month':<10} | {'Page views (Includes Home)':>26} | "
        f"{'Page views (Excludes Home)':>26} | {'Bookings':>8}"
    )
    print("-" * 82)
    total_includes = 0
    total_excludes = 0
    total_bookings = 0
    for m in months:
        inc = views_includes_home.get(m, 0)
        exc = views_excludes_home.get(m, 0)
        bookings = BOOKINGS_BY_MONTH.get(m, 0)
        label = datetime.strptime(m, "%Y-%m").strftime("%b %Y")
        print(f"{label:<10} | {inc:>26,} | {exc:>26,} | {bookings:>8,}")
        total_includes += inc
        total_excludes += exc
        total_bookings += bookings
    print("-" * 82)
    print(
        f"{'TOTAL':<10} | {total_includes:>26,} | {total_excludes:>26,} | "
        f"{total_bookings:>8,}"
    )

    print("\n=== NOTES ===")
    print(
        f"- Page views (Includes Home) = GA4 screenPageViews across {len(pages)} embed "
        f"pages plus home ({HOME_PATH})."
    )
    print(
        f"- Page views (Excludes Home) = GA4 screenPageViews across {len(pages)} embed "
        "pages only."
    )
    print("- Bookings = GHL appointments by dateAdded (all 42 calendars, Jul 25 2025+).")
    print("- Jul 2025 bookings/views are partial (range starts ~25 Jul for GHL).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
