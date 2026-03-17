"""Marginalia: house ads, public notices, factoids, and classifieds.

Curated propaganda for things you actually want people to know about.
Rotated into the sidebar. Not sponsored. Many should be.
"""

import random

# Each entry: (type, content_html)
# Types: ad, notice, factoid, classified, correction
MARGINALIA = [
    # --- House Ads ---
    ("ad", """
        <strong>TIMELINE GOT YOU DOWN?</strong><br>
        Try primary sources.<br>
        <a href="https://courtlistener.com">courtlistener.com</a>
    """),
    ("ad", """
        <strong>THE INTERNET ARCHIVE</strong><br>
        For when the web decides it never said that.<br>
        <a href="https://archive.org">archive.org</a>
    """),
    ("ad", """
        <strong>TIRED OF SYNTHETIC CONSENSUS?</strong><br>
        Read people who still cite things.<br>
        <a href="https://www.404media.co">404media.co</a>
    """),
    ("ad", """
        <strong>PUBLIC-INTEREST JOURNALISM</strong><br>
        Still cheaper than being wrong.<br>
        <a href="https://www.propublica.org">ProPublica</a>
    """),
    ("ad", """
        <strong>LICHESS</strong><br>
        A rare website that behaves like one.<br>
        <a href="https://lichess.org">lichess.org</a>
    """),
    ("ad", """
        <strong>MUCKROCK</strong><br>
        FOIA as a public service.<br>
        <a href="https://www.muckrock.com">muckrock.com</a>
    """),
    ("ad", """
        <strong>OPENSECRETS</strong><br>
        Follow the money. Literally.<br>
        <a href="https://www.opensecrets.org">opensecrets.org</a>
    """),
    ("ad", """
        <strong>NEED LESS VIBE, MORE DOCUMENT?</strong><br>
        Congress is posting again.<br>
        <a href="https://congress.gov">congress.gov</a>
    """),

    # --- Internal Cross-promo ---
    ("ad", """
        <strong>LABELWATCH</strong><br>
        Because someone should keep receipts on the labelers.<br>
        <a href="https://labelwatch.neutral.zone">labelwatch.neutral.zone</a>
    """),
    ("ad", """
        <strong>RECEIPTS FEED</strong><br>
        Pin it. Less repost fog.<br>
        <a href="https://bsky.app/profile/instantinternet.news/feed/receipts">Open in Bluesky</a>
    """),

    # --- Public Notices ---
    ("notice", """
        <strong>PUBLIC NOTICE:</strong>
        <em>Baumol effect</em> is why labor-heavy services get more
        expensive without getting more "productive." Your barber is not
        less efficient than your barber in 1970.
    """),
    ("notice", """
        <strong>PUBLIC NOTICE:</strong>
        <em>Goodhart's Law</em> &mdash; when a measure becomes a target,
        it ceases to be a good measure. See also: engagement metrics.
    """),
    ("notice", """
        <strong>PUBLIC NOTICE:</strong>
        <em>Vendor lock-in</em> is not a bug. It is the business model.
    """),
    ("notice", """
        <strong>CIVIC INFORMATION:</strong>
        A <em>municipal bond</em> is the machine that helps build your
        stadium and ruin your tax base.
    """),

    # --- Factoids ---
    ("factoid", """
        <strong>DID YOU KNOW?</strong>
        The Bloomberg Terminal chat system is called Instant Bloomberg.
        Naturally.
    """),
    ("factoid", """
        <strong>DID YOU KNOW?</strong>
        The phrase "too big to fail" was first used in a Congressional
        hearing in 1984, about Continental Illinois.
    """),
    ("factoid", """
        <strong>DID YOU KNOW?</strong>
        PACER charges 10 cents per page to access federal court records.
        CourtListener mirrors them for free.
    """),
    ("factoid", """
        <strong>DID YOU KNOW?</strong>
        The AT Protocol uses content-addressed CIDs. This means a post's
        hash proves what was written. It does not prove it was worth reading.
    """),

    # --- Classifieds ---
    ("classified", """
        <strong>WANTED:</strong> Readers with tolerance for evidence.
        Apply within.
    """),
    ("classified", """
        <strong>FOR SALE:</strong> One slightly used algorithm.
        Has opinions. No refunds.
    """),
    ("classified", """
        <strong>HELP WANTED:</strong> Sources. Primary ones.
        Screenshots of tweets need not apply.
    """),

    # --- Corrections ---
    ("correction", """
        <strong>CORRECTION:</strong>
        Not every interface is a product replacement.
    """),
    ("correction", """
        <strong>CORRECTION:</strong>
        Engagement is not the same as agreement.
    """),
    ("correction", """
        <strong>ADVISORY:</strong>
        This site is a condensed skeuomorphic social media product,
        chunked and processed to remove harmful toxins and thoughtloaf.
    """),
]


def get_marginalia(count: int = 2, seed: int = None) -> list[dict]:
    """Pick random marginalia items for display.

    Uses a seed based on the current hour so items rotate roughly hourly
    but stay stable within a page load.
    """
    if seed is None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        seed = now.year * 100000 + now.timetuple().tm_yday * 100 + now.hour

    rng = random.Random(seed)
    # Pick items, preferring variety in types
    pool = list(MARGINALIA)
    rng.shuffle(pool)

    selected = []
    seen_types = set()
    for item_type, html in pool:
        if len(selected) >= count:
            break
        # Try to get variety
        if item_type in seen_types and len(selected) < count - 1:
            continue
        selected.append({"type": item_type, "html": html.strip()})
        seen_types.add(item_type)

    # If we didn't get enough, fill from remainder
    if len(selected) < count:
        for item_type, html in pool:
            if len(selected) >= count:
                break
            if {"type": item_type, "html": html.strip()} not in selected:
                selected.append({"type": item_type, "html": html.strip()})

    return selected[:count]
