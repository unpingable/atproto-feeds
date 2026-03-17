"""Public tag taxonomy for the site.

Tags are reader-facing categorical labels. No numeric debug output.
Max 4 tags per item on the public site. Strict precedence order.
"""

from markupsafe import Markup


# Tag definitions: (css_class, display_label)
# Ordered by display precedence — first match wins within each category
TAG_MAP = {
    # Relationship
    "mutual": ("mutual", "mutual"),
    "followed": ("graph", "graph"),
    "follower": ("graph", "graph"),
    "trusted_list": ("graph", "trusted"),
    "unknown_author": ("outsider", "outsider"),

    # Form
    "original": ("original", "original"),
    "substantive_reply": ("substance", "good reply"),
    "has_link": ("link", "linked"),

    # Quality
    # (substance:+X.X → just "substance")
}

# Domain reasons get mapped to evidence tags
# "domain:congress.gov:+3.5" → ("source", "primary source") or ("source", "reporting")
PRIMARY_THRESHOLDS = 2.5  # domain bonus >= this → "primary source"
REPORTING_THRESHOLD = 1.0  # domain bonus >= this → "reporting"


def reasons_to_tags(reasons: list, max_tags: int = 4) -> list[dict]:
    """Convert internal reason strings to reader-facing tag dicts.

    Returns list of {"css": str, "label": str}, capped at max_tags.
    Skips debug/numeric output entirely.
    """
    tags = []
    seen_categories = set()  # prevent duplicate categories

    for reason in reasons:
        if len(tags) >= max_tags:
            break

        # Direct mapped tags
        if reason in TAG_MAP:
            css, label = TAG_MAP[reason]
            if css not in seen_categories:
                tags.append({"css": css, "label": label})
                seen_categories.add(css)
            continue

        # Domain tags: "domain:congress.gov:+3.5"
        if reason.startswith("domain:"):
            parts = reason.split(":")
            if len(parts) >= 3:
                domain = parts[1]
                try:
                    bonus = float(parts[2].lstrip("+"))
                except ValueError:
                    bonus = 0
                if "source" not in seen_categories:
                    if bonus >= PRIMARY_THRESHOLDS:
                        tags.append({"css": "source", "label": "primary source"})
                    elif bonus >= REPORTING_THRESHOLD:
                        tags.append({"css": "reporting", "label": "reporting"})
                    else:
                        tags.append({"css": "link", "label": domain})
                    seen_categories.add("source")
            continue

        # Substance (strip numeric suffix)
        if reason.startswith("substance"):
            if "substance" not in seen_categories:
                tags.append({"css": "substance", "label": "substance"})
                seen_categories.add("substance")
            continue

        # Skip all debug/numeric tags:
        # flood:*, volume:*, stale:*, weight:*, image_stub:*, short_reply:*, quote:*, repost:*
        if any(reason.startswith(skip) for skip in (
            "flood", "volume", "stale", "weight", "image_stub",
            "short_reply", "quote", "repost",
        )):
            continue

    return tags


def render_tags_html(reasons: list, max_tags: int = 4) -> str:
    """Render reason tags as HTML spans. Returns safe markup."""
    tags = reasons_to_tags(reasons, max_tags)
    parts = []
    for tag in tags:
        parts.append(f'<span class="reason-tag {tag["css"]}">{tag["label"]}</span>')
    return Markup(" ".join(parts))
