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
# Discovery reads RES_DETAIL (the residential-building table: houses, condos,
# townhomes, duplexes, manufactured homes). Until 2026-10-25 it then kept SPTD
# A11 only; it now keeps every account whose OWN SPTD code is a type the
# hunting strategy asks for, and counts each type it saw, kept or left out.
# Commercial, industrial and land accounts have no RES_DETAIL row (no CDU
# rating), so DCAD can't discover them by condition - they arrive through the
# tax roll / clerk notices and DCAD then supplies their owner, value and code.


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


def _legacy_type(code) -> Optional[str]:
    """DCAD's own SPTD code -> the older type word: A11 is a house; other A1x
    codes (condo, townhome, half-duplex) are residential but not called a
    house - the code itself is kept as the evidence."""
    from app.services.evosense import property_class as PCL
    c = (code or "").strip().upper()
    if c in SFR_SPTD:
        return "single_family"
    if c.startswith("A1"):
        return None
    return PCL.legacy_type_for(c)


class DcadReader:
    adapter_version = "dcad_certified/3"

    def locate(self, year: Optional[int] = None) -> str:
        return DCAD_BASE + urllib.parse.quote(DCAD_PATH.format(year=year or datetime.utcnow().year))

    def discover(self, *, limit: int, candidate_factor: int = 3, skip: int = 0,
                 categories=None) -> Dict[str, Any]:
        from app.services.evosense import property_class as PCL
        categories = tuple(categories or ("residential",))
        url = self.locate()
        oz = B.open_zip("dcad", url)
        stats: Counter = Counter()
        try:
            cands: Dict[str, Dict[str, Any]] = {}
            head: Dict[str, Dict[str, Any]] = {}
            want = max(limit * candidate_factor, limit)
            for header, vals in _csv_rows(oz.zip, "RES_DETAIL.CSV"):
                if "CDU_RATING_DESC" not in header:
                    raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "RES_DETAIL.CSV has no CDU_RATING_DESC")
                row = dict(zip(header, vals))
                stats["res_scanned"] += 1
                cdu = (row.get("CDU_RATING_DESC") or "").strip().upper()
                if cdu in DISTRESSED_CDU and row["ACCOUNT_NUM"] not in cands:
                    stats["condition_seen"] += 1
                    if stats["condition_seen"] <= skip:      # rotation: a different window each hunt
                        if len(head) < want:
                            head[row["ACCOUNT_NUM"]] = {"res": row}
                        continue
                    cands[row["ACCOUNT_NUM"]] = {"res": row}
                    if len(cands) >= want:
                        break
            for a, c0 in head.items():                         # wrap when the file ran out first
                if len(cands) >= want:
                    break
                cands.setdefault(a, c0)
            stats["condition_candidates"] = len(cands)
            self._join(oz.zip, "ACCOUNT_APPRL_YEAR.CSV", cands, "val", stats)
            # the account's own SPTD code decides its type; no code -> left out, never "a house"
            for a in list(cands):
                v = cands[a].get("val")
                if not v:
                    stats["no_value_row"] += 1
                    del cands[a]
                    continue
                cat, _sub, outcome = PCL.reader_category(v.get("SPTD_CODE"))
                PCL.count_category(stats, "found", cat, outcome)
                if outcome != "target" or cat not in categories:
                    stats["excluded_type"] += 1
                    del cands[a]
                    continue
                PCL.count_category(stats, "kept", cat, outcome)
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
            stats["imported_%s" % (PCL.reader_category((c.get("val") or {}).get("SPTD_CODE"))[0] or "unknown")] += 1
            if len(recs) >= limit:
                break
        stats["records"] = len(recs)
        return {"records": recs, "stats": dict(stats), "source_url": url, "source_updated_at": None}

    def lookup_many(self, accounts) -> Dict[str, Any]:
        """Lookup by DCAD account: owner of record, situs, value, building facts
        for properties another source found (e.g. a tax-foreclosure listing).
        One pass over each member of the cached certified export."""
        wanted = {(a or "").strip() for a in accounts if (a or "").strip()}
        stats: Counter = Counter()
        if not wanted:
            return {"records": {}, "stats": {}, "source_url": DCAD_PAGE}
        url = self.locate()
        oz = B.open_zip("dcad", url)
        cands: Dict[str, Dict[str, Any]] = {a: {} for a in wanted}
        try:
            self._join(oz.zip, "ACCOUNT_INFO.CSV", cands, "info", stats)
            for a in [a for a, c in cands.items() if "info" not in c]:
                del cands[a]                          # not a DCAD account: no record, not a guess
            self._join(oz.zip, "ACCOUNT_APPRL_YEAR.CSV", cands, "val", stats)
            self._join(oz.zip, "RES_DETAIL.CSV", cands, "res", stats)
            self._join(oz.zip, "APPLIED_STD_EXEMPT.CSV", cands, "exempt", stats)
        finally:
            oz.close()
        recs = {}
        for a, c in cands.items():
            c.setdefault("res", {"ACCOUNT_NUM": a})   # land / non-residential: no building row
            rec = self.record(c, url)
            if not c["res"].get("TOT_LIVING_AREA_SF") and rec.get("property_type") == "single_family":
                rec["property_type"] = None           # only RES_DETAIL says single-family
            recs[a] = rec
        stats["matched"] = len(recs)
        return {"records": recs, "stats": dict(stats), "source_url": DCAD_PAGE}

    def accounts_for_addresses(self, targets: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """{target_id: {street, unit, zip_code, city}} -> {target_id: DCAD account}.

        One pass over ACCOUNT_INFO.CSV. An account counts only when it is the ONE
        DCAD account whose situs is the same address (same house number and
        street, suffix spelled either way, ZIP not contradicting, same unit).
        Two or more candidates is AMBIGUOUS and gets no account - never a guess."""
        from app.services.evosense.identity import normalize_street, notice_spelling, _split_suffix, same_address
        stats: Counter = Counter()
        cores: Dict[str, List[str]] = {}
        for tid, t in targets.items():
            norm, unit = normalize_street(t.get("street_address"))
            if not norm or not re.match(r"^\d", norm):
                stats["no_house_number"] += 1
                continue
            t["_unit"] = (t.get("unit") or unit or "").strip().upper().lstrip("#") or None
            t["_streets"] = [t.get("street_address")]
            cores.setdefault(_split_suffix(norm)[0], []).append(tid)
            alt = notice_spelling(t.get("street_address"))      # "SOUTH SECOND AVE" -> "S 2ND AVE"
            if alt:
                t["_streets"].append(alt)
                alt_core = _split_suffix(normalize_street(alt)[0])[0]
                if tid not in cores.setdefault(alt_core, []):
                    cores[alt_core].append(tid)
                stats["notice_spelling_tried"] += 1
        hits: Dict[str, List[Dict[str, str]]] = {tid: [] for ids in cores.values() for tid in ids}
        if not cores:
            return {"accounts": {}, "stats": dict(stats)}
        url = self.locate()
        oz = B.open_zip("dcad", url)
        try:
            for header, vals in _csv_rows(oz.zip, "ACCOUNT_INFO.CSV"):
                row = dict(zip(header, vals))
                street = " ".join(x for x in ((row.get("STREET_NUM") or "").strip(),
                                              re.sub(r"\s+", " ", row.get("FULL_STREET_NAME") or "").strip()) if x)
                if not street:
                    continue
                core = _split_suffix(normalize_street(street)[0])[0]
                for tid in cores.get(core, ()):
                    t = targets[tid]
                    if not any(same_address(x, street, t.get("zip_code"), row.get("PROPERTY_ZIPCODE")) is True
                               for x in t["_streets"]):
                        continue
                    unit = (row.get("UNIT_ID") or "").strip().upper() or None
                    if (t["_unit"] or None) != unit:
                        continue
                    hits[tid].append(row)
        finally:
            oz.close()
        accounts = {}
        for tid, rows in hits.items():
            # one row can be indexed under both spellings: count the account once
            accts = {(r.get("ACCOUNT_NUM") or "").strip() for r in rows}
            if len(accts) == 1:
                accounts[tid] = accts.pop()
            else:
                stats["ambiguous" if accts else "no_match"] += 1
        stats["matched"] = len(accounts)
        return {"accounts": accounts, "stats": dict(stats)}

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
            "property_type": _legacy_type(val.get("SPTD_CODE")),
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
