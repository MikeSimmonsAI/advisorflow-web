"""
Self-serve onboarding — lets a new business sign up and get a working
BookaBoost account without Mike manually creating anything.

Flow:
  POST /onboarding/register
    - Creates Organization + first admin User in one transaction
    - Returns a JWT so the user is immediately logged in
    - Plan defaults to "trial"

  GET /onboarding/check-slug?slug=restland
    - Returns whether a slug is available

No auth required on these endpoints — they are public.
"""

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.deps import get_db
from app.limiter import limiter
from app.models.models import Organization, User
from app.services.auth_service import hash_password, create_access_token

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:40]


class OnboardingRegisterRequest(BaseModel):
    business_name: str = Field(..., min_length=1, max_length=200)
    admin_full_name: str = Field(..., min_length=1, max_length=200)
    admin_email: EmailStr
    admin_password: str = Field(..., min_length=8)
    industry: str = "funeral"  # funeral, roofing, insurance, real_estate, dental, legal


class OnboardingRegisterResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    org_id: str
    org_name: str
    user_id: str
    user_email: str


@router.get("/check-slug")
def check_slug(slug: str, db: Session = Depends(get_db)):
    existing = db.query(Organization).filter(Organization.slug == slug).first()
    return {"slug": slug, "available": existing is None}


@router.post("/register", response_model=OnboardingRegisterResponse)
@limiter.limit("5/hour")
def register_org(
    request: Request,
    req: OnboardingRegisterRequest,
    db: Session = Depends(get_db),
):
    """RETIRED — 410 Gone. Customer creation goes through provisioning.

    This route was public, unauthenticated, and created a customer organization
    with:

      · NO platform_id — an organization belonging to no brand, which sits
        outside every scoping decision in the system. It would not appear in the
        customer list of the operator who was supposed to own it, and
        `get_platform_org_ids` would never return it.
      · a caller-chosen password with `must_change_password = False`, bypassing
        the one-time activation-link discipline every other account-creation
        path in this codebase enforces.
      · no entitlement, no location, no readiness check, and no audit row.

    Rate limiting is not the control here. Five orgs an hour from anywhere on
    the internet, each invisible to its own brand, is not a signup funnel — it
    is an unattended door into the tenant table.

    The supported path is POST /god/customers (customers_router), which requires
    a brand, records who created it, starts the customer with no features
    enabled, and hands over access by a one-time link.

    The route is kept rather than deleted so that anything still pointing at it
    gets a specific, greppable 410 explaining where to go, instead of a 404 that
    looks like a deploy problem.
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "Self-service organization signup has been retired. It created "
            "organizations with no brand, which are invisible to every scoped "
            "query in the platform. Customers are now provisioned by an operator "
            "through the customer provisioning flow."
        ),
    )


def _register_org_retired_implementation(
    request: Request,
    req: OnboardingRegisterRequest,
    db: Session,
):
    """The former body of register_org. Unreachable; kept for reference only."""
    if len(req.admin_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    if len(req.business_name.strip()) < 2:
        raise HTTPException(status_code=400, detail="Business name is too short.")
    if len(req.admin_full_name.strip()) < 2:
        raise HTTPException(status_code=400, detail="Full name is too short.")

    # Check email uniqueness
    existing_user = db.query(User).filter(User.email == req.admin_email.lower()).first()
    if existing_user:
        raise HTTPException(status_code=409, detail="An account with that email already exists.")

    # Generate unique slug
    base_slug = _slugify(req.business_name)
    slug = base_slug
    suffix = 1
    while db.query(Organization).filter(Organization.slug == slug).first():
        slug = f"{base_slug}-{suffix}"
        suffix += 1

    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    org = Organization(
        id=org_id,
        name=req.business_name.strip(),
        slug=slug,
        plan="trial",
        is_active=True,
        created_at=datetime.utcnow(),
    )
    db.add(org)

    user = User(
        id=user_id,
        organization_id=org_id,
        email=req.admin_email.lower(),
        password_hash=hash_password(req.admin_password),
        full_name=req.admin_full_name.strip(),
        role="org_admin",
        is_active=True,
        must_change_password=False,  # self-service signup — user set their own password
        created_at=datetime.utcnow(),
    )
    db.add(user)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not create account. Please try again.")

    # Refresh user from DB so SQLAlchemy populates all fields (id, role, etc.)
    # before passing to create_access_token, which expects a User model object.
    db.refresh(user)
    token = create_access_token(user, db)

    return OnboardingRegisterResponse(
        access_token=token,
        org_id=org_id,
        org_name=org.name,
        user_id=user_id,
        user_email=user.email,
    )
