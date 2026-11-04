"""SINGLE-LOCATION PROGRAM CUTOVER - an existing customer joins the program shell.

Issue #507: Restland Cemetery and Funeral Home (one location, ~10.6k families
already in the platform) moves onto the same operating model as SCI. SCI's
families arrive through the program importer; Restland's are ALREADY leads.
This module links them, in place, without moving or rewriting anything.

WHAT APPLY DOES (one transaction, additive only):
  1. One OutreachProgram for the org - EVERY sending control off:
     AI setter OFF, approval mode ON, daily batch 0, staff texts/emails off.
  2. One Location + LocationProfile (the business itself) plus the standard
     Location Review bucket. The facility phone comes from the org's own
     recorded phone; nothing else is invented.
  3. One campaign family, INACTIVE, linked to the org's own existing default
     cadence template (its own words; nothing new is written).
  4. One ProgramSourceRecord per existing lead, mapped to that location, with
     NO campaign assigned (identity.send_refusal then refuses every automated
     send for it) and source_lead_id = the lead's own id (stable, unique).
  5. A ProgramImportRun row whose summary lists every id created - the
     rollback deletes exactly those rows and nothing else.

WHAT IT NEVER DOES: change a lead, contact, conversation, appointment,
suppression entry, cadence state, template, user or setting; switch anything
on; send anything; touch any other organization. Leads, history and journey
progress stay exactly where they are.
"""
import json
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.location_models import Location
from app.models.models import Lead, Organization
from app.models.program_models import (
    CampaignFamily, LocationProfile, OutreachProgram, ProgramImportRun, ProgramSourceRecord,
)

RUN_FILENAME = "single-location-cutover"
FAMILY_KEY = "existing_family_follow_up"
FAMILY_NAME = "Existing Family Follow-up"
BATCH = 1000


class CutoverRefused(RuntimeError):
    pass


def _counts(db: Session, org_id: str) -> Dict:
    leads = db.query(Lead.id).filter(Lead.organization_id == org_id)
    by_status = dict(db.query(Lead.status, func.count(Lead.id))
                     .filter(Lead.organization_id == org_id).group_by(Lead.status).all())
    out = {
        "leads": leads.count(),
        "leads_by_status": {str(k): v for k, v in sorted(by_status.items(), key=lambda kv: str(kv[0]))},
        "dnc_leads": by_status.get("dnc", 0),
        "programs": db.query(OutreachProgram.id).filter(OutreachProgram.organization_id == org_id).count(),
        "locations": db.query(Location.id).filter(Location.organization_id == org_id).count(),
        "location_profiles": db.query(LocationProfile.id).filter(LocationProfile.organization_id == org_id).count(),
        "source_records": db.query(ProgramSourceRecord.id).filter(ProgramSourceRecord.organization_id == org_id).count(),
        "linked_leads": (db.query(ProgramSourceRecord.lead_id)
                         .filter(ProgramSourceRecord.organization_id == org_id,
                                 ProgramSourceRecord.lead_id.isnot(None)).distinct().count()),
        "campaign_families": db.query(CampaignFamily.id).filter(CampaignFamily.organization_id == org_id).count(),
    }
    try:
        from app.models.models import SuppressionEntry as _S
        out["suppression_entries"] = db.query(_S.id).filter(_S.organization_id == org_id).count()
    except Exception:                                    # noqa: BLE001
        db.rollback()
    return out


def _default_template(db: Session, org_id: str):
    try:
        from app.models.models import CadenceTemplate, CadenceTemplateTouch
    except Exception:                                    # noqa: BLE001
        return None, None
    tpl = (db.query(CadenceTemplate)
           .filter(CadenceTemplate.organization_id == org_id, CadenceTemplate.is_active.is_(True))
           .order_by(CadenceTemplate.is_default.desc(), CadenceTemplate.created_at).first())
    if tpl is None:
        return None, None
    first_sms = (db.query(CadenceTemplateTouch)
                 .filter(CadenceTemplateTouch.template_id == tpl.id,
                         CadenceTemplateTouch.channel.in_(("sms", "SMS")),
                         CadenceTemplateTouch.is_active.isnot(False))
                 .order_by(CadenceTemplateTouch.touch_number).first())
    return tpl, (getattr(first_sms, "message_template", None) if first_sms else None)


def preflight(db: Session, org: Organization) -> Dict:
    from app.services import sms_programs
    problems: List[str] = []
    if org is None:
        raise CutoverRefused("Organization not found.")
    if sms_programs.is_sci_org(db, org.id):
        problems.append("This is the SCI organization; it already runs the program shell.")
    if org.is_active is False:
        problems.append("Organization is not active.")
    existing = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org.id).first()
    if existing is not None:
        problems.append("Organization already has an outreach program (%s)." % existing.id)
    locs = db.query(Location.id).filter(Location.organization_id == org.id).count()
    if locs:
        problems.append("Organization already has %d location(s); this cutover is for a workspace with none." % locs)
    tpl, sms = _default_template(db, org.id)
    return {"organization_id": org.id, "organization_name": org.name, "problems": problems,
            "baseline": _counts(db, org.id),
            "template": {"id": getattr(tpl, "id", None), "name": getattr(tpl, "name", None),
                         "first_sms_present": bool(sms)}}


def apply(db: Session, org: Organization, *, actor_id: Optional[str] = None,
          hero_title: Optional[str] = None, contact_name: Optional[str] = None) -> Dict:
    """Run the cutover. Raises CutoverRefused (nothing written) on any problem."""
    pre = preflight(db, org)
    if pre["problems"]:
        raise CutoverRefused("; ".join(pre["problems"]))
    from app.services.programs import setup
    created: Dict[str, List[str]] = {"programs": [], "locations": [], "location_profiles": [],
                                     "campaign_families": [], "source_records": []}
    run = ProgramImportRun(organization_id=org.id, filename=RUN_FILENAME, dry_run=False, created_by=actor_id)
    db.add(run)
    db.flush()

    prog = OutreachProgram(
        organization_id=org.id, name=org.name, hero_title=hero_title or org.name,
        hero_subtitle="Family Service Center",
        primary_contact_name=contact_name,
        customer_channels=json.dumps(["sms", "email"]),
        reply_instructions_sms=setup.DEFAULT_REPLY_SMS, reply_instructions_email=setup.DEFAULT_REPLY_EMAIL,
        require_location_to_send=True, staff_sms_alerts_enabled=False,
        ai_setter_mode="off", require_send_approval=True, daily_batch_count=0, is_active=True)
    db.add(prog)
    db.flush()
    created["programs"].append(prog.id)

    loc = Location(organization_id=org.id, name=org.name, slug=_slug(org.name), is_primary=True, is_active=True)
    review = Location(organization_id=org.id, name=setup.REVIEW_BUCKET_NAME, slug=_slug(setup.REVIEW_BUCKET_NAME),
                      is_primary=False, is_active=True)
    db.add_all([loc, review])
    db.flush()
    created["locations"] += [loc.id, review.id]
    prof = LocationProfile(organization_id=org.id, location_id=loc.id, official_name=org.name,
                           source_names=json.dumps([org.name]), is_review_bucket=False,
                           facility_phone=getattr(org, "org_phone", None))
    bucket = LocationProfile(organization_id=org.id, location_id=review.id, official_name=setup.REVIEW_BUCKET_NAME,
                             source_names="[]", is_review_bucket=True)
    db.add_all([prof, bucket])
    db.flush()
    created["location_profiles"] += [prof.id, bucket.id]

    tpl, first_sms = _default_template(db, org.id)
    fam = CampaignFamily(organization_id=org.id, key=FAMILY_KEY, name=FAMILY_NAME,
                         source_campaign_patterns="[]", language="en",
                         cadence_template_id=getattr(tpl, "id", None), sms_template=first_sms,
                         is_active=False)
    db.add(fam)
    db.flush()
    created["campaign_families"].append(fam.id)

    now = datetime.utcnow()
    q = (db.query(Lead).filter(Lead.organization_id == org.id).order_by(Lead.created_at, Lead.id))
    n = 0
    for lead in q.yield_per(BATCH):
        n += 1
        rec = ProgramSourceRecord(
            organization_id=org.id, source_lead_id=lead.id, import_run_id=run.id, row_number=n,
            raw_json=json.dumps({"origin": "existing_lead", "lead_id": lead.id, "linked_at": now.isoformat() + "Z"}),
            first_name=lead.first_name, last_name=lead.last_name, email=lead.email, phone=lead.phone,
            source_status=lead.status, source_campaign=None, source_channel=getattr(lead, "source", None),
            source_location_name=org.name, location_id=loc.id, location_status="mapped",
            campaign_family=None, link_status="unique", needs_data_review=False, lead_id=lead.id)
        db.add(rec)
        if n % BATCH == 0:
            db.flush()
    db.flush()
    created["source_records_count"] = n
    after = _counts(db, org.id)
    run.summary_json = json.dumps({"kind": RUN_FILENAME, "created": created, "baseline": pre["baseline"],
                                   "after": after, "applied_at": now.isoformat() + "Z"})
    db.commit()
    return {"run_id": run.id, "created": {k: (len(v) if isinstance(v, list) else v) for k, v in created.items()},
            "baseline": pre["baseline"], "after": after, "reconciliation": reconcile(db, org)}


def reconcile(db: Session, org: Organization) -> Dict:
    """Every lead linked exactly once, to a real location, nothing lost."""
    c = _counts(db, org.id)
    dup = (db.query(ProgramSourceRecord.lead_id)
           .filter(ProgramSourceRecord.organization_id == org.id, ProgramSourceRecord.lead_id.isnot(None))
           .group_by(ProgramSourceRecord.lead_id).having(func.count(ProgramSourceRecord.id) > 1).count())
    orphan = (db.query(ProgramSourceRecord.id)
              .outerjoin(Lead, Lead.id == ProgramSourceRecord.lead_id)
              .filter(ProgramSourceRecord.organization_id == org.id, Lead.id.is_(None)).count())
    cross = (db.query(ProgramSourceRecord.id).join(Lead, Lead.id == ProgramSourceRecord.lead_id)
             .filter(ProgramSourceRecord.organization_id == org.id, Lead.organization_id != org.id).count())
    on_family = (db.query(ProgramSourceRecord.id)
                 .filter(ProgramSourceRecord.organization_id == org.id,
                         ProgramSourceRecord.campaign_family.isnot(None)).count())
    active_fams = (db.query(CampaignFamily.id)
                   .filter(CampaignFamily.organization_id == org.id, CampaignFamily.is_active.is_(True)).count())
    ok = (c["linked_leads"] == c["leads"] and not dup and not orphan and not cross)
    return {"ok": ok, "leads": c["leads"], "linked_leads": c["linked_leads"], "duplicate_links": dup,
            "orphan_records": orphan, "cross_tenant_links": cross,
            "records_with_campaign": on_family, "active_campaign_families": active_fams}


def rollback(db: Session, org: Organization, run_id: str) -> Dict:
    """Delete exactly the rows the given cutover run created. Refuses if any
    of them has since been worked (approved, held, assigned a campaign, a
    journey started) - that is post-cutover activity a person must reconcile."""
    run = db.query(ProgramImportRun).filter(ProgramImportRun.id == run_id,
                                            ProgramImportRun.organization_id == org.id).first()
    if run is None or run.filename != RUN_FILENAME:
        raise CutoverRefused("Cutover run not found for this organization.")
    summary = json.loads(run.summary_json or "{}")
    created = summary.get("created") or {}
    recs = db.query(ProgramSourceRecord).filter(ProgramSourceRecord.import_run_id == run.id)
    worked = recs.filter((ProgramSourceRecord.approved_to_send_at.isnot(None))
                         | (ProgramSourceRecord.on_hold.is_(True))
                         | (ProgramSourceRecord.campaign_family.isnot(None))
                         | (ProgramSourceRecord.journey_started_at.isnot(None))).count()
    if worked:
        raise CutoverRefused("%d linked records have been worked since the cutover; reconcile before rollback." % worked)
    fam_on = db.query(CampaignFamily.id).filter(CampaignFamily.id.in_(created.get("campaign_families") or ["-"]),
                                                CampaignFamily.is_active.is_(True)).count()
    if fam_on:
        raise CutoverRefused("The cutover campaign has been switched on; switch it off before rollback.")
    n = recs.delete(synchronize_session=False)
    db.query(CampaignFamily).filter(CampaignFamily.id.in_(created.get("campaign_families") or ["-"])).delete(
        synchronize_session=False)
    db.query(LocationProfile).filter(LocationProfile.id.in_(created.get("location_profiles") or ["-"])).delete(
        synchronize_session=False)
    db.query(OutreachProgram).filter(OutreachProgram.id.in_(created.get("programs") or ["-"])).delete(
        synchronize_session=False)
    db.flush()
    # A location can be referenced by leads/other rows created after cutover;
    # those stay, and so does the location they point at.
    left = []
    for lid in created.get("locations") or []:
        try:
            with db.begin_nested():
                db.query(Location).filter(Location.id == lid).delete(synchronize_session=False)
        except Exception:                                # noqa: BLE001
            left.append(lid)
    run.summary_json = json.dumps({**summary, "rolled_back_at": datetime.utcnow().isoformat() + "Z",
                                   "locations_kept": left})
    db.commit()
    return {"deleted_source_records": n, "locations_kept": left, "after": _counts(db, org.id)}


def _slug(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:80]
