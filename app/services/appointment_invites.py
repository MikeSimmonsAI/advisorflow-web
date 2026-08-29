"""
Prospect invitations and the secure confirmation link.

SEPARATE FROM `appointment_sync` ON PURPOSE
-------------------------------------------
That module writes to the INTERNAL team's calendars. This one writes to the
PROSPECT. They share nothing but the appointment row, and the two audiences see
deliberately different text: an internal event carries the prospect's phone
number and company, a prospect invitation carries neither the internal notes
nor the names of anyone who has not been introduced.

`appointment.notes` reaches neither. It has no path into this file.

WHY THE CONFIRM LINK IS GET-TO-VIEW, POST-TO-ACT
------------------------------------------------
Corporate mail scanners (Outlook Safe Links, Proofpoint, Mimecast) FETCH every
link in an inbound message to check it. If confirming were a GET, a large share
of invitations would be auto-confirmed by a security appliance seconds after
delivery, and the prospect's actual answer would never be recorded — worse,
the salesperson would see a confirmation nobody made.

So GET renders a page and changes nothing; the confirm and decline actions are
POSTs from that page. This is why the token can safely live in a URL.
"""
import logging
import os
import secrets
from datetime import datetime, timedelta
from html import escape
from typing import Optional

from sqlalchemy.orm import Session

from app.models.calendar_models import AppointmentConfirmationToken, AppointmentSyncLog
from app.models.scheduling_models import (
    SalesAppointment, CONF_PENDING, CONF_SENT, CONF_CONFIRMED, CONF_DECLINED,
    CONF_SRC_PROSPECT_LINK, APPT_CANCELLED,
)
from app.services.ics_builder import build_ics, ics_uid, METHOD_REQUEST, METHOD_CANCEL

log = logging.getLogger(__name__)

# Last-resort host for a confirmation link. The brand's own `app_base_url` is
# what actually gets used (see `confirm_url`); the prospect has no account and
# is never sent anywhere that asks them to log in - /appointments/confirm/:token
# is a public route.
PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL",
    os.environ.get("TRACKING_BASE_URL", "https://advisorflow-backend.onrender.com"),
).rstrip("/")

# Shared with the public-identity resolver, which owns the same judgement for
# family-facing booking and survey links. Imported lazily inside the helper so
# this module keeps no import-time dependency on it.
def _is_infrastructure_host(value) -> bool:
    try:
        from app.services.public_identity import _is_infrastructure_host as _chk
        return _chk(value)
    except Exception:
        low = str(value or "").lower()
        return (".onrender.com" in low or ".vercel.app" in low
                or "localhost" in low or "127.0.0.1" in low)

# A confirmation link outlives the meeting slightly so a late click still lands
# somewhere sensible instead of on an opaque error.
TOKEN_GRACE_DAYS = 2


# ── brand identity ──────────────────────────────────────────────────────────
#
# Keyed on the PLATFORM slug, because a brand sales org sells one brand and the
# prospect must see that brand — not "AdvisorFlow", which is the platform they
# are not buying and have never heard of.
#
# These values are the real, verified EvoSys Pro details. Nothing here is
# invented: an unknown platform falls back to a neutral identity rather than to
# a plausible-looking made-up address, because a bounced invitation is
# recoverable and a wrong-but-believable one is not.

BRAND_IDENTITY = {
    "evosyspro": {
        "name": "EvoSys Pro",
        "from_email": "support@evosyspro.live",
        "support_phone": "469-553-7417",
        "website": "https://evosyspro.live",
        # Where a CUSTOMER-facing link points. The frontend app, not the API —
        # the secure deal portal is a page a human opens, and sending them to
        # the backend host would give them a JSON error.
        "app_base_url": "https://app.evosyspro.live",
        "accent": "#1d4ed8",
    },
}

FALLBACK_IDENTITY = {
    "name": "AdvisorFlow",
    "from_email": None,     # None -> the configured global FROM_EMAIL is used
    "support_phone": None,
    "website": None,
    # Env-driven so a new brand works before it has an entry above.
    "app_base_url": os.environ.get("FRONTEND_URL", "").rstrip("/") or None,
    "accent": "#1d4ed8",
}


def brand_identity_for_brand(db: Session, brand_sales_org_id: str) -> dict:
    """The identity for a brand sales org. Never raises.

    Keyed on brand id rather than on an appointment so proposals, portals and
    any future brand-scoped email can resolve the same identity — one brand
    means one from-address and one support number everywhere, not three
    slightly different ones that drift apart.
    """
    try:
        from app.models.sales_models import BrandSalesOrg
        from app.models.models import Platform
        bso = (db.query(BrandSalesOrg)
               .filter(BrandSalesOrg.id == brand_sales_org_id).first())
        if bso and bso.platform_id:
            plat = db.query(Platform).filter(Platform.id == bso.platform_id).first()
            if plat and plat.slug in BRAND_IDENTITY:
                ident = dict(BRAND_IDENTITY[plat.slug])
                ident["name"] = plat.name or ident["name"]
                return ident
            if plat and plat.name:
                ident = dict(FALLBACK_IDENTITY)
                ident["name"] = plat.name
                return ident
    except Exception:
        log.exception("could not resolve brand identity for brand %s",
                      brand_sales_org_id)
    return dict(FALLBACK_IDENTITY)


def brand_identity(db: Session, appt: SalesAppointment) -> dict:
    """The brand this appointment is sold under. Never raises."""
    return brand_identity_for_brand(db, appt.brand_sales_org_id)


class _SendingOrg(object):
    """Adapter for `send_email_via_provider(org=...)`, which reads
    `from_email` / `resend_api_key` off whatever it is handed.

    A brand sales org is not an Organization — it has no email configuration of
    its own — so this carries the brand's verified from-address without
    pretending a customer tenant is involved.
    """
    def __init__(self, from_email, resend_api_key=None):
        self.from_email = from_email
        self.resend_api_key = resend_api_key


# ── the token ───────────────────────────────────────────────────────────────

def get_or_create_token(db: Session, appt: SalesAppointment,
                        now: Optional[datetime] = None) -> AppointmentConfirmationToken:
    """One live token per appointment, reused across reschedules.

    Reused deliberately. A reschedule sends a fresh invitation, and if that
    carried a new token the prospect's ORIGINAL email would become a dead link.
    People confirm from whichever message they happen to open, which is often
    not the newest one.

    `secrets.token_urlsafe`, not uuid4: this guards a state change on a real
    appointment, and wants CSPRNG entropy rather than a UUID's partly
    structured bytes.
    """
    now = now or datetime.utcnow()
    tok = (db.query(AppointmentConfirmationToken)
           .filter(AppointmentConfirmationToken.appointment_id == appt.id,
                   AppointmentConfirmationToken.revoked_at.is_(None))
           .order_by(AppointmentConfirmationToken.created_at.desc())
           .first())
    if tok is not None:
        # Extend expiry to match the meeting's current time — a rescheduled
        # meeting must not carry an expiry from where it used to be.
        tok.expires_at = (appt.ends_at or now) + timedelta(days=TOKEN_GRACE_DAYS)
        tok.recipient_email = appt.prospect_email or tok.recipient_email
        tok.recipient_name = appt.prospect_name or tok.recipient_name
        return tok

    tok = AppointmentConfirmationToken(
        appointment_id=appt.id,
        token=secrets.token_urlsafe(32),
        recipient_email=appt.prospect_email,
        recipient_name=appt.prospect_name,
        expires_at=(appt.ends_at or now) + timedelta(days=TOKEN_GRACE_DAYS),
        created_at=now,
    )
    db.add(tok)
    db.flush()
    return tok


def confirm_url(token: str, base: Optional[str] = None) -> str:
    """The link a PROSPECT clicks. Branded host, never the API hostname.

    `base` is the brand's own `app_base_url`. The branded frontend serves
    /appointments/confirm/:token, which reads its context and posts the answer
    through the JSON endpoints beside the original HTML page - so the GET stays
    side-effect free and a link scanner still cannot confirm a meeting.

    PUBLIC_BASE_URL remains as a fallback for a brand with no app host, but an
    infrastructure hostname is refused rather than emailed to a prospect: a
    stranger who has never heard of AdvisorFlow reads
    `advisorflow-backend.onrender.com` as a phishing link, and it outlives the
    deployment in their inbox.
    """
    # The guard applies to the brand's own host too, not only to the fallback.
    # A registry entry or a platform row can be wrong, and when it is, the
    # thing that reaches a stranger's inbox is still an infrastructure URL.
    chosen = None
    for candidate in ((base or "").rstrip("/"), (PUBLIC_BASE_URL or "").rstrip("/")):
        if not candidate:
            continue
        if _is_infrastructure_host(candidate):
            log.error("appointment_invites: refusing infrastructure host %r for a "
                      "prospect confirmation link", candidate)
            continue
        chosen = candidate
        break
    if not chosen:
        log.error("appointment_invites: no branded host for a confirmation link "
                  "- refusing to send an infrastructure URL to a prospect")
        return ""
    return "%s/appointments/confirm/%s" % (chosen, token)


def resolve_token(db: Session, token: str,
                  now: Optional[datetime] = None):
    """(token_row, appointment, error_message). Never raises.

    Every rejection returns the SAME shape and a message safe to show a
    stranger — an unauthenticated endpoint must not let someone probe which
    tokens exist by distinguishing 'unknown' from 'revoked'.
    """
    now = now or datetime.utcnow()
    if not token or len(token) < 20:
        return None, None, "This link is not valid."
    row = (db.query(AppointmentConfirmationToken)
           .filter(AppointmentConfirmationToken.token == token).first())
    if row is None:
        return None, None, "This link is not valid."
    if row.revoked_at is not None:
        return None, None, "This link is no longer active."
    if row.expires_at is not None and row.expires_at < now:
        return None, None, "This link has expired."
    appt = (db.query(SalesAppointment)
            .filter(SalesAppointment.id == row.appointment_id).first())
    if appt is None:
        return None, None, "This link is no longer active."
    return row, appt, None


def redeem_token(db: Session, row: AppointmentConfirmationToken,
                 appt: SalesAppointment, action: str,
                 ip: Optional[str] = None,
                 now: Optional[datetime] = None) -> dict:
    """Record the prospect's answer. Idempotent for the SAME action.

    The token is NOT burned on use. A prospect who confirms and then needs to
    decline must be able to, and someone who clicks their own link twice should
    not be told it is broken. What is recorded is the latest answer, plus the
    first redemption time, which is the one that matters for 'did they respond
    before the meeting'.
    """
    now = now or datetime.utcnow()
    action = (action or "").strip().lower()
    if action not in ("confirm", "decline"):
        return {"ok": False, "error": "Unknown action."}

    row.use_count = (row.use_count or 0) + 1
    row.last_used_at = now
    if row.first_redeemed_at is None:
        row.first_redeemed_at = now
    row.responded_action = action
    row.responded_at = now
    # Truncated hard: this is attacker-controlled via proxy headers.
    row.responded_ip = (ip or "")[:64] or None

    if action == "confirm":
        appt.confirmation_status = CONF_CONFIRMED
        appt.confirmed_at = now
        # Deliberately NOT `confirmed_by`: that column is a users.id foreign
        # key and the prospect is not a user. Writing anything there would be a
        # lie about who clicked.
        appt.confirmation_source = CONF_SRC_PROSPECT_LINK
    else:
        appt.confirmation_status = CONF_DECLINED
        appt.confirmation_source = CONF_SRC_PROSPECT_LINK
        appt.confirmed_at = None

    try:
        db.add(AppointmentSyncLog(
            appointment_id=appt.id, user_id=None, provider="prospect",
            action="confirm", status=action, ok=True, attempt=1, occurred_at=now))
    except Exception:
        log.exception("could not log prospect response for appointment %s", appt.id)
    return {"ok": True, "action": action}


# ── the invitation ──────────────────────────────────────────────────────────

def _local_when(appt: SalesAppointment) -> str:
    """The meeting time in the PROSPECT's timezone when we know it, otherwise
    the meeting's own. Showing a prospect in New York a Chicago time without
    saying so is how people arrive an hour late."""
    from app.services import availability as av
    tz = appt.prospect_timezone or appt.timezone or "UTC"
    try:
        local = av.utc_to_local(appt.starts_at, tz)
        end = av.utc_to_local(appt.ends_at, tz)
    except Exception:
        return appt.starts_at.strftime("%Y-%m-%d %H:%M") + " UTC"
    day = local.strftime("%A, %B ") + str(local.day) + local.strftime(", %Y")
    t1 = str(int(local.strftime("%I"))) + local.strftime(":%M %p")
    t2 = str(int(end.strftime("%I"))) + end.strftime(":%M %p")
    return "%s · %s – %s (%s)" % (day, t1, t2, tz.split("/")[-1].replace("_", " "))


def _prospect_body(appt: SalesAppointment, ident: dict, url: str,
                   kind: str) -> str:
    """The prospect-facing email. Built here and NOWHERE else.

    There is no code path from `appt.notes` into this function. That is the
    guarantee — not a rule someone has to remember when editing a shared
    template.
    """
    name = escape(appt.prospect_name or "there")
    brand = escape(ident.get("name") or "AdvisorFlow")
    accent = ident.get("accent") or "#1d4ed8"
    when = escape(_local_when(appt))
    title = escape(appt.title or "Meeting")

    if kind == "cancel":
        lead = "Your meeting with %s has been cancelled. Nothing further is needed." % brand
    elif kind == "reschedule":
        lead = "Your meeting with %s has been moved. The new time is below." % brand
    else:
        lead = "You're confirmed for a meeting with %s. Details are below." % brand

    where = ""
    if appt.meeting_url and kind != "cancel":
        where = ('<p style="margin:4px 0"><strong>Join:</strong> '
                 '<a href="%s" style="color:%s">%s</a></p>'
                 % (escape(appt.meeting_url), accent, escape(appt.meeting_url)))
    elif appt.location:
        where = ('<p style="margin:4px 0"><strong>Where:</strong> %s</p>'
                 % escape(appt.location))

    buttons = ""
    if kind != "cancel":
        buttons = (
            '<div style="margin:26px 0">'
            '<a href="%s" style="background:%s;color:#ffffff;text-decoration:none;'
            'padding:12px 22px;border-radius:6px;font-weight:600;display:inline-block">'
            'Confirm or reschedule</a></div>'
            '<p style="font-size:13px;color:#6b7280">'
            'If the button does not work, open this link:<br>'
            '<a href="%s" style="color:%s">%s</a></p>'
            % (escape(url), accent, escape(url), accent, escape(url))
        )

    contact = []
    if ident.get("support_phone"):
        contact.append("Call %s" % escape(ident["support_phone"]))
    if ident.get("from_email"):
        contact.append("or reply to this email")
    footer = ""
    if contact:
        footer = ('<p style="font-size:13px;color:#6b7280;margin-top:8px">'
                  'Questions? %s.</p>' % " ".join(contact))

    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        'color:#111827;line-height:1.55;max-width:560px">'
        '<p>Hi %s,</p><p>%s</p>'
        '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:16px;margin:18px 0">'
        '<p style="margin:0 0 8px;font-size:17px;font-weight:700">%s</p>'
        '<p style="margin:4px 0"><strong>When:</strong> %s</p>%s</div>'
        '%s%s'
        '<p style="font-size:12px;color:#9ca3af;margin-top:26px">%s</p></div>'
    ) % (name, lead, title, when, where, buttons, footer,
         escape(ident.get("website") or brand))


def send_prospect_invitation(db: Session, appt: SalesAppointment,
                             kind: str = "invite",
                             now: Optional[datetime] = None,
                             commit: bool = True) -> dict:
    """Email the prospect their invitation, with a .ics attached.

    `kind` is 'invite', 'reschedule' or 'cancel'.

    NEVER RAISES. A prospect email that fails must not undo a booked meeting;
    it is recorded on the appointment and surfaced as something a human can
    retry. Returns a small report.
    """
    import base64
    now = now or datetime.utcnow()

    if not appt.prospect_email:
        # Not a failure. Plenty of meetings are booked from a phone call with
        # no email on file, and the internal team is already synced.
        return {"ok": False, "sent": False, "reason": "no_prospect_email"}

    ident = brand_identity(db, appt)
    tok = get_or_create_token(db, appt, now=now)
    # The BRAND's own host. This is the line that used to email a prospect an
    # `advisorflow-backend.onrender.com` link.
    url = confirm_url(tok.token, ident.get("app_base_url"))

    cancelling = kind == "cancel"
    uid = ics_uid(appt.id, appt.prospect_email)
    try:
        ics = build_ics(
            uid=uid,
            starts_at=appt.starts_at, ends_at=appt.ends_at,
            summary=appt.title or "Meeting",
            # The prospect-facing description. Not `appt.notes`.
            description=("Confirm or reschedule: %s" % url) if not cancelling else "",
            location=appt.location or (appt.meeting_url or ""),
            organizer_email=ident.get("from_email") or "",
            organizer_name=ident.get("name") or "",
            attendees=[(appt.prospect_email, appt.prospect_name or appt.prospect_email)],
            method=METHOD_CANCEL if cancelling else METHOD_REQUEST,
            # Rises with each reschedule, and once more for a cancellation, or
            # the recipient's client discards the update as a duplicate.
            sequence=(appt.rescheduled_count or 0) + (1 if cancelling else 0),
            url=url if not cancelling else "",
            dtstamp=now,
        )
        attachment = [{
            "filename": "invitation.ics",
            "content": base64.b64encode(ics.encode("utf-8")).decode("ascii"),
            "content_type": ("text/calendar; charset=UTF-8; method=%s"
                             % ("CANCEL" if cancelling else "REQUEST")),
        }]
    except Exception as e:
        log.exception("could not build prospect .ics for appointment %s", appt.id)
        attachment = None
        ics = None

    if cancelling:
        subject = "Cancelled: %s" % (appt.title or "your meeting")
    elif kind == "reschedule":
        subject = "Updated time: %s" % (appt.title or "your meeting")
    else:
        subject = "Your meeting with %s" % (ident.get("name") or "us")

    sending_org = _SendingOrg(ident.get("from_email"))
    try:
        from app.services.email_service import send_email_via_provider
        result = send_email_via_provider(
            to_email=appt.prospect_email,
            subject=subject,
            body_html=_prospect_body(appt, ident, url, kind),
            attachments=attachment,
            org=sending_org,
        )
    except Exception as e:
        log.exception("prospect invitation send blew up for appointment %s", appt.id)
        result = {"success": False, "error": str(e)[:400]}

    ok = bool(result.get("success"))
    if ok:
        appt.prospect_invite_sent_at = now
        appt.prospect_invite_error = None
        if not cancelling and appt.confirmation_status == CONF_PENDING:
            # 'sent' is a real state distinct from 'pending': the prospect has
            # been asked and has not answered, which reads very differently on
            # a pipeline board from nobody having asked them at all.
            appt.confirmation_status = CONF_SENT
            appt.confirmation_sent_at = now
    else:
        # Kept so the UI can show WHY and offer a retry, rather than silently
        # leaving a meeting the prospect was never told about.
        appt.prospect_invite_error = (result.get("error") or "Send failed")[:2000]

    try:
        db.add(AppointmentSyncLog(
            appointment_id=appt.id, user_id=None, provider="email",
            action="invite" if not cancelling else "cancel",
            status="sent" if ok else "failed", ok=ok,
            error_message=(None if ok else (result.get("error") or "")[:2000]),
            attempt=1, occurred_at=now))
    except Exception:
        log.exception("could not log prospect invitation for appointment %s", appt.id)

    if commit:
        try:
            db.commit()
        except Exception:
            log.exception("could not commit invitation state for appointment %s", appt.id)
            db.rollback()

    return {"ok": ok, "sent": ok, "reason": None if ok else "send_failed",
            "error": None if ok else result.get("error"),
            "to": appt.prospect_email, "had_attachment": bool(attachment)}
