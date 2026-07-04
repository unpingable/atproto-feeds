"""Tests for domain source handlers (PubMed) + og_fetch dispatch."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from receipts_feed import handlers, og_fetch


class TestGetHandler:
    def test_routes_pubmed(self):
        assert handlers.get_handler("pubmed.ncbi.nlm.nih.gov") is handlers.pubmed_handler
        assert handlers.get_handler("www.pubmed.ncbi.nlm.nih.gov") is handlers.pubmed_handler

    def test_non_handled_domain_is_none(self):
        assert handlers.get_handler("nytimes.com") is None
        assert handlers.get_handler(None) is None


class TestPubmedHandler:
    def test_resolves_title_and_journal(self, monkeypatch):
        fake = {"result": {"38743512": {"title": "A study of things.", "source": "J Test"}}}
        monkeypatch.setattr(handlers, "_get_json", lambda url, *, timeout: fake)
        out = handlers.pubmed_handler("https://pubmed.ncbi.nlm.nih.gov/38743512/", timeout=6)
        assert out["fetch_status"] == 200
        assert out["og_title"] == "A study of things"   # trailing dot stripped
        assert out["og_description"] == "J Test"
        assert out["content_type"] == "application/json"

    def test_non_pubmed_url_returns_none(self, monkeypatch):
        monkeypatch.setattr(handlers, "_get_json", lambda url, *, timeout: {})
        assert handlers.pubmed_handler("https://example.com/x", timeout=6) is None

    def test_api_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(handlers, "_get_json", lambda url, *, timeout: None)
        assert handlers.pubmed_handler("https://pubmed.ncbi.nlm.nih.gov/1/", timeout=6) is None

    def test_empty_title_returns_none(self, monkeypatch):
        fake = {"result": {"1": {"title": "", "source": "J"}}}
        monkeypatch.setattr(handlers, "_get_json", lambda url, *, timeout: fake)
        assert handlers.pubmed_handler("https://pubmed.ncbi.nlm.nih.gov/1/", timeout=6) is None


class TestFetchAndStoreDispatch:
    def test_handler_short_circuits_generic_fetch(self, monkeypatch):
        stub = {"final_url": "https://pubmed.ncbi.nlm.nih.gov/1/",
                "content_type": "application/json", "fetch_status": 200,
                "fetch_error": None, "og_title": "Resolved Title",
                "og_description": "J", "og_image": None}
        monkeypatch.setattr(og_fetch.handlers, "get_handler",
                            lambda d: (lambda url, *, timeout: stub))
        called = {}
        monkeypatch.setattr(og_fetch, "_fetch_url",
                            lambda *a, **k: called.setdefault("generic", True))
        captured = {}
        monkeypatch.setattr(og_fetch.db, "upsert_url_metadata",
                            lambda cu, **kw: captured.update(kw))
        out = og_fetch.fetch_and_store("https://pubmed.ncbi.nlm.nih.gov/1/", timeout=6)
        assert out["fetch_status"] == 200
        assert "generic" not in called          # generic fetch was skipped
        assert captured["og_title"] == "Resolved Title"

    def test_falls_back_to_generic_when_no_handler(self, monkeypatch):
        monkeypatch.setattr(og_fetch.handlers, "get_handler", lambda d: None)
        generic = {"final_url": "https://x.com/a", "content_type": "text/html",
                   "fetch_status": 200, "fetch_error": None, "og_title": "G",
                   "og_description": None, "og_image": None}
        monkeypatch.setattr(og_fetch, "_fetch_url", lambda *a, **k: generic)
        monkeypatch.setattr(og_fetch.db, "upsert_url_metadata", lambda cu, **kw: None)
        out = og_fetch.fetch_and_store("https://x.com/a", timeout=6)
        assert out["og_title"] == "G"


class TestInterstitial:
    def test_detected_by_host(self):
        assert og_fetch._is_interstitial("https://unblock.federalregister.gov/x", "anything")

    def test_detected_by_title(self):
        assert og_fetch._is_interstitial("https://www.federalregister.gov/x",
                                         "Federal Register :: Request Access")
        assert og_fetch._is_interstitial("https://x.com/y", "Just a moment...")
        # Nature's Radware bot-challenge page (returns 200 + this title).
        assert og_fetch._is_interstitial("https://www.nature.com/articles/d41", "Client Challenge")

    def test_clean_page_not_interstitial(self):
        assert not og_fetch._is_interstitial("https://reuters.com/x", "A Real Headline")

    def test_fetch_and_store_demotes_false_200(self, monkeypatch):
        monkeypatch.setattr(og_fetch.handlers, "get_handler", lambda d: None)
        monkeypatch.setattr(og_fetch, "_fetch_url", lambda *a, **k: {
            "final_url": "https://unblock.federalregister.gov/x", "content_type": "text/html",
            "fetch_status": 200, "fetch_error": None,
            "og_title": "Federal Register :: Request Access",
            "og_description": None, "og_image": None})
        captured = {}
        monkeypatch.setattr(og_fetch.db, "upsert_url_metadata", lambda cu, **kw: captured.update(kw))
        og_fetch.fetch_and_store("https://www.federalregister.gov/documents/x", timeout=6)
        assert captured["fetch_status"] == 403          # demoted from the false 200
        assert captured["fetch_error"] == "interstitial"
        assert captured["og_title"] is None             # denied title dropped
