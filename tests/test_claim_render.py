"""Render tests for the fail-closed claim card partial."""
import os
import sys

import pytest
from jinja2 import Environment, FileSystemLoader

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEMPLATES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "receipts_feed", "templates",
)


@pytest.fixture
def card():
    env = Environment(loader=FileSystemLoader(TEMPLATES))
    return env.get_template("_claim_card.html")


def _render(card, item):
    return card.render(item=item, trunc=lambda s, n: s, excerpt_ok=lambda a, b: False)


def test_strong_resolved_shows_receipts(card):
    item = {
        "source_class": "filing", "source_domain": "courtlistener.com",
        "display_headline": "Order granted", "display_url": "https://courtlistener.com/x",
        "text": "the court granted the motion",
        "claim": {"claim_mode": "sourced", "basis_resolved": True,
                  "basis_kind": "primary_source", "seal_digest": "abcd1234abcd1234",
                  "freshness": "fresh"},
    }
    html = _render(card, item)
    assert "Truth: unknown." in html
    assert "compile" in html
    assert "abcd1234abcd1234" in html
    assert "Order granted" in html


def test_carrier_fails_closed(card):
    item = {
        "source_domain": "example.com", "display_headline": "unverified headline",
        "claim": {"claim_mode": "carrier", "basis_resolved": False,
                  "basis_kind": "carrier_only", "seal_digest": "deadbeefdeadbeef"},
    }
    html = _render(card, item)
    assert "title unverified" in html
    assert "Truth: unknown." not in html
    # The unbacked headline never appears as a strong claim.
    assert "compile ✓" not in html


def test_missing_claim_fails_closed(card):
    # A strong mode with resolved basis is required; absent claim -> carrier form.
    html = _render(card, {"source_domain": "example.com"})
    assert "title unverified" in html
    assert "Truth: unknown." not in html
