"""Tests for receipts_feed.urls.

Source-first pivot acceptance: tracking params can't split a story; mobile
YT folds; `www.` stripped; http/https collapse; param ordering doesn't
shift the cluster key.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from receipts_feed.urls import canonicalize_url, extract_domain, url_key


class TestCanonicalize:
    def test_strips_utm(self):
        assert canonicalize_url(
            "https://apnews.com/article/x?utm_source=twitter&utm_medium=social"
        ) == "https://apnews.com/article/x"

    def test_strips_fbclid(self):
        assert canonicalize_url(
            "https://example.com/x?fbclid=abc123"
        ) == "https://example.com/x"

    def test_strips_cmpid(self):
        # The CNN/AP newsroom-mailer attribution param
        assert canonicalize_url(
            "https://apnews.com/article/foo?cmpid=newsletter"
        ) == "https://apnews.com/article/foo"

    def test_strips_smid(self):
        # NYT social attribution
        assert canonicalize_url(
            "https://nytimes.com/2026/06/10/foo.html?smid=tw-share"
        ) == "https://nytimes.com/2026/06/10/foo.html"

    def test_strips_wapo_tracking(self):
        # The WaPo tracking garbage that fable specifically called out —
        # cannot be allowed to split a story
        a = canonicalize_url(
            "https://washingtonpost.com/news/x/?itid=hp-top&pgtype=article"
        )
        b = canonicalize_url("https://washingtonpost.com/news/x/")
        assert a == b

    def test_preserves_real_query(self):
        # search?q=cats is real; q is not in the tracking set
        assert canonicalize_url(
            "https://google.com/search?q=cats&utm_source=foo"
        ) == "https://google.com/search?q=cats"

    def test_sorts_query_params(self):
        # Param order is normalized so two URLs with re-ordered params
        # cluster to the same canonical form.
        a = canonicalize_url("https://example.com/x?b=2&a=1")
        b = canonicalize_url("https://example.com/x?a=1&b=2")
        assert a == b

    def test_strips_fragment(self):
        assert canonicalize_url(
            "https://example.com/x#section-2"
        ) == "https://example.com/x"

    def test_strips_www(self):
        assert canonicalize_url(
            "https://www.apnews.com/x"
        ) == "https://apnews.com/x"

    def test_http_collapses_to_https(self):
        # Same article served via http and https should cluster together
        assert (
            canonicalize_url("http://apnews.com/x")
            == canonicalize_url("https://apnews.com/x")
        )

    def test_host_case_insensitive(self):
        assert (
            canonicalize_url("https://APNews.com/x")
            == canonicalize_url("https://apnews.com/x")
        )

    def test_youtube_short_link(self):
        assert canonicalize_url(
            "https://youtu.be/dQw4w9WgXcQ"
        ) == "https://youtube.com/watch?v=dQw4w9WgXcQ"

    def test_youtube_shorts_folds_to_watch(self):
        assert canonicalize_url(
            "https://youtube.com/shorts/abcDEF12345"
        ) == "https://youtube.com/watch?v=abcDEF12345"

    def test_mobile_youtube_folds(self):
        assert canonicalize_url(
            "https://m.youtube.com/watch?v=abcDEF12345"
        ) == "https://youtube.com/watch?v=abcDEF12345"

    def test_empty_returns_empty(self):
        assert canonicalize_url("") == ""
        assert canonicalize_url(None) == ""  # type: ignore[arg-type]

    def test_garbage_is_deterministic_and_nonempty(self):
        # urlparse won't error on garbage; we get back a deterministic
        # rebuild. We just need that result to be a stable string so the
        # cluster key for malformed input is reproducible.
        bad = "not a url at all"
        out1 = canonicalize_url(bad)
        out2 = canonicalize_url(bad)
        assert out1 == out2
        assert isinstance(out1, str) and out1


class TestUrlKey:
    def test_deterministic(self):
        url = "https://apnews.com/article/foo"
        assert url_key(url) == url_key(url)

    def test_different_urls_different_keys(self):
        assert url_key("https://a.com/1") != url_key("https://a.com/2")

    def test_canonical_equivalents_collide(self):
        a = canonicalize_url("https://apnews.com/x?utm_source=tw")
        b = canonicalize_url("https://www.apnews.com/x?fbclid=abc")
        assert url_key(a) == url_key(b)


class TestExtractDomain:
    def test_basic(self):
        assert extract_domain("https://apnews.com/x") == "apnews.com"

    def test_strips_www(self):
        assert extract_domain("https://www.example.com/x") == "example.com"

    def test_returns_empty_for_garbage(self):
        assert extract_domain("not a url") == ""
        assert extract_domain("") == ""

    def test_lowercases(self):
        assert extract_domain("https://APNews.com/x") == "apnews.com"
