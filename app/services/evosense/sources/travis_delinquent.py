"""Travis County, Texas: the Tax Office's published delinquent-tax file.

    https://tax-office.traviscountytx.gov/about-us/reports-data/property-taxes
    https://tax-office.traviscountytx.gov/voterdata/TaxDelqOpenData.csv

Verified against the live file on 2026-10-22 (Last-Modified 2026-10-20,
1.74 MB, 7,533 accounts, one row per account). Real header:

    Account #, Last Tax Roll Year, Property Type Code, Land Value,
    Improvement Value, Ag Market, Appraisal Value, Ag Taxable Value,
    Assessed Value, Exemptions, Delinquent Total, Current Year Total,
    Total Due, Sequence #, 1st Year Delinquent, Cause #, Judgement Date,
    Bankruptcy Number, Owner ID, Owner Name, Address 1, Address 2, Address 3,
    City, State, Zip Code, Country, Street Number, Street Name, Property Zip,
    Legal Description

City/State/Zip belong to the OWNER'S mailing address; the property itself
has only Street Number, Street Name and Property Zip.

KEPT (all must hold; every rejection is counted):
  - a Property Type Code (the file's own state class) that is a type the
    hunting strategy asks for: residential (A, B2, M, O), multifamily (B1),
    land (C, E), agricultural (D), commercial (F1), industrial (F2). Until
    2026-10-25 only A1 houses were kept. Personal property, minerals and
    utilities, and rows with no readable code, are counted and left out -
    a missing code is never taken for a house;
  - Delinquent Total (past-due balance, NOT the current-year bill) at least
    `min_due`;
  - an account whose last tax roll year is current (stale accounts are left);
  - not in bankruptcy;
  - a situs house number and street, and a named owner.
A Cause # means a delinquent-tax lawsuit (TAX_SUIT); a Judgement Date is kept
as evidence. Nothing here is a foreclosure or a tax sale.
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from app.services.evosense.sources import base as B

PAGE = "https://tax-office.traviscountytx.gov/about-us/reports-data/property-taxes"
FILE = "https://tax-office.traviscountytx.gov/voterdata/TaxDelqOpenData.csv"
HOUSE_CLASSES = ("A1",)
REQUIRED = ("Account #", "Property Type Code", "Delinquent Total", "Owner Name", "Street Number",
            "Street Name", "Property Zip", "Last Tax Roll Year")


def money(v) -> float:
    try:
        return float(re.sub(r"[^\d.\-]", "", str(v or "")) or 0)
    except ValueError:
        return 0.0


def _year(v) -> Optional[int]:
    m = re.match(r"^\s*((?:19|20)\d{2})\s*$", str(v or ""))
    return int(m.group(1)) if m else None


def latest_roll_year(today: datetime) -> int:
    """Year N bills go out in October N and fall delinquent February 1, N+1;
    in October 2026 the newest roll in a delinquent file is 2025."""
    return today.year - 1 if today.month >= 2 else today.year - 2


def rows_from_text(text: str) -> List[Dict[str, str]]:
    # The live file starts with a UTF-8 byte-order mark ("\ufeffAccount #").
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    header = [h.strip().lstrip("\ufeff") for h in next(reader, [])]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "Travis delinquent file lacks %s; header is %s"
                            % (", ".join(missing), ", ".join(header[:10])))
    out = []
    for cells in reader:
        if not cells or not any(c.strip() for c in cells):
            continue
        cells += [""] * (len(header) - len(cells))
        out.append({h: (cells[i] or "").strip() for i, h in enumerate(header)})
    return out


def situs(r: Dict[str, str]) -> Optional[str]:
    """'1012' + 'MILTON ST W' -> '1012 W MILTON ST' (the file puts the
    direction last; the rest of EvoSense writes it first)."""
    num = (r.get("Street Number") or "").strip().lstrip("0")
    name = re.sub(r"\s+", " ", (r.get("Street Name") or "").strip().upper())
    if not num or not name:
        return None
    m = re.match(r"^(.*\S)\s+(N|S|E|W|NE|NW|SE|SW)$", name)
    if m:
        name = "%s %s" % (m.group(2), m.group(1))
    return "%s %s" % (num, name)


def _legacy(code) -> Optional[str]:
    from app.services.evosense import property_class as PCL
    c = (code or "").strip().upper()
    return None if c.startswith("A1") else PCL.legacy_type_for(c)


class TravisDelinquentReader:
    adapter_version = "travis_delinquent/3"

    def fetch(self) -> str:
        return B.get_text(FILE, max_bytes=20 * 1024 * 1024)

    def discover(self, *, limit: int, min_due: float = 250.0, skip: int = 0, zips=None,
                 today: Optional[datetime] = None, text: Optional[str] = None,
                 categories=None) -> Dict[str, Any]:
        from app.services.evosense import property_class as PCL
        categories = tuple(categories or ("residential",))
        today = today or datetime.utcnow()
        rows = rows_from_text(text if text is not None else self.fetch())
        newest = latest_roll_year(today)
        zips = {z[:5] for z in zips or [] if z}
        stats: Counter = Counter()
        pool: List[Dict[str, str]] = []
        for r in rows:
            stats["rows"] += 1
            cls = (r.get("Property Type Code") or "").upper()
            cat, _sub, outcome = PCL.reader_category(cls)
            PCL.count_category(stats, "found", cat, outcome)
            if outcome != "target" or cat not in categories:
                stats["excluded_type"] += 1
                continue
            if cls in HOUSE_CLASSES:
                stats["houses"] += 1
            yr = _year(r.get("Last Tax Roll Year"))
            if yr is None or yr < newest - 1:
                stats["stale_account"] += 1
                continue
            if money(r.get("Delinquent Total")) < min_due:
                stats["below_min_due"] += 1
                continue
            if (r.get("Bankruptcy Number") or "").strip():
                stats["bankruptcy_skipped"] += 1
                continue
            owner = (r.get("Owner Name") or "").strip()
            if not owner or owner.upper() in ("CURRENT OWNER", "UNKNOWN OWNER", "UNKNOWN"):
                stats["no_owner"] += 1
                continue
            if not situs(r):
                stats["no_situs"] += 1
                continue
            if zips and (r.get("Property Zip") or "")[:5] not in zips:
                continue
            pool.append(r)
            stats["kept_%s" % cat] += 1
        pool.sort(key=lambda r: r["Account #"])
        stats["delinquent_kept"] = len(pool)
        stats["with_lawsuit"] = sum(1 for r in pool if (r.get("Cause #") or "").strip())
        records = []
        if pool:
            start = skip % len(pool)
            take = min(limit, len(pool))
            if start + take > len(pool):
                stats["wrapped"] = start + take - len(pool)
            records = [self.record(pool[(start + i) % len(pool)], today) for i in range(take)]
            for i in range(take):
                c = PCL.reader_category(pool[(start + i) % len(pool)].get("Property Type Code"))[0]
                stats["imported_%s" % (c or "unknown")] += 1
        stats["records"] = len(records)
        return {"records": records, "stats": dict(stats), "source_url": FILE}

    def record(self, r: Dict[str, str], today: datetime) -> Dict[str, Any]:
        from app.services.evosense.sources.tarrant import state_code
        acct = r["Account #"].strip()
        due = money(r.get("Delinquent Total"))
        first = _year(r.get("1st Year Delinquent"))
        roll = _year(r.get("Last Tax Roll Year"))
        since = first or roll
        owner = {"name": re.sub(r"\s*&\s*$", "", r["Owner Name"].strip()),
                 "mailing_street": B.mailing_line(r.get("Address 1"), r.get("Address 2"), r.get("Address 3")),
                 "mailing_city": (r.get("City") or "").title() or None,
                 "mailing_state": state_code((r.get("State") or "").upper()) if r.get("State") else None,
                 "mailing_zip": re.sub(r"\D", "", r.get("Zip Code") or "")[:5] or None}
        what = ("Travis County Tax Office: $%s delinquent (past due, not this year's bill)%s"
                % (format(due, ",.2f"), ", unpaid since tax year %s" % since if since else ""))
        sigs = [{"type": "TAX_DELINQUENT", "confidence": 90, "value": what,
                 "raw": "ACCOUNT=%s;DELINQUENT_TOTAL=%s;FIRST_YEAR=%s;ROLL_YEAR=%s"
                        % (acct, r.get("Delinquent Total"), r.get("1st Year Delinquent"), r.get("Last Tax Roll Year")),
                 "effective_at": "%s-02-01" % (since + 1) if since else None,
                 "evidence_basis": "Travis County Tax Office delinquent-tax file (balance still owed as of the file)",
                 "observed_days_ago": 0}]
        cause = (r.get("Cause #") or "").strip()
        if cause:
            sigs.append({"type": "TAX_SUIT", "confidence": 90,
                         "value": "delinquent-tax lawsuit, cause %s%s"
                                  % (cause, "; judgment %s" % r["Judgement Date"] if r.get("Judgement Date") else ""),
                         "raw": "CAUSE=%s;JUDGMENT=%s" % (cause, r.get("Judgement Date") or ""),
                         "evidence_basis": "Travis County Tax Office delinquent-tax file (Cause #)",
                         "observed_days_ago": 0})
        appraisal = None
        val = money(r.get("Appraisal Value"))
        if val > 0:
            appraisal = {"value": int(val), "land": int(money(r.get("Land Value"))) or None,
                         "improvements": int(money(r.get("Improvement Value"))) or None, "year": roll,
                         "district": "Travis Central Appraisal District",
                         "basis": "Travis County Tax Office delinquent file (appraised value on the roll)"}
        rec = {
            "source_reference": "TRAVISDELQ:%s" % acct,
            "street_address": situs(r), "unit": None, "city": None, "state": "TX",
            "zip_code": re.sub(r"\D", "", r.get("Property Zip") or "")[:5] or None,
            "county": "Travis", "parcel_apn": acct,
            "property_type": "single_family" if (r.get("Property Type Code") or "").upper() in HOUSE_CLASSES
            else _legacy(r.get("Property Type Code")),
            "owner": owner, "signals": sigs,
            "occupancy": "owner_occupied" if re.search(r"\bHS?\b", (r.get("Exemptions") or "").upper()) else None,
            "_raw": dict(r), "_source_url": FILE, "_source_updated_at": None,
            "_adapter_version": self.adapter_version,
            "_evidence": {"account": acct, "delinquent_total": due, "current_year_total": money(r.get("Current Year Total")),
                          "first_year_delinquent": first, "last_roll_year": roll, "cause": cause or None,
                          "judgment_date": r.get("Judgement Date") or None, "legal": r.get("Legal Description") or None,
                          "property_class": r.get("Property Type Code")},
        }
        if appraisal:
            rec["appraisal"] = appraisal
        return rec
