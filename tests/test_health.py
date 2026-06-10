"""Tests for receipts_feed.health.

Pure-function coverage for the verdict mapping + renderable-ratio check.
Full compute_health is exercised by smoking the CLI against a live DB.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from receipts_feed.health import (
    _check_renderable_ratio,
    _verdict_for,
)


class TestVerdictMapping:
    def test_higher_is_worse_pass(self):
        assert _verdict_for(10, warn=100, fail=500) == "pass"

    def test_higher_is_worse_warn(self):
        assert _verdict_for(150, warn=100, fail=500) == "warn"

    def test_higher_is_worse_fail(self):
        assert _verdict_for(600, warn=100, fail=500) == "fail"

    def test_higher_is_better_pass(self):
        # ratio of 0.95 with warn=0.9 fail=0.8 → pass (above warn)
        assert _verdict_for(0.95, warn=0.9, fail=0.8, higher_is_worse=False) == "pass"

    def test_higher_is_better_at_warn_threshold_passes(self):
        # ratio EXACTLY at warn threshold → pass (safely at the boundary).
        # Important: with renderable_ratio_warn=1.0, hitting 1.0 must not warn.
        assert _verdict_for(0.9, warn=0.9, fail=0.8, higher_is_worse=False) == "pass"

    def test_higher_is_better_below_warn_warns(self):
        # ratio just below warn → warn
        assert _verdict_for(0.85, warn=0.9, fail=0.8, higher_is_worse=False) == "warn"

    def test_higher_is_better_fail(self):
        assert _verdict_for(0.5, warn=0.9, fail=0.8, higher_is_worse=False) == "fail"


class TestRenderableRatio:
    def test_all_posts_renderable(self):
        skel = [
            {"post": "at://did:plc:a/app.bsky.feed.post/abc"},
            {"post": "at://did:plc:b/app.bsky.feed.post/def"},
            {"post": "at://did:plc:c/app.bsky.feed.post/ghi"},
        ]
        c = _check_renderable_ratio(skel)
        assert c["value"] == 1.0
        assert c["verdict"] == "pass"

    def test_reposts_in_skeleton_failure(self):
        """The May 30 → June 10 bug: skeleton with reposts is unrenderable."""
        skel = [
            {"post": "at://did:plc:a/app.bsky.feed.post/abc"},
            {"post": "at://did:plc:b/app.bsky.feed.repost/def"},
            {"post": "at://did:plc:c/app.bsky.feed.repost/ghi"},
        ]
        c = _check_renderable_ratio(skel)
        assert c["value"] == pytest.approx(1.0 / 3, rel=0.01)
        # 0.33 is below the fail threshold of 0.9 → fail
        assert c["verdict"] == "fail"
        assert "not feed.post" in c.get("note", "")

    def test_empty_skeleton_fails(self):
        c = _check_renderable_ratio([])
        assert c["verdict"] == "fail"
        assert "empty" in c.get("note", "")

    def test_partial_renderable_warns_or_fails(self):
        """One repost in five posts → 0.8 ratio, below the 1.0 warn threshold."""
        skel = [
            {"post": "at://did:plc:a/app.bsky.feed.post/1"},
            {"post": "at://did:plc:b/app.bsky.feed.post/2"},
            {"post": "at://did:plc:c/app.bsky.feed.post/3"},
            {"post": "at://did:plc:d/app.bsky.feed.post/4"},
            {"post": "at://did:plc:e/app.bsky.feed.repost/5"},
        ]
        c = _check_renderable_ratio(skel)
        assert c["value"] == 0.8
        # 0.8 is below warn=1.0 AND below fail=0.9 → fail (because the
        # threshold is "must be 100% post records")
        assert c["verdict"] == "fail"
