"""Denton County, Texas: the county's own public GIS parcel layer, which carries
the Denton Central Appraisal District's roll fields.

    Denton County GIS  https://gis.dentoncounty.gov/arcgis/rest/services/CAD/MapServer/0
                       (ArcGIS REST "query" - the service's published programmatic interface)

WHY NOT THE DENTON CAD WEBSITE. The appraisal district's property search says it
is not for bulk transfer, and the district publishes no free bulk export. So
EvoSense never touches that site. It asks the county's public map service the
same small questions a map viewer does: one property by ID, or one page (at
most 1,000 rows, the service's own cap) of houses matching a filter.

What the layer can honestly tell EvoSense: owner of record (unless marked
confidential), mailing address, situs, state property-category code (A1 =
single-family), certified market value and main improvement value, homestead
(exemption codes), year built, living area. It carries NO deed date: only the
recording instrument number, whose prefix is the recording YEAR ("2018-137347",
"05-94548"). EvoSense uses that year - and says it is a year, not a date.
Bedrooms and bathrooms are not published: they stay UNKNOWN.
"""
from __future__ import annotations

import re
import urllib.parse
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services.evosense.sources import base as B

LAYER = "https://gis.dentoncounty.gov/arcgis/rest/services/CAD/MapServer/0"
PAGE_URL = "https://gis.dentoncounty.gov/arcgis/rest/services/CAD/MapServer/0"
MAX_PAGE = 1000
FIELDS = ("prop_id,PID,prop_val_yr,owner_name,confidential,state_cd,situs_num,situs_street_prefx,situs_street,"
          "situs_street_sufix,situs_city,situs_zip,situs,addr_line1,addr_line2,addr_line3,addr_city,addr_state,"
          "addr_zip,addrCountry,yr_blt,living_area,cert_mkt_val,cert_appr_val,main_imprv_val,exemptions,"
          "instrumentNum,deedType,property_url")


def _i(v) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _q(v: str) -> str:
    return "'%s'" % str(v).replace("'", "''")


def deed_year(instrument: Optional[str], today: Optional[datetime] = None) -> Optional[int]:
    """The recording YEAR from an instrument number's prefix, or None."""
    m = re.match(r"^\s*(\d{2}|\d{4})-\d+", instrument or "")
    if not m:
        return None
    y = int(m.group(1))
    now = (today or datetime.utcnow()).year
    if y < 100:
        y += 2000 if y <= now % 100 else 1900
    return y if 1900 < y <= now else None


def property_type(row: Dict[str, Any]) -> Optional[str]:
    codes = [c.strip().upper() for c in (row.get("state_cd") or "").split(",") if c.strip()]
    if not codes:
        return None
    first = codes[0]
    if first.startswith("A"):
        return "single_family" if (_i(row.get("main_imprv_val")) or 0) > 0 or row.get("living_area") else "land"
    if first.startswith("B"):
        return "multifamily"
    if first.startswith(("C", "D", "E")):
        return "land"
    if first.startswith(("F", "L", "J")):
        return "commercial"
    if first.startswith("M"):
        return "mobile_home"
    return None


class DentonReader:
    adapter_version = "denton_county_gis/1"

    def _query(self, params: Dict[str, Any]) -> Dict[str, Any]:
        p = {"f": "json", "returnGeometry": "false"}
        p.update(params)
        j = B.get_json(LAYER + "/query", p)
        if not isinstance(j, dict) or ("features" not in j and "count" not in j):
            if isinstance(j, dict) and j.get("error"):
                raise B.SourceError(B.AUTH_FAILED if j["error"].get("code") in (498, 499) else
                                    B.SOURCE_FORMAT_CHANGED, "Denton County GIS: %s" % j["error"].get("message"))
            raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "Denton County GIS did not return features")
        return j

    def count(self, where: str = "1=1") -> int:
        return _i(self._query({"where": where, "returnCountOnly": "true"}).get("count")) or 0

    def lookup_many(self, accounts) -> Dict[str, Any]:
        """By Denton CAD property ID (the number the district and tax lists use)."""
        wanted = [re.sub(r"\D", "", str(a or "")) for a in accounts]
        wanted = [a for a in wanted if a]
        stats: Counter = Counter()
        recs: Dict[str, Dict[str, Any]] = {}
        for i in range(0, len(wanted), 100):
            chunk = wanted[i:i + 100]
            j = self._query({"where": "prop_id IN (%s)" % ",".join(chunk), "outFields": FIELDS,
                             "resultRecordCount": MAX_PAGE})
            stats["calls"] += 1
            for f in j.get("features") or []:
                a = f.get("attributes") or {}
                key = str(a.get("prop_id"))
                if key in chunk and key not in recs:
                    recs[key] = self.record(a)
        stats["matched"] = len(recs)
        return {"records": recs, "stats": dict(stats), "source_url": PAGE_URL}

    def discover(self, *, limit: int, houses_only: bool = True, absentee: bool = True,
                 min_years: int = 10, cities: Optional[List[str]] = None, zips: Optional[List[str]] = None,
                 rotate: Optional[int] = None, today: Optional[datetime] = None) -> Dict[str, Any]:
        """Houses whose owner gets the tax bill somewhere else (no homestead
        exemption, a different mailing city), recorded at least `min_years` ago
        by instrument-number year. One page per hunt; `rotate` (default day of
        year) moves the window so successive hunts reach new owners."""
        today = today or datetime.utcnow()
        where = ["confidential = 0"]
        if houses_only:
            where += ["state_cd LIKE 'A1%'", "main_imprv_val > 0"]
        if absentee:
            where += ["(exemptions IS NULL OR exemptions NOT LIKE '%HS%')",
                      "UPPER(addr_city) <> UPPER(situs_city)"]
        if cities:
            where.append("UPPER(situs_city) IN (%s)" % ",".join(_q(c.upper()) for c in cities))
        if zips:
            where.append("(%s)" % " OR ".join("situs_zip LIKE %s" % _q(z[:5] + "%") for z in zips))
        w = " AND ".join(where)
        total = self.count(w)
        page = max(1, min(limit * 3, MAX_PAGE))            # over-fetch: the year filter is applied here
        day = rotate if rotate is not None else today.timetuple().tm_yday
        offset = (day * page) % total if total > page else 0
        j = self._query({"where": w, "outFields": FIELDS, "orderByFields": "prop_id",
                         "resultOffset": offset, "resultRecordCount": page})
        from app.services.evosense.ingest import owner_type_of
        stats = Counter(matching=total, offset=offset)
        recs = []
        for f in j.get("features") or []:
            a = f.get("attributes") or {}
            y = deed_year(a.get("instrumentNum"), today)
            if y is None or today.year - y < max(0, min_years):
                stats["recent_or_unknown_deed"] += 1
                continue
            if owner_type_of(a.get("owner_name")) == "government":
                stats["government"] += 1
                continue
            recs.append(self.record(a, today=today))
            if len(recs) >= limit:
                break
        stats["records"] = len(recs)
        return {"records": recs, "stats": dict(stats), "source_url": PAGE_URL, "source_updated_at": None}

    def record(self, a: Dict[str, Any], today: Optional[datetime] = None) -> Dict[str, Any]:
        pid = str(a.get("prop_id") or a.get("PID") or "").strip()
        yr = _i(a.get("prop_val_yr"))
        street = re.sub(r"\s+", " ", " ".join(x for x in (
            a.get("situs_num"), a.get("situs_street_prefx"), a.get("situs_street"), a.get("situs_street_sufix"))
            if x)).strip() or None
        confidential = bool(_i(a.get("confidential")))
        owner = None
        name = re.sub(r"\s+", " ", (a.get("owner_name") or "")).strip()
        if name and not confidential:
            from app.services.evosense.sources.tarrant import state_code
            owner = {"name": name,
                     "mailing_street": B.mailing_line(a.get("addr_line1"), a.get("addr_line2"), a.get("addr_line3")),
                     "mailing_city": (a.get("addr_city") or "").strip().title() or None,
                     "mailing_state": state_code((a.get("addr_state") or "").strip().upper()),
                     "mailing_zip": re.sub(r"\D", "", a.get("addr_zip") or "")[:5] or None}
        homestead = "HS" in [c.strip().upper() for c in (a.get("exemptions") or "").split(",")]
        dy = deed_year(a.get("instrumentNum"), today)
        built = _i(a.get("yr_blt"))
        rec = {
            "source_reference": "DENTON:%s:%s" % (pid, yr or "na"),
            "street_address": street, "unit": None,
            "city": (a.get("situs_city") or "").strip().title() or None, "state": "TX",
            "zip_code": re.sub(r"\D", "", a.get("situs_zip") or "")[:5] or None,
            "county": "Denton", "parcel_apn": pid,
            "property_type": property_type(a),
            "bedrooms": None, "bathrooms": None, "half_bathrooms": None,    # not published
            "square_feet": _i(a.get("living_area")) or None,
            "year_built": built if built and built > 1800 else None,
            # YEAR precision only - from the recording instrument number
            "deed_transfer_date": "%s-01-01" % dy if dy else None,
            "occupancy": "owner_occupied" if homestead else None,
            "owner": owner, "signals": [],
            "_raw": {k: v for k, v in a.items() if not (confidential and str(k).startswith(("owner", "addr")))},
            "_source_url": a.get("property_url") or PAGE_URL, "_source_updated_at": None,
            "_adapter_version": self.adapter_version,
            "_evidence": {"homestead": homestead, "owner_confidential": confidential,
                          "deed_basis": ("recording year %s from instrument %s (year only, not a date)"
                                         % (dy, a.get("instrumentNum"))) if dy else None,
                          "state_cd": a.get("state_cd")},
        }
        total = _i(a.get("cert_mkt_val")) or _i(a.get("cert_appr_val"))
        if total:
            # The appraisal district's TAX value. Never a market value, never an ARV.
            rec["appraisal"] = {"value": total, "land": None, "improvements": _i(a.get("main_imprv_val")),
                                "year": yr, "district": "Denton CAD",
                                "basis": "Denton CAD %s certified market value via Denton County GIS "
                                         "(property-tax value)" % (yr or "")}
        return rec
