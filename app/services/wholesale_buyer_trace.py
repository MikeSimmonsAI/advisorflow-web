"""Find a phone number and email for cash buyers (skip trace).

Buyers found in the county records arrive with a name and a mailing address
and nothing else - the county file has no phone or email. This looks each one
up by that mailing address with the skip-trace vendor (Tracerfy's instant
lookup) and fills in what it finds.

MONEY RULES
- Nothing is looked up until the person has seen the most it can cost and
  sent that number back (`max_cost_cents`). If the cost went up since they
  looked, the run is refused, not quietly charged.
- Tracerfy charges only when it finds someone; a miss is free.
- Every lookup leaves a row in wholesale_enrichment_requests (property_id
  empty, buyer id in `inputs`), so the organization's daily / monthly paid
  lookup caps count buyer lookups too, and a buyer is never paid for twice
  unless the person asks again.

WHAT IS NEVER DONE
- A phone or email a person typed is never overwritten.
- When the vendor finds a different person at a person-buyer's address (the
  names do not agree), nothing is filled in - it is noted for a person to
  judge. A company has no person name to compare, so the person found at the
  company's mailing address is filled in and named as the contact.
- Numbers on the Do Not Call list are only used when nothing else came back,
  and the note says so.
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from app.models.wholesale_models import (ACTOR_USER, WholesaleBuyer,
                                         WholesaleEnrichmentRequest)
from app.services import wholesale_enrichment as WE

log = logging.getLogger(__name__)

PROVIDER_KEY = "tracerfy"
PROVIDER_LABEL = "Tracerfy"
TOKEN_ENV = "TRACERFY_API_TOKEN"
COST_PER_HIT_CENTS = 10          # instant lookup: 5 credits x $0.02, misses free
MAX_PER_CALL = 25                # the screen sends small batches and shows progress
NO_CONTACT_LINE = "No phone or email yet - look one up or mail them before sending deals."

_MAIL = re.compile(r"Mailing address \(county record\):\s*(.+)")
_TAIL = re.compile(r"^(?P<state>[A-Z]{2})(?:\s+(?P<zip>\d{5})(?:-?\d{4})?)?$")


def _default_provider():
    from app.services.evosense.vendors import TracerfySkipTrace
    return TracerfySkipTrace()


# Swappable in tests; production always uses the real vendor adapter.
provider_factory: Callable[[], Any] = _default_provider


def configured() -> bool:
    return bool(os.environ.get(TOKEN_ENV))


def name_of(b: WholesaleBuyer) -> str:
    return b.company_name or b.contact_name or ""


def mailing(b: WholesaleBuyer) -> Optional[Dict[str, str]]:
    """The county mailing address saved on the buyer when it was imported."""
    m = _MAIL.search(b.notes or "")
    if not m:
        return None
    parts = [p.strip() for p in m.group(1).strip().split(",") if p.strip()]
    if len(parts) < 3:
        return None
    t = _TAIL.match(parts[-1].upper())
    if not t:
        return None
    return {"street": ", ".join(parts[:-2]), "city": parts[-2],
            "state": t.group("state"), "zip": t.group("zip") or ""}


def _tried(db, org_id: str, buyer_id: str) -> bool:
    return (db.query(WholesaleEnrichmentRequest.id)
            .filter(WholesaleEnrichmentRequest.organization_id == org_id,
                    WholesaleEnrichmentRequest.property_id.is_(None),
                    WholesaleEnrichmentRequest.provider == PROVIDER_KEY,
                    WholesaleEnrichmentRequest.status.in_((WE.STATUS_SUCCEEDED, WE.STATUS_NO_MATCH)),
                    WholesaleEnrichmentRequest.inputs.like('%%"buyer_id": "%s"%%' % buyer_id))
            .first() is not None)


def plan(db, org_id: str, buyer_ids: Optional[List[str]] = None, *,
         again: bool = False) -> Dict[str, Any]:
    """Which buyers would be looked up, and the most it can cost."""
    q = db.query(WholesaleBuyer).filter(WholesaleBuyer.organization_id == org_id,
                                        WholesaleBuyer.is_test.is_(False))
    if buyer_ids is not None:
        q = q.filter(WholesaleBuyer.id.in_(list(buyer_ids)[:1000]))
    buyers = q.order_by(WholesaleBuyer.created_at.asc()).all()
    eligible, skipped = [], {"has_phone_and_email": 0, "no_mailing_address": 0,
                             "do_not_contact": 0, "already_looked_up": 0}
    for b in buyers:
        if b.phone and b.email:
            skipped["has_phone_and_email"] += 1
        elif b.do_not_contact:
            skipped["do_not_contact"] += 1
        elif mailing(b) is None:
            skipped["no_mailing_address"] += 1
        elif not again and _tried(db, org_id, b.id):
            skipped["already_looked_up"] += 1
        else:
            eligible.append(b.id)
    return {"provider": PROVIDER_KEY, "provider_label": PROVIDER_LABEL,
            "configured": configured(), "missing": [] if configured() else [TOKEN_ENV],
            "cost_per_find_cents": COST_PER_HIT_CENTS, "misses_free": True,
            "buyer_ids": eligible, "count": len(eligible),
            "max_cost_cents": len(eligible) * COST_PER_HIT_CENTS,
            "skipped": skipped, "considered": len(buyers)}


class _Billable:
    """What the shared paid-lookup cap check needs to know about a provider."""
    billable = True


def cap_refusal(db, org_id: str, settings: Any, n: int) -> Optional[str]:
    return WE.admit(db, org_id, _Billable(), settings, requested=n)


def _pick_phone(phones) -> Optional[Any]:
    def order(p):
        return (bool(p.dnc_flag), 0 if p.phone_type == "mobile" else 1)
    return sorted(phones, key=order)[0] if phones else None


def _fmt_phone(n: str) -> str:
    d = "".join(ch for ch in n if ch.isdigit())[-10:]
    return "(%s) %s-%s" % (d[:3], d[3:6], d[6:]) if len(d) == 10 else n


def apply(b: WholesaleBuyer, res: WE.EnrichmentResult, when: datetime) -> Dict[str, Any]:
    """Write what the lookup found onto the buyer, never over a person's entry.
    Returns what changed (for the contact write-through and the event)."""
    ev = res.match_evidence or {}
    found_name = res.owner_name
    phones = list(res.phones or [])
    emails = [e.address if hasattr(e, "address") else str(e) for e in (res.emails or [])]
    emails = [e for e in emails if e and "@" in e]
    lines, changed = [], {}
    stamp = "Phone/email lookup (%s, %s)" % (PROVIDER_LABEL, when.strftime("%Y-%m-%d"))

    if not res.found_anything:
        b.notes = _note(b.notes, "%s: nothing found at the mailing address." % stamp)
        return changed
    if ev.get("deceased"):
        b.notes = _note(b.notes, "%s: the person found (%s) is listed as deceased - nothing filled in."
                        % (stamp, found_name or "unnamed"))
        return changed
    if b.entity_type != "company" and ev.get("name_match") == "none":
        b.notes = _note(b.notes, "%s: found %s at that address, not %s - nothing filled in. Numbers: %s"
                        % (stamp, found_name or "someone else", name_of(b),
                           ", ".join(_fmt_phone(p.number) for p in phones[:3]) or "none"))
        return changed

    best = _pick_phone(phones)
    if best is not None and not b.phone:
        b.phone = _fmt_phone(best.number)
        changed["phone"] = b.phone
    if emails and not b.email:
        b.email = emails[0]
        changed["email"] = b.email
    if b.entity_type == "company" and found_name and not b.contact_name:
        b.contact_name = found_name.title()
        changed["contact_name"] = b.contact_name
    if changed.get("phone"):
        b.preferred_channel = "phone"
    elif changed.get("email") and not b.phone:
        b.preferred_channel = "email"

    who = found_name.title() if found_name else "a match"
    lines.append("%s: found %s%s." % (stamp, who, " (person at the company's mailing address)"
                                      if b.entity_type == "company" else ""))
    if phones:
        lines.append("Phones: " + ", ".join(
            "%s%s%s" % (_fmt_phone(p.number), " %s" % p.phone_type if p.phone_type else "",
                        " - DO NOT CALL list" if p.dnc_flag else "") for p in phones[:5]))
        if best is not None and best.dnc_flag and changed.get("phone"):
            lines.append("Every number found is on the Do Not Call list - email or mail them instead of a cold call or text.")
    if emails:
        lines.append("Emails: " + ", ".join(emails[:4]))
    notes = b.notes or ""
    if changed and NO_CONTACT_LINE in notes:
        notes = notes.replace(NO_CONTACT_LINE, "").replace("\n\n", "\n").strip()
    b.notes = _note(notes, "\n".join(lines))
    return changed


def _note(existing: Optional[str], line: str) -> str:
    return ((existing or "").rstrip() + "\n" + line).strip()


def lookup_one(db, org_id: str, b: WholesaleBuyer, user, provider) -> Dict[str, Any]:
    """One paid lookup for one buyer. Never raises for a vendor failure."""
    from app.services.evosense.providers import ProviderRateLimited
    m = mailing(b)
    data = WE.EnrichmentInput(
        street_address=m["street"], city=m["city"], state=m["state"], zip_code=m["zip"],
        owner_name=name_of(b), business_name=b.company_name,
        mailing_street=m["street"], mailing_city=m["city"], mailing_state=m["state"],
        mailing_zip=m["zip"])
    inputs = json.loads(data.to_json())
    inputs["buyer_id"] = b.id
    rec = WholesaleEnrichmentRequest(
        organization_id=org_id, property_id=None, provider=PROVIDER_KEY,
        requested_by_id=getattr(user, "id", None), requested_by_actor=ACTOR_USER,
        inputs=json.dumps(inputs), billable=True)
    db.add(rec)
    db.flush()
    stop = False
    try:
        res = provider.enrich(data)
    except ProviderRateLimited:
        stop = True
        res = WE.EnrichmentResult(status=WE.STATUS_FAILED, provider=PROVIDER_KEY,
                                  message="Tracerfy asked us to slow down - try the rest in a few minutes.")
    except Exception as exc:                                     # noqa: BLE001
        log.warning("buyer skip trace failed for %s: %s", b.id, exc)
        res = WE.EnrichmentResult(status=WE.STATUS_FAILED, provider=PROVIDER_KEY,
                                  message=str(exc)[:200])
    now = datetime.utcnow()
    rec.status = res.status
    rec.result = res.to_json()
    rec.billable = bool(res.billable)
    rec.cost_cents = res.cost_cents
    rec.error = res.message if res.status == WE.STATUS_FAILED else None
    rec.completed_at = now
    changed = apply(b, res, now) if res.status != WE.STATUS_FAILED else {}
    return {"buyer_id": b.id, "name": name_of(b), "status": res.status,
            "message": res.message, "filled": changed, "phone": b.phone, "email": b.email,
            "cost_cents": res.cost_cents or 0, "stop": stop}
