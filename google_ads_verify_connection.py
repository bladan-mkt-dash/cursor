"""
Verify Google Ads API credentials (google-ads.yaml) and print last-7-day
campaign performance: name, impressions, clicks, cost (account currency).
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pandas as pd
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

# Manager (MCC) — sent as login-customer-id header when accessing client accounts.
LOGIN_CUSTOMER_ID = "9759824543"
# Client account to query (metrics for this customer ID).
CUSTOMER_ID = "5504078633"

QUERY = """
    SELECT
        campaign.id,
        campaign.name,
        metrics.impressions,
        metrics.clicks,
        metrics.cost_micros
    FROM campaign
    WHERE segments.date DURING LAST_7_DAYS
      AND campaign.status != 'REMOVED'
"""


def main() -> None:
    customer_id = CUSTOMER_ID.replace("-", "").strip()
    login_cid = LOGIN_CUSTOMER_ID.replace("-", "").strip()
    if not customer_id.isdigit() or len(customer_id) != 10:
        raise SystemExit(
            "Set CUSTOMER_ID at the top of this file to your 10-digit "
            "Google Ads customer ID (digits only, no dashes)."
        )
    if not login_cid.isdigit() or len(login_cid) != 10:
        raise SystemExit(
            "Set LOGIN_CUSTOMER_ID to your 10-digit MCC ID (digits only, no dashes)."
        )

    config = Path("google-ads.yaml")
    if not config.is_file():
        raise SystemExit(
            f"Missing {config.resolve()} — run this script from the folder that contains google-ads.yaml."
        )

    client = GoogleAdsClient.load_from_storage(path="google-ads.yaml")
    client.login_customer_id = "9759824543"
    ga_service = client.get_service("GoogleAdsService")

    totals: dict[int, dict] = defaultdict(
        lambda: {"name": "", "impressions": 0, "clicks": 0, "cost_micros": 0}
    )

    try:
        stream = ga_service.search_stream(customer_id=customer_id, query=QUERY)
        for batch in stream:
            for row in batch.results:
                camp_id = row.campaign.id
                t = totals[camp_id]
                if not t["name"]:
                    t["name"] = row.campaign.name
                t["impressions"] += row.metrics.impressions
                t["clicks"] += row.metrics.clicks
                t["cost_micros"] += row.metrics.cost_micros
    except GoogleAdsException as ex:
        print("Google Ads API error — connection or permission issue:\n")
        for err in ex.failure.errors:
            print(f"  {err.message}")
        raise SystemExit(1) from ex

    if not totals:
        print("Connection OK, but no campaign rows returned for LAST_7_DAYS.")
        return

    rows = [
        {
            "Campaign": t["name"],
            "Impressions": int(t["impressions"]),
            "Clicks": int(t["clicks"]),
            "Cost": t["cost_micros"] / 1_000_000,
        }
        for t in totals.values()
    ]
    df = pd.DataFrame(rows).sort_values("Campaign", ignore_index=True)

    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", None)
    pd.set_option("display.max_colwidth", 60)

    print("Google Ads API: connection successful.")
    print(f"Login customer ID (MCC): {login_cid}")
    print(f"Customer ID (query): {customer_id}")
    print(f"Date range: last 7 days (aggregated per campaign)\n")
    print(
        df.to_string(
            index=False,
            formatters={"Cost": lambda x: f"{x:,.2f}"},
        )
    )


if __name__ == "__main__":
    main()
