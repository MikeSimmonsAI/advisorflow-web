"""TARGETED REPAIR for the Oct 17-19, 2026 Tarrant tax-roll misread.

The general re-derive tool (reprocess.py) re-derives EVERY property in the
workspace under the current rules. This repair touches only what the misread
produced, and nothing else:

SCOPE (exact, from evidence - never a guess)
    tax-roll observations saved by the OLD reader (adapter tarrant_tax_roll/2)
    whose raw line is the NEW 781-character layout. Their properties are the
    targets. Everything else in the workspace is out of scope.

PER TARGET PROPERTY
    * the saved raw line is read again with the corrected reader;
    * signals that came from THAT observation and the corrected record does
      not support are retracted (with a reason); one it does support is kept
      and given the corrected wording (the 12 truly delinquent);
    * every other signal - other sources, other tax-roll observations,
      evidence a person added - is PRESERVED untouched;
    * the observation's stored record is replaced by the corrected one (the
      raw line is never changed);
    * the owner of record is corrected (full name, mailing address) ONLY when
      that owner was created from this observation and belongs to no other
      property; otherwise it is left alone and listed as ambiguous;
    * the property is rescored by the normal rules (derived signals such as
      absentee / out-of-state follow the corrected owner).

    dry_run()  runs the whole repair inside a SAVEPOINT and rolls it back:
               nothing is written, and the scores shown are the ones the
               normal scoring rules actually produce.
    apply()    the same, committed, behind a typed code bound to the plan
               (the dry run's code no longer matches if anything changed).
               A full before-image is kept on a run record.
    rollback() puts every before-value back.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.models.evosense_models import (EvoSenseObservation, EvoSenseOwner, EvoSenseOwnership,
                                        EvoSenseProperty, EvoSenseReprocessRun, EvoSenseScore,
                                        EvoSenseSignal)
from app.services.evosense import common as C
from app.services.evosense import contacts as CT

VERSION = "roll_layout_fix/1"
OLD_ADAPTER = "tarrant_tax_roll/2"
NEW_LINE_MIN = 770
STATUS_APPLIED = "roll_fix_applied"          # never "applied": the general rollback must not touch it
STATUS_ROLLED_BACK = "roll_fix_rolled_back"
OWNER_FIELDS = ("display_name", "name_key", "name_truncated", "owner_type", "review_flags",
                "mailing_street", "mailing_city", "mailing_state", "mailing_zip", "mailing_key")
PROP_FIELDS = ("opportunity_score", "data_confidence", "contact_confidence", "seller_intent", "signal_count",
               "status", "next_action", "next_action_detail", "blocked_reason", "contactability",
               "contactability_detail", "contactability_at", "derivation_version", "city", "zip_code")


def _j(v):
    return v.isoformat() + "Z" if isinstance(v, datetime) else v


def affected_observations(db, org_id: str) -> List[EvoSenseObservation]:
    from sqlalchemy import func
    return (db.query(EvoSenseObservation)
            .filter(EvoSenseObservation.organization_id == org_id,
                    EvoSenseObservation.provider_key == "tarrant_tax_roll",
                    EvoSenseObservation.adapter_version == OLD_ADAPTER,
                    EvoSenseObservation.raw_payload.isnot(None),
                    func.length(EvoSenseObservation.raw_payload) >= NEW_LINE_MIN,
                    EvoSenseObservation.property_id.isnot(None))
            .order_by(EvoSenseObservation.property_id, EvoSenseObservation.id).all())


def _corrected(obs) -> Optional[Dict[str, Any]]:
    from app.services.evosense.sources.tarrant import TarrantTaxRollReader
    return TarrantTaxRollReader().record_from_raw(obs.raw_payload, obs.source_url or "", obs.source_updated_at)


def _sig_view(s) -> Dict[str, Any]:
    return {"id": s.id, "type": s.signal_type, "source": s.source, "ref": s.source_reference,
            "value": s.normalized_value}


def _owner_fix(db, org_id: str, prop, obs, rec) -> Dict[str, Any]:
    """The owner change for this property, or why there is none."""
    from app.services.evosense.ingest import mailing_key, name_key, owner_flags, owner_type_of
    links = (db.query(EvoSenseOwnership)
             .filter(EvoSenseOwnership.organization_id == org_id, EvoSenseOwnership.property_id == prop.id,
                     EvoSenseOwnership.observation_id == obs.id).all())
    want = rec.get("owner") or {}
    if not links or not want.get("name"):
        return {"action": "none", "why": "no owner came from this record"}
    owner = db.query(EvoSenseOwner).filter(EvoSenseOwner.id == links[0].owner_id).first()
    if owner is None:
        return {"action": "none", "why": "owner missing"}
    elsewhere = (db.query(EvoSenseOwnership.id)
                 .filter(EvoSenseOwnership.organization_id == org_id, EvoSenseOwnership.owner_id == owner.id,
                         EvoSenseOwnership.property_id != prop.id).count())
    other_sources = (db.query(EvoSenseOwnership.id)
                     .filter(EvoSenseOwnership.organization_id == org_id, EvoSenseOwnership.owner_id == owner.id,
                             EvoSenseOwnership.property_id == prop.id,
                             EvoSenseOwnership.observation_id != obs.id).count())
    after = {"display_name": want["name"], "name_key": name_key(want["name"]),
             "name_truncated": bool(want.get("name_truncated")),
             "owner_type": owner_type_of(want["name"]),
             "review_flags": C.jdump(owner_flags(want["name"], truncated=bool(want.get("name_truncated")))) or None,
             "mailing_street": want.get("mailing_street"), "mailing_city": want.get("mailing_city"),
             "mailing_state": (want.get("mailing_state") or "").upper()[:2] or None,
             "mailing_zip": want.get("mailing_zip"),
             "mailing_key": mailing_key(want.get("mailing_street"), want.get("mailing_zip"))}
    before = {f: getattr(owner, f) for f in OWNER_FIELDS}
    if all(before[f] == after[f] for f in OWNER_FIELDS):
        return {"action": "none", "why": "owner already correct", "owner_id": owner.id}
    if elsewhere or other_sources:
        return {"action": "ambiguous", "owner_id": owner.id, "before": before, "after": after,
                "why": "this owner is also on %s other propert%s / %s other record%s here - left for a person"
                       % (elsewhere, "y" if elsewhere == 1 else "ies", other_sources,
                          "" if other_sources == 1 else "s")}
    return {"action": "correct", "owner_id": owner.id, "before": before, "after": after}


def _plan_property(db, org_id: str, obs, prop) -> Dict[str, Any]:
    rec = _corrected(obs)
    keep_types = {s["type"]: s for s in (rec.get("signals") or [])}
    from_obs = (db.query(EvoSenseSignal)
                .filter(EvoSenseSignal.organization_id == org_id, EvoSenseSignal.property_id == prop.id,
                        EvoSenseSignal.observation_id == obs.id, EvoSenseSignal.source == "tarrant_tax_roll",
                        EvoSenseSignal.active.is_(True)).all())
    retract, rewrite = [], []
    for s in from_obs:
        good = keep_types.get(s.signal_type)
        if good is None:
            retract.append(_sig_view(s))
        elif (s.normalized_value or "") != (good.get("value") or ""):
            rewrite.append(dict(_sig_view(s), new_value=good.get("value")))
    preserved = [_sig_view(s) for s in db.query(EvoSenseSignal)
                 .filter(EvoSenseSignal.organization_id == org_id, EvoSenseSignal.property_id == prop.id,
                         EvoSenseSignal.active.is_(True)).all()
                 if not (s.observation_id == obs.id and s.source == "tarrant_tax_roll")
                 and s.source != "evosense_derived"]
    return {"property_id": prop.id, "observation_id": obs.id, "address": prop.street_address,
            "parcel_apn": prop.parcel_apn, "score_before": prop.opportunity_score, "status_before": prop.status,
            "retract": retract, "rewrite": rewrite, "preserved": preserved,
            "corrected_signals": [{"type": s["type"], "value": s.get("value")} for s in rec.get("signals") or []],
            "owner": _owner_fix(db, org_id, prop, obs, rec), "_record": rec}


def _snapshot(db, org_id: str, plan_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    pids = [r["property_id"] for r in plan_rows]
    snap = {"properties": {}, "signals": {}, "scores": {}, "owners": {}, "observations": {}}
    for p in db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id,
                                               EvoSenseProperty.id.in_(pids)).all():
        snap["properties"][p.id] = {f: _j(getattr(p, f, None)) for f in PROP_FIELDS}
    for s in db.query(EvoSenseSignal).filter(EvoSenseSignal.organization_id == org_id,
                                             EvoSenseSignal.property_id.in_(pids)).all():
        snap["signals"][s.id] = {"active": bool(s.active), "normalized_value": s.normalized_value,
                                 "raw_value": s.raw_value, "retracted_at": _j(s.retracted_at),
                                 "retracted_reason": s.retracted_reason, "effective_at": _j(s.effective_at)}
    for sc in db.query(EvoSenseScore).filter(EvoSenseScore.organization_id == org_id,
                                             EvoSenseScore.property_id.in_(pids)).all():
        snap["scores"][sc.id] = bool(sc.is_current)
    for r in plan_rows:
        o = r["owner"]
        if o["action"] == "correct":
            snap["owners"][o["owner_id"]] = o["before"]
        ob = db.query(EvoSenseObservation).filter(EvoSenseObservation.id == r["observation_id"]).first()
        snap["observations"][ob.id] = {"payload": ob.payload, "adapter_version": ob.adapter_version}
    return snap


def _do(db, org_id: str, plan_rows: List[Dict[str, Any]], *, tag: str) -> List[Dict[str, Any]]:
    """Perform the repair in the current transaction. Returns the after-view."""
    from app.services.evosense import evaluate as EV
    from app.services.evosense import ingest as IN
    now = C.now()
    out = []
    for r in plan_rows:
        prop = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == r["property_id"]).first()
        obs = db.query(EvoSenseObservation).filter(EvoSenseObservation.id == r["observation_id"]).first()
        rec = r["_record"]
        ids = {x["id"] for x in r["retract"]}
        for s in db.query(EvoSenseSignal).filter(EvoSenseSignal.id.in_(ids or ["-"])).all():
            s.active = False
            s.retracted_at = now
            s.retracted_reason = ("Tax-roll line read with the wrong column layout (Oct 2026); "
                                  "re-read by %s: not supported (%s)" % (VERSION, tag))[:250]
        good = {s["type"]: s for s in rec.get("signals") or []}
        for x in r["rewrite"]:
            s = db.query(EvoSenseSignal).filter(EvoSenseSignal.id == x["id"]).first()
            g = good[s.signal_type]
            s.normalized_value, s.raw_value = g.get("value"), g.get("raw")
            if g.get("effective_at"):
                s.effective_at = datetime.strptime(g["effective_at"][:10], "%Y-%m-%d")
        obs.payload = C.jdump(IN._sanitize(rec))
        obs.adapter_version = rec.get("_adapter_version") or "tarrant_tax_roll/3"
        o = r["owner"]
        if o["action"] == "correct":
            owner = db.query(EvoSenseOwner).filter(EvoSenseOwner.id == o["owner_id"]).first()
            for f in OWNER_FIELDS:
                setattr(owner, f, o["after"][f])
        db.flush()
        EV.rescore(db, prop)
        db.flush()
        out.append({"property_id": prop.id, "score_after": prop.opportunity_score, "status_after": prop.status,
                    "next_action_after": prop.next_action,
                    "active_after": sorted({s.signal_type for s in db.query(EvoSenseSignal)
                                           .filter(EvoSenseSignal.property_id == prop.id,
                                                   EvoSenseSignal.active.is_(True)).all()})})
    return out


def _public(rows):
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


def plan(db, org_id: str) -> Dict[str, Any]:
    obs = affected_observations(db, org_id)
    rows = []
    for o in obs:
        prop = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == o.property_id,
                                                 EvoSenseProperty.organization_id == org_id).first()
        if prop is not None:
            rows.append(_plan_property(db, org_id, o, prop))
    body = json.dumps(_public(rows), sort_keys=True, default=str)
    return {"rows": rows, "hash": hashlib.sha256(body.encode()).hexdigest()[:16]}


def _report(rows, after, code) -> Dict[str, Any]:
    by = {a["property_id"]: a for a in after}
    props = []
    for r in _public(rows):
        a = by.get(r["property_id"], {})
        props.append(dict(r, score_after=a.get("score_after"), status_after=a.get("status_after"),
                          next_action_after=a.get("next_action_after"), active_after=a.get("active_after")))

    def band(v):
        return "65+" if (v or 0) >= 65 else "55-64" if (v or 0) >= 55 else "45-54" if (v or 0) >= 45 else "under 45"
    moves: Dict[str, int] = {}
    for p in props:
        k = "%s -> %s" % (band(p["score_before"]), band(p["score_after"]))
        moves[k] = moves.get(k, 0) + 1
    return {
        "version": VERSION, "plan_code": code, "confirm_with": "APPLY ROLL FIX %s" % code,
        "scope": {"rule": "tax-roll observations read by %s whose raw line is the 781-character layout"
                          % OLD_ADAPTER, "properties": len(props),
                  "observations": len({p["observation_id"] for p in props})},
        "totals": {
            "signals_retracted": sum(len(p["retract"]) for p in props),
            "signals_rewritten_kept": sum(len(p["rewrite"]) for p in props),
            "signals_preserved_other_sources": sum(len(p["preserved"]) for p in props),
            "still_tax_delinquent": sum(1 for p in props if p["corrected_signals"]),
            "owners_corrected": sum(1 for p in props if p["owner"]["action"] == "correct"),
            "owners_ambiguous_left_alone": sum(1 for p in props if p["owner"]["action"] == "ambiguous"),
            "score_bands": moves,
            "at_or_above_45_before": sum(1 for p in props if (p["score_before"] or 0) >= 45),
            "at_or_above_45_after": sum(1 for p in props if (p["score_after"] or 0) >= 45),
        },
        "properties": props,
        "safeguards": [
            "Only the properties in scope are read or written; the scope is recomputed and must match the code.",
            "Signals from any other source or record, and anything a person entered, are never changed.",
            "Raw county lines are never changed; retracted signals are deactivated with a reason, not deleted.",
            "Owners shared with another property or record are not changed (listed as ambiguous).",
            "A full before-image is stored; rollback restores every signal, score, owner, record and property field.",
            "The general re-derive rollback cannot act on this run (different status).",
        ],
    }


def dry_run(db, org_id: str) -> Dict[str, Any]:
    """The full repair inside a savepoint, then rolled back. Writes nothing."""
    pl = plan(db, org_id)
    sp = db.begin_nested()
    try:
        after = _do(db, org_id, pl["rows"], tag="dry run")
    finally:
        sp.rollback()
    db.expire_all()
    return _report(pl["rows"], after, pl["hash"])


def apply(db, org_id: str, *, confirm: str, user=None) -> Dict[str, Any]:
    pl = plan(db, org_id)
    if not pl["rows"]:
        raise ValueError("Nothing in scope - already repaired?")
    if confirm != "APPLY ROLL FIX %s" % pl["hash"]:
        raise ValueError("Type APPLY ROLL FIX %s exactly (run the dry run again if the code changed)."
                         % pl["hash"])
    snap = _snapshot(db, org_id, pl["rows"])
    run = EvoSenseReprocessRun(organization_id=org_id, rule_version=VERSION, status="dry_run",
                               property_count=len(pl["rows"]), snapshot=C.jdump(snap),
                               diff=C.jdump(_public(pl["rows"])), summary=C.jdump({"plan": pl["hash"]}),
                               created_by_id=getattr(user, "id", None))
    db.add(run)
    db.flush()
    after = _do(db, org_id, pl["rows"], tag="run %s" % run.id)
    rep = _report(pl["rows"], after, pl["hash"])
    run.status = STATUS_APPLIED
    run.applied_at = C.now()
    run.applied_by_id = getattr(user, "id", None)
    run.summary = C.jdump({"plan": pl["hash"], "totals": rep["totals"]})
    C.log_event(db, org_id, "roll_fix.applied", user=user, actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM,
                summary="Tarrant tax-roll misread repaired on %s properties (plan %s)" % (len(pl["rows"]), pl["hash"]),
                details={"run_id": run.id, "totals": rep["totals"]})
    return {"run_id": run.id, "totals": rep["totals"]}


def rollback(db, org_id: str, run_id: str, *, user=None) -> Dict[str, Any]:
    run = (db.query(EvoSenseReprocessRun)
           .filter(EvoSenseReprocessRun.organization_id == org_id, EvoSenseReprocessRun.id == run_id).first())
    if run is None or run.status != STATUS_APPLIED or run.rule_version != VERSION:
        raise ValueError("Only an applied tax-roll repair of this workspace can be rolled back.")
    snap = C.jload(run.snapshot, {}) or {}
    col_types = {c.name: str(c.type) for c in EvoSenseProperty.__table__.columns}

    def dt(v):
        return datetime.fromisoformat(v.rstrip("Z")) if isinstance(v, str) else v
    for pid, cols in snap.get("properties", {}).items():
        p = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id,
                                              EvoSenseProperty.id == pid).first()
        for f, v in cols.items():
            setattr(p, f, dt(v) if col_types.get(f, "").startswith("DATETIME") else v)
    for sid, v in snap.get("signals", {}).items():
        s = db.query(EvoSenseSignal).filter(EvoSenseSignal.organization_id == org_id,
                                            EvoSenseSignal.id == sid).first()
        if s is None:
            continue
        s.active, s.normalized_value, s.raw_value = v["active"], v["normalized_value"], v["raw_value"]
        s.retracted_at, s.retracted_reason = dt(v["retracted_at"]), v["retracted_reason"]
        s.effective_at = dt(v["effective_at"])
    # Signals the repair's rescore created (derived) did not exist before: off.
    pids = list(snap.get("properties", {}).keys())
    for s in db.query(EvoSenseSignal).filter(EvoSenseSignal.organization_id == org_id,
                                             EvoSenseSignal.property_id.in_(pids or ["-"])).all():
        if s.id not in snap.get("signals", {}):
            s.active = False
            s.retracted_at, s.retracted_reason = C.now(), "rolled back (%s run %s)" % (VERSION, run.id)
    for sc in db.query(EvoSenseScore).filter(EvoSenseScore.organization_id == org_id,
                                             EvoSenseScore.property_id.in_(pids or ["-"])).all():
        sc.is_current = snap.get("scores", {}).get(sc.id, False)
    for oid, cols in snap.get("owners", {}).items():
        o = db.query(EvoSenseOwner).filter(EvoSenseOwner.organization_id == org_id, EvoSenseOwner.id == oid).first()
        for f, v in cols.items():
            setattr(o, f, v)
    for obid, v in snap.get("observations", {}).items():
        ob = db.query(EvoSenseObservation).filter(EvoSenseObservation.organization_id == org_id,
                                                  EvoSenseObservation.id == obid).first()
        ob.payload, ob.adapter_version = v["payload"], v["adapter_version"]
    run.status = STATUS_ROLLED_BACK
    run.rolled_back_at = C.now()
    run.rolled_back_by_id = getattr(user, "id", None)
    C.log_event(db, org_id, "roll_fix.rolled_back", user=user, actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM,
                summary="Tarrant tax-roll repair %s rolled back" % run.id, details={"run_id": run.id})
    return {"run_id": run.id, "restored_properties": len(pids)}
