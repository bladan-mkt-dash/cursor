"""Row maps for 2025 vs 2026 Digital Cross-Channel Tracker (Monthly Tracker tab)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WebsiteLayout:
    unique_pageviews: int
    users: int
    sessions: int
    sessions_per_user: int
    views_per_session: int
    scrolled_users: int | None  # None = do not write
    avg_session_duration: int
    bounce_rate: int
    organic_search: int
    direct: int
    display: int | None
    cross_network: int | None
    email: int
    referral: int
    paid_social: int
    organic_social: int
    paid_search: int
    paid_other: int
    all_users: int | None
    new_users: int | None
    new_users_pct: int | None


@dataclass(frozen=True)
class BlogLayout:
    published: int
    pageviews: int
    users: int
    sessions: int
    views_per_user: int
    avg_session_duration: int
    bounce_rate: int


@dataclass(frozen=True)
class YouTubeLayout:
    videos_published: int
    new_subscribers: int
    views: int
    avg_watch_duration: int | None
    engaged_views: int | None
    likes: int | None
    watch_hours: int | None
    legacy: bool = False


@dataclass(frozen=True)
class WooLayout:
    home_views: int
    home_users: int
    checkout_views: int
    checkout_users: int
    avg_time: int
    bounce: int
    gross: int
    items: int
    orders: int
    net: int
    avg_net_order: int
    items_per_order: int
    orders_refunded: int
    refunded_amount: int


@dataclass(frozen=True)
class TrackerLayout:
    version: int
    spreadsheet_id: str
    sheet: str
    jan_column: str
    website: WebsiteLayout
    blog: BlogLayout
    youtube: YouTubeLayout
    woo: WooLayout
    ghl_boston: dict[str, int]
    ghl_newton: dict[str, int]
    ghl_boston_total: int
    ghl_newton_total: int
    ghl_boston_avg: int
    ghl_newton_avg: int
    meta_fj_supported: bool
    meta_wt_supported: bool
    fb_rows: dict[int, str] | None = None
    ig_rows: dict[int, str] | None = None
    youtube_views_key: str = "views"  # 2024 sheet row is "Unique Viewers" → engagedViews


LAYOUT_2025 = TrackerLayout(
    version=2025,
    spreadsheet_id="1oPIba48QuaDhfUP0l6JvoIAQYHQDPgU2JanerfsmYJM",
    sheet="Monthly Tracker",
    jan_column="H",
    website=WebsiteLayout(
        unique_pageviews=73,
        users=74,
        sessions=75,
        sessions_per_user=76,
        views_per_session=77,
        scrolled_users=None,
        avg_session_duration=78,
        bounce_rate=79,
        organic_search=81,
        direct=82,
        display=None,
        cross_network=None,
        email=83,
        referral=84,
        paid_social=85,
        organic_social=86,
        paid_search=87,
        paid_other=88,
        all_users=90,
        new_users=91,
        new_users_pct=92,
    ),
    blog=BlogLayout(
        published=95,
        pageviews=96,
        users=97,
        sessions=98,
        views_per_user=99,
        avg_session_duration=100,
        bounce_rate=101,
    ),
    youtube=YouTubeLayout(
        videos_published=46,
        new_subscribers=47,
        views=48,
        avg_watch_duration=49,
        engaged_views=None,
        likes=None,
        watch_hours=50,
        legacy=True,
    ),
    woo=WooLayout(
        home_views=134,
        home_users=135,
        checkout_views=136,
        checkout_users=137,
        avg_time=138,
        bounce=139,
        gross=140,
        items=141,
        orders=142,
        net=143,
        avg_net_order=144,
        items_per_order=145,
        orders_refunded=146,
        refunded_amount=147,
    ),
    ghl_boston={"Standard": 185, "Silver": 186, "Gold": 187, "Platinum": 188},
    ghl_newton={"Standard": 193, "Silver": 194, "Gold": 195, "Platinum": 196},
    ghl_boston_total=189,
    ghl_newton_total=197,
    ghl_boston_avg=190,
    ghl_newton_avg=198,
    meta_fj_supported=False,
    meta_wt_supported=False,
)

# 2026 Meta Five Journeys (split posts / reels / views rows)
META_FB_ROWS_2026 = {
    8: "contents",
    9: "posts_stories",
    10: "reels",
    11: "views",
    12: "viewers",
    13: "interactions",
    14: "link_clicks",
    15: "visits",
    16: "new_followers",
    17: "fb_followers",
}
META_IG_ROWS_2026 = {
    32: "contents",
    33: "posts_stories",
    34: "reels",
    35: "views",
    36: "reach",
    37: "interactions",
    38: "link_clicks",
    39: "visits",
    40: "new_followers",
    41: "ig_followers",
}

# 2024 Meta Five Journeys (legacy labels; W. Trubow rows skipped in runner)
META_FB_ROWS_2024 = {
    8: "contents",
    9: "posts_stories",
    10: "visits",
    11: "fb_followers",
    12: "viewers",
    13: "new_followers",
    14: "interactions",
}
META_IG_ROWS_2024 = {
    26: "contents",
    27: "reach",
    28: "visits",
    29: "ig_followers",
    30: "views",
    31: "new_followers",
    32: "interactions",
}

LAYOUT_2024 = TrackerLayout(
    version=2024,
    spreadsheet_id="1T95rA_WCjY2RH-hu8uReMiLflvkQsXOI13Iev0csKtg",
    sheet="Monthly Tracker",
    jan_column="H",
    website=WebsiteLayout(
        unique_pageviews=71,
        users=72,
        sessions=73,
        sessions_per_user=74,
        views_per_session=75,
        scrolled_users=None,
        avg_session_duration=76,
        bounce_rate=77,
        organic_search=79,
        direct=80,
        display=None,
        cross_network=None,
        email=81,
        referral=82,
        paid_social=83,
        organic_social=84,
        paid_search=85,
        paid_other=86,
        all_users=88,
        new_users=89,
        new_users_pct=90,
    ),
    blog=BlogLayout(
        published=93,
        pageviews=94,
        users=95,
        sessions=96,
        views_per_user=97,
        avg_session_duration=98,
        bounce_rate=99,
    ),
    youtube=YouTubeLayout(
        videos_published=44,
        new_subscribers=45,
        views=46,
        avg_watch_duration=47,
        engaged_views=None,
        likes=None,
        watch_hours=48,
        legacy=True,
    ),
    woo=WooLayout(
        home_views=132,
        home_users=133,
        checkout_views=134,
        checkout_users=135,
        avg_time=136,
        bounce=137,
        gross=138,
        items=139,
        orders=140,
        net=141,
        avg_net_order=142,
        items_per_order=143,
        orders_refunded=144,
        refunded_amount=145,
    ),
    ghl_boston={"Standard": 183, "Silver": 184, "Gold": 185, "Platinum": 186},
    ghl_newton={"Standard": 191, "Silver": 192, "Gold": 193, "Platinum": 194},
    ghl_boston_total=187,
    ghl_newton_total=195,
    ghl_boston_avg=188,
    ghl_newton_avg=196,
    meta_fj_supported=True,
    meta_wt_supported=False,
    fb_rows=META_FB_ROWS_2024,
    ig_rows=META_IG_ROWS_2024,
    youtube_views_key="engaged_views",
)

LAYOUT_2026 = TrackerLayout(
    version=2026,
    spreadsheet_id="1F7Lq0IBrOWolov5vEx5ztcBsZTbZCKfalQ1bwHuqakc",
    sheet="Monthly Tracker",
    jan_column="H",
    website=WebsiteLayout(
        unique_pageviews=83,
        users=84,
        sessions=85,
        sessions_per_user=86,
        views_per_session=87,
        scrolled_users=88,
        avg_session_duration=89,
        bounce_rate=90,
        organic_search=92,
        direct=93,
        display=94,
        cross_network=95,
        email=96,
        referral=97,
        paid_social=98,
        organic_social=99,
        paid_search=100,
        paid_other=101,
        all_users=None,
        new_users=None,
        new_users_pct=None,
    ),
    blog=BlogLayout(
        published=108,
        pageviews=109,
        users=110,
        sessions=111,
        views_per_user=112,
        avg_session_duration=113,
        bounce_rate=114,
    ),
    youtube=YouTubeLayout(
        videos_published=56,
        new_subscribers=60,
        views=57,
        avg_watch_duration=None,
        engaged_views=58,
        likes=59,
        watch_hours=None,
        legacy=False,
    ),
    woo=WooLayout(
        home_views=147,
        home_users=148,
        checkout_views=149,
        checkout_users=150,
        avg_time=151,
        bounce=152,
        gross=153,
        items=154,
        orders=155,
        net=156,
        avg_net_order=157,
        items_per_order=158,
        orders_refunded=159,
        refunded_amount=160,
    ),
    ghl_boston={"Standard": 185, "Silver": 186, "Gold": 187, "Platinum": 188},
    ghl_newton={"Standard": 193, "Silver": 194, "Gold": 195, "Platinum": 196},
    ghl_boston_total=189,
    ghl_newton_total=197,
    ghl_boston_avg=190,
    ghl_newton_avg=198,
    meta_fj_supported=True,
    meta_wt_supported=True,
    fb_rows=META_FB_ROWS_2026,
    ig_rows=META_IG_ROWS_2026,
)

LAYOUTS: dict[int, TrackerLayout] = {
    2024: LAYOUT_2024,
    2025: LAYOUT_2025,
    2026: LAYOUT_2026,
}
