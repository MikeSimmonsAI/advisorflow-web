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
    # The corrected name + mailing address is ALREADY an owner here (usually
    # the appraisal district's record of the same person): point this record's
    # ownership at that owner rather than create a twin - which also settles
    # the "sources disagree about the owner" conflict the misread caused.
    same = None
    if after["name_key"] and after["mailing_key"]:
        same = (db.query(EvoSenseOwner)
                .filter(EvoSenseOwner.organization_id == org_id, EvoSenseOwner.id != owner.id,
                        EvoSenseOwner.name_key == after["name_key"],
                        EvoSenseOwner.mailing_key == after["mailing_key"])
                .order_by(EvoSenseOwner.created_at, EvoSenseOwner.id).first())
    if same is None and after["mailing_key"]:
        # The ingest's own rule for one owner spelled two ways by two county
        # files: already on THIS property, same mailing address, a shared name
        # word ("BURNETTE ROBERT AND BURNETTE PATTIE" / "ROBERT & PATTIE ETAL
        # BURNETTE").
        from app.services.evosense.ingest import same_owner_other_spelling
        cand = same_owner_other_spelling(db, org_id, prop, want["name"], after["mailing_key"])
        if cand is not None and cand.id != owner.id:
            same = cand
    if same is not None and not other_sources:
        return {"action": "relink", "owner_id": owner.id, "to_owner_id": same.id,
                "before": before, "after": {f: getattr(same, f) for f in OWNER_FIELDS},
                "link_ids": [l.id for l in links],
                "why": "the corrected owner already exists here (%s)" % (same.display_name or "")[:60]}
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


# ── before / after images ───────────────────────────────────────────────────
#
# Every row the repair can write is imaged IN FULL before it starts, and hashed
# after it finishes. Rollback refuses if any of those rows (or any new row on
# the target properties) changed after the repair - so it can never undo work
# done since - and otherwise restores the before-image exactly: changed rows
# back, rows the repair created removed, rows it removed put back.

def _cell(v):
    from decimal import Decimal
    if isinstance(v, datetime):
        return {"$dt": v.isoformat()}
    if hasattr(v, "isoformat") and not isinstance(v, str):
        return {"$d": v.isoformat()}
    if isinstance(v, Decimal):
        return {"$n": str(v)}
    return v


def _uncell(col, v):
    from datetime import date
    from decimal import Decimal
    if isinstance(v, dict):
        if "$dt" in v:
            return datetime.fromisoformat(v["$dt"])
        if "$d" in v:
            return date.fromisoformat(v["$d"])
        if "$n" in v:
            return Decimal(v["$n"])
    return v


def _row(o) -> Dict[str, Any]:
    return {c.name: _cell(getattr(o, c.key if hasattr(c, "key") else c.name)) for c in o.__table__.columns}


def _digest(row: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:20]


def _tracked(db, org_id: str, pids: List[str], obs_ids: List[str], owner_ids: List[str]):
    """{table: {id: row}} for every row the repair may touch."""
    from app.models.evosense_models import EvoSensePerson
    out = {}
    q = [("evosense_properties", EvoSenseProperty, EvoSenseProperty.id.in_(pids or ["-"])),
         ("evosense_signals", EvoSenseSignal, EvoSenseSignal.property_id.in_(pids or ["-"])),
         ("evosense_ownerships", EvoSenseOwnership, EvoSenseOwnership.property_id.in_(pids or ["-"])),
         ("evosense_observations", EvoSenseObservation, EvoSenseObservation.id.in_(obs_ids or ["-"])),
         ("evosense_owners", EvoSenseOwner, EvoSenseOwner.id.in_(owner_ids or ["-"])),
         ("evosense_persons", EvoSensePerson, EvoSensePerson.owner_id.in_(owner_ids or ["-"]))]
    for name, model, cond in q:
        out[name] = {o.id: _row(o) for o in db.query(model).filter(model.organization_id == org_id, cond).all()}
    out["evosense_scores"] = {sc.id: {"is_current": bool(sc.is_current)} for sc in db.query(EvoSenseScore)
                              .filter(EvoSenseScore.organization_id == org_id,
                                      EvoSenseScore.property_id.in_(pids or ["-"])).all()}
    return out


def _owner_ids(db, org_id: str, pids: List[str], plan_rows) -> List[str]:
    ids = {l.owner_id for l in db.query(EvoSenseOwnership.owner_id)
           .filter(EvoSenseOwnership.organization_id == org_id, EvoSenseOwnership.property_id.in_(pids or ["-"])).all()}
    for r in plan_rows:
        for k in ("owner_id", "to_owner_id"):
            if r["owner"].get(k):
                ids.add(r["owner"][k])
    return sorted(ids)


_MODELS = None


def _models():
    global _MODELS
    if _MODELS is None:
        from app.models.evosense_models import EvoSensePerson
        _MODELS = {"evosense_properties": EvoSenseProperty, "evosense_signals": EvoSenseSignal,
                   "evosense_ownerships": EvoSenseOwnership, "evosense_observations": EvoSenseObservation,
                   "evosense_owners": EvoSenseOwner, "evosense_persons": EvoSensePerson}
    return _MODELS


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
            owner.resolution = ("unresolved" if owner.owner_type in ("llc", "corporation", "trust")
                                else "resolved")
            _refresh_people(db, org_id, owner, obs.provider_key)
        elif o["action"] == "relink":
            for link in db.query(EvoSenseOwnership).filter(EvoSenseOwnership.id.in_(o["link_ids"])).all():
                twin = (db.query(EvoSenseOwnership)
                        .filter(EvoSenseOwnership.organization_id == org_id,
                                EvoSenseOwnership.property_id == prop.id,
                                EvoSenseOwnership.owner_id == o["to_owner_id"],
                                EvoSenseOwnership.source == link.source).first())
                if twin is not None:
                    # The right owner is already linked from this source (an
                    # earlier roll): the misread link stops being current.
                    link.is_current = False
                    twin.is_current = True
                else:
                    link.owner_id = o["to_owner_id"]
            db.flush()
            _settle_owner_conflict(db, org_id, prop)
        db.flush()
        EV.rescore(db, prop)
        db.flush()
        out.append({"property_id": prop.id, "score_after": prop.opportunity_score, "status_after": prop.status,
                    "next_action_after": prop.next_action,
                    "active_after": sorted({s.signal_type for s in db.query(EvoSenseSignal)
                                           .filter(EvoSenseSignal.property_id == prop.id,
                                                   EvoSenseSignal.active.is_(True)).all()})})
    return out


def _refresh_people(db, org_id: str, owner, source: str) -> None:
    """People named by the corrected name; people read off the misread name go
    (never one EvoSense is working or a person entered)."""
    from app.models.evosense_models import EvoSensePerson
    from app.services.evosense import ingest as IN
    want = {IN.name_key(f): (f, role) for f, role in IN._persons_for(owner.display_name, owner.owner_type)}
    for pr in db.query(EvoSensePerson).filter(EvoSensePerson.organization_id == org_id,
                                              EvoSensePerson.owner_id == owner.id).all():
        if pr.name_key not in want and pr.lead_id is None and pr.source == source:
            db.delete(pr)
        want.pop(pr.name_key, None)
    for nk, (full, role) in want.items():
        db.add(EvoSensePerson(organization_id=org_id, owner_id=owner.id, full_name=full, name_key=nk,
                              role=role, source=source, is_test=owner.is_test))
    db.flush()


def _settle_owner_conflict(db, org_id: str, prop) -> None:
    """When every current ownership now names ONE owner, the owner conflict
    the misread caused is over."""
    links = (db.query(EvoSenseOwnership)
             .filter(EvoSenseOwnership.organization_id == org_id, EvoSenseOwnership.property_id == prop.id,
                     EvoSenseOwnership.is_current.is_(True)).all())
    if links and len({l.owner_id for l in links}) == 1:
        for l in links:
            l.in_conflict = False
        items = C.jload(prop.conflicts, []) or []
        for c in items:
            if c.get("field") == "owner" and c.get("status") == "open":
                c["status"] = "resolved"
                c["resolved_by"] = VERSION
        prop.conflicts = C.jdump(items) if items else prop.conflicts
        prop.has_conflicts = any(c.get("status") == "open" for c in items)


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
            "owners_relinked_to_existing": sum(1 for p in props if p["owner"]["action"] == "relink"),
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
            "A full before-image of every row the repair can write is stored; rollback restores it exactly.",
            "Rollback refuses if any of those rows, or the target properties' evidence, changed after the repair.",
            "Apply refuses while a hunt is running and locks the target properties while it works.",
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


def _hunt_running(db, org_id: str) -> bool:
    from app.models.evosense_models import EvoSenseHuntSchedule
    return db.query(EvoSenseHuntSchedule.id).filter(
        EvoSenseHuntSchedule.organization_id == org_id,
        EvoSenseHuntSchedule.locked_until.isnot(None),
        EvoSenseHuntSchedule.locked_until > C.now()).first() is not None


def apply(db, org_id: str, *, confirm: str, user=None) -> Dict[str, Any]:
    if _hunt_running(db, org_id):
        raise ValueError("A hunt is running. Apply the repair when it has finished.")
    pl = plan(db, org_id)
    if not pl["rows"]:
        raise ValueError("Nothing in scope - already repaired?")
    if confirm != "APPLY ROLL FIX %s" % pl["hash"]:
        raise ValueError("Type APPLY ROLL FIX %s exactly (run the dry run again if the code changed)."
                         % pl["hash"])
    pids = [r["property_id"] for r in pl["rows"]]
    obs_ids = [r["observation_id"] for r in pl["rows"]]
    # Lock the targets (Postgres) so nothing writes them while the repair works.
    db.query(EvoSenseProperty.id).filter(EvoSenseProperty.id.in_(pids)).with_for_update().all()
    owner_ids = _owner_ids(db, org_id, pids, pl["rows"])
    before = _tracked(db, org_id, pids, obs_ids, owner_ids)
    run = EvoSenseReprocessRun(organization_id=org_id, rule_version=VERSION, status="dry_run",
                               property_count=len(pids), snapshot=C.jdump({
                                   "pids": pids, "obs_ids": obs_ids, "owner_ids": owner_ids, "before": before}),
                               diff=C.jdump(_public(pl["rows"])), summary=C.jdump({"plan": pl["hash"]}),
                               created_by_id=getattr(user, "id", None))
    db.add(run)
    db.flush()
    after_view = _do(db, org_id, pl["rows"], tag="run %s" % run.id)
    db.flush()
    after = _tracked(db, org_id, pids, obs_ids, owner_ids)
    rep = _report(pl["rows"], after_view, pl["hash"])
    run.status = STATUS_APPLIED
    run.applied_at = C.now()
    run.applied_by_id = getattr(user, "id", None)
    run.applied_changes = C.jdump({t: {i: _digest(r) for i, r in rows.items()} for t, rows in after.items()})
    run.summary = C.jdump({"plan": pl["hash"], "totals": rep["totals"]})
    C.log_event(db, org_id, "roll_fix.applied", user=user, actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM,
                summary="Tarrant tax-roll misread repaired on %s properties (plan %s)" % (len(pids), pl["hash"]),
                details={"run_id": run.id, "totals": rep["totals"]})
    return {"run_id": run.id, "totals": rep["totals"]}


def rollback(db, org_id: str, run_id: str, *, user=None) -> Dict[str, Any]:
    """Restore the before-image - only if nothing has touched those rows since."""
    run = (db.query(EvoSenseReprocessRun)
           .filter(EvoSenseReprocessRun.organization_id == org_id, EvoSenseReprocessRun.id == run_id).first())
    if run is None or run.status != STATUS_APPLIED or run.rule_version != VERSION:
        raise ValueError("Only an applied tax-roll repair of this workspace can be rolled back.")
    snap = C.jload(run.snapshot, {}) or {}
    pids, obs_ids, owner_ids = snap["pids"], snap["obs_ids"], snap["owner_ids"]
    db.query(EvoSenseProperty.id).filter(EvoSenseProperty.id.in_(pids or ["-"])).with_for_update().all()
    expected = C.jload(run.applied_changes, {}) or {}
    now = _tracked(db, org_id, pids, obs_ids, owner_ids)
    changed = []
    for t, rows in now.items():
        exp = expected.get(t, {})
        for i, r in rows.items():
            if exp.get(i) != _digest(r):
                changed.append("%s %s" % (t, i))
        for i in exp:
            if i not in rows:
                changed.append("%s %s (removed)" % (t, i))
    if changed:
        raise ValueError("Changed since the repair, so rollback would undo later work (%d rows, e.g. %s). "
                         "Nothing was rolled back." % (len(changed), ", ".join(changed[:3])))
    before = snap["before"]
    models = _models()
    for t, model in models.items():
        cols = {c.name: c for c in model.__table__.columns}
        was = before.get(t, {})
        for i in set(now.get(t, {})) - set(was):                       # created by the repair
            db.query(model).filter(model.id == i).delete(synchronize_session=False)
        db.flush()
        for i, row in was.items():
            obj = db.query(model).filter(model.id == i).first()
            vals = {k: _uncell(cols[k], v) for k, v in row.items() if k in cols}
            if obj is None:                                           # removed by the repair
                db.add(model(**vals))
            else:
                for k, v in vals.items():
                    setattr(obj, k, v)
        db.flush()
    for i in set(now.get("evosense_scores", {})) - set(before.get("evosense_scores", {})):
        db.query(EvoSenseScore).filter(EvoSenseScore.id == i).delete(synchronize_session=False)
    for sc in db.query(EvoSenseScore).filter(EvoSenseScore.organization_id == org_id,
                                             EvoSenseScore.id.in_(list(before.get("evosense_scores", {})) or ["-"])).all():
        sc.is_current = before["evosense_scores"][sc.id]["is_current"]
    run.status = STATUS_ROLLED_BACK
    run.rolled_back_at = C.now()
    run.rolled_back_by_id = getattr(user, "id", None)
    C.log_event(db, org_id, "roll_fix.rolled_back", user=user, actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM,
                summary="Tarrant tax-roll repair %s rolled back" % run.id, details={"run_id": run.id})
    return {"run_id": run.id, "restored_properties": len(pids)}
