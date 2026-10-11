"""PropStream CSV exports -> EvoSense's import columns.

PropStream is OPTIONAL. Nothing here calls PropStream or needs an account: it
only reads a CSV file a person exported themselves, if they ever have one.

Header names vary a little between PropStream export templates, so headers are
matched loosely (case, spaces and punctuation ignored) against the aliases
below, which can be extended without code elsewhere changing. A column that is
not recognised is reported, never guessed at.

Distress columns become SIGNALS only when the cell clearly says yes (Yes / Y /
True / 1 / X, or a non-empty status such as "Notice of Default"): a blank or
"No" never asserts anything. The signals are recorded as the list's claim
("as stated by a PropStream export"), not as verified fact.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List, Optional, Tuple


def norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


# canonical column -> PropStream header spellings (any template)
ALIASES: Dict[str, Tuple[str, ...]] = {
    "street_address": ("Address", "Property Address", "Site Address", "Street Address"),
    "unit": ("Unit", "Unit #", "Unit Number", "Property Unit"),
    "city": ("City", "Property City", "Site City"),
    "state": ("State", "Property State", "Site State"),
    "zip_code": ("Zip", "Zip Code", "Property Zip", "Property Zip Code", "Site Zip"),
    "county": ("County", "Property County"),
    "parcel_apn": ("APN", "Parcel Number", "APN (Parcel ID)", "Parcel ID"),
    "property_type": ("Property Type", "Property Use", "Land Use"),
    "owner_name": ("Owner Name", "Owner 1 Full Name", "Owner(s)", "Owner 1 Name", "Owner Full Name"),
    "owner_first": ("Owner 1 First Name", "Owner First Name"),
    "owner_last": ("Owner 1 Last Name", "Owner Last Name"),
    "mailing_street": ("Mailing Address", "Owner Mailing Address", "Mail Address"),
    "mailing_city": ("Mailing City", "Owner Mailing City", "Mail City"),
    "mailing_state": ("Mailing State", "Owner Mailing State", "Mail State"),
    "mailing_zip": ("Mailing Zip", "Mailing Zip Code", "Owner Mailing Zip", "Mail Zip"),
    "bedrooms": ("Bedrooms", "Beds"),
    "bathrooms": ("Total Bathrooms", "Bathrooms", "Baths"),
    "square_feet": ("Building Sqft", "Living Sqft", "Building Square Feet", "Sq Ft", "Square Footage",
                    "Living Square Feet"),
    "year_built": ("Year Built", "Effective Year Built"),
    "estimated_value": ("Est. Value", "Estimated Value", "Est Value", "AVM", "Estimated Market Value"),
    "mortgage_balance": ("Est. Remaining Balance of Open Loans", "Total Loan Balance", "Est. Loan Balance",
                         "Open Mortgage Balance", "Loan Balance", "Total Open Loans"),
    "estimated_equity": ("Est. Equity", "Estimated Equity", "Equity"),
    "last_sale_date": ("Last Sale Date", "Last Sale Recording Date", "Last Sold Date"),
    "record_id": ("PropStream ID", "Property ID", "Record ID"),
}

# PropStream yes/no or status columns -> EvoSense signal
FLAGS: Dict[str, Tuple[str, ...]] = {
    "VACANT": ("Vacant", "Vacant?", "Vacancy", "Is Vacant"),
    "PRE_FORECLOSURE": ("Pre-Foreclosure", "Preforeclosure", "Foreclosure", "Foreclosure Status",
                        "Notice of Default", "Lis Pendens", "Notice of Trustee Sale", "Auction"),
    "PROBATE": ("Probate", "Probate?"),
    "ESTATE": ("Inherited", "Estate", "Deceased Owner"),
    "TAX_DELINQUENT": ("Tax Delinquent", "Tax Default", "Tax Delinquency", "Tax Lien"),
    "LIEN": ("Lien", "Liens", "Involuntary Lien"),
    "FREE_AND_CLEAR": ("Free and Clear", "Free & Clear", "Free And Clear?"),
    "TIRED_LANDLORD": ("Tired Landlord",),
}

YES = {"yes", "y", "true", "t", "1", "x"}
NO = {"no", "n", "false", "f", "0", "", "none", "n/a", "na", "-"}

CANON_COLUMNS = ("street_address", "unit", "city", "state", "zip_code", "county", "parcel_apn",
                 "property_type", "owner_name", "mailing_street", "mailing_city", "mailing_state",
                 "mailing_zip", "estimated_value", "mortgage_balance", "last_sale_date", "signals",
                 "record_id", "bedrooms", "bathrooms", "square_feet", "year_built")

_LOOKUP = {norm(a): c for c, al in ALIASES.items() for a in al}
_FLAG_LOOKUP = {norm(a): s for s, al in FLAGS.items() for a in al}


def mapping(fieldnames: List[str]) -> Dict[str, Any]:
    cols, flags, unknown = {}, {}, []
    for h in fieldnames or []:
        n = norm(h)
        if n in _LOOKUP and _LOOKUP[n] not in cols.values():
            cols[h] = _LOOKUP[n]
        elif n in _FLAG_LOOKUP:
            flags[h] = _FLAG_LOOKUP[n]
        elif n:
            unknown.append(h)
    return {"columns": cols, "flags": flags, "unknown": unknown}


def is_propstream(fieldnames: List[str]) -> bool:
    """A file is treated as a PropStream export when it is NOT already in
    EvoSense's own column names and its headers map onto an address plus at
    least two more PropStream fields."""
    names = set(fieldnames or [])
    if "street_address" in names:
        return False
    m = mapping(fieldnames)
    have = set(m["columns"].values())
    return "street_address" in have and len(have | set(m["flags"].values())) >= 3


def _flag_on(value: Optional[str]) -> bool:
    v = (value or "").strip().lower()
    if v in NO:
        return False
    if v in YES:
        return True
    return not v.startswith(("no ", "none"))          # a status such as "Notice of Default"


def convert(content: str) -> Tuple[str, Dict[str, Any]]:
    """PropStream CSV text -> EvoSense CSV text (CANON_COLUMNS) + a report."""
    reader = csv.DictReader(io.StringIO(content))
    m = mapping(reader.fieldnames or [])
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=list(CANON_COLUMNS))
    w.writeheader()
    flags_seen: Dict[str, int] = {}
    rows = 0
    for row in reader:
        rows += 1
        rec = {c: "" for c in CANON_COLUMNS}
        for h, c in m["columns"].items():
            v = (row.get(h) or "").strip()
            if c in ("owner_first", "owner_last", "estimated_equity"):
                continue
            rec[c] = v
        if not rec["owner_name"]:
            first = next((row.get(h, "").strip() for h, c in m["columns"].items() if c == "owner_first"), "")
            last = next((row.get(h, "").strip() for h, c in m["columns"].items() if c == "owner_last"), "")
            rec["owner_name"] = (" ".join(x for x in (first, last) if x)).strip()
        for c in ("estimated_value", "mortgage_balance"):
            rec[c] = re.sub(r"[^0-9.]", "", rec[c])
        sigs = []
        for h, s in m["flags"].items():
            if _flag_on(row.get(h)) and s not in sigs:
                sigs.append(s)
                flags_seen[s] = flags_seen.get(s, 0) + 1
        rec["signals"] = ";".join(sigs)
        if rec["record_id"]:
            rec["record_id"] = "propstream:%s" % rec["record_id"]
        w.writerow(rec)
    report = {"format": "propstream", "rows": rows,
              "mapped": {h: c for h, c in m["columns"].items()},
              "signal_columns": m["flags"], "not_used": m["unknown"], "signals_found": flags_seen,
              "note": "As stated by a PropStream export - imported as the list's claim, not verified."}
    return out.getvalue(), report
