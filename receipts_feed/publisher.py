"""Publish/update feed generator record on Bluesky."""

import logging

import httpx

from . import config

LOG = logging.getLogger("receipts.publisher")


def create_session() -> dict:
    """Authenticate and return session (accessJwt, did)."""
    resp = httpx.post(
        f"{config.BSKY_SERVICE}/xrpc/com.atproto.server.createSession",
        json={
            "identifier": config.FEED_PUBLISHER_HANDLE,
            "password": config.FEED_PUBLISHER_PASSWORD,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def publish_feed(
    feed_name: str = "receipts",
    display_name: str = "Receipts",
    description: str = "Original, source-bearing, graph-adjacent posts. Less repost sludge. More people showing their work.",
):
    """Create or update a feed generator record."""
    session = create_session()
    publisher_did = session["did"]
    access_jwt = session["accessJwt"]

    hostname = config.FEED_SERVICE_HOSTNAME
    if not hostname:
        raise ValueError("FEED_SERVICE_HOSTNAME must be set to publish a feed")

    service_did = f"did:web:{hostname}"

    record = {
        "$type": "app.bsky.feed.generator",
        "did": service_did,
        "displayName": display_name,
        "description": description,
        "createdAt": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
    }

    resp = httpx.post(
        f"{config.BSKY_SERVICE}/xrpc/com.atproto.repo.putRecord",
        headers={"Authorization": f"Bearer {access_jwt}"},
        json={
            "repo": publisher_did,
            "collection": "app.bsky.feed.generator",
            "rkey": feed_name,
            "record": record,
        },
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()

    feed_uri = f"at://{publisher_did}/app.bsky.feed.generator/{feed_name}"
    LOG.info("published feed: %s", feed_uri)
    return {"uri": feed_uri, "cid": result.get("cid", "")}
