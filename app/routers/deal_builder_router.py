"""TEAM PRICING DEAL BUILDER — who may see which compensation.

TWO BUILDERS, ONE SET OF CUSTOMER PRICES
----------------------------------------
  Salesperson builder   /team-prices/sales-deal-builder (frontend)
                        Any EvoSys sales member (rep or manager) or the owner.
                        Builds and prints the customer's quote. Gets ONLY the
                        salesperson's own estimated commission, from
                        POST /deal-builder/my-commission.

  Executive builder     /team-prices/executive-deal-builder (frontend entry;
                        the old /team-prices/deal-builder.html forwards here)
                        The owner (god_admin) or a person explicitly granted
                        the brand capability `deal_builder_executive`.
                        GET /deal-builder/executive-page returns the full page,
                        which shows every compensation layer.

WHY THE EXECUTIVE PAGE IS SERVED FROM HERE
------------------------------------------
It used to be a file in the public static site, so anyone with the URL could
download it — a login screen in front of a public file protects nothing. The
file now lives in app/private_pages/, which nothing mounts, and the only way to
receive it is this route, after the server has checked who is asking.

Being a sales manager is deliberately NOT enough: an Area Sales Manager must
not see the Sr Executive Vice President's or EvoSys's share.
"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import User
from app.services import capabilities as caps
from app.services import deal_builder_commission as dbc
from app.services import sales_team

router = APIRouter(prefix="/deal-builder", tags=["deal-builder"])

EXEC_CAPABILITY = "deal_builder_executive"
_PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "private_pages", "deal_builder_executive.html")
_NO_STORE = {"Cache-Control": "no-store, private", "Pragma": "no-cache",
             "X-Robots-Tag": "noindex, nofollow"}


def may_use_executive_builder(user: User, db: Session) -> bool:
    """The full executive page: God, sales program owners and the named
    executive-builder grant."""
    return sales_team.deal_view(db, user) == dbc.VIEW_FULL


def may_use_sales_builder(user: User, db: Session) -> bool:
    """Any approved seat in the EvoSys Pro sales program (or a full-view user)."""
    return sales_team.deal_view(db, user) is not None


def access_summary(db: Session, user: User) -> dict:
    """Booleans and labels only - no amounts."""
    v = sales_team.deal_view(db, user)
    seat = sales_team.seat(db, user)
    return {
        "sales": v is not None,
        "executive": v == dbc.VIEW_FULL,
        "view": v,
        "team_view": v in (dbc.VIEW_ASM, dbc.VIEW_SEVP),
        "seat": seat,
        "seat_label": sales_team.SEAT_LABELS.get(seat, ""),
        "name": user.full_name or "",
    }


@router.get("/access")
def deal_builder_access(user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """Which builder this signed-in person may open. Booleans only."""
    return Response(
        content=json.dumps(access_summary(db, user)),
        media_type="application/json", headers=_NO_STORE)


class AllocationIn(BaseModel):
    package: float | None = 0
    addons: float | None = 0


class DealIn(BaseModel):
    plan: str
    saas_setup: float | None = 0
    addon_setup: float | None = 0
    monthly: float | None = 0
    basis: str | None = "quote"
    collected_setup: float | None = 0
    collected_monthly: float | None = 0
    allocation: AllocationIn | None = None


@router.post("/my-commission")
def my_commission(body: DealIn, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """The caller's own estimated commission for the deal on screen.

    Nothing about any other role is computed into, or returned by, this route.
    """
    if not may_use_sales_builder(user, db):
        raise HTTPException(status_code=403, detail=_no_view_reason(db, user))
    try:
        result = dbc.seller_commission(body.model_dump())
    except dbc.DealInputError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return Response(content=json.dumps(result),
                    media_type="application/json", headers=_NO_STORE)


@router.post("/commission")
def commission(body: DealIn, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """The commission this caller may see for the deal on screen.

    The server decides the view from the caller's seat: a rep gets his own
    layer, an Area Sales Manager his and his reps', the Sr EVP the three sales
    layers, God and owners everything including EvoSys's share. Layers above
    the caller are never computed into the response body.
    """
    v = sales_team.deal_view(db, user)
    if v is None:
        raise HTTPException(status_code=403, detail=_no_view_reason(db, user))
    deal = body.model_dump()
    try:
        result = dbc.view(dbc.split(deal), v)
    except dbc.DealInputError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return Response(content=json.dumps(result), media_type="application/json",
                    headers=_NO_STORE)


def _no_view_reason(db: Session, user: User) -> str:
    seat = sales_team.seat(db, user)
    if seat is None:
        return "This login is not on the EvoSys Pro sales team."
    return "Your onboarding has not been approved yet, so commission is not shown."


@router.get("/executive-page")
def executive_page(user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """The full executive Deal Builder, to the owner or an explicit grantee only."""
    if not may_use_executive_builder(user, db):
        raise HTTPException(status_code=403,
                            detail="The executive Deal Builder requires executive access.")
    with open(_PAGE, encoding="utf-8") as f:
        html = f.read()
    # The page carries no rate table of its own: the one set of rules is
    # injected here, so the executive page and every other view agree.
    html = html.replace("/*__DEAL_RULES__*/null", json.dumps(dbc.RULES))
    return Response(content=html, media_type="text/html; charset=utf-8",
                    headers=_NO_STORE)
