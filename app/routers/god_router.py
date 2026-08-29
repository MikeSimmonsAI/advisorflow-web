"""
AdvisorFlow Command Center — god_admin only.

All routes here are gated by require_god (deps.py). Returns 403 with no
information to anyone whose role != 'god_admin', including super_admins.
The existence of this router and the AdvisorFlow layer is invisible to
every role below god_admin.

Endpoints:
  GET   /god/stats                       — top-level KPIs across all platforms
  GET   /god/platforms                   — all platforms with org/lead counts
  POST  /god/platforms                   — create a new platform
  GET   /god/orgs                        — all orgs across all platforms
  POST  /god/orgs                        — create a new org
  GET   /god/leads                       — all leads across all platforms
  GET   /god/users                       — all super_admins + god_admins
  POST  /god/users                       — create a new admin user
  PATCH /god/users/{user_id}/role        — promote/demote a user's role
  POST  /god/users/{user_id}/deactivate  — deactivate any account
  POST  /god/users/{user_id}/activate    — reactivate any account
  POST  /god/orgs/{org_id}/impersonate   — return a short-lived org context token
"""

import logging
import os
import re
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import User, Organization, Lead, Platform, Message
from app.services.auth_service import hash_password
from app.services import staff_activation
from app.models.staff_models import PURPOSE_SETUP

log = logging.getLogger(__name__)

router = APIRouter(prefix="/god", tags=["AdvisorFlow Command Center"])


# ── Schemas ────────────────────────────────────────────────────────────────

class RolePatch(BaseModel):
    role: str  # "god_admin" | "super_admin" | "org_admin" | "advisor" | "viewer"


class PlatformCreate(BaseModel):
    name: str
    slug: str
    domain: Optional[str] = None
    support_email: Optional[str] = None


class OrgCreate(BaseModel):
    name: str
    platform_slug: str
    plan: Optional[str] = "trial"


class OrgStatusUpdate(BaseModel):
    reason: Optional[str] = None


class UserCreate(BaseModel):
    email: str
    full_name: str
    role: str = "super_admin"
    platform_slug: Optional[str] = None
    org_id: Optional[str] = None
    # Where the one-time link should point. Absent gives a relative path, which
    # is correct when the caller already knows its own host.
    base_url: Optional[str] = None


# ── Helpers ────────────────────────────────────────────────────────────────

ALLOWED_ROLES = {"god_admin", "super_admin", "org_admin", "advisor", "viewer"}


def _slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _unknowable_password() -> str:
    """Generated, hashed by the caller, and discarded in the same breath.

    This REPLACED `_temp_password`, which returned a short human-typeable string
    that was then handed back through the API. The account needs a hash so that
    no code path treats it as password-less; nobody needs to know what it is,
    and now nobody can. Access arrives by one-time link.
    """
    return secrets.token_urlsafe(48)


def _safe_count(db: Session, model, filters=None):
    q = db.query(func.count(model.id))
    if filters:
        q = q.filter(*filters)
    return q.scalar() or 0


def _compute_health_score(
    is_active: bool,
    lead_count: int,
    advisor_count: int,
    messages_30d: int,
    days_since_activity: Optional[float],
) -> int:
    """80-100 = Healthy, 60-79 = Attention, <60 = Critical"""
    if not is_active:
        return 0
    score = 100
    if lead_count == 0:
        score -= 30
    elif lead_count < 5:
        score -= 10
    if advisor_count == 0:
        score -= 20
    if messages_30d == 0:
        score -= 25
    elif messages_30d < 5:
        score -= 10
    if days_since_activity is None:
        score -= 20
    elif days_since_activity > 60:
        score -= 20
    elif days_since_activity > 30:
        score -= 10
    return max(0, min(100, score))


def _enrich_org(db: Session, org: Organization) -> dict:
    """Build the full God Mode intelligence record for one org."""
    cutoff_30 = datetime.utcnow() - timedelta(days=30)
    lead_count    = _safe_count(db, Lead, [Lead.organization_id == org.id])
    advisor_count = _safe_count(db, User, [User.organization_id == org.id, User.role == "advisor"])
    user_count    = _safe_count(db, User, [User.organization_id == org.id])
    try:
        messages_30d = db.execute(text("""
            SELECT COUNT(m.id) FROM messages m
            JOIN leads l ON m.lead_id = l.id
            WHERE l.organization_id = :org_id AND m.sent_at >= :cutoff
        """), {"org_id": org.id, "cutoff": cutoff_30}).scalar() or 0
    except Exception:
        messages_30d = 0
    try:
        last_msg_at = db.execute(text("""
            SELECT MAX(m.sent_at) FROM messages m
            JOIN leads l ON m.lead_id = l.id
            WHERE l.organization_id = :org_id
        """), {"org_id": org.id}).scalar()
    except Exception:
        last_msg_at = None
    try:
        last_login_at = db.execute(text("""
            SELECT MAX(last_login_at) FROM users WHERE organization_id = :org_id
        """), {"org_id": org.id}).scalar()
    except Exception:
        last_login_at = None
    candidates = [t for t in [last_msg_at, last_login_at] if t is not None]
    last_activity = max(candidates) if candidates else None
    days_since = None
    if last_activity:
        la = last_activity.replace(tzinfo=None) if hasattr(last_activity, 'replace') and last_activity.tzinfo else last_activity
        days_since = (datetime.utcnow() - la).total_seconds() / 86400
    health_score = _compute_health_score(
        is_active=org.is_active, lead_count=lead_count, advisor_count=advisor_count,
        messages_30d=int(messages_30d), days_since_activity=days_since,
    )
    return {
        "id": org.id, "name": org.name, "slug": getattr(org, "slug", None),
        "plan": getattr(org, "plan", "trial"), "is_active": org.is_active,
        "status": "active" if org.is_active else "dormant",
        "platform_id": getattr(org, "platform_id", None),
        "lead_count": lead_count, "user_count": user_count, "advisor_count": advisor_count,
        "messages_30d": int(messages_30d),
        "last_activity": last_activity.isoformat() if last_activity else None,
        "health_score": health_score,
        "created_at": org.created_at.isoformat() if getattr(org, "created_at", None) else None,
        "brand_name": getattr(org, "brand_name", None),
        "org_phone": getattr(org, "org_phone", None),
        "org_address": getattr(org, "org_address", None),
        "industry": getattr(org, "industry", None),
    }


# ── Routes ─────────────────────────────────────────────────────────────────

@router.post("/platforms", status_code=201)
def god_create_platform(body: PlatformCreate, god: User = Depends(require_god), db: Session = Depends(get_db)):
    slug = body.slug.strip().lower() or _slugify(body.name)
    if db.query(Platform).filter(Platform.slug == slug).first():
        raise HTTPException(status_code=409, detail=f"Platform slug '{slug}' already exists.")
    platform = Platform(name=body.name.strip(), slug=slug, domain=body.domain,
                        support_email=body.support_email, is_active=True)
    db.add(platform); db.commit(); db.refresh(platform)
    log.info("AUDIT: god_admin %s created platform %s (%s)", god.email, platform.name, platform.id)
    return {"id": platform.id, "name": platform.name, "slug": platform.slug,
            "domain": platform.domain, "support_email": platform.support_email,
            "is_active": platform.is_active, "org_count": 0, "lead_count": 0}


@router.post("/orgs", status_code=201)
def god_create_org(body: OrgCreate, god: User = Depends(require_god), db: Session = Depends(get_db)):
    platform = db.query(Platform).filter(Platform.slug == body.platform_slug).first()
    if not platform:
        raise HTTPException(status_code=404, detail=f"Platform '{body.platform_slug}' not found.")
    slug = _slugify(body.name)
    base_slug, suffix = slug, 1
    while db.query(Organization).filter(Organization.slug == slug).first():
        slug = f"{base_slug}-{suffix}"; suffix += 1
    org = Organization(name=body.name.strip(), slug=slug, plan=body.plan or "trial",
                       platform_id=platform.id, is_active=True)
    db.add(org); db.commit(); db.refresh(org)
    log.info("AUDIT: god_admin %s created org %s (%s) on %s", god.email, org.name, org.id, body.platform_slug)
    return {"id": org.id, "name": org.name, "slug": org.slug, "plan": org.plan,
            "platform_id": org.platform_id, "lead_count": 0, "user_count": 0,
            "created_at": org.created_at.isoformat() if org.created_at else None}


@router.post("/users", status_code=201)
def god_create_user(body: UserCreate, god: User = Depends(require_god), db: Session = Depends(get_db)):
    if body.role not in ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Allowed: {sorted(ALLOWED_ROLES)}")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail=f"User '{body.email}' already exists.")
    org_id = body.org_id
    if not org_id and body.platform_slug:
        row = db.execute(text("SELECT o.id FROM organizations o JOIN platforms p ON o.platform_id=p.id WHERE p.slug=:slug LIMIT 1"),
                         {"slug": body.platform_slug}).fetchone()
        if row: org_id = row[0]
    # NEVER auto-select an organization.
    #
    # This previously fell back to "the first organization in the database",
    # which would have silently placed the EvoSys Pro sales team inside a funeral
    # home customer tenant. A user's tenancy is a deliberate decision, never a
    # convenience default.
    #
    #   tenant role (advisor/org_admin/super_admin) → organization_id REQUIRED
    #   non-tenant role (god_admin, brand-sales)    → organization_id may be NULL,
    #                                                 access comes from memberships
    TENANT_ROLES = {"advisor", "org_admin", "super_admin"}
    if not org_id:
        if body.role in TENANT_ROLES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"role '{body.role}' is a customer-tenant role and requires an "
                    "explicit org_id or platform_slug. Refusing to guess an "
                    "organization."
                ),
            )
        # Non-tenant user: organization_id stays NULL on purpose.
        org_id = None
    # NO PLAINTEXT PASSWORD LEAVES THIS ROUTE.
    #
    # It used to return `temp_password`, which meant a real credential travelled
    # through an API response, a browser, and whatever the operator pasted it
    # into. The account still needs SOME hash so nothing downstream treats it as
    # password-less, so one is generated, hashed and discarded inside this
    # function - nobody, including the god_admin who called this, can know it.
    #
    # The person is reached by the one-time link instead, through the same
    # `staff_activation` machinery the brand-sales flow uses. The link is shown
    # exactly once and is not recoverable; a lost one is replaced by issuing
    # another, which revokes the first.
    user = User(organization_id=org_id, email=body.email.strip().lower(),
                full_name=body.full_name.strip(),
                password_hash=hash_password(_unknowable_password()),
                role=body.role, must_change_password=True, is_active=True)
    db.add(user)
    db.flush()

    row, raw = staff_activation.issue(db, user, god, purpose=PURPOSE_SETUP)
    setup_url = staff_activation.activation_url(body.base_url, raw)

    db.commit(); db.refresh(user)
    log.info("AUDIT: god_admin %s created user %s (%s) role=%s", god.email, user.email, user.id, user.role)
    return {"id": user.id, "email": user.email, "name": user.full_name, "role": user.role,
            "is_active": user.is_active, "must_change_password": True,
            "organization_id": user.organization_id,
            "setup_url": setup_url,
            "activation": {"id": row.id, "expires_at": row.expires_at,
                           "prefix": row.token_prefix},
            "warning": "The link is shown once and is not recoverable. No password "
                       "was created, returned or is knowable by anyone."}


@router.get("/stats")
def god_stats(god: User = Depends(require_god), db: Session = Depends(get_db)):
    total_leads  = _safe_count(db, Lead)
    total_orgs   = _safe_count(db, Organization)
    total_users  = _safe_count(db, User)
    total_admins = _safe_count(db, User, [User.role.in_(["org_admin","super_admin","god_admin"])])
    cutoff = datetime.utcnow() - timedelta(days=30)
    new_leads_30d = _safe_count(db, Lead, [Lead.created_at >= cutoff])
    try:
        platform_rows = db.execute(text("""
            SELECT p.name, p.slug, COUNT(DISTINCT o.id) AS org_count
            FROM platforms p LEFT JOIN organizations o ON o.platform_id = p.id
            GROUP BY p.id, p.name, p.slug ORDER BY p.name
        """)).fetchall()
        platforms = [{"name": r[0], "slug": r[1], "org_count": r[2]} for r in platform_rows]
    except Exception:
        platforms = []
    try:
        active_org_count = db.execute(text("SELECT COUNT(DISTINCT organization_id) FROM leads")).scalar() or 0
    except Exception:
        active_org_count = 0
    return {"total_platforms": len(platforms) or 3, "total_orgs": total_orgs,
            "active_orgs": active_org_count, "total_leads": total_leads,
            "new_leads_30d": new_leads_30d, "total_users": total_users,
            "total_admins": total_admins, "platforms": platforms,
            "as_of": datetime.utcnow().isoformat()}


@router.get("/platform-health")
def god_platform_health(god: User = Depends(require_god), db: Session = Depends(get_db)):
    """PLATFORM HEALTH — real conditions, computed by the server, in grouped queries.

    WHY THIS IS ONE ENDPOINT AND NOT SIX FRONTEND DERIVATIONS. The health grid
    needs facts that live in five tables. Asking the browser to derive them from
    the org list would either be wrong (it cannot see delivery receipts or
    integration credentials at all) or would need a request per organization,
    which is the N+1 the app was just hardened against. Every query below is
    grouped or aggregate and runs once for the whole platform.

    EVERY SECTION REPORTS ITS OWN SOURCE. A section whose data does not exist
    says so with `status: "no_source"` and a `needs` string naming what would
    have to be built. It never guesses, and it never renders green for silence.

    Sections: messaging · billing · jobs · integrations · customer_activity ·
    security. `status` is one of ok | warn | bad | no_source.
    """
    now = datetime.utcnow()
    cutoff_30 = now - timedelta(days=30)
    cutoff_7 = now - timedelta(days=7)
    cutoff_1 = now - timedelta(days=1)

    def _section(key, label, status, headline, detail, needs=None, to=None):
        return {"key": key, "label": label, "status": status,
                "headline": headline, "detail": detail,
                "needs": needs, "to": to}

    out = []

    # ── messaging ──────────────────────────────────────────────────────────
    # Twilio delivery receipts are written by the status-callback webhook, so
    # this is a real delivery figure and not a send count.
    try:
        rows = db.execute(text("""
            SELECT COALESCE(delivery_status, 'pending') AS s, COUNT(*)
            FROM messages WHERE sent_at >= :c GROUP BY 1
        """), {"c": cutoff_30}).fetchall()
        by_status = {str(r[0]): int(r[1]) for r in rows}
        sent_30 = sum(by_status.values())
        failed = by_status.get("failed", 0) + by_status.get("undelivered", 0)
        delivered = by_status.get("delivered", 0)
        pending = by_status.get("pending", 0)
        if sent_30 == 0:
            out.append(_section(
                "messaging", "Messaging", "off",
                "No messages in 30 days",
                "Nothing has been sent platform-wide, so there is no delivery "
                "rate to report.", to="/god/organizations"))
        else:
            fail_pct = round((failed / sent_30) * 100, 1)
            # Receipts only exist for messages Twilio has reported on. Counting
            # pending as a failure would show a red platform every time a
            # webhook is briefly behind.
            settled = sent_30 - pending
            deliv_pct = round((delivered / settled) * 100, 1) if settled else None
            if settled == 0:
                # NO RECEIPTS AT ALL IS NOT A CLEAN BILL OF HEALTH.
                #
                # Production returned 3,589 sent and 3,589 pending: every single
                # message is still awaiting a delivery receipt, which means the
                # Twilio status-callback webhook is not reporting. Scoring that
                # as "ok" because the failure count is zero is the exact
                # green-for-silence mistake this endpoint exists to avoid — the
                # failure count is zero because NOTHING has been reported, not
                # because nothing failed. Whether those messages arrived is
                # currently unknown, and the tile has to say so.
                out.append(_section(
                    "messaging", "Messaging", "warn",
                    "%d sent · no delivery receipts" % sent_30,
                    "Not one of %d messages sent in 30 days has a delivery "
                    "receipt, so whether they arrived is unknown. Receipts are "
                    "written by the Twilio status-callback webhook."
                    % sent_30,
                    needs="the status-callback webhook reporting back",
                    to="/god/organizations"))
            else:
                status = "bad" if fail_pct >= 10 else "warn" if fail_pct >= 2 else "ok"
                out.append(_section(
                    "messaging", "Messaging", status,
                    "%s%% delivered" % deliv_pct,
                    "%d sent in 30 days · %d failed or undelivered (%s%%) · %d awaiting a receipt."
                    % (sent_30, failed, fail_pct, pending),
                    to="/god/organizations"))
    except Exception as e:                                   # pragma: no cover
        log.warning("platform-health messaging failed: %s", e)
        out.append(_section("messaging", "Messaging", "no_source",
                            "Query failed", str(e)[:160]))

    # ── billing ────────────────────────────────────────────────────────────
    # Real, and deliberately unflattering: a customer with no Stripe customer
    # id cannot be charged, whatever their plan field says.
    try:
        real_orgs = db.query(Organization).filter(
            Organization.id != "org-god-platform").all()
        no_pm = [o for o in real_orgs
                 if not getattr(o, "stripe_customer_id", None)]
        unpriced = [o for o in real_orgs
                    if not getattr(o, "plan", None) or o.plan == "trial"]
        if not real_orgs:
            out.append(_section("billing", "Billing", "off",
                                "No customers yet", "Nothing to bill."))
        elif len(no_pm) == len(real_orgs):
            out.append(_section(
                "billing", "Billing", "bad",
                "Billing has never run",
                "None of %d customers has a payment method, and there is no "
                "invoice or payment table for charges to be written to."
                % len(real_orgs),
                needs="invoices + payments tables", to="/god/organizations"))
        else:
            status = "warn" if (no_pm or unpriced) else "ok"
            out.append(_section(
                "billing", "Billing", status,
                "%d of %d payable" % (len(real_orgs) - len(no_pm), len(real_orgs)),
                "%d without a payment method · %d with no package assigned."
                % (len(no_pm), len(unpriced)),
                to="/god/organizations"))
    except Exception as e:                                   # pragma: no cover
        log.warning("platform-health billing failed: %s", e)
        out.append(_section("billing", "Billing", "no_source",
                            "Query failed", str(e)[:160]))

    # ── background jobs ────────────────────────────────────────────────────
    # THERE IS NO JOB TABLE. Scheduled sends run in-process and leave no
    # durable record of success or failure, so there is nothing truthful to
    # report. A green tick here would be a lie about the one subsystem whose
    # silent failure nobody would notice.
    out.append(_section(
        "jobs", "Background jobs", "no_source",
        "No source",
        "Scheduled work leaves no durable record, so queue depth and failure "
        "counts cannot be reported.",
        needs="a job/queue table with outcomes"))

    # ── integrations ───────────────────────────────────────────────────────
    try:
        cred_rows = db.execute(text("""
            SELECT COUNT(*),
                   SUM(CASE WHEN is_active THEN 1 ELSE 0 END),
                   SUM(CASE WHEN last_used_at IS NULL THEN 1 ELSE 0 END),
                   SUM(CASE WHEN last_used_at IS NOT NULL AND last_used_at < :c30
                            THEN 1 ELSE 0 END)
            FROM integration_credentials
        """), {"c30": cutoff_30}).fetchone()
        total_c = int(cred_rows[0] or 0)
        active_c = int(cred_rows[1] or 0)
        never_used = int(cred_rows[2] or 0)
        stale = int(cred_rows[3] or 0)
        try:
            fails = db.execute(text("""
                SELECT COUNT(*) FROM integration_request_logs
                WHERE occurred_at >= :c7 AND success = false
            """), {"c7": cutoff_7}).scalar() or 0
        except Exception:
            fails = db.execute(text("""
                SELECT COUNT(*) FROM integration_request_logs
                WHERE occurred_at >= :c7 AND success = 0
            """), {"c7": cutoff_7}).scalar() or 0
        fails = int(fails)
        if total_c == 0:
            out.append(_section(
                "integrations", "Integrations", "off",
                "None issued",
                "No integration credentials exist. Voice and calendar bridges "
                "are not connected for any customer.",
                to="/god/customers"))
        else:
            status = "bad" if fails else "warn" if (stale or never_used) else "ok"
            bits = ["%d active of %d" % (active_c, total_c)]
            if never_used:
                bits.append("%d never used" % never_used)
            if stale:
                bits.append("%d unused for 30+ days" % stale)
            if fails:
                bits.append("%d failed calls in 7 days" % fails)
            out.append(_section(
                "integrations", "Integrations", status,
                "%d active" % active_c, " · ".join(bits) + ".",
                to="/god/customers"))
    except Exception as e:
        log.warning("platform-health integrations failed: %s", e)
        out.append(_section(
            "integrations", "Integrations", "no_source",
            "No source",
            "Integration credential state could not be read.",
            needs="integration_credentials table"))

    # ── customer activity ──────────────────────────────────────────────────
    # Same definition of "activity" that _enrich_org uses, so this tile and the
    # organization table cannot disagree about who is quiet.
    try:
        active_orgs = db.query(Organization).filter(
            Organization.id != "org-god-platform",
            Organization.is_active == True).all()          # noqa: E712
        ids = [o.id for o in active_orgs]
        used_today, used_week = set(), set()
        if ids:
            for cutoff, bucket in ((cutoff_1, used_today), (cutoff_7, used_week)):
                for src in (
                    "SELECT DISTINCT l.organization_id FROM messages m "
                    "JOIN leads l ON m.lead_id = l.id WHERE m.sent_at >= :c",
                    "SELECT DISTINCT organization_id FROM users "
                    "WHERE last_login_at >= :c AND organization_id IS NOT NULL",
                ):
                    for r in db.execute(text(src), {"c": cutoff}).fetchall():
                        if r[0]:
                            bucket.add(str(r[0]))
        t = len([i for i in ids if str(i) in used_today])
        w = len([i for i in ids if str(i) in used_week])
        n = len(ids)
        if n == 0:
            out.append(_section("customer_activity", "Customer activity", "off",
                                "No active customers", "Nothing to measure."))
        else:
            quiet = n - w
            status = "bad" if quiet >= max(1, n // 2) else "warn" if quiet else "ok"
            out.append(_section(
                "customer_activity", "Customer activity", status,
                "%d of %d used it today" % (t, n),
                "%d active in the last 7 days · %d silent for a week or more."
                % (w, quiet),
                to="/god/organizations"))
    except Exception as e:                                   # pragma: no cover
        log.warning("platform-health activity failed: %s", e)
        out.append(_section("customer_activity", "Customer activity",
                            "no_source", "Query failed", str(e)[:160]))

    # ── security & access ──────────────────────────────────────────────────
    try:
        owners = _safe_count(db, User, [User.role == "god_admin",
                                        User.is_active == True])   # noqa: E712
        suspended_orgs = _safe_count(db, Organization, [
            Organization.is_active == False,                        # noqa: E712
            Organization.id != "org-god-platform"])
        deactivated = _safe_count(db, User, [User.is_active == False])  # noqa: E712
        try:
            ctx = db.execute(text("""
                SELECT COUNT(*) FROM audit_log_entries
                WHERE created_at >= :c7 AND action LIKE 'platform_owner.%'
            """), {"c7": cutoff_7}).scalar() or 0
        except Exception:
            ctx = None
        # More than one platform-owner identity is the condition worth shouting
        # about: the whole identity model says there is exactly one.
        status = "bad" if owners != 1 else "ok"
        headline = ("%d platform owner identities" % owners) if owners != 1 \
            else "1 platform owner"
        detail = "%d organization(s) suspended · %d user account(s) deactivated" \
                 % (suspended_orgs, deactivated)
        if ctx is not None:
            detail += " · %d privileged context action(s) in 7 days" % int(ctx)
        out.append(_section("security", "Security & access", status,
                            headline, detail + ".", to="/god/audit"))
    except Exception as e:                                   # pragma: no cover
        log.warning("platform-health security failed: %s", e)
        out.append(_section("security", "Security & access", "no_source",
                            "Query failed", str(e)[:160]))

    return {"as_of": now.isoformat(), "sections": out}


@router.get("/twilio-diagnostics")
def god_twilio_diagnostics(god: User = Depends(require_god), db: Session = Depends(get_db)):
    """WHY DELIVERY RECEIPTS ARE OR ARE NOT ARRIVING — checkable, not argued.

    Production ran with 3,583 outbound messages and zero delivery receipts, and
    nothing in the product could say why: the callback URL was resolved from an
    environment variable inside a service module, and when that variable was
    empty the parameter was simply dropped. There was no surface anywhere that
    said "Twilio was never told where to send a receipt".

    This is that surface. Read-only, god-only, and it NEVER returns a secret —
    auth tokens are reported as configured / not configured, never by value.
    """
    from app.services import twilio_callbacks as tc

    callback = tc.status_callback_url()
    base = tc.public_api_base()

    # Which env spelling actually supplied it. The three names for one idea are
    # how this broke, so the answer names the winner rather than implying one.
    source = None
    for var in tc._BASE_ENV_VARS:
        if (os.environ.get(var) or "").strip():
            source = var
            break
    if source is None and base:
        for var in tc._DERIVE_FROM:
            if (os.environ.get(var) or "").strip():
                source = var + " (derived)"
                break

    try:
        rows = db.execute(text("""
            SELECT COALESCE(delivery_status, 'null') AS s, COUNT(*)
            FROM messages GROUP BY 1 ORDER BY 2 DESC
        """)).fetchall()
        by_status = {str(r[0]): int(r[1]) for r in rows}
    except Exception as e:                                   # pragma: no cover
        by_status = {"error": str(e)[:160]}

    total = sum(v for v in by_status.values() if isinstance(v, int))
    settled = sum(n for s, n in by_status.items()
                  if isinstance(n, int) and s not in ("pending", "null"))

    try:
        newest_settled = db.execute(text("""
            SELECT MAX(delivery_status_at) FROM messages
            WHERE delivery_status_at IS NOT NULL
        """)).scalar()
    except Exception:
        newest_settled = None

    return {
        "callback_url": callback,
        "callback_url_source": source,
        "public_api_base": base or None,
        # The single most useful line: if this is false, no receipt can arrive.
        "can_receive_receipts": bool(callback),
        "signature_validation": (
            "enforced" if os.environ.get("TWILIO_AUTH_TOKEN")
            else "SKIPPED — TWILIO_AUTH_TOKEN is not set on this service"
        ),
        "env_present": {
            v: bool((os.environ.get(v) or "").strip())
            for v in list(tc._BASE_ENV_VARS) + list(tc._DERIVE_FROM)
            + ["TWILIO_AUTH_TOKEN"]
        },
        "messages_by_delivery_status": by_status,
        "messages_total": total,
        "messages_with_a_receipt": settled,
        # SQLite hands MAX(timestamp) back as a string and Postgres as a
        # datetime. The gates run on SQLite and production runs on Postgres, so
        # assuming either one crashes in exactly the environment not being
        # looked at when the assumption was written.
        "newest_receipt_at": (
            newest_settled.isoformat() if hasattr(newest_settled, "isoformat")
            else (str(newest_settled) if newest_settled else None)
        ),
        "verdict": (
            "No callback URL can be built, so Twilio is never told where to "
            "report. Every message will stay 'pending' forever."
            if not callback else
            "A callback URL is configured. Messages sent from now on will be "
            "reported on; messages sent BEFORE it was configured stay 'pending' "
            "permanently, because Twilio was never asked to report them."
        ),
    }


@router.get("/platforms")
def god_platforms(god: User = Depends(require_god), db: Session = Depends(get_db)):
    try:
        rows = db.execute(text("""
            SELECT p.id, p.name, p.slug, p.domain, p.support_email, p.is_active,
                   COUNT(DISTINCT o.id) AS org_count, COUNT(DISTINCT l.id) AS lead_count,
                   COUNT(DISTINCT u.id) AS user_count
            FROM platforms p
            LEFT JOIN organizations o ON o.platform_id = p.id
            LEFT JOIN leads l ON l.organization_id = o.id
            LEFT JOIN users u ON u.organization_id = o.id
            GROUP BY p.id, p.name, p.slug, p.domain, p.support_email, p.is_active ORDER BY p.name
        """)).fetchall()
        return [{"id": r[0], "name": r[1], "slug": r[2], "domain": r[3],
                 "support_email": r[4], "is_active": r[5],
                 "org_count": r[6], "lead_count": r[7], "user_count": r[8]} for r in rows]
    except Exception as e:
        log.warning("god_platforms query failed: %s", e)
        return []


@router.get("/orgs")
def god_orgs(
    platform_slug: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    health: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    god: User = Depends(require_god),
    db: Session = Depends(get_db),
):
    q = db.query(Organization)
    if platform_slug:
        q = q.filter(Organization.platform_id.in_(
            db.execute(text("SELECT id FROM platforms WHERE slug = :slug"), {"slug": platform_slug}).scalars().all()
        ))
    if search:
        q = q.filter(
            Organization.name.ilike(f"%{search}%") |
            Organization.slug.ilike(f"%{search}%") |
            Organization.id.ilike(f"%{search}%")
        )
    if status == "active":
        q = q.filter(Organization.is_active == True)
    elif status == "dormant":
        q = q.filter(Organization.is_active == False)
    total = q.count()
    orgs = q.order_by(Organization.name).offset(skip).limit(limit).all()
    result = [_enrich_org(db, org) for org in orgs]
    if health:
        def _band(s): return "healthy" if s >= 80 else "attention" if s >= 60 else "critical"
        result = [r for r in result if _band(r["health_score"]) == health.lower()]
    return {"total": total, "orgs": result}


@router.get("/orgs/{org_id}/detail")
def god_org_detail(org_id: str, god: User = Depends(require_god), db: Session = Depends(get_db)):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    enriched = _enrich_org(db, org)
    advisors = db.query(User).filter(
        User.organization_id == org_id,
        User.role.in_(["advisor","org_admin","super_admin"]),
        User.is_active == True,
    ).order_by(User.full_name).limit(10).all()
    enriched["advisors"] = [{"id": u.id, "full_name": u.full_name, "email": u.email, "role": u.role} for u in advisors]
    try:
        recent = db.execute(text("""
            SELECT DATE(m.sent_at) as day, COUNT(*) as cnt
            FROM messages m JOIN leads l ON m.lead_id = l.id
            WHERE l.organization_id = :org_id AND m.sent_at >= NOW() - INTERVAL '7 days'
            GROUP BY day ORDER BY day
        """), {"org_id": org_id}).fetchall()
        enriched["msg_trend_7d"] = [{"date": str(r[0]), "count": r[1]} for r in recent]
    except Exception:
        enriched["msg_trend_7d"] = []
    if org.platform_id:
        try:
            plat = db.execute(text("SELECT name, slug FROM platforms WHERE id = :pid"), {"pid": org.platform_id}).fetchone()
            enriched["platform_name"] = plat[0] if plat else None
            enriched["platform_slug"] = plat[1] if plat else None
        except Exception:
            enriched["platform_name"] = None; enriched["platform_slug"] = None
    return enriched


@router.post("/orgs/{org_id}/suspend")
def god_suspend_org(org_id: str, body: OrgStatusUpdate, god: User = Depends(require_god), db: Session = Depends(get_db)):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org: raise HTTPException(status_code=404, detail="Organization not found")
    org.is_active = False; db.commit()
    log.info("AUDIT: god_admin %s SUSPENDED org %s (%s) reason=%s", god.email, org.name, org_id, body.reason or "none")
    return {"org_id": org_id, "name": org.name, "status": "dormant", "is_active": False}


@router.post("/orgs/{org_id}/reactivate")
def god_reactivate_org(org_id: str, body: OrgStatusUpdate, god: User = Depends(require_god), db: Session = Depends(get_db)):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org: raise HTTPException(status_code=404, detail="Organization not found")
    org.is_active = True; db.commit()
    log.info("AUDIT: god_admin %s REACTIVATED org %s (%s) reason=%s", god.email, org.name, org_id, body.reason or "none")
    return {"org_id": org_id, "name": org.name, "status": "active", "is_active": True}


@router.get("/leads")
def god_leads(
    platform_slug: Optional[str] = Query(None), org_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None), status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
    god: User = Depends(require_god), db: Session = Depends(get_db),
):
    q = db.query(Lead)
    if org_id:
        q = q.filter(Lead.organization_id == org_id)
    elif platform_slug:
        try:
            org_ids = db.execute(text("""
                SELECT o.id FROM organizations o JOIN platforms p ON o.platform_id = p.id WHERE p.slug = :slug
            """), {"slug": platform_slug}).scalars().all()
            q = q.filter(Lead.organization_id.in_(org_ids))
        except Exception: pass
    if search:
        q = q.filter(Lead.first_name.ilike(f"%{search}%") | Lead.last_name.ilike(f"%{search}%") |
                     Lead.email.ilike(f"%{search}%") | Lead.phone.ilike(f"%{search}%"))
    if status: q = q.filter(Lead.status == status)
    total = q.count()
    leads = q.order_by(Lead.created_at.desc()).offset(skip).limit(limit).all()
    def _ld(l):
        return {"id": l.id, "name": f"{l.first_name or ''} {l.last_name or ''}".strip() or None,
                "email": getattr(l,"email",None), "phone": getattr(l,"phone",None),
                "status": getattr(l,"status",None), "tier": getattr(l,"tier",None),
                "source": getattr(l,"source_file",None), "organization_id": l.organization_id,
                "created_at": l.created_at.isoformat() if getattr(l,"created_at",None) else None}
    return {"total": total, "leads": [_ld(l) for l in leads]}


@router.get("/users")
def god_users(
    role: Optional[str] = Query(None), search: Optional[str] = Query(None),
    scope: str = Query("admins", regex="^(admins|all|internal|tenant)$"),
    skip: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500),
    god: User = Depends(require_god), db: Session = Depends(get_db),
):
    """THE CANONICAL IDENTITY LIST. One row per HUMAN, never per context.

    A person who is a god_admin, sells for EvoSys Pro, and administers a
    customer is ONE row here carrying three contexts — not three rows. That is
    the whole point of the centralized identity model, and a user screen that
    listed them three times would quietly teach the operator otherwise.

    Every context is resolved in GROUPED queries: one for organizations, one
    for platforms, one for memberships, one for brand-sales org names. Fetching
    them per user is what made the old admin screens 600 statements deep.

    `scope`:
      admins   — god / super / org admins (unchanged default; existing callers)
      internal — organization_id IS NULL: the control plane and brand sales
      tenant   — everyone who belongs to a customer organization
      all      — every account
    """
    from app.models.sales_models import BrandSalesOrg, Membership

    q = db.query(User)
    if role:
        q = q.filter(User.role == role)
    elif scope == "admins":
        q = q.filter(User.role.in_(["god_admin", "super_admin", "org_admin"]))
    elif scope == "internal":
        q = q.filter(User.organization_id.is_(None))
    elif scope == "tenant":
        q = q.filter(User.organization_id.isnot(None))
    if search:
        q = q.filter(User.email.ilike(f"%{search}%") | User.full_name.ilike(f"%{search}%"))
    total = q.count()
    users = q.order_by(User.email).offset(skip).limit(limit).all()

    # ── contexts, resolved once for the whole page ─────────────────────────
    org_ids = sorted({u.organization_id for u in users if u.organization_id})
    orgs = {}
    if org_ids:
        orgs = {o.id: o for o in
                db.query(Organization).filter(Organization.id.in_(org_ids)).all()}
    plat_ids = sorted({o.platform_id for o in orgs.values() if o.platform_id})
    platforms = {}
    if plat_ids:
        platforms = {p.id: p for p in
                     db.query(Platform).filter(Platform.id.in_(plat_ids)).all()}

    user_ids = sorted({u.id for u in users})
    mems_by_user = {}
    scope_names = {}
    if user_ids:
        try:
            rows = (db.query(Membership)
                      .filter(Membership.user_id.in_(user_ids)).all())
            for m in rows:
                mems_by_user.setdefault(m.user_id, []).append(m)
            bs_ids = sorted({m.scope_id for m in rows
                             if m.scope_type == "brand_sales_org"})
            if bs_ids:
                for b in db.query(BrandSalesOrg).filter(
                        BrandSalesOrg.id.in_(bs_ids)).all():
                    scope_names[b.id] = b.name
            m_org_ids = sorted({m.scope_id for m in rows
                                if m.scope_type == "organization"
                                and m.scope_id not in orgs})
            if m_org_ids:
                for o in db.query(Organization).filter(
                        Organization.id.in_(m_org_ids)).all():
                    scope_names[o.id] = o.name
        except Exception as e:                               # pragma: no cover
            log.warning("god_users membership lookup failed: %s", e)

    def _ud(u):
        org = orgs.get(u.organization_id) if u.organization_id else None
        plat = platforms.get(org.platform_id) if org and org.platform_id else None
        mems = mems_by_user.get(u.id, [])
        return {
            # ── existing contract, unchanged ──
            "id": u.id, "email": u.email, "name": getattr(u, "full_name", None),
            "role": u.role, "is_active": getattr(u, "is_active", True),
            "organization_id": getattr(u, "organization_id", None),
            "created_at": u.created_at.isoformat() if getattr(u, "created_at", None) else None,
            # ── added: the contexts this one identity holds ──
            "full_name": getattr(u, "full_name", None),
            "organization_name": org.name if org else None,
            "platform_id": plat.id if plat else None,
            "platform_name": plat.name if plat else None,
            # NULL organization_id is this architecture's positive assertion
            # that somebody belongs to the control plane and to no tenant.
            "is_internal": u.organization_id is None,
            "must_change_password": bool(getattr(u, "must_change_password", False)),
            "last_login_at": (u.last_login_at.isoformat()
                              if getattr(u, "last_login_at", None) else None),
            "memberships": [
                {"id": m.id, "scope_type": m.scope_type, "scope_id": m.scope_id,
                 "scope_name": scope_names.get(m.scope_id),
                 "role": m.role, "is_active": bool(m.is_active)}
                for m in mems
            ],
        }

    return {"total": total, "scope": scope, "users": [_ud(u) for u in users]}


@router.patch("/users/{user_id}/role")
def god_set_role(user_id: str, body: RolePatch, god: User = Depends(require_god), db: Session = Depends(get_db)):
    if body.role not in ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Allowed: {sorted(ALLOWED_ROLES)}")
    target = db.query(User).filter(User.id == user_id).first()
    if not target: raise HTTPException(status_code=404, detail="User not found")
    if target.id == god.id and body.role != "god_admin":
        raise HTTPException(status_code=400, detail="Cannot demote your own god_admin account.")
    old_role = target.role; target.role = body.role; db.commit()
    log.info("AUDIT: god_admin %s changed user %s (%s) role: %s → %s", god.email, target.email, user_id, old_role, body.role)
    return {"user_id": user_id, "email": target.email, "old_role": old_role, "new_role": body.role}


@router.post("/users/{user_id}/deactivate")
def god_deactivate_user(user_id: str, god: User = Depends(require_god), db: Session = Depends(get_db)):
    if user_id == god.id: raise HTTPException(status_code=400, detail="Cannot deactivate your own account.")
    target = db.query(User).filter(User.id == user_id).first()
    if not target: raise HTTPException(status_code=404, detail="User not found")
    target.is_active = False; db.commit()
    log.info("AUDIT: god_admin %s deactivated user %s (%s)", god.email, target.email, user_id)
    return {"user_id": user_id, "email": target.email, "is_active": False}


@router.post("/users/{user_id}/activate")
def god_activate_user(user_id: str, god: User = Depends(require_god), db: Session = Depends(get_db)):
    target = db.query(User).filter(User.id == user_id).first()
    if not target: raise HTTPException(status_code=404, detail="User not found")
    target.is_active = True; db.commit()
    log.info("AUDIT: god_admin %s activated user %s (%s)", god.email, target.email, user_id)
    return {"user_id": user_id, "email": target.email, "is_active": True}


@router.post("/orgs/{org_id}/impersonate")
def god_impersonate_org(org_id: str, god: User = Depends(require_god), db: Session = Depends(get_db)):
    """ENTER ORGANIZATION — establishes a god-mode tenant session with full audit trail."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org: raise HTTPException(status_code=404, detail="Organization not found")
    session_id = secrets.token_urlsafe(16)
    entered_at = datetime.utcnow().isoformat()
    log.info("AUDIT: GOD_ENTER_ORG | admin=%s | org_id=%s | org_name=%s | session=%s | entered_at=%s",
             god.email, org_id, org.name, session_id, entered_at)
    return {"org_id": org_id, "org_name": org.name, "org_slug": getattr(org,"slug",None),
            "org_plan": getattr(org,"plan","trial"), "is_active": org.is_active,
            "session_id": session_id, "entered_at": entered_at, "god_email": god.email,
            "header_name": "X-Org-Override", "header_value": org_id}


@router.post("/orgs/{org_id}/exit-session")
def god_exit_org_session(org_id: str, god: User = Depends(require_god), db: Session = Depends(get_db)):
    """Record exit from a God Mode tenant session."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    org_name = org.name if org else org_id
    log.info("AUDIT: GOD_EXIT_ORG | admin=%s | org_id=%s | org_name=%s | exited_at=%s",
             god.email, org_id, org_name, datetime.utcnow().isoformat())
    return {"status": "exited", "org_id": org_id}


# ── Voice agent configuration ────────────────────────────────────────────────
# organization -> provider -> agent -> outbound number.
#
# The smallest operational surface that makes the mapping creatable without a
# Render shell, a seed script, or a direct database insert — all three of which
# are standing prohibitions. Adding a second Retell agent later must cost one
# row through here, not a deploy.
#
# God-only, and it NEVER returns a credential: the per-org API key override is
# reported as configured / not configured, never by value. That mirrors
# /god/twilio-diagnostics, which was written to the same rule.

class VoiceAgentCreate(BaseModel):
    organization_id: str
    agent_id: str
    from_number: str
    provider: str = "retell"
    use_case: str = "file_check"
    label: Optional[str] = None
    # Which published version of that agent this organization runs. Optional
    # here only so a mapping can be created and pinned in two steps; a mapping
    # with no version cannot place a call.
    agent_version: Optional[int] = None


class VoiceAgentVersionUpdate(BaseModel):
    """Repoint one mapping at a different published agent version.

    Exists so changing which version a customer runs costs one API call rather
    than a deploy. The version is configuration; no version number is written
    in the provider or the orchestrator.
    """
    agent_version: int


def _voice_agent_row(cfg, org_name=None, ready=None, why=None):
    return {
        "id": cfg.id,
        "organization_id": cfg.organization_id,
        "organization_name": org_name,
        "provider": cfg.provider,
        "agent_id": cfg.agent_id,
        # Reported plainly, including when it is missing, because an unpinned
        # mapping looks identical to a pinned one everywhere else and yet
        # cannot place a call.
        "agent_version": getattr(cfg, "agent_version", None),
        "version_pinned": getattr(cfg, "agent_version", None) is not None,
        "from_number": cfg.from_number,
        "use_case": cfg.use_case,
        "label": cfg.label,
        "is_active": bool(cfg.is_active),
        # Never the value. Only whether one exists.
        "org_api_key_override": bool(cfg.api_key_encrypted),
        "provider_ready": ready,
        "provider_not_ready_reason": why,
        "created_at": cfg.created_at.isoformat() if cfg.created_at else None,
    }


@router.get("/voice/agents")
def god_list_voice_agents(god: User = Depends(require_god),
                          db: Session = Depends(get_db)):
    """Every voice agent mapping, with a live readiness check per row."""
    from app.models.models import VoiceAgentConfig
    from app.services.comms import get_voice_provider

    rows = (db.query(VoiceAgentConfig)
            .order_by(VoiceAgentConfig.created_at.desc()).all())
    org_names = {}
    if rows:
        ids = list({r.organization_id for r in rows})
        for oid, name in db.query(Organization.id, Organization.name).filter(
                Organization.id.in_(ids)).all():
            org_names[oid] = name

    out = []
    for cfg in rows:
        ready, why = None, None
        try:
            ready, why = get_voice_provider(db, cfg).is_ready()
        except Exception as exc:                                  # noqa: BLE001
            ready, why = False, str(exc)[:200]
        out.append(_voice_agent_row(cfg, org_names.get(cfg.organization_id),
                                    ready, why))
    return {"count": len(out), "agents": out}


@router.post("/voice/agents", status_code=201)
def god_create_voice_agent(req: VoiceAgentCreate,
                           god: User = Depends(require_god),
                           db: Session = Depends(get_db)):
    """Create one mapping. Refuses to duplicate an existing active one.

    The duplicate guard is on (organization_id, use_case, provider) rather than
    on the whole row: two active agents for the same org and use case would
    make `active_voice_config` pick one arbitrarily, and "arbitrarily" is not a
    property you want deciding which agent phones a family.
    """
    from app.models.models import VoiceAgentConfig
    from app.services.comms import get_voice_provider

    org = db.query(Organization).filter(
        Organization.id == req.organization_id).first()
    if org is None:
        raise HTTPException(404, "Organization not found.")

    existing = (db.query(VoiceAgentConfig)
                .filter(VoiceAgentConfig.organization_id == req.organization_id,
                        VoiceAgentConfig.use_case == req.use_case,
                        VoiceAgentConfig.provider == req.provider,
                        VoiceAgentConfig.is_active.is_(True))
                .first())
    if existing is not None:
        # Idempotent by design: re-running setup must not create a second row.
        ready, why = None, None
        try:
            ready, why = get_voice_provider(db, existing).is_ready()
        except Exception as exc:                                  # noqa: BLE001
            ready, why = False, str(exc)[:200]
        return {
            "created": False,
            "reason": "An active mapping already exists for this organization, "
                      "use case and provider.",
            "agent": _voice_agent_row(existing, org.name, ready, why),
        }

    cfg = VoiceAgentConfig(
        organization_id=req.organization_id,
        provider=req.provider,
        agent_id=req.agent_id,
        agent_version=req.agent_version,
        from_number=req.from_number,
        use_case=req.use_case,
        label=req.label,
        is_active=True,
        created_by=god.id,
    )
    db.add(cfg)
    db.commit()
    db.refresh(cfg)

    log.info("AUDIT: GOD_VOICE_AGENT_CREATED | admin=%s | org=%s | provider=%s "
             "| agent=%s | use_case=%s",
             god.email, req.organization_id, req.provider, req.agent_id,
             req.use_case)

    ready, why = None, None
    try:
        ready, why = get_voice_provider(db, cfg).is_ready()
    except Exception as exc:                                      # noqa: BLE001
        ready, why = False, str(exc)[:200]
    return {"created": True,
            "agent": _voice_agent_row(cfg, org.name, ready, why)}


@router.patch("/voice/agents/{config_id}/version")
def god_set_voice_agent_version(config_id: str,
                                req: VoiceAgentVersionUpdate,
                                god: User = Depends(require_god),
                                db: Session = Depends(get_db)):
    """Pin which published agent version this mapping runs.

    THE POINT OF THE ROUTE. Version selection has to be configuration, because
    the alternative is a deploy every time a vendor agent is republished — and
    a deploy is exactly the pressure that produces a hard-coded number. Nothing
    in the provider or the orchestrator names a version; both read this column.

    A negative version is rejected rather than stored: the provider would
    refuse it later anyway, and a value that can never work should not be
    accepted while a human is looking at the result.
    """
    from app.models.models import VoiceAgentConfig
    from app.services.comms import get_voice_provider

    cfg = (db.query(VoiceAgentConfig)
           .filter(VoiceAgentConfig.id == config_id).first())
    if cfg is None:
        raise HTTPException(404, "Voice agent mapping not found.")
    if req.agent_version < 0:
        raise HTTPException(400, "agent_version must be zero or greater.")

    previous = getattr(cfg, "agent_version", None)
    cfg.agent_version = req.agent_version
    db.commit()
    db.refresh(cfg)

    org = db.query(Organization).filter(
        Organization.id == cfg.organization_id).first()

    log.info("AUDIT: GOD_VOICE_AGENT_VERSION_PINNED | admin=%s | org=%s "
             "| agent=%s | use_case=%s | from=%s | to=%s",
             god.email, cfg.organization_id, cfg.agent_id, cfg.use_case,
             previous, cfg.agent_version)

    ready, why = None, None
    try:
        ready, why = get_voice_provider(db, cfg).is_ready()
    except Exception as exc:                                      # noqa: BLE001
        ready, why = False, str(exc)[:200]
    return {"updated": True,
            "previous_version": previous,
            "agent": _voice_agent_row(cfg, org.name if org else None,
                                      ready, why)}


class VoiceTestCall(BaseModel):
    lead_id: str
    organization_id: str
    use_case: str = "file_check"


@router.post("/voice/test-call", status_code=201)
def god_place_voice_call(req: VoiceTestCall,
                         god: User = Depends(require_god),
                         db: Session = Depends(get_db)):
    """Place ONE outbound voice call for a lead. God-only.

    The smallest surface that can exercise the outbound path at all — there is
    no other way to start a call, and the legacy Twilio voice router is
    deliberately fail-closed and must not be used.

    It adds NO logic of its own: eligibility, suppression, the attempt cap and
    provider readiness are all decided by `voice_orchestrator`, exactly as they
    would be for any other caller. A refusal comes back as 409 with the reason
    the orchestrator gave, so "why won't it call?" is answerable without
    reading logs.
    """
    from app.models.models import Lead
    from app.services.voice_orchestrator import (check_call_eligibility,
                                                 start_file_check_call)

    lead = db.query(Lead).filter(Lead.id == req.lead_id).first()
    if lead is None:
        raise HTTPException(404, "Lead not found.")

    # Report the refusal reason rather than a bare 403 — this endpoint exists
    # to make the outbound path debuggable.
    elig = check_call_eligibility(db, lead, req.organization_id, req.use_case)
    if not elig.ok:
        raise HTTPException(409, "Call refused: %s (%s)" % (elig.reason, elig.code))

    call = start_file_check_call(db, lead, req.organization_id,
                                 use_case=req.use_case)
    log.info("AUDIT: GOD_VOICE_TEST_CALL | admin=%s | org=%s | lead=%s | "
             "call=%s | provider_call_id=%s | status=%s",
             god.email, req.organization_id, req.lead_id, call.id,
             call.provider_call_id, call.status)
    return {
        "call_id": call.id,
        "provider": call.provider,
        "provider_call_id": call.provider_call_id,
        "agent_id": call.agent_id,
        "from_phone": call.from_phone,
        "to_phone": call.to_phone,
        "status": call.status,
        "outcome": call.outcome,
        "error_message": call.error_message,
    }


@router.get("/voice/calls/{call_id}")
def god_get_voice_call(call_id: str, god: User = Depends(require_god),
                       db: Session = Depends(get_db)):
    """Read one call back, for watching a lifecycle land."""
    from app.models.models import VoiceCall
    c = db.query(VoiceCall).filter(VoiceCall.id == call_id).first()
    if c is None:
        raise HTTPException(404, "Call not found.")
    return {
        "id": c.id, "organization_id": c.organization_id, "lead_id": c.lead_id,
        "provider": c.provider, "provider_call_id": c.provider_call_id,
        "agent_id": c.agent_id, "direction": c.direction,
        "from_phone": c.from_phone, "to_phone": c.to_phone,
        "status": c.status, "outcome": c.outcome,
        "disconnect_reason": c.disconnect_reason,
        "started_at": c.started_at.isoformat() if c.started_at else None,
        "answered_at": c.answered_at.isoformat() if c.answered_at else None,
        "ended_at": c.ended_at.isoformat() if c.ended_at else None,
        "duration_seconds": c.duration_seconds,
        "transcript": c.transcript,
        "transcript_chars": len(c.transcript or ""),
        "summary": c.summary,
        "analysis_json": c.analysis_json,
        "booking_link_id": c.booking_link_id,
        "callback_at": c.callback_at.isoformat() if c.callback_at else None,
        "transfer_requested": bool(c.transfer_requested),
        "transfer_status": c.transfer_status,
        "error_message": c.error_message,
    }
