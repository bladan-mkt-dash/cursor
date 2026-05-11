from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

DEFAULT_GOOGLE_ADS_CUSTOMER_ID = "5504078633"
DEFAULT_GOOGLE_ADS_LOGIN_CUSTOMER_ID = "9759824543"
CAMPAIGN_NAME = "Leads-Performance Max March '26 Restart"


def _load_client() -> GoogleAdsClient:
    project_dir = Path(__file__).resolve().parent
    client = GoogleAdsClient.load_from_storage(path=str(project_dir / "google-ads.yaml"))
    client.login_customer_id = DEFAULT_GOOGLE_ADS_LOGIN_CUSTOMER_ID
    return client


def _fetch_campaign_daily(client: GoogleAdsClient, end_date: date) -> pd.DataFrame:
    customer_id = DEFAULT_GOOGLE_ADS_CUSTOMER_ID.replace("-", "").strip()
    campaign_name = CAMPAIGN_NAME.replace("'", "\\'")
    query = f"""
        SELECT
            segments.date,
            campaign.name,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          AND campaign.name = '{campaign_name}'
          AND segments.date BETWEEN '2020-01-01' AND '{end_date.isoformat()}'
        ORDER BY segments.date
    """

    service = client.get_service("GoogleAdsService")
    rows: list[dict] = []
    try:
        stream = service.search_stream(customer_id=customer_id, query=query)
        for batch in stream:
            for row in batch.results:
                rows.append(
                    {
                        "date": pd.to_datetime(row.segments.date),
                        "impressions": int(row.metrics.impressions or 0),
                        "clicks": int(row.metrics.clicks or 0),
                        "cost": float(row.metrics.cost_micros or 0) / 1_000_000.0,
                    }
                )
    except GoogleAdsException as ex:
        msg = "\n".join(err.message for err in ex.failure.errors)
        raise RuntimeError(f"Google Ads API error:\n{msg}") from ex

    if not rows:
        return pd.DataFrame(columns=["date", "impressions", "clicks", "cost"])

    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return df


def _save_graph(df: pd.DataFrame, metric: str, title: str, out_path: Path, color: str) -> None:
    plt.figure(figsize=(11, 4))
    plt.plot(df["date"], df[metric], color=color, linewidth=2)
    plt.title(title)
    plt.xlabel("Date")
    plt.ylabel(metric.capitalize())
    plt.grid(alpha=0.25)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def main() -> None:
    today = date.today()
    end_date = today - timedelta(days=2)  # Exclude yesterday + today.
    client = _load_client()
    df = _fetch_campaign_daily(client, end_date=end_date)

    if df.empty:
        print(f"No rows returned for campaign: {CAMPAIGN_NAME}")
        return

    start_date = df["date"].dt.date.min()
    out_dir = Path(__file__).resolve().parent / "outputs"
    out_dir.mkdir(exist_ok=True)

    impressions_png = out_dir / "google_ads_leads_pm_march26_restart_impressions.png"
    clicks_png = out_dir / "google_ads_leads_pm_march26_restart_clicks.png"
    cost_png = out_dir / "google_ads_leads_pm_march26_restart_cost.png"

    _save_graph(
        df,
        metric="impressions",
        title=f"{CAMPAIGN_NAME} — Impressions ({start_date} to {end_date})",
        out_path=impressions_png,
        color="#4285F4",
    )
    _save_graph(
        df,
        metric="clicks",
        title=f"{CAMPAIGN_NAME} — Clicks ({start_date} to {end_date})",
        out_path=clicks_png,
        color="#FB8C00",
    )
    _save_graph(
        df,
        metric="cost",
        title=f"{CAMPAIGN_NAME} — Cost ({start_date} to {end_date})",
        out_path=cost_png,
        color="#1E8E3E",
    )

    print(f"Campaign: {CAMPAIGN_NAME}")
    print(f"Date range used: {start_date} to {end_date} (excluded yesterday and today)")
    print(f"Rows: {len(df):,}")
    print(f"Total impressions: {int(df['impressions'].sum()):,}")
    print(f"Total clicks: {int(df['clicks'].sum()):,}")
    print(f"Total cost: ${float(df['cost'].sum()):,.2f}")
    print("")
    print("Saved graphs:")
    print(impressions_png)
    print(clicks_png)
    print(cost_png)


if __name__ == "__main__":
    main()
