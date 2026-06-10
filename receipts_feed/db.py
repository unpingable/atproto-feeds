import json
import os
import pathlib
import sqlite3
from typing import Optional

from . import timeutil

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "receipts.sqlite"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    conn = get_conn()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS authors (
            did TEXT PRIMARY KEY,
            handle TEXT,
            seed_class TEXT,
            trusted_score REAL DEFAULT 0,
            posts_24h INTEGER DEFAULT 0,
            updated_at TEXT,
            stink_score REAL DEFAULT 0,
            link_post_ratio REAL DEFAULT 0,
            reply_ratio REAL DEFAULT 0,
            avg_non_url_len REAL DEFAULT 0
        )
    """)
    # Migration for existing DBs
    for col, typedef in [
        ("stink_score", "REAL DEFAULT 0"),
        ("link_post_ratio", "REAL DEFAULT 0"),
        ("reply_ratio", "REAL DEFAULT 0"),
        ("avg_non_url_len", "REAL DEFAULT 0"),
    ]:
        try:
            conn.execute(f"ALTER TABLE authors ADD COLUMN {col} {typedef}")
        except Exception:
            pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            uri TEXT PRIMARY KEY,
            cid TEXT,
            author_did TEXT,
            created_at TEXT,
            text TEXT,
            reply_to_uri TEXT,
            root_uri TEXT,
            quote_uri TEXT,
            external_uri TEXT,
            external_domain TEXT,
            external_title TEXT,
            external_description TEXT,
            has_external_embed INTEGER DEFAULT 0,
            has_image INTEGER DEFAULT 0,
            has_video INTEGER DEFAULT 0,
            is_repost INTEGER DEFAULT 0,
            langs TEXT,
            link_count INTEGER DEFAULT 0,
            facets_count INTEGER DEFAULT 0,
            indexed_at TEXT
        )
    """)

    # Migration for existing post DBs (idempotent)
    for col, typedef in [
        ("external_title", "TEXT"),
        ("external_description", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE posts ADD COLUMN {col} {typedef}")
        except Exception:
            pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ranked_posts (
            feed_name TEXT,
            uri TEXT,
            score REAL,
            reasons_json TEXT,
            ranked_at TEXT,
            PRIMARY KEY (feed_name, uri)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cursors (
            consumer TEXT PRIMARY KEY,
            cursor TEXT,
            updated_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS feed_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS editions (
            edition_id TEXT PRIMARY KEY,
            feed_name TEXT,
            created_at TEXT,
            items_json TEXT,
            stats_json TEXT,
            hero_idx INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS story_clusters (
            cluster_id TEXT PRIMARY KEY,
            cluster_type TEXT,
            cluster_key TEXT,
            canonical_url TEXT,
            root_uri TEXT,
            domain TEXT,
            title_norm TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            state TEXT DEFAULT 'active',
            lead_post_uri TEXT,
            lead_score REAL DEFAULT 0,
            cluster_score REAL DEFAULT 0,
            post_count INTEGER DEFAULT 0,
            unique_authors INTEGER DEFAULT 0,
            editions_present INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cluster_members (
            cluster_id TEXT,
            post_uri TEXT,
            author_did TEXT,
            post_score REAL DEFAULT 0,
            joined_at TEXT,
            is_lead INTEGER DEFAULT 0,
            PRIMARY KEY (cluster_id, post_uri)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS exclusions (
            did TEXT PRIMARY KEY,
            requested_at TEXT,
            source TEXT DEFAULT 'dm',
            state TEXT DEFAULT 'excluded',
            note TEXT
        )
    """)

    # url_metadata — cached OG / source metadata per canonical URL.
    # Source-first pivot (2026-06-10): the story is the URL, not the post.
    # The site does NOT mirror Bluesky posts; this table caches public
    # source metadata for linked URLs only — title, description, image,
    # domain, content-type. See about/method for the doctrine line.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS url_metadata (
            canonical_url TEXT PRIMARY KEY,
            final_url TEXT,
            domain TEXT,
            content_type TEXT,
            og_title TEXT,
            og_description TEXT,
            og_image TEXT,
            fetch_status INTEGER,
            fetch_error TEXT,
            fetched_at TEXT,
            source_class TEXT
        )
    """)

    # Indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_clusters_type ON story_clusters(cluster_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_clusters_key ON story_clusters(cluster_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_clusters_state ON story_clusters(state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_clusters_score ON story_clusters(cluster_score DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cluster_members_post ON cluster_members(post_uri)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_editions_feed ON editions(feed_name, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_author ON posts(author_did)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_domain ON posts(external_domain)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ranked_score ON ranked_posts(feed_name, score DESC)")
    # Compound index for the rank pass's outsider-relay aggregate
    # (`SELECT author_did, external_domain, COUNT(*) GROUP BY ...`). Keeps
    # the pre-aggregation cheap so we can replace the N+1 in score_post
    # with a single dict lookup.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_author_domain "
        "ON posts(author_did, external_domain)"
    )
    # Index for the OG fetcher's "needs-fetch" query (find canonical_urls
    # referenced by recent posts that don't have a row yet, ordered by
    # last-attempted-at so failures back off).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_url_metadata_fetched "
        "ON url_metadata(fetched_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_url_metadata_status "
        "ON url_metadata(fetch_status)"
    )

    conn.commit()
    conn.close()


def upsert_cursor(consumer: str, cursor: Optional[str]):
    conn = get_conn()
    now = timeutil.now_utc().isoformat()
    conn.execute(
        "INSERT INTO cursors (consumer, cursor, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(consumer) DO UPDATE SET cursor=excluded.cursor, updated_at=excluded.updated_at",
        (consumer, cursor or "", now),
    )
    conn.commit()
    conn.close()


def get_cursor(consumer: str) -> Optional[str]:
    conn = get_conn()
    row = conn.execute("SELECT cursor FROM cursors WHERE consumer = ?", (consumer,)).fetchone()
    conn.close()
    if not row:
        return None
    return row[0] or None


def upsert_author(did: str, handle: str, seed_class: str, trusted_score: float = 0):
    conn = get_conn()
    now = timeutil.now_utc().isoformat()
    conn.execute(
        "INSERT INTO authors (did, handle, seed_class, trusted_score, updated_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(did) DO UPDATE SET handle=excluded.handle, seed_class=excluded.seed_class, "
        "trusted_score=excluded.trusted_score, updated_at=excluded.updated_at",
        (did, handle, seed_class, trusted_score, now),
    )
    conn.commit()
    conn.close()


def get_seed_dids() -> set[str]:
    conn = get_conn()
    rows = conn.execute("SELECT did FROM authors").fetchall()
    conn.close()
    return {r[0] for r in rows}


def get_author(did: str) -> Optional[dict]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM authors WHERE did = ?", (did,)).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def get_all_authors_by_did() -> dict[str, dict]:
    """Batch alternative to N×get_author() in the rank score loop.

    Single SELECT returning every author keyed by DID. The whole authors
    table is small (~4k rows on the live DB; payload <1 MB), so this is
    cheaper than the N+1 connection-open pattern even for ranking
    a few thousand candidates.

    May 30 2026 incident: per-candidate get_author() opened a fresh
    connection (and PRAGMA-set busy_timeout=5000) for every post; under
    drain-task write contention the connection setup queued behind the
    writer lock, blowing up score_loop from ~3s to 75s. See
    rank.run_rank()'s RANK PROFILE log for the after-numbers.
    """
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM authors").fetchall()
    finally:
        conn.close()
    return {row["did"]: dict(row) for row in rows}


def get_outsider_domain_counts() -> dict[tuple[str, str], int]:
    """Pre-aggregate `(author_did, external_domain) -> count` for the
    outsider-relay check inside score_post.

    Replaces an in-score N+1 (`SELECT COUNT(*) FROM posts WHERE author_did
    = ? AND external_domain = ?` per outsider candidate). Backed by the
    compound index idx_posts_author_domain.

    Empty external_domain entries (no embed) are skipped — the score
    function only consults this map when has_external_embed is true.
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT author_did, external_domain, COUNT(*) "
            "FROM posts "
            "WHERE external_domain IS NOT NULL AND external_domain != '' "
            "GROUP BY author_did, external_domain"
        ).fetchall()
    finally:
        conn.close()
    return {(r[0], r[1]): r[2] for r in rows}


def insert_post(post: dict):
    conn = get_conn()
    now = timeutil.now_utc().isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO posts "
        "(uri, cid, author_did, created_at, text, reply_to_uri, root_uri, quote_uri, "
        "external_uri, external_domain, external_title, external_description, "
        "has_external_embed, has_image, has_video, "
        "is_repost, langs, link_count, facets_count, indexed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            post["uri"],
            post.get("cid", ""),
            post["author_did"],
            post["created_at"],
            post.get("text", ""),
            post.get("reply_to_uri"),
            post.get("root_uri"),
            post.get("quote_uri"),
            post.get("external_uri"),
            post.get("external_domain"),
            post.get("external_title"),
            post.get("external_description"),
            int(post.get("has_external_embed", False)),
            int(post.get("has_image", False)),
            int(post.get("has_video", False)),
            int(post.get("is_repost", False)),
            post.get("langs", ""),
            post.get("link_count", 0),
            post.get("facets_count", 0),
            now,
        ),
    )
    conn.commit()
    conn.close()


def delete_post(uri: str):
    conn = get_conn()
    conn.execute("DELETE FROM posts WHERE uri = ?", (uri,))
    conn.commit()
    conn.close()


def get_recent_posts(hours: int = 24) -> list[dict]:
    conn = get_conn()
    cutoff = (timeutil.now_utc() - __import__("datetime").timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT * FROM posts WHERE created_at >= ? ORDER BY created_at DESC",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_ranked_posts(feed_name: str, ranked: list[dict]):
    conn = get_conn()
    now = timeutil.now_utc().isoformat()
    conn.execute("DELETE FROM ranked_posts WHERE feed_name = ?", (feed_name,))
    for item in ranked:
        conn.execute(
            "INSERT INTO ranked_posts (feed_name, uri, score, reasons_json, ranked_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (feed_name, item["uri"], item["score"], json.dumps(item.get("reasons", [])), now),
        )
    conn.commit()
    conn.close()


def get_ranked_posts(feed_name: str, limit: int = 30, cursor_score: Optional[float] = None) -> list[dict]:
    conn = get_conn()
    if cursor_score is not None:
        rows = conn.execute(
            "SELECT uri, score, reasons_json FROM ranked_posts "
            "WHERE feed_name = ? AND score < ? ORDER BY score DESC LIMIT ?",
            (feed_name, cursor_score, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT uri, score, reasons_json FROM ranked_posts "
            "WHERE feed_name = ? ORDER BY score DESC LIMIT ?",
            (feed_name, limit),
        ).fetchall()
    conn.close()
    return [{"uri": r[0], "score": r[1], "reasons": json.loads(r[2])} for r in rows]


def update_author_post_counts():
    conn = get_conn()
    cutoff = (timeutil.now_utc() - __import__("datetime").timedelta(hours=24)).isoformat()
    conn.execute(
        "UPDATE authors SET posts_24h = ("
        "  SELECT COUNT(*) FROM posts WHERE posts.author_did = authors.did AND posts.created_at >= ?"
        ")",
        (cutoff,),
    )
    conn.commit()
    conn.close()


def set_state(key: str, value: str):
    conn = get_conn()
    now = timeutil.now_utc().isoformat()
    conn.execute(
        "INSERT INTO feed_state (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, now),
    )
    conn.commit()
    conn.close()


def get_state(key: str) -> Optional[str]:
    conn = get_conn()
    row = conn.execute("SELECT value FROM feed_state WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else None


def get_edition_by_id(edition_id: str) -> Optional[dict]:
    conn = get_conn()
    row = conn.execute(
        "SELECT edition_id, created_at, items_json, stats_json, hero_idx "
        "FROM editions WHERE edition_id = ?",
        (edition_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "edition_id": row[0],
        "created_at": row[1],
        "items": json.loads(row[2]),
        "stats": json.loads(row[3]),
        "hero_idx": row[4],
    }


def get_previous_edition(feed_name: str) -> Optional[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT edition_id, created_at, items_json, stats_json, hero_idx "
        "FROM editions WHERE feed_name = ? ORDER BY created_at DESC LIMIT 2",
        (feed_name,),
    ).fetchall()
    conn.close()
    if len(rows) < 2:
        return None
    row = rows[1]  # Second most recent
    return {
        "edition_id": row[0],
        "created_at": row[1],
        "items": json.loads(row[2]),
        "stats": json.loads(row[3]),
        "hero_idx": row[4],
    }


def get_recent_editions(feed_name: str, limit: int = 7) -> list[dict]:
    """Get the N most recent editions for the archive page."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT edition_id, created_at, items_json, stats_json, hero_idx "
        "FROM editions WHERE feed_name = ? ORDER BY created_at DESC LIMIT ?",
        (feed_name, limit),
    ).fetchall()
    conn.close()
    return [
        {
            "edition_id": r[0],
            "created_at": r[1],
            "items": json.loads(r[2]),
            "stats": json.loads(r[3]),
            "hero_idx": r[4],
        }
        for r in rows
    ]


def compute_author_stink_scores():
    """Compute account-level stink scores from recent posting behavior.

    Stink score = how relay/exhaust-shaped is this account's behavior.
    Higher = more likely a syndication bot. Range 0-1.
    """
    import re
    url_re = re.compile(r'https?://\S+|www\.\S+|\S+\.\w{2,4}/\S+')

    conn = get_conn()
    cutoff = (timeutil.now_utc() - __import__("datetime").timedelta(days=7)).isoformat()

    # Get all authors with recent posts
    authors = conn.execute("SELECT DISTINCT author_did FROM posts WHERE created_at >= ?", (cutoff,)).fetchall()

    for (did,) in authors:
        rows = conn.execute(
            "SELECT text, has_external_embed, reply_to_uri, quote_uri, external_domain "
            "FROM posts WHERE author_did = ? AND created_at >= ?",
            (did, cutoff),
        ).fetchall()

        total = len(rows)
        if total < 3:
            continue  # Not enough data

        link_posts = sum(1 for r in rows if r[1])  # has_external_embed
        reply_posts = sum(1 for r in rows if r[2])  # reply_to_uri
        quote_posts = sum(1 for r in rows if r[3])  # quote_uri

        # Non-URL text lengths
        non_url_lens = []
        title_url_count = 0
        domains = []
        for r in rows:
            text = r[0] or ""
            stripped = url_re.sub("", text)
            stripped = re.sub(r'\S+\.(com|org|gov|net|io|co|edu|news)/\S*', '', stripped).strip()
            non_url_lens.append(len(stripped))
            if r[1] and len(stripped) < 30:  # link post with little commentary
                title_url_count += 1
            if r[4]:
                domains.append(r[4])

        link_post_ratio = link_posts / total if total else 0
        reply_ratio = (reply_posts + quote_posts) / total if total else 0
        avg_non_url_len = sum(non_url_lens) / total if total else 0
        title_url_ratio = title_url_count / link_posts if link_posts else 0

        # Domain concentration (how much one domain dominates)
        domain_conc = 0.0
        if domains:
            from collections import Counter
            top = Counter(domains).most_common(1)[0][1]
            domain_conc = top / len(domains)

        # Stink score: higher = more relay-like
        commentary_norm = min(avg_non_url_len / 120.0, 1.0)
        reply_norm = min(reply_ratio / 0.20, 1.0)

        stink = (
            0.30 * link_post_ratio
            + 0.25 * title_url_ratio
            + 0.15 * domain_conc
            + 0.15 * (1.0 - reply_norm)
            + 0.15 * (1.0 - commentary_norm)
        )
        stink = max(0.0, min(1.0, stink))

        conn.execute(
            "UPDATE authors SET stink_score=?, link_post_ratio=?, reply_ratio=?, avg_non_url_len=? WHERE did=?",
            (stink, link_post_ratio, reply_ratio, avg_non_url_len, did),
        )

    conn.commit()
    conn.close()


def purge_old_posts(hours: int = 48):
    conn = get_conn()
    cutoff = (timeutil.now_utc() - __import__("datetime").timedelta(hours=hours)).isoformat()
    conn.execute("DELETE FROM posts WHERE created_at < ?", (cutoff,))
    conn.commit()
    conn.close()


def add_exclusion(did: str, source: str = "dm", note: str = ""):
    conn = get_conn()
    now = timeutil.now_utc().isoformat()
    conn.execute(
        "INSERT INTO exclusions (did, requested_at, source, state, note) VALUES (?, ?, ?, 'excluded', ?) "
        "ON CONFLICT(did) DO UPDATE SET state='excluded', requested_at=excluded.requested_at, note=excluded.note",
        (did, now, source, note),
    )
    conn.commit()
    conn.close()


def remove_exclusion(did: str):
    conn = get_conn()
    now = timeutil.now_utc().isoformat()
    conn.execute(
        "UPDATE exclusions SET state='included', requested_at=? WHERE did=?",
        (now, did),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# url_metadata helpers (source-first pivot, 2026-06-10)
# ---------------------------------------------------------------------------

def upsert_url_metadata(
    canonical_url: str,
    *,
    final_url: Optional[str] = None,
    domain: Optional[str] = None,
    content_type: Optional[str] = None,
    og_title: Optional[str] = None,
    og_description: Optional[str] = None,
    og_image: Optional[str] = None,
    fetch_status: Optional[int] = None,
    fetch_error: Optional[str] = None,
    source_class: Optional[str] = None,
) -> None:
    """Upsert a row in url_metadata. Always stamps fetched_at to now.

    Field semantics:
      canonical_url   — primary key, the canonical form the cluster keys on
      final_url       — URL after following redirects (may differ from canonical)
      domain          — bare host (e.g. apnews.com)
      content_type    — Content-Type header on the final response
      og_title        — og:title (or <title>) extracted from the response body
      og_description  — og:description
      og_image        — og:image (kept as URL, not mirrored)
      fetch_status    — HTTP status of the final response, or null on connect error
      fetch_error     — short error string when fetch_status is null
      source_class    — classification from source_class.classify_domain(domain)
    """
    conn = get_conn()
    now = timeutil.now_utc().isoformat()
    try:
        conn.execute(
            """
            INSERT INTO url_metadata
              (canonical_url, final_url, domain, content_type,
               og_title, og_description, og_image,
               fetch_status, fetch_error, fetched_at, source_class)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_url) DO UPDATE SET
              final_url       = excluded.final_url,
              domain          = excluded.domain,
              content_type    = excluded.content_type,
              og_title        = excluded.og_title,
              og_description  = excluded.og_description,
              og_image        = excluded.og_image,
              fetch_status    = excluded.fetch_status,
              fetch_error     = excluded.fetch_error,
              fetched_at      = excluded.fetched_at,
              source_class    = excluded.source_class
            """,
            (
                canonical_url, final_url, domain, content_type,
                og_title, og_description, og_image,
                fetch_status, fetch_error, now, source_class,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_url_metadata(canonical_url: str) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM url_metadata WHERE canonical_url = ?",
            (canonical_url,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def get_url_metadata_batch(canonical_urls: list[str]) -> dict[str, dict]:
    """Return a {canonical_url -> metadata dict} map for the given URLs.

    Cheap-batch alternative to get_url_metadata() in tight render loops.
    """
    if not canonical_urls:
        return {}
    conn = get_conn()
    try:
        placeholders = ",".join("?" for _ in canonical_urls)
        rows = conn.execute(
            f"SELECT * FROM url_metadata WHERE canonical_url IN ({placeholders})",
            canonical_urls,
        ).fetchall()
    finally:
        conn.close()
    return {row["canonical_url"]: dict(row) for row in rows}


def list_canonical_urls_needing_fetch(
    *,
    limit: int = 50,
    max_age_hours: int = 24 * 7,
) -> list[str]:
    """Return canonical_urls referenced by posts but missing from
    url_metadata OR with a stale fetch.

    Stale = older than max_age_hours, or fetched_at is null. Successful
    rows (fetch_status 200) get a longer refetch interval; failed rows
    (4xx/5xx/timeout) get a shorter one applied by the caller.

    We can't store the canonical form on the posts table without a
    migration, so we compute it on the fly via the existing
    `external_uri` column. That's bounded by the active post window
    (~24h) so the candidate set is small.
    """
    # Lazy-import to avoid the urls↔db cycle at module load
    from .urls import canonicalize_url
    conn = get_conn()
    try:
        # Distinct posts.external_uri from recent window
        rows = conn.execute(
            "SELECT DISTINCT external_uri FROM posts "
            "WHERE external_uri IS NOT NULL AND external_uri != '' "
            "  AND created_at >= datetime('now', '-2 days') "
            "LIMIT 2000"
        ).fetchall()
        existing_meta = {
            row["canonical_url"]: row["fetched_at"]
            for row in conn.execute(
                "SELECT canonical_url, fetched_at FROM url_metadata"
            ).fetchall()
        }
    finally:
        conn.close()

    cutoff_iso = (
        timeutil.now_utc().timestamp() - max_age_hours * 3600
    )

    needs: list[str] = []
    seen: set[str] = set()
    for row in rows:
        raw = row[0] if not hasattr(row, "keys") else row["external_uri"]
        canonical = canonicalize_url(raw)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        last_fetched_iso = existing_meta.get(canonical)
        if last_fetched_iso is None:
            needs.append(canonical)
        else:
            try:
                last_ts = timeutil.to_utc_datetime(last_fetched_iso).timestamp()
            except Exception:
                needs.append(canonical)
                continue
            if last_ts < cutoff_iso:
                needs.append(canonical)
        if len(needs) >= limit:
            break
    return needs


def get_excluded_dids() -> set[str]:
    conn = get_conn()
    rows = conn.execute("SELECT did FROM exclusions WHERE state = 'excluded'").fetchall()
    conn.close()
    return {r[0] for r in rows}


def save_edition(feed_name: str, items: list[dict], stats: dict, hero_idx: int = 0):
    import uuid
    conn = get_conn()
    edition_id = str(uuid.uuid4())[:8]
    now = timeutil.now_utc().isoformat()
    conn.execute(
        "INSERT INTO editions (edition_id, feed_name, created_at, items_json, stats_json, hero_idx) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (edition_id, feed_name, now, json.dumps(items), json.dumps(stats), hero_idx),
    )
    # Keep only last 100 editions per feed
    conn.execute(
        "DELETE FROM editions WHERE feed_name = ? AND edition_id NOT IN "
        "(SELECT edition_id FROM editions WHERE feed_name = ? ORDER BY created_at DESC LIMIT 100)",
        (feed_name, feed_name),
    )
    conn.commit()
    conn.close()
    return edition_id


def get_latest_edition(feed_name: str) -> Optional[dict]:
    conn = get_conn()
    row = conn.execute(
        "SELECT edition_id, created_at, items_json, stats_json, hero_idx "
        "FROM editions WHERE feed_name = ? ORDER BY created_at DESC LIMIT 1",
        (feed_name,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "edition_id": row[0],
        "created_at": row[1],
        "items": json.loads(row[2]),
        "stats": json.loads(row[3]),
        "hero_idx": row[4],
    }
