"""Hydrate post URIs into display-ready metadata via Bluesky public API."""

import logging
from typing import Optional

import httpx

from . import config

LOG = logging.getLogger("receipts.hydrate")

# Public API — no auth needed for getPosts
PUBLIC_API = "https://public.api.bsky.app"


def at_uri_to_web_url(uri: str) -> str:
    """Convert at:// URI to bsky.app web URL for embeds."""
    parts = uri.replace("at://", "").split("/")
    if len(parts) >= 3:
        did = parts[0]
        rkey = parts[2]
        return f"https://bsky.app/profile/{did}/post/{rkey}"
    return ""


def _has_no_unauth_label(author: dict) -> bool:
    """Check if author has !no-unauthenticated label (opted out of logged-out visibility)."""
    labels = author.get("labels", [])
    for label in labels:
        val = label.get("val", "")
        if val == "!no-unauthenticated":
            return True
    return False


def _extract_embed_meta(embed: dict) -> dict:
    """Extract display metadata from any embed type."""
    embed_type = embed.get("$type", "")
    result = {
        "external_uri": None,
        "external_title": None,
        "external_description": None,
        "has_media": False,
        "media_type": None,
    }

    # Direct external embed
    if embed_type == "app.bsky.embed.external#view":
        ext = embed.get("external", {})
        result["external_uri"] = ext.get("uri")
        result["external_title"] = ext.get("title")
        result["external_description"] = ext.get("description")

    # Record-with-media (quote + external link or images)
    elif embed_type == "app.bsky.embed.recordWithMedia#view":
        media = embed.get("media", {})
        media_type = media.get("$type", "")
        if media_type == "app.bsky.embed.external#view":
            ext = media.get("external", {})
            result["external_uri"] = ext.get("uri")
            result["external_title"] = ext.get("title")
            result["external_description"] = ext.get("description")
        elif "images" in media_type:
            result["has_media"] = True
            result["media_type"] = "image"

    # Images
    elif "images" in embed_type:
        result["has_media"] = True
        result["media_type"] = "image"

    # Video
    elif "video" in embed_type:
        result["has_media"] = True
        result["media_type"] = "video"

    # Quote post with its own embed
    elif embed_type == "app.bsky.embed.record#view":
        rec = embed.get("record", {})
        # Check if the quoted post has embeds
        inner_embeds = rec.get("embeds", [])
        for inner in inner_embeds:
            inner_meta = _extract_embed_meta(inner)
            if inner_meta["external_uri"]:
                result = inner_meta
                break

    return result


def hydrate_posts(uris: list[str]) -> dict[str, dict]:
    """Fetch post views from public API. Returns {uri: post_view}.

    Posts from authors with !no-unauthenticated label are excluded.
    Posts not returned by the API (deleted, blocked) are excluded.
    """
    if not uris:
        return {}

    results = {}
    for i in range(0, len(uris), 25):
        batch = uris[i:i + 25]
        try:
            resp = httpx.get(
                f"{PUBLIC_API}/xrpc/app.bsky.feed.getPosts",
                params=[("uris", u) for u in batch],
                timeout=15,
            )
            resp.raise_for_status()
            for post in resp.json().get("posts", []):
                uri = post.get("uri", "")
                author = post.get("author", {})
                record = post.get("record", {})
                embed = post.get("embed", {})

                # Skip authors who opted out of logged-out visibility
                if _has_no_unauth_label(author):
                    continue

                # Extract embed metadata
                embed_meta = _extract_embed_meta(embed) if embed else {
                    "external_uri": None, "external_title": None,
                    "external_description": None, "has_media": False,
                    "media_type": None,
                }

                # Build display headline: prefer embed title for link posts.
                # Post text is often commentary/quote fragments; the embed
                # title is the actual article headline.
                text = record.get("text", "")
                display_headline = text
                embed_title = embed_meta["external_title"]
                if embed_title and len(embed_title.strip()) >= 10:
                    text_stripped = text.strip()
                    # Use embed title when post text is:
                    # - short / stub-like
                    # - starts with a quote fragment ("...)
                    # - starts with "- " (platform stub)
                    # - is mostly a URL
                    # - OR when embed title is just a better headline
                    has_url_in_text = ("http" in text_stripped or "www." in text_stripped
                                       or ".com/" in text_stripped or ".org/" in text_stripped)
                    use_embed = (
                        len(text_stripped) < 60
                        or text_stripped.startswith(("- ", '"...', '\u201c...', '...', '\u266b', '\u266a'))
                        or text_stripped.startswith(("http", "www."))
                        or (has_url_in_text and len(text_stripped) < 140)
                    )
                    # Always prefer embed title if it's a real article/video title
                    if use_embed or len(embed_title) > len(text_stripped):
                        display_headline = embed_title

                results[uri] = {
                    "uri": uri,
                    "web_url": at_uri_to_web_url(uri),
                    "author_did": author.get("did", ""),
                    "author_handle": author.get("handle", ""),
                    "author_display_name": author.get("displayName", author.get("handle", "")),
                    "author_avatar": author.get("avatar", ""),
                    "text": text,
                    "display_headline": display_headline,
                    "created_at": record.get("createdAt", ""),
                    "external_uri": embed_meta["external_uri"],
                    "external_title": embed_meta["external_title"],
                    "external_description": embed_meta["external_description"],
                    "has_media": embed_meta["has_media"],
                    "media_type": embed_meta["media_type"],
                    "like_count": post.get("likeCount", 0),
                    "reply_count": post.get("replyCount", 0),
                    "repost_count": post.get("repostCount", 0),
                    "langs": record.get("langs", []),
                    "visible": True,
                }
        except Exception:
            LOG.exception("failed to hydrate batch of %d posts", len(batch))

    return results
