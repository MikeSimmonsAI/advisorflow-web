"""Who is importing, into which organization, in what capacity.

Every intake route resolves this FIRST. The organization comes from
`platform_owner.tenant_write_org_id`, which is the platform's one refusal for
"a write with no customer selected": a customer admin is always in their own
organization; a God admin must have explicitly entered a customer
(X-Org-Override) or the request is refused with 409. There is no default
organization and no fallback to the platform pseudo-org.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.services.platform_owner import is_platform_owner, tenant_write_org_id


@dataclass
class IntakeContext:
    org_id: str
    org_name: str
    org_slug: Optional[str]
    actor_id: str
    actor_name: str
    actor_email: Optional[str]
    role: str
    acting_as_platform_owner: bool

    def audit_details(self) -> dict:
        return {
            "imported_by": self.actor_name,
            "imported_by_email": self.actor_email,
            "role": self.role,
            "acting_as_platform_owner": self.acting_as_platform_owner,
            "acting_organization": self.org_name,
            "acting_organization_id": self.org_id,
        }

    def payload(self) -> dict:
        return {
            "organization_id": self.org_id,
            "organization_name": self.org_name,
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
            "role": self.role,
            "acting_as_platform_owner": self.acting_as_platform_owner,
        }


def resolve(db: Session, user: User) -> IntakeContext:
    if is_platform_owner(user):
        org_id = tenant_write_org_id(user)      # 409 when no customer is selected
    else:
        # A customer user imports into the workspace this request is in - the
        # platform's one seam for that answer - and nowhere else.
        from app.services.lead_scope import active_workspace_org_id
        from app.services.platform_owner import is_platform_pseudo_org
        org_id = active_workspace_org_id(user, db)
        if not org_id or is_platform_pseudo_org(org_id):
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail=(
                "No customer organization is selected. Imports always belong to one "
                "organization; select it first."))
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Organization not found.")
    name = getattr(user, "full_name", None) or getattr(user, "email", None) or user.id
    return IntakeContext(
        org_id=org.id,
        org_name=org.name or org.brand_name,
        org_slug=org.slug,
        actor_id=user.id,
        actor_name=name,
        actor_email=getattr(user, "email", None),
        role=getattr(user, "role", None) or "user",
        acting_as_platform_owner=is_platform_owner(user),
    )
