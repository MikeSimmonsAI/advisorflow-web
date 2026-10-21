"""ROOFING FIELD INSPECTIONS - documenting what a salesperson finds at the property.

A booked appointment (BookingLink) says WHEN somebody is going. This module is
WHAT THEY FOUND: the roof, the damage checklist, severity, the areas affected,
the inspector's notes, photos, the recommendation - saved as a draft while
they work, completed as a final report, and handed to sales afterwards.

RULES THAT MATTER
  * Only a COMPLETED report moves the journey to Inspected from here, and a
    report can only be completed with its findings and at least one photo. A
    bare stage change is shown on the dashboard as "inspected without a report"
    rather than passing for a documented inspection.
  * One homeowner can have several inspections (a re-inspection after a
    storm); each is tied to its own appointment.
  * Tenant and salesperson scope come from lead_scope: a salesperson sees and
    works only homeowners they can already see; the owner/manager sees all.
  * Photos are stored by app/services/wholesale_files (the database unless an
    object store is configured) and served only through an authenticated route.
  * Nothing here sends a message, touches a calendar or claims an estimate was
    delivered. The sales hand-off records what the team decided and moves the
    existing roofing stages; it never pretends an integration did something.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.models.models import BookingLink, Lead
from app.models.roofing_models import RoofingInspection, RoofingInspectionPhoto
from app.services import lead_scope
from app.services.roofing import common as C
from app.services.roofing import journey as J

IN_PROGRESS = "in_progress"
COMPLETED = "completed"

ROOF_TYPES = ["Asphalt shingle", "Architectural shingle", "Metal", "Tile", "Slate", "Wood shake",
              "Flat / low slope", "Other"]
CONDITIONS = [("good", "Good"), ("fair", "Fair"), ("poor", "Poor"), ("failed", "Failed")]
SEVERITIES = [("none", "No damage"), ("minor", "Minor"), ("moderate", "Moderate"), ("severe", "Severe")]
DAMAGE = [("hail", "Hail impact"), ("wind", "Wind damage / lifted shingles"), ("missing", "Missing shingles"),
          ("granule_loss", "Granule loss"), ("cracked", "Cracked / curled shingles"), ("leak", "Active leak"),
          ("flashing", "Damaged flashing"), ("vents", "Vent / boot damage"), ("gutters", "Gutter damage"),
          ("decking", "Soft or rotted decking"), ("ventilation", "Poor attic ventilation"),
          ("interior_stain", "Interior water stain"), ("tree", "Tree / debris impact"), ("moss", "Moss / algae")]
AREAS = ["North slope", "South slope", "East slope", "West slope", "Ridge", "Valleys", "Chimney",
         "Skylights", "Gutters", "Attic", "Interior ceiling", "Garage", "Porch"]
RECOMMENDATIONS = [("repair", "Repair"), ("replacement", "Full replacement"), ("monitor", "Monitor / maintenance"),
                   ("none", "No work needed")]
CLAIM = [("yes", "Insurance claim likely"), ("maybe", "Possible claim"), ("no", "No claim")]
PHOTO_LABELS = [("overview", "Overview"), ("damage", "Damage"), ("shingles", "Shingles"), ("flashing", "Flashing"),
                ("gutters", "Gutters"), ("vents", "Vents"), ("attic", "Attic"), ("interior", "Interior"),
                ("other", "Other")]
HANDOFFS = [("prepare_estimate", "Prepare estimate"), ("schedule_follow_up", "Schedule follow-up"),
            ("recommend_replacement", "Recommend replacement"), ("recommend_repair", "Recommend repair"),
            ("close_no_work", "Close - no work needed")]
MAX_PHOTOS = 60

_KEYS = {"condition": {k for k, _ in CONDITIONS}, "severity": {k for k, _ in SEVERITIES},
         "damage": {k for k, _ in DAMAGE}, "recommendation": {k for k, _ in RECOMMENDATIONS},
         "claim": {k for k, _ in CLAIM}, "label": {k for k, _ in PHOTO_LABELS},
         "handoff": {k for k, _ in HANDOFFS}}


def choices() -> Dict[str, Any]:
    """Everything the workspace offers, so the screen never invents a value."""
    pairs = lambda xs: [{"key": k, "label": l} for k, l in xs]  # noqa: E731
    return {"roof_types": ROOF_TYPES, "conditions": pairs(CONDITIONS), "severities": pairs(SEVERITIES),
            "damage": pairs(DAMAGE), "areas": AREAS, "recommendations": pairs(RECOMMENDATIONS),
            "insurance_claim": pairs(CLAIM), "photo_labels": pairs(PHOTO_LABELS), "handoffs": pairs(HANDOFFS)}


# ── time, in the roofing company's own clock ────────────────────────────────
def _now_local(org) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(getattr(org, "timezone", None) or "America/Chicago")).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return datetime.utcnow() - timedelta(hours=5)


# ── loading, always inside the caller's scope ───────────────────────────────
def _lead(ctx: C.Ctx, lead_id: str) -> Lead:
    lead = lead_scope.load_lead_in_scope(ctx.db, ctx.user, lead_id, ctx.request)
    if lead.organization_id != ctx.org_id:
        raise HTTPException(404, "Homeowner not found")
    return lead


def get(ctx: C.Ctx, inspection_id: str) -> RoofingInspection:
    ins = (ctx.db.query(RoofingInspection)
           .filter(RoofingInspection.id == inspection_id, RoofingInspection.organization_id == ctx.org_id).first())
    if ins is None:
        raise HTTPException(404, "Inspection not found")
    _lead(ctx, ins.lead_id)                       # salesperson scope, same as the homeowner
    return ins


def _may_write(ctx: C.Ctx, ins: RoofingInspection) -> None:
    if ctx.manager:
        return
    lead = ctx.db.query(Lead).filter(Lead.id == ins.lead_id).first()
    if ctx.user.id not in (ins.inspector_user_id, getattr(lead, "assigned_to_id", None)):
        raise HTTPException(403, "Only the assigned inspector or a manager can change this inspection.")


def _next_number(ctx: C.Ctx) -> str:
    n = ctx.db.query(RoofingInspection).filter(RoofingInspection.organization_id == ctx.org_id,
                                               RoofingInspection.report_number.isnot(None)).count()
    return "R-%04d" % (1001 + n)


# ── start / open ────────────────────────────────────────────────────────────
def start(ctx: C.Ctx, lead_id: str, booking_link_id: Optional[str] = None) -> RoofingInspection:
    """Open the inspection for this appointment, creating it the first time.
    Idempotent per appointment: opening it twice never makes a second report."""
    lead = _lead(ctx, lead_id)
    db = ctx.db
    bl = None
    if booking_link_id:
        bl = db.query(BookingLink).filter(BookingLink.id == booking_link_id, BookingLink.lead_id == lead.id).first()
        if bl is None:
            raise HTTPException(404, "That appointment isn't on this homeowner.")
        ins = (db.query(RoofingInspection).filter(RoofingInspection.organization_id == ctx.org_id,
                                                  RoofingInspection.booking_link_id == bl.id).first())
        if ins is not None:
            return ins
    else:
        # No appointment named: continue an unfinished one before starting another.
        ins = (db.query(RoofingInspection).filter(RoofingInspection.organization_id == ctx.org_id,
                                                  RoofingInspection.lead_id == lead.id,
                                                  RoofingInspection.status == IN_PROGRESS)
               .order_by(RoofingInspection.created_at.desc()).first())
        if ins is not None:
            return ins
    opp = J.ensure(db, ctx.org_id, [lead])[lead.id]
    f = C.lead_fields(lead)
    ins = RoofingInspection(organization_id=ctx.org_id, lead_id=lead.id, opportunity_id=opp.id,
                            booking_link_id=bl.id if bl else None,
                            inspector_user_id=(bl.user_id if bl else None) or lead.assigned_to_id or ctx.user.id,
                            scheduled_at=bl.booked_time if bl else None,
                            inspection_type=(bl.appt_label if bl else None) or opp.inspection_type or "Roof Inspection",
                            status=IN_PROGRESS, started_at=datetime.utcnow(),
                            roof_age_years=int(f["roof_age"]) if isinstance(f.get("roof_age"), (int, float)) else None)
    if not ctx.manager and ctx.user.id not in (ins.inspector_user_id, lead.assigned_to_id):
        raise HTTPException(403, "Only the assigned inspector or a manager can start this inspection.")
    db.add(ins)
    db.commit()
    return ins


# ── saving findings (a draft) ───────────────────────────────────────────────
def _pick(v, allowed, what):
    if v in (None, ""):
        return None
    if v not in allowed:
        raise HTTPException(400, "Unknown %s: %s" % (what, v))
    return v


def save(ctx: C.Ctx, ins: RoofingInspection, data: Dict[str, Any]) -> RoofingInspection:
    _may_write(ctx, ins)
    if ins.status == COMPLETED and not data.get("_reopening"):
        raise HTTPException(409, "This report is complete. A manager can reopen it to make changes.")
    if "roof_type" in data:
        ins.roof_type = (data["roof_type"] or None) and str(data["roof_type"])[:40]
    for k in ("roof_age_years", "stories"):
        if k in data:
            v = data[k]
            if v in (None, ""):
                setattr(ins, k, None)
            else:
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    raise HTTPException(400, "%s must be a number" % k.replace("_", " "))
                if not 0 <= v <= 150:
                    raise HTTPException(400, "%s is out of range" % k.replace("_", " "))
                setattr(ins, k, v)
    if "roof_condition" in data:
        ins.roof_condition = _pick(data["roof_condition"], _KEYS["condition"], "roof condition")
    if "severity" in data:
        ins.severity = _pick(data["severity"], _KEYS["severity"], "severity")
    if "damage" in data:
        vals = data["damage"] or []
        bad = [x for x in vals if x not in _KEYS["damage"]]
        if bad:
            raise HTTPException(400, "Unknown damage item: %s" % bad[0])
        ins.damage = C.jdump(sorted(set(vals)))
    if "affected_areas" in data:
        ins.affected_areas = C.jdump([str(a)[:40] for a in (data["affected_areas"] or [])][:20])
    if "notes" in data:
        ins.notes = (data["notes"] or "").strip()[:8000] or None
    if "recommendation" in data:
        ins.recommendation = _pick(data["recommendation"], _KEYS["recommendation"], "recommendation")
    if "recommendation_notes" in data:
        ins.recommendation_notes = (data["recommendation_notes"] or "").strip()[:4000] or None
    if "insurance_claim" in data:
        ins.insurance_claim = _pick(data["insurance_claim"], _KEYS["claim"], "insurance answer")
    if "inspection_type" in data and data["inspection_type"]:
        ins.inspection_type = str(data["inspection_type"])[:80]
    ins.updated_at = datetime.utcnow()
    ctx.db.commit()
    return ins


def missing_for_completion(ctx: C.Ctx, ins: RoofingInspection) -> List[str]:
    miss = []
    if not ins.roof_condition:
        miss.append("Roof condition")
    if not ins.severity:
        miss.append("Severity of damage")
    if ins.severity not in (None, "none") and not C.jload(ins.damage, []):
        miss.append("At least one item on the damage checklist")
    if not (ins.notes or "").strip():
        miss.append("Inspector's notes")
    if not ins.recommendation:
        miss.append("Recommendation")
    if not _photo_rows(ctx, ins):
        miss.append("At least one photo")
    return miss


def complete(ctx: C.Ctx, ins: RoofingInspection) -> RoofingInspection:
    _may_write(ctx, ins)
    if ins.status == COMPLETED:
        return ins
    miss = missing_for_completion(ctx, ins)
    if miss:
        raise HTTPException(422, {"message": "The report isn't finished yet.", "missing": miss})
    ins.status = COMPLETED
    ins.completed_at = datetime.utcnow()
    ins.completed_by_id = ctx.user.id
    ins.report_number = ins.report_number or _next_number(ctx)
    ins.next_action = {"replacement": "prepare_estimate", "repair": "prepare_estimate",
                       "monitor": "schedule_follow_up", "none": "close_no_work"}.get(ins.recommendation)
    lead = ctx.db.query(Lead).filter(Lead.id == ins.lead_id).first()
    opp = J.ensure(ctx.db, ctx.org_id, [lead])[lead.id]
    if C.RANK.get(opp.stage, -1) < C.RANK[C.INSPECTED] and opp.stage != C.LOST:
        J.set_stage(ctx.db, opp, C.INSPECTED, ctx.user.id, "Inspection report %s completed" % ins.report_number)
    _note(ctx, ins, "Inspection report %s completed: %s condition, %s damage. Recommendation: %s."
          % (ins.report_number, _label(CONDITIONS, ins.roof_condition), _label(SEVERITIES, ins.severity).lower(),
             _label(RECOMMENDATIONS, ins.recommendation)))
    ctx.db.commit()
    return ins


def reopen(ctx: C.Ctx, ins: RoofingInspection) -> RoofingInspection:
    if not ctx.manager:
        raise HTTPException(403, "Only a manager can reopen a completed report.")
    ins.status = IN_PROGRESS
    ins.updated_at = datetime.utcnow()
    _note(ctx, ins, "Inspection report %s reopened for changes." % (ins.report_number or ""))
    ctx.db.commit()
    return ins


# ── the sales hand-off ──────────────────────────────────────────────────────
def handoff(ctx: C.Ctx, ins: RoofingInspection, action: str, data: Dict[str, Any]) -> RoofingInspection:
    _may_write(ctx, ins)
    if action not in _KEYS["handoff"]:
        raise HTTPException(400, "Unknown next step.")
    if ins.status != COMPLETED:
        raise HTTPException(409, "Complete the inspection report first.")
    db = ctx.db
    lead = db.query(Lead).filter(Lead.id == ins.lead_id).first()
    opp = J.ensure(db, ctx.org_id, [lead])[lead.id]
    detail: Dict[str, Any] = {}
    note = None
    if action == "prepare_estimate":
        amt = data.get("estimate_amount")
        if amt not in (None, ""):
            try:
                amt = float(amt)
            except (TypeError, ValueError):
                raise HTTPException(400, "Estimate amount must be a number")
            if amt < 0 or amt > 10_000_000:
                raise HTTPException(400, "Estimate amount is out of range")
            opp.estimate_amount = amt
            detail["estimate_amount"] = amt
        scope = (data.get("scope") or "").strip()[:2000]
        if scope:
            detail["scope"] = scope
        sent = bool(data.get("estimate_sent"))
        detail["estimate_sent"] = sent
        if sent:
            if opp.estimate_amount is None:
                raise HTTPException(400, "Enter the estimate amount before marking it sent.")
            if C.RANK.get(opp.stage, -1) < C.RANK[C.ESTIMATE_SENT] and opp.stage != C.LOST:
                J.set_stage(db, opp, C.ESTIMATE_SENT, ctx.user.id, "Estimate from report %s" % ins.report_number)
            ins.next_action = "follow_up_estimate"
            note = "Estimate sent from inspection %s%s." % (ins.report_number, (" - $%s" % format(opp.estimate_amount, ",.0f")))
        else:
            ins.next_action = "send_estimate"
            note = "Estimate being prepared from inspection %s%s." % (
                ins.report_number, (" - $%s" % format(opp.estimate_amount, ",.0f")) if opp.estimate_amount else "")
    elif action == "schedule_follow_up":
        when = data.get("follow_up_at")
        if not when:
            raise HTTPException(400, "Pick a follow-up date.")
        try:
            ins.follow_up_at = datetime.fromisoformat(str(when)[:19])
        except ValueError:
            raise HTTPException(400, "Follow-up date isn't a valid date.")
        detail["follow_up_at"] = ins.follow_up_at.isoformat()
        ins.next_action = "follow_up"
        note = "Follow-up set for %s after inspection %s.%s" % (
            ins.follow_up_at.strftime("%b %d, %Y"), ins.report_number,
            (" " + data["note"].strip()[:500]) if data.get("note") else "")
    elif action in ("recommend_replacement", "recommend_repair"):
        ins.recommendation = "replacement" if action == "recommend_replacement" else "repair"
        lead.custom_fields = C.jdump({**C.jload(lead.custom_fields, {}), "service_type": ins.recommendation})
        ins.next_action = "prepare_estimate"
        note = "Recommended %s after inspection %s." % (ins.recommendation, ins.report_number)
    elif action == "close_no_work":
        if opp.stage != C.LOST:
            J.set_stage(db, opp, C.LOST, ctx.user.id, "Inspection %s: no work needed" % ins.report_number)
        ins.recommendation = ins.recommendation or "none"
        ins.next_action = None
        note = "Closed after inspection %s - no work needed." % ins.report_number
    ins.handoff = C.jdump({"action": action, "label": _label(HANDOFFS, action), "at": datetime.utcnow().isoformat(),
                           "by": ctx.user.id, **detail})
    if note:
        _note(ctx, ins, note)
    db.commit()
    return ins


def _note(ctx: C.Ctx, ins: RoofingInspection, body: str) -> None:
    try:
        from app.models.work_models import LeadNote
        ctx.db.add(LeadNote(organization_id=ctx.org_id, lead_id=ins.lead_id, author_user_id=ctx.user.id,
                            body=body[:4000], kind="note"))
    except Exception:  # noqa: BLE001 - a note is a convenience, never the record itself
        pass


def _label(pairs, key) -> str:
    return dict(pairs).get(key, key or "-")


# ── photos ──────────────────────────────────────────────────────────────────
def _photo_rows(ctx: C.Ctx, ins: RoofingInspection) -> List[RoofingInspectionPhoto]:
    return (ctx.db.query(RoofingInspectionPhoto)
            .filter(RoofingInspectionPhoto.organization_id == ctx.org_id,
                    RoofingInspectionPhoto.inspection_id == ins.id)
            .order_by(RoofingInspectionPhoto.position.asc(), RoofingInspectionPhoto.created_at.asc()).all())


def photo_json(p: RoofingInspectionPhoto) -> Dict[str, Any]:
    return {"id": p.id, "label": p.label, "label_text": _label(PHOTO_LABELS, p.label) if p.label else None,
            "caption": p.caption, "is_sample": bool(p.is_sample), "content_type": p.content_type,
            "url": "/roofing-ops/inspections/%s/photos/%s" % (p.inspection_id, p.id),
            "created_at": C.iso(p.created_at)}


async def add_photo(ctx: C.Ctx, ins: RoofingInspection, upload, label=None, caption=None) -> Dict[str, Any]:
    from app.services import wholesale_files as files
    _may_write(ctx, ins)
    if ins.status == COMPLETED:
        raise HTTPException(409, "This report is complete. A manager can reopen it to add photos.")
    data, content_type, original = await files.read_upload(upload, images_only=True)
    checksum = hashlib.sha256(data).hexdigest()
    same = (ctx.db.query(RoofingInspectionPhoto)
            .filter(RoofingInspectionPhoto.organization_id == ctx.org_id,
                    RoofingInspectionPhoto.inspection_id == ins.id,
                    RoofingInspectionPhoto.checksum == checksum).first())
    if same is not None:                          # a retry after a dropped connection
        return photo_json(same)
    rows = _photo_rows(ctx, ins)
    if len(rows) >= MAX_PHOTOS:
        raise HTTPException(422, "This inspection already has %d photos." % MAX_PHOTOS)
    stored = files.put(ctx.org_id, "roofing_inspection", data, content_type, db=ctx.db)
    p = RoofingInspectionPhoto(organization_id=ctx.org_id, inspection_id=ins.id, lead_id=ins.lead_id,
                               label=label if label in _KEYS["label"] else None,
                               caption=(caption or "").strip()[:300] or None, original_filename=original,
                               position=len(rows), created_by_id=ctx.user.id, **stored)
    ctx.db.add(p)
    ctx.db.commit()
    return photo_json(p)


def _photo(ctx: C.Ctx, ins: RoofingInspection, photo_id: str) -> RoofingInspectionPhoto:
    p = (ctx.db.query(RoofingInspectionPhoto)
         .filter(RoofingInspectionPhoto.id == photo_id, RoofingInspectionPhoto.organization_id == ctx.org_id,
                 RoofingInspectionPhoto.inspection_id == ins.id).first())
    if p is None:
        raise HTTPException(404, "Photo not found")
    return p


def update_photo(ctx: C.Ctx, ins: RoofingInspection, photo_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    _may_write(ctx, ins)
    p = _photo(ctx, ins, photo_id)
    if "label" in data:
        p.label = _pick(data["label"], _KEYS["label"], "photo label")
    if "caption" in data:
        p.caption = (data["caption"] or "").strip()[:300] or None
    ctx.db.commit()
    return photo_json(p)


def photo_bytes(ctx: C.Ctx, ins: RoofingInspection, photo_id: str):
    from app.services import wholesale_files as files
    p = _photo(ctx, ins, photo_id)
    return files.fetch(p.storage_backend, p.storage_key, db=ctx.db), p.content_type


def remove_photo(ctx: C.Ctx, ins: RoofingInspection, photo_id: str) -> None:
    from app.services import wholesale_files as files
    _may_write(ctx, ins)
    if ins.status == COMPLETED:
        raise HTTPException(409, "This report is complete. A manager can reopen it to change photos.")
    p = _photo(ctx, ins, photo_id)
    backend, key = p.storage_backend, p.storage_key
    ctx.db.delete(p)
    ctx.db.commit()
    try:
        files.remove(backend, key, db=ctx.db)
    except Exception:  # noqa: BLE001 - the row is gone; an orphaned blob is harmless
        pass


# ── reading ─────────────────────────────────────────────────────────────────
def to_json(ctx: C.Ctx, ins: RoofingInspection, full: bool = True) -> Dict[str, Any]:
    db = ctx.db
    lead = db.query(Lead).filter(Lead.id == ins.lead_id).first()
    names = C.user_names(db, ctx.org_id)
    opp = J.ensure(db, ctx.org_id, [lead])[lead.id] if lead else None
    out = {
        "id": ins.id, "lead_id": ins.lead_id, "status": ins.status,
        "status_label": "Completed" if ins.status == COMPLETED else "In progress",
        "report_number": ins.report_number, "booking_link_id": ins.booking_link_id,
        "inspection_type": ins.inspection_type, "scheduled_at": ins.scheduled_at.isoformat() if ins.scheduled_at else None,
        "inspector_id": ins.inspector_user_id, "inspector_name": names.get(ins.inspector_user_id),
        "started_at": C.iso(ins.started_at), "completed_at": C.iso(ins.completed_at),
        "completed_by_name": names.get(ins.completed_by_id),
        "roof_type": ins.roof_type, "roof_age_years": ins.roof_age_years, "stories": ins.stories,
        "roof_condition": ins.roof_condition, "roof_condition_label": _label(CONDITIONS, ins.roof_condition) if ins.roof_condition else None,
        "damage": C.jload(ins.damage, []), "damage_labels": [_label(DAMAGE, k) for k in C.jload(ins.damage, [])],
        "severity": ins.severity, "severity_label": _label(SEVERITIES, ins.severity) if ins.severity else None,
        "affected_areas": C.jload(ins.affected_areas, []), "notes": ins.notes,
        "recommendation": ins.recommendation,
        "recommendation_label": _label(RECOMMENDATIONS, ins.recommendation) if ins.recommendation else None,
        "recommendation_notes": ins.recommendation_notes, "insurance_claim": ins.insurance_claim,
        "insurance_claim_label": _label(CLAIM, ins.insurance_claim) if ins.insurance_claim else None,
        "next_action": ins.next_action, "follow_up_at": ins.follow_up_at.isoformat() if ins.follow_up_at else None,
        "handoff": C.jload(ins.handoff, None),
        "homeowner": {"name": C.lead_name(lead), "phone": lead.phone, "email": lead.email,
                      "address": C.address(lead), "roof_issue": C.lead_fields(lead).get("roof_issue"),
                      "is_test": bool(lead.is_test)} if lead else None,
        "opportunity": {"stage": opp.stage, "stage_label": C.STAGE_LABEL.get(opp.stage, opp.stage),
                        "estimate_amount": opp.estimate_amount, "won_amount": opp.won_amount} if opp else None,
        "can_edit": ctx.manager or ctx.user.id in (ins.inspector_user_id, getattr(lead, "assigned_to_id", None)),
        "can_reopen": bool(ctx.manager and ins.status == COMPLETED),
    }
    if full:
        out["photos"] = [photo_json(p) for p in _photo_rows(ctx, ins)]
        out["missing"] = missing_for_completion(ctx, ins) if ins.status != COMPLETED else []
        out["company"] = _company(ctx)
        out["sample_data"] = bool(getattr(ctx.org, "is_demo", False))
        out["choices"] = choices()
    else:
        out["photo_count"] = len(_photo_rows(ctx, ins))
    return out


def _company(ctx: C.Ctx) -> Dict[str, Any]:
    from app.services.roofing import setup as S
    p = S.get_profile(ctx.db, ctx.org_id)
    return {"name": (p.company_name if p and p.company_name else None) or ctx.org.brand_name or ctx.org.name,
            "phone": getattr(p, "business_phone", None) or ctx.org.org_phone,
            "email": getattr(p, "business_email", None), "color": getattr(p, "color_primary", None)}


def for_lead(ctx: C.Ctx, lead_id: str) -> List[Dict[str, Any]]:
    rows = (ctx.db.query(RoofingInspection).filter(RoofingInspection.organization_id == ctx.org_id,
                                                   RoofingInspection.lead_id == lead_id)
            .order_by(RoofingInspection.created_at.desc()).all())
    return [{"id": r.id, "status": r.status, "report_number": r.report_number,
             "scheduled_at": r.scheduled_at.isoformat() if r.scheduled_at else None,
             "completed_at": C.iso(r.completed_at), "recommendation": r.recommendation,
             "recommendation_label": _label(RECOMMENDATIONS, r.recommendation) if r.recommendation else None}
            for r in rows]


# ── the dashboard ───────────────────────────────────────────────────────────
def dashboard(ctx: C.Ctx) -> Dict[str, Any]:
    """Every inspection in exactly one bucket, each with its one next action."""
    db = ctx.db
    now = _now_local(ctx.org)
    leads = {l.id: l for l in lead_scope.authorized_lead_query(db, ctx.user, request=ctx.request)
             .filter(Lead.organization_id == ctx.org_id).all()}
    ids = list(leads)
    names = C.user_names(db, ctx.org_id)
    ins_rows: List[RoofingInspection] = []
    books: List[BookingLink] = []
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        ins_rows += (db.query(RoofingInspection).filter(RoofingInspection.organization_id == ctx.org_id,
                                                        RoofingInspection.lead_id.in_(part)).all())
        books += db.query(BookingLink).filter(BookingLink.lead_id.in_(part)).all()
    by_booking = {r.booking_link_id: r for r in ins_rows if r.booking_link_id}
    opps = J.ensure(db, ctx.org_id, list(leads.values())) if leads else {}
    photo_counts: Dict[str, int] = {}
    if ins_rows:
        for (iid,) in (db.query(RoofingInspectionPhoto.inspection_id)
                       .filter(RoofingInspectionPhoto.organization_id == ctx.org_id,
                               RoofingInspectionPhoto.inspection_id.in_([r.id for r in ins_rows])).all()):
            photo_counts[iid] = photo_counts.get(iid, 0) + 1

    def base(lead, when, who, itype):
        f = C.lead_fields(lead)
        return {"lead_id": lead.id, "name": C.lead_name(lead), "address": C.address(lead), "phone": lead.phone,
                "roof_issue": f.get("roof_issue"), "when": when.isoformat() if when else None,
                "inspector_name": names.get(who), "inspection_type": itype,
                "stage": opps[lead.id].stage if lead.id in opps else None,
                "stage_label": C.STAGE_LABEL.get(opps[lead.id].stage) if lead.id in opps else None,
                "is_test": bool(lead.is_test)}

    buckets: Dict[str, List[Dict[str, Any]]] = {k: [] for k in
                                                 ("upcoming", "awaiting", "in_progress", "unfinished",
                                                  "next_step", "completed", "undocumented")}
    # appointments without a report yet
    for b in books:
        lead = leads.get(b.lead_id)
        if lead is None or b.id in by_booking:
            continue
        if b.status in ("booked", "confirmed") and b.booked_time:
            item = base(lead, b.booked_time, b.user_id, b.appt_label)
            item.update(booking_link_id=b.id, inspection_id=None)
            if b.booked_time >= now - timedelta(hours=3):
                item["action"] = {"key": "start", "label": "Open inspection"}
                buckets["upcoming"].append(item)
            else:
                stage = opps[lead.id].stage if lead.id in opps else None
                if C.RANK.get(stage, -1) >= C.RANK[C.INSPECTED] or stage == C.LOST:
                    item["action"] = {"key": "start", "label": "Add the report"}
                    buckets["undocumented"].append(item)
                else:
                    item["action"] = {"key": "start", "label": "Start report"}
                    buckets["unfinished"].append(item)
        elif b.status == "pending":
            item = base(lead, None, b.user_id, b.appt_label)
            item.update(booking_link_id=b.id, inspection_id=None,
                        action={"key": "book", "label": "Confirm a time"}, offered_at=C.iso(b.created_at))
            buckets["awaiting"].append(item)
    # documented inspections
    for r in ins_rows:
        lead = leads.get(r.lead_id)
        if lead is None:
            continue
        item = base(lead, r.scheduled_at or r.started_at, r.inspector_user_id, r.inspection_type)
        item.update(inspection_id=r.id, booking_link_id=r.booking_link_id, report_number=r.report_number,
                    photo_count=photo_counts.get(r.id, 0), recommendation=r.recommendation,
                    recommendation_label=_label(RECOMMENDATIONS, r.recommendation) if r.recommendation else None,
                    severity_label=_label(SEVERITIES, r.severity) if r.severity else None,
                    completed_at=C.iso(r.completed_at), next_action=r.next_action,
                    follow_up_at=r.follow_up_at.isoformat() if r.follow_up_at else None,
                    handoff=C.jload(r.handoff, None))
        if r.status != COMPLETED:
            fresh = r.updated_at and (datetime.utcnow() - r.updated_at) < timedelta(hours=10)
            item["action"] = {"key": "open", "label": "Continue report"}
            buckets["in_progress" if fresh else "unfinished"].append(item)
            continue
        stage = item["stage"]
        open_step = (stage not in (C.WON, C.LOST, C.ESTIMATE_SENT)
                     and r.next_action in ("prepare_estimate", "send_estimate", "follow_up", "schedule_follow_up"))
        if open_step:
            item["action"] = {"key": "next", "label": {"follow_up": "Follow up", "schedule_follow_up": "Schedule follow-up"}
                              .get(r.next_action, "Prepare estimate")}
            buckets["next_step"].append(item)
        else:
            item["action"] = {"key": "report", "label": "View report"}
            buckets["completed"].append(item)

    buckets["upcoming"].sort(key=lambda x: x["when"] or "")
    for k in ("unfinished", "in_progress", "undocumented", "completed", "next_step"):
        buckets[k].sort(key=lambda x: x.get("completed_at") or x["when"] or "", reverse=True)
    # Homeowners who said yes but have no appointment link yet (mission's "requested" lane)
    try:
        from app.services.roofing import mission as MC
        m = MC.build(db, ctx, lane_limit=200)
        have = {x["lead_id"] for x in buckets["awaiting"]}
        for lane in m.get("lanes", []):
            if lane.get("key") == "requested":
                for c in lane.get("cards", []):
                    if c["lead_id"] in have:
                        continue
                    buckets["awaiting"].append({"lead_id": c["lead_id"], "name": c["name"], "address": c.get("address"),
                                                "roof_issue": c.get("roof_issue"), "when": None, "inspection_id": None,
                                                "booking_link_id": None, "inspector_name": c.get("owner_name"),
                                                "summary": c.get("summary"), "stage_label": c.get("stage_label"),
                                                "action": {"key": "book", "label": "Book inspection"},
                                                "is_test": c.get("is_test")})
    except Exception:  # noqa: BLE001 - the request list is a convenience on this screen
        pass
    counts = {k: len(v) for k, v in buckets.items()}
    return {"buckets": buckets, "counts": counts, "now_local": now.isoformat(timespec="minutes"),
            "sample_data": bool(getattr(ctx.org, "is_demo", False)), "choices": choices()}
