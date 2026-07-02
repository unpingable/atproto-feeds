"""Tests for the claim-ledger edition-time layer + persistence."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from receipts_feed import claim_ledger, db, site
from receipts_feed.claimdoc import (
    CLAIM_MODE_CARRIER,
    CLAIM_MODE_SOURCED,
    CLAIM_MODE_UNCOMPILED,
    E004_MISSING_SETTLEMENT,
    E005_UNRESOLVED_BASIS,
    compile_claim,
)
from receipts_feed.source_class import SOURCE_CLASS_FILING, SOURCE_CLASS_UNKNOWN

NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)
FRESH = "2026-07-02T10:00:00+00:00"


def _sourced_item(uri="at://did/post/1"):
    return {
        "uri": uri,
        "canonical_url": "https://courtlistener.com/docket/1/",
        "source_class": SOURCE_CLASS_FILING,
        "source_domain": "courtlistener.com",
        "text": "The court granted the motion.",
        "unique_authors": 2,
    }


def _meta():
    return {"fetch_status": 200, "fetched_at": FRESH, "domain": "courtlistener.com"}


class TestResolveClaimBasis:
    def test_stamps_claim_reject_and_skips_docket(self):
        compiling = _sourced_item("at://did/post/1")
        rejected = {
            "uri": "at://did/post/2", "canonical_url": "https://example.com/x",
            "source_class": SOURCE_CLASS_UNKNOWN, "source_domain": "example.com",
            "text": "some commentary", "unique_authors": 1,
        }
        docket = {"uri": "at://did/post/3", "is_docket": True, "text": ""}
        items = [compiling, rejected, docket]
        url_meta_by_url = {"https://courtlistener.com/docket/1/": _meta()}

        out, docs, rejections = claim_ledger.resolve_claim_basis(items, url_meta_by_url)

        assert out is items  # stamp-only, same list, same order
        assert compiling["claim"]["claim_mode"] == CLAIM_MODE_SOURCED
        assert rejected["claim_rejection"]["reject_code"] == "E006_UNSETTLEABLE_CLASS"
        assert "claim" not in docket and "claim_rejection" not in docket
        assert len(docs) == 1 and len(rejections) == 1


class TestPersistence:
    @pytest.fixture(autouse=True)
    def temp_db(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.sqlite"))
        db.init_db()

    def test_save_and_get_roundtrip(self):
        ed = db.save_edition("receipts", [{"uri": "at://did/post/1"}], {"total": 1}, 0)
        doc = compile_claim(_sourced_item(), _meta(), now=NOW).to_dict()
        db.save_claimdocs(ed, [doc], [])
        got = db.get_claimdocs_for_edition(ed)
        assert len(got) == 1
        assert got[0]["claim_mode"] == CLAIM_MODE_SOURCED
        assert got[0]["basis_resolved"] is True  # int -> bool roundtrip
        assert got[0]["admissibility"] == doc["admissibility"]

    def test_orphan_rows_pruned(self):
        # An edition_id with no editions row should be pruned on save.
        doc = compile_claim(_sourced_item(), _meta(), now=NOW).to_dict()
        db.save_claimdocs("no-such-edition", [doc], [])
        assert db.get_claimdocs_for_edition("no-such-edition") == []

    def test_rejections_roundtrip(self):
        ed = db.save_edition("receipts", [{"uri": "at://did/post/1"}], {"total": 1}, 0)
        rej = {"post_uri": "at://did/post/9", "canonical_url": "https://example.com/x",
               "reject_code": "E006_UNSETTLEABLE_CLASS", "reject_reason": "Not a settleable source"}
        db.save_claimdocs(ed, [], [rej])
        got = db.get_claim_rejections_for_edition(ed)
        assert len(got) == 1 and got[0]["reject_reason"] == "Not a settleable source"


class TestNegativeSurface:
    def test_collects_carrier_uncompiled_rejection_not_strong(self):
        items = [
            {"source_domain": "courtlistener.com", "display_headline": "sourced one",
             "claim": {"claim_mode": CLAIM_MODE_SOURCED, "basis_resolved": True}},
            {"source_domain": "courtlistener.com", "display_headline": "carrier one",
             "claim": {"claim_mode": CLAIM_MODE_CARRIER, "reject_code": E005_UNRESOLVED_BASIS}},
            {"source_domain": "bsky.app", "text": "just vibes",
             "claim": {"claim_mode": CLAIM_MODE_UNCOMPILED, "reject_code": E004_MISSING_SETTLEMENT}},
            {"source_domain": "example.com", "display_headline": "rej",
             "claim_rejection": {"reject_code": "E006_UNSETTLEABLE_CLASS",
                                 "reject_reason": "Not a settleable source"}},
        ]
        rows = site._collect_uncompiled(items)
        reasons = {r["reason"] for r in rows}
        assert len(rows) == 3  # the sourced item is excluded
        assert "Source unreachable" in reasons      # E005
        assert "No primary source" in reasons       # E004
        assert "Not a settleable source" in reasons  # hard rejection


class TestDebugClaimsEndpoint:
    @pytest.fixture(autouse=True)
    def temp_db(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.sqlite"))
        db.init_db()

    def test_groups_by_mode_with_rejection_count(self):
        import asyncio

        from receipts_feed import api

        ed = db.save_edition("receipts", [{"uri": "x"}], {"total": 1}, 0)
        sourced = compile_claim(_sourced_item("at://did/post/1"), _meta(), now=NOW).to_dict()
        carrier = compile_claim(
            _sourced_item("at://did/post/2"),
            {"fetch_status": 403, "fetched_at": FRESH, "domain": "courtlistener.com"},
            now=NOW).to_dict()
        rej = {"post_uri": "at://did/post/3", "canonical_url": "",
               "reject_code": "E006_UNSETTLEABLE_CLASS", "reject_reason": "Not a settleable source"}
        db.save_claimdocs(ed, [sourced, carrier], [rej])

        result = asyncio.run(api.debug_claims())
        assert result["edition_id"] == ed
        assert result["total"] == 2
        assert result["counts"].get(CLAIM_MODE_SOURCED) == 1
        assert result["counts"].get(CLAIM_MODE_CARRIER) == 1
        assert result["counts"].get("rejected") == 1
