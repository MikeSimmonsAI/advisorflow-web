"""RentCast value estimate, held to a HARD free-request budget.

RentCast's free Developer plan includes 50 requests a month; requests past
that can be billed as overage. So EvoSense keeps its OWN count, platform-wide,
in the database, and stops well short of 50 (default 40, never above 45). A
request is reserved atomically BEFORE it is sent; if the month's budget is used
up - or the count cannot be checked - nothing is sent.

What comes back is an ESTIMATE (an automated valuation from nearby listings),
labelled as such. The comparables are LISTINGS: an asking price, with the date
the listing was seen or removed - never a confirmed sale price (Texas does not
publish sale prices). They are kept apart from the deal's comparable SALES
and never feed an ARV.

Only used for properties scoring 65+ or when a person asks, and cached: a
property valued in the last 30 days is not valued again.
"""
from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.services.evosense.sources import base as B

API = "https://api.rentcast.io/v1/avm/value"
KEY_ENV = "RENTCAST_API_KEY"
FREE_ALLOWANCE = 50
HARD_CEILING = 45
DEFAULT_LIMIT = 40
CACHE_DAYS = 30
PROVIDER_KEY = "rentcast"


def configured() -> bool:
    return bool(os.environ.get(KEY_ENV))


def monthly_limit() -> int:
    try:
        v = int(os.environ.get("RENTCAST_MONTHLY_LIMIT") or DEFAULT_LIMIT)
    except ValueError:
        v = DEFAULT_LIMIT
    return max(0, min(v, HARD_CEILING))


def period(now: Optional[datetime] = None) -> str:
    return (now or datetime.utcnow()).strftime("%Y-%m")


def usage(db) -> Dict[str, Any]:
    from app.models.evosense_models import EvoSenseApiUsage
    row = (db.query(EvoSenseApiUsage).filter(EvoSenseApiUsage.provider_key == PROVIDER_KEY,
                                            EvoSenseApiUsage.period == period()).first())
    used = row.count if row else 0
    return {"provider": PROVIDER_KEY, "period": period(), "used": used, "limit": monthly_limit(),
            "free_allowance": FREE_ALLOWANCE, "left": max(0, monthly_limit() - used),
            "configured": configured()}


def reserve(db) -> bool:
    """Take one request from this month's budget, atomically. False = do not send."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from app.models.evosense_models import EvoSenseApiUsage
    limit = monthly_limit()
    if limit <= 0:
        return False
    p = period()
    res = db.execute(text("UPDATE evosense_api_usage SET count = count + 1, updated_at = :now "
                          "WHERE provider_key = :k AND period = :p AND count < :lim"),
                     {"k": PROVIDER_KEY, "p": p, "lim": limit, "now": datetime.utcnow()})
    if res.rowcount == 1:
        db.commit()
        return True
    exists = (db.query(EvoSenseApiUsage.id).filter(EvoSenseApiUsage.provider_key == PROVIDER_KEY,
                                                  EvoSenseApiUsage.period == p).first())
    if exists:
        return False                                  # this month's budget is used up
    try:
        db.add(EvoSenseApiUsage(provider_key=PROVIDER_KEY, period=p, count=1, updated_at=datetime.utcnow()))
        db.commit()
        return True
    except IntegrityError:                            # another worker created the row first
        db.rollback()
        return reserve(db)


def fetch(address: str, *, prop_type: Optional[str] = None, beds=None, baths=None, sqft=None) -> Dict[str, Any]:
    params = {"address": address, "compCount": 10}
    types = {"single_family": "Single Family", "condo": "Condo", "townhouse": "Townhouse",
             "mobile_home": "Manufactured", "duplex": "Multi-Family", "triplex": "Multi-Family",
             "fourplex": "Multi-Family", "multifamily": "Multi-Family"}
    if prop_type in types:
        params["propertyType"] = types[prop_type]
    for k, v in (("bedrooms", beds), ("bathrooms", baths), ("squareFootage", sqft)):
        if v:
            params[k] = float(v)
    url = API + "?" + urllib.parse.urlencode(params)
    with B._request(url, {"X-Api-Key": os.environ[KEY_ENV], "Accept": "application/json"}) as r:
        body = r.read(5 * 1024 * 1024)
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "RentCast did not return JSON")


def label_comp(c: Dict[str, Any]) -> str:
    """What a RentCast comparable actually is - from its own fields."""
    status = (c.get("status") or "").strip().lower()
    if (c.get("listingType") or "").lower().find("rent") >= 0:
        return "Rental comparable"
    if status == "active":
        return "Listed comparable (asking price)"
    if c.get("removedDate"):
        return "Listed comparable (listing removed - sale price not confirmed)"
    return "Estimated market comparable"


def to_detail(body: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    comps = []
    for c in (body.get("comparables") or [])[:10]:
        comps.append({"address": c.get("formattedAddress"), "label": label_comp(c),
                      "price": c.get("price"), "status": c.get("status"),
                      "listed": (c.get("listedDate") or "")[:10] or None,
                      "removed": (c.get("removedDate") or "")[:10] or None,
                      "days_on_market": c.get("daysOnMarket"), "distance_miles": c.get("distance"),
                      "square_feet": c.get("squareFootage"), "bedrooms": c.get("bedrooms"),
                      "bathrooms": c.get("bathrooms"), "year_built": c.get("yearBuilt"),
                      "similarity": c.get("correlation")})
    return {"source": "RentCast automated valuation", "estimate": body.get("price"),
            "low": body.get("priceRangeLow"), "high": body.get("priceRangeHigh"),
            "truth": "ESTIMATE - modelled from nearby listings; not an appraisal and not a sale price",
            "comparables": comps, "at": (now or datetime.utcnow()).isoformat() + "Z"}


def fresh(prop) -> bool:
    src = (getattr(prop, "estimated_value_source", None) or "")
    at = getattr(prop, "estimated_value_at", None)
    return src.startswith(PROVIDER_KEY) and at is not None and at > datetime.utcnow() - timedelta(days=CACHE_DAYS)
