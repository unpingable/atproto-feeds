"""Ranking regression fixtures.

Not a full test suite. Just invariants that should hold after any
ranking change. Run against a live or snapshot DB to verify.

Usage: python -m pytest tests/test_ranking_invariants.py -v
"""

import os
import sys
import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from receipts_feed import db
from receipts_feed.rank import score_post, _strip_urls


@pytest.fixture(autouse=True)
def init():
    db.init_db()


class TestRelayPenalties:
    """Posts that look like syndication exhaust should be penalized."""

    def test_pure_link_dump_penalized(self):
        """A post with just a URL and no commentary gets relay penalty."""
        post = {
            "uri": "test://1", "author_did": "did:test:1",
            "created_at": "2026-03-20T00:00:00Z",
            "text": "https://congress.gov/bill/119/hr123",
            "has_external_embed": 1, "external_domain": "congress.gov",
            "is_repost": 0, "reply_to_uri": None, "quote_uri": None,
            "has_image": 0, "has_video": 0,
            "link_count": 1, "facets_count": 0,
        }
        score, reasons = score_post(post, None)
        assert any("relay" in r for r in reasons), f"Expected relay penalty, got {reasons}"

    def test_short_reaction_plus_link_penalized(self):
        """A post with minimal commentary gets low_commentary penalty."""
        post = {
            "uri": "test://2", "author_did": "did:test:2",
            "created_at": "2026-03-20T00:00:00Z",
            "text": "Wow this is wild https://example.com/article",
            "has_external_embed": 1, "external_domain": "example.com",
            "is_repost": 0, "reply_to_uri": None, "quote_uri": None,
            "has_image": 0, "has_video": 0,
            "link_count": 1, "facets_count": 0,
        }
        score, reasons = score_post(post, None)
        assert any("relay" in r or "low_commentary" in r for r in reasons)

    def test_substantive_commentary_not_penalized(self):
        """A post with real commentary should not get relay penalty."""
        post = {
            "uri": "test://3", "author_did": "did:test:3",
            "created_at": "2026-03-20T00:00:00Z",
            "text": "This ruling is significant because it establishes precedent "
                    "for executive overreach. The court explicitly cited previous "
                    "cases from the DC Circuit. https://courtlistener.com/opinion/123",
            "has_external_embed": 1, "external_domain": "courtlistener.com",
            "is_repost": 0, "reply_to_uri": None, "quote_uri": None,
            "has_image": 0, "has_video": 0,
            "link_count": 1, "facets_count": 0,
        }
        score, reasons = score_post(post, None)
        assert not any("relay" in r or "low_commentary" in r for r in reasons), \
            f"Substantive post should not be penalized: {reasons}"


class TestStinkScores:
    """Account-level stink scores should penalize bot-like behavior."""

    def test_high_stink_penalized(self):
        """Account with stink > 0.6 should get penalty."""
        post = {
            "uri": "test://4", "author_did": "did:test:stinky",
            "created_at": "2026-03-20T00:00:00Z",
            "text": "Check this out https://example.com/link",
            "has_external_embed": 1, "external_domain": "example.com",
            "is_repost": 0, "reply_to_uri": None, "quote_uri": None,
            "has_image": 0, "has_video": 0,
            "link_count": 1, "facets_count": 0,
        }
        author = {
            "did": "did:test:stinky", "handle": "stinky.bot",
            "seed_class": "follower", "trusted_score": 1.0,
            "posts_24h": 20, "stink_score": 0.85,
        }
        score, reasons = score_post(post, author)
        assert any("stink" in r for r in reasons)

    def test_low_stink_not_penalized(self):
        """Account with stink < 0.6 should not get stink penalty."""
        post = {
            "uri": "test://5", "author_did": "did:test:clean",
            "created_at": "2026-03-20T00:00:00Z",
            "text": "Great article about the implications of this ruling for future cases "
                    "and how it relates to prior precedent https://courtlistener.com/op/456",
            "has_external_embed": 1, "external_domain": "courtlistener.com",
            "is_repost": 0, "reply_to_uri": None, "quote_uri": None,
            "has_image": 0, "has_video": 0,
            "link_count": 1, "facets_count": 0,
        }
        author = {
            "did": "did:test:clean", "handle": "good.human",
            "seed_class": "mutual", "trusted_score": 4.0,
            "posts_24h": 3, "stink_score": 0.2,
        }
        score, reasons = score_post(post, author)
        assert not any("stink" in r for r in reasons)


class TestGraphVsOutsider:
    """Graph members should outrank outsiders when quality is similar."""

    def test_mutual_beats_outsider_same_domain(self):
        """A mutual posting congress.gov should outscore an outsider doing the same."""
        base_post = {
            "created_at": "2026-03-20T00:00:00Z",
            "text": "Important new bill on climate policy. Here's the full text. "
                    "Worth reading the sponsor list. https://congress.gov/bill/119/s100",
            "has_external_embed": 1, "external_domain": "congress.gov",
            "is_repost": 0, "reply_to_uri": None, "quote_uri": None,
            "has_image": 0, "has_video": 0,
            "link_count": 1, "facets_count": 0,
        }

        mutual_post = {**base_post, "uri": "test://m", "author_did": "did:test:mutual"}
        outsider_post = {**base_post, "uri": "test://o", "author_did": "did:test:outsider"}

        mutual_author = {
            "did": "did:test:mutual", "handle": "real.person",
            "seed_class": "mutual", "trusted_score": 4.0,
            "posts_24h": 5, "stink_score": 0.1,
        }

        m_score, _ = score_post(mutual_post, mutual_author)
        o_score, _ = score_post(outsider_post, None)
        assert m_score > o_score, f"Mutual ({m_score}) should beat outsider ({o_score})"


class TestUrlStripping:
    """URL stripping should handle various URL formats."""

    def test_strip_http_urls(self):
        assert _strip_urls("Check this https://example.com/article out") == "Check this  out"

    def test_strip_bare_domain_paths(self):
        assert _strip_urls("congress.gov/bill/119/hr123").strip() == ""

    def test_strip_www_urls(self):
        assert _strip_urls("See www.reuters.com/article/foo for details") == "See  for details"

    def test_preserve_real_text(self):
        text = "This ruling is significant and sets precedent"
        assert _strip_urls(text) == text
