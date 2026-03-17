"""Docket compaction: roll document floods into bundle cards.

Post-cluster, pre-layout step. When the same institutional document stream
(congress, research, filings) floods the edition with 3+ items, keep the
best one as a full story and compact the rest into a docket card.

Not a replacement for clustering. A second-layer packaging move.
"""

import logging
from urllib.parse import urlparse

LOG = logging.getLogger("receipts.docket")

# --- Document family classification ---

CONGRESS_DOMAINS = {
    "congress.gov", "congresstracker.bsky.social",
    "rules.house.gov", "senate.gov",
}

RESEARCH_DOMAINS = {
    "pubmed.ncbi.nlm.nih.gov", "arxiv.org",
    "nature.com", "science.org", "scholar.google.com",
    "biorxiv.org", "medrxiv.org",
}

FILINGS_DOMAINS = {
    "sec.gov", "courtlistener.com", "storage.courtlistener.com",
    "supremecourt.gov", "uscourts.gov", "pacer.gov",
}

# Map family name -> (domains, docket title, subtitle template)
FAMILIES = {
    "congress": (
        CONGRESS_DOMAINS,
        "Today in Congress",
        "{count} legislative items from congress.gov and trackers",
    ),
    "research": (
        RESEARCH_DOMAINS,
        "Research Docket",
        "{count} papers from PubMed, arXiv, and journals",
    ),
    "filings": (
        FILINGS_DOMAINS,
        "Court & Filings",
        "{count} filings from courts and regulatory bodies",
    ),
}

# Minimum items before compaction triggers
COMPACT_THRESHOLD = 3


def _classify_family(item: dict) -> str | None:
    """Classify an edition item into a document family, or None."""
    # Check external domain
    ext_uri = item.get("external_uri") or item.get("canonical_url") or ""
    domain = ""
    if ext_uri:
        try:
            d = urlparse(ext_uri).hostname or ""
            if d.startswith("www."):
                d = d[4:]
            domain = d.lower()
        except Exception:
            pass

    for family, (domains, _, _) in FAMILIES.items():
        if domain in domains:
            return family
        for fd in domains:
            if domain.endswith("." + fd):
                return family

    # Check reasons for domain tags
    reasons = item.get("reasons", [])
    for r in reasons:
        if r.startswith("domain:"):
            d = r.split(":")[1] if len(r.split(":")) > 1 else ""
            for family, (domains, _, _) in FAMILIES.items():
                if d in domains:
                    return family

    # Check author handle for known trackers
    handle = item.get("author_handle", "")
    if "congresstracker" in handle or "congress" in handle.lower():
        return "congress"

    return None


def _is_strong_story(item: dict) -> bool:
    """Check if a document item has enough uptake to stay as a full story."""
    post_count = item.get("post_count", 1)
    unique_authors = item.get("unique_authors", 1)
    # Strong cluster: multiple posts from multiple authors
    if post_count >= 3 and unique_authors >= 2:
        return True
    # Graph signal: post from a mutual/follow, not just an outsider break-in
    reasons = item.get("reasons", [])
    is_graph = any(r in reasons for r in ("mutual", "followed", "follower", "trusted_list"))
    if is_graph:
        return True
    # High individual score suggests substance/uptake
    if item.get("score", 0) > 6:
        return True
    return False


def compact_dockets(items: list[dict]) -> list[dict]:
    """Compact document floods into docket cards.

    Takes edition items (post-cluster), returns modified list where
    document floods are replaced with docket bundle cards.

    Rules:
    - Classify each item into a document family (congress, research, filings)
    - If a family has >= COMPACT_THRESHOLD items:
      - Keep the best one as a full story (+ any strong stories)
      - Compact the rest into one docket card
    - Docket card replaces the compacted items in the list
    """
    # Classify items
    family_items: dict[str, list[tuple[int, dict]]] = {}
    for i, item in enumerate(items):
        family = _classify_family(item)
        if family:
            family_items.setdefault(family, []).append((i, item))

    # Determine which indices to compact
    compacted_indices = set()
    docket_cards = []

    for family, members in family_items.items():
        if len(members) < COMPACT_THRESHOLD:
            continue  # Not enough to compact

        domains, title, subtitle_tpl = FAMILIES[family]

        # Sort by score descending
        members.sort(key=lambda x: x[1].get("score", 0), reverse=True)

        # Keep the best item as full story, plus any strong stories
        keep = []
        compact = []
        for idx, item in members:
            if not keep or _is_strong_story(item):
                keep.append((idx, item))
            else:
                compact.append((idx, item))

        if len(compact) < 2:
            continue  # Not worth compacting just 1 item

        # Mark compacted items for removal
        for idx, _ in compact:
            compacted_indices.add(idx)

        # Build docket card
        lead = compact[0][1]  # Best of the compacted items
        count = len(compact)
        docket_card = {
            # Display fields
            "uri": lead.get("uri", ""),
            "web_url": lead.get("web_url", ""),
            "author_did": "",
            "author_handle": "",
            "author_display_name": "",
            "text": "",
            "display_headline": title,
            "created_at": lead.get("created_at", ""),
            "external_uri": lead.get("external_uri"),
            "external_title": None,
            "langs": [],
            "visible": True,
            # Docket-specific
            "is_docket": True,
            "docket_family": family,
            "docket_title": title,
            "docket_subtitle": subtitle_tpl.format(count=count),
            "docket_count": count,
            "docket_members": [
                {
                    "headline": item.get("display_headline", item.get("text", ""))[:80],
                    "web_url": item.get("web_url", ""),
                    "author_handle": item.get("author_handle", ""),
                }
                for _, item in compact[:5]  # Show top 5 in the card
            ],
            # Scoring
            "score": lead.get("score", 0) * 0.8,  # Slightly below lead full story
            "reasons": lead.get("reasons", []),
            "cluster_type": "docket",
            "post_count": count,
            "unique_authors": len({item.get("author_did") for _, item in compact}),
        }
        docket_cards.append(docket_card)

        LOG.info(
            "compacted %s docket: %d items -> 1 card (kept %d full stories)",
            family, count, len(keep),
        )

    # Rebuild items list: remove compacted, insert docket cards
    result = [item for i, item in enumerate(items) if i not in compacted_indices]

    # Insert docket cards after the last kept item from their family
    # (or at the end if no family items remain)
    for card in docket_cards:
        # Find a good insertion point — after source items, before wire
        inserted = False
        for i, item in enumerate(result):
            if item.get("score", 0) < card.get("score", 0):
                result.insert(i, card)
                inserted = True
                break
        if not inserted:
            result.append(card)

    return result
