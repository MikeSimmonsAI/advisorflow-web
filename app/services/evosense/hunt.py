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
import hashlib
import io
from datetime import timedelta
from typing import Any, Dict, List, Optional

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
    return {k: ST.lst(strategy, k) for k in ("states", "counties", "cities", "zips", "markets")}


def run_strategy(db, org_id: str, strategy: EvoSenseStrategy, *, trigger: str = "manual",
                 user=None, max_properties: int = 500, enrich: bool = True) -> EvoSenseRun:
    """THE one hunt, for "Run hunt" and for the scheduler alike (Phase 7.1).

    Takes the strategy's atomic hunt lock first: a second caller while a hunt
    is running gets a SKIPPED run ("Another hunt ... already running"), never a
    second hunt. A hunt that raises is rolled back, recorded as FAILED with the
    error, and the lock released; everything it had committed before the
    failure is idempotent, so the retry re-sees rather than re-creates."""
    from app.services.evosense import scheduler as SCH
    token = SCH.claim(db, strategy)
    if token is None:
        run = EvoSenseRun(organization_id=org_id, strategy_id=strategy.id, trigger=trigger,
                          status="skipped", error="Another hunt of this strategy is already running",
                          counts=C.jdump({}), finished_at=C.now())
        db.add(run)
        C.log_event(db, org_id, "hunt.skipped_lock", strategy_id=strategy.id, user=user,
                    actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION, is_test=strategy.is_test,
                    summary="Hunt not started: this strategy is already hunting")
        db.commit()
        return run
    try:
        run = _run_locked(db, org_id, strategy, trigger=trigger, user=user,
                          max_properties=max_properties, enrich=enrich)
    finally:
        SCH.release(db, strategy, token)
    if run.status != "skipped":
        SCH.record_result(db, strategy, run)
    return run


def _run_locked(db, org_id: str, strategy: EvoSenseStrategy, *, trigger: str, user,
                max_properties: int, enrich: bool) -> EvoSenseRun:
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
                          max_properties=max_properties, enrich=enrich)
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


def _hunt_body(db, org_id, strategy, run, ctl, counts, finish, *, user, max_properties, enrich):
    q = _query(strategy)
    touched: Dict[str, EvoSenseProperty] = {}
    for cap in DISCOVERY:
        for provider, cfg, _cost in PV.route(db, org_id, cap):
            try:
                records = provider.search(cap, q)
                PV.record_success(cfg)
            except Exception as exc:  # noqa: BLE001 - one failing source never ends the hunt
                PV.record_failure(cfg, "%s: %s" % (type(exc).__name__, str(exc)[:160]))
                counts["provider_errors"] += 1
                continue
            for rec in records:
                if len(touched) >= max_properties:
                    break
                counts["observed"] += 1
                outcome, prop = IN.ingest(db, org_id, provider, cap, rec, strategy_id=strategy.id,
                                          run_id=run.id, is_test=strategy.is_test)
                counts[outcome] = counts.get(outcome, 0) + 1
                if prop is not None:
                    touched[prop.id] = prop
        db.flush()

    for prop in touched.values():
        if prop.best_strategy_id and prop.best_strategy_id != strategy.id:
            other = db.query(EvoSenseStrategy).filter(EvoSenseStrategy.id == prop.best_strategy_id).first()
            if other is not None and other.status == "active":
                mine = SC.property_opportunity(prop, EV.stacked_signals(db, prop), strategy)["value"] or 0
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
    if enrich:
        from app.models.evosense_models import EvoSenseEnrichmentDecision
        for prop in above:
            if prop.status == C.S_WAITING_DATA:
                # Retry only a lookup that never happened (paused, no provider);
                # a real no-match / failure waits out its own retry window.
                last = (db.query(EvoSenseEnrichmentDecision)
                        .filter(EvoSenseEnrichmentDecision.organization_id == org_id,
                                EvoSenseEnrichmentDecision.property_id == prop.id)
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
    if pol.get("auto_outreach"):
        for prop in sorted(mine, key=lambda p: -(p.opportunity_score or 0)):
            if prop.status != C.S_READY:
                continue
            res = OU.start(db, prop, strategy)
            counts["outreach_started" if res.get("started") else "outreach_blocked"] += 1
            db.commit()
    return finish("partial" if counts["provider_errors"] else "succeeded")


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
               "record_id")


def import_csv(db, org_id: str, content: str, *, user, strategy=None, filename: str = "upload.csv",
               max_rows: int = 5000) -> Dict[str, Any]:
    """A property list through the SAME ingest as every provider. Re-importing
    the same file is idempotent (row identity = record_id, or a hash of the row)."""
    reader = csv.DictReader(io.StringIO(content))
    missing = [c for c in ("street_address",) if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError("The file needs a street_address column. Columns understood: %s"
                         % ", ".join(CSV_COLUMNS))
    provider = PV.PROVIDERS["csv_import"]
    counts = {"rows": 0, "created": 0, "merged": 0, "seen": 0, "review": 0, "rejected": 0}
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
        rec["source_reference"] = "csv:%s" % ref
        try:
            if row.get("estimated_value"):
                rec["valuation"] = {"value": int(float(row["estimated_value"].replace(",", "").replace("$", ""))),
                                    "mortgage": int(float(row["mortgage_balance"].replace(",", "").replace("$", "")))
                                    if row.get("mortgage_balance") else None,
                                    "basis": "imported file %s" % filename}
        except ValueError:
            counts["rejected"] += 1
            rejected.append({"line": i, "reason": "estimated_value / mortgage_balance not a number"})
            continue
        if row.get("owner_name"):
            rec["owner"] = {"name": row["owner_name"], "mailing_street": row.get("mailing_street"),
                            "mailing_city": row.get("mailing_city"),
                            "mailing_state": row.get("mailing_state"), "mailing_zip": row.get("mailing_zip")}
        sigs = [s.strip().upper() for s in (row.get("signals") or "").replace(",", ";").split(";") if s.strip()]
        from app.services.evosense.signals import CATALOG
        rec["signals"] = [{"type": s, "confidence": 60, "value": "from imported list"}
                          for s in sigs if s in CATALOG]
        outcome, prop = IN.ingest(db, org_id, provider, C.PROPERTY_SEARCH, rec,
                                  strategy_id=getattr(strategy, "id", None), user=user)
        counts[outcome] = counts.get(outcome, 0) + 1
        if prop is not None:
            if strategy is not None and not prop.best_strategy_id:
                prop.best_strategy_id = strategy.id
            EV.rescore(db, prop, strategy)
    C.log_event(db, org_id, "import.csv", user=user, actor_type=C.ACTOR_USER,
                summary="Imported %s rows from %s: %s new, %s merged, %s for review, %s rejected"
                % (counts["rows"], filename, counts["created"], counts["merged"], counts["review"],
                   counts["rejected"]), details=counts)
    return {"counts": counts, "rejected": rejected[:50]}
