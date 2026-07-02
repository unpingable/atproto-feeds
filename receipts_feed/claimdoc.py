"""Structural claim compiler for the Receipts ledger.

This is a "stupid compiler." It NEVER reads the meaning of a post. It only
checks whether a post structurally carries a *settleable apparatus* — an
external source a reader could consult, plus custody over it. It decides
admissibility, never truth. The public output is always "Truth: unknown."

    compile pass   != claim is true
    compile pass   =  the post structurally carries a settleable apparatus
    seal           != claim settled  (only: this claim + custody, content-addressed)
    admissibility  != truth, != popularity, != authorization

The vocabulary rhymes with two sibling projects (claimc: compile/seal/score +
E-codes; claimdocs: claim_mode/basis/adequacy/freshness + fail-closed render)
but imports neither — see specs/claim-ledger/vocabulary.md. Like source_class.py,
this module is intentionally a plain registry of pure functions: easy to read,
no clever classification, no network, no DB.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from . import domains as _domains
from .source_class import (
    SOURCE_CLASS_CODE,
    SOURCE_CLASS_FILING,
    SOURCE_CLASS_PAPER,
    SOURCE_CLASS_REGULATION,
    SOURCE_CLASS_REPORTING,
    SOURCE_CLASS_WIRE,
)

# --- claim modes (claimdocs vocabulary) ------------------------------------
CLAIM_MODE_SOURCED = "sourced"        # primary source, basis resolved  (strong)
CLAIM_MODE_REPORTED = "reported"      # reporting/wire, basis resolved   (strong)
CLAIM_MODE_CARRIER = "carrier"        # external link but basis unresolved / platform-only
CLAIM_MODE_UNCOMPILED = "uncompiled"  # no external source -> negative-results surface

# The fail-closed render gate: only these render as a claim with receipts.
STRONG_MODES = frozenset({CLAIM_MODE_SOURCED, CLAIM_MODE_REPORTED})

# --- basis kinds (what the claim's custody rests on) -----------------------
BASIS_PRIMARY = "primary_source"      # filing/regulation/paper/code
BASIS_REPORTING = "reporting"         # reporting/wire
BASIS_CARRIER = "carrier_only"
BASIS_NONE = "none"

# --- reject codes (claimc idiom) -------------------------------------------
# E001/E006 produce a Rejection; E004/E005 are recorded on a (non-strong) ClaimDoc.
E001_NO_ASSERTION = "E001_NO_ASSERTION"            # predicate #1: empty text
E004_MISSING_SETTLEMENT = "E004_MISSING_SETTLEMENT"  # predicate #2: no external source / platform-only
E005_UNRESOLVED_BASIS = "E005_UNRESOLVED_BASIS"    # predicate #3: fetch != 200
E006_UNSETTLEABLE_CLASS = "E006_UNSETTLEABLE_CLASS"  # predicate #4: unknown/graph_note/wire_blip

# Source classes that could carry a settleable claim. Imported, not re-declared.
PRIMARY_CLASSES = frozenset({
    SOURCE_CLASS_FILING,
    SOURCE_CLASS_REGULATION,
    SOURCE_CLASS_PAPER,
    SOURCE_CLASS_CODE,
})
SETTLEABLE_CLASSES = PRIMARY_CLASSES | frozenset({
    SOURCE_CLASS_REPORTING,
    SOURCE_CLASS_WIRE,
})

# Human-readable reason strings for the negative surface (Phase 4).
REJECT_REASONS = {
    E001_NO_ASSERTION: "No assertion in post",
    E004_MISSING_SETTLEMENT: "No primary source",
    E005_UNRESOLVED_BASIS: "Source unreachable",
    E006_UNSETTLEABLE_CLASS: "Not a settleable source",
}


@dataclass(frozen=True)
class ClaimDoc:
    claim_id: str          # == seal_digest
    claim_mode: str        # sourced | reported | carrier | uncompiled
    basis_kind: str
    basis_resolved: bool   # fail-closed render gate
    reject_code: str | None
    text_carried: str      # verbatim, never parsed
    canonical_url: str
    post_uri: str
    source_domain: str
    source_class: str
    fetch_status: int | None
    fetched_at: str | None
    adequacy: str          # structural: adequate | thin | absent
    freshness: str         # fresh | recent | stale | unknown
    seal_digest: str
    admissibility: float   # NOT truth, NOT popularity
    compiled_at: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Rejection:
    reject_code: str
    reject_reason: str
    post_uri: str
    canonical_url: str

    def to_dict(self) -> dict:
        return asdict(self)


def seal_digest(text, canonical_url, post_uri, fetch_status, fetched_at, source_domain) -> str:
    """Content-address the claim + its custody snapshot.

    Reuses the urls.url_key hash shape (sha256 hex, 16 chars). The same claim
    re-seals identically across editions unless the custody snapshot changes.
    """
    payload = "\x1f".join([
        text or "",
        canonical_url or "",
        post_uri or "",
        str(fetch_status),
        fetched_at or "",
        source_domain or "",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _admissibility(mode: str, basis_resolved: bool, unique_authors: int) -> float:
    """Structural admissibility weight in [0, 1]. Not truth, not popularity.

    Base is the mode's structural strength; a small corroboration term rewards
    independent authors carrying the same source. Deterministic and pinned by
    tests so it can never drift into looking like a truth score.
    """
    base = {
        CLAIM_MODE_SOURCED: 0.70,
        CLAIM_MODE_REPORTED: 0.50,
        CLAIM_MODE_CARRIER: 0.20,
        CLAIM_MODE_UNCOMPILED: 0.0,
    }.get(mode, 0.0)
    corro = 0.0
    if basis_resolved:
        corro = min(max(unique_authors - 1, 0), 4) * 0.05  # up to +0.20
    return round(min(base + corro, 1.0), 3)


def _adequacy(basis_resolved: bool, unique_authors: int) -> str:
    if not basis_resolved:
        return "absent"
    return "adequate" if unique_authors >= 2 else "thin"


def _freshness(fetched_at: str | None, now: datetime) -> str:
    if not fetched_at:
        return "unknown"
    try:
        ts = datetime.fromisoformat(fetched_at)
    except (ValueError, TypeError):
        return "unknown"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_h = (now - ts).total_seconds() / 3600.0
    if age_h < 24:
        return "fresh"
    if age_h < 24 * 7:
        return "recent"
    return "stale"


def compile_claim(item: dict, url_meta: dict | None, *, now: datetime | None = None):
    """Compile one edition item into a ClaimDoc / Rejection, or None to skip.

    Pure and structural. `item` is the edition-time item dict; `url_meta` is its
    url_metadata row (or None). Returns:
      - None       for docket bundles (skip, not a claim)
      - Rejection  for E001 (no assertion) / E006 (unsettleable class)
      - ClaimDoc   otherwise (mode encodes sourced/reported/carrier/uncompiled)
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Docket bundles carry no single assertion — skip, don't fail.
    if item.get("is_docket"):
        return None

    post_uri = item.get("uri") or ""
    canonical_url = item.get("canonical_url") or item.get("external_uri") or ""
    source_class = item.get("source_class") or ""
    source_domain = (item.get("source_domain") or (url_meta or {}).get("domain") or "")
    text = (item.get("text") or "")
    unique_authors = int(item.get("unique_authors") or 1)

    # #1 assertion present
    if not text.strip():
        return Rejection(
            reject_code=E001_NO_ASSERTION,
            reject_reason=REJECT_REASONS[E001_NO_ASSERTION],
            post_uri=post_uri,
            canonical_url=canonical_url,
        )

    fetch_status = (url_meta or {}).get("fetch_status")
    fetched_at = (url_meta or {}).get("fetched_at")

    def _doc(mode, basis_kind, basis_resolved, reject_code):
        digest = seal_digest(text, canonical_url, post_uri, fetch_status, fetched_at, source_domain)
        return ClaimDoc(
            claim_id=digest,
            claim_mode=mode,
            basis_kind=basis_kind,
            basis_resolved=basis_resolved,
            reject_code=reject_code,
            text_carried=text,
            canonical_url=canonical_url,
            post_uri=post_uri,
            source_domain=source_domain,
            source_class=source_class,
            fetch_status=fetch_status,
            fetched_at=fetched_at,
            adequacy=_adequacy(basis_resolved, unique_authors),
            freshness=_freshness(fetched_at, now),
            seal_digest=digest,
            admissibility=_admissibility(mode, basis_resolved, unique_authors),
            compiled_at=now.isoformat(),
        )

    # #2 external source present (non-platform)
    if not canonical_url or _domains.is_platform_domain(source_domain):
        return _doc(CLAIM_MODE_UNCOMPILED, BASIS_NONE, False, E004_MISSING_SETTLEMENT)

    # #4 settleable source class
    if source_class not in SETTLEABLE_CLASSES:
        return Rejection(
            reject_code=E006_UNSETTLEABLE_CLASS,
            reject_reason=REJECT_REASONS[E006_UNSETTLEABLE_CLASS],
            post_uri=post_uri,
            canonical_url=canonical_url,
        )

    # #3 custody resolved (basis actually reachable)
    basis_resolved = bool(url_meta) and fetch_status == 200 and bool(fetched_at)
    if not basis_resolved:
        # Fail-closed: external settleable source but basis unresolved -> carrier.
        return _doc(CLAIM_MODE_CARRIER, BASIS_CARRIER, False, E005_UNRESOLVED_BASIS)

    if source_class in PRIMARY_CLASSES:
        return _doc(CLAIM_MODE_SOURCED, BASIS_PRIMARY, True, None)
    return _doc(CLAIM_MODE_REPORTED, BASIS_REPORTING, True, None)
