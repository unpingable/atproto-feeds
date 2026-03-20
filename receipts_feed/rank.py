"""Receipts feed ranker: periodic scoring of candidate posts."""

import logging
import math
import re
from collections import defaultdict

from . import config, db, timeutil
from .author_weights import get_author_weight
from .domains import domain_bonus, is_platform_domain

LOG = logging.getLogger("receipts.rank")

_URL_RE = re.compile(r'https?://\S+|www\.\S+|\S+\.\w{2,4}/\S+')


def _strip_urls(text: str) -> str:
    """Remove URLs and bare domain paths from text to measure actual commentary."""
    stripped = _URL_RE.sub('', text)
    # Also strip remaining URL-ish fragments
    stripped = re.sub(r'\S+\.(com|org|gov|net|io|co|edu|news)/\S*', '', stripped)
    return stripped.strip()


def _flood_penalty(posts_24h: int) -> float:
    """Penalize authors who post excessively. Ramps up after 10 posts/day."""
    if posts_24h <= 10:
        return 0.0
    return min((posts_24h - 10) * 0.3, 5.0)


def _volume_dampener(posts_24h: int) -> float:
    """Diminishing returns multiplier for prolific authors.

    Separate from flood penalty: this applies even for "good" prolific
    posters. Their posts need to be proportionally better to compete.
    0-5 posts/day: 1.0 (no dampening)
    10 posts/day: 0.85
    20 posts/day: 0.7
    40 posts/day: 0.5
    """
    if posts_24h <= 5:
        return 1.0
    return max(0.5, 1.0 - (posts_24h - 5) * 0.015)


def _substance_bonus(text: str, link_count: int, facets_count: int) -> float:
    """Mild bonus for substantive posts. Not 'longer is better'."""
    score = 0.0
    length = len(text)
    # Sweet spot: 80-500 chars
    if 80 <= length <= 500:
        score += 1.0
    elif length > 500:
        score += 0.5
    # Multiple links
    if link_count >= 2:
        score += 0.5
    # Has structured content (facets = links, mentions, tags)
    if facets_count >= 2:
        score += 0.3
    return score


def _freshness_multiplier(created_at: str) -> float:
    """Time decay with ~12h half-life."""
    now = timeutil.now_utc()
    try:
        dt = timeutil.to_utc_datetime(created_at)
    except Exception:
        return 0.5
    age_hours = (now - dt).total_seconds() / 3600
    if age_hours < 0:
        age_hours = 0
    return math.exp(-0.058 * age_hours)  # half-life ~12h


def score_post(post: dict, author: dict | None) -> tuple[float, list[str]]:
    """Score a single post. Returns (score, reasons)."""
    score = 0.0
    reasons = []

    if author is None:
        # Unknown author, minimal score
        score += 0.5
        reasons.append("unknown_author")
    else:
        seed_class = author.get("seed_class", "")

        if seed_class == "mutual":
            score += 4.0
            reasons.append("mutual")
        elif seed_class == "followed":
            score += 2.0
            reasons.append("followed")
        elif seed_class == "trusted_list":
            score += 3.0
            reasons.append("trusted_list")
        elif seed_class == "follower":
            score += 1.0
            reasons.append("follower")

        trusted_score = author.get("trusted_score", 0)
        if trusted_score > 0:
            score += min(trusted_score * 0.2, 2.0)

        # Flood penalty
        posts_24h = author.get("posts_24h", 0)
        penalty = _flood_penalty(posts_24h)
        if penalty > 0:
            score -= penalty
            reasons.append(f"flood:-{penalty:.1f}")

    # Originality
    is_repost = post.get("is_repost", 0)
    reply_to = post.get("reply_to_uri")
    quote = post.get("quote_uri")

    if is_repost:
        score -= 6.0
        reasons.append("repost:-6")
    elif not reply_to and not quote:
        score += 3.0
        reasons.append("original")
    elif quote:
        score -= 1.0
        reasons.append("quote:-1")
    elif reply_to:
        # Replies get a small penalty unless substantive
        text = post.get("text", "")
        if len(text) > 100:
            score += 0.5
            reasons.append("substantive_reply")
        else:
            score -= 1.0
            reasons.append("short_reply:-1")

    # Evidence
    ext_domain = post.get("external_domain")
    has_embed = post.get("has_external_embed", 0)
    platform_link = is_platform_domain(ext_domain)
    if has_embed and not platform_link:
        score += 2.0
        reasons.append("has_link")

    d_bonus = domain_bonus(ext_domain)
    if d_bonus > 0:
        score += d_bonus
        reasons.append(f"domain:{ext_domain}:+{d_bonus}")

    # Relay/exhaust penalty: posts that are just {title} + {url} with minimal commentary
    # These look source-shaped but add no editorial value
    text = post.get("text", "")
    if has_embed and not platform_link:
        non_url_text = _strip_urls(text)
        if len(non_url_text) < 20:
            # Nearly pure link dump — heavy penalty
            score -= 3.0
            reasons.append("relay:-3")
        elif len(non_url_text) < 60 and not reply_to and not quote:
            # Short text + link, no conversation — mild penalty
            score -= 1.5
            reasons.append("low_commentary:-1.5")

    # Image-only stub penalty: image post with minimal text and no real link
    has_image = post.get("has_image", 0) or post.get("has_video", 0)
    if has_image and len(post.get("text", "")) < 30 and not has_embed:
        score -= 1.5
        reasons.append("image_stub:-1.5")

    # Substance
    text = post.get("text", "")
    link_count = post.get("link_count", 0)
    facets_count = post.get("facets_count", 0)
    sub_bonus = _substance_bonus(text, link_count, facets_count)
    if sub_bonus > 0:
        score += sub_bonus
        reasons.append(f"substance:+{sub_bonus:.1f}")

    # Freshness
    created_at = post.get("created_at", "")
    freshness = _freshness_multiplier(created_at)
    score *= freshness
    if freshness < 0.5:
        reasons.append(f"stale:{freshness:.2f}")

    # Volume dampener (separate from flood penalty)
    if author is not None:
        posts_24h = author.get("posts_24h", 0)
        vol_mult = _volume_dampener(posts_24h)
        if vol_mult < 1.0:
            score *= vol_mult
            reasons.append(f"volume:{vol_mult:.2f}")

    # Per-author weight (editorial taste)
    if author is not None:
        handle = author.get("handle", "")
        did = author.get("did", "")
        weight = get_author_weight(handle=handle, did=did)
        if weight != 1.0:
            score *= weight
            reasons.append(f"weight:{weight:.1f}")

    return score, reasons


def _apply_composition_rules(scored: list[dict], page_size: int) -> list[dict]:
    """Apply per-page composition rules to prevent feed monotony."""
    result = []
    author_count = defaultdict(int)
    root_count = defaultdict(int)
    seen_links = defaultdict(int)

    for item in scored:
        if len(result) >= page_size:
            break

        post = item["post"]
        author = post.get("author_did", "")
        root = post.get("root_uri") or post.get("uri")
        link = post.get("external_uri") or ""

        # Max posts per author
        if author_count[author] >= config.MAX_POSTS_PER_AUTHOR_PER_PAGE:
            continue

        # Max posts per thread
        if root_count[root] >= config.MAX_POSTS_PER_THREAD_PER_PAGE:
            continue

        # Dedupe repeated links (keep best 2)
        if link and seen_links[link] >= 2:
            continue

        result.append(item)
        author_count[author] += 1
        root_count[root] += 1
        if link:
            seen_links[link] += 1

    return result


def run_rank():
    """Score all recent posts and materialize ranked_posts table."""
    LOG.info("starting rank pass")

    posts = db.get_recent_posts(hours=config.MAX_FEED_AGE_HOURS)
    if not posts:
        LOG.info("no posts to rank")
        return

    # Update author post counts
    db.update_author_post_counts()

    # Load exclusions
    excluded = db.get_excluded_dids()

    # Score each post
    scored = []
    for post in posts:
        if post["author_did"] in excluded:
            continue
        author = db.get_author(post["author_did"])
        score, reasons = score_post(post, author)
        scored.append({
            "uri": post["uri"],
            "score": score,
            "reasons": reasons,
            "post": post,
        })

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Apply composition rules for a generous buffer
    composed = _apply_composition_rules(scored, page_size=config.FEED_PAGE_SIZE * 5)

    # Save to DB
    ranked = [{"uri": item["uri"], "score": item["score"], "reasons": item["reasons"]} for item in composed]
    db.save_ranked_posts("receipts", ranked)

    LOG.info("ranked %d posts (from %d candidates)", len(ranked), len(posts))

    # Purge stale posts
    db.purge_old_posts(hours=config.MAX_FEED_AGE_HOURS * 2)
