"""CONTACTABILITY - can we lawfully reach this owner, and by what channel?

A property being interesting does not mean its owner can be contacted, and
HAVING a phone number does not mean we may use it. Contactability keeps those
apart, as one explainable state per owner/property relationship:

    UNKNOWN              owner not identified (or an entity not yet resolved to a
                         person) - nothing to reach yet
    ENRICHMENT_NEEDED    owner identified; no usable contact data
    CONTACT_DATA_FOUND   candidate phone/email exists, but no channel is both
                         permitted and operational (e.g. no SMS consent), or the
                         data is questionable (low Contact Confidence)
    CONTACTABLE_SMS      SMS eligibility is fully satisfied (consent of record,
                         no block, program on, sender registered)
    CONTACTABLE_EMAIL    email eligibility is fully satisfied
    CONTACTABLE_OTHER    another approved channel is: a PERSON may call a seller
                         who contacted us or agreed to calls
    DO_NOT_CONTACT       a deterministic block covers every channel and number
                         (DNC, opt-out, suppression, remove-all)
    REVIEW_REQUIRED      conflicting identity evidence - automation stops

It is DERIVED from the platform's own authorities (communication_eligibility,
EvoSense Contact Confidence, ownership evidence) and never stored as a fact
anyone can set. The explanation travels with the state.

Contact Confidence answers "is this the right person at this number"; it is not
permission. Permission never comes from confidence - but a CONTACTABLE state
also requires the data to be trustworthy, unless the person gave it to us
themselves (their consent of record, their own inquiry).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services import communication_eligibility as CE

VERSION = "contactability/v1"

UNKNOWN = "UNKNOWN"
ENRICHMENT_NEEDED = "ENRICHMENT_NEEDED"
CONTACT_DATA_FOUND = "CONTACT_DATA_FOUND"
CONTACTABLE_SMS = "CONTACTABLE_SMS"
CONTACTABLE_EMAIL = "CONTACTABLE_EMAIL"
CONTACTABLE_OTHER = "CONTACTABLE_OTHER"
DO_NOT_CONTACT = "DO_NOT_CONTACT"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
STATES = (UNKNOWN, ENRICHMENT_NEEDED, CONTACT_DATA_FOUND, CONTACTABLE_SMS, CONTACTABLE_EMAIL,
          CONTACTABLE_OTHER, DO_NOT_CONTACT, REVIEW_REQUIRED)
CONTACTABLE = (CONTACTABLE_SMS, CONTACTABLE_EMAIL, CONTACTABLE_OTHER)

LABELS = {
    UNKNOWN: "Unknown", ENRICHMENT_NEEDED: "Enrichment needed",
    CONTACT_DATA_FOUND: "Contact data found", CONTACTABLE_SMS: "Contactable — SMS",
    CONTACTABLE_EMAIL: "Contactable — Email", CONTACTABLE_OTHER: "Contactable — Phone call",
    DO_NOT_CONTACT: "Do not contact", REVIEW_REQUIRED: "Review required",
}

BAD_STATUSES = ("opted_out", "suppressed")          # deterministic: never usable
UNUSABLE_STATUSES = ("wrong_party", "invalid")      # not this person / not a number
# Bases where the PERSON gave us the channel - confidence cannot be low about
# a number someone typed into our own form.
SELF_PROVIDED = ("SMS_CONSENT_OF_RECORD", "SELLER_INITIATED")


def _result(state: str, reasons: List[str], *, channels=None, best=None, extra=None) -> Dict[str, Any]:
    out = {"state": state, "label": LABELS[state], "version": VERSION,
           "reasons": [r for r in reasons if r], "channels": channels or {},
           "best_contact": best, "evaluated_at": datetime.utcnow().isoformat()}
    if extra:
        out.update(extra)
    return out


def assess(db, org_id: str, *, candidates: List[Dict[str, Any]], lead=None,
           owner_identified: bool = True, owner_note: Optional[str] = None,
           identity_review: bool = False, identity_note: Optional[str] = None,
           enrichment_possible: bool = True, enrichment_note: Optional[str] = None,
           min_confidence: int = 60, seller_initiated: Optional[bool] = None) -> Dict[str, Any]:
    """The state for one owner/person.

    candidates: [{"kind": "phone"|"email", "value", "status" (None=active),
                  "line_type", "confidence": int|None, "id", "source"}]"""
    if lead is not None and ((lead.status or "") == "dnc" or
                             getattr(lead, "manual_flag", None) == "remove_all"):
        return _result(DO_NOT_CONTACT, ["This person is Do Not Contact" if lead.status == "dnc"
                                        else "Manually flagged: no outreach on any channel"])
    if identity_review:
        return _result(REVIEW_REQUIRED, [identity_note or
                                         "Sources disagree about who owns this property"])
    if not owner_identified:
        return _result(UNKNOWN, [owner_note or "The owner has not been identified"])

    usable = [c for c in candidates if (c.get("status") or "active") == "active"]
    barred = [c for c in candidates if c.get("status") in BAD_STATUSES]
    if not usable:
        if barred and len(barred) == len([c for c in candidates
                                           if c.get("status") not in UNUSABLE_STATUSES]):
            return _result(DO_NOT_CONTACT, ["Every contact we hold for this person opted out "
                                            "or is suppressed"])
        why = ("Only wrong-party or invalid contact data" if candidates else
               "No phone or email on file")
        if enrichment_possible:
            return _result(ENRICHMENT_NEEDED, [why])
        return _result(UNKNOWN, [why, enrichment_note or "Enrichment does not apply"])

    channels: Dict[str, Dict[str, Any]] = {}

    def consider(ch: str, verdict: Dict[str, Any], cand: Dict[str, Any]):
        verdict = dict(verdict, contact_id=cand.get("id"), confidence=cand.get("confidence"),
                       source=cand.get("source"))
        cur = channels.get(ch)
        rank = {"ELIGIBLE": 0, "NOT_OPERATIONAL": 1, "NO_PERMISSION": 2, "BLOCKED": 3}
        key = (rank.get(verdict["state"], 9), -(cand.get("confidence") or 0))
        if cur is None or key < cur["_key"]:
            verdict["_key"] = key
            channels[ch] = verdict

    for c in usable:
        if c["kind"] == "phone":
            consider(CE.SMS, CE.sms(db, org_id, c["value"], lead=lead, line_type=c.get("line_type"),
                                    contact_status=c.get("status")), c)
            consider(CE.VOICE, CE.voice(db, org_id, c["value"], lead=lead,
                                        seller_initiated=seller_initiated), c)
        elif c["kind"] == "email":
            consider(CE.EMAIL, CE.email(db, org_id, c["value"], lead=lead,
                                        seller_initiated=seller_initiated,
                                        contact_status=c.get("status")), c)
    for v in channels.values():
        v.pop("_key", None)
        v.pop("checks", None)

    def trustworthy(v) -> bool:
        return (v.get("permission_basis") in SELF_PROVIDED or v.get("confidence") is None
                or (v.get("confidence") or 0) >= min_confidence)

    for state, ch in ((CONTACTABLE_SMS, CE.SMS), (CONTACTABLE_EMAIL, CE.EMAIL),
                      (CONTACTABLE_OTHER, CE.VOICE)):
        v = channels.get(ch)
        if v and v["eligible"] and trustworthy(v):
            others = [x for x in (CE.SMS, CE.EMAIL, CE.VOICE)
                      if x != ch and channels.get(x, {}).get("eligible")]
            return _result(state, ["%s: %s" % (ch.upper(), (v.get("permission_basis") or "")
                                               .replace("_", " ").lower())]
                           + (["Also eligible: %s" % ", ".join(others)] if others else []),
                           channels=channels, best={"channel": ch, "value": v["value"],
                                                    "contact_id": v["contact_id"]})
    if channels and all(v["state"] == "BLOCKED" for v in channels.values()):
        return _result(DO_NOT_CONTACT, sorted({v["reason"] for v in channels.values()}),
                       channels=channels)
    reasons = []
    for ch in (CE.SMS, CE.EMAIL, CE.VOICE):
        v = channels.get(ch)
        if v is None:
            continue
        if v["eligible"] and not trustworthy(v):
            reasons.append("%s: questionable data - Contact Confidence %s below %s"
                           % (ch.upper(), v.get("confidence"), min_confidence))
        elif v["reason"]:
            reasons.append("%s: %s" % (ch.upper(), v["reason"]))
    return _result(CONTACT_DATA_FOUND, reasons, channels=channels)


# ── EvoSense property (the owner of record) ─────────────────────────────────

def for_evosense_property(db, prop, strategy=None) -> Dict[str, Any]:
    from app.models.evosense_models import EvoSenseOwnership
    from app.models.models import Lead
    from app.services.evosense import contacts as CT
    from app.services.evosense.ingest import INSTITUTIONAL
    owners = CT.current_owners(db, prop)
    resolved = [o for o in owners if o.resolution != "unresolved"]
    conflict = (db.query(EvoSenseOwnership.id)
                .filter(EvoSenseOwnership.organization_id == prop.organization_id,
                        EvoSenseOwnership.property_id == prop.id,
                        EvoSenseOwnership.is_current.is_(True),
                        EvoSenseOwnership.in_conflict.is_(True)).first() is not None)
    owner = resolved[0] if resolved else (owners[0] if owners else None)
    institutional = bool(owner is not None and owner.owner_type in INSTITUTIONAL)
    cands, lead = [], None
    for cp, sc in CT.contact_points_for(db, prop):
        cands.append({"id": cp.id, "kind": cp.kind, "value": cp.value, "status": cp.status,
                      "line_type": cp.line_type, "confidence": (sc or {}).get("value"),
                      "source": cp.source})
    from app.models.evosense_models import EvoSensePerson
    person = (db.query(EvoSensePerson)
              .filter(EvoSensePerson.organization_id == prop.organization_id,
                      EvoSensePerson.owner_id == getattr(owner, "id", None),
                      EvoSensePerson.lead_id.isnot(None)).first()) if owner is not None else None
    if person is not None:
        lead = db.query(Lead).filter(Lead.id == person.lead_id,
                                     Lead.organization_id == prop.organization_id).first()
    need = getattr(strategy, "min_contact_confidence", None) or 60
    return assess(db, prop.organization_id, candidates=cands, lead=lead,
                  owner_identified=bool(resolved),
                  owner_note=("The owner of record is an entity not yet resolved to a person"
                              if owners else "The owner has not been identified"),
                  identity_review=(prop.identity_status == "review" or conflict),
                  identity_note=("Two sources may describe the same house"
                                 if prop.identity_status == "review"
                                 else "Sources disagree about who owns this property"),
                  enrichment_possible=not institutional,
                  enrichment_note="Institutional owner - skip-tracing does not apply",
                  min_confidence=need, seller_initiated=False)


def refresh_evosense(db, prop, strategy=None) -> Dict[str, Any]:
    """Compute and cache on the property. Never raises into the caller."""
    try:
        res = for_evosense_property(db, prop, strategy)
    except Exception as exc:  # noqa: BLE001 - an explanation must not break scoring
        import logging
        logging.getLogger(__name__).exception("contactability failed for %s", prop.id)
        res = _result(UNKNOWN, ["Contactability could not be evaluated: %s" % type(exc).__name__])
    prop.contactability = res["state"]
    prop.contactability_detail = json.dumps(res, default=str)
    prop.contactability_at = datetime.utcnow()
    return res


# ── Wholesale seller (a Lead on a property) ─────────────────────────────────

def for_seller(db, org_id: str, lead, *, verified: bool = False) -> Dict[str, Any]:
    """A Wholesale seller is a Lead: its channels are the Lead's own. A seller
    who reached out (the /sell form) gave us these details themselves."""
    if lead is None:
        return _result(UNKNOWN, ["No person attached to this property yet"])
    cands = []
    if lead.phone:
        cands.append({"id": lead.id, "kind": "phone", "value": lead.phone, "status": None,
                      "confidence": None, "source": lead.source_category})
    if lead.email:
        cands.append({"id": lead.id, "kind": "email", "value": lead.email, "status": None,
                      "confidence": None, "source": lead.source_category})
    return assess(db, org_id, candidates=cands, lead=lead, owner_identified=True,
                  enrichment_possible=True)
