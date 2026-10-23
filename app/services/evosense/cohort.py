"""Imported-list cohorts: one list (e.g. the 2010 "Top 100") followed as a group.

A cohort is every property carrying an observation from one distress-list
import (source_reference ``csv:<list_kind>:...``). Nothing here imports
anything: it reads what the list brought in, re-checks it against the free
public records EvoSense already connects to, scores it with the ordinary
scoring, and lays it out for a person to work.

Re-check (``recheck``) - every step free, every finding through the normal
ingest path (an observation per source, dated evidence, one property):
  * owner of record: the county / state lookups a hunt's lookup stage uses;
  * taxes: the Dallas County TRW file and the Tarrant tax roll, read for THESE
    accounts only; the statewide Linebarger tax-suit / tax-sale list by
    account or address. An account a roll carries with no past-due balance is
    recorded as checked-and-current (an audit event), never as a signal;
  * rescore with the property's strategy (or none) - weights are not touched.

Status words (``rows``) are evidence-bound. Mortgage foreclosure is
UNVERIFIED unless a dated notice record exists: no clerk notice source is
connected for these counties, and a 2010 file is history, not a notice.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseEvent, EvoSenseObservation,
                                        EvoSenseOwnership, EvoSenseProperty, EvoSenseSignal)
from app.services.evosense import common as C

OFFICIAL_OWNER_SOURCES = {"dcad", "tad", "collin_cad", "denton_gis", "txgio_parcels", "hcad_roll",
                          "txgio_roll", "dallas_trw", "tarrant_tax_roll", "travis_delinquent"}
TAX_TYPES = ("TAX_DELINQUENT", "TAX_SUIT", "TAX_SALE", "TAX_FORECLOSURE")
FORECLOSURE_TYPES = ("PRE_FORECLOSURE", "FORECLOSURE_NOTICE", "FORECLOSURE", "AUCTION")
TAX_CHECK_EVENT = "cohort.tax_checked"


def _prefix(kind: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", kind or ""):
        raise ValueError("Unknown list")
    return "csv:%s:" % kind


def cohorts(db, org_id: str) -> List[Dict[str, Any]]:
    """Every imported distress list in this workspace, with its size."""
    rows = (db.query(EvoSenseObservation.source_reference, EvoSenseObservation.property_id)
            .filter(EvoSenseObservation.organization_id == org_id,
                    EvoSenseObservation.provider_key == "csv_import",
                    EvoSenseObservation.source_reference.like("csv:%:%")).all())
    seen: Dict[str, set] = {}
    for ref, pid in rows:
        kind = ref.split(":", 2)[1]
        if pid:
            seen.setdefault(kind, set()).add(pid)
    return [{"kind": k, "properties": len(v)} for k, v in sorted(seen.items())]


def members(db, org_id: str, kind: str) -> List[EvoSenseProperty]:
    ids = {pid for (pid,) in db.query(EvoSenseObservation.property_id)
           .filter(EvoSenseObservation.organization_id == org_id,
                   EvoSenseObservation.provider_key == "csv_import",
                   EvoSenseObservation.source_reference.like(_prefix(kind) + "%")).all() if pid}
    if not ids:
        return []
    return (db.query(EvoSenseProperty)
            .filter(EvoSenseProperty.organization_id == org_id, EvoSenseProperty.id.in_(ids))
            .order_by(EvoSenseProperty.county, EvoSenseProperty.street_address).all())


# ── re-check ────────────────────────────────────────────────────────────────

def _county(p) -> str:
    return (p.county or "").strip().lower().replace(" county", "")


def _tax_checked(db, org_id, prop, source: str, file_date, user, found: bool):
    C.log_event(db, org_id, TAX_CHECK_EVENT, property_id=prop.id, user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM, is_test=prop.is_test,
                summary=("%s: past-due taxes on file" if found else "%s: on the roll, no past-due balance")
                % source,
                details={"source": source, "delinquent": found,
                         "file_date": file_date.strftime("%Y-%m-%d") if hasattr(file_date, "strftime") else file_date})


def tax_check(db, org_id: str, props: List[EvoSenseProperty], *, user=None, run_id=None) -> Dict[str, Any]:
    """Look THESE accounts up in the county delinquency files and the statewide
    tax-suit list. Findings are ingested like any hunt's; a clean account is
    recorded as checked. Each source is independent: one failing never stops
    the others."""
    from app.services.evosense import ingest as IN
    from app.services.evosense import providers as PV
    out: Dict[str, Any] = {}
    by_apn = {}
    for p in props:
        if p.parcel_apn:
            by_apn[(_county(p), p.parcel_apn.strip())] = p

    def ingest_all(key, cap, records):
        n = 0
        for rec in records:
            try:
                outcome, prop = IN.ingest(db, org_id, PV.PROVIDERS[key], cap, rec, run_id=run_id,
                                          user=user, is_test=False)
                n += 1 if prop is not None else 0
            except Exception as exc:  # noqa: BLE001 - one record never ends the check
                C.log.warning("cohort tax ingest (%s): %s", key, exc)
        db.commit()
        return n

    # Dallas County TRW (county unit), by DCAD account
    dal = {apn for (c, apn) in by_apn if c == "dallas"}
    if dal:
        from app.services.evosense.sources.dallas_trw import TrwReader
        try:
            res = TrwReader().discover(limit=len(dal), accounts=dal, min_due_cents=100)
            checked = res["stats"].get("checked") or set()
            hit = {r["parcel_apn"] for r in res["records"]}
            for apn in checked:
                _tax_checked(db, org_id, by_apn[("dallas", apn)], "Dallas County tax roll (TRW)",
                             res.get("source_updated_at"), user, apn in hit)
            out["dallas_trw"] = {"accounts": len(dal), "on_roll": len(checked), "delinquent": len(hit),
                                 "ingested": ingest_all("dallas_trw", C.TAX, res["records"]),
                                 "file_date": str(res.get("source_updated_at") or "")[:10]}
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            out["dallas_trw"] = {"error": str(exc)[:200]}

    # Tarrant County tax roll, by TAD account
    tar = {apn for (c, apn) in by_apn if c == "tarrant"}
    if tar:
        from app.services.evosense.sources.tarrant import TarrantTaxRollReader
        try:
            res = TarrantTaxRollReader().discover(limit=len(tar), accounts=tar, min_due=1.0)
            checked = res["stats"].get("checked") or set()
            hit = {r["parcel_apn"] for r in res["records"]}
            for apn in checked:
                _tax_checked(db, org_id, by_apn[("tarrant", apn)], "Tarrant County tax roll",
                             res.get("source_updated_at"), user, apn in hit)
            out["tarrant_tax_roll"] = {"accounts": len(tar), "on_roll": len(checked), "delinquent": len(hit),
                                       "ingested": ingest_all("tarrant_tax_roll", C.TAX, res["records"]),
                                       "file_date": str(res.get("source_updated_at") or "")[:10]}
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            out["tarrant_tax_roll"] = {"error": str(exc)[:200]}

    # Statewide Linebarger tax suits / tax sales, by account or by address
    try:
        from app.services.evosense.sources.lgbs import LgbsTaxSaleReader
        res = LgbsTaxSaleReader().discover(limit=100000, counties=["All Texas"])
        mine = []
        addr = {(_addr_key(p.street_address), (p.zip_code or "")[:5]): p for p in props}
        for rec in res["records"]:
            k = ((rec.get("county") or "").strip().lower().replace(" county", ""), (rec.get("parcel_apn") or "").strip())
            if k in by_apn or (_addr_key(rec.get("street_address")), (rec.get("zip_code") or "")[:5]) in addr:
                mine.append(rec)
        out["lgbs_tax_sales"] = {"listings_texas": len(res["records"]), "matched": len(mine),
                                 "ingested": ingest_all("lgbs_tax_sales", C.TAX, mine)}
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        out["lgbs_tax_sales"] = {"error": str(exc)[:200]}
    return out


def _addr_key(street: Optional[str]) -> str:
    from app.services.evosense.identity import normalize_street
    return (normalize_street(street or "")[0] or "").upper()


def recheck(db, org_id: str, kind: str, *, user=None) -> Dict[str, Any]:
    """Owner lookups, tax check and an ordinary rescore for every member."""
    from app.services.evosense import evaluate as EV
    from app.services.evosense import hunt as HU
    props = members(db, org_id, kind)
    if not props:
        return {"properties": 0}
    from types import SimpleNamespace
    counts: Dict[str, Any] = {}
    # A list re-check belongs to no strategy: decisions are recorded without one.
    HU.free_lookups(db, org_id, SimpleNamespace(id=None, is_test=False), SimpleNamespace(id=None),
                    props, counts)
    db.commit()
    tax = tax_check(db, org_id, props, user=user)
    for p in props:
        db.refresh(p)
        EV.rescore(db, p, EV.strategy_for(db, p))
    db.commit()
    C.log_event(db, org_id, "cohort.rechecked", user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM,
                summary="List %s re-checked against public records (%d properties)" % (kind, len(props)),
                details={"kind": kind, "lookups": counts.get("lookups"), "tax": tax})
    db.commit()
    return {"properties": len(props), "lookups": counts.get("lookups"), "tax": tax}


# ── the view ────────────────────────────────────────────────────────────────

def _signals(db, prop) -> List[EvoSenseSignal]:
    return (db.query(EvoSenseSignal)
            .filter(EvoSenseSignal.organization_id == prop.organization_id,
                    EvoSenseSignal.property_id == prop.id, EvoSenseSignal.active.is_(True)).all())


def foreclosure_status(sigs) -> Dict[str, Any]:
    fc = [s for s in sigs if s.signal_type in FORECLOSURE_TYPES]
    if not fc:
        return {"status": "UNVERIFIED", "detail": "No mortgage-foreclosure notice source is connected for this "
                                                  "county; the 2010 list is not evidence of a current notice."}
    s = max(fc, key=lambda x: x.observed_at or C.now())
    cs = (s.case_status or "").upper()
    word = {"POSTPONED": "POSTPONED", "CANCELED": "CANCELED", "CANCELLED": "CANCELED",
            "RESOLVED": "RESOLVED", "SOLD": "RESOLVED"}.get(cs, "ACTIVE NOTICE")
    return {"status": word, "detail": s.normalized_value or s.raw_value, "source": s.source,
            "date": (s.effective_at or s.observed_at).strftime("%Y-%m-%d") if (s.effective_at or s.observed_at) else None}


def tax_status(db, prop, sigs) -> Dict[str, Any]:
    t = [s for s in sigs if s.signal_type in TAX_TYPES]
    if t:
        kinds = sorted({s.signal_type for s in t})
        best = "TAX SALE" if "TAX_SALE" in kinds else "TAX LAWSUIT" if ("TAX_SUIT" in kinds or "TAX_FORECLOSURE" in kinds) \
            else "DELINQUENT"
        s = max(t, key=lambda x: x.observed_at or C.now())
        return {"status": best, "detail": s.normalized_value or s.raw_value, "source": s.source,
                "date": s.observed_at.strftime("%Y-%m-%d") if s.observed_at else None}
    ev = (db.query(EvoSenseEvent)
          .filter(EvoSenseEvent.organization_id == prop.organization_id, EvoSenseEvent.property_id == prop.id,
                  EvoSenseEvent.action == TAX_CHECK_EVENT)
          .order_by(EvoSenseEvent.created_at.desc()).first())
    if ev is not None:
        d = C.jload(ev.details, {}) or {}
        return {"status": "CURRENT", "detail": ev.summary, "source": d.get("source"),
                "date": d.get("file_date") or ev.created_at.strftime("%Y-%m-%d")}
    return {"status": "NOT CHECKED", "detail": "No county delinquency file connected for %s County"
            % (prop.county or "this")}


def owner_status(db, prop) -> Dict[str, Any]:
    from app.services.evosense import contacts as CT
    links = (db.query(EvoSenseOwnership)
             .filter(EvoSenseOwnership.organization_id == prop.organization_id,
                     EvoSenseOwnership.property_id == prop.id, EvoSenseOwnership.is_current.is_(True)).all())
    owners = CT.current_owners(db, prop)
    if not owners:
        return {"status": "NO OWNER", "name": None, "sources": []}
    o = owners[0]
    srcs = sorted({l.source for l in links if l.owner_id == o.id})
    official = [s for s in srcs if s in OFFICIAL_OWNER_SOURCES]
    status = "CONFLICT" if len(owners) > 1 or any(l.in_conflict for l in links) else \
        "VERIFIED" if official else "UNVERIFIED"
    return {"status": status, "name": o.display_name, "owner_id": o.id, "sources": srcs,
            "mailing": ", ".join(x for x in (o.mailing_street, o.mailing_city, o.mailing_state, o.mailing_zip) if x),
            "other_names": [x.display_name for x in owners[1:]]}


def contacts(db, owner_id: Optional[str]) -> Dict[str, Any]:
    """Contact points on file for the CURRENT owner only (a former owner's
    numbers belong to a different owner record and are never counted)."""
    if not owner_id:
        return {"phones": 0, "emails": 0, "verified": 0}
    pts = (db.query(EvoSenseContactPoint)
           .filter(EvoSenseContactPoint.owner_id == owner_id, EvoSenseContactPoint.status == "active").all())
    c = Counter()
    for p in pts:
        c["phones" if p.kind == "phone" else "emails" if p.kind == "email" else "other"] += 1
        if p.verified_at or (p.validation or "") == "valid":
            c["verified"] += 1
    return {"phones": c["phones"], "emails": c["emails"], "verified": c["verified"]}


def _priority(score, owner, tax, fc, distress) -> str:
    if owner["status"] != "VERIFIED":
        return "REVIEW"
    if tax["status"] in ("TAX SALE", "TAX LAWSUIT") or fc["status"] in ("ACTIVE NOTICE", "POSTPONED"):
        return "A"
    if tax["status"] == "DELINQUENT" or (score or 0) >= 45:
        return "B"
    if distress:
        return "C"
    return "PARK"


def _next_action(prio, owner, tax, contact) -> str:
    if owner["status"] == "NO OWNER":
        return "Find the owner of record (county lookup found none)"
    if owner["status"] == "CONFLICT":
        return "Resolve which owner is current"
    if owner["status"] != "VERIFIED":
        return "Confirm the owner on the county record"
    if prio == "PARK":
        return "Park - no current distress on record; recheck monthly"
    if not (contact["phones"] or contact["emails"]):
        return "Get contact details for %s (free sources exhausted; paid lookup is off)" % owner["name"]
    return "Call or mail %s about the property" % owner["name"]


def rows(db, org_id: str, kind: str) -> List[Dict[str, Any]]:
    from app.services.evosense import evaluate as EV
    from app.services.evosense import scoring as SC
    out = []
    for p in members(db, org_id, kind):
        sigs = _signals(db, p)
        owner = owner_status(db, p)
        tax = tax_status(db, p, sigs)
        fc = foreclosure_status(sigs)
        contact = contacts(db, owner.get("owner_id"))
        distress = sorted({s.signal_type for s in sigs if s.signal_type not in ("LONG_OWNERSHIP",)})
        strategy = EV.strategy_for(db, p)
        po = SC.property_opportunity(p, EV.stacked_signals(db, p), strategy, weights=EV.score_weights(db, org_id),
                                     owner=EV.CT.primary_owner(db, p), options=EV.score_options(db, org_id))
        earned = [f for f in po.get("factors") or [] if (f.get("points") or 0) > 0]
        held = [f["label"] for f in po.get("factors") or [] if (f.get("points") or 0) <= 0][:4]
        missing = []
        if tax["status"] == "NOT CHECKED":
            missing.append("tax status (no county file)")
        if fc["status"] == "UNVERIFIED":
            missing.append("foreclosure notice check")
        if p.equity_pct is None:
            missing.append("equity (no mortgage or value estimate)")
        if owner["status"] != "VERIFIED":
            missing.append("verified owner")
        prio = _priority(po.get("value"), owner, tax, fc, distress)
        out.append({
            "id": p.id, "street_address": p.street_address, "city": p.city, "zip_code": p.zip_code,
            "county": p.county, "parcel_apn": p.parcel_apn, "property_type": p.property_type,
            "owner": owner, "tax": tax, "foreclosure": fc, "contacts": contact,
            "equity": {"pct": p.equity_pct, "basis": p.equity_basis,
                       "confidence": "estimated" if p.equity_pct is not None else "unknown",
                       "appraisal": p.appraisal_value},
            "ownership_years": p.ownership_years,
            "signals": distress, "score": po.get("value"), "score_label": po.get("label"),
            "earned": [{"points": f["points"], "label": f["label"]} for f in earned],
            "held_back": held, "missing": missing, "priority": prio,
            "next_action": _next_action(prio, owner, tax, contact), "status": p.status,
        })
    rank = {"A": 0, "B": 1, "C": 2, "REVIEW": 3, "PARK": 4}
    out.sort(key=lambda r: (rank[r["priority"]], -(r["score"] or 0), r["street_address"] or ""))
    return out


def summary(rs: List[Dict[str, Any]]) -> Dict[str, Any]:
    c = lambda key: dict(Counter(r[key]["status"] if isinstance(r[key], dict) else r[key] for r in rs))  # noqa
    scores = [r["score"] for r in rs if r["score"] is not None]
    return {"properties": len(rs), "owner": c("owner"), "tax": c("tax"), "foreclosure": c("foreclosure"),
            "priority": dict(Counter(r["priority"] for r in rs)),
            "score": {"scored": len(scores), "max": max(scores) if scores else None,
                      "median": sorted(scores)[len(scores) // 2] if scores else None},
            "with_contacts": sum(1 for r in rs if r["contacts"]["phones"] or r["contacts"]["emails"]),
            "counties": dict(Counter(r["county"] for r in rs))}


_RUNNING: Dict[str, datetime] = {}


def start_recheck(org_id: str, kind: str, *, user_id: Optional[str] = None, session_factory=None) -> bool:
    """Re-check a list on a worker thread with its own session (the county
    files take minutes). One re-check per list at a time in this process; the
    result is the 'cohort.rechecked' event."""
    import threading
    key = "%s:%s" % (org_id, kind)
    started = _RUNNING.get(key)
    if started and (C.now() - started).total_seconds() < 3600:
        return False
    _RUNNING[key] = C.now()

    def work():
        factory = session_factory
        if factory is None:
            from app.deps import SessionLocal as factory
        db = factory()
        try:
            user = None
            if user_id:
                from app.models.models import User
                user = db.query(User).filter(User.id == user_id).first()
            recheck(db, org_id, kind, user=user)
        except Exception:  # noqa: BLE001 - recorded in the log; the list stays as it was
            db.rollback()
            C.log.exception("evosense list re-check crashed (%s)", kind)
        finally:
            _RUNNING.pop(key, None)
            db.close()

    threading.Thread(target=work, name="evosense-cohort-%s" % kind[:12], daemon=True).start()
    return True


def last_recheck(db, org_id: str, kind: str) -> Optional[Dict[str, Any]]:
    ev = (db.query(EvoSenseEvent)
          .filter(EvoSenseEvent.organization_id == org_id, EvoSenseEvent.action == "cohort.rechecked",
                  EvoSenseEvent.summary.like("List %s %%" % kind))
          .order_by(EvoSenseEvent.created_at.desc()).first())
    if ev is None:
        return None
    return {"at": ev.created_at.isoformat() if ev.created_at else None, "details": C.jload(ev.details, {})}


def running(org_id: str, kind: str) -> bool:
    return ("%s:%s" % (org_id, kind)) in _RUNNING
