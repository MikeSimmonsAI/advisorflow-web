"""WHO IS ACTUALLY QUALIFIED FOR THIS CAMPAIGN — one engine, every organization.

THE QUESTION THIS ANSWERS
-------------------------
Before any organization runs outreach, the platform has to be able to say who
is actually qualified. Five things were being treated as if they were the same
answer, and none of them is:

    assigned   is not  qualified
    has email  is not  qualified
    has phone  is not  qualified
    imported   is not  qualified
    AUTHORIZED is not  qualified

The last one is the one that matters most, and it is a one-way street:

    AUTHORIZED SCOPE  ->  QUALIFICATION          yes
    QUALIFICATION     ->  AUTHORIZED SCOPE       never

Every entry point here begins from `lead_scope.authorized_lead_query`, which
already carries the organization filter and the advisor's own-records filter.
Qualification can only ever narrow what comes back. There is no rule, no
filter, no channel and no effect in this module that can add a lead the caller
was not already entitled to - and the gate tests prove it by asking for another
advisor's lead and another tenant's lead by id.

WHY IT IS ONE SERVICE AND NOT ONE PER PAGE
------------------------------------------
Before this existed the answer lived in at least five places that did not agree:
`campaign_router._compliance_check`, `campaign_router._apply_filters`,
`test_records.is_outreach_eligible`, `compliance_service.is_phone_suppressed`,
and per-channel checks inside the send services. Adding a sixth for one
customer's report would have made it six. So this reuses those services rather
than reimplementing them - the DNC, suppression, test-record and duplicate
rules still live where they always did, and this module calls them.

THREE BUCKETS, AND WHY NOT TWO
------------------------------
    READY_TO_SEND     the server can defend sending this one
    REVIEW_REQUIRED   a person should look first - NOT a refusal
    EXCLUDED          must not be contacted on this channel

A two-bucket design forces every uncertain lead into one of the extremes:
either you mail an address you are not sure about, or you silently drop a real
family. REVIEW_REQUIRED exists so that "we are not sure" is a state somebody can
act on rather than a decision the machine makes on their behalf.

CHANNEL IS PART OF THE QUESTION
-------------------------------
Qualification is NOT one global yes/no field on a lead. A person with no email
address is EXCLUDED for email and may be perfectly READY for SMS. A person who
replied STOP to a text is excluded from SMS and voice and may still be mailable.
So every answer here is (lead, channel), never (lead).

EMAIL is the channel implemented in this pass and it is authoritative for email.
SMS and VOICE are declared and evaluated, but their existing per-channel guards
remain the enforcement path until each is migrated and independently tested -
removing a proven guard before its replacement is proven is how a compliance
regression ships.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.models.models import (
    Lead, Message, Reply, EmailMessage, BookingLink, LeadOutcome, User,
)
from app.services import lead_scope
from app.services.lead_scope import authorized_lead_query, assert_leads_in_scope

_log = logging.getLogger("advisorflow.qualification")

# ── channels ────────────────────────────────────────────────────────────────
CHANNEL_EMAIL = "email"
CHANNEL_SMS = "sms"
CHANNEL_VOICE = "voice"
CHANNELS = (CHANNEL_EMAIL, CHANNEL_SMS, CHANNEL_VOICE)

# The channel whose answer this engine is authoritative for TODAY. The others
# are evaluated for reporting and preview, but their send paths still enforce
# through their own guards until they are migrated in a later controlled pass.
AUTHORITATIVE_CHANNELS = (CHANNEL_EMAIL,)

# ── buckets ─────────────────────────────────────────────────────────────────
READY = "READY_TO_SEND"
REVIEW = "REVIEW_REQUIRED"
EXCLUDED = "EXCLUDED"
BUCKETS = (READY, REVIEW, EXCLUDED)

# ── priority bands ──────────────────────────────────────────────────────────
HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"
PRIORITIES = (HIGH, MEDIUM, LOW)

# ── the bands ───────────────────────────────────────────────────────────────
#
# CALIBRATED AGAINST THE MAXIMUM A LEAD CAN SCORE ON REACHABILITY ALONE, which
# is 12 (valid email 4 + valid mobile 4 + both 4). The first version set HIGH at
# 60 while contact details plus two default-true conditions were worth exactly
# 60, so every reachable lead with no history landed exactly on the boundary and
# an entire book scored HIGH with zero spread. A band that every row reaches is
# not a priority, it is a label.
#
# HIGH now requires ENGAGEMENT EVIDENCE - a reply, a booking, a genuine customer
# relationship, or contact within the last 90 days. Everything else a lead can
# accumulate tops out at MAX_SCORE_WITHOUT_EVIDENCE below, which is deliberately
# under this line. There is no combination of "we can reach them and their
# record is tidy" that reaches HIGH, and a gate asserts it by constructing
# exactly that lead and checking the number.
HIGH_THRESHOLD = 45
MEDIUM_THRESHOLD = 22

# THE CEILING WITHOUT EVIDENCE, ARITHMETIC RATHER THAN OPINION.
#
#   reachability      valid email 4 + valid phone 4 + both 4         = 12
#   contact history   the best non-evidence branch, never contacted  =  8
#   relationship      batch-stamped assertion                        =  4
#   record quality    no completed outcome 4 + complete 3 + full 2   =  9
#                                                                     ---
#                                                                      33
#
# The cohort line that used to sit here is GONE, not reduced. `source_year` is
# typed by whoever runs the import; scoring recency from it meant a family last
# worked with in 2018 and uploaded under 2026 read as a recent lead. Lowering
# its weight would have kept the same falsehood at a lower volume.
#
# EVIDENCE is what a person did, or a relationship the organization holds:
# replied (30), booked (22), existing customer (18), contacted in the last 90
# days (14). HIGH sits above the ceiling so that no amount of reachability,
# tidiness or import metadata can reach it without one of those.
#
# A gate sweeps every evidence-free combination and asserts the maximum equals
# this number, so the comment cannot quietly stop being true.
MAX_SCORE_WITHOUT_EVIDENCE = 33

# A ceiling so one call cannot try to score an entire estate in one request.
MAX_QUALIFY_ROWS = 50000


# ── the reason vocabulary ───────────────────────────────────────────────────
#
# EVERY DECISION CARRIES REASONS, and every reason is a stable code plus a
# sentence. The code is what the UI groups and the gate asserts on; the
# sentence is what the person reading the screen actually needs. A count with
# no reason is the thing this engine exists to replace - "960 excluded" tells
# an admin nothing they can act on, and "310 have no email address on file"
# tells them to go fix 310 records.
REASONS = {
    # ── hard exclusions ──
    "dnc": "On the Do Not Contact list",
    "suppressed": "On the organization's suppression list",
    "opted_out": "Opted out of this channel",
    "flagged_remove_all": "Manually flagged: removed from all outreach",
    "flagged_bad_email": "Manually flagged: unusable email address",
    "missing_email": "No email address on file",
    "invalid_email": "Email address is not a valid address",
    "missing_phone": "No phone number on file",
    "invalid_phone": "Phone number is not usable",
    "no_sms_consent": "No SMS consent of record",
    "internal_record": "Internal or test record, not a real prospect",
    "duplicate": "Unresolved duplicate of another record",
    "org_rule_exclusion": "Excluded by an organization rule",
    "channel_not_supported": "This lead is not routed to this channel",

    # ── review required ──
    "needs_classification": "Not yet classified - a person should set the type",
    "role_address": "Shared or role address rather than a person",
    "no_name_on_record": "No name on record to address the message to",
    "recently_contacted": "Contacted very recently - may be too soon",
    "org_rule_review": "An organization rule asks for human review",

    # ── priority factors ──
    "has_valid_email": "Valid email address",
    # NOT "valid mobile". The platform holds no line-type or carrier data, so
    # it cannot tell a mobile from a landline and must not say it can. The
    # column is a normalized dialable number and the label now says exactly
    # that. If line-type lookup is ever added, this can say mobile and mean it.
    "has_valid_phone": "Valid phone number",
    "reachable_both": "Reachable by both email and phone",
    "existing_relationship": "Existing or former customer relationship",
    # NAMED FOR WHAT IT ACTUALLY IS. This was "Prior interest or referral on
    # record", which reads as a fact about the person. It is not: it is the
    # relationship_type chosen once for a whole import file and stamped on
    # every row in it. The label now says whose assertion it is, so nobody
    # reading a reason list mistakes a batch setting for a warm lead.
    "batch_relationship": "Import batch classified with this relationship",
    "never_contacted": "Never contacted by anyone on record",
    "recent_contact": "Contacted recently",
    "contact_within_year": "Contacted within the last year",
    "long_since_contact": "No contact in a long time",
    "prior_contact_undated": "Prior contact on record, no date",
    "contacted_no_response": "Contacted before and never replied",
    "prior_response": "Has replied to us before",
    "prior_appointment": "Has booked an appointment before",
    "no_completed_outcome": "No completed outcome recorded",
    "complete_record": "Complete contact record",
    "full_contact_record": "Address, email and phone all on file",
    "org_rule_boost": "Matches an organization priority rule",
    "org_rule_demote": "Matches an organization de-prioritization rule",
}


# ── EVERY PRIORITY FACTOR, CLASSIFIED BY WHAT IT IS EVIDENCE OF ─────────────
#
# The audit that produced this table found two factors ranking leads on values
# that are constant within an import batch - `relationship_type`, chosen once
# per file, and `source_year`, typed once by the person uploading. Neither can
# distinguish anybody inside the batch it labels, and one of them was inventing
# recency out of upload metadata.
#
# So every factor now declares which of three things it is, and the rule that
# follows from it:
#
#   evidence   Something this PERSON did, or a relationship the organization
#              actually holds with them. This is what may carry a lead to HIGH.
#   quality    A property of the RECORD - is it complete, can we reach them.
#              Small weights. Contactability is the entry fee for READY, not a
#              distinction, and a tidy record is not an interested family.
#   batch      An assertion about the FILE the lead arrived in. Kept, because
#              the organization is telling us something real about provenance,
#              but weighted so it can never rank one lead above another - and
#              labelled so nobody reads it as a fact about the person.
#
# Published by /qualification/vocabulary so the UI can show the distinction,
# and asserted by a gate: no factor classified `batch` may be worth more than
# the smallest `evidence` factor.
FACTOR_KINDS = {
    "prior_response": "evidence",
    "prior_appointment": "evidence",
    "existing_relationship": "evidence",
    "recent_contact": "evidence",
    "contact_within_year": "evidence",
    "long_since_contact": "evidence",
    "prior_contact_undated": "evidence",
    "contacted_no_response": "evidence",
    "never_contacted": "evidence",
    "org_rule_boost": "evidence",
    "org_rule_demote": "evidence",

    "has_valid_email": "quality",
    "has_valid_phone": "quality",
    "reachable_both": "quality",
    "no_completed_outcome": "quality",
    "complete_record": "quality",
    "full_contact_record": "quality",

    "batch_relationship": "batch",
}

# Fields that are BATCH METADATA: chosen once for a whole import and identical
# for every lead in it. The scorer must not read these. Listed so the rule is
# checkable rather than a convention somebody remembers.
BATCH_METADATA_FIELDS = (
    "source_year", "source_file", "import_list_name", "imported_by_name",
    "source_category", "relationship_type",
)


def reason(code: str, detail: str = "") -> Dict[str, str]:
    """A reason as the API returns it: a stable code and a readable sentence."""
    label = REASONS.get(code, code.replace("_", " "))
    if detail:
        label = "%s - %s" % (label, detail)
    return {"code": code, "label": label}


# ── what a rule is allowed to look at ───────────────────────────────────────
#
# THE WHITELIST IS THE SECURITY BOUNDARY for organization-authored rules. A
# rule names a field as a string; without this, a rule could be pointed at
# `password_hash`, at a relationship that loads another table, or at a dunder
# that does something interesting. Anything not in here, and not an explicit
# `custom_fields.<key>` reference, is refused when the rule is saved and
# ignored if one somehow reaches evaluation.
RULE_FIELDS = (
    "tier", "status", "engagement_temperature", "message_track",
    "contact_channel", "relationship_type", "source_category",
    "source_year", "source_file", "import_list_name",
    "city", "state", "zip_code",
    "case_status", "manual_flag",
    "last_contact_date", "last_messaged_at", "created_at",
    "last_action_raw", "status_reason_raw",
    "first_name", "last_name", "email", "phone",
    "is_duplicate", "is_test",
)

CUSTOM_FIELD_PREFIX = "custom_fields."


def rule_field_is_allowed(field: str) -> bool:
    if not field:
        return False
    if field in RULE_FIELDS:
        return True
    # An organization may reach anything it imported itself, and nothing else.
    return field.startswith(CUSTOM_FIELD_PREFIX) and len(field) > len(CUSTOM_FIELD_PREFIX)


def _custom_fields(lead: Lead) -> Dict[str, Any]:
    raw = getattr(lead, "custom_fields", None)
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _rule_value(lead: Lead, field: str) -> Any:
    if field.startswith(CUSTOM_FIELD_PREFIX):
        return _custom_fields(lead).get(field[len(CUSTOM_FIELD_PREFIX):])
    return getattr(lead, field, None)


def _as_list(raw: Optional[str]) -> List[str]:
    if raw is None:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(v).strip().lower() for v in parsed]
    except Exception:
        pass
    return [p.strip().lower() for p in str(raw).split(",") if p.strip()]


def _as_number(raw: Optional[str]) -> Optional[float]:
    try:
        return float(str(raw).strip())
    except Exception:
        return None


def evaluate_rule(lead: Lead, rule, now: Optional[datetime] = None) -> bool:
    """Does this organization rule match this lead? Never raises.

    A rule that cannot be evaluated - a bad operator, an unparseable value, a
    field that is not on the whitelist - returns False and is logged. It does
    NOT match, because a malformed rule that excluded people would be a silent
    outage, and one that boosted them would be a silent lie.
    """
    now = now or datetime.utcnow()
    try:
        field = getattr(rule, "field", None)
        if not rule_field_is_allowed(field):
            _log.warning("qualification rule %s names a field that is not allowed: %r",
                         getattr(rule, "id", "?"), field)
            return False

        op = getattr(rule, "operator", None)
        raw = getattr(rule, "value", None)
        actual = _rule_value(lead, field)

        if op == "is_empty":
            return actual is None or str(actual).strip() == ""
        if op == "is_not_empty":
            return actual is not None and str(actual).strip() != ""
        if op == "is_true":
            return bool(actual) is True
        if op == "is_false":
            return bool(actual) is False

        if op in ("older_than_days", "newer_than_days"):
            days = _as_number(raw)
            if days is None or not isinstance(actual, datetime):
                return False
            cutoff = now - timedelta(days=days)
            return actual < cutoff if op == "older_than_days" else actual >= cutoff

        if op in ("greater_than", "less_than"):
            threshold = _as_number(raw)
            current = _as_number(actual)
            if threshold is None or current is None:
                return False
            return current > threshold if op == "greater_than" else current < threshold

        text = "" if actual is None else str(actual).strip().lower()
        wanted = "" if raw is None else str(raw).strip().lower()

        if op == "equals":
            return text == wanted
        if op == "not_equals":
            return text != wanted
        if op == "contains":
            return bool(wanted) and wanted in text
        if op == "not_contains":
            return not (bool(wanted) and wanted in text)
        if op == "in":
            return text in _as_list(raw)
        if op == "not_in":
            return text not in _as_list(raw)

        _log.warning("qualification rule %s has an unknown operator %r",
                     getattr(rule, "id", "?"), op)
        return False
    except Exception:
        _log.warning("qualification rule %s failed to evaluate",
                     getattr(rule, "id", "?"), exc_info=True)
        return False


# ── contact validity ────────────────────────────────────────────────────────
#
# Deliberately conservative. A validator that is too clever throws away real
# families: plenty of legitimate addresses look unusual, and the cost of a
# false EXCLUDED is a person who never hears from their funeral home again.
# The cost of a false READY is one bounce, which the review bucket and the
# existing bad_email flag already catch. So this rejects only what cannot
# possibly be deliverable.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

# Addresses that are structurally valid but are not a person. These go to
# REVIEW rather than EXCLUDED - "info@" is sometimes exactly the right address
# for a business, and a machine should not decide that on its own.
_ROLE_LOCALPARTS = (
    "info", "sales", "admin", "office", "support", "contact", "billing",
    "noreply", "no-reply", "donotreply", "postmaster", "webmaster",
    "enquiries", "inquiries", "hello", "team",
)

# Placeholder text that imports produce when a source file had no address.
# These parse as addresses on a lenient reading and are never deliverable.
_PLACEHOLDER_EMAILS = (
    "unknown@unknown", "none@none", "na@na", "n/a", "null@null",
    "test@test", "noemail@noemail", "email@email",
)


def email_validity(lead: Lead) -> str:
    """One of: "missing", "invalid", "role", "ok"."""
    raw = (getattr(lead, "email", None) or "").strip()
    if not raw:
        return "missing"
    low = raw.lower()
    if low in _PLACEHOLDER_EMAILS:
        return "invalid"
    if not _EMAIL_RE.match(raw):
        return "invalid"
    local, _, domain = low.partition("@")
    # A domain that is a placeholder rather than a host. Deliberately a short
    # list of words no real domain uses, and deliberately NOT including
    # "example": example.com is reserved by RFC 2606 and is therefore
    # undeliverable, but it is also what every test fixture and every sample
    # import uses, and an engine that silently excludes an entire population
    # the moment somebody's data happens to look like a sample is worse than
    # one bounce. If an organization wants those gone it writes a rule.
    if domain.split(".")[0] in ("unknown", "none", "null"):
        return "invalid"
    if local in _ROLE_LOCALPARTS:
        return "role"
    return "ok"


def phone_validity(lead: Lead) -> str:
    """One of: "missing", "invalid", "ok". Uses the E.164 column the importer
    normalizes into, so this agrees with what the send paths actually dial."""
    raw = (getattr(lead, "phone", None) or "").strip()
    if not raw:
        return "missing"
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 10:
        return "invalid"
    return "ok"


# ── the per-request context ─────────────────────────────────────────────────
#
# Everything the scorer needs that is NOT on the Lead row, fetched in bulk.
# A per-lead query here would be four round trips times the size of the book,
# which for an estate-sized campaign is the difference between a preview that
# returns and one that times out - and a preview that times out is a preview
# nobody runs, which puts everyone back to sending the largest possible list.
class QualificationContext:
    def __init__(self, db: Session, leads: Sequence[Lead], organization_id: Optional[str],
                 rules: Sequence[Any], now: Optional[datetime] = None):
        self.now = now or datetime.utcnow()
        self.rules = list(rules)
        self.organization_id = organization_id
        ids = [l.id for l in leads]
        self.contacted: set = set()
        self.replied: set = set()
        self.booked: set = set()
        self.outcome_recorded: set = set()
        self.suppressed_phones: set = set()
        if not ids:
            return

        # Chunked so a very large id list does not exceed a driver's bind
        # parameter limit - the failure mode there is an exception on the
        # biggest campaigns only, which is exactly when it is least welcome.
        for chunk in _chunks(ids, 900):
            self.contacted.update(
                r[0] for r in db.query(Message.lead_id)
                .filter(Message.lead_id.in_(chunk)).distinct().all())
            self.contacted.update(
                r[0] for r in db.query(EmailMessage.lead_id)
                .filter(EmailMessage.lead_id.in_(chunk)).distinct().all())
            self.replied.update(
                r[0] for r in db.query(Reply.lead_id)
                .filter(Reply.lead_id.in_(chunk)).distinct().all())
            self.booked.update(
                r[0] for r in db.query(BookingLink.lead_id)
                .filter(BookingLink.lead_id.in_(chunk),
                        BookingLink.booked_time.isnot(None)).distinct().all())
            self.outcome_recorded.update(
                r[0] for r in db.query(LeadOutcome.lead_id)
                .filter(LeadOutcome.lead_id.in_(chunk)).distinct().all())

        # THE SUPPRESSION LIST IS READ ONCE, from the service that owns it.
        # Only needed for the phone channels; loading it for an email run
        # would be a query nobody uses.
        self.suppressed_phones = set()


def _chunks(seq: Sequence[Any], size: int):
    for i in range(0, len(seq), size):
        yield list(seq[i:i + size])


def load_suppressed_phones(db: Session, organization_id: str) -> set:
    """The organization's suppression list, read from the table the compliance
    service owns rather than re-deriving what "suppressed" means."""
    from app.models.models import SuppressionEntry
    return {
        r[0] for r in db.query(SuppressionEntry.phone)
        .filter(SuppressionEntry.organization_id == organization_id).all()
        if r[0]
    }


# ── the decision ────────────────────────────────────────────────────────────

def _permission_detail(lead, channel: str) -> str:
    """Say WHERE the denial came from, so a refusal can be checked, not trusted."""
    src = (getattr(lead, "permission_source", None) or "").strip()
    return f"{channel} permission denied on record ({src})" if src \
        else f"{channel} permission denied on record"


def qualify_one(lead: Lead, channel: str, ctx: QualificationContext) -> Dict[str, Any]:
    """One lead, one channel, one explainable answer.

    Pure with respect to the database: everything it needs is on the lead or in
    the context. That is what lets the gate drive every branch without standing
    up a request, and what keeps a scoring change from turning into an N+1.

    Order matters. Hard exclusions are evaluated first and the function returns
    on the first one, so a lead on the DNC list is never also described as
    "no email address" - the reason a person reads is the reason that decided it.
    """
    channel = (channel or CHANNEL_EMAIL).lower()
    reasons: List[Dict[str, str]] = []

    # ── 1. EXCLUSIONS THAT APPLY ON EVERY CHANNEL ──
    #
    # These come from the services that already own them. `is_test_record` and
    # the DNC / remove_all rules are `test_records`'; the duplicate rule is the
    # importer's. Re-deciding any of them here would create a second opinion,
    # and two opinions about whether a person may be contacted is one too many.
    from app.services import test_records

    if test_records.is_test_record(lead):
        return _decision(lead, EXCLUDED, [reason("internal_record")], channel)

    if (getattr(lead, "status", None) or "").lower() == "dnc":
        return _decision(lead, EXCLUDED, [reason("dnc")], channel)

    if (getattr(lead, "manual_flag", None) or "") == "remove_all":
        detail = getattr(lead, "manual_flag_reason", None) or ""
        return _decision(lead, EXCLUDED, [reason("flagged_remove_all", detail)], channel)

    # An unresolved duplicate is a data-quality exclusion, not a DNC - the
    # importer is careful about that distinction and so is this. A human who
    # decides the two records are different people sets `duplicate_resolved_at`,
    # and the lead qualifies again with no other change.
    if bool(getattr(lead, "is_duplicate", False)) and not getattr(lead, "duplicate_resolved_at", None):
        detail = getattr(lead, "duplicate_reason", None) or ""
        return _decision(lead, EXCLUDED, [reason("duplicate", detail)], channel)

    # ── 2. ORGANIZATION-DEFINED EXCLUSIONS ──
    #
    # Before the channel checks, because an organization saying "never contact
    # this segment" outranks "but they have a valid address".
    for rule in _rules_for(ctx, channel, "exclude"):
        if evaluate_rule(lead, rule, ctx.now):
            return _decision(lead, EXCLUDED,
                             [reason("org_rule_exclusion", rule.reason_label)], channel)

    # ── 3. CHANNEL ELIGIBILITY ──
    review_reasons: List[Dict[str, str]] = []

    # CHANNEL PERMISSION OF RECORD, BEFORE ANY QUESTION OF REACHABILITY.
    #
    # `allow_<channel>` is tri-state: True allowed, False DENIED, NULL never
    # stated. Only False excludes. NULL must not - most rows predate these
    # columns, and reading "we don't know" as "no" would silently empty every
    # existing send pool; reading it as "yes" is the failure the import fix
    # exists to prevent, and that one is handled at the point the value is
    # WRITTEN, not here.
    #
    # This is the reason those columns exist. A denial stored on a lead that no
    # send path consults is not a compliance record, it is a note.
    _permission_field = {CHANNEL_EMAIL: "allow_email",
                         CHANNEL_SMS: "allow_sms",
                         CHANNEL_VOICE: "allow_voice"}.get(channel)
    if _permission_field and getattr(lead, _permission_field, None) is False:
        return _decision(lead, EXCLUDED,
                         [reason("opted_out", _permission_detail(lead, channel))],
                         channel)

    if channel == CHANNEL_EMAIL:
        if (getattr(lead, "manual_flag", None) or "") == "bad_email":
            detail = getattr(lead, "manual_flag_reason", None) or ""
            return _decision(lead, EXCLUDED, [reason("flagged_bad_email", detail)], channel)
        validity = email_validity(lead)
        if validity == "missing":
            return _decision(lead, EXCLUDED, [reason("missing_email")], channel)
        if validity == "invalid":
            return _decision(lead, EXCLUDED,
                             [reason("invalid_email", (lead.email or "").strip())], channel)
        if validity == "role":
            # NOT an exclusion. A shared mailbox is sometimes the right address
            # for a business, and a machine should not decide that alone.
            review_reasons.append(reason("role_address", (lead.email or "").strip()))

    elif channel in (CHANNEL_SMS, CHANNEL_VOICE):
        validity = phone_validity(lead)
        if validity == "missing":
            return _decision(lead, EXCLUDED, [reason("missing_phone")], channel)
        if validity == "invalid":
            return _decision(lead, EXCLUDED, [reason("invalid_phone")], channel)
        if lead.phone and lead.phone in ctx.suppressed_phones:
            return _decision(lead, EXCLUDED, [reason("suppressed")], channel)
        if channel == CHANNEL_SMS and not bool(getattr(lead, "sms_consent", False)):
            # TCPA consent of record. Its absence is an exclusion for marketing
            # SMS, which is why SMS and email cannot share one qualified pool.
            return _decision(lead, EXCLUDED, [reason("no_sms_consent")], channel)

    # ── 4. REVIEW CONDITIONS ──
    if (getattr(lead, "status", None) or "") == "needs_tier_review":
        review_reasons.append(reason("needs_classification"))

    if not (getattr(lead, "first_name", None) or getattr(lead, "last_name", None)):
        review_reasons.append(reason("no_name_on_record"))

    for rule in _rules_for(ctx, channel, "review"):
        if evaluate_rule(lead, rule, ctx.now):
            review_reasons.append(reason("org_rule_review", rule.reason_label))

    # ── 5. PRIORITY, FROM EXPLAINABLE FACTORS ──
    score, factors = _score(lead, channel, ctx)

    if review_reasons:
        return _decision(lead, REVIEW, review_reasons, channel,
                         score=score, factors=factors)
    return _decision(lead, READY, [], channel, score=score, factors=factors)


def _rules_for(ctx: QualificationContext, channel: str, effect: str) -> List[Any]:
    out = []
    for r in ctx.rules:
        if not getattr(r, "is_active", True):
            continue
        if getattr(r, "effect", None) != effect:
            continue
        rc = getattr(r, "channel", None)
        if rc and rc.lower() != channel:
            continue
        out.append(r)
    out.sort(key=lambda r: (getattr(r, "sort_order", 100) or 100))
    return out


def contact_history(lead: Lead, ctx: QualificationContext):
    """Everything known about whether this person has been contacted before.

    THIS IS THE FIX FOR A REAL DEFECT. The first version asked only whether
    OUR tables held a Message or an EmailMessage for the lead. Every lead
    imported from a customer's previous CRM therefore looked "never contacted"
    - including a family the funeral home had called five times in 2013, whose
    own record carries `last_action_raw = "Called: LM/No Answer"` and a
    `last_contact_date` to prove it. The import writes both columns; the scorer
    ignored both, and then awarded points for the silence it had created.

    "Never contacted" now means never contacted BY ANYONE ON RECORD: no
    outbound from us, no imported contact date, and no imported last action.

    Returns (has_history, most_recent, source).
    """
    # Our own outbound, either as a logged message or as the denormalized
    # timestamp the send paths stamp. Both are checked: a lead can carry
    # `last_messaged_at` from a path that did not write a Message row, and
    # trusting only the join would call that lead never contacted.
    platform_sent = getattr(lead, "last_messaged_at", None)
    if lead.id in ctx.contacted or isinstance(platform_sent, datetime):
        return True, platform_sent, "this platform"

    imported_date = getattr(lead, "last_contact_date", None)
    imported_action = (getattr(lead, "last_action_raw", None) or "").strip()
    if isinstance(imported_date, datetime):
        return True, imported_date, "imported history"
    if imported_action:
        # An action with no date is still evidence of contact. It just cannot
        # say when, so it earns the smallest recency credit rather than the
        # largest.
        return True, None, "imported history"
    return False, None, "none on record"


def _score(lead: Lead, channel: str, ctx: QualificationContext):
    """Points and the reasons for them. No opaque model, no hidden weights -
    the factors this returns ARE the explanation, and they add up to the score
    the band is derived from, so a person can check the arithmetic.

    ── EVIDENCE vs CONTEXT, and why the split matters ──────────────────────
    An audit of the first production run found every lead in one advisor's
    book scoring IDENTICALLY, and landing exactly on the HIGH threshold. The
    band was not measuring anything; it was a constant with a number in front
    of it. Two causes, both fixed here:

    1. REACHABILITY WAS PRICED LIKE ENGAGEMENT. Having a valid email and a
       valid phone was worth 25 of the 60 points needed for HIGH. But being
       contactable is what READY already means - it is the entry fee, not a
       distinction. It is now worth 12, and no combination of contact details
       alone can reach HIGH. That is asserted by a gate.

    2. A BATCH STAMP WAS READ AS PER-LEAD EVIDENCE. `relationship_type` is an
       IMPORT PARAMETER: `import_leads(..., relationship_type=...)` applies one
       value to every row in the file. So "prior interest or referral on
       record" was worth 12 points to all of them because somebody chose it
       once in a dropdown - it can never differentiate anybody within a batch,
       because by construction every lead in that batch shares it.

       It is not worthless - the organization is asserting something real about
       where the list came from - so it is kept, at a weight that reflects that
       it is an assertion about a FILE rather than an observation about a
       PERSON, and its label now says so.

    3. RECENCY WAS INVENTED FROM UPLOAD METADATA. `source_year` is typed by
       whoever runs the import, so a family last worked with in 2018 and
       uploaded under 2026 was scored as a recent lead. That factor is removed
       entirely rather than reduced - a smaller wrong number is still wrong.

    What DOES differentiate is per-lead observed fact: did they reply, did they
    book, how long since anyone actually contacted them, how complete is their
    record. Those carry the weight now, and every factor declares which kind it
    is in FACTOR_KINDS.
    """
    factors: List[Dict[str, Any]] = []

    def add(points: int, code: str, detail: str = ""):
        r = reason(code, detail)
        r["points"] = points
        # Carried on every factor so a reader can see at a glance whether a
        # reason is something the person did or something the file said.
        r["kind"] = FACTOR_KINDS.get(code, "quality")
        factors.append(r)

    # ── reachability: the entry fee, priced as one ──
    email_ok = email_validity(lead) == "ok"
    phone_ok = phone_validity(lead) == "ok"
    if email_ok:
        add(4, "has_valid_email")
    if phone_ok:
        add(4, "has_valid_phone")
    if email_ok and phone_ok:
        add(4, "reachable_both")

    # ── engagement evidence: what this person actually did ──
    if lead.id in ctx.replied:
        add(30, "prior_response")
    if lead.id in ctx.booked:
        add(22, "prior_appointment")

    # ── contact history and its recency ──
    has_history, most_recent, source = contact_history(lead, ctx)
    if not has_history:
        add(8, "never_contacted")
    else:
        if isinstance(most_recent, datetime):
            days = max(0, (ctx.now - most_recent).days)
            if days <= 90:
                # EVIDENCE: somebody is in an active thread with this family.
                add(14, "recent_contact", "%d days ago, %s" % (days, source))
            elif days <= 365:
                add(6, "contact_within_year", "%d days ago, %s" % (days, source))
            else:
                add(3, "long_since_contact", "%d days ago, %s" % (days, source))
        else:
            add(2, "prior_contact_undated", source)
        if lead.id not in ctx.replied:
            add(2, "contacted_no_response")

    # ── relationship, and how much of it is evidence ──
    rel = (getattr(lead, "relationship_type", None) or "").lower()
    if rel in ("existing_customer", "past_customer"):
        add(18, "existing_relationship", rel.replace("_", " "))
    elif rel in ("warm_lead", "previous_prospect", "re_engagement"):
        add(4, "batch_relationship", rel.replace("_", " "))

    # ── record quality and cohort age: real per-lead variation ──
    if lead.id not in ctx.outcome_recorded:
        add(4, "no_completed_outcome")
    if all([getattr(lead, "first_name", None), getattr(lead, "last_name", None),
            getattr(lead, "zip_code", None)]):
        add(3, "complete_record")
    if getattr(lead, "street_address", None) and getattr(lead, "email", None) \
            and getattr(lead, "phone", None):
        add(2, "full_contact_record")

    # ── source_year IS NOT READ HERE, DELIBERATELY ──
    #
    # It was, and it was wrong. `source_year` is chosen by the person running
    # the import - it is a label on the FILE, not a fact about the family. A
    # customer the business last worked with in 2018, uploaded under 2026,
    # would have been scored as a recent lead on the strength of a dropdown.
    # Inventing recency from upload metadata is exactly the failure this whole
    # audit was about, and lowering its weight would have kept the lie and made
    # it quieter.
    #
    # The column stays in the database and stays useful - provenance, batch
    # reporting, filtering, source inventory, admin analytics - and it stays in
    # RULE_FIELDS so an organization can still filter or exclude on it. What it
    # may not do is change one lead's priority relative to another in the same
    # batch, because within a batch it is a constant.
    #
    # A gate asserts this function never reads it.

    for rule in _rules_for(ctx, channel, "boost"):
        if evaluate_rule(lead, rule, ctx.now):
            add(int(getattr(rule, "points", 0) or 0), "org_rule_boost", rule.reason_label)
    for rule in _rules_for(ctx, channel, "demote"):
        if evaluate_rule(lead, rule, ctx.now):
            add(-abs(int(getattr(rule, "points", 0) or 0)), "org_rule_demote", rule.reason_label)

    return sum(f["points"] for f in factors), factors


def _band(score: int) -> str:
    if score >= HIGH_THRESHOLD:
        return HIGH
    if score >= MEDIUM_THRESHOLD:
        return MEDIUM
    return LOW


def _decision(lead: Lead, bucket: str, reasons: List[Dict[str, str]], channel: str,
              score: int = 0, factors: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "lead_id": lead.id,
        "channel": channel,
        "bucket": bucket,
        # An excluded lead has no priority. Reporting one would invite somebody
        # to sort by it and wonder why the top row cannot be sent to.
        "priority": _band(score) if bucket in (READY, REVIEW) else None,
        "score": score if bucket in (READY, REVIEW) else None,
        "reasons": reasons,
        "factors": factors or [],
    }


# ── selection filters ───────────────────────────────────────────────────────
#
# THESE SELECT, THEY DO NOT EXCLUDE. `campaign_router._apply_filters` mixes the
# two: it takes an org id, narrows by tier and year, and then also drops DNC and
# flagged leads. That is right for building a send list and wrong for answering
# "who is qualified", because a lead filtered out by the query can never be
# counted or explained - the admin sees 3,420 selected with no idea that 960
# people were removed before the question was asked.
#
# So the exclusions live in `qualify_one` where they become reported buckets,
# and this only narrows the population the admin asked about. It also takes NO
# organization id: the query it receives is already authorized, and re-applying
# a tenant filter here would create a second place where tenancy is decided.
def apply_selection_filters(query, criteria: Optional[Dict[str, Any]]):
    criteria = criteria or {}

    simple = {
        "tier": Lead.tier,
        "status": Lead.status,
        "engagement_temperature": Lead.engagement_temperature,
        "relationship_type": Lead.relationship_type,
        "source_category": Lead.source_category,
        "import_list_name": Lead.import_list_name,
        "contact_channel": Lead.contact_channel,
        "case_status": Lead.case_status,
    }
    for key, column in simple.items():
        if criteria.get(key):
            query = query.filter(column == criteria[key])

    track = criteria.get("message_track") or criteria.get("lead_type")
    if track:
        query = query.filter(Lead.message_track == track)

    if criteria.get("source_year"):
        query = query.filter(Lead.source_year == int(criteria["source_year"]))
    if criteria.get("source_year_min"):
        query = query.filter(Lead.source_year >= int(criteria["source_year_min"]))
    if criteria.get("source_year_max"):
        query = query.filter(Lead.source_year <= int(criteria["source_year_max"]))

    if criteria.get("source_file"):
        query = query.filter(Lead.source_file.ilike("%%%s%%" % criteria["source_file"]))

    # An advisor filter NARROWS. It cannot widen: the query already carries
    # `assigned_to_id == me` for an owner-scoped caller, so a manager may narrow
    # to one advisor and an advisor naming somebody else gets an empty set
    # rather than that person's book.
    if criteria.get("advisor_id"):
        query = query.filter(Lead.assigned_to_id == criteria["advisor_id"])

    return query


def org_rules(db: Session, organization_id: Optional[str]) -> List[Any]:
    """Active rules for one organization. Empty for an organization that has
    defined none, which is the correct answer and the common one - the engine
    is fully useful with zero rules, and rules only add organization meaning."""
    if not organization_id:
        return []
    try:
        from app.models.qualification_models import QualificationRule
        return (db.query(QualificationRule)
                .filter(QualificationRule.organization_id == organization_id,
                        QualificationRule.is_active.is_(True))
                .order_by(QualificationRule.sort_order.asc()).all())
    except Exception:
        # A missing table on a database that has not migrated yet must not take
        # qualification down - it just means this organization has no rules.
        _log.warning("qualification rules unavailable for org %s", organization_id,
                     exc_info=True)
        return []


# ── THE ENTRY POINT ─────────────────────────────────────────────────────────

def qualify_leads(db: Session, current_user: User, *, channel: str = CHANNEL_EMAIL,
                  filters: Optional[Dict[str, Any]] = None,
                  lead_ids: Optional[Sequence[str]] = None,
                  request=None, include_leads: bool = False,
                  limit: int = MAX_QUALIFY_ROWS) -> Dict[str, Any]:
    """Qualify the caller's AUTHORIZED leads for one channel.

    The first line is the whole security argument: the population comes from
    `authorized_lead_query`, so an advisor is scored against their own book, a
    manager against the workspace they are standing in, and nobody against
    anything else. Every filter after that intersects. There is no argument to
    this function that can widen the set - `lead_ids` is checked against the
    same authorized scope and refuses the entire batch if any id falls outside
    it, rather than quietly dropping the ones it did not like.
    """
    channel = (channel or CHANNEL_EMAIL).lower()
    if channel not in CHANNELS:
        from fastapi import HTTPException
        raise HTTPException(status_code=400,
                            detail="Unknown channel: %s" % channel)

    query = authorized_lead_query(db, current_user, request=request)

    if lead_ids is not None:
        # REFUSES THE WHOLE BATCH rather than silently narrowing it. A bulk
        # send that skipped the ids you were not allowed to touch would report
        # success for a job it did not do.
        assert_leads_in_scope(db, current_user, list(lead_ids), request=request)
        query = query.filter(Lead.id.in_(list(lead_ids)))

    query = apply_selection_filters(query, filters)
    leads = query.limit(limit).all()

    organization_id = lead_scope.active_workspace_org_id(current_user, db, request)
    ctx = QualificationContext(db, leads, organization_id,
                               org_rules(db, organization_id))
    if channel in (CHANNEL_SMS, CHANNEL_VOICE) and organization_id:
        ctx.suppressed_phones = load_suppressed_phones(db, organization_id)

    decisions = [qualify_one(lead, channel, ctx) for lead in leads]
    return summarize(decisions, channel, include_leads=include_leads)


def summarize(decisions: Sequence[Dict[str, Any]], channel: str,
              include_leads: bool = False) -> Dict[str, Any]:
    """The shape the preview screen renders and the report prints."""
    buckets = {READY: 0, REVIEW: 0, EXCLUDED: 0}
    priority = {HIGH: 0, MEDIUM: 0, LOW: 0}
    exclusion_counts: Dict[str, Dict[str, Any]] = {}
    review_counts: Dict[str, Dict[str, Any]] = {}
    factor_counts: Dict[str, Dict[str, Any]] = {}

    def bump(store, r):
        row = store.setdefault(r["code"], {"code": r["code"],
                                           "label": REASONS.get(r["code"], r["code"]),
                                           "kind": FACTOR_KINDS.get(r["code"]),
                                           "count": 0})
        row["count"] += 1

    for d in decisions:
        buckets[d["bucket"]] = buckets.get(d["bucket"], 0) + 1
        if d["bucket"] == EXCLUDED:
            for r in d["reasons"]:
                bump(exclusion_counts, r)
        elif d["bucket"] == REVIEW:
            for r in d["reasons"]:
                bump(review_counts, r)
        if d["priority"]:
            priority[d["priority"]] = priority.get(d["priority"], 0) + 1
        for f in d["factors"]:
            bump(factor_counts, f)

    def ranked(store):
        return sorted(store.values(), key=lambda r: (-r["count"], r["code"]))

    out = {
        "channel": channel,
        "authoritative": channel in AUTHORITATIVE_CHANNELS,
        "total_selected": len(decisions),
        "ready": buckets[READY],
        "review": buckets[REVIEW],
        "excluded": buckets[EXCLUDED],
        "buckets": buckets,
        "priority": priority,
        "exclusion_reasons": ranked(exclusion_counts),
        "review_reasons": ranked(review_counts),
        "priority_factors": ranked(factor_counts),
    }
    if include_leads:
        out["leads"] = list(decisions)
    return out


# ── THE SEND GATE ───────────────────────────────────────────────────────────
#
# Everything above is a report. This is the part that makes it a control.
#
# "Only READY leads may enter the actual send queue unless an authorized user
#  deliberately resolves a REVIEW item."   - Mike
#
# So REVIEW_REQUIRED does not silently become READY because somebody clicked
# Select All. Resolving a review item is a deliberate act on that lead - fixing
# the address, classifying it, confirming the shared mailbox is right - and it
# changes the lead, which changes the answer. It is not a checkbox on the send
# screen, because a checkbox that waives review is just a slower way of not
# having review.

class NotQualified(Exception):
    """Raised with the per-lead reasons, so a refusal can be explained."""

    def __init__(self, blocked: List[Dict[str, Any]], channel: str):
        self.blocked = blocked
        self.channel = channel
        super().__init__("%d lead(s) are not qualified for %s" % (len(blocked), channel))


def qualify_for_send(db: Session, current_user: User, lead_ids: Sequence[str], *,
                     channel: str = CHANNEL_EMAIL, request=None) -> Dict[str, Any]:
    """Qualify an explicit batch and split it into sendable and refused.

    Authorization first, and it REFUSES rather than narrows: an id outside the
    caller's scope aborts the whole call inside `qualify_leads`. Bulk and
    select-all reach this function with the same ids a single send would, so
    there is no path where selecting more leads relaxes the rule.
    """
    result = qualify_leads(db, current_user, channel=channel, lead_ids=lead_ids,
                           request=request, include_leads=True)
    ready, blocked = [], []
    for d in result.get("leads", []):
        (ready if d["bucket"] == READY else blocked).append(d)
    result["ready_lead_ids"] = [d["lead_id"] for d in ready]
    result["blocked"] = blocked
    return result


def assert_ready_for_send(db: Session, current_user: User, lead_ids: Sequence[str], *,
                          channel: str = CHANNEL_EMAIL, request=None) -> List[str]:
    """Every named lead is READY, or nothing is sent. Returns the ready ids.

    ALL-OR-NOTHING, deliberately, and it is the same reasoning as
    `assert_leads_in_scope`: a batch that sent to the qualified half and said
    nothing about the rest is a job that reports success for work it did not
    do, and the leads it dropped are invisible. The caller gets the reasons and
    can re-submit the ids it meant.
    """
    result = qualify_for_send(db, current_user, lead_ids, channel=channel, request=request)
    if result["blocked"]:
        raise NotQualified(result["blocked"], channel)
    return result["ready_lead_ids"]


def send_refusal_detail(exc: NotQualified) -> Dict[str, Any]:
    """The body of a 400 for a refused send - counts, and WHY, per lead.

    A refusal that says only "some leads are not qualified" sends the person
    back to a list of five thousand to find out which. This names them.
    """
    by_reason: Dict[str, Dict[str, Any]] = {}
    for d in exc.blocked:
        for r in d["reasons"]:
            row = by_reason.setdefault(r["code"], {"code": r["code"],
                                                   "label": REASONS.get(r["code"], r["code"]),
                                                   "count": 0, "lead_ids": []})
            row["count"] += 1
            if len(row["lead_ids"]) < 25:
                row["lead_ids"].append(d["lead_id"])
    return {
        "message": ("%d of the selected leads are not qualified for %s and were "
                    "not sent. Nothing was sent." % (len(exc.blocked), exc.channel)),
        "channel": exc.channel,
        "blocked_count": len(exc.blocked),
        "reasons": sorted(by_reason.values(), key=lambda r: (-r["count"], r["code"])),
    }
