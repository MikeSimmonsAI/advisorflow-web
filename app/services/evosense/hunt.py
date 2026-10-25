"""The hunt: one idempotent, bounded, observable run of one strategy.

    discover (every connected discovery source) -> ingest -> identity
    -> rescore -> cost-aware enrichment (best first, within budget)
    -> nurture due-dates -> automatic outreach (only if the strategy says so
       AND every eligibility check passes)

IDEMPOTENT. Re-running the same hunt re-sees the same observations ("seen"),
buys nothing twice (enrichment only runs for properties with no decision yet,
or whose budget has since been freed), and starts no second conversation.
A run that dies halfway is resumed by running it again.

BOUNDED. `max_properties` caps a run. A 100k-record market is worked across
runs, highest score first, never in one unbounded request.

SCHEDULING. `scripts/evosense_hunt.py` runs every active strategy for every
organization once; point the platform's job runner (or cron) at it. The
"Run hunt now" button calls the same function for one strategy.
"""
from __future__ import annotations

import csv
import re
import hashlib
import io
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import or_

from app.models.evosense_models import EvoSenseProperty, EvoSenseRun, EvoSenseStrategy
from app.services.evosense import common as C
from app.services.evosense import conversation as CV
from app.services.evosense import enrichment as EN
from app.services.evosense import evaluate as EV
from app.services.evosense import ingest as IN
from app.services.evosense import outreach as OU
from app.services.evosense import providers as PV
from app.services.evosense import scoring as SC
from app.services.evosense import strategy as ST

DISCOVERY = (C.PROPERTY_SEARCH, C.VACANCY, C.TAX, C.PROBATE, C.CODE_VIOLATION, C.FORECLOSURE)
RUN_LOCK = timedelta(minutes=30)


def _query(strategy) -> Dict[str, Any]:
    q: Dict[str, Any] = {k: ST.lst(strategy, k) for k in ("states", "counties", "cities", "zips", "markets",
                                                          "property_types")}
    q["houses_only"] = bool(getattr(strategy, "houses_only", False))
    if getattr(strategy, "min_ownership_years", None):
        q["min_ownership_years"] = strategy.min_ownership_years
    return q


def run_strategy(db, org_id: str, strategy: EvoSenseStrategy, *, trigger: str = "manual",
                 user=None, max_properties: int = 500, enrich: bool = True) -> EvoSenseRun:
    """THE one hunt, for "Run hunt" and for the scheduler alike (Phase 7.1).

    Takes the strategy's atomic hunt lock first: a second caller while a hunt
    is running gets a SKIPPED run ("Another hunt ... already running"), never a
    second hunt. A hunt that raises is rolled back, recorded as FAILED with the
    error, and the lock released; everything it had committed before the
    failure is idempotent, so the retry re-sees rather than re-creates."""
    from app.services.evosense import scheduler as SCH
    token = SCH.claim(db, strategy, due_only=(trigger == "schedule"))
    if token is None:
        run = EvoSenseRun(organization_id=org_id, strategy_id=strategy.id, trigger=trigger,
                          status="skipped", error="Another hunt of this strategy is already running",
                          counts=C.jdump({}), finished_at=C.now())
        if trigger == "schedule":
            run.error = ("Another hunt of this strategy is already running, or it already ran "
                         "and is not due again yet")
        db.add(run)
        C.log_event(db, org_id, "hunt.skipped_lock", strategy_id=strategy.id, user=user,
                    actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION, is_test=strategy.is_test,
                    summary="Hunt not started: this strategy is already hunting")
        db.commit()
        return run
    SCH.interrupt_stale_runs(db, strategy)
    keeper = SCH.LeaseKeeper(db, strategy, token).start()
    try:
        run = _run_locked(db, org_id, strategy, trigger=trigger, user=user,
                          max_properties=max_properties, enrich=enrich,
                          renew=lambda: keeper.ok() and SCH.renew(db, strategy, token))
        # The next due time is written BEFORE the lock is released, so no
        # other worker can see "unlocked and still due" in between.
        if run.status != "skipped":
            SCH.record_result(db, strategy, run)
    finally:
        keeper.stop()
        SCH.release(db, strategy, token)
    return run


def _run_locked(db, org_id: str, strategy: EvoSenseStrategy, *, trigger: str, user,
                max_properties: int, enrich: bool, renew=None) -> EvoSenseRun:
    ctl = C.controls(db, org_id)
    run = EvoSenseRun(organization_id=org_id, strategy_id=strategy.id, trigger=trigger)
    db.add(run)
    C.log_event(db, org_id, "hunt.started", strategy_id=strategy.id, user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION, is_test=strategy.is_test,
                summary="Hunt started (%s)" % trigger)
    db.commit()           # the run is visible (and recoverable) from its first moment
    run_id = run.id
    counts: Dict[str, Any] = {"observed": 0, "created": 0, "merged": 0, "seen": 0, "review": 0,
                              "scored": 0, "above_threshold": 0, "decisions": {},
                              "contacts_found": 0, "spent_cents": 0, "outreach_started": 0,
                              "outreach_blocked": 0, "provider_errors": 0, "nurture_due": 0}

    def finish(status, error=None):
        run.status = status
        run.error = error
        run.counts = C.jdump(counts)
        run.finished_at = C.now()
        ctl.last_hunt_at = run.finished_at
        ctl.last_hunt_status = status
        strategy.last_hunt_at = run.finished_at
        C.log_event(db, org_id, "hunt." + status, strategy_id=strategy.id, user=user,
                    actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION,
                    is_test=strategy.is_test,
                    summary="Hunt %s: %s new, %s merged, %s above threshold, %s contacts, %s spent"
                    % (status, counts["created"], counts["merged"], counts["above_threshold"],
                       counts["contacts_found"], C.money(counts["spent_cents"])),
                    details=counts)
        db.commit()
        return run

    if ctl.paused_all or ctl.paused_discovery:
        return finish("skipped", "EvoSense is paused" if ctl.paused_all else "Discovery is paused")
    if strategy.status != "active":
        return finish("skipped", "Strategy is %s" % strategy.status)
    try:
        return _hunt_body(db, org_id, strategy, run, ctl, counts, finish, user=user,
                          max_properties=max_properties, enrich=enrich, renew=renew)
    except Exception as exc:  # noqa: BLE001 - recorded, surfaced, retried; never silent
        db.rollback()
        run = db.query(EvoSenseRun).filter(EvoSenseRun.id == run_id).first()
        ctl = C.controls(db, org_id)
        strategy = db.query(EvoSenseStrategy).filter(EvoSenseStrategy.id == strategy.id).first()
        C.log.exception("evosense hunt failed (strategy %s)", strategy.id)
        return _finish_failed(db, org_id, strategy, run, ctl, counts, "%s: %s" % (
            type(exc).__name__, str(exc)[:200]), user)


def _finish_failed(db, org_id, strategy, run, ctl, counts, error, user):
    run.status = "failed"
    run.error = error
    run.counts = C.jdump(counts)
    run.finished_at = C.now()
    ctl.last_hunt_status = "failed"
    C.log_event(db, org_id, "hunt.failed", strategy_id=strategy.id, user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION, is_test=strategy.is_test,
                summary="Hunt failed — nothing is lost; it will be retried. %s" % error, details=counts)
    db.commit()
    return run


def discovery_focus(strategy) -> List[str]:
    """The discovery sources a strategy asked for: capabilities (TAX,
    PROPERTY_SEARCH, CODE_VIOLATION, ...) or source keys. Empty = every
    connected discovery source (the behaviour of searches that never set it)."""
    prefs = C.jload(getattr(strategy, "provider_preferences", None), {}) or {}
    focus = prefs.get("discovery") if isinstance(prefs, dict) else None
    return [str(x) for x in focus if x] if isinstance(focus, list) else []


def expand_focus(focus: Optional[List[str]]) -> List[str]:
    """A strategy that names one appraisal-roll source (Tired Landlord named
    collin_cad and denton_gis) means the FAMILY: every owner-roll source,
    including ones added later (Harris, statewide), reads for it - no strategy
    has to be edited when a new county comes online."""
    focus = list(focus or [])
    fams = {getattr(PV.PROVIDERS.get(k), "discovery_family", None) for k in focus} - {None}
    return focus + sorted(f for f in fams if f not in focus)


def _discovery_routes(db, org_id, focus: Optional[List[str]] = None, *, sandbox_allowed: bool = True):
    """(capability, provider, cfg) for every connected DISCOVERY source. A
    public-record LOOKUP source (TAD, geocoder, code cases) is never asked to
    discover; it is called per property in the lookup stage. With a focus,
    only the named capabilities / sources discover."""
    out, seen = [], set()
    focus = expand_focus(focus)
    for cap in DISCOVERY:
        for provider, cfg, _cost in PV.route(db, org_id, cap, sandbox_allowed=sandbox_allowed):
            if not getattr(provider, "discovery", True):
                continue
            if focus and cap not in focus and provider.key not in focus \
                    and getattr(provider, "discovery_family", None) not in focus:
                continue
            if getattr(provider, "discovery_opt_in", False) and not (focus and (cap in focus or provider.key in focus)):
                # An opt-in source (county clerk foreclosure notices) feeds only
                # a strategy that asks for it - enabling it never changes what an
                # existing all-sources strategy brings in.
                continue
            if (provider.key, cap) in seen:
                continue
            seen.add((provider.key, cap))
            out.append((cap, provider, cfg))
    return out


def _source_failed(db, org_id, strategy, provider, cfg, exc, counts, user):
    from app.services.evosense.sources import base as SRC
    if isinstance(exc, SRC.SourceError):
        code, msg = exc.code, exc.message
        PV.note_source_failure(db, provider, cfg, code, msg, retry_after=exc.retry_after)
    else:
        code, msg = type(exc).__name__, str(exc)[:200]
        PV.record_failure(cfg, "%s: %s" % (code, msg))
    # a list re-check passes a bare dict: count without assuming the key
    counts["provider_errors"] = counts.get("provider_errors", 0) + 1
    counts.setdefault("source_errors", []).append({"provider": provider.key, "code": code,
                                                   "message": msg[:200]})
    C.log_event(db, org_id, "provider.failed", strategy_id=strategy.id, is_test=strategy.is_test,
                actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION,
                summary="%s failed — %s: %s" % (provider.label, code, msg[:160]))


def _checkpoint(run, counts, source_key: Optional[str], *, stage: str = "discovery") -> None:
    """Progress saved on the run as each source finishes, so a hunt cut off by
    a restart shows exactly what it completed (and recovery can say so)."""
    prog = counts.setdefault("progress", {"sources_done": []})
    if source_key and source_key not in prog["sources_done"]:
        prog["sources_done"].append(source_key)
    prog["stage"] = stage
    prog["at"] = C.now().isoformat()
    run.counts = C.jdump(counts)


class LeaseLost(RuntimeError):
    """The hunt lock expired and another worker may hold it: stop, don't double-run."""


def _hunt_body(db, org_id, strategy, run, ctl, counts, finish, *, user, max_properties, enrich,
               renew=None):
    q = _query(strategy)
    pilot = ST.is_pilot(strategy)
    if pilot:
        max_properties = min(max_properties, ST.pilot_cap(strategy))
        counts["pilot"] = {"record_cap": max_properties, "spend_cap_cents": ST.pilot_spend_cap(strategy),
                           "outreach": "off", "paid_data": "on" if ST.pilot_spend_cap(strategy) else "off"}
    touched: Dict[str, EvoSenseProperty] = {}
    focus = discovery_focus(strategy)
    # Sandbox (synthetic) sources feed TEST strategies only: switching one on
    # can never put invented houses into a real strategy's results.
    routes = _discovery_routes(db, org_id, focus, sandbox_allowed=bool(getattr(strategy, "is_test", False)))
    if focus:
        counts["discovery_focus"] = focus
        if not routes:
            # Nothing connected can serve what this strategy hunts: say so -
            # a hunt that searched nothing is BLOCKED, never "succeeded".
            counts["blocked"] = ("No connected source serves this strategy's search (%s). Enable one under "
                                 "Sources, or import a list." % ", ".join(focus))
            C.log_event(db, org_id, "hunt.blocked", strategy_id=strategy.id, is_test=strategy.is_test,
                        actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION, summary=counts["blocked"])
    real = [r for r in routes if r[1].connector_kind == C.REAL]
    share = max(1, -(-max_properties // max(1, len(real))))      # ceil: a fair share per real source
    counts["sources"] = {}
    for cap, provider, cfg in routes:
        if len(touched) >= max_properties:
            break
        if renew is not None and not renew():
            raise LeaseLost("hunt lock lease was lost; stopped before %s" % provider.key)
        cfg.last_attempt_at = C.now()
        pq = dict(q, limit=min(share, max_properties - len(touched)))
        # NO DATABASE TRANSACTION IS HELD OPEN ACROSS A NETWORK CALL. A county
        # download can take minutes; the app's connections end any transaction
        # idle for 5 minutes (deps.py), and the hunt then failed with
        # PendingRollbackError at its next commit (2026-10-20 14:25 UTC).
        db.commit()
        try:
            records = provider.search(cap, pq)
        except Exception as exc:  # noqa: BLE001 - one failing source never ends the hunt
            _source_failed(db, org_id, strategy, provider, cfg, exc, counts, user)
            db.commit()
            continue
        PV.record_success(cfg, capability=cap)
        cfg.last_record_count = len(records)
        if provider.connector_kind == C.REAL:
            cfg.last_verified_at = C.now()
        stats = getattr(records, "stats", None)
        if stats is not None:
            counts["sources"][provider.key] = {"records": len(records), **{k: v for k, v in stats.items()
                                                                          if isinstance(v, (int, float, str))}}
        for rec in records:
            if len(touched) >= max_properties:
                break
            counts["observed"] += 1
            try:
                outcome, prop = IN.ingest(db, org_id, provider, cap, rec, strategy_id=strategy.id,
                                          run_id=run.id, is_test=strategy.is_test)
            except Exception as exc:  # noqa: BLE001 - one malformed record is recorded, not fatal
                counts["rejected"] = counts.get("rejected", 0) + 1
                C.log.warning("evosense record rejected (%s): %s", provider.key, exc)
                continue
            counts[outcome] = counts.get(outcome, 0) + 1
            if prop is not None:
                touched[prop.id] = prop
        db.flush()
        _checkpoint(run, counts, provider.key)
        db.commit()

    _checkpoint(run, counts, None, stage="lookups")
    db.commit()
    if touched:
        free_lookups(db, org_id, strategy, run, list(touched.values()), counts)
    backlog = lookup_backlog(db, org_id, exclude=set(touched))
    if backlog:
        # Houses that arrived with no owner (an imported clerk list): each hunt
        # gives the free county lookups another pass at them.
        counts["lookup_backlog"] = len(backlog)
        free_lookups(db, org_id, strategy, run, backlog, counts)
        for prop in backlog:
            EV.rescore(db, prop, EV.strategy_for(db, prop))
        db.commit()

    for prop in touched.values():
        if prop.best_strategy_id and prop.best_strategy_id != strategy.id:
            other = db.query(EvoSenseStrategy).filter(EvoSenseStrategy.id == prop.best_strategy_id).first()
            if other is not None and other.status == "active":
                mine = SC.property_opportunity(prop, EV.stacked_signals(db, prop), strategy,
                                              weights=EV.score_weights(db, org_id),
                                              owner=EV.CT.primary_owner(db, prop),
                                              options=EV.score_options(db, org_id))["value"] or 0
                if mine <= (prop.opportunity_score or 0):
                    continue
        prop.best_strategy_id = strategy.id
        EV.rescore(db, prop, strategy)
        counts["scored"] += 1
    db.commit()

    mine = [p for p in touched.values() if p.best_strategy_id == strategy.id]
    above = sorted([p for p in mine if (p.opportunity_score or 0) >= strategy.min_opportunity_score],
                   key=lambda p: -(p.opportunity_score or 0))
    counts["above_threshold"] = len(above)
    from app.services.evosense import value_lookup as VL
    from app.services.evosense.sources import rentcast as RC
    if RC.configured() and not pilot:
        valued = 0
        for prop in above:
            if valued >= VL.AUTO_PER_HUNT or (prop.opportunity_score or 0) < VL.AUTO_MIN_SCORE or RC.fresh(prop):
                continue
            res = VL.value_property(db, prop, auto=True)
            valued += 0 if res.get("cached") else 1
            if not res.get("ok") and "used up" in (res.get("reason") or ""):
                break
        counts["value_estimates"] = valued
    if enrich:
        from app.models.evosense_models import EvoSenseEnrichmentDecision
        for prop in above:
            if prop.status == C.S_WAITING_DATA:
                # Retry only a lookup that never happened (paused, no provider);
                # a real no-match / failure waits out its own retry window.
                last = (db.query(EvoSenseEnrichmentDecision)
                        .filter(EvoSenseEnrichmentDecision.organization_id == org_id,
                                EvoSenseEnrichmentDecision.property_id == prop.id,
                                EvoSenseEnrichmentDecision.capability == C.CONTACT_ENRICHMENT)
                        .order_by(EvoSenseEnrichmentDecision.created_at.desc()).first())
                if last is None or last.outcome != "skipped":
                    continue
            elif prop.status not in (C.S_HIGH, C.S_BUDGET_BLOCKED):
                continue
            res = EN.run(db, prop, strategy)
            counts["decisions"][res["decision"]] = counts["decisions"].get(res["decision"], 0) + 1
            if res.get("outcome") == "found":
                counts["contacts_found"] += 1
            for a in res.get("attempts", []):
                counts["spent_cents"] += a.get("charged", 0) or 0
            db.commit()

    counts["nurture_due"] = CV.resume_due(db, org_id)
    pol = ST.outreach_policy(strategy)
    if pol.get("auto_outreach") and not pilot:
        for prop in sorted(mine, key=lambda p: -(p.opportunity_score or 0)):
            if prop.status != C.S_READY:
                continue
            res = OU.start(db, prop, strategy)
            counts["outreach_started" if res.get("started") else "outreach_blocked"] += 1
            db.commit()
    # A source can answer a batch but fail for individual properties.
    # Such a run is PARTIAL, not green, even if no source-wide error fired.
    property_failures = sum(int(row.get("failed", 0)) for row in counts.get("lookups", {}).values())
    return finish("partial" if counts["provider_errors"] or property_failures or counts.get("blocked")
                  else "succeeded")


# ── free public-record lookups (the cost governor's QUEUE_FREE_LOOKUP lane) ──

LOOKUP_ORDER = (C.ASSESSOR, C.GEOCODING, C.CODE_VIOLATION)


def _target(prop) -> Dict[str, Any]:
    return {"id": prop.id, "street_address": prop.street_address, "unit": prop.unit,
            "city": prop.city, "state": prop.state, "zip_code": prop.zip_code,
            "county": prop.county, "parcel_apn": prop.parcel_apn}


def _lookup_decision(db, prop, strategy, provider, cap, decision, reason, outcome=None):
    from app.models.evosense_models import EvoSenseEnrichmentDecision
    row = EvoSenseEnrichmentDecision(
        organization_id=prop.organization_id, property_id=prop.id, strategy_id=strategy.id,
        capability=cap, decision=decision, reasons=C.jdump([reason]), provider_key=provider.key,
        estimated_cost_cents=0, outcome=outcome, decided_by="engine")
    db.add(row)
    return row


BACKLOG_LIMIT = 150
# Recent negative public lookups are not permanent evidence. Avoid querying the
# same address three times during adjacent daily strategy hunts; retry later.
NO_MATCH_RECHECK_HOURS = 2


def lookup_backlog(db, org_id: str, exclude=(), limit: int = BACKLOG_LIMIT) -> List[EvoSenseProperty]:
    """Recent Texas properties that still have no account number OR no
    current owner of record - newest first. Each lookup source decides which
    of them it covers (applies), and its own freshness rule keeps a property
    that was just looked up from being looked up again."""
    from sqlalchemy import and_, exists, or_
    from app.models.evosense_models import EvoSenseOwnership
    has_owner = exists().where(and_(EvoSenseOwnership.property_id == EvoSenseProperty.id,
                                    EvoSenseOwnership.is_current.is_(True)))
    q = (db.query(EvoSenseProperty)
         .filter(EvoSenseProperty.organization_id == org_id,
                 or_(EvoSenseProperty.parcel_apn.is_(None), ~has_owner),
                 EvoSenseProperty.street_address.isnot(None),
                 EvoSenseProperty.archived_at.is_(None),
                 or_(EvoSenseProperty.state.is_(None), EvoSenseProperty.state == "TX"))
         .order_by(EvoSenseProperty.discovered_at.desc()))
    return [p for p in q.limit(limit + len(exclude or ())).all() if p.id not in (exclude or ())][:limit]


def _lookup_failure_code(error) -> str:
    """Safe reason code; never expose exception text, customer data or tokens."""
    from app.services.evosense.sources import base as SRC
    if isinstance(error, SRC.SourceError):
        return error.code if error.code in SRC.ERROR_CODES else "OTHER"
    if error == "MISSING_PROVIDER_RESULT":
        return "MISSING_PROVIDER_RESULT"
    if isinstance(error, TimeoutError):
        return SRC.TIMEOUT_ERR
    if isinstance(error, (ConnectionError, OSError)):
        return "NETWORK_ERROR"
    return "OTHER"


def _count_lookup_failure(tally: Dict[str, Any], error, how_many: int = 1) -> str:
    """A failed lookup contributes one attempted outcome and a coded reason."""
    code = _lookup_failure_code(error)
    tally["failed"] += how_many
    codes = tally.setdefault("failure_codes", {})
    codes[code] = codes.get(code, 0) + how_many
    return code


def free_lookups(db, org_id, strategy, run, props: List[EvoSenseProperty], counts, *,
                 force_ids: Optional[set] = None) -> None:
    """Ask each enabled FREE public-record lookup source about each property it
    covers, recording one governor decision per (property, source):

        SKIP_FRESH_DATA       this source answered for this property recently
        SKIP_PROVIDER_DOWN    the source is enabled but degraded / rate limited / failing
        QUEUE_FREE_LOOKUP     looked up now ($0) -> outcome found | no_match | provider_failed

    Paid sources never run here. Results go through the same ingest as any
    record, so every fact keeps its own observation and raw evidence."""
    from app.models.evosense_models import EvoSenseEnrichmentDecision, EvoSenseObservation
    from app.services.evosense.sources import base as SRC
    counts.setdefault("lookups", {})
    for cap in LOOKUP_ORDER:
        for key, provider in PV.PROVIDERS.items():
            if not isinstance(provider, PV.PublicRecordSource) or provider.lookup_capability != cap:
                continue
            if provider.costs:
                continue                                  # never a paid call in this lane
            cfg = PV.config(db, org_id, key)
            if not cfg.enabled:
                continue                                  # NOT CONFIGURED: nothing to decide
            tally = counts["lookups"].setdefault(key, {"queued": 0, "found": 0, "no_match": 0,
                                                       "skipped_fresh": 0, "skipped_down": 0,
                                                       "failed": 0, "review": 0, "failure_codes": {}, "duration_ms": 0,
                                                       "skipped_no_match": 0})
            applicable = [p for p in props if provider.applies(_target(p))]
            if not applicable:
                continue
            health = PV.health_state(provider, cfg, blocked=PV.platform_blocked(db, key, cfg))
            if health != C.H_CONNECTED:
                why = ("%s is blocked platform-wide (the source refused the platform's requests); "
                       "not called." % provider.label) if health == C.H_BLOCKED else \
                    "%s is %s; not called." % (provider.label, health.replace("_", " ").lower())
                for p in applicable:
                    _lookup_decision(db, p, strategy, provider, cap, C.L_SKIP_PROVIDER_DOWN, why,
                                     outcome="skipped")
                    tally["skipped_down"] += 1
                db.commit()
                continue
            fresh_after = C.now() - timedelta(days=provider.freshness_days)
            # A no-match is UNKNOWN, not permanent evidence that a case never
            # exists. But querying the identical property/source in each of
            # three closely spaced strategy runs wastes public API requests.
            # One tenant-scoped batch query avoids a per-property SQL sweep.
            negative_since = C.now() - timedelta(hours=NO_MATCH_RECHECK_HOURS)
            recent_no_matches = {row[0] for row in (
                db.query(EvoSenseEnrichmentDecision.property_id)
                .filter(EvoSenseEnrichmentDecision.organization_id == org_id,
                        EvoSenseEnrichmentDecision.property_id.in_([p.id for p in applicable]),
                        EvoSenseEnrichmentDecision.provider_key == key,
                        EvoSenseEnrichmentDecision.decision == C.L_QUEUE_FREE,
                        EvoSenseEnrichmentDecision.outcome == "no_match",
                        EvoSenseEnrichmentDecision.created_at >= negative_since)
                .distinct().all())}
            queued = []
            for p in applicable:
                if force_ids and p.id in force_ids:
                    # A person just corrected this house's identifiers: earlier
                    # answers (or no-matches) were about the wrong address/account.
                    queued.append(p)
                    continue
                q = (db.query(EvoSenseObservation.id)
                     .filter(EvoSenseObservation.organization_id == org_id,
                             EvoSenseObservation.property_id == p.id,
                             EvoSenseObservation.provider_key == key,
                             EvoSenseObservation.observed_at >= fresh_after))
                stale = tuple(getattr(provider, "stale_adapter_versions", ()) or ())
                if stale:
                    # a record from an older adapter that lacks a field we now
                    # read (TxGIO /1: no legal description) is not fresh
                    q = q.filter(or_(EvoSenseObservation.adapter_version.is_(None),
                                     ~EvoSenseObservation.adapter_version.in_(stale)))
                recent = q.first()
                if recent is not None:
                    _lookup_decision(db, p, strategy, provider, cap, C.L_SKIP_FRESH_DATA,
                                     "%s already answered within %s days." % (provider.label, provider.freshness_days),
                                     outcome="skipped")
                    tally["skipped_fresh"] += 1
                elif p.id in recent_no_matches:
                    _lookup_decision(db, p, strategy, provider, cap, C.L_SKIP_FRESH_DATA,
                                     "A public-source lookup found no matching record in the last %s hours. "
                                     "Automatically retry when the short cooldown expires."
                                     % NO_MATCH_RECHECK_HOURS, outcome="skipped")
                    tally["skipped_no_match"] += 1
                else:
                    queued.append(p)
            if not queued:
                db.commit()
                continue
            cfg.last_attempt_at = C.now()
            by_id = {p.id: p for p in queued}
            targets = [_target(p) for p in queued]
            # A systemic batch failure still attempted all these lookups.
            # Old counting put failed=N but queued=0 on that path.
            tally["queued"] += len(queued)
            db.commit()                 # never idle in a transaction during the lookups (see _hunt_body)
            started_at = time.monotonic()
            try:
                res = provider.lookup_many(targets)
            except Exception as exc:  # noqa: BLE001 - systemic source failure: recorded per property
                _source_failed(db, org_id, strategy, provider, cfg, exc, counts, None)
                code = _count_lookup_failure(tally, exc, len(queued))
                for p in queued:
                    _lookup_decision(db, p, strategy, provider, cap, C.L_QUEUE_FREE,
                                     "Free lookup failed: %s. UNKNOWN is not NO — nothing was concluded." % code,
                                     outcome="provider_failed")
                db.commit()
                continue
            finally:
                # Batch latency belongs in the run summary, even on errors.
                # Never include query URLs, addresses or tokens.
                tally["duration_ms"] += round((time.monotonic() - started_at) * 1000)
            PV.record_success(cfg, capability=cap)
            cfg.last_verified_at = C.now()
            found = 0
            results = res.get("results", {})
            errors = res.get("errors", {})
            for pid, p in by_id.items():
                err = errors.get(pid)
                if err is not None:
                    code = _count_lookup_failure(tally, err)
                    _lookup_decision(db, p, strategy, provider, cap, C.L_QUEUE_FREE,
                                     "Free lookup failed for this property: %s" % code,
                                     outcome="provider_failed")
                    continue
                if pid not in results:
                    # A missing answer is UNKNOWN, not a true no-match.
                    _count_lookup_failure(tally, "MISSING_PROVIDER_RESULT")
                    _lookup_decision(db, p, strategy, provider, cap, C.L_QUEUE_FREE,
                                     "Source omitted this property from its lookup response.",
                                     outcome="provider_failed")
                    continue
                rec = results.get(pid)
                if not rec:
                    _lookup_decision(db, p, strategy, provider, cap, C.L_QUEUE_FREE,
                                     "%s has no record for this property (not the same as 'none exists')."
                                     % provider.label, outcome="no_match")
                    tally["no_match"] += 1
                    continue
                try:
                    outcome, merged = IN.ingest(db, org_id, provider, cap, rec, strategy_id=strategy.id,
                                                run_id=run.id, is_test=strategy.is_test, target=p)
                except Exception as exc:  # noqa: BLE001
                    _lookup_decision(db, p, strategy, provider, cap, C.L_QUEUE_FREE,
                                     "Record could not be ingested: %s" % str(exc)[:160],
                                     outcome="provider_failed")
                    _count_lookup_failure(tally, exc)
                    continue
                if outcome == "review":
                    tally["review"] += 1
                    _lookup_decision(db, p, strategy, provider, cap, C.L_MANUAL_REVIEW,
                                     "%s answered, but its record might describe a different property; "
                                     "it waits in identity review." % provider.label, outcome="review")
                    continue
                _lookup_decision(db, p, strategy, provider, cap, C.L_QUEUE_FREE,
                                 "%s answered at no cost (%s)." % (provider.label, outcome), outcome="found")
                tally["found"] += 1
                found += 1
            cfg.last_record_count = found
            db.commit()


def run_all(db, *, trigger: str = "schedule", org_id: Optional[str] = None) -> List[EvoSenseRun]:
    q = db.query(EvoSenseStrategy).filter(EvoSenseStrategy.status == "active")
    if org_id:
        q = q.filter(EvoSenseStrategy.organization_id == org_id)
    out = []
    for s in q.all():
        try:
            out.append(run_strategy(db, s.organization_id, s, trigger=trigger))
        except Exception:  # noqa: BLE001 - one organization's failure never stops the others
            db.rollback()
            C.log.exception("evosense hunt failed for strategy %s", s.id)
    return out


# ── manual and import fallbacks (same ingest pipeline as any provider) ─────

def add_manual(db, org_id: str, data: Dict[str, Any], *, user, strategy=None) -> Dict[str, Any]:
    if not (data.get("street_address") and (data.get("zip_code") or data.get("city"))):
        raise ValueError("A street address and a ZIP or city are required.")
    rec = {k: data.get(k) for k in ("street_address", "unit", "city", "state", "zip_code", "county",
                                    "parcel_apn", "property_type", "bedrooms", "bathrooms",
                                    "square_feet", "year_built", "last_sale_date", "occupancy")}
    rec["source_reference"] = "manual:%s" % hashlib.sha1(
        ("%s|%s|%s" % (data.get("street_address"), data.get("zip_code"), C.now().isoformat())).encode()
    ).hexdigest()[:16]
    if data.get("estimated_value") not in (None, ""):
        rec["valuation"] = {"value": int(float(data["estimated_value"])),
                            "mortgage": int(float(data["mortgage_balance"]))
                            if data.get("mortgage_balance") not in (None, "") else None,
                            "basis": "entered by a person"}
    if data.get("owner_name"):
        rec["owner"] = {"name": data["owner_name"], "mailing_street": data.get("mailing_street"),
                        "mailing_city": data.get("mailing_city"), "mailing_state": data.get("mailing_state"),
                        "mailing_zip": data.get("mailing_zip")}
    rec["signals"] = [{"type": s, "confidence": 70, "value": "entered by a person"}
                      for s in (data.get("signals") or []) if s]
    outcome, prop = IN.ingest(db, org_id, PV.PROVIDERS["manual"], C.PROPERTY_SEARCH, rec,
                              strategy_id=getattr(strategy, "id", None), user=user,
                              is_test=bool(data.get("is_test")))
    if prop is not None:
        if strategy is not None and not prop.best_strategy_id:
            prop.best_strategy_id = strategy.id
        EV.rescore(db, prop, strategy)
    return {"outcome": outcome, "property_id": prop.id if prop else None}


CSV_COLUMNS = ("street_address", "unit", "city", "state", "zip_code", "county", "parcel_apn",
               "property_type", "owner_name", "mailing_street", "mailing_city", "mailing_state",
               "mailing_zip", "estimated_value", "mortgage_balance", "last_sale_date", "signals",
               "record_id", "bedrooms", "bathrooms", "square_feet", "year_built")
# Columns that never change a row's identity (adding them to a file re-sees
# the same rows): the list's claims about the property.
CLAIM_COLUMNS = ("sale_date", "list_owner_name", "loan_type")
BUILDING_COLUMNS = ("bedrooms", "bathrooms", "square_feet", "year_built")
# Evidence columns a DISTRESS LIST may carry. They describe the list's claim;
# they are stored as the signal's evidence, never as verified fact.
EVIDENCE_COLUMNS = ("signal_date", "case_number", "case_status", "amount")

# ── Distress lists: one hook, not one importer per county ──────────────────
# A tax-sale list, a code-enforcement export, a clerk's foreclosure postings,
# a driving-for-dollars sheet: each is the SAME flow - a file of addresses that
# says "this property has this distress" - differing only in which signal it
# asserts. So each is a declared LIST KIND of the one EvoSense import (which
# runs the same ingest -> identity/dedupe -> signals -> scoring as every
# provider), and a new source is a new entry here, not new code. A row's own
# `signals` column, when present, still wins over the list's default.
DISTRESS_LISTS: Dict[str, Dict[str, str]] = {
    "tax_delinquent":      {"label": "Delinquent tax list", "signal": "TAX_DELINQUENT"},
    "tax_sale":            {"label": "Tax-sale list", "signal": "TAX_DELINQUENT"},
    "tax_suit":            {"label": "Tax suit filings", "signal": "TAX_SUIT"},
    "pre_foreclosure":     {"label": "Pre-foreclosure / notice of default", "signal": "PRE_FORECLOSURE"},
    "foreclosure_filing":  {"label": "Foreclosure / trustee-sale postings", "signal": "PRE_FORECLOSURE"},
    "code_enforcement":    {"label": "Code enforcement cases", "signal": "CODE_VIOLATION"},
    "municipal_violation": {"label": "Municipal violations", "signal": "CODE_VIOLATION"},
    "lien":                {"label": "Liens (clerk / public record)", "signal": "LIEN"},
    "probate":             {"label": "Probate filings", "signal": "PROBATE"},
    "driving_for_dollars": {"label": "Driving for dollars", "signal": "DISTRESSED_CONDITION"},
}


def distress_list_kinds() -> List[Dict[str, str]]:
    from app.services.evosense.signals import CATALOG
    return [{"key": k, "label": v["label"], "signal": v["signal"],
             "signal_label": CATALOG[v["signal"]]["label"]} for k, v in DISTRESS_LISTS.items()]


TAX_LIST_SIGNALS = ("TAX_DELINQUENT", "TAX_SUIT", "TAX_SALE")


def foreclosure_claim(sale, *, source: str, declared_by: Optional[str] = None,
                      case_status: Optional[str] = None, tax: bool = False) -> Dict[str, Any]:
    """A list's CLAIM of a scheduled sale: a mortgage / trustee sale
    (PRE_FORECLOSURE) - or, on a tax list, a tax foreclosure sale (TAX_SALE).

    `sale` comes from a row's sale_date column or from a date a person
    declared for the whole list - never from a file name. A row's own
    case_status (postponed, cancelled ...) is kept, never overwritten."""
    how = ("declared for the whole list by %s" % declared_by) if declared_by else "stated in the row's sale date"
    status = (case_status or "").strip().lower() or "scheduled"
    if tax:
        return {"type": "TAX_SALE", "confidence": 60, "claim": "imported",
                "sale_date": sale.strftime("%Y-%m-%d"), "foreclosure_type": "tax_foreclosure_sale",
                "value": "Tax foreclosure sale %s %s - claimed by %s (%s); not verified against the sale list "
                         "or court record" % (status, sale.strftime("%Y-%m-%d"), source, how),
                "evidence_basis": "imported list claim", "case_status": status,
                "limitations": "A list's claim of a tax sale date. Confirm on the county / constable sale list."}
    return {"type": "PRE_FORECLOSURE", "confidence": 60, "claim": "imported",
            "sale_date": sale.strftime("%Y-%m-%d"),
            "value": "Foreclosure sale scheduled %s - claimed by %s (%s); not verified against the county "
                     "clerk's notice" % (sale.strftime("%Y-%m-%d"), source, how),
            "evidence_basis": "imported list claim", "case_status": status,
            "limitations": "A list's claim of a scheduled trustee sale. Confirm on the county clerk's posted "
                           "Notice of (Substitute) Trustee Sale; sales are often postponed or cancelled."}


def import_csv(db, org_id: str, content: str, *, user, strategy=None, filename: str = "upload.csv",
               max_rows: int = 5000, list_kind: Optional[str] = None,
               list_source: Optional[str] = None, declared_sale_date: Optional[str] = None,
               ) -> Dict[str, Any]:
    """A property list through the SAME ingest as every provider. Re-importing
    the same file is idempotent (row identity = record_id, or a hash of the row).

    `list_kind` declares a DISTRESS LIST (see DISTRESS_LISTS): rows with no
    `signals` column value assert that list's signal, carrying the row's own
    evidence (signal_date, case_number, case_status, amount). The list kind is
    part of the row identity, so the same address on a tax list and on a code
    list is two pieces of evidence, not one overwriting the other."""
    if list_kind is not None and list_kind not in DISTRESS_LISTS:
        raise ValueError("Unknown list kind %r. Known: %s" % (list_kind, ", ".join(DISTRESS_LISTS)))
    # The file NAME is only a label for people: nothing below reads a date,
    # a year or a distress claim out of it. A sale date for every row exists
    # only when a person declares one here (recorded as theirs).
    declared = _parse_list_date(declared_sale_date) if declared_sale_date else None
    if declared_sale_date and declared is None:
        raise ValueError("The list's sale date is not a date (use YYYY-MM-DD or MM/DD/YYYY)")
    source_label = list_source or "an imported list"
    kind = DISTRESS_LISTS.get(list_kind) if list_kind else None
    from app.services.evosense import propstream_csv as PS
    fmt = None
    head = csv.DictReader(io.StringIO(content))
    if PS.is_propstream(head.fieldnames or []):
        content, fmt = PS.convert(content)
        list_source = list_source or "PropStream export"
    reader = csv.DictReader(io.StringIO(content))
    missing = [c for c in ("street_address",) if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError("The file needs a street_address column. Columns understood: %s"
                         % ", ".join(CSV_COLUMNS))
    provider = PV.PROVIDERS["csv_import"]
    counts = {"rows": 0, "created": 0, "merged": 0, "seen": 0, "review": 0, "rejected": 0,
              "sale_dates": 0, "sale_dates_declared": 0}
    rejected: List[Dict[str, Any]] = []
    for i, row in enumerate(reader, start=2):
        if counts["rows"] >= max_rows:
            break
        counts["rows"] += 1
        row = {k: (v or "").strip() for k, v in row.items() if k}
        if not row.get("street_address") or not (row.get("zip_code") or row.get("city")):
            counts["rejected"] += 1
            rejected.append({"line": i, "reason": "street_address and zip_code/city required"})
            continue
        ref = row.get("record_id") or hashlib.sha1("|".join(
            row.get(c, "") for c in CSV_COLUMNS[:8]).lower().encode()).hexdigest()[:20]
        rec = {k: row.get(k) or None for k in ("street_address", "unit", "city", "state", "zip_code",
                                               "county", "parcel_apn", "property_type", "last_sale_date")}
        for k in BUILDING_COLUMNS:
            try:
                v = float(str(row.get(k) or "").replace(",", ""))
                rec[k] = (int(v) if k in ("square_feet", "year_built") else v) if v > 0 else None
            except ValueError:
                rec[k] = None
        rec["source_reference"] = ("csv:%s:%s" % (list_kind, ref)) if list_kind else "csv:%s" % ref
        try:
            if row.get("estimated_value"):
                rec["valuation"] = {"value": int(float(row["estimated_value"].replace(",", "").replace("$", ""))),
                                    "mortgage": int(float(row["mortgage_balance"].replace(",", "").replace("$", "")))
                                    if row.get("mortgage_balance") else None,
                                    # never the file name: a name is a label, not evidence
                                    # (a name like "Appraisal District 2010.csv" would
                                    # otherwise read as a 2010 tax value)
                                    "basis": "imported list (value as the file states it)"}
        except ValueError:
            counts["rejected"] += 1
            rejected.append({"line": i, "reason": "estimated_value / mortgage_balance not a number"})
            continue
        claims = {k: row.get(k) for k in CLAIM_COLUMNS if row.get(k)}
        if claims:
            rec["_evidence"] = {"list_claims": claims}
        sale = _parse_list_date(row.get("sale_date")) if row.get("sale_date") else None
        if row.get("sale_date") and sale is None:
            counts["rejected"] += 1
            rejected.append({"line": i, "reason": "sale_date is not a date (use YYYY-MM-DD or MM/DD/YYYY)"})
            continue
        fc_claim = None
        # A sale date on a TAX list is a tax sale, never a mortgage foreclosure.
        tax_list = bool(kind) and kind["signal"] in TAX_LIST_SIGNALS
        row_status = (row.get("case_status") or "").strip() or None
        if sale is not None:
            fc_claim = foreclosure_claim(sale, source=source_label, case_status=row_status, tax=tax_list)
            counts["sale_dates"] += 1
        elif declared is not None:
            fc_claim = foreclosure_claim(declared, source=source_label, case_status=row_status, tax=tax_list,
                                         declared_by=getattr(user, "email", None) or "the importer")
            counts["sale_dates_declared"] += 1
        if row.get("owner_name"):
            rec["owner"] = {"name": row["owner_name"], "mailing_street": row.get("mailing_street"),
                            "mailing_city": row.get("mailing_city"),
                            "mailing_state": row.get("mailing_state"), "mailing_zip": row.get("mailing_zip")}
        sigs = [s.strip().upper() for s in (row.get("signals") or "").replace(",", ";").split(";") if s.strip()]
        from app.services.evosense.signals import CATALOG
        if not sigs and kind:
            sigs = [kind["signal"]]
        if kind:
            case = (row.get("case_number") or "").strip()
            when = _parse_list_date(row.get("signal_date"))
            if row.get("signal_date") and when is None:
                counts["rejected"] += 1
                rejected.append({"line": i, "reason": "signal_date is not a date (use YYYY-MM-DD)"})
                continue
            what = "%s - %s" % (kind["label"], list_source or filename)
            rec["signals"] = [{
                "type": s, "confidence": 60, "value": what,
                "ref": ("list:%s:%s" % (list_kind, case)) if case else None,
                "effective_at": when.isoformat() if when else None,
                "case_status": (row.get("case_status") or "").strip() or None,
                "raw": (row.get("amount") or "").strip() or None,
                "evidence_basis": "imported list",
                "limitations": "As stated by an imported %s (%s); not verified against the "
                               "primary record." % (kind["label"].lower(), list_source or filename),
            } for s in sigs if s in CATALOG]
        elif fmt:
            rec["signals"] = [{"type": s, "confidence": 55,
                               "value": "As stated by a PropStream export (%s)" % (list_source or filename),
                               "evidence_basis": "imported PropStream export",
                               "limitations": "The export's own flag - not verified against the primary "
                                              "record (county clerk / court)."}
                              for s in sigs if s in CATALOG]
        else:
            rec["signals"] = [{"type": s, "confidence": 60, "value": "from imported list"}
                              for s in sigs if s in CATALOG]
        if fc_claim is not None:
            # one foreclosure claim per row: a list kind's own PRE_FORECLOSURE
            # takes the sale date instead of being doubled
            rec["signals"] = [x for x in rec.get("signals") or [] if x["type"] != fc_claim["type"]] + [fc_claim]
        outcome, prop = IN.ingest(db, org_id, provider, C.PROPERTY_SEARCH, rec,
                                  strategy_id=getattr(strategy, "id", None), user=user)
        counts[outcome] = counts.get(outcome, 0) + 1
        if prop is not None and (row.get("phones") or row.get("emails")):
            _attach_list_contacts(db, prop, row, list_source or filename, counts)
        if prop is not None:
            if strategy is not None and not prop.best_strategy_id:
                prop.best_strategy_id = strategy.id
            EV.rescore(db, prop, strategy)
    C.log_event(db, org_id, "import.csv", user=user, actor_type=C.ACTOR_USER,
                summary="Imported %s rows from %s%s: %s new, %s merged, %s for review, %s rejected"
                % (counts["rows"], filename, (" as %s" % kind["label"]) if kind else "",
                   counts["created"], counts["merged"], counts["review"], counts["rejected"]),
                details=dict(counts, list_kind=list_kind, list_source=list_source))
    return {"counts": counts, "rejected": rejected[:50], "list_kind": list_kind, "format": fmt}


def _attach_list_contacts(db, prop, row: Dict[str, Any], label: str, counts: Dict[str, Any]) -> None:
    """Phones / emails a list carries (a PropStream skip trace, say) become the
    LIST'S CONTACT CLAIM for the owner of record - only when the list names the
    same person (contact_match_guard), never on an unrelated homeowner, and
    never as permission to call or text. Free: the list was paid for elsewhere."""
    from app.services import wholesale_enrichment as WE
    from app.services.evosense import contacts as CT
    owner = CT.primary_owner(db, prop)
    if owner is None:
        counts["contacts_no_owner"] = counts.get("contacts_no_owner", 0) + 1
        return
    phones = []
    for item in (row.get("phones") or "").split(";"):
        num, _, kind = item.partition("|")
        if num:
            phones.append(WE.EnrichmentPhone(number=num, phone_type=kind or "unknown",
                                             source="list: %s" % label[:80]))
    emails = [e for e in (row.get("emails") or "").split(";") if e]
    res = WE.EnrichmentResult(status=WE.STATUS_SUCCEEDED, provider="list_import", phones=phones, emails=emails,
                              owner_name=row.get("owner_name") or row.get("list_owner_name") or None,
                              billable=False, cost_cents=0)
    from app.services import contact_match_guard as G
    if G.assess(owner.display_name, res)["verdict"] != G.MATCHED:
        # a list naming nobody, or only a surname, is not enough to put a
        # number on this owner - it waits for a person
        counts["contacts_refused"] = counts.get("contacts_refused", 0) + 1
        return
    touched = CT.apply_result(db, owner, res, PV.PROVIDERS["csv_import"])
    if touched:
        counts["contacts_attached"] = counts.get("contacts_attached", 0) + len(touched)
    else:
        counts["contacts_refused"] = counts.get("contacts_refused", 0) + 1


def preview_csv(db, org_id: str, content: str, *, filename: str = "upload.csv") -> Dict[str, Any]:
    """What an import WOULD do - nothing is written: the detected format, which
    columns were understood, rows that would be rejected, rows already
    imported (duplicates), and the distress signals the file asserts."""
    from app.models.evosense_models import EvoSenseObservation
    from app.services.evosense import propstream_csv as PS
    head = csv.DictReader(io.StringIO(content))
    fmt = None
    if PS.is_propstream(head.fieldnames or []):
        content, fmt = PS.convert(content)
    reader = csv.DictReader(io.StringIO(content))
    if "street_address" not in (reader.fieldnames or []):
        return {"ok": False, "format": fmt, "reason": "No street address column was recognised.",
                "columns": reader.fieldnames}
    out = {"ok": True, "format": (fmt or {}).get("format") or "evosense", "rows": 0, "would_reject": 0,
           "already_imported": 0, "new": 0, "signals": {}, "sample": [], "mapping": fmt}
    for i, row in enumerate(reader, start=2):
        if out["rows"] >= 5000:
            break
        out["rows"] += 1
        row = {k: (v or "").strip() for k, v in row.items() if k}
        if not row.get("street_address") or not (row.get("zip_code") or row.get("city")):
            out["would_reject"] += 1
            continue
        ref = row.get("record_id") or hashlib.sha1("|".join(
            row.get(c, "") for c in CSV_COLUMNS[:8]).lower().encode()).hexdigest()[:20]
        seen = (db.query(EvoSenseObservation.id)
                .filter(EvoSenseObservation.organization_id == org_id,
                        EvoSenseObservation.source_reference == "csv:%s" % ref).first())
        out["already_imported" if seen else "new"] += 1
        for sig in [x for x in (row.get("signals") or "").split(";") if x]:
            out["signals"][sig] = out["signals"].get(sig, 0) + 1
        if row.get("phones") or row.get("emails"):
            out["rows_with_contacts"] = out.get("rows_with_contacts", 0) + 1
        if len(out["sample"]) < 5:
            out["sample"].append({k: row.get(k) for k in ("street_address", "city", "zip_code", "owner_name",
                                                          "signals")})
    return out


def _parse_list_date(value):
    """A date written in a cell or typed by a person. Never a bare year, and
    never a value that merely contains digits (a file name, a list tag)."""
    v = re.sub(r"\s+", " ", str(value or "")).strip().rstrip(".")
    if not v:
        return None
    v = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", v, flags=re.I)
    if re.fullmatch(r"\d{2}-\d{2}-\d{4}", v):
        v = v.replace("-", "/")
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%b %d %Y",
                "%B %d %Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M:%S",
                "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


# ── background runs and pilot rollback ─────────────────────────────────────

def start_background(org_id: str, strategy_id: str, *, user_id: Optional[str] = None,
                     trigger: str = "manual", session_factory=None) -> bool:
    """Run one hunt on a worker thread with its own session, so a public-record
    pilot (tens of seconds to minutes of downloads) never holds a web request.
    Progress and the result are the run row + events; the strategy's hunt lock
    still guarantees one hunt at a time."""
    import threading

    def work():
        if session_factory is None:
            from app.deps import SessionLocal as factory
        else:
            factory = session_factory
        db = factory()
        try:
            s = db.query(EvoSenseStrategy).filter(EvoSenseStrategy.id == strategy_id,
                                                  EvoSenseStrategy.organization_id == org_id).first()
            if s is None:
                return
            user = None
            if user_id:
                from app.models.models import User
                user = db.query(User).filter(User.id == user_id).first()
            run_strategy(db, org_id, s, trigger=trigger, user=user)
        except Exception:  # noqa: BLE001 - the run row already records failures
            db.rollback()
            C.log.exception("evosense background hunt crashed (strategy %s)", strategy_id)
        finally:
            db.close()

    t = threading.Thread(target=work, name="evosense-hunt-%s" % strategy_id[:8], daemon=True)
    t.start()
    return True


def archive_pilot(db, org_id: str, strategy: EvoSenseStrategy, *, user, reason: str = None) -> Dict[str, Any]:
    """EASY ROLLBACK. Hide every property this strategy discovered that nobody
    has promoted or is talking to. Nothing is deleted: observations, raw
    evidence, signals, scores and decisions stay, and the archive is reversible."""
    from app.models.evosense_models import EvoSenseEngagement
    live = {pid for (pid,) in db.query(EvoSenseEngagement.property_id)
            .filter(EvoSenseEngagement.organization_id == org_id).all()}
    rows = (db.query(EvoSenseProperty)
            .filter(EvoSenseProperty.organization_id == org_id,
                    EvoSenseProperty.first_strategy_id == strategy.id,
                    EvoSenseProperty.archived_at.is_(None)).all())
    archived, kept = 0, 0
    why = (reason or "Pilot rolled back")[:200]
    for p in rows:
        if p.promoted_deal_id or p.id in live:
            kept += 1
            continue
        p.archived_at = C.now()
        p.archive_reason = why
        archived += 1
    C.log_event(db, org_id, "pilot.archived", strategy_id=strategy.id, user=user,
                actor_type=C.ACTOR_USER, is_test=strategy.is_test,
                summary="Pilot rollback: %s properties archived, %s kept (promoted or in conversation)"
                % (archived, kept), details={"archived": archived, "kept": kept, "reason": why})
    return {"archived": archived, "kept": kept}


def unarchive_pilot(db, org_id: str, strategy: EvoSenseStrategy, *, user) -> Dict[str, Any]:
    rows = (db.query(EvoSenseProperty)
            .filter(EvoSenseProperty.organization_id == org_id,
                    EvoSenseProperty.first_strategy_id == strategy.id,
                    EvoSenseProperty.archived_at.isnot(None)).all())
    for p in rows:
        p.archived_at = None
        p.archive_reason = None
    C.log_event(db, org_id, "pilot.restored", strategy_id=strategy.id, user=user,
                actor_type=C.ACTOR_USER, is_test=strategy.is_test,
                summary="Pilot restored: %s properties back in the inbox" % len(rows))
    return {"restored": len(rows)}
