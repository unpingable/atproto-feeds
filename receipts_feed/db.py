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
            updated_at TEXT
        )
    """)

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


def insert_post(post: dict):
    conn = get_conn()
    now = timeutil.now_utc().isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO posts "
        "(uri, cid, author_did, created_at, text, reply_to_uri, root_uri, quote_uri, "
        "external_uri, external_domain, has_external_embed, has_image, has_video, "
        "is_repost, langs, link_count, facets_count, indexed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
