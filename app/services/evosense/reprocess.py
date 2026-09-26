"""Re-derive an organization's EvoSense records from their PRESERVED raw evidence.

    dry_run    snapshot the current derived state, re-derive every property
               under the current rules (derive/v3, property_opportunity/v3)
               WITHOUT writing any derived record, and store the complete
               before/after diff on an EvoSenseReprocessRun. Nothing else
               changes: no property, owner, signal or score row is touched.
    apply      write exactly what the dry run showed. Old signals are
               DEACTIVATED with a reason, never deleted; old scores stay as
               history; raw observations are never modified.
    rollback   restore the run's snapshot: columns back, signals re-activated
               / de-activated, the previous scores current again.

TENANT SCOPE. Every function takes one organization id and reads and writes
only that organization's rows. A run belongs to its organization; another
tenant cannot see, apply or roll it back.

WHY THE RE-DERIVATION IS PURE. It reads the raw payload each observation kept
(a DCAD CSV row set, a tax-roll fixed-width line, a city API answer), runs the
same adapter code the engine now runs, the same `signals.derive_specs`, and
the same scoring functions - but on in-memory copies. So the dry run shows
exactly what the engine will write, and computing it cannot change anything.
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from app.models.evosense_models import (EvoSenseObservation, EvoSenseOwner, EvoSenseOwnership,
                                        EvoSenseProperty, EvoSenseReprocessRun, EvoSenseScore,
                                        EvoSenseSignal)
from app.services.evosense import common as C
from app.services.evosense import contacts as CT
from app.services.evosense import economics as ECO
from app.services.evosense import scoring as SC
from app.services.evosense import signals as SIG
from app.services.evosense import valuation as VAL
from app.services.evosense.ingest import (DERIVATION_VERSION, INSTITUTIONAL, OWNER_TYPE_LABELS,
                                          owner_flags, owner_type_of)

RULE_VERSION = "%s + %s" % (DERIVATION_VERSION, SC.PO_VERSION)

PROP_COLS = ("street_address", "unit", "city", "state", "zip_code", "county", "parcel_apn", "property_type",
             "bedrooms", "bathrooms", "half_bathrooms", "square_feet", "year_built", "situs_city_basis",
             "estimated_value", "estimated_value_source", "estimated_value_at",
             "appraisal_value", "appraisal_land_value", "appraisal_improvement_value", "appraisal_year",
             "appraisal_source", "appraisal_at", "mortgage_balance", "mortgage_source", "equity_pct",
             "equity_basis", "last_sale_date", "last_deed_transfer_date", "ownership_years", "occupancy",
             "occupancy_source", "opportunity_score", "data_confidence", "signal_count", "status",
             "next_action", "next_action_detail", "blocked_reason", "derivation_version")
OWNER_COLS = ("owner_type", "name_truncated", "review_flags", "resolution")
SIG_ATTRS = ("id", "signal_type", "source", "connector_kind", "source_reference", "observation_id",
             "observed_at", "effective_at", "stale_at", "confidence", "strength", "raw_value",
             "normalized_value", "provenance", "cost_cents", "active", "evidence_basis", "case_status",
             "rule_version")


# ── (de)serialising the derived state ───────────────────────────────────────

def _j(v):
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "is_finite"):          # Decimal
        return float(v)
    return v


def _parse_dt(v):
    if v is None or isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


def snapshot_property(db, prop) -> Dict[str, Any]:
    owners = []
    for o in CT.current_owners(db, prop):
        owners.append({"id": o.id, "display_name": o.display_name, **{c: _j(getattr(o, c, None)) for c in OWNER_COLS}})
    sigs = (db.query(EvoSenseSignal)
            .filter(EvoSenseSignal.organization_id == prop.organization_id,
                    EvoSenseSignal.property_id == prop.id, EvoSenseSignal.active.is_(True)).all())
    scores = (db.query(EvoSenseScore)
              .filter(EvoSenseScore.organization_id == prop.organization_id,
                      EvoSenseScore.property_id == prop.id, EvoSenseScore.is_current.is_(True)).all())
    return {"id": prop.id, "columns": {c: _j(getattr(prop, c, None)) for c in PROP_COLS},
            "owners": owners,
            "active_signal_ids": [s.id for s in sigs],
            "signals": [{"id": s.id, "type": s.signal_type, "source": s.source, "ref": s.source_reference,
                         "value": s.normalized_value} for s in sigs],
            "current_score_ids": [s.id for s in scores],
            "scores": {s.score_type: {"value": s.value, "label": s.label, "version": s.version}
                       for s in scores if s.subject_type == "property"}}


# ── rebuilding each observation's record from its raw evidence ──────────────

def _target(prop) -> Dict[str, Any]:
    return {"id": prop.id, "street_address": prop.street_address, "unit": prop.unit, "city": prop.city,
            "state": prop.state, "zip_code": prop.zip_code, "county": prop.county, "parcel_apn": prop.parcel_apn}


def _legacy_payload(obs) -> Dict[str, Any]:
    rec = C.jload(obs.payload, {}) or {}
    rec = dict(rec)
    if "last_sale_date" in rec and "deed_transfer_date" not in rec:
        rec["deed_transfer_date"] = rec.pop("last_sale_date")
    return rec


def rebuild_record(obs, prop) -> Tuple[Optional[Dict[str, Any]], str]:
    """(record, how). `how` says whether the record came from the preserved
    RAW evidence under the current adapter, or from the stored normalized
    payload (sources with nothing to re-read, or no raw kept)."""
    raw = obs.raw_payload
    key = obs.provider_key
    try:
        if key == "dcad" and raw:
            from app.services.evosense.sources.dallas import DcadReader
            ev = (C.jload(obs.payload, {}) or {}).get("evidence") or {}
            return DcadReader().record_from_raw(json.loads(raw), homestead=bool(ev.get("homestead"))), "raw"
        if key == "tarrant_tax_roll" and raw:
            from app.services.evosense.sources.tarrant import TarrantTaxRollReader
            return TarrantTaxRollReader().record_from_raw(raw, obs.source_url or "", obs.source_updated_at), "raw"
        if key == "fw_code_violations" and raw:
            from app.services.evosense.sources.fortworth import FortWorthCodeReader
            return FortWorthCodeReader().to_record(_target(prop), json.loads(raw)), "raw"
        if key == "dallas_311_code" and raw:
            from app.services.evosense.sources.dallas import Dallas311Reader
            return Dallas311Reader().to_record(_target(prop), json.loads(raw)), "raw"
    except Exception as exc:  # noqa: BLE001 - a record that cannot be re-read keeps what it said
        return _legacy_payload(obs), "stored payload (raw could not be re-read: %s)" % type(exc).__name__
    return _legacy_payload(obs), "stored payload"


# ── the pure re-derivation ──────────────────────────────────────────────────

def _ns_signal(**kw):
    base = {a: None for a in SIG_ATTRS}
    base.update(kw)
    base.setdefault("cost_cents", 0)
    base["active"] = True
    return SimpleNamespace(**base)


def rederive(db, prop, *, strategy=None, weights=None, options=None, when=None) -> Dict[str, Any]:
    """The property as the current rules derive it from its preserved evidence.
    Reads only; returns in-memory results."""
    from app.services.evosense import evaluate as EV
    when = when or C.now()
    obs = (db.query(EvoSenseObservation)
           .filter(EvoSenseObservation.organization_id == prop.organization_id,
                   EvoSenseObservation.property_id == prop.id)
           .order_by(EvoSenseObservation.observed_at.asc()).all())
    v = SimpleNamespace(**{c: getattr(prop, c, None) for c in PROP_COLS})
    v.id, v.organization_id, v.has_conflicts, v.is_test = prop.id, prop.organization_id, prop.has_conflicts, prop.is_test
    # an appraisal tax value parked in the market-estimate columns moves out
    if v.estimated_value is not None and VAL.is_appraisal_basis(v.estimated_value_source):
        legacy = VAL.view(prop)["appraisal"]
        v.appraisal_value = v.appraisal_value or (legacy or {}).get("value")
        v.appraisal_year = v.appraisal_year or (legacy or {}).get("year")
        v.appraisal_source = v.appraisal_source or v.estimated_value_source
        v.estimated_value = v.estimated_value_source = v.estimated_value_at = None
        v.equity_pct = v.equity_basis = None
    notes, how_by_obs = [], {}
    truncated = False
    new_sigs: List[SimpleNamespace] = []
    existing = {(s.signal_type, s.source, s.source_reference): s for s in
                db.query(EvoSenseSignal).filter(EvoSenseSignal.organization_id == prop.organization_id,
                                                EvoSenseSignal.property_id == prop.id).all()}
    for o in obs:
        rec, how = rebuild_record(o, prop)
        how_by_obs[o.id] = {"provider": o.provider_key, "how": how, "adapter": (rec or {}).get("_adapter_version")}
        if not rec:
            continue
        for f in ("city", "zip_code"):
            if rec.get(f) and not getattr(v, f):
                setattr(v, f, rec[f])
                if f == "city":
                    v.situs_city_basis = ((rec.get("_evidence") or {}).get("city_basis")
                                          or "%s situs record" % o.provider_key)
        for f in ("bedrooms", "bathrooms", "half_bathrooms", "square_feet", "year_built", "property_type"):
            if rec.get(f) is not None and getattr(v, f) in (None, ""):
                setattr(v, f, rec[f])
        appr = rec.get("appraisal") or {}
        if appr.get("value") is not None:
            v.appraisal_value = int(appr["value"])
            v.appraisal_land_value = appr.get("land")
            v.appraisal_improvement_value = appr.get("improvements")
            v.appraisal_year = appr.get("year")
            v.appraisal_source = "%s (%s)" % (o.provider_key, appr.get("basis") or "appraisal district tax value")
        deed = rec.get("deed_transfer_date")
        if deed:
            d = datetime.strptime(str(deed)[:10], "%Y-%m-%d")
            v.last_deed_transfer_date = d.date()
            v.ownership_years = max(0, int((when - d).days // 365.25))
        if rec.get("occupancy"):
            v.occupancy, v.occupancy_source = rec["occupancy"], o.provider_key
        if (rec.get("owner") or {}).get("name_truncated"):
            truncated = True
        for sg in rec.get("signals") or []:
            ref = sg.get("ref") or rec.get("source_reference")
            prior = existing.get((sg["type"], o.provider_key, ref))
            eff = sg.get("effective_at")
            new_sigs.append(_ns_signal(
                id=prior.id if prior is not None else "new:%s:%s:%s" % (sg["type"], o.provider_key, ref),
                signal_type=sg["type"], source=o.provider_key, connector_kind=o.connector_kind,
                source_reference=ref, observation_id=o.id,
                observed_at=prior.observed_at if prior is not None else when,
                effective_at=datetime.strptime(eff[:10], "%Y-%m-%d") if isinstance(eff, str) and eff else None,
                confidence=sg.get("confidence"), raw_value=sg.get("raw"), normalized_value=sg.get("value"),
                provenance=C.jdump({"provider": o.provider_key, "record": rec.get("source_reference"),
                                    "observation": o.id, "adapter_version": rec.get("_adapter_version"),
                                    "source_url": rec.get("_source_url")}),
                evidence_basis=sg.get("evidence_basis"), case_status=sg.get("case_status"),
                rule_version=rec.get("_adapter_version")))
    # evidence no source record states (an operator's flag, a manual signal) is kept as it is
    record_sources = {o.provider_key for o in obs}
    for s in existing.values():
        if s.active and s.source != SIG.DERIVED_SOURCE and s.source not in record_sources:
            new_sigs.append(_ns_signal(**{a: getattr(s, a, None) for a in SIG_ATTRS if a != "active"}))

    owner = CT.primary_owner(db, prop)
    vo, owner_after, owner_source = None, None, None
    if owner is not None:
        otype = owner_type_of(owner.display_name or "")
        flags = owner_flags(owner.display_name or "", truncated=truncated or bool(owner.name_truncated))
        vo = SimpleNamespace(id=owner.id, display_name=owner.display_name, owner_type=otype,
                             mailing_street=owner.mailing_street, mailing_city=owner.mailing_city,
                             mailing_state=owner.mailing_state, mailing_zip=owner.mailing_zip,
                             name_truncated=truncated or bool(owner.name_truncated), review_flags=C.jdump(flags))
        owner_after = {"id": owner.id, "display_name": owner.display_name, "owner_type": otype,
                       "owner_type_label": OWNER_TYPE_LABELS.get(otype, otype),
                       "name_truncated": vo.name_truncated, "review_flags": flags}
        link = (db.query(EvoSenseOwnership)
                .filter(EvoSenseOwnership.organization_id == prop.organization_id,
                        EvoSenseOwnership.property_id == prop.id, EvoSenseOwnership.owner_id == owner.id,
                        EvoSenseOwnership.is_current.is_(True)).first())
        owner_source = link.source if link is not None else None

    spec = SIG.derive_specs(v, vo, owner_source=owner_source, when=when)
    for stype, m in spec["make"].items():
        prior = existing.get((stype, SIG.DERIVED_SOURCE, None))
        same_value = prior is not None and prior.active and prior.normalized_value == m["value"]
        new_sigs.append(_ns_signal(
            id=prior.id if prior is not None else "new:%s:derived" % stype, signal_type=stype,
            source=SIG.DERIVED_SOURCE, observed_at=prior.observed_at if same_value else when,
            effective_at=m.get("effective_at"), confidence=m["confidence"], normalized_value=m["value"],
            provenance=C.jdump(m["provenance"]), evidence_basis=m.get("evidence_basis"),
            rule_version=SIG.DERIVED_RULE_VERSION))

    stacked = SIG.stack(new_sigs, when)
    strategy = strategy or EV.strategy_for(db, prop)
    po = SC.property_opportunity(v, stacked, strategy, weights=weights, owner=vo, options=options)
    dc = SC.data_confidence(v, stacked, owner_known=owner is not None)
    threshold = getattr(strategy, "min_opportunity_score", 60) if strategy else 60
    score_driven = (C.S_NEW, C.S_LOW, C.S_HIGH, C.S_WAITING_DATA, C.S_NEEDS_ENRICHMENT, C.S_BUDGET_BLOCKED)
    status_after = prop.status
    if prop.status in score_driven:
        if po["value"] is None:
            status_after = C.S_NEW
        elif po["value"] < threshold:
            status_after = C.S_LOW
        elif prop.status == C.S_LOW:
            status_after = C.S_HIGH
    v.opportunity_score, v.data_confidence = po["value"], dc["label"]
    v.derivation_version = DERIVATION_VERSION
    arv = VAL.arv(db, v)
    return {"property": v, "owner": owner_after, "signals": new_sigs, "stacked": stacked,
            "opportunity": po, "data_confidence": dc, "status_after": status_after, "threshold": threshold,
            "arv": arv, "observations": how_by_obs, "unknown": spec["unknown"], "notes": notes}


# ── the diff ────────────────────────────────────────────────────────────────

def _money(v):
    return None if v is None else int(v)


def _econ_before(db, prop) -> Dict[str, Any]:
    """What the property page showed BEFORE: the pre-change engine used the
    stored estimated_value (a DCAD tax value on DFW rows) as ARV."""
    ev = prop.estimated_value
    return {"value_label_before": "Est. value (actually %s)" % ("an appraisal district tax value"
                                                                if VAL.is_appraisal_basis(prop.estimated_value_source)
                                                                else "a market estimate") if ev else None,
            "value_before": _money(ev), "arv_before": _money(ev),
            "arv_basis_before": prop.estimated_value_source if ev else None}


def diff_property(db, prop, after: Dict[str, Any]) -> Dict[str, Any]:
    before_sigs = {(s.signal_type, s.source, s.source_reference): s for s in
                   db.query(EvoSenseSignal).filter(EvoSenseSignal.organization_id == prop.organization_id,
                                                   EvoSenseSignal.property_id == prop.id,
                                                   EvoSenseSignal.active.is_(True)).all()}
    after_sigs = {(s.signal_type, s.source, s.source_reference): s for s in after["signals"]}
    removed = [{"type": k[0], "source": k[1], "value": s.normalized_value,
                "why": _removal_reason(k[0], after)} for k, s in before_sigs.items() if k not in after_sigs]
    added = [{"type": k[0], "source": k[1], "value": s.normalized_value,
              "evidence_date": s.effective_at.strftime("%Y-%m-%d") if s.effective_at else None,
              "evidence_basis": s.evidence_basis, "points": SIG.CATALOG.get(k[0], {}).get("points")}
             for k, s in after_sigs.items() if k not in before_sigs]
    changed = [{"type": k[0], "source": k[1], "before": before_sigs[k].normalized_value, "after": s.normalized_value}
               for k, s in after_sigs.items() if k in before_sigs and before_sigs[k].normalized_value != s.normalized_value]
    cur_po = (db.query(EvoSenseScore)
              .filter(EvoSenseScore.organization_id == prop.organization_id, EvoSenseScore.property_id == prop.id,
                      EvoSenseScore.score_type == "property_opportunity", EvoSenseScore.is_current.is_(True)).first())
    v = after["property"]
    owner_before = None
    o = CT.primary_owner(db, prop)
    if o is not None:
        owner_before = {"owner_type": o.owner_type, "name_truncated": bool(getattr(o, "name_truncated", False)),
                        "review_flags": C.jload(getattr(o, "review_flags", None), []) or []}
    fields = {}
    for c in ("city", "zip_code", "situs_city_basis", "bathrooms", "half_bathrooms", "occupancy",
              "ownership_years"):
        b, a = _j(getattr(prop, c, None)), _j(getattr(v, c, None))
        if b != a:
            fields[c] = {"before": b, "after": a}
    deed_before = prop.last_sale_date.date().isoformat() if getattr(prop, "last_sale_date", None) else None
    appraisal_after = {"value": v.appraisal_value, "year": v.appraisal_year, "land": v.appraisal_land_value,
                       "improvements": v.appraisal_improvement_value, "source": v.appraisal_source} \
        if v.appraisal_value is not None else None
    threshold = after["threshold"]
    before_score = prop.opportunity_score
    after_score = after["opportunity"]["value"]
    return {
        "property_id": prop.id, "address": prop.street_address,
        "place": ", ".join(x for x in (v.city, v.state, v.zip_code) if x), "county": prop.county,
        "score": {"before": before_score, "after": after_score,
                  "version_before": cur_po.version if cur_po else None,
                  "version_after": after["opportunity"]["version"],
                  "threshold": threshold,
                  "crossed": ("fell below threshold" if (before_score or 0) >= threshold > (after_score or 0)
                              else "rose above threshold" if (after_score or 0) >= threshold > (before_score or 0)
                              else None),
                  "factors_after": after["opportunity"]["factors"]},
        "data_confidence": {"before": prop.data_confidence, "after": after["data_confidence"]["label"]},
        "status": {"before": prop.status, "after_predicted": after["status_after"]},
        "signals": {"removed": removed, "added": added, "changed": changed,
                    "after": sorted({s["signal_type"] for s in after["stacked"]
                                     if s["freshness"] != SIG.STALE})},
        "owner": {"before": owner_before, "after": after["owner"]},
        "value": {**_econ_before(db, prop),
                  "market_estimate_after": _money(v.estimated_value),
                  "appraisal_tax_value_after": appraisal_after},
        "arv": {"before": _econ_before(db, prop)["arv_before"], "after": None,
                "after_label": after["arv"]["label"]},
        "mao": {"before": _mao_before(db, prop), "after": None,
                "after_label": ECO.MAO_BLOCKED_LABEL},
        "deed_transfer": {"before_shown_as": ("sold %s" % deed_before) if deed_before else None,
                          "after": v.last_deed_transfer_date.isoformat() if v.last_deed_transfer_date else None},
        "fields": fields,
        "unknown": after["unknown"],
        "observations": after["observations"],
    }


def _mao_before(db, prop) -> Optional[float]:
    """The preliminary MAO the pre-change engine showed (ARV x investor% -
    3% - fee, repairs as zero) - reproduced so the diff states what changes."""
    ev = prop.estimated_value
    if not ev:
        return None
    try:
        from app.services import wholesale_analysis
        from app.services.wholesale_service import resolve_settings
        st = resolve_settings(db, prop.organization_id, commit=False)
        calc = wholesale_analysis.calculate_offer(ev, None, st.investor_percentage, st.default_wholesale_fee,
                                                  getattr(st, "transaction_cost_percent", 0),
                                                  getattr(st, "transaction_cost_flat", 0))
        return calc.get("mao") and float(calc["mao"])
    except Exception:  # noqa: BLE001
        return None


def _removal_reason(stype: str, after: Dict[str, Any]) -> str:
    if stype in after["unknown"]:
        return after["unknown"][stype]
    return {
        "DISTRESSED_CONDITION": "Replaced by the appraisal district CDU rating signal (CDU_POOR / CDU_UNDESIRABLE).",
        "CODE_COMPLAINT": "The 311 request is closed: code history, not a current complaint.",
        "CODE_VIOLATION": "The city case is closed: code history, not a current violation.",
        "TAX_DELINQUENT": "The delinquency date is non-standard: tax terms unverified.",
    }.get(stype, "Not supported by the evidence under %s." % DERIVATION_VERSION)


def _summary(diffs: List[Dict[str, Any]]) -> Dict[str, Any]:
    out = {"properties": len(diffs), "score_changed": 0, "fell_below_threshold": [], "rose_above_threshold": [],
           "signals_removed": {}, "signals_added": {}, "owner_type_changed": 0, "review_flags": {},
           "appraisal_moved_out_of_estimate": 0, "arv_before_non_null": 0, "mao_before_non_null": 0,
           "city_zip_filled": 0}
    for d in diffs:
        if d["score"]["before"] != d["score"]["after"]:
            out["score_changed"] += 1
        if d["score"]["crossed"] == "fell below threshold":
            out["fell_below_threshold"].append(d["address"])
        if d["score"]["crossed"] == "rose above threshold":
            out["rose_above_threshold"].append(d["address"])
        for s in d["signals"]["removed"]:
            out["signals_removed"][s["type"]] = out["signals_removed"].get(s["type"], 0) + 1
        for s in d["signals"]["added"]:
            out["signals_added"][s["type"]] = out["signals_added"].get(s["type"], 0) + 1
        ob, oa = d["owner"]["before"], d["owner"]["after"]
        if ob and oa and ob["owner_type"] != oa["owner_type"]:
            out["owner_type_changed"] += 1
        for f in (oa or {}).get("review_flags") or []:
            out["review_flags"][f] = out["review_flags"].get(f, 0) + 1
        if d["value"].get("value_before") and d["value"]["market_estimate_after"] is None \
                and d["value"]["appraisal_tax_value_after"]:
            out["appraisal_moved_out_of_estimate"] += 1
        if d["arv"]["before"]:
            out["arv_before_non_null"] += 1
        if d["mao"]["before"]:
            out["mao_before_non_null"] += 1
        if "city" in d["fields"] or "zip_code" in d["fields"]:
            out["city_zip_filled"] += 1
    return out


# ── the three operations ────────────────────────────────────────────────────

def _properties(db, org_id: str, strategy_id: Optional[str]):
    q = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id,
                                          EvoSenseProperty.archived_at.is_(None))
    if strategy_id:
        q = q.filter(EvoSenseProperty.best_strategy_id == strategy_id)
    return q.order_by(EvoSenseProperty.opportunity_score.desc().nullslast(), EvoSenseProperty.id).all()


def dry_run(db, org_id: str, *, strategy_id: Optional[str] = None, user=None) -> EvoSenseReprocessRun:
    """Snapshot + re-derive + diff. The ONLY row written is the run itself."""
    from app.services.evosense import evaluate as EV
    weights, options = EV.score_weights(db, org_id), EV.score_options(db, org_id)
    props = _properties(db, org_id, strategy_id)
    snap, diffs = [], []
    for p in props:
        snap.append(snapshot_property(db, p))
        diffs.append(diff_property(db, p, rederive(db, p, weights=weights, options=options)))
    run = EvoSenseReprocessRun(organization_id=org_id, strategy_id=strategy_id, rule_version=RULE_VERSION,
                               status="dry_run", property_count=len(props), snapshot=C.jdump(snap),
                               diff=C.jdump(diffs), summary=C.jdump(_summary(diffs)),
                               created_by_id=getattr(user, "id", None))
    db.add(run)
    db.flush()
    C.log_event(db, org_id, "reprocess.dry_run", user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_SYSTEM,
                summary="Re-derivation dry run under %s: %s properties, nothing written" % (RULE_VERSION, len(props)),
                details={"run_id": run.id})
    return run


def get_run(db, org_id: str, run_id: str) -> Optional[EvoSenseReprocessRun]:
    return (db.query(EvoSenseReprocessRun)
            .filter(EvoSenseReprocessRun.organization_id == org_id, EvoSenseReprocessRun.id == run_id).first())


def run_json(run: EvoSenseReprocessRun, *, with_diff: bool = True) -> Dict[str, Any]:
    out = {"id": run.id, "status": run.status, "rule_version": run.rule_version,
           "property_count": run.property_count, "strategy_id": run.strategy_id,
           "summary": C.jload(run.summary, {}), "created_at": _j(run.created_at),
           "applied_at": _j(run.applied_at), "rolled_back_at": _j(run.rolled_back_at)}
    if with_diff:
        out["diff"] = C.jload(run.diff, [])
    return out


def apply(db, org_id: str, run: EvoSenseReprocessRun, *, user) -> Dict[str, Any]:
    """Write the re-derivation. Only a dry run of THIS organization can be
    applied, once. Old evidence is deactivated with a reason, never deleted."""
    from app.services.evosense import evaluate as EV
    if run.organization_id != org_id or run.status != "dry_run":
        raise ValueError("Only an unapplied dry run of this organization can be applied.")
    weights, options = EV.score_weights(db, org_id), EV.score_options(db, org_id)
    ids = [d["property_id"] for d in C.jload(run.diff, [])]
    changes = {"deactivated": [], "created": [], "reactivated": [], "scores_created": [], "owners": {}}
    mismatches = []
    for pid in ids:
        prop = (db.query(EvoSenseProperty)
                .filter(EvoSenseProperty.organization_id == org_id, EvoSenseProperty.id == pid).first())
        if prop is None:
            continue
        after = rederive(db, prop, weights=weights, options=options)
        v = after["property"]
        for c in PROP_COLS:
            if c in ("opportunity_score", "data_confidence", "signal_count", "status", "next_action",
                     "next_action_detail", "blocked_reason"):
                continue
            setattr(prop, c, getattr(v, c))
        owner = CT.primary_owner(db, prop)
        if owner is not None and after["owner"]:
            changes["owners"][owner.id] = {c: _j(getattr(owner, c, None)) for c in OWNER_COLS}
            owner.owner_type = after["owner"]["owner_type"]
            owner.name_truncated = after["owner"]["name_truncated"]
            owner.review_flags = C.jdump(after["owner"]["review_flags"]) or None
        keep = {(s.signal_type, s.source, s.source_reference) for s in after["signals"]}
        now = C.now()
        for s in (db.query(EvoSenseSignal)
                  .filter(EvoSenseSignal.organization_id == org_id, EvoSenseSignal.property_id == prop.id,
                          EvoSenseSignal.active.is_(True)).all()):
            if (s.signal_type, s.source, s.source_reference) not in keep:
                s.active = False
                s.retracted_at = now
                s.retracted_reason = ("superseded by %s (reprocess run %s): %s"
                                      % (DERIVATION_VERSION, run.id, _removal_reason(s.signal_type, after)))[:250]
                changes["deactivated"].append(s.id)
        for ns in after["signals"]:
            if ns.source == SIG.DERIVED_SOURCE:
                continue                     # the rescore below derives these through the same rules
            existing = (db.query(EvoSenseSignal)
                        .filter(EvoSenseSignal.organization_id == org_id, EvoSenseSignal.property_id == prop.id,
                                EvoSenseSignal.signal_type == ns.signal_type, EvoSenseSignal.source == ns.source,
                                EvoSenseSignal.source_reference == ns.source_reference).first())
            was_active = existing is not None and existing.active
            row = SIG.upsert(db, prop, ns.signal_type, source=ns.source, connector_kind=ns.connector_kind,
                             source_reference=ns.source_reference, observation_id=ns.observation_id,
                             observed_at=ns.observed_at, effective_at=ns.effective_at, confidence=ns.confidence,
                             raw_value=ns.raw_value, normalized_value=ns.normalized_value,
                             provenance=C.jload(ns.provenance, None), evidence_basis=ns.evidence_basis,
                             case_status=ns.case_status, rule_version=ns.rule_version)
            db.flush()
            if existing is None:
                changes["created"].append(row.id)
            elif not was_active:
                changes["reactivated"].append(row.id)
        before_scores = {s.id for s in db.query(EvoSenseScore).filter(
            EvoSenseScore.organization_id == org_id, EvoSenseScore.property_id == prop.id).all()}
        before_derived = {s.id: s.active for s in db.query(EvoSenseSignal).filter(
            EvoSenseSignal.organization_id == org_id, EvoSenseSignal.property_id == prop.id,
            EvoSenseSignal.source == SIG.DERIVED_SOURCE).all()}
        res = EV.rescore(db, prop)
        db.flush()
        for s in db.query(EvoSenseSignal).filter(EvoSenseSignal.organization_id == org_id,
                                                 EvoSenseSignal.property_id == prop.id,
                                                 EvoSenseSignal.source == SIG.DERIVED_SOURCE).all():
            if s.id not in before_derived and s.active:
                changes["created"].append(s.id)
            elif before_derived.get(s.id) and not s.active:
                changes["deactivated"].append(s.id)
            elif before_derived.get(s.id) is False and s.active:
                changes["reactivated"].append(s.id)
        changes["scores_created"].extend(s.id for s in db.query(EvoSenseScore).filter(
            EvoSenseScore.organization_id == org_id, EvoSenseScore.property_id == prop.id).all()
            if s.id not in before_scores)
        prop.derivation_version = DERIVATION_VERSION
        if res["opportunity"]["value"] != after["opportunity"]["value"]:
            mismatches.append({"property_id": prop.id, "dry_run": after["opportunity"]["value"],
                               "applied": res["opportunity"]["value"]})
    run.status = "applied"
    run.applied_at = C.now()
    run.applied_by_id = getattr(user, "id", None)
    run.applied_changes = C.jdump(changes)
    summary = C.jload(run.summary, {}) or {}
    summary["apply_mismatches"] = mismatches
    run.summary = C.jdump(summary)
    C.log_event(db, org_id, "reprocess.applied", user=user, actor_type=C.ACTOR_USER,
                summary="Re-derivation applied under %s to %s properties" % (RULE_VERSION, len(ids)),
                details={"run_id": run.id, "deactivated": len(changes["deactivated"]),
                         "created": len(changes["created"]), "mismatches": len(mismatches)})
    return {"run": run_json(run, with_diff=False), "changes": {k: len(v) for k, v in changes.items()},
            "mismatches": mismatches}


def rollback(db, org_id: str, run: EvoSenseReprocessRun, *, user) -> Dict[str, Any]:
    """Put back exactly what the run's snapshot recorded, for this organization only."""
    if run.organization_id != org_id or run.status != "applied":
        raise ValueError("Only an applied run of this organization can be rolled back.")
    snap = {s["id"]: s for s in C.jload(run.snapshot, [])}
    changes = C.jload(run.applied_changes, {}) or {}
    for pid, s in snap.items():
        prop = (db.query(EvoSenseProperty)
                .filter(EvoSenseProperty.organization_id == org_id, EvoSenseProperty.id == pid).first())
        if prop is None:
            continue
        col_types = {c.name: c.type for c in EvoSenseProperty.__table__.columns}
        for c, val in s["columns"].items():
            t = str(col_types.get(c, ""))
            if val is not None and t.startswith("DATETIME"):
                val = _parse_dt(val)
            elif val is not None and t == "DATE":
                val = datetime.strptime(val[:10], "%Y-%m-%d").date()
            setattr(prop, c, val)
        active = set(s["active_signal_ids"])
        for sig in db.query(EvoSenseSignal).filter(EvoSenseSignal.organization_id == org_id,
                                                   EvoSenseSignal.property_id == pid).all():
            want = sig.id in active
            if sig.active != want:
                sig.active = want
                if want:
                    sig.retracted_at = sig.retracted_reason = None
                else:
                    sig.retracted_at = C.now()
                    sig.retracted_reason = "rolled back (reprocess run %s)" % run.id
        current = set(s["current_score_ids"])
        for sc in db.query(EvoSenseScore).filter(EvoSenseScore.organization_id == org_id,
                                                 EvoSenseScore.property_id == pid).all():
            sc.is_current = sc.id in current
    for oid, cols in (changes.get("owners") or {}).items():
        o = db.query(EvoSenseOwner).filter(EvoSenseOwner.organization_id == org_id, EvoSenseOwner.id == oid).first()
        if o is not None:
            for c, val in cols.items():
                setattr(o, c, val)
    run.status = "rolled_back"
    run.rolled_back_at = C.now()
    run.rolled_back_by_id = getattr(user, "id", None)
    C.log_event(db, org_id, "reprocess.rolled_back", user=user, actor_type=C.ACTOR_USER,
                summary="Re-derivation rolled back to the snapshot of run %s" % run.id, details={"run_id": run.id})
    return {"run": run_json(run, with_diff=False)}
