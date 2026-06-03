"""WooCommerce REST + Analytics API helpers for supplement store reporting."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")


@dataclass
class WooCommerceMonthStats:
    gross_sales: float = 0.0
    net_sales: float = 0.0
    items_purchased: int = 0
    orders: int = 0
    orders_refunded: int = 0
    refunded_amount: float = 0.0
    avg_items_per_order: float = 0.0
    avg_net_order_value: float = 0.0


def _strip_env(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().strip('"').strip("'")


def _store_url() -> str:
    raw = _strip_env(
        os.getenv("WOOCOMMERCE_URL")
        or os.getenv("WOO_STORE_URL")
        or "https://www.fivejourneys.com"
    )
    url = raw.rstrip("/")
    # REST keys on this site authenticate against www; bare domain returns 401.
    if url == "https://fivejourneys.com":
        return "https://www.fivejourneys.com"
    return url


def _normalize_woo_credential(value: str, prefix: str) -> str:
    """Fix duplicated prefix when pasting keys that already include ck_/cs_."""
    v = value.strip()
    double = f"{prefix}{prefix}"
    if v.startswith(double):
        return prefix + v[len(double) :]
    return v


def _consumer_key() -> str:
    raw = _strip_env(os.getenv("WOOCOMMERCE_CONSUMER_KEY") or os.getenv("WOO_CONSUMER_KEY"))
    return _normalize_woo_credential(raw, "ck_")


def _consumer_secret() -> str:
    raw = _strip_env(
        os.getenv("WOOCOMMERCE_CONSUMER_SECRET") or os.getenv("WOO_CONSUMER_SECRET")
    )
    return _normalize_woo_credential(raw, "cs_")


def woo_credentials_configured() -> bool:
    return bool(_consumer_key() and _consumer_secret())


def _request(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 120,
) -> Any:
    if not woo_credentials_configured():
        raise ValueError(
            "Set WOOCOMMERCE_CONSUMER_KEY and WOOCOMMERCE_CONSUMER_SECRET in .env "
            "(WooCommerce > Settings > Advanced > REST API, Read access)."
        )
    url = f"{_store_url()}/wp-json/{path.lstrip('/')}"
    response = requests.get(
        url,
        params=params or {},
        auth=(_consumer_key(), _consumer_secret()),
        timeout=timeout,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text[:500]}
    if not response.ok:
        raise RuntimeError(f"WooCommerce HTTP {response.status_code}: {payload}")
    return payload


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    last = _last_day(year, month)
    return (
        f"{year}-{month:02d}-01T00:00:00",
        f"{last.isoformat()}T23:59:59",
    )


def _last_day(year: int, month: int) -> date:
    import calendar

    return date(year, month, calendar.monthrange(year, month)[1])


def fetch_month_order_stats(year: int, month: int) -> WooCommerceMonthStats:
    """
    Month totals from WooCommerce Analytics (matches Woo admin revenue/orders reports).

    Uses ``/wc-analytics/reports/revenue/stats`` and ``/wc-analytics/reports/orders/stats``.
    """
    after, before = _month_bounds(year, month)
    common = {
        "after": after,
        "before": before,
        "interval": "month",
        "per_page": 1,
    }

    revenue = _request(
        "wc-analytics/reports/revenue/stats",
        params={
            **common,
            "fields": "total_sales,net_revenue,refunds",
        },
    )
    orders = _request(
        "wc-analytics/reports/orders/stats",
        params={
            **common,
            "fields": "orders_count,num_items_sold,avg_items_per_order,avg_order_value,net_revenue",
        },
    )

    rev_totals = (revenue.get("totals") or {}) if isinstance(revenue, dict) else {}
    ord_totals = (orders.get("totals") or {}) if isinstance(orders, dict) else {}

    gross = float(rev_totals.get("total_sales") or 0.0)
    net = float(rev_totals.get("net_revenue") or ord_totals.get("net_revenue") or 0.0)
    refunds_amt = abs(float(rev_totals.get("refunds") or 0.0))
    order_count = int(ord_totals.get("orders_count") or 0)
    items = int(ord_totals.get("num_items_sold") or 0)
    avg_items = float(ord_totals.get("avg_items_per_order") or 0.0)
    avg_order = float(ord_totals.get("avg_order_value") or 0.0)

    refund_count = _count_refunded_orders(year, month)

    avg_net = (net / order_count) if order_count else 0.0
    if not avg_items and order_count:
        avg_items = items / order_count

    return WooCommerceMonthStats(
        gross_sales=gross,
        net_sales=net,
        items_purchased=items,
        orders=order_count,
        orders_refunded=refund_count,
        refunded_amount=refunds_amt,
        avg_items_per_order=avg_items,
        avg_net_order_value=avg_net if avg_net else avg_order,
    )


def _count_refunded_orders(year: int, month: int) -> int:
    """Count orders with status ``refunded`` in the calendar month (date_created)."""
    after, before = _month_bounds(year, month)
    count = 0
    page = 1
    while page <= 50:
        batch = _request(
            "wc/v3/orders",
            params={
                "after": after,
                "before": before,
                "status": "refunded",
                "per_page": 100,
                "page": page,
            },
        )
        if not isinstance(batch, list) or not batch:
            break
        count += len(batch)
        if len(batch) < 100:
            break
        page += 1
    return count
