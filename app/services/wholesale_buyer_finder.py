"""Find cash buyers for free in the county appraisal rolls.

The people most likely to buy a wholesale deal are the ones already doing it:
owners - companies and individuals - who hold several houses in the county
and keep buying. The appraisal district files EvoSense already reads (DCAD
for Dallas, TAD for Tarrant) list every parcel's owner, mailing address and
last deed date. Grouping them by owner gives a list of local investors:

    properties   how many houses / multifamily parcels they hold in the county
    recent       how many of those changed hands to them in the last N months
                 (a deed date - the record states no price, and Texas does not
                 publish one, so "bought" means "took title", nothing more)

Nothing here is a guess: every number is a count of rows in the county file,
and the list says which file and when. It does NOT find phone numbers or
emails (the roll has none) - an imported investor starts with a mailing
address, and the phone lookup or a letter gets the conversation going.

Who is left out: government, churches, nonprofits, associations/HOAs, banks
and mortgage servicers, housing authorities, utilities, and anyone whose
owner record is confidential. Large national landlords (over `max_props`)
are left out by default - they do not buy wholesale deals.

Memory: three streaming passes; per-owner state is kept as hashed integers
until the qualifying owners are known, so a 900k-parcel county fits.
"""
from __future__ import annotations

import json
import os
import re
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, Optional, Tuple

from app.services.evosense.sources import base as B

COUNTIES = ("dallas", "tarrant")

_EXCLUDE = re.compile(
    r"\b(CITY OF|COUNTY OF|STATE OF|UNITED STATES|USA|HUD|SECRETARY OF|VETERANS|ISD|SCHOOL|"
    r"HOUSING AUTH|HOUSING FINANCE|LAND BANK|CHURCH|MINISTR|DIOCESE|TEMPLE|MOSQUE|"
    r"HOMEOWNERS|HOA|ASSOCIATION|ASSN|CONDOMINIUM|COMMUNITY ASSOC|BANK|MORTGAGE|FANNIE|FREDDIE|"
    r"FEDERAL|NATIONAL ASSOC|UNIVERSITY|COLLEGE|HOSPITAL|RAILROAD|RAILWAY|ONCOR|ATMOS|UTILIT|"
    r"CEMETERY|HABITAT FOR HUMANITY|DALLAS AREA RAPID|DART|NOT ON ROLL|UNKNOWN)\b")

# Home builders, iBuyers and institutional rental funds: they hold many houses
# and take title constantly, but they do not buy wholesale assignments.
_INSTITUTIONAL = re.compile(
    r"\b(LENNAR|HORTON|PULTE|KB HOME|MERITAGE|HIGHLAND HOMES|BLOOMFIELD|HISTORY MAKER|CENTEX|"
    r"TAYLOR MORRISON|ASHTON WOODS|WEEKLEY|GEHAN|PERRY HOMES|M I HOMES|CENTURY COMMUNITIES|"
    r"FIRST TEXAS HOMES|IMPRESSION HOMES|CHESMAR|TRENDMAKER|LGI HOMES|STARLIGHT HOMES|ANTARES|"
    r"PACESETTER|SANDLIN|NEWMARK|BEAZER|TOLL BROTHERS|SHEA HOMES|DREES|GRAND HOMES|"
    r"OPENDOOR|OFFERPAD|INVITATION HOMES|AMERICAN HOMES 4 RENT|AMH|PROGRESS RESIDENTIAL|"
    r"TRICON|FIRSTKEY|FKH|HOME PARTNERS|MAIN STREET RENEWAL|VINEBROOK|PRETIUM|"
    r"BORROWER|OWNERCO|PROPCO|SFR|BUILD TO RENT|BTR|HOMEBUYER|PURCHASING FUND|ASSET COMPANY|"
    r"BRIGHTLAND|LEGEND CLASSIC|BUILDING CO|BUILDING COMPANY|BUILDERS|CONSTRUCTION|"
    r"LAND TRUST|ECONOMIC DEVELOPMENT|DEVELOPMENT CORP|DEVELOPMENT CORPORATION)\b")

_ENTITY = re.compile(r"\b(LLC|L L C|INC|CORP|CORPORATION|CO|LP|LTD|LLP|HOLDINGS|PROPERTIES|"
                     r"INVESTMENTS?|CAPITAL|VENTURES|GROUP|PARTNERS|REALTY|HOMES|ENTERPRISES|"
                     r"TRUST|TR|TRUSTEE)\b")


def norm_name(name: str) -> str:
    n = re.sub(r"[^A-Z0-9& ]", " ", (name or "").upper())
    n = re.sub(r"\bL L C\b", "LLC", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def owner_kind(name: str) -> str:
    return "company" if _ENTITY.search(norm_name(name)) else "person"


def institutional(name: str) -> bool:
    return bool(_INSTITUTIONAL.search(norm_name(name)))


def excluded(name: str) -> bool:
    n = norm_name(name)
    if not n or len(n) < 3:
        return True
    if _EXCLUDE.search(n) or _INSTITUTIONAL.search(n):
        return True
    try:
        from app.services.evosense.ingest import owner_type_of
        return owner_type_of(name) in ("government", "religious_org", "nonprofit")
    except Exception:
        return False


def _date(v: str) -> Optional[datetime]:
    v = (v or "").strip()[:10]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


# A parcel row, county-neutral:
# (account, owner, mail_street, mail_city, mail_state, mail_zip, situs, city, zip, deed_date, kind)
Row = Tuple[str, str, Optional[str], Optional[str], Optional[str], Optional[str],
            Optional[str], Optional[str], Optional[str], Optional[datetime], str]


# ── county readers ─────────────────────────────────────────────────────────

def _dallas_rows(zf, residential: Optional[Dict[int, str]]) -> Iterator[Row]:
    """DCAD ACCOUNT_INFO rows for residential accounts (house / multifamily /
    vacant residential lot), owner not confidential."""
    from app.services.evosense.sources.dallas import _csv_rows
    from app.services.evosense.sources.tarrant import state_code
    for header, vals in _csv_rows(zf, "ACCOUNT_INFO.CSV"):
        row = dict(zip(header, vals))
        acct = (row.get("ACCOUNT_NUM") or "").strip()
        kind = residential.get(hash(acct)) if residential is not None else "house"
        if not kind:
            continue
        if (row.get("EXCLUDE_OWNER") or "").strip().upper() == "Y":
            continue
        owner = " ".join(x.strip() for x in (row.get("OWNER_NAME1"), row.get("OWNER_NAME2")) if x and x.strip())
        if not owner:
            continue
        lines = (row.get("OWNER_ADDRESS_LINE1"), row.get("OWNER_ADDRESS_LINE2"),
                 row.get("OWNER_ADDRESS_LINE3"), row.get("OWNER_ADDRESS_LINE4"))
        situs = " ".join(x for x in ((row.get("STREET_NUM") or "").strip(),
                                     re.sub(r"\s+", " ", row.get("FULL_STREET_NAME") or "").strip()) if x)
        yield (acct, owner, B.mailing_line(*lines), (row.get("OWNER_CITY") or "").strip().title() or None,
               state_code((row.get("OWNER_STATE") or "").strip().upper()),
               re.sub(r"\D", "", row.get("OWNER_ZIPCODE") or "")[:5] or None,
               situs or None,
               re.sub(r"\s*\(.*\)$", "", (row.get("PROPERTY_CITY") or "").strip()).title() or None,
               re.sub(r"\D", "", row.get("PROPERTY_ZIPCODE") or "")[:5] or None,
               _date(row.get("DEED_TXFR_DATE")), kind)


def _dallas_residential(zf) -> Dict[int, str]:
    """hash(account) -> 'house' | 'multifamily' | 'lot' from the state
    property-type (SPTD) code: A = single-family, B = multifamily,
    C1 = vacant residential lot."""
    from app.services.evosense.sources.dallas import _csv_rows
    out: Dict[int, str] = {}
    for header, vals in _csv_rows(zf, "ACCOUNT_APPRL_YEAR.CSV"):
        row = dict(zip(header, vals))
        code = (row.get("SPTD_CODE") or "").strip().upper()
        kind = "house" if code.startswith("A") else "multifamily" if code.startswith("B") \
            else "lot" if code.startswith("C1") else None
        if kind:
            out[hash((row.get("ACCOUNT_NUM") or "").strip())] = kind
    return out


def _tarrant_rows(zf) -> Iterator[Row]:
    from app.services.evosense.sources.tarrant import _split_citystate
    member = next((n for n in zf.namelist() if n.lower().endswith(".txt")), None)
    if member is None:
        raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "no .txt data file inside the TAD archive")
    header = None
    for line in B.text_lines(zf, member):
        if header is None:
            header = line.split("|")
            continue
        vals = line.split("|")
        if len(vals) < len(header):
            continue
        row = dict(zip(header, vals))
        code = (row.get("State_Use_Code") or "").strip().upper()
        kind = "house" if code.startswith("A") else "multifamily" if code.startswith("B") \
            else "lot" if code.startswith("C1") else None
        if not kind:
            continue
        owner = (row.get("Owner_Name") or "").strip()
        if not owner:
            continue
        city, st = _split_citystate(row.get("Owner_CityState") or "")
        yield (row["Account_Num"].strip(), owner, (row.get("Owner_Address") or "").strip() or None,
               (city or "").strip().title() or None, st,
               re.sub(r"\D", "", row.get("Owner_Zip") or "")[:5] or None,
               re.sub(r"\s+", " ", (row.get("Situs_Address") or "")).strip() or None, None, None,
               _date(row.get("Deed_Date")), kind)


def _open(county: str):
    if county == "dallas":
        from app.services.evosense.sources.dallas import DcadReader
        oz = B.open_zip("dcad", DcadReader().locate())
        res = _dallas_residential(oz.zip)
        return oz, (lambda: _dallas_rows(oz.zip, res)), "DCAD certified appraisal export"
    if county == "tarrant":
        from app.services.evosense.sources.tarrant import TAD_URL
        oz = B.open_zip("tad", TAD_URL)
        return oz, (lambda: _tarrant_rows(oz.zip)), "TAD property data export"
    raise ValueError("county must be one of %s" % ", ".join(COUNTIES))


# ── the scan ───────────────────────────────────────────────────────────────

def scan(rows_factory, *, county: str, min_props: int = 3, max_props: int = 60,
         recent_months: int = 24, limit: int = 2000, today: Optional[datetime] = None) -> Dict[str, Any]:
    """Two passes over the parcel rows: count per owner (hashed), then collect
    details for the owners who qualify."""
    today = today or datetime.utcnow()
    since = today - timedelta(days=int(recent_months * 30.44))
    stats: Counter = Counter()
    counts: Dict[int, int] = {}
    for r in rows_factory():
        stats["parcels"] += 1
        k = hash(norm_name(r[1]))
        counts[k] = counts.get(k, 0) + 1
    stats["owners"] = len(counts)
    wanted = {k for k, n in counts.items() if min_props <= n <= max_props}
    stats["owners_too_big"] = sum(1 for n in counts.values() if n > max_props)
    del counts

    agg: Dict[int, Dict[str, Any]] = {}
    for (acct, owner, mst, mcity, mstate, mzip, situs, city, zip5, deed, kind) in rows_factory():
        k = hash(norm_name(owner))
        if k not in wanted:
            continue
        a = agg.get(k)
        if a is None:
            if excluded(owner):
                wanted.discard(k)
                stats["owners_excluded"] += 1
                continue
            a = agg[k] = {"name": owner.strip(), "kinds": Counter(), "zips": Counter(), "cities": Counter(),
                          "mail": Counter(), "recent": 0, "last": None, "samples": []}
        a["kinds"][kind] += 1
        if zip5:
            a["zips"][zip5] += 1
        if city:
            a["cities"][city] += 1
        if mst:
            a["mail"][(mst, mcity, mstate, mzip)] += 1
        if deed:
            if deed >= since:
                a["recent"] += 1
            if a["last"] is None or deed > a["last"]:
                a["last"] = deed
        if situs and len(a["samples"]) < 4:
            a["samples"].append(" ".join(x for x in (situs, city, zip5) if x))

    out = []
    for k, a in agg.items():
        total = sum(a["kinds"].values())
        mail = a["mail"].most_common(1)[0][0] if a["mail"] else (None, None, None, None)
        out.append({
            "key": "%s:%x" % (county, k & 0xFFFFFFFFFFFF),
            "name": a["name"], "kind": owner_kind(a["name"]),
            "properties": total, "houses": a["kinds"]["house"], "multifamily": a["kinds"]["multifamily"],
            "lots": a["kinds"]["lot"], "bought_recently": a["recent"],
            "last_deed": a["last"].strftime("%Y-%m-%d") if a["last"] else None,
            "mailing": {"street": mail[0], "city": mail[1], "state": mail[2], "zip": mail[3]},
            "out_of_state": bool(mail[2] and mail[2] != "TX"),
            "zips": [z for z, _ in a["zips"].most_common(8)],
            "cities": [c for c, _ in a["cities"].most_common(5)],
            "samples": a["samples"],
        })
    # Local, active operators first: a Texas mailing address, then the most
    # recent purchases, then the most held. A portfolio bought entirely in the
    # window (every parcel recent) is usually a fund or a builder's rental
    # program, so it sorts after owners who have been buying over time.
    out.sort(key=lambda x: (x["out_of_state"],
                            x["properties"] > 5 and x["bought_recently"] == x["properties"],
                            -x["bought_recently"], -x["properties"], x["name"]))
    stats["qualifying"] = len(out)
    return {"county": county, "generated_at": today.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "criteria": {"min_props": min_props, "max_props": max_props, "recent_months": recent_months},
            "stats": dict(stats), "buyers": out[:limit]}


# ── background run + stored result ─────────────────────────────────────────

_LOCK = threading.Lock()
_STATE: Dict[str, Dict[str, Any]] = {}


def result_path(county: str) -> str:
    return os.path.join(B.cache_dir(), "buyer_finder_%s.json" % county)


def load(county: str) -> Optional[Dict[str, Any]]:
    try:
        with open(result_path(county), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def status(county: str) -> Dict[str, Any]:
    st = dict(_STATE.get(county) or {"state": "idle"})
    res = load(county)
    if res:
        st["last_result_at"] = res.get("generated_at")
        st["last_count"] = len(res.get("buyers") or [])
    return st


def run(county: str, **kw) -> Dict[str, Any]:
    oz, rows_factory, label = _open(county)
    try:
        res = scan(rows_factory, county=county, **kw)
    finally:
        oz.close()
    res["source"] = label
    tmp = result_path(county) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(res, fh)
    os.replace(tmp, result_path(county))
    return res


def start(county: str, **kw) -> Dict[str, Any]:
    """Run in a background thread (a county takes a few minutes). One at a time."""
    if county not in COUNTIES:
        raise ValueError("county must be one of %s" % ", ".join(COUNTIES))
    with _LOCK:
        if any(s.get("state") == "running" for s in _STATE.values()):
            return {"started": False, "reason": "A scan is already running.", **status(county)}
        _STATE[county] = {"state": "running", "started_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}

    def work():
        try:
            res = run(county, **kw)
            _STATE[county] = {"state": "done", "finished_at": res["generated_at"],
                              "found": len(res["buyers"])}
        except Exception as exc:  # recorded and shown, never swallowed
            _STATE[county] = {"state": "failed", "error": str(exc)[:300],
                              "finished_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}

    threading.Thread(target=work, name="buyer-finder-%s" % county, daemon=True).start()
    return {"started": True, **status(county)}
