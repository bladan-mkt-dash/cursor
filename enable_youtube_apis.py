"""Open Google Cloud Console pages to enable YouTube APIs for this project."""

from __future__ import annotations

import webbrowser

PROJECT = "996528452668"
APIS = [
    ("YouTube Data API v3", f"youtube.googleapis.com"),
    ("YouTube Analytics API", f"youtubeanalytics.googleapis.com"),
]


def main() -> None:
    print("Enable both APIs in project cursor-marketing-dashboard, then click ENABLE:\n")
    for label, service in APIS:
        url = (
            f"https://console.developers.google.com/apis/api/{service}/overview"
            f"?project={PROJECT}"
        )
        print(f"  {label}:\n    {url}\n")
        webbrowser.open(url)
    print("After enabling, wait ~1 minute, then run:")
    print('  python "EOM Updates/_fetch_youtube_may_tracker.py"')


if __name__ == "__main__":
    main()
