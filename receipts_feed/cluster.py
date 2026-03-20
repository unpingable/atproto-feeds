"""Story clustering for homepage editions.

Clusters posts into stories by three methods (in precedence order):
1. Canonical external URL — multiple posts linking the same thing
2. Root-thread — reply trees / quote chains sharing a root
3. Singleton — unclustered posts become their own story

Feed stays post-level. Site homepage becomes cluster-level.
"""

import hashlib
import json
import logging
import math
import re
from collections import defaultdict
from urllib.parse import urlparse, urlencode, parse_qs

from . import db, timeutil
from .domains import domain_bonus, is_platform_domain

LOG = logging.getLogger("receipts.cluster")

# --- URL canonicalization ---

# Tracking params to strip
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "fbclid", "gclid", "gclsrc", "ref", "ref_src", "ref_url",
    "si", "feature", "mc_cid", "mc_eid",
}


def canonicalize_url(url: str) -> str:
    """Normalize a URL for clustering: strip tracking, fragments, normalize host."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        # Strip tracking params
        if parsed.query:
            params = parse_qs(parsed.query, keep_blank_values=False)
            cleaned = {k: v for k, v in params.items() if k.lower() not in _TRACKING_PARAMS}
            query = urlencode(cleaned, doseq=True) if cleaned else ""
        else:
            query = ""
        # Normalize mobile YouTube
        path = parsed.path
        if host in ("youtube.com", "m.youtube.com", "youtu.be"):
            host = "youtube.com"
            if host == "youtu.be" or path.startswith("/shorts/"):
                # Extract video ID
                vid = path.split("/")[-1] if "/" in path else path
                if parsed.query:
                    v_param = parse_qs(parsed.query).get("v", [])
                    if v_param:
                        vid = v_param[0]
                path = f"/watch"
                query = f"v={vid}" if vid else query
        # Rebuild without fragment
        scheme = parsed.scheme or "https"
        port = f":{parsed.port}" if parsed.port and parsed.port not in (80, 443) else ""
        canonical = f"{scheme}://{host}{port}{path}"
        if query:
            canonical += f"?{query}"
        return canonical
    except Exception:
        return url


def _url_key(url: str) -> str:
    """Generate a short hash key from a canonical URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


# Stopwords for title normalization
_TITLE_STOPWORDS = {"the", "a", "an", "of", "in", "on", "at", "to", "for", "is", "are", "was",
                     "and", "or", "but", "by", "with", "from", "that", "this", "has", "have"}


def _normalize_title(text: str) -> str:
    """Normalize text into a comparable title slug."""
    # Lowercase, strip URLs, strip punctuation
    text = text.lower()
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'www\.\S+', '', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    words = [w for w in text.split() if w not in _TITLE_STOPWORDS and len(w) > 2]
    return " ".join(words[:6])


def _domain_family(domain: str) -> str:
    """Map domain to a family for cross-domain clustering."""
    # Known reporting families that often cover the same story
    families = {
        "apnews.com": "wire",
        "reuters.com": "wire",
        "bbc.com": "wire", "bbc.co.uk": "wire",
        "congress.gov": "legislative",
        "courtlistener.com": "legal", "storage.courtlistener.com": "legal",
        "supremecourt.gov": "legal",
        "pubmed.ncbi.nlm.nih.gov": "research",
        "arxiv.org": "research",
        "nature.com": "research",
        "science.org": "research",
    }
    d = domain.lower()
    if d in families:
        return families[d]
    for fd, fam in families.items():
        if d.endswith("." + fd):
            return fam
    return d  # Use domain itself as family for unknowns


def _make_title_key(domain: str, text: str) -> str:
    """Create a cluster key from domain family + sorted significant words.

    Uses sorted word bag (not position-dependent) so different phrasings
    of the same story cluster together. Only clusters within the same
    domain family to avoid false matches across unrelated domains.
    """
    norm = _normalize_title(text)
    words = norm.split()
    if len(words) < 3:
        return ""  # Too short to reliably cluster
    # Use the 5 most significant words, sorted, as the key
    # This makes "Iran strikes Israel retaliation Damascus" match
    # "Iran retaliates against Israel following Damascus"
    key_words = sorted(words[:7])[:5]
    family = _domain_family(domain)
    return f"{family}:{' '.join(key_words)}"


# --- Cluster building ---

def build_clusters(ranked_posts: list[dict], post_details: dict[str, dict]) -> list[dict]:
    """Build story clusters from ranked posts.

    Args:
        ranked_posts: list of {"uri", "score", "reasons"} from ranked_posts table
        post_details: {uri: post_row_dict} from posts table

    Returns:
        list of cluster dicts, sorted by cluster_score descending
    """
    # Track which posts are claimed
    claimed = set()

    # Phase 1: URL clusters
    url_groups = defaultdict(list)
    for r in ranked_posts:
        post = post_details.get(r["uri"])
        if not post:
            continue
        ext_url = post.get("external_uri")
        if ext_url and not is_platform_domain(post.get("external_domain")):
            canonical = canonicalize_url(ext_url)
            if canonical:
                url_groups[canonical].append({**r, "_post": post})

    url_clusters = []
    for canonical_url, members in url_groups.items():
        if len(members) < 2:
            continue  # Need at least 2 posts to form a URL cluster
        uris = {m["uri"] for m in members}
        claimed.update(uris)
        cluster = _build_cluster(
            cluster_type="url",
            cluster_key=_url_key(canonical_url),
            members=members,
            canonical_url=canonical_url,
        )
        url_clusters.append(cluster)

    # Phase 2: Root-thread clusters
    root_groups = defaultdict(list)
    for r in ranked_posts:
        if r["uri"] in claimed:
            continue
        post = post_details.get(r["uri"])
        if not post:
            continue
        root = post.get("root_uri") or post.get("quote_uri")
        if root:
            root_groups[root].append({**r, "_post": post})

    root_clusters = []
    for root_uri, members in root_groups.items():
        if len(members) < 2:
            continue
        uris = {m["uri"] for m in members}
        claimed.update(uris)
        cluster = _build_cluster(
            cluster_type="root",
            cluster_key=hashlib.sha256(root_uri.encode()).hexdigest()[:16],
            members=members,
            root_uri=root_uri,
        )
        root_clusters.append(cluster)

    # Phase 2.5: Domain+title fallback clusters
    # Posts linking to the same domain family with similar headlines
    # (e.g. AP, Reuters, NYT all covering the same story with different URLs)
    title_groups = defaultdict(list)
    for r in ranked_posts:
        if r["uri"] in claimed:
            continue
        post = post_details.get(r["uri"])
        if not post:
            continue
        domain = post.get("external_domain", "")
        if not domain or is_platform_domain(domain):
            continue
        text = post.get("text", "")
        if len(text) < 30:
            continue
        # Normalize: domain family + first 5 significant words
        title_key = _make_title_key(domain, text)
        if title_key:
            title_groups[title_key].append({**r, "_post": post})

    title_clusters = []
    for key, members in title_groups.items():
        if len(members) < 2:
            continue
        uris = {m["uri"] for m in members}
        claimed.update(uris)
        cluster = _build_cluster(
            cluster_type="headline",
            cluster_key=hashlib.sha256(key.encode()).hexdigest()[:16],
            members=members,
            domain=members[0]["_post"].get("external_domain", ""),
        )
        title_clusters.append(cluster)

    # Phase 3: Singletons (everything unclaimed)
    singleton_clusters = []
    for r in ranked_posts:
        if r["uri"] in claimed:
            continue
        post = post_details.get(r["uri"])
        if not post:
            continue
        cluster = _build_cluster(
            cluster_type="singleton",
            cluster_key=hashlib.sha256(r["uri"].encode()).hexdigest()[:16],
            members=[{**r, "_post": post}],
        )
        singleton_clusters.append(cluster)

    # Combine and sort by cluster score
    all_clusters = url_clusters + root_clusters + title_clusters + singleton_clusters
    all_clusters.sort(key=lambda c: c["cluster_score"], reverse=True)

    return all_clusters


def _representative_sort_key(member: dict) -> tuple:
    """Sort key for choosing cluster representative.

    Prefers human curators over relays, even when relay text is longer.
    Returns a tuple for multi-level sort (higher = better).
    """
    reasons = member.get("reasons", [])
    post = member.get("_post", {})
    score = member.get("score", 0)

    # Tier 1: relationship (graph > outsider)
    is_graph = any(r in reasons for r in ("mutual", "followed", "follower", "trusted_list"))
    graph_tier = 2 if is_graph else 0

    # Tier 2: commentary quality (strip URLs, measure real text)
    text = post.get("text", "")
    # Inline URL stripping (avoid circular import)
    import re
    non_url = re.sub(r'https?://\S+|www\.\S+|\S+\.\w{2,4}/\S+', '', text)
    non_url = re.sub(r'\S+\.(com|org|gov|net|io|co|edu|news)/\S*', '', non_url).strip()
    commentary_tier = 1 if len(non_url) > 60 else 0

    # Tier 3: raw score as tiebreaker
    return (graph_tier, commentary_tier, score)


def _build_cluster(
    cluster_type: str,
    cluster_key: str,
    members: list[dict],
    canonical_url: str = None,
    root_uri: str = None,
    domain: str = None,
) -> dict:
    """Build a cluster dict from its members and compute scores."""
    # Sort members by representative preference, not just raw score.
    # Graph members with commentary beat outsider relays with longer text.
    members.sort(key=_representative_sort_key, reverse=True)
    lead = members[0]
    lead_post = lead["_post"]

    # Unique authors
    author_dids = {m["_post"]["author_did"] for m in members}

    # Domain from lead post (or passed-in for headline clusters)
    if not domain:
        domain = lead_post.get("external_domain") or ""
    if canonical_url:
        try:
            d = urlparse(canonical_url).hostname or ""
            if d.startswith("www."):
                d = d[4:]
            domain = d
        except Exception:
            pass

    # --- Cluster scoring ---
    lead_score = lead["score"]

    # Corroboration: multiple posts, capped quickly
    n = len(members)
    if n == 1:
        corroboration = 0.0
    elif n <= 3:
        corroboration = n * 1.5
    else:
        corroboration = 4.5 + math.log(n - 3 + 1) * 0.5  # diminishing

    # Author diversity
    author_count = len(author_dids)
    if author_count <= 1:
        diversity = 0.0
    else:
        diversity = min(author_count * 0.8, 3.0)

    # Source quality
    source_quality = domain_bonus(domain)

    # Single-author penalty
    if n >= 3 and author_count == 1:
        single_author_penalty = 3.0
    elif n >= 2 and author_count == 1:
        single_author_penalty = 1.5
    else:
        single_author_penalty = 0.0

    cluster_score = (
        lead_score * 0.45
        + corroboration * 0.15
        + diversity * 0.10
        + source_quality * 0.10
        - single_author_penalty
    )

    now = timeutil.now_utc().isoformat()

    # Build member list (without _post to keep it serializable)
    member_list = []
    for i, m in enumerate(members):
        member_list.append({
            "uri": m["uri"],
            "score": m["score"],
            "reasons": m["reasons"],
            "author_did": m["_post"]["author_did"],
            "is_lead": i == 0,
        })

    # Determine why lead was chosen
    lead_reasons_list = lead.get("reasons", [])
    lead_is_graph = any(r in lead_reasons_list for r in ("mutual", "followed", "follower", "trusted_list"))
    if n > 1 and lead_is_graph:
        suppressed_outsiders = sum(1 for m in members[1:] if "unknown_author" in m.get("reasons", []))
        if suppressed_outsiders:
            lead_reason = f"graph member chosen over {suppressed_outsiders} outsider(s)"
        else:
            lead_reason = "graph member with best commentary"
    elif n > 1:
        lead_reason = "best representative by score"
    else:
        lead_reason = "singleton"

    return {
        "cluster_id": f"{cluster_type}_{cluster_key}",
        "cluster_type": cluster_type,
        "cluster_key": cluster_key,
        "canonical_url": canonical_url,
        "root_uri": root_uri,
        "domain": domain,
        "first_seen_at": now,
        "last_seen_at": now,
        "state": "active",
        "lead_post_uri": lead["uri"],
        "lead_score": lead_score,
        "cluster_score": cluster_score,
        "post_count": n,
        "unique_authors": author_count,
        "members": member_list,
        "lead_reasons": lead["reasons"],
        "lead_reason": lead_reason,
    }


# --- Cluster-aware edition building ---

def build_clustered_edition(limit: int = 30) -> list[dict]:
    """Build clusters from current ranked posts.

    Returns cluster list sorted by cluster_score, ready for edition composition.
    """
    ranked = db.get_ranked_posts("receipts", limit=limit * 3)  # fetch generously
    if not ranked:
        return []

    # Fetch post details for all ranked URIs
    conn = db.get_conn()
    post_details = {}
    for r in ranked:
        row = conn.execute("SELECT * FROM posts WHERE uri = ?", (r["uri"],)).fetchone()
        if row:
            post_details[r["uri"]] = dict(row)
    conn.close()

    clusters = build_clusters(ranked, post_details)
    return clusters[:limit]


def persist_clusters(clusters: list[dict]):
    """Save clusters to DB for tracking state across editions."""
    conn = db.get_conn()
    now = timeutil.now_utc().isoformat()

    for c in clusters:
        # Check if cluster exists
        existing = conn.execute(
            "SELECT editions_present, first_seen_at FROM story_clusters WHERE cluster_id = ?",
            (c["cluster_id"],)
        ).fetchone()

        if existing:
            editions = existing[0] + 1
            first_seen = existing[1]
            # Update state based on persistence
            if editions >= 3:
                state = "persistent"
            else:
                state = "active"
            conn.execute(
                "UPDATE story_clusters SET last_seen_at=?, state=?, lead_post_uri=?, "
                "lead_score=?, cluster_score=?, post_count=?, unique_authors=?, "
                "editions_present=? WHERE cluster_id=?",
                (now, state, c["lead_post_uri"], c["lead_score"], c["cluster_score"],
                 c["post_count"], c["unique_authors"], editions, c["cluster_id"]),
            )
        else:
            conn.execute(
                "INSERT INTO story_clusters "
                "(cluster_id, cluster_type, cluster_key, canonical_url, root_uri, domain, "
                "title_norm, first_seen_at, last_seen_at, state, lead_post_uri, lead_score, "
                "cluster_score, post_count, unique_authors, editions_present) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (c["cluster_id"], c["cluster_type"], c["cluster_key"],
                 c.get("canonical_url"), c.get("root_uri"), c.get("domain"), None,
                 now, now, "emerging", c["lead_post_uri"], c["lead_score"],
                 c["cluster_score"], c["post_count"], c["unique_authors"], 1),
            )

        # Upsert members
        for m in c.get("members", []):
            conn.execute(
                "INSERT OR REPLACE INTO cluster_members "
                "(cluster_id, post_uri, author_did, post_score, joined_at, is_lead) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (c["cluster_id"], m["uri"], m["author_did"], m["score"], now, int(m["is_lead"])),
            )

    # Mark clusters not in this edition as fading
    current_ids = {c["cluster_id"] for c in clusters}
    all_active = conn.execute(
        "SELECT cluster_id FROM story_clusters WHERE state IN ('active', 'emerging', 'persistent')"
    ).fetchall()
    for row in all_active:
        if row[0] not in current_ids:
            conn.execute(
                "UPDATE story_clusters SET state='fading', last_seen_at=? WHERE cluster_id=?",
                (now, row[0]),
            )

    conn.commit()
    conn.close()
