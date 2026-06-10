"""URL canonicalization and tracking-param hygiene.

Doctrine (chatty 2026-06-10, source-first pivot):

    Stories are URLs. Skeets are quotes about them.

A story cluster is keyed by the canonical URL, not by the post that
links to it. Two posts pointing at the same article with different
tracking params must collapse into one story; the Washington Post's
analytics garbage cannot be allowed to split one story into three.

Promoted out of cluster.py so the OG fetcher, the story-card renderer,
and the cluster builder can all share one canonicalization implementation.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, urlencode, urlparse


# Tracking / ad-attribution / mailer params. Be generous — over-strip
# beats under-strip when the goal is "same story → same cluster key".
# Lower-cased; matched case-insensitively.
_TRACKING_PARAMS: set[str] = {
    # Google / UTM family
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "utm_id", "utm_brand", "utm_social", "utm_social-type",
    "gclid", "gclsrc", "dclid", "_ga", "_gl",
    # Facebook / Meta
    "fbclid", "mibextid",
    # Instagram
    "igshid", "igsh",
    # Yandex
    "yclid",
    # Microsoft / Bing
    "msclkid",
    # YouTube / referrer
    "si", "feature", "kw",
    # Mailchimp
    "mc_cid", "mc_eid",
    # HubSpot
    "_hsenc", "_hsmi",
    # Marketo
    "mkt_tok",
    # Adobe Analytics
    "s_cid", "ito",
    # Common newsroom mailers / promo IDs
    "cmpid",          # CNN, AP, BBC
    "smid",           # NYT social
    "mbid",           # Mother Jones / inc.
    "taid", "tid",    # TaskUs / ad pipelines
    "src",            # generic
    "ref", "ref_src", "ref_url",
    "src_id", "campaign_id",
    "email_source", "email_subject",
    "mod",            # NYT
    "pgtype",         # WaPo
    "itid",           # WaPo
    "tpcc",           # NYT promotional
    "partner",        # generic
}


# Mobile / canonical host folds: hosts whose canonical form is shorter or
# rerouted. Order-sensitive: applied before the www. strip.
_HOST_FOLDS: dict[str, str] = {
    "m.youtube.com": "youtube.com",
    "youtu.be": "youtube.com",
    "music.youtube.com": "youtube.com",
    "m.facebook.com": "facebook.com",
    "mbasic.facebook.com": "facebook.com",
    "mobile.twitter.com": "twitter.com",
    "m.twitter.com": "twitter.com",
    "old.reddit.com": "reddit.com",
    "np.reddit.com": "reddit.com",
}


# Trailing-slash policy: paths ending in `/` are kept (preserves directory-
# style article URLs), except for the empty path which becomes `/`. This is
# important because some sites distinguish `/article/` from `/article`.
def canonicalize_url(url: str) -> str:
    """Return a stable canonical form for clustering.

    - lower-cases scheme + host
    - strips `www.`
    - folds known mobile/alt hosts (m.youtube.com → youtube.com)
    - strips tracking params (utm_*, fbclid, gclid, cmpid, smid, mbid, ...)
    - rebuilds the query string in sorted order so param re-ordering can't
      split clusters
    - drops fragments
    - rewrites mobile / shorts YouTube URLs to /watch?v=<id>

    Returns the original string on any parse error so the caller can still
    use it as a fallback key.
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return url

    host = (parsed.hostname or "").lower()
    if host in _HOST_FOLDS:
        host = _HOST_FOLDS[host]
    if host.startswith("www."):
        host = host[4:]

    scheme = (parsed.scheme or "https").lower()
    if scheme == "http":
        scheme = "https"  # collapse http↔https variants for clustering

    port = ""
    if parsed.port and parsed.port not in (80, 443):
        port = f":{parsed.port}"

    path = parsed.path or "/"

    # YouTube normalization: shorts, youtu.be, music → /watch?v=<id>
    yt_video_id: str | None = None
    if host == "youtube.com":
        # /shorts/<id> or path is the video id (from youtu.be)
        if path.startswith("/shorts/"):
            yt_video_id = path.split("/")[2].split("?")[0]
        elif path == "/watch":
            qs_v = parse_qs(parsed.query or "").get("v") or []
            if qs_v:
                yt_video_id = qs_v[0]
        elif path and path.strip("/") and "/" not in path.strip("/"):
            # legacy youtu.be that re-parsed as plain /<id>
            yt_video_id = path.strip("/")

    # Build query: strip tracking, keep the rest sorted for stability.
    query = ""
    if parsed.query:
        params = parse_qs(parsed.query, keep_blank_values=False)
        cleaned = {
            k: v for k, v in params.items()
            if k.lower() not in _TRACKING_PARAMS
        }
        if cleaned:
            query = urlencode(sorted(cleaned.items()), doseq=True)

    if yt_video_id:
        path = "/watch"
        query = f"v={yt_video_id}"

    out = f"{scheme}://{host}{port}{path}"
    if query:
        out += f"?{query}"
    return out


def url_key(canonical: str) -> str:
    """Stable short hash of a canonical URL, suitable for cluster IDs and
    story_page slugs.
    """
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


_HOST_RE = re.compile(r"^[a-z0-9.-]+$")


def extract_domain(url_or_canonical: str) -> str:
    """Pull the bare host (without `www.` or port) from a URL or canonical.

    Returns '' on parse failure or when the host doesn't look like one.
    """
    if not url_or_canonical:
        return ""
    try:
        host = (urlparse(url_or_canonical).hostname or "").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    if not _HOST_RE.match(host):
        return ""
    return host
