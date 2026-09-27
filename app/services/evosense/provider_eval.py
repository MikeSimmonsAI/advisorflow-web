"""THE PROVIDER EVALUATION HARNESS - the same authorized sample through each
provider, compared on the same metrics, and nothing else.

TWO MODES
  contact   CONTACT_ENRICHMENT providers (skip trace), optionally refereed
            by an independent PHONE_VALIDATION provider (line type):
              match rate, correct-owner rate (ground truth), owner-name
              agreement with the owner of record (a proxy, labelled so),
              phone / mobile / email coverage, line-type completeness and
              agreement with the referee, freshness, false positives,
              latency, failures, cost per lookup / matched owner / usable
              contact / verified contact
  comps     COMPS providers: each subject's comps go through the workspace's
            comp_eligibility rules and the ARV engine exactly as a deal's
            would - IN MEMORY. Captures closed-price availability by PRICE
            SOURCE (MLS closed / public record / unverified record /
            estimated / list / AVM), close dates, fact completeness,
            eligible comps per subject and the ARV confidence that WOULD
            result. Nothing is written to any deal.

ISOLATION (the rules this file exists to keep)
  * Nothing a provider returns becomes a contact point, a comp, a lead or a
    deal value. Returned values live only in this evaluation's record,
    labelled REAL PROVIDER EVALUATION DATA (or SYNTHETIC for sandbox runs).
  * Nothing here sends anything: no SMS, email or call. Twilio Lookup is a
    lookup of line type, never a message.
  * Results never touch contactability, scores, statuses or outreach.
  * `purge()` deletes the returned values (and any ground truth supplied)
    and keeps only the aggregate metrics.

MONEY
  Every call goes through the workspace budget (reserve -> settle to what the
  vendor actually billed, misses free where the vendor says so) and the cost
  ledger, attributed to the property. A run that includes a REAL provider is
  refused unless the owner types the exact confirmation:
      RUN PAID EVALUATION <n>
  where n = the maximum number of paid calls the run may make.

SYNTHETIC vs REAL: a run whose providers are all sandbox adapters is
SYNTHETIC - it proves the software counts correctly and nothing about a
vendor. Sandbox adapters never run on real properties.
"""
from __future__ import annotations

import time
from datetime import date, datetime
from statistics import median
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseProperty,
                                        EvoSenseProviderEvaluation)
from app.services import arv_engine
from app.services import comp_rules as CR
from app.services.evosense import budget as B
from app.services.evosense import common as C
from app.services.evosense import contacts as CT
from app.services.evosense import enrichment as EN
from app.services.evosense import providers as PV
from app.services.evosense.vendors import name_agreement

MAX_SAMPLE = 200
PAID_CONFIRMATION = "RUN PAID EVALUATION"
REFEREE_MAX_PER_LOOKUP = 3            # numbers per provider per property sent to the referee
MODE_CONTACT, MODE_COMPS = "contact", "comps"
REAL_LABEL = "REAL PROVIDER EVALUATION DATA - isolated; never a contact, comp or deal value"
SYNTHETIC_LABEL = "SYNTHETIC TEST RESULTS - software behaviour only"


class EvaluationRefused(ValueError):
    pass


def _digits(v) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())[-10:]


def _paid(p) -> bool:
    return p.connector_kind != C.SANDBOX


def _check_provider(db, org_id, key, capability):
    p = PV.PROVIDERS.get(key)
    if p is None or capability not in (p.capabilities or ()):
        raise EvaluationRefused("%s cannot supply %s." % (key, capability.replace("_", " ").lower()))
    if p.connector_kind not in (C.SANDBOX, C.REAL):
        raise EvaluationRefused("%s cannot be called (%s)." % (p.label, p.connector_kind))
    cfg = PV.config(db, org_id, key)
    canon = PV.canonical(p, cfg, blocked=PV.platform_blocked(db, key, cfg))
    if not canon.get("eval_routable"):
        raise EvaluationRefused("%s is not usable for this workspace: %s" % (p.label, canon.get("why")))
    return p, cfg


def required_confirmation(n_props: int, providers, referee=None) -> Optional[str]:
    n = n_props * len([p for p, _ in providers if _paid(p)])
    if referee is not None and _paid(referee[0]):
        n += n_props * len(providers) * REFEREE_MAX_PER_LOOKUP
    return "%s %s" % (PAID_CONFIRMATION, n) if n else None


def run(db, org_id: str, *, name: str, provider_keys: List[str], property_ids: List[str],
        mode: str = MODE_CONTACT, ground_truth: Optional[Dict[str, Dict[str, Any]]] = None,
        referee_key: Optional[str] = None, user=None,
        confirm: Optional[str] = None) -> EvoSenseProviderEvaluation:
    """Run one evaluation. `ground_truth` (contact mode): {property_id:
    {"phones": [...], "emails": [...]}} for records whose real contact is
    lawfully known. Raises EvaluationRefused with a sentence."""
    if mode not in (MODE_CONTACT, MODE_COMPS):
        raise EvaluationRefused("Mode must be contact or comps.")
    if not provider_keys or not property_ids:
        raise EvaluationRefused("Choose at least one provider and one property.")
    if len(set(property_ids)) > MAX_SAMPLE:
        raise EvaluationRefused("An evaluation sample is capped at %s properties." % MAX_SAMPLE)
    cap = C.CONTACT_ENRICHMENT if mode == MODE_CONTACT else C.COMPS
    providers = [_check_provider(db, org_id, k, cap) for k in dict.fromkeys(provider_keys)]
    referee = None
    if referee_key:
        if mode != MODE_CONTACT:
            raise EvaluationRefused("A line-type referee applies to contact evaluations only.")
        referee = _check_provider(db, org_id, referee_key, C.PHONE_VALIDATION)

    props = (db.query(EvoSenseProperty)
             .filter(EvoSenseProperty.organization_id == org_id,
                     EvoSenseProperty.id.in_(property_ids)).all())
    if len(props) != len(set(property_ids)):
        raise EvaluationRefused("Every sample property must belong to this workspace.")
    for p, _ in providers + ([referee] if referee else []):
        if p.connector_kind == C.SANDBOX and any(not pr.is_test for pr in props):
            raise EvaluationRefused("Sandbox adapters only ever run on sandbox (test) properties.")

    needed = required_confirmation(len(props), providers, referee)
    if needed and (confirm or "").strip() != needed:
        raise EvaluationRefused(
            "Real providers cost money per call. Running one is the owner's decision: confirm with "
            "\"%s\" (the most paid calls this run can make)." % needed)

    synthetic = not needed
    ev = EvoSenseProviderEvaluation(
        organization_id=org_id, name=(name or "Provider evaluation")[:120],
        provider_keys=C.jdump(list(dict.fromkeys(provider_keys))),
        sample=C.jdump({"mode": mode, "property_ids": [pr.id for pr in props],
                        "ground_truth": ground_truth or {}, "referee": referee_key}),
        synthetic=synthetic, created_by_id=getattr(user, "id", None))
    db.add(ev)
    db.flush()

    if mode == MODE_CONTACT:
        out, records, total = _run_contact(db, org_id, providers, props, ground_truth or {}, referee)
    else:
        out, records, total = _run_comps(db, org_id, providers, props)
    ev.results = C.jdump({"mode": mode, "providers": out, "sample_size": len(props),
                          "label": SYNTHETIC_LABEL if synthetic else REAL_LABEL,
                          "referee": referee_key, "records": records, "purged_at": None})
    ev.total_cost_cents = total
    ev.status = "completed"
    ev.finished_at = datetime.utcnow()
    C.log_event(db, org_id, "provider_evaluation.completed", user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION, is_test=synthetic,
                summary="Provider evaluation '%s' (%s, %s): %s provider(s) x %s properties, %s"
                % (ev.name, mode, "SYNTHETIC" if synthetic else "REAL", len(providers), len(props),
                   C.money(total)))
    db.flush()
    return ev


def _reserve(db, org_id, p, cfg, capability, prop, owner=None):
    cost = p.cost(capability, C.jload(cfg.cost_overrides, {}) or {}) or 0
    return B.reserve(db, org_id, None, cost, provider_key=p.key, connector_kind=p.connector_kind,
                     capability=capability, operation="provider_evaluation", property_id=prop.id,
                     owner_id=getattr(owner, "id", None), is_test=bool(prop.is_test))


def _settle(db, entry, res_billable, res_cost, success):
    """Charge what the vendor billed: nothing for a free miss; the vendor's
    stated cost when it gave one (never more than was reserved)."""
    if not res_billable:
        B.settle(db, entry, 0, success=success)
    elif res_cost is not None and res_cost <= (entry.total_cents or 0):
        B.settle(db, entry, res_cost, success=success)
    else:
        B.charge(db, entry, success=success)
    return entry.total_cents or 0


# ── contact mode ──────────────────────────────────────────────────────────

def _run_contact(db, org_id, providers, props, truth_all, referee):
    known_bad = {(c.kind, c.value) for c in db.query(EvoSenseContactPoint).filter(
        EvoSenseContactPoint.organization_id == org_id,
        EvoSenseContactPoint.status.in_(("wrong_party", "opted_out", "invalid")))}
    out: Dict[str, Any] = {}
    records: List[Dict[str, Any]] = []
    total = 0
    referee_cache: Dict[str, Optional[str]] = {}
    for p, cfg in providers:
        rows, lat, ages = [], [], []
        for prop in props:
            owner = CT.primary_owner(db, prop)
            ok, why, entry = _reserve(db, org_id, p, cfg, C.CONTACT_ENRICHMENT, prop, owner)
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
                rows.append({"property_id": prop.id, "result": "failed", "error": type(exc).__name__,
                             "ms": int((time.monotonic() - t0) * 1000), "cost_cents": 0})
                continue
            ms = int((time.monotonic() - t0) * 1000)
            lat.append(ms)
            PV.record_success(cfg, capability=C.CONTACT_ENRICHMENT)
            phones = [{"number": _digits(ph.number), "type": ph.phone_type,
                       "last_seen": getattr(ph, "last_seen", None), "dnc": getattr(ph, "dnc_flag", None),
                       "ref": getattr(ph, "provider_reference", None)} for ph in (res.phones or [])
                      if _digits(ph.number)]
            emails = [(e if isinstance(e, str) else e.address or "").strip().lower() for e in (res.emails or [])]
            emails = [e for e in emails if e]
            matched = bool(phones or emails)
            cost = _settle(db, entry, getattr(res, "billable", True), getattr(res, "cost_cents", None), matched)
            total += cost
            for ph in phones:
                if ph["last_seen"]:
                    try:
                        ages.append((date.today() - date.fromisoformat(str(ph["last_seen"])[:10])).days)
                    except ValueError:
                        pass
            owner_name = getattr(owner, "display_name", None)
            agree = name_agreement(owner_name, getattr(res, "owner_name", None)) if matched else None
            # Independent line-type referee (lookup only - sends nothing).
            if referee is not None and phones:
                for ph in phones[:REFEREE_MAX_PER_LOOKUP]:
                    ph["referee_type"], spent = _referee(db, org_id, referee, prop, ph["number"],
                                                         referee_cache)
                    total += spent
                    if ph["type"] and ph["type"] != "unknown" and ph["referee_type"]:
                        ph["referee_agrees"] = _same_line(ph["type"], ph["referee_type"])
            truth = truth_all.get(prop.id) or {}
            t_phones = {_digits(x) for x in truth.get("phones", [])}
            t_emails = {str(x).strip().lower() for x in truth.get("emails", [])}
            verified = [ph["number"] for ph in phones if ph["number"] in t_phones] + \
                [e for e in emails if e in t_emails]
            false_pos = [ph["number"] for ph in phones if ("phone", "+1" + ph["number"]) in known_bad] + \
                [e for e in emails if ("email", e) in known_bad]
            if t_phones:
                false_pos += [ph["number"] for ph in phones if ph["number"] not in t_phones]
            rows.append({"property_id": prop.id, "result": "match" if matched else "no_match",
                         "phones": len(phones), "mobile": sum(1 for x in phones if x["type"] == "mobile"),
                         "typed": sum(1 for x in phones if x["type"] and x["type"] != "unknown"),
                         "emails": len(emails), "verified": len(verified),
                         "false_positives": len(set(false_pos)), "has_truth": bool(t_phones or t_emails),
                         "name_match": agree,
                         "referee_checked": sum(1 for x in phones if x.get("referee_type")),
                         "referee_agree": sum(1 for x in phones if x.get("referee_agrees") is True),
                         "referee_comparable": sum(1 for x in phones if "referee_agrees" in x),
                         "ms": ms, "cost_cents": cost})
            records.append({"provider": p.key, "property_id": prop.id,
                            "owner_of_record": owner_name, "vendor_name": getattr(res, "owner_name", None),
                            "name_match": agree, "phones": phones, "emails": emails,
                            "provider_reference": getattr(res, "provider_reference", None),
                            "looked_up_at": getattr(res, "looked_up_at", None), "cost_cents": cost})
        out[p.key] = _contact_metrics(p, rows, lat, ages)
    return out, records, total


def _same_line(a: str, b: str) -> bool:
    norm = lambda x: "voip" if "voip" in (x or "") else (x or "")  # noqa: E731
    return norm(a) == norm(b)


def _referee(db, org_id, referee, prop, number, cache):
    """(line type, cents charged by THIS call). A number already checked in
    this run is not paid for twice."""
    if number in cache:
        return cache[number], 0
    p, cfg = referee
    ok, why, entry = _reserve(db, org_id, p, cfg, C.PHONE_VALIDATION, prop)
    if not ok:
        cache[number] = None
        return None, 0
    try:
        r = p.validate_phone(number) or {}
    except Exception as exc:  # noqa: BLE001
        B.refund(db, entry)
        PV.record_failure(cfg, "%s: %s" % (type(exc).__name__, str(exc)[:120]), capability=C.PHONE_VALIDATION)
        cache[number] = None
        return None, 0
    B.charge(db, entry, success=True)
    PV.record_success(cfg, capability=C.PHONE_VALIDATION)
    cache[number] = r.get("line_type")
    return cache[number], entry.total_cents or 0


def _ratio(a, b):
    return round(a / b, 3) if b else None


def _contact_metrics(p, rows, lat, ages) -> Dict[str, Any]:
    attempted = [r for r in rows if r["result"] in ("match", "no_match")]
    n = len(attempted) or 1
    matched = [r for r in attempted if r["result"] == "match"]
    phones = sum(r["phones"] for r in attempted)
    usable = sum(1 for r in attempted if r["mobile"] or r["emails"])
    verified = sum(r["verified"] for r in attempted)
    truthy = [r for r in attempted if r["has_truth"]]
    named = [r for r in matched if r["name_match"] is not None]
    owner_ok = [r for r in named if r["name_match"] in ("full", "surname")]
    cost = sum(r["cost_cents"] for r in rows)
    returned = phones + sum(r["emails"] for r in attempted)
    comparable = sum(r["referee_comparable"] for r in attempted)
    return {
        "provider": p.key, "label": p.label, "connector_kind": p.connector_kind,
        "synthetic": p.connector_kind == C.SANDBOX, "evaluation_only": bool(getattr(p, "evaluation_only", False)),
        "price_confirmed": getattr(p, "price_confirmed", None), "price_note": getattr(p, "price_note", None),
        "attempted": len(attempted), "failures": len([r for r in rows if r["result"] == "failed"]),
        "not_attempted": len([r for r in rows if r["result"] == "not_attempted"]),
        "match_rate": _ratio(len(matched), n),
        "owner_match_accuracy": _ratio(sum(1 for r in truthy if r["verified"]), len(truthy)),
        "correct_owner_rate_truth": _ratio(sum(1 for r in truthy if r["verified"]), len(truthy)),
        "owner_name_agreement_rate": _ratio(len(owner_ok), len(named)),
        "owner_name_agreement_note": "Proxy: vendor's person name vs the owner of record - evidence, not proof",
        "phone_coverage": _ratio(sum(1 for r in attempted if r["phones"]), n),
        "mobile_coverage": _ratio(sum(1 for r in attempted if r["mobile"]), n),
        "email_coverage": _ratio(sum(1 for r in attempted if r["emails"]), n),
        "validation_quality": _ratio(sum(r["typed"] for r in attempted), phones),
        "line_type_completeness": _ratio(sum(r["typed"] for r in attempted), phones),
        "line_type_agreement": _ratio(sum(r["referee_agree"] for r in attempted), comparable),
        "line_type_compared": comparable,
        "false_positive_rate": _ratio(sum(r["false_positives"] for r in attempted), returned),
        "median_last_seen_days": median(ages) if ages else None,
        "median_latency_ms": median(lat) if lat else None,
        "cost_cents": cost,
        "cost_per_lookup_cents": _ratio(cost, len(attempted)),
        "cost_per_matched_owner_cents": _ratio(cost, len(owner_ok) if named else len(matched)),
        "cost_per_usable_contact_cents": _ratio(cost, usable),
        "cost_per_verified_contact_cents": _ratio(cost, verified),
        "rows": rows,
    }


# ── comps mode ────────────────────────────────────────────────────────────

def _subject(prop) -> Dict[str, Any]:
    f = lambda v: float(v) if v is not None else None  # noqa: E731
    return {"street_address": prop.street_address, "city": prop.city, "state": prop.state,
            "zip_code": prop.zip_code, "county": prop.county, "square_feet": prop.square_feet,
            "bedrooms": f(prop.bedrooms), "bathrooms": f(prop.bathrooms), "year_built": prop.year_built,
            "property_type": prop.property_type, "latitude": f(getattr(prop, "latitude", None)),
            "longitude": f(getattr(prop, "longitude", None)), "lot_size_sqft": None}


FACTS = ("square_feet", "bedrooms", "bathrooms", "year_built", "property_type")


def _run_comps(db, org_id, providers, props):
    from app.services import wholesale_sms as WS
    rules = CR.rules_for(WS.settings_row(db, org_id))
    out: Dict[str, Any] = {}
    records: List[Dict[str, Any]] = []
    total = 0
    for p, cfg in providers:
        rows, lat = [], []
        for prop in props:
            ok, why, entry = _reserve(db, org_id, p, cfg, C.COMPS, prop)
            if not ok:
                rows.append({"property_id": prop.id, "result": "not_attempted", "reason": why})
                continue
            subject = _subject(prop)
            t0 = time.monotonic()
            try:
                comps = p.search(C.COMPS, subject) or []
            except Exception as exc:  # noqa: BLE001
                B.refund(db, entry)
                PV.record_failure(cfg, "%s: %s" % (type(exc).__name__, str(exc)[:120]), capability=C.COMPS)
                rows.append({"property_id": prop.id, "result": "failed", "error": type(exc).__name__,
                             "ms": int((time.monotonic() - t0) * 1000), "cost_cents": 0})
                continue
            ms = int((time.monotonic() - t0) * 1000)
            lat.append(ms)
            PV.record_success(cfg, capability=C.COMPS)
            B.charge(db, entry, success=bool(comps))
            cost = entry.total_cents or 0
            total += cost
            objs = [SimpleNamespace(
                id="%s:%s" % (p.key, i), street_address=c.get("street_address"),
                sale_price=c.get("sale_price"), sale_date=c.get("sale_date"),
                price_source=c.get("price_source"), provider_key=p.key,
                source_reference=str(c.get("provider_record_id") or "%s record" % p.key),
                square_feet=c.get("square_feet"), bedrooms=c.get("bedrooms"), bathrooms=c.get("bathrooms"),
                year_built=c.get("year_built"), lot_size_sqft=c.get("lot_size_sqft"),
                property_type=c.get("property_type"), latitude=c.get("latitude"),
                longitude=c.get("longitude"), distance_miles=c.get("distance_miles"),
                sale_type=c.get("sale_type"), included=True, exclusion_reason=None, half_baths=None,
                verification_state="provider", created_at=str(i)) for i, c in enumerate(comps)]
            engine = arv_engine.compute(SimpleNamespace(**subject), objs, rules)
            by_source: Dict[str, int] = {}
            for c in comps:
                by_source[c.get("price_source") or "NONE"] = by_source.get(c.get("price_source") or "NONE", 0) + 1
            closed = [c for c in comps if c.get("price_source") in CR.CLOSED_PRICE_SOURCES
                      and c.get("sale_price") and c.get("sale_date")]
            ages = []
            for c in closed:
                try:
                    ages.append((date.today() - date.fromisoformat(str(c["sale_date"])[:10])).days)
                except ValueError:
                    pass
            complete = sum(1 for c in comps if all(c.get(k) is not None for k in FACTS)
                           and (c.get("distance_miles") is not None or c.get("latitude") is not None))
            rows.append({"property_id": prop.id, "result": "returned" if comps else "none",
                         "returned": len(comps), "by_source": by_source, "closed_price": len(closed),
                         "median_close_age_days": median(ages) if ages else None,
                         "facts_complete": complete, "eligible": len(engine["comps_used"]),
                         "arv_status": engine["status"], "arv_value": engine.get("value"),
                         "arv_confidence": (engine.get("confidence") or {}).get("label"),
                         "ms": ms, "cost_cents": cost})
            records.append({"provider": p.key, "property_id": prop.id,
                            "subject": subject.get("street_address"),
                            "comps": [{k: c.get(k) for k in ("street_address", "sale_price", "sale_date",
                                                             "price_source", "distance_miles",
                                                             "provider_record_id")} for c in comps],
                            "would_be_arv": {"status": engine["status"], "value": engine.get("value"),
                                             "confidence": (engine.get("confidence") or {}).get("label"),
                                             "excluded": [{"address": x["address"],
                                                           "why": [e["code"] for e in x["excluded"]]}
                                                          for x in engine["comps_excluded"]]},
                            "cost_cents": cost})
        out[p.key] = _comps_metrics(p, rows, lat)
    return out, records, total


def _comps_metrics(p, rows, lat) -> Dict[str, Any]:
    done = [r for r in rows if r["result"] in ("returned", "none")]
    n = len(done) or 1
    returned = sum(r["returned"] for r in done)
    by_source: Dict[str, int] = {}
    for r in done:
        for k, v in r["by_source"].items():
            by_source[k] = by_source.get(k, 0) + v
    closed = sum(r["closed_price"] for r in done)
    ages = [r["median_close_age_days"] for r in done if r["median_close_age_days"] is not None]
    conf: Dict[str, int] = {}
    for r in done:
        key = r["arv_confidence"] or "insufficient"
        conf[key] = conf.get(key, 0) + 1
    cost = sum(r["cost_cents"] for r in rows)
    eligible = sum(r["eligible"] for r in done)
    origin_confirmed = bool(getattr(p, "closed_price_origin_confirmed", False))
    return {
        "provider": p.key, "label": p.label, "connector_kind": p.connector_kind,
        "synthetic": p.connector_kind == C.SANDBOX, "evaluation_only": bool(getattr(p, "evaluation_only", False)),
        "price_confirmed": getattr(p, "price_confirmed", None), "price_note": getattr(p, "price_note", None),
        "subjects": len(done), "failures": len([r for r in rows if r["result"] == "failed"]),
        "not_attempted": len([r for r in rows if r["result"] == "not_attempted"]),
        "comps_returned": returned, "comps_by_price_source": by_source,
        "closed_price_share": _ratio(closed, returned),
        "mls_closed_share": _ratio(by_source.get(CR.PRICE_MLS_CLOSED, 0), returned),
        "median_close_age_days": median(ages) if ages else None,
        "facts_complete_share": _ratio(sum(r["facts_complete"] for r in done), returned),
        "eligible_per_subject": _ratio(eligible, n),
        "subjects_with_3_eligible": _ratio(sum(1 for r in done if r["eligible"] >= 3), n),
        "would_be_arv_confidence": conf,
        "median_latency_ms": median(lat) if lat else None,
        "cost_cents": cost, "cost_per_subject_cents": _ratio(cost, len(done)),
        "cost_per_eligible_comp_cents": _ratio(cost, eligible),
        "closed_price_origin_confirmed": origin_confirmed,
        "production_ready": False,
        "readiness_note": ("Closed-price origin for Texas NOT established in writing: this provider "
                           "cannot unlock production ARV, however good these numbers look."
                           if not origin_confirmed else
                           "Origin confirmed; production use still needs Mike's approval."),
        "rows": rows,
    }


# ── sample, payload, purge ────────────────────────────────────────────────

def proposed_sample(db, org_id: str, mode: str = MODE_CONTACT, limit: int = 50) -> Dict[str, Any]:
    """The authorized sample the harness proposes: REAL properties this
    workspace is already researching. Contact mode: owners awaiting contact
    data (individual owners only - an entity is not skip-traced). Comps
    mode: properties with enough facts to compare."""
    q = (db.query(EvoSenseProperty)
         .filter(EvoSenseProperty.organization_id == org_id, EvoSenseProperty.is_test.isnot(True),
                 EvoSenseProperty.archived_at.is_(None)))
    items, left_out = [], []
    if mode == MODE_CONTACT:
        q = q.filter(EvoSenseProperty.contactability == "ENRICHMENT_NEEDED")
        for prop in q.order_by(EvoSenseProperty.opportunity_score.desc()).limit(limit * 3).all():
            owner = CT.primary_owner(db, prop)
            why = None
            if owner is None:
                why = "no owner of record"
            elif (owner.owner_type or "individual") not in ("individual", "unknown"):
                why = "entity owner (%s) - not skip-traced" % owner.owner_type
            elif not (prop.street_address and prop.city and prop.state):
                # Skip-trace vendors match on street + city + state; a lookup
                # without a city would be a guaranteed miss that skews the rates.
                why = "address incomplete (no city)" if not prop.city else "address incomplete"
            if why:
                left_out.append({"property_id": prop.id, "address": prop.street_address, "why": why})
                continue
            items.append({"property_id": prop.id, "address": prop.street_address, "city": prop.city,
                          "owner_of_record": owner.display_name, "score": prop.opportunity_score})
            if len(items) >= limit:
                break
    else:
        q = q.filter(EvoSenseProperty.square_feet.isnot(None))
        for prop in q.order_by(EvoSenseProperty.opportunity_score.desc()).limit(limit).all():
            items.append({"property_id": prop.id, "address": prop.street_address, "city": prop.city,
                          "county": prop.county, "square_feet": prop.square_feet})
    return {"mode": mode, "items": items, "count": len(items), "left_out": left_out,
            "note": "Real properties this workspace already researches. Every lookup is research on a "
                    "property owner; nobody is contacted and nothing is written to the contact graph."}


def purge(db, ev: EvoSenseProviderEvaluation, user=None) -> None:
    """Delete the returned values (contacts, comps, ground truth); keep the
    aggregate metrics. Irreversible by design - the point is deletion."""
    res = C.jload(ev.results, {}) or {}
    res["records"] = []
    for block in (res.get("providers") or {}).values():
        for row in block.get("rows") or []:
            row.pop("reason", None)
    res["purged_at"] = datetime.utcnow().isoformat() + "Z"
    ev.results = C.jdump(res)
    sample = C.jload(ev.sample, {}) or {}
    sample["ground_truth"] = {}
    ev.sample = C.jdump(sample)
    C.log_event(db, ev.organization_id, "provider_evaluation.purged", user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION, is_test=bool(ev.synthetic),
                summary="Provider evaluation '%s': returned data deleted, metrics kept" % ev.name)
    db.flush()


def payload(ev: EvoSenseProviderEvaluation, *, with_records: bool = True) -> Dict[str, Any]:
    res = C.jload(ev.results, None)
    if res is not None and not with_records:
        res = dict(res, records=None)
    sample = C.jload(ev.sample, {}) or {}
    return {"id": ev.id, "name": ev.name, "status": ev.status, "synthetic": bool(ev.synthetic),
            "label": "SYNTHETIC" if ev.synthetic else "REAL",
            "mode": (res or {}).get("mode") or sample.get("mode") or MODE_CONTACT,
            "provider_keys": C.jload(ev.provider_keys, []), "results": res,
            "purged": bool((res or {}).get("purged_at")),
            "total_cost_cents": ev.total_cost_cents,
            "created_at": ev.created_at.isoformat() + "Z" if ev.created_at else None,
            "finished_at": ev.finished_at.isoformat() + "Z" if ev.finished_at else None}


def candidates(db, org_id: str) -> Dict[str, Any]:
    """Which providers the harness could evaluate, per mode, with each one's
    readiness in plain words - for the evaluation screen."""
    out = {MODE_CONTACT: [], MODE_COMPS: [], "referees": []}
    for key, p in PV.PROVIDERS.items():
        caps = set(p.capabilities or ())
        if p.connector_kind not in (C.REAL, C.SANDBOX):
            continue
        slot = (MODE_CONTACT if C.CONTACT_ENRICHMENT in caps else MODE_COMPS if C.COMPS in caps
                else "referees" if C.PHONE_VALIDATION in caps else None)
        if slot is None:
            continue
        cfg = PV.config(db, org_id, key)
        canon = PV.canonical(p, cfg, blocked=PV.platform_blocked(db, key, cfg))
        out[slot].append({"key": key, "label": p.label, "connector_kind": p.connector_kind,
                          "ready": bool(canon.get("eval_routable")), "why": canon.get("why"),
                          "configured": canon.get("configured"), "enabled": canon.get("enabled"),
                          "missing_env": list(p.missing_config()) if hasattr(p, "missing_config") else [],
                          "evaluation_only": bool(getattr(p, "evaluation_only", False)),
                          "cost_cents": p.cost(C.CONTACT_ENRICHMENT if slot == MODE_CONTACT else
                                               C.COMPS if slot == MODE_COMPS else C.PHONE_VALIDATION,
                                               C.jload(cfg.cost_overrides, {}) or {}),
                          "price_confirmed": getattr(p, "price_confirmed", None),
                          "price_note": getattr(p, "price_note", None)})
    return out
