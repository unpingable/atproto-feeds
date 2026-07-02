"""Edition-time orchestration for the claim ledger.

Keeps site.py lean and claimdoc.py pure. `resolve_claim_basis` runs the pure
compiler over each edition item, stamps the result onto the item (so it lands
inside the frozen edition's items_json), and returns the collected docs /
rejections for persistence. Stamp-only: it never reorders or drops items, so
hero selection and the existing section grouping are untouched.
"""
from __future__ import annotations

import logging

from .claimdoc import Rejection, compile_claim

LOG = logging.getLogger("receipts.claim_ledger")


def resolve_claim_basis(items: list[dict], url_meta_by_url: dict):
    """Compile each item; stamp item["claim"] / item["claim_rejection"].

    Returns (items, docs, rejections) where docs/rejections are lists of dicts
    ready for db.save_claimdocs once the edition_id exists.
    """
    docs: list[dict] = []
    rejections: list[dict] = []
    for item in items:
        canonical = item.get("canonical_url")
        url_meta = url_meta_by_url.get(canonical) if canonical else None
        try:
            result = compile_claim(item, url_meta)
        except Exception:
            LOG.exception("compile_claim failed for %s (non-fatal)", item.get("uri"))
            continue
        if result is None:
            continue  # docket bundle — skip
        if isinstance(result, Rejection):
            rej = result.to_dict()
            item["claim_rejection"] = rej
            rejections.append(rej)
        else:
            doc = result.to_dict()
            item["claim"] = doc
            docs.append(doc)
    return items, docs, rejections
