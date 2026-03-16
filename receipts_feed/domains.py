# Domain bonus scoring for Receipts feed.
# Keep this transparent and editable.

PRIMARY_SOURCE_DOMAINS: dict[str, float] = {
    # Courts / legal
    "courtlistener.com": 3.5,
    "supremecourt.gov": 3.5,
    "uscourts.gov": 3.0,
    "pacer.gov": 3.0,
    # Government
    "congress.gov": 3.5,
    "sec.gov": 3.5,
    "federalregister.gov": 3.0,
    "gao.gov": 3.0,
    "whitehouse.gov": 2.5,
    "state.gov": 2.5,
    # Research / academic
    "arxiv.org": 2.5,
    "scholar.google.com": 2.0,
    "pubmed.ncbi.nlm.nih.gov": 2.5,
    "nature.com": 2.0,
    "science.org": 2.0,
    # Technical / code
    "github.com": 2.0,
    "gitlab.com": 2.0,
    "docs.python.org": 2.0,
    "developer.mozilla.org": 2.0,
    # Standards / specs
    "rfc-editor.org": 2.5,
    "w3.org": 2.0,
    "ietf.org": 2.5,
}

REPORTING_DOMAINS: dict[str, float] = {
    "reuters.com": 2.0,
    "apnews.com": 2.0,
    "propublica.org": 2.0,
    "404media.co": 1.5,
    "themarkup.org": 1.5,
    "documentcloud.org": 2.0,
    "nytimes.com": 1.5,
    "washingtonpost.com": 1.5,
    "theguardian.com": 1.0,
    "bbc.com": 1.0,
    "bbc.co.uk": 1.0,
}


# Platform / self-reference domains — not real sources.
# These should not earn link bonuses or appear as "source" in the edition.
PLATFORM_DOMAINS: set[str] = {
    "bsky.app",
    "bsky.social",
    "staging.bsky.app",
    "twitter.com",
    "x.com",
    "threads.net",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "reddit.com",
    "t.co",
    "bit.ly",
    "linktr.ee",
}


def is_platform_domain(domain: str | None) -> bool:
    """Return True if domain is a social platform / self-reference, not a real source."""
    if not domain:
        return False
    domain = domain.lower().strip()
    if domain in PLATFORM_DOMAINS:
        return True
    for d in PLATFORM_DOMAINS:
        if domain.endswith("." + d):
            return True
    return False


def domain_bonus(domain: str | None) -> float:
    if not domain:
        return 0.0
    domain = domain.lower().strip()
    # Platform domains are not sources
    if is_platform_domain(domain):
        return 0.0
    # Check exact match first
    if domain in PRIMARY_SOURCE_DOMAINS:
        return PRIMARY_SOURCE_DOMAINS[domain]
    if domain in REPORTING_DOMAINS:
        return REPORTING_DOMAINS[domain]
    # Check suffix match (e.g. "www.reuters.com" matches "reuters.com")
    for d, score in PRIMARY_SOURCE_DOMAINS.items():
        if domain.endswith("." + d):
            return score
    for d, score in REPORTING_DOMAINS.items():
        if domain.endswith("." + d):
            return score
    return 0.0
