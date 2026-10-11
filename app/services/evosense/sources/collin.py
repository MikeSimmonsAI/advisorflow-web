"""Collin County, Texas: the Collin Central Appraisal District (Collin CAD)
appraisal roll, published free by the district on the State of Texas open-data
portal (data.texas.gov, Socrata) - one dataset per appraisal year plus a
preliminary one. Queried with the portal's own API (SoQL), a page at a time;
nothing is scraped and no bulk download is needed.

    Collin CAD open data   https://collincad.org/open-data-portal/
    Appraisal Data - 2026  https://data.texas.gov/d/5tkr-3759
    Preliminary            https://data.texas.gov/d/nne4-8riu

What Collin CAD can honestly tell EvoSense: owner of record and mailing
address, situs, property category (A = single-family residence), the
district's TAX values (land / improvements / market), homestead, the last deed
date (no price - Texas deed records carry none), year built and main living
area. It does NOT publish bedrooms or bathrooms: those stay UNKNOWN.

Each row says whether its values are Preliminary or Certified (propStatus);
that label travels with the value and is never dropped.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.services.evosense.sources import base as B

PORTAL = "https://data.texas.gov"
COLLIN_PAGE = "https://collincad.org/open-data-portal/"
# Appraisal-year datasets the district publishes on the portal. The newest year
# that answers is used; EVOSENSE_COLLIN_DATASET pins one.
YEAR_DATASETS = {2026: "5tkr-3759", 2025: "vffy-snc6", 2024: "6dqt-e958"}
PRELIMINARY_DATASET = "nne4-8riu"
CATALOG = "https://api.us.socrata.com/api/catalog/v1"
PAGE = 1000
RESIDENTIAL = ("A",)          # Collin CAD state category A: single-family residence
MULTI = ("B",)                # B: multifamily
LAND = ("C1", "C2", "D1", "D2", "E")   # vacant lots, ag / rural land


def resource(dataset: str) -> str:
    return "%s/resource/%s.json" % (PORTAL, dataset)


def _q(v: str) -> str:
    return "'%s'" % str(v).replace("'", "''")


def _i(v) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _date(v) -> Optional[datetime]:
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def property_type(row: Dict[str, Any]) -> Optional[str]:
    """From the district's own category code - never guessed from a value."""
    cat = (row.get("propcategorycode") or "").strip().upper()
    if cat in RESIDENTIAL:
        return "single_family" if (_i(row.get("currvalimprv")) or 0) > 0 or row.get("imprvmainarea") else "land"
    if cat in MULTI:
        return "multifamily"
    if cat in LAND or cat.startswith("C"):
        return "land"
    if cat.startswith(("F", "L", "J")):
        return "commercial"
    return None


class CollinReader:
    adapter_version = "collin_cad_socrata/1"

    def __init__(self, dataset: Optional[str] = None):
        self._dataset = dataset or os.environ.get("EVOSENSE_COLLIN_DATASET")

    def dataset(self) -> str:
        """The newest appraisal-year dataset the portal answers for."""
        if self._dataset:
            return self._dataset
        year = datetime.utcnow().year
        for y in (year, year - 1, year - 2):
            ds = YEAR_DATASETS.get(y)
            if ds:
                self._dataset = ds
                return ds
        self._dataset = PRELIMINARY_DATASET
        return self._dataset

    def _get(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = B.get_json(resource(self.dataset()), params)
        if not isinstance(rows, list):
            raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "Collin CAD data did not return a list")
        return rows

    # ── lookup by account (a property another source found) ───────────────
    def lookup_many(self, accounts) -> Dict[str, Any]:
        """By Collin CAD geographic ID (R-xxxx-xxx-xxxx-x, the id tax-sale lists
        use) or numeric property ID."""
        wanted = [(a or "").strip() for a in accounts if (a or "").strip()]
        stats: Counter = Counter()
        recs: Dict[str, Dict[str, Any]] = {}
        for i in range(0, len(wanted), 50):
            chunk = wanted[i:i + 50]
            geo = [a for a in chunk if not a.isdigit()]
            pid = [a for a in chunk if a.isdigit()]
            parts = []
            if geo:
                parts.append("geoid in(%s)" % ",".join(_q(a) for a in geo))
            if pid:
                parts.append("propid in(%s)" % ",".join(pid))
            rows = self._get({"$where": " OR ".join(parts), "$limit": PAGE})
            stats["calls"] += 1
            for r in rows:
                key = r.get("geoid") if r.get("geoid") in chunk else str(r.get("propid"))
                if key in chunk:
                    recs[key] = self.record(r)
        stats["matched"] = len(recs)
        return {"records": recs, "stats": dict(stats), "source_url": COLLIN_PAGE}

    # ── discovery ──────────────────────────────────────────────────────────
    def discover(self, *, limit: int, min_years: int = 10, houses_only: bool = True,
                 absentee: bool = True, cities: Optional[List[str]] = None,
                 zips: Optional[List[str]] = None, rotate: Optional[int] = None,
                 today: Optional[datetime] = None) -> Dict[str, Any]:
        """Residences held a long time by owners who do not live there:
        no homestead exemption, owner's mailing city differs from the property's,
        last deed at least `min_years` old. Oldest deeds first; `rotate` (default
        day of year) moves the window so successive hunts reach new owners."""
        today = today or datetime.utcnow()
        cut = (today - timedelta(days=365 * max(1, min_years))).strftime("%Y-%m-%d")
        where = ["deedeffdate < %s" % _q(cut)]
        if houses_only:
            where += ["propcategorycode = 'A'", "currvalimprv > 0"]
        if absentee:
            where += ["exempthmstdflag = false", "upper(owneraddrcity) != upper(situscity)"]
        if cities:
            where.append("upper(situscity) in(%s)" % ",".join(_q(c.upper()) for c in cities))
        if zips:
            where.append("situszip in(%s)" % ",".join(_q(z[:5]) for z in zips))
        w = " AND ".join(where)
        total = _i((self._get({"$select": "count(*)", "$where": w}) or [{}])[0].get("count")) or 0
        day = rotate if rotate is not None else today.timetuple().tm_yday
        offset = (day * limit) % total if total > limit else 0
        rows = self._get({"$where": w, "$order": "deedeffdate ASC, propid ASC",
                          "$limit": max(1, min(limit, PAGE)), "$offset": offset})
        from app.services.evosense.ingest import owner_type_of
        recs, stats = [], Counter(matching=total, offset=offset)
        for r in rows:
            if owner_type_of(r.get("ownername")) == "government":
                stats["government"] += 1
                continue
            recs.append(self.record(r))
        stats["records"] = len(recs)
        return {"records": recs, "stats": dict(stats), "source_url": resource(self.dataset()),
                "source_updated_at": None}

    def count(self) -> int:
        return _i((self._get({"$select": "count(*)"}) or [{}])[0].get("count")) or 0

    # ── one row -> one EvoSense record ─────────────────────────────────────
    def record(self, r: Dict[str, Any]) -> Dict[str, Any]:
        pid = str(r.get("propid") or "").strip()
        geo = (r.get("geoid") or "").strip() or pid
        yr = _i(r.get("currvalyear")) or _i(r.get("propyear"))
        status = (r.get("propstatus") or "").strip() or None          # Preliminary / Certified
        street = re.sub(r"\s+", " ", " ".join(x for x in (
            r.get("situsbldgnum"), r.get("situsstreetprefix"), r.get("situsstreetname"),
            r.get("situsstreetsuffix")) if x)).strip() or None
        zip5 = re.sub(r"\D", "", r.get("situszip") or "")[:5] or None
        owner = None
        name = " ".join(x.strip() for x in (r.get("ownername"), r.get("ownernameaddtl")) if x and x.strip())
        name = re.sub(r"\s*&\s*$", "", name).strip()
        if name and "CONFIDENTIAL" not in name.upper():
            from app.services.evosense.sources.tarrant import state_code
            owner = {"name": name,
                     "mailing_street": B.mailing_line(r.get("owneraddrline1"), r.get("owneraddrline2")),
                     "mailing_city": (r.get("owneraddrcity") or "").strip().title() or None,
                     "mailing_state": state_code((r.get("owneraddrstate") or "").strip().upper()),
                     "mailing_zip": re.sub(r"\D", "", r.get("owneraddrzip") or "")[:5] or None}
        deed = _date(r.get("deedeffdate")) or _date(r.get("deedfiledate"))
        homestead = bool(r.get("exempthmstdflag"))
        built = _i(r.get("imprvyearbuilt"))
        rec = {
            "source_reference": "CCAD:%s:%s" % (pid or geo, yr or "na"),
            "street_address": street, "unit": (r.get("situsunit") or "").strip() or None,
            "city": (r.get("situscity") or "").strip().title() or None, "state": "TX",
            "zip_code": zip5, "county": "Collin", "parcel_apn": geo,
            "property_type": property_type(r),
            "bedrooms": None, "bathrooms": None, "half_bathrooms": None,   # not published by Collin CAD
            "square_feet": _i(r.get("imprvmainarea")) or None,
            "year_built": built if built and built > 1800 else None,
            # a DEED date - the record states no price and no sale
            "deed_transfer_date": deed.strftime("%Y-%m-%d") if deed else None,
            "occupancy": "owner_occupied" if homestead else None,
            "owner": owner,
            "signals": [],
            "_raw": {k: v for k, v in r.items() if not k.startswith(":")},
            "_source_url": "%s/d/%s" % (PORTAL, self.dataset()),
            "_source_updated_at": (r.get("datadate") or "")[:19] or None,
            "_adapter_version": self.adapter_version,
            "_evidence": {"homestead": homestead, "value_status": status,
                          "category": r.get("propcategorycode"), "dataset": self.dataset()},
        }
        total = _i(r.get("currvalmarket")) or _i(r.get("currvalappraised"))
        if total:
            # The appraisal district's TAX value. Never a market value, never an ARV.
            rec["appraisal"] = {"value": total, "land": _i(r.get("currvalland")),
                                "improvements": _i(r.get("currvalimprv")), "year": yr,
                                "district": "Collin CAD",
                                "basis": "Collin CAD %s %s appraisal (property-tax value)"
                                         % (yr or "", (status or "").lower() or "roll")}
        return rec
