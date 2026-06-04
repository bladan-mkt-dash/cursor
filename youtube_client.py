"""YouTube Data + Analytics API helpers for channel monthly metrics."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

CONFIG_DIR = Path.home() / ".config" / "mcp-google-sheets"
CREDENTIALS_PATH = CONFIG_DIR / "credentials.json"
YOUTUBE_TOKEN_PATH = CONFIG_DIR / "youtube_token.json"

DEFAULT_CHANNEL_ID = "UCB96DmDqfzdJW5GsT06MLQQ"  # "5 Journeys" on YouTube

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _load_token_info() -> dict | None:
    if not YOUTUBE_TOKEN_PATH.exists():
        return None
    return json.loads(YOUTUBE_TOKEN_PATH.read_text(encoding="utf-8"))


def _credentials_from_env() -> Credentials | None:
    refresh = (os.getenv("YOUTUBE_REFRESH_TOKEN") or "").strip()
    client_id = (
        os.getenv("YOUTUBE_OAUTH_CLIENT_ID")
        or os.getenv("GOOGLE_OAUTH_CLIENT_ID")
        or ""
    ).strip()
    client_secret = (
        os.getenv("YOUTUBE_OAUTH_CLIENT_SECRET")
        or os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
        or ""
    ).strip()
    if not (refresh and client_id and client_secret):
        return None
    creds = Credentials(
        token=None,
        refresh_token=refresh,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def get_credentials(*, allow_interactive: bool = False) -> Credentials:
    creds: Credentials | None = None
    info = _load_token_info()
    if info:
        creds = Credentials.from_authorized_user_info(info, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        YOUTUBE_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        return creds

    if creds and creds.valid:
        return creds

    env_creds = _credentials_from_env()
    if env_creds and env_creds.valid:
        return env_creds

    if not allow_interactive:
        raise RuntimeError(
            "YouTube is not authorized. Run:\n  python auth_youtube.py\n"
            f"Token path: {YOUTUBE_TOKEN_PATH}"
        )

    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"Missing OAuth client file: {CREDENTIALS_PATH}\n"
            "Use the same Desktop OAuth client as Google Sheets (Google Cloud Console)."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(
        port=0,
        open_browser=True,
        access_type="offline",
        prompt="consent select_account",
        authorization_prompt_message=(
            "If Google shows a YouTube channel/account picker, choose "
            "**5 Journeys** (brand), not a personal channel."
        ),
    )
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    YOUTUBE_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def channel_id() -> str:
    return (os.getenv("YOUTUBE_CHANNEL_ID") or DEFAULT_CHANNEL_ID).strip()


def _youtube_data(creds: Credentials):
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _youtube_analytics(creds: Credentials):
    return build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)


def resolve_channel_id(creds: Credentials) -> str:
    """Return configured channel id, or the authenticated user's channel when Data API works."""
    configured = channel_id()
    try:
        yt = _youtube_data(creds)
        resp = yt.channels().list(part="id", mine=True, maxResults=5).execute()
    except Exception:
        return configured
    items = resp.get("items") or []
    if not items:
        return configured
    mine_ids = {item["id"] for item in items}
    if configured in mine_ids:
        return configured
    if len(mine_ids) == 1:
        return next(iter(mine_ids))
    return configured


def count_videos_published(
    creds: Credentials,
    channel: str,
    start: date,
    end_exclusive: date,
) -> int:
    """Count uploads on channel with publishedAt in [start, end_exclusive)."""
    yt = _youtube_data(creds)
    published_after = datetime(
        start.year, start.month, start.day, tzinfo=timezone.utc
    ).isoformat().replace("+00:00", "Z")
    published_before = datetime(
        end_exclusive.year,
        end_exclusive.month,
        end_exclusive.day,
        tzinfo=timezone.utc,
    ).isoformat().replace("+00:00", "Z")

    total = 0
    page_token: str | None = None
    while True:
        resp = (
            yt.search()
            .list(
                part="id",
                channelId=channel,
                type="video",
                publishedAfter=published_after,
                publishedBefore=published_before,
                maxResults=50,
                pageToken=page_token,
            )
            .execute()
        )
        total += len(resp.get("items") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            return total


def _analytics_ids_candidates(channel: str) -> list[str]:
    """Brand accounts usually require channel==MINE after OAuth as the brand identity."""
    out = ["channel==MINE"]
    if channel:
        out.append(f"channel=={channel}")
    return out


def fetch_channel_month_analytics(
    creds: Credentials,
    channel: str,
    start: date,
    end: date,
    *,
    include_watch_time: bool = False,
) -> dict[str, float]:
    """Channel-level totals for an inclusive calendar month (Analytics API)."""
    ya = _youtube_analytics(creds)
    metrics = "views,engagedViews,likes,subscribersGained"
    if include_watch_time:
        metrics += ",estimatedMinutesWatched,averageViewDuration"
    last_err: HttpError | None = None
    for ids in _analytics_ids_candidates(channel):
        try:
            resp = (
                ya.reports()
                .query(
                    ids=ids,
                    startDate=start.isoformat(),
                    endDate=end.isoformat(),
                    metrics=metrics,
                )
                .execute()
            )
            headers = [h["name"] for h in resp.get("columnHeaders") or []]
            row = (resp.get("rows") or [[0] * len(headers)])[0]
            out: dict[str, float] = {}
            for name, value in zip(headers, row):
                out[name] = float(value or 0)
            return out
        except HttpError as exc:
            last_err = exc
            if exc.resp.status != 403:
                raise
    raise last_err or RuntimeError("YouTube Analytics request failed")


def fetch_channel_month_metrics(
    creds: Credentials, year: int, month: int, *, legacy_layout: bool = False
) -> dict[str, int | float]:
    """Channel metrics for an inclusive calendar month."""
    channel = resolve_channel_id(creds)
    import sys
    from pathlib import Path

    eom = Path(__file__).resolve().parent / "EOM Updates"
    if str(eom) not in sys.path:
        sys.path.insert(0, str(eom))
    from tracker_config import month_period_dates

    start, end = month_period_dates(year, month)
    if month == 12:
        end_exclusive = date(year + 1, 1, 1)
    else:
        end_exclusive = date(year, month + 1, 1)
    analytics = fetch_channel_month_analytics(
        creds, channel, start, end, include_watch_time=legacy_layout
    )
    videos = count_videos_published(creds, channel, start, end_exclusive)
    out: dict[str, int | float] = {
        "channel_id": channel,
        "videos_published": videos,
        "views": int(analytics.get("views", 0)),
        "engaged_views": int(analytics.get("engagedViews", 0)),
        "likes": int(analytics.get("likes", 0)),
        "new_subscribers": int(analytics.get("subscribersGained", 0)),
    }
    if legacy_layout:
        out["watch_minutes"] = float(analytics.get("estimatedMinutesWatched", 0))
        out["avg_view_seconds"] = float(analytics.get("averageViewDuration", 0))
    return out


def fetch_may_2026_metrics(creds: Credentials) -> dict[str, int]:
    return fetch_channel_month_metrics(creds, 2026, 5)
