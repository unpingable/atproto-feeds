"""DM opt-out/opt-in listener for project-level exclusions.

Polls for new DMs to @instantinternet.news and processes commands:
  - "opt out" / "exclude me" / "remove me" → exclude sender's DID
  - "opt in" / "include me" → remove exclusion

Runs as a periodic background task.
"""

import logging
import re
import time

import httpx

from . import config, db

LOG = logging.getLogger("receipts.dm")

# Commands that trigger opt-out
OPT_OUT_PATTERNS = re.compile(
    r"^\s*(opt\s*out|exclude\s*me|remove\s*me|don'?t\s*include\s*me)\s*[.!]?\s*$",
    re.IGNORECASE,
)

# Commands that trigger opt-in
OPT_IN_PATTERNS = re.compile(
    r"^\s*(opt\s*in|include\s*me|add\s*me\s*back)\s*[.!]?\s*$",
    re.IGNORECASE,
)

# Confirmation messages
OPT_OUT_REPLY = (
    "You're excluded from Instant Internet News surfaces now. "
    "This removes your posts from the feed and site going forward. "
    "DM \"opt in\" to reverse it."
)

OPT_IN_REPLY = (
    "You're back in. Your posts are eligible for the feed and site again."
)

IGNORED_REPLY = None  # Don't reply to unrecognized messages


def _create_session() -> tuple[str, str, str]:
    """Authenticate and return (access_jwt, did, pds_endpoint)."""
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
    # Extract PDS endpoint from DID doc
    pds = (
        data.get("didDoc", {})
        .get("service", [{}])[0]
        .get("serviceEndpoint", "")
    )
    return data["accessJwt"], data["did"], pds


def _chat_request(pds: str, jwt: str, method: str, params: dict = None, json_body: dict = None) -> dict:
    """Make an XRPC request to the chat service via the PDS."""
    headers = {
        "Authorization": f"Bearer {jwt}",
        "Atproto-Proxy": "did:web:api.bsky.chat#bsky_chat",
    }
    if json_body is not None:
        resp = httpx.post(
            f"{pds}/xrpc/{method}",
            headers=headers,
            json=json_body,
            timeout=15,
        )
    else:
        resp = httpx.get(
            f"{pds}/xrpc/{method}",
            headers=headers,
            params=params or {},
            timeout=15,
        )
    resp.raise_for_status()
    return resp.json()


def _send_message(pds: str, jwt: str, convo_id: str, text: str):
    """Send a DM reply."""
    _chat_request(pds, jwt, "chat.bsky.convo.sendMessage", json_body={
        "convoId": convo_id,
        "message": {"text": text},
    })


def check_dms():
    """Poll for new DMs and process opt-out/opt-in commands.

    Called periodically by the background task in api.py.
    """
    try:
        jwt, our_did, pds = _create_session()
    except Exception:
        LOG.exception("failed to authenticate for DM check")
        return

    if not pds:
        LOG.warning("no PDS endpoint found, skipping DM check")
        return

    # Get last processed message timestamp
    last_checked = db.get_state("dm_last_checked") or "2000-01-01T00:00:00Z"

    try:
        data = _chat_request(pds, jwt, "chat.bsky.convo.listConvos", params={"limit": 50})
    except Exception:
        LOG.exception("failed to list conversations")
        return

    processed = 0
    latest_ts = last_checked

    for convo in data.get("convos", []):
        convo_id = convo.get("id", "")
        last_msg = convo.get("lastMessage", {})

        if not isinstance(last_msg, dict):
            continue

        # Skip messages from ourselves
        sender_did = last_msg.get("sender", {}).get("did", "")
        if sender_did == our_did:
            continue

        # Skip already-processed messages
        msg_sent_at = last_msg.get("sentAt", "")
        if msg_sent_at <= last_checked:
            continue

        text = last_msg.get("text", "").strip()
        if not text:
            continue

        # Track latest timestamp
        if msg_sent_at > latest_ts:
            latest_ts = msg_sent_at

        # Check for opt-out
        if OPT_OUT_PATTERNS.match(text):
            LOG.info("opt-out request from %s", sender_did)
            db.add_exclusion(sender_did, source="dm", note=text)
            try:
                _send_message(pds, jwt, convo_id, OPT_OUT_REPLY)
            except Exception:
                LOG.exception("failed to send opt-out confirmation to %s", sender_did)
            processed += 1
            continue

        # Check for opt-in
        if OPT_IN_PATTERNS.match(text):
            LOG.info("opt-in request from %s", sender_did)
            db.remove_exclusion(sender_did)
            try:
                _send_message(pds, jwt, convo_id, OPT_IN_REPLY)
            except Exception:
                LOG.exception("failed to send opt-in confirmation to %s", sender_did)
            processed += 1
            continue

        # Unrecognized message — log but don't reply
        LOG.debug("unrecognized DM from %s: %s", sender_did, text[:50])

    # Update last-checked timestamp
    if latest_ts > last_checked:
        db.set_state("dm_last_checked", latest_ts)

    if processed:
        LOG.info("processed %d DM commands", processed)
