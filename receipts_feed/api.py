"""FastAPI app exposing getFeedSkeleton, site, and health endpoints."""

import asyncio
import logging

from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, db
from .ingest import JetstreamConsumer
from .rank import run_rank
from .dm_listener import check_dms
from .site import router as site_router, build_and_freeze_edition

LOG = logging.getLogger("receipts.api")

app = FastAPI(title="Receipts Feed Generator")

# Mount site routes (/, /about, /method, /feed, /desk, /watch)
app.include_router(site_router)

# Static files
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_consumer: JetstreamConsumer | None = None
_consumer_task: asyncio.Task | None = None
_rank_task: asyncio.Task | None = None
_edition_task: asyncio.Task | None = None
_dm_task: asyncio.Task | None = None

DM_CHECK_INTERVAL = 300  # 5 minutes


async def _periodic_rank():
    """Background task to run ranking on an interval."""
    loop = asyncio.get_event_loop()
    while True:
        try:
            await loop.run_in_executor(None, run_rank)
        except Exception:
            LOG.exception("rank pass failed")
        await asyncio.sleep(config.RANK_INTERVAL_SECONDS)


async def _periodic_dm_check():
    """Background task to check DMs for opt-out/opt-in commands."""
    loop = asyncio.get_event_loop()
    await asyncio.sleep(60)  # Wait for startup
    while True:
        try:
            await loop.run_in_executor(None, check_dms)
        except Exception:
            LOG.exception("DM check failed")
        await asyncio.sleep(DM_CHECK_INTERVAL)


async def _periodic_edition():
    """Background task to freeze site editions on an interval."""
    loop = asyncio.get_event_loop()
    # Wait a bit for first rank pass to complete
    await asyncio.sleep(30)
    while True:
        try:
            await loop.run_in_executor(None, build_and_freeze_edition)
        except Exception:
            LOG.exception("edition freeze failed")
        await asyncio.sleep(config.EDITION_INTERVAL_SECONDS)


@app.on_event("startup")
async def startup():
    db.init_db()

    global _consumer, _consumer_task, _rank_task, _edition_task

    _consumer = JetstreamConsumer()
    _consumer_task = asyncio.create_task(_consumer.run())
    LOG.info("started Jetstream consumer")

    _rank_task = asyncio.create_task(_periodic_rank())
    LOG.info("started periodic ranker (interval=%ds)", config.RANK_INTERVAL_SECONDS)

    _edition_task = asyncio.create_task(_periodic_edition())
    LOG.info("started periodic edition freeze (interval=%ds)", config.EDITION_INTERVAL_SECONDS)

    _dm_task = asyncio.create_task(_periodic_dm_check())
    LOG.info("started DM listener (interval=%ds)", DM_CHECK_INTERVAL)


@app.on_event("shutdown")
async def shutdown():
    if _consumer:
        _consumer.stop()
    if _consumer_task:
        _consumer_task.cancel()
    if _rank_task:
        _rank_task.cancel()
    if _edition_task:
        _edition_task.cancel()
    if _dm_task:
        _dm_task.cancel()


# -- Feed skeleton endpoint --

FEED_URIS = {
    "receipts": True,
}


@app.get("/xrpc/app.bsky.feed.getFeedSkeleton")
async def get_feed_skeleton(
    feed: str = Query(...),
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = Query(default=None),
):
    feed_name = feed.rsplit("/", 1)[-1] if "/" in feed else feed

    if feed_name not in FEED_URIS:
        return JSONResponse(
            status_code=400,
            content={"error": "UnknownFeed", "message": f"Unknown feed: {feed_name}"},
        )

    cursor_score = None
    if cursor:
        try:
            cursor_score = float(cursor)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": "BadCursor", "message": "Invalid cursor"},
            )

    ranked = db.get_ranked_posts(feed_name, limit=limit, cursor_score=cursor_score)
    feed_items = [{"post": item["uri"]} for item in ranked]

    next_cursor = None
    if ranked and len(ranked) == limit:
        next_cursor = str(ranked[-1]["score"])

    return {"cursor": next_cursor, "feed": feed_items}


# -- DID document for feed service --

@app.get("/.well-known/did.json")
async def did_json():
    hostname = config.FEED_SERVICE_HOSTNAME
    if not hostname:
        return JSONResponse(
            status_code=500,
            content={"error": "FEED_SERVICE_HOSTNAME not configured"},
        )
    service_did = f"did:web:{hostname}"
    return {
        "@context": ["https://www.w3.org/ns/did/v1"],
        "id": service_did,
        "service": [
            {
                "id": "#bsky_fg",
                "type": "BskyFeedGenerator",
                "serviceEndpoint": f"https://{hostname}",
            }
        ],
    }


# -- Health / debug --

@app.get("/robots.txt")
async def robots_txt():
    robots_path = STATIC_DIR / "robots.txt"
    if robots_path.exists():
        return FileResponse(str(robots_path), media_type="text/plain")
    return Response("User-agent: *\nAllow: /\n", media_type="text/plain")


@app.get("/favicon.ico")
async def favicon_ico():
    # Serve SVG favicon for .ico requests too (browsers handle it)
    svg_path = STATIC_DIR / "favicon.svg"
    if svg_path.exists():
        return FileResponse(str(svg_path), media_type="image/svg+xml")
    return Response(status_code=204)


@app.get("/favicon.svg")
async def favicon_svg():
    svg_path = STATIC_DIR / "favicon.svg"
    if svg_path.exists():
        return FileResponse(str(svg_path), media_type="image/svg+xml")
    return Response(status_code=204)


@app.get("/sitemap.xml")
async def sitemap():
    now = db.timeutil.now_utc().strftime("%Y-%m-%d")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://instantinternet.news/</loc><changefreq>hourly</changefreq><priority>1.0</priority><lastmod>{now}</lastmod></url>
  <url><loc>https://instantinternet.news/about</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>
  <url><loc>https://instantinternet.news/method</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>
  <url><loc>https://instantinternet.news/feed</loc><changefreq>weekly</changefreq><priority>0.7</priority></url>
  <url><loc>https://instantinternet.news/desk</loc><changefreq>hourly</changefreq><priority>0.6</priority></url>
  <url><loc>https://instantinternet.news/watch</loc><changefreq>hourly</changefreq><priority>0.5</priority></url>
</urlset>"""
    return Response(content=xml, media_type="application/xml")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/debug/top")
async def debug_top(feed: str = Query(default="receipts"), limit: int = Query(default=20)):
    """Debug endpoint: show top ranked posts with reasons."""
    ranked = db.get_ranked_posts(feed, limit=limit)
    return {"feed": feed, "count": len(ranked), "posts": ranked}


@app.get("/debug/stats")
async def debug_stats():
    """Debug endpoint: basic stats."""
    conn = db.get_conn()
    post_count = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    author_count = conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
    ranked_count = conn.execute("SELECT COUNT(*) FROM ranked_posts").fetchone()[0]
    edition_count = conn.execute("SELECT COUNT(*) FROM editions").fetchone()[0]
    conn.close()
    latest = db.get_latest_edition("receipts")
    return {
        "posts": post_count,
        "authors": author_count,
        "ranked": ranked_count,
        "editions": edition_count,
        "latest_edition": latest["edition_id"] if latest else None,
        "latest_edition_at": latest["created_at"] if latest else None,
        "consumer_events": _consumer._event_count if _consumer else 0,
    }
