"""WHICH NUMBER A CALL USES, AND WHOSE NUMBER WAS CALLED.

Replaces the hard-coded sender literal the voice router used to dial from. Two
questions, both answered from data and never from a constant:

  resolve_voice_number(db, org, workspace_id=None, purpose=...)
      The number an outbound call (or an inbound route) for this organization
      uses. Most specific assignment wins:

          WORKSPACE  phone_numbers row for this org + this workspace
          ORG        phone_numbers row for this org
          ORG*       organizations.org_twilio_phone_number (legacy, implicit)
          BRAND      phone_numbers row for the org's platform (brand pool)
          PLATFORM   phone_numbers row with no org and no platform
          USER*      users.twilio_phone_number of the acting user (legacy)

      A phone_numbers row always governs its own number: a row that is inactive
      or lacks the capability is NOT bypassed by finding the same number in a
      legacy column. When nothing is assigned the answer is a refusal with a
      reason - never a literal fallback.

  resolve_owner_by_called_number(db, to)
      Inbound: the organization that owns the number Twilio says was called.
      A brand/platform pool number has no tenant and resolves to None - an
      inbound call to it is answered generically and looks nothing up. A number
      claimed by more than one organization in the legacy columns is ambiguous
      and also resolves to None (fail closed, logged).

No provider API is called here. Nothing is purchased or provisioned.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

PURPOSE_OUTBOUND = "voice_outbound"
PURPOSE_INBOUND = "voice_inbound"
PURPOSE_VOICEMAIL = "voicemail"
PURPOSE_SMS = "sms"

_CAP = {
    PURPOSE_OUTBOUND: "cap_voice_outbound",
    PURPOSE_INBOUND: "cap_voice_inbound",
    PURPOSE_VOICEMAIL: "cap_voicemail",
    PURPOSE_SMS: "cap_sms",
}

NO_NUMBER_REASON = ("No active voice-capable phone number is assigned to this organization "
                    "(or to its brand or the platform). A platform administrator assigns one "
                    "in Organization Control Center > Operations > Phone numbers.")


def normalize_e164(raw) -> Optional[str]:
    """+1XXXXXXXXXX for a usable US number, else None. The SAME rule the SMS and
    Wholesale inbound paths use (wholesale_sms.normalize_e164)."""
    from app.services import wholesale_sms
    return wholesale_sms.normalize_e164(raw)


def phone_forms(raw) -> List[str]:
    """Every spelling of one number the platform stores: +1XXXXXXXXXX,
    1XXXXXXXXXX and XXXXXXXXXX. Empty when the number is unusable."""
    e = normalize_e164(raw)
    if not e:
        return []
    d = e[2:]
    return [e, "1" + d, d]


@dataclass
class ResolvedNumber:
    ok: bool
    e164: Optional[str] = None
    level: Optional[str] = None          # workspace | organization | brand | platform | user
    source: Optional[str] = None         # "phone_numbers" | "org_column" | "user_column"
    number_id: Optional[str] = None      # phone_numbers.id when source == phone_numbers
    organization_id: Optional[str] = None
    reason: Optional[str] = None
    record: object = field(default=None, repr=False)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "e164": self.e164, "level": self.level, "source": self.source,
                "number_id": self.number_id, "reason": self.reason}


def _load_org(db, org):
    from app.models.models import Organization
    if org is None:
        return None
    if isinstance(org, str):
        return db.query(Organization).filter(Organization.id == org).first()
    return org


def _record_for(db, e164: Optional[str]):
    if not e164:
        return None
    from app.models.telephony_models import PhoneNumber
    return db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first()


def _usable(rec, cap: str) -> bool:
    return bool(rec is not None and rec.is_active and getattr(rec, cap, False))


def _from_record(rec, level: str, org_id: Optional[str]) -> ResolvedNumber:
    return ResolvedNumber(ok=True, e164=rec.e164, level=level, source="phone_numbers",
                          number_id=rec.id, organization_id=org_id, record=rec)


def resolve_voice_number(db, org, workspace_id: Optional[str] = None,
                         purpose: str = PURPOSE_OUTBOUND, user=None) -> ResolvedNumber:
    """The number to use for `purpose` in this organization. See module doc."""
    from app.models.telephony_models import PhoneNumber
    cap = _CAP.get(purpose, "cap_voice_outbound")
    org = _load_org(db, org)
    if org is None:
        return ResolvedNumber(ok=False, reason="Organization not found.")

    def _first(q):
        return q.filter(PhoneNumber.is_active.is_(True), getattr(PhoneNumber, cap).is_(True)) \
                .order_by(PhoneNumber.created_at.asc(), PhoneNumber.id.asc()).first()

    base = db.query(PhoneNumber)
    if workspace_id:
        rec = _first(base.filter(PhoneNumber.organization_id == org.id,
                                 PhoneNumber.workspace_id == workspace_id))
        if rec is not None:
            return _from_record(rec, "workspace", org.id)

    rec = _first(base.filter(PhoneNumber.organization_id == org.id,
                             PhoneNumber.workspace_id.is_(None)))
    if rec is not None:
        return _from_record(rec, "organization", org.id)

    # Legacy org column: an IMPLICIT organization-level number, voice capable
    # because this is the number the organization has always been called back
    # on. A phone_numbers row for the same number governs it instead.
    legacy = normalize_e164(getattr(org, "org_twilio_phone_number", None))
    if legacy and purpose != PURPOSE_SMS:
        governing = _record_for(db, legacy)
        if governing is None:
            return ResolvedNumber(ok=True, e164=legacy, level="organization",
                                  source="org_column", organization_id=org.id)

    if getattr(org, "platform_id", None):
        rec = _first(base.filter(PhoneNumber.organization_id.is_(None),
                                 PhoneNumber.platform_id == org.platform_id))
        if rec is not None:
            return _from_record(rec, "brand", org.id)

    rec = _first(base.filter(PhoneNumber.organization_id.is_(None),
                             PhoneNumber.platform_id.is_(None)))
    if rec is not None:
        return _from_record(rec, "platform", org.id)

    if user is not None and getattr(user, "organization_id", None) == org.id:
        u_num = normalize_e164(getattr(user, "twilio_phone_number", None))
        if u_num and _record_for(db, u_num) is None:
            return ResolvedNumber(ok=True, e164=u_num, level="user", source="user_column",
                                  organization_id=org.id)

    return ResolvedNumber(ok=False, organization_id=org.id, reason=NO_NUMBER_REASON)


@dataclass
class CalledNumberOwner:
    organization_id: str
    e164: str
    number_id: Optional[str] = None
    source: str = "phone_numbers"
    record: object = field(default=None, repr=False)
    user_id: Optional[str] = None


def resolve_owner_by_called_number(db, to_raw) -> Optional[CalledNumberOwner]:
    """The organization that owns the called number, or None. READ ONLY."""
    from app.models.models import Organization, User
    e164 = normalize_e164(to_raw)
    if not e164:
        return None
    rec = _record_for(db, e164)
    if rec is not None:
        if not rec.is_active or not rec.organization_id:
            # Inactive, or a brand/platform pool number: no tenant to route to.
            return None
        return CalledNumberOwner(organization_id=rec.organization_id, e164=e164,
                                 number_id=rec.id, source="phone_numbers", record=rec)
    forms = phone_forms(e164)
    orgs = {o.id for o in db.query(Organization.id)
            .filter(Organization.org_twilio_phone_number.in_(forms)).all()}
    users = db.query(User.id, User.organization_id).filter(
        User.twilio_phone_number.in_(forms)).all()
    user_orgs = {u.organization_id for u in users if u.organization_id}
    owners = orgs | user_orgs
    if len(owners) != 1:
        if len(owners) > 1:
            log.warning("number_resolution: %s is claimed by %d organizations - "
                        "refusing to route", e164, len(owners))
        return None
    org_id = owners.pop()
    if org_id in orgs:
        return CalledNumberOwner(organization_id=org_id, e164=e164, source="org_column")
    uid = next((u.id for u in users if u.organization_id == org_id), None)
    return CalledNumberOwner(organization_id=org_id, e164=e164, source="user_column",
                             user_id=uid)


# ── credentials for the account that owns a number ──────────────────────────

def twilio_credentials(db, org, resolved: Optional[ResolvedNumber] = None,
                       user=None) -> Optional[Tuple[str, str]]:
    """(account_sid, auth_token) able to place a call from `resolved`, or None.

    The account that owns the number places the call and signs its callbacks.
    Organization-owned numbers use the organization's stored account; a legacy
    user number uses that user's account when they hold one; brand/platform
    pool numbers use the platform account from the environment. Never logged.
    """
    from app.utils.crypto import decrypt_value
    org = _load_org(db, org)

    def _dec(v):
        try:
            return decrypt_value(v) if v else None
        except Exception:                                    # noqa: BLE001
            return None

    if resolved is not None and resolved.level == "user" and user is not None:
        if user.twilio_account_sid and user.twilio_auth_token_encrypted:
            tok = _dec(user.twilio_auth_token_encrypted)
            if tok:
                return user.twilio_account_sid, tok
    if resolved is not None and resolved.level in ("brand", "platform"):
        sid = (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
        tok = (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip()
        return (sid, tok) if sid and tok else None
    if org is not None and org.org_twilio_account_sid and org.org_twilio_auth_token_encrypted:
        tok = _dec(org.org_twilio_auth_token_encrypted)
        if tok:
            return org.org_twilio_account_sid, tok
    if user is not None and user.twilio_account_sid and user.twilio_auth_token_encrypted:
        tok = _dec(user.twilio_auth_token_encrypted)
        if tok:
            return user.twilio_account_sid, tok
    return None


def parse_route(raw) -> dict:
    """A phone number's inbound route as a dict with defaults filled in."""
    import json
    route = {}
    if isinstance(raw, dict):
        route = dict(raw)
    elif raw:
        try:
            route = json.loads(raw) or {}
        except (TypeError, ValueError):
            route = {}
    mode = route.get("mode") or "ring_then_voicemail"
    if mode not in ("ring_then_voicemail", "voicemail_only", "ai_agent"):
        mode = "ring_then_voicemail"
    try:
        timeout = int(route.get("timeout_seconds") or 20)
    except (TypeError, ValueError):
        timeout = 20
    ids = route.get("ring_user_ids") or []
    if not isinstance(ids, list):
        ids = []
    return {
        "mode": mode,
        "ring_user_ids": [str(i) for i in ids if i][:10],
        "timeout_seconds": max(5, min(timeout, 60)),
        "voicemail": route.get("voicemail", True) is not False,
        "greeting_text": (route.get("greeting_text") or "").strip()[:500] or None,
        "greeting_recording_url": (route.get("greeting_recording_url") or "").strip() or None,
    }


_E164_RE = re.compile(r"^\+1\d{10}$")


def is_e164(value) -> bool:
    return bool(value and _E164_RE.match(str(value)))


# ── PUBLIC CONTACT NUMBER vs OUTREACH NUMBER (2026-10-01) ────────────────────
#
# Two different numbers that were easy to conflate:
#
#   PUBLIC CONTACT   the business's own published phone - what a customer is
#                    told to call, printed on booking pages and said by an AI
#                    agent ("call us back at ..."). organizations.org_phone.
#                    Display only; nothing is ever SENT or DIALLED from it.
#
#   OUTREACH         the provider number a call or text goes OUT on. Voice:
#                    resolve_voice_number above. SMS: the advisor/org sender
#                    ladder in sms_service.describe_sms_sender (unchanged, the
#                    same function the send path uses).
#
# Neither falls back to the other, and neither falls back to a literal: a
# missing number is reported as "Not configured".

NOT_CONFIGURED = "Not configured"


def resolve_public_contact_number(db, org) -> dict:
    """The organization's published contact number. Never an outreach number."""
    org = _load_org(db, org)
    if org is None:
        return {"ok": False, "e164": None, "display": NOT_CONFIGURED, "source": None,
                "reason": "Organization not found."}
    raw = getattr(org, "org_phone", None)
    e164 = normalize_e164(raw)
    if not e164:
        return {"ok": False, "e164": None, "display": NOT_CONFIGURED, "source": None,
                "reason": ("No public contact phone is set for this organization "
                           "(Org Settings > business phone).")}
    return {"ok": True, "e164": e164, "display": e164, "source": "organization.org_phone",
            "reason": None}


def communication_identity(db, org, user=None, workspace_id: Optional[str] = None) -> dict:
    """Everything a screen needs to say WHO a call or text comes from, per
    tenant, with no secret and no cross-tenant fallback. Read only."""
    org = _load_org(db, org)
    # Voice: resolved exactly as the human dialer resolves it (no legacy USER
    # number), so the screen can never show a caller ID the call won't use.
    voice = resolve_voice_number(db, org, workspace_id=workspace_id,
                                 purpose=PURPOSE_OUTBOUND) if org is not None \
        else ResolvedNumber(ok=False, reason="Organization not found.")
    creds = twilio_credentials(db, org, voice) if voice.ok else None
    sms = {"ready": False, "from_number": None, "reason": NOT_CONFIGURED}
    if user is not None and org is not None and getattr(user, "organization_id", None) == org.id:
        try:
            from app.services.sms_service import describe_sms_sender
            d = describe_sms_sender(user, db) or {}
            sms = {"ready": bool(d.get("ready")), "from_number": d.get("from_number"),
                   "source": d.get("source"), "reason": d.get("reason")}
        except Exception as exc:                              # noqa: BLE001
            log.warning("communication_identity: sms sender lookup failed: %s", exc)
    return {
        "organization_id": getattr(org, "id", None),
        "public_contact": resolve_public_contact_number(db, org),
        "voice_outbound": {
            "configured": voice.ok,
            "e164": voice.e164 if voice.ok else None,
            "display": voice.e164 if voice.ok else NOT_CONFIGURED,
            "level": voice.level if voice.ok else None,
            "reason": None if voice.ok else voice.reason,
            "provider_ready": bool(creds),
        },
        "sms_outbound": sms,
    }
