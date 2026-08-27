"""
Sales proposals and the secure deal room — Checkpoint 4.

TWO SURFACES IN ONE FILE, kept rigorously apart:

  /sales/proposals/*        internal. require_sales_member, per-record checks.
  /deal-room/*              PUBLIC. the token IS the authorization.

The public half returns a deliberately narrow projection built by
`_public_payload`. It is a whitelist, not a filter: it names the fields a
customer may see, so no future column added to Proposal or Opportunity can leak
outward by default. Internal notes, price-override reasons, demo build notes,
other opportunities and every internal user detail are structurally absent
rather than stripped.

REUSE: this router does NOT replace `proposal_router.py`, which serves the
original customer-organization portal and still works. Both write the same
tables. What is new here is the SALES path — opportunity-scoped, versioned,
priced and audited.
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import (
    APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.models import (
    User, Proposal, ProposalBlock, ProposalToken, PortalEvent,
    PROP_DRAFT, PROP_INTERNAL_REVIEW, PROP_READY, PROP_SENT, PROP_VIEWED,
    PROP_ACCEPTED, PROP_DECLINED, PROP_CHANGE_REQUESTED, PROP_EXPIRED,
    PROP_SUPERSEDED, PROPOSAL_STATUSES, PROPOSAL_STATUS_LABELS,
    PROPOSAL_EDITABLE_STATUSES,
    PORTAL_OPENED, PORTAL_PROPOSAL_VIEWED, PORTAL_DEMO_OPENED,
    PORTAL_DOC_OPENED, PORTAL_DOC_DOWNLOADED, PORTAL_LINK_OPENED,
    PORTAL_EVENT_LABELS,
)
from app.models.sales_models import Opportunity, BrandPackage
from app.services import package_pricing as _pp
from app.services import demo_sites as _demos
from app.services.sales_access import (
    require_sales_member, assert_can_view_opportunity, sales_org_ids,
    is_sales_manager, is_god,
)
from app.services import proposal_service as ps
from app.services import pricing_approvals as _appr
from app.routers.sales_router import _user_name

log = logging.getLogger(__name__)

router = APIRouter(tags=["sales-proposals"])

# Portal block types the existing viewer already renders. Reused verbatim —
# this vocabulary predates Checkpoint 4 and the customer-facing viewer is built
# around it.
BLOCK_TYPES = ("text", "image", "pdf", "video", "divider", "cta", "website_url")


# ── access helpers ──────────────────────────────────────────────────────────

def _load_proposal(db: Session, proposal_id: str, user: User) -> Proposal:
    """Load a SALES proposal the caller is entitled to see.

    Three gates, all server-side:
      1. it exists and is not deleted
      2. it is a sales proposal (has a brand_sales_org_id)
      3. the caller can see its OPPORTUNITY — reusing the Checkpoint 1 rule
         rather than inventing a second, weaker one for proposals
    """
    prop = (db.query(Proposal)
            .filter(Proposal.id == proposal_id,
                    Proposal.deleted_at.is_(None)).first())
    if prop is None or not prop.brand_sales_org_id:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if prop.brand_sales_org_id not in sales_org_ids(user, db):
        # Cross-brand access fails CLOSED and looks identical to "missing", so
        # the endpoint cannot be used to probe another brand's proposal ids.
        raise HTTPException(status_code=404, detail="Proposal not found")
    if prop.opportunity_id:
        opp = db.query(Opportunity).filter(
            Opportunity.id == prop.opportunity_id).first()
        if opp is not None:
            assert_can_view_opportunity(user, opp, db)
    return prop


def _editable_or_400(prop: Proposal) -> None:
    if prop.sales_status not in PROPOSAL_EDITABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="This proposal is %s. Create a new version to change it."
                   % PROPOSAL_STATUS_LABELS.get(prop.sales_status, prop.sales_status))


def _money(v):
    return None if v is None else float(v)


def _proposal_opp(db: Session, prop: Proposal):
    """The opportunity behind this proposal - needed because a per-deal
    implementation fee lives there, not on the package."""
    return (db.query(Opportunity).filter(Opportunity.id == prop.opportunity_id).first()
            if prop.opportunity_id else None)


def _proposal_commercials(db: Session, prop: Proposal, pkg) -> Optional[dict]:
    """Implementation fee, monthly rate, MRR, term, RCV and TCV for this quote.

    Assembled from the package and the deal rather than stored on the proposal,
    so there is no second copy of the arithmetic that can disagree with the
    first. The proposal's own snapshot - which option, which term - is what
    steers it.
    """
    if pkg is None:
        return None
    opp = _proposal_opp(db, prop)
    option = _pp.normalize_option(prop.billing_option, pkg)
    return _pp.quote(pkg, option, opp,
                     term_months=prop.contract_term_months or None)


def _proposal_out(db: Session, prop: Proposal, user: User,
                  include_blocks: bool = False) -> dict:
    pkg = (db.query(BrandPackage).filter(BrandPackage.id == prop.package_id).first()
           if prop.package_id else None)
    out = {
        "id": prop.id,
        "opportunity_id": prop.opportunity_id,
        "brand_sales_org_id": prop.brand_sales_org_id,
        "proposal_number": prop.proposal_number,
        "version": prop.version or 1,
        "supersedes_id": prop.supersedes_id,
        "status": prop.sales_status,
        "status_label": PROPOSAL_STATUS_LABELS.get(prop.sales_status, prop.sales_status),
        "is_published": prop.status == "published",
        "title": prop.title,
        "subtitle": prop.subtitle,
        "client_name": prop.client_name,
        "client_email": prop.client_email,
        "client_company": prop.client_company,
        "package_id": prop.package_id,
        "billing_option": prop.billing_option,
        "billing_option_label": (_pp.option_label(prop.billing_option,
                                                  prop.contract_term_months)
                                 if prop.billing_option else None),
        "contract_term_months": prop.contract_term_months,
        # The full commercial picture, each component named. `base_amount` above
        # still means the one-time figure it always meant; these are the
        # recurring numbers beside it, not a redefinition of it.
        "commercials": _proposal_commercials(db, prop, pkg),
        "package_pricing": (_pp.package_pricing(pkg, _proposal_opp(db, prop))
                            if pkg is not None else None),
        "package_name": pkg.name if pkg else None,
        "base_amount": _money(prop.base_amount),
        "adjustment": _money(prop.adjustment),
        "final_amount": _money(prop.final_amount),
        "currency": prop.currency or "USD",
        # Manager-visible audit of any discount. A rep sees THAT it was
        # adjusted; the reason is a management artefact.
        "price_override_at": prop.price_override_at,
        "price_override_reason": (
            prop.price_override_reason
            if (is_god(user) or is_sales_manager(user, db, prop.brand_sales_org_id))
            else None),
        # WHO approved it. The column was written from the first day of
        # Checkpoint 4 and returned by nothing, so the audit trail could only be
        # reconstructed from the timeline. Same manager-only visibility as the
        # reason it sits beside.
        "price_override_by_name": (
            _user_name(db, prop.price_override_by)
            if (prop.price_override_by
                and (is_god(user) or is_sales_manager(user, db, prop.brand_sales_org_id)))
            else None),
        "can_override_price": ps.can_override_price(db, user, prop.brand_sales_org_id),
        "executive_summary": prop.executive_summary,
        "business_need": prop.business_need,
        "objectives": prop.objectives,
        "recommended_solution": prop.recommended_solution,
        "scope": prop.scope,
        "deliverables": prop.deliverables,
        "implementation_plan": prop.implementation_plan,
        "terms": prop.terms,
        "expires_at": prop.expires_at,
        "sent_at": prop.sent_at,
        "first_viewed_at": prop.first_viewed_at,
        "last_viewed_at": prop.last_viewed_at,
        "accepted_at": prop.accepted_at,
        "declined_at": prop.declined_at,
        "change_requested_at": prop.change_requested_at,
        "superseded_at": prop.superseded_at,
        "customer_response_note": prop.customer_response_note,
        "responded_by_email": prop.responded_by_email,
        "created_at": prop.created_at,
        "updated_at": prop.updated_at,
        "editable": prop.sales_status in PROPOSAL_EDITABLE_STATUSES,
    }
    # The rep's outstanding ask, if any (Checkpoint 5). Carried on the proposal
    # rather than fetched separately so the panel can show "waiting on your
    # manager" in the same render that shows the price it applies to.
    _open_req = _appr.open_request_for(db, prop.id)
    out["pricing_request"] = _appr.request_out(db, _open_req) if _open_req else None
    if include_blocks:
        blocks = (db.query(ProposalBlock)
                  .filter(ProposalBlock.proposal_id == prop.id)
                  .order_by(ProposalBlock.position.asc()).all())
        out["blocks"] = [{
            "id": b.id, "block_type": b.block_type, "position": b.position,
            "content": b.content, "file_url": b.file_url,
            "file_name": b.file_name, "file_size": b.file_size,
            "generated": (b.file_name or "") == "af-generated",
        } for b in blocks]
    return out


# ── request models ──────────────────────────────────────────────────────────

class CreateProposalIn(BaseModel):
    opportunity_id: str
    title: Optional[str] = None
    package_id: Optional[str] = None
    # Omitted, this inherits whatever the opportunity was sold on rather than
    # defaulting to the regular rate and quietly re-rating an agreed deal.
    billing_option: Optional[str] = None


class UpdateProposalIn(BaseModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    client_name: Optional[str] = None
    client_email: Optional[str] = None
    client_company: Optional[str] = None
    executive_summary: Optional[str] = None
    business_need: Optional[str] = None
    objectives: Optional[str] = None
    recommended_solution: Optional[str] = None
    scope: Optional[str] = None
    deliverables: Optional[str] = None
    implementation_plan: Optional[str] = None
    terms: Optional[str] = None
    expires_at: Optional[datetime] = None
    # Pricing is accepted here but ALWAYS routed through apply_pricing, which
    # enforces manager authority. There is no path that writes final_amount
    # directly from a request body.
    package_id: Optional[str] = None
    billing_option: Optional[str] = None      # month_to_month | term_agreement
    adjustment: Optional[float] = None
    price_reason: Optional[str] = None


class BlockIn(BaseModel):
    block_type: str
    content: Optional[str] = None
    file_url: Optional[str] = None
    file_name: Optional[str] = None
    position: Optional[int] = None


class SendIn(BaseModel):
    recipient_email: Optional[str] = None
    recipient_name: Optional[str] = None
    valid_hours: int = Field(720, ge=1, le=8760)
    # Test/preview path. Publishes and issues the key but sends no email, so a
    # real prospect can never be contacted by an automated run.
    dry_run: bool = False


# ── internal endpoints ──────────────────────────────────────────────────────

@router.get("/sales/opportunities/{opp_id}/proposals")
def list_opportunity_proposals(opp_id: str,
                               user: User = Depends(require_sales_member),
                               db: Session = Depends(get_db)):
    """Every version on this deal, newest first, plus which one is live.

    Superseded versions ARE included. They are the audit trail, and a UI that
    hides them cannot answer "what did we actually send them in March".
    """
    opp = db.query(Opportunity).filter(Opportunity.id == opp_id).first()
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    assert_can_view_opportunity(user, opp, db)
    history = ps.proposal_history(db, opp.id)
    current = ps.current_proposal(db, opp.id)
    return {
        "current_id": current.id if current else None,
        "proposals": [_proposal_out(db, p, user) for p in history],
    }


@router.post("/sales/proposals", status_code=201)
def create_proposal(body: CreateProposalIn,
                    user: User = Depends(require_sales_member),
                    db: Session = Depends(get_db)):
    """Create a proposal from an opportunity, prefilled with what we know."""
    opp = db.query(Opportunity).filter(Opportunity.id == body.opportunity_id).first()
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    assert_can_view_opportunity(user, opp, db)
    if opp.brand_sales_org_id not in sales_org_ids(user, db):
        raise HTTPException(status_code=404, detail="Opportunity not found")

    existing = ps.current_proposal(db, opp.id)
    if existing is not None and existing.sales_status not in (
            PROP_DECLINED, PROP_EXPIRED):
        # Refuse rather than quietly making a second parallel v1. Two live
        # proposals on one deal is how a customer ends up with two prices.
        raise HTTPException(
            status_code=409,
            detail="This opportunity already has proposal %s (v%d). "
                   "Create a new version of it instead."
                   % (existing.proposal_number, existing.version or 1))

    overrides = {}
    if body.title:
        overrides["title"] = body.title
    prop = ps.create_proposal(db, opp, user, overrides=overrides)
    if body.package_id:
        # The opportunity already records which option this deal is on. Falling
        # back to it - rather than to the default - stops a proposal quoting the
        # month-to-month rate for a deal that was agreed on a term agreement.
        res = ps.apply_pricing(
            db, prop, user, package_id=body.package_id,
            billing_option=(body.billing_option
                            if body.billing_option is not None
                            else opp.billing_option))
        if not res["ok"]:
            db.rollback()
            raise HTTPException(status_code=400, detail=res["error"])
    db.commit()
    db.refresh(prop)
    return _proposal_out(db, prop, user, include_blocks=True)


@router.get("/sales/proposals/{proposal_id}")
def get_proposal(proposal_id: str,
                 user: User = Depends(require_sales_member),
                 db: Session = Depends(get_db)):
    prop = _load_proposal(db, proposal_id, user)
    return _proposal_out(db, prop, user, include_blocks=True)


@router.patch("/sales/proposals/{proposal_id}")
def update_proposal(proposal_id: str, body: UpdateProposalIn,
                    user: User = Depends(require_sales_member),
                    db: Session = Depends(get_db)):
    """Edit a proposal the customer has NOT yet seen.

    Once it is SENT or VIEWED this refuses, and the caller must create a
    version. That is the whole integrity guarantee: the document a customer
    read cannot be silently rewritten underneath them.
    """
    prop = _load_proposal(db, proposal_id, user)
    _editable_or_400(prop)

    for field in ("title", "subtitle", "client_name", "client_email",
                  "client_company", "executive_summary", "business_need",
                  "objectives", "recommended_solution", "scope", "deliverables",
                  "implementation_plan", "terms", "expires_at"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(prop, field, val)

    if (body.package_id is not None or body.adjustment is not None
            or body.billing_option is not None):
        res = ps.apply_pricing(db, prop, user,
                               package_id=body.package_id,
                               billing_option=body.billing_option,
                               adjustment=body.adjustment,
                               reason=body.price_reason)
        if not res["ok"]:
            db.rollback()
            # 403 when it is an authority problem, 400 when it is bad input —
            # the rep needs to know whether to fix the number or ask a manager.
            code = 403 if "manager" in (res["error"] or "") else 400
            raise HTTPException(status_code=code, detail=res["error"])

    db.commit()
    db.refresh(prop)
    return _proposal_out(db, prop, user, include_blocks=True)


@router.post("/sales/proposals/{proposal_id}/version", status_code=201)
def create_new_version(proposal_id: str,
                       user: User = Depends(require_sales_member),
                       db: Session = Depends(get_db)):
    """Version n+1. The current one becomes SUPERSEDED and is preserved."""
    prev = _load_proposal(db, proposal_id, user)
    if prev.sales_status == PROP_SUPERSEDED:
        raise HTTPException(status_code=400,
                            detail="That version has already been superseded.")
    nxt = ps.create_version(db, prev, user)
    db.commit()
    db.refresh(nxt)
    return _proposal_out(db, nxt, user, include_blocks=True)


@router.post("/sales/proposals/{proposal_id}/publish")
def publish(proposal_id: str,
            user: User = Depends(require_sales_member),
            db: Session = Depends(get_db)):
    """Make it live in the portal. Sends nothing."""
    prop = _load_proposal(db, proposal_id, user)
    res = ps.publish_proposal(db, prop, user)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["error"])
    db.commit()
    db.refresh(prop)
    return _proposal_out(db, prop, user, include_blocks=True)


@router.post("/sales/proposals/{proposal_id}/send")
def send(proposal_id: str, body: SendIn,
         user: User = Depends(require_sales_member),
         db: Session = Depends(get_db)):
    """Publish, issue secure access, and email the customer.

    Does NOT move the opportunity to Won, or advance its stage at all.
    """
    prop = _load_proposal(db, proposal_id, user)
    res = ps.send_proposal(db, prop, user,
                           recipient_email=body.recipient_email,
                           recipient_name=body.recipient_name,
                           dry_run=body.dry_run)
    if not res["ok"]:
        db.commit()   # keep the publish + token even when the email failed
        raise HTTPException(status_code=502, detail=res["error"])
    db.commit()
    db.refresh(prop)
    out = _proposal_out(db, prop, user, include_blocks=True)
    out["portal_url"] = res.get("portal_url")
    out["dry_run"] = bool(res.get("dry_run"))
    return out


@router.post("/sales/proposals/{proposal_id}/revoke-access")
def revoke_access(proposal_id: str,
                  user: User = Depends(require_sales_member),
                  db: Session = Depends(get_db)):
    """Kill every live portal link for this proposal, immediately."""
    prop = _load_proposal(db, proposal_id, user)
    count = ps.revoke_access(db, prop)
    db.commit()
    return {"revoked": count}


class PricingRequestIn(BaseModel):
    requested_adjustment: float = Field(
        ..., description="Signed amount against the list price. A discount is negative.")
    reason: str


@router.post("/sales/proposals/{proposal_id}/pricing-request", status_code=201)
def request_pricing_approval(proposal_id: str, body: PricingRequestIn,
                             user: User = Depends(require_sales_member),
                             db: Session = Depends(get_db)):
    """A rep asks their manager for a price they cannot set themselves.

    This creates a QUESTION, not a price. Nothing about the proposal's amount
    changes until a manager decides. Before Checkpoint 5 the refusal message
    said "ask your manager", which meant the ask left the product entirely and
    the manager had no queue.
    """
    prop = _load_proposal(db, proposal_id, user)
    res = _appr.create_request(db, prop, user,
                               body.requested_adjustment, body.reason)
    if not res.get("ok"):
        db.rollback()
        raise HTTPException(status_code=400, detail=res.get("error"))
    db.commit()
    return _appr.request_out(db, res["request"])


@router.post("/sales/proposals/{proposal_id}/pricing-request/withdraw")
def withdraw_pricing_approval(proposal_id: str,
                              user: User = Depends(require_sales_member),
                              db: Session = Depends(get_db)):
    """The rep no longer needs it. Only the person who asked may withdraw."""
    prop = _load_proposal(db, proposal_id, user)
    req = _appr.open_request_for(db, prop.id)
    if req is None:
        raise HTTPException(status_code=404, detail="No open request on this proposal.")
    res = _appr.withdraw_request(db, req, user)
    if not res.get("ok"):
        db.rollback()
        raise HTTPException(status_code=400, detail=res.get("error"))
    db.commit()
    return {"ok": True}


@router.post("/sales/proposals/{proposal_id}/blocks", status_code=201)
def add_block(proposal_id: str, body: BlockIn,
              user: User = Depends(require_sales_member),
              db: Session = Depends(get_db)):
    """Add customer-facing content to the deal room.

    This is the flexible half of the portal, preserved exactly as it was: a demo
    URL, a slide deck, a video, a document, a note. Every deal room can be
    different, which is the point — a generic identical portal is a brochure,
    not a deal room.
    """
    prop = _load_proposal(db, proposal_id, user)
    if body.block_type not in BLOCK_TYPES:
        raise HTTPException(status_code=400,
                            detail="Unknown content type '%s'." % body.block_type)
    last = (db.query(ProposalBlock)
            .filter(ProposalBlock.proposal_id == prop.id)
            .order_by(ProposalBlock.position.desc()).first())
    pos = body.position if body.position is not None else ((last.position + 1) if last else 0)
    block = ProposalBlock(
        proposal_id=prop.id, block_type=body.block_type,
        position=pos, content=body.content, file_url=body.file_url,
        # Never 'af-generated' from a request — that marker is what protects
        # hand-added blocks from being wiped on republish, so it must not be
        # settable by a client.
        file_name=(body.file_name if (body.file_name or "") != "af-generated" else None),
        created_at=datetime.utcnow())
    db.add(block)
    db.commit()
    db.refresh(prop)
    return _proposal_out(db, prop, user, include_blocks=True)


@router.delete("/sales/proposals/{proposal_id}/blocks/{block_id}")
def delete_block(proposal_id: str, block_id: str,
                 user: User = Depends(require_sales_member),
                 db: Session = Depends(get_db)):
    prop = _load_proposal(db, proposal_id, user)
    block = (db.query(ProposalBlock)
             .filter(ProposalBlock.id == block_id,
                     ProposalBlock.proposal_id == prop.id).first())
    if block is None:
        raise HTTPException(status_code=404, detail="Content block not found")
    db.delete(block)
    db.commit()
    db.refresh(prop)
    return _proposal_out(db, prop, user, include_blocks=True)


@router.get("/sales/proposals/{proposal_id}/activity")
def proposal_activity(proposal_id: str,
                      user: User = Depends(require_sales_member),
                      db: Session = Depends(get_db)):
    """What the buyer actually did, newest first.

    Every row is an act the server observed. Nothing here is inferred from
    scroll depth or dwell time — see the PortalEvent docstring for why.
    """
    prop = _load_proposal(db, proposal_id, user)
    rows = (db.query(PortalEvent)
            .filter(PortalEvent.proposal_id == prop.id)
            .order_by(PortalEvent.occurred_at.desc())
            .limit(200).all())
    return {
        "proposal_id": prop.id,
        "first_viewed_at": prop.first_viewed_at,
        "last_viewed_at": prop.last_viewed_at,
        "events": [{
            "id": e.id,
            "event_type": e.event_type,
            "label": PORTAL_EVENT_LABELS.get(e.event_type, e.event_type),
            "detail": e.label,
            "proposal_version": e.proposal_version,
            "recipient_email": e.recipient_email,
            "browser": e.user_agent_family,
            "occurred_at": e.occurred_at,
        } for e in rows],
    }


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC DEAL ROOM
# ═══════════════════════════════════════════════════════════════════════════
#
# NO AUTHENTICATION. The token is the authorization. A customer must never need
# an account to read what we sent them.
#
# Every rejection returns the SAME shape and message. Distinguishing "unknown"
# from "revoked" from "expired" would turn this into an oracle for which tokens
# exist.

def _resolve(db: Session, token: str):
    """(token_row, proposal, error). Never raises."""
    now = datetime.utcnow()
    if not token or len(token) < 20:
        return None, None, "This link is not valid."
    row = db.query(ProposalToken).filter(ProposalToken.token == token).first()
    if row is None:
        return None, None, "This link is not valid."
    if row.revoked_at is not None:
        return None, None, "This link is no longer active."
    if row.expires_at is not None and row.expires_at < now:
        return None, None, "This link has expired."
    prop = (db.query(Proposal)
            .filter(Proposal.id == row.proposal_id,
                    Proposal.deleted_at.is_(None)).first())
    if prop is None:
        return None, None, "This link is no longer active."
    # Unpublished or superseded content must not be served even with a valid
    # token — a v1 link must stop showing v1's price once v2 replaces it.
    if prop.status != "published":
        return None, None, "This proposal is not currently available."
    if prop.expires_at is not None and prop.expires_at < now:
        return None, None, "This proposal has expired."
    return row, prop, None


def _public_payload(db: Session, prop: Proposal) -> dict:
    """EXACTLY what a customer may see. A whitelist, never a filter.

    Adding a column to Proposal or Opportunity cannot leak it here, because
    nothing is copied wholesale — each customer-facing field is named. Absent by
    construction: internal notes, price override reasons and who approved them,
    demo build notes, the opportunity's stage/value/owner, every internal user's
    name and email, other proposals, other customers, team availability.
    """
    blocks = (db.query(ProposalBlock)
              .filter(ProposalBlock.proposal_id == prop.id)
              .order_by(ProposalBlock.position.asc()).all())
    from app.services.appointment_invites import brand_identity_for_brand
    ident = brand_identity_for_brand(db, prop.brand_sales_org_id)
    return {
        "proposal": {
            "number": prop.proposal_number,
            "version": prop.version or 1,
            "title": prop.title,
            "subtitle": prop.subtitle,
            "client_name": prop.client_name,
            "client_company": prop.client_company,
            # The agreed total only. `base_amount` and `adjustment` stay
            # internal — a customer seeing "list 4,995, adjustment -500" is
            # being invited to negotiate against our own audit trail.
            "amount": _money(prop.final_amount),
            "currency": prop.currency or "USD",
            "expires_at": prop.expires_at,
            "status": prop.sales_status,
            "accepted_at": prop.accepted_at,
            "declined_at": prop.declined_at,
            "change_requested_at": prop.change_requested_at,
        },
        "blocks": [{
            "id": b.id,
            "block_type": b.block_type,
            "position": b.position,
            "content": b.content,
            "file_url": b.file_url,
            "file_name": (None if (b.file_name or "") == "af-generated" else b.file_name),
        } for b in blocks],
        "brand": {
            "name": ident.get("name"),
            "support_email": ident.get("from_email"),
            "support_phone": ident.get("support_phone"),
            "website": ident.get("website"),
            "accent": ident.get("accent"),
        },
    }


class TrackIn(BaseModel):
    event_type: str
    block_id: Optional[str] = None
    label: Optional[str] = None


class DecisionIn(BaseModel):
    action: str          # accept | decline | request_change
    note: Optional[str] = None


@router.get("/deal-room/{token}")
def open_deal_room(token: str, request: Request, db: Session = Depends(get_db)):
    """Open the deal room. Records PORTAL_OPENED.

    A GET that writes an activity row is deliberate and different from the
    appointment confirmation link: this changes no business state and grants
    nothing. A link scanner prefetching it produces one extra 'opened' row,
    which is a small, visible inaccuracy — whereas a scanner prefetching an
    ACCEPT would fabricate a customer decision, which is why accept/decline are
    POSTs below.
    """
    row, prop, err = _resolve(db, token)
    if err:
        raise HTTPException(status_code=404, detail=err)

    now = datetime.utcnow()
    if row.first_redeemed_at is None:
        row.first_redeemed_at = now
    # No use_count / last_used_at here: ProposalToken does not have them (that
    # is the appointment-confirmation token), and it does not need them —
    # PortalEvent already records EVERY open with a timestamp, which is both
    # the count and the history, without a redundant counter to keep in sync.

    ps.record_portal_event(db, prop, PORTAL_OPENED, token=row,
                           user_agent=request.headers.get("user-agent"), now=now)
    db.commit()
    return _public_payload(db, prop)


@router.post("/deal-room/{token}/track")
def track_deal_room_event(token: str, body: TrackIn, request: Request,
                          db: Session = Depends(get_db)):
    """Record a defensible act: the proposal was viewed, a demo was opened, a
    document was downloaded.

    The event type is validated against a strict allowlist. Without it, anyone
    holding a link could post arbitrary strings into a salesperson's activity
    feed — 'engagement data' a rep would act on and that nobody generated.
    """
    allowed = {PORTAL_PROPOSAL_VIEWED, PORTAL_DEMO_OPENED, PORTAL_DOC_OPENED,
               PORTAL_DOC_DOWNLOADED, PORTAL_LINK_OPENED}
    if body.event_type not in allowed:
        raise HTTPException(status_code=400, detail="Unknown event type.")
    row, prop, err = _resolve(db, token)
    if err:
        raise HTTPException(status_code=404, detail=err)
    ps.record_portal_event(db, prop, body.event_type, token=row,
                           label=body.label, block_id=body.block_id,
                           user_agent=request.headers.get("user-agent"))
    db.commit()
    return {"ok": True}


@router.post("/deal-room/{token}/decision")
def deal_room_decision(token: str, body: DecisionIn, request: Request,
                       db: Session = Depends(get_db)):
    """Accept, decline, or request a change.

    A POST, never a GET — mail scanners prefetch links, and a GET that accepted
    would manufacture agreement nobody gave.

    Acceptance records a decision. It does NOT mark the opportunity Won and does
    NOT provision anything; it sets the rep a next action and stops there.
    """
    row, prop, err = _resolve(db, token)
    if err:
        raise HTTPException(status_code=404, detail=err)
    res = ps.record_decision(db, prop, body.action, token=row, note=body.note,
                             user_agent=request.headers.get("user-agent"))
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["error"])
    db.commit()
    return {"ok": True, "action": res["action"],
            "proposal": _public_payload(db, prop)["proposal"]}


# ── demo sites ──────────────────────────────────────────────────────────────
#
# A mockup the prospect can open, hosted on the BRAND's own domain. The
# proposal upload path refuses HTML for good reason - a customer-supplied page
# served same-origin with an app that keeps its token in localStorage is stored
# XSS - and this does not reopen that door. A demo is authored here by an
# authenticated brand-sales user, and the public route below hands it to a
# sandboxed frame that has no same-origin access to anything.

class DemoSiteIn(BaseModel):
    title: str
    html: str
    # Which shelf this mockup sits on. "platform" is the product walkthrough and
    # is what the deal advertises as its demo; "website" is an optional website
    # concept and does not claim the deal's demo slot. Anything unrecognised
    # falls back to the default rather than minting a new shelf.
    slot: Optional[str] = None
    # Older links in this slot stay live unless this says otherwise. A link a
    # prospect already has keeps working until somebody decides it should not.
    retire_previous: bool = False


@router.post("/sales/opportunities/{opportunity_id}/demo-site", status_code=201)
def publish_demo_site(opportunity_id: str, body: DemoSiteIn,
                      user: User = Depends(require_sales_member),
                      db: Session = Depends(get_db)):
    """Publish a demo mockup for this deal and return its shareable link.

    Also sets the opportunity's `demo_url` and marks the demo ready, so the
    Demo Build panel stops saying "set when the environment exists" the moment
    the environment exists.
    """
    opp = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    assert_can_view_opportunity(user, opp, db)
    if opp.brand_sales_org_id not in sales_org_ids(user, db):
        raise HTTPException(status_code=404, detail="Opportunity not found")

    slot = _demos.normalize_slot(body.slot)
    res = _demos.create(db, opp, user, title=body.title, html=body.html, slot=slot,
                        retire_previous=bool(body.retire_previous))
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["error"])
    demo = res["demo"]

    from app.services.appointment_invites import brand_identity_for_brand, PUBLIC_BASE_URL
    ident = brand_identity_for_brand(db, opp.brand_sales_org_id)
    base = ident.get("app_base_url") or PUBLIC_BASE_URL
    url = _demos.public_url(base, demo.token)

    # Only the product walkthrough is "this deal's demo". A website concept is
    # an optional add-on being illustrated, and marking the demo ready because
    # one was published would tell the pipeline something that is not true.
    if slot == _demos.DEFAULT_SLOT:
        opp.demo_url = url
        if opp.demo_status in (None, "not_requested", "requested", "in_progress"):
            opp.demo_status = "ready"
        if not opp.demo_ready_at:
            opp.demo_ready_at = datetime.utcnow()

    db.commit()
    db.refresh(demo)
    return {"demo": _demos.out(demo, base), "url": url}


@router.get("/sales/opportunities/{opportunity_id}/demo-sites")
def list_demo_sites(opportunity_id: str,
                    user: User = Depends(require_sales_member),
                    db: Session = Depends(get_db)):
    opp = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    assert_can_view_opportunity(user, opp, db)
    if opp.brand_sales_org_id not in sales_org_ids(user, db):
        raise HTTPException(status_code=404, detail="Opportunity not found")

    from app.services.appointment_invites import brand_identity_for_brand, PUBLIC_BASE_URL
    ident = brand_identity_for_brand(db, opp.brand_sales_org_id)
    base = ident.get("app_base_url") or PUBLIC_BASE_URL
    rows = _demos.for_opportunity(db, opportunity_id)
    return {"demos": [_demos.out(d, base) for d in rows]}


@router.post("/sales/demo-sites/{demo_id}/revoke")
def revoke_demo_site(demo_id: str,
                     user: User = Depends(require_sales_member),
                     db: Session = Depends(get_db)):
    """Kill a live demo link immediately."""
    from app.models.demo_site_models import DemoSite
    demo = db.query(DemoSite).filter(DemoSite.id == demo_id).first()
    # 404 rather than 403 for another brand's demo: confirming it exists would
    # be a small leak for no benefit.
    if demo is None or demo.brand_sales_org_id not in sales_org_ids(user, db):
        raise HTTPException(status_code=404, detail="Demo not found")
    _demos.revoke(db, demo)
    opp = db.query(Opportunity).filter(Opportunity.id == demo.opportunity_id).first()
    if opp is not None and opp.demo_url and demo.token in opp.demo_url:
        # The link no longer opens, so the field must not keep advertising it.
        opp.demo_url = None
    db.commit()
    return {"revoked": True, "demo_id": demo.id}


# ── PUBLIC: the prospect's view ─────────────────────────────────────────────
# No ProtectedRoute, no account, no JWT. The token IS the authorization, and it
# is the only thing this route accepts.

@router.get("/public/demo/{token}")
def resolve_demo_site(token: str, db: Session = Depends(get_db)):
    demo = _demos.resolve(db, token)
    if demo is None:
        # One message for every failure mode. Distinguishing expired from
        # never-existed would let somebody probe for live tokens.
        raise HTTPException(status_code=404,
                            detail="This link is no longer available. "
                                   "Ask your contact for a new one.")
    payload = {"title": demo.title, "html": demo.html}
    db.commit()   # persist the view counters
    return payload


# ── file upload ─────────────────────────────────────────────────────────────
#
# REUSES the existing ProposalFile storage and the existing public
# /proposals/files/{id} serving route. Nothing about how files are stored or
# served changes — this only adds the SALES-scoped upload path, with the brand
# and opportunity checks the customer-org path had no need for.

# What a salesperson may actually attach to a deal room. An allowlist, not a
# blocklist: an executable or an HTML file served from our own domain is a
# problem, and enumerating what is safe is the only way to be sure.
ALLOWED_UPLOAD_TYPES = {
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "image/gif": "image",
    "image/webp": "image",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "pdf",
    "application/msword": "pdf",
    "application/vnd.ms-powerpoint": "pdf",
    "application/vnd.ms-excel": "pdf",
}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024   # 20MB, same as the existing portal path


@router.post("/sales/proposals/{proposal_id}/upload", status_code=201)
async def upload_proposal_file(proposal_id: str,
                               file: UploadFile = File(...),
                               label: Optional[str] = Form(None),
                               user: User = Depends(require_sales_member),
                               db: Session = Depends(get_db)):
    """Attach a document, deck or image to the deal room.

    The file is stored as bytes in `proposal_files` — the same mechanism the
    customer-org portal already used — and a content block is created pointing
    at the existing public serving route.

    THE BLOCK IS WHAT PUBLISHES IT. Uploading stores a file; it becomes visible
    to the customer only because a block references it, and blocks are only
    served for a published proposal through a live token. Nothing here bypasses
    that rule.
    """
    from app.models.models import ProposalFile

    prop = _load_proposal(db, proposal_id, user)

    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(
            status_code=400,
            detail="That file type is not supported. Use a PDF, an image, or an "
                   "Office document.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That file is larger than 20MB. Link to it instead, or send a "
                   "smaller version.")

    row = ProposalFile(
        # A sales proposal has no customer organization — that is the whole
        # tenancy rule of this checkpoint — so this stays NULL just as it does
        # on the proposal itself.
        organization_id=None,
        proposal_id=prop.id,
        filename=(file.filename or "attachment")[:255],
        content_type=ctype,
        file_size=len(data),
        file_data=data,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()

    last = (db.query(ProposalBlock)
            .filter(ProposalBlock.proposal_id == prop.id)
            .order_by(ProposalBlock.position.desc()).first())
    db.add(ProposalBlock(
        proposal_id=prop.id,
        block_type=ALLOWED_UPLOAD_TYPES[ctype],
        position=(last.position + 1) if last else 0,
        content=(label or file.filename or "Document")[:255],
        file_url="/proposals/files/%s" % row.id,
        file_name=(file.filename or "attachment")[:255],
        file_size=len(data),
        created_at=datetime.utcnow(),
    ))
    db.commit()
    db.refresh(prop)
    return _proposal_out(db, prop, user, include_blocks=True)


# ── video provider status ───────────────────────────────────────────────────

@router.get("/sales/video/status")
def video_provider_status(brand_sales_org_id: Optional[str] = Query(None),
                          verify: bool = Query(False),
                          user: User = Depends(require_sales_member),
                          db: Session = Depends(get_db)):
    """Is video actually working for this brand, and if not, why?

    Deliberately NOT a settings screen — Checkpoint 4 does not build one. This
    answers the one question a person actually has when a meeting has no Zoom
    link: is it us, or is it them?

    `verify=true` performs a REAL round-trip to the provider. A green tick that
    only proves a row exists in our own database is worth nothing — the whole
    failure mode this guards against is a UI that says CONFIGURED while every
    meeting silently fails to provision.

    NO CREDENTIAL IS EVER RETURNED. The response says whether credentials are
    present, never what they are.
    """
    from app.services import meeting_providers as mreg
    from app.models.meeting_models import PROVIDER_LABELS
    from app.models.scheduling_models import MeetingType

    org_ids = sales_org_ids(user, db)
    bid = brand_sales_org_id or (list(org_ids)[0] if org_ids else None)
    if not bid or bid not in org_ids:
        raise HTTPException(status_code=404, detail="Brand not found")

    key = mreg.DEFAULT_PROVIDER
    cfg = mreg.brand_config(db, bid, key)
    provider = mreg.get_provider(db, bid, key=key)

    ready, reason = (False, "No provider available")
    if provider is not None:
        ready, reason = provider.is_ready()

    # Where the credentials came from matters operationally: an env-var setup
    # is shared platform-wide and will NOT scale to a second brand.
    source = "brand_config" if cfg is not None else ("environment" if ready else None)

    state = "ready" if ready else "not_configured"
    detail = None if ready else (reason or "Zoom is not configured for this brand.")

    verified = None
    if verify and provider is not None and ready:
        result = provider.verify()
        verified = bool(result.ok)
        if not result.ok:
            state = "error"
            detail = result.error_message or "Zoom rejected the request."
        else:
            detail = result.error_message   # "Connected as ..."
        if cfg is not None:
            cfg.last_verified_at = datetime.utcnow() if result.ok else cfg.last_verified_at
            cfg.last_error = None if result.ok else (result.error_message or "")[:1000]
            db.commit()

    types = (db.query(MeetingType)
             .filter(MeetingType.brand_sales_org_id == bid,
                     MeetingType.is_active.is_(True))
             .order_by(MeetingType.sort_order.asc()).all())

    return {
        "brand_sales_org_id": bid,
        "provider": key,
        "provider_label": PROVIDER_LABELS.get(key, key),
        "state": state,                    # ready | not_configured | error
        "detail": detail,
        "has_credentials": bool(ready),
        "credential_source": source,
        "verified": verified,
        "last_verified_at": cfg.last_verified_at if cfg else None,
        "last_error": cfg.last_error if cfg else None,
        # Which meeting types will actually produce a room. Without this, "Zoom
        # is ready" and "no link on my Discovery call" look contradictory when
        # they are both true and the type simply has video switched off.
        "meeting_types": [{
            "id": t.id, "key": t.key, "name": t.name,
            "requires_video": bool(t.requires_video),
            "provider": t.video_provider or key,
        } for t in types],
        "setup_hint": (
            None if ready else
            "Create a Server-to-Server OAuth app in the Zoom admin console with "
            "the meeting:write scope, then set ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID "
            "and ZOOM_CLIENT_SECRET on the backend service."),
    }
