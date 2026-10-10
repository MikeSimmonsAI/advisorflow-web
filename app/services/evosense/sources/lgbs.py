"""Property-tax foreclosure lawsuits and sales (Linebarger Goggan Blair & Sampson).

    https://taxsales.lgbs.com           public map and list
    https://taxsales.lgbs.com/api/property_sales/?county=DALLAS%20COUNTY&state=TX

Linebarger is the delinquent-tax law firm for most North Texas taxing units.
Its public list names every property it has taken to judgment for unpaid
property tax and is selling (or will sell) at the county's tax sale: address,
county account number, cause (case) number, adjudged value, minimum bid,
sale status and the firm's sale notes. robots.txt allows every agent.

What it can honestly tell EvoSense:
  * A tax-foreclosure JUDGMENT exists against this property (TAX_SUIT) and
    property tax is unpaid (TAX_DELINQUENT) - both public court facts.
  * Whether a sale is scheduled (date) or the property waits for a future sale.

What it does NOT say: the owner (it is joined from the appraisal district by
account number), any mortgage, or the amount owed beyond the minimum bid.

Kept: FUTURE SALE (judgment, not yet sold) and scheduled / pending SALE rows -
the owner still holds title. Skipped (counted, never ingested): STRUCK OFF and
"struck off to jurisdiction" (the county or city now owns it - no seller),
SOLD, and CANCELLED (usually paid off).
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services.evosense.sources import base as B

API = "https://taxsales.lgbs.com/api/property_sales/"
PAGE = "https://taxsales.lgbs.com/"
PAGE_SIZE = 200
MAX_PAGES = 30                      # 6,000 rows per county - a hard stop, never an endless crawl

# The counties EvoSense discovers in, as Linebarger names them.
COUNTIES = {"dallas": "DALLAS COUNTY", "tarrant": "TARRANT COUNTY", "collin": "COLLIN COUNTY",
            "denton": "DENTON COUNTY", "ellis": "ELLIS COUNTY", "kaufman": "KAUFMAN COUNTY",
            "rockwall": "ROCKWALL COUNTY", "parker": "PARKER COUNTY", "johnson": "JOHNSON COUNTY"}
DEFAULT_COUNTIES = ("dallas", "tarrant")

KEEP = "keep"


def classify(row: Dict[str, Any]) -> str:
    """keep | struck_off | sold | cancelled | other - by the firm's own words."""
    st = (row.get("status") or "").strip().lower()
    ty = (row.get("sale_type") or "").strip().upper()
    if "struck off" in st or ty == "STRUCK OFF":
        return "struck_off"
    if "sold" in st:
        return "sold"
    if "cancel" in st or "withdrawn" in st:
        return "cancelled"
    if ty == "FUTURE SALE" or "scheduled" in st or "pending" in st or "available" in st:
        return KEEP
    return "other"


def _money(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _date(v) -> Optional[datetime]:
    if not v:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(v)[:19], fmt)
        except ValueError:
            continue
    return None


def county_key(name: str) -> Optional[str]:
    n = re.sub(r"\s+county$", "", (name or "").strip().lower())
    return n if n in COUNTIES else None


def account(county: str, acct: str) -> str:
    """The appraisal-district account in the form the other adapters use:
    DCAD keeps its 17-character account; TAD drops leading zeros."""
    a = (acct or "").strip()
    if county == "tarrant":
        from app.services.evosense.sources.tarrant import apn
        return apn(a)
    return a


class LgbsTaxSaleReader:
    adapter_version = "lgbs_tax_sales/1"

    def fetch_county(self, county: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for page in range(MAX_PAGES):
            j = B.get_json(API, {"county": COUNTIES[county], "state": "TX",
                                 "limit": PAGE_SIZE, "offset": page * PAGE_SIZE})
            if not isinstance(j, dict) or "results" not in j:
                raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "taxsales.lgbs.com did not return a results list")
            rows.extend(j["results"] or [])
            if not j.get("next"):
                break
        return rows

    def discover(self, *, limit: int, counties=DEFAULT_COUNTIES, rotate: Optional[int] = None,
                 today: Optional[datetime] = None) -> Dict[str, Any]:
        """Up to `limit` properties under a tax-foreclosure judgment and still in
        private hands. Scheduled sales come first (they are time-bound), then
        the rest in a stable order; `rotate` (default: day of year) starts the
        window further along each day so every listed property is reached over
        a few hunts instead of the same first `limit` every time."""
        stats: Counter = Counter()
        today = today or datetime.utcnow()
        keep: List[Dict[str, Any]] = []
        for c in counties:
            if c not in COUNTIES:
                continue
            rows = self.fetch_county(c)
            stats["listed_%s" % c] = len(rows)
            for r in rows:
                kind = classify(r)
                stats[kind] += 1
                if kind != KEEP:
                    continue
                if not (r.get("prop_address_one") or "").strip() or not (r.get("account_nbr") or "").strip():
                    stats["no_address_or_account"] += 1
                    continue
                sale_at = _date(r.get("sale_date"))
                if sale_at and sale_at.date() < today.date():
                    # The sale day has passed and the list has not caught up:
                    # the property may already be sold. Not ingested as held.
                    stats["sale_date_passed"] += 1
                    continue
                keep.append(dict(r, _county=c))
        keep.sort(key=lambda r: (0 if _date(r.get("sale_date")) else 1,
                                 _date(r.get("sale_date")) or datetime.max, r.get("uid") or 0))
        n = len(keep)
        if n > limit:
            scheduled = [r for r in keep if _date(r.get("sale_date"))]
            rest = [r for r in keep if not _date(r.get("sale_date"))]
            day = datetime.utcnow().timetuple().tm_yday if rotate is None else rotate
            room = max(0, limit - len(scheduled))
            start = (day * room) % len(rest) if rest and room else 0
            window = (rest[start:] + rest[:start])[:room]
            keep = (scheduled + window)[:limit]
        stats["kept"] = n
        stats["records"] = len(keep)
        now = datetime.utcnow()
        return {"records": [self.record(r, now) for r in keep], "stats": dict(stats),
                "source_url": PAGE, "source_updated_at": now}

    def record(self, r: Dict[str, Any], as_of: datetime) -> Dict[str, Any]:
        c = r.get("_county") or county_key(r.get("county")) or ""
        acct = account(c, r.get("account_nbr"))
        sale_at = _date(r.get("sale_date"))
        minimum = _money(r.get("minimum_bid"))
        value = _money(r.get("value"))
        cause = (r.get("cause_nbr") or "").strip()
        notes = re.sub(r"\s+", " ", (r.get("sale_notes") or "")).strip()
        what = "tax-foreclosure judgment, cause %s" % (cause or "on file")
        if sale_at:
            what += "; tax sale scheduled %s" % sale_at.strftime("%b %d, %Y")
        else:
            what += "; awaiting a future tax sale"
        if minimum:
            what += "; minimum bid $%s" % format(minimum, ",.0f")
        raw = {k: r.get(k) for k in ("uid", "sale_id", "county", "cause_nbr", "account_nbr", "sale_type",
                                     "status", "sale_date", "value", "minimum_bid", "sale_notes",
                                     "prop_address_one", "prop_address_two", "prop_city", "prop_zipcode")}
        geo = (r.get("geometry") or {}).get("coordinates") or []
        street = re.sub(r"\s+", " ", (r.get("prop_address_one") or "")).strip()
        unit = (r.get("prop_address_two") or "").strip() or None
        rec = {
            "source_reference": "LGBS:%s:%s" % (r.get("uid"), cause or acct),
            "street_address": street or None, "unit": unit,
            "city": (r.get("prop_city") or "").strip().title() or None, "state": "TX",
            "zip_code": re.sub(r"\D", "", r.get("prop_zipcode") or "")[:5] or None,
            "county": c.title() if c else None, "parcel_apn": acct or None,
            "signals": [
                {"type": "TAX_SUIT", "confidence": 95, "value": what, "raw": "CAUSE=%s;STATUS=%s" % (cause, r.get("status")),
                 "effective_at": sale_at.strftime("%Y-%m-%d") if sale_at else None,
                 "evidence_basis": "tax-foreclosure judgment listed for sale by the taxing units' attorneys",
                 "observed_days_ago": 0},
                {"type": "TAX_DELINQUENT", "confidence": 90,
                 "value": "property taxes unpaid - sued and taken to judgment%s" % ("; %s" % notes if notes else ""),
                 "raw": "MIN_BID=%s;VALUE=%s" % (r.get("minimum_bid"), r.get("value")),
                 "evidence_basis": "tax-foreclosure judgment", "observed_days_ago": 0},
            ],
            "_raw": raw, "_source_url": PAGE, "_source_updated_at": as_of,
            "_adapter_version": self.adapter_version,
            "_evidence": {"cause": cause, "sale_type": r.get("sale_type"), "status": r.get("status"),
                          "sale_date": sale_at.strftime("%Y-%m-%d") if sale_at else None,
                          "adjudged_value": value, "minimum_bid": minimum, "notes": notes or None},
        }
        if len(geo) == 2 and all(isinstance(x, (int, float)) for x in geo):
            rec["longitude"], rec["latitude"] = geo[0], geo[1]
        return rec
