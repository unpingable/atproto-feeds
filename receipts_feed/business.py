"""Business page: filtered lens over the same ranking engine.

Not generic finance news. Business as infrastructure and conflict:
software wars, workflow captivity, pricing power, labor fights,
market structure, AI replacement theater, vendor/compliance/data moats.
"""

# Domains that signal business/enterprise/market content
BUSINESS_DOMAINS: set[str] = {
    # Financial / market
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "cnbc.com",
    "marketwatch.com",
    "sec.gov",
    "federalreserve.gov",
    "treasury.gov",
    # Enterprise / tech business
    "techcrunch.com",
    "theinformation.com",
    "semafor.com",
    "platformer.news",
    "stratechery.com",
    "theverge.com",
    # Labor / work
    "nlrb.gov",
    "dol.gov",
    "bls.gov",
    # Regulatory
    "ftc.gov",
    "justice.gov",
    "ec.europa.eu",
    # Enterprise software / infra
    "azure.microsoft.com",
    "aws.amazon.com",
    "cloud.google.com",
}

# Keywords in post text that suggest business/enterprise content
# (checked case-insensitively against the post text)
BUSINESS_KEYWORDS: list[str] = [
    "earnings", "revenue", "profit", "quarterly", "fiscal",
    "IPO", "merger", "acquisition", "antitrust", "monopoly",
    "layoff", "layoffs", "workforce", "labor", "union",
    "enterprise", "SaaS", "vendor", "pricing", "license",
    "market cap", "valuation", "stock", "shares",
    "regulatory", "regulation", "compliance", "FTC", "DOJ",
    "contract", "procurement", "vendor lock",
    "AI replace", "automation", "workflow",
    "moat", "lock-in", "switching cost",
]


def is_business_relevant(item: dict) -> bool:
    """Check if a ranked/hydrated item belongs on the business page."""
    # Check domain
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

    if domain in BUSINESS_DOMAINS:
        return True
    for bd in BUSINESS_DOMAINS:
        if domain.endswith("." + bd):
            return True

    # Check reasons for business-adjacent domain bonuses
    reasons = item.get("reasons", [])
    for r in reasons:
        if r.startswith("domain:"):
            d = r.split(":")[1] if ":" in r else ""
            if d in ("sec.gov", "ftc.gov", "treasury.gov", "federalreserve.gov",
                      "justice.gov", "nlrb.gov", "dol.gov", "bls.gov"):
                return True

    # Check text for business keywords
    text = (item.get("text") or "").lower()
    headline = (item.get("display_headline") or "").lower()
    combined = text + " " + headline
    for kw in BUSINESS_KEYWORDS:
        if kw.lower() in combined:
            return True

    return False
