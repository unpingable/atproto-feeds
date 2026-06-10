"""Tests for receipts_feed.source_class."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from receipts_feed.source_class import (
    SOURCE_CLASS_CODE,
    SOURCE_CLASS_FILING,
    SOURCE_CLASS_PAPER,
    SOURCE_CLASS_REGULATION,
    SOURCE_CLASS_REPORTING,
    SOURCE_CLASS_UNKNOWN,
    SOURCE_CLASS_WIRE,
    classify_domain,
    section_for,
)


class TestClassifyDomain:
    def test_wire(self):
        assert classify_domain("apnews.com") == SOURCE_CLASS_WIRE
        assert classify_domain("reuters.com") == SOURCE_CLASS_WIRE

    def test_reporting(self):
        assert classify_domain("nytimes.com") == SOURCE_CLASS_REPORTING
        assert classify_domain("propublica.org") == SOURCE_CLASS_REPORTING
        assert classify_domain("404media.co") == SOURCE_CLASS_REPORTING

    def test_filing(self):
        assert classify_domain("courtlistener.com") == SOURCE_CLASS_FILING
        assert classify_domain("supremecourt.gov") == SOURCE_CLASS_FILING
        # storage. subdomain — exact match in map
        assert classify_domain("storage.courtlistener.com") == SOURCE_CLASS_FILING

    def test_regulation(self):
        assert classify_domain("federalregister.gov") == SOURCE_CLASS_REGULATION
        assert classify_domain("sec.gov") == SOURCE_CLASS_REGULATION
        assert classify_domain("congress.gov") == SOURCE_CLASS_REGULATION

    def test_paper(self):
        assert classify_domain("arxiv.org") == SOURCE_CLASS_PAPER
        assert classify_domain("nature.com") == SOURCE_CLASS_PAPER
        assert classify_domain("nejm.org") == SOURCE_CLASS_PAPER

    def test_code(self):
        assert classify_domain("github.com") == SOURCE_CLASS_CODE
        assert classify_domain("rfc-editor.org") == SOURCE_CLASS_CODE

    def test_platform_domain_returns_unknown(self):
        # Twitter, Bluesky, etc. are not stories
        assert classify_domain("twitter.com") == SOURCE_CLASS_UNKNOWN
        assert classify_domain("bsky.app") == SOURCE_CLASS_UNKNOWN
        assert classify_domain("t.co") == SOURCE_CLASS_UNKNOWN

    def test_subdomain_rolls_up_to_parent(self):
        # `projects.propublica.org` should roll up to propublica.org
        # without requiring an entry per subdomain.
        assert classify_domain("projects.propublica.org") == SOURCE_CLASS_REPORTING

    def test_deep_subdomain_rolls_up(self):
        assert classify_domain("a.b.apnews.com") == SOURCE_CLASS_WIRE

    def test_unmapped_gov_subdomain_uses_suffix(self):
        # No entry for nasa.gov, but .gov suffix → regulation
        assert classify_domain("nasa.gov") == SOURCE_CLASS_REGULATION

    def test_unmapped_edu_uses_suffix(self):
        assert classify_domain("mit.edu") == SOURCE_CLASS_PAPER

    def test_unmapped_random_domain_unknown(self):
        assert classify_domain("randomnews.example") == SOURCE_CLASS_UNKNOWN

    def test_www_prefix_handled(self):
        assert classify_domain("www.apnews.com") == SOURCE_CLASS_WIRE

    def test_case_insensitive(self):
        assert classify_domain("APNews.com") == SOURCE_CLASS_WIRE

    def test_empty_returns_unknown(self):
        assert classify_domain(None) == SOURCE_CLASS_UNKNOWN
        assert classify_domain("") == SOURCE_CLASS_UNKNOWN


class TestSectionFor:
    def test_known_classes_get_section_labels(self):
        assert section_for(SOURCE_CLASS_REPORTING) == "Reporting"
        assert section_for(SOURCE_CLASS_FILING) == "Filings"
        assert section_for(SOURCE_CLASS_PAPER) == "Papers"

    def test_unknown_class_falls_through_to_wire(self):
        assert section_for("invented_class") == "Wire"
