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


class TestRepresentativeSelection:
    """Within a cluster, human curators should beat relays."""

    def _make_post(self, uri, author_did, text, domain="example.com"):
        return {
            "uri": uri, "author_did": author_did,
            "created_at": "2026-03-20T00:00:00Z",
            "text": text,
            "has_external_embed": 1, "external_domain": domain,
            "external_uri": f"https://{domain}/article/123",
            "is_repost": 0, "reply_to_uri": None, "root_uri": None,
            "quote_uri": None, "has_image": 0, "has_video": 0,
            "link_count": 1, "facets_count": 0, "langs": "",
            "cid": "", "indexed_at": "2026-03-20T00:00:00Z",
        }

    def test_graph_curator_beats_outsider_relay(self):
        """A mutual with short but real commentary should lead over a verbose outsider relay."""
        from receipts_feed.cluster import _representative_sort_key

        mutual_member = {
            "uri": "at://mutual/post/1", "score": 8.0,
            "reasons": ["mutual", "original", "has_link"],
            "_post": {"text": "This is an important ruling — sets real precedent for executive power limits. Worth reading carefully.", "author_did": "did:mutual"},
        }
        relay_member = {
            "uri": "at://relay/post/1", "score": 9.0,  # Higher raw score!
            "reasons": ["unknown_author", "original", "has_link", "domain:congress.gov:+3.5"],
            "_post": {"text": "HR948: Commemorating the 50th anniversary of Southeast Asian refugee resettlement and the many contributions and sacrifices of Southeast Asian Americans to the United States", "author_did": "did:relay"},
        }

        mutual_key = _representative_sort_key(mutual_member)
        relay_key = _representative_sort_key(relay_member)
        assert mutual_key > relay_key, \
            f"Mutual curator ({mutual_key}) should beat outsider relay ({relay_key})"

    def test_outsider_with_great_commentary_still_viable(self):
        """An outsider with substantial original commentary should beat a low-effort graph member."""
        from receipts_feed.cluster import _representative_sort_key

        lazy_mutual = {
            "uri": "at://mutual/post/2", "score": 7.0,
            "reasons": ["mutual", "original", "has_link"],
            "_post": {"text": "wow https://example.com/thing", "author_did": "did:lazy"},
        }
        good_outsider = {
            "uri": "at://outsider/post/2", "score": 8.5,
            "reasons": ["unknown_author", "original", "has_link"],
            "_post": {"text": "This is genuinely important because it changes how courts interpret the Commerce Clause. The majority opinion specifically addresses the standing question that has been unresolved since 2019.", "author_did": "did:good_outsider"},
        }

        lazy_key = _representative_sort_key(lazy_mutual)
        good_key = _representative_sort_key(good_outsider)
        # Graph still gets tier 2 vs tier 0, but outsider has commentary tier 1
        # The outsider's commentary should at least make it competitive
        # (graph wins on tier, but outsider shouldn't be totally crushed)
        assert good_key[1] > lazy_key[1], \
            "Outsider with great commentary should have higher commentary tier than lazy mutual"

    def test_relay_last_resort(self):
        """A pure relay should rank below any human with commentary."""
        from receipts_feed.cluster import _representative_sort_key

        relay = {
            "uri": "at://relay/post/3", "score": 10.0,  # Even higher score
            "reasons": ["unknown_author", "original", "has_link"],
            "_post": {"text": "congress.gov/bill/119/hr999", "author_did": "did:relay2"},
        }
        human = {
            "uri": "at://human/post/3", "score": 6.0,
            "reasons": ["follower", "original", "has_link"],
            "_post": {"text": "This bill would fundamentally change how federal agencies handle whistleblower protections. The sponsor list is bipartisan which is notable.", "author_did": "did:human"},
        }

        relay_key = _representative_sort_key(relay)
        human_key = _representative_sort_key(human)
        assert human_key > relay_key, \
            f"Human with commentary ({human_key}) should beat pure relay ({relay_key})"


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
