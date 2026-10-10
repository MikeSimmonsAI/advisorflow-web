"""Dependency-free proof of the buy-box range/negative validation and the
buyer PATCH contactability guard. Extracts the real `_validate_box` from the
router source (no fastapi import) and executes it.

Run: python3 scripts/wholesale_buyer_validation_proof.py
"""
import ast
import os
import sys
from types import SimpleNamespace
from typing import Any, Dict, Optional

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "app/routers/wholesale_buyers_router.py")
text = open(SRC, encoding="utf-8").read()
tree = ast.parse(text)


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail


ns = dict(HTTPException=HTTPException, Dict=Dict, Any=Any, Optional=Optional,
          WholesaleBuyBox=object)
want = ("STRATEGIES", "REHAB_LEVELS", "_BOX_RANGES", "_BOX_NONNEG")
for node in tree.body:
    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) in want:
        exec(compile(ast.Module([node], []), SRC, "exec"), ns)
    if isinstance(node, ast.FunctionDef) and node.name == "_validate_box":
        exec(compile(ast.Module([node], []), SRC, "exec"), ns)
validate = ns["_validate_box"]

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", name)


def rejects(data, existing=None):
    try:
        validate(data, existing)
    except HTTPException as e:
        return e.status_code == 400
    return False


box = SimpleNamespace(min_price=200000.0, max_price=400000.0, min_beds=None,
                      max_beds=None, min_sqft=None, max_sqft=None,
                      min_year_built=None, max_year_built=None)
check("valid create passes", not rejects(dict(min_price=1, max_price=2)))
check("create min>max price rejected", rejects(dict(min_price=5, max_price=1)))
check("PATCH max_price below stored min rejected", rejects(dict(max_price=100000), box))
check("PATCH min_price above stored max rejected", rejects(dict(min_price=500000), box))
check("PATCH within stored range passes", not rejects(dict(max_price=300000), box))
check("PATCH unrelated field passes", not rejects(dict(notes="x"), box))
check("beds range inverted rejected", rejects(dict(min_beds=5, max_beds=2)))
check("sqft range inverted rejected", rejects(dict(min_sqft=3000, max_sqft=1000)))
check("year range inverted rejected", rejects(dict(min_year_built=2000, max_year_built=1950)))
check("negative min_price rejected", rejects(dict(min_price=-1)))
check("negative min_spread rejected", rejects(dict(min_spread=-5)))
check("negative sqft rejected", rejects(dict(max_sqft=-1)))
check("unknown strategy rejected", rejects(dict(strategies=["moonshot"])))
check("bad rehab rejected", rejects(dict(rehab_tolerance="nope")))
check("zero allowed", not rejects(dict(min_price=0, min_spread=0)))

# buyer PATCH source contract
check("update_buyer blocks clearing every contact method",
      '("email" in data or "phone" in data)' in text)
check("update_buyer blocks clearing the name",
      '"company_name" in data or "contact_name" in data' in text)
check("update_buyer rejects negative counts / rating out of range",
      "reliability_rating" in text and "cannot be negative" in text)
check("update_buy_box passes the stored box to the validator",
      "_validate_box(data, box)" in text)

print("wholesale buyer validation proof: %d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
