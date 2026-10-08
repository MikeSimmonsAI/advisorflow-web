"""The SMS programs the universal opt-in page serves, and the SCI send gate.

ONE PAGE, SEPARATE CONSENTS
---------------------------
https://evosyspro.live/sms-optin/ serves three programs:

    /sms-optin/                    general    EvoSys Pro service messages
    /sms-optin/?program=wholesale  wholesale  EvoSys Wholesale seller messages
    /sms-optin/?program=sci        sci        funeral home / cemetery proof of concept

Each shows its OWN sender and its OWN wording, and each consent is filed as its
own row in `sms_consent_records` under its own program key, in the organization
that will actually send. Agreeing to one program is never agreeing to another.

WORDING IS REGISTERED HERE, VERSIONED, AND MIRRORED ON THE PAGE
---------------------------------------------------------------
`DISCLOSURES` holds the exact checkbox text per (program, version). The PHP page
(public-site/private/sms-programs.php) carries the same text, and a test fails
if the two ever differ by one character. A consent arriving with wording that
does not match its registered version is still filed - the evidence is what the
person was shown - but it is marked unregistered and does not count for the SCI
send gate.

A PROGRAM WHOSE COPY IS NOT FINAL IS NOT OPEN
--------------------------------------------
`copy_status = "pending"` means the compliance wording has not been signed off.
Such a program accepts no opt-ins (409), records nothing, and enables no send.
The SCI program's own wording is registered but stays pending until Mike's
final review.

THE SCI SEND GATE
-----------------
No SMS reaches an SCI contact unless ALL hold (every failing code is reported):

    SCI_SMS_DISABLED          SCI_SMS_SEND_ENABLED is not on
    SCI_COPY_NOT_FINAL        the SCI program's wording is still pending
    INVALID_NUMBER            not a usable US mobile number
    NO_SMS_CONSENT            no SCI consent of record for this number in this org
    OPTED_OUT                 the latest SCI consent was withdrawn (STOP etc.)
    CONSENT_WORDING_UNREGISTERED  the consent's wording is not a registered final version
    CAMPAIGN_NOT_APPROVED     the sending number is not on a VERIFIED campaign that lists "sci"

Suppression, DNC, holds and location review are already enforced by send_sms
before this gate runs. There is no override.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Program:
    key: str                  # the page's ?program= value
    consent_program: str      # sms_consent_records.program
    sender: str               # who the messages come from, as disclosed
    form_id: str
    org_env: Optional[str]    # env var holding the sending org id (None = platform intake org)
    copy_status: str          # "final" | "pending"
    current_version: Optional[str]


GENERAL = "general"
WHOLESALE = "wholesale"
SCI = "sci"

# Kept equal to wholesale_sms.PROGRAM so a consent taken here counts for the
# Wholesale send gate exactly like one taken on /sell.
WHOLESALE_CONSENT_PROGRAM = "wholesale_seller_sms"

PROGRAMS: Dict[str, Program] = {
    GENERAL: Program(
        key=GENERAL, consent_program="evosys_general_sms",
        sender="EvoSys Pro (EVO Integrated Solutions LLC)",
        form_id="evosys_sms_optin", org_env=None,
        copy_status="final", current_version="2026-09"),
    WHOLESALE: Program(
        key=WHOLESALE, consent_program=WHOLESALE_CONSENT_PROGRAM,
        sender="EVO Integrated Solutions LLC (operating as EvoSys Wholesale / EvoSysPro)",
        form_id="evosys_sms_optin_wholesale", org_env="SMS_PROGRAM_ORG_WHOLESALE",
        copy_status="final", current_version="evo-wholesale-sell-2026-09-25"),
    SCI: Program(
        key=SCI, consent_program="sci_poc_sms",
        sender=("EvoSys Pro (EVO Integrated Solutions LLC), on behalf of participating "
                "funeral home and cemetery locations"),
        form_id="evosys_sms_optin_sci", org_env="SMS_PROGRAM_ORG_SCI",
        # SCI-specific wording is registered below, PENDING FINAL REVIEW. While
        # pending, the program takes no opt-ins and enables no send. Going live
        # = copy_status="final" here AND 'status' => 'final' in the PHP mirror.
        copy_status="pending", current_version="sci-poc-2026-10-08"),
}

# Exact checkbox wording, per (program, version). Mirrored in
# public-site/private/sms-programs.php - tests/test_sms_consent_center.py
# compares them character for character.
DISCLOSURES: Dict[tuple, str] = {
    (GENERAL, "2026-09"): (
        "By checking this box, I agree to receive SMS/text messages from EvoSys Pro "
        "including appointment confirmations, reminders, and related service follow-ups. "
        "Message frequency varies. Message & data rates may apply. Reply STOP to "
        "unsubscribe, HELP for help. Consent is not a condition of any purchase or service."),
    (WHOLESALE, "evo-wholesale-sell-2026-09-25"): (
        "By checking this box, I agree to receive SMS text messages from EVO Integrated "
        "Solutions LLC (operating as EvoSys Wholesale / EvoSysPro) regarding my property "
        "inquiry, including follow-up questions, appointment scheduling, and transaction "
        "updates. Message frequency varies. Message and data rates may apply. Reply STOP to "
        "unsubscribe, HELP for help. Consent is not a condition of any service. View our "
        "Privacy Policy and Terms."),
    # PENDING FINAL REVIEW - never replace with generic wording.
    (SCI, "sci-poc-2026-10-08"): (
        "I agree to receive SMS messages from EvoSys Pro (operated by EVO Integrated "
        "Solutions LLC), including follow-ups on pre-planning information I requested, "
        "appointment confirmations, reminders, and related service updates from "
        "participating funeral home and cemetery locations. Message frequency varies. "
        "Msg & data rates may apply. Reply STOP to opt out or HELP for help. Consent is "
        "not a condition of any purchase. View our Terms and Privacy Policy."),
}

SCI_SEND_ENV = "SCI_SMS_SEND_ENABLED"
SCI_ORG_NAME = "Service Corporation International"

SCI_SMS_DISABLED = "SCI_SMS_DISABLED"
SCI_COPY_NOT_FINAL = "SCI_COPY_NOT_FINAL"
INVALID_NUMBER = "INVALID_NUMBER"
NO_SMS_CONSENT = "NO_SMS_CONSENT"
OPTED_OUT = "OPTED_OUT"
CONSENT_WORDING_UNREGISTERED = "CONSENT_WORDING_UNREGISTERED"
CAMPAIGN_NOT_APPROVED = "CAMPAIGN_NOT_APPROVED"
SCI_REASONS = (SCI_SMS_DISABLED, SCI_COPY_NOT_FINAL, INVALID_NUMBER, NO_SMS_CONSENT,
               OPTED_OUT, CONSENT_WORDING_UNREGISTERED, CAMPAIGN_NOT_APPROVED)


class ProgramUnavailable(Exception):
    """The program exists but cannot take opt-ins right now. `.code` says why."""

    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


def get(key: Optional[str]) -> Optional[Program]:
    return PROGRAMS.get((key or GENERAL).strip().lower())


def is_open(p: Program) -> bool:
    return p.copy_status == "final" and bool(p.current_version) and \
        (p.key, p.current_version) in DISCLOSURES


def is_final_version(p: Program, version: Optional[str]) -> bool:
    """Only the program's current version counts, and only once it is final."""
    return is_open(p) and version == p.current_version


def registered_text(p: Program, version: Optional[str]) -> Optional[str]:
    return DISCLOSURES.get((p.key, version or ""))


def wording_matches(p: Program, version: Optional[str], text: Optional[str]) -> bool:
    reg = registered_text(p, version)
    return reg is not None and (text or "").strip() == reg


def _truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def program_org(db, p: Program):
    """The organization that sends this program's messages. Raises ProgramUnavailable."""
    from app.models.models import Organization
    org_id = (os.environ.get(p.org_env or "") or "").strip()
    if not org_id:
        raise ProgramUnavailable("program_org_not_configured",
                                 "This SMS program is not available yet.")
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None or org.is_active is False:
        raise ProgramUnavailable("program_org_not_found", "This SMS program is not available.")
    if p.key == SCI and (org.name or "").strip() != SCI_ORG_NAME:
        # A misconfigured env must never file SCI consent into another customer.
        raise ProgramUnavailable("program_org_mismatch", "This SMS program is not available.")
    return org


def record(db, p: Program, org_id: str, *, phone_raw: str, disclosure_text: str,
           disclosure_version: Optional[str], form_version: Optional[str],
           source_url: Optional[str], ip: Optional[str], user_agent: Optional[str],
           lead=None):
    """File one consent of record for this program. Caller commits."""
    from app.services import wholesale_sms
    rec = wholesale_sms.record_consent(
        db, org_id, phone_raw=phone_raw, disclosure_text=disclosure_text,
        disclosure_version=disclosure_version, form_version=form_version,
        source_url=source_url, ip=ip, user_agent=user_agent, lead=None,
        program=p.consent_program, form_id=p.form_id)
    if lead is not None:
        # Linked, not mirrored: the general page's lead already carries its
        # consent columns from public capture, and they stay as written there.
        rec.lead_id = lead.id
    if p.key != WHOLESALE:
        # The campaign this consent was collected for, from the registry - never
        # another program's settings. NULL when no campaign lists the program.
        entry = campaign_for_program(p.key)
        rec.campaign_sid = (entry or {}).get("campaign_id")
        rec.messaging_service_sid = (entry or {}).get("messaging_service_sid")
        db.flush()
    return rec


def campaign_for_program(key: str) -> Optional[Dict[str, Any]]:
    """The registered campaign that lists this program (approved ones first)."""
    from app.services import sms_campaigns
    hits = [e for e in sms_campaigns.registry() if key in (e.get("programs") or [])]
    hits.sort(key=lambda e: not sms_campaigns.is_approved(e))
    return hits[0] if hits else None


# ── The SCI send gate ───────────────────────────────────────────────────────

def is_sci_org(db, org_id: Optional[str]) -> bool:
    if not org_id:
        return False
    if org_id == (os.environ.get(PROGRAMS[SCI].org_env) or "").strip():
        return True
    from app.models.models import Organization
    org = db.query(Organization).filter(Organization.id == org_id).first()
    return bool(org) and (org.name or "").strip() == SCI_ORG_NAME


def sci_check(db, org_id: str, phone: Optional[str], *,
              from_number: Optional[str] = None,
              messaging_service_sid: Optional[str] = None) -> Dict[str, Any]:
    """Deterministic: reads env, the database and the campaign registry."""
    from app.services import sms_campaigns, wholesale_sms
    p = PROGRAMS[SCI]
    reasons: List[str] = []
    if not _truthy(SCI_SEND_ENV):
        reasons.append(SCI_SMS_DISABLED)
    if not is_open(p):
        reasons.append(SCI_COPY_NOT_FINAL)
    e164 = wholesale_sms.normalize_e164(phone)
    consent = None
    if not e164:
        reasons.append(INVALID_NUMBER)
    else:
        consent = wholesale_sms.latest_consent(db, org_id, e164, p.consent_program)
        if consent is None:
            reasons.append(NO_SMS_CONSENT)
        else:
            if consent.status != "opted_in" or consent.opted_out_at is not None:
                reasons.append(OPTED_OUT)
            if not (is_final_version(p, consent.disclosure_version)
                    and wording_matches(p, consent.disclosure_version, consent.disclosure_text)):
                reasons.append(CONSENT_WORDING_UNREGISTERED)
    entry = sms_campaigns.for_sender(from_number, messaging_service_sid)
    if not sms_campaigns.is_approved(entry) or SCI not in (entry.get("programs") or []):
        reasons.append(CAMPAIGN_NOT_APPROVED)
    reasons = [r for r in SCI_REASONS if r in reasons]
    return {"eligible": not reasons, "reasons": reasons, "program": p.consent_program,
            "phone": e164, "consent_id": getattr(consent, "id", None),
            "campaign": (entry or {}).get("key")}


def sci_send_refusal(db, lead, *, from_number: Optional[str] = None,
                     messaging_service_sid: Optional[str] = None) -> Optional[List[str]]:
    """None for a non-SCI lead. For an SCI lead: None when permitted, else codes."""
    org_id = getattr(lead, "organization_id", None)
    if not is_sci_org(db, org_id):
        return None
    result = sci_check(db, org_id, getattr(lead, "phone", None),
                       from_number=from_number, messaging_service_sid=messaging_service_sid)
    if result["eligible"]:
        return None
    log.warning("sci_sms BLOCKED lead=%s reasons=%s", getattr(lead, "id", None),
                ",".join(result["reasons"]))
    return result["reasons"]


# ── Operator view ───────────────────────────────────────────────────────────

def programs_view() -> List[Dict[str, Any]]:
    return [{"program": p.key, "consent_program": p.consent_program, "sender": p.sender,
             "form_id": p.form_id, "copy_status": p.copy_status,
             "current_version": p.current_version, "open": is_open(p),
             "org_configured": (p.org_env is None) or bool((os.environ.get(p.org_env) or "").strip()),
             "page": "/sms-optin/" + ("" if p.key == GENERAL else "?program=" + p.key)}
            for p in PROGRAMS.values()]


def record_view(rec) -> Dict[str, Any]:
    """One ledger row with its program's sender and wording check."""
    from app.services import wholesale_sms
    out = wholesale_sms.consent_json(rec) or {}
    p = next((x for x in PROGRAMS.values() if x.consent_program == rec.program), None)
    out["sender"] = p.sender if p else None
    out["page_program"] = p.key if p else None
    out["wording_registered"] = bool(p) and wording_matches(
        p, rec.disclosure_version, rec.disclosure_text)
    out["stop_status"] = "opted_out" if (rec.status != "opted_in" or rec.opted_out_at) else "active"
    return out
