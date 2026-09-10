"""
Launch Engine delivery — what the IMPLEMENTATION TEAM does after the customer
has answered, and the gate that stands between all of it and Live.

===========================================================================
WHY THESE FIVE TABLES AND NOT MORE MILESTONES
===========================================================================

`ImplementationMilestone` already exists and is the right shape for "a step of
the build, done or not". It was tempting to express integrations, UAT and
training as milestones with a naming convention — `integration:<partner>`,
`uat:<thing that must work>` — and that is exactly the trap. Each of those
three carries facts a milestone has nowhere to put:

    an integration      has a partner, a credential state, and a VERIFIED
                        moment that is not the same as "somebody ticked it"
    a UAT check         has two separate approvals: ours (it works) and the
                        customer's (they agree it works). One boolean cannot
                        hold two people's opinions.
    a training session  has attendees, a date, an owner, and an acknowledgement
                        from the customer that they were trained.

Encoding those in a key string and a notes field is how a checklist becomes a
spreadsheet with extra steps. So each gets a table with its own vocabulary, and
`ImplementationMilestone` keeps doing the job it already does — the build
checklist — without being asked to be four things.

    ImplementationMilestone      the build checklist        (exists — reused)
    ImplementationIntegration    connections to partners    (new)
    ImplementationCheck          UAT / validation           (new)
    ImplementationTraining       training sessions          (new)
    ImplementationBlocker        what is actually stuck     (new)
    ImplementationApproval       the two go-live signatures (new)
    LaunchTemplate               per-brand configuration    (new)

===========================================================================
WHY A BLOCKER TABLE WHEN Implementation.blocker_note EXISTS
===========================================================================

`Implementation.blocker_note` is one string and it is cleared the moment the
status leaves `blocked`. That is right for "this project is currently halted"
and useless for "we are waiting on three different things, two of them from
different people, and one has been open for a month". A blocker has an owner,
an age and a resolution, and none of those survive in a column that gets
nulled. The column stays exactly as it is — the headline — and these rows are
the list underneath it.

===========================================================================
WHY organization_id IS ON EVERY ROW
===========================================================================

Same reason as `launch_intake_models`, and it is not repetition for its own
sake: every read and write here filters on it, so tenant isolation is visible
in the query rather than inferred from the join path. It is written from the
implementation, never from a request body, so no caller can move a row into
another tenant by naming one.

===========================================================================
WHY LaunchTemplate IS A TABLE AND NOT A dict IN CODE
===========================================================================

The engine is white-label. A brand must be able to say "our customers do not
need a data migration, they do need two integrations verified, and we do not
require a customer signature before go-live" without a deploy. A dict in a
module means every such answer is a code change, which means one brand's
onboarding requirements live in a file that another brand's engineer edits.

The code still holds a DEFAULT (services/launch_template.py) so a brand with
no row configured gets a sensible programme rather than an empty one. The row
overrides; the default is the floor. Nothing in either names a brand.
"""

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.models.models import Base, gen_uuid


# ── integrations ────────────────────────────────────────────────────────────
#
# The vocabulary is the lifecycle of a connection, and every value is a
# statement somebody can defend. `connected` means the credentials went in and
# something answered; `verified` means a human watched real data move. The gap
# between those two words is where most "it's connected" launch failures live,
# so they are not the same status.

INT_REQUIRED = "required"
INT_CREDENTIALS_RECEIVED = "credentials_received"
INT_CONFIGURING = "configuring"
INT_CONNECTED = "connected"
INT_TESTING = "testing"
INT_VERIFIED = "verified"
INT_BLOCKED = "blocked"
INT_NOT_APPLICABLE = "not_applicable"

INTEGRATION_STATUSES = (
    INT_REQUIRED, INT_CREDENTIALS_RECEIVED, INT_CONFIGURING, INT_CONNECTED,
    INT_TESTING, INT_VERIFIED, INT_BLOCKED, INT_NOT_APPLICABLE,
)

INTEGRATION_STATUS_LABELS = {
    INT_REQUIRED: "Required",
    INT_CREDENTIALS_RECEIVED: "Credentials received",
    INT_CONFIGURING: "Configuring",
    INT_CONNECTED: "Connected",
    INT_TESTING: "Testing",
    INT_VERIFIED: "Verified",
    INT_BLOCKED: "Blocked",
    INT_NOT_APPLICABLE: "Not applicable",
}

# Settled means "nothing further is owed here". `not_applicable` counts for the
# same reason `skipped` counts on a milestone: a connection this customer does
# not use is not outstanding work.
INTEGRATION_SETTLED = (INT_VERIFIED, INT_NOT_APPLICABLE)


class ImplementationIntegration(Base):
    """One partner connection for one customer, tracked from asked to proven.

    THIS TABLE DOES NOT CONNECT ANYTHING. It is the implementation team's
    record of where a connection stands. The actual provider work stays in the
    integration/service layer where it already lives — `integration_models`,
    `integration_auth`, the calendar and comms services. A row here pointing at
    a provider key is a note about a job, not a second implementation of it.
    """

    __tablename__ = "implementation_integrations"

    id = Column(String, primary_key=True, default=gen_uuid)

    implementation_id = Column(
        String, ForeignKey("implementations.id", ondelete="CASCADE"),
        nullable=False, index=True)
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=False, index=True)

    key = Column(String(64), nullable=False)
    label = Column(String(160), nullable=False)
    # Free text on purpose: the partner is whoever this customer works with,
    # and a select list of providers would be a list somebody has to maintain
    # for every brand's market.
    provider = Column(String(160), nullable=True)

    status = Column(String(32), default=INT_REQUIRED, nullable=False)
    is_required = Column(Boolean, default=True, nullable=False)
    position = Column(Integer, default=0, nullable=False)

    # WHOSE MOVE IT IS. Separate from status because a connection can be
    # `configuring` while the thing holding it up is a credential the customer
    # has not sent. Status says where it is; this says who to chase.
    owner_party = Column(String(16), default="provider", nullable=False)
    owner_user_id = Column(String, ForeignKey("users.id"), nullable=True)

    # Internal. Never shown to the customer — see the customer projection in
    # services/launch_delivery.py, which reads label and status and nothing else.
    notes = Column(Text, nullable=True)

    verified_at = Column(DateTime, nullable=True)
    verified_by = Column(String, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("implementation_id", "key", name="uq_impl_integration_key"),
        Index("ix_impl_integration_org", "organization_id", "implementation_id"),
    )


# ── UAT / validation ────────────────────────────────────────────────────────

CHECK_NOT_TESTED = "not_tested"
CHECK_PASS = "pass"
CHECK_FAIL = "fail"
CHECK_RETEST = "retest"

CHECK_STATUSES = (CHECK_NOT_TESTED, CHECK_PASS, CHECK_FAIL, CHECK_RETEST)

CHECK_STATUS_LABELS = {
    CHECK_NOT_TESTED: "Not tested",
    CHECK_PASS: "Pass",
    CHECK_FAIL: "Fail",
    CHECK_RETEST: "Retest required",
}


class ImplementationCheck(Base):
    """One thing that has to work before this customer goes live.

    TWO APPROVALS, DELIBERATELY NOT ONE.

        status / tested_by          WE tested it and it works
        customer_approved_at        THEY looked at it and agree

    Collapsing those into a single "done" is how a launch ends with the
    implementation team certain everything passed and the customer certain
    nobody showed them anything. They are different people making different
    statements, and the go-live gate can require either, both, or neither
    depending on the brand's template.
    """

    __tablename__ = "implementation_checks"

    id = Column(String, primary_key=True, default=gen_uuid)

    implementation_id = Column(
        String, ForeignKey("implementations.id", ondelete="CASCADE"),
        nullable=False, index=True)
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=False, index=True)

    key = Column(String(64), nullable=False)
    label = Column(String(200), nullable=False)
    # A grouping for the screen, not a rule. "Website", "Data", "Access".
    category = Column(String(48), nullable=True)
    description = Column(Text, nullable=True)

    status = Column(String(16), default=CHECK_NOT_TESTED, nullable=False)
    is_required = Column(Boolean, default=True, nullable=False)
    position = Column(Integer, default=0, nullable=False)

    tested_at = Column(DateTime, nullable=True)
    tested_by = Column(String, ForeignKey("users.id"), nullable=True)
    notes = Column(Text, nullable=True)

    customer_approved_at = Column(DateTime, nullable=True)
    customer_approved_by = Column(String, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("implementation_id", "key", name="uq_impl_check_key"),
        Index("ix_impl_check_org", "organization_id", "implementation_id"),
    )


# ── training ────────────────────────────────────────────────────────────────

class ImplementationTraining(Base):
    """A training session this customer needs before or around go-live.

    DELIBERATELY NOT A LEARNING MANAGEMENT SYSTEM. There is no curriculum here,
    no lesson content, no completion score, no seat licence. Four facts: what
    the session is, when it is, who came, and whether the customer says it
    happened. If a platform-level training capability lands later, this table
    is what it fills in — `external_ref` is the seam — rather than something it
    has to compete with.
    """

    __tablename__ = "implementation_training"

    id = Column(String, primary_key=True, default=gen_uuid)

    implementation_id = Column(
        String, ForeignKey("implementations.id", ondelete="CASCADE"),
        nullable=False, index=True)
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=False, index=True)

    key = Column(String(64), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)

    is_required = Column(Boolean, default=True, nullable=False)
    position = Column(Integer, default=0, nullable=False)

    owner_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    scheduled_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    completed_by = Column(String, ForeignKey("users.id"), nullable=True)

    # [{"name": ..., "email": ..., "role": ...}] — free shape on purpose. The
    # people who attend a customer's training are the customer's people, and
    # they do not all have platform accounts.
    attendees = Column(JSON, nullable=True)

    customer_acknowledged_at = Column(DateTime, nullable=True)
    customer_acknowledged_by = Column(String, ForeignKey("users.id"), nullable=True)

    notes = Column(Text, nullable=True)
    # Where a future platform training record lives, if one ever does.
    external_ref = Column(String(200), nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("implementation_id", "key", name="uq_impl_training_key"),
        Index("ix_impl_training_org", "organization_id", "implementation_id"),
    )


# ── blockers ────────────────────────────────────────────────────────────────
#
# The party is the question everybody actually asks about a stuck project:
# whose move is it. It is not a status and not an owner — it is the SIDE the
# next action sits on, which is what makes "waiting on customer" countable.

PARTY_CUSTOMER = "customer"
PARTY_PROVIDER = "provider"
PARTY_PARTNER = "partner"
PARTY_TECHNICAL = "technical"
PARTY_APPROVAL = "approval"

BLOCKER_PARTIES = (PARTY_CUSTOMER, PARTY_PROVIDER, PARTY_PARTNER,
                   PARTY_TECHNICAL, PARTY_APPROVAL)

BLOCKER_PARTY_LABELS = {
    PARTY_CUSTOMER: "Waiting on customer",
    PARTY_PROVIDER: "Waiting on us",
    PARTY_PARTNER: "Waiting on integration partner",
    PARTY_TECHNICAL: "Technical blocker",
    PARTY_APPROVAL: "Approval required",
}

BLOCKER_OPEN = "open"
BLOCKER_RESOLVED = "resolved"


class ImplementationBlocker(Base):
    """One thing standing in the way, with an owner and an age.

    `customer_visible` DEFAULTS TO FALSE, and that default is the point. Most
    blockers are internal — a partner has not answered, a colleague is on
    leave, a decision is pending — and publishing them all to the customer
    turns a status page into a complaints log. A blocker the customer must act
    on is marked visible deliberately, by the person who wrote it, and the
    customer projection reads nothing else.
    """

    __tablename__ = "implementation_blockers"

    id = Column(String, primary_key=True, default=gen_uuid)

    implementation_id = Column(
        String, ForeignKey("implementations.id", ondelete="CASCADE"),
        nullable=False, index=True)
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=False, index=True)

    title = Column(String(200), nullable=False)
    detail = Column(Text, nullable=True)

    party = Column(String(16), default=PARTY_PROVIDER, nullable=False)
    owner_user_id = Column(String, ForeignKey("users.id"), nullable=True)

    status = Column(String(16), default=BLOCKER_OPEN, nullable=False, index=True)
    customer_visible = Column(Boolean, default=False, nullable=False)

    # What the customer is asked to do about it, in their words rather than
    # ours. Only rendered when customer_visible is true.
    customer_action = Column(Text, nullable=True)

    opened_at = Column(DateTime, server_default=func.now(), nullable=False)
    opened_by = Column(String, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String, ForeignKey("users.id"), nullable=True)
    resolution = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_impl_blocker_open", "implementation_id", "status"),
        Index("ix_impl_blocker_org", "organization_id", "status"),
    )


# ── go-live approvals ───────────────────────────────────────────────────────

APPROVAL_CUSTOMER = "customer_signoff"
APPROVAL_PROVIDER = "provider_signoff"

APPROVAL_KINDS = (APPROVAL_CUSTOMER, APPROVAL_PROVIDER)

APPROVAL_LABELS = {
    APPROVAL_CUSTOMER: "Customer approval to go live",
    APPROVAL_PROVIDER: "Implementation team approval to go live",
}


class ImplementationApproval(Base):
    """A named signature on the go-live decision.

    ONE ROW PER KIND, enforced by the unique constraint, because a standing
    approval is a current fact rather than a history: withdrawing one deletes
    the row, and the audit log — which every write here also writes to — is
    where the history of giving and withdrawing lives. Two tables both claiming
    to hold "is this approved" is the drift this whole engine avoids.
    """

    __tablename__ = "implementation_approvals"

    id = Column(String, primary_key=True, default=gen_uuid)

    implementation_id = Column(
        String, ForeignKey("implementations.id", ondelete="CASCADE"),
        nullable=False, index=True)
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=False, index=True)

    kind = Column(String(32), nullable=False)
    given_at = Column(DateTime, server_default=func.now(), nullable=False)
    given_by = Column(String, ForeignKey("users.id"), nullable=True)
    # The name typed at sign-off, kept because the account that clicked and the
    # person who authorised are not always the same human.
    given_name = Column(String(160), nullable=True)
    note = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("implementation_id", "kind", name="uq_impl_approval_kind"),
        Index("ix_impl_approval_org", "organization_id", "kind"),
    )


# ── per-brand configuration ─────────────────────────────────────────────────

class LaunchTemplate(Base):
    """What one white-label brand's launch programme consists of.

    ONE ROW PER PLATFORM. `config` holds the whole programme as JSON rather
    than as five child tables, because the template is edited as a document —
    somebody decides what their onboarding looks like and saves it — and
    because a brand's programme has no independent identity outside the brand.
    The rows it produces (integrations, checks, training) ARE relational, and
    they are the ones that get queried.

    Shape (every key optional; services/launch_template.py holds the default):

        {
          "milestones":   [{key, label, description, required}],
          "integrations": [{key, label, provider, required}],
          "checks":       [{key, label, category, required}],
          "training":     [{key, title, description, required}],
          "golive": {
            "intake_submitted": true, "intake_reviewed": false,
            "files_received": true,  "access_received": true,
            "milestones_complete": true, "integrations_verified": true,
            "uat_complete": true, "customer_uat_approval": false,
            "training_complete": true, "no_open_blockers": true,
            "customer_signoff": true, "provider_signoff": true
          }
        }
    """

    __tablename__ = "launch_templates"

    id = Column(String, primary_key=True, default=gen_uuid)
    platform_id = Column(String, ForeignKey("platforms.id"),
                         nullable=False, unique=True, index=True)
    name = Column(String(160), nullable=True)
    config = Column(JSON, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(),
                        onupdate=func.now(), nullable=False)
    updated_by = Column(String, ForeignKey("users.id"), nullable=True)
