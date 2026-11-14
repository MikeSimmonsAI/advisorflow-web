"""EVOSYS PRO SALES TEAM — who holds which seat, decided in one place.

THE SEATS (top to bottom)
-------------------------
  God Mode       god_admin. Above everyone, every right, always. Nothing here
                 narrows it.
  Owner          brand capability `sales_program_owner` on EvoSys Pro Sales
                 (platform@evosyspro.live and Michael Schlueter). Full view,
                 runs the team. Granted only by God Mode.
  Sr EVP         brand capability `sales_team_executive` + an active sales
                 membership on EvoSys Pro Sales. Leads the team.
  Area Sales Mgr membership role `sales_manager`.
  Rep            membership role `sales_rep`.

The executive grant `deal_builder_executive` (from PR #591) still opens the
full executive Deal Builder; it is treated as full view, nothing more.

One record decides everything: the membership on EvoSys Pro Sales (role,
reports_to, is_active) plus these named grants. Program Access cards, the Deal
Builder views, My Team and commission attribution all read it from here.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, BrandSalesOrg,
                                     Membership)
from app.services import capabilities as caps
from app.services.sales_access import is_god

PROGRAM_SLUG = "evosyspro-sales"
CAP_OWNER = "sales_program_owner"
CAP_EXECUTIVE = "sales_team_executive"
CAP_EXEC_BUILDER = "deal_builder_executive"

SEAT_GOD, SEAT_OWNER, SEAT_SEVP, SEAT_ASM, SEAT_REP = "god", "owner", "sevp", "asm", "rep"
SEAT_LABELS = {SEAT_GOD: "God Mode", SEAT_OWNER: "Owner",
               SEAT_SEVP: "Sr Executive Vice President",
               SEAT_ASM: "Area Sales Manager", SEAT_REP: "Salesperson"}


def program_brand(db: Session) -> Optional[BrandSalesOrg]:
    return (db.query(BrandSalesOrg)
            .filter(BrandSalesOrg.slug == PROGRAM_SLUG,
                    BrandSalesOrg.is_active.is_(True)).first())


def _has(db: Session, user: User, key: str, brand_id: str) -> bool:
    return brand_id in caps.brands_with_capability(db, user, key)


def membership(db: Session, user: User, brand_id: str) -> Optional[Membership]:
    return (db.query(Membership)
            .filter(Membership.user_id == user.id,
                    Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                    Membership.scope_id == brand_id,
                    Membership.is_active.is_(True),
                    Membership.role.in_((ROLE_SALES_MANAGER, ROLE_SALES_REP)))
            .first())


def seat(db: Session, user: Optional[User], brand: Optional[BrandSalesOrg] = None) -> Optional[str]:
    """This person's seat in the EvoSys Pro sales program, or None."""
    if user is None:
        return None
    if is_god(user):
        return SEAT_GOD
    brand = brand or program_brand(db)
    if brand is None:
        return None
    if _has(db, user, CAP_OWNER, brand.id):
        return SEAT_OWNER
    m = membership(db, user, brand.id)
    if m is None:
        return None
    if _has(db, user, CAP_EXECUTIVE, brand.id):
        return SEAT_SEVP
    return SEAT_ASM if m.role == ROLE_SALES_MANAGER else SEAT_REP


def is_owner_or_god(db: Session, user: Optional[User]) -> bool:
    return seat(db, user) in (SEAT_GOD, SEAT_OWNER)


def deal_view(db: Session, user: Optional[User]) -> Optional[str]:
    """Which Deal Builder commission view this person gets, or None.

    full  God, owners, and holders of the executive builder grant
    sevp / asm / rep  by seat - and only once onboarding is approved.
    """
    from app.services import deal_builder_commission as dbc
    if user is None:
        return None
    if is_god(user):
        return dbc.VIEW_FULL
    brand = program_brand(db)
    if brand is None:
        return None
    if _has(db, user, CAP_OWNER, brand.id) or _has(db, user, CAP_EXEC_BUILDER, brand.id):
        return dbc.VIEW_FULL
    s = seat(db, user, brand)
    if s is None:
        return None
    if not onboarding_cleared(db, user, brand):
        return None
    return {SEAT_SEVP: dbc.VIEW_SEVP, SEAT_ASM: dbc.VIEW_ASM, SEAT_REP: dbc.VIEW_REP}[s]


def onboarding_cleared(db: Session, user: User, brand: BrandSalesOrg) -> bool:
    """Approved to sell. A person with an onboarding record must be approved;
    see app/services/sales_onboarding.py. (Members who predate onboarding have
    no record and are not blocked by this check.)"""
    try:
        from app.services import sales_onboarding
    except ImportError:  # onboarding not installed yet
        return True
    return sales_onboarding.is_cleared(db, user.id, brand.id)
