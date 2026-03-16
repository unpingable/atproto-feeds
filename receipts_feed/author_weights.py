"""Per-author weight multipliers for feed ranking.

This is editorial taste, not algorithm. If someone is in your graph but
too noisy for the feed, set their weight lower. They still appear — just
less often and need higher-quality posts to compete.

Weight of 1.0 = normal. 0.5 = half strength. 0.0 = effectively muted.
Values > 1.0 boost an author (use sparingly).
"""

import logging
import os
from pathlib import Path

LOG = logging.getLogger("receipts.author_weights")

# Inline config — edit directly or load from YAML later
# Keys are handles (without @) or DIDs
AUTHOR_WEIGHTS: dict[str, float] = {
    "sophianyx.bsky.social": 0.25,  # chronic high-volume; preserve rare breakthrough posts
}


def get_author_weight(handle: str = "", did: str = "") -> float:
    """Return the weight multiplier for an author. Default 1.0."""
    if handle and handle in AUTHOR_WEIGHTS:
        return AUTHOR_WEIGHTS[handle]
    if did and did in AUTHOR_WEIGHTS:
        return AUTHOR_WEIGHTS[did]
    return 1.0
