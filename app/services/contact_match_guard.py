"""WRONG-PERSON GUARD for contact enrichment (EvoSense and Wholesale paths).

A skip-trace vendor returns the person IT thinks lives at an address. Before any
phone or email from that answer is tied to a homeowner, the person's name is
compared with the owner of record:

    MATCHED     every token of the shorter name appears in the other ("HOGAN
                KRISTINA L" ~ "Kristina Hogan")          -> used, still unverified
                                                           until a call or a person confirms it
    PROBABLE    only the surname agrees, or there is no name to compare
                                                         -> kept for a person to confirm;
                                                           never written to the seller automatically
    NO_VERIFIED_MATCH  a different person, or listed as deceased
                                                         -> not attached to anyone

The vendor's own `name_match` evidence is used when it gave one; otherwise the
name is compared here. Nothing in this module calls a provider or spends money.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

MATCHED = "MATCHED"
PROBABLE = "PROBABLE"
NO_VERIFIED_MATCH = "NO_VERIFIED_MATCH"
LABELS = {MATCHED: "Name matches the owner of record", PROBABLE: "Probable - needs a person to confirm",
          NO_VERIFIED_MATCH: "No verified match - not the owner"}


def assess(owner_name: Optional[str], result: Any) -> Dict[str, Any]:
    from app.services.evosense.vendors import name_agreement
    ev = dict(getattr(result, "match_evidence", None) or {})
    if ev.get("deceased") in (True, "true", "True", 1, "1", "Y", "y"):
        return {"verdict": NO_VERIFIED_MATCH, "why": "The person the provider returned is listed as deceased."}
    agree = ev.get("name_match")
    if agree is None:
        agree = name_agreement(owner_name, getattr(result, "owner_name", None))
    if agree == "full":
        return {"verdict": MATCHED, "why": "The provider's person name agrees with the owner of record."}
    if agree == "none":
        return {"verdict": NO_VERIFIED_MATCH,
                "why": "The provider returned %s, who is not the owner of record (%s)." % (
                    getattr(result, "owner_name", None) or "another person", owner_name or "unknown")}
    if agree == "surname":
        return {"verdict": PROBABLE, "why": "Only the last name agrees with the owner of record."}
    return {"verdict": PROBABLE, "why": "There was no name to compare with the owner of record."}


# ── one plain status per property ───────────────────────────────────────────
NOT_REQUESTED, PROCESSING, ST_MATCHED, PARTIAL, ST_NO_MATCH, FAILED = (
    "NOT_REQUESTED", "PROCESSING", "MATCHED", "PARTIAL", "NO_VERIFIED_MATCH", "FAILED")
STATUS_LABELS = {NOT_REQUESTED: "Not requested", PROCESSING: "Processing", ST_MATCHED: "Matched",
                 PARTIAL: "Partial", ST_NO_MATCH: "No verified match", FAILED: "Failed"}


def property_status(db, prop) -> Dict[str, Any]:
    """Read from what exists: the contact enrichment decisions and the contact points
    on the owner. Matched = a phone or email whose name matched; Partial = contacts that
    are only probable, or email without a phone."""
    import json
    from app.models.evosense_models import EvoSenseContactPoint, EvoSenseEnrichmentDecision
    from app.utils.time_fmt import iso_utc
    last = (db.query(EvoSenseEnrichmentDecision)
            .filter(EvoSenseEnrichmentDecision.organization_id == prop.organization_id,
                    EvoSenseEnrichmentDecision.property_id == prop.id,
                    EvoSenseEnrichmentDecision.capability == "CONTACT_ENRICHMENT")
            .order_by(EvoSenseEnrichmentDecision.created_at.desc()).first())
    from app.services.evosense.contacts import current_owners
    owner_ids = [o.id for o in current_owners(db, prop)]
    cps = []
    if owner_ids:
        cps = (db.query(EvoSenseContactPoint)
               .filter(EvoSenseContactPoint.organization_id == prop.organization_id,
                       EvoSenseContactPoint.owner_id.in_(owner_ids),
                       EvoSenseContactPoint.status == "active").all())
    contacts = []
    for cp in cps:
        try:
            ev = json.loads(cp.match_evidence or "{}")
        except (TypeError, ValueError):
            ev = {}
        q = ev.get("match_quality") or (MATCHED if cp.trust_state == "trusted" or cp.source == "manual" else PROBABLE)
        contacts.append({"kind": cp.kind, "value": cp.value, "source": cp.source, "quality": q,
                         "verified": cp.trust_state == "trusted", "looked_up_at": iso_utc(cp.looked_up_at)})
    good = [c for c in contacts if c["quality"] == MATCHED]
    if good and any(c["kind"] == "phone" for c in good):
        key = ST_MATCHED
    elif contacts:
        key = PARTIAL
    elif last is None:
        key = NOT_REQUESTED
    elif last.outcome in (None, "", "queued"):
        key = PROCESSING
    elif last.outcome == "no_match":
        key = ST_NO_MATCH
    elif last.outcome in ("provider_failed", "budget_blocked"):
        key = FAILED
    else:
        key = NOT_REQUESTED if last.outcome == "skipped" else ST_NO_MATCH
    try:
        reasons = json.loads(last.reasons or "[]") if last is not None else []
    except (TypeError, ValueError):
        reasons = []
    return {"status": key, "label": STATUS_LABELS[key], "contacts": contacts,
            "last_decision": ({"decision": last.decision, "outcome": last.outcome, "provider": last.provider_key,
                               "reasons": reasons, "at": iso_utc(last.created_at)} if last is not None else None),
            "rule": "A provider's answer is attached only when its person is the owner of record; probable "
                    "matches wait for a person; nothing is bought without the budget and approval rules."}
