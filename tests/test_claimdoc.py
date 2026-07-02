"""Tests for receipts_feed.claimdoc — the structural claim compiler.

Pure unit tests, inline-dict fixtures (no fixture files), following the
test_source_class.py idiom. Heavy on the negative cases: carrier, uncompiled,
rejections, and the fail-closed guarantee.
"""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from receipts_feed.claimdoc import (
    BASIS_CARRIER,
    BASIS_NONE,
    BASIS_PRIMARY,
    BASIS_REPORTING,
    CLAIM_MODE_CARRIER,
    CLAIM_MODE_REPORTED,
    CLAIM_MODE_SOURCED,
    CLAIM_MODE_UNCOMPILED,
    E001_NO_ASSERTION,
    E004_MISSING_SETTLEMENT,
    E005_UNRESOLVED_BASIS,
    E006_UNSETTLEABLE_CLASS,
    STRONG_MODES,
    ClaimDoc,
    Rejection,
    carries_settleable_source,
    compile_claim,
    seal_digest,
)
from receipts_feed.claimdoc import (
    FAIL_FETCH_ERROR,
    FAIL_FETCH_PENDING,
    FAIL_FETCH_UNREACHABLE,
    FAIL_HANDLER_MISSING,
    FAIL_NO_PRIMARY_SOURCE,
    FAIL_SOURCE_BLOCKED,
    FAILURE_CLASS_NONE,
    FAILURE_CLASS_STRUCTURAL,
    FAILURE_CLASS_TOOLING,
)
from receipts_feed.source_class import (
    SOURCE_CLASS_FILING,
    SOURCE_CLASS_GRAPH_NOTE,
    SOURCE_CLASS_REPORTING,
    SOURCE_CLASS_UNKNOWN,
)

NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)
FRESH = "2026-07-02T10:00:00+00:00"


def _item(**over):
    base = {
        "uri": "at://did:plc:abc/app.bsky.feed.post/1",
        "canonical_url": "https://courtlistener.com/docket/123/",
        "external_uri": "https://courtlistener.com/docket/123/",
        "source_class": SOURCE_CLASS_FILING,
        "source_domain": "courtlistener.com",
        "text": "The court granted the motion to dismiss.",
        "unique_authors": 1,
    }
    base.update(over)
    return base


def _meta(**over):
    base = {"fetch_status": 200, "fetched_at": FRESH, "domain": "courtlistener.com",
            "og_title": "Order — Docket 123"}
    base.update(over)
    return base


class TestCompilesSourced:
    def test_primary_source_resolved(self):
        doc = compile_claim(_item(), _meta(), now=NOW)
        assert isinstance(doc, ClaimDoc)
        assert doc.claim_mode == CLAIM_MODE_SOURCED
        assert doc.basis_kind == BASIS_PRIMARY
        assert doc.basis_resolved is True
        assert doc.reject_code is None
        assert doc.claim_id == doc.seal_digest

    def test_text_carried_verbatim(self):
        doc = compile_claim(_item(text="  Exactly this.  "), _meta(), now=NOW)
        assert doc.text_carried == "  Exactly this.  "


class TestCompilesReported:
    def test_reporting_domain_resolved(self):
        doc = compile_claim(
            _item(source_class=SOURCE_CLASS_REPORTING, source_domain="nytimes.com",
                  canonical_url="https://nytimes.com/x"),
            _meta(domain="nytimes.com"), now=NOW)
        assert doc.claim_mode == CLAIM_MODE_REPORTED
        assert doc.basis_kind == BASIS_REPORTING
        assert doc.basis_resolved is True


class TestCarrier:
    def test_fetch_403_is_carrier(self):
        doc = compile_claim(_item(), _meta(fetch_status=403), now=NOW)
        assert doc.claim_mode == CLAIM_MODE_CARRIER
        assert doc.basis_kind == BASIS_CARRIER
        assert doc.basis_resolved is False
        assert doc.reject_code == E005_UNRESOLVED_BASIS

    def test_no_url_meta_is_carrier(self):
        doc = compile_claim(_item(), None, now=NOW)
        assert doc.claim_mode == CLAIM_MODE_CARRIER
        assert doc.basis_resolved is False

    def test_missing_fetched_at_is_carrier(self):
        doc = compile_claim(_item(), _meta(fetched_at=None), now=NOW)
        assert doc.claim_mode == CLAIM_MODE_CARRIER


class TestUncompiled:
    def test_no_external_source(self):
        doc = compile_claim(_item(canonical_url="", external_uri=""), _meta(), now=NOW)
        assert doc.claim_mode == CLAIM_MODE_UNCOMPILED
        assert doc.basis_kind == BASIS_NONE
        assert doc.reject_code == E004_MISSING_SETTLEMENT

    def test_platform_only_is_uncompiled(self):
        doc = compile_claim(
            _item(canonical_url="https://bsky.app/profile/x/post/1",
                  source_domain="bsky.app"),
            None, now=NOW)
        assert doc.claim_mode == CLAIM_MODE_UNCOMPILED
        assert doc.reject_code == E004_MISSING_SETTLEMENT


class TestRejections:
    def test_empty_text_rejects(self):
        rej = compile_claim(_item(text="   "), _meta(), now=NOW)
        assert isinstance(rej, Rejection)
        assert rej.reject_code == E001_NO_ASSERTION

    def test_unsettleable_class_rejects(self):
        for cls in (SOURCE_CLASS_UNKNOWN, SOURCE_CLASS_GRAPH_NOTE):
            rej = compile_claim(
                _item(source_class=cls, source_domain="example.com",
                      canonical_url="https://example.com/x"),
                _meta(domain="example.com"), now=NOW)
            assert isinstance(rej, Rejection)
            assert rej.reject_code == E006_UNSETTLEABLE_CLASS


class TestDocketSkipped:
    def test_docket_returns_none(self):
        assert compile_claim(_item(is_docket=True, text=""), None, now=NOW) is None


class TestSealStability:
    def test_identical_inputs_same_id(self):
        a = compile_claim(_item(), _meta(), now=NOW)
        b = compile_claim(_item(), _meta(), now=NOW)
        assert a.claim_id == b.claim_id

    def test_changed_custody_changes_digest(self):
        a = compile_claim(_item(), _meta(), now=NOW)
        b = compile_claim(_item(), _meta(fetched_at="2026-07-01T09:00:00+00:00"), now=NOW)
        assert a.claim_id != b.claim_id

    def test_seal_digest_is_16_hex(self):
        d = seal_digest("t", "u", "p", 200, FRESH, "d")
        assert len(d) == 16
        int(d, 16)  # raises if not hex


class TestFailClosed:
    def test_strong_mode_never_emitted_when_fetch_not_200(self):
        # A primary-source item that would be `sourced` — but the fetch failed.
        for status in (None, 403, 500, 302):
            doc = compile_claim(_item(), _meta(fetch_status=status), now=NOW)
            assert doc.claim_mode not in STRONG_MODES
            assert doc.basis_resolved is False


class TestAdmissibility:
    def test_ordering_sourced_over_reported_over_carrier(self):
        sourced = compile_claim(_item(), _meta(), now=NOW)
        reported = compile_claim(
            _item(source_class=SOURCE_CLASS_REPORTING, source_domain="nytimes.com",
                  canonical_url="https://nytimes.com/x"),
            _meta(domain="nytimes.com"), now=NOW)
        carrier = compile_claim(_item(), _meta(fetch_status=403), now=NOW)
        assert sourced.admissibility > reported.admissibility > carrier.admissibility

    def test_corroboration_raises_admissibility(self):
        solo = compile_claim(_item(unique_authors=1), _meta(), now=NOW)
        crowd = compile_claim(_item(unique_authors=5), _meta(), now=NOW)
        assert crowd.admissibility > solo.admissibility

    def test_admissibility_never_exceeds_one(self):
        doc = compile_claim(_item(unique_authors=99), _meta(), now=NOW)
        assert doc.admissibility <= 1.0


class TestFreshness:
    def test_fresh_recent_stale_unknown(self):
        assert compile_claim(_item(), _meta(fetched_at=FRESH), now=NOW).freshness == "fresh"
        assert compile_claim(_item(), _meta(fetched_at="2026-06-29T12:00:00+00:00"),
                             now=NOW).freshness == "recent"
        assert compile_claim(_item(), _meta(fetched_at="2026-06-01T12:00:00+00:00"),
                             now=NOW).freshness == "stale"


class TestFailureTaxonomy:
    def test_handleable_domain_block_is_handler_missing(self):
        # CourtListener 403 -> tooling coverage gap, NOT a claim failure.
        doc = compile_claim(
            _item(source_domain="courtlistener.com",
                  canonical_url="https://courtlistener.com/docket/1/"),
            _meta(fetch_status=403, domain="courtlistener.com"), now=NOW)
        assert doc.claim_mode == CLAIM_MODE_CARRIER
        assert doc.basis_failure == FAIL_HANDLER_MISSING
        assert doc.failure_class == FAILURE_CLASS_TOOLING

    def test_paywall_block_is_source_blocked(self):
        doc = compile_claim(
            _item(source_class=SOURCE_CLASS_REPORTING, source_domain="reuters.com",
                  canonical_url="https://reuters.com/x"),
            _meta(fetch_status=401, domain="reuters.com"), now=NOW)
        assert doc.basis_failure == FAIL_SOURCE_BLOCKED
        assert doc.failure_class == FAILURE_CLASS_TOOLING

    def test_network_fail_is_unreachable(self):
        # Has a metadata row, status None -> we fetched and failed.
        doc = compile_claim(_item(), _meta(fetch_status=None), now=NOW)
        assert doc.basis_failure == FAIL_FETCH_UNREACHABLE

    def test_no_metadata_is_pending(self):
        # No metadata row -> we haven't looked yet (not "unreachable").
        doc = compile_claim(_item(), None, now=NOW)
        assert doc.basis_failure == FAIL_FETCH_PENDING
        assert doc.failure_class == FAILURE_CLASS_TOOLING

    def test_other_status_is_fetch_error(self):
        doc = compile_claim(_item(), _meta(fetch_status=404), now=NOW)
        assert doc.basis_failure == FAIL_FETCH_ERROR

    def test_uncompiled_is_structural(self):
        doc = compile_claim(_item(canonical_url="", external_uri=""), None, now=NOW)
        assert doc.basis_failure == FAIL_NO_PRIMARY_SOURCE
        assert doc.failure_class == FAILURE_CLASS_STRUCTURAL

    def test_sourced_has_no_failure(self):
        doc = compile_claim(_item(), _meta(), now=NOW)
        assert doc.basis_failure == ""
        assert doc.failure_class == FAILURE_CLASS_NONE

    def test_rejection_is_structural(self):
        rej = compile_claim(
            _item(source_class=SOURCE_CLASS_UNKNOWN, source_domain="example.com",
                  canonical_url="https://example.com/x"),
            _meta(domain="example.com"), now=NOW)
        assert isinstance(rej, Rejection)
        assert rej.failure_class == FAILURE_CLASS_STRUCTURAL


class TestCarriesSettleableSource:
    def test_settleable_domains_true(self):
        assert carries_settleable_source("reuters.com")        # wire
        assert carries_settleable_source("courtlistener.com")  # filing
        assert carries_settleable_source("arxiv.org")          # paper
        assert carries_settleable_source("nytimes.com")        # reporting

    def test_platform_false(self):
        assert not carries_settleable_source("bsky.app")
        assert not carries_settleable_source("twitter.com")

    def test_unknown_and_empty_false(self):
        assert not carries_settleable_source("randomblog.example")
        assert not carries_settleable_source(None)
        assert not carries_settleable_source("")


class TestToDict:
    def test_roundtrip_keys(self):
        d = compile_claim(_item(), _meta(), now=NOW).to_dict()
        assert d["claim_mode"] == CLAIM_MODE_SOURCED
        assert "text_carried" in d and "seal_digest" in d
        assert "basis_failure" in d and "failure_class" in d
