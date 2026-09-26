"""U.S. Census Bureau geocoder: situs ZIP and coordinates for an address.

Free, public, no key: https://geocoding.geo.census.gov/geocoder/
A match is accepted only when it is unique AND falls in the county the
property is already known to be in. Anything else is NO_MATCH, never a guess.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from app.services.evosense.sources import base as B

URL = "https://geocoding.geo.census.gov/geocoder/geographies/address"
PAGE = "https://geocoding.geo.census.gov/geocoder/"


class CensusGeocoder:
    adapter_version = "census_geocoder/1"

    def geocode(self, street: str, city: Optional[str], state: str, zip_code: Optional[str],
                county: Optional[str]) -> Dict[str, Any]:
        params = {"street": street, "state": state or "TX", "benchmark": "Public_AR_Current",
                  "vintage": "Current_Current", "layers": "Counties", "format": "json"}
        if city:
            params["city"] = city
        if zip_code:
            params["zip"] = zip_code[:5]
        data = B.get_json(URL, params)
        try:
            matches = data["result"]["addressMatches"]
        except (KeyError, TypeError):
            raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "census geocoder answer has no addressMatches")
        want = re.sub(r"\s+county$", "", (county or "").strip().lower())
        from app.services.evosense.identity import normalize_street
        mine = normalize_street(street)[0]
        keep = []
        for m in matches:
            cty = ((m.get("geographies") or {}).get("Counties") or [{}])[0].get("BASENAME", "")
            if want and cty.strip().lower() != want:
                continue
            # The geocoder is fuzzy ("1905 ALSTON ST" can come back as "1905
            # ALSTON AVE"). Only the same normalized street counts.
            theirs = normalize_street((m.get("matchedAddress") or "").split(",")[0])[0]
            if mine and theirs != mine:
                continue
            keep.append((m, cty))
        return {"matches": len(matches), "kept": keep, "raw": matches[:3], "params": params}

    def to_record(self, target: Dict[str, Any], result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if len(result["kept"]) != 1:
            return None
        m, cty = result["kept"][0]
        comp = m.get("addressComponents") or {}
        coords = m.get("coordinates") or {}
        zip5 = (comp.get("zip") or "")[:5] or None
        return {"source_reference": "CENSUS:%s:%s" % (target.get("parcel_apn") or target.get("id"), zip5),
                "street_address": target.get("street_address"),
                "city": target.get("city") or (comp.get("city") or "").title() or None,
                "state": target.get("state") or "TX", "zip_code": zip5,
                "county": target.get("county"), "parcel_apn": target.get("parcel_apn"),
                "latitude": coords.get("y"), "longitude": coords.get("x"), "signals": [],
                "_raw": {"matchedAddress": m.get("matchedAddress"), "addressComponents": comp,
                         "coordinates": coords, "county": cty, "query": result["params"]},
                "_source_url": PAGE, "_source_updated_at": None,
                "_adapter_version": self.adapter_version,
                "_evidence": {"matched_address": m.get("matchedAddress"), "county": cty}}
