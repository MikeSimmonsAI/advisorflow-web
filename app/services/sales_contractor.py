"""Contractor onboarding for the EvoSys Pro sales team: the owner's side of
invite -> account -> application -> agreement -> documents -> review ->
approval, and the contractor's in-app agreement signing.

What each status means (kept separate on purpose):
  * Invite sent          the email provider accepted our message. It is not
                         proof anyone read it.
  * Account verified     the person opened the one-time link we emailed and
                         set a password (so they control that address), or
                         they already had an EvoSys Pro login.
  * Document reviewed    an owner opened that exact file and accepted it or
                         wrote what to correct. This is a MANUAL review; no
                         identity-verification service is connected.
  * Agreement signed     the contractor read the agreement in their signed-in
                         session, agreed to sign electronically, typed their
                         legal name and pressed Sign. The signed PDF (with a
                         certificate page: who, which version, when, from
                         where) is stored as their contractor-agreement file.
  * Approved             the owner's final yes; the Sales Workspace opens.

Files never go out by email and never get a public link. Every view and
download is written to the audit log; file contents are not.
"""
from __future__ import annotations

import hashlib
import html as _html
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_contractor_models import (ENV_AWAITING_COUNTERSIGN, ENV_COMPLETED, ENV_CORRECTION,
                                                ENV_LABELS, ENV_SENT, ENV_VOID, REVIEW_ACCEPTED,
                                                REVIEW_NEEDS_CORRECTION, TPL_APPROVED, TPL_DRAFT,
                                                TPL_RETIRED, SalesAgreementEnvelope,
                                                SalesAgreementTemplate, SalesInviteSend,
                                                SalesOnboardingReview)
from app.models.sales_team_models import (DOC_AGREEMENT, DOC_KINDS, DOC_LABELS, ONB_APPROVED,
                                          ONB_RETURNED, ONB_SUBMITTED, SalesOnboarding,
                                          SalesOnboardingDocument)
from app.services import sales_onboarding as onb_svc
from app.services import sales_team as team
from app.routers.audit_log_router import log_action

DRAFT_MARKERS = ("DRAFT FOR LEGAL REVIEW", "NOT FOR SIGNATURE")
APP_BASE = "https://app.evosyspro.live"


# ── small helpers ────────────────────────────────────────────────────────────

def _iso(d) -> Optional[str]:
    return d.isoformat() + "Z" if d else None


def _norm(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _send_email(to: str, subject: str, body_html: str) -> bool:
    try:
        from app.services.email_service import send_email_via_provider
        r = send_email_via_provider(to_email=to, subject=subject, body_html=body_html,
                                    message_type="transactional")
        return bool(r and r.get("success"))
    except Exception:
        return False


def _base(base_url: Optional[str]) -> str:
    return (base_url or APP_BASE).rstrip("/")


def _admin(db: Session, actor: User) -> Dict[str, Any]:
    rights = team.actor_rights(db, actor)
    if not rights["can_approve"]:
        raise HTTPException(status_code=403, detail="Only God Mode and the owners can do this.")
    return rights


def record_send(db: Session, brand_id: str, user_id: str, actor: Optional[User], kind: str,
                link_issued: bool, ok: bool) -> SalesInviteSend:
    row = SalesInviteSend(brand_sales_org_id=brand_id, user_id=user_id,
                          sent_by=actor.id if actor else None, kind=kind,
                          link_issued=link_issued, email_ok=ok)
    db.add(row)
    db.flush()
    return row


# ── account / email verification ─────────────────────────────────────────────

def account_status(db: Session, user: User) -> Dict[str, Any]:
    from app.models.staff_models import STAFF_INVITE_ACCEPTED, StaffActivation
    acc = (db.query(StaffActivation)
           .filter(StaffActivation.user_id == user.id, StaffActivation.status == STAFF_INVITE_ACCEPTED)
           .order_by(StaffActivation.created_at.desc()).first())
    if not user.must_change_password:
        if acc is not None:
            return {"verified": True, "how": "Opened the emailed set-up link and set a password",
                    "at": _iso(getattr(acc, "accepted_at", None) or acc.last_sent_at)}
        return {"verified": True, "how": "Already had an EvoSys Pro login", "at": None}
    return {"verified": False, "how": "Has not opened the set-up link yet", "at": None}


def resend_invite(db: Session, actor: User, target_id: str, base_url: Optional[str]) -> Dict[str, Any]:
    """A fresh invite. If the person still has to set a password, a NEW one-time
    link is issued and every older link stops working. Someone who already has
    an EvoSys Pro login just gets a sign-in reminder - nothing else to do."""
    from app.models.staff_models import PURPOSE_SETUP
    from app.services import staff_activation
    brand = team._require_brand(db)
    rights = team.actor_rights(db, actor)
    if not (rights["is_admin"] or rights["is_leader"]):
        raise HTTPException(status_code=403, detail="Only the owners and the Sr EVP can send invites.")
    if not rights["is_admin"] and target_id not in team.team_of(db, brand.id, actor.id):
        raise HTTPException(status_code=403, detail="That person is not on your team.")
    user = db.query(User).filter(User.id == target_id).first()
    onb = onb_svc.record(db, target_id, brand.id) if user else None
    if user is None or onb is None:
        raise HTTPException(status_code=404, detail="That person has no onboarding to invite to.")
    if onb.status == ONB_APPROVED:
        raise HTTPException(status_code=409, detail="This person is already approved.")
    setup_url = None
    if user.must_change_password:
        _row, raw = staff_activation.issue(db, user, actor, brand_sales_org_id=brand.id, purpose=PURPOSE_SETUP)
        setup_url = staff_activation.activation_url(base_url, raw)
    seat = team._seat_of_member(db, user, team._member(db, brand.id, user.id), brand.id)
    ok = team._send_invite_email(user, actor, seat, setup_url, base_url)
    record_send(db, brand.id, user.id, actor, "resend", bool(setup_url), ok)
    log_action(db, None, actor.id, action="sales_invite_resent", target_type="user", target_id=user.id,
               brand_sales_org_id=brand.id, after={"new_link": bool(setup_url), "email_ok": ok},
               note="Link tokens are never recorded.", commit=False)
    db.commit()
    return {"email_sent": ok, "new_link_issued": bool(setup_url),
            "existing_account": not bool(setup_url),
            "message": ("New set-up link emailed; older links no longer work." if setup_url and ok else
                        "Sign-in reminder emailed (they already have a login)." if ok else
                        "The email could not be sent. Copy the link below and send it yourself."),
            "setup_url": None if ok else setup_url}


# ── document review ──────────────────────────────────────────────────────────

def reviews(db: Session, onb: SalesOnboarding) -> Dict[str, SalesOnboardingReview]:
    rows = db.query(SalesOnboardingReview).filter(SalesOnboardingReview.onboarding_id == onb.id).all()
    return {r.document_id: r for r in rows}


def review_document(db: Session, actor: User, onb: SalesOnboarding, doc_id: str, status: str,
                    note: Optional[str]) -> Dict[str, Any]:
    _admin(db, actor)
    if status not in (REVIEW_ACCEPTED, REVIEW_NEEDS_CORRECTION):
        raise HTTPException(status_code=422, detail="Accept it or say what needs correcting.")
    doc = (db.query(SalesOnboardingDocument)
           .filter(SalesOnboardingDocument.id == doc_id, SalesOnboardingDocument.onboarding_id == onb.id,
                   SalesOnboardingDocument.is_current.is_(True)).first())
    if doc is None:
        raise HTTPException(status_code=404, detail="That is not this person's current file.")
    note = (note or "").strip()[:1000] or None
    if status == REVIEW_NEEDS_CORRECTION and not note:
        raise HTTPException(status_code=422, detail="Write what needs correcting - the contractor sees it.")
    r = db.query(SalesOnboardingReview).filter(SalesOnboardingReview.document_id == doc.id).first()
    if r is None:
        r = SalesOnboardingReview(onboarding_id=onb.id, document_id=doc.id, status=status)
        db.add(r)
    r.status, r.note, r.reviewed_by, r.reviewed_at = status, note, actor.id, datetime.utcnow()
    log_action(db, None, actor.id, action="sales_onboarding_document_reviewed", target_type="user",
               target_id=onb.user_id, brand_sales_org_id=onb.brand_sales_org_id,
               after={"document_id": doc.id, "kind": doc.kind, "status": status},
               note="Manual review by an owner. File contents are not recorded.", commit=False)
    db.commit()
    return {"document_id": doc.id, "status": status, "note": note}


def documents(db: Session, onb: SalesOnboarding, *, with_files: bool) -> List[Dict[str, Any]]:
    docs = onb_svc.current_docs(db, onb)
    revs = reviews(db, onb)
    out = []
    for k in DOC_KINDS:
        d = docs.get(k)
        r = revs.get(d.id) if d else None
        it = {"kind": k, "label": DOC_LABELS[k], "received": d is not None,
              "review": (r.status if r else ("not_reviewed" if d else None)),
              "review_label": ("Accepted" if r and r.status == REVIEW_ACCEPTED else
                               "Needs correction" if r else "Not reviewed yet" if d else "Missing"),
              "correction_note": r.note if r and r.status == REVIEW_NEEDS_CORRECTION else None,
              "step_link": "/sales-onboarding#doc-%s" % k}
        if with_files and d is not None:
            it.update({"document_id": d.id, "filename": d.filename, "version": d.version,
                       "content_type": d.content_type, "size_bytes": d.size_bytes,
                       "uploaded_at": _iso(d.uploaded_at),
                       "reviewed_at": _iso(r.reviewed_at) if r else None})
        out.append(it)
    return out


def corrections(db: Session, onb: SalesOnboarding) -> List[Dict[str, Any]]:
    """What the contractor must fix, each pointing at the exact step."""
    out = [{"label": d["label"], "note": d["correction_note"], "step_link": d["step_link"]}
           for d in documents(db, onb, with_files=False) if d["review"] == REVIEW_NEEDS_CORRECTION]
    env = current_envelope(db, onb)
    if env is not None and env.status == ENV_CORRECTION and env.note:
        out.append({"label": "Agreement", "note": env.note, "step_link": "/sales-onboarding#agreement"})
    return out


def before_approval(db: Session, onb: SalesOnboarding, actor: User) -> None:
    """Approval needs every current file accepted. A file nobody marked is
    accepted by the approver as part of approving (recorded as such); a file
    marked 'needs correction' blocks approval."""
    docs = onb_svc.current_docs(db, onb)
    revs = reviews(db, onb)
    bad = [DOC_LABELS[k] for k, d in docs.items() if revs.get(d.id) and revs[d.id].status == REVIEW_NEEDS_CORRECTION]
    if bad:
        raise HTTPException(status_code=422, detail="Still marked for correction: " + ", ".join(bad))
    env = current_envelope(db, onb)
    if env is not None and env.status in (ENV_SENT, ENV_AWAITING_COUNTERSIGN, ENV_CORRECTION):
        raise HTTPException(status_code=422, detail="The agreement is not fully signed yet (%s)." % ENV_LABELS[env.status])
    now = datetime.utcnow()
    for d in docs.values():
        if d.id not in revs:
            db.add(SalesOnboardingReview(onboarding_id=onb.id, document_id=d.id, status=REVIEW_ACCEPTED,
                                         note="Accepted when approving", reviewed_by=actor.id, reviewed_at=now))


def notify_corrections(db: Session, actor: User, onb: SalesOnboarding, base_url: Optional[str]) -> bool:
    user = db.query(User).filter(User.id == onb.user_id).first()
    if user is None:
        return False
    items = corrections(db, onb)
    base = _base(base_url)
    lis = "".join('<li><a href="%s%s">%s</a>%s</li>' % (base, _html.escape(i["step_link"]), _html.escape(i["label"]),
                                                         (": " + _html.escape(i["note"])) if i["note"] else "")
                  for i in items)
    html = ("<p>Hi %s,</p><p>Your EvoSys Pro onboarding needs a few corrections before it can be approved.</p>"
            "%s%s<p><a href=\"%s/sales-onboarding\">Open Sales Onboarding</a> (sign in first). "
            "No documents are attached to this email.</p><p>EvoSys Pro</p>"
            % (_html.escape(user.full_name or ""),
               ("<p><b>Note:</b> %s</p>" % _html.escape(onb.returned_note)) if onb.returned_note else "",
               ("<ul>%s</ul>" % lis) if lis else "", base))
    ok = _send_email(user.email, "Corrections needed on your EvoSys Pro onboarding", html)
    record_send(db, onb.brand_sales_org_id, user.id, actor, "corrections", False, ok)
    return ok


# ── agreement templates ──────────────────────────────────────────────────────

def _text_to_html(text: str) -> str:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text or "") if b.strip()]
    out = []
    for b in blocks:
        if b.startswith("# "):
            out.append("<h2>%s</h2>" % _html.escape(b[2:].strip()))
        elif b.startswith("## "):
            out.append("<h3>%s</h3>" % _html.escape(b[3:].strip()))
        else:
            out.append("<p>%s</p>" % _html.escape(b).replace("\n", "<br/>"))
    return "\n".join(out)


def template_out(t: SalesAgreementTemplate) -> Dict[str, Any]:
    return {"id": t.id, "title": t.title, "version_label": t.version_label,
            "exhibit_version": t.exhibit_version, "status": t.status,
            "requires_countersign": bool(t.requires_countersign),
            "approved_at": _iso(t.approved_at), "created_at": _iso(t.created_at),
            "is_draft_text": any(m in (t.body_html or "").upper() for m in DRAFT_MARKERS)}


def list_templates(db: Session, actor: User) -> List[Dict[str, Any]]:
    _admin(db, actor)
    brand = team._require_brand(db)
    rows = (db.query(SalesAgreementTemplate).filter(SalesAgreementTemplate.brand_sales_org_id == brand.id)
            .order_by(SalesAgreementTemplate.created_at.desc()).all())
    return [template_out(t) for t in rows]


def create_template(db: Session, actor: User, *, title: str, version_label: str, exhibit_version: str,
                    body_text: str, requires_countersign: bool) -> Dict[str, Any]:
    _admin(db, actor)
    brand = team._require_brand(db)
    if not (title or "").strip() or not (version_label or "").strip() or not (exhibit_version or "").strip():
        raise HTTPException(status_code=422, detail="Title, agreement version and Exhibit A version are required.")
    if len((body_text or "").strip()) < 50:
        raise HTTPException(status_code=422, detail="Paste the full agreement text.")
    t = SalesAgreementTemplate(brand_sales_org_id=brand.id, title=title.strip()[:200],
                               version_label=version_label.strip()[:60],
                               exhibit_version=exhibit_version.strip()[:60],
                               body_html=_text_to_html(body_text[:200_000]), status=TPL_DRAFT,
                               requires_countersign=bool(requires_countersign), created_by=actor.id)
    db.add(t)
    db.flush()
    log_action(db, None, actor.id, action="sales_agreement_template_created", target_type="sales_agreement_template",
               target_id=t.id, brand_sales_org_id=brand.id,
               after={"title": t.title, "version": t.version_label, "exhibit": t.exhibit_version}, commit=False)
    db.commit()
    return template_out(t)


def approve_template(db: Session, actor: User, template_id: str, approved_for_signing: bool) -> Dict[str, Any]:
    _admin(db, actor)
    t = db.query(SalesAgreementTemplate).filter(SalesAgreementTemplate.id == template_id).first()
    if t is None:
        raise HTTPException(status_code=404, detail="Template not found.")
    if not approved_for_signing:
        raise HTTPException(status_code=422, detail="Confirm this version is approved for signing.")
    if any(m in (t.body_html or "").upper() for m in DRAFT_MARKERS):
        raise HTTPException(status_code=422,
                            detail="This text is still marked DRAFT FOR LEGAL REVIEW / NOT FOR SIGNATURE. "
                                   "Only a version approved for signing can be sent.")
    t.status, t.approved_by, t.approved_at = TPL_APPROVED, actor.id, datetime.utcnow()
    log_action(db, None, actor.id, action="sales_agreement_template_approved", target_type="sales_agreement_template",
               target_id=t.id, brand_sales_org_id=t.brand_sales_org_id,
               after={"version": t.version_label, "exhibit": t.exhibit_version}, commit=False)
    db.commit()
    return template_out(t)


def retire_template(db: Session, actor: User, template_id: str) -> Dict[str, Any]:
    _admin(db, actor)
    t = db.query(SalesAgreementTemplate).filter(SalesAgreementTemplate.id == template_id).first()
    if t is None:
        raise HTTPException(status_code=404, detail="Template not found.")
    t.status = TPL_RETIRED
    db.commit()
    return template_out(t)


# ── this person's compensation terms (their own layer only) ──────────────────

def exhibit_html(seat: str, exhibit_version: str) -> str:
    from app.services import deal_builder_commission as dbc
    role = {"sevp": "sevp", "asm": "asm"}.get(seat, "rep")
    label = dbc.ROLE_LABELS.get(role, role)
    rows = "".join("<li>%s (setup %s): <b>$%s</b> when the setup is collected</li>"
                   % (_html.escape(p["name"]), "${:,.0f}".format(p["setup"]), "{:,.0f}".format(p["payout"][role]))
                   for p in dbc.PLANS.values() if p["kind"] == "package")
    return ("<h2>Exhibit A — Your compensation (%s, version %s)</h2>"
            "<p>These are the commission terms for the %s seat only. Commission rules version %s.</p>"
            "<ul>%s<li>Package monthly service from Month 2 onward: <b>%s%%</b> of the monthly fee collected. Month 1 covers the flat setup fee only</li>"
            "<li>Custom SaaS setup and monthly service: <b>%s%%</b> of what is collected</li>"
            "<li>Add-ons follow the deal they are sold on.</li>"
            "<li>Commission is held 14 days after the customer pays. Partial payments are prorated.</li></ul>"
            % (_html.escape(label), _html.escape(exhibit_version), _html.escape(label), dbc.RULES_VERSION, rows,
               dbc.PACKAGE_MONTHLY_PCT[role], dbc.SAAS_PCT[role]))


# ── agreement envelopes ──────────────────────────────────────────────────────

def current_envelope(db: Session, onb: SalesOnboarding) -> Optional[SalesAgreementEnvelope]:
    return (db.query(SalesAgreementEnvelope)
            .filter(SalesAgreementEnvelope.onboarding_id == onb.id, SalesAgreementEnvelope.status != ENV_VOID)
            .order_by(SalesAgreementEnvelope.sent_at.desc()).first())


def envelope_out(env: Optional[SalesAgreementEnvelope], *, with_html: bool = False) -> Optional[Dict[str, Any]]:
    if env is None:
        return None
    out = {"id": env.id, "status": env.status, "status_label": ENV_LABELS.get(env.status, env.status),
           "title": env.title, "version_label": env.version_label, "exhibit_version": env.exhibit_version,
           "sent_at": _iso(env.sent_at), "viewed_at": _iso(env.viewed_at), "signed_at": _iso(env.signed_at),
           "signer_typed_name": env.signer_typed_name, "countersigned_at": _iso(env.countersigned_at),
           "note": env.note if env.status == ENV_CORRECTION else None,
           "document_sha256": env.document_sha256, "final_sha256": env.final_sha256,
           "has_signed_copy": bool(env.final_document_id)}
    if with_html:
        out["document_html"] = env.document_html
    return out


def send_agreement(db: Session, actor: User, onb: SalesOnboarding, template_id: str,
                   base_url: Optional[str]) -> Dict[str, Any]:
    _admin(db, actor)
    t = db.query(SalesAgreementTemplate).filter(SalesAgreementTemplate.id == template_id,
                                                SalesAgreementTemplate.brand_sales_org_id == onb.brand_sales_org_id).first()
    if t is None:
        raise HTTPException(status_code=404, detail="Template not found.")
    if t.status != TPL_APPROVED:
        raise HTTPException(status_code=422, detail="Only an agreement version approved for signing can be sent.")
    if onb.status == ONB_APPROVED:
        raise HTTPException(status_code=409, detail="This person is already approved.")
    user = db.query(User).filter(User.id == onb.user_id).first()
    brand = team._require_brand(db)
    seat = team._seat_of_member(db, user, team._member(db, brand.id, user.id), brand.id)
    for old in db.query(SalesAgreementEnvelope).filter(SalesAgreementEnvelope.onboarding_id == onb.id,
                                                       SalesAgreementEnvelope.status.in_((ENV_SENT, ENV_CORRECTION))).all():
        old.status = ENV_VOID
    who = _html.escape(onb.legal_name or user.full_name or user.email)
    doc_html = ("<h1>%s</h1><p><b>Contractor:</b> %s &middot; <b>Agreement version:</b> %s &middot; "
                "<b>Exhibit A version:</b> %s</p>%s%s"
                % (_html.escape(t.title), who, _html.escape(t.version_label), _html.escape(t.exhibit_version),
                   t.body_html, exhibit_html(seat, t.exhibit_version)))
    env = SalesAgreementEnvelope(onboarding_id=onb.id, template_id=t.id, status=ENV_SENT, title=t.title,
                                 version_label=t.version_label, exhibit_version=t.exhibit_version,
                                 document_html=doc_html,
                                 document_sha256=hashlib.sha256(doc_html.encode("utf-8")).hexdigest(),
                                 sent_by=actor.id)
    db.add(env)
    db.flush()
    base = _base(base_url)
    ok = _send_email(user.email, "Your EvoSys Pro contractor agreement is ready to sign",
                     "<p>Hi %s,</p><p>Your contractor agreement (version %s) is ready. Sign in and open "
                     "<a href=\"%s/sales-onboarding#agreement\">Sales Onboarding &rarr; Agreement</a> to read, "
                     "download and sign it.</p><p>The agreement is not attached to this email.</p><p>EvoSys Pro</p>"
                     % (_html.escape(user.full_name or ""), _html.escape(t.version_label), base))
    record_send(db, onb.brand_sales_org_id, user.id, actor, "agreement", False, ok)
    log_action(db, None, actor.id, action="sales_agreement_sent", target_type="user", target_id=user.id,
               brand_sales_org_id=onb.brand_sales_org_id,
               after={"envelope_id": env.id, "version": t.version_label, "exhibit": t.exhibit_version,
                      "sha256": env.document_sha256, "email_ok": ok}, commit=False)
    db.commit()
    return {"agreement": envelope_out(env), "email_sent": ok}


def _matches(typed: str, *names: Optional[str]) -> bool:
    t = _norm(typed)
    return bool(t) and any(t == _norm(n) for n in names if n)


def mark_viewed(db: Session, onb: SalesOnboarding, env: SalesAgreementEnvelope) -> None:
    if env.viewed_at is None:
        env.viewed_at = datetime.utcnow()
        log_action(db, None, onb.user_id, action="sales_agreement_viewed", target_type="user", target_id=onb.user_id,
                   brand_sales_org_id=onb.brand_sales_org_id, after={"envelope_id": env.id}, commit=False)
        db.commit()


def sign(db: Session, user: User, onb: SalesOnboarding, *, typed_name: str, consent: bool,
         ip: Optional[str], ua: Optional[str]) -> Dict[str, Any]:
    from app.services.evosys_esign import CONSENT_VERSION
    if onb.user_id != user.id:
        raise HTTPException(status_code=403, detail="Only the contractor can sign their agreement.")
    env = current_envelope(db, onb)
    if env is None or env.status != ENV_SENT:
        raise HTTPException(status_code=409, detail="There is no agreement waiting for your signature.")
    if not consent:
        raise HTTPException(status_code=422, detail="Agree to sign electronically first.")
    if not _matches(typed_name, onb.legal_name, user.full_name):
        raise HTTPException(status_code=422, detail="Type your full legal name exactly as it appears on your application.")
    now = datetime.utcnow()
    env.consent_at = env.consent_at or now
    env.signer_typed_name = typed_name.strip()[:200]
    env.signed_at = now
    env.sign_ip = (ip or "")[:64] or None
    env.sign_user_agent = (ua or "")[:300] or None
    t = db.query(SalesAgreementTemplate).filter(SalesAgreementTemplate.id == env.template_id).first()
    log_action(db, None, user.id, action="sales_agreement_signed", target_type="user", target_id=user.id,
               brand_sales_org_id=onb.brand_sales_org_id,
               after={"envelope_id": env.id, "version": env.version_label, "exhibit": env.exhibit_version,
                      "sha256": env.document_sha256, "consent_version": CONSENT_VERSION}, commit=False)
    if t is not None and t.requires_countersign:
        env.status = ENV_AWAITING_COUNTERSIGN
        db.commit()
    else:
        _finalize(db, onb, env, user)
    return {"agreement": envelope_out(env)}


def countersign(db: Session, actor: User, onb: SalesOnboarding, typed_name: str, ip: Optional[str]) -> Dict[str, Any]:
    _admin(db, actor)
    env = current_envelope(db, onb)
    if env is None or env.status != ENV_AWAITING_COUNTERSIGN:
        raise HTTPException(status_code=409, detail="Nothing is waiting for a countersignature.")
    if not _matches(typed_name, actor.full_name):
        raise HTTPException(status_code=422, detail="Type your own full name to countersign.")
    env.countersigner_id = actor.id
    env.countersign_typed_name = typed_name.strip()[:200]
    env.countersigned_at = datetime.utcnow()
    env.countersign_ip = (ip or "")[:64] or None
    log_action(db, None, actor.id, action="sales_agreement_countersigned", target_type="user", target_id=onb.user_id,
               brand_sales_org_id=onb.brand_sales_org_id, after={"envelope_id": env.id}, commit=False)
    user = db.query(User).filter(User.id == onb.user_id).first()
    _finalize(db, onb, env, user)
    return {"agreement": envelope_out(env)}


def _finalize(db: Session, onb: SalesOnboarding, env: SalesAgreementEnvelope, signer: User) -> None:
    """Seal: build the signed PDF with its certificate page, store it privately
    as this person's contractor-agreement file, fingerprint it."""
    from app.services import esign_pdf
    from app.services import wholesale_files as storage
    from app.services.evosys_esign import CONSENT_VERSION
    env.status = ENV_COMPLETED
    signers = [{"id": "contractor", "role": "Contractor", "name": onb.legal_name or signer.full_name,
                "email": signer.email, "signature_kind": "typed", "signature_text": env.signer_typed_name,
                "signature_image": None, "signed_at": env.signed_at, "verified_at": None,
                "consent_at": env.consent_at, "sign_ip": env.sign_ip, "sign_user_agent": env.sign_user_agent}]
    events = [{"action": "sent", "at": env.sent_at, "ip": None, "signer_id": None},
              {"action": "viewed", "at": env.viewed_at or env.signed_at, "ip": env.sign_ip, "signer_id": "contractor"},
              {"action": "signed (in signed-in EvoSys Pro session; consent %s)" % CONSENT_VERSION,
               "at": env.signed_at, "ip": env.sign_ip, "signer_id": "contractor"}]
    if env.countersigned_at:
        cs = db.query(User).filter(User.id == env.countersigner_id).first()
        signers.append({"id": "company", "role": "EvoSys Pro", "name": cs.full_name if cs else "EvoSys Pro",
                        "email": cs.email if cs else "", "signature_kind": "typed",
                        "signature_text": env.countersign_typed_name, "signature_image": None,
                        "signed_at": env.countersigned_at, "verified_at": None, "consent_at": env.countersigned_at,
                        "sign_ip": env.countersign_ip, "sign_user_agent": None})
        events.append({"action": "countersigned", "at": env.countersigned_at, "ip": env.countersign_ip,
                       "signer_id": "company"})
    envelope = {"id": env.id, "title": "%s (version %s, Exhibit A %s)" % (env.title, env.version_label, env.exhibit_version),
                "status": "completed", "document_html": env.document_html, "document_sha256": env.document_sha256,
                "sender_name": "EvoSys Pro", "sender_email": "", "created_at": env.sent_at,
                "completed_at": env.countersigned_at or env.signed_at, "brand": "EvoSys Pro"}
    pdf = esign_pdf.render_signed_pdf(envelope, signers, events)
    stored = storage.put(onb.brand_sales_org_id, onb_svc.STORAGE_KIND, pdf, "application/pdf", db=db)
    prior = (db.query(SalesOnboardingDocument).filter(SalesOnboardingDocument.onboarding_id == onb.id,
                                                      SalesOnboardingDocument.kind == DOC_AGREEMENT).all())
    for p in prior:
        p.is_current = False
    sha = hashlib.sha256(pdf).hexdigest()
    doc = SalesOnboardingDocument(onboarding_id=onb.id, kind=DOC_AGREEMENT, version=len(prior) + 1, is_current=True,
                                  storage_backend=stored["storage_backend"], storage_key=stored["storage_key"],
                                  filename="Signed contractor agreement v%s.pdf" % re.sub(r"[^A-Za-z0-9._-]", "_", env.version_label),
                                  content_type="application/pdf", size_bytes=len(pdf), sha256=sha,
                                  uploaded_by=signer.id)
    db.add(doc)
    db.flush()
    db.add(SalesOnboardingReview(onboarding_id=onb.id, document_id=doc.id, status=REVIEW_ACCEPTED,
                                 note="Signed in EvoSys Pro", reviewed_by=None, reviewed_at=datetime.utcnow()))
    env.final_document_id, env.final_sha256 = doc.id, sha
    log_action(db, None, signer.id, action="sales_agreement_completed", target_type="user", target_id=onb.user_id,
               brand_sales_org_id=onb.brand_sales_org_id,
               after={"envelope_id": env.id, "document_id": doc.id, "pdf_sha256": sha}, commit=False)
    db.commit()


def request_agreement_correction(db: Session, actor: User, onb: SalesOnboarding, note: str) -> Dict[str, Any]:
    """Either side can stop an unsigned agreement: the contractor asks a
    question or spots an error; the owner pulls it to fix the version."""
    env = current_envelope(db, onb)
    if env is None or env.status != ENV_SENT:
        raise HTTPException(status_code=409, detail="There is no unsigned agreement to correct.")
    is_signer = actor.id == onb.user_id
    if not is_signer:
        _admin(db, actor)
    note = (note or "").strip()[:1000]
    if not note:
        raise HTTPException(status_code=422, detail="Say what needs to change.")
    env.status, env.note = ENV_CORRECTION, ("Contractor: " if is_signer else "Owner: ") + note
    log_action(db, None, actor.id, action="sales_agreement_correction_requested", target_type="user",
               target_id=onb.user_id, brand_sales_org_id=onb.brand_sales_org_id,
               after={"envelope_id": env.id, "by": "contractor" if is_signer else "owner"}, commit=False)
    db.commit()
    return {"agreement": envelope_out(env)}


def signed_copy(db: Session, actor: User, onb: SalesOnboarding) -> bytes:
    env = current_envelope(db, onb)
    if env is None or not env.final_document_id:
        raise HTTPException(status_code=404, detail="There is no signed copy yet.")
    if actor.id != onb.user_id:
        _admin(db, actor)
    doc = db.query(SalesOnboardingDocument).filter(SalesOnboardingDocument.id == env.final_document_id).first()
    return onb_svc.read_document(db, doc, onb, actor, "downloaded")


# ── where this person is, from invite to selling ─────────────────────────────

def journey(db: Session, onb: SalesOnboarding, user: User) -> Dict[str, Any]:
    from app.models.sales_models import Opportunity
    sends = (db.query(SalesInviteSend).filter(SalesInviteSend.user_id == user.id,
                                              SalesInviteSend.brand_sales_org_id == onb.brand_sales_org_id)
             .order_by(SalesInviteSend.sent_at.desc()).all())
    last_invite = next((s for s in sends if s.kind in ("invite", "resend")), None)
    acct = account_status(db, user)
    chk = onb_svc.checklist(db, onb, with_file_names=False)
    items = {i["key"]: i for i in chk["items"]}
    docs = documents(db, onb, with_files=False)
    env = current_envelope(db, onb)
    uploads_done = all(items[k]["done"] for k in DOC_KINDS if k != DOC_AGREEMENT)
    agreement_done = items[DOC_AGREEMENT]["done"] and (env is None or env.status == ENV_COMPLETED)
    reviewed = all(d["review"] == REVIEW_ACCEPTED for d in docs if d["received"]) and all(d["received"] for d in docs)
    needs_fix = any(d["review"] == REVIEW_NEEDS_CORRECTION for d in docs)
    deals = db.query(Opportunity).filter(Opportunity.owner_user_id == user.id,
                                         Opportunity.brand_sales_org_id == onb.brand_sales_org_id).count()
    approved = onb.status == ONB_APPROVED
    if env is None:
        ag = ("owner", "Send the agreement for signature") if not items[DOC_AGREEMENT]["done"] else ("—", "Uploaded agreement on file")
    elif env.status == ENV_SENT:
        ag = ("contractor", "Read and sign the agreement")
    elif env.status == ENV_AWAITING_COUNTERSIGN:
        ag = ("owner", "Countersign the agreement")
    elif env.status == ENV_CORRECTION:
        ag = ("owner", "Fix the agreement and send a new version")
    else:
        ag = ("—", "Signed")
    steps = [
        {"key": "invite", "label": "Invite sent", "done": True, "owner": "owner",
         "detail": (("Email accepted by the provider" if last_invite.email_ok else "Email could not be sent — resend")
                    if last_invite else "Invited before send tracking started"),
         "at": _iso(last_invite.sent_at) if last_invite else _iso(onb.created_at)},
        {"key": "account", "label": "Email & account verified", "done": acct["verified"], "owner": "contractor",
         "detail": acct["how"], "at": acct["at"]},
        {"key": "application", "label": "Application", "done": items["application"]["done"], "owner": "contractor",
         "detail": ("Missing: " + ", ".join(items["application"]["missing_fields"])) if items["application"]["missing_fields"] else "Complete"},
        {"key": "agreement", "label": "Agreement", "done": agreement_done, "owner": ag[0], "detail": ag[1],
         "at": _iso(env.signed_at) if env and env.signed_at else None},
        {"key": "documents", "label": "W-9, photo ID (front and back), I-9", "done": uploads_done, "owner": "contractor",
         "detail": "Uploaded" if uploads_done else "Missing: " + ", ".join(d["label"] for d in docs
                                                                         if not d["received"] and d["kind"] != DOC_AGREEMENT)},
        {"key": "submitted", "label": "Sent for review", "done": onb.status in (ONB_SUBMITTED, ONB_APPROVED),
         "owner": "contractor", "detail": "Sent back for corrections" if onb.status == ONB_RETURNED else "",
         "at": _iso(onb.submitted_at)},
        {"key": "review", "label": "Document review (manual, by an owner)", "done": reviewed and not needs_fix,
         "owner": "owner", "detail": "A file needs correction" if needs_fix else ""},
        {"key": "approval", "label": "Owner approval", "done": approved, "owner": "owner", "at": _iso(onb.approved_at)},
        {"key": "selling", "label": "Selling", "done": approved and deals > 0, "owner": "contractor",
         "detail": ("%d deal%s in the pipeline" % (deals, "" if deals == 1 else "s")) if approved else "Opens after approval"},
    ]
    nxt = None
    if needs_fix or onb.status == ONB_RETURNED:
        nxt = {"who": "contractor", "what": "Fix the items sent back and resubmit"}
    else:
        for s in steps:
            if not s["done"]:
                who = s["owner"] if s["owner"] != "—" else "contractor"
                what = {"account": "Open the invite email and set a password",
                        "application": "Finish the application",
                        "agreement": ag[1],
                        "documents": "Upload the missing documents",
                        "submitted": "Send the onboarding for review",
                        "review": "Review the documents",
                        "approval": "Approve",
                        "selling": "Create the first prospect or open assigned leads"}.get(s["key"], s["label"])
                if s["key"] == "submitted" and not (items["application"]["done"] and uploads_done and agreement_done):
                    continue
                nxt = {"who": who, "what": what}
                break
    return {"steps": steps, "next": nxt, "status": onb.status,
            "agreement": envelope_out(env), "corrections": corrections(db, onb)}
