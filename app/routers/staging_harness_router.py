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
]


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
                    status="new", is_test=True, test_note=note, assigned_to_id=god.id)
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
    raise HTTPException(status_code=404, detail="Unknown case. Cases: normal, active, opt_out, wrong_person, "
                        "unknown_sender, wrong_alias, duplicate_inbound, duplicate_outbound, missing_location, "
                        "held_contact, flyer_missing, resend_temporary_failure, mailbox_temporary_failure, "
                        "bounce, sla_escalation")
