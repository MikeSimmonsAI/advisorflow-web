"""WORKSPACE DATA RESET + FIRST CAMPAIGN PREPARATION (Issue #507).

Restland Cemetery and Funeral Home: ~10.6k families from several imports, a
legacy texting cadence and an old AI follow-up pipeline, about to start its
first program campaign ("Family File Review - Batch 1"). This module:

  plan(db, org)            READ ONLY. Every family's disposition and every
                           campaign-audience decision, with reasons and counts.
  apply(db, org, key)      Writes the plan, recorded item by item, and
                           prepares the campaign as a DRAFT. Idempotent by key.
  rollback(db, org, run)   Restores exactly what that run changed.

WHAT APPLY CHANGES (each an item with before/after):
  * legacy cadence states (active/ACTIVE)  -> status "paused" (touch numbers,
    due dates and history untouched; a paused state is never picked up)
  * legacy AI pipeline conversations with a scheduled next touch -> paused,
    next_send_at cleared (the old value is in the item)
  * the org's default cadence template -> no longer default; a new program
    cadence (3 texts) becomes default for the campaign's schedule
  * one campaign family, INACTIVE, with drafted copy for every step
  * one LeadDisposition per family and one CampaignAudienceMember per family
    in the candidate pool (25 staged for Batch 1, none approved)

WHAT IT NEVER DOES: send, approve, start a journey, switch a campaign or the
texting switch on, delete a row of history, change a lead's own values,
clear a do-not-contact status or suppression entry, or touch another
organization. Duplicates are never merged here: an identical duplicate row is
already linked to its canonical family and keeps its own history.
"""
import json
import re
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import (BookingLink, CadenceState, CadenceTemplate, CadenceTemplateTouch, EmailMessage,
                               Lead, Message, Organization, PipelineConversation, Reply)
from app.models.program_models import CampaignFamily, OutreachProgram, ProgramSourceRecord
from app.models.reset_models import (CampaignAudienceMember, LeadDisposition, WorkspaceResetItem,
                                     WorkspaceResetRun)

CAMPAIGN_KEY = "restland_family_file_review"
CAMPAIGN_NAME = "Restland — Family File Review — Batch 1"
CADENCE_NAME = "Restland Family File Review (3 texts)"
BATCH_SIZE = 25
SENDER_E164 = "+18553601164"

# ── the sequence (drafts for the owner's review; nothing sends) ──────────────
# Day offsets / Central send hours. Every text names Mike and Restland, asks for
# a short visit, implies no problem or obligation, and ends with the opt-out the
# platform's content policy requires.
SMS_STEPS = [(1, 0, 10), (2, 3, 11), (3, 8, 14)]
SMS_COPY = {
    1: ("Hi {first_name}, this is Mike Simmons with Restland Cemetery & Funeral Home. I'm reaching out to "
        "families to schedule a short Family File Review here at Restland: about 20-30 minutes to confirm "
        "your file is up to date, sign the permission-to-access form and go over our pre-planning guide. "
        "Would a weekday or a Saturday suit you better? Reply STOP to opt out."),
    2: ("Hi {first_name}, Mike Simmons at Restland again. If it helps, the Family File Review takes about "
        "20-30 minutes at 13005 Greenville Ave. {booking_line} Or just reply with a day that works for you. "
        "Reply STOP to opt out."),
    3: ("{first_name}, a last note from Mike Simmons at Restland. Whenever you'd like to come in for your "
        "Family File Review, reply here or call 469-553-7417 and I'll set a time. Reply STOP to opt out."),
}
EMAIL_COPY = {
    1: ("Your Family File Review at Restland",
        "Hello {first_name},\n\nI'm Mike Simmons with Restland Cemetery & Funeral Home. We're meeting with "
        "families for a short Family File Review here at Restland - about 20-30 minutes in person to confirm "
        "your file is up to date, complete the permission-to-access form and go over our pre-planning guide.\n\n"
        "{booking_line}\n\nOr reply to this email with a day that works.\n\nMike Simmons\nRestland Cemetery & "
        "Funeral Home\n13005 Greenville Ave, Dallas, TX 75243\n469-553-7417"),
    2: ("A short visit to review your family file",
        "Hello {first_name},\n\nFollowing up on the Family File Review. It takes about 20-30 minutes and we'll "
        "do it together here at Restland.\n\n{booking_line}\n\nMike Simmons\nRestland Cemetery & Funeral Home\n"
        "469-553-7417"),
    3: ("Weekday or Saturday?",
        "Hello {first_name},\n\nIf a weekday or a Saturday is easier for your Family File Review, just reply and "
        "I'll find a time.\n\n{booking_line}\n\nMike Simmons\nRestland Cemetery & Funeral Home\n469-553-7417"),
    4: ("Your pre-planning guide",
        "Hello {first_name},\n\nDuring your Family File Review we'll go over our pre-planning guide so you have "
        "it for your records. It's a short, in-person visit.\n\n{booking_line}\n\nMike Simmons\nRestland Cemetery "
        "& Funeral Home\n469-553-7417"),
    5: ("Whenever you're ready",
        "Hello {first_name},\n\nWhenever you'd like to come in for your Family File Review, reply here or call "
        "469-553-7417.\n\nMike Simmons\nRestland Cemetery & Funeral Home\n13005 Greenville Ave, Dallas, TX 75243"),
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
NOT_GENERAL_PRE_NEED = ("contract_sold", "at_need", "imminent")


class ResetRefused(RuntimeError):
    pass


# ── normalization (never written onto the lead) ──────────────────────────────

def e164(raw: Optional[str]) -> Optional[str]:
    """A US number as +1XXXXXXXXXX, or None. 10 digits, or 11 starting with 1;
    area code and exchange may not start with 0 or 1."""
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if len(d) != 10 or d[0] in "01" or d[3] in "01":
        return None
    return "+1" + d


def phone_issue(raw: Optional[str]) -> Optional[str]:
    if not (raw or "").strip():
        return "missing"
    return None if e164(raw) else "invalid"


def email_issue(raw: Optional[str]) -> Optional[str]:
    if not (raw or "").strip():
        return "missing"
    return None if EMAIL_RE.match(raw.strip()) else "malformed"


def _name_issue(l: Lead) -> Optional[str]:
    return None if (l.first_name or "").strip() and (l.last_name or "").strip() else "incomplete"


def _norm(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


# ── evidence, loaded once ────────────────────────────────────────────────────

def _evidence(db: Session, org_id: str) -> Dict:
    leads = db.query(Lead).filter(Lead.organization_id == org_id).order_by(Lead.created_at, Lead.id).all()
    ids = {l.id for l in leads}
    org_leads = db.query(Lead.id).filter(Lead.organization_id == org_id)

    def lead_set(q):
        return {r[0] for r in q.all() if r[0] in ids}

    replied = lead_set(db.query(Reply.lead_id).filter(Reply.lead_id.in_(org_leads)))
    texted = lead_set(db.query(Message.lead_id).filter(Message.lead_id.in_(org_leads)))
    emailed = lead_set(db.query(EmailMessage.lead_id).filter(EmailMessage.lead_id.in_(org_leads)))
    bookings = lead_set(db.query(BookingLink.lead_id).filter(BookingLink.lead_id.in_(org_leads),
                                                             BookingLink.booked_time.isnot(None)))
    future = lead_set(db.query(BookingLink.lead_id).filter(BookingLink.lead_id.in_(org_leads),
                                                           BookingLink.booked_time >= datetime.utcnow(),
                                                           BookingLink.status.in_(("booked", "confirmed"))))
    cadence = {s.lead_id: s for s in db.query(CadenceState).filter(CadenceState.lead_id.in_(org_leads)).all()}
    pipeline = {p.lead_id: p for p in db.query(PipelineConversation)
                .filter(PipelineConversation.organization_id == org_id).all()}
    recs = {r.lead_id: r for r in db.query(ProgramSourceRecord)
            .filter(ProgramSourceRecord.organization_id == org_id, ProgramSourceRecord.lead_id.isnot(None)).all()}
    try:
        from app.models.models import SuppressionEntry
        supp = {e164(s.phone) for s in db.query(SuppressionEntry).filter(SuppressionEntry.organization_id == org_id)}
        supp.discard(None)
    except Exception:                                    # noqa: BLE001
        db.rollback()
        supp = set()
    try:
        from app.models.program_models import ProgramResponse, ProgramConversation
        attention = lead_set(db.query(ProgramResponse.lead_id).filter(
            ProgramResponse.organization_id == org_id, ProgramResponse.handling_status != "closed"))
        attention |= lead_set(db.query(ProgramConversation.lead_id).filter(
            ProgramConversation.organization_id == org_id, ProgramConversation.handoff_reason.isnot(None)))
    except Exception:                                    # noqa: BLE001
        db.rollback()
        attention = set()
    return {"leads": leads, "replied": replied, "texted": texted, "emailed": emailed, "bookings": bookings,
            "future": future, "cadence": cadence, "pipeline": pipeline, "recs": recs, "suppressed_phones": supp,
            "attention": attention}


# ── the plan (read-only) ─────────────────────────────────────────────────────

def plan(db: Session, org: Organization) -> Dict:
    ev = _evidence(db, org.id)
    leads = ev["leads"]
    by_id = {l.id: l for l in leads}
    phone_groups = defaultdict(list)
    for l in leads:
        p = e164(l.phone)
        if p:
            phone_groups[p].append(l.id)
    # Contact-wide do-not-contact: a phone number is blocked when ANY family on
    # it is do-not-contact for a reason other than being a duplicate row, or the
    # number is on the suppression list. A merge or a new record can never
    # revive it.
    blocked_phones = set(ev["suppressed_phones"])
    for l in leads:
        if l.status == "dnc" and not l.is_duplicate:
            p = e164(l.phone)
            if p:
                blocked_phones.add(p)

    dispositions: Dict[str, Dict] = {}
    for l in leads:
        p = e164(l.phone)
        notes = []
        canon = l.duplicate_of_lead_id if (l.duplicate_of_lead_id in by_id) else None
        pi, ei, ni = phone_issue(l.phone), email_issue(l.email), _name_issue(l)
        if l.is_test:
            disp, queue = "test", None
        elif l.is_duplicate and canon:
            c = by_id[canon]
            identical = (_norm(l.first_name) == _norm(c.first_name) and _norm(l.last_name) == _norm(c.last_name)
                         and e164(l.phone) == e164(c.phone) and _norm(l.email) == _norm(c.email))
            disp = "merged_duplicate" if identical else "duplicate_review"
            queue = None if identical else "duplicate"
            if l.status == "dnc":
                notes.append("do-not-contact set on the duplicate row (kept; the canonical family is the contact)")
        elif l.is_duplicate:
            disp, queue = "duplicate_review", "duplicate"
            notes.append("flagged duplicate with no canonical family on record")
        elif l.status == "dnc" or (p and p in blocked_phones):
            disp, queue = "suppressed", None
        elif l.status == "needs_tier_review":
            disp, queue = "manual_review", "needs_classification"
        elif pi and ei:
            disp, queue = "manual_review", "contact_data"
        elif pi == "missing" and not ei:
            disp, queue = "email_only", None
        elif pi == "invalid":
            disp, queue = "manual_review", "contact_data"
        else:
            disp, queue = "retain", None
        shares = max(0, len(phone_groups.get(p, [])) - 1) if p else 0
        dispositions[l.id] = {"disposition": disp, "review_queue": queue, "phone_e164": p, "phone_issue": pi,
                              "email_issue": ei, "name_issue": ni, "canonical_lead_id": canon,
                              "shares_phone_with": shares, "notes": notes}

    # ── campaign audience: pre-need families only ───────────────────────────
    members: Dict[str, Dict] = {}
    pool = [l for l in leads if not l.is_test and not l.is_duplicate and l.status == "new"
            and (l.tier or "") == "pre_need" and e164(l.phone)]
    for l in pool:
        reasons, seg = [], "ready"
        p = e164(l.phone)
        if p in blocked_phones:
            seg = "suppressed"; reasons.append("number is do-not-contact or suppressed")
        elif l.id in ev["future"]:
            seg = "upcoming_appointment"; reasons.append("has an upcoming appointment")
        elif l.id in ev["attention"]:
            seg = "needs_staff"; reasons.append("conversation waiting on staff")
        elif l.id in ev["replied"]:
            seg = "prior_conversation"; reasons.append("replied before; follow up personally, not as new outreach")
        elif l.id in ev["bookings"]:
            seg = "past_appointment"; reasons.append("has a past or cancelled appointment on record")
        elif l.id in ev["cadence"]:
            st = ev["cadence"][l.id]
            seg = "earlier_sequence"
            reasons.append("in the earlier texting sequence (touch %s, status %s)" % (st.current_touch_number, st.status))
        elif l.id in ev["pipeline"]:
            seg = "earlier_sequence"; reasons.append("in the earlier AI follow-up pipeline")
        elif l.id in ev["texted"] or l.id in ev["emailed"]:
            seg = "previously_contacted"; reasons.append("texted or emailed before")
        else:
            rec = ev["recs"].get(l.id)
            if rec is None:
                seg = "not_linked"; reasons.append("no program record")
            elif rec.campaign_family and rec.campaign_family != CAMPAIGN_KEY:
                seg = "other_campaign"; reasons.append("already in campaign %s" % rec.campaign_family)
            elif rec.on_hold or rec.needs_data_review:
                seg = "held"; reasons.append("program record on hold or needs data review")
            elif _name_issue(l):
                seg = "contact_data"; reasons.append("first or last name missing")
        members[l.id] = {"segment": seg, "reasons": reasons, "destination": p}
    # One family per destination: shared numbers send once, to the oldest record.
    seen = {}
    for l in pool:
        m = members[l.id]
        if m["segment"] != "ready":
            continue
        if m["destination"] in seen:
            m["segment"] = "shared_number"
            m["reasons"].append("shares this number with another ready family (%s)" % seen[m["destination"]][:8])
        else:
            seen[m["destination"]] = l.id
    ready = [l.id for l in pool if members[l.id]["segment"] == "ready"]
    for lid in ready:
        members[lid]["reasons"] = ["pre-need, status new, valid US number, never contacted, no reply, "
                                   "no appointment, no earlier sequence, not suppressed, number not shared"]
    batch = ready[:BATCH_SIZE]
    for lid in batch:
        members[lid]["batch"] = 1

    def count(d, k):
        out = defaultdict(int)
        for v in d.values():
            out[v[k]] += 1
        return dict(sorted(out.items()))

    review = defaultdict(int)
    for v in dispositions.values():
        if v["review_queue"]:
            review[v["review_queue"]] += 1
    summary = {
        "families": len(leads),
        "dispositions": count(dispositions, "disposition"),
        "review_queues": dict(review),
        "phone_issues": dict(defaultdict(int, {k: sum(1 for v in dispositions.values() if v["phone_issue"] == k)
                                             for k in ("missing", "invalid")})),
        "email_issues": {k: sum(1 for v in dispositions.values() if v["email_issue"] == k) for k in ("missing", "malformed")},
        "incomplete_names": sum(1 for v in dispositions.values() if v["name_issue"]),
        "blocked_numbers": len(blocked_phones),
        "candidate_pool": len(pool),
        "audience_segments": count(members, "segment"),
        "ready": len(ready),
        "batch_1": len(batch),
        "legacy_cadence_states": {s: sum(1 for c in ev["cadence"].values() if c.status == s)
                                  for s in sorted({c.status for c in ev["cadence"].values()})},
        "pipeline_scheduled": sum(1 for p in ev["pipeline"].values() if p.next_send_at is not None),
    }
    return {"summary": summary, "dispositions": dispositions, "members": members, "batch": batch}


# ── apply / rollback ─────────────────────────────────────────────────────────

def _item(db, run, entity, entity_id, action, before, after, reason):
    db.add(WorkspaceResetItem(run_id=run.id, organization_id=run.organization_id, entity=entity,
                              entity_id=entity_id, action=action, before_json=json.dumps(before, default=str),
                              after_json=json.dumps(after, default=str), reason=reason))


def _iso(dt):
    return dt.isoformat() if dt else None


def apply(db: Session, org: Organization, run_key: str) -> Dict:
    from app.services import sms_programs
    if org is None:
        raise ResetRefused("Organization not found.")
    if sms_programs.is_sci_org(db, org.id):
        raise ResetRefused("Refused: this is the SCI organization.")
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org.id).first()
    if prog is None:
        raise ResetRefused("This workspace has no program record.")
    done = db.query(WorkspaceResetRun).filter_by(organization_id=org.id, run_key=run_key, mode="apply").first()
    if done is not None and done.status in ("applied", "rolled_back"):
        return {"run_id": done.id, "status": done.status, "already": True,
                "summary": json.loads(done.summary_json or "{}")}
    p = plan(db, org)
    run = done or WorkspaceResetRun(organization_id=org.id, run_key=run_key, mode="apply")
    db.add(run)
    db.flush()
    now = datetime.utcnow()
    changes = defaultdict(int)

    # 1. Legacy cadence: pause every running state, any casing.
    for st in (db.query(CadenceState).join(Lead, Lead.id == CadenceState.lead_id)
               .filter(Lead.organization_id == org.id).all()):
        if (st.status or "").lower() == "active":
            _item(db, run, "cadence_state", st.id, "pause",
                  {"status": st.status, "next_touch_due_at": _iso(st.next_touch_due_at),
                   "current_touch_number": st.current_touch_number}, {"status": "paused"},
                  "superseded by the program campaign; touch history and due date kept")
            st.status = "paused"
            changes["cadence_paused"] += 1
    # 2. Legacy AI pipeline: nothing scheduled may fire.
    for pc in db.query(PipelineConversation).filter(PipelineConversation.organization_id == org.id).all():
        if pc.next_send_at is not None or not pc.paused:
            _item(db, run, "pipeline_conversation", pc.id, "pause",
                  {"paused": bool(pc.paused), "next_send_at": _iso(pc.next_send_at)},
                  {"paused": True, "next_send_at": None}, "superseded by the program campaign")
            pc.paused, pc.next_send_at = True, None
            changes["pipeline_paused"] += 1
    # 3. The campaign's own 3-text schedule becomes the program cadence.
    tpl = (db.query(CadenceTemplate).filter(CadenceTemplate.organization_id == org.id,
                                            CadenceTemplate.name == CADENCE_NAME).first())
    if tpl is None:
        for old in db.query(CadenceTemplate).filter(CadenceTemplate.organization_id == org.id,
                                                    CadenceTemplate.is_default.is_(True)).all():
            _item(db, run, "cadence_template", old.id, "unset_default", {"is_default": True}, {"is_default": False},
                  "the program campaign's own schedule becomes the default")
            old.is_default = False
        tpl = CadenceTemplate(organization_id=org.id, name=CADENCE_NAME, industry="program", is_default=True,
                              is_active=True, description="Family File Review - 3 texts (draft, nothing sends)")
        db.add(tpl)
        db.flush()
        for n, day, hour in SMS_STEPS:
            db.add(CadenceTemplateTouch(template_id=tpl.id, touch_number=n, day_offset=day, send_hour=hour,
                                        channel="sms", message_template=SMS_COPY[n], is_active=True))
        _item(db, run, "cadence_template", tpl.id, "create", None, {"name": CADENCE_NAME}, "Batch 1 schedule")
        changes["cadence_template_created"] += 1
    # 4. The campaign, INACTIVE, with every step drafted.
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key=CAMPAIGN_KEY).first()
    if fam is None:
        copy = {"sms": {str(n): SMS_COPY[n] for n in SMS_COPY if n > 1},
                "email": {str(n): {"subject": s, "body": b} for n, (s, b) in EMAIL_COPY.items() if n > 1},
                "draft": True, "sender": SENDER_E164}
        fam = CampaignFamily(organization_id=org.id, key=CAMPAIGN_KEY, name=CAMPAIGN_NAME,
                             source_campaign_patterns="[]", language="en", sms_template=SMS_COPY[1],
                             email_subject_template=EMAIL_COPY[1][0], email_body_template=EMAIL_COPY[1][1],
                             touch_copy_json=json.dumps(copy), is_active=False)
        db.add(fam)
        db.flush()
        _item(db, run, "campaign_family", fam.id, "create", None, {"key": CAMPAIGN_KEY, "is_active": False},
              "Batch 1 drafted; off")
        changes["campaign_created"] += 1
    # 5. Dispositions and the audience snapshot (upsert; same run key = same rows).
    have_d = {d.lead_id: d for d in db.query(LeadDisposition).filter_by(organization_id=org.id).all()}
    for lid, d in p["dispositions"].items():
        row = have_d.get(lid) or LeadDisposition(organization_id=org.id, lead_id=lid)
        row.disposition, row.review_queue = d["disposition"], d["review_queue"]
        row.phone_e164, row.phone_issue, row.email_issue = d["phone_e164"], d["phone_issue"], d["email_issue"]
        row.name_issue, row.canonical_lead_id = d["name_issue"], d["canonical_lead_id"]
        row.shares_phone_with, row.notes_json, row.run_id = d["shares_phone_with"], json.dumps(d["notes"]), run.id
        if lid not in have_d:
            db.add(row)
    have_m = {m.lead_id: m for m in db.query(CampaignAudienceMember)
              .filter_by(organization_id=org.id, campaign_key=CAMPAIGN_KEY).all()}
    for lid, m in p["members"].items():
        row = have_m.get(lid) or CampaignAudienceMember(organization_id=org.id, campaign_key=CAMPAIGN_KEY, lead_id=lid)
        row.segment = m["segment"]
        row.status = "proposed" if m.get("batch") else ("eligible" if m["segment"] == "ready" else "excluded")
        row.reasons_json, row.destination = json.dumps(m["reasons"]), m["destination"]
        row.batch, row.is_staged, row.run_id = m.get("batch"), bool(m.get("batch")), run.id
        if lid not in have_m:
            db.add(row)
    changes["dispositions"] = len(p["dispositions"])
    changes["audience_rows"] = len(p["members"])
    run.status, run.finished_at = "applied", datetime.utcnow()
    run.summary_json = json.dumps({"plan": p["summary"], "changes": dict(changes), "applied_at": now.isoformat() + "Z"})
    db.commit()
    return {"run_id": run.id, "status": run.status, "already": False, "summary": json.loads(run.summary_json)}


def rollback(db: Session, org: Organization, run_id: str) -> Dict:
    run = db.query(WorkspaceResetRun).filter_by(id=run_id, organization_id=org.id, mode="apply").first()
    if run is None:
        raise ResetRefused("Run not found for this organization.")
    if run.status == "rolled_back":
        return {"run_id": run.id, "status": "rolled_back", "already": True}
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key=CAMPAIGN_KEY).first()
    if fam is not None and fam.is_active:
        raise ResetRefused("The campaign is switched on; switch it off before rolling back.")
    if (db.query(ProgramSourceRecord.id).filter(ProgramSourceRecord.organization_id == org.id,
                                                ProgramSourceRecord.campaign_family == CAMPAIGN_KEY,
                                                ProgramSourceRecord.approved_to_send_at.isnot(None)).first()):
        raise ResetRefused("Families were approved into the campaign since this run; reconcile first.")
    restored = defaultdict(int)
    for it in (db.query(WorkspaceResetItem).filter_by(run_id=run.id).filter(WorkspaceResetItem.reverted_at.is_(None))
               .order_by(WorkspaceResetItem.created_at.desc()).all()):
        before = json.loads(it.before_json or "null")
        if it.entity == "cadence_state":
            st = db.query(CadenceState).filter_by(id=it.entity_id).first()
            if st is not None and st.status == "paused":
                st.status = before["status"]
                restored["cadence_state"] += 1
        elif it.entity == "pipeline_conversation":
            pc = db.query(PipelineConversation).filter_by(id=it.entity_id).first()
            if pc is not None:
                pc.paused = before["paused"]
                pc.next_send_at = datetime.fromisoformat(before["next_send_at"]) if before["next_send_at"] else None
                restored["pipeline_conversation"] += 1
        elif it.entity == "cadence_template" and it.action == "unset_default":
            t = db.query(CadenceTemplate).filter_by(id=it.entity_id).first()
            if t is not None:
                t.is_default = True
                restored["template_default"] += 1
        elif it.entity == "cadence_template" and it.action == "create":
            db.query(CadenceTemplateTouch).filter_by(template_id=it.entity_id).delete(synchronize_session=False)
            db.query(CadenceTemplate).filter_by(id=it.entity_id).delete(synchronize_session=False)
            restored["template_removed"] += 1
        elif it.entity == "campaign_family" and it.action == "create":
            db.query(CampaignFamily).filter_by(id=it.entity_id, is_active=False).delete(synchronize_session=False)
            restored["campaign_removed"] += 1
        it.reverted_at = datetime.utcnow()
    restored["audience_rows"] = (db.query(CampaignAudienceMember).filter_by(organization_id=org.id, run_id=run.id)
                                 .delete(synchronize_session=False))
    restored["dispositions"] = (db.query(LeadDisposition).filter_by(organization_id=org.id, run_id=run.id)
                                .delete(synchronize_session=False))
    run.status, run.rolled_back_at = "rolled_back", datetime.utcnow()
    db.commit()
    return {"run_id": run.id, "status": "rolled_back", "restored": dict(restored)}


def last_run(db: Session, org_id: str) -> Optional[WorkspaceResetRun]:
    return (db.query(WorkspaceResetRun).filter_by(organization_id=org_id, mode="apply")
            .order_by(WorkspaceResetRun.started_at.desc()).first())
