"""
Internal test records — one rule, one place.

WHY THIS EXISTS
---------------
Internal staff end up in production lead tables. On Aug 25 2026 the EvoSys Pro
Sales Manager was found sitting in a funeral home's pre-need queue with SMS
enabled and a booking link — nothing had been sent yet, but a single
"start cadence for all eligible leads" would have put automated funeral nurture
messages on his phone.

`Lead.is_test = True` marks a record as a staff member or QA fixture. Mike's
instruction was to keep those records (they are genuinely useful for regression
testing) but make it impossible for them to receive real outreach.

USE THIS, DO NOT RE-DERIVE THE RULE
-----------------------------------
Every bulk-outreach query filters through `exclude_test_records()`, and every
single-send path checks `is_outreach_eligible()`. Re-implementing "and not
is_test" at each call site is how one path eventually gets missed — which is the
exact failure this module exists to prevent.

Test records are excluded from:
    bulk cadence enrolment · campaigns · production SMS · production email ·
    automated outreach · reporting meant to reflect real performance

They are NOT excluded from: the leads list, search, or a deliberate one-off
manual send by a human who can see the TEST badge. Testers still need to test.
"""
from typing import Optional

from app.models.models import Lead


def is_test_record(lead: Lead) -> bool:
    """True for an internal/QA record. Tolerates the column being absent on very
    old rows so this is safe to call before the migration has run everywhere."""
    return bool(getattr(lead, "is_test", False))


def manual_send_allowed() -> bool:
    """May a person's deliberate one-to-one send reach a test record?

    Oct 2026 (lead workflow redesign spec, Mike's call): in PRODUCTION, no -
    a test record is blocked on every path, manual included. On staging and
    demo, yes, so live tests against staff phones keep working. Decided by
    APP_ENV (app/services/environment.py), which defaults to production.
    """
    from app.services import environment
    return not environment.is_production()


def exclude_test_records(query):
    """Apply to EVERY bulk-outreach and performance-reporting query.

        q = db.query(Lead).filter(Lead.organization_id == org_id)
        q = exclude_test_records(q)

    Uses `IS NOT TRUE` rather than `== False` so pre-migration NULL rows are
    treated as real leads rather than being silently dropped from outreach.
    """
    return query.filter(Lead.is_test.isnot(True))


def not_test_booking(query):
    """Drop bookings that belong to a test record. A booking with no lead
    (rare, manual) stays."""
    from sqlalchemy import or_
    from app.models.models import BookingLink
    test_ids = query.session.query(Lead.id).filter(Lead.is_test.is_(True))
    return query.filter(or_(BookingLink.lead_id.is_(None), ~BookingLink.lead_id.in_(test_ids)))


def exclude_test_program_records(query):
    """Drop outreach-program source rows that belong to a test record (the
    booking-page test contact). They are kept in the database so the test link
    keeps working, but no count, list or queue shows them."""
    from sqlalchemy import or_
    from app.models.program_models import ProgramSourceRecord as R
    test_ids = query.session.query(Lead.id).filter(Lead.is_test.is_(True))
    return query.filter(~R.source_lead_id.like("TEST-BOOKING-%"),
                        or_(R.lead_id.is_(None), ~R.lead_id.in_(test_ids)))


def is_outreach_eligible(lead: Lead) -> bool:
    """Single-send gate. False = never send automated outreach to this record.

    Covers the test flag AND the pre-existing suppression signals, so callers
    have one question to ask instead of four.
    """
    if lead is None:
        return False
    if is_test_record(lead):
        return False
    if getattr(lead, "status", None) == "dnc":
        return False
    if getattr(lead, "manual_flag", None) == "remove_all":
        return False
    return True


def blocked_reason(lead: Lead) -> Optional[str]:
    """Human-readable reason a send was skipped, for logs and the UI. Silent
    skips are how people lose confidence in an automation."""
    if lead is None:
        return "lead not found"
    if is_test_record(lead):
        return "internal test record — excluded from production outreach"
    if getattr(lead, "status", None) == "dnc":
        return "lead is on the DNC list"
    if getattr(lead, "manual_flag", None) == "remove_all":
        return "lead manually flagged remove_all"
    return None


def exclude_test_responses(query):
    """Drop replies from test / synthetic contacts. Kept in the database for
    audit; never in a production count, list or alert sweep."""
    from app.models.program_models import ProgramResponse as PR
    test_ids = query.session.query(Lead.id).filter(Lead.is_test.is_(True))
    return query.filter(~PR.lead_id.in_(test_ids))


def exclude_test_alerts(query):
    """Drop staff alerts produced by test contacts or test sends."""
    from sqlalchemy import or_
    from app.models.program_models import ProgramAlert as PA, ProgramResponse as PR
    test_ids = query.session.query(Lead.id).filter(Lead.is_test.is_(True))
    test_resps = query.session.query(PR.id).filter(PR.lead_id.in_(test_ids))
    return query.filter(or_(PA.kind.is_(None), PA.kind != "test"),
                        or_(PA.response_id.is_(None), ~PA.response_id.in_(test_resps)),
                        or_(PA.message.is_(None), ~PA.message.like("TEST%")))


# ── THE OWNER'S LIVE TEST CONTACT ───────────────────────────────────────────
# One synthetic contact per outreach program whose phone is the program
# owner's own verified alert number. It proves the REAL path end to end (AI
# reply -> SCI line -> carrier -> the owner's phone, and the owner's reply
# back in) without touching a family. It stays is_test (out of every count,
# list and alert) and only the appointment setter may text it.
OWNER_TEST_LAST_NAME = "OWNER-LIVE-TEST"


def _digits(p) -> str:
    d = "".join(ch for ch in (p or "") if ch.isdigit())
    return d[-10:]


def owner_live_test_send_allowed(db, lead, send_source) -> bool:
    from app.services import send_source as _ss
    if not (is_test_record(lead) and getattr(lead, "last_name", None) == OWNER_TEST_LAST_NAME):
        return False
    # MANUAL: the owner answering his own test conversation by hand in SCI Live.
    if send_source not in (_ss.SCI_SETTER, _ss.APPOINTMENT_FOLLOWUP, _ss.MANUAL):
        return False
    from app.services.programs import identity
    prog = identity.program_for_org(db, lead.organization_id)
    owner = _digits(getattr(prog, "alert_phone", None)) if prog else ""
    return bool(owner) and len(owner) == 10 and _digits(getattr(lead, "phone", None)) == owner


def owner_live_test_email_allowed(db, lead, to_addr) -> bool:
    """The owner's live test contact may be EMAILED only at the program's own
    owner alert address - the email twin of owner_live_test_send_allowed."""
    if not (is_test_record(lead) and getattr(lead, "last_name", None) == OWNER_TEST_LAST_NAME):
        return False
    from app.services.programs import identity
    prog = identity.program_for_org(db, lead.organization_id)
    owner = (getattr(prog, "alert_email", None) or "").strip().lower() if prog else ""
    return bool(owner) and "@" in owner and (to_addr or "").strip().lower() == owner


def is_owner_test_lead(lead) -> bool:
    return bool(is_test_record(lead) and getattr(lead, "last_name", None) == OWNER_TEST_LAST_NAME)
