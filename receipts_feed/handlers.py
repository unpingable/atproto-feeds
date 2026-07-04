"""Domain-specific source handlers — coverage expansion, not a smarter compiler.

Some citation-native / public-record sources block a naive HTML fetch (403) but
expose the same object through a free, anonymous API. A handler resolves the
canonical title/custody via that API and returns the SAME dict shape as
`og_fetch._fetch_url`, so a previously-`carrier` (handler_missing) item can
become `sourced` — WITHOUT touching the compile predicate.

Doctrine: handlers improve source custody and reachability. They do NOT make the
compiler read English. Better hands, not a bigger brain. A handler that can't
resolve returns None and the generic fetch runs, recording whatever it gets.

Registry is intentionally tiny. Add one domain at a time, each with a live
before/after receipt showing carrier/unreachable -> sourced.
"""
from __future__ import annotations

import json
import logging
import re
import socket
import urllib.error
import urllib.request

LOG = logging.getLogger("receipts.handlers")

# Own UA (not imported from og_fetch, to avoid a circular import).
_USER_AGENT = "InstantInternetNewsBot/0.1 (+https://instantinternet.news/about)"
_MAX_BYTES = 256 * 1024


def _get_json(url: str, *, timeout: float) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.getcode() != 200:
                return None
            return json.loads(resp.read(_MAX_BYTES).decode("utf-8", "replace"))
    except (urllib.error.URLError, socket.timeout, ValueError):
        return None
    except Exception:  # noqa: BLE001 — a handler must never kill the fetch loop
        LOG.exception("handler json fetch failed: %s", url)
        return None


# --- PubMed (NCBI E-utilities) ---------------------------------------------
# pubmed.ncbi.nlm.nih.gov/<pmid>/  ->  esummary JSON (free, anonymous).
_PUBMED_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)")
_EUTILS = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    "?db=pubmed&id={pmid}&retmode=json&tool=InstantInternetNewsBot"
)


def pubmed_handler(url: str, *, timeout: float) -> dict | None:
    m = _PUBMED_RE.search(url)
    if not m:
        return None
    pmid = m.group(1)
    data = _get_json(_EUTILS.format(pmid=pmid), timeout=timeout)
    if not data:
        return None
    rec = (data.get("result") or {}).get(pmid) or {}
    title = (rec.get("title") or "").strip().rstrip(".")
    if not title:
        return None
    journal = rec.get("source") or rec.get("fulljournalname") or None
    return {
        "final_url": url,
        "content_type": "application/json",
        "fetch_status": 200,
        "fetch_error": None,
        "og_title": title,
        "og_description": journal,
        "og_image": None,
    }


# --- Crossref (DOI -> title/journal) ---------------------------------------
# Academic publishers (nature, science, pnas, biorxiv) sit behind JS bot walls
# that return an interstitial, so a naive fetch can't get the title. But every
# article carries a DOI, and Crossref resolves DOI -> title anonymously. One
# handler reclaims the whole DOI-bearing academic tier.
_DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s?#]+)")
_CROSSREF = "https://api.crossref.org/works/{doi}?mailto=instantinternet.news"


def _extract_doi(url: str) -> str | None:
    # Nature article IDs ARE the DOI suffix under 10.1038 (…/articles/<id>).
    m = re.search(r"nature\.com/articles/([^/?#.]+)", url, re.IGNORECASE)
    if m:
        return "10.1038/" + m.group(1)
    # Otherwise a DOI embedded in the path (science.org/doi/…, pnas.org/doi/…,
    # biorxiv.org/content/…, doi.org/…).
    m = _DOI_RE.search(url)
    if m:
        return m.group(1).rstrip("/.")
    return None


def crossref_handler(url: str, *, timeout: float) -> dict | None:
    doi = _extract_doi(url)
    if not doi:
        return None
    data = _get_json(_CROSSREF.format(doi=doi), timeout=timeout)
    if not data:
        return None
    msg = data.get("message") or {}
    title = ((msg.get("title") or [""])[0] or "").strip()
    if not title:
        return None
    journal = (msg.get("container-title") or [None])[0]
    return {
        "final_url": url,
        "content_type": "application/json",
        "fetch_status": 200,
        "fetch_error": None,
        "og_title": title,
        "og_description": journal,
        "og_image": None,
    }


# Domain -> handler. Suffix-matched by get_handler (so subdomains resolve).
DOMAIN_HANDLERS: dict[str, callable] = {
    "pubmed.ncbi.nlm.nih.gov": pubmed_handler,
    "nature.com": crossref_handler,
    "science.org": crossref_handler,
    "pnas.org": crossref_handler,
    "biorxiv.org": crossref_handler,
    "medrxiv.org": crossref_handler,
    "doi.org": crossref_handler,
}


def get_handler(domain: str | None):
    if not domain:
        return None
    d = domain.lower().strip()
    if d.startswith("www."):
        d = d[4:]
    if d in DOMAIN_HANDLERS:
        return DOMAIN_HANDLERS[d]
    for k, h in DOMAIN_HANDLERS.items():
        if d.endswith("." + k):
            return h
    return None
