"""The owner -> person -> contact-point graph, and Contact Confidence.

Identity rules:
  * one person per (owner, name); one contact point per (person, kind, value)
  * the SAME value reported again by a DIFFERENT source raises
    `agreeing_sources` - that is what "two sources agree" means
  * a wrong-party / suppressed / opted-out contact point is never deleted; its
    status is what stops every other strategy from using it again
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseOwner, EvoSenseOwnership,
                                        EvoSensePerson, EvoSenseProperty)
from app.services.evosense import common as C
from app.services.evosense import scoring as SC
from app.services.evosense.ingest import mailing_key, name_key

BAD_STATUSES = ("wrong_party", "suppressed", "opted_out", "invalid")


def normalize_phone(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    d = re.sub(r"\D", "", str(raw))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return "+1" + d if len(d) == 10 else None


def current_owners(db, prop) -> List[EvoSenseOwner]:
    links = (db.query(EvoSenseOwnership)
             .filter(EvoSenseOwnership.organization_id == prop.organization_id,
                     EvoSenseOwnership.property_id == prop.id,
                     EvoSenseOwnership.is_current.is_(True)).all())
    ids = [l.owner_id for l in links]
    if not ids:
        return []
    owners = (db.query(EvoSenseOwner)
              .filter(EvoSenseOwner.organization_id == prop.organization_id,
                      EvoSenseOwner.id.in_(ids)).all())
    # conflicting owners last: the undisputed owner of record is the one we work
    disputed = {l.owner_id for l in links if l.in_conflict}
    return sorted(owners, key=lambda o: (o.id in disputed, o.created_at or C.now()))


def primary_owner(db, prop) -> Optional[EvoSenseOwner]:
    owners = current_owners(db, prop)
    return owners[0] if owners else None


def _person(db, owner, full_name, role, source) -> EvoSensePerson:
    nk = name_key(full_name)
    p = (db.query(EvoSensePerson)
         .filter(EvoSensePerson.organization_id == owner.organization_id,
                 EvoSensePerson.owner_id == owner.id,
                 EvoSensePerson.name_key == nk).first())
    if p is None:
        p = EvoSensePerson(organization_id=owner.organization_id, owner_id=owner.id,
                           full_name=full_name, name_key=nk, role=role, source=source,
                           is_test=owner.is_test)
        db.add(p)
        db.flush()
    return p


def apply_result(db, owner: EvoSenseOwner, result, provider, *, manual_user=None
                 ) -> List[EvoSenseContactPoint]:
    """Write an EnrichmentResult into the graph. Returns the contact points touched."""
    person = None
    if result.message and result.message.startswith("person:"):
        full, _, role = result.message[7:].partition("|")
        person = _person(db, owner, full, role or "unknown", provider.key)
    if person is None:
        person = (db.query(EvoSensePerson)
                  .filter(EvoSensePerson.organization_id == owner.organization_id,
                          EvoSensePerson.owner_id == owner.id,
                          EvoSensePerson.role.in_(("owner", "co_owner"))).first())
    if person is None:
        # An entity with no named person: the contact belongs to "someone at"
        # the entity, and says so. No name is invented.
        person = _person(db, owner, "Unnamed contact for %s" % (owner.display_name or "owner"),
                         "unknown", provider.key)

    mailing_match = None
    if result.mailing_street:
        mailing_match = mailing_key(result.mailing_street, result.mailing_zip) == owner.mailing_key

    touched = []
    items = [("phone", normalize_phone(ph.number), ph.number, ph.phone_type, ph.confidence,
              ph.source) for ph in result.phones]
    items += [("email", (e or "").strip().lower(), e, None, result.confidence, provider.key)
              for e in result.emails]
    db.flush()
    for kind, value, raw, line, conf, sub in items:
        if not value:
            continue
        cp = (db.query(EvoSenseContactPoint)
              .filter(EvoSenseContactPoint.organization_id == owner.organization_id,
                      EvoSenseContactPoint.person_id == person.id,
                      EvoSenseContactPoint.kind == kind,
                      EvoSenseContactPoint.value == value).first())
        # A provider that reports several of its own sources for one number
        # ("utility+telecom") is counted as that many - and labelled as the
        # provider's claim, not as independent vendors.
        sub_count = len((sub or "").split(":", 1)[-1].split("+")) if sub else 1
        if cp is None:
            cp = EvoSenseContactPoint(
                organization_id=owner.organization_id, person_id=person.id, owner_id=owner.id,
                kind=kind, value=value, raw_value=raw, source=provider.key,
                connector_kind=provider.connector_kind, provider_confidence=conf,
                line_type=line, validation="unverified", agreeing_sources=max(1, sub_count),
                mailing_match=mailing_match, is_test=owner.is_test)
            db.add(cp)
        else:
            if cp.source != provider.key:
                cp.agreeing_sources = (cp.agreeing_sources or 1) + 1
            cp.last_seen_at = C.now()
            if conf is not None:
                cp.provider_confidence = max(cp.provider_confidence or 0, conf)
            if mailing_match:
                cp.mailing_match = True
        # A number already known as wrong-party / suppressed anywhere in this
        # organization inherits that status: a new strategy cannot resurrect it.
        prior = (db.query(EvoSenseContactPoint)
                 .filter(EvoSenseContactPoint.organization_id == owner.organization_id,
                         EvoSenseContactPoint.kind == kind,
                         EvoSenseContactPoint.value == value,
                         EvoSenseContactPoint.status.in_(BAD_STATUSES)).first())
        if prior is not None and prior is not cp and cp.status == "active":
            cp.status = prior.status
            cp.status_reason = "inherited: %s" % (prior.status_reason or prior.status)
        touched.append(cp)
        db.flush()
    owner.last_enriched_at = C.now()
    if person.role in ("heir", "executor") and owner.owner_type == "estate":
        owner.resolution = "resolved"
    db.flush()
    return touched


def score_contact_point(db, prop, cp) -> Dict[str, Any]:
    person = db.query(EvoSensePerson).filter(EvoSensePerson.id == cp.person_id).first()
    owner = db.query(EvoSenseOwner).filter(EvoSenseOwner.id == cp.owner_id).first()
    res = SC.contact_confidence(cp, person, owner, mailing_agrees=cp.mailing_match)
    SC.record(db, prop, "contact_confidence", res, subject_type="contact_point", subject_id=cp.id)
    return res


def contact_points_for(db, prop) -> List[Tuple[EvoSenseContactPoint, Dict[str, Any]]]:
    """Every contact point for the property's current owners, best first, scored."""
    out = []
    for owner in current_owners(db, prop):
        cps = (db.query(EvoSenseContactPoint)
               .filter(EvoSenseContactPoint.organization_id == prop.organization_id,
                       EvoSenseContactPoint.owner_id == owner.id).all())
        for cp in cps:
            out.append((cp, score_contact_point(db, prop, cp)))
    out.sort(key=lambda t: (t[0].status != "active", -(t[1]["value"] or 0)))
    return out


def best_contact(db, prop):
    for cp, sc in contact_points_for(db, prop):
        if cp.status == "active" and cp.kind == "phone":
            return cp, sc
    for cp, sc in contact_points_for(db, prop):
        if cp.status == "active":
            return cp, sc
    return None, None
