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

    # THE DEMO SUITE'S SEEDER. Same category as the two fixture builders above,
    # and the reason is worth stating rather than inheriting: the tenants it
    # writes into are flagged `is_demo`, have no plan, no subscription and no
    # Stripe customer, and nobody is ever billed for a seat in them. Counting a
    # fictional advisor against a plan limit would be counting a seat that does
    # not exist against a plan that does not exist.
    #
    # It is NOT an exemption for demo ACTIONS: `demo_actions.py` creates no
    # User and no Lead, deliberately — a presenter can move a deal, send a
    # simulated message and book an appointment, and cannot conjure a person.
    "services/demo_environment.py": "Demo Suite seeder; writes only into is_demo tenants, which have no plan and no billable seats",

    # Brand-sales staff live at SCOPE_BRAND_SALES_ORG with organization_id
    # NULL. They are not seats in any customer's plan.
    "services/sales_staff.py": "brand sales-org staff; organization_id is NULL, not a customer seat",

    # Retired, unreachable. The live POST /onboarding/register raises 410; the
    # old body is kept as documentation. Asserted below to stay unreachable.
    "routers/onboarding_router.py": "retired implementation, unreachable behind a 410",

    # services/tenancy.py was exempted here for a docstring example. The file was
    # deleted in 0fef68b ("REMOVE: delete 7 dead-code files"), and the entry
    # outlived it — which is exactly what test_exempt_list_has_no_stale_entries
    # exists to catch. An exemption for a file that no longer exists is not
    # harmless: it is a standing permission slip nobody can review, and if the
    # path is ever reused the new file inherits the waiver silently.
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
    """Either guard counts, because there are two doors into one decision.

    `plan_limits` is the ceiling itself. `lead_capacity` is the arrival policy
    built on top of it - it decides hold-vs-refuse and then calls plan_limits
    to answer "is there room". A file that reaches either one has asked the
    question; a file that reaches neither has not, and that is the only thing
    this test is looking for.
    """
    return "plan_limits" in src or "lead_capacity" in src


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


# Paths where a prospect ARRIVES rather than being typed in. Each must reach
# lead_capacity.hold_if_over_capacity - i.e. keep the lead - and must NOT
# refuse it. Listed explicitly so that adding a new webhook without a hold is
# a decision somebody has to make here, in the open.
INBOUND_PATHS = {
    "routers/social_webhooks_router.py",
    "routers/fiber_intake_router.py",
    "routers/lead_scraper_router.py",
    "services/crm_service.py",
    "services/tenant_scheduling.py",
}


def test_every_inbound_arrival_path_holds_rather_than_refuses():
    """THE policy regression.

    The first ceiling refused every path equally, public webhooks included -
    a real family filled in a form and the platform threw them away over a
    billing number. If one of these files ever calls a refusing guard again
    instead of holding, that decision comes back silently.
    """
    wrong = []
    for rel in sorted(INBOUND_PATHS):
        p = os.path.join(APP, rel.replace("/", os.sep))
        src = open(p, encoding="utf-8").read()

        if "hold_if_over_capacity" not in src:
            wrong.append(f"{rel}: does not hold inbound leads over capacity")

        # The refusing guards. `require_capacity(` and
        # `require_capacity_user_initiated(` both raise; neither belongs on a
        # path where nobody is watching to be told.
        if "require_capacity" in src:
            wrong.append(
                f"{rel}: calls a REFUSING capacity guard. Inbound arrival "
                f"must hold the prospect, never discard it.")

    assert not wrong, "\n  ".join([""] + wrong)


def test_user_initiated_paths_refuse_rather_than_hold():
    """The other half. Somebody is present, so tell them.

    Silently holding a lead the user believes they just created would be its
    own kind of lie.
    """
    for rel in ("routers/fiber_leads_router.py",):
        src = open(os.path.join(APP, rel.replace("/", os.sep)),
                   encoding="utf-8").read()
        assert "require_capacity_user_initiated" in src, (
            f"{rel} is a user-initiated create path and must refuse with a "
            f"structured PLAN_CAPACITY_REACHED, not hold silently.")


def test_the_held_state_is_excluded_from_every_paid_resource_path():
    """`dnc` is enforced by a check at each consumer; so is the hold.

    If a consumer gains a dnc check but not a hold check, the customer gets
    billed for outreach on a prospect their plan does not cover.
    """
    must_gate = {
        "services/sms_service.py": "SMS",
        "services/cadence_service.py": "cadence",
        "services/ai_conversation_service.py": "AI conversations",
        "services/voice_orchestrator.py": "voice",
        "services/pipeline_service.py": "pipeline outreach",
        "services/qualification.py": "qualification",
        "services/compliance_service.py": "the compliance preflight",
        "routers/auto_send_router.py": "auto-send selection",
    }
    missing = []
    for rel, label in must_gate.items():
        src = open(os.path.join(APP, rel.replace("/", os.sep)),
                   encoding="utf-8").read()
        if "is_held" not in src and "capacity_state" not in src:
            missing.append(f"{rel} ({label})")

    assert not missing, (
        "These paths can spend money on a lead but do not check the capacity "
        "hold:\n  " + "\n  ".join(missing))


def test_bypass_reasons_are_all_role_restricted_and_explained():
    from app.services import plan_limits
    for name, spec in plan_limits.BYPASS_REASONS.items():
        assert spec.get("roles"), f"bypass {name!r} has no role restriction"
        assert all(r for r in spec["roles"]), f"bypass {name!r} has an empty role"
        assert spec.get("why"), f"bypass {name!r} has no stated reason"
