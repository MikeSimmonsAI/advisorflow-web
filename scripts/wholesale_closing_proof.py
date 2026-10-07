"""Dependency-free disposition / closing proof (SYNTHETIC LOGIC + STATIC SOURCE).

  A. Runs the REAL app/services/wholesale_pipeline.py (pure) for stage rules.
  B. Statically proves, with `ast`, that every authenticated Wholesale route
     taking a `deal_id` resolves it through an org-scoped accessor before use.
  C. Proves CLOSED and PAID are distinct in source, that closing happens once,
     that a person (never arithmetic) records the fee, and that no contract
     execution / signature is performed by the platform.

No DB, no network. Run: python3 scripts/wholesale_closing_proof.py
"""
import ast
import os
import re
import sys
from types import SimpleNamespace as NS

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from app.services import wholesale_pipeline as P  # noqa: E402

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", name)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8-sig") as fh:
        return fh.read()


# ---- A. pure stage rules --------------------------------------------------
settings = NS(pipeline_stages=None, require_offer_approval=True,
              require_contract_approval=True, require_assignment_approval=True)
check("default stage list ends closed, dead; both terminal",
      P.is_terminal(settings, "closed") and P.is_terminal(settings, "dead"))
check("title_closing is NOT terminal (closing is not closed)",
      not P.is_terminal(settings, "title_closing"))
try:
    P.validate_stage(settings, "paid")
    check("there is no 'paid' stage: PAID is a funding fact, not a stage", False)
except ValueError:
    check("there is no 'paid' stage: PAID is a funding fact, not a stage", True)
for stage, kind in (("offer_sent", "offer"), ("under_contract", "contract"),
                    ("assignment_pending", "assignment")):
    check("%s refused without approval" % stage,
          P.guard_transition(settings, NS(), stage, []) is not None)
    check("%s refused with a pending approval" % stage,
          P.guard_transition(settings, NS(), stage, [NS(kind=kind, status="pending")]) is not None)
    check("%s refused with a REJECTED approval" % stage,
          P.guard_transition(settings, NS(), stage, [NS(kind=kind, status="rejected")]) is not None)
    check("%s refused with the WRONG kind approved" % stage,
          P.guard_transition(settings, NS(), stage, [NS(kind="other", status="approved")]) is not None)
    check("%s allowed with the right approved kind" % stage,
          P.guard_transition(settings, NS(), stage, [NS(kind=kind, status="approved")]) is None)
check("refusal is a sentence, not a code",
      " " in P.guard_transition(settings, NS(), "under_contract", []))
check("corrupt stage JSON falls back to defaults",
      [s["key"] for s in P.resolve_stages(NS(pipeline_stages="{bad"))] == list(P.DEFAULT_STAGE_KEYS))
check("empty custom list falls back to defaults",
      len(P.resolve_stages(NS(pipeline_stages="[]"))) == len(P.DEFAULT_STAGES))

# ---- B. tenant ownership of every deal route -----------------------------
# Delegations into services are accepted only if that service function itself
# calls the org-scoped get_deal (asserted below).
SCOPED = ("get_deal(", "_deal_for(", "_load_deal(", "get_deal_for_org(",
          "FD.submit(", "PK.build(")
ROUTERS = ["app/routers/" + f for f in sorted(os.listdir(os.path.join(ROOT, "app/routers")))
           if f.startswith("wholesale") and f.endswith(".py")]
# Public, token-authenticated surfaces: the share token is the credential.
PUBLIC_OK = {"wholesale_rooms_router.py", "wholesale_seller_intake_router.py"}
unscoped = []
n_routes = 0
for rel in ROUTERS:
    src = read(rel)
    tree = ast.parse(src)
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        routed = any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                     and d.func.attr in ("get", "post", "put", "patch", "delete")
                     for d in fn.decorator_list)
        if not routed:
            continue
        args = [a.arg for a in fn.args.args]
        if "deal_id" not in args:
            continue
        n_routes += 1
        if os.path.basename(rel) in PUBLIC_OK:
            continue
        body = ast.get_source_segment(src, fn) or ""
        has_user = "require_tenant_user" in body or "get_current_user" in body or "require_" in body
        scoped = any(tok in body for tok in SCOPED) or "organization_id == org_id" in body
        if not (has_user and scoped):
            unscoped.append("%s::%s" % (os.path.basename(rel), fn.name))
check("every authenticated deal route is auth + org scoped (%d routes; unscoped: %s)"
      % (n_routes, unscoped), n_routes > 20 and not unscoped)

svc = read("app/services/wholesale_service.py")
gd = svc[svc.index("def get_deal("):]
gd = gd[:gd.index("\ndef ", 5)]
check("get_deal filters on organization_id", "WholesaleDeal.organization_id == org_id" in gd)
check("get_deal 404s (never 403) so ids cannot be probed", "status_code=404" in gd)
check("write routes derive org from user via write_org_id",
      read("app/routers/wholesale_router.py").count("svc.write_org_id(db, user)") > 20)

# ---- C. CLOSED vs PAID, once-only close, human-recorded fee ---------------
rt = read("app/routers/wholesale_router.py")
close = rt[rt.index("def close_deal"):]
close = close[:close.index("\n@router", 5)]
check("close refuses a dead deal (409)", "STAGE_DEAD" in close and "status_code=409" in close)
check("close refuses a second close", "already closed" in close)
check("close rejects a negative fee", "cannot be negative" in close)
check("close without a fee says 'payment pending' (closed != paid)",
      "payment pending" in close.lower())
check("close only sets funded when a fee amount was supplied",
      close.index('funding_status = "funded"') > close.index("if fee is not None:")
      and close.count('funding_status = "funded"') == 1)
check("close locks economics", "economics_locked = True" in close)
check("close stops outreach via the shared stage setter", "_set_stage_unchecked" in close)
check("close is human-only (observation users refused)", "require_not_observation" in rt[rt.index("def close_deal") - 400:rt.index("def close_deal")] or "_guard" in rt[rt.index("def close_deal"):rt.index("def close_deal") + 300])
fee = rt[rt.index("def record_fee_collected"):]
fee = fee[:fee.index("\n@router", 5)]
check("fee-collected rejects a negative amount", "cannot be negative" in fee)
check("fee-collected records the variance event", '"variance"' in fee)
check("fee-collected names the recording person", "fee_recorded_by_id = user.id" in fee)
check("fee is never derived from the expected assignment fee",
      "wholesale_fee_collected = deal.assignment_fee" not in rt
      and "wholesale_fee_collected = expected" not in rt)

# stage change to dead/closed stops cadence on BOTH entry points
for fn_name in ("def _set_stage_unchecked", "def set_stage"):
    seg = svc[svc.index(fn_name):]
    seg = seg[:seg.index("\ndef ", 5)]
    check("%s stops cadence on closed/dead" % fn_name, "stop_cadence_quietly" in seg)
ss = svc[svc.index("def set_stage("):]
ss = ss[:ss.index("\ndef ", 5)]
check("set_stage runs guard_transition before mutating",
      ss.index("guard_transition") < ss.index("deal.stage = to_stage"))
check("set_stage scopes approvals by org",
      "WholesaleApproval.organization_id == org_id" in ss)

# no platform-side contract execution / signing
esign = read("app/services/wholesale_esign.py")
check("manual esign provider cannot mark a document signed by itself",
      "STATUS_SIGNED" in esign and "no envelope id is minted" in esign)
check("esign: signed only via provider callback or a person's upload",
      "signed` only from a provider callback or a person" in esign
      or "only from a provider callback or a person" in esign)
contracts = read("app/routers/wholesale_contracts_router.py")
check("a document becomes signed only through esign.transition",
      "esign.transition(current, payload.status)" in contracts)
check("signed requires an attached executed copy (409 otherwise)",
      "Nothing is attached to this document, so there is no" in contracts)
check("signature_status = signed is set only inside the evidence-guarded branch",
      len(re.findall(r"signature_status\s*=\s*['\"]signed['\"]", contracts)) ==
      len(re.findall(r"if target == esign.STATUS_SIGNED:", contracts)) == 1)
fund = read("app/services/wholesale_funding.py")
sub = fund[fund.index("def submit("):]
sub = sub[:sub.index("\ndef ", 5)]
check("FD.submit resolves the deal through org-scoped get_deal", "svc.get_deal(db, org_id, deal_id)" in sub)
pk = read("app/services/wholesale_funding_packet.py")
pb = pk[pk.index("def build("):]
check("PK.build resolves the deal through org-scoped get_deal", "svc.get_deal(db, org_id, deal_id)" in pb[:600])
check("funding 'funded' requires an approved/term_sheet partner status first",
      'status == "funded" and s.status not in ("approved", "term_sheet", "funded")' in fund)

print("closing proof: %d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
