"""BUYER NETWORK - import the operator's existing cash-buyer network, review it,
and activate the DFW part of it.

Built on what exists: the WholesaleBuyer / WholesaleBuyBox rows the buyer CRM,
matching and disposition already use, the shared file reader of Universal Intake
(CSV / TSV / XLSX), and the R2 evidence tiers. No second buyer table.

THE FLOW (a batch per file):
    upload -> mapping (suggested, editable) -> analyze (validate + duplicate check)
    -> review (conflicts decided by a person, error rows fixed) -> commit
    -> retry (commit again after fixing; applied rows are never applied twice)
    -> rollback (buyers the batch created are removed if nothing happened to them
       since, otherwise deactivated and listed; updated buyers get their old values
       back; buy boxes the batch added are removed). Nothing is deleted silently:
       every rollback returns what it did, row by row.

DUPLICATES are explained, never guessed:
    same email (any case)            -> the same buyer
    same phone (10 digits)           -> the same buyer
    email says buyer A, phone says B -> CONFLICT: a person decides
    same name AND company, no shared email/phone -> CONFLICT: a person decides
    same company only / same ZIP     -> NOT a match (two people at one company)

WHAT A FILE CAN AND CANNOT PROVE. A file is the operator's own records. Its
"proof of funds", "status", "last contact" and "past purchases" are stored as
what the file said (file_* columns) and shown that way. They never make a buyer
"verified active": that still needs evidence this platform observed (R2).
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

MAX_BYTES = 25 * 1024 * 1024
MAX_ROWS = 20000

# ── canonical fields and the headers people actually use ───────────────────
FIELDS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "entity_type": ("Individual or company", ("type", "entity", "entity type", "buyer type", "individual or company")),
    "company_name": ("Company", ("company", "company name", "business", "business name", "llc", "entity name",
                                 "organization", "org")),
    "contact_name": ("Contact name", ("name", "contact", "contact name", "buyer", "buyer name", "full name", "owner")),
    "first_name": ("First name", ("first", "first name", "firstname")),
    "last_name": ("Last name", ("last", "last name", "lastname", "surname")),
    "phone": ("Phone", ("phone", "phone number", "mobile", "cell", "cell phone", "telephone", "tel", "primary phone")),
    "phone2": ("Second phone", ("phone 2", "phone2", "alt phone", "secondary phone", "other phone", "work phone")),
    "email": ("Email", ("email", "email address", "e-mail", "mail")),
    "states": ("States", ("state", "states", "buying states", "target states")),
    "counties": ("Counties", ("county", "counties", "buying counties", "target counties")),
    "cities": ("Cities", ("city", "cities", "buying cities", "target cities")),
    "zips": ("ZIP codes", ("zip", "zips", "zip code", "zip codes", "zipcodes", "postal code", "target zips")),
    "markets": ("Markets", ("market", "markets", "metro", "area", "areas", "region", "buying area", "territory")),
    "property_types": ("Property types", ("property type", "property types", "asset type", "asset types", "types")),
    "min_price": ("Minimum price", ("min price", "minimum price", "price min", "min purchase", "price from")),
    "max_price": ("Maximum price", ("max price", "maximum price", "price max", "max purchase", "price to", "budget")),
    "rehab_tolerance": ("Rehab tolerance", ("rehab", "rehab tolerance", "rehab level", "condition", "repair level")),
    "financing": ("Cash / financing", ("financing", "funding", "cash or finance", "payment", "purchase method",
                                       "cash buyer", "funds")),
    "strategies": ("Investment strategy", ("strategy", "strategies", "exit", "exit strategy", "investment strategy")),
    "close_days": ("Closing speed (days)", ("close days", "closing days", "days to close", "closing speed",
                                           "close time", "typical close")),
    "pof_status": ("Proof of funds (as your file says)", ("pof", "proof of funds", "pof status", "pof on file")),
    "purchase_evidence": ("Prior purchases", ("purchases", "prior purchases", "past purchases", "deals done",
                                              "recent purchase", "purchase history", "bought")),
    "past_deals": ("Number of past deals (claimed)", ("deals", "past deals", "deal count", "# deals", "number of deals")),
    "last_contact": ("Last contact", ("last contact", "last contacted", "last touch", "last call")),
    "last_response": ("Last response", ("last response", "last reply", "last responded")),
    "status": ("Buyer status (your file)", ("status", "buyer status", "active")),
    "notes": ("Notes", ("notes", "note", "comments", "comment", "description")),
    "source": ("Source", ("source", "lead source", "list", "list source")),
}
LIST_FIELDS = ("states", "counties", "cities", "zips", "markets", "property_types", "strategies")
REHAB = {"light": "light", "cosmetic": "light", "moderate": "moderate", "medium": "moderate", "heavy": "heavy",
         "full": "full_gut", "full gut": "full_gut", "full_gut": "full_gut", "gut": "full_gut", "any": "full_gut"}
STRATEGY = {"flip": "flip", "fix and flip": "flip", "fix & flip": "flip", "fix/flip": "flip", "rental": "rental",
            "rent": "rental", "buy and hold": "rental", "buy & hold": "rental", "hold": "rental", "brrrr": "brrrr",
            "land": "land", "wholesale": "other", "str": "rental", "airbnb": "rental", "other": "other"}
PTYPE = {"sfr": "single_family", "single family": "single_family", "single-family": "single_family",
         "sfh": "single_family", "house": "single_family", "single_family": "single_family",
         "multifamily": "multifamily", "multi family": "multifamily", "multi-family": "multifamily", "mf": "multifamily",
         "duplex": "duplex", "triplex": "multifamily", "fourplex": "multifamily", "quad": "multifamily",
         "condo": "condo", "townhouse": "townhome", "townhome": "townhome", "land": "land", "lot": "land",
         "lots": "land", "mobile": "mobile_home", "mobile home": "mobile_home", "manufactured": "mobile_home",
         "commercial": "commercial", "residential": "residential", "industrial": "industrial",
         "warehouse": "warehouse", "office": "office", "retail": "retail", "agricultural": "agricultural",
         "farm": "farm", "ranch": "ranch", "acreage": "land", "mixed use": "mixed_use", "mixed-use": "mixed_use",
         "self storage": "self_storage", "storage": "self_storage", "rv park": "rv_park",
         "mobile home park": "mobile_home_park", "hotel": "hotel", "apartments": "multifamily"}
FINANCING = {"cash": "cash", "all cash": "cash", "hard money": "hard_money", "hml": "hard_money",
             "private money": "private", "private": "private", "conventional": "conventional", "bank": "conventional",
             "dscr": "conventional", "mixed": "mixed", "cash or finance": "mixed", "both": "mixed"}
US_STATES = {"AL", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
             "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
             "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "AK", "HI"}
STATE_NAMES = {"texas": "TX", "oklahoma": "OK", "louisiana": "LA", "arkansas": "AR", "new mexico": "NM",
               "florida": "FL", "georgia": "GA", "california": "CA", "arizona": "AZ", "tennessee": "TN",
               "north carolina": "NC", "south carolina": "SC", "ohio": "OH", "michigan": "MI", "illinois": "IL",
               "indiana": "IN", "missouri": "MO", "alabama": "AL", "mississippi": "MS", "kentucky": "KY",
               "colorado": "CO", "nevada": "NV", "utah": "UT", "pennsylvania": "PA", "new york": "NY",
               "new jersey": "NJ", "virginia": "VA", "maryland": "MD", "washington": "WA", "oregon": "OR"}

# ── DFW: the proof-of-concept market ────────────────────────────────────────
DFW_PRIORITY = ("dallas", "tarrant", "collin", "denton", "johnson")
DFW_COUNTIES = DFW_PRIORITY + ("ellis", "kaufman", "rockwall", "parker", "wise", "hunt", "hood", "somervell")
DFW_CITIES = {"dallas", "fort worth", "arlington", "plano", "garland", "irving", "grand prairie", "mckinney",
              "frisco", "mesquite", "denton", "carrollton", "richardson", "lewisville", "allen", "flower mound",
              "north richland hills", "mansfield", "rowlett", "euless", "desoto", "grapevine", "bedford",
              "cedar hill", "wylie", "keller", "coppell", "rockwall", "haltom city", "the colony", "burleson",
              "duncanville", "lancaster", "little elm", "hurst", "southlake", "waxahachie", "cleburne", "weatherford",
              "forney", "midlothian", "prosper", "celina", "anna", "crowley", "saginaw", "watauga", "benbrook",
              "white settlement", "balch springs", "seagoville", "hutchins", "wilmer", "glenn heights",
              "red oak", "sachse", "murphy", "lucas", "farmers branch", "addison", "university park",
              "highland park", "joshua", "alvarado", "keene", "azle", "terrell", "kaufman", "greenville",
              "granbury", "decatur", "corinth", "denison", "princeton", "melissa", "royse city", "heath", "aledo"}
DFW_MARKET_WORDS = ("dfw", "dallas", "fort worth", "ft worth", "ft. worth", "metroplex", "north texas", "tarrant",
                    "collin", "denton", "johnson county")


def dfw_zip(z: str) -> bool:
    """USPS prefixes 750-753 (Dallas area), 754 (Greenville/Hunt), 760-762 (Fort Worth area)."""
    z = (z or "").strip()[:5]
    return len(z) == 5 and z.isdigit() and z[:3] in ("750", "751", "752", "753", "754", "760", "761", "762")


def norm_phone(p: Any) -> Optional[str]:
    d = re.sub(r"\D", "", str(p or ""))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 and d[0] not in "01" else None


EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[A-Za-z]{2,}$")


def norm_email(e: Any) -> Optional[str]:
    e = str(e or "").strip().lower()
    return e if EMAIL_RE.match(e) else None


def _norm_name(s: Any) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower())
    s = re.sub(r"\b(llc|l l c|inc|corp|co|ltd|lp|llp|the|company|group|holdings)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _split(v: Any) -> List[str]:
    return [x.strip() for x in re.split(r"[;,|/\n]+", str(v or "")) if x.strip()]


def _money(v: Any) -> Optional[float]:
    s = str(v or "").strip().lower().replace("$", "").replace(",", "")
    if not s:
        return None
    mult = 1
    if s.endswith("k"):
        mult, s = 1000, s[:-1]
    elif s.endswith("m"):
        mult, s = 1000000, s[:-1]
    try:
        n = float(s) * mult
    except ValueError:
        return None
    return n if n > 0 else None


def _date(v: Any) -> Optional[datetime]:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "")[:19])
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s.split(" ")[0] if fmt.count(" ") == 0 else s, fmt)
        except ValueError:
            continue
    return None


def suggest_mapping(headers: List[str]) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    used = set()
    for h in headers:
        low = re.sub(r"\s+", " ", (h or "").strip().lower())
        key = re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", low))
        hit = None
        for field, (_label, aliases) in FIELDS.items():
            if key == field.replace("_", " ") or key in aliases or low in aliases:
                hit = field
                break
        if hit and hit not in used:
            out[h] = hit
            used.add(hit)
        else:
            out[h] = None
    return out


# ── one row ─────────────────────────────────────────────────────────────────

def normalize(cells: Dict[str, str], mapping: Dict[str, Optional[str]]) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """(normalized, errors, warnings). An error keeps the row out of the commit;
    a warning is shown and the row still goes in."""
    v: Dict[str, str] = {}
    extra: List[str] = []
    for header, cell in cells.items():
        field = mapping.get(header)
        cell = (cell or "").strip()
        if not cell:
            continue
        if field:
            v[field] = cell if field not in v else v[field] + ", " + cell
        else:
            extra.append("%s: %s" % (header, cell))
    errors: List[str] = []
    warnings: List[str] = []
    n: Dict[str, Any] = {}

    contact = v.get("contact_name") or " ".join(x for x in (v.get("first_name"), v.get("last_name")) if x) or None
    company = v.get("company_name") or None
    n["contact_name"], n["company_name"] = contact, company
    et = (v.get("entity_type") or "").lower()
    n["entity_type"] = ("person" if et.startswith(("ind", "person", "pers")) else
                        "company" if et.startswith(("comp", "llc", "bus", "corp", "ent")) else
                        ("company" if company else "person"))
    if not contact and not company:
        errors.append("No name or company.")

    phones = [p for p in (norm_phone(v.get("phone")), norm_phone(v.get("phone2"))) if p]
    if v.get("phone") and not norm_phone(v.get("phone")):
        warnings.append("Phone '%s' is not a US number; left out." % v["phone"][:30])
    n["phone"] = phones[0] if phones else None
    n["phone2"] = phones[1] if len(phones) > 1 else None
    n["email"] = norm_email(v.get("email"))
    if v.get("email") and not n["email"]:
        warnings.append("Email '%s' is not a valid address; left out." % v["email"][:40])
    if not n["phone"] and not n["email"]:
        warnings.append("No usable phone or email - imported, but not contactable until one is added.")

    states = []
    for s in _split(v.get("states")):
        code = s.upper() if s.upper() in US_STATES else STATE_NAMES.get(s.lower())
        if code:
            states.append(code)
        else:
            warnings.append("State '%s' not recognized; left out." % s[:30])
    if {"AK", "HI"} & set(states):
        warnings.append("Alaska/Hawaii listed - outside the lower 48; kept as the file said.")
    n["states"] = sorted(set(states))
    n["counties"] = sorted({re.sub(r"\s+county$", "", c, flags=re.I).strip().title() for c in _split(v.get("counties"))})
    n["cities"] = sorted({c.strip().title() for c in _split(v.get("cities"))})
    zips = []
    for z in _split(v.get("zips")):
        z5 = re.sub(r"\D", "", z)[:5]
        if len(z5) == 5:
            zips.append(z5)
        else:
            warnings.append("ZIP '%s' is not 5 digits; left out." % z[:12])
    n["zips"] = sorted(set(zips))
    n["markets"] = sorted({m.strip() for m in _split(v.get("markets"))})
    ptypes = []
    for t in _split(v.get("property_types")):
        k = PTYPE.get(t.lower().strip())
        if k:
            ptypes.append(k)
        else:
            warnings.append("Property type '%s' not recognized; kept in notes." % t[:30])
            extra.append("property type: %s" % t)
    n["property_types"] = sorted(set(ptypes))
    strats = []
    for t in _split(v.get("strategies")):
        k = STRATEGY.get(t.lower().strip())
        if k:
            strats.append(k)
        else:
            extra.append("strategy: %s" % t)
    n["strategies"] = sorted(set(strats))
    n["min_price"], n["max_price"] = _money(v.get("min_price")), _money(v.get("max_price"))
    for k in ("min_price", "max_price"):
        if v.get(k) and n[k] is None:
            warnings.append("%s '%s' is not a number; left out." % (FIELDS[k][0], v[k][:20]))
    if n["min_price"] and n["max_price"] and n["min_price"] > n["max_price"]:
        errors.append("Minimum price is above maximum price.")
    r = (v.get("rehab_tolerance") or "").lower().strip()
    n["rehab_tolerance"] = REHAB.get(r) if r else None
    if r and not n["rehab_tolerance"]:
        warnings.append("Rehab '%s' not recognized; kept in notes." % r[:20])
        extra.append("rehab: %s" % r)
    f = (v.get("financing") or "").lower().strip()
    n["financing"] = FINANCING.get(f) or (("cash" if f in ("yes", "y", "true") else None) if f else None)
    if f and not n["financing"]:
        extra.append("financing: %s" % f)
    cd = re.sub(r"[^\d]", "", v.get("close_days") or "")
    n["close_days"] = int(cd) if cd and 0 < int(cd) < 366 else None
    pd = re.sub(r"[^\d]", "", v.get("past_deals") or "")
    n["past_deals"] = int(pd) if pd and int(pd) < 100000 else None
    n["pof_status"] = (v.get("pof_status") or "").strip()[:40] or None
    n["purchase_evidence"] = (v.get("purchase_evidence") or "").strip()[:2000] or None
    for k in ("last_contact", "last_response"):
        n[k] = _date(v.get(k))
        if v.get(k) and n[k] is None:
            warnings.append("%s '%s' is not a date; left out." % (FIELDS[k][0], v[k][:20]))
        n[k] = n[k].isoformat() if n[k] else None
    st = (v.get("status") or "").lower().strip()
    n["file_status"] = ("inactive" if st in ("inactive", "no", "n", "false", "dead", "do not use", "dnc", "stopped")
                        else "active" if st else None)
    n["do_not_contact"] = st in ("dnc", "do not contact", "do not use", "unsubscribed", "opted out")
    n["source"] = (v.get("source") or "").strip()[:120] or None
    notes = [v.get("notes")] if v.get("notes") else []
    notes += extra
    n["notes"] = "\n".join(notes)[:4000] or None
    return n, errors, warnings


# ── DFW and status ──────────────────────────────────────────────────────────

def territory_dfw(box_like: Dict[str, Any]) -> Dict[str, Any]:
    """Does what the buyer SAYS they buy include DFW? Returns {dfw, priority, why}."""
    why: List[str] = []
    counties = [c.lower() for c in box_like.get("counties") or []]
    cities = [c.lower() for c in box_like.get("cities") or []]
    zips = box_like.get("zips") or []
    markets = " ".join(box_like.get("markets") or []).lower()
    pri = [c for c in counties if c in DFW_PRIORITY]
    other = [c for c in counties if c in DFW_COUNTIES and c not in DFW_PRIORITY]
    if pri:
        why.append("Buys in %s County" % ", ".join(c.title() for c in pri))
    if other:
        why.append("Buys in %s County" % ", ".join(c.title() for c in other))
    dc = [c for c in cities if c in DFW_CITIES]
    if dc:
        why.append("Buys in %s" % ", ".join(c.title() for c in dc[:5]))
    dz = [z for z in zips if dfw_zip(z)]
    if dz:
        why.append("%d DFW ZIP code%s" % (len(dz), "" if len(dz) == 1 else "s"))
    if any(w in markets for w in DFW_MARKET_WORDS):
        why.append("Market listed as DFW / Dallas / Fort Worth")
    statewide = "TX" in (box_like.get("states") or []) and not (counties or cities or zips or markets)
    if statewide:
        why.append("Buys statewide in Texas (no narrower area given)")
    dfw = bool(why)
    return {"dfw": dfw, "priority": bool(pri or dc or dz or any(w in markets for w in DFW_MARKET_WORDS)),
            "statewide_only": statewide and len(why) == 1, "why": why}


def buyer_status(b, activity: Optional[Dict[str, Any]], tier: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Imported -> Contactable -> Responded -> Verified active -> Qualified, or Inactive.
    Each step is a fact; the file's own claims never move a buyer past Contactable."""
    a = activity or {}
    from_file = (b.source or "").startswith(("import:", "csv:", "network:"))
    contactable = bool((b.email or b.phone) and not b.do_not_contact and b.is_active)
    responded = int(a.get("responded") or 0) > 0
    verified = bool(tier and tier.get("tier") == "VERIFIED_ACTIVE_BUYER")
    qualified = verified and bool(b.cash_verified or (b.proof_of_funds_on_file and (
        b.proof_of_funds_expires is None or b.proof_of_funds_expires >= datetime.utcnow().date())))
    if not b.is_active or b.do_not_contact or (b.file_status == "inactive"):
        key, label = "inactive", "Inactive"
    elif qualified:
        key, label = "qualified", "Qualified (verified active + funds checked by your team)"
    elif verified:
        key, label = "verified_active", "Verified active"
    elif responded:
        key, label = "responded", "Responded"
    elif contactable:
        key, label = "contactable", "Contactable"
    else:
        key, label = "imported", "Imported - no usable phone or email"
    return {"status": key, "label": label, "from_file": from_file,
            "flags": {"contactable": contactable, "responded": responded, "verified_active": verified,
                      "qualified": qualified}}


# ── batches ─────────────────────────────────────────────────────────────────

def _j(x) -> str:
    return json.dumps(x, default=str)


def _l(x, d=None):
    try:
        return json.loads(x) if x else d
    except (TypeError, ValueError):
        return d


def create_batch(db, org_id: str, user, content: bytes, filename: str, list_name: Optional[str], is_test: bool):
    from app.models.wholesale_buyer_import_models import WholesaleBuyerImportBatch, WholesaleBuyerImportRow
    from app.services.intake.engine import IntakeError, read_table
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="That file is over 25 MB. Split it and import in parts.")
    name = (filename or "buyers.csv").lower()
    if not name.endswith((".csv", ".tsv", ".txt", ".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Upload a CSV or an Excel (.xlsx) file.")
    try:
        headers, rows, stats = read_table(content, filename or "buyers.csv")
    except IntakeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not rows:
        raise HTTPException(status_code=400, detail="The file has a header row but no buyers.")
    if len(rows) > MAX_ROWS:
        raise HTTPException(status_code=400, detail="This file has %d rows; import at most %d at a time."
                                                    % (len(rows), MAX_ROWS))
    mapping = suggest_mapping(headers)
    batch = WholesaleBuyerImportBatch(
        organization_id=org_id, filename=(filename or "")[:200],
        file_format="xlsx" if name.endswith((".xlsx", ".xlsm")) else ("tsv" if name.endswith(".tsv") else "csv"),
        list_name=(list_name or "").strip()[:120] or None, status="mapping", headers=_j(headers),
        mapping=_j(mapping), row_count=len(rows), file_stats=_j(stats), is_test=bool(is_test),
        created_by_id=getattr(user, "id", None))
    db.add(batch)
    db.flush()
    for i, cells in enumerate(rows):
        raw = {h: (cells[j] if j < len(cells) else "") for j, h in enumerate(headers)}
        db.add(WholesaleBuyerImportRow(organization_id=org_id, batch_id=batch.id, row_number=i + 2, raw=_j(raw)))
    db.flush()
    return batch


def get_batch(db, org_id: str, batch_id: str):
    from app.models.wholesale_buyer_import_models import WholesaleBuyerImportBatch
    b = (db.query(WholesaleBuyerImportBatch)
         .filter(WholesaleBuyerImportBatch.id == batch_id, WholesaleBuyerImportBatch.organization_id == org_id).first())
    if b is None:
        raise HTTPException(status_code=404, detail="Import not found.")
    return b


def _rows(db, batch, state: Optional[str] = None):
    from app.models.wholesale_buyer_import_models import WholesaleBuyerImportRow
    q = db.query(WholesaleBuyerImportRow).filter(WholesaleBuyerImportRow.batch_id == batch.id,
                                                 WholesaleBuyerImportRow.organization_id == batch.organization_id)
    if state:
        q = q.filter(WholesaleBuyerImportRow.state == state)
    return q.order_by(WholesaleBuyerImportRow.row_number.asc())


def set_mapping(db, batch, mapping: Dict[str, Optional[str]]):
    if batch.status not in ("mapping", "analyzed"):
        raise HTTPException(status_code=409, detail="This import was already committed; start a new one to remap.")
    headers = _l(batch.headers, [])
    clean: Dict[str, Optional[str]] = {}
    seen = set()
    for h in headers:
        f = mapping.get(h)
        if f in (None, "", "ignore"):
            clean[h] = None
            continue
        if f not in FIELDS:
            raise HTTPException(status_code=400, detail="'%s' is not a buyer field." % f)
        if f in seen and f not in LIST_FIELDS and f not in ("notes", "phone2"):
            raise HTTPException(status_code=400, detail="Two columns are mapped to %s." % FIELDS[f][0])
        seen.add(f)
        clean[h] = f
    batch.mapping = _j(clean)
    batch.status = "mapping"
    return clean


def _existing_index(db, org_id: str, is_test: bool):
    from app.models.wholesale_models import WholesaleBuyer
    by_email: Dict[str, Any] = {}
    by_phone: Dict[str, Any] = {}
    by_name: Dict[Tuple[str, str], Any] = {}
    for b in db.query(WholesaleBuyer).filter(WholesaleBuyer.organization_id == org_id).all():
        if bool(b.is_test) != bool(is_test):
            continue
        if b.email and norm_email(b.email):
            by_email.setdefault(norm_email(b.email), b)
        if norm_phone(b.phone):
            by_phone.setdefault(norm_phone(b.phone), b)
        key = (_norm_name(b.contact_name), _norm_name(b.company_name))
        if key != ("", ""):
            by_name.setdefault(key, b)
    return by_email, by_phone, by_name


def _bname(b) -> str:
    return b.company_name or b.contact_name or "(unnamed buyer)"


def analyze(db, batch) -> Dict[str, Any]:
    """Validate every row and check it against the buyer list AND the rest of the file."""
    if batch.status not in ("mapping", "analyzed"):
        raise HTTPException(status_code=409, detail="This import was already committed.")
    mapping = _l(batch.mapping, {})
    if not any(mapping.values()):
        raise HTTPException(status_code=400, detail="Map at least one column first.")
    by_email, by_phone, by_name = _existing_index(db, batch.organization_id, batch.is_test)
    seen_email: Dict[str, int] = {}
    seen_phone: Dict[str, int] = {}
    for r in _rows(db, batch).all():
        if r.state in ("created", "updated"):
            continue
        n, errors, warnings = normalize(_l(r.raw, {}), mapping)
        r.normalized, r.errors, r.warnings = _j(n), _j(errors), _j(warnings)
        if errors:
            r.state, r.match = "error", None
            continue
        e, p = n.get("email"), n.get("phone")
        dup_of = (seen_email.get(e) if e else None) or (seen_phone.get(p) if p else None)
        if dup_of:
            r.state = "duplicate_in_file"
            r.match = _j({"kind": "in_file", "reasons": ["Same %s as row %d of this file" % (
                "email" if e and seen_email.get(e) else "phone", dup_of)]})
            continue
        if e:
            seen_email[e] = r.row_number
        if p:
            seen_phone[p] = r.row_number
        cands: Dict[str, Dict[str, Any]] = {}

        def add(b, reason):
            c = cands.setdefault(b.id, {"buyer_id": b.id, "name": _bname(b), "reasons": []})
            c["reasons"].append(reason)
        if e and e in by_email:
            add(by_email[e], "Same email (%s)" % e)
        for ph in (p, n.get("phone2")):
            if ph and ph in by_phone:
                add(by_phone[ph], "Same phone (%s-%s-%s)" % (ph[:3], ph[3:6], ph[6:]))
        strong = dict(cands)
        nk = (_norm_name(n.get("contact_name")), _norm_name(n.get("company_name")))
        if not strong and nk != ("", "") and nk in by_name and nk[0] and nk[1]:
            add(by_name[nk], "Same contact name and company, but no shared phone or email")
        if not cands:
            r.state, r.match = "new", None
        elif len(strong) == 1 and len(cands) == 1:
            r.state = "update"
            r.match = _j({"kind": "same_buyer", "candidates": list(cands.values())})
        else:
            r.state = "conflict"
            r.match = _j({"kind": "uncertain", "candidates": list(cands.values()),
                          "why": ("Email and phone point to different buyers." if len(strong) > 1
                                  else "Same name and company only - could be the same person or not.")})
        if r.state == "conflict" and r.decision:
            pass                                    # a person's earlier decision still stands
    counts = _count(db, batch)
    batch.status = "analyzed"
    return counts


def _count(db, batch) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in _rows(db, batch).all():
        out[r.state] = out.get(r.state, 0) + 1
    batch.counts = _j(out)
    return out


def decide(db, batch, row_id: str, user, decision: str, buyer_id: Optional[str], fix: Optional[Dict[str, str]]):
    from app.models.wholesale_buyer_import_models import WholesaleBuyerImportRow
    from app.models.wholesale_models import WholesaleBuyer
    r = (db.query(WholesaleBuyerImportRow)
         .filter(WholesaleBuyerImportRow.id == row_id, WholesaleBuyerImportRow.batch_id == batch.id).first())
    if r is None:
        raise HTTPException(status_code=404, detail="Row not found.")
    if r.state in ("created", "updated", "rolled_back"):
        raise HTTPException(status_code=409, detail="This row was already applied.")
    if fix:
        raw = _l(r.raw, {})
        for h, val in fix.items():
            if h in raw:
                raw[h] = str(val)[:500]
        r.raw = _j(raw)
        n, errors, warnings = normalize(raw, _l(batch.mapping, {}))
        r.normalized, r.errors, r.warnings = _j(n), _j(errors), _j(warnings)
        r.state = "error" if errors else ("new" if r.state == "error" else r.state)
    if decision not in (None, "", "create", "merge", "skip"):
        raise HTTPException(status_code=400, detail="decision must be create, merge or skip.")
    if decision == "merge":
        target = buyer_id or ((_l(r.match, {}) or {}).get("candidates") or [{}])[0].get("buyer_id")
        b = (db.query(WholesaleBuyer).filter(WholesaleBuyer.id == target,
                                             WholesaleBuyer.organization_id == batch.organization_id).first()
             if target else None)
        if b is None:
            raise HTTPException(status_code=400, detail="Pick the buyer to merge into.")
        r.decision_buyer_id = b.id
    if decision:
        r.decision = decision
        r.decided_by_id = getattr(user, "id", None)
    _count(db, batch)
    return r


FILL_FIELDS = (("contact_name", "contact_name"), ("company_name", "company_name"), ("phone", "phone"),
               ("email", "email"), ("financing_preference", "financing"), ("purchase_evidence", "purchase_evidence"),
               ("file_pof_status", "pof_status"), ("typical_close_days", "close_days"))


def _box_fields(n: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: n.get(k) for k in LIST_FIELDS if n.get(k)}
    for k in ("min_price", "max_price", "rehab_tolerance"):
        if n.get(k) is not None:
            out[k] = n[k]
    return out


def commit(db, batch, user) -> Dict[str, Any]:
    """Apply every reviewed row. Safe to call again: applied rows are skipped, so
    this is also how failed rows are retried after they are fixed."""
    from app.models.wholesale_models import WholesaleBuyer, WholesaleBuyBox
    from app.routers.wholesale_buyers_router import _apply_box
    if batch.status not in ("analyzed", "partially_committed", "committed"):
        raise HTTPException(status_code=409, detail="Analyze the file before committing it.")
    done = {"created": 0, "updated": 0, "skipped": 0, "waiting": 0, "errors": 0}
    source = "import:%s" % (batch.list_name or batch.filename or "buyer file")[:100]
    for r in _rows(db, batch).all():
        if r.state in ("created", "updated", "rolled_back"):
            continue
        if r.state == "skipped" and r.decision not in ("create", "merge"):
            continue                                    # skipped stays skipped unless a person says otherwise
        n = _l(r.normalized, {}) or {}
        if r.state == "error":
            done["errors"] += 1
            continue
        if r.decision == "skip" or (r.state == "duplicate_in_file" and r.decision != "create"):
            r.state = "skipped"
            done["skipped"] += 1
            continue
        if r.state == "conflict" and r.decision not in ("create", "merge"):
            done["waiting"] += 1
            continue
        target_id = None
        if r.state == "update" and r.decision != "create":
            target_id = ((_l(r.match, {}) or {}).get("candidates") or [{}])[0].get("buyer_id")
        if r.decision == "merge":
            target_id = r.decision_buyer_id
        if target_id:
            b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == target_id,
                                                WholesaleBuyer.organization_id == batch.organization_id).first()
            if b is None:
                r.state = "error"
                r.errors = _j(["The buyer this row matched no longer exists. Analyze again."])
                done["errors"] += 1
                continue
            before = {}
            for col, key in FILL_FIELDS:                # fill blanks only - never overwrite what is there
                val = n.get(key)
                if val not in (None, "") and getattr(b, col, None) in (None, "", 0):
                    before[col] = getattr(b, col, None)
                    setattr(b, col, val)
            if n.get("last_contact") and not b.file_last_contact_at:
                before["file_last_contact_at"] = None
                b.file_last_contact_at = _date(n["last_contact"])
            if n.get("last_response") and not b.file_last_response_at:
                before["file_last_response_at"] = None
                b.file_last_response_at = _date(n["last_response"])
            r.before = _j(before)
            r.buyer_id, r.created_buyer, r.state = b.id, False, "updated"
            done["updated"] += 1
        else:
            b = WholesaleBuyer(
                organization_id=batch.organization_id, entity_type=n.get("entity_type") or "company",
                company_name=n.get("company_name"), contact_name=n.get("contact_name"),
                phone=n.get("phone"), email=n.get("email"),
                preferred_channel="email" if n.get("email") else "phone",
                typical_close_days=n.get("close_days"), past_deals_count=n.get("past_deals") or 0,
                source=source, source_detail=(n.get("source") or "")[:250] or None,
                notes=n.get("notes"), is_active=n.get("file_status") != "inactive",
                do_not_contact=bool(n.get("do_not_contact")),
                do_not_contact_reason="Marked do-not-contact in the imported file" if n.get("do_not_contact") else None,
                is_test=bool(batch.is_test), financing_preference=n.get("financing"),
                purchase_evidence=n.get("purchase_evidence"), file_pof_status=n.get("pof_status"),
                file_status=n.get("file_status"), file_last_contact_at=_date(n.get("last_contact")),
                file_last_response_at=_date(n.get("last_response")))
            db.add(b)
            db.flush()
            r.buyer_id, r.created_buyer, r.state = b.id, True, "created"
            done["created"] += 1
        box = _box_fields(n)
        if box:
            bx = WholesaleBuyBox(organization_id=batch.organization_id, buyer_id=b.id,
                                 label="Imported: %s" % (batch.list_name or batch.filename or "file")[:60])
            _apply_box(bx, box)
            db.add(bx)
            db.flush()
            r.box_id = bx.id
        r.applied_at = datetime.utcnow()
    _count(db, batch)
    left = done["errors"] + done["waiting"]
    batch.status = "partially_committed" if left else "committed"
    batch.committed_at = datetime.utcnow()
    batch.committed_by_id = getattr(user, "id", None)
    return {**done, "status": batch.status}


def rollback(db, batch, user) -> Dict[str, Any]:
    from app.models.wholesale_models import (WholesaleBuyer, WholesaleBuyBox, WholesaleBuyerMatch,
                                             WholesaleBuyerOutreach, WholesaleDeal)
    if batch.status not in ("committed", "partially_committed"):
        raise HTTPException(status_code=409, detail="Only a committed import can be rolled back.")
    report = {"removed": [], "deactivated": [], "restored": [], "boxes_removed": 0}
    for r in _rows(db, batch).all():
        if r.state not in ("created", "updated"):
            continue
        if r.box_id:
            n = db.query(WholesaleBuyBox).filter(WholesaleBuyBox.id == r.box_id,
                                                 WholesaleBuyBox.organization_id == batch.organization_id).delete()
            report["boxes_removed"] += n
        b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == r.buyer_id,
                                            WholesaleBuyer.organization_id == batch.organization_id).first()
        if b is None:
            r.state = "rolled_back"
            continue
        if r.created_buyer:
            used = (db.query(WholesaleBuyerOutreach.id).filter(WholesaleBuyerOutreach.buyer_id == b.id).first()
                    or db.query(WholesaleDeal.id).filter(WholesaleDeal.assigned_buyer_id == b.id).first())
            if used:
                b.is_active = False
                b.do_not_contact_reason = b.do_not_contact_reason or None
                report["deactivated"].append({"row": r.row_number, "buyer": _bname(b),
                                              "why": "has deal history since the import, so it was kept and turned off"})
            else:
                db.query(WholesaleBuyerMatch).filter(WholesaleBuyerMatch.buyer_id == b.id).delete()
                report["removed"].append({"row": r.row_number, "buyer": _bname(b)})
                db.delete(b)
        else:
            for col, val in (_l(r.before, {}) or {}).items():
                setattr(b, col, val)
            report["restored"].append({"row": r.row_number, "buyer": _bname(b)})
        r.state = "rolled_back"
    batch.status = "rolled_back"
    batch.rolled_back_at = datetime.utcnow()
    batch.rolled_back_by_id = getattr(user, "id", None)
    _count(db, batch)
    return report


def batch_json(batch) -> Dict[str, Any]:
    from app.utils.time_fmt import iso_utc
    return {"id": batch.id, "filename": batch.filename, "file_format": batch.file_format,
            "list_name": batch.list_name, "status": batch.status, "row_count": batch.row_count,
            "headers": _l(batch.headers, []), "mapping": _l(batch.mapping, {}), "counts": _l(batch.counts, {}),
            "file_stats": _l(batch.file_stats, {}), "is_test": bool(batch.is_test),
            "created_at": iso_utc(batch.created_at), "committed_at": iso_utc(batch.committed_at),
            "rolled_back_at": iso_utc(batch.rolled_back_at),
            "fields": {k: v[0] for k, v in FIELDS.items()}}


def row_json(r) -> Dict[str, Any]:
    return {"id": r.id, "row": r.row_number, "state": r.state, "raw": _l(r.raw, {}),
            "normalized": _l(r.normalized, {}), "errors": _l(r.errors, []), "warnings": _l(r.warnings, []),
            "match": _l(r.match, None), "decision": r.decision, "decision_buyer_id": r.decision_buyer_id,
            "buyer_id": r.buyer_id, "created_buyer": bool(r.created_buyer)}


def error_report_csv(db, batch) -> str:
    buf = io.StringIO()
    headers = _l(batch.headers, [])
    w = csv.writer(buf)
    w.writerow(["row", "state", "problems"] + headers)
    for r in _rows(db, batch).all():
        if r.state not in ("error", "conflict", "duplicate_in_file", "skipped"):
            continue
        probs = (_l(r.errors, []) or []) + [x for c in ((_l(r.match, {}) or {}).get("candidates") or [])
                                            for x in c.get("reasons", [])]
        probs += (_l(r.match, {}) or {}).get("reasons") or []
        raw = _l(r.raw, {})
        w.writerow([r.row_number, r.state, "; ".join(probs)] + [raw.get(h, "") for h in headers])
    return buf.getvalue()


# ── the network, and the DFW segment ───────────────────────────────────────

def network(db, org_id: str, *, segment: Optional[str] = None, status: Optional[str] = None,
            q: Optional[str] = None, state: Optional[str] = None, county: Optional[str] = None,
            include_test: bool = False, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    from app.models.wholesale_models import WholesaleBuyer
    from app.routers.wholesale_buyers_router import _buyer_activity
    from app.services import wholesale_buyer_intel as BI
    rows = db.query(WholesaleBuyer).filter(WholesaleBuyer.organization_id == org_id).all()
    rows = [b for b in rows if include_test or not b.is_test]
    from app.models.wholesale_models import WholesaleBuyBox
    boxes_by: Dict[str, List[Any]] = {}
    for x in db.query(WholesaleBuyBox).filter(WholesaleBuyBox.organization_id == org_id,
                                              WholesaleBuyBox.is_active.is_(True)).all():
        boxes_by.setdefault(x.buyer_id, []).append(x)
    activity = _buyer_activity(db, org_id, [b.id for b in rows])
    hist = BI.history_rows(db, org_id, [b.id for b in rows])
    items = []
    counts = {"total": 0, "dfw": 0, "dfw_priority": 0, "by_status": {}}
    for b in rows:
        boxes = boxes_by.get(b.id, [])
        terr = {"states": [], "counties": [], "cities": [], "zips": [], "markets": []}
        for x in boxes:
            for k in terr:
                terr[k] += _l(getattr(x, k), []) or []
        terr["states"] = sorted({str(v).upper() for v in terr["states"]})      # boxes store lowercase
        terr["counties"] = sorted({str(v).title() for v in terr["counties"]})
        terr["cities"] = sorted({str(v).title() for v in terr["cities"]})
        t = territory_dfw(terr)
        acted_dfw = [h for h in hist.get(b.id) or [] if h.get("zip") and dfw_zip(h["zip"])
                     and h.get("action") in ("offer", "chosen", "closed")]
        ce = BI.county_evidence(b)
        if ce and ce.get("county", "").lower() in DFW_COUNTIES and ce.get("recent_deeds"):
            acted_dfw.append({"action": "deed", "zip": None})
        dfw_evidence = []
        if any(h["action"] in ("offer", "chosen", "closed") for h in acted_dfw):
            dfw_evidence.append("Offered on, was chosen for or closed a DFW deal with you")
        if ce and ce.get("county", "").lower() in DFW_COUNTIES and ce.get("recent_deeds"):
            dfw_evidence.append("%s County records: recent deeds to them" % ce["county"])
        tier = BI.tier(b, activity.get(b.id))
        st = buyer_status(b, activity.get(b.id), tier)
        in_dfw = t["dfw"] or bool(dfw_evidence)
        counts["total"] += 1
        counts["by_status"][st["status"]] = counts["by_status"].get(st["status"], 0) + 1
        if in_dfw:
            counts["dfw"] += 1
        if in_dfw and (t["priority"] or dfw_evidence):
            counts["dfw_priority"] += 1
        if segment == "dfw" and not in_dfw:
            continue
        if status and st["status"] != status:
            continue
        if state and state.upper() not in [s.upper() for s in terr["states"]]:
            continue
        if county and county.lower() not in [c.lower() for c in terr["counties"]]:
            continue
        if q:
            hay = " ".join(str(x or "") for x in (b.company_name, b.contact_name, b.email, b.phone)).lower()
            if q.lower() not in hay:
                continue
        items.append({
            "buyer_id": b.id, "name": _bname(b), "contact_name": b.contact_name, "company_name": b.company_name,
            "has_phone": bool(b.phone), "has_email": bool(b.email), "status": st, "tier": tier["label"],
            "dfw": {"in_segment": in_dfw, "priority": bool(t["priority"] or dfw_evidence),
                    "territory": t["why"], "evidence": dfw_evidence, "statewide_only": t["statewide_only"]},
            "territory": {k: v[:10] for k, v in terr.items()},
            "financing": b.financing_preference, "close_days": b.typical_close_days,
            "from_file": {"pof": b.file_pof_status, "purchases": b.purchase_evidence, "status": b.file_status,
                          "last_contact": b.file_last_contact_at.isoformat() if b.file_last_contact_at else None,
                          "last_response": b.file_last_response_at.isoformat() if b.file_last_response_at else None,
                          "past_deals_claimed": b.past_deals_count},
            "activity": activity.get(b.id), "source": b.source, "is_test": bool(b.is_test),
        })
    order = {"qualified": 0, "verified_active": 1, "responded": 2, "contactable": 3, "imported": 4, "inactive": 5}
    items.sort(key=lambda x: (not x["dfw"]["priority"], order.get(x["status"]["status"], 9), x["name"].lower()))
    return {"counts": counts, "total_matching": len(items), "buyers": items[offset:offset + limit],
            "dfw_counties": [c.title() for c in DFW_COUNTIES],
            "note": "Territory is what the buyer (or your file) says they buy; evidence is what happened. "
                    "Nothing from a file makes a buyer verified active."}
