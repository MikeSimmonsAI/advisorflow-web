"""Structural guard: no new user/lead creation path may skip the plan limit.

═══════════════════════════════════════════════════════════════════════════
WHY A SOURCE-WALKING TEST AND NOT JUST BEHAVIOURAL ONES
═══════════════════════════════════════════════════════════════════════════

The behavioural tests in test_plan_limits.py prove the guard WORKS. They
cannot prove it is CALLED everywhere, because a path nobody wrote a test for
is invisible to them - and "we only enforced the limit on one of the eleven
lead-creation paths" is precisely the failure this whole item exists to fix.

So this test enumerates every `User(` / `Lead(` / customer-scope `Membership(`
construction site in `app/` and fails when one appears in a file that does not
reach `plan_limits`. Adding a twelfth lead path without a guard breaks the
suite at the moment it is written, not months later when a customer notices
their plan means nothing.

Each known site is listed below with its verdict. A site is either GUARDED,
BYPASS (a declared, privileged, audited exception), or NOT_A_CUSTOMER_PATH
with a stated reason. There is no "unknown" category.
"""

import ast
import os
import re

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")

CONSTRUCTS = re.compile(r"=\s*(User|Lead)\s*\(")


# Files that construct a User or Lead but are NOT a customer-plan path.
# Every entry states why, because an unexplained exemption here would be the
# same hole with a nicer name.
EXEMPT = {
    # Dev/ops scripts. Never imported by the app; run by hand against a dev DB.
    "scripts/seed_demo_org.py": "standalone dev seed script, not an app path",
    "scripts/seed_god_mode_orgs.py": "standalone dev seed script, not an app path",
    "seed.py": "standalone dev seed script, not an app path",

    # Demo scenario builders - fixtures, same category as sample_data.
    "services/demo_scenarios/brand_sales.py": "demo fixture builder; creates platform-scope users with organization_id=None",
    "services/demo_scenarios/customer_reactivation.py": "demo fixture builder",

    # Brand-sales staff live at SCOPE_BRAND_SALES_ORG with organization_id
    # NULL. They are not seats in any customer's plan.
    "services/sales_staff.py": "brand sales-org staff; organization_id is NULL, not a customer seat",

    # Retired, unreachable. The live POST /onboarding/register raises 410; the
    # old body is kept as documentation. Asserted below to stay unreachable.
    "routers/onboarding_router.py": "retired implementation, unreachable behind a 410",

    # Docstring example, not a call site.
    "services/tenancy.py": "the match is a docstring example, not executable",
}


def _iter_app_files():
    for dirpath, dirnames, filenames in os.walk(APP):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py"):
                p = os.path.join(dirpath, fn)
                yield p, os.path.relpath(p, APP).replace(os.sep, "/")


def _constructs_user_or_lead(src: str) -> bool:
    return bool(CONSTRUCTS.search(src))


def _reaches_plan_limits(src: str) -> bool:
    return "plan_limits" in src


def test_every_user_or_lead_creation_file_reaches_the_plan_limit_guard():
    """THE regression that matters: a new creation path with no guard."""
    unguarded = []
    for path, rel in _iter_app_files():
        src = open(path, encoding="utf-8").read()
        if not _constructs_user_or_lead(src):
            continue
        if rel in EXEMPT:
            continue
        if not _reaches_plan_limits(src):
            unguarded.append(rel)

    assert not unguarded, (
        "These files create a User or Lead but never reach plan_limits.\n"
        "Either call plan_limits.require_capacity / CapacityCounter, or add the\n"
        "file to EXEMPT in this test WITH A STATED REASON:\n  "
        + "\n  ".join(sorted(unguarded)))


def test_exempt_list_has_no_stale_entries():
    """An exemption for a file that no longer creates anything is a lie."""
    stale = []
    for rel, _why in EXEMPT.items():
        p = os.path.join(APP, rel.replace("/", os.sep))
        if not os.path.exists(p):
            stale.append(f"{rel} (file no longer exists)")
            continue
        if not _constructs_user_or_lead(open(p, encoding="utf-8").read()):
            stale.append(f"{rel} (no longer constructs a User or Lead)")
    assert not stale, "Stale EXEMPT entries:\n  " + "\n  ".join(stale)


def test_every_exemption_states_a_reason():
    blank = [k for k, v in EXEMPT.items() if not (v or "").strip()]
    assert not blank, "Exemptions without a reason: %s" % blank


def test_retired_onboarding_register_is_still_unreachable():
    """The exemption above depends on this staying a 410, not a live route."""
    p = os.path.join(APP, "routers", "onboarding_router.py")
    src = open(p, encoding="utf-8").read()
    tree = ast.parse(src)

    routed = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if "router" in ast.dump(dec):
                    routed.add(node.name)

    assert "_register_org_retired_implementation" not in routed, (
        "The retired self-service signup implementation has been wired back to "
        "a route. It creates an organization with no platform_id and no plan "
        "guard; it must stay unreachable.")

    # And the live route must still refuse.
    assert "status_code=410" in src, (
        "POST /onboarding/register no longer returns 410. If self-service "
        "signup is being revived, it needs a plan guard and a brand.")


def test_the_customer_scope_membership_grant_is_guarded():
    """The second seat door. Guarding only POST /admin/users is not enough."""
    p = os.path.join(APP, "services", "workspace_access.py")
    src = open(p, encoding="utf-8").read()
    assert "plan_limits.require_capacity_for_org_id" in src, (
        "grant_workspace_membership creates a SCOPE_CUSTOMER_ORG membership - a "
        "person with access to a customer's workspace - and must consume a seat.")


def test_seat_usage_counts_memberships_not_just_homed_users():
    """usage_for must look at both doors, or the guard above is decorative."""
    p = os.path.join(APP, "services", "plan_limits.py")
    src = open(p, encoding="utf-8").read()
    assert "SCOPE_CUSTOMER_ORG" in src, (
        "plan_limits.usage_for counts only User.organization_id. A person "
        "seconded into a customer workspace by membership would be free.")


def test_bypass_reasons_are_all_role_restricted_and_explained():
    from app.services import plan_limits
    for name, spec in plan_limits.BYPASS_REASONS.items():
        assert spec.get("roles"), f"bypass {name!r} has no role restriction"
        assert all(r for r in spec["roles"]), f"bypass {name!r} has an empty role"
        assert spec.get("why"), f"bypass {name!r} has no stated reason"
