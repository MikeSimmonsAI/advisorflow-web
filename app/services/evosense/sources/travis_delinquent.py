"""Travis County, Texas: the Tax Office's published delinquent-tax roll (capability: "tax delinquency").

    https://tax-office.traviscountytx.gov/about-us/reports-data/property-taxes

NOT LIVE VERIFIED. When this adapter was written the build sandbox could not reach
the county site, so the column names below are an alias list of the headers a
county tax export normally carries, not a copy of a fetched file. The parser is
header-driven and strict: if the required columns cannot be found it raises
SOURCE_FORMAT_CHANGED instead of guessing. Replace/extend COLUMN_ALIASES with the
first real header row.

WHAT QUALIFIES (all must hold, else the row is counted and dropped):
  - a county account id AND an owner name from the roll itself (no placeholders)
  - a past-due balance > 0 for a tax year strictly before the current tax year
  - a situs street address
  - an appraisal lookup (`residential_lookup`, e.g. the CAD roll) says the same
    account is residential improved property. No lookup, no lead: a tax roll alone
    cannot tell a house from a vacant lot.
The whole file is never saved: only qualifying rows, capped by `limit`, leave the adapter.
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional

from app.services.evosense.sources import base as B

PAGE = "https://tax-office.traviscountytx.gov/about-us/reports-data/property-taxes"
CAPABILITY = "tax delinquency"          # not "tax foreclosure": no suit is implied
RESIDENTIAL_TYPES = ("single_family", "townhouse", "condo", "duplex")

COLUMN_ALIASES = {
    "account": ("account", "account number", "account no", "acct", "acct no", "prop id", "propid", "property id"),
    "owner": ("owner", "owner name", "name", "taxpayer"),
    "year": ("tax year", "year", "levy year"),
    "due": ("total due", "amount due", "balance due", "delinquent amount", "balance", "total"),
    "situs": ("situs", "situs address", "property address", "site address", "address"),
    "mail1": ("mailing address", "mail address", "owner address", "address 1", "mail address 1"),
    "city": ("mailing city", "mail city", "city"),
    "state": ("mailing state", "mail state", "state"),
    "zip": ("mailing zip", "mail zip", "zip", "zip code"),
}
REQUIRED = ("account", "owner", "year", "due", "situs")


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-]", " ", (h or "").strip().lower()))


def map_columns(header: Iterable[str]) -> Dict[str, int]:
    idx = {_norm(h): i for i, h in enumerate(header)}
    out: Dict[str, int] = {}
    for field, names in COLUMN_ALIASES.items():
        for n in names:
            if n in idx:
                out[field] = idx[n]
                break
    missing = [f for f in REQUIRED if f not in out]
    if missing:
        raise B.SourceError(B.SOURCE_FORMAT_CHANGED,
                            "Travis delinquent file lacks column(s) %s; saw %s"
                            % (", ".join(missing), ", ".join(sorted(idx)[:12])))
    return out


def money(v) -> float:
    s = re.sub(r"[^\d.\-()]", "", str(v or ""))
    neg = s.startswith("(") or s.startswith("-")
    s = s.strip("()-")
    try:
        x = float(s)
    except ValueError:
        return 0.0
    return -x if neg else x


def current_tax_year(today: datetime) -> int:
    """Texas taxes for year N are billed ~Oct N and go delinquent Feb 1 N+1.
    Until Feb 1 the newest year is not past due."""
    return today.year if today.month >= 2 else today.year - 1


class TravisDelinquentReader:
    adapter_version = "travis_delinquent/1"

    def discover_rows(self, rows: Iterable[List[str]], *, limit: int,
                      residential_lookup: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
                      min_due: float = 500.0, today: Optional[datetime] = None) -> Dict[str, Any]:
        today = today or datetime.utcnow()
        it = iter(rows)
        cols = map_columns(next(it, []))
        stats: Counter = Counter()
        accts: Dict[str, Dict[str, Any]] = {}
        get = lambda r, k: (r[cols[k]].strip() if k in cols and cols[k] < len(r) else "")  # noqa: E731
        for r in it:
            stats["rows"] += 1
            acct = re.sub(r"\s", "", get(r, "account"))
            if not acct:
                stats["no_account"] += 1
                continue
            m = re.search(r"(19|20)\d{2}", get(r, "year"))
            yr = int(m.group(0)) if m else None
            due = money(get(r, "due"))
            if yr is None or due <= 0 or yr >= current_tax_year(today):
                stats["not_past_due"] += 1
                continue
            a = accts.setdefault(acct, {"account": acct, "years": set(), "due": 0.0, "row": r, "seen": set()})
            key = (yr, due, get(r, "owner"))
            if key in a["seen"]:                      # an exact repeated row is not a second debt
                stats["duplicate_rows"] += 1
                continue
            a["seen"].add(key)
            a["years"].add(yr)
            a["due"] += due
        if residential_lookup is None:
            stats["no_residential_lookup"] = len(accts)
            return {"records": [], "stats": dict(stats), "source_url": PAGE}
        recs: List[Dict[str, Any]] = []
        for acct in sorted(accts):
            a = accts[acct]
            r = a["row"]
            owner = re.sub(r"\s+", " ", get(r, "owner"))
            situs = re.sub(r"\s+", " ", get(r, "situs"))
            if not owner or owner.upper() in ("UNKNOWN", "N/A", "OWNER", "CURRENT OWNER"):
                stats["missing_owner"] += 1
            elif not re.match(r"^\d", situs):
                stats["no_situs"] += 1
            elif a["due"] < min_due:
                stats["below_min_due"] += 1
            else:
                prop = residential_lookup(acct) or {}
                if prop.get("property_type") not in RESIDENTIAL_TYPES or not prop.get("improved", True):
                    stats["not_residential"] += 1
                elif _street_key(prop.get("street_address")) != _street_key(situs):
                    stats["situs_mismatch"] += 1
                else:
                    recs.append(self.record(a, owner, situs, get, cols, today))
            if len(recs) >= limit:
                break
        stats["records"] = len(recs)
        return {"records": recs, "stats": dict(stats), "source_url": PAGE}

    def discover_csv(self, text: str, **kw) -> Dict[str, Any]:
        return self.discover_rows(csv.reader(io.StringIO(text)), **kw)

    def record(self, a, owner, situs, get, cols, today) -> Dict[str, Any]:
        years = sorted(a["years"])
        r = a["row"]
        what = ("Travis County tax roll: $%s past due, tax year%s %s"
                % (format(a["due"], ",.2f"), "s" if len(years) > 1 else "",
                   "%s-%s" % (years[0], years[-1]) if len(years) > 1 else years[0]))
        return {
            "source_reference": "TRAVISDQ:%s" % a["account"],
            "street_address": situs, "unit": None, "city": None, "state": "TX", "zip_code": None,
            "county": "Travis", "parcel_apn": a["account"],
            "owner": {"name": owner, "mailing_street": get(r, "mail1") or None,
                      "mailing_city": get(r, "city").title() or None,
                      "mailing_state": get(r, "state").upper() or None,
                      "mailing_zip": re.sub(r"\D", "", get(r, "zip"))[:5] or None},
            "signals": [{"type": "TAX_DELINQUENT", "confidence": 90, "value": what,
                         "raw": "ACCOUNT=%s;YEARS=%s;DUE=%.2f" % (a["account"], ",".join(map(str, years)), a["due"]),
                         "evidence_basis": "Travis County Tax Office delinquent roll (balance remaining)",
                         "observed_days_ago": 0}],
            "_raw": {"account": a["account"], "years": years, "due": a["due"]},
            "_source_url": PAGE, "_source_updated_at": None, "_adapter_version": self.adapter_version,
            "_evidence": {"capability": CAPABILITY, "live_verified": False},
        }


_SUFFIX = {"STREET": "ST", "DRIVE": "DR", "ROAD": "RD", "LANE": "LN", "AVENUE": "AVE", "COURT": "CT",
           "BOULEVARD": "BLVD", "TRAIL": "TRL", "CIRCLE": "CIR"}


def _street_key(s: Optional[str]) -> str:
    toks = re.sub(r"[^A-Z0-9 ]", " ", (s or "").upper()).split()
    return " ".join(_SUFFIX.get(t, t) for t in toks)
