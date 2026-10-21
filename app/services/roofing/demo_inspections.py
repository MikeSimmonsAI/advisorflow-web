"""SAMPLE field inspections for the roofing demo workspace.

Called by demo_seed.seed after the homeowner story exists. For homeowners the
story already took past an inspection it writes a COMPLETED report (findings,
recommendation, photos); for an inspection whose time has just passed it
leaves a DRAFT, so the "unfinished report" path can be shown too.

Everything is sample: the workspace is is_demo (nothing can send), every
homeowner is is_test, and every photo is a drawn illustration stamped
"SAMPLE PHOTO" (app/static/roofing_samples) stored with is_sample=True.
Idempotent: a homeowner who already has an inspection is skipped.
"""
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy.orm import Session

from app.models.models import BookingLink, Lead, User
from app.models.roofing_models import RoofingInspection, RoofingInspectionPhoto, RoofingOpportunity
from app.services.roofing import common as C

SAMPLE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static", "roofing_samples")

# (photo file, label, caption) sets per kind of finding
SETS = {
    "hail": [("overview", "overview", "Front elevation"), ("hail", "damage", "Hail impacts on the north slope, chalked"),
             ("granules", "shingles", "Granule loss where the hail hit"), ("gutter", "gutters", "Dented gutter, granules in the trough")],
    "wind": [("overview", "overview", "Front elevation"), ("missing", "damage", "Missing shingles, underlayment exposed"),
             ("flashing", "flashing", "Flashing lifted at the chimney")],
    "leak": [("overview", "overview", "Front elevation"), ("flashing", "flashing", "Chimney flashing pulled away"),
             ("attic", "attic", "Water staining on the attic decking"), ("interior", "interior", "Ceiling stain in bedroom 2")],
    "age": [("overview", "overview", "Front elevation"), ("granules", "shingles", "Widespread granule loss - end of life"),
            ("gutter", "gutters", "Granules collecting in the gutters")],
}
FINDINGS = {
    "hail": dict(damage=["hail", "granule_loss", "gutters"], severity="moderate", condition="poor",
                 areas=["North slope", "West slope", "Gutters"], rec="replacement", claim="yes",
                 notes="Consistent hail bruising across the north and west slopes - roughly 9-12 hits per test square. "
                       "Granule loss at the impact points and dents in the front gutter. Meets the threshold for an "
                       "insurance claim; homeowner has a claim open with their carrier.",
                 scope="Full tear-off and replacement, architectural shingle, new drip edge and gutter section."),
    "wind": dict(damage=["wind", "missing", "flashing"], severity="moderate", condition="fair",
                 areas=["South slope", "Ridge", "Chimney"], rec="repair", claim="maybe",
                 notes="Six shingles missing on the south slope after last week's wind, two more creased and lifting. "
                       "Step flashing at the chimney has pulled away. Decking is sound - a targeted repair will hold.",
                 scope="Replace 2 squares on the south slope, reseal and re-flash the chimney."),
    "leak": dict(damage=["leak", "flashing", "interior_stain", "decking"], severity="severe", condition="poor",
                 areas=["Chimney", "Attic", "Interior ceiling"], rec="replacement", claim="maybe",
                 notes="Active leak at the chimney. Flashing failed, water has stained the attic decking and the bedroom "
                       "ceiling. Two sheets of decking are soft. Roof is 21 years old - replacement recommended over a patch.",
                 scope="Tear-off, replace soft decking, new chimney flashing and cricket, full replacement."),
    "age": dict(damage=["granule_loss", "cracked", "ventilation"], severity="minor", condition="fair",
                areas=["South slope", "West slope", "Gutters"], rec="monitor", claim="no",
                notes="No storm damage. Shingles are near end of life - granule loss and some cracking on the south and "
                      "west faces. No leaks today. Recommend planning a replacement within 2-3 years.",
                scope="Re-inspect next spring; budget for replacement."),
}
ROOF = ["Architectural shingle", "Asphalt shingle", "Architectural shingle", "Metal"]


def _bytes(name: str) -> bytes:
    with open(os.path.join(SAMPLE_DIR, name + ".jpg"), "rb") as fh:
        return fh.read()


def seed(db: Session, org, now: datetime) -> Dict[str, int]:
    from app.services import wholesale_files as files
    rnd = random.Random(20261021)
    if not os.path.isdir(SAMPLE_DIR):
        return {"inspections": 0, "photos": 0}
    leads = {l.id: l for l in db.query(Lead).filter(Lead.organization_id == org.id, Lead.is_test.is_(True)).all()}
    have = {r for (r,) in db.query(RoofingInspection.lead_id).filter(RoofingInspection.organization_id == org.id).all()}
    opps = {o.lead_id: o for o in db.query(RoofingOpportunity).filter(RoofingOpportunity.organization_id == org.id).all()}
    made = photos = 0
    drafts = db.query(RoofingInspection).filter(RoofingInspection.organization_id == org.id,
                                                RoofingInspection.status == "in_progress").count()
    # Make the sample calendar coherent with the story: a homeowner already
    # past the inspection can't have it in the future, and a sales team always
    # has a few visits coming up. Only this demo workspace's sample bookings move.
    past_booked: List[BookingLink] = []
    for lid, lead in sorted(leads.items(), key=lambda kv: kv[1].email or ""):
        if lid in have:
            continue
        o = opps.get(lid)
        bl = (db.query(BookingLink).filter(BookingLink.lead_id == lid, BookingLink.status.in_(("booked", "confirmed")))
              .order_by(BookingLink.booked_time.desc()).first())
        if not o or not bl or not bl.booked_time:
            continue
        if o.stage in (C.INSPECTED, C.ESTIMATE_SENT, C.WON, C.LOST) and bl.booked_time > now:
            bl.booked_time = (o.stage_entered_at or now) - timedelta(days=1)
            bl.booked_time = bl.booked_time.replace(hour=rnd.choice([9, 10, 13, 15]), minute=0, second=0, microsecond=0)
        elif o.stage == C.BOOKED and bl.booked_time < now:
            past_booked.append(bl)
    for i, bl in enumerate(past_booked[max(0, 5 - drafts):] if not have else []):           # 2 drafts + 3 unfinished stay; the rest are coming up
        day = now + timedelta(days=1 + i % 6)
        bl.booked_time = day.replace(hour=[9, 10, 13, 15][i % 4], minute=0, second=0, microsecond=0)
    db.flush()
    n = db.query(RoofingInspection).filter(RoofingInspection.organization_id == org.id,
                                           RoofingInspection.report_number.isnot(None)).count()
    cache: Dict[str, bytes] = {}
    for lid, lead in sorted(leads.items(), key=lambda kv: kv[1].email or ""):
        if lid in have:
            continue
        opp = opps.get(lid)
        b = (db.query(BookingLink).filter(BookingLink.lead_id == lid, BookingLink.status.in_(("booked", "confirmed")))
             .order_by(BookingLink.booked_time.desc()).first())
        stage = opp.stage if opp else None
        documented = stage in (C.INSPECTED, C.ESTIMATE_SENT, C.WON, C.LOST)
        draft = (not documented and b is not None and b.booked_time and b.booked_time < now - timedelta(hours=2)
                 and stage == C.BOOKED and drafts < 2)
        if not (documented or draft):
            continue
        issue = (C.lead_fields(lead).get("roof_issue") or "").lower()
        kind = ("hail" if "hail" in issue or "granule" in issue else "leak" if ("leak" in issue or "stain" in issue)
                else "wind" if ("wind" in issue or "missing" in issue or "flashing" in issue) else
                "age" if ("old" in issue or "sagging" in issue) else rnd.choice(["hail", "wind", "leak"]))
        if stage == C.LOST and kind != "age":
            kind = "age"
        f = FINDINGS[kind]
        when = (b.booked_time if b and b.booked_time else (opp.stage_entered_at if opp and opp.stage_entered_at else now)
                - timedelta(days=1))
        ins = RoofingInspection(organization_id=org.id, lead_id=lid, opportunity_id=opp.id if opp else None,
                                booking_link_id=b.id if b else None, inspector_user_id=(b.user_id if b else None) or lead.assigned_to_id,
                                scheduled_at=when, inspection_type=(b.appt_label if b else None) or "Roof Inspection",
                                started_at=when, roof_type=ROOF[rnd.randint(0, 3)],
                                roof_age_years=C.lead_fields(lead).get("roof_age") if isinstance(C.lead_fields(lead).get("roof_age"), int) else 15,
                                stories=rnd.choice([1, 1, 2]), roof_condition=f["condition"],
                                damage=C.jdump(f["damage"]), severity=f["severity"], affected_areas=C.jdump(f["areas"]),
                                notes=f["notes"], recommendation=f["rec"], recommendation_notes=f["scope"],
                                insurance_claim=f["claim"])
        pics = SETS[kind]
        if draft:
            drafts += 1
            ins.status = "in_progress"
            ins.notes = None                         # mid-inspection: notes and recommendation not written yet
            ins.recommendation = None
            ins.recommendation_notes = None
            pics = pics[:2]
        else:
            n += 1
            ins.status = "completed"
            ins.report_number = "R-%04d" % (1000 + n)
            ins.completed_at = when + timedelta(minutes=55)
            ins.completed_by_id = ins.inspector_user_id
            nxt = {"replacement": "prepare_estimate", "repair": "prepare_estimate", "monitor": "schedule_follow_up"}
            ins.next_action = ("follow_up_estimate" if stage == C.ESTIMATE_SENT else None if stage in (C.WON, C.LOST)
                               else nxt.get(f["rec"]))
            if stage == C.LOST:
                ins.recommendation = "none" if rnd.random() < .5 else f["rec"]
                ins.handoff = C.jdump({"action": "close_no_work", "label": "Close - no work needed",
                                       "at": (ins.completed_at + timedelta(days=1)).isoformat()})
            elif stage in (C.ESTIMATE_SENT, C.WON):
                ins.handoff = C.jdump({"action": "prepare_estimate", "label": "Prepare estimate", "estimate_sent": True,
                                       "estimate_amount": opp.estimate_amount if opp else None,
                                       "at": (ins.completed_at + timedelta(hours=20)).isoformat()})
        db.add(ins)
        db.flush()
        for i, (fname, label, caption) in enumerate(pics):
            data = cache.get(fname) or cache.setdefault(fname, _bytes(fname))
            stored = files.put(org.id, "roofing_inspection", data, "image/jpeg", db=db)
            db.add(RoofingInspectionPhoto(organization_id=org.id, inspection_id=ins.id, lead_id=lid, label=label,
                                          caption=caption, is_sample=True, position=i,
                                          original_filename="sample-%s.jpg" % fname,
                                          created_by_id=ins.inspector_user_id, **stored))
            photos += 1
        made += 1
    db.commit()
    return {"inspections": made, "photos": photos}
