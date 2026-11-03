"""Where the acquisition program may operate.

SUPPORTED TERRITORY: the contiguous United States EXCEPT California (the 47
other contiguous states plus DC). Excluded: California, Alaska, Hawaii, every
US territory, Canada, Mexico and every other country.

Applies to a client's service area and to every prospect. A prospect whose
state can't be read from real evidence (an address or a listing) stays in
REVIEW - it can be researched, never emailed - until an operator confirms the
location. An area code alone is not evidence of where a business is.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

ELIGIBLE = "eligible"
REVIEW = "review"
EXCLUDED = "excluded"

SUPPORTED = frozenset((
    "AL", "AZ", "AR", "CO", "CT", "DE", "DC", "FL", "GA", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
    "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY"))
EXCLUDED_US = frozenset(("CA", "AK", "HI", "PR", "GU", "VI", "AS", "MP", "UM"))

NAMES = {
    "alabama": "AL", "arizona": "AZ", "arkansas": "AR", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY", "california": "CA", "alaska": "AK", "hawaii": "HI", "puerto rico": "PR", "guam": "GU",
}
FOREIGN = ("canada", "mexico", "méxico", "united kingdom", "uk", "australia", "india", "philippines")


def normalize_state(raw: Optional[str]) -> Optional[str]:
    s = (raw or "").strip()
    if not s:
        return None
    if len(s) == 2 and s.isalpha():
        return s.upper()
    return NAMES.get(s.lower())


def status(state: Optional[str], country: Optional[str] = None) -> Tuple[str, str]:
    """(geo_status, why)."""
    c = (country or "").strip().lower()
    if c and c not in ("us", "usa", "united states", "united states of america"):
        return EXCLUDED, "Outside the United States (%s)" % country
    st = normalize_state(state)
    if not st:
        return REVIEW, "Location not confirmed - needs review before any outreach"
    if st in SUPPORTED:
        return ELIGIBLE, "In %s" % st
    if st in EXCLUDED_US:
        return EXCLUDED, "%s is outside the supported territory" % st
    return REVIEW, "Unrecognized state %r - needs review" % state


_ADDR = re.compile(r",\s*([A-Za-z .]+?)\s+(\d{5})(?:-\d{4})?\s*(?:,\s*([A-Za-z .]+))?\s*$")


def from_address(addr: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(state, zip, country) read from a formatted address such as
    '1 Main St, Dallas, TX 75201, USA'. Nothing is guessed: unreadable -> Nones."""
    a = (addr or "").strip()
    if not a:
        return None, None, None
    low = a.lower()
    for f in FOREIGN:
        if low.endswith(", " + f) or low.endswith(" " + f):
            return None, None, f
    m = _ADDR.search(a)
    if not m:
        return None, None, None
    st = normalize_state(m.group(1).strip())
    return st, m.group(2), (m.group(3) or "").strip() or None
