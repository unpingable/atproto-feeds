"""Jetstream-based ATProto event consumer for Receipts feed.

Connects to Bluesky Jetstream (JSON over WebSocket), filters to seed graph
authors + allowlisted domain posts, persists candidates to SQLite.
"""

import asyncio
import json
import logging
from typing import Optional
from urllib.parse import urlparse

import websockets

from . import config, db, timeutil
from .domains import domain_bonus

LOG = logging.getLogger("receipts.ingest")

WANTED_COLLECTIONS = ["app.bsky.feed.post", "app.bsky.feed.repost"]


def _build_ws_url(base_url: str, cursor: Optional[str] = None) -> str:
    params = []
    for col in WANTED_COLLECTIONS:
        params.append(f"wantedCollections={col}")
    if cursor:
        params.append(f"cursor={cursor}")
    if params:
        sep = "&" if "?" in base_url else "?"
        return base_url + sep + "&".join(params)
    return base_url


def _extract_domain(uri: str | None) -> str | None:
    if not uri:
        return None
    try:
        parsed = urlparse(uri)
        host = parsed.hostname or ""
        if host.startswith("www."):
            host = host[4:]
        return host.lower() if host else None
    except Exception:
        return None


def _parse_post(js: dict) -> Optional[dict]:
    """Transform a Jetstream commit into a post record dict, or None."""
    if js.get("kind") != "commit":
        return None

    commit = js.get("commit", {})
    operation = commit.get("operation")
    did = js.get("did", "")
    collection = commit.get("collection", "")
    rkey = commit.get("rkey", "")
    cid = commit.get("cid", "")
    record = commit.get("record", {})
    uri = f"at://{did}/{collection}/{rkey}"

    time_us = js.get("time_us")
    if time_us:
        created_at = timeutil.to_utc_iso(time_us / 1_000_000)
    else:
        created_at = timeutil.now_utc().isoformat()

    if operation == "delete":
        return {"_op": "delete", "uri": uri}

    if operation not in ("create", "update"):
        return None

    if collection == "app.bsky.feed.repost":
        return {
            "_op": "create",
            "uri": uri,
            "cid": cid,
            "author_did": did,
            "created_at": record.get("createdAt", created_at),
            "text": "",
            "is_repost": True,
        }

    if collection != "app.bsky.feed.post":
        return None

    # Extract reply pointers
    reply = record.get("reply", {})
    reply_parent = reply.get("parent", {}) if reply else {}
    reply_root = reply.get("root", {}) if reply else {}

    # Extract external link
    external_uri = None
    embed = record.get("embed", {})
    has_image = False
    has_video = False
    if embed:
        embed_type = embed.get("$type", "")
        ext = embed.get("external", {})
        if ext and ext.get("uri"):
            external_uri = ext["uri"]
        media = embed.get("media", {})
        if media:
            ext2 = media.get("external", {})
            if ext2 and ext2.get("uri"):
                external_uri = external_uri or ext2["uri"]
        if "image" in embed_type:
            has_image = True
        if "video" in embed_type:
            has_video = True

    # Extract quote-post URI
    quote_uri = None
    if embed:
        quote_record = embed.get("record", {})
        if isinstance(quote_record, dict) and quote_record.get("uri"):
            quote_uri = quote_record["uri"]

    external_domain = _extract_domain(external_uri)

    # Count links from facets
    facets = record.get("facets", [])
    link_count = sum(
        1 for f in facets
        for feat in f.get("features", [])
        if feat.get("$type") == "app.bsky.richtext.facet#link"
    )
    if external_uri and link_count == 0:
        link_count = 1

    langs = ",".join(record.get("langs", []))

    return {
        "_op": "create",
        "uri": uri,
        "cid": cid,
        "author_did": did,
        "created_at": record.get("createdAt", created_at),
        "text": record.get("text", ""),
        "reply_to_uri": reply_parent.get("uri"),
        "root_uri": reply_root.get("uri"),
        "quote_uri": quote_uri,
        "external_uri": external_uri,
        "external_domain": external_domain,
        "has_external_embed": bool(external_uri),
        "has_image": has_image,
        "has_video": has_video,
        "is_repost": False,
        "langs": langs,
        "link_count": link_count,
        "facets_count": len(facets),
    }


class JetstreamConsumer:
    def __init__(self):
        self.ws_url = config.JETSTREAM_URL
        self._stop = False
        self._event_count = 0
        self._events_dropped = 0
        self._last_cursor: Optional[str] = None
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
        self._seed_dids: set[str] = set()

    def _refresh_seed_dids(self):
        self._seed_dids = db.get_seed_dids()
        LOG.info("seed graph loaded: %d authors", len(self._seed_dids))

    def _should_keep(self, post: dict) -> bool:
        """Inclusion gate: keep if author is in seed graph or domain is valuable."""
        if post.get("_op") == "delete":
            return True
        author = post.get("author_did", "")
        if author in self._seed_dids:
            return True
        # Allow posts with high-value domain links even from outside graph
        if domain_bonus(post.get("external_domain")) >= 2.0:
            return True
        return False

    def _process_event(self, post: dict):
        if post.get("_op") == "delete":
            db.delete_post(post["uri"])
            return
        db.insert_post(post)

    async def _drain_queue(self):
        loop = asyncio.get_event_loop()
        last_stats = loop.time()
        last_graph_refresh = loop.time()

        while not self._stop:
            try:
                ev = await asyncio.wait_for(self._event_queue.get(), timeout=10.0)
            except asyncio.TimeoutError:
                ev = None
            except asyncio.CancelledError:
                break

            if ev is not None:
                try:
                    await loop.run_in_executor(None, self._process_event, ev)
                except Exception:
                    LOG.exception("failed to process event")

                self._event_count += 1

                if self._event_count % config.CURSOR_SAVE_INTERVAL == 0:
                    if self._last_cursor:
                        await loop.run_in_executor(None, db.upsert_cursor, config.CONSUMER_NAME, self._last_cursor)

            now = loop.time()

            # Refresh seed graph every 10 minutes
            if now - last_graph_refresh >= 600:
                last_graph_refresh = now
                try:
                    await loop.run_in_executor(None, self._refresh_seed_dids)
                except Exception:
                    LOG.exception("failed to refresh seed graph")

            # Stats every 60s
            if now - last_stats >= 60:
                last_stats = now
                dropped = self._events_dropped
                self._events_dropped = 0
                backlog = self._event_queue.qsize()
                LOG.info(
                    "STATS events=%d backlog=%d dropped=%d seed_authors=%d",
                    self._event_count, backlog, dropped, len(self._seed_dids),
                )

    async def run(self):
        db.init_db()
        self._refresh_seed_dids()

        if not self._seed_dids:
            LOG.warning("seed graph is empty — run bootstrap_graph first")

        saved_cursor = db.get_cursor(config.CONSUMER_NAME)
        LOG.info("starting Jetstream consumer, cursor=%s", saved_cursor)

        drain_task = asyncio.ensure_future(self._drain_queue())

        while not self._stop:
            try:
                url = _build_ws_url(self.ws_url, cursor=self._last_cursor or saved_cursor)
                async with websockets.connect(
                    url,
                    max_size=10 * 1024 * 1024,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=10,
                ) as ws:
                    LOG.info("connected to Jetstream")
                    async for msg in ws:
                        try:
                            js = json.loads(msg)
                        except Exception:
                            continue

                        time_us = js.get("time_us")
                        if time_us:
                            self._last_cursor = str(time_us)

                        post = _parse_post(js)
                        if post is None:
                            continue

                        if not self._should_keep(post):
                            continue

                        try:
                            self._event_queue.put_nowait(post)
                        except asyncio.QueueFull:
                            self._events_dropped += 1

            except asyncio.CancelledError:
                break
            except Exception:
                LOG.exception("Jetstream connection error, reconnecting in 5s")
                await asyncio.sleep(5)

        drain_task.cancel()
        if self._last_cursor:
            db.upsert_cursor(config.CONSUMER_NAME, self._last_cursor)
            LOG.info("saved cursor on shutdown: %s", self._last_cursor)

    def stop(self):
        self._stop = True
