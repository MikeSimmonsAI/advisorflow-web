"""THE CUSTOMER LAUNCH EXPERIENCE — HTTP.

THE ACCESS RULE IS THE LAUNCH ENGINE'S, UNCHANGED
=================================================
The customer route takes no organization id. It is `/launch-experience/me`,
and the workspace is resolved from the session, for the same reason
`/launch/me` is: a `/launch-experience/{org_id}` route is a customer
enumeration endpoint with a UUID for a lock.

PREVIEW IS NOT IMPERSONATION
============================
`/launch-experience/preview/{organization_id}` is the internal view of exactly
what a customer will see, and it is safe for three structural reasons rather
than three promises:

  IT RUNS THE SAME COMPOSER. There is no second rendering path that could
  drift from the customer's own, and nothing to keep in step.

  THE COMPOSER IS READ-ONLY. `launch_experience.compose` and everything it
  calls read rows and return a payload. There is no write behind the preview
  because there is no write behind the customer's read either.

  NOBODY IS LOGGED IN AS ANYBODY. The caller stays themselves — their own
  token, their own audit identity. No customer session is minted, no
  invitation is sent, and no activity is attributed to a customer who has not
  opened anything.

The payload is marked `preview: true` so the shell can say so on screen. An
operator looking at a customer's onboarding must never be unsure whether the
customer has seen it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.implementation_models import Implementation
from app.models.launch_experience_models import (
    EXPERIENCE_SCOPES, LaunchExperienceConfig, SCOPE_BRAND, SCOPE_INDUSTRY,
    SCOPE_LABELS, SCOPE_ORGANIZATION, SCOPE_PLATFORM_DEFAULT,
)
from app.models.models import Organization, User
from app.models.sales_models import BrandSalesOrg
from app.routers.audit_log_router import log_action
from app.services import industry_templates, launch_experience
from app.services.sales_access import is_god, sales_memberships

log = logging.getLogger("launch_experience_router")

router = APIRouter(prefix="/launch-experience", tags=["Launch Experience"])


# ── scope ───────────────────────────────────────────────────────────────────

def _actor_platform_ids(db: Session, actor: User) -> set:
    ids = set()
    pid = getattr(actor, "platform_id", None)
    if pid:
        ids.add(pid)
    scope_ids = [m.scope_id for m in sales_memberships(actor, db)]
    if scope_ids:
        rows = (db.query(BrandSalesOrg)
                .filter(BrandSalesOrg.id.in_(scope_ids)).all())
        ids.update(r.platform_id for r in rows if r.platform_id)
    return ids


def _staff_org(db: Session, actor: User, organization_id: str) -> Organization:
    """404, never 403 — an id the caller may not touch must not be confirmed."""
    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    if org is None:
        raise HTTPException(status_code=404, detail="Customer not found.")
    if is_god(actor):
        return org
    if org.platform_id and org.platform_id in _actor_platform_ids(db, actor):
        return org
    raise HTTPException(status_code=404, detail="Customer not found.")


def _impl_for(db: Session, org: Organization) -> Implementation:
    impl = (db.query(Implementation)
            .filter(Implementation.organization_id == org.id).first())
    if impl is None:
        raise HTTPException(
            status_code=404,
            detail="This customer has no launch record yet. One is created "
                   "when their implementation starts.")
    return impl


def _caller_org_id(user: User, db: Session, request: Request) -> str:
    from app.services.lead_scope import active_workspace_org_id
    org_id = active_workspace_org_id(user, db, request)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No customer workspace is selected for this session.")
    return org_id


# ── the customer's own experience ───────────────────────────────────────────


@router.get("/me")
def my_experience(request: Request,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)) -> dict:
    """Everything the customer's onboarding shell renders, in ONE response.

    The launch payload and the experience come back together deliberately. The
    shell needs both to draw a single frame, and two requests means a first
    paint with a brand, a customer and a progress bar but no identity, copy or
    imagery — which is exactly the flash of a generic admin form this design
    exists to replace.
    """
    from app.routers.launch_router import _payload as launch_payload

    org_id = _caller_org_id(user, db, request)
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    impl = _impl_for(db, org)
    payload = launch_payload(db, impl, org, user)
    payload["experience"] = launch_experience.compose(db, impl, org, user,
                                                      preview=False)
    return payload


# ── the internal preview ────────────────────────────────────────────────────


@router.get("/preview/{organization_id}")
def preview_experience(organization_id: str,
                       db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)) -> dict:
    """See exactly what this customer will see. Changes nothing.

    The audit entry records that somebody LOOKED. It is written against the
    organization so an operator reviewing a customer's history can tell a
    preview apart from the customer's own first visit — which is precisely the
    confusion an unlogged preview would create.
    """
    from app.routers.launch_router import _payload as launch_payload

    org = _staff_org(db, user, organization_id)
    impl = _impl_for(db, org)

    # THE SAME PAYLOAD BUILDER THE CUSTOMER'S OWN ROUTE USES, with `user` left
    # as None — the customer's identity block is not filled in with the
    # operator's name, because the operator is not the customer and the preview
    # must not quietly show them their own initials in the customer's avatar.
    payload = launch_payload(db, impl, org, None)
    payload["experience"] = launch_experience.compose(db, impl, org, None,
                                                      preview=True)

    # THE DELIVERY PANEL, COMPOSED HERE RATHER THAN FETCHED BY THE SHELL.
    #
    # The customer's own shell asks `/launch/me/delivery` for this, and that
    # route resolves the workspace from the session — so in a preview it would
    # answer with the OPERATOR's delivery state and quietly paint another
    # customer's integrations into this customer's page. Composing it here
    # from the previewed implementation is what makes the preview exact
    # instead of merely similar, and `customer_view` is the same read-only
    # builder the customer's route calls.
    from app.services import launch_delivery
    try:
        payload["delivery"] = launch_delivery.customer_view(db, impl)
    except Exception:                                        # pragma: no cover
        log.warning("preview delivery failed for %s", impl.id, exc_info=True)
        payload["delivery"] = None

    try:
        log_action(db, org.id, user.id,
                   action="launch_experience_previewed",
                   target_type="implementation", target_id=impl.id,
                   platform_id=org.platform_id,
                   details={"read_only": True, "customer_notified": False},
                   commit=True)
    except Exception:                                        # pragma: no cover
        # A preview that cannot be logged is still a preview that changed
        # nothing. Never fail the read for the sake of the note about it.
        log.warning("preview audit failed for %s", org.id, exc_info=True)

    payload["preview_context"] = {
        "organization_id": org.id,
        "organization_name": org.name,
        "viewed_by": getattr(user, "full_name", None) or getattr(user, "email", None),
        "read_only": True,
        "customer_notified": False,
        # The customer's typed answers are NOT in this payload and the shell
        # says so on screen. A preview is for judging the experience; reading
        # what a customer wrote is the staff review screen, which is where an
        # operator's access to it is already scoped and already audited.
        "answers_included": False,
        "note": "This is the customer's own onboarding experience, rendered "
                "from their real configuration and real progress. Nothing on "
                "this screen was created by opening it, and no invitation was "
                "sent.",
    }
    return payload


# ── configuration ───────────────────────────────────────────────────────────
#
# WHO MAY EDIT WHICH LAYER, AND WHY THE ANSWER DIFFERS
#
#   platform_default   god only. It is what every unconfigured customer on
#                      every brand sees.
#   industry           god only. An industry template is platform policy, not
#                      one brand's preference.
#   brand              god, or an operator of that brand.
#   organization       god, or an operator of the brand that owns the
#                      customer.
#
# A CUSTOMER MAY NOT EDIT THEIR OWN EXPERIENCE CONFIGURATION. They fill in
# their onboarding; the shell it renders in belongs to the brand delivering it.


class ExperienceConfigBody(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    presentation: Optional[Dict[str, Any]] = None
    journey: Optional[List[Dict[str, Any]]] = None
    form: Optional[Dict[str, Any]] = None
    is_active: bool = True


def _assert_may_edit(db: Session, actor: User, scope_type: str,
                     scope_id: Optional[str]) -> None:
    if is_god(actor):
        return
    if scope_type in (SCOPE_PLATFORM_DEFAULT, SCOPE_INDUSTRY):
        raise HTTPException(
            status_code=403,
            detail="Platform and industry templates are configured by the "
                   "platform owner.")
    if scope_type == SCOPE_BRAND:
        if scope_id and scope_id in _actor_platform_ids(db, actor):
            return
        raise HTTPException(status_code=404, detail="Brand not found.")
    if scope_type == SCOPE_ORGANIZATION:
        _staff_org(db, actor, scope_id or "")
        return
    raise HTTPException(status_code=400, detail="Unknown configuration scope.")


def _public(row: LaunchExperienceConfig) -> dict:
    return {
        "id": row.id,
        "scope_type": row.scope_type,
        "scope_id": row.scope_id,
        "scope_label": SCOPE_LABELS.get(row.scope_type, row.scope_type),
        "name": row.name,
        "description": row.description,
        "presentation": row.presentation,
        "journey": row.journey,
        "form": row.form,
        "is_active": bool(row.is_active),
        "updated_at": row.updated_at,
    }


@router.get("/config")
def list_configs(db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)) -> dict:
    """Every layer this caller may see, plus the defaults they sit on top of."""
    query = db.query(LaunchExperienceConfig)
    rows = query.order_by(LaunchExperienceConfig.scope_type,
                          LaunchExperienceConfig.scope_id).all()
    if not is_god(user):
        mine = _actor_platform_ids(db, user)
        allowed = []
        for row in rows:
            if row.scope_type == SCOPE_BRAND and row.scope_id in mine:
                allowed.append(row)
            elif row.scope_type == SCOPE_ORGANIZATION:
                org = (db.query(Organization)
                       .filter(Organization.id == row.scope_id).first())
                if org is not None and org.platform_id in mine:
                    allowed.append(row)
            elif row.scope_type in (SCOPE_PLATFORM_DEFAULT, SCOPE_INDUSTRY):
                allowed.append(row)
        rows = allowed

    return {
        "configs": [_public(r) for r in rows],
        "scopes": list(EXPERIENCE_SCOPES),
        "industries": industry_templates.choices(),
        "defaults": {
            "presentation": launch_experience.DEFAULT_PRESENTATION,
            "journey": launch_experience.DEFAULT_JOURNEY,
            "form": launch_experience.DEFAULT_FORM,
        },
    }


@router.get("/config/resolved")
def resolved_config(organization_id: Optional[str] = Query(None),
                    industry: Optional[str] = Query(None),
                    platform_id: Optional[str] = Query(None),
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)) -> dict:
    """What the four layers merge to, and which rows contributed.

    The `layers` list is the point: an operator asking "why does this customer's
    page say that" is told which row to edit rather than left guessing.
    """
    if organization_id:
        org = _staff_org(db, user, organization_id)
        return launch_experience.resolve(
            db, industry=org.industry, platform_id=org.platform_id,
            organization_id=org.id)
    if platform_id and not is_god(user) and platform_id not in _actor_platform_ids(db, user):
        raise HTTPException(status_code=404, detail="Brand not found.")
    return launch_experience.resolve(db, industry=industry,
                                     platform_id=platform_id)


@router.put("/config/{scope_type}/{scope_id}")
def upsert_config(scope_type: str, scope_id: str, body: ExperienceConfigBody,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)) -> dict:
    """Create or replace one layer.

    `scope_id` is a path segment, so the platform default is addressed by the
    literal "default" rather than by an empty path — a route that matches an
    empty segment is a route that also matches things nobody meant.
    """
    if scope_type not in EXPERIENCE_SCOPES:
        raise HTTPException(status_code=400,
                            detail="Unknown configuration scope '%s'." % scope_type)

    resolved_id = None if scope_type == SCOPE_PLATFORM_DEFAULT else scope_id
    if scope_type == SCOPE_INDUSTRY:
        resolved_id = industry_templates.normalize(scope_id)

    _assert_may_edit(db, user, scope_type, resolved_id)

    clause = (LaunchExperienceConfig.scope_id.is_(None)
              if resolved_id is None
              else LaunchExperienceConfig.scope_id == resolved_id)
    row = (db.query(LaunchExperienceConfig)
           .filter(LaunchExperienceConfig.scope_type == scope_type, clause)
           .first())

    before = None if row is None else {
        "name": row.name, "is_active": row.is_active,
        "has_presentation": bool(row.presentation),
        "has_journey": bool(row.journey), "has_form": bool(row.form)}

    if row is None:
        row = LaunchExperienceConfig(scope_type=scope_type, scope_id=resolved_id,
                                     created_by_user_id=user.id)
        db.add(row)

    if body.name is not None:
        row.name = body.name
    if body.description is not None:
        row.description = body.description
    if body.presentation is not None:
        row.presentation = body.presentation
    if body.journey is not None:
        row.journey = body.journey
    if body.form is not None:
        row.form = body.form
    row.is_active = bool(body.is_active)
    row.updated_by_user_id = user.id
    db.flush()

    log_action(db, (resolved_id if scope_type == SCOPE_ORGANIZATION else None),
               user.id,
               action="launch_experience_config_saved",
               target_type="launch_experience_config", target_id=row.id,
               platform_id=(resolved_id if scope_type == SCOPE_BRAND else None),
               before=before,
               after={"scope_type": scope_type, "scope_id": resolved_id,
                      "is_active": row.is_active},
               commit=False)
    db.commit()
    return _public(row)
