"""RESTLAND BOOKING + 14-DAY CAMPAIGN SETUP - one recorded, reversible run
(Issue #507, owner decisions Nov 7 2026).

What one `configure` run does, each change a workspace_reset_items row with
its before value (rollback restores exactly those):

  1. Booking account: the program's booking user = the existing account the
     owner confirmed, passed in by id (never created; its email, password,
     access and calendar grant untouched; it must hold a seat in this
     workspace); its display name -> "Mike Simmons".
  2. Appointment rules (booking_policy.RESTLAND_POLICY): every day; starts
     10:00, 11:30, 1:00, 2:30, 4:00 Central; 45 minutes + a 45-minute gap;
     at Restland or at the family's home.
  3. Restland's verified street address on its location (it was blank, so
     every campaign email would have been held for having no postal address).
  4. The program cadence: 5 texts on days 1, 3, 6, 10, 14 (the 3-text draft
     template is updated in place; every earlier template stays as it is).
  5. "Restland - Family File Review - Batch 1": the approved 5 texts + 4 emails
     replace the earlier draft steps (a step a person already edited is kept).
     Still OFF.
  6. "Restland - Family File Review - Return": drafted for families coming back
     after the 30-day rest. OFF.
  7. Batch 1 revalidated against today's records: staged families still ready
     stay; any that no longer qualify are unstaged (reason kept) and replaced
     from the ready pool in the same order; the 25 are recorded as round
     "ffr-batch-1" (staged) and their program record is assigned to the
     campaign. Nobody is approved, enrolled or messaged.
  8. A READ-ONLY calendar check (one events.list against the booking
     calendar) - result in the run summary; nothing is written to it.

Sends nothing. Switches nothing on. Refuses SCI. Idempotent per run key.
"""
import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models.models import CadenceTemplate, CadenceTemplateTouch, Lead, Organization, User
from app.models.program_models import CampaignFamily, LocationProfile, OutreachProgram, ProgramSourceRecord
from app.models.reset_models import (CampaignAudienceMember, CampaignCycleMember, WorkspaceResetItem,
                                     WorkspaceResetRun)
from app.services.programs import booking_policy as BP
from app.services.programs import restland_ffr as F
from app.services.programs import workspace_reset as WR

log = logging.getLogger(__name__)

MODE = "configure"
DISPLAY_NAME = "Mike Simmons"
CYCLE_KEY = "ffr-batch-1"
ADDRESS = {"address_line1": "13005 Greenville Ave", "city": "Dallas", "state": "TX", "postal_code": "75243"}

_MODELS = {"OutreachProgram": OutreachProgram, "User": User, "CampaignFamily": CampaignFamily,
           "CadenceTemplate": CadenceTemplate, "ProgramSourceRecord": ProgramSourceRecord,
           "CampaignAudienceMember": CampaignAudienceMember}


def _location_model():
    from app.models.location_models import Location
    return Location


def _set(db, run, row, model: str, fields: Dict, reason: str, changes, key: str) -> bool:
    """Set fields on a row, recording the before values. False when nothing changed."""
    before = {k: _plain(getattr(row, k, None)) for k in fields}
    after = {k: _plain(v) for k, v in fields.items()}
    if before == after:
        return False
    db.add(WorkspaceResetItem(run_id=run.id, organization_id=run.organization_id, entity="field:" + model,
                              entity_id=row.id, action="set", before_json=json.dumps(before, default=str),
                              after_json=json.dumps(after, default=str), reason=reason))
    for k, v in fields.items():
        setattr(row, k, v)
    changes[key] += 1
    return True


def _plain(v):
    return v.isoformat() if isinstance(v, datetime) else v


def _booking_user(db: Session, org_id: str, user_id: Optional[str]) -> Optional[User]:
    """The existing account, and only if it holds a seat in THIS workspace."""
    from app.models.sales_models import Membership, SCOPE_CUSTOMER_ORG
    if not user_id:
        return None
    u = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if u is None:
        return None
    if u.organization_id == org_id:
        return u
    seat = (db.query(Membership.id).filter(Membership.user_id == u.id, Membership.scope_type == SCOPE_CUSTOMER_ORG,
                                           Membership.scope_id == org_id, Membership.is_active.is_(True)).first())
    return u if seat else None


def calendar_probe(user: Optional[User]) -> Dict:
    """One read-only events.list on the booking calendar. Never writes."""
    if user is None:
        return {"checked": False, "reason": "no booking user"}
    out = {"checked": True, "provider": "google", "calendar_id": getattr(user, "google_calendar_id", None) or "primary",
           "token_present": bool(getattr(user, "google_oauth_refresh_token_encrypted", None))}
    if not out["token_present"]:
        return dict(out, read_ok=False, error="no Google grant stored")
    try:
        from datetime import timedelta, timezone
        from app.services.calendar_service import _get_calendar_service
        svc = _get_calendar_service(user)
        now = datetime.now(timezone.utc)
        resp = svc.events().list(calendarId=out["calendar_id"], timeMin=now.isoformat(),
                                 timeMax=(now + timedelta(days=1)).isoformat(), maxResults=1,
                                 singleEvents=True).execute()
        acct = (resp.get("summary") or "").strip().lower()
        return dict(out, read_ok=True, calendar_timezone=resp.get("timeZone"),
                    account_matches_login=bool(acct) and acct == (user.email or "").lower())
    except Exception as e:                               # noqa: BLE001
        msg = str(e)
        return dict(out, read_ok=False, error=msg[:200],
                    needs_reconnect=any(k in msg.lower() for k in ("invalid_grant", "unauthorized", "revoked",
                                                                    "expired", "401")))


def _copy_steps(fam, sms: Dict, email: Dict, run, db, changes, reason) -> None:
    """Replace draft steps; keep any step a person edited in Campaigns."""
    from app.services.programs import campaigns as C
    d = C._copy_json(fam)
    meta = d.get("meta") or {}
    before = {"sms_template": fam.sms_template, "email_subject_template": fam.email_subject_template,
              "email_body_template": fam.email_body_template, "touch_copy_json": fam.touch_copy_json}
    keep_sms = {int(k.split(":")[1]) for k, v in meta.items() if k.startswith("sms:") and v.get("updated_by")
                not in (None, "system", "Owner's campaign brief", "Restland setup (#507)")}
    keep_email = {int(k.split(":")[1]) for k, v in meta.items() if k.startswith("email:") and v.get("updated_by")
                  not in (None, "system", "Owner's campaign brief", "Restland setup (#507)")}
    new_sms = {n: t for n, t in sms.items() if n not in keep_sms}
    new_email = {n: {"subject": s, "body": b} for n, (s, b) in email.items() if n not in keep_email}
    # Steps beyond the approved sequence (the 3-text draft had 5 emails) are cleared.
    for n in range(len(email) + 1, 13):
        if C.email_for(fam, n) is not None and n not in keep_email:
            new_email[n] = {"subject": "", "body": ""}
    C.set_touch_copy(fam, sms=new_sms, email=new_email, editor="Restland setup (#507)")
    d = C._copy_json(fam)
    d["sender"], d["draft"], d["sequence"] = WR.SENDER_E164, True, "5 texts + 4 emails over 14 days"
    fam.touch_copy_json = json.dumps(d)
    db.add(WorkspaceResetItem(run_id=run.id, organization_id=run.organization_id, entity="field:CampaignFamily",
                              entity_id=fam.id, action="set", before_json=json.dumps(before),
                              after_json=json.dumps({"kept_person_edits": sorted(keep_sms | keep_email)}),
                              reason=reason))
    changes["campaign_copy"] += 1


def configure(db: Session, org: Organization, run_key: str, *, probe: bool = True,
              booking_user_id: Optional[str] = None) -> Dict:
    from app.services import sms_programs
    if org is None:
        raise WR.ResetRefused("Organization not found.")
    if sms_programs.is_sci_org(db, org.id):
        raise WR.ResetRefused("Refused: this is the SCI organization.")
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org.id).first()
    if prog is None:
        raise WR.ResetRefused("This workspace has no program record.")
    done = db.query(WorkspaceResetRun).filter_by(organization_id=org.id, run_key=run_key, mode=MODE).first()
    if done is not None and done.status in ("applied", "rolled_back"):
        return {"run_id": done.id, "status": done.status, "already": True,
                "summary": json.loads(done.summary_json or "{}")}
    if db.query(WorkspaceResetRun.id).filter_by(organization_id=org.id, mode="apply", status="applied").first() \
            is None:
        raise WR.ResetRefused("Run the recorded clean-up first.")
    run = done or WorkspaceResetRun(organization_id=org.id, run_key=run_key, mode=MODE)
    db.add(run)
    db.flush()
    changes = defaultdict(int)
    notes = []

    # 1. Booking account + the name families and staff see.
    user = _booking_user(db, org.id, booking_user_id)
    if user is None:
        notes.append("booking account not given or not a member of this workspace - booking user left unset")
    else:
        _set(db, run, prog, "OutreachProgram", {"booking_user_id": user.id}, "owner-confirmed booking account",
             changes, "booking_user_set")
        _set(db, run, user, "User", {"full_name": DISPLAY_NAME}, "owner: the name shown is Mike Simmons",
             changes, "display_name_set")
    if (prog.primary_contact_name or "") != DISPLAY_NAME:
        _set(db, run, prog, "OutreachProgram", {"primary_contact_name": DISPLAY_NAME}, "name on outreach",
             changes, "contact_name_set")

    # 2. Appointment rules.
    _set(db, run, prog, "OutreachProgram", {"booking_policy_json": json.dumps(BP.RESTLAND_POLICY, sort_keys=True)},
         "every day, 5 starts, 45 min + 45 min gap, office or home", changes, "booking_policy_set")

    # 3. The location's street address (blank -> verified address only).
    Location = _location_model()
    for prof in db.query(LocationProfile).filter(LocationProfile.organization_id == org.id,
                                                 LocationProfile.is_review_bucket.is_(False)).all():
        loc = db.query(Location).filter(Location.id == prof.location_id).first()
        if loc is not None and not (loc.address_line1 or "").strip():
            fields = {k: v for k, v in ADDRESS.items() if not (getattr(loc, k, None) or "").strip()}
            fields["address_line1"] = ADDRESS["address_line1"]
            db.add(WorkspaceResetItem(run_id=run.id, organization_id=org.id, entity="field:Location", entity_id=loc.id,
                                      action="set", before_json=json.dumps({k: getattr(loc, k, None) for k in fields}),
                                      after_json=json.dumps(fields), reason="verified Restland address (postal line)"))
            for k, v in fields.items():
                setattr(loc, k, v)
            changes["location_address_set"] += 1

    # 4. The 5-text cadence on the program template.
    tpl = (db.query(CadenceTemplate).filter(CadenceTemplate.organization_id == org.id,
                                            CadenceTemplate.name.in_((WR.CADENCE_NAME, F.CADENCE_NAME))).first())
    if tpl is not None:
        touches = (db.query(CadenceTemplateTouch).filter(CadenceTemplateTouch.template_id == tpl.id)
                   .order_by(CadenceTemplateTouch.touch_number).all())
        want = {n: (d, h) for n, d, h in F.SMS_STEPS}
        have = {(t.touch_number, t.day_offset, t.send_hour, bool(t.is_active)) for t in touches}
        if have != {(n, d, h, True) for n, (d, h) in want.items()} or tpl.name != F.CADENCE_NAME:
            db.add(WorkspaceResetItem(
                run_id=run.id, organization_id=org.id, entity="cadence_touches", entity_id=tpl.id, action="replace",
                before_json=json.dumps({"name": tpl.name, "description": tpl.description,
                                        "touches": [{"n": t.touch_number, "day": t.day_offset, "hour": t.send_hour,
                                                     "channel": t.channel, "text": t.message_template,
                                                     "active": bool(t.is_active)} for t in touches]}),
                after_json=json.dumps({"name": F.CADENCE_NAME, "touches": F.SMS_STEPS}),
                reason="owner-approved 5 texts over 14 days"))
            for t in touches:
                db.delete(t)
            db.flush()
            for n, d, h in F.SMS_STEPS:
                db.add(CadenceTemplateTouch(template_id=tpl.id, touch_number=n, day_offset=d, send_hour=h,
                                            channel="sms", message_template=F.SMS[n], is_active=True))
            tpl.name, tpl.description = F.CADENCE_NAME, F.CADENCE_DESCRIPTION
            changes["cadence_touches_set"] = len(F.SMS_STEPS)
    else:
        notes.append("program cadence template not found - texts unchanged")

    # 5. Batch 1 copy (still OFF).
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key=F.CAMPAIGN_KEY).first()
    if fam is None:
        raise WR.ResetRefused("The Batch 1 campaign is missing - the clean-up run created it.")
    if fam.is_active:
        raise WR.ResetRefused("The Batch 1 campaign is switched on; nothing is changed while it runs.")
    _copy_steps(fam, F.SMS, F.EMAIL, run, db, changes, "owner-approved 5 texts + 4 emails")

    # 6. The Return campaign, drafted, OFF.
    ret = db.query(CampaignFamily).filter_by(organization_id=org.id, key=F.RETURN_KEY).first()
    if ret is None:
        ret = CampaignFamily(organization_id=org.id, key=F.RETURN_KEY, name=F.RETURN_NAME,
                             source_campaign_patterns="[]", language="en", is_active=False,
                             touch_copy_json=json.dumps({"draft": True, "sender": WR.SENDER_E164}))
        db.add(ret)
        db.flush()
        from app.services.programs import campaigns as C
        C.set_touch_copy(ret, sms=F.RETURN_SMS,
                         email={n: {"subject": s, "body": b} for n, (s, b) in F.RETURN_EMAIL.items()},
                         editor="Restland setup (#507)")
        db.add(WorkspaceResetItem(run_id=run.id, organization_id=org.id, entity="campaign_family",
                                  entity_id=ret.id, action="create", before_json="null",
                                  after_json=json.dumps({"key": F.RETURN_KEY, "is_active": False}),
                                  reason="drafted for families returning after the 30-day rest"))
        changes["return_campaign_created"] += 1

    # 7. Batch 1 revalidated (no dispatch).
    p = WR.plan(db, org)
    rows = {m.lead_id: m for m in db.query(CampaignAudienceMember)
            .filter_by(organization_id=org.id, campaign_key=F.CAMPAIGN_KEY).all()}
    staged_set = {lid for lid, m in rows.items() if m.is_staged}
    keep, dropped = [], []
    for lid in p["members"]:                  # the pool's own (record) order
        if lid in staged_set:
            (keep if p["members"][lid]["segment"] == "ready" else dropped).append(lid)
    dropped += sorted(lid for lid in staged_set if lid not in p["members"])   # left the pre-need pool
    ready_pool = [lid for lid, m in p["members"].items() if m["segment"] == "ready" and lid not in keep]
    fresh = ready_pool[:max(0, WR.BATCH_SIZE - len(keep))]
    final = keep + fresh
    for lid, m in p["members"].items():
        row = rows.get(lid)
        if row is None:
            row = CampaignAudienceMember(organization_id=org.id, campaign_key=F.CAMPAIGN_KEY, lead_id=lid,
                                         run_id=run.id)
            db.add(row)
        staged = lid in final
        status = "proposed" if staged else ("eligible" if m["segment"] == "ready" else "excluded")
        reasons = list(m["reasons"])
        if lid in dropped:
            reasons.append("unstaged on revalidation (%s)" % datetime.utcnow().strftime("%b %-d"))
        new = {"segment": m["segment"], "status": status, "is_staged": staged, "batch": 1 if staged else None,
               "reasons_json": json.dumps(reasons), "destination": m["destination"]}
        if row.id is not None and (bool(row.is_staged) != staged):
            _set(db, run, row, "CampaignAudienceMember", {"is_staged": staged, "batch": 1 if staged else None,
                                                          "status": status},
                 "Batch 1 revalidated", changes, "batch_changed")
        for k, v in new.items():
            setattr(row, k, v)
    for lid in dropped:
        if lid in rows and rows[lid].is_staged:
            rows[lid].is_staged, rows[lid].batch = False, None
    changes["batch_kept"], changes["batch_unstaged"], changes["batch_added"] = len(keep), len(dropped), len(fresh)
    db.flush()

    # The 25 as round "ffr-batch-1", and their program record on the campaign.
    from app.services.programs import rotation
    final_leads = db.query(Lead).filter(Lead.organization_id == org.id, Lead.id.in_(final or ["-"])).all()
    staged_rows = rotation.stage(db, org.id, F.CAMPAIGN_KEY, CYCLE_KEY, final_leads, run_id=run.id)
    changes["round_staged"] = len(staged_rows)
    for lid in dropped:
        cm = db.query(CampaignCycleMember).filter_by(organization_id=org.id, cycle_key=CYCLE_KEY, lead_id=lid,
                                                     status="staged").first()
        if cm is not None:
            cm.status, cm.completion_reason = "withdrawn", "unstaged on revalidation"
    for rec in (db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org.id,
                                                     ProgramSourceRecord.lead_id.in_(final or ["-"])).all()):
        if rec.approved_to_send_at is not None:
            notes.append("record %s already approved - left as it is" % rec.id)
            continue
        _set(db, run, rec, "ProgramSourceRecord", {"campaign_family": F.CAMPAIGN_KEY},
             "staged for Batch 1 (not approved)", changes, "records_assigned")

    # 8. Read-only calendar check.
    cal = calendar_probe(user) if probe else {"checked": False, "reason": "skipped"}

    run.status, run.finished_at = "applied", datetime.utcnow()
    run.summary_json = json.dumps({
        "changes": dict(changes), "notes": notes, "calendar": cal,
        "batch_1": {"kept": keep, "unstaged": dropped, "added": fresh},
        "audience_segments": p["summary"]["audience_segments"], "ready": p["summary"]["ready"],
        "applied_at": datetime.utcnow().isoformat() + "Z"}, default=str)
    db.commit()
    return {"run_id": run.id, "status": run.status, "already": False, "summary": json.loads(run.summary_json)}


def rollback(db: Session, org: Organization, run_id: str) -> Dict:
    """Undo exactly one configure run. Refused while either Restland campaign
    is on or a family in its round was approved."""
    run = db.query(WorkspaceResetRun).filter_by(id=run_id, organization_id=org.id, mode=MODE).first()
    if run is None:
        raise WR.ResetRefused("Run not found for this organization.")
    if run.status == "rolled_back":
        return {"run_id": run.id, "status": "rolled_back", "already": True}
    for key in (F.CAMPAIGN_KEY, F.RETURN_KEY):
        f = db.query(CampaignFamily).filter_by(organization_id=org.id, key=key).first()
        if f is not None and f.is_active:
            raise WR.ResetRefused("A Restland campaign is switched on; switch it off before rolling back.")
    if (db.query(ProgramSourceRecord.id).filter(ProgramSourceRecord.organization_id == org.id,
                                                ProgramSourceRecord.campaign_family.in_((F.CAMPAIGN_KEY, F.RETURN_KEY)),
                                                ProgramSourceRecord.approved_to_send_at.isnot(None)).first()):
        raise WR.ResetRefused("Families were approved into the campaign since this run; reconcile first.")
    restored = defaultdict(int)
    Location = _location_model()
    models = dict(_MODELS, Location=Location)
    for it in (db.query(WorkspaceResetItem).filter_by(run_id=run.id).filter(WorkspaceResetItem.reverted_at.is_(None))
               .order_by(WorkspaceResetItem.created_at.desc()).all()):
        before = json.loads(it.before_json or "null")
        if it.entity.startswith("field:"):
            model = models.get(it.entity.split(":", 1)[1])
            row = db.query(model).filter_by(id=it.entity_id).first() if model is not None else None
            if row is not None and isinstance(before, dict):
                for k, v in before.items():
                    setattr(row, k, v)
                restored[it.entity] += 1
        elif it.entity == "cadence_touches":
            db.query(CadenceTemplateTouch).filter_by(template_id=it.entity_id).delete(synchronize_session=False)
            t = db.query(CadenceTemplate).filter_by(id=it.entity_id).first()
            if t is not None:
                t.name, t.description = before["name"], before["description"]
            for x in before["touches"]:
                db.add(CadenceTemplateTouch(template_id=it.entity_id, touch_number=x["n"], day_offset=x["day"],
                                            send_hour=x["hour"], channel=x["channel"], message_template=x["text"],
                                            is_active=x["active"]))
            restored["cadence_touches"] += 1
        elif it.entity == "campaign_family" and it.action == "create":
            db.query(CampaignFamily).filter_by(id=it.entity_id, is_active=False).delete(synchronize_session=False)
            restored["campaign_removed"] += 1
        it.reverted_at = datetime.utcnow()
    restored["round_rows"] = (db.query(CampaignCycleMember).filter_by(organization_id=org.id, run_id=run.id)
                              .delete(synchronize_session=False))
    restored["new_audience_rows"] = (db.query(CampaignAudienceMember)
                                     .filter_by(organization_id=org.id, run_id=run.id)
                                     .delete(synchronize_session=False))
    run.status, run.rolled_back_at = "rolled_back", datetime.utcnow()
    db.commit()
    return {"run_id": run.id, "status": "rolled_back", "restored": dict(restored)}


def last_run(db: Session, org_id: str) -> Optional[WorkspaceResetRun]:
    return (db.query(WorkspaceResetRun).filter_by(organization_id=org_id, mode=MODE)
            .order_by(WorkspaceResetRun.started_at.desc()).first())
