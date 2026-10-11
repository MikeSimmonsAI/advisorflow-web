"""Get phones & emails - one paid lookup for every list that holds people.

Cash buyers, funding partners and property owners (sellers) all go through
here, with the same screen and the same rules:

  1. A person ticks the records they want. Nothing is looked up "first N".
  2. /estimate says, record by record, what will happen and the most it can
     cost. Records with no street address can be given one right there.
  3. /run looks up only records the person sent, in small batches, and only
     when the approved cost covers the batch. The organization's paid-lookup
     caps (Wholesale Settings) apply to all of it.

The lookup is Tracerfy's instant search by street address: charged only when
it finds someone. Every attempt leaves a row in wholesale_enrichment_requests,
so a record is never paid for twice unless the person asks again.

NEVER: overwrite a phone or email a person entered; fill in a different
person found at a person's address; hand on a Do Not Call number as the phone
to use (those are listed in the notes for a person to judge).
"""

import json
import logging
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from app.models.wholesale_models import (ACTOR_USER, WholesaleBuyer, WholesaleEnrichmentRequest,
                                         WholesaleFundingPartner, WholesaleProperty,
                                         WholesaleSellerProfile)
from app.services import wholesale_enrichment as WE

log = logging.getLogger(__name__)

PROVIDER_KEY = "tracerfy"
MAX_PER_CALL = 25
NO_CONTACT_LINE = "No phone or email yet - look one up or mail them before sending deals."

_MAIL = re.compile(r"Mailing address \([^)]*\):\s*(.+)")
_TAIL = re.compile(r"^(?P<state>[A-Z]{2})(?:\s+(?P<zip>\d{5})(?:-?\d{4})?)?$")
_COMPANY = re.compile(r"\b(LLC|L\.L\.C|INC|CORP|CO|COMPANY|LP|LTD|LLP|TRUST|BANK|CAPITAL|FUND|FUNDING|"
                      r"LENDING|LENDERS?|FINANCIAL|HOLDINGS|PARTNERS|GROUP|INVESTMENTS?|PROPERTIES|"
                      r"REALTY|VENTURES|ENTERPRISES|MORTGAGE|CREDIT)\b", re.I)


def _provider():
    return WE.PROVIDERS[PROVIDER_KEY]


# Swappable in tests; production always uses the registered Tracerfy provider.
provider_factory: Callable[[], Any] = _provider


def configured() -> bool:
    return provider_factory().is_configured()


def cost_per_find() -> int:
    return getattr(provider_factory(), "cost_per_find_cents", 10)


def parse_address(text: str) -> Optional[Dict[str, str]]:
    """'4736 Trail Lake Dr, Fort Worth, TX 76133' -> parts, or None."""
    parts = [p.strip() for p in (text or "").strip().split(",") if p.strip()]
    if len(parts) < 3:
        return None
    t = _TAIL.match(parts[-1].upper())
    if not t:
        return None
    return {"street": ", ".join(parts[:-2]), "city": parts[-2],
            "state": t.group("state"), "zip": t.group("zip") or ""}


def _note_address(notes: Optional[str]) -> Optional[Dict[str, str]]:
    found = _MAIL.findall(notes or "")
    for text in reversed(found):          # the newest line wins
        a = parse_address(text)
        if a:
            return a
    return None


def _note(existing: Optional[str], line: str) -> str:
    return ((existing or "").rstrip() + "\n" + line).strip()


def _fmt_phone(n: str) -> str:
    d = "".join(ch for ch in str(n) if ch.isdigit())[-10:]
    return "(%s) %s-%s" % (d[:3], d[3:6], d[6:]) if len(d) == 10 else str(n)


# ── The lists ──────────────────────────────────────────────────────────────

class Kind:
    key = ""
    label = ""
    model = None

    def name(self, r) -> str: ...
    def is_company(self, r) -> bool: ...
    def address(self, db, r) -> Optional[Dict[str, str]]: ...
    def contact(self, db, r) -> Dict[str, Any]: ...      # {"phone", "email"}
    def blocked(self, r) -> Optional[str]:
        return None

    def save_address(self, db, r, text: str) -> None:
        r.notes = _note(re.sub(r"(?m)^Mailing address \(entered\):.*\n?", "", r.notes or ""),
                        "Mailing address (entered): %s" % text.strip())


class BuyerKind(Kind):
    key, label, model = "buyer", "cash buyer", WholesaleBuyer

    def name(self, r):
        return r.company_name or r.contact_name or ""

    def is_company(self, r):
        return r.entity_type == "company"

    def address(self, db, r):
        return _note_address(r.notes)

    def contact(self, db, r):
        return {"phone": r.phone, "email": r.email}

    def blocked(self, r):
        return "marked do-not-contact" if r.do_not_contact else None


class PartnerKind(Kind):
    key, label, model = "funding_partner", "funding partner", WholesaleFundingPartner

    def name(self, r):
        return r.name or ""

    def is_company(self, r):
        return bool(_COMPANY.search(r.name or ""))

    def address(self, db, r):
        return _note_address(r.notes)

    def contact(self, db, r):
        return {"phone": r.phone, "email": r.email}


class PropertyKind(Kind):
    """Sellers. The lookup runs through the same per-property path as the
    Properties screen always has, so the seller record, the deal stage and
    the history are written exactly as before."""
    key, label, model = "property", "property owner", WholesaleProperty

    def name(self, r):
        return r.owner_name or r.street_address or ""

    def is_company(self, r):
        return r.ownership_type == "llc"

    def address(self, db, r):
        if r.street_address and r.city and r.state:
            return {"street": r.street_address, "city": r.city, "state": r.state,
                    "zip": r.zip_code or ""}
        return None

    def contact(self, db, r):
        from app.models.models import Lead
        prof = (db.query(WholesaleSellerProfile)
                .filter(WholesaleSellerProfile.property_id == r.id,
                        WholesaleSellerProfile.organization_id == r.organization_id).first())
        lead = db.query(Lead).filter(Lead.id == prof.lead_id).first() if prof and prof.lead_id else None
        return {"phone": getattr(lead, "phone", None), "email": getattr(lead, "email", None)}

    def save_address(self, db, r, text):
        a = parse_address(text)
        r.street_address, r.city, r.state = a["street"], a["city"], a["state"]
        r.zip_code = a["zip"] or r.zip_code


KINDS = {k.key: k for k in (BuyerKind(), PartnerKind(), PropertyKind())}


def kind(key: str) -> Kind:
    if key not in KINDS:
        raise ValueError("Unknown list: %s" % key)
    return KINDS[key]


def get(db, org_id: str, k: Kind, rid: str):
    return (db.query(k.model).filter(k.model.id == rid, k.model.organization_id == org_id).first())


def _tried(db, org_id: str, k: Kind, rid: str) -> bool:
    q = (db.query(WholesaleEnrichmentRequest.id)
         .filter(WholesaleEnrichmentRequest.organization_id == org_id,
                 WholesaleEnrichmentRequest.provider == PROVIDER_KEY,
                 WholesaleEnrichmentRequest.status.in_((WE.STATUS_SUCCEEDED, WE.STATUS_NO_MATCH))))
    if k.key == "property":
        q = q.filter(WholesaleEnrichmentRequest.property_id == rid)
    else:
        q = q.filter(WholesaleEnrichmentRequest.property_id.is_(None),
                     WholesaleEnrichmentRequest.inputs.like('%%"%s_id": "%s"%%' % (k.key, rid)))
    return q.first() is not None


# ── Estimate ───────────────────────────────────────────────────────────────

READY, HAS_BOTH, LOOKED_UP, NO_ADDRESS, BLOCKED, NOT_FOUND = (
    "ready", "has_both", "looked_up", "no_address", "blocked", "not_found")


def plan(db, org_id: str, key: str, ids: List[str], *, again: bool = False) -> Dict[str, Any]:
    """Record by record: what a lookup would do, and the most it can cost."""
    k = kind(key)
    rows = []
    for rid in list(dict.fromkeys(ids))[:1000]:
        r = get(db, org_id, k, rid)
        if r is None:
            rows.append({"id": rid, "name": None, "status": NOT_FOUND})
            continue
        c = k.contact(db, r)
        a = k.address(db, r)
        row = {"id": rid, "name": k.name(r), "phone": c.get("phone"), "email": c.get("email"),
               "address": ("%s, %s, %s %s" % (a["street"], a["city"], a["state"], a["zip"])).strip()
               if a else None}
        why = k.blocked(r)
        if why:
            row.update(status=BLOCKED, why=why)
        elif c.get("phone") and c.get("email"):
            row["status"] = HAS_BOTH
        elif a is None:
            row["status"] = NO_ADDRESS
        elif not again and _tried(db, org_id, k, rid):
            row["status"] = LOOKED_UP
        else:
            row["status"] = READY
        rows.append(row)
    ready = [r["id"] for r in rows if r["status"] == READY]
    each = cost_per_find()
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"kind": key, "label": k.label, "provider": PROVIDER_KEY, "provider_label": "Tracerfy",
            "configured": configured(), "missing": [] if configured() else ["TRACERFY_API_TOKEN"],
            "cost_per_find_cents": each, "misses_free": True, "batch_size": 10,
            "rows": rows, "counts": counts, "ready_ids": ready, "count": len(ready),
            "max_cost_cents": len(ready) * each}


class _NoPerRunLimit:
    """Settings minus the per-run record limit: the whole selection is checked
    against the daily / monthly caps, each batch against the per-run limit."""

    def __init__(self, settings: Any):
        self._s = settings

    def __getattr__(self, name):
        if name == "enrichment_max_records_per_run":
            return None
        return getattr(self._s, name)


def cap_refusal(db, org_id: str, settings: Any, n: int, *, whole_list: bool = False) -> Optional[str]:
    return WE.admit(db, org_id, provider_factory(), _NoPerRunLimit(settings) if whole_list else settings,
                    requested=n)


# ── Run ────────────────────────────────────────────────────────────────────

def _apply(k: Kind, r, res: WE.EnrichmentResult, when: datetime) -> Dict[str, Any]:
    """Buyers and funding partners: write what was found, never over a person."""
    ev = res.match_evidence or {}
    found = res.owner_name
    dnc = ev.get("dnc_numbers") or []
    stamp = "Phone/email lookup (Tracerfy, %s)" % when.strftime("%Y-%m-%d")
    contact_attr = "contact_name" if k.key == "buyer" else "contact_person"
    if not res.found_anything:
        r.notes = _note(r.notes, "%s: nothing usable found at the mailing address.%s" % (
            stamp, " Do Not Call numbers only: %s." % ", ".join(_fmt_phone(n) for n in dnc) if dnc else ""))
        return {}
    if ev.get("deceased"):
        r.notes = _note(r.notes, "%s: the person found (%s) is listed as deceased - nothing filled in."
                        % (stamp, found or "unnamed"))
        return {}
    if not k.is_company(r) and ev.get("name_match") == "none":
        r.notes = _note(r.notes, "%s: found %s at that address, not %s - nothing filled in."
                        % (stamp, found or "someone else", k.name(r)))
        return {}
    changed: Dict[str, Any] = {}
    if res.phones and not r.phone:
        r.phone = _fmt_phone(res.phones[0].number)
        changed["phone"] = r.phone
    if res.emails and not r.email:
        r.email = str(res.emails[0])
        changed["email"] = r.email
    if k.is_company(r) and found and not getattr(r, contact_attr):
        setattr(r, contact_attr, found.title())
        changed[contact_attr] = getattr(r, contact_attr)
    if k.key == "buyer":
        if changed.get("phone"):
            r.preferred_channel = "phone"
        elif changed.get("email") and not r.phone:
            r.preferred_channel = "email"
    lines = ["%s: found %s%s." % (stamp, found.title() if found else "a match",
                                  " (person at the company's address)" if k.is_company(r) else "")]
    if res.phones:
        lines.append("Phones: " + ", ".join("%s%s" % (_fmt_phone(p.number), " %s" % p.phone_type
                                                     if p.phone_type else "") for p in res.phones[:5]))
    if dnc:
        lines.append("On the Do Not Call list (not filled in - don't cold call or text): "
                     + ", ".join(_fmt_phone(n) for n in dnc[:5]))
    if res.emails:
        lines.append("Emails: " + ", ".join(str(e) for e in res.emails[:4]))
    notes = r.notes or ""
    if changed and NO_CONTACT_LINE in notes:
        notes = notes.replace(NO_CONTACT_LINE, "").replace("\n\n", "\n").strip()
    r.notes = _note(notes, "\n".join(lines))
    return changed


def lookup_one(db, org_id: str, k: Kind, r, user, settings, provider) -> Dict[str, Any]:
    """One paid lookup for one record. Never raises for a vendor failure."""
    if k.key == "property":
        from app.routers.wholesale_router import enrich_property
        out = enrich_property(db, org_id, user, settings, provider, r)
        return {"id": r.id, "name": k.name(r), "status": out["status"], "message": out.get("message"),
                "filled": {"phone": out["phones"][0]} if out.get("phones") else
                          ({"email": out["emails"][0]} if out.get("emails") else {}),
                "cost_cents": out.get("cost_cents") or 0}
    a = k.address(db, r)
    data = WE.EnrichmentInput(
        street_address=a["street"], city=a["city"], state=a["state"], zip_code=a["zip"],
        owner_name=k.name(r), business_name=k.name(r) if k.is_company(r) else None,
        mailing_street=a["street"], mailing_city=a["city"], mailing_state=a["state"], mailing_zip=a["zip"])
    inputs = json.loads(data.to_json())
    inputs["%s_id" % k.key] = r.id
    rec = WholesaleEnrichmentRequest(
        organization_id=org_id, property_id=None, provider=PROVIDER_KEY,
        requested_by_id=getattr(user, "id", None), requested_by_actor=ACTOR_USER,
        inputs=json.dumps(inputs), billable=True)
    db.add(rec)
    db.flush()
    try:
        res = provider.lookup(data)
    except Exception as exc:                                     # noqa: BLE001
        log.warning("contact lookup failed for %s %s: %s", k.key, r.id, exc)
        res = WE.EnrichmentResult(status=WE.STATUS_FAILED, provider=PROVIDER_KEY, message=str(exc)[:200])
    now = datetime.utcnow()
    rec.status = res.status
    rec.result = res.to_json()
    rec.billable = bool(res.billable)
    rec.cost_cents = res.cost_cents
    rec.error = res.message if res.status in (WE.STATUS_FAILED, WE.STATUS_NOT_CONFIGURED) else None
    rec.completed_at = now
    changed = _apply(k, r, res, now) if res.status in (WE.STATUS_SUCCEEDED, WE.STATUS_NO_MATCH) else {}
    return {"id": r.id, "name": k.name(r), "status": res.status, "message": res.message,
            "filled": changed, "cost_cents": res.cost_cents or 0}
