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


# ── ENRICHMENT POLICY ───────────────────────────────────────────────────────
# Whether paying for contact data is on the table at all, per strategy. Every
# paid lookup is still bounded by the budgets, caps and pilot limits below; a
# policy can only make the engine MORE careful than those, or (aggressive) let
# it pay for a slightly weaker property - never past a budget.
#
#   free_only        never pays; free sources only
#   standard         pays when Property Opportunity meets the strategy minimum
#   aggressive       pays down to AGGRESSIVE_MARGIN below that minimum
#   manual_approval  every paid lookup waits for a person
P_FREE_ONLY, P_STANDARD, P_AGGRESSIVE, P_MANUAL = ("free_only", "standard", "aggressive",
                                                   "manual_approval")
POLICIES = (P_FREE_ONLY, P_STANDARD, P_AGGRESSIVE, P_MANUAL)
POLICY_LABELS = {P_FREE_ONLY: "FREE ONLY", P_STANDARD: "STANDARD", P_AGGRESSIVE: "AGGRESSIVE",
                 P_MANUAL: "MANUAL APPROVAL"}
AGGRESSIVE_MARGIN = 15


def policy_of(strategy) -> str:
    p = (getattr(strategy, "enrichment_policy", None) or P_STANDARD) if strategy else P_STANDARD
    return p if p in POLICIES else P_STANDARD


def _decision(db, prop, strategy, owner, decision, reasons, *, provider=None, cost=None,
              user=None) -> EvoSenseEnrichmentDecision:
    row = EvoSenseEnrichmentDecision(
        organization_id=prop.organization_id, property_id=prop.id,
        strategy_id=getattr(strategy, "id", None), owner_id=getattr(owner, "id", None),
        capability=C.CONTACT_ENRICHMENT, decision=decision, reasons=C.jdump(reasons),
        provider_key=getattr(provider, "key", None), estimated_cost_cents=cost,
        decided_by="user" if user else "engine", decided_by_id=getattr(user, "id", None),
        policy=policy_of(strategy), opportunity_score=prop.opportunity_score,
        confidence_before=prop.contact_confidence)
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

    # VALUE OF INFORMATION: would another lookup change anything? Checked
    # before any provider or price is considered. No ROI is invented; these
    # are the cases the system already KNOWS a purchase is wasted.
    if getattr(prop, "contactability", None) == "DO_NOT_CONTACT":
        return _decision(db, prop, strategy, owner, C.D_SUPPRESSED,
                         ["Do not contact: every channel is blocked for this owner. A lookup could "
                          "not make them reachable."], user=user)
    if getattr(prop, "promoted_deal_id", None):
        return _decision(db, prop, strategy, owner, C.D_USE_EXISTING,
                         ["Already a Wholesale deal with a seller attached; contact comes from the "
                          "deal, not a new lookup."], user=user)
    if getattr(prop, "seller_intent", None) == 0:
        return _decision(db, prop, strategy, owner, C.D_INSUFFICIENT,
                         ["The owner already said no (Seller Intent 0). Paying to find them again "
                          "would not change the answer."], user=user)
    if not approved and getattr(prop, "data_confidence", None) == "insufficient" \
            and policy_of(strategy) != P_AGGRESSIVE:
        return _decision(db, prop, strategy, owner, C.D_INSUFFICIENT,
                         ["The property's own evidence is too thin (data confidence INSUFFICIENT) to "
                          "justify paying for a contact yet."], user=user)

    policy = policy_of(strategy)
    threshold = getattr(strategy, "min_opportunity_score", 60) if strategy else 60
    if policy == P_AGGRESSIVE:
        threshold = max(0, threshold - AGGRESSIVE_MARGIN)
    if not approved and (prop.opportunity_score is None or prop.opportunity_score < threshold):
        return _decision(db, prop, strategy, owner, C.D_INSUFFICIENT,
                         ["Property Opportunity %s is below this strategy's %s%s; not worth paying "
                          "for a contact." % (prop.opportunity_score if prop.opportunity_score is not None
                                             else "INSUFFICIENT EVIDENCE", threshold,
                                             " (AGGRESSIVE policy)" if policy == P_AGGRESSIVE else "")],
                         user=user)

    prefs = (C.jload(getattr(strategy, "provider_preferences", None), {}) or {}).get(C.CONTACT_ENRICHMENT)
    routes = PV.route(db, org_id, C.CONTACT_ENRICHMENT, sandbox_allowed=bool(prop.is_test),
                      preferences=prefs)
    if not routes:
        return _decision(db, prop, strategy, owner, C.D_RETRY,
                         ["No contact-enrichment provider is connected" +
                          ("" if prop.is_test else " (sandbox providers never serve real properties)") +
                          ". WAITING FOR DATA — add a contact manually."], user=user)
    if policy == P_FREE_ONLY:
        free = [r for r in routes if r[2] == 0]
        if not free:
            return _decision(db, prop, strategy, owner, C.D_POLICY,
                             ["Policy FREE ONLY: no free source can supply this owner's contact; the "
                              "cheapest connected provider costs %s and this strategy never pays."
                              % C.money(min(r[2] for r in routes))], user=user)
        routes = free
    provider, cfg, cost = routes[0]
    if provider.key == "tracerfy_contact":
        stop = tracerfy_gate(db, prop, approved=approved)
        if stop:
            return _decision(db, prop, strategy, owner, stop[0], [stop[1]], provider=provider, cost=cost,
                             user=user)
    if cost == 0:
        return _decision(db, prop, strategy, owner, C.D_FREE,
                         ["%s answers at no cost." % provider.label], provider=provider, cost=0, user=user)
    if policy == P_MANUAL and not approved:
        return _decision(db, prop, strategy, owner, C.D_APPROVAL,
                         ["Policy MANUAL APPROVAL: every paid lookup waits for a person. %s would "
                          "cost %s." % (provider.label, C.money(cost))],
                         provider=provider, cost=cost, user=user)
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


AUTO_LOOKUP_MIN_SCORE = 65


def tracerfy_gate(db, prop, *, approved: bool = False):
    """(decision, reason) when an automatic Tracerfy lookup must not run, else None.
    Only properties scoring 65+ (unless a person approved it), and only inside
    the organization's Wholesale Settings paid-lookup limits - where 0 means OFF,
    never unlimited."""
    if not approved and (prop.opportunity_score or 0) < AUTO_LOOKUP_MIN_SCORE:
        return (C.D_INSUFFICIENT, "Automatic paid lookups start at Property Opportunity %s; this one is %s."
                % (AUTO_LOOKUP_MIN_SCORE, prop.opportunity_score if prop.opportunity_score is not None
                   else "not scored"))
    from app.services import wholesale_service as WS
    settings = WS.resolve_settings(db, prop.organization_id, commit=False)
    refusal = WE.admit(db, prop.organization_id, WE.PROVIDERS["tracerfy"], settings, requested=1)
    if refusal:
        return (C.D_BUDGET, "Wholesale Settings paid-lookup limit: " + refusal)
    return None


def record_wholesale_lookup(db, prop, owner, result, *, cost_cents: int = 0, error: str = None) -> None:
    """Count an automatic Tracerfy lookup against the organization's Wholesale
    paid-lookup limits, exactly like a lookup from the Get phones & emails button."""
    import json as _json
    from datetime import datetime as _dt
    from app.models.wholesale_models import ACTOR_AUTOMATION, WholesaleEnrichmentRequest
    data = _input_for(prop, owner)
    inputs = _json.loads(data.to_json())
    inputs["evosense_property_id"] = prop.id
    row = WholesaleEnrichmentRequest(
        organization_id=prop.organization_id, property_id=None, provider="tracerfy",
        requested_by_actor=ACTOR_AUTOMATION, inputs=_json.dumps(inputs),
        status=(result.status if result is not None else WE.STATUS_FAILED),
        result=result.to_json() if result is not None else None,
        billable=bool(result is not None and result.billable), cost_cents=cost_cents,
        error=error, completed_at=_dt.utcnow())
    db.add(row)
    db.flush()


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
    # A fallback may never be something the policy would not have allowed:
    # a free decision never falls back to a paid provider, and an approved
    # lookup never falls back to one dearer than what the person approved.
    policy = policy_of(strategy)
    if decision.decision == C.D_FREE or policy == P_FREE_ONLY:
        routes = [r for r in routes if r[2] == 0]
    elif policy == P_MANUAL and decision.estimated_cost_cents is not None:
        routes = [r for r in routes if r[2] <= decision.estimated_cost_cents]
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
            if provider.key == "tracerfy_contact":
                record_wholesale_lookup(db, prop, owner, result, cost_cents=result.cost_cents or 0)
        except PV.ProviderRateLimited as exc:
            B.refund(db, entry)
            PV.record_failure(cfg, "rate limited", rate_limited_for=exc.retry_after_seconds,
                              capability=C.CONTACT_ENRICHMENT)
            attempts.append({"provider": provider.key, "result": "rate_limited", "refunded": cost})
            continue
        except (PV.ProviderTimeout, PV.ProviderFailure, Exception) as exc:  # noqa: BLE001
            B.refund(db, entry)
            if provider.key == "tracerfy_contact":
                record_wholesale_lookup(db, prop, owner, None, error="%s: %s" % (type(exc).__name__, str(exc)[:160]))
            PV.record_failure(cfg, "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                              capability=C.CONTACT_ENRICHMENT)
            attempts.append({"provider": provider.key, "result": "failed",
                             "error": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                             "refunded": cost})
            C.log_event(db, org_id, "provider.failed", property_id=prop.id, is_test=prop.is_test,
                        summary="%s failed (%s); reservation of %s refunded"
                        % (provider.label, type(exc).__name__, C.money(cost)))
            continue
        PV.record_success(cfg, capability=C.CONTACT_ENRICHMENT)
        if result.status == WE.STATUS_SUCCEEDED and (result.phones or result.emails):
            if result.cost_cents is not None and getattr(provider, "charge_on_miss", True) is False:
                B.settle(db, entry, result.cost_cents, success=True)     # what the vendor billed
            else:
                B.charge(db, entry, success=True)
            touched = CT.apply_result(db, owner, result, provider)
            for cp in touched:
                if entry.contact_point_id is None:
                    entry.contact_point_id = cp.id
            attempts.append({"provider": provider.key, "result": "found",
                             "charged": result.cost_cents if (result.cost_cents is not None and
                                                              getattr(provider, "charge_on_miss", True) is False) else cost,
                             "contacts": len(touched)})
            outcome = "found"
            decision.provider_key = provider.key
            decision.ledger_id = entry.id
            break
        # NO MATCH: the provider answered and found nobody. That is a billable
        # answer (vendors charge for it) and it is NOT a failure: a fallback is
        # for a provider that could not answer, not a second paid opinion.
        if getattr(provider, "charge_on_miss", True) is False:
            B.settle(db, entry, result.cost_cents or 0, success=False)   # a miss is free
        else:
            B.charge(db, entry, success=False)
        owner.last_enriched_at = C.now()
        attempts.append({"provider": provider.key, "result": "no_match",
                         "charged": (result.cost_cents or 0) if getattr(provider, "charge_on_miss", True) is False
                         else cost})
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
            PV.record_failure(cfg, str(exc)[:160], capability=C.PHONE_VALIDATION)
            continue
        PV.record_success(cfg, capability=C.PHONE_VALIDATION)
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
    # What the lookup did to Contact Confidence - the other half of "was it
    # worth it". Recorded on the decision, next to what it cost.
    dec.confidence_after = prop.contact_confidence
    result["confidence_before"] = dec.confidence_before
    result["confidence_after"] = dec.confidence_after
    return result
