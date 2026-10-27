"""SHARED INTELLIGENCE - verification, underwriting switch and the Deal
Opportunity Report (#415, sections 16-19). Read-only: nothing here buys a
lookup, calls a paid provider or changes a record.

    GET /wholesale/intel/property/{evo_id}/verification
    GET /wholesale/intel/buyer/{buyer_id}/verification
    GET /wholesale/intel/{evosense|deal}/{id}/underwriting?strategy=wholesale|investment|both
        (+ optional purchase_price, interest_rate, ltv, amortization_years, target_cap_rate - SCENARIO inputs)
    GET /wholesale/intel/{evosense|deal}/{id}/report[?format=html]          internal
    GET /wholesale/intel/deal/{id}/investor-report[?format=html]            investor-safe
    GET /wholesale/intel/providers                                          readiness (no activation)
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.deps import get_db, require_tenant_or_observer
from app.models.models import User
from app.services import wholesale_intel as INTEL
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

router = APIRouter(prefix="/wholesale/intel", tags=["wholesale-intelligence"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])


def _org(db, user) -> str:
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    return org_id


def _assume(purchase_price, interest_rate, ltv, amortization_years, target_cap_rate):
    a = {"purchase_price": purchase_price, "interest_rate": interest_rate, "ltv": ltv,
         "amortization_years": amortization_years, "target_cap_rate": target_cap_rate}
    a = {k: v for k, v in a.items() if v is not None}
    if "ltv" in a and not 0 < a["ltv"] <= 100:
        raise HTTPException(status_code=400, detail="LTV must be between 0 and 100")
    if "interest_rate" in a and not 0 <= a["interest_rate"] <= 30:
        raise HTTPException(status_code=400, detail="Interest rate must be between 0 and 30")
    if "amortization_years" in a and not 1 <= a["amortization_years"] <= 40:
        raise HTTPException(status_code=400, detail="Amortization must be 1-40 years")
    return a


@router.get("/providers")
def providers(user: User = Depends(require_tenant_or_observer)):
    return {"providers": INTEL.provider_readiness(),
            "note": "Nothing is activated or purchased here. Keys, enablement and budgets are owner-controlled "
                    "on Providers & Controls."}


@router.get("/property/{evo_id}/verification")
def property_verification(evo_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    return INTEL.property_verification(db, _org(db, user), evo_id)


@router.get("/buyer/{buyer_id}/verification")
def buyer_verification(buyer_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    return INTEL.buyer_verification(db, _org(db, user), buyer_id)


def _routes(kind: str):
    """Explicit /evosense/... and /deal/... paths (no free-form path segment)."""
    def underwriting(sid: str, strategy: str = Query("both"), purchase_price: Optional[float] = None,
                     interest_rate: Optional[float] = None, ltv: Optional[float] = None,
                     amortization_years: Optional[float] = None, target_cap_rate: Optional[float] = None,
                     db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
        return INTEL.underwriting(db, _org(db, user), kind, sid, strategy,
                                  _assume(purchase_price, interest_rate, ltv, amortization_years, target_cap_rate))

    def report(sid: str, strategy: str = Query("both"), format: str = Query("json"),
               purchase_price: Optional[float] = None, interest_rate: Optional[float] = None,
               ltv: Optional[float] = None, amortization_years: Optional[float] = None,
               target_cap_rate: Optional[float] = None,
               db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
        rep = INTEL.report(db, _org(db, user), kind, sid, strategy,
                           _assume(purchase_price, interest_rate, ltv, amortization_years, target_cap_rate))
        return HTMLResponse(INTEL.report_html(rep)) if format == "html" else rep

    router.add_api_route("/%s/{sid}/underwriting" % kind, underwriting, methods=["GET"],
                         name="intel_underwriting_%s" % kind)
    router.add_api_route("/%s/{sid}/report" % kind, report, methods=["GET"], name="intel_report_%s" % kind)


_routes("evosense")
_routes("deal")


@router.get("/deal/{deal_id}/investor-report")
def investor_report(deal_id: str, format: str = Query("json"), db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_or_observer)):
    rep = INTEL.investor_report(db, _org(db, user), deal_id)
    if format == "html":
        return HTMLResponse(INTEL.investor_report_html(rep))
    return rep
