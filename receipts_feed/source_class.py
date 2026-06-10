"""Classify a linked URL's domain into a source class.

The source-first pivot reorganizes the homepage by what the linked
content IS — filing, regulation, paper, code, reporting, wire — not by
where the poster sits in the follow graph. Graph position demotes to a
provenance tag.

Used for:
  - story-card section assignment on the homepage
  - tags shown on the story card
  - downstream `From the Graph` notebook treatment for posts without a
    canonical URL

This module is intentionally just a registry — easy to read, easy to
edit, no clever classification. The deeper philosophical "what kind of
thing is this" stays the operator's call, not the machine's.
"""
from __future__ import annotations

from . import domains as _domains


# Source classes:
#   reporting       — journalism with reporting work behind it
#   wire            — agencies whose output reads as headlines + dispatch
#   filing          — court filings, dockets, regulatory comment dockets
#   regulation      — official rule / register / legislative documents
#   paper           — preprints, journals, working papers
#   code            — code repositories, technical docs
#   graph_note      — sentinel for posts WITHOUT a canonical URL (filled
#                     in by the caller, not from a domain map)
#   wire_blip       — short-form / non-source-class posts that we still
#                     want surfaced (graph-only one-liners that pass
#                     the rank threshold)
#   unknown         — domain isn't classified yet


SOURCE_CLASS_REPORTING = "reporting"
SOURCE_CLASS_WIRE = "wire"
SOURCE_CLASS_FILING = "filing"
SOURCE_CLASS_REGULATION = "regulation"
SOURCE_CLASS_PAPER = "paper"
SOURCE_CLASS_CODE = "code"
SOURCE_CLASS_GRAPH_NOTE = "graph_note"
SOURCE_CLASS_WIRE_BLIP = "wire_blip"
SOURCE_CLASS_UNKNOWN = "unknown"


VALID_SOURCE_CLASSES = {
    SOURCE_CLASS_REPORTING,
    SOURCE_CLASS_WIRE,
    SOURCE_CLASS_FILING,
    SOURCE_CLASS_REGULATION,
    SOURCE_CLASS_PAPER,
    SOURCE_CLASS_CODE,
    SOURCE_CLASS_GRAPH_NOTE,
    SOURCE_CLASS_WIRE_BLIP,
    SOURCE_CLASS_UNKNOWN,
}


# Exact-host map. Entries here win over suffix matches.
_DOMAIN_TO_CLASS: dict[str, str] = {
    # Wire services — short, fast, agency
    "apnews.com": SOURCE_CLASS_WIRE,
    "reuters.com": SOURCE_CLASS_WIRE,
    "afp.com": SOURCE_CLASS_WIRE,
    "bloomberg.com": SOURCE_CLASS_WIRE,

    # Reporting — bylined journalism with newsroom infrastructure
    "nytimes.com": SOURCE_CLASS_REPORTING,
    "washingtonpost.com": SOURCE_CLASS_REPORTING,
    "wsj.com": SOURCE_CLASS_REPORTING,
    "theguardian.com": SOURCE_CLASS_REPORTING,
    "bbc.com": SOURCE_CLASS_REPORTING,
    "bbc.co.uk": SOURCE_CLASS_REPORTING,
    "ft.com": SOURCE_CLASS_REPORTING,
    "propublica.org": SOURCE_CLASS_REPORTING,
    "404media.co": SOURCE_CLASS_REPORTING,
    "themarkup.org": SOURCE_CLASS_REPORTING,
    "nbcnews.com": SOURCE_CLASS_REPORTING,
    "cbsnews.com": SOURCE_CLASS_REPORTING,
    "cnn.com": SOURCE_CLASS_REPORTING,
    "vox.com": SOURCE_CLASS_REPORTING,
    "axios.com": SOURCE_CLASS_REPORTING,
    "politico.com": SOURCE_CLASS_REPORTING,
    "theatlantic.com": SOURCE_CLASS_REPORTING,
    "newyorker.com": SOURCE_CLASS_REPORTING,
    "documentcloud.org": SOURCE_CLASS_REPORTING,
    "motherjones.com": SOURCE_CLASS_REPORTING,
    "talkingpointsmemo.com": SOURCE_CLASS_REPORTING,

    # Filings — court documents, dockets, comment systems
    "courtlistener.com": SOURCE_CLASS_FILING,
    "storage.courtlistener.com": SOURCE_CLASS_FILING,
    "pacer.gov": SOURCE_CLASS_FILING,
    "supremecourt.gov": SOURCE_CLASS_FILING,
    "uscourts.gov": SOURCE_CLASS_FILING,
    "regulations.gov": SOURCE_CLASS_FILING,

    # Regulation — rule-making, register, legislative
    "federalregister.gov": SOURCE_CLASS_REGULATION,
    "congress.gov": SOURCE_CLASS_REGULATION,
    "sec.gov": SOURCE_CLASS_REGULATION,
    "gao.gov": SOURCE_CLASS_REGULATION,
    "cdc.gov": SOURCE_CLASS_REGULATION,
    "fda.gov": SOURCE_CLASS_REGULATION,
    "epa.gov": SOURCE_CLASS_REGULATION,
    "ftc.gov": SOURCE_CLASS_REGULATION,
    "treasury.gov": SOURCE_CLASS_REGULATION,
    "state.gov": SOURCE_CLASS_REGULATION,
    "whitehouse.gov": SOURCE_CLASS_REGULATION,
    "justice.gov": SOURCE_CLASS_REGULATION,

    # Papers — preprints, journals
    "arxiv.org": SOURCE_CLASS_PAPER,
    "nature.com": SOURCE_CLASS_PAPER,
    "science.org": SOURCE_CLASS_PAPER,
    "scholar.google.com": SOURCE_CLASS_PAPER,
    "pubmed.ncbi.nlm.nih.gov": SOURCE_CLASS_PAPER,
    "ncbi.nlm.nih.gov": SOURCE_CLASS_PAPER,
    "biorxiv.org": SOURCE_CLASS_PAPER,
    "medrxiv.org": SOURCE_CLASS_PAPER,
    "ssrn.com": SOURCE_CLASS_PAPER,
    "papers.ssrn.com": SOURCE_CLASS_PAPER,
    "psyarxiv.com": SOURCE_CLASS_PAPER,
    "lancet.com": SOURCE_CLASS_PAPER,
    "nejm.org": SOURCE_CLASS_PAPER,
    "pnas.org": SOURCE_CLASS_PAPER,

    # Code / technical
    "github.com": SOURCE_CLASS_CODE,
    "gitlab.com": SOURCE_CLASS_CODE,
    "codeberg.org": SOURCE_CLASS_CODE,
    "docs.python.org": SOURCE_CLASS_CODE,
    "developer.mozilla.org": SOURCE_CLASS_CODE,
    "rfc-editor.org": SOURCE_CLASS_CODE,
    "w3.org": SOURCE_CLASS_CODE,
    "ietf.org": SOURCE_CLASS_CODE,
    "datatracker.ietf.org": SOURCE_CLASS_CODE,
    "stackoverflow.com": SOURCE_CLASS_CODE,
}


# Domain SUFFIX → class. Used when the exact host is unmapped but a known
# parent domain matches. Specific entries above always win.
_SUFFIX_TO_CLASS: list[tuple[str, str]] = [
    (".gov", SOURCE_CLASS_REGULATION),       # default .gov → regulation
    (".uscourts.gov", SOURCE_CLASS_FILING),  # but court subdomains → filing
    (".regulations.gov", SOURCE_CLASS_FILING),
    (".edu", SOURCE_CLASS_PAPER),            # generic university hosts
]


def classify_domain(domain: str | None) -> str:
    """Map a bare domain to a source class.

    Resolution order:
      1. exact match in _DOMAIN_TO_CLASS
      2. parent-host roll-up: strip one subdomain label at a time and
         re-check (so `projects.propublica.org` → `propublica.org` →
         REPORTING). This catches publisher subdomain trees without
         requiring an entry per subdomain.
      3. generic suffix table (`.gov` → regulation, `.edu` → paper)

    Platform domains (twitter, bsky, t.co) → SOURCE_CLASS_UNKNOWN; they're
    not stories. Unmapped domains also → SOURCE_CLASS_UNKNOWN so the
    operator can spot what's missing in the live edition.
    """
    if not domain:
        return SOURCE_CLASS_UNKNOWN
    d = domain.lower().strip()
    if d.startswith("www."):
        d = d[4:]
    if _domains.is_platform_domain(d):
        return SOURCE_CLASS_UNKNOWN
    # Exact match
    if d in _DOMAIN_TO_CLASS:
        return _DOMAIN_TO_CLASS[d]
    # Parent host roll-up: peel labels left-to-right until we hit a match
    # or run out. (Capped to 4 peels so pathological hosts don't loop.)
    parts = d.split(".")
    for i in range(1, min(len(parts), 4)):
        parent = ".".join(parts[i:])
        if parent in _DOMAIN_TO_CLASS:
            return _DOMAIN_TO_CLASS[parent]
    # Generic suffix table fallback (longest match wins)
    best = (SOURCE_CLASS_UNKNOWN, -1)
    for suffix, cls in _SUFFIX_TO_CLASS:
        if d.endswith(suffix) and len(suffix) > best[1]:
            best = (cls, len(suffix))
    return best[0]


def section_for(source_class: str) -> str:
    """Map a source class to a homepage section label.

    Sections are how the homepage groups stories. This is the labelling
    layer between the deterministic classifier and the editorial copy
    operator-curated in the templates.
    """
    return {
        SOURCE_CLASS_REPORTING: "Reporting",
        SOURCE_CLASS_WIRE: "Wire",
        SOURCE_CLASS_FILING: "Filings",
        SOURCE_CLASS_REGULATION: "Regulation",
        SOURCE_CLASS_PAPER: "Papers",
        SOURCE_CLASS_CODE: "Code & Standards",
        SOURCE_CLASS_GRAPH_NOTE: "From the Graph",
        SOURCE_CLASS_WIRE_BLIP: "Wire",
        SOURCE_CLASS_UNKNOWN: "Wire",
    }.get(source_class, "Wire")
