"""Feed-side cluster dedup and light docket suppression.

Applied to getFeedSkeleton output. The feed stays live and flat,
just less repetitive. Not the full site compositor — just enough
to stop the same AP article from appearing 5 times.

Rules:
- Per canonical URL: show max 1 post (the highest-scored)
- Per document family (congress, research, filings): max 2 posts
- Everything else passes through unchanged
"""

import logging
from collections import defaultdict

from . import db
from .cluster import canonicalize_url
from .docket import _classify_family
from .domains import is_platform_domain

LOG = logging.getLogger("receipts.feed_dedup")

# Max posts per canonical URL in the feed
MAX_PER_URL = 1

# Max posts per document family in the feed
MAX_PER_FAMILY = 2


def dedup_feed(ranked: list[dict], limit: int) -> list[dict]:
    """Apply cluster dedup and light docket suppression to feed output.

    Takes ranked posts (from DB), returns filtered list with
    URL dedup and document family caps applied.

    Each item in ranked has: {"uri", "score", "reasons"}
    We need to look up post details for URL/family classification.
    """
    if not ranked:
        return ranked

    # Fetch post details for URL/family classification
    conn = db.get_conn()
    post_cache = {}
    for r in ranked:
        row = conn.execute(
            "SELECT external_uri, external_domain, author_did, text FROM posts WHERE uri = ?",
            (r["uri"],),
        ).fetchone()
        if row:
            post_cache[r["uri"]] = {
                "external_uri": row[0],
                "external_domain": row[1],
                "author_did": row[2],
                "text": row[3] or "",
            }
    conn.close()

    # Track seen URLs and family counts
    seen_urls = defaultdict(int)  # canonical_url -> count
    family_counts = defaultdict(int)  # family_name -> count
    result = []

    for r in ranked:
        if len(result) >= limit:
            break

        post = post_cache.get(r["uri"])
        if not post:
            result.append(r)
            continue

        # URL dedup
        ext_url = post.get("external_uri")
        if ext_url and not is_platform_domain(post.get("external_domain")):
            canonical = canonicalize_url(ext_url)
            if canonical and seen_urls[canonical] >= MAX_PER_URL:
                continue  # Skip duplicate URL
            if canonical:
                seen_urls[canonical] += 1

        # Light docket suppression
        # Build a minimal item dict for family classification
        item_for_classify = {
            "external_uri": post.get("external_uri"),
            "reasons": r.get("reasons", []),
            "author_handle": "",  # not needed for domain-based classification
        }
        family = _classify_family(item_for_classify)
        if family:
            if family_counts[family] >= MAX_PER_FAMILY:
                continue  # Skip excess document family posts
            family_counts[family] += 1

        result.append(r)

    return result
