"""Fetch GA4 eShop traffic + WooCommerce sales; write Mar–May 2026 tracker columns J–L."""

from __future__ import annotations

import calendar
import sys
from dataclasses import dataclass
from pathlib import Path

from _bootstrap import setup
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Filter,
    FilterExpression,
    FilterExpressionList,
    Metric,
    RunReportRequest,
)

from google_data import _ensure_ga_credentials, _strip_env
from woocommerce_client import (
    WooCommerceMonthStats,
    fetch_month_order_stats,
    woo_credentials_configured,
)

setup()

from tracker_config import column_for_month
from tracker_sheets import write_columns

ROW_HOME_VIEWS = 147
ROW_HOME_USERS = 148
ROW_CHECKOUT_VIEWS = 149
ROW_CHECKOUT_USERS = 150
ROW_AVG_TIME = 151
ROW_BOUNCE = 152
ROW_GROSS = 153
ROW_ITEMS = 154
ROW_ORDERS = 155
ROW_NET = 156
ROW_AVG_NET_ORDER = 157
ROW_ITEMS_PER_ORDER = 158
ROW_ORDERS_REFUNDED = 159
ROW_REFUNDED_AMOUNT = 160

BACKFILL_MONTHS = ((2026, 3), (2026, 4), (2026, 5))


@dataclass
class Ga4ShopMetrics:
    home_views: int
    home_users: int
    checkout_views: int
    checkout_users: int
    avg_time_seconds: float
    bounce_rate: float


def _path_filter_exact(path: str) -> FilterExpression:
    return FilterExpression(
        filter=Filter(
            field_name="pagePath",
            string_filter=Filter.StringFilter(
                match_type=Filter.StringFilter.MatchType.EXACT,
                value=path,
            ),
        )
    )


def _path_filter_begins(prefix: str) -> FilterExpression:
    return FilterExpression(
        filter=Filter(
            field_name="pagePath",
            string_filter=Filter.StringFilter(
                match_type=Filter.StringFilter.MatchType.BEGINS_WITH,
                value=prefix,
            ),
        )
    )


def _path_filter_checkout_funnel() -> FilterExpression:
    return FilterExpression(
        or_group=FilterExpressionList(
            expressions=[
                _path_filter_exact("/cart/"),
                _path_filter_exact("/checkout/"),
            ]
        )
    )


def _run_filtered(
    client: BetaAnalyticsDataClient,
    property_id: str,
    start: str,
    end: str,
    filt: FilterExpression,
    metrics: list[str],
) -> list[float]:
    req = RunReportRequest(
        property=f"properties/{property_id}",
        metrics=[Metric(name=m) for m in metrics],
        date_ranges=[DateRange(start_date=start, end_date=end)],
        dimension_filter=filt,
    )
    resp = client.run_report(req)
    if not resp.rows:
        return [0.0] * len(metrics)
    first = resp.rows[0]
    return [float(first.metric_values[i].value) for i in range(len(metrics))]


def fetch_ga4_shop_metrics(year: int, month: int) -> Ga4ShopMetrics:
    _ensure_ga_credentials()
    property_id = _strip_env(__import__("os").getenv("GA4_PROPERTY_ID"))
    if not property_id:
        raise ValueError("Set GA4_PROPERTY_ID in .env")

    last = calendar.monthrange(year, month)[1]
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month:02d}-{last:02d}"
    client = BetaAnalyticsDataClient()

    home = _run_filtered(
        client,
        property_id,
        start,
        end,
        _path_filter_begins("/shop"),
        ["screenPageViews", "activeUsers", "averageSessionDuration", "bounceRate"],
    )
    checkout_users = _run_filtered(
        client,
        property_id,
        start,
        end,
        _path_filter_exact("/checkout/"),
        ["activeUsers"],
    )
    checkout_views = _run_filtered(
        client,
        property_id,
        start,
        end,
        _path_filter_checkout_funnel(),
        ["screenPageViews"],
    )

    return Ga4ShopMetrics(
        home_views=int(home[0]),
        home_users=int(home[1]),
        checkout_views=int(checkout_views[0]),
        checkout_users=int(checkout_users[0]),
        avg_time_seconds=home[2],
        bounce_rate=home[3],
    )


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _fmt_money(n: float) -> str:
    return f"{n:,.2f}"


def _fmt_duration(seconds: float) -> str:
    """Use H:MM:SS (e.g. 0:01:36) so Sheets does not treat M:SS as clock time."""
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def _fmt_bounce(rate: float) -> str:
    return f"{rate * 100:.2f}%"


def _fmt_ratio(n: float) -> str:
    return f"{n:.1f}" if abs(n - round(n)) < 0.05 else f"{n:.2f}"


def build_col_updates(year: int, month: int, *, sales_only: bool = False) -> dict[int, str]:
    updates: dict[int, str] = {}
    if not sales_only:
        ga = fetch_ga4_shop_metrics(year, month)
        updates.update(
            {
                ROW_HOME_VIEWS: _fmt_int(ga.home_views),
                ROW_HOME_USERS: _fmt_int(ga.home_users),
                ROW_CHECKOUT_VIEWS: _fmt_int(ga.checkout_views),
                ROW_CHECKOUT_USERS: _fmt_int(ga.checkout_users),
                ROW_AVG_TIME: _fmt_duration(ga.avg_time_seconds),
                ROW_BOUNCE: _fmt_bounce(ga.bounce_rate),
            }
        )

    if woo_credentials_configured():
        wc: WooCommerceMonthStats = fetch_month_order_stats(year, month)
        updates.update(
            {
                ROW_GROSS: _fmt_money(wc.gross_sales),
                ROW_ITEMS: _fmt_int(wc.items_purchased),
                ROW_ORDERS: _fmt_int(wc.orders),
                ROW_NET: _fmt_money(wc.net_sales),
                ROW_AVG_NET_ORDER: _fmt_money(wc.avg_net_order_value),
                ROW_ITEMS_PER_ORDER: _fmt_ratio(wc.avg_items_per_order),
                ROW_ORDERS_REFUNDED: _fmt_int(wc.orders_refunded),
                ROW_REFUNDED_AMOUNT: _fmt_money(wc.refunded_amount),
            }
        )
    return updates


def run_month(
    year: int, month: int, *, dry_run: bool = False, sales_only: bool = False
) -> int:
    if not woo_credentials_configured():
        print(
            "WooCommerce API keys not in .env — GA4 traffic rows only.",
            file=sys.stderr,
        )

    col = column_for_month(year, month)
    print(f"Fetching eCommerce for {year}-{month:02d} (column {col})...")
    updates = build_col_updates(year, month, sales_only=sales_only)
    print(
        f"  home views={updates.get(ROW_HOME_VIEWS)}, orders={updates.get(ROW_ORDERS, '(Woo N/A)')}"
    )

    if dry_run:
        print("(dry-run: sheet not updated)")
        return 0

    write_columns({col: updates})
    scope = "rows 153-160" if sales_only else "rows 147-160"
    print(f"Updated FJ E-Commerce {scope}, column {col}.")
    return 0


def main() -> int:
    sales_only = "--sales-only" in sys.argv
    if "--backfill-jkl" in sys.argv:
        updates_by_col: dict[str, dict[int, str]] = {}
        for year, month in BACKFILL_MONTHS:
            col = column_for_month(year, month)
            updates_by_col[col] = build_col_updates(year, month, sales_only=sales_only)
        if "--dry-run" in sys.argv:
            return 0
        write_columns(updates_by_col)
        print("Backfill complete.")
        return 0

    from tracker_config import parse_month_arg

    year, month = parse_month_arg("2026-05")
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--month" and i < len(sys.argv) - 1:
            year, month = parse_month_arg(sys.argv[i + 1])
            break
    return run_month(
        year, month, dry_run="--dry-run" in sys.argv, sales_only=sales_only
    )


if __name__ == "__main__":
    raise SystemExit(main())
