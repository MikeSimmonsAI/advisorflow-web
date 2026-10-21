"""CUT-OFF OWNER NAMES - a controlled, previewed repair.

The Tarrant tax roll cuts owner names at its field width ("SAMPLE ROBIN AND
SAMPLE P"). Since the ingest fix, a later full-name record for the SAME owner
completes the name on arrival. Owners cut BEFORE that fix stay cut, and the
contact lookup refuses them ("confirm the full name first").

This module repairs those owners from records ALREADY ON FILE - nothing is
fetched - in three steps:

    preview(db, org_id)            writes nothing. Every cut owner, classified:
        SAFE           exactly one full name on file for the same owner: same
                       mailing address, and every word of the cut name is in the
                       full name (the last may itself be cut: "P" -> "PATTIE")
        AMBIGUOUS      more than one different full name qualifies, or a full
                       name at the same address that does NOT complete the cut
                       one (a relative, a new owner), or one at another address
        UNRECOVERABLE  no full-name record on file for that property
    plan(...)          the SAFE rows as an exact, reversible change list with a
                       hash; nothing else is in it
    apply(db, org_id, plan, confirm=<plan hash>, user)  changes only the plan's
                       fields, each checked against its "before" value first
    revert(db, org_id, applied_plan, user)  puts every "before" value back

Only owner name fields change (display_name, name_key, name_truncated,
owner_type, review_flags). Addresses, contacts, scores, ownership links and
every other field are left as they are. Each owner changed gets an audit event
carrying its before and after values.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from app.models.evosense_models import (EvoSenseObservation, EvoSenseOwner, EvoSenseOwnership,
                                        EvoSenseProperty)
from app.services.evosense import common as C
from app.services.evosense.ingest import (completes_cut_name, mailing_key, name_key, owner_flags,
                                          owner_type_of)

VERSION = "owner_names/v1"
SAFE, AMBIGUOUS, UNRECOVERABLE = "safe", "ambiguous", "unrecoverable"
FIELDS = ("display_name", "name_key", "name_truncated", "owner_type", "review_flags")


def _owner_records(db, org_id: str, property_ids: List[str], own_obs=()) -> List[Dict[str, Any]]:
    """Every owner block any source recorded for these properties."""
    out = []
    for o in (db.query(EvoSenseObservation)
              .filter(EvoSenseObservation.organization_id == org_id,
                      EvoSenseObservation.property_id.in_(property_ids or ["-"])).all()):
        again = reread(o)
        rec = again or C.jload(o.payload, {}) or {}
        own = rec.get("owner") or {}
        if own.get("name"):
            out.append({"name": own["name"].strip(), "truncated": bool(own.get("name_truncated")),
                        "mailing_key": mailing_key(own.get("mailing_street"), own.get("mailing_zip")),
                        "street_key": street_key(own.get("mailing_street")),
                        # the very record that named this owner
                        "own": o.id in own_obs, "reread": again is not None,
                        "source": o.provider_key, "reference": o.source_reference,
                        "observed_at": o.observed_at.isoformat() + "Z" if o.observed_at else None})
    return out


def reread(obs) -> Optional[Dict[str, Any]]:
    """A tax-roll observation is read again from its saved raw line with the
    CURRENT reader: lines saved Oct 17-19, 2026 were stored under the wrong
    column layout (cut names, missing mailing ZIP), and the raw line is the
    truth. Anything else uses what was stored."""
    if obs.provider_key != "tarrant_tax_roll" or not obs.raw_payload:
        return None
    try:
        from app.services.evosense.sources.tarrant import TarrantTaxRollReader
        return TarrantTaxRollReader().record_from_raw(obs.raw_payload, obs.source_url or "",
                                                      obs.source_updated_at)
    except Exception:  # noqa: BLE001 - an unreadable line keeps what was stored
        return None


def street_key(street: Optional[str]) -> Optional[str]:
    from app.services.evosense import identity as ID
    s, unit = ID.normalize_street(street)
    return "%s|%s" % (s, unit or "") if s else None


def _same_place(owner_key: Optional[str], own_streets, r: Dict[str, Any]) -> bool:
    """Same mailing address: the full keys when both have a ZIP; otherwise the
    normalized street with house number (and unit) must be identical."""
    if owner_key and r.get("mailing_key"):
        return r["mailing_key"] == owner_key
    return bool(r.get("street_key")) and r["street_key"] in own_streets


def classify(owner: EvoSenseOwner, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure: one cut owner + the owner records on file -> a verdict."""
    full = [r for r in records if not r["truncated"] and name_key(r["name"]) != owner.name_key]
    # The very county row that named this owner, read again with the corrected
    # layout: the same record, so the same owner - no name matching needed.
    reread_own = sorted({r["name"] for r in full if r.get("own") and r.get("reread")})
    if len(reread_own) == 1:
        best = [r for r in full if r.get("own") and r["name"] == reread_own[0]]
        return {"verdict": SAFE, "proposed": reread_own[0], "evidence": best,
                "reason": "The same county record (%s) read again with the corrected column layout."
                          % best[0]["source"]}
    if not full:
        return {"verdict": UNRECOVERABLE, "reason": "No record on file carries this owner's full name.",
                "proposed": None, "evidence": []}
    # Same mailing address. The owner's own key may lack a ZIP (the misread
    # tax-roll lines); then the street of the owner's own records must match
    # exactly - house number and street, normalized.
    own_streets = {r.get("street_key") for r in records
                   if r.get("street_key") and (r.get("own") or name_key(r["name"]) == owner.name_key)}
    if owner.mailing_key:
        parts = owner.mailing_key.split("|")
        own_streets.add("%s|%s" % (parts[0], parts[1] if len(parts) > 1 else ""))
    same_place = [r for r in full if r.get("own") or _same_place(owner.mailing_key, own_streets, r)]
    completing = [r for r in same_place if completes_cut_name(owner.display_name, r["name"])]
    names = sorted({name_key(r["name"]) for r in completing})
    if len(names) == 1:
        best = sorted(completing, key=lambda r: r["observed_at"] or "")[-1]
        others = [r for r in full if name_key(r["name"]) != names[0]]
        if others:
            return {"verdict": AMBIGUOUS, "proposed": best["name"],
                    "reason": "A full name completes it, but other records name someone else: %s."
                              % ", ".join(sorted({r["name"] for r in others})),
                    "evidence": completing + others}
        return {"verdict": SAFE, "proposed": best["name"], "evidence": completing,
                "reason": "Same mailing address; every word of the cut name is in %s (%s)."
                          % (best["name"], best["source"])}
    if len(names) > 1:
        return {"verdict": AMBIGUOUS, "proposed": None, "evidence": completing,
                "reason": "More than one different full name fits: %s." % ", ".join(
                    sorted({r["name"] for r in completing}))}
    if same_place:
        return {"verdict": AMBIGUOUS, "proposed": None, "evidence": same_place,
                "reason": "A full name at the same address does not complete the cut one "
                          "(a relative or a new owner?): %s." % ", ".join(sorted({r["name"] for r in same_place}))}
    return {"verdict": AMBIGUOUS, "proposed": None, "evidence": full,
            "reason": "Full names on file are at a different mailing address: %s."
                      % ", ".join(sorted({r["name"] for r in full}))}


def preview(db, org_id: str, *, include_test: bool = False) -> Dict[str, Any]:
    """Writes nothing."""
    q = db.query(EvoSenseOwner).filter(EvoSenseOwner.organization_id == org_id,
                                       EvoSenseOwner.name_truncated.is_(True))
    if not include_test:
        q = q.filter(EvoSenseOwner.is_test.is_(False))
    rows = []
    for o in q.all():
        links = (db.query(EvoSenseOwnership.property_id, EvoSenseOwnership.observation_id)
                 .filter(EvoSenseOwnership.organization_id == org_id, EvoSenseOwnership.owner_id == o.id,
                         EvoSenseOwnership.is_current.is_(True)).all())
        pids = [l.property_id for l in links]
        if not pids:
            continue
        v = classify(o, _owner_records(db, org_id, pids, {l.observation_id for l in links if l.observation_id}))
        addr = [p.street_address for p in db.query(EvoSenseProperty.street_address)
                .filter(EvoSenseProperty.id.in_(pids)).all() if p.street_address]
        rows.append({"owner_id": o.id, "old_name": o.display_name, "proposed": v["proposed"],
                     "verdict": v["verdict"], "reason": v["reason"], "properties": addr[:5],
                     "property_count": len(pids),
                     "sources": sorted({"%s:%s" % (e["source"], e["reference"]) for e in v["evidence"]})[:6]})
    counts = {k: sum(1 for r in rows if r["verdict"] == k) for k in (SAFE, AMBIGUOUS, UNRECOVERABLE)}
    return {"version": VERSION, "writes": "nothing", "total": len(rows), "counts": counts,
            "rows": sorted(rows, key=lambda r: (r["verdict"] != SAFE, r["old_name"] or ""))}


def _after(name: str) -> Dict[str, Any]:
    flags = owner_flags(name)
    return {"display_name": name, "name_key": name_key(name), "name_truncated": False,
            "owner_type": owner_type_of(name), "review_flags": C.jdump(flags) if flags else None}


def plan(db, org_id: str, preview_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The SAFE rows as an exact change list. Writes nothing."""
    pv = preview_result or preview(db, org_id)
    changes = []
    for r in pv["rows"]:
        if r["verdict"] != SAFE:
            continue
        o = db.query(EvoSenseOwner).filter(EvoSenseOwner.id == r["owner_id"],
                                           EvoSenseOwner.organization_id == org_id).first()
        if o is None:
            continue
        changes.append({"owner_id": o.id, "before": {f: getattr(o, f) for f in FIELDS},
                        "after": _after(r["proposed"]), "reason": r["reason"], "sources": r["sources"]})
    body = json.dumps(changes, sort_keys=True, default=str)
    return {"version": VERSION, "organization_id": org_id, "changes": changes,
            "hash": hashlib.sha256(body.encode()).hexdigest()[:16],
            "counts": pv["counts"]}


def apply(db, org_id: str, the_plan: Dict[str, Any], *, confirm: str, user=None) -> Dict[str, Any]:
    """Apply exactly the plan. Refuses a plan for another organization, a wrong
    confirmation, or any owner whose current values no longer match "before"
    (someone changed it since the preview) - that owner is skipped, not forced."""
    if the_plan.get("organization_id") != org_id:
        raise ValueError("This plan belongs to another workspace.")
    body = json.dumps(the_plan.get("changes") or [], sort_keys=True, default=str)
    if hashlib.sha256(body.encode()).hexdigest()[:16] != the_plan.get("hash") or confirm != the_plan.get("hash"):
        raise ValueError("Type the plan's code exactly to apply it.")
    done, skipped = [], []
    for ch in the_plan["changes"]:
        o = db.query(EvoSenseOwner).filter(EvoSenseOwner.id == ch["owner_id"],
                                           EvoSenseOwner.organization_id == org_id).first()
        if o is None or any(getattr(o, f) != ch["before"][f] for f in FIELDS):
            skipped.append(ch["owner_id"])
            continue
        for f in FIELDS:
            setattr(o, f, ch["after"][f])
        C.log_event(db, org_id, "owner.full_name_repaired", user=user,
                    actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM,
                    summary="Owner name completed from a record on file: %s -> %s"
                            % (ch["before"]["display_name"], ch["after"]["display_name"]),
                    details={"owner_id": o.id, "before": ch["before"], "after": ch["after"],
                             "plan": the_plan["hash"], "sources": ch.get("sources")})
        done.append(o.id)
    db.flush()
    return {"applied": len(done), "skipped_changed_since_preview": skipped, "plan": the_plan["hash"]}


def revert(db, org_id: str, the_plan: Dict[str, Any], *, user=None) -> Dict[str, Any]:
    """Put every "before" value back - only on owners still showing "after"."""
    if the_plan.get("organization_id") != org_id:
        raise ValueError("This plan belongs to another workspace.")
    n, skipped = 0, []
    for ch in the_plan["changes"]:
        o = db.query(EvoSenseOwner).filter(EvoSenseOwner.id == ch["owner_id"],
                                           EvoSenseOwner.organization_id == org_id).first()
        if o is None or any(getattr(o, f) != ch["after"][f] for f in FIELDS):
            skipped.append(ch["owner_id"])
            continue
        for f in FIELDS:
            setattr(o, f, ch["before"][f])
        C.log_event(db, org_id, "owner.full_name_reverted", user=user,
                    actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM,
                    summary="Owner name repair undone: %s" % ch["before"]["display_name"],
                    details={"owner_id": o.id, "restored": ch["before"], "plan": the_plan["hash"]})
        n += 1
    db.flush()
    return {"reverted": n, "skipped_changed_since": skipped}
