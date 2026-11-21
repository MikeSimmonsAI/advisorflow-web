"""The old EvoSys BD router and the EvoSys Sales router share the /sales/evosys prefix.
main.py includes BD first, so any path both define is answered by BD and the Sales
handler never runs (this hid GET /calls from the Calls screen)."""
from app.routers.evosys_bd_router import router as bd
from app.routers.evosys_sales_router import router as sales


def _paths(r):
    return {(m, route.path) for route in r.routes for m in getattr(route, "methods", ()) or ()}


def test_no_shared_paths():
    assert _paths(bd) & _paths(sales) == set()
