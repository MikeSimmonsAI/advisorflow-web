# leads_router.py — thin assembler (LEAD-02 split)
#
# Route registration order is preserved from the original file so that
# FastAPI's first-match routing still resolves fixed paths before /{lead_id}.
from fastapi import APIRouter

from app.routers.leads_import_router  import router as _import_router
from app.routers.leads_query_router   import router as _query_router
from app.routers.leads_detail_router  import router as _detail_router
from app.routers.leads_dedup_router   import router as _dedup_router
from app.routers.leads_crud_router    import router as _crud_router
from app.routers.leads_public_router  import router as _public_router

router = APIRouter(prefix="/leads", tags=["leads"])

# Same order as the original file: fixed-path groups before parameterised.
router.include_router(_import_router)
router.include_router(_query_router)
router.include_router(_detail_router)
router.include_router(_dedup_router)
router.include_router(_crud_router)
router.include_router(_public_router)
