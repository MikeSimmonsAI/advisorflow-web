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
Foreclosure groups (``category``):
  A VERIFIED NOTICE - a notice filed with the county clerk schedules the sale
                      (document number, sale date, link to the record);
  B CLAIMED         - a list says a sale is scheduled; the county's clerk
                      notices are not read automatically, so not yet checked;
  C CONFLICT        - the clerk notice and the list disagree on the sale date
    POSTPONED       - a source says the sale was postponed;
  D CANCELED / RESOLVED / SALE PASSED - withdrawn, done, or the date is behind
                      us (outcome unknown until checked);
  E NO NOTICE FOUND - the clerk record was checked and holds no notice for this
                      address; NOT proof there is no foreclosure.
    NO EVIDENCE     - nothing claims a foreclosure and no clerk check ran.
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
FC_CHECK_EVENT = "cohort.foreclosure_checked"
CLERK_SOURCE = "tx_clerk_foreclosure"


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


def fc_check(db, org_id: str, props: List[EvoSenseProperty], *, user=None) -> Dict[str, Any]:
    """Look THESE properties up in the county clerks' filed foreclosure
    notices. Each match is ingested onto that property (never a new one); every
    property in a connected county gets a dated 'checked' event, matched or
    not. A county without a clerk source, or whose source failed, says so -
    its properties are not marked as checked."""
    from app.services.evosense import ingest as IN
    from app.services.evosense import providers as PV
    from app.services.evosense.sources import base as SB
    from app.services.evosense.sources.clerk_fc import COUNTIES, NO_PORTAL, ClerkForeclosureReader, notice_facts, to_record
    reader = ClerkForeclosureReader()
    groups: Dict[str, List[EvoSenseProperty]] = {}
    for p in props:
        groups.setdefault(_county(p) or "unknown", []).append(p)
    out: Dict[str, Any] = {}
    for county, ps in sorted(groups.items()):
        if county not in COUNTIES:
            out[county] = {"status": "NOT CONNECTED", "properties": len(ps),
                           "reason": NO_PORTAL.get(county, "No county clerk foreclosure source is connected for %s "
                                                          "County" % county.title())}
            continue
        targets = [{"id": p.id, "street_address": p.street_address, "zip_code": p.zip_code, "city": p.city}
                   for p in ps]
        try:
            res = reader.check(county, targets)
        except SB.SourceError as exc:
            db.rollback()
            out[county] = {"status": "FAILED", "properties": len(ps), "error": "%s: %s" % (exc.code, exc.message)}
            continue
        except Exception as exc:  # noqa: BLE001 - one county never stops the others
            db.rollback()
            out[county] = {"status": "FAILED", "properties": len(ps), "error": str(exc)[:200]}
            continue
        portal, ingested = res["portal"], 0
        for p in ps:
            hits = res["matches"].get(p.id, [])
            docs = []
            for doc, how in hits:
                f = notice_facts(portal, doc)
                docs.append({k: f[k] for k in ("document_number", "sale_date", "recorded", "category", "status", "url")})
                rec = to_record(portal, doc, target={"street_address": p.street_address, "city": p.city,
                                                     "zip_code": p.zip_code}, matched_by=how)
                if rec is None:
                    continue
                try:
                    IN.ingest(db, org_id, PV.PROVIDERS[CLERK_SOURCE], C.FORECLOSURE, rec, user=user,
                              is_test=p.is_test, target=p, target_trusted=True)
                    ingested += 1
                except Exception as exc:  # noqa: BLE001 - one notice never ends the check
                    C.log.warning("cohort foreclosure ingest (%s): %s", county, exc)
            C.log_event(db, org_id, FC_CHECK_EVENT, property_id=p.id, user=user,
                        actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM, is_test=p.is_test,
                        summary=("%s: %d foreclosure notice%s on file" % (portal.cfg["label"], len(docs),
                                                                           "" if len(docs) == 1 else "s"))
                        if docs else "%s: no foreclosure notice found for this address" % portal.cfg["label"],
                        details={"source": portal.cfg["label"], "county": county, "matched": len(docs),
                                 "documents": docs, "checked": C.now().strftime("%Y-%m-%d"),
                                 "search": "https://%s/" % portal.host})
        db.commit()
        out[county] = dict(res["stats"], ingested=ingested)
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
    try:
        fc = fc_check(db, org_id, props, user=user)
    except Exception as exc:  # noqa: BLE001 - the rest of the re-check still lands
        db.rollback()
        C.log.exception("cohort foreclosure check failed (%s)", kind)
        fc = {"all": {"status": "FAILED", "error": str(exc)[:200]}}
    for p in props:
        db.refresh(p)
        EV.rescore(db, p, EV.strategy_for(db, p))
    db.commit()
    C.log_event(db, org_id, "cohort.rechecked", user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM,
                summary="List %s re-checked against public records (%d properties)" % (kind, len(props)),
                details={"kind": kind, "lookups": counts.get("lookups"), "tax": tax, "foreclosure": fc,
                         "outcome": recheck_outcome(tax, fc)})
    db.commit()
    return {"properties": len(props), "lookups": counts.get("lookups"), "tax": tax, "foreclosure": fc,
            "outcome": recheck_outcome(tax, fc)}


def recheck_outcome(tax: Dict[str, Any], fc: Dict[str, Any]) -> str:
    """COMPLETED when every connected source answered; PARTIAL when one failed
    (the others still landed); FAILED when nothing did."""
    parts = list((tax or {}).values()) + [v for v in (fc or {}).values() if v.get("status") != "NOT CONNECTED"]
    bad = [v for v in parts if isinstance(v, dict) and (v.get("error") or v.get("status") == "FAILED")]
    if parts and len(bad) == len(parts):
        return "FAILED"
    return "PARTIAL" if bad else "COMPLETED"


# ── the view ────────────────────────────────────────────────────────────────

def _signals(db, prop) -> List[EvoSenseSignal]:
    return (db.query(EvoSenseSignal)
            .filter(EvoSenseSignal.organization_id == prop.organization_id,
                    EvoSenseSignal.property_id == prop.id, EvoSenseSignal.active.is_(True)).all())


CATEGORY = {"VERIFIED NOTICE": "A", "CLAIMED": "B", "CONFLICT": "C", "POSTPONED": "C", "CANCELED": "D",
            "RESOLVED": "D", "SALE PASSED": "D", "NO NOTICE FOUND": "E", "NO EVIDENCE": "E"}
CATEGORY_LABELS = {"A": "Verified scheduled foreclosure", "B": "Claimed - awaiting verification",
                   "C": "Conflicting or postponed", "D": "Canceled / resolved / sale date passed",
                   "E": "No notice found - not proof of no foreclosure"}


def _sd(v):
    from app.services.evosense import scoring as SC
    return SC._sale_date(v)


def foreclosure_status(sigs, today=None, check: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Mortgage / trustee / HOA / judicial foreclosure status of one property.

    `sigs`: its active signals. `check`: the latest clerk check for it
    ({"checked", "source", "matched", ...}) or None when its county's clerk
    notices are not read automatically (or no check has run yet)."""
    from datetime import date
    from app.services.evosense import scoring as SC
    today = today or date.today()
    fc = [x for x in sigs if x.signal_type in FORECLOSURE_TYPES]
    prov = {x.id: (C.jload(x.provenance, {}) or {}) for x in fc}

    def is_claim(x):
        return bool(prov[x.id].get("claim")) or x.source in SC.CLAIM_SOURCES

    claims = [x for x in fc if is_claim(x)]
    notices = [x for x in fc if not is_claim(x)]
    checked = (check or {}).get("checked")
    base = {"verified": False, "sale_date": None, "days": None, "claimed_sale_date": None, "notices": [],
            "document": None, "document_url": None, "foreclosure_type": None, "source": None,
            "evidence_reference": None, "evidence": None, "observed": None, "clerk_checked": checked,
            "reposted_for": [], "other_records": [],
            "clerk_source": (check or {}).get("source"), "confidence": 0,
            "last_checked": checked}
    # what a person recorded beside the notice that decides the status (a lawsuit, a
    # lis pendens, a list's source) - shown on the row, never counted as a sale
    base["other_records"] = [{"document": prov[x.id].get("document") or x.source_reference,
                              "url": prov[x.id].get("document_url"), "note": prov[x.id].get("note")}
                             for x in claims if prov[x.id].get("note")]
    claim_dates = sorted({d for d in (_sd(prov[x.id].get("sale_date")) for x in claims) if d})
    upcoming_claims = [d for d in claim_dates if d >= today]
    claim_sale = upcoming_claims[0] if upcoming_claims else (claim_dates[-1] if claim_dates else None)
    if claim_sale:
        base["claimed_sale_date"] = claim_sale.isoformat()

    def days(d):
        return (d - today).days if d else None

    def out(status, detail, **kw):
        r = dict(base)
        r.update(kw)
        r["status"], r["detail"], r["category"] = status, detail, CATEGORY[status]
        r["category_label"] = CATEGORY_LABELS[r["category"]]
        if r.get("sale_date") and r.get("days") is None:
            r["days"] = days(_sd(r["sale_date"]))
        return r

    if notices:
        from app.services.evosense.sources.clerk_fc import CATEGORY_LABELS as KIND
        rows_ = []
        for x in notices:
            pv = prov[x.id]
            rows_.append({"document": pv.get("document") or x.source_reference, "url": pv.get("document_url")
                          or pv.get("source_url"), "sale_date": pv.get("sale_date"), "recorded": pv.get("recorded"),
                          "type": KIND.get(pv.get("foreclosure_type") or "", pv.get("foreclosure_type")
                                           or "Foreclosure notice"),
                          "status": (x.case_status or "scheduled").lower(), "source": x.source,
                          "matched_by": pv.get("matched_by"), "evidence": pv.get("note"),
                          "observed": x.observed_at.strftime("%Y-%m-%d") if x.observed_at else None})
        rows_.sort(key=lambda r: r["sale_date"] or "")
        base["notices"] = rows_
        live = [r for r in rows_ if r["status"] == "scheduled" and _sd(r["sale_date"]) and _sd(r["sale_date"]) >= today]
        cancelled = [r for r in rows_ if r["status"] in ("canceled", "cancelled", "withdrawn")]
        postponed = [r for r in rows_ if r["status"] == "postponed"]
        resolved = [r for r in rows_ if r["status"] in ("resolved", "sold")]
        pick = live[0] if live else rows_[-1]
        kw = {"verified": True, "sale_date": pick["sale_date"], "document": pick["document"],
              "document_url": pick["url"], "foreclosure_type": pick["type"], "source": pick["source"],
              "evidence_reference": pick["document"], "evidence": pick.get("evidence"), "observed": pick["observed"],
              "confidence": 92 if (pick.get("matched_by") or "").startswith("clerk index") else 85}
        if live:
            dates = sorted({r["sale_date"] for r in live})
            if claim_sale and claim_sale >= today and claim_sale.isoformat() not in dates:
                return out("CONFLICT", "The list says the sale is %s; the %s notice %s schedules it for %s. "
                                       "Open the notice to see which sale is live." % (
                                           claim_sale.isoformat(), base["clerk_source"] or "county clerk",
                                           pick["document"], pick["sale_date"]), **dict(kw, confidence=70))
            extra = ("; also posted for %s (a re-posting - the earlier sale may be postponed)" %
                     ", ".join(dates[1:])) if len(dates) > 1 else ""
            kw["reposted_for"] = dates[1:]
            return out("VERIFIED NOTICE", "%s scheduled %s - notice %s%s%s" % (
                pick["type"], pick["sale_date"], pick["document"],
                " filed %s" % pick["recorded"] if pick["recorded"] else "", extra), **kw)
        if cancelled:
            c = cancelled[-1]
            return out("CANCELED", "Notice %s withdrawn / cancelled" % c["document"], **dict(kw, document=c["document"]))
        if resolved:
            return out("RESOLVED", "Sale reported done / resolved (%s)" % resolved[-1]["document"], **kw)
        if postponed:
            return out("POSTPONED", "Sale reported postponed (%s)" % postponed[-1]["document"], **kw)
        if claim_sale and claim_sale >= today:
            return out("CONFLICT", "The list says the sale is %s, but the clerk record only shows notice %s for %s "
                                   "(passed). No notice found for the list's date." % (
                                       claim_sale.isoformat(), pick["document"], pick["sale_date"]),
                       **dict(kw, verified=False, sale_date=claim_sale.isoformat(), confidence=40))
        return out("SALE PASSED", "Clerk notice %s scheduled the sale for %s - that date has passed; sold, "
                                  "cancelled or postponed is unknown until checked" % (pick["document"], pick["sale_date"]),
                   **kw)

    if claims:
        newest = max(claims, key=lambda x: x.observed_at or C.now())
        cs = (newest.case_status or "").upper()
        kw = {"sale_date": claim_sale.isoformat() if claim_sale else None, "source": newest.source,
              "evidence_reference": prov[newest.id].get("record") or newest.source_reference,
              "observed": newest.observed_at.strftime("%Y-%m-%d") if newest.observed_at else None,
              "confidence": 50}
        if cs in ("POSTPONED", "CANCELED", "CANCELLED", "RESOLVED", "SOLD"):
            word = {"CANCELLED": "CANCELED", "SOLD": "RESOLVED"}.get(cs, cs)
            return out(word, "The list reports the sale %s" % word.lower(), **kw)
        if claim_sale and claim_sale < today:
            return out("SALE PASSED", "The list's sale date %s has passed - outcome unknown until checked"
                       % claim_sale.isoformat(), **kw)
        if checked:
            return out("NO NOTICE FOUND", "Claimed by the list for %s, but the %s holds no foreclosure notice for "
                                          "this address (checked %s). Not proof there is none: the notice may be "
                                          "unindexed, filed late, or the sale may have been cancelled."
                       % (kw["sale_date"] or "an unstated date", base["clerk_source"], checked), **dict(kw, confidence=25))
        return out("CLAIMED", "%s - not yet checked against a county clerk notice" % (newest.normalized_value
                                                                                       or newest.raw_value or "Claimed"),
                   **kw)
    if checked:
        return out("NO NOTICE FOUND", "No foreclosure claim, and the %s holds no notice for this address (checked "
                                      "%s). Not proof there is none." % (base["clerk_source"], checked))
    return out("NO EVIDENCE", "No foreclosure claim or notice on file, and no county clerk check has run for this "
                              "address. Not proof there is none.")


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
    """A: a sale scheduled on a VERIFIED clerk notice within IMMINENT_DAYS, a
    tax sale or a tax lawsuit. B: any other open foreclosure lead (verified
    further out, claimed, conflicting, postponed, claimed-but-not-found),
    delinquent taxes or a score of 45+. C: other current distress, or a sale
    that passed / was cancelled. A claim alone is never "act now"; taxes being
    current never lowers a foreclosure."""
    if owner["status"] != "VERIFIED":
        return "REVIEW"
    imminent = fc.get("days") is not None and 0 <= fc["days"] <= IMMINENT_DAYS and fc["status"] == "VERIFIED NOTICE"
    if imminent or tax["status"] in ("TAX SALE", "TAX LAWSUIT"):
        return "A"
    open_lead = fc["status"] in ("VERIFIED NOTICE", "CLAIMED", "CONFLICT", "POSTPONED") or \
        (fc["status"] == "NO NOTICE FOUND" and fc.get("claimed_sale_date"))
    if open_lead or tax["status"] == "DELINQUENT" or (score or 0) >= 45:
        return "B"
    if distress or fc["status"] in ("SALE PASSED", "CANCELED"):
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
    st = fc.get("status")
    if st == "VERIFIED NOTICE" and fc.get("days") is not None and fc["days"] >= 0:
        return ("Sale confirmed on clerk notice %s for %s (%d days). Reach %s now; recheck the clerk record the "
                "week of the sale for a cancellation or postponement" % (fc.get("document"), fc["sale_date"],
                                                                         fc["days"], owner["name"]))
    if st == "CONFLICT":
        return "Dates disagree - open clerk notice %s and confirm which sale is live, then reach %s" % (
            fc.get("document"), owner["name"])
    if st == "NO NOTICE FOUND" and fc.get("claimed_sale_date"):
        return ("No clerk notice found for the claimed %s sale (checked %s). Look up the address on the %s "
                "County clerk search for that month, or get the notice's document number from the list's source"
                % (fc["claimed_sale_date"], fc.get("clerk_checked"), fc.get("county") or ""))
    if st == "CLAIMED" and fc.get("days") is not None:
        return ("Confirm the %s sale on the %s County clerk's posted notice (%d days left; that county's notices "
                "are not read automatically) and record the document number, then reach %s"
                % (fc["sale_date"], fc.get("county") or "", fc["days"], owner["name"]))
    if st == "CANCELED":
        return "Notice withdrawn - ask %s whether the loan was reinstated; keep only if other distress" % owner["name"]
    if st == "SALE PASSED":
        return "Check whether the %s sale happened, was cancelled or postponed" % fc.get("sale_date")
    if prio == "PARK":
        return "Park - no current distress on record; recheck monthly"
    if not (contact["phones"] or contact["emails"]):
        return "Get contact details for %s (free sources exhausted; paid lookup is off)" % owner["name"]
    return "Call or mail %s about the property" % owner["name"]


def _prefetch(db, org_id: str, props: List[EvoSenseProperty]) -> Dict[str, Any]:
    """Everything the list view needs for ALL its properties, in about a
    dozen queries instead of ~15 per property (the Top 100 took 4-8 s)."""
    from app.models.evosense_models import EvoSenseControl, EvoSenseOwner, EvoSenseStrategy
    ids = [p.id for p in props]
    pre: Dict[str, Any] = {"signals": {}, "links": {}, "owners": {}, "tax_event": {}, "contacts": {},
                           "claims": {}, "strategies": {}}
    if not ids:
        return pre
    for sig in (db.query(EvoSenseSignal).filter(EvoSenseSignal.organization_id == org_id,
                                                EvoSenseSignal.property_id.in_(ids)).all()):
        pre["signals"].setdefault(sig.property_id, []).append(sig)
    links = (db.query(EvoSenseOwnership).filter(EvoSenseOwnership.organization_id == org_id,
                                                EvoSenseOwnership.property_id.in_(ids),
                                                EvoSenseOwnership.is_current.is_(True)).all())
    for l in links:
        pre["links"].setdefault(l.property_id, []).append(l)
    owner_ids = sorted({l.owner_id for l in links if l.owner_id})
    if owner_ids:
        for o in db.query(EvoSenseOwner).filter(EvoSenseOwner.organization_id == org_id,
                                                EvoSenseOwner.id.in_(owner_ids)).all():
            pre["owners"][o.id] = o
        for cp in (db.query(EvoSenseContactPoint).filter(EvoSenseContactPoint.owner_id.in_(owner_ids),
                                                         EvoSenseContactPoint.status == "active").all()):
            pre["contacts"].setdefault(cp.owner_id, []).append(cp)
    for ev in (db.query(EvoSenseEvent).filter(EvoSenseEvent.organization_id == org_id,
                                              EvoSenseEvent.action == TAX_CHECK_EVENT,
                                              EvoSenseEvent.property_id.in_(ids))
               .order_by(EvoSenseEvent.created_at.asc()).all()):
        pre["tax_event"][ev.property_id] = ev                 # ascending: the newest wins
    for pid, payload in (db.query(EvoSenseObservation.property_id, EvoSenseObservation.payload)
                         .filter(EvoSenseObservation.organization_id == org_id,
                                 EvoSenseObservation.property_id.in_(ids),
                                 EvoSenseObservation.provider_key == "csv_import").all()):
        pre["claims"].setdefault(pid, []).append(payload)
    sids = sorted({p.best_strategy_id or p.first_strategy_id for p in props if (p.best_strategy_id or p.first_strategy_id)})
    if sids:
        for st in db.query(EvoSenseStrategy).filter(EvoSenseStrategy.organization_id == org_id,
                                                    EvoSenseStrategy.id.in_(sids)).all():
            pre["strategies"][st.id] = st
    ctl = db.query(EvoSenseControl).filter(EvoSenseControl.organization_id == org_id).first()
    pre["weights"] = C.jload(getattr(ctl, "score_weights", None), None) if ctl is not None else None
    pre["options"] = C.jload(getattr(ctl, "scoring_options", None), None) if ctl is not None else None
    return pre


def _owners_of(pre, prop) -> List[Any]:
    links = pre["links"].get(prop.id, [])
    owners = [pre["owners"][l.owner_id] for l in links if l.owner_id in pre["owners"]]
    disputed = {l.owner_id for l in links if l.in_conflict}
    return sorted(owners, key=lambda o: (o.id in disputed, o.created_at or C.now()))


def _owner_status_pre(pre, prop) -> Dict[str, Any]:
    links = pre["links"].get(prop.id, [])
    owners = _owners_of(pre, prop)
    if not owners:
        return {"status": "NO OWNER", "name": None, "sources": []}
    o = owners[0]
    srcs = sorted({l.source for l in links if l.owner_id == o.id})
    official = [x for x in srcs if x in OFFICIAL_OWNER_SOURCES]
    status = "CONFLICT" if len(owners) > 1 or any(l.in_conflict for l in links) else \
        "VERIFIED" if official else "UNVERIFIED"
    from app.services.evosense.ingest import REVIEW_FLAG_LABELS, owner_flags
    flags = owner_flags(o.display_name or "")
    return {"status": status, "name": o.display_name, "owner_id": o.id, "sources": srcs,
            "flags": [{"key": f, "label": REVIEW_FLAG_LABELS.get(f, f)} for f in flags],
            "mailing": ", ".join(x for x in (o.mailing_street, o.mailing_city, o.mailing_state, o.mailing_zip) if x),
            "other_names": [x.display_name for x in owners[1:]]}


def _tax_status_pre(pre, prop, sigs) -> Dict[str, Any]:
    t = [x for x in sigs if x.signal_type in TAX_TYPES]
    if t:
        kinds = sorted({x.signal_type for x in t})
        best = "TAX SALE" if "TAX_SALE" in kinds else "TAX LAWSUIT" if ("TAX_SUIT" in kinds or "TAX_FORECLOSURE" in kinds) \
            else "DELINQUENT"
        x = max(t, key=lambda y: y.observed_at or C.now())
        return {"status": best, "detail": x.normalized_value or x.raw_value, "source": x.source,
                "date": x.observed_at.strftime("%Y-%m-%d") if x.observed_at else None}
    ev = pre["tax_event"].get(prop.id)
    if ev is not None:
        d = C.jload(ev.details, {}) or {}
        return {"status": "CURRENT", "detail": ev.summary, "source": d.get("source"),
                "date": d.get("file_date") or ev.created_at.strftime("%Y-%m-%d")}
    return {"status": "NOT CHECKED", "detail": "No county delinquency file connected for %s County"
            % (prop.county or "this")}


def _contacts_pre(pre, owner_id) -> Dict[str, Any]:
    c = Counter()
    for cp in pre["contacts"].get(owner_id, []) if owner_id else []:
        c["phones" if cp.kind == "phone" else "emails" if cp.kind == "email" else "other"] += 1
        if cp.verified_at or (cp.validation or "") == "valid":
            c["verified"] += 1
    return {"phones": c["phones"], "emails": c["emails"], "verified": c["verified"]}


def _list_claims_pre(pre, prop) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for payload in pre["claims"].get(prop.id, []):
        p = C.jload(payload, {}) or {}
        out.update(((p.get("evidence") or {}).get("list_claims")) or {})
        v = p.get("valuation") or {}
        if v.get("value"):
            out.setdefault("list_value", v.get("value"))
        if v.get("mortgage") is not None:
            out.setdefault("list_unpaid_balance", v.get("mortgage"))
    return out


def rows(db, org_id: str, kind: str) -> List[Dict[str, Any]]:
    from app.services.evosense import scoring as SC
    from app.services.evosense import signals as SIG
    out = []
    ms = members(db, org_id, kind)
    checks = clerk_checks(db, org_id, [p.id for p in ms])
    pre = _prefetch(db, org_id, ms)
    clerk_counties = _CLERK_COUNTIES()
    now = C.now()
    for p in ms:
        all_sigs = pre["signals"].get(p.id, [])
        sigs = [x for x in all_sigs if x.active]
        owner = _owner_status_pre(pre, p)
        tax = _tax_status_pre(pre, p, sigs)
        fc = foreclosure_status(sigs, check=checks.get(p.id))
        contact = _contacts_pre(pre, owner.get("owner_id"))
        distress = sorted({x.signal_type for x in sigs if x.signal_type not in ("LONG_OWNERSHIP",)})
        sid = p.best_strategy_id or p.first_strategy_id
        strategy = pre["strategies"].get(sid) if sid else None
        owners = _owners_of(pre, p)
        po = SC.property_opportunity(p, SIG.stack(all_sigs, now), strategy, weights=pre["weights"],
                                     owner=owners[0] if owners else None, options=pre["options"])
        earned = [f for f in po.get("factors") or [] if (f.get("points") or 0) > 0]
        held = [f["label"] for f in po.get("factors") or [] if (f.get("points") or 0) <= 0][:4]
        fc["county"] = p.county
        missing = []
        if tax["status"] == "NOT CHECKED":
            missing.append("tax status (no county file)")
        if fc["status"] in ("CLAIMED", "NO EVIDENCE") and not fc.get("clerk_checked"):
            missing.append("clerk foreclosure-notice check (%s County notices not read automatically)"
                           % (p.county or "this") if _county(p) not in clerk_counties else
                           "clerk foreclosure-notice check (run Re-check)")
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
            "list_claims": _list_claims_pre(pre, p),
        })
    rank = {"A": 0, "B": 1, "C": 2, "REVIEW": 3, "PARK": 4}
    out.sort(key=lambda r: (rank[r["priority"]], -(r["score"] or 0),
                            r["foreclosure"]["days"] if r["foreclosure"]["days"] is not None else 9999,
                            r["street_address"] or ""))
    return out


def _CLERK_COUNTIES():
    from app.services.evosense.sources.clerk_fc import COUNTIES
    return set(COUNTIES)


def clerk_checks(db, org_id: str, prop_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """The latest clerk foreclosure check per property."""
    if not prop_ids:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for ev in (db.query(EvoSenseEvent)
               .filter(EvoSenseEvent.organization_id == org_id, EvoSenseEvent.action == FC_CHECK_EVENT,
                       EvoSenseEvent.property_id.in_(prop_ids))
               .order_by(EvoSenseEvent.created_at.asc()).all()):
        out[ev.property_id] = C.jload(ev.details, {}) or {}
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
    cats = Counter(f.get("category") or "E" for f in fcs)
    groups = {k: {"label": CATEGORY_LABELS[k], "count": cats.get(k, 0)} for k in "ABCDE"}
    return {"groups": groups, "sale_dates": sale_dates, "score_buckets": dict(sorted(buckets.items(), key=lambda kv: int(kv[0].split("-")[0]))),
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
    # Stored times are UTC without a zone; say so ("Z"), or a browser shows
    # 9:03 PM Central as "2:03 AM" (read live 2026-10-24).
    return {"at": _utc_iso(ev.created_at), "details": C.jload(ev.details, {})}


def _utc_iso(dt) -> Optional[str]:
    if dt is None:
        return None
    s = dt.isoformat()
    return s if (dt.tzinfo is not None or s.endswith("Z")) else s + "Z"


RECHECK_STARTED = "cohort.recheck_started"


def running(org_id: str, kind: str, db=None) -> bool:
    """True while a re-check of this list is under way - in this process, or
    (with `db`) started by any server process within the hour and not yet
    finished. A crashed run stops counting as running after the hour."""
    if ("%s:%s" % (org_id, kind)) in _RUNNING:
        return True
    if db is None:
        return False
    from datetime import timedelta
    started = (db.query(EvoSenseEvent)
               .filter(EvoSenseEvent.organization_id == org_id, EvoSenseEvent.action == RECHECK_STARTED,
                       EvoSenseEvent.summary.like("List %s %%" % kind))
               .order_by(EvoSenseEvent.created_at.desc()).first())
    if started is None or started.created_at is None or C.now() - started.created_at > timedelta(hours=1):
        return False
    done = (db.query(EvoSenseEvent)
            .filter(EvoSenseEvent.organization_id == org_id, EvoSenseEvent.action == "cohort.rechecked",
                    EvoSenseEvent.summary.like("List %s %%" % kind))
            .order_by(EvoSenseEvent.created_at.desc()).first())
    return not (done is not None and done.created_at is not None and done.created_at >= started.created_at)


def mark_started(db, org_id: str, kind: str, user=None):
    C.log_event(db, org_id, RECHECK_STARTED, user=user, actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM,
                summary="List %s re-check started" % kind, details={"kind": kind})
    db.commit()


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
