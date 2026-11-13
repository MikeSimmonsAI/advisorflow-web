"""TEAM PRICING DEAL BUILDER — who may see which compensation.

TWO BUILDERS, ONE SET OF CUSTOMER PRICES
----------------------------------------
  Salesperson builder   /team-prices/sales-deal-builder (frontend)
                        Any EvoSys sales member (rep or manager) or the owner.
                        Builds and prints the customer's quote. Gets ONLY the
                        salesperson's own estimated commission, from
                        POST /deal-builder/my-commission.

  Executive builder     /team-prices/deal-builder.html (frontend entry point)
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
from app.services.sales_access import is_god, is_sales_member

router = APIRouter(prefix="/deal-builder", tags=["deal-builder"])

EXEC_CAPABILITY = "deal_builder_executive"
_PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "private_pages", "deal_builder_executive.html")
_NO_STORE = {"Cache-Control": "no-store, private", "Pragma": "no-cache",
             "X-Robots-Tag": "noindex, nofollow"}


def may_use_executive_builder(user: User, db: Session) -> bool:
    if is_god(user):
        return True
    return bool(caps.brands_with_capability(db, user, EXEC_CAPABILITY))


def may_use_sales_builder(user: User, db: Session) -> bool:
    return is_sales_member(user, db) or may_use_executive_builder(user, db)


@router.get("/access")
def deal_builder_access(user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """Which builder this signed-in person may open. Booleans only."""
    return Response(
        content=json.dumps({
            "sales": may_use_sales_builder(user, db),
            "executive": may_use_executive_builder(user, db),
            "name": user.full_name or "",
        }),
        media_type="application/json", headers=_NO_STORE)


class DealIn(BaseModel):
    plan: str
    saas_setup: float | None = 0
    addon_setup: float | None = 0
    monthly: float | None = 0
    basis: str | None = "quote"
    collected_setup: float | None = 0
    collected_monthly: float | None = 0


@router.post("/my-commission")
def my_commission(body: DealIn, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """The caller's own estimated commission for the deal on screen.

    Nothing about any other role is computed into, or returned by, this route.
    """
    if not may_use_sales_builder(user, db):
        raise HTTPException(status_code=403, detail="Sales workspace access required.")
    try:
        result = dbc.seller_commission(body.model_dump())
    except dbc.DealInputError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return Response(content=json.dumps(result),
                    media_type="application/json", headers=_NO_STORE)


@router.get("/executive-page")
def executive_page(user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """The full executive Deal Builder, to the owner or an explicit grantee only."""
    if not may_use_executive_builder(user, db):
        raise HTTPException(status_code=403,
                            detail="The executive Deal Builder requires executive access.")
    with open(_PAGE, encoding="utf-8") as f:
        html = f.read()
    return Response(content=html, media_type="text/html; charset=utf-8",
                    headers=_NO_STORE)
