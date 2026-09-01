"""Qualification — who is actually qualified for this campaign.

Two surfaces, deliberately separated:

    /qualification/preview     ANY authorized user, over their OWN scope.
                               An advisor qualifies their own book, a manager
                               the workspace they are standing in. Read-only.

    /qualification/rules       ORG ADMIN and above. The organization's own
                               definition of a valuable lead - which is data,
                               not code, so no industry is written into the
                               platform.

There is no endpoint here that takes an organization id, an advisor id or a
lead id and returns something the caller was not already entitled to. Every
read starts from `lead_scope.authorized_lead_query`, and `require_tenant_user`
keeps a brand-sales identity out of customer data entirely.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_tenant_user, require_admin
from app.models.models import User
from app.models.qualification_models import (
    QualificationRule, RULE_EFFECTS, RULE_OPERATORS,
)
from app.routers.audit_log_router import log_action
from app.services import lead_scope, qualification

router = APIRouter(prefix="/qualification", tags=["qualification"])


# ── preview ─────────────────────────────────────────────────────────────────

class PreviewRequest(BaseModel):
    channel: str = qualification.CHANNEL_EMAIL
    filters: Optional[Dict[str, Any]] = None
    lead_ids: Optional[List[str]] = None
    include_leads: bool = False


@router.post("/preview")
def preview(req: PreviewRequest, request: Request,
            db: Session = Depends(get_db),
            current_user: User = Depends(require_tenant_user)):
    """WHO IS QUALIFIED, and why not for the rest.

    Returns TOTAL SELECTED / READY / REVIEW / EXCLUDED with the reason
    breakdown and the HIGH / MEDIUM / LOW split, so the person about to send
    can choose a population rather than accept the largest one the filter
    happened to produce.

    Sends nothing. Writes nothing.
    """
    return qualification.qualify_leads(
        db, current_user,
        channel=req.channel,
        filters=req.filters,
        lead_ids=req.lead_ids,
        request=request,
        include_leads=req.include_leads,
    )


@router.get("/preview")
def preview_get(request: Request,
                channel: str = Query(default=qualification.CHANNEL_EMAIL),
                advisor_id: Optional[str] = Query(default=None),
                tier: Optional[str] = Query(default=None),
                status: Optional[str] = Query(default=None),
                db: Session = Depends(get_db),
                current_user: User = Depends(require_tenant_user)):
    """The same answer for a link or a dashboard tile."""
    filters = {k: v for k, v in
               {"advisor_id": advisor_id, "tier": tier, "status": status}.items() if v}
    return qualification.qualify_leads(db, current_user, channel=channel,
                                       filters=filters or None, request=request)


@router.get("/lead/{lead_id}")
def qualify_single_lead(lead_id: str, request: Request,
                        channel: str = Query(default=qualification.CHANNEL_EMAIL),
                        db: Session = Depends(get_db),
                        current_user: User = Depends(require_tenant_user)):
    """Why is THIS lead in the bucket it is in.

    Loaded through `load_lead_in_scope`, so a lead outside the caller's scope
    is a 404 here exactly as it is everywhere else - this endpoint is not a
    back door for asking questions about somebody else's book.
    """
    lead = lead_scope.load_lead_in_scope(db, current_user, lead_id, request=request)
    org_id = lead_scope.active_workspace_org_id(current_user, db, request)
    ctx = qualification.QualificationContext(db, [lead], org_id,
                                             qualification.org_rules(db, org_id))
    if channel in (qualification.CHANNEL_SMS, qualification.CHANNEL_VOICE) and org_id:
        ctx.suppressed_phones = qualification.load_suppressed_phones(db, org_id)
    return qualification.qualify_one(lead, channel, ctx)


@router.get("/vocabulary")
def vocabulary():
    """The reason codes, buckets, channels and rule primitives, so a client
    renders labels from the server rather than keeping its own copy that
    drifts."""
    return {
        "channels": list(qualification.CHANNELS),
        "authoritative_channels": list(qualification.AUTHORITATIVE_CHANNELS),
        "buckets": list(qualification.BUCKETS),
        "priorities": list(qualification.PRIORITIES),
        "reasons": [{"code": c, "label": l} for c, l in sorted(qualification.REASONS.items())],
        "rule_effects": list(RULE_EFFECTS),
        "rule_operators": list(RULE_OPERATORS),
        "rule_fields": list(qualification.RULE_FIELDS),
        "custom_field_prefix": qualification.CUSTOM_FIELD_PREFIX,
        "thresholds": {"high": qualification.HIGH_THRESHOLD,
                       "medium": qualification.MEDIUM_THRESHOLD},
    }


# ── organization rules ──────────────────────────────────────────────────────
#
# THE ORGANIZATION IS NEVER TAKEN FROM THE REQUEST BODY. It is resolved from the
# caller's active workspace, so an org_admin cannot author a rule into another
# tenant by editing a field, and a rule id from another tenant is a 404 rather
# than an edit.

class RuleIn(BaseModel):
    name: str
    effect: str
    field: str
    operator: str
    value: Optional[str] = None
    reason_label: str
    channel: Optional[str] = None
    points: int = 0
    sort_order: int = 100
    is_active: bool = True


def _org_of(current_user: User, db: Session, request: Request) -> str:
    org_id = lead_scope.active_workspace_org_id(current_user, db, request)
    if not org_id:
        raise HTTPException(status_code=403,
                            detail="This account is not inside a customer organization.")
    return org_id


def _validate(payload: RuleIn):
    if payload.effect not in RULE_EFFECTS:
        raise HTTPException(status_code=400,
                            detail="effect must be one of: %s" % ", ".join(RULE_EFFECTS))
    if payload.operator not in RULE_OPERATORS:
        raise HTTPException(status_code=400,
                            detail="operator must be one of: %s" % ", ".join(RULE_OPERATORS))
    # THE WHITELIST IS ENFORCED AT WRITE TIME, not only at evaluation. A rule
    # that names a field it may not read is refused when it is saved, so it
    # never sits in the table looking legitimate.
    if not qualification.rule_field_is_allowed(payload.field):
        raise HTTPException(
            status_code=400,
            detail=("field must be one of the supported lead fields, or "
                    "'%s<your column>' for a column your organization imported."
                    % qualification.CUSTOM_FIELD_PREFIX))
    if payload.channel and payload.channel not in qualification.CHANNELS:
        raise HTTPException(status_code=400,
                            detail="channel must be one of: %s" % ", ".join(qualification.CHANNELS))
    if not (payload.reason_label or "").strip():
        raise HTTPException(
            status_code=400,
            detail=("reason_label is required - a qualification result whose "
                    "reason is 'a rule matched' is not explainable."))
    if payload.effect in ("boost", "demote") and not payload.points:
        raise HTTPException(status_code=400,
                            detail="a boost or demote rule needs a non-zero points value.")


def _serialize(rule: QualificationRule) -> Dict[str, Any]:
    return {
        "id": rule.id, "name": rule.name, "channel": rule.channel,
        "effect": rule.effect, "points": rule.points, "field": rule.field,
        "operator": rule.operator, "value": rule.value,
        "reason_label": rule.reason_label, "sort_order": rule.sort_order,
        "is_active": rule.is_active,
        "created_at": rule.created_at, "updated_at": rule.updated_at,
    }


@router.get("/rules")
def list_rules(request: Request, db: Session = Depends(get_db),
               current_user: User = Depends(require_tenant_user)):
    """Readable by any authorized user in the workspace: a person looking at a
    reason on their own screen should be able to see the rule that produced it."""
    org_id = _org_of(current_user, db, request)
    rows = (db.query(QualificationRule)
            .filter(QualificationRule.organization_id == org_id)
            .order_by(QualificationRule.sort_order.asc(),
                      QualificationRule.created_at.asc()).all())
    return [_serialize(r) for r in rows]


@router.post("/rules")
def create_rule(payload: RuleIn, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(require_admin)):
    org_id = _org_of(current_user, db, request)
    _validate(payload)
    rule = QualificationRule(
        organization_id=org_id, name=payload.name.strip(),
        channel=(payload.channel or None), effect=payload.effect,
        points=int(payload.points or 0), field=payload.field,
        operator=payload.operator, value=payload.value,
        reason_label=payload.reason_label.strip(),
        sort_order=int(payload.sort_order or 100),
        is_active=bool(payload.is_active), created_by_id=current_user.id,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    log_action(db, org_id, current_user.id, "qualification_rule.created",
               "qualification_rule", rule.id,
               details={"name": rule.name, "effect": rule.effect,
                        "channel": rule.channel, "field": rule.field},
               after=_serialize(rule))
    return _serialize(rule)


def _load_rule(db: Session, org_id: str, rule_id: str) -> QualificationRule:
    rule = (db.query(QualificationRule)
            .filter(QualificationRule.id == rule_id,
                    QualificationRule.organization_id == org_id).first())
    if rule is None:
        # 404 rather than 403 - the same reasoning as lead_scope. A 403 would
        # confirm that a rule with this id exists in some other tenant.
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.put("/rules/{rule_id}")
def update_rule(rule_id: str, payload: RuleIn, request: Request,
                db: Session = Depends(get_db),
                current_user: User = Depends(require_admin)):
    org_id = _org_of(current_user, db, request)
    rule = _load_rule(db, org_id, rule_id)
    _validate(payload)
    before = _serialize(rule)
    rule.name = payload.name.strip()
    rule.channel = payload.channel or None
    rule.effect = payload.effect
    rule.points = int(payload.points or 0)
    rule.field = payload.field
    rule.operator = payload.operator
    rule.value = payload.value
    rule.reason_label = payload.reason_label.strip()
    rule.sort_order = int(payload.sort_order or 100)
    rule.is_active = bool(payload.is_active)
    db.commit()
    db.refresh(rule)
    log_action(db, org_id, current_user.id, "qualification_rule.updated",
               "qualification_rule", rule.id,
               details={"name": rule.name}, before=before, after=_serialize(rule))
    return _serialize(rule)


@router.delete("/rules/{rule_id}")
def deactivate_rule(rule_id: str, request: Request, db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin)):
    """Deactivates. Does not delete.

    Which rule was in force when a campaign went out is worth being able to
    answer six months later, and a deleted row cannot answer it.
    """
    org_id = _org_of(current_user, db, request)
    rule = _load_rule(db, org_id, rule_id)
    rule.is_active = False
    db.commit()
    log_action(db, org_id, current_user.id, "qualification_rule.deactivated",
               "qualification_rule", rule.id, details={"name": rule.name})
    return {"id": rule.id, "is_active": False}
