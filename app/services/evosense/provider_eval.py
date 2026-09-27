"""THE PROVIDER EVALUATION HARNESS - the same authorized sample through each
contact provider, compared on the same metrics.

    match rate              lookups that returned any contact
    owner match accuracy    returned contacts that match the KNOWN truth
                            (only when the sample supplies ground truth)
    phone / mobile / email  coverage
    validation quality      phones returned with a line type
    false-positive rate     returned numbers already known wrong in this
                            workspace (wrong party, opted out, invalid) or
                            contradicting the ground truth
    freshness               median age of the vendor's "last seen"
    latency                 median milliseconds per lookup
    cost per lookup / per usable contact / per verified contact
    provider failures

SYNTHETIC vs REAL. A run that touched a sandbox adapter is SYNTHETIC: it
proves the software counts correctly and nothing about a vendor. Each
provider's block carries `synthetic`, and the run carries it overall.

MONEY. Every lookup goes through the workspace's budget (reserve -> charge /
refund) and the cost ledger, attributed to the property, exactly like a real
enrichment. A REAL (non-sandbox) provider is refused unless the caller passes
the owner's explicit confirmation - running paid lookups is an owner decision.

Results stay here: nothing a provider returns during an evaluation becomes a
contact point, so no evaluation can put an unvetted number in front of anyone.
"""
from __future__ import annotations

import time
from datetime import date, datetime
from statistics import median
from typing import Any, Dict, List, Optional

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseProperty,
                                        EvoSenseProviderEvaluation)
from app.services.evosense import budget as B
from app.services.evosense import common as C
from app.services.evosense import contacts as CT
from app.services.evosense import enrichment as EN
from app.services.evosense import providers as PV

MAX_SAMPLE = 200
PAID_CONFIRMATION = "RUN PAID EVALUATION"


class EvaluationRefused(ValueError):
    pass


def _digits(v) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())[-10:]


def run(db, org_id: str, *, name: str, provider_keys: List[str], property_ids: List[str],
        ground_truth: Optional[Dict[str, Dict[str, Any]]] = None, user=None,
        confirm: Optional[str] = None) -> EvoSenseProviderEvaluation:
    """Run the evaluation. `ground_truth`: {property_id: {"phones": [...],
    "emails": [...]}} for records whose real contact is known (e.g. the
    workspace's own closed deals). Raises EvaluationRefused with a sentence."""
    if not provider_keys or not property_ids:
        raise EvaluationRefused("Choose at least one provider and one property.")
    if len(property_ids) > MAX_SAMPLE:
        raise EvaluationRefused("An evaluation sample is capped at %s properties." % MAX_SAMPLE)
    providers = []
    for key in provider_keys:
        p = PV.PROVIDERS.get(key)
        if p is None or C.CONTACT_ENRICHMENT not in (p.capabilities or ()):
            raise EvaluationRefused("%s is not a contact-enrichment provider." % key)
        if p.connector_kind not in (C.SANDBOX, C.REAL):
            raise EvaluationRefused("%s cannot be called (%s)." % (p.label, p.connector_kind))
        cfg = PV.config(db, org_id, key)
        canon = PV.canonical(p, cfg)
        if not canon.get("routable"):
            raise EvaluationRefused("%s is not usable for this workspace: %s" % (p.label, canon.get("why")))
        providers.append((p, cfg))
    real = [p for p, _ in providers if p.connector_kind != C.SANDBOX]
    if real and (confirm or "").strip() != "%s %s" % (PAID_CONFIRMATION, len(property_ids) * len(real)):
        raise EvaluationRefused(
            "Real providers cost money per lookup. Running one is the owner's decision: confirm with "
            "\"%s %s\"." % (PAID_CONFIRMATION, len(property_ids) * len(real)))

    props = (db.query(EvoSenseProperty)
             .filter(EvoSenseProperty.organization_id == org_id,
                     EvoSenseProperty.id.in_(property_ids)).all())
    if len(props) != len(set(property_ids)):
        raise EvaluationRefused("Every sample property must belong to this workspace.")
    for p, _ in providers:
        if p.connector_kind == C.SANDBOX and any(not pr.is_test for pr in props):
            raise EvaluationRefused("Sandbox adapters only ever run on sandbox (test) properties.")

    ev = EvoSenseProviderEvaluation(
        organization_id=org_id, name=(name or "Provider evaluation")[:120],
        provider_keys=C.jdump(provider_keys),
        sample=C.jdump({"property_ids": property_ids, "ground_truth": ground_truth or {}}),
        synthetic=not real, created_by_id=getattr(user, "id", None))
    db.add(ev)
    db.flush()

    known_bad = {(c.kind, c.value) for c in db.query(EvoSenseContactPoint).filter(
        EvoSenseContactPoint.organization_id == org_id,
        EvoSenseContactPoint.status.in_(("wrong_party", "opted_out", "invalid")))}
    out: Dict[str, Any] = {}
    total_cost = 0
    for p, cfg in providers:
        rows, lat, ages = [], [], []
        for prop in props:
            owner = CT.primary_owner(db, prop)
            cost = p.cost(C.CONTACT_ENRICHMENT, C.jload(cfg.cost_overrides, {}) or {}) or 0
            ok, why, entry = B.reserve(db, org_id, None, cost, provider_key=p.key,
                                       connector_kind=p.connector_kind,
                                       capability=C.CONTACT_ENRICHMENT, operation="provider_evaluation",
                                       property_id=prop.id, owner_id=getattr(owner, "id", None),
                                       is_test=bool(prop.is_test))
            if not ok:
                rows.append({"property_id": prop.id, "result": "not_attempted", "reason": why})
                continue
            t0 = time.monotonic()
            try:
                res = p.enrich(EN._input_for(prop, owner))
            except Exception as exc:  # noqa: BLE001 - a failure is a result, and refunded
                B.refund(db, entry)
                PV.record_failure(cfg, "%s: %s" % (type(exc).__name__, str(exc)[:120]),
                                  capability=C.CONTACT_ENRICHMENT)
                rows.append({"property_id": prop.id, "result": "failed",
                             "error": type(exc).__name__, "ms": int((time.monotonic() - t0) * 1000)})
                continue
            ms = int((time.monotonic() - t0) * 1000)
            lat.append(ms)
            PV.record_success(cfg, capability=C.CONTACT_ENRICHMENT)
            phones = [{"number": _digits(ph.number), "type": ph.phone_type,
                       "last_seen": getattr(ph, "last_seen", None)} for ph in (res.phones or [])]
            emails = [(e if isinstance(e, str) else e.address or "").strip().lower()
                      for e in (res.emails or [])]
            matched = bool(phones or emails)
            B.charge(db, entry, success=matched)
            total_cost += entry.total_cents or 0
            for ph in phones:
                if ph["last_seen"]:
                    try:
                        ages.append((date.today() - date.fromisoformat(str(ph["last_seen"])[:10])).days)
                    except ValueError:
                        pass
            truth = (ground_truth or {}).get(prop.id) or {}
            t_phones = {_digits(x) for x in truth.get("phones", [])}
            t_emails = {str(x).strip().lower() for x in truth.get("emails", [])}
            verified = [ph["number"] for ph in phones if ph["number"] in t_phones] + \
                [e for e in emails if e in t_emails]
            false_pos = [ph["number"] for ph in phones
                         if ("phone", "+1" + ph["number"]) in known_bad] + \
                [e for e in emails if ("email", e) in known_bad]
            if truth and (t_phones or t_emails):
                false_pos += [ph["number"] for ph in phones if t_phones and ph["number"] not in t_phones]
            rows.append({"property_id": prop.id, "result": "match" if matched else "no_match",
                         "phones": len(phones), "mobile": sum(1 for x in phones if x["type"] == "mobile"),
                         "typed": sum(1 for x in phones if x["type"] and x["type"] != "unknown"),
                         "emails": len(emails), "verified": len(verified),
                         "false_positives": len(set(false_pos)), "has_truth": bool(truth),
                         "ms": ms, "cost_cents": entry.total_cents or 0})
        out[p.key] = _metrics(p, rows, lat, ages)
    ev.results = C.jdump({"providers": out, "sample_size": len(props),
                          "label": "SYNTHETIC TEST RESULTS - software behaviour only"
                          if ev.synthetic else "REAL PROVIDER RESULTS"})
    ev.total_cost_cents = total_cost
    ev.status = "completed"
    ev.finished_at = datetime.utcnow()
    C.log_event(db, org_id, "provider_evaluation.completed", user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION, is_test=ev.synthetic,
                summary="Provider evaluation '%s' (%s): %s providers x %s properties, %s"
                % (ev.name, "SYNTHETIC" if ev.synthetic else "REAL", len(providers), len(props),
                   C.money(total_cost)))
    db.flush()
    return ev


def _metrics(p, rows, lat, ages) -> Dict[str, Any]:
    attempted = [r for r in rows if r["result"] in ("match", "no_match")]
    n = len(attempted) or 1
    matched = [r for r in attempted if r["result"] == "match"]
    phones = sum(r["phones"] for r in attempted)
    usable = sum(1 for r in attempted if r["mobile"] or r["emails"])
    verified = sum(r["verified"] for r in attempted)
    truthy = [r for r in attempted if r["has_truth"]]
    cost = sum(r["cost_cents"] for r in attempted)
    returned = phones + sum(r["emails"] for r in attempted)

    def ratio(a, b):
        return round(a / b, 3) if b else None
    return {
        "provider": p.key, "label": p.label, "connector_kind": p.connector_kind,
        "synthetic": p.connector_kind == C.SANDBOX,
        "attempted": len(attempted), "failures": len([r for r in rows if r["result"] == "failed"]),
        "not_attempted": len([r for r in rows if r["result"] == "not_attempted"]),
        "match_rate": ratio(len(matched), n),
        "owner_match_accuracy": ratio(sum(1 for r in truthy if r["verified"]), len(truthy)),
        "phone_coverage": ratio(sum(1 for r in attempted if r["phones"]), n),
        "mobile_coverage": ratio(sum(1 for r in attempted if r["mobile"]), n),
        "email_coverage": ratio(sum(1 for r in attempted if r["emails"]), n),
        "validation_quality": ratio(sum(r["typed"] for r in attempted), phones),
        "false_positive_rate": ratio(sum(r["false_positives"] for r in attempted), returned),
        "median_last_seen_days": median(ages) if ages else None,
        "median_latency_ms": median(lat) if lat else None,
        "cost_cents": cost,
        "cost_per_lookup_cents": ratio(cost, len(attempted)),
        "cost_per_usable_contact_cents": ratio(cost, usable),
        "cost_per_verified_contact_cents": ratio(cost, verified),
        "rows": rows,
    }


def payload(ev: EvoSenseProviderEvaluation) -> Dict[str, Any]:
    return {"id": ev.id, "name": ev.name, "status": ev.status, "synthetic": bool(ev.synthetic),
            "label": "SYNTHETIC" if ev.synthetic else "REAL",
            "provider_keys": C.jload(ev.provider_keys, []), "results": C.jload(ev.results, None),
            "total_cost_cents": ev.total_cost_cents,
            "created_at": ev.created_at.isoformat() + "Z" if ev.created_at else None,
            "finished_at": ev.finished_at.isoformat() + "Z" if ev.finished_at else None}
