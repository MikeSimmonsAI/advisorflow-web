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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.models import Organization, User
from app.services.auth_service import hash_password, create_access_token

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:40]


class OnboardingRegisterRequest(BaseModel):
    business_name: str
    admin_full_name: str
    admin_email: EmailStr
    admin_password: str
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
def register_org(
    req: OnboardingRegisterRequest,
    db: Session = Depends(get_db),
):
    """
    Creates a new org + admin user in one transaction.
    Returns a JWT so the caller is immediately authenticated.
    """
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
        created_at=datetime.utcnow(),
    )
    db.add(user)

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not create account. Please try again.")

    token = create_access_token({"sub": user_id})

    return OnboardingRegisterResponse(
        access_token=token,
        org_id=org_id,
        org_name=org.name,
        user_id=user_id,
        user_email=user.email,
    )
