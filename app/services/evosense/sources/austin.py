"""City of Austin - Austin Code complaint cases (open data, Socrata 6wtj-zbtb).

    https://data.austintexas.gov/resource/6wtj-zbtb.json

Checked live 2026-10-22 (newest case opened that day). Fields used:
case_id, status (Active / Pending / Closed ...), house_number, street_name
("DUVAL ST"), zip_code, opened_date, date_updated, case_type, description,
priority, parcelid, repeatoffenderrelated.

A case is a COMPLAINT a city inspector works - not a confirmed violation. An
open case is a current code complaint (a lead to investigate); a closed one is
code history, shown and never scored as a current problem. The match is the
same house number and ZIP, then the same street (directions and suffixes
normalised) - a neighbour's case is never attached.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.services.evosense.sources import base as B

API = "https://data.austintexas.gov/resource/6wtj-zbtb.json"
PAGE = "https://data.austintexas.gov/d/6wtj-zbtb"
OPEN = ("active", "pending", "open", "new", "in progress", "assigned")


class AustinCodeReader:
    adapter_version = "austin_code/1"

    def lookup(self, street: str, zip_code: Optional[str], *, days: int = 730) -> List[Dict[str, Any]]:
        from app.services.evosense.sources.txgio import same_parcel_address
        m = re.match(r"^\s*(\d+)", street or "")
        if not m:
            return []
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
        where = "house_number = '%s' AND opened_date > '%s'" % (m.group(1), since)
        if zip_code:
            where += " AND zip_code = '%s'" % re.sub(r"\D", "", zip_code)[:5]
        rows = B.get_json(API, {"$where": where, "$limit": 50, "$order": "opened_date DESC"})
        if not isinstance(rows, list):
            raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "Austin code cases did not return a list")
        return [r for r in rows
                if same_parcel_address(street, "%s %s" % (r.get("house_number") or "", r.get("street_name") or ""))]

    def to_record(self, target: Dict[str, Any], cases: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not cases:
            return None
        sigs = []
        for c in cases[:5]:
            opened = (c.get("opened_date") or "")[:10]
            try:
                age = max(0, (datetime.utcnow() - datetime.strptime(opened, "%Y-%m-%d")).days)
            except ValueError:
                age = 0
            status = (c.get("status") or "").strip()
            is_open = status.lower() in OPEN
            what = c.get("description") or c.get("case_type") or "code case"
            repeat = " · repeat offender" if (c.get("repeatoffenderrelated") or "").lower() == "yes" else ""
            sigs.append({"type": "CODE_COMPLAINT" if is_open else "CODE_HISTORY",
                         "confidence": 55 if is_open else 50,
                         "value": "Austin Code case %s: %s (opened %s) - %s%s" % (
                             c.get("case_id"), what, opened, status.lower() or "status unknown", repeat),
                         "raw": "CASE=%s;STATUS=%s;PRIORITY=%s" % (c.get("case_id"), status, c.get("priority")),
                         "observed_days_ago": age, "effective_at": opened or None,
                         "evidence_basis": "Austin Code case opened date (a complaint, not a confirmed violation)",
                         "case_status": status.lower() or None,
                         "ref": "AUSCODE:%s" % c.get("case_id")})
        newest = cases[0]
        return {"source_reference": "AUSCODE:%s" % newest.get("case_id"),
                "street_address": target.get("street_address"), "city": target.get("city") or "Austin",
                "state": "TX", "zip_code": target.get("zip_code"), "county": target.get("county") or "Travis",
                "parcel_apn": target.get("parcel_apn"), "signals": sigs,
                "_raw": cases[:5], "_source_url": PAGE, "_source_updated_at": (newest.get("date_updated") or "")[:10] or None,
                "_adapter_version": self.adapter_version,
                "_evidence": {"note": "Austin Code complaint cases are leads for investigation, not confirmed violations",
                              "parcelid": newest.get("parcelid")}}
