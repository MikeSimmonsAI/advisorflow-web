"""Harris County houses from the Harris Central Appraisal District's own
public data files.

    https://hcad.org/pdata/pdata-property-downloads.html
    https://download.hcad.org/data/CAMA/<year>/Real_acct_owner.zip  ->  real_acct.txt

real_acct.txt is a tab-delimited table, one row per account: the mail-to
owner name and mailing address, the situs (site) address in parts, the state
property class, values and the year the improvements were built.

Rows are mapped onto the same fields the statewide parcel reader uses
(txgio_roll), so Harris houses are kept by exactly the same rule: residential
(state class A*), a situs house number, a named owner, and an owner who mails
elsewhere (absentee) or is an estate. The file is streamed with HTTP byte
ranges when the server allows them, otherwise downloaded once to the cache.
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any, Dict, Iterator, Optional

from app.services.evosense.sources import base as B
from app.services.evosense.sources.txgio_roll import TxRollReader

PAGE = "https://hcad.org/pdata/pdata-property-downloads.html"
FILE = "https://download.hcad.org/data/CAMA/%d/Real_acct_owner.zip"
MEMBER = "real_acct.txt"
DISTRICT = "HARRIS CENTRAL APPRAISAL DISTRICT"


def roll_year(now: Optional[datetime] = None) -> int:
    return (now or datetime.utcnow()).year


def file_url(year: Optional[int] = None) -> str:
    return FILE % (year or roll_year())


def _j(*parts) -> str:
    return re.sub(r"\s+", " ", " ".join((p or "").strip() for p in parts)).strip()


PLACEHOLDER_OWNERS = {"CURRENT OWNER", "UNKNOWN OWNER", "OWNER UNKNOWN", "UNKNOWN"}


def as_roll_row(r: Dict[str, str]) -> Dict[str, str]:
    """One real_acct.txt row in the statewide reader's field names. HCAD
    writes "CURRENT OWNER" where it has no name to mail to; that is no owner."""
    owner = (r.get("mailto") or "").strip()
    if owner.upper() in PLACEHOLDER_OWNERS:
        owner = ""
    num = _j(r.get("str_num"), r.get("str_num_sfx"))
    street = _j(r.get("str_pfx"), r.get("str"), r.get("str_sfx"), r.get("str_sfx_dir"))
    if num in ("0", ""):
        num = ""
    return {
        "PROP_ID": (r.get("acct") or "").strip(),
        "OWNER_NAME": owner,
        "STAT_LAND_": (r.get("state_class") or "").strip(),
        "LOC_LAND_U": "",
        "SITUS_NUM": num,
        "SITUS_ST_1": street,
        "SITUS_ADDR": (r.get("site_addr_1") or "").strip(),
        "SITUS_CITY": (r.get("site_addr_2") or "").strip(),
        "SITUS_ZIP": (r.get("site_addr_3") or "").strip(),
        "UNIT": (r.get("str_unit") or "").strip(),
        "MAIL_LINE1": (r.get("mail_addr_1") or "").strip(),
        "MAIL_LINE2": (r.get("mail_addr_2") or "").strip(),
        "MAIL_CITY": (r.get("mail_city") or "").strip(),
        "MAIL_STAT": (r.get("mail_state") or "").strip(),
        "MAIL_ZIP": (r.get("mail_zip") or "").strip(),
        "MAIL_ADDR": "",
        "LAND_VALUE": (r.get("land_val") or "").strip(),
        "IMP_VALUE": (r.get("bld_val") or "").strip(),
        "MKT_VALUE": (r.get("tot_mkt_val") or "").strip(),
        "YEAR_BUILT": (r.get("yr_impr") or "").strip(),
        "TAX_YEAR": (r.get("yr") or "").strip(),
        "SOURCE": DISTRICT,
        "DATE_ACQ": "",
        "COUNTY": "HARRIS",
        "NEW_OWNER_DATE": (r.get("new_own_dt") or "").strip(),
    }


def tsv_rows(lines) -> Iterator[Dict[str, str]]:
    header = None
    for line in lines:
        cells = line.split("\t")
        if header is None:
            header = [c.strip().lower() for c in cells]
            if "acct" not in header or "mailto" not in header or "state_class" not in header:
                raise B.SourceError(B.SOURCE_FORMAT_CHANGED,
                                    "real_acct.txt header changed: %s" % ", ".join(header[:8]))
            continue
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        yield dict(zip(header, cells))


class HcadRollReader(TxRollReader):
    adapter_version = "hcad_real_acct/1"
    key = "hcad_roll"

    def __init__(self, year: Optional[int] = None):
        self.year = year or roll_year()

    def url(self) -> str:
        return file_url(self.year)

    def county_rows(self, county: str = "Harris") -> Iterator[Dict[str, str]]:
        oz = B.open_zip(self.key, self.url())
        try:
            for r in tsv_rows(B.text_lines(oz.zip, MEMBER)):
                row = as_roll_row(r)
                if row["UNIT"]:
                    continue                       # apartments / condo units: not a house
                yield row
        finally:
            oz.close()

    def discover(self, county: str = "Harris", **kw) -> Dict[str, Any]:
        res = super().discover("Harris", **kw)
        res["source_url"] = self.url()
        return res

    def record(self, county: str, r: Dict[str, str]) -> Dict[str, Any]:
        rec = super().record("Harris", r)
        acct = r.get("PROP_ID") or ""
        rec["source_reference"] = "HCAD:%s:%s" % (acct or rec["street_address"], r.get("TAX_YEAR") or "")
        rec["parcel_apn"] = acct or None              # HCAD's own 13-digit account
        rec["_source_url"] = self.url()
        rec["_adapter_version"] = self.adapter_version
        rec["_raw"]["NEW_OWNER_DATE"] = r.get("NEW_OWNER_DATE")
        rec["_evidence"]["district"] = DISTRICT
        if rec.get("appraisal"):
            rec["appraisal"]["district"] = DISTRICT
            rec["appraisal"]["basis"] = "HCAD %s appraisal roll (property-tax value)" % (r.get("TAX_YEAR") or "")
        return rec
