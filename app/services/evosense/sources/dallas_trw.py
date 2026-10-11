"""Dallas County Tax Office - Tax Roll (TRW) file: current and DELINQUENT
accounts for every taxing unit the county collects for, published free every
week ("available on the Dallas County Tax Office website for no additional
charge"; robots.txt allows all agents).

    Page    https://www.dallascounty.org/departments/tax/tax-roll.php
    File    trwfile.NNNNNN.zip (~290 MB zip, one fixed-width ASCII member,
            layout TRW-02), read with HTTP byte ranges - only the part needed.

The Tax Office's own definition: an account is delinquent when its DUE-DATE is
in the past and a balance remains. EvoSense reads the DALLAS COUNTY taxing
unit's rows (jurisdiction 1002 - every property in the county carries county
tax), residential category A1*, real property, and adds the years up per
account. The amount is the Tax Office's own "total amount due" (levy, penalty
and interest) for the county unit only - other units (city, school) usually
owe more, so it is stated as "at least".

Never read as a seller lead:
  * DEFERRAL 'D' - a 65+ / disabled homeowner's legally deferred taxes;
  * a BANKRUPTCY on file - the automatic stay;
and a payment agreement is carried as evidence ("on a payment plan").
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services.evosense.sources import base as B

PAGE = "https://www.dallascounty.org/departments/tax/tax-roll.php"
BASE = "https://www.dallascounty.org"
COUNTY_UNIT = "1002"
SUIT_CODES = {"A", "J", "L"}
CITY_ABBR = {"DA": "Dallas"}


def f(line: str, start: int, length: int) -> str:
    return line[start - 1:start - 1 + length].strip()


def num(v: str) -> int:
    try:
        return int(v or 0)
    except ValueError:
        return 0


def parse(line: str) -> Dict[str, Any]:
    return {"account": f(line, 1, 34), "year": f(line, 35, 4), "jurisdiction": f(line, 39, 4),
            "cad_account": f(line, 43, 34), "levy": num(f(line, 77, 11)), "homestead": f(line, 88, 1),
            "over65": f(line, 89, 1), "date_paid": f(line, 93, 8), "due_date": f(line, 101, 8),
            "balance": num(f(line, 111, 11)), "suit": f(line, 122, 1), "cause": f(line, 123, 40),
            "bank": f(line, 163, 1), "deferral": f(line, 219, 1), "category": f(line, 222, 4),
            "owner": f(line, 226, 40), "addr2": f(line, 266, 40), "addr3": f(line, 306, 40),
            "addr4": f(line, 346, 40), "city": f(line, 386, 40), "state": f(line, 426, 2),
            "zip": f(line, 428, 12), "roll": f(line, 440, 1), "situs_no": f(line, 441, 8),
            "situs_name": f(line, 449, 40), "agreement": f(line, 489, 1), "total_due": num(f(line, 490, 11))}


def _d(v: str) -> Optional[datetime]:
    try:
        return datetime.strptime(v, "%Y%m%d")
    except (TypeError, ValueError):
        return None


class TrwReader:
    adapter_version = "dallas_trw/2"

    def locate(self) -> str:
        override = B.local_override("dallas_trw")
        if override:
            return "file://" + override
        html = B.get_text(PAGE)
        found = [u for u in re.findall(r"(/Assets/uploads/docs/tax/trw/trwfile\.\d+\.zip)", html, re.I)]
        if not found:
            raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "no trwfile.NNNNNN.zip link on the Tax Roll (TRW) page")
        return BASE + found[0]

    def discover(self, *, limit: int, skip: int = 0, min_due_cents: int = 25000,
                 today: Optional[datetime] = None, max_rows: int = 3_000_000) -> Dict[str, Any]:
        """The data member is sorted by jurisdiction, then tax year, then account,
        so one account's delinquent years are spread through the county block.
        The whole county block (~1M rows, ~60 MB of byte ranges) is read once,
        rows are added up per account, and `skip` rotates which accounts this
        run returns (wrapping to the top)."""
        today = today or datetime.utcnow()
        url = self.locate()
        oz = B.open_zip("dallas_trw", url, allow_download=False)
        stats: Counter = Counter()
        as_of = None
        names = oz.zip.namelist()
        m = re.search(r"flat404\.DALLASCOUNTY\.(\d{8})", " ".join(names))
        if m:
            as_of = _d(m.group(1))
        member = next((n for n in names if "flat404" in n.lower()), None)
        if member is None:
            oz.close()
            raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "the TRW zip has no flat404 data member")
        accts: Dict[str, Dict[str, Any]] = {}
        seen_county = False
        try:
            with oz.zip.open(member) as fh:
                for raw in fh:
                    stats["rows"] += 1
                    if stats["rows"] > max_rows:
                        stats["row_cap_hit"] = 1
                        break
                    line = raw.decode("latin-1").rstrip("\r\n")
                    if len(line) < 500:
                        stats["short_line"] += 1
                        if stats["short_line"] > 50:
                            raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "many TRW lines shorter than the layout")
                        continue
                    if line[38:42] != COUNTY_UNIT:
                        if seen_county:
                            break                     # the county unit's rows are contiguous
                        continue
                    seen_county = True
                    stats["county_rows"] += 1
                    if line[439:440] != "R" or line[221:223] != "A1":
                        continue
                    r = parse(line)
                    due = _d(r["due_date"])
                    if r["balance"] <= 0 or due is None or due >= today:
                        continue
                    stats["delinquent_rows"] += 1
                    cur = accts.get(r["account"])
                    if cur is None:
                        cur = accts[r["account"]] = {
                            "account": r["account"], "cad_account": r["cad_account"], "years": [],
                            "total_due": 0, "suit": None, "cause": None, "deferral": False,
                            "bankruptcy": False, "agreement": False, "first": r}
                    else:
                        cur["first"] = r                  # newest year carries the current owner
                    cur["years"].append(r["year"])
                    cur["total_due"] += r["total_due"]
                    if r["suit"] in SUIT_CODES:
                        cur["suit"], cur["cause"] = r["suit"], r["cause"] or cur["cause"]
                    cur["deferral"] = cur["deferral"] or r["deferral"] == "D"
                    cur["bankruptcy"] = cur["bankruptcy"] or bool(r["bank"])
                    cur["agreement"] = cur["agreement"] or r["agreement"] == "Y"
        finally:
            stats["bytes_fetched"] = oz.bytes_fetched()
            oz.close()
        if not seen_county:
            raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "no Dallas County (unit %s) rows in the TRW file" % COUNTY_UNIT)
        pool: List[Dict[str, Any]] = []
        for acc in accts.values():
            if acc["deferral"]:
                stats["deferred_skipped"] += 1
            elif acc["bankruptcy"]:
                stats["bankruptcy_skipped"] += 1
            elif acc["total_due"] < min_due_cents:
                stats["below_min_due"] += 1
            else:
                pool.append(acc)
        pool.sort(key=lambda a: a["account"])
        stats["delinquent_accounts"] = len(pool)
        records: List[Dict[str, Any]] = []
        if pool:
            start = skip % len(pool)
            take = min(limit, len(pool))
            if start + take > len(pool):
                stats["wrapped"] = start + take - len(pool)
            for i in range(take):
                records.append(self.record(pool[(start + i) % len(pool)], url, as_of))
        stats["records"] = len(records)
        return {"records": records, "stats": dict(stats), "source_url": url, "source_updated_at": as_of}

    def record(self, acc: Dict[str, Any], url: str, as_of: Optional[datetime]) -> Dict[str, Any]:
        r = acc["first"]
        name_part, _, city_abbr = (r["situs_name"] or "").partition(",")
        street = re.sub(r"\s+", " ", " ".join(x for x in (r["situs_no"].lstrip("0") or None, name_part.strip())
                                               if x)).strip() or None
        years = sorted(set(acc["years"]))
        amount = acc["total_due"] / 100.0
        owner = None
        if r["owner"]:
            from app.services.evosense.sources.tarrant import state_code
            lines = (r["addr2"], r["addr3"], r["addr4"])
            more = B.name_continuation(*lines)
            owner = {"name": " ".join(x for x in (r["owner"], more) if x),
                     "mailing_street": B.mailing_line(*lines),
                     "mailing_city": (r["city"] or "").title() or None,
                     "mailing_state": state_code((r["state"] or "").upper()),
                     "mailing_zip": re.sub(r"\D", "", r["zip"] or "")[:5] or None}
        what = ("Dallas County tax roll: at least $%s delinquent to the county alone, tax year%s %s"
                % (format(amount, ",.2f"), "s" if len(years) > 1 else "",
                   ("%s-%s" % (years[0], years[-1])) if len(years) > 1 else years[0]))
        if acc["agreement"]:
            what += " (on a payment agreement)"
        # The file says the balance is STILL owed as of its date, so the
        # evidence is as fresh as the file (like the Tarrant roll: a prior-year
        # balance is dated by the roll, not by the year it first went unpaid -
        # otherwise a longer delinquency would read as staler evidence).
        # Only a single, latest-year delinquency carries its Feb 1 date.
        newest = max((int(y) for y in years if y.isdigit()), default=None)
        single = len(years) == 1 and newest is not None
        observed = max(0, (datetime.utcnow() - as_of).days) if as_of else 0
        sigs = [{"type": "TAX_DELINQUENT", "confidence": 90, "value": what,
                 "raw": "ACCOUNT=%s;YEARS=%s;TOTAL_DUE=%s" % (acc["account"], ",".join(years), acc["total_due"]),
                 "effective_at": "%s-02-01" % (newest + 1) if single else None,
                 "evidence_basis": ("Dallas County Tax Office TRW file (DUE-DATE passed, balance remaining)"
                                    if single else "Dallas County Tax Office TRW file: prior-year balance still owed"),
                 "observed_days_ago": observed}]
        if acc["suit"]:
            sigs.append({"type": "TAX_SUIT", "confidence": 90,
                         "value": "delinquent-tax lawsuit pending (cause %s)" % (acc["cause"] or "on file"),
                         "raw": "SUIT=%s;CAUSE=%s" % (acc["suit"], acc["cause"]),
                         "evidence_basis": "Dallas County Tax Office TRW suit flag", "observed_days_ago": observed})
        return {
            "source_reference": "DALTRW:%s" % acc["account"],
            "street_address": street, "unit": None,
            "city": CITY_ABBR.get(city_abbr.strip().upper()), "state": "TX", "zip_code": None,
            "county": "Dallas", "parcel_apn": acc["cad_account"] or None,
            "owner": owner, "signals": sigs,
            "occupancy": "owner_occupied" if r["homestead"] == "Y" else None,
            # The whole per-account sum (every year's row folded in), so a
            # reprocess can rebuild the record exactly under a newer adapter.
            "_raw": {"acc": acc, "as_of": as_of.strftime("%Y-%m-%d") if as_of else None},
            "_source_url": PAGE,
            "_source_updated_at": as_of, "_adapter_version": self.adapter_version,
            "_evidence": {"years": years, "total_due_county_unit": amount, "suit": acc["suit"],
                          "cause": acc["cause"], "payment_agreement": acc["agreement"]},
        }
