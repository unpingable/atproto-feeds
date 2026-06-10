"""Tests for receipts_feed.og_fetch — meta extraction (no network)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from receipts_feed.og_fetch import _extract_meta, _pick_description, _pick_image, _pick_title


class TestMetaExtract:
    def test_basic_og_title(self):
        body = b'<html><head><meta property="og:title" content="Hello world"/></head></html>'
        meta = _extract_meta(body)
        assert meta.get("og:title") == "Hello world"

    def test_description(self):
        body = b'<html><head><meta name="description" content="A description"/></head></html>'
        meta = _extract_meta(body)
        assert meta.get("description") == "A description"

    def test_attribute_order_reversed(self):
        # content="..." property="..." (rarer but real)
        body = b'<html><head><meta content="X" property="og:title"/></head></html>'
        meta = _extract_meta(body)
        assert meta.get("og:title") == "X"

    def test_handles_html_entities(self):
        body = b'<html><head><meta property="og:title" content="A &amp; B"/></head></html>'
        meta = _extract_meta(body)
        assert meta.get("og:title") == "A & B"

    def test_title_tag_fallback(self):
        body = b"<html><head><title>Title only</title></head></html>"
        meta = _extract_meta(body)
        assert meta.get("title") == "Title only"

    def test_og_title_wins_over_title_tag(self):
        body = (
            b'<html><head>'
            b'<title>Fallback</title>'
            b'<meta property="og:title" content="Winner"/>'
            b'</head></html>'
        )
        meta = _extract_meta(body)
        assert _pick_title(meta) == "Winner"

    def test_twitter_title_used_if_og_missing(self):
        body = b'<meta name="twitter:title" content="Twitter only"/>'
        meta = _extract_meta(body)
        assert _pick_title(meta) == "Twitter only"

    def test_image_pulled(self):
        body = b'<meta property="og:image" content="https://example.com/a.png"/>'
        meta = _extract_meta(body)
        assert _pick_image(meta) == "https://example.com/a.png"

    def test_description_priority_order(self):
        body = (
            b'<meta name="description" content="generic"/>'
            b'<meta property="og:description" content="og wins"/>'
        )
        meta = _extract_meta(body)
        assert _pick_description(meta) == "og wins"

    def test_no_meta_no_title(self):
        meta = _extract_meta(b"<html><body>just body</body></html>")
        assert _pick_title(meta) is None
        assert _pick_description(meta) is None
        assert _pick_image(meta) is None
