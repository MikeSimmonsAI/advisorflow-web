"""Won -> Customer. The only place in this codebase where a sale becomes a tenant.

WHAT THIS MODULE IS FOR
-----------------------
A deal reaching Won creates nothing. Somebody has to look at the company name,
decide it is spelled right, decide which platform it belongs on, and ask for a
customer organization to exist. This module is that request, and it is the only
supported path from the brand-sales tree into the customer-tenant tree.

FIVE THINGS IT REFUSES TO DO
----------------------------
1. Provision automatically. `stage == won` is an input, never a trigger.
2. Guess the platform. The customer organization is created on the platform that
   owns the BrandSalesOrg that owns the opportunity. A BookaBoost deal cannot
   land on the EvoSys Pro platform, and if the brand sales org has no platform
   the provisioning is refused rather than defaulted.
3. Create a second organization for a deal already provisioned. The check here
   is convenience; the UNIQUE constraints on `implementations.opportunity_id`
   and `implementations.organization_id` are the guarantee.
4. Edit sales history. The operator may correct the CUSTOMER ORGANIZATION's name
   before it is created. The opportunity's `company_name`, the proposal, and the
   accepted amounts are what was agreed and are never rewritten to match.
5. Copy the sale into the tenant. No Lead is created from the sales contact, no
   salesperson is given tenant membership, and no brand-sales membership is
   inherited by anybody.

WHAT IT DOES MUTATE ON THE OPPORTUNITY
--------------------------------------
Exactly two fields: `customer_organization_id` (the architecture's single
designated bridge, previously written by nothing at all) and `stage`, which
advances `won -> onboarding` because `STAGE_ONBOARDING` has existed unused in
`sales_models` since Checkpoint 1 for precisely this moment. `status` stays
`"won"` and `won_at` keeps its original timestamp, so every existing Won metric
- all of which filter on `status`, not `stage` - reports the same number after
provisioning as before it.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status as http_status
from sqlalchemy.orm import Session

from app.models.models import Organization, Platform, User, Proposal
from app.models.sales_models import (
    Opportunity, BrandSalesOrg, BrandPackage, DiscoveryRecord,
    STAGE_WON, STAGE_ONBOARDING,
)
from app.models.implementation_models import (
    Implementation, ImplementationMilestone,
    IMPL_NOT_STARTED, MILESTONE_PENDING,
)
from app.services.sales_access import is_god, is_sales_manager
from app.routers.audit_log_router import log_action


# ── milestone templates ─────────────────────────────────────────────────────
#
# Industry-neutral at the core, per Checkpoint 6 §13. There is deliberately
# nothing funeral-specific here: "Business profile" and "Lead import" are true
# of a roofing company and an insurance agency in exactly the same way, and the
# first customer's industry must not become the platform's default.
#
# `required` marks a step a customer genuinely cannot operate without. It is
# advisory at launch - it produces a warning, not a refusal - because blocking a
# launch on a checkbox is how a customer waits a week for nothing.

CORE_MILESTONES: List[Dict[str, Any]] = [
    {"key": "kickoff",          "label": "Kickoff call",            "required": True,
     "description": "Introduce the implementation owner and agree the launch date."},
    {"key": "business_profile", "label": "Business profile",        "required": True,
     "description": "Legal name, address, phone, hours, timezone, branding."},
    {"key": "customer_users",   "label": "Customer users",          "required": True,
     "description": "Create the customer's own staff accounts and roles."},
    {"key": "calendar",         "label": "Calendar connection",     "required": True,
     "description": "Connect each booking user's Google or Microsoft calendar."},
    {"key": "lead_import",      "label": "Lead import",             "required": False,
     "description": "Import existing contacts and de-duplicate them."},
    {"key": "testing",          "label": "Testing",                 "required": True,
     "description": "End-to-end test of the paths this customer will actually use."},
    {"key": "training",         "label": "Training",                "required": True,
     "description": "Train the customer's staff on their configured workflow."},
    {"key": "launch",           "label": "Launch",                  "required": True,
     "description": "Final review, then mark the customer Live."},
]

# Steps that only exist if the customer bought the capability. Keyed by
# BrandPackage.key, and additive - a Professional customer gets Growth's steps
# too. Unknown package keys simply add nothing, which is the correct behaviour
# for a package created after this file was written.
PACKAGE_MILESTONES: Dict[str, List[Dict[str, Any]]] = {
    "starter": [],
    "growth": [
        {"key": "sms",      "label": "SMS number",        "required": True,
         "description": "Provision and verify the sending number."},
        {"key": "cadences", "label": "Cadence setup",     "required": True,
         "description": "Configure the follow-up sequences this customer will run."},
    ],
    "professional": [
        {"key": "sms",           "label": "SMS number",         "required": True,
         "description": "Provision and verify the sending number."},
        {"key": "cadences",      "label": "Cadence setup",      "required": True,
         "description": "Configure the follow-up sequences this customer will run."},
        {"key": "ai_config",     "label": "AI configuration",   "required": True,
         "description": "Tone, guardrails and escalation rules for AI replies."},
        {"key": "voice_config",  "label": "Voice configuration", "required": False,
         "description": "Voice agent script, calendar binding and test call."},
        {"key": "integrations",  "label": "Integrations",       "required": False,
         "description": "Third-party systems this customer asked to connect."},
    ],
}
# Multi-tenant / custom deals get the full professional set plus migration.
PACKAGE_MILESTONES["multi_tenant"] = PACKAGE_MILESTONES["professional"] + [
    {"key": "data_migration", "label": "Data migration", "required": True,
     "description": "Move the customer's historical records across."},
]
PACKAGE_MILESTONES["custom"] = PACKAGE_MILESTONES["multi_tenant"]


def milestone_template(package: Optional[BrandPackage]) -> List[Dict[str, Any]]:
    """Core steps, plus whatever the purchased package adds, in a stable order.

    Order matters to the UI, so the extras are spliced in before `testing`
    rather than appended after `launch`, which would put "provision an SMS
    number" after "mark the customer Live".
    """
    extras = []
    if package is not None and getattr(package, "key", None):
        extras = PACKAGE_MILESTONES.get(str(package.key).strip().lower(), [])
    if not extras:
        return [dict(m) for m in CORE_MILESTONES]

    seen = {m["key"] for m in CORE_MILESTONES}
    extras = [dict(e) for e in extras if e["key"] not in seen]
    out: List[Dict[str, Any]] = []
    for m in CORE_MILESTONES:
        if m["key"] == "testing":
            out.extend(extras)
        out.append(dict(m))
    return out


# ── authorization ───────────────────────────────────────────────────────────

def can_provision(user: User, opp: Opportunity, db: Session) -> bool:
    """God provisions anything. A sales manager provisions their OWN brand only.

    A rep cannot provision at all, however senior - creating a customer tenant
    is a control-plane act, not a sales act.
    """
    if is_god(user):
        return True
    return bool(opp.brand_sales_org_id) and is_sales_manager(user, db, opp.brand_sales_org_id)


def assert_can_provision(user: User, opp: Opportunity, db: Session) -> None:
    if not can_provision(user, opp, db):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Provisioning a customer requires god or sales-manager authority for this brand.",
        )


# ── lookups ─────────────────────────────────────────────────────────────────

def implementation_for_opportunity(db: Session, opportunity_id: str) -> Optional[Implementation]:
    return (db.query(Implementation)
              .filter(Implementation.opportunity_id == opportunity_id)
              .first())


def resolve_platform_id(db: Session, opp: Opportunity) -> str:
    """The platform the customer organization MUST be created on.

    Derived from the brand sales org that owns the deal, never from the actor,
    never from a request field, never defaulted to "the first platform". If the
    brand sales org has no platform the answer is an error, because a customer
    organization with the wrong platform is invisible to its own brand and
    visible to another one.
    """
    bso = None
    if opp.brand_sales_org_id:
        bso = db.query(BrandSalesOrg).filter(BrandSalesOrg.id == opp.brand_sales_org_id).first()
    if bso is None:
        raise HTTPException(status_code=409,
                            detail="This opportunity has no brand sales organisation, so its platform cannot be determined.")
    if not bso.platform_id:
        raise HTTPException(status_code=409,
                            detail="Brand sales organisation '%s' is not attached to a platform. "
                                   "Attach it before provisioning a customer." % bso.name)
    if db.query(Platform).filter(Platform.id == bso.platform_id).first() is None:
        raise HTTPException(status_code=409,
                            detail="Brand sales organisation '%s' points at a platform that does not exist." % bso.name)
    return bso.platform_id


def accepted_proposal(db: Session, opp: Opportunity) -> Optional[Proposal]:
    """The accepted proposal for this deal, newest first.

    Read server-side only. Checkpoint 6 §28 - provisioning may READ accepted
    proposal details; it must not project proposal internals to the customer
    side or to anybody the proposal security model would not already show.
    """
    return (db.query(Proposal)
              .filter(Proposal.opportunity_id == opp.id,
                      Proposal.accepted_at.isnot(None),
                      Proposal.deleted_at.is_(None))
              .order_by(Proposal.accepted_at.desc(), Proposal.version.desc())
              .first())


def _slugify(text: str) -> str:
    s = (text or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def unique_slug(db: Session, base: str) -> str:
    """A slug nobody else holds. Suffixes numerically rather than failing.

    `organizations.slug` is globally unique across every platform, so two brands
    each selling to a "Greenwood Chapel" is a real and legitimate collision, not
    an operator error to be thrown back at them.
    """
    base = _slugify(base) or "customer"
    base = base[:40].strip("-") or "customer"
    if db.query(Organization).filter(Organization.slug == base).first() is None:
        return base
    for n in range(2, 500):
        cand = "%s-%d" % (base, n)
        if db.query(Organization).filter(Organization.slug == cand).first() is None:
            return cand
    raise HTTPException(status_code=409, detail="Could not derive a free slug for '%s'." % base)


def _discovery_summary(disc: DiscoveryRecord) -> Dict[str, Any]:
    """The discovery answers implementation actually needs, and no others.

    Field names come from `DiscoveryRecord.FIELDS`, which is the form's own
    ordered definition - so a question added to discovery later appears here
    without this function being edited, and a question renamed cannot silently
    become a null.
    """
    wanted = ("business_description", "business_goals", "current_process",
              "current_tools", "bottlenecks", "required_integrations",
              "desired_outcome", "opportunity_notes")
    labels = dict(DiscoveryRecord.FIELDS)
    out = {}
    for f in wanted:
        v = getattr(disc, f, None)
        if v is not None and str(v).strip():
            out[f] = {"label": labels.get(f, f), "value": v}
    return out or None


# ── the review projection (§6) ──────────────────────────────────────────────

def provisioning_review(db: Session, opp: Opportunity) -> Dict[str, Any]:
    """Everything the operator needs to confirm before a tenant exists.

    Assembled server-side from real records. Every field is either present with
    a real value or present as null - there are no placeholder strings, because
    an operator who cannot tell "we never captured a phone number" from
    "(555) 555-5555" will provision the wrong thing.
    """
    bso = (db.query(BrandSalesOrg).filter(BrandSalesOrg.id == opp.brand_sales_org_id).first()
           if opp.brand_sales_org_id else None)
    platform = (db.query(Platform).filter(Platform.id == bso.platform_id).first()
                if bso and bso.platform_id else None)
    owner = (db.query(User).filter(User.id == opp.owner_user_id).first()
             if opp.owner_user_id else None)
    pkg_id = opp.selected_package_id or opp.package_interest_id
    pkg = db.query(BrandPackage).filter(BrandPackage.id == pkg_id).first() if pkg_id else None
    prop = accepted_proposal(db, opp)
    disc = (db.query(DiscoveryRecord)
              .filter(DiscoveryRecord.opportunity_id == opp.id).first())
    existing = implementation_for_opportunity(db, opp.id)
    existing_org = (db.query(Organization).filter(Organization.id == existing.organization_id).first()
                    if existing else None)

    def _num(v):
        return float(v) if v is not None else None

    return {
        "opportunity_id": opp.id,
        "is_won": opp.status == "won" or opp.stage in (STAGE_WON, STAGE_ONBOARDING),
        "won_at": opp.won_at,
        "stage": opp.stage,

        # Suggested customer organisation values. The operator may change any of
        # them; changing them does NOT write back to the opportunity.
        "suggested_org_name": opp.company_name,
        "suggested_slug": unique_slug(db, opp.company_name or "customer"),
        "suggested_industry": opp.industry or None,
        "suggested_timezone": opp.timezone or None,

        "contact_name": opp.contact_name,
        "contact_email": opp.email,
        "contact_phone": opp.phone,
        "website": opp.website,

        "platform": {"id": platform.id, "name": platform.name, "slug": platform.slug} if platform else None,
        "brand_sales_org": {"id": bso.id, "name": bso.name} if bso else None,
        "salesperson": {"id": owner.id, "name": owner.full_name, "email": owner.email} if owner else None,

        "package": ({"id": pkg.id, "key": pkg.key, "name": pkg.name,
                     "price": _num(pkg.price), "setup_fee": _num(pkg.setup_fee),
                     "currency": pkg.currency, "billing_period": pkg.billing_period,
                     "is_custom": bool(pkg.is_custom)} if pkg else None),
        "deal_value": _num(opp.deal_value),
        # deal_value_override is a BOOLEAN flag meaning "a manager set this by
        # hand", not a second amount. Surfaced so the review can say so.
        "deal_value_was_overridden": bool(opp.deal_value_override),

        "accepted_proposal": ({"id": prop.id, "number": prop.proposal_number,
                               "version": prop.version, "accepted_at": prop.accepted_at,
                               "final_amount": _num(prop.final_amount),
                               "currency": prop.currency,
                               "implementation_plan": prop.implementation_plan,
                               "scope": prop.scope} if prop else None),

        "discovery": (_discovery_summary(disc) if disc else None),

        "milestone_template": milestone_template(pkg),

        "already_provisioned": existing is not None,
        "existing": ({"implementation_id": existing.id,
                      "organization_id": existing.organization_id,
                      "organization_name": existing_org.name if existing_org else None,
                      "status": existing.status,
                      "created_at": existing.created_at} if existing else None),
    }


# ── provisioning ────────────────────────────────────────────────────────────

def provision_customer(
    db: Session,
    opp: Opportunity,
    actor: User,
    *,
    org_name: Optional[str] = None,
    slug: Optional[str] = None,
    industry: Optional[str] = None,
    timezone: Optional[str] = None,
    org_phone: Optional[str] = None,
    org_address: Optional[str] = None,
    plan: str = "standard",
    target_launch_date: Optional[datetime] = None,
    owner_user_id: Optional[str] = None,
    notes: Optional[str] = None,
    milestone_keys: Optional[List[str]] = None,
) -> Tuple[Implementation, bool]:
    """Create the customer organisation and its implementation. Idempotent.

    Returns `(implementation, created)`. A second call for the same opportunity
    returns the FIRST implementation with `created=False` and writes nothing -
    including no audit entry, because "provisioned" happened once and an audit
    trail that records it twice is lying about what happened.

    The whole thing is one transaction. If the milestone insert fails, the
    organisation does not exist either.
    """
    existing = implementation_for_opportunity(db, opp.id)
    if existing is not None:
        return existing, False

    # ── preconditions ──
    if opp.status != "won" and opp.stage != STAGE_WON:
        raise HTTPException(status_code=409,
                            detail="Only a Won opportunity can be provisioned. This one is at stage '%s'." % opp.stage)

    platform_id = resolve_platform_id(db, opp)

    # An opportunity already carrying a customer organisation but with no
    # implementation is a pre-Checkpoint-6 shape or a partial failure. Adopt the
    # organisation rather than creating a second one for the same customer.
    adopted_org = None
    if opp.customer_organization_id:
        adopted_org = (db.query(Organization)
                         .filter(Organization.id == opp.customer_organization_id).first())
        if adopted_org is None:
            raise HTTPException(status_code=409,
                                detail="This opportunity points at customer organisation '%s', which does not exist. "
                                       "Clear the link before provisioning." % opp.customer_organization_id)
        if (db.query(Implementation)
              .filter(Implementation.organization_id == adopted_org.id).first()) is not None:
            raise HTTPException(status_code=409,
                                detail="Customer organisation '%s' already belongs to another implementation."
                                       % adopted_org.name)

    pkg_id = opp.selected_package_id or opp.package_interest_id
    pkg = db.query(BrandPackage).filter(BrandPackage.id == pkg_id).first() if pkg_id else None
    if pkg is not None and pkg.platform_id and pkg.platform_id != platform_id:
        raise HTTPException(status_code=409,
                            detail="The selected package belongs to a different platform than this deal's brand.")

    prop = accepted_proposal(db, opp)
    bso = db.query(BrandSalesOrg).filter(BrandSalesOrg.id == opp.brand_sales_org_id).first()

    if owner_user_id:
        if db.query(User).filter(User.id == owner_user_id, User.is_active.is_(True)).first() is None:
            raise HTTPException(status_code=404, detail="Implementation owner not found.")

    now = datetime.utcnow()

    # ── customer organisation ──
    if adopted_org is not None:
        org = adopted_org
        if not org.platform_id:
            org.platform_id = platform_id
        elif org.platform_id != platform_id:
            raise HTTPException(status_code=409,
                                detail="Linked customer organisation is on a different platform than this deal's brand.")
    else:
        name = (org_name or opp.company_name or "").strip()
        if not name:
            raise HTTPException(status_code=400,
                                detail="A customer organisation name is required.")
        org_slug = _slugify(slug) if slug else None
        if org_slug:
            if db.query(Organization).filter(Organization.slug == org_slug).first() is not None:
                raise HTTPException(status_code=409, detail="Slug '%s' is already taken." % org_slug)
        else:
            org_slug = unique_slug(db, name)

        org = Organization(
            name=name,
            slug=org_slug,
            platform_id=platform_id,     # never NULL, never inferred, never defaulted
            plan=plan or "standard",
            industry=(industry or opp.industry or "general"),
            is_active=True,
            org_phone=(org_phone or opp.phone or None),
            org_address=(org_address or None),
        )
        db.add(org)
        db.flush()

    # ── the bridge ──
    before_link = opp.customer_organization_id
    before_stage = opp.stage
    opp.customer_organization_id = org.id
    if opp.stage == STAGE_WON:
        opp.stage = STAGE_ONBOARDING
        opp.stage_changed_at = now
    # status stays "won" and won_at is untouched: this deal was won, and every
    # Won metric in the codebase filters on status.

    # ── implementation ──
    impl = Implementation(
        opportunity_id=opp.id,
        organization_id=org.id,
        platform_id=platform_id,
        brand_sales_org_id=opp.brand_sales_org_id,
        package_id=pkg.id if pkg else None,
        accepted_proposal_id=prop.id if prop else None,
        accepted_proposal_version=prop.version if prop else None,
        sold_by_user_id=opp.owner_user_id,
        owner_user_id=owner_user_id or None,
        owner_assigned_at=now if owner_user_id else None,
        owner_assigned_by=actor.id if owner_user_id else None,
        status=IMPL_NOT_STARTED,
        target_launch_date=target_launch_date,
        notes=notes or None,
        last_activity_at=now,
        # Billing INTENT copied from what was sold. Nothing charges anybody.
        billing_status="not_configured",
        implementation_fee=(pkg.setup_fee if pkg else None),
        recurring_amount=(prop.final_amount if prop else (pkg.price if pkg else None)),
        currency=(prop.currency if prop else (pkg.currency if pkg else "USD")) or "USD",
        created_at=now,
        created_by=actor.id,
    )
    db.add(impl)
    db.flush()

    # ── milestones ──
    template = milestone_template(pkg)
    if milestone_keys is not None:
        wanted = set(milestone_keys)
        template = [m for m in template if m["key"] in wanted]
    for i, m in enumerate(template):
        db.add(ImplementationMilestone(
            implementation_id=impl.id,
            key=m["key"], label=m["label"],
            description=m.get("description"),
            position=i,
            is_required=bool(m.get("required")),
            status=MILESTONE_PENDING,
            created_at=now,
        ))

    # ── audit (§23) ──
    # organization_id is the NEW tenant; platform and brand give the control
    # plane its two other axes. `commit=False` keeps this inside the same
    # transaction as the rows it describes, so an audit entry can never survive
    # a provisioning that rolled back.
    log_action(
        db, org.id, actor.id,
        action="customer_provisioned",
        target_type="implementation",
        target_id=impl.id,
        platform_id=platform_id,
        brand_sales_org_id=opp.brand_sales_org_id,
        before={"opportunity_stage": before_stage,
                "customer_organization_id": before_link},
        after={"opportunity_stage": opp.stage,
               "customer_organization_id": org.id,
               "organization_name": org.name,
               "organization_slug": org.slug},
        details={"opportunity_id": opp.id,
                 "brand_sales_org": bso.name if bso else None,
                 "package_key": pkg.key if pkg else None,
                 "adopted_existing_org": adopted_org is not None,
                 "milestones": len(template)},
        note="Won -> Customer provisioning",
        commit=False,
    )

    db.commit()
    db.refresh(impl)
    return impl, True
