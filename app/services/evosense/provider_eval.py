"""THE PROVIDER EVALUATION HARNESS - a measurement environment, not a backdoor
enrichment process. The same authorized sample through each provider,
compared on the same metrics, with the evidence and no declared winner.

TWO MODES
  contact   CONTACT_ENRICHMENT providers (skip trace), optionally refereed
            by an independent PHONE_VALIDATION provider (line type):
              owner match rate, correct-owner rate (ground truth), owner-name
              agreement with the owner of record (a proxy, labelled so),
              phone / usable-phone / mobile / usable-mobile / email /
              usable-email coverage, line-type completeness and referee
              agreement, freshness, false positives / wrong party, latency,
              failures, hits / misses / free / refunded, cost per lookup /
              matched owner / usable contact / verified contact
  comps     COMPS providers: each subject's comps go through the workspace's
            comp_eligibility rules -> ARV engine -> confidence exactly as a
            deal's would - IN MEMORY. Captures comps returned, closed-price
            availability by PRICE SOURCE (MLS closed / public record /
            unverified record / estimated / list / AVM), close dates,
            distance, fact completeness, eligible vs excluded with the
            reasons, subjects reaching >= 3 eligible, the ARV and confidence
            that WOULD result, ground-truth price accuracy, latency, cost.

THE APPROVAL GATE (money)
  plan()     validates everything and computes the MAXIMUM SPEND - per
             provider, per call, referee included - before anything is
             called. A plan that includes a real (paid) provider is saved as
             PLANNED and calls nothing.
  execute()  runs a plan. A paid plan needs the owner's exact phrase
                 RUN PAID EVALUATION <evaluation id>
             and never spends beyond the planned maximum: a call that would
             exceed it is recorded "not attempted - authorized maximum
             reached". Every call is reserved against the workspace budget
             and settled to what the vendor actually billed (misses free where
             the vendor says so); refunds on failure.
  A plan made only of sandbox adapters is SYNTHETIC and needs no approval.

ISOLATION (the rules this file exists to keep)
  * Nothing a provider returns becomes a contact point, a comp, a lead, a
    deal value or a score. Returned values live only in this evaluation's
    record, labelled REAL PROVIDER EVALUATION DATA (SYNTHETIC for sandbox).
  * Nothing is sent: no SMS, email, call, cadence or AI message. Twilio
    Lookup is a lookup of line type, never a message.
  * A FINGERPRINT of every sample property's production state (Property
    Opportunity, Contact Confidence, Seller Intent, contactability, status,
    data confidence) and of the workspace's contacts, leads, deals, comps and
    outreach is taken before and checked after. Any difference is recorded as
    an ISOLATION VIOLATION, the property values are restored, and the
    evaluation is marked failed.
  * purge() deletes the returned values (and supplied ground truth) and keeps
    the aggregate metrics.

GROUND TRUTH is only what the workspace already lawfully knows, entered by a
person with how it is known (EvoSenseEvalGroundTruth). It is never inferred.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime
from statistics import median
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseEvalGroundTruth, EvoSenseProperty,
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
# Owners a PERSON skip trace cannot meaningfully match. Joint owners, "ET AL",
# life estates and estates are people (or their heirs) and ARE in the sample.
ENTITY_OWNER_TYPES = ("llc", "corporation", "trust", "government", "religious_org", "nonprofit")
REAL_LABEL = "REAL PROVIDER EVALUATION DATA - isolated; never a contact, comp or deal value"
SYNTHETIC_LABEL = "SYNTHETIC TEST RESULTS - software behaviour only"
CAP_REACHED = "AUTHORIZED MAXIMUM REACHED"
GT_CONTACT, GT_CLOSED_SALE = "contact", "closed_sale"


class EvaluationRefused(ValueError):
    def __init__(self, message: str, evaluation_id: Optional[str] = None):
        super().__init__(message)
        self.evaluation_id = evaluation_id


def _digits(v) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())[-10:]


def _addr_key(v) -> str:
    return re.sub(r"[^A-Z0-9]", "", (v or "").upper())


def _paid(p) -> bool:
    return p.connector_kind != C.SANDBOX


def _ratio(a, b):
    return round(a / b, 3) if b else None


def _readiness(db, org_id, p, cfg) -> Dict[str, Any]:
    """May the harness call this provider right now, and if not, why not.

    An EVALUATION ONLY vendor needs its credential in the platform
    environment and no platform block - the owner's typed authorization of a
    specific capped plan is the switch, so the per-tenant toggle is not also
    required (and production routing never reaches it either way)."""
    canon = PV.canonical(p, cfg, blocked=PV.platform_blocked(db, p.key, cfg))
    if canon.get("eval_routable"):
        return {"ready": True, "why": "ready"}
    if getattr(p, "evaluation_only", False) and canon.get("configured") and not canon.get("blocked"):
        return {"ready": True, "why": "ready (evaluation only - runs solely under an authorized plan)"}
    missing = list(p.missing_config()) if hasattr(p, "missing_config") else []
    return {"ready": False, "why": ("credential not set on the platform: %s" % ", ".join(missing)) if missing
            else canon.get("why")}


def _check_provider(db, org_id, key, capability, *, require_ready: bool = True):
    p = PV.PROVIDERS.get(key)
    if p is None or capability not in (p.capabilities or ()):
        raise EvaluationRefused("%s cannot supply %s." % (key, capability.replace("_", " ").lower()))
    if p.connector_kind not in (C.SANDBOX, C.REAL):
        raise EvaluationRefused("%s cannot be called (%s)." % (p.label, p.connector_kind))
    cfg = PV.config(db, org_id, key)
    if require_ready:
        r = _readiness(db, org_id, p, cfg)
        if not r["ready"]:
            raise EvaluationRefused("%s is not usable yet: %s" % (p.label, r["why"]))
    return p, cfg


def _unit(p, cfg, capability) -> int:
    return int(p.cost(capability, C.jload(cfg.cost_overrides, {}) or {}) or 0)


# ── plan -> approve -> execute ────────────────────────────────────────────

def plan(db, org_id: str, *, name: str, provider_keys: List[str], property_ids: List[str],
         mode: str = MODE_CONTACT, referee_key: Optional[str] = None,
         ground_truth: Optional[Dict[str, Dict[str, Any]]] = None, user=None) -> EvoSenseProviderEvaluation:
    """Validate and price an evaluation. Calls nothing. Returns a PLANNED
    evaluation whose `max_spend_cents` is the most it can ever cost."""
    if mode not in (MODE_CONTACT, MODE_COMPS):
        raise EvaluationRefused("Mode must be contact or comps.")
    if not provider_keys or not property_ids:
        raise EvaluationRefused("Choose at least one provider and one property.")
    if len(set(property_ids)) > MAX_SAMPLE:
        raise EvaluationRefused("An evaluation sample is capped at %s properties." % MAX_SAMPLE)
    cap = C.CONTACT_ENRICHMENT if mode == MODE_CONTACT else C.COMPS
    keys = list(dict.fromkeys(provider_keys))
    # A PAID vendor may be planned before its credential exists: planning
    # calls nothing, and the plan shows exactly what is still missing. A
    # sandbox adapter must be switched on to be planned.
    providers = [_check_provider(db, org_id, k, cap,
                                 require_ready=PV.PROVIDERS.get(k) is not None
                                 and PV.PROVIDERS[k].connector_kind == C.SANDBOX) for k in keys]
    referee = None
    if referee_key:
        if mode != MODE_CONTACT:
            raise EvaluationRefused("A line-type referee applies to contact evaluations only.")
        rk = PV.PROVIDERS.get(referee_key)
        referee = _check_provider(db, org_id, referee_key, C.PHONE_VALIDATION,
                                  require_ready=rk is not None and rk.connector_kind == C.SANDBOX)
    props = (db.query(EvoSenseProperty)
             .filter(EvoSenseProperty.organization_id == org_id,
                     EvoSenseProperty.id.in_(property_ids)).all())
    if len(props) != len(set(property_ids)):
        raise EvaluationRefused("Every sample property must belong to this workspace.")
    for p, _ in providers + ([referee] if referee else []):
        if p.connector_kind == C.SANDBOX and any(not pr.is_test for pr in props):
            raise EvaluationRefused("Sandbox adapters only ever run on sandbox (test) properties.")

    n = len(props)
    lines = []
    for p, cfg in providers:
        unit = _unit(p, cfg, cap)
        lines.append({"provider": p.key, "label": p.label, "role": "provider", "max_calls": n,
                      "unit_cents": unit, "max_cents": unit * n, "paid": _paid(p),
                      "readiness": _readiness(db, org_id, p, cfg),
                      "price_confirmed": getattr(p, "price_confirmed", None),
                      "price_note": getattr(p, "price_note", None),
                      "misses_free": getattr(p, "charge_on_miss", True) is False})
    if referee is not None:
        rp, rcfg = referee
        unit = _unit(rp, rcfg, C.PHONE_VALIDATION)
        calls = n * len(providers) * REFEREE_MAX_PER_LOOKUP
        lines.append({"provider": rp.key, "label": rp.label, "role": "line-type referee",
                      "max_calls": calls, "unit_cents": unit, "max_cents": unit * calls, "paid": _paid(rp),
                      "readiness": _readiness(db, org_id, rp, rcfg),
                      "price_confirmed": getattr(rp, "price_confirmed", None),
                      "price_note": getattr(rp, "price_note", None), "misses_free": False})
    paid = any(l["paid"] for l in lines)
    max_spend = sum(l["max_cents"] for l in lines if l["paid"])
    ev = EvoSenseProviderEvaluation(
        organization_id=org_id, name=(name or "Provider evaluation")[:120], provider_keys=C.jdump(keys),
        sample=C.jdump({"mode": mode, "property_ids": [pr.id for pr in props],
                        "ground_truth": ground_truth or {}, "referee": referee_key}),
        synthetic=not paid, created_by_id=getattr(user, "id", None), mode=mode, status="planned",
        plan=C.jdump({"lines": lines, "max_spend_cents": max_spend, "sample_size": n,
                      "unconfirmed_prices": [l["provider"] for l in lines if l["paid"] and not l["price_confirmed"]],
                      "confirmation": ("%s <evaluation id>" % PAID_CONFIRMATION) if paid else None,
                      "note": ("Nothing has been called. The most this evaluation can cost is shown; "
                               "execution stops before exceeding it." if paid else
                               "Sandbox only - synthetic, free, no approval needed.")}),
        max_spend_cents=max_spend if paid else 0)
    db.add(ev)
    db.flush()
    C.log_event(db, org_id, "provider_evaluation.planned", user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION, is_test=not paid,
                summary="Provider evaluation '%s' planned (%s, %s properties): maximum spend %s"
                % (ev.name, mode, n, C.money(max_spend)))
    return ev


def confirmation_for(ev: EvoSenseProviderEvaluation) -> Optional[str]:
    return None if ev.synthetic else "%s %s" % (PAID_CONFIRMATION, ev.id)


def execute(db, ev: EvoSenseProviderEvaluation, *, confirm: Optional[str] = None,
            user=None) -> EvoSenseProviderEvaluation:
    """Run a PLANNED evaluation. A paid one needs "RUN PAID EVALUATION <id>"
    and never spends more than its planned maximum."""
    if ev.status != "planned":
        raise EvaluationRefused("This evaluation is %s; only a planned evaluation can run." % ev.status)
    needed = confirmation_for(ev)
    if needed and (confirm or "").strip() != needed:
        raise EvaluationRefused(
            "Real providers cost money per call. Running one is the owner's decision: the maximum "
            "spend is %s. Confirm with \"%s\"." % (C.money(ev.max_spend_cents or 0), needed), ev.id)
    org_id = ev.organization_id
    sample = C.jload(ev.sample, {}) or {}
    mode = ev.mode or sample.get("mode") or MODE_CONTACT
    cap = C.CONTACT_ENRICHMENT if mode == MODE_CONTACT else C.COMPS
    providers = [_check_provider(db, org_id, k, cap) for k in C.jload(ev.provider_keys, [])]
    referee = _check_provider(db, org_id, sample["referee"], C.PHONE_VALIDATION) if sample.get("referee") else None
    props = (db.query(EvoSenseProperty)
             .filter(EvoSenseProperty.organization_id == org_id,
                     EvoSenseProperty.id.in_(sample.get("property_ids") or [])).all())
    if needed:
        ev.authorized_at, ev.authorized_by_id = datetime.utcnow(), getattr(user, "id", None)
    ev.status = "running"
    db.flush()
    spend = {"cap": None if ev.synthetic else (ev.max_spend_cents or 0), "spent": 0}
    before = _fingerprint(db, org_id, props)
    if mode == MODE_CONTACT:
        truth = _contact_truth(db, org_id, sample.get("ground_truth") or {})
        out, records, total = _run_contact(db, org_id, providers, props, truth, referee, spend)
    else:
        out, records, total = _run_comps(db, org_id, providers, props, _sale_truth(db, org_id), spend)
    violations = _check_isolation(db, org_id, props, before)
    ev.results = C.jdump({"mode": mode, "providers": out, "sample_size": len(props),
                          "label": SYNTHETIC_LABEL if ev.synthetic else REAL_LABEL,
                          "referee": sample.get("referee"), "records": records, "purged_at": None,
                          "isolation": {"checked": True, "violations": violations},
                          "max_spend_cents": ev.max_spend_cents, "spent_cents": total})
    ev.total_cost_cents = total
    ev.status = "isolation_violation" if violations else "completed"
    ev.finished_at = datetime.utcnow()
    C.log_event(db, org_id, "provider_evaluation.completed", user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION, is_test=bool(ev.synthetic),
                summary="Provider evaluation '%s' (%s, %s): %s provider(s) x %s properties, %s of %s max%s"
                % (ev.name, mode, "SYNTHETIC" if ev.synthetic else "REAL", len(providers), len(props),
                   C.money(total), C.money(ev.max_spend_cents or 0),
                   " - ISOLATION VIOLATION" if violations else ""))
    db.flush()
    return ev


def run(db, org_id: str, *, name: str, provider_keys: List[str], property_ids: List[str],
        mode: str = MODE_CONTACT, ground_truth: Optional[Dict[str, Dict[str, Any]]] = None,
        referee_key: Optional[str] = None, user=None, confirm: Optional[str] = None,
        evaluation_id: Optional[str] = None) -> EvoSenseProviderEvaluation:
    """Plan and run in one step. A synthetic plan runs immediately. A paid
    plan is saved and refused with its id and maximum spend, so the owner
    can authorize exactly that plan (execute with the id's phrase)."""
    ev = plan(db, org_id, name=name, provider_keys=provider_keys, property_ids=property_ids, mode=mode,
              referee_key=referee_key, ground_truth=ground_truth, user=user)
    if not ev.synthetic:
        db.flush()
        raise EvaluationRefused(
            "Planned, not run: this evaluation calls paid providers. Maximum spend %s. To authorize "
            "it, execute evaluation %s with \"%s\"." % (C.money(ev.max_spend_cents or 0), ev.id,
                                                         confirmation_for(ev)), ev.id)
    return execute(db, ev, user=user)


def _reserve(db, org_id, p, cfg, capability, prop, spend, owner=None):
    cost = _unit(p, cfg, capability)
    if spend.get("cap") is not None and _paid(p) and spend["spent"] + cost > spend["cap"]:
        return False, CAP_REACHED, None
    ok, why, entry = B.reserve(db, org_id, None, cost, provider_key=p.key, connector_kind=p.connector_kind,
                               capability=capability, operation="provider_evaluation", property_id=prop.id,
                               owner_id=getattr(owner, "id", None), is_test=bool(prop.is_test))
    if ok:
        spend["spent"] += cost          # reserved now; settled (possibly lower) after the call
        spend["_last_reserved"] = cost
    return ok, why, entry


def _release(spend, reserved, actual):
    spend["spent"] -= max(0, reserved - actual)


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


# ── isolation ─────────────────────────────────────────────────────────────

PROP_FIELDS = ("opportunity_score", "contact_confidence", "seller_intent", "contactability",
               "contactability_detail", "status", "data_confidence", "promoted_deal_id")


def _fingerprint(db, org_id, props) -> Dict[str, Any]:
    from app.models.evosense_models import EvoSenseEngagement, EvoSenseScore
    from app.models.models import Lead
    from app.models.wholesale_models import WholesaleComp, WholesaleDeal
    ids = [p.id for p in props]
    counts = {
        "contact_points": db.query(EvoSenseContactPoint).filter_by(organization_id=org_id).count(),
        "leads": db.query(Lead).filter_by(organization_id=org_id).count(),
        "deals": db.query(WholesaleDeal).filter_by(organization_id=org_id).count(),
        "comps": db.query(WholesaleComp).filter_by(organization_id=org_id).count(),
        "engagements": db.query(EvoSenseEngagement).filter_by(organization_id=org_id).count(),
        "scores": (db.query(EvoSenseScore).filter(EvoSenseScore.organization_id == org_id,
                                                  EvoSenseScore.property_id.in_(ids)).count() if ids else 0),
    }
    try:
        from app.models.models import Message
        counts["messages"] = db.query(Message).filter_by(organization_id=org_id).count()
    except Exception:  # noqa: BLE001 - a model without organization_id: skip that count
        pass
    return {"props": {p.id: {f: getattr(p, f, None) for f in PROP_FIELDS} for p in props}, "counts": counts}


def _check_isolation(db, org_id, props, before) -> List[str]:
    db.flush()
    after = _fingerprint(db, org_id, props)
    out = []
    for name, n in before["counts"].items():
        if after["counts"].get(name) != n:
            out.append("%s changed %s -> %s" % (name, n, after["counts"].get(name)))
    by_id = {p.id: p for p in props}
    for pid, vals in before["props"].items():
        for f, v in vals.items():
            if after["props"][pid].get(f) != v:
                out.append("property %s: %s changed" % (pid, f))
                setattr(by_id[pid], f, v)            # restore production state
    if out:
        C.log.error("provider evaluation isolation violation: %s", "; ".join(out[:10]))
    return out


# ── ground truth ──────────────────────────────────────────────────────────

def add_ground_truth(db, org_id: str, *, kind: str, data: Dict[str, Any], source_note: str,
                     property_id: Optional[str] = None, user=None) -> EvoSenseEvalGroundTruth:
    """Record something the workspace already lawfully knows. The person must
    say how it is known; nothing is inferred or looked up here."""
    note = (source_note or "").strip()
    if len(note) < 10:
        raise EvaluationRefused("Say how this is lawfully known (e.g. \"seller's own phone from our "
                                "signed contract, deal #123\").")
    if kind == GT_CONTACT:
        if not property_id:
            raise EvaluationRefused("A contact ground truth belongs to a property.")
        prop = db.query(EvoSenseProperty).filter_by(organization_id=org_id, id=property_id).first()
        if prop is None:
            raise EvaluationRefused("That property is not in this workspace.")
        phones = [_digits(x) for x in data.get("phones") or [] if len(_digits(x)) == 10]
        emails = [str(x).strip().lower() for x in data.get("emails") or [] if "@" in str(x)]
        if not phones and not emails:
            raise EvaluationRefused("Give at least one known phone or email.")
        clean = {"phones": phones, "emails": emails, "owner_name": (data.get("owner_name") or None)}
    elif kind == GT_CLOSED_SALE:
        try:
            price = float(data.get("sale_price"))
            sdate = date.fromisoformat(str(data.get("sale_date"))[:10]).isoformat()
        except (TypeError, ValueError):
            raise EvaluationRefused("A closed sale needs a sale price and a sale date (YYYY-MM-DD).")
        if price <= 0 or not (data.get("street_address") or "").strip():
            raise EvaluationRefused("A closed sale needs a street address and a positive price.")
        clean = {"street_address": data["street_address"].strip(), "city": (data.get("city") or "").strip() or None,
                 "sale_price": price, "sale_date": sdate}
    else:
        raise EvaluationRefused("Ground truth is contact or closed_sale.")
    row = EvoSenseEvalGroundTruth(organization_id=org_id, kind=kind, property_id=property_id,
                                  data=C.jdump(clean), source_note=note[:500],
                                  entered_by_id=getattr(user, "id", None))
    db.add(row)
    db.flush()
    return row


def ground_truth_payload(row) -> Dict[str, Any]:
    return {"id": row.id, "kind": row.kind, "property_id": row.property_id, "data": C.jload(row.data, {}),
            "source_note": row.source_note,
            "created_at": row.created_at.isoformat() + "Z" if row.created_at else None}


def _contact_truth(db, org_id, supplied) -> Dict[str, Dict[str, Any]]:
    out = {k: dict(v) for k, v in (supplied or {}).items()}
    for r in db.query(EvoSenseEvalGroundTruth).filter_by(organization_id=org_id, kind=GT_CONTACT).all():
        d = C.jload(r.data, {}) or {}
        cur = out.setdefault(r.property_id, {"phones": [], "emails": []})
        cur["phones"] = list(cur.get("phones", [])) + d.get("phones", [])
        cur["emails"] = list(cur.get("emails", [])) + d.get("emails", [])
    return out


def _sale_truth(db, org_id) -> Dict[str, Dict[str, Any]]:
    out = {}
    for r in db.query(EvoSenseEvalGroundTruth).filter_by(organization_id=org_id, kind=GT_CLOSED_SALE).all():
        d = C.jload(r.data, {}) or {}
        out[_addr_key(d.get("street_address"))] = d
    return out


# ── contact mode ──────────────────────────────────────────────────────────

def _run_contact(db, org_id, providers, props, truth_all, referee, spend):
    known_bad = {(c.kind, c.value) for c in db.query(EvoSenseContactPoint).filter(
        EvoSenseContactPoint.organization_id == org_id,
        EvoSenseContactPoint.status.in_(("wrong_party", "opted_out", "invalid")))}
    out: Dict[str, Any] = {}
    records: List[Dict[str, Any]] = []
    total = 0
    referee_cache: Dict[str, Optional[Dict[str, Any]]] = {}
    for p, cfg in providers:
        rows, lat, ages = [], [], []
        for prop in props:
            owner = CT.primary_owner(db, prop)
            ok, why, entry = _reserve(db, org_id, p, cfg, C.CONTACT_ENRICHMENT, prop, spend, owner)
            if not ok:
                rows.append({"property_id": prop.id, "result": "not_attempted", "reason": why, "cost_cents": 0})
                continue
            reserved = entry.total_cents or 0
            t0 = time.monotonic()
            try:
                res = p.enrich(EN._input_for(prop, owner))
            except Exception as exc:  # noqa: BLE001 - a failure is a result, and refunded
                B.refund(db, entry)
                _release(spend, reserved, 0)
                PV.record_failure(cfg, "%s: %s" % (type(exc).__name__, str(exc)[:120]),
                                  capability=C.CONTACT_ENRICHMENT)
                rows.append({"property_id": prop.id, "result": "failed", "error": type(exc).__name__,
                             "ms": int((time.monotonic() - t0) * 1000), "cost_cents": 0, "refunded": True})
                continue
            ms = int((time.monotonic() - t0) * 1000)
            lat.append(ms)
            PV.record_success(cfg, capability=C.CONTACT_ENRICHMENT)
            phones = [{"number": _digits(ph.number), "type": ph.phone_type,
                       "last_seen": getattr(ph, "last_seen", None), "dnc": getattr(ph, "dnc_flag", None),
                       "connected": (getattr(ph, "match_evidence", None) or {}).get("connected"),
                       "ref": getattr(ph, "provider_reference", None)} for ph in (res.phones or [])
                      if _digits(ph.number)]
            emails = [(e if isinstance(e, str) else e.address or "").strip().lower() for e in (res.emails or [])]
            emails = [e for e in emails if e]
            matched = bool(phones or emails)
            cost = _settle(db, entry, getattr(res, "billable", True), getattr(res, "cost_cents", None), matched)
            _release(spend, reserved, cost)
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
                    ref, spent = _referee(db, org_id, referee, prop, ph["number"], referee_cache, spend)
                    total += spent
                    ph["referee_type"] = (ref or {}).get("line_type")
                    ph["referee_validation"] = (ref or {}).get("validation")
                    if ph["type"] and ph["type"] != "unknown" and ph["referee_type"]:
                        ph["referee_agrees"] = _same_line(ph["type"], ph["referee_type"])
            # USABLE: not known wrong-party / opted-out / invalid here, not
            # reported disconnected by the vendor, not invalid per the referee.
            for ph in phones:
                ph["usable"] = (("phone", "+1" + ph["number"]) not in known_bad and ph.get("connected") is not False
                                and ph.get("referee_validation") != "invalid")
                line = ph.get("referee_type") or ph["type"]
                ph["usable_mobile"] = ph["usable"] and line == "mobile"
            usable_emails = [e for e in emails if ("email", e) not in known_bad]
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
                         "usable_phones": sum(1 for x in phones if x["usable"]),
                         "usable_mobile": sum(1 for x in phones if x["usable_mobile"]),
                         "typed": sum(1 for x in phones if x["type"] and x["type"] != "unknown"),
                         "emails": len(emails), "usable_emails": len(usable_emails),
                         "verified": len(verified), "false_positives": len(set(false_pos)),
                         "has_truth": bool(t_phones or t_emails), "name_match": agree,
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


def _referee(db, org_id, referee, prop, number, cache, spend):
    """({line_type, validation}, cents charged by THIS call). A number
    already checked in this run is not paid for twice."""
    if number in cache:
        return cache[number], 0
    p, cfg = referee
    ok, why, entry = _reserve(db, org_id, p, cfg, C.PHONE_VALIDATION, prop, spend)
    if not ok:
        cache[number] = None
        return None, 0
    reserved = entry.total_cents or 0
    try:
        r = p.validate_phone(number) or {}
    except Exception as exc:  # noqa: BLE001
        B.refund(db, entry)
        _release(spend, reserved, 0)
        PV.record_failure(cfg, "%s: %s" % (type(exc).__name__, str(exc)[:120]), capability=C.PHONE_VALIDATION)
        cache[number] = None
        return None, 0
    B.charge(db, entry, success=True)
    PV.record_success(cfg, capability=C.PHONE_VALIDATION)
    cache[number] = {"line_type": r.get("line_type"), "validation": r.get("validation")}
    return cache[number], entry.total_cents or 0


def _contact_metrics(p, rows, lat, ages) -> Dict[str, Any]:
    attempted = [r for r in rows if r["result"] in ("match", "no_match")]
    n = len(attempted) or 1
    matched = [r for r in attempted if r["result"] == "match"]
    phones = sum(r["phones"] for r in attempted)
    usable = sum(1 for r in attempted if r["usable_mobile"] or r["usable_emails"])
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
        "hits_charged": len([r for r in matched if r["cost_cents"] > 0]),
        "hits_free": len([r for r in matched if r["cost_cents"] == 0]),
        "misses": len([r for r in attempted if r["result"] == "no_match"]),
        "misses_charged": len([r for r in attempted if r["result"] == "no_match" and r["cost_cents"] > 0]),
        "refunded": len([r for r in rows if r.get("refunded")]),
        "match_rate": _ratio(len(matched), n),
        "owner_match_accuracy": _ratio(sum(1 for r in truthy if r["verified"]), len(truthy)),
        "correct_owner_rate_truth": _ratio(sum(1 for r in truthy if r["verified"]), len(truthy)),
        "ground_truth_records": len(truthy),
        "owner_name_agreement_rate": _ratio(len(owner_ok), len(named)),
        "owner_name_agreement_note": "Proxy: vendor's person name vs the owner of record - evidence, not proof",
        "phone_coverage": _ratio(sum(1 for r in attempted if r["phones"]), n),
        "usable_phone_coverage": _ratio(sum(1 for r in attempted if r["usable_phones"]), n),
        "mobile_coverage": _ratio(sum(1 for r in attempted if r["mobile"]), n),
        "usable_mobile_coverage": _ratio(sum(1 for r in attempted if r["usable_mobile"]), n),
        "email_coverage": _ratio(sum(1 for r in attempted if r["emails"]), n),
        "usable_email_coverage": _ratio(sum(1 for r in attempted if r["usable_emails"]), n),
        "validation_quality": _ratio(sum(r["typed"] for r in attempted), phones),
        "line_type_completeness": _ratio(sum(r["typed"] for r in attempted), phones),
        "line_type_agreement": _ratio(sum(r["referee_agree"] for r in attempted), comparable),
        "line_type_compared": comparable,
        "false_positive_rate": _ratio(sum(r["false_positives"] for r in attempted), returned),
        "median_last_seen_days": median(ages) if ages else None,
        "median_latency_ms": median(lat) if lat else None,
        "cost_cents": cost,
        "cost_per_lookup_cents": _ratio(cost, len(attempted)),
        "cost_per_match_cents": _ratio(cost, len(matched)),
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


def _run_comps(db, org_id, providers, props, sale_truth, spend):
    from app.services import wholesale_sms as WS
    rules = CR.rules_for(WS.settings_row(db, org_id))
    out: Dict[str, Any] = {}
    records: List[Dict[str, Any]] = []
    total = 0
    for p, cfg in providers:
        rows, lat = [], []
        for prop in props:
            ok, why, entry = _reserve(db, org_id, p, cfg, C.COMPS, prop, spend)
            if not ok:
                rows.append({"property_id": prop.id, "result": "not_attempted", "reason": why, "cost_cents": 0})
                continue
            subject = _subject(prop)
            t0 = time.monotonic()
            try:
                comps = p.search(C.COMPS, subject) or []
            except Exception as exc:  # noqa: BLE001
                B.refund(db, entry)
                _release(spend, entry.total_cents or 0, 0)
                PV.record_failure(cfg, "%s: %s" % (type(exc).__name__, str(exc)[:120]), capability=C.COMPS)
                rows.append({"property_id": prop.id, "result": "failed", "error": type(exc).__name__,
                             "ms": int((time.monotonic() - t0) * 1000), "cost_cents": 0, "refunded": True})
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
            reasons: Dict[str, int] = {}
            for x in engine["comps_excluded"]:
                for e in x["excluded"]:
                    reasons[e["code"]] = reasons.get(e["code"], 0) + 1
            closed = [c for c in comps if c.get("price_source") in CR.CLOSED_PRICE_SOURCES
                      and c.get("sale_price") and c.get("sale_date")]
            ages = []
            for c in closed:
                try:
                    ages.append((date.today() - date.fromisoformat(str(c["sale_date"])[:10])).days)
                except ValueError:
                    pass
            dists = [float(c["distance_miles"]) for c in comps if c.get("distance_miles") is not None]
            complete = sum(1 for c in comps if all(c.get(k) is not None for k in FACTS)
                           and (c.get("distance_miles") is not None or c.get("latitude") is not None))
            # Ground truth: a comp at an address whose closed price is lawfully known.
            t_hits, t_exact = 0, 0
            for c in comps:
                t = sale_truth.get(_addr_key(c.get("street_address")))
                if t:
                    t_hits += 1
                    if c.get("sale_price") and abs(float(c["sale_price"]) - float(t["sale_price"])) <= 1000 \
                            and str(c.get("sale_date") or "")[:7] == str(t["sale_date"])[:7]:
                        t_exact += 1
            rows.append({"property_id": prop.id, "result": "returned" if comps else "none",
                         "returned": len(comps), "by_source": by_source, "closed_price": len(closed),
                         "median_close_age_days": median(ages) if ages else None,
                         "median_distance_miles": round(median(dists), 2) if dists else None,
                         "facts_complete": complete, "eligible": len(engine["comps_used"]),
                         "excluded": len(engine["comps_excluded"]), "exclusion_reasons": reasons,
                         "arv_status": engine["status"], "arv_value": engine.get("value"),
                         "arv_confidence": (engine.get("confidence") or {}).get("label"),
                         "truth_matches": t_hits, "truth_exact": t_exact, "ms": ms, "cost_cents": cost})
            records.append({"provider": p.key, "property_id": prop.id,
                            "subject": subject.get("street_address"),
                            "comps": [{k: c.get(k) for k in ("street_address", "sale_price", "sale_date",
                                                             "price_source", "distance_miles",
                                                             "provider_record_id")} for c in comps],
                            "would_be_arv": {"status": engine["status"], "value": engine.get("value"),
                                             "low": engine.get("low"), "high": engine.get("high"),
                                             "confidence": (engine.get("confidence") or {}).get("label"),
                                             "excluded": [{"address": x["address"],
                                                           "why": [e["code"] for e in x["excluded"]]}
                                                          for x in engine["comps_excluded"]]},
                            "cost_cents": cost})
        out[p.key] = _comps_metrics(db, p, rows, lat)
    return out, records, total


def _comps_metrics(db, p, rows, lat) -> Dict[str, Any]:
    done = [r for r in rows if r["result"] in ("returned", "none")]
    n = len(done) or 1
    returned = sum(r["returned"] for r in done)
    by_source: Dict[str, int] = {}
    reasons: Dict[str, int] = {}
    for r in done:
        for k, v in r["by_source"].items():
            by_source[k] = by_source.get(k, 0) + v
        for k, v in r["exclusion_reasons"].items():
            reasons[k] = reasons.get(k, 0) + v
    closed = sum(r["closed_price"] for r in done)
    ages = [r["median_close_age_days"] for r in done if r["median_close_age_days"] is not None]
    dists = [r["median_distance_miles"] for r in done if r["median_distance_miles"] is not None]
    conf: Dict[str, int] = {}
    for r in done:
        key = r["arv_confidence"] or "insufficient"
        conf[key] = conf.get(key, 0) + 1
    cost = sum(r["cost_cents"] for r in rows)
    eligible = sum(r["eligible"] for r in done)
    t_hits = sum(r["truth_matches"] for r in done)
    return {
        "provider": p.key, "label": p.label, "connector_kind": p.connector_kind,
        "synthetic": p.connector_kind == C.SANDBOX, "evaluation_only": bool(getattr(p, "evaluation_only", False)),
        "price_confirmed": getattr(p, "price_confirmed", None), "price_note": getattr(p, "price_note", None),
        "subjects": len(done), "failures": len([r for r in rows if r["result"] == "failed"]),
        "not_attempted": len([r for r in rows if r["result"] == "not_attempted"]),
        "refunded": len([r for r in rows if r.get("refunded")]),
        "comps_returned": returned, "comps_by_price_source": by_source,
        "closed_price_share": _ratio(closed, returned),
        "mls_closed_share": _ratio(by_source.get(CR.PRICE_MLS_CLOSED, 0), returned),
        "median_close_age_days": median(ages) if ages else None,
        "median_distance_miles": median(dists) if dists else None,
        "facts_complete_share": _ratio(sum(r["facts_complete"] for r in done), returned),
        "eligible_comps": eligible, "excluded_comps": sum(r["excluded"] for r in done),
        "exclusion_reasons": reasons,
        "eligible_per_subject": _ratio(eligible, n),
        "subjects_with_3_eligible": _ratio(sum(1 for r in done if r["eligible"] >= 3), n),
        "would_be_arv_confidence": conf,
        "ground_truth_sales_matched": t_hits,
        "ground_truth_price_accuracy": _ratio(sum(r["truth_exact"] for r in done), t_hits),
        "median_latency_ms": median(lat) if lat else None,
        "cost_cents": cost, "cost_per_subject_cents": _ratio(cost, len(done)),
        "cost_per_eligible_comp_cents": _ratio(cost, eligible),
        "production_ready": False,
        "readiness_note": ("A comps provider never becomes production-ready from these numbers. SOLD_COMPS "
                           "stays manual until the DFW truth gate is met (what the Texas price is, where it "
                           "comes from, coverage, freshness, storage and display rights) and Mike approves."),
        "rows": rows,
    }


# ── sample, report, payload, purge ────────────────────────────────────────

def proposed_sample(db, org_id: str, mode: str = MODE_CONTACT, limit: int = 50) -> Dict[str, Any]:
    """The authorized sample the harness proposes: REAL properties this
    workspace is already researching. Contact mode: owners awaiting contact
    data (people - an entity is left out, with the reason). Comps mode:
    properties with enough facts to compare."""
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
            elif (owner.owner_type or "individual") in ENTITY_OWNER_TYPES:
                why = "entity owner (%s) - a person skip trace does not apply" % owner.owner_type
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


def _pct(v):
    return "—" if v is None else "%d%%" % round(v * 100)


def _usd(c):
    return "—" if c is None else "$%.2f" % (c / 100.0)


def report(ev: EvoSenseProviderEvaluation) -> Dict[str, Any]:
    """The comparison, provider by provider on the SAME records - evidence,
    never a declared winner."""
    res = C.jload(ev.results, {}) or {}
    mode = res.get("mode") or ev.mode or MODE_CONTACT
    lines = []
    for m in (res.get("providers") or {}).values():
        if mode == MODE_CONTACT:
            owner = ("%s correct owner (ground truth, %s records)" % (_pct(m.get("correct_owner_rate_truth")),
                                                                       m.get("ground_truth_records"))
                     if m.get("ground_truth_records") else
                     "%s name agrees with owner of record (proxy)" % _pct(m.get("owner_name_agreement_rate")))
            text = "; ".join([owner, "%s usable mobile" % _pct(m.get("usable_mobile_coverage")),
                              "%s usable email" % _pct(m.get("usable_email_coverage")),
                              "%s per usable contact" % _usd(m.get("cost_per_usable_contact_cents"))])
        else:
            text = "; ".join(["%s closed-sale comps (MLS %s)" % (_pct(m.get("closed_price_share")),
                                                                 _pct(m.get("mls_closed_share"))),
                              "%s of subjects reach 3 eligible" % _pct(m.get("subjects_with_3_eligible")),
                              "would-be ARV confidence %s" % (m.get("would_be_arv_confidence") or {}),
                              "%s per subject" % _usd(m.get("cost_per_subject_cents"))])
        lines.append({"provider": m.get("provider"), "label": m.get("label"), "summary": text,
                      "synthetic": m.get("synthetic"), "price_confirmed": m.get("price_confirmed")})
    examples = _examples(res) if mode == MODE_CONTACT else []
    notes = ["Same records for every provider (%s properties)." % res.get("sample_size"),
             "No winner is declared: this is evidence for Mike's decision."]
    if ev.synthetic:
        notes.append("SYNTHETIC - sandbox adapters; says nothing about any vendor.")
    if any(l["price_confirmed"] is False for l in lines):
        notes.append("Some costs use UNCONFIRMED prices; the ledger shows what was actually charged.")
    if (res.get("isolation") or {}).get("violations"):
        notes.append("ISOLATION VIOLATION recorded - results must not be relied on.")
    return {"mode": mode, "lines": lines, "notes": notes, "examples": examples}


def _mask(number: Optional[str]) -> Optional[str]:
    d = _digits(number)
    return ("***-***-%s" % d[-4:]) if len(d) >= 4 else None


def _examples(res: Dict[str, Any], per_kind: int = 3) -> List[Dict[str, Any]]:
    """A few STRONG and QUESTIONABLE matches, with provenance and nothing
    more personal than needed: the (public) property address, how the
    vendor's name compared with the owner of record, line types, last-seen
    dates, the vendor's reference, and phones masked to the last 4 digits.
    No names, no full numbers, no emails. Empty once the data is purged."""
    out: List[Dict[str, Any]] = []
    strong, doubtful = [], []
    for r in res.get("records") or []:
        phones = r.get("phones") or []
        if not phones and not r.get("emails"):
            continue
        item = {"provider": r.get("provider"), "property_id": r.get("property_id"),
                "name_match": r.get("name_match"),
                "phones": [{"masked": _mask(ph.get("number")), "type": ph.get("type"),
                            "referee_type": ph.get("referee_type"), "dnc": ph.get("dnc"),
                            "last_seen": ph.get("last_seen"), "usable": ph.get("usable")} for ph in phones[:4]],
                "emails": len(r.get("emails") or []),
                "provider_reference": r.get("provider_reference"), "looked_up_at": r.get("looked_up_at")}
        good = r.get("name_match") == "full" and any(ph.get("usable_mobile") for ph in phones)
        bad = r.get("name_match") in ("none", None) or not any(ph.get("usable") for ph in phones)
        if good and len(strong) < per_kind:
            strong.append(dict(item, kind="strong",
                               why="Vendor's person name fully agrees with the owner of record, "
                                   "and a usable mobile was returned"))
        elif bad and len(doubtful) < per_kind:
            doubtful.append(dict(item, kind="questionable",
                                 why="Name does not agree with the owner of record" if r.get("name_match") ==
                                 "none" else "No name to compare" if r.get("name_match") is None
                                 else "No usable phone returned"))
    return strong + doubtful


def purge(db, ev: EvoSenseProviderEvaluation, user=None) -> None:
    """Delete the returned values (contacts, comps, supplied ground truth);
    keep the aggregate metrics. Irreversible by design - the point is deletion."""
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


def plan_readiness(db, ev: EvoSenseProviderEvaluation) -> Dict[str, Any]:
    """LIVE readiness of a planned evaluation (a credential added after the
    plan was made shows up here). Calls nothing."""
    plan_ = C.jload(ev.plan, {}) or {}
    lines = []
    for l in plan_.get("lines") or []:
        p = PV.PROVIDERS.get(l["provider"])
        r = _readiness(db, ev.organization_id, p, PV.config(db, ev.organization_id, p.key)) if p else \
            {"ready": False, "why": "provider no longer exists"}
        lines.append({"provider": l["provider"], "role": l["role"], **r})
    return {"ready": bool(lines) and all(l["ready"] for l in lines), "lines": lines}


def payload(ev: EvoSenseProviderEvaluation, *, with_records: bool = True) -> Dict[str, Any]:
    res = C.jload(ev.results, None)
    if res is not None and not with_records:
        res = dict(res, records=None)
    sample = C.jload(ev.sample, {}) or {}
    return {"id": ev.id, "name": ev.name, "status": ev.status, "synthetic": bool(ev.synthetic),
            "label": "SYNTHETIC" if ev.synthetic else "REAL",
            "mode": ev.mode or (res or {}).get("mode") or sample.get("mode") or MODE_CONTACT,
            "provider_keys": C.jload(ev.provider_keys, []), "results": res,
            "plan": C.jload(ev.plan, None), "max_spend_cents": ev.max_spend_cents,
            "confirmation": confirmation_for(ev) if ev.status == "planned" else None,
            "authorized_at": ev.authorized_at.isoformat() + "Z" if ev.authorized_at else None,
            "report": (report(ev) if with_records else dict(report(ev), examples=[])) if res else None,
            "sample_size": len(sample.get("property_ids") or []),
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
