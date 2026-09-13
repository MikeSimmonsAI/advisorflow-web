# leads_router.py — thin assembler (LEAD-02 split)
#
# Route registration order is preserved from the original file so that
# FastAPI's first-match routing still resolves fixed paths before /{lead_id}.
from fastapi import APIRouter, Depends

from app.routers.leads_import_router  import router as _import_router
from app.routers.leads_query_router   import router as _query_router
from app.routers.leads_detail_router  import router as _detail_router
from app.routers.leads_dedup_router   import router as _dedup_router
from app.routers.leads_crud_router    import router as _crud_router
from app.routers.leads_public_router  import router as _public_router
from app.services.entitlements import require_feature

router = APIRouter(prefix="/leads", tags=["leads"])

# THE `leads` ENTITLEMENT, ENFORCED WHERE THE ASSEMBLER CAN SEE THE SEAM.
#
# `entitlements.FEATURES` has registered `leads` all along and NOTHING checked
# it: an organization with `enabled_features = []` was refused on /campaigns,
# /crm-native and /case-files and served /leads normally. The sidebar hid
# nothing either, so a customer entitled to no modules at all was given the
# lead book of the workspace they were standing in.
#
# It is applied to the five AUTHENTICATED groups rather than to the assembler,
# because `_public_router` carries the unauthenticated marketing-site intake
# (`/leads/demo-request`, `/leads/sms-optin`). A router-level dependency here
# would apply to those too and turn every public submission into a 403 — the
# gate needs a signed-in user to have a workspace at all.
_gate = [Depends(require_feature("leads"))]

# Same order as the original file: fixed-path groups before parameterised.
router.include_router(_import_router, dependencies=_gate)
router.include_router(_query_router, dependencies=_gate)
router.include_router(_detail_router, dependencies=_gate)
router.include_router(_dedup_router, dependencies=_gate)
router.include_router(_crud_router, dependencies=_gate)
router.include_router(_public_router)
