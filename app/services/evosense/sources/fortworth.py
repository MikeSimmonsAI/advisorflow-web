"""City of Fort Worth code-violation cases (public ArcGIS feature service).

    https://services5.arcgis.com/3ddLCBXe1bRt7mzj/arcgis/rest/services/
        CFW_Open_Data_Code_Violations_Table_view/FeatureServer/0

A case is a CODE_VIOLATION the city itself opened (not a resident complaint).
Only cases created in the last 24 months at the exact situs address count.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.services.evosense.sources import base as B

LAYER = ("https://services5.arcgis.com/3ddLCBXe1bRt7mzj/arcgis/rest/services/"
         "CFW_Open_Data_Code_Violations_Table_view/FeatureServer/0")
PAGE = "https://data.fortworthtexas.gov/"
FIELDS = ("Case_ID", "Violation_ID", "Violation_Address", "Complaint_Type_Description",
          "Violation_Current_Status", "Case_Current_Status", "Case_Created_Date", "Update_Date")


class FortWorthCodeReader:
    adapter_version = "fw_code_violations/2"

    def lookup(self, street: str, *, days: int = 730) -> Dict[str, Any]:
        s = re.sub(r"\s+", " ", (street or "").upper()).strip()
        if not s:
            return {"cases": []}
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        data = B.get_json(LAYER + "/query", {
            "where": "UPPER(Violation_Address) = '%s' AND Case_Created_Date >= DATE '%s'" % (
                s.replace("'", "''"), since),
            "outFields": ",".join(FIELDS), "orderByFields": "Case_Created_Date DESC",
            "resultRecordCount": 25, "f": "json"})
        if isinstance(data, dict) and data.get("error"):
            raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "ArcGIS error: %s" % str(data["error"])[:160])
        feats = (data or {}).get("features")
        if feats is None:
            raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "ArcGIS answer has no features")
        return {"cases": [f.get("attributes") or {} for f in feats]}

    def to_record(self, target: Dict[str, Any], cases: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """An OPEN city case is a current CODE_VIOLATION. A CLOSED case is code
        HISTORY: real, shown with its dates, never scored as a current
        violation. Each case is dated by the city's own created date."""
        if not cases:
            return None
        sigs, seen = [], set()
        for c in cases:
            cid = c.get("Case_ID")
            if cid in seen:
                continue
            seen.add(cid)
            ts = c.get("Case_Created_Date")
            created = datetime.utcfromtimestamp(ts / 1000.0) if isinstance(ts, (int, float)) else None
            status = (c.get("Case_Current_Status") or c.get("Violation_Current_Status") or "").strip()
            open_case = status.lower() not in ("closed", "")
            sigs.append({"type": "CODE_VIOLATION" if open_case else "CODE_HISTORY",
                         "confidence": 90 if open_case else 70,
                         "value": "Fort Worth case %s: %s (%s%s)" % (
                             cid, c.get("Complaint_Type_Description") or "violation", status.lower() or "status unknown",
                             ", opened %s" % created.strftime("%m/%d/%Y") if created else ""),
                         "raw": "CASE=%s;STATUS=%s" % (cid, status), "ref": "FWCODE:%s" % cid,
                         "effective_at": created.strftime("%Y-%m-%d") if created else None,
                         "evidence_basis": "city case created date",
                         "case_status": status.lower() or None,
                         "observed_days_ago": 0})
            if len(sigs) >= 5:
                break
        return {"source_reference": "FWCODE:%s" % cases[0].get("Case_ID"),
                "street_address": target.get("street_address"), "city": target.get("city"),
                "state": "TX", "zip_code": target.get("zip_code"), "county": target.get("county"),
                "parcel_apn": target.get("parcel_apn"), "signals": sigs,
                "_raw": cases[:5], "_source_url": PAGE, "_source_updated_at": None,
                "_adapter_version": self.adapter_version}
