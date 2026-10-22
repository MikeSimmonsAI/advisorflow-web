"""Texas statewide property discovery from the TxGIO 2026 land-parcel files.

    https://api.tnris.org/api/v1/resources/?collection_id=<Land Parcels, 2026-09-25>
    https://data.geographic.texas.gov/<collection>/resources/stratmap26-landparcels_48<FIPS>_lp.zip

The Texas Geographic Information Office publishes each county appraisal
district's 2026 parcel roll as one zip per county (253 files). Each zip holds
a shapefile whose .dbf is a plain table: owner, mailing address, situs
address, the district's account id, state land-use code, values, year built.

Only that .dbf is read, streamed out of the zip with HTTP byte ranges (the
same ranged reader the DCAD and TAD files use) - never the whole download,
never the map geometry. A hunt reads a few counties; a strategy over "All
Texas" rotates through every county over successive hunts.

Kept: residential parcels (state code A*, or local code RES / A*) with a
situs house number and a named owner whose MAILING address is somewhere else
(absentee) or whose name is an estate. Nothing here is a distress claim: the
engine derives absentee / out-of-state / estate signals from the owner record
exactly as it does for the Collin and Denton rolls.
"""
from __future__ import annotations

import io
import re
import struct
from collections import Counter
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

from app.services.evosense.sources import base as B

COLLECTION = "1d33dafb-6e9c-40ac-887d-f4577909ed9b"          # "Land Parcels", acquired 2026-09-25
FILE = "https://data.geographic.texas.gov/%s/resources/stratmap26-landparcels_48%03d_lp.zip"
PAGE = "https://geographic.texas.gov/stratmap/land-parcels.html"
SCAN_CAP = 2_000_000                 # rows read per county, a hard stop

# Texas county FIPS codes run alphabetically by odd number: Anderson 001 ... Zavala 507.
TEXAS_COUNTIES = (
    "Anderson", "Andrews", "Angelina", "Aransas", "Archer", "Armstrong", "Atascosa", "Austin", "Bailey",
    "Bandera", "Bastrop", "Baylor", "Bee", "Bell", "Bexar", "Blanco", "Borden", "Bosque", "Bowie", "Brazoria",
    "Brazos", "Brewster", "Briscoe", "Brooks", "Brown", "Burleson", "Burnet", "Caldwell", "Calhoun", "Callahan",
    "Cameron", "Camp", "Carson", "Cass", "Castro", "Chambers", "Cherokee", "Childress", "Clay", "Cochran", "Coke",
    "Coleman", "Collin", "Collingsworth", "Colorado", "Comal", "Comanche", "Concho", "Cooke", "Coryell", "Cottle",
    "Crane", "Crockett", "Crosby", "Culberson", "Dallam", "Dallas", "Dawson", "Deaf Smith", "Delta", "Denton",
    "DeWitt", "Dickens", "Dimmit", "Donley", "Duval", "Eastland", "Ector", "Edwards", "Ellis", "El Paso", "Erath",
    "Falls", "Fannin", "Fayette", "Fisher", "Floyd", "Foard", "Fort Bend", "Franklin", "Freestone", "Frio",
    "Gaines", "Galveston", "Garza", "Gillespie", "Glasscock", "Goliad", "Gonzales", "Gray", "Grayson", "Gregg",
    "Grimes", "Guadalupe", "Hale", "Hall", "Hamilton", "Hansford", "Hardeman", "Hardin", "Harris", "Harrison",
    "Hartley", "Haskell", "Hays", "Hemphill", "Henderson", "Hidalgo", "Hill", "Hockley", "Hood", "Hopkins",
    "Houston", "Howard", "Hudspeth", "Hunt", "Hutchinson", "Irion", "Jack", "Jackson", "Jasper", "Jeff Davis",
    "Jefferson", "Jim Hogg", "Jim Wells", "Johnson", "Jones", "Karnes", "Kaufman", "Kendall", "Kenedy", "Kent",
    "Kerr", "Kimble", "King", "Kinney", "Kleberg", "Knox", "Lamar", "Lamb", "Lampasas", "La Salle", "Lavaca",
    "Lee", "Leon", "Liberty", "Limestone", "Lipscomb", "Live Oak", "Llano", "Loving", "Lubbock", "Lynn",
    "McCulloch", "McLennan", "McMullen", "Madison", "Marion", "Martin", "Mason", "Matagorda", "Maverick",
    "Medina", "Menard", "Midland", "Milam", "Mills", "Mitchell", "Montague", "Montgomery", "Moore", "Morris",
    "Motley", "Nacogdoches", "Navarro", "Newton", "Nolan", "Nueces", "Ochiltree", "Oldham", "Orange",
    "Palo Pinto", "Panola", "Parker", "Parmer", "Pecos", "Polk", "Potter", "Presidio", "Rains", "Randall",
    "Reagan", "Real", "Red River", "Reeves", "Refugio", "Roberts", "Robertson", "Rockwall", "Runnels", "Rusk",
    "Sabine", "San Augustine", "San Jacinto", "San Patricio", "San Saba", "Schleicher", "Scurry", "Shackelford",
    "Shelby", "Sherman", "Smith", "Somervell", "Starr", "Stephens", "Sterling", "Stonewall", "Sutton", "Swisher",
    "Tarrant", "Taylor", "Terrell", "Terry", "Throckmorton", "Titus", "Tom Green", "Travis", "Trinity", "Tyler",
    "Upshur", "Upton", "Uvalde", "Val Verde", "Van Zandt", "Victoria", "Walker", "Waller", "Ward", "Washington",
    "Webb", "Wharton", "Wheeler", "Wichita", "Wilbarger", "Willacy", "Williamson", "Wilson", "Winkler", "Wise",
    "Wood", "Yoakum", "Young", "Zapata", "Zavala")
_KEY = {re.sub(r"[^a-z]", "", c.lower()): c for c in TEXAS_COUNTIES}

STATES = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS",
          "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC",
          "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
          "PR", "GU", "VI"}
_ESTATE = re.compile(r"\bEST(ATE)?\s+OF\b|\bESTATE\b|\bDECEASED\b|\bDEC'?D\b|\bHEIRS?\b")


def county_name(name: str) -> Optional[str]:
    return _KEY.get(re.sub(r"[^a-z]", "", re.sub(r"\s+county$", "", (name or "").strip().lower())))


def fips(county: str) -> int:
    return 2 * TEXAS_COUNTIES.index(county) + 1


def county_url(county: str) -> str:
    return FILE % (COLLECTION, fips(county))


def dbf_rows(fh) -> Iterator[Dict[str, str]]:
    """Stream a dBASE III table: header, field descriptors, fixed-width rows."""
    head = fh.read(32)
    if len(head) < 32:
        raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "parcel table header is truncated")
    nrec, hlen, rlen = struct.unpack("<IHH", head[4:12])
    fields, pos = [], 1
    desc = fh.read(hlen - 32)
    for i in range(0, len(desc) - 1, 32):
        d = desc[i:i + 32]
        if not d or d[0] == 0x0D:
            break
        name = d[:11].split(b"\0", 1)[0].decode("latin-1").strip()
        size = d[16]
        fields.append((name, pos, pos + size))
        pos += size
    for _ in range(nrec):
        rec = fh.read(rlen)
        if len(rec) < rlen:
            break
        if rec[:1] == b"*":
            continue                                          # deleted row
        yield {n: rec[a:b].decode("latin-1").strip() for n, a, b in fields}


def _residential(r: Dict[str, str]) -> bool:
    st = (r.get("STAT_LAND_") or "").upper()
    loc = (r.get("LOC_LAND_U") or "").upper()
    if st:
        return st.startswith("A")                            # A1/A2... single-family residential
    if loc:
        return loc.startswith("RES") or loc.startswith("A")
    return False


def mail_state(r: Dict[str, str]) -> Optional[str]:
    s = (r.get("MAIL_STAT") or "").upper()
    addr = (r.get("MAIL_ADDR") or "").upper()
    if s in ("TE", "TX") or ", TEXAS " in addr or addr.endswith(", TEXAS"):
        return "TX"
    return s if s in STATES else None


def _int(v) -> Optional[int]:
    try:
        n = int(float(v))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def absentee(r: Dict[str, str]) -> Optional[bool]:
    """True when the owner's mailing street is a different place than the
    parcel; None when the mailing street is a PO box or missing (unknown)."""
    from app.services.evosense.sources.txgio import _core
    line = B.mailing_line(r.get("MAIL_LINE1"), r.get("MAIL_LINE2"))
    if not line or not re.match(r"^\d", line):
        return None
    situs = "%s %s" % (r.get("SITUS_NUM") or "", r.get("SITUS_ST_1") or "")
    n1, c1 = _core(situs)
    n2, c2 = _core(line)
    if not (n1 and n2):
        return None
    return not (n1 == n2 and (c1 == c2 or c1.startswith(c2) or c2.startswith(c1)))


class TxRollReader:
    adapter_version = "txgio_parcel_roll/1"

    def county_rows(self, county: str) -> Iterator[Dict[str, str]]:
        oz = B.open_zip("txgio_roll_%03d" % fips(county), county_url(county), allow_download=False)
        try:
            member = next((n for n in oz.zip.namelist() if n.lower().endswith(".dbf")), None)
            if not member:
                raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "%s parcel zip has no table" % county)
            with oz.zip.open(member) as raw:
                yield from dbf_rows(io.BufferedReader(raw, 1 << 20))
        finally:
            oz.close()

    def discover(self, county: str, *, limit: int, rotate: int = 0, cities=None, zips=None) -> Dict[str, Any]:
        """Up to `limit` absentee-owned or estate-owned houses in one county.
        `rotate` skips that many qualifying rows first, so successive hunts
        reach new owners instead of the same first page."""
        stats: Counter = Counter()
        cities = {c.strip().upper() for c in cities or [] if c}
        zips = {z[:5] for z in zips or [] if z}
        recs: List[Dict[str, Any]] = []
        head: List[Dict[str, Any]] = []          # the first qualifying rows, for wrap-around
        skip = max(0, rotate)
        for i, r in enumerate(self.county_rows(county)):
            if i >= SCAN_CAP:
                stats["scan_cap"] += 1
                break
            stats["scanned"] += 1
            if not _residential(r):
                continue
            stats["residential"] += 1
            if not (r.get("SITUS_NUM") or "").strip() or not (r.get("SITUS_ST_1") or "").strip():
                stats["no_situs"] += 1
                continue
            owner = (r.get("OWNER_NAME") or "").strip()
            if not owner or "CONFIDENTIAL" in owner.upper():
                stats["no_owner"] += 1
                continue
            if cities and (r.get("SITUS_CITY") or "").upper() not in cities:
                continue
            if zips and (r.get("SITUS_ZIP") or "")[:5] not in zips:
                continue
            away = absentee(r)
            estate = bool(_ESTATE.search(owner.upper()))
            if not (away or estate):
                continue
            stats["qualifying"] += 1
            if skip:
                skip -= 1
                if len(head) < limit:
                    head.append(r)
                continue
            recs.append(self.record(county, r))
            if len(recs) >= limit:
                break
        if len(recs) < limit and head:
            # The rotation ran past the end of this county's list: wrap to its start.
            stats["wrapped"] = 1
            recs.extend(self.record(county, r) for r in head[:limit - len(recs)])
        stats["records"] = len(recs)
        return {"records": recs, "stats": dict(stats), "source_url": county_url(county)}

    def record(self, county: str, r: Dict[str, str]) -> Dict[str, Any]:
        from app.services.evosense.sources.tarrant import state_code
        street = re.sub(r"\s+", " ", "%s %s" % (r.get("SITUS_NUM") or "", r.get("SITUS_ST_1") or "")).strip()
        city = (r.get("SITUS_CITY") or "").strip()
        city = None if not city or city.upper() in ("NO TOWN", "UNINCORPORATED", "NULL") else city.title()
        ms = mail_state(r)
        mcity = (r.get("MAIL_CITY") or "").strip()
        owner = {"name": re.sub(r"\s*&\s*$", "", (r.get("OWNER_NAME") or "").strip()),
                 "mailing_street": B.mailing_line(r.get("MAIL_LINE1"), r.get("MAIL_LINE2")),
                 "mailing_city": mcity.title() or None,
                 "mailing_state": state_code(ms) if ms else None,
                 "mailing_zip": re.sub(r"\D", "", r.get("MAIL_ZIP") or "")[:5] or None}
        built = _int(r.get("YEAR_BUILT"))
        acq = r.get("DATE_ACQ") or ""
        acq = "%s-%s-%s" % (acq[:4], acq[4:6], acq[6:8]) if re.match(r"^\d{8}$", acq) else None
        pid = (r.get("PROP_ID") or "").strip()
        rec = {
            "source_reference": "TXROLL:%s:%s:%s" % (county, pid or street, r.get("TAX_YEAR") or ""),
            "street_address": street, "unit": None, "city": city, "state": "TX",
            "zip_code": re.sub(r"\D", "", r.get("SITUS_ZIP") or "")[:5] or None,
            "county": county, "parcel_apn": None,             # each county's own sources own the APN format
            "property_type": "single_family",
            "year_built": built if built and 1800 < built <= datetime.utcnow().year else None,
            "owner": owner, "signals": [],
            "_raw": {k: r.get(k) for k in ("PROP_ID", "GEO_ID", "OWNER_NAME", "NAME_CARE", "STAT_LAND_",
                                           "LOC_LAND_U", "LAND_VALUE", "IMP_VALUE", "MKT_VALUE", "SITUS_ADDR",
                                           "SITUS_CITY", "SITUS_ZIP", "MAIL_ADDR", "MAIL_LINE1", "MAIL_LINE2",
                                           "MAIL_CITY", "MAIL_STAT", "MAIL_ZIP", "SOURCE", "DATE_ACQ", "FIPS",
                                           "COUNTY", "TAX_YEAR", "YEAR_BUILT")},
            "_source_url": county_url(county), "_source_updated_at": acq,
            "_adapter_version": self.adapter_version,
            "_evidence": {"county_account": pid or None, "district": r.get("SOURCE") or None,
                          "tax_year": r.get("TAX_YEAR") or None, "data_acquired": acq,
                          "land_use": r.get("STAT_LAND_") or r.get("LOC_LAND_U") or None},
        }
        value = _int(r.get("MKT_VALUE"))
        if value:
            rec["appraisal"] = {"value": value, "land": _int(r.get("LAND_VALUE")),
                                "improvements": _int(r.get("IMP_VALUE")), "year": _int(r.get("TAX_YEAR")),
                                "district": r.get("SOURCE") or "%s County appraisal district" % county,
                                "basis": "%s %s appraisal roll via TxGIO (property-tax value)"
                                         % (r.get("SOURCE") or county, r.get("TAX_YEAR") or "")}
        return rec
