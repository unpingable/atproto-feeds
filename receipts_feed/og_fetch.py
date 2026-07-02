"""OG / source metadata fetcher.

Doctrine line, copy verbatim into about/method (chatty 2026-06-10):

    The site does not mirror Bluesky posts. It may cache public source
    metadata for linked URLs: canonical URL, title, description, domain,
    content type, and retrieval status.

Boring, necessary. The HVAC filter of curation.

Design constraints:

  - Never blocks ingest, rank, or the API loop. Runs as a periodic task
    out of `api._periodic_og_fetch`, bounded by both batch size and
    per-fetch wall clock.
  - Uses urllib only — no aiohttp / httpx dependency for one job.
  - Follows up to N redirects; records `final_url` post-redirect so
    canonicalization can later be checked against the actual landing.
  - Records `fetch_status` on every attempt (success or failure) so the
    next round can back off failed URLs.
  - Does NOT keep the response body. Just extracts og:title /
    og:description / og:image / <title> / content-type, then drops it.

Backoff policy (applied by `db.list_canonical_urls_needing_fetch`):
  - successful 200          → refetch after 7 days
  - 404 / 410 / 4xx         → refetch after 7 days (don't keep hammering)
  - 5xx / timeout / 403     → refetch after 24h (server may recover)
"""
from __future__ import annotations

import html
import logging
import re
import socket
import time
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import urljoin

from . import db, handlers, source_class as source_class_mod
from .urls import canonicalize_url, extract_domain

LOG = logging.getLogger("receipts.og_fetch")


# Conservative user agent. Some sites refuse "python-urllib/" by default.
USER_AGENT = (
    "InstantInternetNewsBot/0.1 (+https://instantinternet.news/about) "
    "Python-urllib"
)
ACCEPT_HEADER = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
)
MAX_RESPONSE_BYTES = 256 * 1024  # 256 KB — plenty for <head>
FETCH_TIMEOUT_SECONDS = 6.0


# ---------------------------------------------------------------------------
# OG / title extraction
# ---------------------------------------------------------------------------

# Match meta tags with property/name + content. Tolerant of attribute order
# and quoting. Anchored to the <head> section in practice — we just slice
# the first 64 KB of the body.
_META_RE = re.compile(
    rb"""<meta
        \s+
        (?:[^>]*?)
        (?:property|name)
        \s*=\s*
        ['"](?P<key>[^'"]+)['"]
        (?:[^>]*?)
        content
        \s*=\s*
        ['"](?P<value>[^'"]*)['"]""",
    re.IGNORECASE | re.VERBOSE,
)
_META_RE_REVERSED = re.compile(
    rb"""<meta
        \s+
        (?:[^>]*?)
        content
        \s*=\s*
        ['"](?P<value>[^'"]*)['"]
        (?:[^>]*?)
        (?:property|name)
        \s*=\s*
        ['"](?P<key>[^'"]+)['"]""",
    re.IGNORECASE | re.VERBOSE,
)
_TITLE_RE = re.compile(rb"<title[^>]*>(.+?)</title>", re.IGNORECASE | re.DOTALL)


def _extract_meta(html_bytes: bytes) -> dict[str, str]:
    """Pull og:* + name=description / <title> from the HTML <head>."""
    snippet = html_bytes[:64 * 1024]
    out: dict[str, str] = {}
    for rx in (_META_RE, _META_RE_REVERSED):
        for m in rx.finditer(snippet):
            try:
                key = m.group("key").decode("utf-8", errors="replace").strip().lower()
                value = m.group("value").decode("utf-8", errors="replace").strip()
            except Exception:
                continue
            if key in out:
                continue  # first wins
            if key.startswith("og:") or key in ("description", "twitter:title",
                                                "twitter:description",
                                                "twitter:image"):
                out[key] = html.unescape(value)
    # Plain <title> as fallback for og:title
    if "og:title" not in out and "twitter:title" not in out:
        m = _TITLE_RE.search(snippet)
        if m:
            try:
                title = m.group(1).decode("utf-8", errors="replace").strip()
                out["title"] = html.unescape(title)
            except Exception:
                pass
    return out


def _pick_title(meta: dict[str, str]) -> Optional[str]:
    return (
        meta.get("og:title")
        or meta.get("twitter:title")
        or meta.get("title")
        or None
    )


def _pick_description(meta: dict[str, str]) -> Optional[str]:
    return (
        meta.get("og:description")
        or meta.get("twitter:description")
        or meta.get("description")
        or None
    )


def _pick_image(meta: dict[str, str]) -> Optional[str]:
    return meta.get("og:image") or meta.get("twitter:image") or None


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

class _RedirectHandler(urllib.request.HTTPRedirectHandler):
    """Standard handler; we just bound the redirect depth via maxredirs."""
    max_redirects = 4


def _fetch_url(url: str, *, timeout: float = FETCH_TIMEOUT_SECONDS) -> dict:
    """Perform one HTTP GET and parse OG metadata.

    Returns a dict with keys:
      final_url        — URL after redirects
      content_type     — header value (lowercased, parameters stripped)
      fetch_status     — HTTP status (200, 403, 404, ...) or None on error
      fetch_error      — short error string when fetch_status is None
      og_title         — string or None
      og_description   — string or None
      og_image         — string or None
    """
    out: dict = {
        "final_url": url,
        "content_type": None,
        "fetch_status": None,
        "fetch_error": None,
        "og_title": None,
        "og_description": None,
        "og_image": None,
    }
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": ACCEPT_HEADER,
        "Accept-Language": "en-US,en;q=0.9",
    })
    opener = urllib.request.build_opener(_RedirectHandler())
    try:
        with opener.open(req, timeout=timeout) as resp:
            out["final_url"] = resp.geturl()
            out["fetch_status"] = resp.getcode()
            ctype_raw = resp.headers.get("Content-Type") or ""
            out["content_type"] = ctype_raw.split(";")[0].strip().lower() or None
            body = resp.read(MAX_RESPONSE_BYTES)
        # Only parse HTML responses
        if out["content_type"] and "html" in out["content_type"]:
            meta = _extract_meta(body)
            out["og_title"] = _pick_title(meta)
            out["og_description"] = _pick_description(meta)
            out["og_image"] = _pick_image(meta)
    except urllib.error.HTTPError as e:
        out["fetch_status"] = e.code
        out["fetch_error"] = f"http_{e.code}"
        try:
            out["final_url"] = e.url or url
        except Exception:
            pass
    except urllib.error.URLError as e:
        out["fetch_error"] = f"url_error: {e.reason!s}"[:200]
    except socket.timeout:
        out["fetch_error"] = "timeout"
    except Exception as e:  # noqa: BLE001 — defensive: never let one URL kill the loop
        out["fetch_error"] = f"unexpected: {type(e).__name__}: {e}"[:200]
    return out


def fetch_and_store(canonical_url: str, *, timeout: float = FETCH_TIMEOUT_SECONDS) -> dict:
    """Fetch one canonical URL, upsert the result, return the row.

    Resolves source_class from `final_url`'s domain so a 301 from a vanity
    host to a known publisher is classified correctly.

    A registered domain handler runs first (coverage expansion for sources that
    block a naive fetch but expose a free API). If it can't resolve, the generic
    fetch runs and records whatever it gets.
    """
    result = None
    handler = handlers.get_handler(extract_domain(canonical_url) or "")
    if handler is not None:
        try:
            result = handler(canonical_url, timeout=timeout)
            if result is not None:
                LOG.info("handler resolved %s", canonical_url)
        except Exception:
            LOG.exception("handler error for %s (falling back to generic fetch)", canonical_url)
            result = None
    if result is None:
        result = _fetch_url(canonical_url, timeout=timeout)
    final_url = result["final_url"] or canonical_url
    domain = extract_domain(final_url) or extract_domain(canonical_url) or ""
    sc = source_class_mod.classify_domain(domain)
    db.upsert_url_metadata(
        canonical_url,
        final_url=final_url,
        domain=domain,
        content_type=result["content_type"],
        og_title=_truncate(result["og_title"], 300),
        og_description=_truncate(result["og_description"], 800),
        og_image=_truncate(result["og_image"], 600),
        fetch_status=result["fetch_status"],
        fetch_error=result["fetch_error"],
        source_class=sc,
    )
    return {
        "canonical_url": canonical_url,
        "final_url": final_url,
        "domain": domain,
        "source_class": sc,
        **result,
    }


def _truncate(s: Optional[str], n: int) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    return s[:n] if len(s) > n else s


# ---------------------------------------------------------------------------
# Periodic driver (called from api._periodic_og_fetch)
# ---------------------------------------------------------------------------

def run_batch(*, batch_size: int = 25, per_fetch_timeout: float = FETCH_TIMEOUT_SECONDS) -> dict:
    """Fetch metadata for up to `batch_size` canonical URLs needing refresh.

    Returns a summary dict with the counts so the caller can log
    succinctly. Wall-clock-bounded by batch_size * per_fetch_timeout in
    the worst case (~150s for the default 25 × 6s), but in practice
    most fetches complete in <1s.
    """
    started = time.perf_counter()
    needs = db.list_canonical_urls_needing_fetch(limit=batch_size)
    if not needs:
        return {"requested": 0, "ok": 0, "failed": 0, "duration_ms": 0}

    ok = 0
    failed = 0
    for url in needs:
        try:
            result = fetch_and_store(url, timeout=per_fetch_timeout)
            if result.get("fetch_status") == 200:
                ok += 1
            else:
                failed += 1
        except Exception:
            LOG.exception("og_fetch unexpected failure for %s", url)
            failed += 1
    duration_ms = (time.perf_counter() - started) * 1000.0
    LOG.info(
        "OG_FETCH requested=%d ok=%d failed=%d duration_ms=%.0f",
        len(needs), ok, failed, duration_ms,
    )
    return {
        "requested": len(needs),
        "ok": ok,
        "failed": failed,
        "duration_ms": round(duration_ms, 1),
    }
