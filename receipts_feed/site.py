"""Site routes: public website on top of the same ranking engine."""

import logging
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import config, db, timeutil
from .domains import is_platform_domain
from .business import is_business_relevant
from .sports import is_sports_relevant
from .weather import is_weather_relevant
from .cluster import build_clustered_edition, persist_clusters
from .docket import compact_dockets
from .hydrate import hydrate_posts, at_uri_to_web_url
from .marginalia import get_marginalia
from .tags import render_tags_html
from .watchlist import WATCHLIST

LOG = logging.getLogger("receipts.site")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


# --- Headline cleaning ---

_URL_FRAG_RE = re.compile(r'https?://\S{20,}')
_PROFILE_REF_RE = re.compile(r'bsky\.app/profile/\S+')
_RAW_DID_RE = re.compile(r'did:(plc|web):\S+')


def _clean_headline(text: str) -> str:
    """Strip raw URL fragments, platform junk, and embed artifacts from headline text."""
    cleaned = _URL_FRAG_RE.sub('', text)
    cleaned = _PROFILE_REF_RE.sub('', cleaned)
    cleaned = _RAW_DID_RE.sub('', cleaned)
    # Strip trailing "undefined" from broken embed metadata
    cleaned = re.sub(r'\bundefined\b', '', cleaned)
    # Strip hashtag fragments that look like metadata (#shorts etc)
    cleaned = re.sub(r'#\w+\s*$', '', cleaned)
    # Collapse whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _is_hero_eligible(item: dict) -> bool:
    """Determine if a post is worthy of the lead story slot."""
    headline = item.get("display_headline", "") or item.get("text", "")
    cleaned = _clean_headline(headline)

    if len(cleaned) < 20:
        return False
    if len(cleaned) < len(headline) * 0.4:
        return False
    if cleaned.startswith("- "):
        return False
    if _PROFILE_REF_RE.search(cleaned) or _RAW_DID_RE.search(cleaned):
        return False

    reasons = item.get("reasons", [])
    has_source = any(r.startswith("domain:") for r in reasons)
    has_substance = any(r.startswith("substance") for r in reasons)
    is_original = "original" in reasons

    if not (is_original or has_substance or has_source):
        return False

    # Prefer English for the hero slot
    # Check both the langs field and the actual text for non-Latin scripts
    langs = item.get("langs", [])
    if langs and "en" not in langs:
        return False
    # Detect non-Latin dominant text even when langs is unset
    headline_text = item.get("display_headline", "") or item.get("text", "")
    if headline_text:
        latin_chars = sum(1 for c in headline_text if c.isascii() and c.isalpha())
        total_alpha = sum(1 for c in headline_text if c.isalpha())
        if total_alpha > 10 and latin_chars < total_alpha * 0.5:
            return False

    return True


def _extract_display_domain(uri: str | None) -> str | None:
    """Extract domain for stats display, excluding platform self-references.

    Normalizes known subdomains so storage.courtlistener.com -> courtlistener.com
    in the stats, while keeping the full domain for link display.
    """
    if not uri:
        return None
    try:
        d = urlparse(uri).hostname or ""
        if d.startswith("www."):
            d = d[4:]
        if not d or is_platform_domain(d):
            return None
        # Normalize known subdomains for cleaner stats
        from .domains import PRIMARY_SOURCE_DOMAINS, REPORTING_DOMAINS
        for known in list(PRIMARY_SOURCE_DOMAINS) + list(REPORTING_DOMAINS):
            if d.endswith("." + known) or d == known:
                return known
        return d
    except Exception:
        return None


# --- Edition builder (used by the periodic freeze task) ---

def build_and_freeze_edition(limit: int = 30) -> str | None:
    """Build a fresh edition from clusters, hydrate lead posts, freeze to DB.

    Called periodically (every 15 min) by the edition clock in api.py.
    Returns the edition_id, or None if nothing to publish.
    """
    # Build clusters from ranked posts
    clusters = build_clustered_edition(limit=limit)
    if not clusters:
        return None

    # Persist cluster state for tracking across editions
    try:
        persist_clusters(clusters)
    except Exception:
        LOG.exception("failed to persist clusters (non-fatal)")

    # Hydrate lead posts from each cluster for display
    lead_uris = [c["lead_post_uri"] for c in clusters]
    hydrated = hydrate_posts(lead_uris)

    items = []
    for c in clusters:
        if len(items) >= limit:
            break
        h = hydrated.get(c["lead_post_uri"])
        if not h:
            continue
        raw_headline = h.get("display_headline", "") or h.get("text", "")
        h["display_headline"] = _clean_headline(raw_headline) or raw_headline
        items.append({
            **h,
            "score": c["cluster_score"],
            "reasons": c["lead_reasons"],
            # Cluster metadata for the template
            "cluster_type": c["cluster_type"],
            "cluster_id": c["cluster_id"],
            "post_count": c["post_count"],
            "unique_authors": c["unique_authors"],
            "cluster_state": c.get("state", "active"),
            "canonical_url": c.get("canonical_url"),
        })

    if not items:
        return None

    # Compact document floods into docket cards
    try:
        items = compact_dockets(items)
    except Exception:
        LOG.exception("docket compaction failed (non-fatal)")

    # Find hero: first hero-eligible item
    hero_idx = 0
    for i, item in enumerate(items):
        if _is_hero_eligible(item):
            hero_idx = i
            break

    # Compute stats
    domains = Counter()
    original_count = 0
    url_cluster_count = 0
    root_cluster_count = 0
    singleton_count = 0
    for item in items:
        d = _extract_display_domain(item.get("external_uri") or item.get("canonical_url"))
        if d:
            domains[d] += 1
        if "original" in item.get("reasons", []):
            original_count += 1
        ct = item.get("cluster_type", "singleton")
        if ct == "url":
            url_cluster_count += 1
        elif ct == "root":
            root_cluster_count += 1
        else:
            singleton_count += 1

    total = len(items) or 1
    stats = {
        "total": len(items),
        "original_pct": round(100 * original_count / total),
        "top_domains": domains.most_common(5),
        "url_clusters": url_cluster_count,
        "root_clusters": root_cluster_count,
        "singletons": singleton_count,
    }

    # Edition number
    conn = db.get_conn()
    row = conn.execute("SELECT COUNT(*) FROM editions WHERE feed_name = 'receipts'").fetchone()
    conn.close()
    edition_num = (row[0] if row else 0) + 1
    stats["edition_num"] = edition_num

    edition_id = db.save_edition("receipts", items, stats, hero_idx)
    LOG.info(
        "froze edition #%d (%s): %d items (%d url clusters, %d root clusters, %d singletons), hero=%d",
        edition_num, edition_id, len(items), url_cluster_count, root_cluster_count, singleton_count, hero_idx,
    )
    return edition_id


def _get_current_edition() -> dict:
    """Get the latest frozen edition, or build one live as fallback."""
    edition = db.get_latest_edition("receipts")
    if edition:
        return edition
    # Fallback: build live (cold start, no edition frozen yet)
    build_and_freeze_edition()
    edition = db.get_latest_edition("receipts")
    if edition:
        return edition
    return {"items": [], "stats": {}, "created_at": None, "hero_idx": 0}


def _truncate_word(text: str, limit: int) -> str:
    """Truncate text at a word boundary, no mid-word cuts."""
    if len(text) <= limit:
        return text
    # Find last space before the limit
    cut = text[:limit].rfind(" ")
    if cut < limit * 0.5:
        # No good break point — just use the limit
        cut = limit
    return text[:cut].rstrip() + "..."


def _relative_time(iso_str: str) -> str:
    """Convert ISO timestamp to relative time string."""
    try:
        dt = timeutil.to_utc_datetime(iso_str)
        now = timeutil.now_utc()
        diff = now - dt
        minutes = int(diff.total_seconds() / 60)
        if minutes < 1:
            return "just now"
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        return f"{days}d ago"
    except Exception:
        return ""


# --- DESK: house posts ---

def _build_desk(limit: int = 10) -> list[dict]:
    """Fetch recent posts from house accounts via public API."""
    from .graph import resolve_did

    # Resolve house handles to DIDs (cached in config)
    if not config.HOUSE_DIDS:
        for handle in config.HOUSE_HANDLES:
            try:
                did = resolve_did(handle)
                config.HOUSE_DIDS.append(did)
                LOG.info("resolved house handle %s -> %s", handle, did)
            except Exception:
                LOG.warning("failed to resolve house handle: %s", handle)

    if not config.HOUSE_DIDS:
        return []

    # Fetch recent posts from house accounts via getAuthorFeed
    import httpx
    from .hydrate import PUBLIC_API

    items = []
    for did in config.HOUSE_DIDS:
        try:
            resp = httpx.get(
                f"{PUBLIC_API}/xrpc/app.bsky.feed.getAuthorFeed",
                params={"actor": did, "limit": 10, "filter": "posts_no_replies"},
                timeout=15,
            )
            resp.raise_for_status()
            for entry in resp.json().get("feed", []):
                post = entry.get("post", {})
                record = post.get("record", {})
                author = post.get("author", {})
                embed = post.get("embed", {})

                text = record.get("text", "")
                # Skip very short / empty posts
                if len(text.strip()) < 20:
                    continue
                # Skip reposts
                if entry.get("reason"):
                    continue

                # Extract external link
                external_uri = None
                external_title = None
                if embed.get("$type") == "app.bsky.embed.external#view":
                    ext = embed.get("external", {})
                    external_uri = ext.get("uri")
                    external_title = ext.get("title")

                uri = post.get("uri", "")
                items.append({
                    "uri": uri,
                    "web_url": at_uri_to_web_url(uri),
                    "author_did": author.get("did", ""),
                    "author_handle": author.get("handle", ""),
                    "author_display_name": author.get("displayName", author.get("handle", "")),
                    "text": text,
                    "display_headline": _clean_headline(external_title or text) or text,
                    "created_at": record.get("createdAt", ""),
                    "external_uri": external_uri,
                    "external_title": external_title,
                    "like_count": post.get("likeCount", 0),
                    "reply_count": post.get("replyCount", 0),
                })
        except Exception:
            LOG.exception("failed to fetch house feed for %s", did)

    # Sort by recency, cap at limit
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return items[:limit]


# --- Routes ---

@router.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    edition = _get_current_edition()
    items = edition.get("items", [])
    hero_idx = edition.get("hero_idx", 0)
    hero = items[hero_idx] if items else None
    rest = [it for i, it in enumerate(items) if i != hero_idx][:29]
    return templates.TemplateResponse("index.html", {
        "request": request,
        "hero": hero,
        "posts": rest,
        "stats": edition.get("stats", {}),
        "updated_at": edition.get("created_at"),
        "relative_time": _relative_time,
        "trunc": _truncate_word,
        "tags": render_tags_html,
        "marginalia": get_marginalia(count=2),
    })


@router.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})


@router.get("/method", response_class=HTMLResponse)
async def method(request: Request):
    return templates.TemplateResponse("method.html", {"request": request})


@router.get("/feed", response_class=HTMLResponse)
async def feed_landing(request: Request):
    edition = _get_current_edition()
    items = edition.get("items", [])[:5]
    return templates.TemplateResponse("feed.html", {
        "request": request,
        "posts": items,
        "stats": edition.get("stats", {}),
        "updated_at": edition.get("created_at"),
        "relative_time": _relative_time,
        "trunc": _truncate_word,
        "tags": render_tags_html,
    })


@router.get("/desk", response_class=HTMLResponse)
async def desk(request: Request):
    items = _build_desk(limit=10)
    return templates.TemplateResponse("desk.html", {
        "request": request,
        "posts": items,
        "relative_time": _relative_time,
    })


# --- WATCH: must-see authors ---

# Cache resolved watchlist DIDs
_watchlist_dids: list[str] = []


def _build_watch(limit_per_author: int = 5) -> list[dict]:
    """Fetch recent posts from watchlist authors, grouped by author."""
    import httpx
    from .graph import resolve_did
    from .hydrate import PUBLIC_API

    if not WATCHLIST:
        return []

    # Resolve handles to DIDs once
    global _watchlist_dids
    if not _watchlist_dids:
        for handle_or_did in WATCHLIST:
            if handle_or_did.startswith("did:"):
                _watchlist_dids.append(handle_or_did)
            else:
                try:
                    did = resolve_did(handle_or_did)
                    _watchlist_dids.append(did)
                    LOG.info("resolved watchlist %s -> %s", handle_or_did, did)
                except Exception:
                    LOG.warning("failed to resolve watchlist: %s", handle_or_did)

    # Fetch per author
    authors = []
    for did in _watchlist_dids:
        try:
            resp = httpx.get(
                f"{PUBLIC_API}/xrpc/app.bsky.feed.getAuthorFeed",
                params={"actor": did, "limit": limit_per_author, "filter": "posts_no_replies"},
                timeout=15,
            )
            resp.raise_for_status()
            items = []
            for entry in resp.json().get("feed", []):
                post = entry.get("post", {})
                record = post.get("record", {})
                author = post.get("author", {})
                embed = post.get("embed", {})

                text = record.get("text", "")
                if entry.get("reason"):
                    continue
                if len(text.strip()) < 10:
                    continue

                external_uri = None
                external_title = None
                if embed.get("$type") == "app.bsky.embed.external#view":
                    ext = embed.get("external", {})
                    external_uri = ext.get("uri")
                    external_title = ext.get("title")

                uri = post.get("uri", "")
                items.append({
                    "uri": uri,
                    "web_url": at_uri_to_web_url(uri),
                    "author_did": author.get("did", ""),
                    "author_handle": author.get("handle", ""),
                    "author_display_name": author.get("displayName", author.get("handle", "")),
                    "text": text,
                    "display_headline": _clean_headline(external_title or text) or text,
                    "created_at": record.get("createdAt", ""),
                    "external_uri": external_uri,
                    "external_title": external_title,
                    "like_count": post.get("likeCount", 0),
                    "reply_count": post.get("replyCount", 0),
                })

            if items:
                authors.append({
                    "handle": items[0]["author_handle"],
                    "display_name": items[0]["author_display_name"],
                    "posts": items,
                })
        except Exception:
            LOG.exception("failed to fetch watchlist feed for %s", did)

    return authors


@router.get("/watch", response_class=HTMLResponse)
async def watch(request: Request):
    authors = _build_watch(limit_per_author=5)
    return templates.TemplateResponse("watch.html", {
        "request": request,
        "authors": authors,
        "relative_time": _relative_time,
    })


# --- BUSINESS: filtered lens ---

@router.get("/business", response_class=HTMLResponse)
async def business(request: Request):
    edition = _get_current_edition()
    items = edition.get("items", [])
    biz_items = [item for item in items if is_business_relevant(item)]
    return templates.TemplateResponse("business.html", {
        "request": request,
        "posts": biz_items,
        "stats": edition.get("stats", {}),
        "updated_at": edition.get("created_at"),
        "relative_time": _relative_time,
        "trunc": _truncate_word,
        "tags": render_tags_html,
    })


@router.get("/sports", response_class=HTMLResponse)
async def sports(request: Request):
    edition = _get_current_edition()
    items = edition.get("items", [])
    sports_items = [item for item in items if is_sports_relevant(item)]
    return templates.TemplateResponse("sports.html", {
        "request": request,
        "posts": sports_items,
        "stats": edition.get("stats", {}),
        "updated_at": edition.get("created_at"),
        "relative_time": _relative_time,
        "trunc": _truncate_word,
        "tags": render_tags_html,
    })


@router.get("/weather", response_class=HTMLResponse)
async def weather_page(request: Request):
    edition = _get_current_edition()
    items = edition.get("items", [])
    wx_items = [item for item in items if is_weather_relevant(item)]
    return templates.TemplateResponse("weather.html", {
        "request": request,
        "posts": wx_items,
        "stats": edition.get("stats", {}),
        "updated_at": edition.get("created_at"),
        "relative_time": _relative_time,
        "trunc": _truncate_word,
        "tags": render_tags_html,
    })
