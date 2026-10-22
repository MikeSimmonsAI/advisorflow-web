"""Texas statewide owner of record: the TxGIO StratMap land-parcel layer.

    https://feature.geographic.texas.gov/arcgis/rest/services/Parcels/
        stratmap_land_parcels_48_most_recent/MapServer

The Texas Geographic Information Office assembles every county appraisal
district's parcel roll into one statewide layer (all 254 counties): owner
name, mailing address, situs address, the district's account number, county
and tax year (2026 records in October 2026). Its `query` operation is switched
off, but `identify` (what parcel is at this point?) answers.

So a lookup is two free public calls:
    1. the U.S. Census geocoder places the address (unique match, same county);
    2. `identify` returns the parcel at that point.

The parcel is accepted ONLY when its own situs address has the same house
number and street as the property. A geocoded point can land on the parcel
next door; a neighbour's owner is never recorded. Anything short of that is
NO_MATCH - UNKNOWN, not a guess.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.services.evosense.sources import base as B

SERVICE = ("https://feature.geographic.texas.gov/arcgis/rest/services/Parcels/"
           "stratmap_land_parcels_48_most_recent/MapServer")
IDENTIFY = SERVICE + "/identify"
PAGE = "https://geographic.texas.gov/"


def _v(a: Dict[str, Any], k: str) -> Optional[str]:
    v = a.get(k)
    if v is None:
        return None
    v = re.sub(r"\s+", " ", str(v)).strip()
    return None if not v or v.upper() in ("NULL", "NONE") else v


def _excel_date(v) -> Optional[str]:
    """DATE_ACQ is an Excel serial day (46235 -> 2026-08-01)."""
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    if not 30000 < n < 80000:
        return None
    return (datetime(1899, 12, 30) + timedelta(days=n)).strftime("%Y-%m-%d")


def _core(street: Optional[str]):
    """('1015', 'HEIGHTSBLVD') - house number and the street without spaces,
    directions and the trailing suffix, for a strict same-address test."""
    from app.services.evosense.identity import _split_suffix, normalize_street
    norm = normalize_street(street or "")[0]
    if not norm:
        return None, ""
    toks = norm.split()
    if not toks or not re.match(r"^\d+[A-Z]?$", toks[0]):
        return None, ""
    num = toks[0]
    rest, _sfx = _split_suffix(" ".join(toks))
    words = [t for t in rest.split()[1:] if t not in ("N", "S", "E", "W", "NE", "NW", "SE", "SW")]
    return num, "".join(words)


def same_parcel_address(target_street: str, situs: Optional[str], city: Optional[str] = None) -> bool:
    """Same house number, and the parcel's street begins with the property's
    street name (the layer sometimes runs city/state into the situs text)."""
    if not situs:
        return False
    text = situs.split(",")[0]
    if city:
        text = re.sub(r"\s+%s\s*$" % re.escape(city.upper()), "", text.upper())
    n1, c1 = _core(target_street)
    n2, c2 = _core(text)
    if not (n1 and c1 and n2 and c2) or n1 != n2:
        return False
    return c2 == c1 or c2.startswith(c1) or c1.startswith(c2)


class TxParcelReader:
    adapter_version = "txgio_stratmap_parcels/1"

    # (half-extent in degrees, tolerance in pixels of an 800 px map): the
    # first is the parcel under the point; the second reaches ~80 m, because
    # a Census point is interpolated along the street centerline and often
    # lands in the road or on the lot next door. The same-address test below
    # is what makes a wider search safe.
    REACH = ((0.0015, 2), (0.003, 100))

    def identify(self, lon: float, lat: float, d: float = 0.0015, tolerance: int = 2) -> list:
        j = B.get_json(IDENTIFY, {"geometry": '{"x":%s,"y":%s}' % (lon, lat),
                                  "geometryType": "esriGeometryPoint", "sr": "4326", "layers": "all",
                                  "tolerance": str(tolerance),
                                  "mapExtent": "%s,%s,%s,%s" % (lon - d, lat - d, lon + d, lat + d),
                                  "imageDisplay": "800,800,96", "returnGeometry": "false", "f": "json"})
        if not isinstance(j, dict) or "results" not in j:
            raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "TxGIO parcels identify did not return results")
        return [r.get("attributes") or {} for r in j["results"] or []]

    def lookup(self, target: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from app.services.evosense.sources.census import CensusGeocoder
        street = target.get("street_address")
        if not street or not (target.get("city") or target.get("zip_code")):
            return None
        g = CensusGeocoder()
        res = g.geocode(street, target.get("city"), target.get("state") or "TX",
                        target.get("zip_code"), target.get("county"))
        if len(res["kept"]) != 1:
            return None
        m, cty = res["kept"][0]
        xy = m.get("coordinates") or {}
        if xy.get("x") is None or xy.get("y") is None:
            return None
        for d, tol in self.REACH:
            parcels = [a for a in self.identify(xy["x"], xy["y"], d, tol)
                       if same_parcel_address(street, _v(a, "SITUS_ADDR"), _v(a, "SITUS_CITY") or target.get("city"))]
            if len(parcels) == 1:
                return self.record(target, parcels[0], cty, xy)
            if len(parcels) > 1:
                return None                               # ambiguous: never a guess
        return None

    def record(self, target, a: Dict[str, Any], county_name: str, xy) -> Dict[str, Any]:
        from app.services.evosense.sources.tarrant import state_code
        name = re.sub(r"\s*&\s*$", "", _v(a, "OWNER_NAME") or "").strip()
        owner = None
        if name and "CONFIDENTIAL" not in name.upper():
            mstate = (_v(a, "MAIL_STAT") or "").upper()
            mcity = _v(a, "MAIL_CITY") or ""
            m2 = re.match(r"^(.*?),\s*([A-Z]{2})$", mcity.upper())
            if m2:                                         # "ARLINGTON, TX" with MAIL_STAT wrong
                mcity, mstate = m2.group(1), m2.group(2)
            owner = {"name": name,
                     "mailing_street": B.mailing_line(_v(a, "MAIL_LINE1"), _v(a, "MAIL_LINE2")),
                     "mailing_city": mcity.title() or None,
                     "mailing_state": state_code(mstate) if mstate else None,
                     "mailing_zip": re.sub(r"\D", "", _v(a, "MAIL_ZIP") or "")[:5] or None}
        county = (_v(a, "COUNTY") or county_name or target.get("county") or "").title() or None
        acq = _excel_date(a.get("DATE_ACQ"))
        prop_id = _v(a, "PROP_ID")
        return {
            "source_reference": "TXGIO:%s:%s:%s" % (county or "", prop_id or target.get("id"), _v(a, "TAX_YEAR") or ""),
            "street_address": target.get("street_address"), "unit": target.get("unit"),
            "city": target.get("city"), "state": "TX", "zip_code": target.get("zip_code"),
            # The state layer's account id is kept as evidence, never written as the
            # property's APN: each county source has its own account format.
            "county": county, "parcel_apn": target.get("parcel_apn"),
            "latitude": xy.get("y"), "longitude": xy.get("x"),
            "owner": owner, "signals": [],
            "_raw": {k: a.get(k) for k in ("PROP_ID", "GEO_ID", "OWNER_NAME", "NAME_CARE", "SITUS_ADDR",
                                           "SITUS_CITY", "SITUS_ZIP", "MAIL_ADDR", "MAIL_LINE1", "MAIL_LINE2",
                                           "MAIL_CITY", "MAIL_STAT", "MAIL_ZIP", "COUNTY", "TAX_YEAR",
                                           "DATE_ACQ", "SOURCE") if k in a},
            "_source_url": SERVICE, "_source_updated_at": acq,
            "_adapter_version": self.adapter_version,
            "_evidence": {"situs": _v(a, "SITUS_ADDR"), "tax_year": _v(a, "TAX_YEAR"),
                          "data_acquired": acq, "county_account": prop_id,
                          "matched_by": "Census geocode + parcel at that point + same situs address"},
        }
