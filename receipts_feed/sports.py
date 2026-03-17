"""SPORTS page: the financialization desk for leisure, fandom, and event culture.

Where play, fandom, and spectacle are translated into wagers, rents,
telemetry, and leverage.
"""

SPORTS_DOMAINS: set[str] = {
    # Betting / prediction markets
    "polymarket.com",
    "kalshi.com",
    "predictit.org",
    "draftkings.com",
    "fanduel.com",
    "betmgm.com",
    "espnbet.com",
    # Sports business / labor
    "sportico.com",
    "theathletic.com",
    "frontofficesports.com",
    # Leagues / orgs (when they show up in business context)
    "mlbpa.org",
    "nbpa.com",
    "nflpa.com",
    # Ticketing / platforms
    "ticketmaster.com",
    "stubhub.com",
    "seatgeek.com",
}

SPORTS_KEYWORDS: list[str] = [
    "prediction market", "polymarket", "betting odds", "sportsbook",
    "gambling", "wager", "prop bet", "point spread", "parlay",
    "DraftKings", "FanDuel", "fantasy sports",
    "stadium deal", "stadium subsidy", "stadium financing",
    "naming rights", "arena deal", "taxpayer funded",
    "media rights", "broadcast deal", "streaming rights",
    "ticket prices", "Ticketmaster", "dynamic pricing",
    "NIL deal", "NIL collective", "transfer portal",
    "player union", "lockout", "CBA", "collective bargaining",
    "match fixing", "officiating", "referee",
    "private equity", "team valuation", "franchise value",
    "fan engagement", "gamification",
    "esports", "creator economy",
    "collectible", "trading card", "NFT",
]


def is_sports_relevant(item: dict) -> bool:
    """Check if a ranked/hydrated item belongs on the sports page."""
    domain = ""
    ext_uri = item.get("external_uri") or item.get("canonical_url") or ""
    if ext_uri:
        try:
            from urllib.parse import urlparse
            d = urlparse(ext_uri).hostname or ""
            if d.startswith("www."):
                d = d[4:]
            domain = d.lower()
        except Exception:
            pass

    if domain in SPORTS_DOMAINS:
        return True
    for sd in SPORTS_DOMAINS:
        if domain.endswith("." + sd):
            return True

    text = (item.get("text") or "").lower()
    headline = (item.get("display_headline") or "").lower()
    combined = text + " " + headline
    for kw in SPORTS_KEYWORDS:
        if kw.lower() in combined:
            return True

    return False
