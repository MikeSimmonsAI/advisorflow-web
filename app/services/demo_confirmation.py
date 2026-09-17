"""
THE BRANDED DEMO CONFIRMATION EMAIL.

A SIBLING OF appointment_invites, NOT A REFACTOR OF IT. That module's
`_prospect_body` carries a stated guarantee - "there is no code path from
`appt.notes` into this function" - and that guarantee is structural, not a
rule somebody remembers. Folding both emails into one shared template would
turn it back into a rule. So the invitation path is untouched and this reuses
its parts: `brand_identity`, `_local_when`, `_SendingOrg`, the confirm-token
URL and the ics builder.

THE VISUAL RULE, WHICH IS THE WHOLE POINT

    GRAPHIC   branding, industry, product preview. Decoration.
    HTML      the meeting details and the real, clickable CTA.

Nothing that a prospect must be able to CLICK may live inside the image. Not
"Join on Zoom", not "Join your live demo", not the meeting URL, not a button
that looks like a button. An image is not a link, images are blocked by
default in most corporate mail clients, and a painted button that does nothing
is worse than no button - the prospect clicks it, nothing happens, and they
assume the meeting is not real.

`build()` enforces that in code rather than in a comment: it renders the CTA
itself from `appt.meeting_url` and refuses to accept any caller-supplied
graphic whose alt text or URL contains a join phrase.

AND IF THERE IS NO MEETING URL, THERE IS NO CTA. appointment_meetings states
the house rule already - "a provider failure produces an appointment with no
video link and a visible reason. It never produces a fake link" - so a
confirmation for an appointment with no URL shows the location, or says the
link is to follow, and renders no button at all. A dead CTA is the one
outcome this module exists to prevent.

NOTHING HERE SENDS BY ITSELF. `build()` is pure and reaches no provider;
`send()` goes through the same gate every other restored path uses and is
disabled by default.
"""

from datetime import datetime
from html import escape
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

# ── INDUSTRY PROFILES ───────────────────────────────────────────────────────
#
# What the graphic says and what the opening line calls the product, per
# industry. GENERIC is not a fallback bolted on afterwards - it is the default
# every unknown industry resolves to, because a prospect shown the wrong
# industry's language notices immediately and it reads as a mail-merge.
#
# Keyed on the values app/services/industry_templates.py already normalises to,
# so there is one industry vocabulary in the platform rather than two.

GENERIC = "generic"

INDUSTRY_PROFILES: Dict[str, Dict[str, str]] = {
    GENERIC: {
        "label": "your team",
        "headline": "See it working with your own pipeline",
        "points": ("Your leads, your follow-up, your calendar",
                   "Live data rather than a slide deck",
                   "Questions answered as we go"),
        "accent_hint": "#1d4ed8",
    },
    "funeral": {
        "label": "your funeral home",
        "headline": "See it working with your own families",
        "points": ("Pre-need and at-need tracked in one place",
                   "Follow-up that never drops a family",
                   "Appointments booked straight into your calendar"),
        "accent_hint": "#4c1d95",
    },
    "insurance": {
        "label": "your agency",
        "headline": "See it working with your own book",
        "points": ("Every quote followed up on schedule",
                   "Renewals that surface before they lapse",
                   "Appointments booked without the phone tag"),
        "accent_hint": "#065f46",
    },
    "real_estate": {
        "label": "your brokerage",
        "headline": "See it working with your own listings",
        "points": ("Buyer and seller leads worked the same day",
                   "Showings booked without the back and forth",
                   "Nothing lost between portals"),
        "accent_hint": "#9a3412",
    },
    "home_services": {
        "label": "your team",
        "headline": "See it working with your own jobs",
        "points": ("Every enquiry answered within minutes",
                   "Estimates followed up automatically",
                   "Crews scheduled without the phone tag"),
        "accent_hint": "#1e3a8a",
    },
}

# Phrases that must never appear in a graphic. Checked against the image URL
# and its alt text, because both are things a caller can get wrong.
_FORBIDDEN_IN_GRAPHIC = (
    "join on zoom", "join your live demo", "join the demo", "join now",
    "click here", "http://", "https://", "zoom.us", "meet.google", "teams.microsoft",
)


def profile_for(industry: Optional[str]) -> Dict[str, str]:
    """The industry profile, or GENERIC. Never a guess and never empty."""
    try:
        from app.services import industry_templates
        key = industry_templates.normalize(industry)
    except Exception:                                            # noqa: BLE001
        key = (industry or GENERIC)
    key = (key or GENERIC).strip().lower()
    return INDUSTRY_PROFILES.get(key, INDUSTRY_PROFILES[GENERIC])


class GraphicRefused(ValueError):
    """The supplied graphic carries something that must be clickable."""


def _check_graphic(graphic_url: Optional[str], alt: Optional[str]) -> None:
    haystack = " ".join(x for x in (graphic_url, alt) if x).lower()
    if not haystack:
        return
    for phrase in _FORBIDDEN_IN_GRAPHIC:
        # The image URL is naturally a URL, so only the ALT text is checked for
        # link-shaped content; the phrase check applies to both.
        if phrase in ("http://", "https://"):
            if alt and phrase in alt.lower():
                raise GraphicRefused(
                    "The graphic's alt text contains a URL. A link a prospect "
                    "must click cannot live in an image - put it in the HTML "
                    "CTA, which is rendered from the appointment's meeting_url.")
            continue
        if phrase in haystack:
            raise GraphicRefused(
                "The graphic references %r. Nothing clickable may live inside "
                "the image: it is decoration, and the join link is rendered as "
                "real HTML from the appointment's meeting_url." % phrase)


def build(appt, ident: Dict[str, Any], *, confirm_url: Optional[str] = None,
          industry: Optional[str] = None, salesperson=None,
          graphic_url: Optional[str] = None,
          graphic_alt: Optional[str] = None) -> Dict[str, str]:
    """Render the confirmation. PURE - no database, no provider, no side effect.

    Returns {"subject", "html", "has_cta", "industry", "graphic_url"}.
    """
    _check_graphic(graphic_url, graphic_alt)

    from app.services.appointment_invites import _local_when

    prof = profile_for(industry)
    brand = escape(ident.get("name") or "AdvisorFlow")
    accent = ident.get("accent") or ident.get("invite_accent_color") or prof["accent_hint"]
    name = escape(getattr(appt, "prospect_name", None) or "there")
    company = escape(getattr(appt, "prospect_company", None) or "")
    when = escape(_local_when(appt))
    title = escape(getattr(appt, "title", None) or "Your demo")
    logo = ident.get("logo_url")

    rep_line = ""
    rep_name = (getattr(salesperson, "full_name", None)
                or getattr(salesperson, "email", None))
    if rep_name:
        rep_line = ('<p style="margin:4px 0"><strong>With:</strong> %s</p>'
                    % escape(rep_name))

    # ── THE GRAPHIC. Decoration only, and never a link. ─────────────────────
    graphic = ""
    if graphic_url:
        graphic = (
            '<img src="%s" alt="%s" width="560" '
            'style="display:block;width:100%%;max-width:560px;height:auto;'
            'border-radius:8px;border:0" />'
            % (escape(graphic_url), escape(graphic_alt or "%s preview" % brand)))
    elif logo:
        graphic = ('<img src="%s" alt="%s" height="34" '
                   'style="max-height:34px;border:0" />' % (escape(logo), brand))

    points = "".join(
        '<li style="margin:4px 0">%s</li>' % escape(p) for p in prof["points"])

    # ── WHERE, AND THE CTA. Real HTML, from the real meeting URL. ──────────
    meeting_url = getattr(appt, "meeting_url", None)
    location = getattr(appt, "location", None)
    has_cta = bool(meeting_url)

    if meeting_url:
        where = ('<p style="margin:4px 0"><strong>Join:</strong> '
                 '<a href="%s" style="color:%s">%s</a></p>'
                 % (escape(meeting_url), accent, escape(meeting_url)))
        cta = (
            '<div style="margin:26px 0">'
            '<a href="%s" style="background:%s;color:#ffffff;text-decoration:none;'
            'padding:13px 24px;border-radius:6px;font-weight:600;display:inline-block">'
            'Join your demo</a></div>'
            '<p style="font-size:13px;color:#6b7280">'
            'If the button does not work, open this link:<br>'
            '<a href="%s" style="color:%s">%s</a></p>'
            % (escape(meeting_url), accent,
               escape(meeting_url), accent, escape(meeting_url)))
    elif location:
        where = ('<p style="margin:4px 0"><strong>Where:</strong> %s</p>'
                 % escape(location))
        cta = ""
    else:
        # NO URL, NO BUTTON. Saying the link is to follow is honest; a button
        # that goes nowhere makes the prospect think the meeting is not real.
        where = ('<p style="margin:4px 0;color:#6b7280">The joining link will '
                 'follow before the meeting.</p>')
        cta = ""

    confirm = ""
    if confirm_url:
        confirm = ('<p style="font-size:13px;color:#6b7280;margin-top:18px">'
                   'Need to change the time? '
                   '<a href="%s" style="color:%s">Reschedule here</a>.</p>'
                   % (escape(confirm_url), accent))

    subject = "Your %s demo is confirmed%s" % (
        ident.get("name") or "AdvisorFlow",
        " — %s" % (getattr(appt, "prospect_company", None) or "") if company else "")

    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        'color:#111827;line-height:1.55;max-width:560px">'
        '%s'
        '<p style="margin-top:18px">Hi %s,</p>'
        '<p>You are confirmed for a demo with %s. %s</p>'
        '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:16px;margin:18px 0">'
        '<p style="margin:0 0 8px;font-size:17px;font-weight:700">%s</p>'
        '<p style="margin:4px 0"><strong>When:</strong> %s</p>'
        '%s%s</div>'
        '<p style="font-weight:600;margin-bottom:6px">%s</p>'
        '<ul style="margin:0 0 4px 18px;padding:0;color:#374151">%s</ul>'
        '%s%s'
        '<div style="border-top:3px solid %s;margin-top:26px;padding-top:10px">'
        '<p style="font-size:12px;color:#9ca3af">%s</p></div></div>'
    ) % (graphic, name, brand,
         ("We will use %s's own data shape so you can see it working for %s."
          % (brand, company or prof["label"])),
         title, when, rep_line, where,
         escape(prof["headline"]), points, cta, confirm,
         accent, escape(ident.get("website") or brand))

    return {"subject": subject, "html": html, "has_cta": has_cta,
            "industry": profile_key(industry), "graphic_url": graphic_url}


def profile_key(industry: Optional[str]) -> str:
    try:
        from app.services import industry_templates
        key = industry_templates.normalize(industry)
    except Exception:                                            # noqa: BLE001
        key = industry or GENERIC
    key = (key or GENERIC).strip().lower()
    return key if key in INDUSTRY_PROFILES else GENERIC


def preview(db: Session, appt, *, graphic_url=None, graphic_alt=None) -> Dict[str, Any]:
    """Render it without sending. Reaches no provider, by construction: there
    is no import of one in this function's call graph."""
    from app.services.appointment_invites import brand_identity
    ident = brand_identity(db, appt)
    industry, rep = _context(db, appt)
    out = build(appt, ident, industry=industry, salesperson=rep,
                graphic_url=graphic_url, graphic_alt=graphic_alt)
    out["to"] = getattr(appt, "prospect_email", None)
    out["meeting_url"] = getattr(appt, "meeting_url", None)
    out["would_send"] = bool(out["to"])
    return out


def _context(db: Session, appt):
    """The industry and the salesperson, from the deal behind the appointment."""
    industry = None
    rep = None
    try:
        from app.models.sales_models import Opportunity
        from app.models.scheduling_models import AppointmentParticipant
        from app.models.models import User
        opp = None
        if getattr(appt, "opportunity_id", None):
            opp = db.query(Opportunity).filter(
                Opportunity.id == appt.opportunity_id).first()
        industry = getattr(opp, "industry", None)
        part = (db.query(AppointmentParticipant)
                .filter(AppointmentParticipant.appointment_id == appt.id)
                .order_by(AppointmentParticipant.is_required.desc())
                .first())
        if part is not None and getattr(part, "user_id", None):
            rep = db.query(User).filter(User.id == part.user_id).first()
        if rep is None and opp is not None and getattr(opp, "owner_user_id", None):
            rep = db.query(User).filter(User.id == opp.owner_user_id).first()
    except Exception:                                            # noqa: BLE001
        pass
    return industry, rep


def send(db: Session, appt, *, actor=None, graphic_url=None, graphic_alt=None):
    """Send the confirmation, if this deployment allows it.

    Goes through the same gate as every other restored path, which is disabled
    by default, so this raises rather than sends in the shipped build. When it
    is enabled, delivery and failure are both recorded on the appointment and
    in AppointmentSyncLog, exactly as send_prospect_invitation does - a resend
    is this same call again and the counter distinguishes them.
    """
    from app.services import outbound_email_gate
    from app.services.appointment_invites import brand_identity, _SendingOrg

    to = getattr(appt, "prospect_email", None)
    if not to:
        raise ValueError("This appointment has no prospect email address.")

    ident = brand_identity(db, appt)
    industry, rep = _context(db, appt)
    rendered = build(appt, ident, industry=industry, salesperson=rep,
                     graphic_url=graphic_url, graphic_alt=graphic_alt)

    # The staff gate, deliberately: this is a brand-sales email about a demo,
    # not outreach to a customer's lead, so there is no Lead to run the
    # compliance preflight against and no customer entitlement that governs it.
    outbound_email_gate.gate_staff_email(to, purpose="demo confirmation")

    from app.services.email_service import send_email_via_provider
    identity = _SendingOrg(ident.get("from_email"),
                           from_name=ident.get("name"))
    result = send_email_via_provider(
        to_email=to, subject=rendered["subject"], body_html=rendered["html"],
        org=identity, message_type="demo_confirmation",
        template_id="sales.demo_confirmation")

    _record(db, appt, rendered, result, actor=actor)
    if not result.get("success"):
        raise RuntimeError(result.get("error") or "The mail provider refused it.")
    return rendered


def _record(db: Session, appt, rendered, result, actor=None) -> None:
    """Status on the appointment, and one sync-log row. Best effort."""
    try:
        from app.models.calendar_models import AppointmentSyncLog
        ok = bool(result.get("success"))
        count = (getattr(appt, "demo_confirmation_count", 0) or 0) + 1
        if hasattr(appt, "demo_confirmation_count"):
            appt.demo_confirmation_count = count
        if hasattr(appt, "demo_confirmation_sent_at") and ok:
            appt.demo_confirmation_sent_at = datetime.utcnow()
        if hasattr(appt, "demo_confirmation_error"):
            appt.demo_confirmation_error = None if ok else str(result.get("error"))[:300]
        db.add(AppointmentSyncLog(
            appointment_id=appt.id, provider="email",
            action="demo_confirmation", status="ok" if ok else "failed",
            ok=ok, error_message=None if ok else str(result.get("error"))[:300],
            attempt=count))
        db.flush()
    except Exception:                                            # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "demo confirmation sent but not recorded for %s",
            getattr(appt, "id", None), exc_info=True)
