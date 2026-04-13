import sys
from google.ads.googleads.client import GoogleAdsClient

# 1. SET THESE MANUALLY FOR THIS TEST
MCC_ID = "9759824543"           # The Manager ID
CLIENT_ID = "5504078633" # The FiveJourneys Ad ID

def run_test():
    # Load the YAML file
    client = GoogleAdsClient.load_from_storage("google-ads.yaml")
    
    # OVERRIDE the login header manually
    client.login_customer_id = MCC_ID

    ga_service = client.get_service("GoogleAdsService")

    # A very simple query to just pull campaign names
    query = "SELECT campaign.name FROM campaign LIMIT 5"

    try:
        stream = ga_service.search_stream(customer_id=CLIENT_ID, query=query)
        print("--- CONNECTION SUCCESSFUL! ---")
        for batch in stream:
            for row in batch.results:
                print(f"Campaign found: {row.campaign.name}")
    except Exception as e:
        print(f"--- CONNECTION FAILED ---")
        print(f"Error details: {e}")

if __name__ == "__main__":
    run_test()