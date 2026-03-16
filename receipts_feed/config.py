import os

FEED_PUBLISHER_HANDLE = os.getenv("FEED_PUBLISHER_HANDLE", "instantinternet.news")
FEED_PUBLISHER_PASSWORD = os.getenv("FEED_PUBLISHER_PASSWORD", "")

TRUST_GRAPH_HANDLE = os.getenv("TRUST_GRAPH_HANDLE", "neutral.zone")

BSKY_SERVICE = os.getenv("BSKY_SERVICE", "https://bsky.social")

JETSTREAM_URL = os.getenv("JETSTREAM_URL", "wss://jetstream2.us-east.bsky.network/subscribe")

FEED_SERVICE_HOSTNAME = os.getenv("FEED_SERVICE_HOSTNAME", "")
FEED_SERVICE_PORT = int(os.getenv("FEED_SERVICE_PORT", "8100"))

RANK_INTERVAL_SECONDS = int(os.getenv("RANK_INTERVAL_SECONDS", "120"))
MAX_FEED_AGE_HOURS = int(os.getenv("MAX_FEED_AGE_HOURS", "24"))
MAX_POSTS_PER_AUTHOR_PER_PAGE = int(os.getenv("MAX_POSTS_PER_AUTHOR_PER_PAGE", "2"))
MAX_POSTS_PER_THREAD_PER_PAGE = int(os.getenv("MAX_POSTS_PER_THREAD_PER_PAGE", "3"))
FEED_PAGE_SIZE = int(os.getenv("FEED_PAGE_SIZE", "30"))

CONSUMER_NAME = os.getenv("CONSUMER_NAME", "receipts_consumer")
CURSOR_SAVE_INTERVAL = int(os.getenv("CURSOR_SAVE_INTERVAL", "500"))

EDITION_INTERVAL_SECONDS = int(os.getenv("EDITION_INTERVAL_SECONDS", "900"))  # 15 min

# House accounts — for the DESK page, not the main ranking
HOUSE_DIDS: list[str] = []  # populated at startup from handles below
HOUSE_HANDLES: list[str] = [
    h.strip() for h in
    os.getenv("HOUSE_HANDLES", "neutral.zone,labelwatch.neutral.zone,instantinternet.news").split(",")
    if h.strip()
]
