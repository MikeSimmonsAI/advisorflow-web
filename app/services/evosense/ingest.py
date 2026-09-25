"""Ingestion: one source record -> observation -> canonical property + owner graph.

Every provider, the manual form and the CSV import call `ingest()`. There is
no second cleaning path per connector.

FACT PRECEDENCE. A value is only replaced by a HIGHER-ranked source. A
different value from an equal or lower source is recorded as a CONFLICT on
the property and left for a person — never silently overwritten.

    human (manual correction) 100 > assessor/parcel/ownership records 60
    > other provider feeds 40 > file import 30
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.models.evosense_models import (EvoSenseIdentityReview, EvoSenseObservation,
                                        EvoSenseOwner, EvoSenseOwnership, EvoSensePerson,
                                        EvoSenseProperty)
from app.services.evosense import common as C
from app.services.evosense import identity as ID
from app.services.evosense import signals as SIG

RANK_HUMAN = 100
RANK_RECORDS = 60
RANK_FEED = 40
RANK_IMPORT = 30


def source_rank(provider, capability: str) -> int:
    if provider.connector_kind == C.MANUAL:
        return RANK_HUMAN
    if provider.connector_kind == C.IMPORT:
        return RANK_IMPORT
    if capability in (C.PROPERTY_SEARCH, C.ASSESSOR, C.PARCEL, C.OWNERSHIP):
        return RANK_RECORDS
    return RANK_FEED


# ── owner parsing (deterministic; never invents a beneficial owner) ────────

def owner_type_of(name: str) -> str:
    n = " %s " % re.sub(r"[.,]", " ", (name or "").upper())
    n = re.sub(r"\s+", " ", n)
    if not name:
        return "unknown"
    if n.strip().startswith("ESTATE OF") or " ESTATE " in n and " HEIRS" in n:
        return "estate"
    if re.search(r" (CITY|COUNTY|STATE) OF | HOUSING AUTHORITY | ISD ", n):
        return "government"
    if re.search(r" (TRUST|TRUSTEE|TR) ", n):
        return "trust"
    if re.search(r" (LLC|L L C) ", n):
        return "llc"
    if re.search(r" (INC|CORP|CORPORATION|CO|LP|LTD|LLP|HOLDINGS|PROPERTIES|INVESTMENTS) ", n):
        return "corporation"
    if re.search(r" (&|AND) ", n):
        return "joint"
    toks = [t for t in n.split() if t]
    if 2 <= len(toks) <= 5 and all(re.fullmatch(r"[A-Z'-]+", t) for t in toks):
        return "individual"
    return "unknown"


def name_key(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    k = re.sub(r"[^A-Z0-9 ]", " ", name.upper())
    toks = [t for t in k.split() if t not in ("JR", "SR", "II", "III", "MR", "MRS", "MS")]
    # Middle initials are dropped: "Evelyn R. Harper" and "Evelyn Harper" are
    # the same name for matching purposes.
    toks = [t for i, t in enumerate(toks) if not (len(t) == 1 and 0 < i < len(toks) - 1)]
    return " ".join(toks) or None


def mailing_key(street, zip_code) -> Optional[str]:
    s, unit = ID.normalize_street(street)
    z = re.sub(r"\D", "", str(zip_code or ""))[:5]
    return "%s|%s|%s" % (s, unit or "", z) if s and z else None


def upsert_owner(db, org_id: str, prop: EvoSenseProperty, data: Dict[str, Any], *,
                 source: str, observation_id: Optional[str], rank: int,
                 is_test: bool, user=None) -> Optional[EvoSenseOwner]:
    name = (data.get("name") or "").strip()
    if not name:
        return None
    nk = name_key(name)
    mk = mailing_key(data.get("mailing_street"), data.get("mailing_zip"))
    q = db.query(EvoSenseOwner).filter(EvoSenseOwner.organization_id == org_id,
                                       EvoSenseOwner.name_key == nk)
    owner = q.filter(EvoSenseOwner.mailing_key == mk).first() if mk else q.first()
    if owner is None:
        otype = data.get("owner_type") or owner_type_of(name)
        owner = EvoSenseOwner(
            organization_id=org_id, owner_type=otype, display_name=name, name_key=nk,
            mailing_street=data.get("mailing_street"), mailing_city=data.get("mailing_city"),
            mailing_state=(data.get("mailing_state") or "").upper()[:2] or None,
            mailing_zip=data.get("mailing_zip"), mailing_key=mk,
            # An entity with no legitimately known people is UNRESOLVED, and
            # stays that way until a real source names someone.
            resolution="unresolved" if otype in ("llc", "corporation", "trust") else "resolved",
            is_test=is_test)
        db.add(owner)
        db.flush()
        for full, role in _persons_for(name, otype):
            db.add(EvoSensePerson(organization_id=org_id, owner_id=owner.id, full_name=full,
                                  name_key=name_key(full), role=role, source=source,
                                  is_test=is_test))
        db.flush()

    link = (db.query(EvoSenseOwnership)
            .filter(EvoSenseOwnership.organization_id == org_id,
                    EvoSenseOwnership.property_id == prop.id,
                    EvoSenseOwnership.owner_id == owner.id,
                    EvoSenseOwnership.source == source).first())
    if link is None:
        link = EvoSenseOwnership(organization_id=org_id, property_id=prop.id,
                                 owner_id=owner.id, source=source,
                                 observation_id=observation_id,
                                 corrected_by_id=getattr(user, "id", None))
        db.add(link)
    link.observed_at = C.now()
    link.is_current = True
    db.flush()

    # A different owner already on this property is a CONFLICT unless this
    # source outranks it (a human correction does).
    others = (db.query(EvoSenseOwnership)
              .filter(EvoSenseOwnership.organization_id == org_id,
                      EvoSenseOwnership.property_id == prop.id,
                      EvoSenseOwnership.owner_id != owner.id,
                      EvoSenseOwnership.is_current.is_(True)).all())
    if others:
        if rank >= RANK_HUMAN:
            for o in others:
                o.is_current = False
                o.in_conflict = False
            link.in_conflict = False
        else:
            names = []
            for o in others:
                o.in_conflict = True
                ow = db.query(EvoSenseOwner).filter(EvoSenseOwner.id == o.owner_id).first()
                names.append({"owner": ow.display_name if ow else None, "source": o.source})
            link.in_conflict = True
            add_conflict(prop, "owner", {"value": name, "source": source}, names)
    return owner


def _persons_for(name: str, otype: str):
    if otype == "individual":
        return [(name, "owner")]
    if otype == "joint":
        parts = [p.strip() for p in re.split(r"\s(?:&|and|AND)\s", name) if p.strip()]
        return [(p, "co_owner") for p in parts]
    return []


def add_conflict(prop, field: str, incoming: Dict, existing) -> None:
    items = C.jload(prop.conflicts, []) or []
    entry = {"field": field, "incoming": incoming, "existing": existing,
             "at": C.now().isoformat() + "Z", "status": "open"}
    for it in items:
        if it.get("field") == field and it.get("incoming") == incoming and it.get("status") == "open":
            return
    items.append(entry)
    prop.conflicts = C.jdump(items)
    prop.has_conflicts = True


# ── the ingest ──────────────────────────────────────────────────────────────

FACTS = ("property_type", "bedrooms", "bathrooms", "square_feet", "year_built",
         "latitude", "longitude", "county", "parcel_apn")


def _set_fact(prop, field, value, rank, source, prop_ranks):
    if value in (None, ""):
        return
    cur = getattr(prop, field)
    if cur in (None, ""):
        setattr(prop, field, value)
        prop_ranks[field] = rank
        return
    try:
        same = float(cur) == float(value)
    except (TypeError, ValueError):
        same = str(cur).strip().lower() == str(value).strip().lower()
    if same:
        return
    if rank > prop_ranks.get(field, 0):
        setattr(prop, field, value)
        prop_ranks[field] = rank
    else:
        add_conflict(prop, field, {"value": value, "source": source},
                     [{"value": cur, "source": "earlier source"}])


def _parse_date(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(value)[:19], fmt)
        except ValueError:
            continue
    return None


def ingest(db, org_id: str, provider, capability: str, rec: Dict[str, Any], *,
           strategy_id: Optional[str] = None, run_id: Optional[str] = None,
           is_test: bool = False, user=None, cost_cents: int = 0,
           ledger_id: Optional[str] = None) -> Tuple[str, Optional[EvoSenseProperty]]:
    """Returns (outcome, property). outcome: created|merged|seen|review."""
    ref = str(rec.get("source_reference") or "").strip()
    if not ref:
        raise ValueError("Every observation needs a source_reference.")
    is_test = bool(is_test or provider.connector_kind == C.SANDBOX)
    rank = source_rank(provider, capability)

    existing = (db.query(EvoSenseObservation)
                .filter(EvoSenseObservation.organization_id == org_id,
                        EvoSenseObservation.provider_key == provider.key,
                        EvoSenseObservation.source_reference == ref).first())
    if existing is not None:
        # IDEMPOTENT. The same record seen again refreshes evidence; it never
        # creates a second observation, property or signal.
        existing.last_seen_at = C.now()
        prop = None
        if existing.property_id:
            prop = db.query(EvoSenseProperty).filter(
                EvoSenseProperty.id == existing.property_id).first()
            if prop is not None:
                _apply_signals(db, prop, provider, rec, existing.id, cost_cents)
                prop.last_observed_at = C.now()
        return "seen", prop

    match, prop, candidates, keys = ID.resolve(db, org_id, rec)
    obs = EvoSenseObservation(
        organization_id=org_id, property_id=prop.id if prop else None,
        strategy_id=strategy_id, run_id=run_id, provider_key=provider.key,
        connector_kind=provider.connector_kind, capability=capability,
        source_reference=ref, payload=C.jdump(_sanitize(rec)), match_type=match,
        match_keys=C.jdump(keys), ledger_id=ledger_id, is_test=is_test)
    db.add(obs)
    db.flush()

    if match == ID.AMBIGUOUS:
        db.add(EvoSenseIdentityReview(
            organization_id=org_id, observation_id=obs.id,
            candidate_property_ids=C.jdump([c.id for c in candidates]),
            reason="Could be %s — %s" % (
                ", ".join(filter(None, (c.street_address for c in candidates))) or "an existing property",
                "; ".join(keys) or "partial match")))
        for c in candidates:
            c.identity_status = "review"
        C.log_event(db, org_id, "identity.review", summary="Ambiguous property identity for %s"
                    % (rec.get("street_address") or ref), strategy_id=strategy_id,
                    details={"observation": obs.id, "candidates": [c.id for c in candidates]},
                    is_test=is_test)
        return "review", None

    outcome = "merged"
    if prop is None:
        prop = EvoSenseProperty(organization_id=org_id, status=C.S_NEW,
                                first_strategy_id=strategy_id, best_strategy_id=strategy_id,
                                is_test=is_test)
        db.add(prop)
        outcome = "created"
    attach_to(db, prop, provider, capability, rec, obs, rank, user=user, cost_cents=cost_cents)
    obs.property_id = prop.id
    if outcome == "created":
        C.log_event(db, org_id, "property.discovered", property_id=prop.id,
                    strategy_id=strategy_id, actor_type=C.ACTOR_AUTOMATION if not user else C.ACTOR_USER,
                    user=user, summary="Discovered %s via %s" % (
                        prop.street_address or ref, provider.label), is_test=is_test)
    return outcome, prop


def attach_to(db, prop, provider, capability, rec, obs, rank, *, user=None, cost_cents=0):
    """Apply one record's facts, owner and signals to a canonical property."""
    ranks = C.jload(prop.fact_ranks, {}) or {}
    for f in ("street_address", "city", "state", "zip_code", "unit"):
        v = rec.get(f)
        if v and not getattr(prop, f):
            setattr(prop, f, v if f != "state" else str(v).upper()[:2])
    if rec.get("zip_code") and prop.zip_code and "-" in str(rec["zip_code"]) and "-" not in prop.zip_code:
        pass   # ZIP+4 from a feed does not replace a clean ZIP5
    for f in FACTS:
        v = rec.get(f)
        if f == "county" and v:
            v = re.sub(r"\s+county$", "", str(v).strip(), flags=re.I).title()
        _set_fact(prop, f, v, rank, provider.key, ranks)
    prop.fact_ranks = C.jdump(ranks)
    if prop.street_address:
        ID.apply_keys(prop, {"street_address": prop.street_address, "unit": prop.unit,
                             "zip_code": prop.zip_code, "city": prop.city, "state": prop.state,
                             "parcel_apn": prop.parcel_apn, "county": prop.county})
    db.flush()

    val = rec.get("valuation") or {}
    if val.get("value") is not None:
        cur = prop.estimated_value
        if cur is None or rank >= RANK_RECORDS:
            prop.estimated_value = int(val["value"])
            prop.estimated_value_source = "%s (%s)" % (provider.key, val.get("basis") or capability)
            prop.estimated_value_at = C.now()
        elif int(val["value"]) != cur:
            add_conflict(prop, "estimated_value", {"value": val["value"], "source": provider.key},
                         [{"value": cur, "source": prop.estimated_value_source}])
    if val.get("mortgage") is not None and (prop.mortgage_balance is None or rank >= RANK_RECORDS):
        prop.mortgage_balance = int(val["mortgage"])
        prop.mortgage_source = provider.key
    if prop.estimated_value and prop.mortgage_balance is not None and prop.estimated_value > 0:
        prop.equity_pct = int(round(100.0 * (prop.estimated_value - prop.mortgage_balance)
                                    / prop.estimated_value))
        prop.equity_basis = "computed"
    sale = _parse_date(rec.get("last_sale_date"))
    if sale and (prop.last_sale_date is None or rank >= RANK_RECORDS):
        prop.last_sale_date = sale
        prop.ownership_years = max(0, int((C.now() - sale).days // 365.25))
    if rec.get("occupancy") and (prop.occupancy is None or rank >= RANK_RECORDS):
        prop.occupancy = rec["occupancy"]
        prop.occupancy_source = provider.key

    if rec.get("owner"):
        upsert_owner(db, prop.organization_id, prop, rec["owner"], source=provider.key,
                     observation_id=obs.id if obs else None, rank=rank,
                     is_test=prop.is_test, user=user)
    _apply_signals(db, prop, provider, rec, obs.id if obs else None, cost_cents, user=user)
    prop.last_observed_at = C.now()


def _apply_signals(db, prop, provider, rec, observation_id, cost_cents, user=None):
    for s in rec.get("signals") or []:
        observed = C.now() - timedelta(days=int(s.get("observed_days_ago") or 0))
        SIG.upsert(db, prop, s["type"], source=provider.key,
                   connector_kind=provider.connector_kind,
                   source_reference=rec.get("source_reference"),
                   observation_id=observation_id, observed_at=observed,
                   effective_at=_parse_date(s.get("effective_at")),
                   confidence=s.get("confidence"), strength=s.get("strength"),
                   raw_value=s.get("raw"), normalized_value=s.get("value"),
                   provenance={"provider": provider.key, "connector": provider.connector_kind,
                               "record": rec.get("source_reference")},
                   cost_cents=cost_cents if len(rec.get("signals") or []) == 1 else 0,
                   user=user)
    db.flush()
    from app.models.evosense_models import EvoSenseSignal
    prop.signal_count = (db.query(EvoSenseSignal)
                         .filter(EvoSenseSignal.organization_id == prop.organization_id,
                                 EvoSenseSignal.property_id == prop.id,
                                 EvoSenseSignal.active.is_(True)).count())


def _sanitize(rec: Dict[str, Any]) -> Dict[str, Any]:
    """What we keep of a source record. Nothing credential-shaped survives."""
    bad = re.compile(r"(key|token|secret|password|auth)", re.I)
    return {k: v for k, v in rec.items() if not bad.search(str(k))}


def resolve_review(db, org_id: str, review: EvoSenseIdentityReview, action: str,
                   property_id: Optional[str], user) -> Optional[EvoSenseProperty]:
    """A person settles an ambiguous identity: merge into a candidate, or new."""
    from app.services.evosense import providers as PV
    obs = db.query(EvoSenseObservation).filter(
        EvoSenseObservation.id == review.observation_id,
        EvoSenseObservation.organization_id == org_id).first()
    rec = C.jload(obs.payload, {}) if obs else {}
    provider = PV.PROVIDERS.get(obs.provider_key) if obs else None
    prop = None
    if action == "merge":
        cands = C.jload(review.candidate_property_ids, []) or []
        if property_id not in cands:
            raise ValueError("Choose one of the candidate properties.")
        prop = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == property_id,
                                                 EvoSenseProperty.organization_id == org_id).first()
    elif action == "new":
        prop = EvoSenseProperty(organization_id=org_id, status=C.S_NEW,
                                first_strategy_id=obs.strategy_id if obs else None,
                                is_test=bool(obs.is_test) if obs else False)
        db.add(prop)
        db.flush()
    elif action != "dismiss":
        raise ValueError("action must be merge, new or dismiss")
    if prop is not None and obs is not None and provider is not None:
        attach_to(db, prop, provider, obs.capability, rec, obs,
                  source_rank(provider, obs.capability), user=user)
        obs.property_id = prop.id
    review.status = {"merge": "merged", "new": "new", "dismiss": "dismissed"}[action]
    review.resolved_property_id = prop.id if prop else None
    review.resolved_by_id = getattr(user, "id", None)
    review.resolved_at = C.now()
    db.flush()
    for pid in C.jload(review.candidate_property_ids, []) or []:
        open_left = (db.query(EvoSenseIdentityReview)
                     .filter(EvoSenseIdentityReview.organization_id == org_id,
                             EvoSenseIdentityReview.status == "open",
                             EvoSenseIdentityReview.candidate_property_ids.like('%%"%s"%%' % pid)
                             ).count())
        if not open_left:
            p = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == pid).first()
            if p is not None:
                p.identity_status = "resolved"
    C.log_event(db, org_id, "identity.resolved", property_id=prop.id if prop else None,
                user=user, actor_type=C.ACTOR_USER, summary="Identity review %s" % review.status)
    return prop
