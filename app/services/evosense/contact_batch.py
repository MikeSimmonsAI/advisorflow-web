"""Contact-lookup batches - over an EXISTING list (the Nov 3 Top 100) or the
opportunity queue of every distressed property that needs a contact.

    preflight  free: who is ready, who needs a person first, who is skipped
               and why; duplicate owners; the provider's own price with its
               source and date; the maximum the batch could cost
    create     a draft batch with its items (nothing bought)
    approve    the ONE step that authorizes spend: a typed phrase naming the
               count and the dollar cap. It switches the provider on, raises
               the paid-lookup caps to fit the batch and sets the org's daily
               spend ceiling - every change recorded before / after
    run_step   the scheduler (or the Start button) works the items in order
               through enrichment.run - the one per-property path with the
               owner-match guard, the cost ledger and duplicate-billing
               protection. Pause / resume / stop / emergency stop.

Safety rules that never bend:
  * nothing is looked up for a batch that is not approved and running;
  * the batch stops at its own dollar cap (unit cost x lookups so far, and the
    actual ledger), before the org's daily ceiling, before the Wholesale caps;
  * an item interrupted mid-call (a crash, a restart) is NEVER retried
    automatically - the vendor may have billed - it goes to a person;
  * a no-match is never retried; a provider failure (refunded) at most twice;
  * estates, trusts, companies, unknown or unverified owners go to a person;
    no identity is guessed.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.services.evosense import common as C

PROVIDER = "tracerfy_contact"
MAX_ATTEMPTS = 2
STEP = 10                                  # items per run_step
LEASE = timedelta(minutes=10)
INTERRUPTED_AFTER = timedelta(minutes=15)  # an item "processing" this long died mid-call
REVIEW_OWNER_TYPES = ("estate", "estate_indicated", "life_estate", "trust", "llc", "corporation", "unknown",
                      "government", "nonprofit", "bank", "church")
CONF_RANK = {"verified": 0, "partial": 1, "insufficient": 2}

STATE_LABELS = {"ready": "Ready for lookup", "review": "Needs a person first", "skipped": "Skipped",
                "processing": "Looking up", "found": "Contact found", "no_match": "No verified match",
                "failed": "Provider failed (refunded)", "interrupted": "Interrupted - a person checks billing",
                "cap_reached": "Not looked up - cap reached"}


def _money(c: Optional[int]) -> str:
    return "$%.2f" % ((c or 0) / 100.0)


def _norm(name: Optional[str]) -> str:
    return re.sub(r"[^A-Z ]", "", (name or "").upper()).strip()


def members(db, org_id: str, source: str) -> List[Any]:
    from app.models.evosense_models import EvoSenseProperty
    from app.services.evosense import cohort as CO
    if source.startswith("list:"):
        return CO.members(db, org_id, source[5:])
    if source == "queue:find_contact":
        return (db.query(EvoSenseProperty)
                .filter(EvoSenseProperty.organization_id == org_id, EvoSenseProperty.archived_at.is_(None),
                        EvoSenseProperty.next_best_action.in_(("find_contact", "escalate_deadline")))
                .order_by(EvoSenseProperty.priority_score.desc().nullslast()).limit(500).all())
    raise ValueError("Unknown source")


def provider_facts(db, org_id: str) -> Dict[str, Any]:
    """The provider the batch would use, its OWN price with source and date,
    and every switch that stands between it and a lookup."""
    from app.services import skiptrace_costing as SKC
    from app.services import wholesale_service as WS
    from app.services.evosense import providers as PV
    p = PV.PROVIDERS[PROVIDER]
    cfg = PV.config(db, org_id, PROVIDER)
    settings = WS.resolve_settings(db, org_id, commit=False)
    ctl = C.controls(db, org_id)
    cat = next((e for e in SKC.CATALOGUE if e["provider"] == "tracerfy" and e["product"] == "instant"), {})
    unit = int(p.costs.get(C.CONTACT_ENRICHMENT, 0))
    switches = {
        "credential": {"ok": p.is_configured(), "what": "TRACERFY_API_TOKEN on the server"},
        "provider_on": {"ok": bool(cfg.enabled), "what": "Tracerfy owner lookup switched on for this workspace"},
        "daily_cap": {"ok": bool(settings.enrichment_daily_cap), "value": settings.enrichment_daily_cap,
                      "what": "Wholesale Settings daily paid-lookup cap (0 = off)"},
        "monthly_cap": {"ok": bool(settings.enrichment_monthly_cap), "value": settings.enrichment_monthly_cap,
                        "what": "Wholesale Settings monthly paid-lookup cap (0 = off)"},
        "per_run": {"ok": True, "value": settings.enrichment_max_records_per_run,
                    "what": "Wholesale Settings records per run"},
        "paid_data_running": {"ok": not (ctl.paused_paid_data or ctl.paused_all),
                              "what": "Controls: paid data not paused"},
        "org_daily_budget": {"ok": True, "value": ctl.org_daily_budget_cents,
                             "what": "Controls: organization daily spend ceiling (cents)"},
    }
    return {"provider": PROVIDER, "label": p.label, "unit_cost_cents": unit,
            "charge_on_miss": bool(getattr(p, "charge_on_miss", True)),
            "price": {"per_hit": _money(unit), "per_miss": "$0.00" if not getattr(p, "charge_on_miss", True)
                      else _money(unit), "status": cat.get("price_status"), "source": cat.get("source_url"),
                      "read_on": cat.get("verified_at"), "minimum_purchase": _money(int(cat.get(
                          "minimum_purchase_cents") or 0)) if cat.get("minimum_purchase_cents") else None,
                      "note": "Read from the vendor's published documentation on the date shown - confirm on "
                              "the vendor's pricing page before approving. The account's credit balance is "
                              "not visible from here."},
            "switches": switches,
            "ready_after_approval": switches["credential"]["ok"],
            "blocked_now": [s["what"] for s in switches.values() if not s["ok"]]}


def _classify(p, owner, owner_status: Dict[str, Any], contacts: Dict[str, Any], flags: List[str]) -> Tuple[str, str]:
    from app.services.evosense.ingest import INSTITUTIONAL
    if p.promoted_deal_id:
        return "skipped", "Already a Wholesale deal - the seller is attached there"
    if getattr(p, "contactability", None) == "DO_NOT_CONTACT":
        return "skipped", "Do not contact - every channel is blocked for this owner"
    if contacts.get("phones") or contacts.get("emails"):
        return "skipped", "A phone or email is already on file for the current owner"
    if owner is None:
        return "review", "No owner of record - find the owner first"
    if owner_status.get("status") == "CONFLICT":
        return "review", "Sources disagree about the current owner"
    if owner_status.get("status") != "VERIFIED":
        return "review", "Owner not confirmed on an official county record"
    if owner.owner_type in INSTITUTIONAL:
        return "skipped", "Institutional owner - not a typical seller"
    if owner.owner_type in REVIEW_OWNER_TYPES or {"ESTATE_INDICATED", "LIFE_ESTATE"} & set(flags):
        kind = {"estate_indicated": "an estate", "estate": "an estate", "life_estate": "a life estate",
                "trust": "a trust", "llc": "a company (LLC)",
                "corporation": "a company", "unknown": "an owner type the record does not state"}.get(
            owner.owner_type, "an estate / life estate")
        return "review", "The owner of record is %s - confirm who can sell before paying for a lookup" % kind
    if getattr(owner, "name_truncated", False):
        return "review", "The owner's name was cut off by the source - confirm the full name first"
    if owner.last_enriched_at and owner.last_enriched_at > C.now() - timedelta(days=90):
        return "skipped", "Looked up on %s - the result is reused, not bought again" % owner.last_enriched_at.strftime(
            "%b %d")
    return "ready", "Verified owner, no contact on file"


def preflight(db, org_id: str, source: str) -> Dict[str, Any]:
    from app.services.evosense import cohort as CO
    from app.services.evosense.ingest import owner_flags
    props = members(db, org_id, source)
    pre = CO._prefetch(db, org_id, props)
    rows, seen = [], {}
    for p in props:
        owners = CO._owners_of(pre, p)
        owner = owners[0] if owners else None
        ost = CO._owner_status_pre(pre, p)
        contacts = CO._contacts_pre(pre, ost.get("owner_id"))
        flags = list(set((C.jload(getattr(owner, "review_flags", None), []) or []) +
                         owner_flags(getattr(owner, "display_name", "") or "")))
        state, reason = _classify(p, owner, ost, contacts, flags)
        dup_key = None
        if owner is not None:
            dup_key = owner.id
            name_key = (_norm(owner.display_name), _norm(owner.mailing_street))
            if state == "ready":
                other = seen.get(owner.id) or seen.get(name_key)
                if other is not None:
                    state, reason = "skipped", "Same owner as %s - one lookup covers both" % other
                else:
                    seen[owner.id] = seen[name_key] = p.street_address
        rows.append({"property_id": p.id, "owner_id": getattr(owner, "id", None), "state": state, "reason": reason,
                     "address": p.street_address, "city": p.city, "county": p.county,
                     "owner_type": getattr(owner, "owner_type", None), "owner_status": ost.get("status"),
                     "priority": p.priority_score, "urgency": p.urgency_score,
                     "confidence": p.evidence_confidence,
                     "sale_deadline": p.sale_deadline.isoformat() if p.sale_deadline else None,
                     "has_mailing": bool(getattr(owner, "mailing_street", None)), "dup_key": dup_key})

    def order(r):
        return (0 if r["state"] == "ready" else 1 if r["state"] == "review" else 2,
                0 if r["owner_status"] == "VERIFIED" else 1, -(r["urgency"] or 0), -(r["priority"] or 0),
                CONF_RANK.get(r["confidence"] or "", 3), 0 if r["has_mailing"] else 1, r["address"] or "")
    rows.sort(key=order)
    for i, r in enumerate(rows, 1):
        r["position"] = i
    facts = provider_facts(db, org_id)
    ready = [r for r in rows if r["state"] == "ready"]
    owners = {r["owner_id"] for r in rows if r["owner_id"]}
    dups = [r for r in rows if r["reason"].startswith("Same owner")]
    unit = facts["unit_cost_cents"]
    summary = {"source": source, "properties": len(rows), "unique_owners": len(owners),
               "duplicate_owners": len(dups), "ready": len(ready),
               "manual_review": sum(1 for r in rows if r["state"] == "review"),
               "skipped": sum(1 for r in rows if r["state"] == "skipped"),
               "estimated_lookups": len(ready),
               "max_spend_cents": unit * len(ready) if not facts["charge_on_miss"] else unit * len(ready),
               "max_spend": _money(unit * len(ready)),
               "spend_note": ("Maximum assumes every lookup finds someone; a miss costs %s." %
                              facts["price"]["per_miss"]),
               "review_reasons": _count(r["reason"] for r in rows if r["state"] == "review"),
               "skip_reasons": _count(r["reason"] for r in rows if r["state"] == "skipped"),
               "provider": facts}
    return {"summary": summary, "rows": rows}


def _count(xs) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for x in xs:
        k = re.sub(r" as .*$", "", x) if x.startswith("Same owner") else x
        k = re.sub(r"Looked up on .* - ", "Looked up recently - ", k)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def create(db, org_id: str, source: str, *, user=None, max_lookups: Optional[int] = None) -> Any:
    """A draft batch with its items. Nothing is bought."""
    from app.models.evosense_models import EvoSenseContactBatch, EvoSenseContactBatchItem
    pf = preflight(db, org_id, source)
    ready = [r for r in pf["rows"] if r["state"] == "ready"]
    n = len(ready) if max_lookups is None else max(0, min(int(max_lookups), len(ready)))
    unit = pf["summary"]["provider"]["unit_cost_cents"]
    b = EvoSenseContactBatch(organization_id=org_id, source=source, status="draft", provider_key=PROVIDER,
                             unit_cost_cents=unit, charge_on_miss=pf["summary"]["provider"]["charge_on_miss"],
                             max_lookups=n, max_spend_cents=unit * n,
                             preflight=json.dumps({k: v for k, v in pf["summary"].items() if k != "provider"}),
                             created_by_id=getattr(user, "id", None))
    db.add(b)
    db.flush()
    chosen = {r["property_id"] for r in ready[:n]}
    for r in pf["rows"]:
        state, reason = r["state"], r["reason"]
        if state == "ready" and r["property_id"] not in chosen:
            state, reason = "skipped", "Beyond this batch's size (%d)" % n
        db.add(EvoSenseContactBatchItem(batch_id=b.id, organization_id=org_id, property_id=r["property_id"],
                                        owner_id=r["owner_id"], position=r["position"], state=state,
                                        reason=reason[:250]))
    C.log_event(db, org_id, "contact_batch.created", user=user, actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM,
                summary="Contact-lookup batch prepared from %s: %d ready, %d need a person, max %s (nothing bought)"
                % (source, n, pf["summary"]["manual_review"], _money(unit * n)), details={"batch": b.id})
    db.flush()
    return b


def approval_phrase(b) -> str:
    return "APPROVE %d LOOKUPS UP TO %s" % (b.max_lookups or 0, _money(b.max_spend_cents))


class ApprovalRefused(PermissionError):
    pass


def approve(db, org_id: str, b, phrase: Optional[str], user, *, daily_lookups: Optional[int] = None) -> Dict[str, Any]:
    """THE spend authorization. Refused unless the typed phrase matches
    exactly. Turns on only what the batch needs, records before / after."""
    from app.services import wholesale_service as WS
    from app.services.evosense import providers as PV
    if b.status != "draft":
        raise ApprovalRefused("This batch is %s, not a draft." % b.status)
    want = approval_phrase(b)
    if (phrase or "").strip().upper() != want:
        raise ApprovalRefused('Type exactly: %s' % want)
    if not b.max_lookups:
        raise ApprovalRefused("Nothing in this batch is ready for a lookup.")
    p = PV.PROVIDERS[PROVIDER]
    if not p.is_configured():
        raise ApprovalRefused("Tracerfy is not connected on the server (TRACERFY_API_TOKEN).")
    settings = WS.resolve_settings(db, org_id, commit=False)
    ctl = C.controls(db, org_id)
    cfg = PV.config(db, org_id, PROVIDER)
    daily = int(daily_lookups or b.max_lookups)
    before = {"provider_on": bool(cfg.enabled), "daily_cap": settings.enrichment_daily_cap,
              "monthly_cap": settings.enrichment_monthly_cap, "per_run": settings.enrichment_max_records_per_run,
              "paused_paid_data": bool(ctl.paused_paid_data), "org_daily_budget_cents": ctl.org_daily_budget_cents}
    cfg.enabled = True
    # The Wholesale caps count BILLABLE lookups already made in their window, so
    # the batch's room is added on top of what is already used - otherwise a
    # second batch would be refused part-way by a cap its own approval set.
    from app.models.wholesale_models import WholesaleEnrichmentRequest as _WER
    now = datetime.utcnow()

    def used(since):
        return (db.query(_WER).filter(_WER.organization_id == org_id, _WER.billable.is_(True),
                                      _WER.created_at >= since).count())
    settings.enrichment_daily_cap = max(int(settings.enrichment_daily_cap or 0),
                                        used(now - timedelta(days=1)) + daily)
    settings.enrichment_monthly_cap = max(int(settings.enrichment_monthly_cap or 0),
                                          used(now - timedelta(days=30)) + int(b.max_lookups))
    ctl.paused_paid_data = False
    ceiling = (b.unit_cost_cents or 0) * daily
    if ctl.org_daily_budget_cents is None or ctl.org_daily_budget_cents < ceiling:
        ctl.org_daily_budget_cents = ceiling
    b.daily_spend_cents = ceiling
    after = {"provider_on": True, "daily_cap": settings.enrichment_daily_cap,
             "monthly_cap": settings.enrichment_monthly_cap, "per_run": settings.enrichment_max_records_per_run,
             "paused_paid_data": False, "org_daily_budget_cents": ctl.org_daily_budget_cents}
    b.status = "approved"
    b.approval_phrase = want
    b.approved_by_id = getattr(user, "id", None)
    b.approved_at = C.now()
    b.activation = json.dumps({"before": before, "after": after})
    C.log_event(db, org_id, "contact_batch.approved", user=user, actor_type=C.ACTOR_USER,
                summary="Contact-lookup batch approved by %s: up to %d lookups, cap %s"
                % (getattr(user, "email", "a person"), b.max_lookups, _money(b.max_spend_cents)),
                details={"batch": b.id, "before": before, "after": after})
    db.flush()
    return {"before": before, "after": after}


def set_status(db, org_id: str, b, action: str, user=None, reason: Optional[str] = None) -> None:
    """start | pause | resume | stop | emergency_stop."""
    allowed = {"start": ("approved", "paused"), "resume": ("paused",), "pause": ("running",),
               "stop": ("approved", "running", "paused", "draft"),
               "emergency_stop": ("approved", "running", "paused", "draft", "completed", "stopped")}
    if b.status not in allowed[action]:
        raise ApprovalRefused("Cannot %s a batch that is %s." % (action.replace("_", " "), b.status))
    if action in ("start", "resume"):
        b.status = "running"
        b.started_at = b.started_at or C.now()
    elif action == "pause":
        b.status = "paused"
    elif b.status not in ("completed", "stopped"):
        b.status = "stopped"
        b.stopped_reason = reason or ("Emergency stop" if action == "emergency_stop" else "Stopped by a person")
        b.finished_at = C.now()
    if action == "emergency_stop":
        ctl = C.controls(db, org_id)
        ctl.paused_paid_data = True                # every paid lookup in the workspace stops
    C.log_event(db, org_id, "contact_batch." + action, user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM,
                summary="Contact-lookup batch %s%s" % (action.replace("_", " "),
                                                       (": " + reason) if reason else ""),
                details={"batch": b.id})
    db.flush()


def _ledger_spent(db, b) -> int:
    from sqlalchemy import func
    from app.models.evosense_models import EvoSenseContactBatchItem, EvoSenseCostEntry
    ids = [d for (d,) in db.query(EvoSenseContactBatchItem.decision_id)
           .filter(EvoSenseContactBatchItem.batch_id == b.id,
                   EvoSenseContactBatchItem.decision_id.isnot(None)).all()]
    if not ids:
        return 0
    return int(db.query(func.coalesce(func.sum(EvoSenseCostEntry.total_cents), 0))
               .filter(EvoSenseCostEntry.decision_id.in_(ids),
                       EvoSenseCostEntry.status.in_(("charged", "failed_charged", "reserved"))).scalar() or 0)


def run_step(db, org_id: str, b, *, limit: int = STEP, now: Optional[datetime] = None) -> Dict[str, int]:
    """Work up to `limit` items of a RUNNING batch, in order. Idempotent and
    restart-safe: one worker holds the lease; an item left 'processing' by a
    crash becomes 'interrupted' (never retried automatically)."""
    from app.models.evosense_models import EvoSenseContactBatchItem, EvoSenseEnrichmentDecision, EvoSenseProperty
    from app.services.evosense import enrichment as EN
    from app.services.evosense import evaluate as EV
    now = now or C.now()
    out = {"looked_up": 0, "found": 0, "no_match": 0, "failed": 0, "interrupted": 0}
    if b.status != "running":
        return out
    if b.lease_until and b.lease_until > now:
        return out
    b.lease_until = now + LEASE
    db.commit()
    for it in (db.query(EvoSenseContactBatchItem)
               .filter(EvoSenseContactBatchItem.batch_id == b.id, EvoSenseContactBatchItem.state == "processing",
                       EvoSenseContactBatchItem.started_at < now - INTERRUPTED_AFTER).all()):
        it.state, it.reason = "interrupted", "Stopped mid-lookup (restart). Not retried: check the vendor's billing."
        out["interrupted"] += 1
    db.commit()
    done = 0
    while done < limit:
        db.refresh(b)
        if b.status != "running":
            break
        if (b.looked_up or 0) >= (b.max_lookups or 0):
            _finish(db, b, None)
            break
        spent = max(b.spent_cents or 0, _ledger_spent(db, b))
        if b.max_spend_cents is not None and spent + (b.unit_cost_cents or 0) > b.max_spend_cents:
            _finish(db, b, "Batch spending cap reached (%s of %s)" % (_money(spent), _money(b.max_spend_cents)),
                    cap=True)
            break
        it = (db.query(EvoSenseContactBatchItem)
              .filter(EvoSenseContactBatchItem.batch_id == b.id,
                      ((EvoSenseContactBatchItem.state == "ready") |
                       ((EvoSenseContactBatchItem.state == "failed") &
                        (EvoSenseContactBatchItem.attempts < MAX_ATTEMPTS))))
              .order_by(EvoSenseContactBatchItem.attempts.asc(), EvoSenseContactBatchItem.position.asc()).first())
        if it is None:
            _finish(db, b, None)
            break
        it.state, it.started_at, it.attempts = "processing", C.now(), (it.attempts or 0) + 1
        db.commit()                                  # claimed before any call is made
        prop = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == it.property_id,
                                                 EvoSenseProperty.organization_id == org_id).first()
        try:
            res = EN.run(db, prop, EV.strategy_for(db, prop), approved=True)
        except Exception as exc:  # noqa: BLE001 - one record never stops the batch
            db.rollback()
            it = db.query(EvoSenseContactBatchItem).get(it.id)
            # the vendor may have answered (and billed) before the error: never retried automatically
            it.state, it.finished_at = "interrupted", C.now()
            it.reason = ("Error after the lookup started (%s) - not retried; check the vendor's billing"
                         % type(exc).__name__)[:250]
            out["interrupted"] += 1
            C.log.exception("contact batch %s item %s failed", b.id, it.id)
            db.commit()
            done += 1
            continue
        dec = (db.query(EvoSenseEnrichmentDecision)
               .filter(EvoSenseEnrichmentDecision.property_id == prop.id,
                       EvoSenseEnrichmentDecision.organization_id == org_id)
               .order_by(EvoSenseEnrichmentDecision.created_at.desc()).first())
        it.decision_id = dec.id if dec is not None else None
        outcome = res.get("outcome")
        charged = sum(int(a.get("charged") or 0) for a in res.get("attempts") or [])
        it.cost_cents = charged
        it.finished_at = C.now()
        if outcome == "found":
            it.state, it.reason = "found", "Contact found and matched to the owner"
            b.found, b.looked_up = (b.found or 0) + 1, (b.looked_up or 0) + 1
            out["found"] += 1
        elif outcome == "no_match":
            it.state = "no_match"
            it.reason = ((res.get("attempts") or [{}])[-1].get("why") or "The provider found no contact for this owner")[:250]
            b.no_match, b.looked_up = (b.no_match or 0) + 1, (b.looked_up or 0) + 1
            out["no_match"] += 1
        elif outcome == "provider_failed":
            it.state, it.reason = "failed", "Provider failed - the reservation was refunded"
            b.failed = (b.failed or 0) + 1
            out["failed"] += 1
        elif res.get("decision") == C.D_RETRY and outcome is None:
            # the provider is off / paid data paused: nothing was called - wait, do not skip owners
            it.state, it.reason = "ready", ("; ".join(res.get("reasons") or []))[:250]
            it.attempts = max(0, (it.attempts or 1) - 1)
            set_status(db, org_id, b, "pause", reason=(it.reason or "Provider not available")[:200])
        elif outcome == "budget_blocked" or res.get("decision") == C.D_BUDGET:
            it.state, it.reason = "ready", (res.get("reason") or "; ".join(res.get("reasons") or []))[:250]
            it.attempts = max(0, (it.attempts or 1) - 1)
            set_status(db, org_id, b, "pause", reason="Budget limit: " + (it.reason or ""))
        else:
            it.state = "skipped"
            it.reason = ("Not looked up: " + "; ".join(res.get("reasons") or [res.get("decision") or ""]))[:250]
        b.spent_cents = (b.spent_cents or 0) + charged
        out["looked_up"] += 1 if outcome in ("found", "no_match") else 0
        db.commit()
        done += 1
    b.lease_until = None
    db.commit()
    return out


def _finish(db, b, reason: Optional[str], cap: bool = False) -> None:
    from app.models.evosense_models import EvoSenseContactBatchItem
    if cap:
        for it in db.query(EvoSenseContactBatchItem).filter(EvoSenseContactBatchItem.batch_id == b.id,
                                                            EvoSenseContactBatchItem.state == "ready").all():
            it.state, it.reason = "cap_reached", "Not looked up - the batch's spending cap was reached"
    b.status = "completed" if not cap else "stopped"
    b.stopped_reason = reason
    b.finished_at = C.now()
    C.log_event(db, b.organization_id, "contact_batch.finished", actor_type=C.ACTOR_AUTOMATION,
                summary="Contact-lookup batch %s: %d looked up, %d found, %d no match, %d failed, %s spent%s"
                % ("stopped" if cap else "finished", b.looked_up or 0, b.found or 0, b.no_match or 0,
                   b.failed or 0, _money(b.spent_cents), (" - " + reason) if reason else ""),
                details={"batch": b.id})
    db.commit()


def run_due(db) -> int:
    """Scheduler pass: every running batch takes one step."""
    from app.models.evosense_models import EvoSenseContactBatch
    n = 0
    for b in db.query(EvoSenseContactBatch).filter(EvoSenseContactBatch.status == "running").all():
        try:
            n += sum(run_step(db, b.organization_id, b).values())
        except Exception:  # noqa: BLE001
            db.rollback()
            C.log.exception("contact batch %s step failed", b.id)
    return n


def payload(db, b, *, items: bool = True) -> Dict[str, Any]:
    from app.models.evosense_models import EvoSenseContactBatchItem, EvoSenseProperty
    out = {k: getattr(b, k) for k in ("id", "source", "status", "provider_key", "unit_cost_cents", "charge_on_miss",
                                      "max_lookups", "max_spend_cents", "daily_spend_cents", "looked_up", "found",
                                      "no_match", "failed", "spent_cents", "stopped_reason")}
    out.update({"max_spend": _money(b.max_spend_cents), "spent": _money(b.spent_cents),
                "approval_phrase": approval_phrase(b) if b.status == "draft" else b.approval_phrase,
                "approved_at": b.approved_at.isoformat() + "Z" if b.approved_at else None,
                "preflight": json.loads(b.preflight) if b.preflight else None,
                "activation": json.loads(b.activation) if b.activation else None})
    if items:
        rows = (db.query(EvoSenseContactBatchItem, EvoSenseProperty.street_address, EvoSenseProperty.city)
                .join(EvoSenseProperty, EvoSenseProperty.id == EvoSenseContactBatchItem.property_id)
                .filter(EvoSenseContactBatchItem.batch_id == b.id)
                .order_by(EvoSenseContactBatchItem.position.asc()).all())
        out["items"] = [{"property_id": it.property_id, "address": a, "city": c, "position": it.position,
                         "state": it.state, "state_label": STATE_LABELS.get(it.state, it.state), "reason": it.reason,
                         "attempts": it.attempts, "cost_cents": it.cost_cents} for it, a, c in rows]
        counts: Dict[str, int] = {}
        for i in out["items"]:
            counts[i["state"]] = counts.get(i["state"], 0) + 1
        out["counts"] = counts
    return out
