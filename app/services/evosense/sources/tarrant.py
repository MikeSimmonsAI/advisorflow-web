"""Tarrant County, Texas: the county tax roll (delinquency) and the Tarrant
Appraisal District (TAD) export (parcel facts, owner of record, value).

Both files are published free for public download with no account:
    tax roll   https://www.tarrantcountytx.gov/en/tax/property-tax/tarrant-county-tax-roll.html
    TAD        https://www.tad.org/resources/data-downloads
Both servers answer byte-range requests, so a pilot reads a few megabytes of
each file instead of the ~420 MB / ~50 MB archives.

IDENTITY. The tax-roll account number IS the TAD account number; EvoSense
keeps it (leading zeros removed) as the parcel APN for Tarrant County, so the
two sources join on APN, never on a fuzzy address.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from app.services.evosense.sources import base as B

TAX_ROLL_PAGE = "https://www.tarrantcountytx.gov/en/tax/property-tax/tarrant-county-tax-roll.html"
TAX_ROLL_BASE = "https://www.tarrantcountytx.gov"
TAD_URL = "https://www.tad.org/content/data-download/PropertyData(Delimited)_R.ZIP"
TAD_PAGE = "https://www.tad.org/resources/data-downloads"

# Fixed-width layout of Master.dat (TarrantTaxRollRecordDefinitionLayout, 1-based).
MASTER = [("account", 1, 11), ("sptb", 31, 3), ("roll", 34, 2), ("legal", 36, 120),
          ("street_name", 199, 25), ("street_no", 224, 7), ("year_built", 243, 4),
          ("owner1", 327, 30), ("owner2", 357, 30), ("addr1", 387, 30), ("addr2", 417, 30),
          ("ocity", 447, 30), ("ostate", 477, 20), ("ozip", 497, 18), ("def_start", 515, 10),
          ("def_end", 525, 10), ("deed_date", 547, 10), ("exempt", 557, 15),
          ("delq_date", 572, 10), ("land", 627, 11), ("impr", 638, 11), ("levy", 649, 12),
          ("cur_due", 661, 12), ("prior_due", 673, 12), ("status", 685, 15), ("litig", 740, 2)]
MIN_LINE = 690

# State property-tax-board codes as published by the Tarrant tax office
# (Definition_Layout_SPTB_Codes): 1 = A1 single-family, 39 = A residential.
RESIDENTIAL_SPTB = {"001": "single_family", "039": None}

# The tax office's own note: H (half-pay), Q (quarter-pay) and D (deferral)
# accounts "are not delinquent". They are excluded, not guessed about.
NOT_DELINQUENT_STATUS = ("H", "Q", "D")
NO_DATE = ("", "01/01/9999", "12/31/1900", "00/00/0000")

STATE_NAMES = {"TEXAS": "TX", "CALIFORNIA": "CA", "FLORIDA": "FL", "OKLAHOMA": "OK",
               "NEW YORK": "NY", "ARIZONA": "AZ", "COLORADO": "CO", "LOUISIANA": "LA",
               "GEORGIA": "GA", "ILLINOIS": "IL", "NEVADA": "NV", "WASHINGTON": "WA"}


def _mdY(v: str) -> Optional[datetime]:
    v = (v or "").strip()
    if v in NO_DATE:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def _num(v: str) -> float:
    try:
        return float((v or "0").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def parse_master(line: str) -> Dict[str, str]:
    if len(line) < MIN_LINE:
        raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "tax-roll line is %s characters; layout expects %s+"
                            % (len(line), MIN_LINE))
    return {k: line[a - 1:a - 1 + n].strip() for k, a, n in MASTER}


def apn(account: str) -> str:
    a = (account or "").strip().lstrip("0")
    return a or "0"


def state_code(v: str) -> Optional[str]:
    v = (v or "").strip().upper()
    if len(v) == 2:
        return v
    return STATE_NAMES.get(v)


def situs(r: Dict[str, str]) -> Optional[str]:
    no = (r.get("street_no") or "").lstrip("0")
    name = re.sub(r"\s+", " ", r.get("street_name") or "").strip()
    if not no or not name:
        return None
    return "%s %s" % (no, name)


def classify(r: Dict[str, str], today: datetime) -> Dict[str, Any]:
    """Deterministic delinquency reading of one tax-roll row. Returns
    {delinquent: bool, reason, prior_due, current_due, excluded: reason|None}."""
    from app.services.evosense.ingest import owner_type_of
    prior = _num(r["prior_due"])
    cur = _num(r["cur_due"])
    delq = _mdY(r["delq_date"])
    out = {"prior_due": prior, "current_due": cur, "delinquent_since": delq, "excluded": None,
           "delinquent": False}
    status = (r.get("status") or "").upper()
    if any(c in status for c in NOT_DELINQUENT_STATUS):
        out["excluded"] = "payment option or deferral on file (status %s)" % status
        return out
    if _mdY(r.get("def_start")):
        out["excluded"] = "tax deferral on file since %s" % r["def_start"]
        return out
    otype = owner_type_of(" ".join(x for x in (r.get("owner1"), r.get("owner2")) if x))
    if otype == "government":
        out["excluded"] = "government-owned"
        return out
    past_current = bool(cur > 0 and delq is not None and today >= delq)
    out["delinquent"] = bool(prior > 0 or past_current)
    return out


class TarrantTaxRollReader:
    """Discovery: residential accounts with delinquent taxes on the current roll."""

    adapter_version = "tarrant_tax_roll/1"

    def locate(self) -> str:
        override = B.local_override("tarrant_tax_roll")
        if override:
            return "file://" + override
        html = B.get_text(TAX_ROLL_PAGE)
        found = sorted(set(re.findall(r"/content/dam/main/tax/tax-rolls/\d{4}/TaxRoll\d{8}\.zip", html)))
        if not found:
            raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "no TaxRollYYYYMMDD.zip link on the tax-roll page")
        return TAX_ROLL_BASE + found[-1]

    def discover(self, *, limit: int, scan_limit: int = 600_000, min_due: float = 250.0,
                 today: Optional[datetime] = None) -> Dict[str, Any]:
        today = today or datetime.utcnow()
        url = self.locate()
        oz = B.open_zip("tarrant_tax_roll", url)
        records: List[Dict[str, Any]] = []
        stats: Counter = Counter()
        as_of = B.parse_http_date(oz.meta.get("last_modified"))
        m = re.search(r"TaxRoll(\d{8})", url)
        if m:
            as_of = datetime.strptime(m.group(1), "%Y%m%d")
        try:
            for line in B.text_lines(oz.zip, "Master.dat"):
                stats["scanned"] += 1
                if stats["scanned"] > scan_limit or len(records) >= limit:
                    break
                if len(line) < MIN_LINE:
                    stats["short_line"] += 1
                    if stats["short_line"] > 50:
                        raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "many tax-roll lines shorter than the layout")
                    continue
                r = parse_master(line)
                if r["sptb"] not in RESIDENTIAL_SPTB:
                    continue
                stats["residential"] += 1
                if _num(r["impr"]) <= 0:
                    stats["no_improvement"] += 1
                    continue
                c = classify(r, today)
                if c["excluded"]:
                    stats["excluded"] += 1
                    continue
                if not c["delinquent"]:
                    continue
                if c["prior_due"] + c["current_due"] < min_due:
                    stats["below_min_due"] += 1
                    continue
                street = situs(r)
                if not street:
                    stats["no_situs"] += 1
                    continue
                stats["delinquent"] += 1
                records.append(self.record(r, c, line, url, as_of))
        finally:
            fetched = oz.bytes_fetched()
            oz.close()
        stats["bytes_fetched"] = fetched
        return {"records": records, "stats": dict(stats), "source_url": url,
                "source_updated_at": as_of}

    def record(self, r, c, line, url, as_of) -> Dict[str, Any]:
        owner = " ".join(x for x in (r["owner1"], r["owner2"]) if x).strip()
        mail = B.mailing_line(r["addr1"], r["addr2"])
        # The roll's "prior years due" and "current due" are what the tax office
        # itself reports; nothing is estimated.
        bits = []
        if c["prior_due"] > 0:
            bits.append("prior-year taxes due $%s" % format(c["prior_due"], ",.2f"))
        if c["current_due"] > 0:
            bits.append("%s taxes unpaid after %s ($%s)" % (
                (c["delinquent_since"].year - 1) if c["delinquent_since"] else "current",
                c["delinquent_since"].strftime("%m/%d/%Y") if c["delinquent_since"] else "the due date",
                format(c["current_due"], ",.2f")))
        observed_days = max(0, (datetime.utcnow() - as_of).days) if as_of else 0
        signals = [{"type": "TAX_DELINQUENT", "confidence": 90, "value": "; ".join(bits),
                    "raw": "PRIOR_DUE=%s;CUR_DUE=%s;DELQ_DATE=%s;STATUS=%s" % (
                        r["prior_due"], r["cur_due"], r["delq_date"], r["status"] or "-"),
                    "effective_at": c["delinquent_since"].strftime("%Y-%m-%d")
                    if c["delinquent_since"] and not c["prior_due"] else None,
                    "observed_days_ago": observed_days}]
        lit = (r.get("litig") or "").strip().upper()
        if lit and lit not in ("N", "0", "00"):
            signals.append({"type": "TAX_SUIT", "confidence": 70,
                            "value": "tax-roll litigation indicator set (%s)" % lit,
                            "raw": "LITIG=%s" % lit, "observed_days_ago": observed_days})
        deed = _mdY(r["deed_date"])
        # The roll has no situs city / ZIP. When the owner's mailing street IS
        # the situs street, the mailing city and ZIP are the property's own -
        # recorded with that basis. Otherwise they stay UNKNOWN here.
        from app.services.evosense.identity import normalize_street
        situs_city = situs_zip = city_basis = None
        if mail and normalize_street(mail)[0] and normalize_street(mail)[0] == normalize_street(situs(r))[0]:
            situs_city = (r["ocity"] or "").title() or None
            situs_zip = re.sub(r"\D", "", r["ozip"])[:5] or None
            city_basis = "owner mailing address is the property"
        rec = {
            "source_reference": "TCTAX:%s:%s" % (apn(r["account"]), as_of.strftime("%Y%m%d") if as_of else "na"),
            "street_address": situs(r), "city": situs_city, "zip_code": situs_zip,
            "state": "TX", "county": "Tarrant",
            "parcel_apn": apn(r["account"]),
            "property_type": RESIDENTIAL_SPTB.get(r["sptb"]),
            "year_built": int(r["year_built"]) if r["year_built"].isdigit() and int(r["year_built"]) > 1800 else None,
            "last_sale_date": deed.strftime("%Y-%m-%d") if deed else None,
            "owner": {"name": owner or None, "mailing_street": mail,
                      "mailing_city": r["ocity"] or None, "mailing_state": state_code(r["ostate"]),
                      "mailing_zip": re.sub(r"\D", "", r["ozip"])[:5] or None},
            "signals": signals,
            "_raw": line, "_source_url": url, "_source_updated_at": as_of,
            "_adapter_version": self.adapter_version,
            "_evidence": {"account": r["account"], "sptb": r["sptb"], "legal": r["legal"],
                          "prior_due": c["prior_due"], "current_due": c["current_due"],
                          "delinquency_date": r["delq_date"], "status": r["status"] or None,
                          "city_basis": city_basis},
        }
        return rec


# ── TAD (Tarrant Appraisal District) ───────────────────────────────────────

TAD_REQUIRED = ("Account_Num", "Owner_Name", "Owner_Address", "Owner_CityState", "Owner_Zip",
                "Situs_Address", "City", "Total_Value", "Year_Built", "Living_Area", "Deed_Date")


def _split_citystate(v: str):
    parts = [p.strip() for p in (v or "").split(",")]
    city = parts[0] if parts and parts[0] else None
    st = state_code(parts[1]) if len(parts) > 1 else None
    return city, st


def _norm_addr(v: str) -> str:
    return re.sub(r"\s+", " ", (v or "").upper()).strip()


class TadReader:
    """Lookup by account (APN): owner of record, situs city, value, facts."""

    adapter_version = "tad_property_data/1"

    def lookup_many(self, accounts: Iterable[str], *, scan_limit: int = 1_500_000) -> Dict[str, Any]:
        wanted = {apn(a) for a in accounts if a}
        found: Dict[str, Dict[str, Any]] = {}
        votes: Dict[str, Counter] = defaultdict(Counter)
        stats: Counter = Counter()
        if not wanted:
            return {"records": {}, "stats": {}, "source_url": TAD_URL}
        oz = B.open_zip("tad", TAD_URL)
        as_of = B.parse_http_date(oz.meta.get("last_modified"))
        header: Optional[List[str]] = None
        last_acct = -1
        ascending = True
        max_wanted = max(int(a) for a in wanted if a.isdigit()) if any(a.isdigit() for a in wanted) else None
        member = next((n for n in oz.zip.namelist() if n.lower().endswith(".txt")), None)
        if member is None:
            oz.close()
            raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "no .txt data file inside the TAD archive")
        try:
            for line in B.text_lines(oz.zip, member):
                if header is None:
                    header = line.split("|")
                    missing = [c for c in TAD_REQUIRED if c not in header]
                    if missing:
                        raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "TAD file lacks columns: %s" % ", ".join(missing))
                    continue
                stats["scanned"] += 1
                if stats["scanned"] > scan_limit:
                    break
                vals = line.split("|")
                if len(vals) < len(header):
                    continue
                row = dict(zip(header, vals))
                acct = row["Account_Num"].strip()
                if acct.isdigit():
                    n = int(acct)
                    if n < last_acct:
                        ascending = False
                    last_acct = n
                # situs-city votes: owner-occupied rows name their own city
                if _norm_addr(row["Owner_Address"]) == _norm_addr(row["Situs_Address"]):
                    city, _st = _split_citystate(row["Owner_CityState"])
                    if city:
                        votes[row["City"].strip()][city.upper()] += 1
                if acct in wanted and acct not in found:
                    found[acct] = {"row": row, "line": line}
                    if len(found) == len(wanted):
                        break
                if ascending and max_wanted is not None and acct.isdigit() and int(acct) > max_wanted \
                        and stats["scanned"] > 1000:
                    break
        finally:
            fetched = oz.bytes_fetched()
            oz.close()
        stats["bytes_fetched"] = fetched
        # A city code maps to a city only on clear evidence: at least 5
        # owner-occupied rows and an 80% majority. Otherwise the city stays
        # UNKNOWN rather than guessed.
        city_map = {}
        for code, c in votes.items():
            if not c:
                continue
            name, n = c.most_common(1)[0]
            if n >= 5 and n >= 0.8 * sum(c.values()):
                city_map[code] = name
        recs = {a: self.record(v["row"], v["line"], city_map, as_of) for a, v in found.items()}
        stats["matched"] = len(recs)
        return {"records": recs, "stats": dict(stats), "source_url": TAD_URL,
                "source_updated_at": as_of, "city_map_size": len(city_map)}

    def record(self, row, line, city_map, as_of) -> Dict[str, Any]:
        acct = row["Account_Num"].strip()
        ocity, ost = _split_citystate(row["Owner_CityState"])
        situs_addr = _norm_addr(row["Situs_Address"]) or None
        owner_occupied_addr = situs_addr and _norm_addr(row["Owner_Address"]) == situs_addr
        city = ocity.title() if owner_occupied_addr and ocity else \
            (city_map.get(row["City"].strip()) or "").title() or None
        city_basis = ("owner mailing address is the property" if owner_occupied_addr and ocity
                      else "TAD city code %s mapped from owner-occupied rows" % row["City"].strip()
                      if city else None)
        zip5 = re.sub(r"\D", "", row["Owner_Zip"])[:5] if owner_occupied_addr else None

        def i(v):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return None
        total = i(row.get("Total_Value"))
        deed = _mdY(row.get("Deed_Date") or "")
        rec = {
            "source_reference": "TAD:%s:%s" % (acct, row.get("Appraisal_Year") or "na"),
            "street_address": situs_addr, "city": city, "state": "TX", "zip_code": zip5 or None,
            "county": "Tarrant", "parcel_apn": acct,
            "bedrooms": i(row.get("Num_Bedrooms")) or None,
            "bathrooms": (lambda b: b if b else None)(i(row.get("Num_Bathrooms"))),
            "square_feet": i(row.get("Living_Area")) or None,
            "year_built": i(row.get("Year_Built")) if (i(row.get("Year_Built")) or 0) > 1800 else None,
            "last_sale_date": deed.strftime("%Y-%m-%d") if deed else None,
            "owner": {"name": row["Owner_Name"].strip() or None,
                      "mailing_street": row["Owner_Address"].strip() or None,
                      "mailing_city": ocity, "mailing_state": ost,
                      "mailing_zip": re.sub(r"\D", "", row["Owner_Zip"])[:5] or None},
            "signals": [],
            "_raw": line, "_source_url": TAD_PAGE, "_source_updated_at": as_of,
            "_adapter_version": self.adapter_version,
            "_evidence": {"city_basis": city_basis, "property_class": row.get("Property_Class"),
                          "state_use_code": row.get("State_Use_Code"),
                          "appraisal_year": row.get("Appraisal_Year")},
        }
        if total:
            rec["valuation"] = {"value": total, "mortgage": None,
                                "basis": "TAD %s appraised value (appraisal district, not a market estimate)"
                                % (row.get("Appraisal_Year") or "")}
        return rec
