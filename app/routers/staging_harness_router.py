"""SCI STAGING TEST HARNESS - owner-only, OFF unless STAGING_TEST_HARNESS=on.

Exists so the SCI email loop can be proven end to end on an ISOLATED staging
stack (its own database) with the real Resend sender and the real
support@evosyspro.live mailbox - without a shell on the staging host.

    POST /god/staging/sci/seed          SCI org, 39 locations, aliases, program
                                        settings, ONE approved test contact (and,
                                        only with simulation_contacts=true,
                                        synthetic example.com contacts, never emailed)
    POST /god/staging/sci/send-test     the ONE real test email (refuses a second)
    POST /god/staging/sci/poll          read the reply mailbox now
    GET  /god/staging/sci/status        the whole chain for the test contact
    POST /god/staging/sci/simulate/{c}  matrix cases that must not touch real mail
    GET  /god/staging/sci/pool-numbers  the six regional pools: provisioned rows + webhook targets
    POST /god/staging/sci/pool-numbers  record purchased pool numbers (dry run unless apply=true)

In production STAGING_TEST_HARNESS is never set and every route answers 404.
Nothing here can email anyone but the test contact; simulations use
example.com contacts and fake Graph messages, and patch the provider where a
provider failure is the thing under test.
"""
import base64
import csv
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timedelta
from typing import List, Optional
from unittest import mock

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import CadenceState, EmailMessage, Lead, Organization, Platform, User
from app.models.program_models import (
    LocationProfile, OutreachProgram, ProgramAlert, ProgramEmailTouch, ProgramMailFiling,
    ProgramResponse, ProgramSourceRecord, ProgramUnmatchedReply,
)

router = APIRouter(prefix="/god/staging/sci", tags=["staging-harness"])

ORG_NAME = "Service Corporation International"
TEST_SOURCE_ID = "STAGING-TEST-1"
TEST_LOCATION = "Eastern Gate Memorial Gardens"
SUBJECT_MARK = "[SCI staging test]"
_ALIAS_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                          "scripts", "sci_location_aliases.csv")
SIM_CONTACTS = [  # synthetic, never emailed
    ("SIM-ACTIVE", "Sam", "Active", "sim.active@example.com", TEST_LOCATION),
    ("SIM-OPTOUT", "Olive", "Optout", "sim.optout@example.com", TEST_LOCATION),
    ("SIM-WRONG", "Wendy", "Wrong", "sim.wrong@example.com", "Striffler-Hamby Mortuary"),
    ("SIM-NOLOC", "Nora", "Noloc", "sim.noloc@example.com", None),
    ("SIM-HELD", "Hal", "Held", "sim.held@example.com", TEST_LOCATION),
    ("SIM-CAMPUS", "Cami", "Campus", "sim.campus@example.com", "Eastern Gate Memorial Funeral Home"),
]
# Fictional 555-01xx numbers (reserved for fiction; never routable).
SIM_PHONES = {"SIM-CAMPUS": "12055550101", "SIM-ACTIVE": "12055550102"}
SIM_CAMPUS_NUMBER = "+18505550100"     # a staging-only campus number record, never a real Twilio number


def _on():
    if (os.environ.get("STAGING_TEST_HARNESS") or "").strip().lower() not in ("1", "on", "true", "yes"):
        raise HTTPException(status_code=404, detail="Not Found")


def _org(db: Session) -> Organization:
    org = db.query(Organization).filter(Organization.name == ORG_NAME).first()
    if org is None:
        raise HTTPException(status_code=409, detail="Seed first: POST /god/staging/sci/seed")
    return org


def _prog(db: Session, org: Organization) -> OutreachProgram:
    return db.query(OutreachProgram).filter(OutreachProgram.organization_id == org.id).one()


def _test_lead(db: Session, org: Organization) -> Lead:
    rec = (db.query(ProgramSourceRecord)
           .filter(ProgramSourceRecord.organization_id == org.id,
                   ProgramSourceRecord.source_lead_id == TEST_SOURCE_ID).first())
    if rec is None or not rec.lead_id:
        raise HTTPException(status_code=409, detail="No test contact - seed first.")
    return db.query(Lead).filter(Lead.id == rec.lead_id).one()


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


class SeedIn(BaseModel):
    test_email: str
    mgmt_sms: Optional[str] = None
    mgmt_email: Optional[str] = None
    mail_folder: Optional[str] = "Inbox/Customers Folder/SCI"
    # Off by default: the live-loop proof seeds the approved test contact ONLY.
    # The simulation matrix needs the synthetic example.com contacts.
    simulation_contacts: bool = False


@router.post("/seed")
def seed(body: SeedIn, db: Session = Depends(get_db), god: User = Depends(require_god)):
    _on()
    from app.services import customer_provisioning as cp
    from app.services.programs import aliases, setup
    from app.routers.program_router import _clean_email, _clean_phone
    test_email = _clean_email(body.test_email, "test_email")
    plat = db.query(Platform).filter(Platform.slug == "evosyspro").first()
    if plat is None:
        raise HTTPException(status_code=409, detail="Platform evosyspro missing (startup seeds it).")
    org = db.query(Organization).filter(Organization.name == ORG_NAME).first()
    if org is None:
        org, _ = cp.create_customer(db, god, name=ORG_NAME, platform_id=plat.id, industry="funeral",
                                    timezone="America/Chicago")
        org.enabled_features = json.dumps(["leads", "reports", "users", "branding_settings",
                                           "audit_log", "imports", "email", "sms"])
        org.brand_name = ORG_NAME
    rows = list(csv.DictReader(open(_ALIAS_CSV, encoding="utf-8")))
    names = [r["Location"] for r in rows]
    prog = setup.ensure_program(db, org, name=ORG_NAME, primary_contact_name="Kerry Allan",
                                primary_contact_title="Head of Sales", hero_title=ORG_NAME,
                                hero_subtitle="Family Service Lead & Communication Center")
    profiles = setup.ensure_locations(db, org, god, names)
    setup.ensure_campaign_families(db, org)            # all OFF
    db.flush()
    assigned = aliases.assign(db, prog)
    mismatch = {r["Location"]: (assigned.get(r["Location"]), r["Alias"]) for r in rows
                if assigned.get(r["Location"]) != r["Alias"]}
    if body.mgmt_sms or body.mgmt_email:
        prog.management_recipients = json.dumps([{"name": "Management", "role": "management",
                                                  "phone": _clean_phone(body.mgmt_sms, "mgmt_sms"),
                                                  "email": _clean_email(body.mgmt_email, "mgmt_email")}])
    prog.staff_sms_alerts_enabled = True
    prog.mailbox_folder_path = (body.mail_folder or "").strip().strip("/") or None
    by_name = {p.official_name: p for p in profiles.values()}
    from app.services.programs import campuses as _campuses
    campus_result = _campuses.assign(db, org.id, _campuses.load_grouping())
    # Mike verified easterngategardens@ receives (2026-10-05 21:03 CT) - the
    # rest switch on as mail to them is seen arriving.
    aliases.mark_verified(by_name[TEST_LOCATION])

    def contact(src, first, last, email, loc_name, note):
        rec = (db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org.id,
                                                    ProgramSourceRecord.source_lead_id == src).first())
        if rec is not None and rec.lead_id:
            return rec
        prof = by_name.get(loc_name) if loc_name else None
        review = next((p for p in profiles.values() if p.is_review_bucket), None)
        # The plan's lead limit applies here like on every lead-creating path.
        from app.services import plan_limits
        plan_limits.require_capacity(db, org, plan_limits.LIMIT_LEADS, adding=1, actor=god)
        lead = Lead(organization_id=org.id, first_name=first, last_name=last, email=email,
                    phone=SIM_PHONES.get(src), status="new", is_test=True, test_note=note,
                    assigned_to_id=god.id)
        db.add(lead)
        db.flush()
        raw = {"Lead ID": src, "First Name": first, "Last Name": last, "Email": email,
               "Location Friendly Name": loc_name or ""}
        rec = rec or ProgramSourceRecord(organization_id=org.id, source_lead_id=src, row_number=0,
                                         raw_json=json.dumps(raw), first_name=first, last_name=last,
                                         email=email, source_status="Qualified",
                                         source_campaign="Direct Mail> Veteran> Veteran Planning Guide",
                                         source_location_name=loc_name)
        rec.location_id = (prof or review).location_id if (prof or review) else None
        rec.location_status = "mapped" if prof else "location_review"
        rec.campaign_family, rec.link_status, rec.contact_master_key = "veteran_planning_guide", "unique", src
        rec.lead_id = lead.id
        db.add(rec)
        db.add(CadenceState(lead_id=lead.id, status="active"))
        db.flush()
        return rec

    contact(TEST_SOURCE_ID, "Mike", "Simmons", test_email, TEST_LOCATION,
            "STAGING approved test contact - the only address ever emailed")
    if body.simulation_contacts:
        for src, first, last, email, loc in SIM_CONTACTS:
            contact(src, first, last, email, loc, "STAGING simulation contact - never emailed")
        held = (db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org.id,
                                                     ProgramSourceRecord.source_lead_id == "SIM-HELD").one())
        held.on_hold, held.hold_reason, held.held_at = True, "STAGING hold test", datetime.utcnow()
    db.commit()
    return {"organization_id": org.id, "locations": len(names), "aliases_assigned": len(assigned),
            "alias_mismatches": mismatch, "test_contact": test_email,
            "simulation_contacts": [c[3] for c in SIM_CONTACTS] if body.simulation_contacts else [],
            "contacts_in_workspace": db.query(Lead).filter(Lead.organization_id == org.id).count(),
            "mail_folder": prog.mailbox_folder_path,
            "campuses": campus_result,
            "management": json.loads(prog.management_recipients or "[]")}


class SendIn(BaseModel):
    again: bool = False


@router.post("/send-test")
def send_test(body: SendIn, db: Session = Depends(get_db), god: User = Depends(require_god)):
    """The ONE real email: to the approved test contact only, as a person's
    MANUAL send (test contacts never receive automated mail)."""
    _on()
    from app.services.email_service import send_email_to_lead
    from app.services.programs import email_touches as et, identity
    from app.services.public_identity import sending_identity_for_org
    org = _org(db)
    prog = _prog(db, org)
    lead = _test_lead(db, org)
    # Only an email the provider ACCEPTED counts: a failed attempt may be re-run.
    prior = (db.query(EmailMessage).filter(EmailMessage.lead_id == lead.id,
                                           EmailMessage.subject.like("%" + SUBJECT_MARK + "%"),
                                           EmailMessage.status != "failed").all())
    if prior and not body.again:
        raise HTTPException(status_code=409, detail="A test email was already sent (%s). Duplicate refused."
                            % ", ".join("%s %s" % (m.status, _iso(m.sent_at)) for m in prior))
    from app.models.program_models import CampaignFamily
    fam = (db.query(CampaignFamily).filter(CampaignFamily.organization_id == org.id,
                                           CampaignFamily.key == "veteran_planning_guide").one())
    prof = identity.location_profile_for_lead(db, lead)
    r = et.render_touch(db, prog, fam, prof, lead.first_name, "first")
    subject = "%s %s" % (r["subject"], SUBJECT_MARK)
    ident = identity.apply_email_identity(db, lead, sending_identity_for_org(db, org.id))
    try:
        msg = send_email_to_lead(db, god, lead, subject=subject, body_html=r["body_html"],
                                 send_source="manual", sent_by_user_id=god.id, raise_on_provider_failure=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Refused before the provider: %s" % exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail="The email provider did not accept it: %s" % exc)
    return {"sent": msg.status, "email_message_id": msg.id, "provider_message_id": msg.provider_message_id,
            "to": lead.email, "subject": subject, "from_name": getattr(ident, "from_name", None),
            "from_email": getattr(ident, "from_email", None), "reply_to": getattr(ident, "reply_to_email", None)}


@router.post("/poll")
def poll(db: Session = Depends(get_db), god: User = Depends(require_god)):
    _on()
    from app.services.inbound_mailbox_service import poll_all_mailboxes
    res = poll_all_mailboxes(db)
    return {k: v for k, v in res.items()}


@router.get("/status")
def status(db: Session = Depends(get_db), god: User = Depends(require_god)):
    """The whole chain for the test contact, as the database sees it."""
    _on()
    from app.models.inbound_mailbox_models import InboundMailbox, InboundMailboxMessage
    from app.models.models import Notification, Reply
    org = _org(db)
    prog = _prog(db, org)
    lead = _test_lead(db, org)
    prof = (db.query(LocationProfile).filter(LocationProfile.organization_id == org.id,
                                             LocationProfile.official_name == TEST_LOCATION).one())
    resp = (db.query(ProgramResponse).filter(ProgramResponse.lead_id == lead.id)
            .order_by(ProgramResponse.created_at.desc()).first())
    inbound = (db.query(InboundMailboxMessage).filter(InboundMailboxMessage.lead_id == lead.id)
               .order_by(InboundMailboxMessage.created_at.desc()).all())
    filings = (db.query(ProgramMailFiling).filter(ProgramMailFiling.mailbox_message_id.in_([i.id for i in inbound]))
               .all()) if inbound else []
    cad = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
    alerts = (db.query(ProgramAlert).filter(ProgramAlert.response_id == resp.id).all()) if resp else []
    boxes = db.query(InboundMailbox).all()
    return {
        "alias": {"address": prof.email_alias, "location": prof.official_name,
                  "seen_receiving_at": _iso(prof.alias_verified_at), "last_seen_at": _iso(prof.alias_last_seen_at),
                  "workspace": org.id},
        "mailboxes": [{"address": b.address, "status": b.last_status, "last_poll": _iso(b.last_polled_at),
                       "error": b.last_error} for b in boxes],
        "outbound": [{"subject": m.subject, "status": m.status, "sent_at": _iso(m.sent_at),
                      "provider_message_id": m.provider_message_id}
                     for m in db.query(EmailMessage).filter(EmailMessage.lead_id == lead.id)],
        "inbound": [{"from": i.from_address, "subject": i.subject, "outcome": i.outcome, "detail": i.detail,
                     "workspace": i.organization_id, "received_at": _iso(i.received_at)} for i in inbound],
        "replies_on_conversation": db.query(Reply).filter(Reply.lead_id == lead.id).count(),
        "response": None if resp is None else {
            "class": resp.response_class, "intents": json.loads(resp.intents or "[]"),
            "via_alias": resp.reply_to_alias, "location_id": resp.location_id,
            "location_matches": resp.location_id == prof.location_id,
            "sla_due_at": _iso(resp.sla_due_at), "received_at": _iso(resp.received_at),
            "cadence_paused": resp.cadence_paused, "summary": resp.summary,
            "suggested_reply": resp.suggested_reply, "handling_status": resp.handling_status},
        "cadence_state": str(getattr(cad.status, "value", cad.status)) if cad else None,
        "alerts": [{"audience": a.audience, "channel": a.channel, "recipient": a.recipient,
                    "delivered": a.delivered, "reason": a.reason} for a in alerts],
        "in_app_notifications": db.query(Notification).filter(Notification.lead_id == lead.id).count(),
        "outlook_filing": [{"status": f.status, "folder": f.folder_path, "attempts": f.attempts,
                            "error": f.last_error, "filed_at": _iso(f.filed_at)} for f in filings],
        "campaign_touches": db.query(ProgramEmailTouch).filter(ProgramEmailTouch.organization_id == org.id).count(),
        "active_campaigns": [f.key for f in db.query(__import__("app.models.program_models", fromlist=["CampaignFamily"]).CampaignFamily)
                             .filter_by(organization_id=org.id, is_active=True)],
        "contacts_in_workspace": [l.email for l in db.query(Lead).filter(Lead.organization_id == org.id)],
        "held_records": db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org.id,
                                                             ProgramSourceRecord.on_hold.is_(True)).count(),
        "unmatched_open": db.query(ProgramUnmatchedReply).filter(ProgramUnmatchedReply.organization_id == org.id,
                                                                 ProgramUnmatchedReply.status == "open").count(),
    }


class MarkIn(BaseModel):
    state: str = "responded"


@router.get("/manager-provisioning")
def manager_provisioning(db: Session = Depends(get_db), god: User = Depends(require_god)):
    """God-only, staging-only, read-only. Booleans/counts; no PII or secrets.

    Gated on APP_ENV=staging (not STAGING_TEST_HARNESS) so it answers 404 in
    production and demo.
    """
    from app.services import environment, sci_staging_bootstrap
    if environment.current() != environment.ENV_STAGING:
        raise HTTPException(status_code=404, detail="Not Found")
    return sci_staging_bootstrap.provisioning_status(db)


@router.post("/mark-test-response")
def mark_test_response(body: MarkIn, db: Session = Depends(get_db), god: User = Depends(require_god)):
    """Mark the live test reply handled (stops its SLA escalation), exactly as
    Kerry pressing "I responded" would. Test contact only."""
    _on()
    from app.services.programs import responses as R
    org = _org(db)
    lead = _test_lead(db, org)
    resp = (db.query(ProgramResponse).filter(ProgramResponse.lead_id == lead.id)
            .order_by(ProgramResponse.created_at.desc()).first())
    if resp is None:
        raise HTTPException(status_code=404, detail="The test contact has no response yet.")
    try:
        R.mark(db, resp, body.state, god)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"id": resp.id, "status": resp.handling_status, "sla_alert_count": resp.sla_alert_count,
            "opened_at": _iso(resp.opened_at), "responded_at": _iso(resp.responded_at)}


@router.post("/remove-simulation-contacts")
def remove_simulation_contacts(db: Session = Depends(get_db), god: User = Depends(require_god)):
    """Delete the synthetic example.com contacts (and everything recorded about
    them) after a simulation run. Touches nothing else: the approved test
    contact, real records and held records are never matched here."""
    _on()
    from app.models.inbound_mailbox_models import InboundMailboxMessage
    from app.models.models import Notification, Reply
    org = _org(db)
    sims = [c[0] for c in SIM_CONTACTS]
    recs = (db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org.id,
                                                 ProgramSourceRecord.source_lead_id.in_(sims)).all())
    lead_ids = [r.lead_id for r in recs if r.lead_id]
    leads = (db.query(Lead).filter(Lead.organization_id == org.id, Lead.id.in_(lead_ids or ["-"]),
                                   Lead.email.like("%@example.com"), Lead.is_test.is_(True)).all())
    ids = [l.id for l in leads]
    removed = {"contacts": len(ids)}
    if ids:
        resp_ids = [r.id for r in db.query(ProgramResponse.id).filter(ProgramResponse.lead_id.in_(ids))]
        removed["alerts"] = (db.query(ProgramAlert).filter(ProgramAlert.response_id.in_(resp_ids or ["-"]))
                             .delete(synchronize_session=False))
        removed["responses"] = db.query(ProgramResponse).filter(ProgramResponse.lead_id.in_(ids)).delete(
            synchronize_session=False)
        removed["touches"] = db.query(ProgramEmailTouch).filter(ProgramEmailTouch.lead_id.in_(ids)).delete(
            synchronize_session=False)
        mm = [m.id for m in db.query(InboundMailboxMessage.id).filter(InboundMailboxMessage.lead_id.in_(ids))]
        db.query(ProgramMailFiling).filter(ProgramMailFiling.mailbox_message_id.in_(mm or ["-"])).delete(
            synchronize_session=False)
        db.query(InboundMailboxMessage).filter(InboundMailboxMessage.id.in_(mm or ["-"])).delete(
            synchronize_session=False)
        for model in (Notification, Reply, EmailMessage, CadenceState):
            db.query(model).filter(model.lead_id.in_(ids)).delete(synchronize_session=False)
        for r in recs:
            db.delete(r)
        db.flush()
        for l in leads:
            db.delete(l)
    from app.models.telephony_models import PhoneNumber
    removed["sim_numbers"] = db.query(PhoneNumber).filter(PhoneNumber.e164 == SIM_CAMPUS_NUMBER).delete(
        synchronize_session=False)
    removed["sms_unmatched"] = (db.query(ProgramUnmatchedReply)
                                .filter(ProgramUnmatchedReply.organization_id == org.id,
                                        ProgramUnmatchedReply.from_address.like("+1205555%"))
                                .delete(synchronize_session=False))
    # simulated unknown senders kept in the unmatched queue (example.org only)
    removed["unmatched"] = (db.query(ProgramUnmatchedReply)
                            .filter(ProgramUnmatchedReply.organization_id == org.id,
                                    ProgramUnmatchedReply.from_address.like("%@example.org"))
                            .delete(synchronize_session=False))
    db.commit()
    removed["contacts_in_workspace"] = [l.email for l in db.query(Lead).filter(Lead.organization_id == org.id)]
    return removed


# ── simulations (no real mail) ──────────────────────────────────────────────

class _NoMove:
    can_write = True

    def __init__(self):
        self.calls = []

    def __call__(self, gid, path):
        self.calls.append(path)
        return True, None          # a simulated message has no real Outlook copy to move


def _graph(gid, sender, to, body, subject="Re: staging simulation"):
    return {"id": gid, "internetMessageId": "<%s@staging.sim>" % gid, "subject": subject,
            "from": {"emailAddress": {"address": sender}}, "toRecipients": [{"emailAddress": {"address": to}}],
            "ccRecipients": [], "receivedDateTime": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": {"content": body}, "bodyPreview": body[:40]}


def _box(db):
    from app.models.inbound_mailbox_models import InboundMailbox
    box = db.query(InboundMailbox).filter(InboundMailbox.address == "support@evosyspro.live").first()
    if box is None:
        raise HTTPException(status_code=409, detail="Connect support@evosyspro.live first.")
    return box


def _inbound(db, msgs):
    from app.services.inbound_mailbox_service import poll_mailbox
    box = _box(db)
    cursor, status = box.cursor_received_at, box.last_status
    mover = _NoMove()
    # Simulated replies never send a real staff alert - only the one live
    # test does. The alert decision is still recorded ("simulation - not sent").
    with mock.patch("app.services.programs.responses._deliver_staff_alert",
                    side_effect=lambda *a, **k: (False, "simulation - not sent")):
        res = poll_mailbox(db, box, fetch=lambda since: msgs, mover=mover)
    box.cursor_received_at, box.last_status = cursor, status      # simulations never move the real cursor
    db.commit()
    return res, mover.calls


def _resp_for(db, email):
    lead = db.query(Lead).filter(Lead.email == email).first()
    r = (db.query(ProgramResponse).filter(ProgramResponse.lead_id == lead.id)
         .order_by(ProgramResponse.created_at.desc()).first()) if lead else None
    return None if r is None else {"class": r.response_class, "intents": json.loads(r.intents or "[]"),
                                   "summary": r.summary, "location_id": r.location_id}


@router.post("/simulate/{case}")
def simulate(case: str, db: Session = Depends(get_db), god: User = Depends(require_god)):
    _on()
    org = _org(db)
    prog = _prog(db, org)
    prof = {p.official_name: p for p in db.query(LocationProfile).filter(LocationProfile.organization_id == org.id)}
    eg, sh = prof[TEST_LOCATION].email_alias, prof["Striffler-Hamby Mortuary"].email_alias
    gid = "sim-%s-%s" % (case, uuid.uuid4().hex[:8])
    if (case not in ("duplicate_outbound", "resend_temporary_failure", "mailbox_temporary_failure")
            and db.query(Lead).filter(Lead.organization_id == org.id,
                                      Lead.email == "sim.active@example.com").first() is None):
        raise HTTPException(status_code=409, detail="This case needs the synthetic contacts: "
                            "seed again with simulation_contacts=true.")
    if case == "active":
        res, moves = _inbound(db, [_graph(gid, "sim.active@example.com", eg, "What does the veteran guide include?")])
        return {"result": res, "response": _resp_for(db, "sim.active@example.com"), "filed_to": moves}
    if case == "normal":
        res, moves = _inbound(db, [_graph(gid, "sim.active@example.com", eg, "ok thanks")])
        return {"result": res, "response": _resp_for(db, "sim.active@example.com"), "filed_to": moves}
    if case == "opt_out":
        res, _ = _inbound(db, [_graph(gid, "sim.optout@example.com", eg, "Please remove me, not interested. (%s)" % gid[-6:])])
        lead = db.query(Lead).filter(Lead.email == "sim.optout@example.com").one()
        cad = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
        return {"response": _resp_for(db, "sim.optout@example.com"), "allow_email": lead.allow_email,
                "cadence": str(getattr(cad.status, "value", cad.status)) if cad else None}
    if case == "wrong_person":
        res, _ = _inbound(db, [_graph(gid, "sim.wrong@example.com", sh, "This is not him, you have the wrong person")])
        rec = (db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org.id,
                                                    ProgramSourceRecord.source_lead_id == "SIM-WRONG").one())
        return {"response": _resp_for(db, "sim.wrong@example.com"), "data_review": rec.needs_data_review,
                "flags": json.loads(rec.data_note_flags or "[]")}
    if case == "unknown_sender":
        res, moves = _inbound(db, [_graph(gid, "stranger.%s@example.org" % gid[-4:], eg, "Can you send me the guide?")])
        u = (db.query(ProgramUnmatchedReply).filter(ProgramUnmatchedReply.organization_id == org.id)
             .order_by(ProgramUnmatchedReply.created_at.desc()).first())
        return {"result": res, "unmatched": {"alias": u.alias, "status": u.status} if u else None, "filed_to": moves}
    if case == "wrong_alias":
        res, _ = _inbound(db, [_graph(gid, "sim.active@example.com", sh, "Is the guide free?")])
        return {"response": _resp_for(db, "sim.active@example.com"),
                "contact_location_kept": _resp_for(db, "sim.active@example.com")["location_id"] == prof[TEST_LOCATION].location_id}
    if case == "duplicate_inbound":
        m = _graph(gid, "sim.active@example.com", eg, "Duplicate check message")
        first, _ = _inbound(db, [m])
        second, _ = _inbound(db, [m])
        lead = db.query(Lead).filter(Lead.email == "sim.active@example.com").one()
        from app.models.models import Reply
        return {"first": first, "second": second,
                "replies_with_body": db.query(Reply).filter(Reply.lead_id == lead.id,
                                                            Reply.body == "Duplicate check message").count()}
    if case == "duplicate_outbound":
        lead = _test_lead(db, org)
        if not db.query(EmailMessage).filter(EmailMessage.lead_id == lead.id,
                                             EmailMessage.subject.like("%" + SUBJECT_MARK + "%"),
                                             EmailMessage.status != "failed").first():
            return {"skipped": "run send-test first - this case only proves a SECOND send is refused"}
        try:
            send_test(SendIn(again=False), db, god)
            return {"refused": False}
        except HTTPException as exc:
            return {"refused": exc.status_code == 409, "detail": exc.detail}
    if case == "missing_location":
        from app.services.programs import identity
        lead = db.query(Lead).filter(Lead.email == "sim.noloc@example.com").one()
        return {"refusal": identity.send_refusal(db, lead, "manual")}
    if case == "held_contact":
        from app.services.programs import identity
        lead = db.query(Lead).filter(Lead.email == "sim.held@example.com").one()
        return {"refusal": identity.send_refusal(db, lead, "manual")}
    if case == "flyer_missing":
        from app.models.program_models import CampaignFamily
        from app.services.programs import email_touches as et
        fam = (db.query(CampaignFamily).filter(CampaignFamily.organization_id == org.id,
                                               CampaignFamily.key == "veteran_planning_guide").one())
        was = fam.is_active
        fam.is_active = True                          # in this transaction only - rolled back below
        db.flush()
        rep = et.run(db, org.id, dry_run=True)
        db.rollback()
        return {"held_no_flyer": rep["held_no_flyer"], "would_send": len(rep["would_send"]),
                "campaign_left_as": was}
    if case == "resend_temporary_failure":
        from app.services.programs import email_touches as et
        return {"429": et.classify_provider_error("429 Too Many Requests"),
                "503": et.classify_provider_error("503 Service Unavailable"),
                "timeout": et.classify_provider_error("Read timed out"),
                "422": et.classify_provider_error("422 domain not verified"),
                "backoff_minutes": [int(b.total_seconds() // 60) for b in et.RETRY_BACKOFF]}
    if case == "mailbox_temporary_failure":
        from app.services.inbound_mailbox_service import poll_mailbox
        box = _box(db)
        cursor, before = box.cursor_received_at, box.last_status

        def boom(since):
            raise RuntimeError("Graph inbox read failed 503: simulated")
        res = poll_mailbox(db, box, fetch=boom)
        seen = {"result": res, "status_recorded": box.last_status, "error": box.last_error}
        box.cursor_received_at, box.last_status, box.last_error = cursor, before, None
        db.commit()
        return seen
    if case == "bounce":
        secret = (os.environ.get("RESEND_WEBHOOK_SECRET") or "").strip()
        if not secret:
            return {"skipped": "RESEND_WEBHOOK_SECRET not set on staging"}
        lead = db.query(Lead).filter(Lead.email == "sim.active@example.com").one()
        em = EmailMessage(lead_id=lead.id, sender_id=god.id, subject="sim", body_html="sim",
                          status="sent", provider_message_id="sim-%s" % uuid.uuid4().hex[:10])
        db.add(em)
        db.commit()
        from fastapi.testclient import TestClient
        from app.main import app as _app
        raw = json.dumps({"type": "email.bounced", "data": {"email_id": em.provider_message_id,
                                                            "bounce": {"type": "hard"}}}).encode()
        key = base64.b64decode(secret[6:] if secret.startswith("whsec_") else secret)
        ts, eid = str(int(time.time())), "evt_sim_%s" % uuid.uuid4().hex[:8]
        sig = base64.b64encode(hmac.new(key, ("%s.%s." % (eid, ts)).encode() + raw, hashlib.sha256).digest()).decode()
        h = {"svix-id": eid, "svix-timestamp": ts, "svix-signature": "v1,%s" % sig, "content-type": "application/json"}
        c = TestClient(_app)
        first = c.post("/email/events/resend", content=raw, headers=h).json()
        again = c.post("/email/events/resend", content=raw, headers=h).json()
        db.refresh(lead)
        db.refresh(em)
        return {"first": first, "redelivered": again, "message_status": em.status, "lead_flag": lead.manual_flag}
    if case == "sla_escalation":
        from app.services.programs import responses as R
        lead = db.query(Lead).filter(Lead.email == "sim.active@example.com").one()
        with mock.patch("app.services.programs.responses._deliver_staff_alert",
                        side_effect=lambda *a, **k: (False, "simulation - not sent")):
            r = R.on_inbound(db, lead, "Yes I'd like to set up a time to visit", "email")
            r.sla_due_at = datetime.utcnow() - timedelta(minutes=1)
            db.commit()
            realerted = R.sla_sweep(db)
        return {"class": r.response_class, "realerted": len(realerted),
                "alert_kinds": sorted({a.kind for a in db.query(ProgramAlert).filter(ProgramAlert.response_id == r.id)})}
    if case == "sms_round_trip":
        return _sms_round_trip(db, org, prog, prof)
    if case == "pool_round_trip":
        return _pool_round_trip(db, org, prof)
    if case == "toll_free_round_trip":
        return _toll_free_round_trip(db, org, prof)
    raise HTTPException(status_code=404, detail="Unknown case. Cases: sms_round_trip, pool_round_trip, normal, active, opt_out, wrong_person, "
                        "toll_free_round_trip, unknown_sender, wrong_alias, duplicate_inbound, duplicate_outbound, missing_location, "
                        "held_contact, flyer_missing, resend_temporary_failure, mailbox_temporary_failure, "
                        "bounce, sla_escalation")


def _sms_round_trip(db, org, prog, prof):
    """The SMS software path on the staging database, with no carrier involved:
    outbound composition (Kerry sign-off, location identity) -> a reply on the
    CAMPUS number -> campus/location resolution -> contact match -> cadence
    pause -> classification -> alerts (recorded, not delivered) -> audit ->
    campus report. A fictional staging-only campus number; nothing is sent."""
    from app.models.models import Reply
    from app.models.telephony_models import PhoneNumber
    from app.routers.sms_router import process_inbound_sms
    from app.services.programs import campuses, identity
    gardens, home = prof[TEST_LOCATION], prof["Eastern Gate Memorial Funeral Home"]
    num = db.query(PhoneNumber).filter(PhoneNumber.e164 == SIM_CAMPUS_NUMBER).first()
    if num is None:
        num = PhoneNumber(e164=SIM_CAMPUS_NUMBER, organization_id=org.id, workspace_id=gardens.location_id,
                          cap_sms=True, cap_voice_inbound=True, cap_voicemail=True,
                          label="STAGING SIM campus number (Eastern Gate campus)")
        db.add(num)
        db.commit()
    lead = db.query(Lead).filter(Lead.organization_id == org.id, Lead.email == "sim.campus@example.com").one()
    outbound = identity.apply_sms_signoff(db, lead, (identity.cadence_text(db, lead, 1) or "Hi Cami"))
    sid = "SMsim%s" % uuid.uuid4().hex[:10]
    with mock.patch("app.services.programs.responses._deliver_staff_alert",
                    side_effect=lambda *a, **k: (False, "simulation - not sent")):
        process_inbound_sms(db, org_id=org.id, advisor=None, From="+12055550101",
                            Body="Yes, can I come by Friday to go over the guide?", MessageSid=sid,
                            called_number=SIM_CAMPUS_NUMBER, called_location_id=gardens.location_id)
        process_inbound_sms(db, org_id=org.id, advisor=None, From="+12055550999",
                            Body="Who is this? Got your text.", MessageSid=sid + "u",
                            called_number=SIM_CAMPUS_NUMBER, called_location_id=gardens.location_id)
    resp = (db.query(ProgramResponse).filter(ProgramResponse.lead_id == lead.id)
            .order_by(ProgramResponse.created_at.desc()).first())
    cad = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
    alerts = db.query(ProgramAlert).filter(ProgramAlert.response_id == resp.id).all() if resp else []
    unknown = (db.query(ProgramUnmatchedReply).filter(ProgramUnmatchedReply.organization_id == org.id,
                                                      ProgramUnmatchedReply.from_address == "+12055550999").first())
    return {
        "outbound_text": outbound,
        "outbound_identity_ok": "Kerry Allan" in outbound and home.official_name in outbound,
        "same_campus": campuses.same_campus(db, org.id, gardens.location_id, home.location_id),
        "reply_attached": db.query(Reply).filter(Reply.lead_id == lead.id).count(),
        "response": None if resp is None else {
            "class": resp.response_class, "urgency": resp.urgency, "intents": json.loads(resp.intents or "[]"),
            "location_is_contacts_own": resp.location_id == home.location_id,
            "via_number": resp.reply_to_alias, "summary": resp.summary, "sla_due_at": _iso(resp.sla_due_at),
            "suggested_reply": resp.suggested_reply},
        "cadence": str(getattr(cad.status, "value", cad.status)) if cad else None,
        "alerts": [{"audience": a.audience, "channel": a.channel, "delivered": a.delivered, "reason": a.reason}
                   for a in alerts],
        "unknown_sender_kept": None if unknown is None else {"location_id": unknown.location_id,
                                                             "status": unknown.status},
        "campus_report": [r for r in campuses.plan(db, org.id) if r["campus"] == gardens.campus_key],
    }


# ── Regional pool numbers (six local numbers, one per area code) ─────────────
#
# A pool number is shared by every campus in its area code and names NO
# location: workspace_id stays NULL and label is "pool:<pool id>"
# (app/services/programs/regional_pools.py). Known senders route by contact to
# their own entity; unknown senders go to the regional review queue.

FICTIONAL_POOL_NUMBER = "+12055550199"   # 555-01xx: reserved for fiction, never routable


def _pool_round_trip(db, org, prof):
    """Inbound text on a SHARED REGIONAL number, through the real inbound handler
    (process_inbound_sms with called_pool_id, exactly what the Twilio webhook
    passes for a "pool:" number). No row is created, nothing is sent, alerts are
    recorded but not delivered. Proves: a known contact texting a pool number is
    attached to ITS OWN location; an unknown sender goes to the regional review
    queue with no location; a STOP from an unknown sender is suppressed."""
    from app.models.models import Reply
    from app.routers.sms_router import process_inbound_sms
    from app.services.programs import regional_pools as rp
    pool = rp.POOLS["205"]
    home = prof["Eastern Gate Memorial Funeral Home"]
    lead = db.query(Lead).filter(Lead.organization_id == org.id, Lead.email == "sim.campus@example.com").one()
    before = db.query(Reply).filter(Reply.lead_id == lead.id).count()
    sid = "SMpool%s" % uuid.uuid4().hex[:10]
    stranger, stopper = "+12055550988", "+12055550977"
    with mock.patch("app.services.programs.responses._deliver_staff_alert",
                    side_effect=lambda *a, **k: (False, "simulation - not sent")):
        known = process_inbound_sms(db, org_id=org.id, advisor=None, From="+12055550101",
                                    Body="Got your text - what times are open next week?", MessageSid=sid,
                                    called_number=FICTIONAL_POOL_NUMBER, called_location_id=None,
                                    called_pool_id=pool["pool_id"])
        unknown = process_inbound_sms(db, org_id=org.id, advisor=None, From=stranger,
                                      Body="Who is this?", MessageSid=sid + "u",
                                      called_number=FICTIONAL_POOL_NUMBER, called_location_id=None,
                                      called_pool_id=pool["pool_id"])
        stop = process_inbound_sms(db, org_id=org.id, advisor=None, From=stopper,
                                   Body="STOP", MessageSid=sid + "s",
                                   called_number=FICTIONAL_POOL_NUMBER, called_location_id=None,
                                   called_pool_id=pool["pool_id"])
    resp = (db.query(ProgramResponse).filter(ProgramResponse.lead_id == lead.id)
            .order_by(ProgramResponse.created_at.desc()).first())
    u = (db.query(ProgramUnmatchedReply).filter(ProgramUnmatchedReply.organization_id == org.id,
                                                ProgramUnmatchedReply.from_address == stranger)
         .order_by(ProgramUnmatchedReply.created_at.desc()).first())
    from app.services.compliance_service import is_phone_suppressed
    stop_suppressed = bool(is_phone_suppressed(db, org.id, stopper))
    return {
        "pool": pool,
        "known_sender": {"result": known,
                         "reply_attached": db.query(Reply).filter(Reply.lead_id == lead.id).count() - before,
                         "location_is_contacts_own": bool(resp and resp.location_id == home.location_id),
                         "class": resp.response_class if resp else None},
        "unknown_sender": {"result": unknown,
                           "queued": None if u is None else {"location_id": u.location_id, "alias": u.alias,
                                                             "status": u.status}},
        "stop_from_unknown": {"result": stop, "suppressed": stop_suppressed},
        "sent": 0,
    }


class PoolNumberIn(BaseModel):
    e164: str
    sid: Optional[str] = None


class PoolNumbersIn(BaseModel):
    numbers: dict           # area code -> {"e164": "+1...", "sid": "PN..."}
    apply: bool = False     # dry run unless explicitly true


def _pool_targets():
    base = (os.environ.get("API_BASE_URL") or "").rstrip("/")
    return {"sms_url": base + "/sms/webhook/inbound", "voice_url": base + "/voice/inbound",
            "method": "POST", "base_configured": bool(base)}


def _staging_only():
    if (os.environ.get("APP_ENV") or "").strip().lower() != "staging":
        raise HTTPException(status_code=404, detail="Not Found")


@router.get("/pool-numbers")
def pool_numbers(db: Session = Depends(get_db), god: User = Depends(require_god)):
    _on()
    _staging_only()
    from app.models.telephony_models import PhoneNumber
    from app.services.programs import regional_pools as rp
    org = _org(db)
    rows = (db.query(PhoneNumber).filter(PhoneNumber.organization_id == org.id,
                                         PhoneNumber.label.like(rp.POOL_LABEL_PREFIX + "%")).all())
    by_pool = {}
    for r in rows:
        by_pool.setdefault(r.label[len(rp.POOL_LABEL_PREFIX):], []).append(
            {"e164": r.e164, "active": r.is_active, "workspace_id": r.workspace_id,
             "cap_sms": r.cap_sms, "cap_voice_inbound": r.cap_voice_inbound, "cap_voicemail": r.cap_voicemail})
    return {"targets": _pool_targets(), "backup_toll_free": rp.BACKUP_TOLL_FREE,
            "pools": [{"area_code": ac, **p, "numbers": by_pool.get(p["pool_id"], []),
                       "status": "provisioned" if by_pool.get(p["pool_id"]) else "not provisioned"}
                      for ac, p in rp.POOLS.items()]}


def _plan_pool_numbers(db, org, numbers: dict):
    """Validate every requested pool number; returns (plan, errors). Pure checks + reads."""
    from app.models.telephony_models import PhoneNumber
    from app.services.programs import regional_pools as rp
    plan, errors = [], []
    for ac, raw in (numbers or {}).items():
        item = raw if isinstance(raw, dict) else {"e164": raw}
        e164 = "".join(ch for ch in str(item.get("e164") or "") if ch.isdigit() or ch == "+")
        pool = rp.pool_for_area_code(str(ac))
        if pool is None:
            errors.append({"area_code": ac, "error": "not one of the six SCI pool area codes"})
            continue
        if not (e164.startswith("+1") and len(e164) == 12 and e164[2:5] == str(ac)):
            errors.append({"area_code": ac, "error": "%r is not a +1 %s number" % (e164, ac)})
            continue
        if e164 == rp.BACKUP_TOLL_FREE or "555" == e164[5:8]:
            errors.append({"area_code": ac, "error": "backup toll-free or a fictional 555 number cannot be a pool number"})
            continue
        existing = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first()
        label = rp.POOL_LABEL_PREFIX + pool["pool_id"]
        if existing is not None and (existing.organization_id != org.id or (existing.label or "") != label):
            errors.append({"area_code": ac, "error": "%s already exists for another org or purpose" % e164})
            continue
        plan.append({"area_code": ac, "e164": e164, "sid": item.get("sid"), "pool_id": pool["pool_id"],
                     "label": label, "action": "unchanged" if existing is not None else "create"})
    return plan, errors


@router.post("/pool-numbers")
def set_pool_numbers(body: PoolNumbersIn, db: Session = Depends(get_db), god: User = Depends(require_god)):
    """Record PURCHASED pool numbers on staging. Never buys, never calls Twilio,
    never sets workspace_id. Refuses the whole request if any entry is invalid."""
    _on()
    _staging_only()
    from app.models.telephony_models import PhoneNumber
    org = _org(db)
    plan, errors = _plan_pool_numbers(db, org, body.numbers)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors, "nothing_written": True})
    if not body.apply:
        return {"dry_run": True, "plan": plan, "targets": _pool_targets()}
    for it in plan:
        if it["action"] == "create":
            db.add(PhoneNumber(e164=it["e164"], provider="twilio", provider_sid=it["sid"],
                               organization_id=org.id, workspace_id=None, label=it["label"],
                               cap_sms=True, cap_voice_inbound=True, cap_voicemail=True,
                               cap_voice_outbound=False, is_active=True, created_by_id=god.id))
    db.commit()
    return {"dry_run": False, "plan": plan, "targets": _pool_targets()}


class TollFreeIn(BaseModel):
    apply: bool = False
    sid: Optional[str] = None


@router.post("/toll-free")
def set_toll_free(body: TollFreeIn, db: Session = Depends(get_db), god: User = Depends(require_god)):
    """Record the EXISTING SCI toll-free line (+1 844-917-2171) on staging as the
    SCI program's number: SMS + inbound voice + voicemail, NO outbound voice,
    route voicemail_only (no ringing, no forwarding, no AI). Never buys, never
    calls Twilio. Dry run unless `apply`. Idempotent."""
    import json as _json
    _on()
    _staging_only()
    from app.models.telephony_models import PhoneNumber
    from app.services.programs import regional_pools as rp
    org = _org(db)
    label = rp.POOL_LABEL_PREFIX + rp.TOLL_FREE_POOL["pool_id"]
    route = {"mode": "voicemail_only", "voicemail": True, "ring_user_ids": []}
    existing = db.query(PhoneNumber).filter(PhoneNumber.e164 == rp.TOLL_FREE).first()
    if existing is not None and existing.organization_id != org.id:
        raise HTTPException(status_code=409, detail={"error": "the toll-free number belongs to another organization",
                                                     "nothing_written": True})
    action = "create" if existing is None else (
        "unchanged" if ((existing.label or "") == label and existing.is_active and existing.cap_sms
                        and existing.cap_voicemail and not existing.cap_voice_outbound
                        and _route_mode(existing) == "voicemail_only") else "update")
    plan = {"e164": rp.TOLL_FREE, "label": label, "route": route, "action": action,
            "cap_sms": True, "cap_voice_inbound": True, "cap_voicemail": True, "cap_voice_outbound": False}
    if not body.apply or action == "unchanged":
        return {"dry_run": not body.apply, "plan": plan, "targets": _pool_targets()}
    rec = existing or PhoneNumber(e164=rp.TOLL_FREE, provider="twilio", organization_id=org.id,
                                  created_by_id=god.id)
    rec.provider_sid = body.sid or getattr(rec, "provider_sid", None)
    rec.workspace_id, rec.label, rec.is_active = None, label, True
    rec.cap_sms, rec.cap_voice_inbound, rec.cap_voicemail, rec.cap_voice_outbound = True, True, True, False
    rec.default_inbound_route = _json.dumps(route)
    if existing is None:
        db.add(rec)
    db.commit()
    return {"dry_run": False, "plan": plan, "targets": _pool_targets()}


def _route_mode(rec) -> str:
    from app.services import number_resolution as NR
    return NR.parse_route(rec.default_inbound_route)["mode"]


def _toll_free_round_trip(db, org, prof):
    """The SCI toll-free line end to end, through the REAL handlers, without a
    carrier: inbound SMS (known contact, unknown sender, STOP) via
    process_inbound_sms with the toll-free pool id; inbound calls via
    telephony_service.handle_inbound with a platform-verified request; a
    voicemail via store_voicemail. Nothing is sent and Twilio is never called
    (staff alerts are recorded, not delivered)."""
    import re as _re
    from app.models.models import Notification, Reply
    from app.models.telephony_models import InboundCallLog, PhoneNumber, Voicemail
    from app.models.work_models import LeadTask
    from app.routers.sms_router import process_inbound_sms
    from app.services import telephony_service as TS
    from app.services.programs import regional_pools as rp
    from app.services.telephony_webhook_guard import VerifiedVoiceRequest
    from app.services.compliance_service import is_phone_suppressed
    tf = rp.TOLL_FREE
    num = db.query(PhoneNumber).filter(PhoneNumber.e164 == tf, PhoneNumber.organization_id == org.id).first()
    if num is None:
        raise HTTPException(status_code=409, detail="Record the toll-free line first: POST /god/staging/sci/toll-free")
    home = prof["Eastern Gate Memorial Funeral Home"]
    lead = db.query(Lead).filter(Lead.organization_id == org.id, Lead.email == "sim.campus@example.com").one()
    tag = uuid.uuid4().hex[:8]
    stranger, stopper = "+12055550966", "+12055550955"
    out = {"toll_free": tf, "sent": 0}
    before = db.query(Reply).filter(Reply.lead_id == lead.id).count()
    with mock.patch("app.services.programs.responses._deliver_staff_alert",
                    side_effect=lambda *a, **k: (False, "simulation - not sent")):
        known = process_inbound_sms(db, org_id=org.id, advisor=None, From="+12055550101",
                                    Body="Can someone call me about the planning guide?", MessageSid="SMtf%s" % tag,
                                    called_number=tf, called_location_id=None,
                                    called_pool_id=rp.TOLL_FREE_POOL["pool_id"])
        unknown = process_inbound_sms(db, org_id=org.id, advisor=None, From=stranger, Body="Who is this?",
                                      MessageSid="SMtf%su" % tag, called_number=tf, called_location_id=None,
                                      called_pool_id=rp.TOLL_FREE_POOL["pool_id"])
        stop = process_inbound_sms(db, org_id=org.id, advisor=None, From=stopper, Body="STOP",
                                   MessageSid="SMtf%ss" % tag, called_number=tf, called_location_id=None,
                                   called_pool_id=rp.TOLL_FREE_POOL["pool_id"])
    resp = (db.query(ProgramResponse).filter(ProgramResponse.lead_id == lead.id)
            .order_by(ProgramResponse.created_at.desc()).first())
    u = (db.query(ProgramUnmatchedReply).filter(ProgramUnmatchedReply.organization_id == org.id,
                                                ProgramUnmatchedReply.from_address == stranger)
         .order_by(ProgramUnmatchedReply.created_at.desc()).first())
    out["sms"] = {
        "known": {"result": known, "reply_attached": db.query(Reply).filter(Reply.lead_id == lead.id).count() - before,
                  "cemetery_is_contacts_own": bool(resp and resp.location_id == home.location_id)},
        "unknown": {"result": unknown, "queued_for_review": u is not None and u.location_id is None},
        "stop": {"result": stop, "suppressed": bool(is_phone_suppressed(db, org.id, stopper))}}

    def call(frm, sid):
        v = VerifiedVoiceRequest({"To": tf, "From": frm, "CallSid": sid}, None, org.id, True)
        xml = TS.handle_inbound(db, v)
        said = " ".join(_re.findall(r"<Say[^>]*>(.*?)</Say>", xml))
        return xml, said
    xml_k, said_k = call("+12055550101", "CAtf%sk" % tag)
    xml_u, said_u = call(stranger, "CAtf%su" % tag)
    out["voice"] = {
        "known": {"voicemail_only": "<Record" in xml_k and "<Dial" not in xml_k and "<Connect" not in xml_k,
                  "greeting_names_contacts_cemetery": home.official_name in said_k, "greeting": said_k[:300]},
        "unknown": {"voicemail_only": "<Record" in xml_u and "<Dial" not in xml_u,
                    "greeting_is_neutral": not any(p.official_name in said_u for p in prof.values()),
                    "greeting": said_u[:300]}}
    row = db.query(InboundCallLog).filter(InboundCallLog.call_sid == "CAtf%sk" % tag).one()
    rec_sid = "RE" + uuid.uuid4().hex
    vm = TS.store_voicemail(db, row, recording_sid=rec_sid,
                            recording_url="https://api.twilio.com/simulated/%s" % rec_sid,
                            duration="9", call_sid=row.call_sid)
    task = db.query(LeadTask).filter(LeadTask.id == vm.task_id).first()
    notes = (db.query(Notification).filter(Notification.lead_id == lead.id,
                                           Notification.message.like("Voicemail from%")).count())
    out["voicemail"] = {"saved_to_contact": vm.lead_id == lead.id, "recording_stored": bool(vm.recording_url),
                        "callback_task": bool(task), "task_assignee_is_contacts_rep": bool(task) and
                        task.assigned_to_id == lead.assigned_to_id,
                        "rep_notified": notes > 0}
    checks = [out["sms"]["known"]["reply_attached"] == 1, out["sms"]["known"]["cemetery_is_contacts_own"],
              out["sms"]["unknown"]["queued_for_review"], out["sms"]["stop"]["suppressed"],
              out["voice"]["known"]["voicemail_only"], out["voice"]["known"]["greeting_names_contacts_cemetery"],
              out["voice"]["unknown"]["voicemail_only"], out["voice"]["unknown"]["greeting_is_neutral"],
              out["voicemail"]["saved_to_contact"], out["voicemail"]["callback_task"],
              out["voicemail"]["rep_notified"]]
    out["pass"] = all(checks)
    out["passed_checks"] = "%d/%d" % (sum(1 for c in checks if c), len(checks))
    return out


class TestPhoneIn(BaseModel):
    phone: str
    apply: bool = False


@router.post("/test-phone")
def set_test_phone(body: TestPhoneIn, db: Session = Depends(get_db), god: User = Depends(require_god)):
    """Put a DESIGNATED TEST PHONE on the seeded test contact (a TEST record at
    Eastern Gate Memorial Gardens), so a live text or call from that phone to
    the toll-free line matches a contact and a cemetery. Staging only; dry run
    unless `apply`; refuses a number already used by any other contact in the
    SCI workspace. Sends nothing."""
    _on()
    _staging_only()
    from app.services import wholesale_sms
    org = _org(db)
    lead = _test_lead(db, org)
    e164 = wholesale_sms.normalize_e164(body.phone)
    if not e164:
        raise HTTPException(status_code=422, detail="Not a usable US mobile number.")
    digits = e164[2:]
    clash = (db.query(Lead).filter(Lead.organization_id == org.id, Lead.id != lead.id,
                                   Lead.phone.in_([e164, "1" + digits, digits])).first())
    if clash is not None:
        raise HTTPException(status_code=409, detail="That number belongs to another contact in this workspace.")
    plan = {"contact": lead.email, "is_test": bool(getattr(lead, "is_test", False)),
            "location": TEST_LOCATION, "phone_last4": digits[-4:], "action": "set"}
    if body.apply:
        lead.phone = "1" + digits
        db.commit()
    return {"dry_run": not body.apply, "plan": plan}


class SendSmsIn(BaseModel):
    again: bool = False
    phone: Optional[str] = None      # pick a named test contact by phone; default: the seeded test contact


@router.post("/send-test-sms")
def send_test_sms(body: SendSmsIn, db: Session = Depends(get_db), god: User = Depends(require_god)):
    """The ONE live SCI text: to the seeded TEST contact's designated test phone
    only, as a person's MANUAL send, through the real send_sms - every gate
    (SCI consent, approved toll-free sender, SCI_SMS_SEND_ENABLED, suppression,
    content approval) applies. Staging only. Refuses a second send unless `again`."""
    _on()
    _staging_only()
    from app.models.models import Message
    from app.services import send_source as _ss, sms_service
    org = _org(db)
    lead = _test_lead(db, org)
    if body.phone:
        from app.services import wholesale_sms
        e164 = wholesale_sms.normalize_e164(body.phone)
        d = e164[2:] if e164 else "-"
        lead = (db.query(Lead).filter(Lead.organization_id == org.id, Lead.is_test.is_(True),
                                      Lead.phone.in_([e164 or "-", "1" + d, d])).first())
        if lead is None:
            raise HTTPException(status_code=404, detail="No TEST contact has that phone: POST /god/staging/sci/test-contact first.")
    if not lead.phone:
        raise HTTPException(status_code=409, detail="No designated test phone: POST /god/staging/sci/test-phone first.")
    prior = db.query(Message).filter(Message.lead_id == lead.id).count()
    if prior and not body.again:
        raise HTTPException(status_code=409, detail="A test text was already sent to the test contact (%d)." % prior)
    template = ("Hi {first_name}, this is a test text from the SCI line via EvoSys Pro. "
                "Reply to this message to test replies. Reply STOP to opt out.")
    try:
        msg = sms_service.send_sms(db, god, lead, template, include_booking_link=False,
                                   send_source=_ss.MANUAL, sent_by_user_id=god.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Refused before the provider: %s" % exc)
    return {"sent": True, "twilio_status": getattr(msg, "twilio_status", None),
            "provider_sid": getattr(msg, "twilio_sid", None), "to_last4": (lead.phone or "")[-4:],
            "body": msg.body}


class TestContactIn(BaseModel):
    first_name: str
    last_name: str
    phone: str
    location: str = TEST_LOCATION
    apply: bool = False


@router.post("/test-contact")
def add_test_contact(body: TestContactIn, db: Session = Depends(get_db), god: User = Depends(require_god)):
    """An ADDITIONAL named TEST contact at a location, for a phone Mike has
    designated for a live test (e.g. SCI management seeing a text arrive).
    Leaves every other contact untouched. Staging only; dry run unless
    `apply`; idempotent per phone; refuses a phone owned by a non-test contact."""
    _on()
    _staging_only()
    from app.services import wholesale_sms
    org = _org(db)
    e164 = wholesale_sms.normalize_e164(body.phone)
    if not e164:
        raise HTTPException(status_code=422, detail="Not a usable US mobile number.")
    d = e164[2:]
    prof = (db.query(LocationProfile).filter(LocationProfile.organization_id == org.id,
                                             LocationProfile.official_name == body.location,
                                             LocationProfile.is_review_bucket.is_(False)).first())
    if prof is None:
        raise HTTPException(status_code=404, detail="Unknown location.")
    owner = db.query(Lead).filter(Lead.organization_id == org.id, Lead.phone.in_([e164, "1" + d, d])).first()
    if owner is not None and not owner.is_test:
        raise HTTPException(status_code=409, detail="That number belongs to a real contact in this workspace.")
    src = "TEST-" + d
    plan = {"contact": "%s %s" % (body.first_name, body.last_name), "location": prof.official_name,
            "phone_last4": d[-4:], "action": "unchanged" if owner is not None else "create"}
    if not body.apply or owner is not None:
        return {"dry_run": not body.apply, "plan": plan}
    lead = Lead(organization_id=org.id, first_name=body.first_name.strip(), last_name=body.last_name.strip(),
                phone="1" + d, status="new", is_test=True, assigned_to_id=god.id,
                test_note="STAGING live-test contact designated by Mike")
    db.add(lead)
    db.flush()
    raw = {"Lead ID": src, "First Name": lead.first_name, "Last Name": lead.last_name,
           "Location Friendly Name": prof.official_name}
    rec = ProgramSourceRecord(organization_id=org.id, source_lead_id=src, row_number=0, raw_json=json.dumps(raw),
                              first_name=lead.first_name, last_name=lead.last_name, source_status="Qualified",
                              source_campaign="Direct Mail> Veteran> Veteran Planning Guide",
                              source_location_name=prof.official_name)
    rec.location_id, rec.location_status = prof.location_id, "mapped"
    rec.campaign_family, rec.link_status, rec.contact_master_key = "veteran_planning_guide", "unique", src
    rec.lead_id = lead.id
    db.add(rec)
    db.commit()
    return {"dry_run": False, "plan": plan, "lead_id": lead.id}

