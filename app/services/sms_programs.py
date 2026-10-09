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

THE SCI SEND GATE (revised 2026-10-08: toll-free line, existing contacts)
-----------------
No SMS reaches an SCI contact unless ALL hold (every failing code is reported):

    SCI_SMS_DISABLED              SCI_SMS_SEND_ENABLED is not on
    INVALID_NUMBER                not a usable US mobile number
    NO_SMS_CONSENT                no SCI consent of record for this number in this org
    OPTED_OUT                     the latest SCI consent was withdrawn (STOP etc.)
    SUPPRESSED                    on the organization's suppression list
    CONSENT_WORDING_UNREGISTERED  a web consent not under the FINAL SCI wording, or an
                                  owner-attested one without its attestation
    CAMPAIGN_NOT_APPROVED         the sender is not an APPROVED sender listing "sci"
                                  (the toll-free line needs Toll-Free Verification)
    SENDER_SCOPE_NOT_APPROVED     promotional content on a sender approved only for
                                  informational messages

Existing contacts count through owner-attested consent (`reconcile_existing`):
they do not have to sign up again. Suppression, DNC, holds and location review
are also enforced by send_sms before this gate runs. There is no override.
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
CAMPAIGN_NOT_APPROVED = "CAMPAIGN_NOT_APPROVED"        # the sending number is not an approved SCI sender
SCOPE_NOT_APPROVED = "SENDER_SCOPE_NOT_APPROVED"      # e.g. promotional content on an informational-only toll-free
SUPPRESSED = "SUPPRESSED"
LOCATION_UNVERIFIED = "LOCATION_UNVERIFIED"          # e.g. Oaklawn: address / area code not verified
SCI_REASONS = (SCI_SMS_DISABLED, INVALID_NUMBER, NO_SMS_CONSENT, OPTED_OUT, SUPPRESSED,
               LOCATION_UNVERIFIED, CONSENT_WORDING_UNREGISTERED, CAMPAIGN_NOT_APPROVED,
               SCOPE_NOT_APPROVED)


def location_unverified(db, lead) -> bool:
    """True when the contact's cemetery is UNVERIFIED in the SCI grouping file
    (scripts/sci_campuses.csv: Address Status "unverified" or no area code -
    today only Oaklawn Central Care Center). Such contacts are held: nothing is
    sent for a location whose identity is not confirmed."""
    try:
        from app.services.programs import campuses, identity
        prof = identity.location_profile_for_lead(db, lead)
        if prof is None:
            return False                     # unresolved is refused upstream (Location Review)
        row = next((r for r in campuses.load_grouping() if r.get("Location") == prof.official_name), None)
        return row is not None and ((row.get("Address Status") or "").strip().lower() == "unverified"
                                    or not (row.get("Area Code") or "").strip())
    except Exception:                                   # noqa: BLE001
        return True                          # cannot tell: hold

# ── Existing contacts: owner-attested consent (Mike, 2026-10-08) ────────────
# The SCI contact list and its consent records are held by EVO Integrated
# Solutions LLC: the contacts opted in through the EvoSys Pro opt-in page.
# Those contacts are NOT made to sign up again. `reconcile_existing` files one
# consent of record per eligible contact, method `owner_attested_import`, with
# who attested, when, and the evidence reference - and it skips anyone who
# is suppressed, DNC, or has ever opted out of SCI messages.
METHOD_OWNER_ATTESTED = "owner_attested_import"
ATTESTED_VERSION = "owner-attested-evosyspro-optin"
ATTESTED_FORM_ID = "owner_attested_import"
ATTESTED_SOURCE = "https://evosyspro.live/sms-optin/"

# SCI sends from the toll-free line, never an advisor's or a local number.
SCI_SENDER_ENV = "SCI_TOLL_FREE_NUMBER"
SCI_SENDER_DEFAULT = "+18449172171"
# Campaign families whose SMS is promotional (needs "promotional" in the
# sender's approved scope). Everything else is informational follow-up on
# information the contact asked for. Override with SCI_PROMOTIONAL_FAMILIES.
SCI_PROMOTIONAL_FAMILIES_ENV = "SCI_PROMOTIONAL_FAMILIES"
SCI_PROMOTIONAL_FAMILIES_DEFAULT = ("cemetery_x_sell",)


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
        # 10DLC: the campaign id. Toll-free (no campaign): the registry key.
        rec.campaign_sid = (entry or {}).get("campaign_id") or (entry or {}).get("key")
        rec.messaging_service_sid = (entry or {}).get("messaging_service_sid")
        db.flush()
    return rec


def campaign_for_program(key: str) -> Optional[Dict[str, Any]]:
    """The registered campaign that lists this program (approved ones first)."""
    from app.services import sms_campaigns
    if key == SCI:
        sender = sci_sender_entry()          # SCI sends only from its toll-free line
        if sender is not None and SCI in (sender.get("programs") or []):
            return sender
    hits = [e for e in sms_campaigns.registry() if key in (e.get("programs") or [])]
    hits.sort(key=lambda e: not sms_campaigns.is_approved(e))
    return hits[0] if hits else None


# ── The SCI send gate ───────────────────────────────────────────────────────

def sci_org_id(db) -> Optional[str]:
    """SMS_PROGRAM_ORG_SCI, else the ONE active organization named SCI_ORG_NAME."""
    from app.models.models import Organization
    env = (os.environ.get(PROGRAMS[SCI].org_env) or "").strip()
    if env:
        return env
    rows = (db.query(Organization.id).filter(Organization.name == SCI_ORG_NAME,
                                             Organization.is_active.isnot(False)).all())
    return rows[0][0] if len(rows) == 1 else None


def is_sci_org(db, org_id: Optional[str]) -> bool:
    if not org_id:
        return False
    if org_id == (os.environ.get(PROGRAMS[SCI].org_env) or "").strip():
        return True
    from app.models.models import Organization
    org = db.query(Organization).filter(Organization.id == org_id).first()
    return bool(org) and (org.name or "").strip() == SCI_ORG_NAME


def consent_counts(p: Program, rec) -> bool:
    """Does this consent record count for sending? A web-form consent needs the
    program's FINAL registered wording; an owner-attested one needs the
    attestation version and an evidence reference."""
    if rec is None:
        return False
    if rec.consent_method == METHOD_OWNER_ATTESTED:
        return rec.disclosure_version == ATTESTED_VERSION and bool((rec.disclosure_text or "").strip())
    return (is_final_version(p, rec.disclosure_version)
            and wording_matches(p, rec.disclosure_version, rec.disclosure_text))


def sci_sender_number() -> str:
    return (os.environ.get(SCI_SENDER_ENV) or "").strip() or SCI_SENDER_DEFAULT


def sci_sender_entry() -> Optional[Dict[str, Any]]:
    from app.services import sms_campaigns
    return sms_campaigns.for_sender(sci_sender_number())


def promotional_families() -> set:
    raw = os.environ.get(SCI_PROMOTIONAL_FAMILIES_ENV)
    if raw is None:
        return set(SCI_PROMOTIONAL_FAMILIES_DEFAULT)
    return {x.strip() for x in raw.split(",") if x.strip()}


def message_category(db, lead) -> str:
    """informational | promotional, from the contact's campaign family."""
    from app.services import sms_campaigns
    try:
        from app.services.programs import identity
        rec = identity.source_record_for_lead(db, lead)
        fam = getattr(rec, "campaign_family", None)
    except Exception:                                   # noqa: BLE001
        fam = None
    return (sms_campaigns.CATEGORY_PROMOTIONAL if fam and fam in promotional_families()
            else sms_campaigns.CATEGORY_INFORMATIONAL)


def sci_check(db, org_id: str, phone: Optional[str], *,
              from_number: Optional[str] = None,
              messaging_service_sid: Optional[str] = None,
              category: Optional[str] = None) -> Dict[str, Any]:
    """Deterministic: reads env, the database and the sender registry."""
    from app.services import sms_campaigns, wholesale_sms
    from app.services.compliance_service import is_phone_suppressed
    p = PROGRAMS[SCI]
    reasons: List[str] = []
    if not _truthy(SCI_SEND_ENV):
        reasons.append(SCI_SMS_DISABLED)
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
            if not consent_counts(p, consent):
                reasons.append(CONSENT_WORDING_UNREGISTERED)
        if is_phone_suppressed(db, org_id, e164):
            reasons.append(SUPPRESSED)
    entry = sms_campaigns.for_sender(from_number, messaging_service_sid)
    if not sms_campaigns.is_approved(entry) or SCI not in (entry.get("programs") or []):
        reasons.append(CAMPAIGN_NOT_APPROVED)
    elif not sms_campaigns.scope_allows(entry, category):
        reasons.append(SCOPE_NOT_APPROVED)
    reasons = [r for r in SCI_REASONS if r in reasons]
    return {"eligible": not reasons, "reasons": reasons, "program": p.consent_program,
            "phone": e164, "consent_id": getattr(consent, "id", None),
            "consent_method": getattr(consent, "consent_method", None),
            "sender": (entry or {}).get("key"), "sender_kind": sms_campaigns.kind(entry) if entry else None,
            "category": category or sms_campaigns.CATEGORY_INFORMATIONAL}


def content_allowance(db, lead) -> Dict[str, bool]:
    """For an SCI contact: may its message carry a link / a phone number?
    Only when the SCI sender is approved, lists SCI, and covers the message's
    category. {} for everyone else (the platform default - CO3YNIF - applies)."""
    from app.services import sms_campaigns
    if lead is None or not is_sci_org(db, getattr(lead, "organization_id", None)):
        return {}
    entry = sci_sender_entry()
    ok = (sms_campaigns.is_approved(entry) and SCI in (entry.get("programs") or [])
          and sms_campaigns.scope_allows(entry, message_category(db, lead)))
    f = sms_campaigns.features(entry) if ok else {"links": False, "phone": False}
    return {"allow_links": f["links"], "allow_phone": f["phone"]}


def sci_sender():
    """(twilio_client, from_number) for SCI: the EvoSys Pro platform account
    and the toll-free line. Raises ValueError when not configured. Reached only
    after the SCI gate has passed."""
    sid = (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip()
    if not (sid and token):
        raise ValueError("SCI_SENDER_NOT_CONFIGURED: platform Twilio account is not set")
    from twilio.rest import Client
    return Client(sid, token), sci_sender_number()


def reconcile_existing(db, org_id: str, *, attested_by: str, evidence_reference: str,
                       apply: bool = False, evidence_phones=None,
                       owner_holds_records: bool = False) -> Dict[str, Any]:
    """File owner-attested SCI consent for the existing contacts. Dry run unless
    `apply`. Never touches a number that is suppressed, DNC, or has ANY SCI
    opt-out on record; never duplicates an active consent.

    EVIDENCE REQUIRED TO APPLY (Phase 3, 2026-10-08). An attestation is filed
    ONLY for a number that appears in `evidence_phones` - the phone numbers in
    the actual opt-in records (e.g. the EvoSys Pro opt-in page export). A
    number with no matching record is reported `no_opt_in_evidence` and gets
    nothing. Without an evidence list, a dry run reports what the protections
    alone would allow; `apply` is refused.

    OWNER HOLDS THE RECORDS (2026-10-09, Mike's decision). With
    `owner_holds_records=True` the owner attests that he physically holds the
    original opt-in records (seminar sign-ups, guide requests, web forms) for
    these contacts. Apply then needs no evidence list: every number that passes
    the protections (STOP / opt-out, suppression, DNC) is attested, and each
    record says where the originals are held."""
    from app.models.models import Lead
    from app.models.sms_consent_models import SmsConsentRecord
    from app.services import wholesale_sms
    from app.services.compliance_service import is_phone_suppressed
    if not (attested_by or "").strip() or not (evidence_reference or "").strip():
        raise ValueError("attested_by and evidence_reference are required")
    p = PROGRAMS[SCI]
    evidence = None
    if evidence_phones is not None:
        evidence = {e for e in (wholesale_sms.normalize_e164(x) for x in evidence_phones) if e}
    if apply and evidence is None and not owner_holds_records:
        raise ValueError("apply needs the opt-in evidence: evidence_phones from the actual opt-in records")
    out = {"eligible": 0, "created": 0, "skipped": {}, "apply": bool(apply),
           "evidence_supplied": evidence is not None,
           "owner_holds_records": bool(owner_holds_records),
           "evidence_numbers": len(evidence) if evidence is not None else 0}

    def skip(reason):
        out["skipped"][reason] = out["skipped"].get(reason, 0) + 1

    seen = set()
    leads = (db.query(Lead).filter(Lead.organization_id == org_id)
             .order_by(Lead.created_at.asc()).all())
    for lead in leads:
        e164 = wholesale_sms.normalize_e164(lead.phone)
        if not e164:
            skip("no_valid_mobile")
            continue
        if e164 in seen:
            skip("duplicate_number")
            continue
        seen.add(e164)
        status = (getattr(lead.status, "value", lead.status) or "").lower()
        if status == "dnc":
            skip("dnc")
            continue
        if is_phone_suppressed(db, org_id, e164):
            skip("suppressed")
            continue
        recs = (db.query(SmsConsentRecord)
                .filter(SmsConsentRecord.organization_id == org_id,
                        SmsConsentRecord.program == p.consent_program,
                        SmsConsentRecord.phone_normalized == e164).all())
        if any(r.status != "opted_in" or r.opted_out_at for r in recs):
            skip("opted_out_before")
            continue
        if any(consent_counts(p, r) for r in recs):
            skip("already_consented")
            continue
        if evidence is not None and e164 not in evidence:
            skip("no_opt_in_evidence")
            continue
        out["eligible"] += 1
        if apply:
            if owner_holds_records and evidence is None:
                text = _owner_attestation_text(attested_by, evidence_reference)
            else:
                text = ("Owner-attested: this contact opted in to text messages through the EvoSys Pro "
                        "opt-in page (%s). Consent records are held by EVO Integrated Solutions LLC. "
                        "Attested by %s. Evidence: %s." % (ATTESTED_SOURCE, attested_by.strip(),
                                                           evidence_reference.strip()))
            rec = wholesale_sms.record_consent(
                db, org_id, phone_raw=lead.phone, disclosure_text=text,
                disclosure_version=ATTESTED_VERSION, form_version=None,
                source_url=ATTESTED_SOURCE, ip=None, user_agent=None, lead=None,
                program=p.consent_program, form_id=ATTESTED_FORM_ID,
                consent_method=METHOD_OWNER_ATTESTED)
            rec.lead_id = lead.id
            entry = sci_sender_entry()
            rec.campaign_sid = (entry or {}).get("campaign_id") or (entry or {}).get("key")
            out["created"] += 1
    if apply:
        db.commit()
    return out


# The owner (Mike Simmons) holds the original sign-up records for every contact
# he imports. So a contact with no consent on file is cleared automatically the
# first time a text is about to go to it - no import step, no operator step.
# The protections still hold: STOP / opt-out, suppression and DNC are never
# cleared. Turn off with SCI_OWNER_HOLDS_RECORDS=off.
OWNER_RECORDS_ENV = "SCI_OWNER_HOLDS_RECORDS"
OWNER_NAME_ENV = "SCI_OWNER_NAME"


def owner_holds_records() -> bool:
    v = (os.environ.get(OWNER_RECORDS_ENV) or "on").strip().lower()
    return v not in ("off", "0", "false", "no")


def _owner_attestation_text(attested_by: str, evidence_reference: str) -> str:
    return ("Owner-attested: this contact opted in through the location's original "
            "sign-up (seminar registration, guide request or web form). The original "
            "opt-in records are held by the owner, Mike Simmons. Attested by %s. "
            "Evidence: %s." % (attested_by.strip(), evidence_reference.strip()))


def auto_attest_lead(db, org_id: str, lead) -> bool:
    """File the owner's consent record for one SCI contact if it has none.
    True when a record was filed. Never touches an opted-out, suppressed or
    DNC number, and never duplicates an active consent."""
    if not owner_holds_records() or not _truthy(SCI_SEND_ENV):
        return False                                # nothing is filed while SCI texting is off
    from app.models.sms_consent_models import SmsConsentRecord
    from app.services import wholesale_sms
    from app.services.compliance_service import is_phone_suppressed
    e164 = wholesale_sms.normalize_e164(getattr(lead, "phone", None))
    if not e164:
        return False
    status = (getattr(getattr(lead, "status", None), "value", getattr(lead, "status", None)) or "").lower()
    if status == "dnc" or is_phone_suppressed(db, org_id, e164):
        return False
    p = PROGRAMS[SCI]
    recs = (db.query(SmsConsentRecord)
            .filter(SmsConsentRecord.organization_id == org_id,
                    SmsConsentRecord.program == p.consent_program,
                    SmsConsentRecord.phone_normalized == e164).all())
    if any(r.status != "opted_in" or r.opted_out_at for r in recs):
        return False
    if any(consent_counts(p, r) for r in recs):
        return False
    owner = (os.environ.get(OWNER_NAME_ENV) or "Mike Simmons").strip()
    rec = wholesale_sms.record_consent(
        db, org_id, phone_raw=lead.phone,
        disclosure_text=_owner_attestation_text(owner, "original sign-up records held by the owner"),
        disclosure_version=ATTESTED_VERSION, form_version=None,
        source_url=ATTESTED_SOURCE, ip=None, user_agent=None, lead=None,
        program=p.consent_program, form_id=ATTESTED_FORM_ID,
        consent_method=METHOD_OWNER_ATTESTED)
    rec.lead_id = getattr(lead, "id", None)
    entry = sci_sender_entry()
    rec.campaign_sid = (entry or {}).get("campaign_id") or (entry or {}).get("key")
    db.commit()
    log.info("sci_sms auto-attested lead=%s", getattr(lead, "id", None))
    return True


def sci_send_refusal(db, lead, *, from_number: Optional[str] = None,
                     messaging_service_sid: Optional[str] = None) -> Optional[List[str]]:
    """None for a non-SCI lead. For an SCI lead: None when permitted, else codes."""
    org_id = getattr(lead, "organization_id", None)
    if not is_sci_org(db, org_id):
        return None
    try:
        auto_attest_lead(db, org_id, lead)
    except Exception:                                   # noqa: BLE001
        db.rollback()
        log.exception("sci_sms auto-attest failed lead=%s", getattr(lead, "id", None))
    result = sci_check(db, org_id, getattr(lead, "phone", None),
                       from_number=from_number, messaging_service_sid=messaging_service_sid,
                       category=message_category(db, lead))
    if location_unverified(db, lead):
        result["reasons"] = [r for r in SCI_REASONS if r in set(result["reasons"]) | {LOCATION_UNVERIFIED}]
        result["eligible"] = False
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
