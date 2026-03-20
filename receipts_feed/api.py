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
from .feed_dedup import dedup_feed
from .graph import refresh_graph
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
_graph_task: asyncio.Task | None = None

DM_CHECK_INTERVAL = 300  # 5 minutes
GRAPH_REFRESH_INTERVAL = 86400  # 24 hours


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


async def _periodic_graph_refresh():
    """Background task to refresh seed graph daily."""
    loop = asyncio.get_event_loop()
    await asyncio.sleep(300)  # Wait 5 min after startup
    while True:
        try:
            result = await loop.run_in_executor(None, refresh_graph)
            LOG.info("graph refresh complete: %s", result)
        except Exception:
            LOG.exception("graph refresh failed")
        await asyncio.sleep(GRAPH_REFRESH_INTERVAL)


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

    _graph_task = asyncio.create_task(_periodic_graph_refresh())
    LOG.info("started graph refresh (interval=%ds)", GRAPH_REFRESH_INTERVAL)


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
    if _graph_task:
        _graph_task.cancel()


# -- Feed skeleton endpoint --

FEED_URIS = {
    "receipts": "live",       # Live post-level ranking with dedup
    "edition": "edition",     # Frozen edition — cluster-level, cleaner
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

    feed_mode = FEED_URIS[feed_name]

    if feed_mode == "edition":
        # Edition feed: serve from frozen edition (cluster-level, cleaner)
        edition = db.get_latest_edition("receipts")
        if not edition:
            return {"cursor": None, "feed": []}
        items = edition.get("items", [])
        # Filter out docket cards (not real posts)
        post_items = [item for item in items if not item.get("is_docket")]
        # Apply cursor (index-based for edition)
        start = 0
        if cursor:
            try:
                start = int(cursor)
            except ValueError:
                pass
        page = post_items[start:start + limit]
        feed_items = [{"post": item["uri"]} for item in page if item.get("uri")]
        next_cursor = str(start + limit) if start + limit < len(post_items) else None
        return {"cursor": next_cursor, "feed": feed_items}

    # Live feed: post-level ranking with dedup
    cursor_score = None
    if cursor:
        try:
            cursor_score = float(cursor)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": "BadCursor", "message": "Invalid cursor"},
            )

    # Fetch extra to account for dedup filtering
    ranked = db.get_ranked_posts("receipts", limit=limit * 2, cursor_score=cursor_score)

    # Apply cluster dedup + light docket suppression
    ranked = dedup_feed(ranked, limit=limit)

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
    stale_count = conn.execute("SELECT COUNT(*) FROM authors WHERE seed_class='stale'").fetchone()[0]
    ranked_count = conn.execute("SELECT COUNT(*) FROM ranked_posts").fetchone()[0]
    edition_count = conn.execute("SELECT COUNT(*) FROM editions").fetchone()[0]
    exclusion_count = conn.execute("SELECT COUNT(*) FROM exclusions WHERE state='excluded'").fetchone()[0]
    conn.close()
    latest = db.get_latest_edition("receipts")
    last_graph = db.get_state("last_graph_refresh")
    return {
        "posts": post_count,
        "authors": author_count,
        "authors_stale": stale_count,
        "exclusions": exclusion_count,
        "ranked": ranked_count,
        "editions": edition_count,
        "latest_edition": latest["edition_id"] if latest else None,
        "latest_edition_at": latest["created_at"] if latest else None,
        "last_graph_refresh": last_graph,
        "consumer_events": _consumer._event_count if _consumer else 0,
    }


@app.post("/debug/refresh-graph")
async def debug_refresh_graph():
    """Debug endpoint: trigger manual graph refresh."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, refresh_graph)
    return {"status": "ok", "result": result}


@app.get("/debug/ranking")
async def debug_ranking(limit: int = Query(default=30)):
    """Debug endpoint: full rank introspection with penalties and account stats."""
    from .rank import score_post, _strip_urls
    conn = db.get_conn()
    ranked = db.get_ranked_posts("receipts", limit=limit)
    items = []
    for r in ranked:
        post_row = conn.execute("SELECT * FROM posts WHERE uri = ?", (r["uri"],)).fetchone()
        if not post_row:
            continue
        post = dict(post_row)
        author = db.get_author(post["author_did"])
        non_url_text = _strip_urls(post.get("text", ""))
        items.append({
            "uri": r["uri"],
            "score": r["score"],
            "reasons": r["reasons"],
            "author_handle": author.get("handle", "") if author else "?",
            "author_stink": round(author.get("stink_score", 0), 3) if author else 0,
            "author_link_ratio": round(author.get("link_post_ratio", 0), 2) if author else 0,
            "author_reply_ratio": round(author.get("reply_ratio", 0), 2) if author else 0,
            "author_seed_class": author.get("seed_class", "") if author else "",
            "non_url_text_len": len(non_url_text),
            "external_domain": post.get("external_domain", ""),
            "text_preview": post.get("text", "")[:80],
        })
    conn.close()
    return {"count": len(items), "items": items}


@app.get("/debug/stinky")
async def debug_stinky(limit: int = Query(default=20)):
    """Debug endpoint: accounts with highest stink scores."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT did, handle, seed_class, stink_score, link_post_ratio, reply_ratio, "
        "avg_non_url_len, posts_24h FROM authors WHERE stink_score > 0.3 "
        "ORDER BY stink_score DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return {
        "count": len(rows),
        "accounts": [
            {
                "handle": r[1], "seed_class": r[2],
                "stink": round(r[3], 3), "link_ratio": round(r[4], 2),
                "reply_ratio": round(r[5], 2), "avg_non_url_len": round(r[6], 1),
                "posts_24h": r[7],
            }
            for r in rows
        ],
    }
