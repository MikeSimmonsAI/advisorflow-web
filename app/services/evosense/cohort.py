"""Imported-list cohorts: one imported list (e.g. the "Top 100") followed as a group.

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

Status words (``rows``) are evidence-bound and never come from a list's name
or tag. Mortgage foreclosure, tax delinquency, tax foreclosure and probate are
separate columns; a current tax account says nothing about a mortgage.
  * CLAIMED     - a list (a sale-date column, or a sale date a person declared
                  for the list) says a trustee sale is scheduled; not yet
                  confirmed on the county clerk's posted notice;
  * VERIFIED    - a clerk / trustee notice record from a connected source;
  * SALE PASSED - the scheduled date is behind us: sold, cancelled or
                  postponed is unknown until checked;
  * NO EVIDENCE - nothing says foreclosure, which is not proof there is none.
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


def foreclosure_status(sigs, today=None) -> Dict[str, Any]:
    from datetime import date
    from app.services.evosense import scoring as SC
    today = today or date.today()
    fc = [x for x in sigs if x.signal_type in FORECLOSURE_TYPES]
    if not fc:
        return {"status": "NO EVIDENCE", "verified": False, "sale_date": None, "days": None,
                "detail": "No foreclosure claim or notice on file. Not proof there is none: no county "
                          "clerk notice source is connected for this county.", "last_checked": None}
    ev = [{"source": x.source, "provenance": C.jload(x.provenance, {}) or {}} for x in fc]
    terms = SC.foreclosure_terms({"evidence": ev}, today=today)
    newest = max(fc, key=lambda x: x.observed_at or C.now())
    cs = (newest.case_status or "").upper()
    if terms["passed"]:
        word = "SALE PASSED"
    elif cs in ("POSTPONED", "CANCELED", "CANCELLED", "RESOLVED", "SOLD"):
        word = {"CANCELLED": "CANCELED", "SOLD": "RESOLVED"}.get(cs, cs)
    elif terms["verified"]:
        word = "VERIFIED NOTICE"
    else:
        word = "CLAIMED"
    pick = next((x for x in fc if (C.jload(x.provenance, {}) or {}).get("sale_date") == terms["sale_date"]), newest)
    prov = C.jload(pick.provenance, {}) or {}
    return {"status": word, "verified": terms["verified"], "sale_date": terms["sale_date"],
            "days": terms["days"], "detail": pick.normalized_value or pick.raw_value, "source": pick.source,
            "evidence_reference": prov.get("record") or pick.source_reference,
            "observed": pick.observed_at.strftime("%Y-%m-%d") if pick.observed_at else None,
            "last_checked": (getattr(pick, "last_seen_at", None) or pick.observed_at).strftime("%Y-%m-%d")
            if (getattr(pick, "last_seen_at", None) or pick.observed_at) else None}


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
    from app.services.evosense.ingest import REVIEW_FLAG_LABELS, owner_flags
    flags = owner_flags(o.display_name or "")
    return {"status": status, "name": o.display_name, "owner_id": o.id, "sources": srcs,
            "flags": [{"key": f, "label": REVIEW_FLAG_LABELS.get(f, f)} for f in flags],
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


IMMINENT_DAYS = 45


def _priority(score, owner, tax, fc, distress) -> str:
    """A: a scheduled foreclosure sale within IMMINENT_DAYS (verified, or
    claimed by the list - the next action then says to confirm it), a tax sale
    or a tax lawsuit. B: delinquent taxes or a score of 45+. C: other current
    distress. Taxes being current never lowers a foreclosure."""
    if owner["status"] != "VERIFIED":
        return "REVIEW"
    imminent = fc.get("days") is not None and 0 <= fc["days"] <= IMMINENT_DAYS \
        and fc["status"] in ("VERIFIED NOTICE", "CLAIMED", "POSTPONED")
    if imminent or tax["status"] in ("TAX SALE", "TAX LAWSUIT"):
        return "A"
    if fc["status"] in ("VERIFIED NOTICE", "CLAIMED", "POSTPONED") or tax["status"] == "DELINQUENT" \
            or (score or 0) >= 45:
        return "B"
    if distress or fc["status"] == "SALE PASSED":
        return "C"
    return "PARK"


def _next_action(prio, owner, tax, contact, fc=None) -> str:
    fc = fc or {}
    if owner["status"] == "NO OWNER":
        return "Find the owner of record (county lookup found none)"
    if owner["status"] == "CONFLICT":
        return "Resolve which owner is current"
    if owner["status"] != "VERIFIED":
        return "Confirm the owner on the county record"
    keys = {f["key"] for f in owner.get("flags") or []}
    if "ESTATE_INDICATED" in keys or "LIFE_ESTATE" in keys:
        return "Confirm probate / heirs (who can sell) on the county and court record before any contact"
    if fc.get("status") == "CLAIMED" and fc.get("days") is not None:
        return ("Confirm the %s trustee sale on the %s County clerk's posted notice (%d days left), then "
                "reach %s" % (fc["sale_date"], fc.get("county") or "", fc["days"], owner["name"]))
    if fc.get("status") == "SALE PASSED":
        return "Check whether the %s sale happened, was cancelled or postponed" % fc.get("sale_date")
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
        fc["county"] = p.county
        missing = []
        if tax["status"] == "NOT CHECKED":
            missing.append("tax status (no county file)")
        if fc["status"] in ("CLAIMED", "NO EVIDENCE"):
            missing.append("clerk foreclosure-notice check")
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
            "next_action": _next_action(prio, owner, tax, contact, fc), "status": p.status,
            "list_claims": _list_claims(db, p),
        })
    rank = {"A": 0, "B": 1, "C": 2, "REVIEW": 3, "PARK": 4}
    out.sort(key=lambda r: (rank[r["priority"]], -(r["score"] or 0),
                            r["foreclosure"]["days"] if r["foreclosure"]["days"] is not None else 9999,
                            r["street_address"] or ""))
    return out


def _list_claims(db, prop) -> Dict[str, Any]:
    """What the imported list itself said about this house (its owner name,
    loan type, value / balance), kept apart from the county's record."""
    out: Dict[str, Any] = {}
    for (payload,) in (db.query(EvoSenseObservation.payload)
                       .filter(EvoSenseObservation.organization_id == prop.organization_id,
                               EvoSenseObservation.property_id == prop.id,
                               EvoSenseObservation.provider_key == "csv_import").all()):
        p = C.jload(payload, {}) or {}
        out.update(((p.get("evidence") or {}).get("list_claims")) or {})
        v = p.get("valuation") or {}
        if v.get("value"):
            out.setdefault("list_value", v.get("value"))
        if v.get("mortgage") is not None:
            out.setdefault("list_unpaid_balance", v.get("mortgage"))
    return out


def summary(rs: List[Dict[str, Any]]) -> Dict[str, Any]:
    c = lambda key: dict(Counter(r[key]["status"] if isinstance(r[key], dict) else r[key] for r in rs))  # noqa
    scores = [r["score"] for r in rs if r["score"] is not None]
    fcs = [r["foreclosure"] for r in rs]
    dated = [f for f in fcs if f.get("sale_date")]
    sale_dates = {"supplied": sum(1 for f in dated if not f.get("verified")),
                  "verified": sum(1 for f in dated if f.get("verified")),
                  "missing": len(rs) - len(dated),
                  "dates": dict(Counter(f["sale_date"] for f in dated))}
    buckets = Counter()
    for sc_ in scores:
        buckets["%d-%d" % (sc_ // 10 * 10, sc_ // 10 * 10 + 9)] += 1
    return {"sale_dates": sale_dates, "score_buckets": dict(sorted(buckets.items(), key=lambda kv: int(kv[0].split("-")[0]))),
            "properties": len(rs), "owner": c("owner"), "tax": c("tax"), "foreclosure": c("foreclosure"),
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


# ── a list's own claims, attached to the houses it already brought in ──────

def _row_key(street: Optional[str], zip_code: Optional[str]):
    from app.services.evosense.sources.txgio import _core
    num, core = _core(street or "")
    return (num, core, re.sub(r"\D", "", str(zip_code or ""))[:5])


def _original_rows(db, org_id: str, kind: str) -> Dict[tuple, EvoSenseProperty]:
    """Each member keyed by the street / ZIP its list FIRST gave (before any
    identifier correction), so the same list's columns find the same house."""
    out: Dict[tuple, EvoSenseProperty] = {}
    props = {p.id: p for p in members(db, org_id, kind)}
    for o in (db.query(EvoSenseObservation)
              .filter(EvoSenseObservation.organization_id == org_id,
                      EvoSenseObservation.provider_key == "csv_import",
                      EvoSenseObservation.source_reference.like(_prefix(kind) + "%")).all()):
        p = props.get(o.property_id)
        if p is None:
            continue
        pay = C.jload(o.payload, {}) or {}
        for key in (_row_key(pay.get("street_address"), pay.get("zip_code")), _row_key(p.street_address, p.zip_code)):
            if key[0]:
                out.setdefault(key, p)
    return out


def reattach(db, org_id: str, kind: str, content: str, *, user, filename: str = "list.csv",
             declared_sale_date: Optional[str] = None) -> Dict[str, Any]:
    """Read the list's FULL file again and attach what the first import left
    out (value, unpaid balance, loan type, the list's owner name, sale dates)
    to the SAME properties - matched row by row to the house this list already
    created. A row matching no member is reported, never created. Idempotent.
    The file name is only recorded; nothing is read out of it."""
    import csv as _csv
    import hashlib
    import io as _io
    from app.services.evosense import evaluate as EV
    from app.services.evosense import hunt as HU
    from app.services.evosense import ingest as IN
    from app.services.evosense import propstream_csv as PS
    from app.services.evosense import providers as PV
    head = _csv.DictReader(_io.StringIO(content))
    if PS.is_propstream(head.fieldnames or []):
        content, _fmt = PS.convert(content)
    declared = HU._parse_list_date(declared_sale_date) if declared_sale_date else None
    if declared_sale_date and declared is None:
        raise ValueError("The list's sale date is not a date (use YYYY-MM-DD or MM/DD/YYYY)")
    index = _original_rows(db, org_id, kind)
    provider = PV.PROVIDERS["csv_import"]
    out = Counter()
    unmatched: List[str] = []
    touched: Dict[str, EvoSenseProperty] = {}
    for row in _csv.DictReader(_io.StringIO(content)):
        row = {k: (v or "").strip() for k, v in row.items() if k}
        out["rows"] += 1
        p = index.get(_row_key(row.get("street_address"), row.get("zip_code")))
        if p is None:
            out["unmatched"] += 1
            unmatched.append("%s %s" % (row.get("street_address"), row.get("zip_code")))
            continue
        ref = hashlib.sha1(("%s|%s" % (row.get("street_address"), row.get("zip_code"))).upper().encode()).hexdigest()[:16]
        rec = {"source_reference": "%sfull:%s" % (_prefix(kind), ref),
               "street_address": p.street_address, "city": p.city, "state": p.state, "zip_code": p.zip_code,
               "county": p.county, "signals": []}
        claims = {k: row.get(k) for k in HU.CLAIM_COLUMNS if row.get(k)}
        if claims:
            rec["_evidence"] = {"list_claims": claims}
        def num(v):
            v = re.sub(r"[^0-9.]", "", v or "")
            return int(float(v)) if v else None
        val, bal = num(row.get("estimated_value")), num(row.get("mortgage_balance"))
        if val:
            rec["valuation"] = {"value": val, "mortgage": bal, "basis": "imported list (value and unpaid "
                                                                       "balance as the list states them)"}
        sale = HU._parse_list_date(row.get("sale_date")) if row.get("sale_date") else None
        if sale is not None:
            rec["signals"].append(HU.foreclosure_claim(sale, source="the imported list"))
            out["sale_dates"] += 1
        elif declared is not None:
            rec["signals"].append(HU.foreclosure_claim(declared, source="the imported list",
                                                       declared_by=getattr(user, "email", None) or "the importer"))
            out["sale_dates_declared"] += 1
        outcome, prop = IN.ingest(db, org_id, provider, C.PROPERTY_SEARCH, rec, user=user,
                                  target=p, target_trusted=True)
        out[outcome] += 1
        if prop is not None:
            touched[prop.id] = prop
    db.flush()
    for prop in touched.values():
        EV.rescore(db, prop, EV.strategy_for(db, prop))
    C.log_event(db, org_id, "cohort.reattached", user=user, actor_type=C.ACTOR_USER,
                summary="List %s: full file read again (%s) - %d rows matched to its houses, %d unmatched"
                % (kind, filename, len(touched), out["unmatched"]),
                details={"kind": kind, "filename": filename, "counts": dict(out), "unmatched": unmatched[:50],
                         "declared_sale_date": declared.strftime("%Y-%m-%d") if declared else None})
    db.commit()
    return {"counts": dict(out), "matched_properties": len(touched), "unmatched": unmatched[:50]}


def declare_sale(db, org_id: str, kind: str, sale_date: str, *, user, note: str) -> Dict[str, Any]:
    """A person states that every house on this list is scheduled for a
    foreclosure sale on `sale_date`. Recorded per house as THEIR claim (name,
    note, time), never as a verified notice. Idempotent per date."""
    from app.services.evosense import evaluate as EV
    from app.services.evosense import hunt as HU
    from app.services.evosense import ingest as IN
    from app.services.evosense import providers as PV
    sale = HU._parse_list_date(sale_date)
    if sale is None:
        raise ValueError("The sale date is not a date (use YYYY-MM-DD or MM/DD/YYYY)")
    if len((note or "").strip()) < 5:
        raise ValueError("Say where the date comes from.")
    who = getattr(user, "email", None) or "a workspace user"
    n = Counter()
    for p in members(db, org_id, kind):
        claim = HU.foreclosure_claim(sale, source="the list's owner (%s)" % (note.strip()[:120]), declared_by=who)
        rec = {"source_reference": "%sdeclared-sale:%s:%s" % (_prefix(kind), p.id, sale.strftime("%Y%m%d")),
               "street_address": p.street_address, "city": p.city, "state": p.state, "zip_code": p.zip_code,
               "county": p.county, "signals": [claim]}
        outcome, prop = IN.ingest(db, org_id, PV.PROVIDERS["csv_import"], C.PROPERTY_SEARCH, rec, user=user,
                                  target=p, target_trusted=True)
        n[outcome] += 1
        if prop is not None:
            EV.rescore(db, prop, EV.strategy_for(db, prop))
    C.log_event(db, org_id, "cohort.sale_declared", user=user, actor_type=C.ACTOR_USER,
                summary="List %s: foreclosure sale %s declared by %s for %d houses (claim, unverified)"
                % (kind, sale.strftime("%Y-%m-%d"), who, sum(n.values())),
                details={"kind": kind, "sale_date": sale.strftime("%Y-%m-%d"), "note": note[:500], "counts": dict(n)})
    db.commit()
    return {"sale_date": sale.strftime("%Y-%m-%d"), "counts": dict(n)}
