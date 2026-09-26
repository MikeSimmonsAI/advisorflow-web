"""Cost-aware contact enrichment: DECIDE, then (maybe) SPEND.

EvoSense never buys everything. Before a paid lookup it asks, in order:

    is anything paused?                         -> RETRY_LATER
    is there an owner to look up at all?         -> INSUFFICIENT_OPPORTUNITY
    has this owner asked not to be contacted?    -> SUPPRESSED
    do we already have a good contact?           -> USE_EXISTING_DATA
    was this OWNER already looked up (any of     -> USE_EXISTING_DATA
      their properties, any strategy)?              (six properties, one lookup)
    did a recent lookup find nothing?            -> RETRY_LATER
    does the property pass the strategy?         -> INSUFFICIENT_OPPORTUNITY
    is a provider available at all?              -> RETRY_LATER (waiting for data)
    free source?                                 -> USE_FREE_SOURCE
    over the per-property cap?                   -> BUDGET_BLOCKED
    over the "needs a person" amount?            -> APPROVAL_REQUIRED
    budget left (display check)?                 -> BUDGET_BLOCKED
    otherwise                                    -> PAID_LOOKUP_APPROVED

Every decision is stored with its reasons, whether or not money moved. The
money itself moves only through `budget.reserve()` (atomic) — the display
check above is advisory; the reservation is the authority.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseCostEntry,
                                        EvoSenseEnrichmentDecision, EvoSenseProperty)
from app.services import wholesale_enrichment as WE
from app.services.evosense import budget as B
from app.services.evosense import common as C
from app.services.evosense import contacts as CT
from app.services.evosense import providers as PV
from app.services.evosense import strategy as ST

RECENT_LOOKUP_DAYS = 90
NO_MATCH_RETRY_DAYS = 7


def _decision(db, prop, strategy, owner, decision, reasons, *, provider=None, cost=None,
              user=None) -> EvoSenseEnrichmentDecision:
    row = EvoSenseEnrichmentDecision(
        organization_id=prop.organization_id, property_id=prop.id,
        strategy_id=getattr(strategy, "id", None), owner_id=getattr(owner, "id", None),
        capability=C.CONTACT_ENRICHMENT, decision=decision, reasons=C.jdump(reasons),
        provider_key=getattr(provider, "key", None), estimated_cost_cents=cost,
        decided_by="user" if user else "engine", decided_by_id=getattr(user, "id", None))
    db.add(row)
    db.flush()
    return row


def spent_on_property(db, prop) -> int:
    return int(db.query(func.coalesce(func.sum(EvoSenseCostEntry.total_cents), 0))
               .filter(EvoSenseCostEntry.organization_id == prop.organization_id,
                       EvoSenseCostEntry.property_id == prop.id,
                       EvoSenseCostEntry.status == "charged").scalar() or 0)


def pilot_spent(db, strategy) -> int:
    return int(db.query(func.coalesce(func.sum(EvoSenseCostEntry.total_cents), 0))
               .filter(EvoSenseCostEntry.organization_id == strategy.organization_id,
                       EvoSenseCostEntry.strategy_id == strategy.id,
                       EvoSenseCostEntry.status.in_(("charged", "reserved"))).scalar() or 0)


def decide(db, prop: EvoSenseProperty, strategy, *, user=None,
           approved: bool = False) -> EvoSenseEnrichmentDecision:
    db.flush()
    org_id = prop.organization_id
    ctl = C.controls(db, org_id)
    owner = CT.primary_owner(db, prop)

    if ctl.paused_all or ctl.paused_paid_data:
        return _decision(db, prop, strategy, owner, C.D_RETRY,
                         ["EvoSense is paused." if ctl.paused_all else "Paid data is paused."],
                         user=user)
    if owner is None:
        return _decision(db, prop, strategy, owner, C.D_INSUFFICIENT,
                         ["No owner of record is known, so there is nobody to look up."], user=user)
    from app.services.evosense.ingest import INSTITUTIONAL, OWNER_TYPE_LABELS
    flags = C.jload(getattr(owner, "review_flags", None), []) or []
    if owner.owner_type in INSTITUTIONAL:
        return _decision(db, prop, strategy, owner, C.D_INSUFFICIENT,
                         ["The owner of record is an institution (%s), not a typical seller. No contact "
                          "lookup is made." % OWNER_TYPE_LABELS.get(owner.owner_type, owner.owner_type).lower()],
                         user=user)
    if getattr(owner, "name_truncated", False):
        return _decision(db, prop, strategy, owner, C.D_INSUFFICIENT,
                         ["The source cut this owner's name at its field width (\"%s\"). A lookup on a "
                          "truncated name finds the wrong person; confirm the full name first."
                          % owner.display_name], user=user)
    if not approved and ({"ESTATE_INDICATED", "LIFE_ESTATE"} & set(flags)):
        return _decision(db, prop, strategy, owner, C.D_INSUFFICIENT,
                         ["The name of record indicates %s. Confirm who can sell before paying for a "
                          "lookup (approve it to look up anyway)."
                          % ("an estate" if "ESTATE_INDICATED" in flags else "a life estate")], user=user)

    cps = (db.query(EvoSenseContactPoint)
           .filter(EvoSenseContactPoint.organization_id == org_id,
                   EvoSenseContactPoint.owner_id == owner.id).all())
    if any(c.status in ("opted_out", "suppressed") for c in cps) and \
            not any(c.status == "active" for c in cps):
        return _decision(db, prop, strategy, owner, C.D_SUPPRESSED,
                         ["This owner's contact is on the do-not-contact / suppression list. "
                          "No money is spent looking them up again."], user=user)
    min_cc = getattr(strategy, "min_contact_confidence", 60) if strategy else 60
    if any(c.status == "active" for c in cps):
        best = max((CT.score_contact_point(db, prop, c)["value"] or 0) for c in cps
                   if c.status == "active")
        why = ("A contact is already on file with confidence %s." % best)
        if best >= min_cc or (owner.last_enriched_at and
                              owner.last_enriched_at > C.now() - timedelta(days=RECENT_LOOKUP_DAYS)):
            return _decision(db, prop, strategy, owner, C.D_USE_EXISTING, [why], user=user)
    if owner.last_enriched_at and owner.last_enriched_at > C.now() - timedelta(days=RECENT_LOOKUP_DAYS) \
            and cps:
        return _decision(db, prop, strategy, owner, C.D_USE_EXISTING,
                         ["This owner was already looked up on %s (they own other properties "
                          "here too); the result is reused, not bought again."
                          % owner.last_enriched_at.strftime("%b %d")], user=user)

    recent_miss = (db.query(EvoSenseEnrichmentDecision)
                   .filter(EvoSenseEnrichmentDecision.organization_id == org_id,
                           EvoSenseEnrichmentDecision.capability == C.CONTACT_ENRICHMENT,
                           EvoSenseEnrichmentDecision.owner_id == owner.id,
                           EvoSenseEnrichmentDecision.outcome.in_(("no_match", "provider_failed")),
                           EvoSenseEnrichmentDecision.created_at >
                           C.now() - timedelta(days=NO_MATCH_RETRY_DAYS)).first())
    if recent_miss is not None and not approved:
        return _decision(db, prop, strategy, owner, C.D_RETRY,
                         ["A lookup on %s found no usable contact (%s). EvoSense waits %s days "
                          "before paying again; add a contact manually if you have one."
                          % (recent_miss.created_at.strftime("%b %d"), recent_miss.outcome.replace("_", " "),
                             NO_MATCH_RETRY_DAYS)], user=user)

    threshold = getattr(strategy, "min_opportunity_score", 60) if strategy else 60
    if not approved and (prop.opportunity_score is None or prop.opportunity_score < threshold):
        return _decision(db, prop, strategy, owner, C.D_INSUFFICIENT,
                         ["Property Opportunity %s is below this strategy's %s; not worth paying "
                          "for a contact." % (prop.opportunity_score if prop.opportunity_score is not None
                                             else "INSUFFICIENT EVIDENCE", threshold)], user=user)

    prefs = (C.jload(getattr(strategy, "provider_preferences", None), {}) or {}).get(C.CONTACT_ENRICHMENT)
    routes = PV.route(db, org_id, C.CONTACT_ENRICHMENT, sandbox_allowed=bool(prop.is_test),
                      preferences=prefs)
    if not routes:
        return _decision(db, prop, strategy, owner, C.D_RETRY,
                         ["No contact-enrichment provider is connected" +
                          ("" if prop.is_test else " (sandbox providers never serve real properties)") +
                          ". WAITING FOR DATA — add a contact manually."], user=user)
    provider, cfg, cost = routes[0]
    if cost == 0:
        return _decision(db, prop, strategy, owner, C.D_FREE,
                         ["%s answers at no cost." % provider.label], provider=provider, cost=0, user=user)
    if ST.is_pilot(strategy):
        pcap = ST.pilot_spend_cap(strategy)
        pspent = pilot_spent(db, strategy)
        if pcap <= 0 or pspent + cost > pcap:
            return _decision(db, prop, strategy, owner, C.D_BUDGET,
                             ["PILOT MODE: paid lookups are off for this pilot." if pcap <= 0 else
                              "PILOT MODE: the pilot spend cap %s is reached (%s spent)."
                              % (C.money(pcap), C.money(pspent))],
                             provider=provider, cost=cost, user=user)
    cap = getattr(strategy, "max_cost_per_property_cents", None)
    already = spent_on_property(db, prop)
    if cap is not None and already + cost > cap:
        return _decision(db, prop, strategy, owner, C.D_BUDGET,
                         ["Per-property cap %s reached (%s already spent here)."
                          % (C.money(cap), C.money(already))], provider=provider, cost=cost, user=user)
    over = getattr(strategy, "approval_over_cents", None)
    if over is not None and cost > over and not approved:
        return _decision(db, prop, strategy, owner, C.D_APPROVAL,
                         ["%s lookup costs %s; this strategy asks a person above %s."
                          % (provider.label, C.money(cost), C.money(over))],
                         provider=provider, cost=cost, user=user)
    left = B.remaining(db, org_id, strategy)
    if left is not None and left < cost:
        return _decision(db, prop, strategy, owner, C.D_BUDGET,
                         ["BUDGET BLOCKED: %s left today, lookup costs %s." % (C.money(max(0, left)),
                                                                              C.money(cost))],
                         provider=provider, cost=cost, user=user)
    return _decision(db, prop, strategy, owner, C.D_PAID,
                     ["Property Opportunity %s passes the strategy; %s lookup costs %s; budget left %s."
                      % (prop.opportunity_score, provider.label, C.money(cost),
                         C.money(left) if left is not None else "unlimited")],
                     provider=provider, cost=cost, user=user)


def _input_for(prop, owner) -> WE.EnrichmentInput:
    return WE.EnrichmentInput(
        street_address=prop.street_address, city=prop.city, state=prop.state,
        zip_code=prop.zip_code, county=prop.county, parcel_apn=prop.parcel_apn,
        owner_name=owner.display_name if owner else None,
        business_name=owner.display_name if owner and owner.owner_type in ("llc", "corporation") else None,
        mailing_street=getattr(owner, "mailing_street", None), mailing_city=getattr(owner, "mailing_city", None),
        mailing_state=getattr(owner, "mailing_state", None), mailing_zip=getattr(owner, "mailing_zip", None))


def execute(db, prop, strategy, decision: EvoSenseEnrichmentDecision, *, user=None) -> Dict[str, Any]:
    """Carry out a PAID / FREE decision: reserve -> call -> charge or refund ->
    graph -> validate. Falls back to the next provider on failure, but only
    if the fallback also fits the budget. Never fabricates a contact."""
    if decision.decision not in (C.D_PAID, C.D_FREE):
        return {"outcome": "skipped", "decision": decision.decision}
    org_id = prop.organization_id
    owner = CT.primary_owner(db, prop)
    prefs = (C.jload(getattr(strategy, "provider_preferences", None), {}) or {}).get(C.CONTACT_ENRICHMENT)
    routes = PV.route(db, org_id, C.CONTACT_ENRICHMENT, sandbox_allowed=bool(prop.is_test),
                      preferences=prefs)
    attempts: List[Dict[str, Any]] = []
    touched = []
    outcome = "provider_failed"
    for provider, cfg, cost in routes:
        if cost > 0 and ST.is_pilot(strategy) and \
                pilot_spent(db, strategy) + cost > ST.pilot_spend_cap(strategy):
            attempts.append({"provider": provider.key, "result": "not attempted",
                             "reason": "PILOT MODE spend cap"})
            break
        ok, why, entry = B.reserve(db, org_id, strategy, cost, provider_key=provider.key,
                                   connector_kind=provider.connector_kind,
                                   capability=C.CONTACT_ENRICHMENT, operation="owner_contact_lookup",
                                   property_id=prop.id, owner_id=owner.id if owner else None,
                                   decision_id=decision.id, is_test=bool(prop.is_test))
        if not ok:
            attempts.append({"provider": provider.key, "result": "not attempted", "reason": why})
            if not attempts[:-1]:
                decision.decision = C.D_BUDGET
                decision.reasons = C.jdump(["BUDGET BLOCKED: %s — the %s lookup was refused before any "
                                            "call was made." % (why, C.money(cost))])
                decision.outcome = "skipped"
                C.log_event(db, org_id, "enrichment.budget_blocked", property_id=prop.id,
                            strategy_id=getattr(strategy, "id", None), is_test=prop.is_test,
                            summary="Lookup refused: %s" % why)
                return {"outcome": "budget_blocked", "reason": why, "attempts": attempts}
            outcome = "provider_failed"
            break
        try:
            result = provider.enrich(_input_for(prop, owner))
        except PV.ProviderRateLimited as exc:
            B.refund(db, entry)
            PV.record_failure(cfg, "rate limited", rate_limited_for=exc.retry_after_seconds)
            attempts.append({"provider": provider.key, "result": "rate_limited", "refunded": cost})
            continue
        except (PV.ProviderTimeout, PV.ProviderFailure, Exception) as exc:  # noqa: BLE001
            B.refund(db, entry)
            PV.record_failure(cfg, "%s: %s" % (type(exc).__name__, str(exc)[:160]))
            attempts.append({"provider": provider.key, "result": "failed",
                             "error": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                             "refunded": cost})
            C.log_event(db, org_id, "provider.failed", property_id=prop.id, is_test=prop.is_test,
                        summary="%s failed (%s); reservation of %s refunded"
                        % (provider.label, type(exc).__name__, C.money(cost)))
            continue
        PV.record_success(cfg)
        if result.status == WE.STATUS_SUCCEEDED and (result.phones or result.emails):
            B.charge(db, entry, success=True)
            touched = CT.apply_result(db, owner, result, provider)
            for cp in touched:
                if entry.contact_point_id is None:
                    entry.contact_point_id = cp.id
            attempts.append({"provider": provider.key, "result": "found", "charged": cost,
                             "contacts": len(touched)})
            outcome = "found"
            decision.provider_key = provider.key
            decision.ledger_id = entry.id
            break
        # NO MATCH: the provider answered and found nobody. That is a billable
        # answer (vendors charge for it) and it is NOT a failure: a fallback is
        # for a provider that could not answer, not a second paid opinion.
        B.charge(db, entry, success=False)
        owner.last_enriched_at = C.now()
        attempts.append({"provider": provider.key, "result": "no_match", "charged": cost})
        outcome = "no_match"
        decision.provider_key = provider.key
        decision.ledger_id = entry.id
        break
    decision.outcome = outcome
    if outcome != "found":
        parts = []
        for a in attempts:
            label = PV.PROVIDERS[a["provider"]].label if a["provider"] in PV.PROVIDERS else a["provider"]
            if a["result"] == "failed":
                parts.append("%s failed (%s) — reservation refunded" % (label, a.get("error", "error").split(":")[0]))
            elif a["result"] == "rate_limited":
                parts.append("%s rate limited — refunded" % label)
            elif a["result"] == "no_match":
                parts.append("%s answered: no contact on file (charged %s)" % (label, C.money(a.get("charged"))))
            elif a["result"] == "not attempted":
                parts.append("%s not attempted: %s" % (label, a.get("reason")))
        head = ("WAITING FOR DATA — %s. Nothing was invented; add a contact manually if you have one."
                % "; ".join(parts or ["no provider could answer"]))
        decision.reasons = C.jdump([head] + (C.jload(decision.reasons, []) or []))
    if outcome == "found":
        validate_contacts(db, prop, strategy, touched)
    summary = {"found": "Contact found", "no_match": "No contact found",
               "provider_failed": "Provider failed"}[outcome]
    C.log_event(db, org_id, "enrichment." + outcome, property_id=prop.id,
                strategy_id=getattr(strategy, "id", None), is_test=prop.is_test,
                actor_type=C.ACTOR_AUTOMATION if not user else C.ACTOR_USER, user=user,
                summary="%s — %s" % (summary, "; ".join("%s: %s" % (a["provider"], a["result"])
                                                        for a in attempts)),
                details={"attempts": attempts, "decision": decision.id})
    return {"outcome": outcome, "attempts": attempts, "contacts": [c.id for c in touched]}


def validate_contacts(db, prop, strategy, cps) -> None:
    """Phone validation, through the same budget. If the budget refuses, the
    number stays UNVERIFIED — it is never marked valid on faith."""
    for cp in cps:
        if cp.kind != "phone" or cp.validation == "valid" or cp.status != "active":
            continue
        routes = PV.route(db, prop.organization_id, C.PHONE_VALIDATION,
                          sandbox_allowed=bool(prop.is_test))
        if not routes:
            continue
        provider, cfg, cost = routes[0]
        ok, why, entry = B.reserve(db, prop.organization_id, strategy, cost,
                                   provider_key=provider.key, connector_kind=provider.connector_kind,
                                   capability=C.PHONE_VALIDATION, operation="phone_validation",
                                   property_id=prop.id, owner_id=cp.owner_id, is_test=bool(prop.is_test))
        if not ok:
            C.log_event(db, prop.organization_id, "validation.skipped", property_id=prop.id,
                        is_test=prop.is_test, summary="Phone validation skipped: %s" % why)
            continue
        try:
            res = provider.validate_phone(cp.value)
        except Exception as exc:  # noqa: BLE001
            B.refund(db, entry)
            PV.record_failure(cfg, str(exc)[:160])
            continue
        PV.record_success(cfg)
        B.charge(db, entry, success=True)
        entry.contact_point_id = cp.id
        cp.validation = res.get("validation") or "unverified"
        cp.line_type = res.get("line_type") or cp.line_type
        cp.validated_at = C.now()
        if cp.validation == "invalid":
            cp.status = "invalid"
            cp.status_reason = "Failed phone validation"


def run(db, prop, strategy, *, user=None, approved: bool = False) -> Dict[str, Any]:
    """decide + execute + rescore, for one property."""
    from app.services.evosense import evaluate as EV
    dec = decide(db, prop, strategy, user=user, approved=approved)
    result = {"decision": dec.decision, "reasons": C.jload(dec.reasons, []),
              "estimated_cost_cents": dec.estimated_cost_cents}
    if dec.decision in (C.D_PAID, C.D_FREE):
        result.update(execute(db, prop, strategy, dec, user=user))
        result["decision"] = dec.decision
        result["reasons"] = C.jload(dec.reasons, [])
    else:
        dec.outcome = "skipped"
    EV.rescore(db, prop, strategy)
    return result
