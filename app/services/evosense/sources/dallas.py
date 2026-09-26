"""Dallas County, Texas: the Dallas Central Appraisal District (DCAD) certified
export and the City of Dallas 311 service-request feed (Code Compliance).

    DCAD   https://www.dallascad.org/DataProducts.aspx   (free zip, no account)
    311    https://www.dallasopendata.com  dataset d7e7-envw (Socrata, public)

What DCAD can honestly tell EvoSense: owner of record, situs, the appraisal
district's TAX value (land + improvements), homestead, the last DEED TRANSFER
date (no price: Texas deed records carry none), and the appraiser's CDU rating.

CDU is Condition, Desirability and Utility - ONE composite appraiser opinion
of a building, rated for the appraisal year. POOR / VERY POOR is recorded as
CDU_POOR; UNDESIRABLE (which speaks to desirability, not physical distress) as
CDU_UNDESIRABLE. Neither is called "distressed", and each is dated to the
appraisal year, not to the day EvoSense read the file.

Owners DCAD marks EXCLUDE_OWNER = Y (confidential under Texas Tax Code
§25.025) are never read into EvoSense: the property may be discovered, the
owner stays UNKNOWN.

Dallas County tax delinquency and foreclosure notices are NOT offered as a
free bulk file; they are registered as MANUAL ONLY (see providers.py).
"""
from __future__ import annotations

import csv
import io
import re
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.services.evosense.sources import base as B

DCAD_PAGE = "https://www.dallascad.org/DataProducts.aspx"
DCAD_BASE = "https://www.dallascad.org/ViewPDFs.aspx?type=3&id="
DCAD_PATH = r"\\DCAD.ORG\WEB\WEBDATA\WEBFORMS\DATA PRODUCTS\DCAD{year}_CURRENT.ZIP"
DISTRESSED_CDU = {"POOR": 75, "VERY POOR": 85, "UNDESIRABLE": 80}   # the CDU ratings discovery reads
CDU_SIGNAL = {"POOR": "CDU_POOR", "VERY POOR": "CDU_POOR", "UNDESIRABLE": "CDU_UNDESIRABLE"}
SFR_SPTD = ("A11",)          # DCAD SPTD code for single-family residence


def _csv_rows(zf, member):
    names = {n.upper(): n for n in zf.namelist()}
    real = names.get(member.upper())
    if real is None:
        raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "DCAD archive has no %s" % member)
    with zf.open(real) as f:
        text = io.TextIOWrapper(f, encoding="latin-1", newline="")
        reader = csv.reader(text)
        header = next(reader)
        for vals in reader:
            yield header, vals


def _d(v: str) -> Optional[datetime]:
    try:
        return datetime.strptime((v or "").strip(), "%m/%d/%Y")
    except ValueError:
        return None


class DcadReader:
    adapter_version = "dcad_certified/2"

    def locate(self, year: Optional[int] = None) -> str:
        return DCAD_BASE + urllib.parse.quote(DCAD_PATH.format(year=year or datetime.utcnow().year))

    def discover(self, *, limit: int, candidate_factor: int = 3) -> Dict[str, Any]:
        url = self.locate()
        oz = B.open_zip("dcad", url)
        stats: Counter = Counter()
        try:
            cands: Dict[str, Dict[str, Any]] = {}
            want = max(limit * candidate_factor, limit)
            for header, vals in _csv_rows(oz.zip, "RES_DETAIL.CSV"):
                if "CDU_RATING_DESC" not in header:
                    raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "RES_DETAIL.CSV has no CDU_RATING_DESC")
                row = dict(zip(header, vals))
                stats["res_scanned"] += 1
                cdu = (row.get("CDU_RATING_DESC") or "").strip().upper()
                if cdu in DISTRESSED_CDU and row["ACCOUNT_NUM"] not in cands:
                    cands[row["ACCOUNT_NUM"]] = {"res": row}
                    if len(cands) >= want:
                        break
            stats["condition_candidates"] = len(cands)
            self._join(oz.zip, "ACCOUNT_APPRL_YEAR.CSV", cands, "val", stats)
            # single-family only, with a value
            for a in list(cands):
                v = cands[a].get("val")
                if not v or (v.get("SPTD_CODE") or "").strip() not in SFR_SPTD:
                    del cands[a]
            self._join(oz.zip, "ACCOUNT_INFO.CSV", cands, "info", stats)
            self._join(oz.zip, "APPLIED_STD_EXEMPT.CSV", cands, "exempt", stats)
        finally:
            oz.close()
        from app.services.evosense.ingest import owner_type_of
        recs = []
        for a, c in cands.items():
            info = c.get("info")
            if not info:
                continue
            if owner_type_of(info.get("OWNER_NAME1")) == "government":
                stats["government"] += 1
                continue
            recs.append(self.record(c, url))
            if len(recs) >= limit:
                break
        stats["records"] = len(recs)
        return {"records": recs, "stats": dict(stats), "source_url": url, "source_updated_at": None}

    def _join(self, zf, member, cands, slot, stats):
        need = {a for a, c in cands.items() if slot not in c}
        if not need:
            return
        for header, vals in _csv_rows(zf, member):
            a = vals[0] if vals else None
            if a in need:
                cands[a][slot] = dict(zip(header, vals))
                need.discard(a)
                if not need:
                    break
        stats["%s_missing" % slot] = len(need)

    def record_from_raw(self, raw: Dict[str, Any], *, homestead: Optional[bool], url: str = DCAD_PAGE) -> Dict[str, Any]:
        """Re-derive a record from the raw evidence an observation preserved.
        The raw keeps RES_DETAIL, ACCOUNT_INFO and the value row; the homestead
        answer (from APPLIED_STD_EXEMPT) was kept as evidence alongside it."""
        c = {"res": raw.get("RES_DETAIL") or {}, "info": raw.get("ACCOUNT_INFO") or {},
             "val": raw.get("ACCOUNT_APPRL_YEAR") or {}, "homestead": homestead}
        return self.record(c, url)

    def record(self, c, url) -> Dict[str, Any]:
        res, info, val, ex = c["res"], c["info"], c.get("val") or {}, c.get("exempt")
        acct = res["ACCOUNT_NUM"].strip()
        street = " ".join(x for x in (info.get("STREET_NUM", "").strip(), info.get("STREET_HALF_NUM", "").strip(),
                                      re.sub(r"\s+", " ", info.get("FULL_STREET_NAME", "")).strip()) if x)
        unit = (info.get("UNIT_ID") or "").strip() or None
        city = re.sub(r"\s*\(.*\)$", "", (info.get("PROPERTY_CITY") or "").strip()).title() or None
        zip5 = re.sub(r"\D", "", info.get("PROPERTY_ZIPCODE") or "")[:5] or None
        confidential = (info.get("EXCLUDE_OWNER") or "").strip().upper() == "Y"
        owner = None
        if not confidential and (info.get("OWNER_NAME1") or "").strip():
            lines = (info.get("OWNER_ADDRESS_LINE1"), info.get("OWNER_ADDRESS_LINE2"),
                     info.get("OWNER_ADDRESS_LINE3"), info.get("OWNER_ADDRESS_LINE4"))
            mail = B.mailing_line(*lines)
            more = B.name_continuation(*lines)
            st = (info.get("OWNER_STATE") or "").strip().upper()
            from app.services.evosense.sources.tarrant import state_code
            owner = {"name": " ".join(x.strip() for x in (info.get("OWNER_NAME1"), info.get("OWNER_NAME2"), more)
                                      if x and x.strip()),
                     "mailing_street": mail, "mailing_city": (info.get("OWNER_CITY") or "").strip().title() or None,
                     "mailing_state": state_code(st), "mailing_zip": re.sub(r"\D", "", info.get("OWNER_ZIPCODE") or "")[:5] or None}
        cdu = (res.get("CDU_RATING_DESC") or "").strip().upper()
        yr = res.get("APPRAISAL_YR") or val.get("APPRAISAL_YR")

        def i(v):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return None
        deed = _d(info.get("DEED_TXFR_DATE"))
        homestead = c.get("homestead") if "homestead" in c else bool(ex and (ex.get("HOMESTEAD_EFF_DT") or "").strip())
        rec = {
            "source_reference": "DCAD:%s:%s" % (acct, yr or "na"),
            "street_address": street or None, "unit": unit, "city": city, "state": "TX",
            "zip_code": zip5, "county": "Dallas", "parcel_apn": acct,
            "property_type": "single_family",
            "bedrooms": i(res.get("NUM_BEDROOMS")) or None,
            "bathrooms": i(res.get("NUM_FULL_BATHS")) or None,          # FULL baths
            "half_bathrooms": i(res.get("NUM_HALF_BATHS")) or None,
            "square_feet": i(res.get("TOT_LIVING_AREA_SF")) or None,
            "year_built": i(res.get("YR_BUILT")) if (i(res.get("YR_BUILT")) or 0) > 1800 else None,
            # a DEED TRANSFER date - the record states no price and no sale
            "deed_transfer_date": deed.strftime("%Y-%m-%d") if deed else None,
            "occupancy": "owner_occupied" if homestead else None,
            "owner": owner,
            "signals": ([{"type": CDU_SIGNAL[cdu], "confidence": DISTRESSED_CDU[cdu],
                          "value": "DCAD %s CDU rating: %s (condition, desirability & utility — appraiser's opinion)"
                                   % (yr or "", cdu),
                          "raw": "CDU_RATING_DESC=%s;DEPRECIATION_PCT=%s" % (cdu, res.get("DEPRECIATION_PCT")),
                          "effective_at": "%s-01-01" % yr if yr and str(yr).isdigit() else None,
                          "evidence_basis": "DCAD appraisal year %s rating" % (yr or "?"),
                          "observed_days_ago": 0}] if cdu in CDU_SIGNAL else []),
            "_raw": {"RES_DETAIL": res, "ACCOUNT_INFO": {k: v for k, v in info.items()
                                                         if not (confidential and k.startswith("OWNER"))},
                     "ACCOUNT_APPRL_YEAR": {k: val.get(k) for k in ("TOT_VAL", "IMPR_VAL", "LAND_VAL",
                                                                   "SPTD_CODE", "CITY_JURIS_DESC")}},
            "_source_url": DCAD_PAGE, "_source_updated_at": None,
            "_adapter_version": self.adapter_version,
            "_evidence": {"cdu": cdu, "homestead": homestead, "owner_confidential": confidential},
        }
        if i(val.get("TOT_VAL")):
            # The appraisal district's TAX value. Never a market value, never an ARV.
            rec["appraisal"] = {"value": i(val["TOT_VAL"]), "land": i(val.get("LAND_VAL")),
                                "improvements": i(val.get("IMPR_VAL")),
                                "year": int(yr) if yr and str(yr).isdigit() else None, "district": "DCAD",
                                "basis": "DCAD %s certified appraisal (property-tax value)" % (yr or "")}
        return rec


# ── City of Dallas 311 (Code Compliance service requests) ──────────────────

DALLAS_311 = "https://www.dallasopendata.com/resource/d7e7-envw.json"
DALLAS_311_PAGE = "https://www.dallasopendata.com/d/d7e7-envw"


class Dallas311Reader:
    adapter_version = "dallas_311/2"
    OPEN_STATUSES = ("new", "open", "in progress", "in-progress", "assigned", "pending")

    def lookup(self, street: str, *, days: int = 365) -> Dict[str, Any]:
        s = re.sub(r"\s+", " ", (street or "").upper()).strip()
        if not s:
            return {"cases": []}
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
        where = ("department = 'Code Compliance' AND starts_with(address, '%s,') AND created_date > '%s'"
                 % (s.replace("'", "''"), since))
        rows = B.get_json(DALLAS_311, {"$where": where, "$limit": 25, "$order": "created_date DESC"})
        if not isinstance(rows, list):
            raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "Dallas 311 did not return a list")
        return {"cases": rows}

    def to_record(self, target: Dict[str, Any], cases: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Each request is its own evidence. An OPEN request is a current code
        complaint; a CLOSED one is code HISTORY - shown, never scored as a
        current problem. A "Proactive" request was opened by the city itself."""
        if not cases:
            return None
        newest = cases[0]
        sigs = []
        for c in cases[:5]:
            created = (c.get("created_date") or "")[:10]
            try:
                age = max(0, (datetime.utcnow() - datetime.strptime(created, "%Y-%m-%d")).days)
            except ValueError:
                age = 0
            status = (c.get("status") or "").strip()
            is_open = status.lower() in self.OPEN_STATUSES
            method = (c.get("method_received_description") or "").strip()
            how = " · city-initiated" if method.lower() == "proactive" else (" · %s" % method.lower() if method else "")
            closed = (c.get("closed_date") or "")[:10]
            sigs.append({"type": "CODE_COMPLAINT" if is_open else "CODE_HISTORY",
                         "confidence": 55 if is_open else 50,
                         "value": "311 %s (opened %s%s) — %s%s" % (
                             c.get("service_request_type") or "request", created, how, status.lower() or "status unknown",
                             (", closed %s" % closed) if closed and not is_open else ""),
                         "raw": "SR=%s;STATUS=%s" % (c.get("service_request_number"), status),
                         "observed_days_ago": age, "effective_at": created or None,
                         "evidence_basis": "311 request created date",
                         "case_status": status.lower() or None,
                         "ref": "DAL311:%s" % c.get("service_request_number")})
        return {"source_reference": "DAL311:%s" % newest.get("service_request_number"),
                "street_address": target.get("street_address"), "city": target.get("city"),
                "state": "TX", "zip_code": target.get("zip_code"), "county": target.get("county"),
                "parcel_apn": target.get("parcel_apn"), "signals": sigs,
                "_raw": cases[:5], "_source_url": DALLAS_311_PAGE, "_source_updated_at": None,
                "_adapter_version": self.adapter_version,
                "_evidence": {"note": "311 requests are complaints or city inspections, not confirmed violations"}}
