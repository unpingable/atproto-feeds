"""Seed graph bootstrap: fetch follows/mutuals from trust graph source."""

import logging

import httpx

from . import config, db

LOG = logging.getLogger("receipts.graph")


def _create_session() -> tuple[str, str]:
    """Authenticate and return (access_jwt, did)."""
    resp = httpx.post(
        f"{config.BSKY_SERVICE}/xrpc/com.atproto.server.createSession",
        json={
            "identifier": config.FEED_PUBLISHER_HANDLE,
            "password": config.FEED_PUBLISHER_PASSWORD,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["accessJwt"], data["did"]


def _auth_headers(jwt: str) -> dict:
    return {"Authorization": f"Bearer {jwt}"}


def resolve_did(handle: str) -> str:
    """Resolve a handle to a DID via the Bluesky API."""
    url = f"{config.BSKY_SERVICE}/xrpc/com.atproto.identity.resolveHandle"
    resp = httpx.get(url, params={"handle": handle}, timeout=15)
    resp.raise_for_status()
    return resp.json()["did"]


def fetch_follows(actor_did: str, jwt: str) -> list[dict]:
    """Fetch all follows for an actor. Returns list of {did, handle}."""
    follows = []
    cursor = None
    while True:
        params = {"actor": actor_did, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        resp = httpx.get(
            f"{config.BSKY_SERVICE}/xrpc/app.bsky.graph.getFollows",
            params=params,
            headers=_auth_headers(jwt),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        for f in data.get("follows", []):
            follows.append({"did": f["did"], "handle": f.get("handle", "")})
        cursor = data.get("cursor")
        if not cursor:
            break
    return follows


def fetch_followers(actor_did: str, jwt: str) -> list[dict]:
    """Fetch all followers for an actor. Returns list of {did, handle}."""
    followers = []
    cursor = None
    while True:
        params = {"actor": actor_did, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        resp = httpx.get(
            f"{config.BSKY_SERVICE}/xrpc/app.bsky.graph.getFollowers",
            params=params,
            headers=_auth_headers(jwt),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        for f in data.get("followers", []):
            followers.append({"did": f["did"], "handle": f.get("handle", "")})
        cursor = data.get("cursor")
        if not cursor:
            break
    return followers


def bootstrap_graph():
    """Bootstrap the seed graph from the trust graph source account."""
    db.init_db()
    return refresh_graph()


def refresh_graph():
    """Refresh the seed graph: add new follows/mutuals, demote unfollows.

    Safe to call repeatedly. Upserts current graph and demotes authors
    who are no longer in the follow/follower set to 'stale' with a low
    trusted_score (but does not delete them — they may still have posts
    in the ranking window).
    """
    LOG.info("authenticating as %s", config.FEED_PUBLISHER_HANDLE)
    jwt, _publisher_did = _create_session()

    handle = config.TRUST_GRAPH_HANDLE
    LOG.info("resolving trust graph source: %s", handle)
    source_did = resolve_did(handle)
    LOG.info("source DID: %s", source_did)

    LOG.info("fetching follows...")
    follows = fetch_follows(source_did, jwt)
    follow_dids = {f["did"] for f in follows}
    LOG.info("found %d follows", len(follows))

    LOG.info("fetching followers...")
    followers = fetch_followers(source_did, jwt)
    follower_dids = {f["did"] for f in followers}
    LOG.info("found %d followers", len(followers))

    mutual_dids = follow_dids & follower_dids
    current_dids = follow_dids | follower_dids

    # Upsert all current follows
    for f in follows:
        seed_class = "mutual" if f["did"] in mutual_dids else "followed"
        trusted_score = 4.0 if seed_class == "mutual" else 2.0
        db.upsert_author(f["did"], f["handle"], seed_class, trusted_score)

    # Upsert followers not in follows
    for f in followers:
        if f["did"] not in follow_dids:
            db.upsert_author(f["did"], f["handle"], "follower", 1.0)

    # Demote authors no longer in the graph
    existing_dids = db.get_seed_dids()
    stale_dids = existing_dids - current_dids
    if stale_dids:
        conn = db.get_conn()
        now = db.timeutil.now_utc().isoformat()
        for did in stale_dids:
            conn.execute(
                "UPDATE authors SET seed_class='stale', trusted_score=0.1, updated_at=? "
                "WHERE did=? AND seed_class != 'stale'",
                (now, did),
            )
        conn.commit()
        conn.close()
        LOG.info("demoted %d stale authors", len(stale_dids))

    total = len(current_dids)
    mutuals = len(mutual_dids)
    LOG.info("graph refreshed: %d authors (%d mutuals, %d stale)", total, mutuals, len(stale_dids))
    db.set_state("last_graph_refresh", db.timeutil.now_utc().isoformat())
    return {"total": total, "mutuals": mutuals, "follows": len(follows), "followers": len(followers), "stale": len(stale_dids)}
