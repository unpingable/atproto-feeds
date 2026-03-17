"""WEATHER page: where abstraction stops working.

Material conditions: storms, heat, floods, fire, drought, grid trouble,
insurance collapse, and the municipal fallout from all of it.
"""

WEATHER_DOMAINS: set[str] = {
    # Federal weather / climate
    "weather.gov",
    "nhc.noaa.gov",
    "spc.noaa.gov",
    "noaa.gov",
    "climate.gov",
    "drought.gov",
    "nws.noaa.gov",
    # Emergency / disaster
    "fema.gov",
    "ready.gov",
    "disasterassistance.gov",
    "airnow.gov",
    # Fire
    "nifc.gov",
    "inciweb.wildfire.gov",
    "fire.ca.gov",
    # Utilities / grid
    "eia.gov",
    "ferc.gov",
    "nerc.com",
    # Insurance
    "naic.org",
    "floodsmart.gov",
    # Reporting
    "yaleclimateconnections.org",
    "carbonbrief.org",
    "insideclimatenews.org",
    "grist.org",
}

WEATHER_KEYWORDS: list[str] = [
    "hurricane", "tropical storm", "typhoon", "cyclone",
    "tornado", "severe weather", "weather warning", "weather alert",
    "flood", "flooding", "flash flood", "storm surge",
    "wildfire", "brush fire", "forest fire", "fire season", "fire weather",
    "drought", "water shortage", "water crisis", "aquifer",
    "heat wave", "heat dome", "extreme heat", "heat advisory",
    "polar vortex", "winter storm", "blizzard", "ice storm",
    "power outage", "grid failure", "blackout", "brownout",
    "grid strain", "rolling blackout", "ERCOT",
    "insurance crisis", "insurance withdrawal", "uninsurable",
    "flood insurance", "FEMA", "disaster declaration",
    "air quality", "AQI", "smoke", "particulate",
    "sea level", "coastal erosion", "king tide",
    "crop failure", "crop damage", "agricultural",
    "infrastructure failure", "dam", "levee",
    "climate change", "global warming", "emission",
    "renewable", "solar", "wind power",
]


def is_weather_relevant(item: dict) -> bool:
    """Check if a ranked/hydrated item belongs on the weather page."""
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

    if domain in WEATHER_DOMAINS:
        return True
    for wd in WEATHER_DOMAINS:
        if domain.endswith("." + wd):
            return True

    text = (item.get("text") or "").lower()
    headline = (item.get("display_headline") or "").lower()
    combined = text + " " + headline
    for kw in WEATHER_KEYWORDS:
        if kw.lower() in combined:
            return True

    return False
