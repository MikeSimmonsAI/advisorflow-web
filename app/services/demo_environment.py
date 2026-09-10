"""BUILDING, SEEDING AND RESETTING A BRAND'S DEMONSTRATION ENVIRONMENT.

WHAT A DEMO ENVIRONMENT IS

    Platform (EvoSys Pro)                     the brand, unchanged, real
      ├── Organization  is_demo=True          the demo CUSTOMER workspace
      └── BrandSalesOrg is_demo=True          the demo SALES organization

Two ordinary tenants, flagged. Every query in the product already scopes by
`organization_id` or `brand_sales_org_id`, so demo records are invisible to a
real customer for exactly the reason one customer's records are invisible to
another. Nothing new enforces the isolation because nothing new needs to.

THE ENVIRONMENT SITS UNDER THE REAL PLATFORM, ON PURPOSE

The alternative — a separate demo Platform per brand — would have been more
isolated and worse. A demo is a demonstration of THIS brand: its name, its
colours, its domain, its packages, its pricing. Copying those onto a shadow
platform means maintaining a copy that drifts, and the first time it drifts the
prospect is shown last quarter's price list. Sitting under the real platform,
the demo wears the brand's real configuration because it IS the brand's
configuration.

The cost of that choice is that demo tenants would otherwise appear in
platform-wide enumerations — an executive's portfolio, the customer count on
the Command Center. Those places exclude `is_demo` explicitly. That exclusion
is not a workaround: real reporting must never have to filter demo rows out of
its own numbers, which is the same rule the APP_ENV=demo box states about its
event log.

EVERY SEEDED ROW HAS A DETERMINISTIC, PREFIXED ID

    ds-<environment id>-<what it is>

Two consequences, both of which the reset depends on. Re-seeding is idempotent
because the same seed produces the same primary keys, so a duplicate is a
collision rather than a second silent copy. And reset deletes rows matching
BOTH the id prefix AND the demo tenant — either condition alone would be a
sweep; together they are a provable extent.

TIME IS SEEDED, NOT SIMULATED

There is no demo clock. "The first message went out six days ago" is a
timestamp six days in the past. Threading a fake clock through every service
that calls `datetime.utcnow()` would need dozens of call sites to agree, and
the one that was missed would produce an inconsistent record mid-presentation
with no error. The presenter advances the STORY, which is what they narrate
anyway.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.models.demo_suite_models import (DEMO_SUITE_ID_PREFIX, ENV_BUILDING,
                                          ENV_EMPTY, ENV_ERROR, ENV_READY,
                                          DemoActionEvent, DemoEnvironment,
                                          DemoSession)
from app.models.models import (BookingLink, EmailMessage, EngagementTemperature,
                               Lead, Message, Organization, Platform, User)
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, SCOPE_CUSTOMER_ORG,
                                     STAGE_CLOSING, STAGE_CONTACTED,
                                     STAGE_DISCOVERY, STAGE_LIVE,
                                     STAGE_PROPOSAL, STAGE_PROSPECT,
                                     BrandSalesOrg, DiscoveryRecord,
                                     Membership, Opportunity,
                                     OpportunityEvent)
from app.services import demo_guard

log = logging.getLogger(__name__)

# Bumped when the SEED CONTENT changes in code. An environment built at an
# older version is offered a rebuild rather than silently presenting a story
# the guided scenarios no longer describe.
SEED_VERSION = 1


# ─────────────────────────────────────────────────────────────────────────────
# SAFE SYNTHETIC CONTACT DETAILS
#
# Every phone number comes from the 555-01xx block, reserved for fiction and
# unable to connect to a real person. Every email address uses a reserved
# example / invalid domain (RFC 2606), undeliverable by design.
#
# THIS MATTERS EVEN THOUGH THE SEND PATHS ALREADY REFUSE A DEMO TENANT. The
# refusal stops THIS PROCESS from sending. It does nothing about a number
# exported to a spreadsheet, pasted into a real console, or read aloud in a
# room. A demo record has to be harmless on its own, not merely harmless in
# context.
# ─────────────────────────────────────────────────────────────────────────────

def demo_phone(n: int) -> str:
    return "+1555010%04d" % (n % 10000)


def demo_email(local: str) -> str:
    return "%s@example.com" % local.strip().lower().replace(" ", ".")


def staff_email(platform_slug: str, local: str) -> str:
    """A demo STAFF login. `.invalid` is reserved and can never be delivered to.

    Distinct from `demo_email` (which is a prospect's address, on
    `example.com`) so that a glance at the users list says which fictional
    people are staff and which are contacts.
    """
    return "%s@%s.demo.invalid" % (local.strip().lower().replace(" ", "."),
                                   (platform_slug or "brand").strip().lower())


def _unknowable_password() -> str:
    """A password nobody knows, including this process a line later.

    Demo staff identities exist so that records have owners with names on
    screen. They are not accounts anybody signs in as; the presenter signs in
    as themselves. Giving them a knowable password would create real logins
    with no owner, which is the one thing worse than no login at all.
    """
    return secrets.token_urlsafe(48)


# ─────────────────────────────────────────────────────────────────────────────
# IDS
# ─────────────────────────────────────────────────────────────────────────────

def env_prefix(env: DemoEnvironment) -> str:
    return "%s%s-" % (DEMO_SUITE_ID_PREFIX, env.id)


def canonical_id(env: DemoEnvironment, *parts) -> str:
    """The ONLY way this module mints an id for a seeded record."""
    tail = "-".join(str(p).strip().lower().replace(" ", "-")
                    for p in parts if p is not None)
    return "%s%s" % (env_prefix(env), tail)


# ─────────────────────────────────────────────────────────────────────────────
# RESOLUTION
# ─────────────────────────────────────────────────────────────────────────────

def get_environment(db: Session, platform_id: str) -> Optional[DemoEnvironment]:
    if not platform_id:
        return None
    return (db.query(DemoEnvironment)
            .filter(DemoEnvironment.platform_id == platform_id).first())


def ensure_environment(db: Session, platform_id: str,
                       commit: bool = True) -> DemoEnvironment:
    """The environment ROW, created if absent. Does not seed anything.

    Separated from `build` because a God screen needs to be able to say "this
    brand has no demonstration environment yet" and offer to make one, and
    because the row's id is what every seeded record's id is derived from —
    so it has to exist before the first record does.
    """
    env = get_environment(db, platform_id)
    if env is not None:
        return env
    plat = db.query(Platform).filter(Platform.id == platform_id).first()
    if plat is None:
        raise HTTPException(status_code=404, detail="No such brand.")
    env = DemoEnvironment(platform_id=platform_id, status=ENV_EMPTY,
                          seed_version=0)
    db.add(env)
    if commit:
        db.commit()
        db.refresh(env)
    else:
        db.flush()
    return env


def resolve_tenants(db: Session,
                    env: DemoEnvironment) -> Tuple[Organization, BrandSalesOrg]:
    """The environment's two tenants, each proved to be a demo tenant.

    THE GUARD RUNS ON EVERY RESOLUTION, not once at build time. A row that
    named a real organization — through a bad migration, a hand-edited id, or
    a bug in a future build path — must not be usable, and the only way to
    guarantee that is to check at the moment of use.
    """
    org = (db.query(Organization)
           .filter(Organization.id == env.organization_id).first()
           if env.organization_id else None)
    brand = (db.query(BrandSalesOrg)
             .filter(BrandSalesOrg.id == env.brand_sales_org_id).first()
             if env.brand_sales_org_id else None)
    demo_guard.assert_demo_org(org)
    demo_guard.assert_demo_brand(brand)
    return org, brand


def require_ready(db: Session, env: Optional[DemoEnvironment]
                  ) -> Tuple[DemoEnvironment, Organization, BrandSalesOrg]:
    """The environment, ready, with both tenants resolved and proved."""
    if env is None or not env.is_ready():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This brand's demonstration environment has not been built "
                   "yet. An operator can build it from God Mode → Demo Suite.")
    org, brand = resolve_tenants(db, env)
    return env, org, brand


# ─────────────────────────────────────────────────────────────────────────────
# RESET — precise, by tenant AND by prefix
# ─────────────────────────────────────────────────────────────────────────────

# (table, id-prefixed?, scoping SQL fragment, params-builder key)
#
# Child before parent. Every statement carries BOTH conditions where the table
# has an owning tenant column, and the id prefix alone only where the row's
# parent has already been deleted above it in the same pass.
def _reset_statements(env: DemoEnvironment, org_id: str, brand_id: str
                      ) -> List[Tuple[str, Dict[str, Any]]]:
    p = env_prefix(env) + "%"
    lead_scope = ("SELECT id FROM leads WHERE organization_id = :org "
                  "AND id LIKE :p")
    opp_scope = ("SELECT id FROM opportunities WHERE brand_sales_org_id = :b "
                 "AND id LIKE :p")
    common = {"org": org_id, "b": brand_id, "p": p}
    return [
        ("DELETE FROM messages WHERE lead_id IN (%s)" % lead_scope, common),
        ("DELETE FROM email_messages WHERE lead_id IN (%s)" % lead_scope, common),
        ("DELETE FROM booking_links WHERE lead_id IN (%s)" % lead_scope, common),
        ("DELETE FROM leads WHERE organization_id = :org AND id LIKE :p", common),

        ("DELETE FROM opportunity_events WHERE opportunity_id IN (%s)" % opp_scope,
         common),
        ("DELETE FROM discovery_records WHERE opportunity_id IN (%s)" % opp_scope,
         common),
        ("DELETE FROM opportunities WHERE brand_sales_org_id = :b AND id LIKE :p",
         common),

        # Memberships and grants belong to the demo STAFF identities, which are
        # themselves prefixed. Scoping on the user id prefix is the tenant
        # condition here — a membership row has no tenant column of its own
        # that could be checked instead.
        ("DELETE FROM memberships WHERE user_id LIKE :p", {"p": p}),
        ("DELETE FROM user_capability_grants WHERE user_id LIKE :p", {"p": p}),
        ("DELETE FROM user_sessions WHERE user_id LIKE :p", {"p": p}),
        ("DELETE FROM users WHERE id LIKE :p", {"p": p}),

        ("DELETE FROM demo_action_events WHERE environment_id = :e",
         {"e": env.id}),
        ("DELETE FROM demo_sessions WHERE environment_id = :e", {"e": env.id}),
    ]


def reset(db: Session, env: DemoEnvironment, actor: Optional[User] = None,
          commit: bool = True) -> Dict[str, Any]:
    """Remove every seeded record. The two tenant rows themselves survive.

    THE TENANTS ARE KEPT DELIBERATELY. Their ids are referenced by the
    environment row, by any capability grant scoped to the brand, and by the
    audit trail of every build that has ever run. Deleting and recreating them
    would make "which organization was the demo in July" unanswerable, and
    would mean a rebuild changes the ids that a presenter's bookmarked URL
    contains.

    A table that does not exist in this database is skipped rather than
    fataling, which is what lets the same reset run against SQLite in the test
    suite and Postgres in production.
    """
    if env.organization_id is None or env.brand_sales_org_id is None:
        return {"deleted": {}, "note": "Environment has no tenants yet."}

    org, brand = resolve_tenants(db, env)

    deleted: Dict[str, int] = {}
    for sql, params in _reset_statements(env, org.id, brand.id):
        table = sql.split()[2]
        # EACH STATEMENT IN ITS OWN SAVEPOINT. A missing table must not undo
        # the work of the statements that already succeeded, and — because
        # `build` calls this inside its own transaction — must not roll the
        # freshly-created tenants back either. `db.rollback()` here would do
        # exactly that, which is why it is not used.
        try:
            with db.begin_nested():
                result = db.execute(sa_text(sql), params)
                deleted[table] = deleted.get(table, 0) + int(result.rowcount or 0)
        except Exception as e:                       # pragma: no cover
            log.warning("demo reset skipped %s: %s", table, e)
    env.status = ENV_EMPTY
    env.last_reset_at = datetime.utcnow()
    env.last_reset_by = getattr(actor, "id", None)
    env.last_error = None
    if commit:
        db.commit()
    else:
        db.flush()
    return {"deleted": deleted}


# ─────────────────────────────────────────────────────────────────────────────
# THE SEED CONTENT
#
# Believable fictional people running a believable fictional business. Not
# "Test User 1", not "ABC Company", not Lorem Ipsum — a prospect reads these
# names off the screen and every placeholder-shaped one costs the presenter a
# sentence of apology.
#
# The customer workspace is a services business with appointments and a book of
# older enquiries, because that is the shape both brands sell into. The sales
# organization is the brand's own back office. The two are deliberately
# unrelated: no foreign key joins a demo Lead to a demo Opportunity, because no
# such relationship exists in the product and inventing one for a demo would be
# demonstrating software that does not ship.
# ─────────────────────────────────────────────────────────────────────────────

DEMO_WORKSPACE_NAME = "Lakemont Family Services"

# (key, full name, role, title)
WORKSPACE_STAFF = [
    ("dana",   "Dana Whitfield",  "org_admin", "Client Services Director"),
    ("marcus", "Marcus Ayala",    "advisor",   "Family Service Advisor"),
    ("renee",  "Renee Okonkwo",   "advisor",   "Family Service Advisor"),
]

# (key, first, last, phone_n, tier, status, temperature, owner_key,
#  days_since_contact, source, note)
#
# `days_since_contact` is None for a lead nobody has touched yet — which is a
# different and much more interesting state than "touched a long time ago", and
# the two must be visibly different on screen.
WORKSPACE_LEADS = [
    ("terrence", "Terrence", "Blakely",  7104, "at_need",  "new",     "hot",   "marcus", None,
     "website-enquiry-form",
     "Submitted the website enquiry form. Nobody has responded yet — this is "
     "the speed-to-lead story."),
    ("gloria",   "Gloria",   "Petrakis", 7118, "imminent", "replied", "hot",   "marcus", 0,
     "inbound-call",
     "Replied asking to be called back this afternoon."),
    ("alice",    "Alice",    "Nakamura", 7126, "pre_need", "replied", "warm",  "renee",  1,
     "seminar-signup",
     "Asked what a pre-need arrangement costs."),
    ("nathan",   "Nathan",   "Brice",    7133, "pre_need", "sent",    "warm",  "renee",  2,
     "referral",
     "Two messages out, no reply yet."),
    ("priscilla", "Priscilla", "Vaughn", 7140, "pre_need", "booked",  "hot",   "marcus", 3,
     "website-enquiry-form",
     "Consultation booked for tomorrow morning."),
    ("devon",    "Devon",    "Carr",     7147, "pre_need", "sent",    "cold",  "renee",  9,
     "2024-mailing-list",
     "Three attempts, no answer."),
    ("yusuf",    "Yusuf",    "Demir",    7155, "pre_need", "new",     "cold",  None,     420,
     "2023-seminar-list",
     "Went quiet fourteen months ago. The reactivation story."),
    ("camille",  "Camille",  "Fontaine", 7162, "pre_need", "new",     "cold",  None,     512,
     "2023-seminar-list",
     "Went quiet, same list as Yusuf."),
    ("bianca",   "Bianca",   "Serrano",  7169, "pre_need", "new",     "warm",  None,     None,
     "facebook-lead-form",
     "Came in overnight from the social form."),
    ("sandra",   "Sandra",   "Ilves",    7176, "pre_need", "sent",    "warm",  "renee",  4,
     "email-list",
     "Email only — no phone consent on record."),
    ("owen",     "Owen",     "Radcliffe", 7183, "pre_need", "booked", "warm",  "marcus", 21,
     "referral",
     "Consultation completed three weeks ago."),
    ("harold",   "Harold",   "Mbeki",    7190, "pre_need", "dnc",     "cold",  None,     33,
     "2024-mailing-list",
     "Replied STOP. Present to show the compliance boundary is real."),
]

# (lead_key, days_ago, direction, body)
#   direction "out" — from the assigned advisor
#   direction "in"  — the family's reply, written as an inbound Message row
WORKSPACE_CONVERSATION = [
    ("gloria", 6, "out", "Hi Gloria, this is Marcus at Lakemont Family Services. "
                         "You asked about arrangements for your father — would a "
                         "short call this week help?"),
    ("gloria", 5, "in",  "Yes please. Afternoons are better for me."),
    ("gloria", 0, "out", "Understood — I have 2pm or 4pm tomorrow. Which suits?"),
    ("alice",  4, "out", "Hi Alice, thanks for coming to the planning session. "
                         "Happy to answer anything that came up."),
    ("alice",  1, "in",  "What does a pre-need plan usually cost?"),
    ("nathan", 5, "out", "Hi Nathan, Renee from Lakemont. Following up on the "
                         "referral from the Brices."),
    ("nathan", 2, "out", "Still happy to talk this through whenever suits you."),
    ("devon",  9, "out", "Hi Devon, following up on your enquiry."),
    ("priscilla", 3, "out", "Confirmed — see you Tuesday at 10am. I'll bring the "
                            "options we discussed."),
    ("owen",  21, "out", "Thanks for coming in, Owen. Everything is filed."),
]

# (lead_key, days_ago, subject, body)
WORKSPACE_EMAILS = [
    ("sandra", 4, "Your planning options",
     "<p>Hi Sandra,</p><p>Here are the three options we discussed, with the "
     "figures alongside each one.</p><p>— Renee</p>"),
    ("alice", 1, "Pre-need planning — what it costs",
     "<p>Hi Alice,</p><p>Attaching the summary you asked for.</p>"),
]

# (lead_key, days_from_now, label, minutes, status)
WORKSPACE_APPOINTMENTS = [
    ("priscilla", 1, "Pre-Need Planning Consultation", 60, "booked"),
    ("owen", -21, "Pre-Need Planning Consultation", 60, "confirmed"),
]

# ── the brand's own sales organization ───────────────────────────────────────

# (key, full name, role, title)
SALES_STAFF = [
    ("elena",  "Elena Vasquez", ROLE_SALES_MANAGER, "Sales Manager"),
    ("jordan", "Jordan Pike",   ROLE_SALES_REP,     "Account Executive"),
    ("aisha",  "Aisha Bello",   ROLE_SALES_REP,     "Account Executive"),
]

# (key, company, contact, phone_n, industry, stage, owner_key, value,
#  days_in_stage, next_action, next_action_in_days)
SALES_OPPORTUNITIES = [
    ("cordova", "Cordova Dental Partners", "Rafael Cordova", 7205,
     "Dental group, 4 locations", STAGE_DISCOVERY, "jordan", "2495.00", 3,
     "Send discovery summary and confirm the demo date", 1),
    ("halverson", "Halverson Roofing Co.", "Britt Halverson", 7212,
     "Residential roofing", STAGE_PROPOSAL, "jordan", "4995.00", 6,
     "Follow up on the proposal sent Monday", 0),
    ("sable", "Sable Creek Wellness", "Nadia Ferris", 7219,
     "Multi-site wellness clinics", STAGE_CLOSING, "aisha", "4995.00", 2,
     "Confirm the start date and send the agreement", 2),
    ("trellis", "Trellis Home Care", "Peter Nwosu", 7226,
     "In-home care agency", STAGE_PROSPECT, "aisha", "1497.00", 11,
     "First call — no contact attempted yet", -2),
    ("vantage", "Vantage Auto Group", "Simone Ledoux", 7233,
     "Auto dealership group", STAGE_CONTACTED, "jordan", "2495.00", 4,
     "Book the discovery call", 1),
    ("meridian", "Meridian Senior Living", "Grant Ozawa", 7240,
     "Senior living operator", STAGE_LIVE, "elena", "4995.00", 34,
     "Quarterly review", 14),
]

# (opportunity_key, days_ago, event_type, summary)
SALES_TIMELINE = [
    ("cordova", 12, "created", "Opportunity created from an inbound enquiry"),
    ("cordova", 8, "stage_changed", "Moved to Contacted / Qualified"),
    ("cordova", 3, "stage_changed", "Moved to Discovery"),
    ("cordova", 3, "discovery_completed", "Discovery completed with Rafael Cordova"),
    ("halverson", 26, "created", "Opportunity created from a referral"),
    ("halverson", 14, "stage_changed", "Moved to Discovery"),
    ("halverson", 9, "demo_ready", "Demo build marked ready"),
    ("halverson", 6, "stage_changed", "Moved to Demo / Proposal"),
    ("halverson", 5, "proposal_sent", "Proposal sent to Britt Halverson"),
    ("sable", 31, "created", "Opportunity created from a webinar registration"),
    ("sable", 12, "stage_changed", "Moved to Demo / Proposal"),
    ("sable", 2, "stage_changed", "Moved to Closing"),
    ("vantage", 9, "created", "Opportunity created from an outbound campaign"),
    ("vantage", 4, "stage_changed", "Moved to Contacted / Qualified"),
    ("trellis", 11, "created", "Opportunity created from a list import"),
    ("meridian", 120, "created", "Opportunity created"),
    ("meridian", 40, "stage_changed", "Moved to Won"),
    ("meridian", 34, "stage_changed", "Moved to Live / Completed"),
]


# ─────────────────────────────────────────────────────────────────────────────
# BUILD
# ─────────────────────────────────────────────────────────────────────────────

def _slug(platform: Platform, tail: str) -> str:
    return "demo-%s-%s" % ((platform.slug or platform.id)[:40], tail)


def _ensure_tenants(db: Session, env: DemoEnvironment,
                    platform: Platform) -> Tuple[Organization, BrandSalesOrg]:
    """Create the two demo tenants if they are not there, and prove them.

    Idempotent: a rebuild reuses the same two rows, so ids referenced by the
    environment, by capability grants and by anybody's bookmark survive it.
    """
    org = (db.query(Organization)
           .filter(Organization.id == env.organization_id).first()
           if env.organization_id else None)
    if org is None:
        org = Organization(
            id=canonical_id(env, "org"),
            name=DEMO_WORKSPACE_NAME,
            slug=_slug(platform, "workspace"),
            plan="standard",
            platform_id=platform.id,
            is_active=True,
            is_demo=True,
            industry="funeral",
            org_phone=demo_phone(7000),
            org_address="1400 Lakemont Parkway, Suite 200",
            support_email=demo_email("care.lakemont"),
            # NO TWILIO, NO RESEND, NO STRIPE. A demo tenant is given no
            # provider credentials at all, so even if every guard above it
            # were removed there would be nothing here to send with.
        )
        db.add(org)
        db.flush()
    else:
        # Repair, rather than trust. If anything ever flipped this off, the
        # environment must not be usable until it is a demo tenant again.
        org.is_demo = True
        org.is_active = True

    brand = (db.query(BrandSalesOrg)
             .filter(BrandSalesOrg.id == env.brand_sales_org_id).first()
             if env.brand_sales_org_id else None)
    if brand is None:
        brand = BrandSalesOrg(
            id=canonical_id(env, "bso"),
            platform_id=platform.id,
            name="%s Sales (Demonstration)" % (platform.name or "Brand"),
            slug=_slug(platform, "sales"),
            timezone="America/Chicago",
            is_active=True,
            is_demo=True,
        )
        db.add(brand)
        db.flush()
    else:
        brand.is_demo = True
        brand.is_active = True

    env.organization_id = org.id
    env.brand_sales_org_id = brand.id
    db.flush()
    demo_guard.assert_demo_org(org)
    demo_guard.assert_demo_brand(brand)
    return org, brand


def _seed_workspace(db: Session, env: DemoEnvironment, platform: Platform,
                    org: Organization, now: datetime) -> Dict[str, Any]:
    staff: Dict[str, User] = {}
    for key, name, role, title in WORKSPACE_STAFF:
        u = User(
            id=canonical_id(env, "user", key),
            organization_id=org.id,
            email=staff_email(platform.slug or "brand", "%s.demo" % key),
            full_name=name,
            password_hash="demo-identity-no-password-%s" % secrets.token_hex(8),
            role=role,
            is_active=True,
            must_change_password=False,
        )
        db.add(u)
        staff[key] = u
    db.flush()

    for key, u in staff.items():
        db.add(Membership(
            id=canonical_id(env, "mem", "ws", key),
            user_id=u.id, scope_type=SCOPE_CUSTOMER_ORG, scope_id=org.id,
            role=u.role if u.role in ("org_admin", "advisor") else "advisor",
            is_active=True))

    leads: Dict[str, Lead] = {}
    for (key, first, last, phone_n, tier, lead_status, temp, owner_key,
         days, source, note) in WORKSPACE_LEADS:
        lead = Lead(
            id=canonical_id(env, "lead", key),
            organization_id=org.id,
            assigned_to_id=staff[owner_key].id if owner_key else None,
            first_name=first, last_name=last,
            phone=demo_phone(phone_n),
            phone_raw="(555) 010-%04d" % (phone_n % 10000),
            email=demo_email("%s.%s" % (first, last)),
            tier=tier,
            status=lead_status,
            # THE SAEnum NAME TRAP. `engagement_temperature` is a
            # SAEnum(EngagementTemperature), and SQLAlchemy looks a value up by
            # the member's NAME, not by its `.value`. Passing the string "hot"
            # raises a LookupError at flush; the member is what must be passed.
            engagement_temperature=EngagementTemperature[temp.upper()],
            contact_channel="email_only" if key == "sandra" else "sms",
            source=source,
            source_file=source,
            last_contact_date=(now - timedelta(days=days)) if days is not None else None,
            ai_lead_quality_note=note,
        )
        db.add(lead)
        leads[key] = lead
    db.flush()

    messages = 0
    for lead_key, days_ago, direction, body in WORKSPACE_CONVERSATION:
        lead = leads[lead_key]
        sender = (db.query(User).filter(User.id == lead.assigned_to_id).first()
                  if lead.assigned_to_id else staff["marcus"])
        db.add(Message(
            id=canonical_id(env, "msg", lead_key, messages),
            lead_id=lead.id,
            sender_id=(sender or staff["marcus"]).id,
            body=("[FROM %s] %s" % (lead.first_name, body)
                  if direction == "in" else body),
            # SIMULATED, AND IT SAYS SO IN THE COLUMN THE PROVIDER'S OWN ID
            # WOULD OCCUPY. Anybody reading this row in a database shell can
            # see at a glance that no carrier ever saw it.
            twilio_sid="SIMULATED-DEMO-%s" % secrets.token_hex(6),
            twilio_status="delivered",
            delivery_status="delivered",
            send_state="delivered",
            sent_at=now - timedelta(days=days_ago),
        ))
        messages += 1
    db.flush()

    emails = 0
    for lead_key, days_ago, subject, body_html in WORKSPACE_EMAILS:
        lead = leads[lead_key]
        db.add(EmailMessage(
            id=canonical_id(env, "email", lead_key, emails),
            lead_id=lead.id,
            sender_id=(lead.assigned_to_id or staff["renee"].id),
            subject=subject,
            body_html=body_html,
            provider_message_id="SIMULATED-DEMO-%s" % secrets.token_hex(6),
            status="delivered",
            sent_at=now - timedelta(days=days_ago),
        ))
        emails += 1

    appointments = 0
    for lead_key, days_from_now, label, minutes, appt_status in WORKSPACE_APPOINTMENTS:
        lead = leads[lead_key]
        when = (now + timedelta(days=days_from_now)).replace(
            hour=10, minute=0, second=0, microsecond=0)
        db.add(BookingLink(
            id=canonical_id(env, "appt", lead_key),
            token=canonical_id(env, "token", lead_key),
            lead_id=lead.id,
            user_id=(lead.assigned_to_id or staff["marcus"].id),
            status=appt_status,
            booked_time=when,
            appt_label=label,
            appt_duration=minutes,
            expires_at=now + timedelta(days=14),
            confirmed_at=when if appt_status == "confirmed" else None,
        ))
        appointments += 1

    db.flush()
    return {"staff": len(staff), "leads": len(leads), "messages": messages,
            "emails": emails, "appointments": appointments}


def _seed_sales(db: Session, env: DemoEnvironment, platform: Platform,
                brand: BrandSalesOrg, now: datetime) -> Dict[str, Any]:
    from decimal import Decimal

    staff: Dict[str, User] = {}
    manager_id = None
    for key, name, role, title in SALES_STAFF:
        u = User(
            id=canonical_id(env, "user", "sales", key),
            # NULL BY POSITIVE ASSERTION. A brand-sales identity belongs to the
            # control plane and to no customer tenant — the same shape every
            # real salesperson has.
            organization_id=None,
            email=staff_email(platform.slug or "brand", "%s.sales.demo" % key),
            full_name=name,
            password_hash="demo-identity-no-password-%s" % secrets.token_hex(8),
            role="advisor",
            is_active=True,
            must_change_password=False,
        )
        db.add(u)
        staff[key] = u
    db.flush()
    manager_id = staff["elena"].id

    for key, name, role, title in SALES_STAFF:
        db.add(Membership(
            id=canonical_id(env, "mem", "sales", key),
            user_id=staff[key].id,
            scope_type=SCOPE_BRAND_SALES_ORG, scope_id=brand.id,
            role=role, is_active=True,
            reports_to_user_id=None if role == ROLE_SALES_MANAGER else manager_id))
    db.flush()

    opps: Dict[str, Opportunity] = {}
    for (key, company, contact, phone_n, industry, stage, owner_key, value,
         days_in_stage, next_action, next_in) in SALES_OPPORTUNITIES:
        opp = Opportunity(
            id=canonical_id(env, "opp", key),
            brand_sales_org_id=brand.id,
            owner_user_id=staff[owner_key].id,
            company_name=company,
            contact_name=contact,
            phone=demo_phone(phone_n),
            email=demo_email(contact),
            industry=industry,
            timezone="America/Chicago",
            stage=stage,
            status="won" if stage == STAGE_LIVE else "open",
            source="demonstration",
            deal_value=Decimal(value),
            next_action=next_action,
            next_action_due_at=now + timedelta(days=next_in),
            stage_changed_at=now - timedelta(days=days_in_stage),
            created_at=now - timedelta(days=days_in_stage + 8),
        )
        if stage in (STAGE_PROPOSAL, STAGE_CLOSING, STAGE_LIVE):
            opp.discovery_completed_at = now - timedelta(days=days_in_stage + 4)
            opp.demo_status = "delivered"
            opp.demo_ready_at = now - timedelta(days=days_in_stage + 2)
        if stage == STAGE_PROPOSAL:
            opp.proposal_status = "sent"
            opp.proposal_sent_at = now - timedelta(days=days_in_stage - 1)
        if stage == STAGE_LIVE:
            opp.won_at = now - timedelta(days=days_in_stage + 6)
        db.add(opp)
        opps[key] = opp
    db.flush()

    db.add(DiscoveryRecord(
        id=canonical_id(env, "disc", "cordova"),
        opportunity_id=opps["cordova"].id,
        business_description="Four dental practices under one owner, sharing a "
                             "front desk team of six.",
        business_goals="Stop losing new-patient enquiries that arrive after "
                       "hours.",
        current_process="Enquiries land in a shared inbox and are triaged the "
                        "following morning.",
        current_tools="Practice management software, a shared inbox, a paper "
                      "call log.",
        bottlenecks="Nobody owns the first response. Weekend enquiries wait "
                    "until Monday.",
        lead_sources="Website form, Google, referrals from two orthodontists.",
        team_size="6 front desk, 4 practices",
        appointment_process="Front desk calls back and offers whatever is open.",
        follow_up_process="One call. If there is no answer, nothing further.",
        required_integrations="Practice management calendar",
        automation_opportunities="Immediate response to after-hours enquiries; "
                                 "reminders before the first appointment.",
        desired_outcome="Every enquiry answered within five minutes, seven days "
                        "a week.",
        demo_requirements="Show the after-hours enquiry path end to end.",
        completed_at=now - timedelta(days=3),
        completed_by=staff["jordan"].id,
    ))

    events = 0
    for opp_key, days_ago, event_type, summary in SALES_TIMELINE:
        opp = opps[opp_key]
        db.add(OpportunityEvent(
            id=canonical_id(env, "oppev", opp_key, events),
            opportunity_id=opp.id,
            event_type=event_type,
            summary=summary,
            actor_user_id=opp.owner_user_id,
            occurred_at=now - timedelta(days=days_ago),
        ))
        events += 1

    db.flush()
    return {"staff": len(staff), "opportunities": len(opps), "events": events}


def build(db: Session, platform_id: str, actor: Optional[User] = None
          ) -> Dict[str, Any]:
    """Reset, then build one brand's demonstration world. Idempotent.

    RESET RUNS FIRST, ALWAYS. Seeding on top of an existing world would produce
    a primary-key collision at best and two of everything at worst, and a demo
    that has drifted is worse than one that is missing — the presenter finds
    out in front of the prospect.
    """
    env = ensure_environment(db, platform_id, commit=False)
    platform = db.query(Platform).filter(Platform.id == platform_id).first()
    if platform is None:
        raise HTTPException(status_code=404, detail="No such brand.")

    env.status = ENV_BUILDING
    db.flush()

    try:
        org, brand = _ensure_tenants(db, env, platform)
        reset(db, env, actor=actor, commit=False)
        # `reset` sets the status back to empty; we are mid-build.
        env.status = ENV_BUILDING

        now = datetime.utcnow()
        workspace = _seed_workspace(db, env, platform, org, now)
        sales = _seed_sales(db, env, platform, brand, now)

        env.status = ENV_READY
        env.seed_version = SEED_VERSION
        env.seeded_at = now
        env.last_error = None
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:                                # pragma: no cover
        db.rollback()
        env = ensure_environment(db, platform_id, commit=False)
        env.status = ENV_ERROR
        env.last_error = "%s: %s" % (type(e).__name__, str(e)[:400])
        db.commit()
        log.exception("demo environment build failed for platform %s", platform_id)
        raise HTTPException(
            status_code=500,
            detail="The demonstration environment could not be built: %s"
                   % env.last_error)

    log.info("AUDIT: demo environment built platform=%s by=%s", platform_id,
             getattr(actor, "email", "system"))
    return {"environment_id": env.id, "platform_id": platform_id,
            "organization_id": env.organization_id,
            "brand_sales_org_id": env.brand_sales_org_id,
            "seed_version": env.seed_version,
            "workspace": workspace, "sales": sales}


# ─────────────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────────────

def overview(db: Session, platform: Platform) -> Dict[str, Any]:
    """What a God screen renders for one brand, in words rather than ids."""
    env = get_environment(db, platform.id)
    if env is None:
        return {
            "platform_id": platform.id, "platform_name": platform.name,
            "platform_slug": platform.slug,
            "exists": False, "status": ENV_EMPTY, "ready": False,
            "seed_version": None, "current_seed_version": SEED_VERSION,
            "stale": False, "seeded_at": None, "last_reset_at": None,
            "counts": {}, "workspace_name": None, "sales_org_name": None,
            "last_error": None,
        }
    org = (db.query(Organization)
           .filter(Organization.id == env.organization_id).first()
           if env.organization_id else None)
    brand = (db.query(BrandSalesOrg)
             .filter(BrandSalesOrg.id == env.brand_sales_org_id).first()
             if env.brand_sales_org_id else None)
    counts = {}
    if org is not None and brand is not None:
        counts = {
            "leads": db.query(Lead).filter(
                Lead.organization_id == org.id).count(),
            "staff": db.query(User).filter(
                User.id.like(env_prefix(env) + "%")).count(),
            "opportunities": db.query(Opportunity).filter(
                Opportunity.brand_sales_org_id == brand.id).count(),
            "sessions": db.query(DemoSession).filter(
                DemoSession.environment_id == env.id).count(),
            "actions": db.query(DemoActionEvent).filter(
                DemoActionEvent.environment_id == env.id).count(),
        }
    return {
        "platform_id": platform.id, "platform_name": platform.name,
        "platform_slug": platform.slug,
        "exists": True,
        "environment_id": env.id,
        "status": env.status,
        "ready": env.is_ready(),
        "seed_version": env.seed_version,
        "current_seed_version": SEED_VERSION,
        # A world built by an older version of the seed no longer matches what
        # the guided scenarios describe. Say so rather than letting a presenter
        # discover it live.
        "stale": bool(env.is_ready() and env.seed_version != SEED_VERSION),
        "seeded_at": env.seeded_at.isoformat() if env.seeded_at else None,
        "last_reset_at": (env.last_reset_at.isoformat()
                          if env.last_reset_at else None),
        "workspace_name": org.name if org else None,
        "workspace_id": org.id if org else None,
        "sales_org_name": brand.name if brand else None,
        "sales_org_id": brand.id if brand else None,
        "counts": counts,
        "last_error": env.last_error,
    }


def record_event(db: Session, env: Optional[DemoEnvironment],
                 user: Optional[User], action: str,
                 scenario_key: Optional[str] = None,
                 step_key: Optional[str] = None,
                 target_type: Optional[str] = None,
                 target_id: Optional[str] = None,
                 simulated_provider: Optional[str] = None,
                 success: bool = True,
                 detail: Optional[str] = None,
                 commit: bool = False) -> DemoActionEvent:
    """Append one line to the demo's own trail. Never `audit_log_entries`."""
    row = DemoActionEvent(
        environment_id=getattr(env, "id", None),
        platform_id=getattr(env, "platform_id", None),
        user_id=getattr(user, "id", None),
        user_email=getattr(user, "email", None),
        scenario_key=scenario_key, step_key=step_key, action=action,
        target_type=target_type, target_id=target_id,
        simulated_provider=simulated_provider, success=success, detail=detail,
    )
    db.add(row)
    if commit:
        db.commit()
    else:
        db.flush()
    return row
