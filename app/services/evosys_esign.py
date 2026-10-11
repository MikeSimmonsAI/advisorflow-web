"""EvoSys e-signature - signing built into the platform. No vendor, no fee.

THE FLOW
    send      envelope = the exact document HTML (+ SHA-256) and the signers in
              order. Signer 1 gets an emailed private link; the others wait.
    open      the link shows the document (event: viewed, IP, browser).
    verify    a 6-digit code is emailed to the signer's address and typed back
              (proves control of the email the sender named). 10-minute expiry,
              5 tries per code, 5 codes per signer, 45 s between codes.
    consent   the signer agrees to sign and receive records electronically
              (ESIGN Act 7001(c) disclosure shown on the page).
    sign      typed name or drawn signature + an explicit Sign action.
              The next signer is emailed; after the last one the envelope is
              SEALED: the PDF (document + signatures + certificate page) is
              built, fingerprinted (SHA-256), stored, and emailed to everyone.
    decline / void / remind / expire (30 days by default).

Link tokens and codes are stored only as SHA-256 hashes. A link stops working
when the envelope is voided, declined, expired or completed (a completed
envelope's link still downloads the signed copy).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.esign_models import EsignEnvelope, EsignEvent, EsignSigner

log = logging.getLogger(__name__)

CONSENT_VERSION = "2026-10-10"
CONSENT_TEXT = (
    "By checking this box you agree to use an electronic signature and to receive this document and "
    "related notices electronically. Your electronic signature is as legally binding as a handwritten one. "
    "You may ask the sender for a paper copy at no charge, and you may withdraw this consent before you "
    "sign by declining and contacting the sender. To view and keep this document you need a device with "
    "a web browser and email; the signed copy is emailed to you as a PDF.")

EXPIRE_DAYS = 30
CODE_TTL = timedelta(minutes=10)
CODE_MAX_ATTEMPTS = 5
CODE_MAX_SENDS = 5
CODE_MIN_GAP = timedelta(seconds=45)
MAX_SIGNATURE_PNG = 300 * 1024
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EsignError(Exception):
    """A refusal a person can read. `status` is the HTTP code to answer with."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.message, self.status = message, status


def _sha(s) -> str:
    if isinstance(s, str):
        s = s.encode("utf-8")
    return hashlib.sha256(s).hexdigest()


def public_base() -> str:
    return (os.environ.get("WHOLESALE_ESIGN_PUBLIC_URL") or os.environ.get("FRONTEND_URL")
            or "https://app.evosyspro.live").rstrip("/")


def sign_url(token: str) -> str:
    return "%s/sign/%s" % (public_base(), token)


def _event(db: Session, env: EsignEnvelope, action: str, *, signer: Optional[EsignSigner] = None,
           ip: Optional[str] = None, ua: Optional[str] = None, detail: Any = None) -> None:
    db.add(EsignEvent(envelope_id=env.id, organization_id=env.organization_id,
                      signer_id=signer.id if signer else None, action=action, at=datetime.utcnow(),
                      ip=(ip or "")[:64] or None, user_agent=(ua or "")[:300] or None,
                      detail=json.dumps(detail) if detail is not None else None))


# ── email ──────────────────────────────────────────────────────────────────

def _org(db: Session, org_id: str):
    from app.models.models import Organization
    return db.query(Organization).filter(Organization.id == org_id).first()


def _send(db: Session, env: EsignEnvelope, to: str, subject: str, html: str, *,
          kind: str, attachments: Optional[List[Dict[str, Any]]] = None) -> bool:
    """Through the platform's brand-resolved sender. Never raises; False when
    the provider did not accept it (the caller records that)."""
    from app.services import email_service
    try:
        r = email_service.send_email_via_provider(
            to_email=to, subject=subject, body_html=html, attachments=attachments,
            org=_org(db, env.organization_id), message_type="esign_%s" % kind)
        ok = bool(r and r.get("success"))
        if not ok:
            log.warning("esign email (%s) to %s not accepted: %s", kind, to, (r or {}).get("error"))
        return ok
    except Exception as exc:  # noqa: BLE001
        log.warning("esign email (%s) failed: %s", kind, exc)
        return False


def _wrap(body: str, org_name: str) -> str:
    return ("<div style='font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;color:#111;"
            "max-width:560px'>%s<p style='color:#777;font-size:12px;margin-top:28px'>Sent with EvoSys "
            "e-signature on behalf of %s.</p></div>" % (body, _esc(org_name or "the sender")))


def _esc(s: Any) -> str:
    import html
    return html.escape(str(s or ""))


def _button(url: str, label: str) -> str:
    return ("<p style='margin:22px 0'><a href='%s' style='background:#1d4ed8;color:#fff;padding:12px 22px;"
            "border-radius:8px;text-decoration:none;font-weight:bold'>%s</a></p>"
            "<p style='font-size:12px;color:#555'>Or open this link: %s</p>" % (_esc(url), _esc(label), _esc(url)))


def _org_name(db: Session, env: EsignEnvelope) -> str:
    o = _org(db, env.organization_id)
    return getattr(o, "name", None) or env.sender_name or "the sender"


def _email_link(db: Session, env: EsignEnvelope, s: EsignSigner, *, reminder: bool = False) -> bool:
    """A fresh private link for this signer (any older link stops working)."""
    token = secrets.token_urlsafe(32)
    s.token_hash = _sha(token)
    s.status = "sent" if s.status in ("waiting", "sent") else s.status
    s.link_sent_at = datetime.utcnow()
    s.links_sent = (s.links_sent or 0) + 1
    who = env.sender_name or _org_name(db, env)
    body = ("<p>Hello %s,</p><p>%s has sent you <b>%s</b> to review and sign electronically.</p>%s%s"
            "<p>This link is only for you - please don't forward it. You'll confirm a one-time code sent to "
            "this email address before signing.</p>" % (
                _esc(s.name or ""), _esc(who), _esc(env.title),
                ("<p>Message from %s: %s</p>" % (_esc(who), _esc(env.message))) if env.message else "",
                _button(sign_url(token), "Review and sign")))
    subject = ("Reminder: please sign %s" if reminder else "Please sign: %s") % env.title
    ok = _send(db, env, s.email, subject, _wrap(body, _org_name(db, env)), kind="link")
    _event(db, env, "reminder" if reminder else "link_sent", signer=s, detail={"delivered": ok})
    return ok


def _current(env_signers: List[EsignSigner]) -> Optional[EsignSigner]:
    for s in sorted(env_signers, key=lambda x: x.sign_order):
        if s.status != "signed":
            return s
    return None


def signers_of(db: Session, env: EsignEnvelope) -> List[EsignSigner]:
    return (db.query(EsignSigner).filter(EsignSigner.envelope_id == env.id)
            .order_by(EsignSigner.sign_order).all())


# ── send ───────────────────────────────────────────────────────────────────

def create_envelope(db: Session, *, org_id: str, title: str, document_html: str,
                    signers: List[Dict[str, Any]], kind: Optional[str] = None, deal_id: Optional[str] = None,
                    document_id: Optional[str] = None, message: Optional[str] = None,
                    sender=None, expire_days: int = EXPIRE_DAYS) -> Tuple[EsignEnvelope, bool]:
    """Create the envelope and email signer 1. Returns (envelope, first_email_delivered)."""
    clean = []
    for i, p in enumerate(signers or [], start=1):
        email = (p.get("email") or "").strip()
        if not EMAIL_RE.match(email):
            raise EsignError("%s needs a valid email address." % (p.get("role") or "Signer %d" % i), 400)
        clean.append({"role": (p.get("role") or "Signer %d" % i)[:60], "name": (p.get("name") or "")[:120],
                      "email": email.lower()})
    if not clean:
        raise EsignError("At least one signer is needed.", 400)
    env = EsignEnvelope(organization_id=org_id, deal_id=deal_id, document_id=document_id, kind=kind,
                        title=title[:250], status="sent", message=(message or "")[:2000] or None,
                        document_html=document_html, document_sha256=_sha(document_html),
                        consent_version=CONSENT_VERSION,
                        sender_name=getattr(sender, "full_name", None), sender_email=getattr(sender, "email", None),
                        created_by_id=getattr(sender, "id", None),
                        expires_at=datetime.utcnow() + timedelta(days=expire_days))
    db.add(env)
    db.flush()
    rows = []
    for i, p in enumerate(clean, start=1):
        s = EsignSigner(envelope_id=env.id, organization_id=org_id, sign_order=i, status="waiting", **p)
        db.add(s)
        rows.append(s)
    db.flush()
    _event(db, env, "sent", detail={"signers": [r.email for r in rows], "document_sha256": env.document_sha256})
    delivered = _email_link(db, env, rows[0])
    return env, delivered


# ── the signer's side (public, by link token) ─────────────────────────────

def resolve(db: Session, token: str) -> Tuple[EsignEnvelope, EsignSigner]:
    if not token or len(token) < 20 or len(token) > 100:
        raise EsignError("This signing link is not valid.", 404)
    s = db.query(EsignSigner).filter(EsignSigner.token_hash == _sha(token)).first()
    if s is None:
        raise EsignError("This signing link is not valid or has been replaced by a newer one.", 404)
    env = db.query(EsignEnvelope).filter(EsignEnvelope.id == s.envelope_id).first()
    if env is None:
        raise EsignError("This signing link is not valid.", 404)
    if env.status == "sent" and env.expires_at and env.expires_at < datetime.utcnow():
        env.status = "expired"
        _event(db, env, "expired")
        db.commit()
    return env, s


def _open_for_signing(env: EsignEnvelope, s: EsignSigner) -> None:
    if env.status == "voided":
        raise EsignError("The sender withdrew this document, so it can no longer be signed.", 410)
    if env.status == "declined":
        raise EsignError("This document was declined, so it can no longer be signed.", 410)
    if env.status == "expired":
        raise EsignError("This signing link has expired. Ask the sender to send it again.", 410)
    if env.status == "completed" or s.status == "signed":
        raise EsignError("You have already signed this document.", 409)


def public_view(db: Session, token: str, *, ip=None, ua=None) -> Dict[str, Any]:
    env, s = resolve(db, token)
    if s.viewed_at is None and env.status == "sent":
        s.viewed_at = datetime.utcnow()
        if s.status == "sent":
            s.status = "viewed"
        _event(db, env, "viewed", signer=s, ip=ip, ua=ua)
        db.commit()
    signers = signers_of(db, env)
    name, _, dom = s.email.partition("@")
    return {
        "title": env.title, "status": env.status, "sender": env.sender_name or _org_name(db, env),
        "organization": _org_name(db, env), "message": env.message,
        "signer": {"name": s.name, "role": s.role, "email_masked": name[:2] + "***@" + dom,
                   "status": s.status, "verified": bool(s.verified_at), "signed_at": s.signed_at},
        "parties": [{"role": x.role, "name": x.name, "status": x.status, "you": x.id == s.id} for x in signers],
        "document_html": env.document_html, "document_sha256": env.document_sha256,
        "consent_text": CONSENT_TEXT, "consent_version": env.consent_version,
        "can_sign": env.status == "sent" and s.status not in ("signed", "declined"),
        "completed": env.status == "completed", "expires_at": env.expires_at,
    }


def _code_hash(s: EsignSigner, code: str) -> str:
    return hmac.new(s.id.encode(), code.encode(), hashlib.sha256).hexdigest()


def send_code(db: Session, token: str, *, ip=None, ua=None) -> Dict[str, Any]:
    env, s = resolve(db, token)
    _open_for_signing(env, s)
    now = datetime.utcnow()
    if s.codes_sent >= CODE_MAX_SENDS:
        raise EsignError("Too many codes were requested for this link. Ask the sender to resend it.", 429)
    if s.code_sent_at and now - s.code_sent_at < CODE_MIN_GAP:
        raise EsignError("A code was just sent. Wait a moment, then check your email (and spam folder).", 429)
    code = "%06d" % secrets.randbelow(1_000_000)
    s.code_hash, s.code_expires_at, s.code_attempts = _code_hash(s, code), now + CODE_TTL, 0
    s.codes_sent, s.code_sent_at = (s.codes_sent or 0) + 1, now
    body = ("<p>Your EvoSys signing code for <b>%s</b> is:</p><p style='font-size:30px;letter-spacing:6px;"
            "font-weight:bold'>%s</p><p>It expires in 10 minutes. If you didn't ask for it, ignore this email."
            "</p>" % (_esc(env.title), code))
    ok = _send(db, env, s.email, "Your signing code: %s" % code, _wrap(body, _org_name(db, env)), kind="code")
    _event(db, env, "code_sent", signer=s, ip=ip, ua=ua, detail={"delivered": ok})
    db.commit()
    if not ok:
        raise EsignError("We couldn't email your code right now. Try again in a minute.", 502)
    return {"sent": True, "expires_in_minutes": int(CODE_TTL.total_seconds() // 60)}


def verify_code(db: Session, token: str, code: str, *, ip=None, ua=None) -> Dict[str, Any]:
    env, s = resolve(db, token)
    _open_for_signing(env, s)
    if s.verified_at:
        return {"verified": True}
    code = re.sub(r"\D", "", code or "")
    if not s.code_hash or not s.code_expires_at or s.code_expires_at < datetime.utcnow():
        raise EsignError("That code has expired. Send a new one.", 400)
    if s.code_attempts >= CODE_MAX_ATTEMPTS:
        raise EsignError("Too many wrong tries. Send a new code.", 429)
    if not hmac.compare_digest(_code_hash(s, code), s.code_hash):
        s.code_attempts += 1
        _event(db, env, "code_failed", signer=s, ip=ip, ua=ua)
        db.commit()
        raise EsignError("That code doesn't match. Check the latest email and try again.", 400)
    s.verified_at, s.code_hash = datetime.utcnow(), None
    if s.status in ("sent", "viewed"):
        s.status = "verified"
    _event(db, env, "verified", signer=s, ip=ip, ua=ua)
    db.commit()
    return {"verified": True}


def _png(data_url: Optional[str]) -> Optional[bytes]:
    if not data_url:
        return None
    m = re.match(r"^data:image/png;base64,([A-Za-z0-9+/=\s]+)$", data_url.strip())
    if not m:
        raise EsignError("The drawn signature could not be read. Draw it again.", 400)
    raw = base64.b64decode(m.group(1))
    if len(raw) > MAX_SIGNATURE_PNG or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise EsignError("The drawn signature could not be read. Draw it again.", 400)
    return raw


def sign(db: Session, token: str, *, consent: bool, typed_name: str, drawn_png: Optional[str] = None,
         ip=None, ua=None) -> Dict[str, Any]:
    env, s = resolve(db, token)
    _open_for_signing(env, s)
    if _current(signers_of(db, env)) is not s:
        raise EsignError("It isn't your turn to sign yet.", 409)
    if not s.verified_at:
        raise EsignError("Confirm the code we emailed you first.", 403)
    if not consent:
        raise EsignError("Check the box agreeing to sign electronically.", 400)
    typed = re.sub(r"\s+", " ", (typed_name or "")).strip()
    if len(typed) < 2 or len(typed) > 120:
        raise EsignError("Type your full name.", 400)
    image = _png(drawn_png)
    now = datetime.utcnow()
    s.consent_at = now
    _event(db, env, "consented", signer=s, ip=ip, ua=ua, detail={"consent_version": env.consent_version})
    s.signature_kind = "drawn" if image else "typed"
    s.signature_text, s.signature_image = typed, image
    s.signed_at, s.status = now, "signed"
    s.sign_ip, s.sign_user_agent = (ip or "")[:64] or None, (ua or "")[:300] or None
    _event(db, env, "signed", signer=s, ip=ip, ua=ua,
           detail={"kind": s.signature_kind, "document_sha256": env.document_sha256})
    db.flush()
    nxt = _current(signers_of(db, env))
    if nxt is not None:
        _email_link(db, env, nxt)
        db.commit()
        return {"signed": True, "completed": False, "next": nxt.role}
    finalize(db, env)
    db.commit()
    return {"signed": True, "completed": True}


def decline(db: Session, token: str, reason: str, *, ip=None, ua=None) -> Dict[str, Any]:
    env, s = resolve(db, token)
    _open_for_signing(env, s)
    s.status, s.declined_at, s.decline_reason = "declined", datetime.utcnow(), (reason or "").strip()[:500] or None
    env.status = "declined"
    _event(db, env, "declined", signer=s, ip=ip, ua=ua, detail={"reason": s.decline_reason})
    if env.sender_email:
        _send(db, env, env.sender_email, "Declined: %s" % env.title, _wrap(
            "<p>%s (%s) declined to sign <b>%s</b>.</p>%s" % (
                _esc(s.name or s.email), _esc(s.role), _esc(env.title),
                ("<p>Their reason: %s</p>" % _esc(s.decline_reason)) if s.decline_reason else ""),
            _org_name(db, env)), kind="declined")
    _sync_document(db, env)
    db.commit()
    return {"declined": True}


def signed_pdf_for_token(db: Session, token: str) -> Tuple[str, bytes]:
    env, _s = resolve(db, token)
    if env.status != "completed" or not env.final_pdf:
        raise EsignError("The signed copy is ready once everyone has signed.", 409)
    return env.title, env.final_pdf


# ── sealing ────────────────────────────────────────────────────────────────

def snapshot(db: Session, env: EsignEnvelope) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    cols = lambda o, names: {n: getattr(o, n) for n in names}  # noqa: E731
    e = cols(env, ("id", "title", "status", "document_html", "document_sha256", "sender_name", "sender_email",
                   "created_at", "completed_at"))
    sg = [cols(s, ("id", "role", "name", "email", "signature_kind", "signature_text", "signature_image",
                   "signed_at", "verified_at", "consent_at", "sign_ip", "sign_user_agent")) for s in signers_of(db, env)]
    ev = [cols(x, ("action", "at", "ip", "signer_id")) for x in
          db.query(EsignEvent).filter(EsignEvent.envelope_id == env.id).order_by(EsignEvent.at).all()]
    return e, sg, ev


def finalize(db: Session, env: EsignEnvelope) -> None:
    """Everyone signed: build the PDF, fingerprint it, store it, email it."""
    from app.services import esign_pdf
    env.status, env.completed_at = "completed", datetime.utcnow()
    _event(db, env, "completed")
    db.flush()
    e, sg, ev = snapshot(db, env)
    pdf = esign_pdf.render_signed_pdf(e, sg, ev)
    env.final_pdf, env.final_sha256 = pdf, _sha(pdf)
    att = [{"filename": _filename(env.title), "content": base64.b64encode(pdf).decode("ascii"),
            "content_type": "application/pdf"}]
    recipients = {s["email"] for s in sg}
    if env.sender_email:
        recipients.add(env.sender_email.lower())
    for to in sorted(recipients):
        _send(db, env, to, "Completed: %s" % env.title, _wrap(
            "<p>Everyone has signed <b>%s</b>. Your signed copy is attached.</p>"
            "<p style='font-size:12px;color:#555'>Envelope %s &middot; signed PDF fingerprint (SHA-256) %s</p>" % (
                _esc(env.title), _esc(env.id), _esc(env.final_sha256)), _org_name(db, env)),
            kind="completed", attachments=att)
    _sync_document(db, env)


def _filename(title: str) -> str:
    return (re.sub(r"[^A-Za-z0-9 _.-]+", "", title or "signed").strip().replace(" ", "_")[:80] or "signed") + "_signed.pdf"


def _sync_document(db: Session, env: EsignEnvelope) -> None:
    """Move the deal's document (and the deal) to what happened here."""
    if not env.document_id:
        return
    from app.models.wholesale_models import WholesaleDocument
    doc = db.query(WholesaleDocument).filter(WholesaleDocument.id == env.document_id,
                                             WholesaleDocument.organization_id == env.organization_id).first()
    if doc is None:
        return
    from app.routers.wholesale_contracts_router import apply_signature_outcome
    from app.services import wholesale_esign as esign
    outcome = {"completed": esign.STATUS_SIGNED, "declined": esign.STATUS_DECLINED}.get(env.status)
    if not outcome:
        return
    apply_signature_outcome(db, doc, {"outcome": outcome, "signed_pdf_url": None,
                                      "audit_log_url": None}, actor="EvoSys e-signature")
    if outcome == esign.STATUS_SIGNED:
        doc.file_url = "/wholesale/documents/%s/signed.pdf" % doc.id
        doc.file_name = _filename(env.title)
        doc.notes = ((doc.notes + "\n") if doc.notes else "") + "Signed PDF fingerprint (SHA-256): %s" % env.final_sha256


# ── the sender's side ──────────────────────────────────────────────────────

def for_document(db: Session, org_id: str, document_id: str) -> Optional[EsignEnvelope]:
    return (db.query(EsignEnvelope).filter(EsignEnvelope.organization_id == org_id,
                                           EsignEnvelope.document_id == document_id)
            .order_by(EsignEnvelope.created_at.desc()).first())


def summary(db: Session, env: EsignEnvelope) -> Dict[str, Any]:
    signers = signers_of(db, env)
    cur = _current(signers) if env.status == "sent" else None
    return {
        "envelope_id": env.id, "title": env.title, "status": env.status, "created_at": env.created_at,
        "completed_at": env.completed_at, "expires_at": env.expires_at, "voided_at": env.voided_at,
        "document_sha256": env.document_sha256, "final_sha256": env.final_sha256,
        "waiting_on": cur.role if cur else None,
        "signers": [{"role": s.role, "name": s.name, "email": s.email, "status": s.status,
                     "link_sent_at": s.link_sent_at, "viewed_at": s.viewed_at, "verified_at": s.verified_at,
                     "signed_at": s.signed_at, "declined_at": s.declined_at, "decline_reason": s.decline_reason}
                    for s in signers],
        "events": [{"action": e.action, "at": e.at, "ip": e.ip,
                    "who": next((s.role for s in signers if s.id == e.signer_id), "Sender")}
                   for e in db.query(EsignEvent).filter(EsignEvent.envelope_id == env.id)
                   .order_by(EsignEvent.at).all()],
    }


def remind(db: Session, env: EsignEnvelope) -> Dict[str, Any]:
    if env.status != "sent":
        raise EsignError("Only a document still out for signature can be re-sent.", 409)
    cur = _current(signers_of(db, env))
    if cur is None:
        raise EsignError("Nobody is waiting to sign.", 409)
    if cur.link_sent_at and datetime.utcnow() - cur.link_sent_at < timedelta(minutes=2):
        raise EsignError("A link was just sent. Give it a couple of minutes.", 429)
    ok = _email_link(db, env, cur, reminder=True)
    db.commit()
    return {"sent": ok, "to": cur.role, "email": cur.email}


def void(db: Session, env: EsignEnvelope, reason: Optional[str], user=None) -> Dict[str, Any]:
    if env.status != "sent":
        raise EsignError("Only a document still out for signature can be withdrawn.", 409)
    env.status, env.voided_at, env.void_reason = "voided", datetime.utcnow(), (reason or "")[:250] or None
    _event(db, env, "voided", detail={"reason": env.void_reason, "by": getattr(user, "email", None)})
    for s in signers_of(db, env):
        if s.link_sent_at and s.status not in ("signed", "declined"):
            _send(db, env, s.email, "Withdrawn: %s" % env.title, _wrap(
                "<p>The sender withdrew <b>%s</b>. Your signing link no longer works and nothing more is "
                "needed from you.</p>" % _esc(env.title), _org_name(db, env)), kind="voided")
    if env.document_id:
        from app.models.wholesale_models import WholesaleDocument
        from app.services import wholesale_esign as esign
        doc = db.query(WholesaleDocument).filter(WholesaleDocument.id == env.document_id).first()
        if doc is not None and esign.can_transition(doc.status, esign.STATUS_VOIDED):
            doc.status, doc.signature_status = esign.STATUS_VOIDED, "none"
    db.commit()
    return {"voided": True}
