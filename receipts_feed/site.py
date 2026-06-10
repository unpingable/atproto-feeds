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


def _edition_character(
    total: int, url_clusters: int, root_clusters: int, dockets: int,
    graph_count: int, outsider_count: int, original_pct: int,
    domains: Counter,
) -> str:
    """Generate a one-line edition character summary.

    Deterministic, based on composition. Not trying to be clever —
    just a dry factual read of what kind of edition this is.
    """
    if total == 0:
        return "Empty edition"

    traits = []

    # Dominant composition type
    if url_clusters >= total * 0.3:
        traits.append("cluster-heavy")
    if dockets >= 2:
        traits.append("document-heavy")
    if outsider_count > graph_count:
        traits.append("outsider-led")
    elif graph_count > outsider_count * 2:
        traits.append("graph-heavy")

    # Domain character
    top = domains.most_common(3)
    if top:
        top_domain, top_count = top[0]
        if top_count >= 4:
            traits.append(f"{top_domain}-dominated")
        elif len(top) >= 3 and sum(c for _, c in top[:3]) >= total * 0.5:
            traits.append("source-diverse")

    # Fallback
    if not traits:
        if original_pct >= 90:
            traits.append("high-originality")
        else:
            traits.append("mixed")

    # Build summary
    summary = ", ".join(traits[:2])
    return summary.capitalize() if summary else "Standard edition"


# --- Edition builder (used by the periodic freeze task) ---

def build_and_freeze_edition(limit: int = 30) -> str | None:
    """Build a fresh edition from clusters, hydrate lead posts, freeze to DB.

    Called periodically (every 15 min) by the edition clock in api.py.
    Returns the edition_id, or None if nothing to publish.
    """
    # Build clusters — fetch extra to ensure graph posts aren't crowded out
    clusters = build_clustered_edition(limit=limit + 10)
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

    # Source-first pivot (2026-06-10): stories ARE URLs. For each cluster
    # with a canonical_url, look up the cached url_metadata so the
    # headline becomes the linked content's og:title, not the lead post's
    # text. The post text becomes the attributed dek/quote.
    from . import source_class as _sc
    canonical_urls = [
        c.get("canonical_url") for c in clusters if c.get("canonical_url")
    ]
    url_meta_by_url = db.get_url_metadata_batch(canonical_urls) if canonical_urls else {}

    items = []
    for c in clusters:
        if len(items) >= limit:
            break
        h = hydrated.get(c["lead_post_uri"])
        if not h:
            continue

        canonical = c.get("canonical_url")
        url_meta = url_meta_by_url.get(canonical) if canonical else None

        if url_meta and url_meta.get("og_title"):
            # URL is the story. og_title is the headline; lead-post text
            # becomes the dek/quote. The post's own display_headline
            # (Bluesky's link card title) is preserved for diagnostic
            # comparison but no longer drives display.
            url_headline = _clean_headline(url_meta["og_title"]) or url_meta["og_title"]
            h["display_headline"] = url_headline
            dek_quote = _clean_headline(h.get("text", "")) or h.get("text", "")
            source_domain = url_meta.get("domain") or ""
            source_class = url_meta.get("source_class") or _sc.SOURCE_CLASS_UNKNOWN
            headline_basis = "og_title"
            og_description = url_meta.get("og_description") or ""
        elif canonical:
            # Canonical URL exists but OG fetch hasn't completed yet (or
            # failed). Fall back to the post's hydrated headline but mark
            # the basis so we can see in the data when OG is missing.
            raw_headline = h.get("display_headline", "") or h.get("text", "")
            h["display_headline"] = _clean_headline(raw_headline) or raw_headline
            dek_quote = ""
            # Source domain: prefer canonical-derived (always present;
            # always lowercase; no `www.`) over the post's stored
            # external_domain (which may be missing or formatted
            # inconsistently). Falls back to external_domain when canonical
            # parse fails for some reason.
            from .urls import extract_domain as _extract_domain
            source_domain = (
                _extract_domain(canonical) or h.get("external_domain") or ""
            )
            source_class = _sc.classify_domain(source_domain)
            headline_basis = "post_fallback"
            og_description = ""
        else:
            # No canonical URL — graph note. Post text IS the object,
            # and should be rendered as a quote/note rather than dressed
            # up as a headline.
            raw_headline = h.get("display_headline", "") or h.get("text", "")
            h["display_headline"] = _clean_headline(raw_headline) or raw_headline
            dek_quote = ""
            source_domain = ""
            source_class = _sc.SOURCE_CLASS_GRAPH_NOTE
            headline_basis = "graph_note"
            og_description = ""

        # Display URL: prefer the canonical (utm-stripped) form so the byline
        # link doesn't carry tracking junk. Falls back to whatever the post
        # had stored. The template still reads `external_uri` for the
        # link href + display text — overwriting on the item copy is the
        # smallest change here; the source data on the post row is
        # untouched.
        display_url = canonical or h.get("external_uri") or ""
        if display_url:
            h["external_uri"] = display_url

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
            # Source-first fields (chatty 2026-06-10)
            "dek_quote": dek_quote,
            "source_domain": source_domain,
            "source_class": source_class,
            "section_label": _sc.section_for(source_class),
            "headline_basis": headline_basis,
            "og_description": og_description,
            "display_url": display_url,
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
    original_pct = round(100 * original_count / total)

    # Count graph vs outsider
    graph_count = sum(1 for item in items if any(
        r in item.get("reasons", []) for r in ("mutual", "followed", "follower", "trusted_list")
    ))
    outsider_count = sum(1 for item in items if "unknown_author" in item.get("reasons", []))
    docket_count = sum(1 for item in items if item.get("is_docket"))

    # Generate edition character summary
    character = _edition_character(
        total, url_cluster_count, root_cluster_count, docket_count,
        graph_count, outsider_count, original_pct, domains,
    )

    stats = {
        "total": total,
        "original_pct": original_pct,
        "top_domains": domains.most_common(5),
        "url_clusters": url_cluster_count,
        "root_clusters": root_cluster_count,
        "singletons": singleton_count,
        "character": character,
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


def _compute_edition_diff() -> dict:
    """Compare current and previous editions. Returns {new, fading, gone} lists."""
    current = db.get_latest_edition("receipts")
    previous = db.get_previous_edition("receipts")
    if not current or not previous:
        return {"new": [], "fading": [], "count_new": 0, "count_gone": 0}

    curr_uris = {item.get("uri", "") for item in current.get("items", [])}
    prev_uris = {item.get("uri", "") for item in previous.get("items", [])}

    # Build lookup by URI for display
    curr_by_uri = {item.get("uri", ""): item for item in current.get("items", [])}
    prev_by_uri = {item.get("uri", ""): item for item in previous.get("items", [])}

    new_uris = curr_uris - prev_uris
    gone_uris = prev_uris - curr_uris

    new_items = []
    for uri in new_uris:
        item = curr_by_uri.get(uri, {})
        headline = item.get("display_headline", "") or item.get("text", "")
        if headline:
            new_items.append({"headline": _truncate_word(headline, 60), "web_url": item.get("web_url", "")})

    fading_items = []
    for uri in gone_uris:
        item = prev_by_uri.get(uri, {})
        headline = item.get("display_headline", "") or item.get("text", "")
        if headline:
            fading_items.append({"headline": _truncate_word(headline, 60), "web_url": item.get("web_url", "")})

    return {
        "new": new_items[:5],
        "fading": fading_items[:3],
        "count_new": len(new_uris),
        "count_gone": len(gone_uris),
    }


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
    """Truncate text at a sentence boundary when possible, otherwise word boundary."""
    if len(text) <= limit:
        return text
    # Try to find a sentence boundary (. ! ?) within the limit window
    # Look in the back half of the limit to avoid cutting too short
    window = text[:limit]
    best_sentence_end = -1
    for i in range(len(window) - 1, max(int(limit * 0.4), 20), -1):
        if window[i] in '.!?' and (i + 1 >= len(window) or window[i + 1] in ' "\'\n)'):
            best_sentence_end = i + 1
            break
    if best_sentence_end > 0:
        return text[:best_sentence_end].rstrip()
    # No good sentence break — fall back to word boundary
    cut = window.rfind(" ")
    if cut < limit * 0.4:
        cut = limit
    return text[:cut].rstrip() + "..."


def _excerpt_differs(headline: str, text: str) -> bool:
    """Check if showing an excerpt would add meaningfully different content.

    Only returns True when the headline came from a different source
    (e.g. embed title) and the post text adds real context. Does NOT
    return True just because the text is longer than the headline —
    that just creates duplicate-looking content.
    """
    if not text or not headline:
        return False
    # Normalize for comparison
    h_clean = headline.strip().rstrip(".!?…")
    t_clean = text.strip()
    # If the text starts with the headline (headline is a truncation of text),
    # showing an excerpt just duplicates. Skip.
    if t_clean.startswith(h_clean[:30]):
        return False
    # If headline starts with the text (text is short), no excerpt needed
    if h_clean.startswith(t_clean[:30]):
        return False
    # Headlines came from different sources — show the excerpt
    return True


# --- Section grouping for the wire-desk homepage ---

# Order in which sections appear on the homepage. Filing + regulation
# share a top slot ("Filings & Regulation") because the wire-desk reads
# them together. Wire goes into the right-rail ticker (short, fast,
# monospace), not the main flow. Unknown / overflow lands below the fold.
_MAIN_SECTION_ORDER: list[tuple[str, list[str]]] = [
    ("Filings & Regulation", ["filing", "regulation"]),
    ("Papers",               ["paper"]),
    ("Reporting",            ["reporting"]),
    ("From the Graph",       ["graph_note"]),
    ("Code & Standards",     ["code"]),
]

# Source classes that go into the right-rail ticker (short, headline-bar
# style). Wire copy is for the rail.
_WIRE_CLASSES = {"wire", "wire_blip"}


def _group_items_into_sections(items: list[dict]) -> dict:
    """Split a flat item list into the wire-desk section structure.

    Returns:
      {
        "main": [
          {"label": "Filings & Regulation", "items": [...]},
          {"label": "Papers", "items": [...]},
          ...
        ],
        "wire": [...short headline items for the rail ticker...],
        "below_fold": [...overflow / unknown class...],
      }

    Items keep their original dict shape; only the grouping changes.
    """
    by_class: dict[str, list[dict]] = {}
    for item in items:
        cls = item.get("source_class") or "unknown"
        by_class.setdefault(cls, []).append(item)

    main: list[dict] = []
    for label, classes in _MAIN_SECTION_ORDER:
        merged: list[dict] = []
        for cls in classes:
            merged.extend(by_class.get(cls, []))
        if merged:
            # Field name is `stories` (not `items`) because Jinja resolves
            # `section.items` to the dict's `.items()` method, not the key.
            main.append({"label": label, "stories": merged})

    wire: list[dict] = []
    for cls in _WIRE_CLASSES:
        wire.extend(by_class.get(cls, []))

    # Anything left (unknown + any class not in the ordered set) goes
    # below the fold. Sorted by score desc so the best survivors lead.
    handled = set()
    for _, classes in _MAIN_SECTION_ORDER:
        handled.update(classes)
    handled.update(_WIRE_CLASSES)
    below: list[dict] = []
    for cls, vals in by_class.items():
        if cls in handled:
            continue
        below.extend(vals)
    below.sort(key=lambda x: x.get("score", 0.0), reverse=True)

    return {
        "main": main,
        "wire": wire,
        "below_fold": below,
    }


def _short_wire_text(item: dict, max_chars: int = 64) -> str:
    """One-line wire-ticker text for the rail.

    Prefers source-class-stamp shape:
      `<DOMAIN> · <headline truncated>`
    Falls back to display_headline / text when the domain is missing.
    """
    parts: list[str] = []
    dom = item.get("source_domain") or item.get("external_domain") or ""
    headline = (
        item.get("display_headline")
        or item.get("dek_quote")
        or item.get("text")
        or ""
    )
    if dom:
        parts.append(dom)
    parts.append(_truncate_word(headline.replace("\n", " ").strip(), max_chars))
    return " · ".join(p for p in parts if p)


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

    # Stamp a relative-time `age` string on every item so the template can
    # render the byline without a Jinja filter call.
    for it in items:
        it["age"] = _relative_time(it.get("created_at") or "")

    # Wire-desk grouping. The first item of the first main section
    # carries `.lead` styling automatically via the template.
    groups = _group_items_into_sections(items)
    wire_ticker = [
        {
            "text": _short_wire_text(it, max_chars=70),
            "web_url": it.get("web_url", ""),
            "display_url": it.get("display_url") or it.get("external_uri") or "",
            "source_domain": it.get("source_domain") or "",
        }
        for it in groups["wire"][:8]
    ]
    below_fold = [
        {
            "headline": _truncate_word(
                it.get("display_headline") or it.get("text", ""), 80
            ),
            "web_url": it.get("web_url", ""),
            "display_url": it.get("display_url") or it.get("external_uri") or "",
            "author_handle": it.get("author_handle", ""),
            "age": it.get("age", ""),
            "source_domain": it.get("source_domain") or "",
            "source_class": it.get("source_class") or "unknown",
        }
        for it in groups["below_fold"][:12]
    ]

    return templates.TemplateResponse("index.html", {
        "request": request,
        # Legacy fields kept for backward compat with the old template
        # (and so the archive / edition_detail pages — which share these —
        # don't break). The new wire-desk template reads `sections`.
        "hero": items[hero_idx] if items else None,
        "posts": [it for i, it in enumerate(items) if i != hero_idx][:29],
        "stats": edition.get("stats", {}),
        "updated_at": edition.get("created_at"),
        "relative_time": _relative_time,
        "trunc": _truncate_word,
        "tags": render_tags_html,
        "excerpt_ok": _excerpt_differs,
        "marginalia": get_marginalia(count=2),
        "diff": _compute_edition_diff(),
        # New wire-desk fields
        "sections": groups["main"],
        "wire_ticker": wire_ticker,
        "below_fold": below_fold,
    })


@router.get("/archive", response_class=HTMLResponse)
async def archive(request: Request):
    editions = db.get_recent_editions("receipts", limit=20)
    # Build summaries for each edition
    summaries = []
    for ed in editions:
        stats = ed.get("stats", {})
        items = ed.get("items", [])
        hero_idx = ed.get("hero_idx", 0)
        hero = items[hero_idx] if items and hero_idx < len(items) else None
        hero_headline = ""
        if hero:
            h = hero.get("display_headline", "") or hero.get("text", "")
            hero_headline = _truncate_word(h, 80)
        # Top domains
        top_domains = stats.get("top_domains", [])[:3]
        summaries.append({
            "edition_id": ed["edition_id"],
            "created_at": ed["created_at"],
            "edition_num": stats.get("edition_num", "?"),
            "total": stats.get("total", 0),
            "original_pct": stats.get("original_pct", 0),
            "url_clusters": stats.get("url_clusters", 0),
            "hero_headline": hero_headline,
            "top_domains": top_domains,
            "character": stats.get("character", ""),
        })
    return templates.TemplateResponse("archive.html", {
        "request": request,
        "editions": summaries,
        "relative_time": _relative_time,
    })


@router.get("/edition/{edition_id}", response_class=HTMLResponse)
async def edition_detail(request: Request, edition_id: str):
    """View a specific frozen edition."""
    edition = db.get_edition_by_id(edition_id)
    if not edition:
        return templates.TemplateResponse("edition_detail.html", {
            "request": request, "edition": None, "hero": None, "posts": [],
            "stats": {}, "relative_time": _relative_time,
            "trunc": _truncate_word, "tags": render_tags_html,
            "excerpt_ok": _excerpt_differs,
        })
    items = edition.get("items", [])
    hero_idx = edition.get("hero_idx", 0)
    hero = items[hero_idx] if items and hero_idx < len(items) else None
    rest = [it for i, it in enumerate(items) if i != hero_idx]
    return templates.TemplateResponse("edition_detail.html", {
        "request": request,
        "edition": edition,
        "hero": hero,
        "posts": rest,
        "stats": edition.get("stats", {}),
        "updated_at": edition.get("created_at"),
        "relative_time": _relative_time,
        "trunc": _truncate_word,
        "tags": render_tags_html,
        "excerpt_ok": _excerpt_differs,
    })


@router.get("/story/{cluster_id}", response_class=HTMLResponse)
async def story_page(request: Request, cluster_id: str):
    """Cluster detail page: show all member posts for a story."""
    conn = db.get_conn()
    cluster_row = conn.execute(
        "SELECT * FROM story_clusters WHERE cluster_id = ?", (cluster_id,)
    ).fetchone()
    if not cluster_row:
        conn.close()
        return templates.TemplateResponse("story.html", {
            "request": request,
            "cluster": None,
            "members": [],
            "relative_time": _relative_time,
            "trunc": _truncate_word,
            "tags": render_tags_html,
        })
    cluster = dict(cluster_row)

    # Get member posts
    member_rows = conn.execute(
        "SELECT post_uri, author_did, post_score, is_lead FROM cluster_members "
        "WHERE cluster_id = ? ORDER BY post_score DESC",
        (cluster_id,),
    ).fetchall()
    conn.close()

    # Hydrate member posts
    member_uris = [r[0] for r in member_rows]
    hydrated = hydrate_posts(member_uris)

    members = []
    for row in member_rows:
        h = hydrated.get(row[0])
        if not h:
            continue
        raw_headline = h.get("display_headline", "") or h.get("text", "")
        h["display_headline"] = _clean_headline(raw_headline) or raw_headline
        members.append({
            **h,
            "score": row[2],
            "is_lead": row[3],
        })

    return templates.TemplateResponse("story.html", {
        "request": request,
        "cluster": cluster,
        "members": members,
        "relative_time": _relative_time,
        "trunc": _truncate_word,
        "tags": render_tags_html,
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
