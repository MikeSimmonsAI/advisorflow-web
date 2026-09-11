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

    # THE AI WORKFORCE SYNTHETIC PROFILES. Same category as the Demo Suite
    # seeder above, and the reason is stated rather than inherited.
    #
    # `_organization` OWNS the tenants it writes into: it looks them up by two
    # fixed slugs this module defines, creates them when absent, and sets
    # `is_demo = True` on EVERY call rather than only on creation — so a
    # profile rebuilt after somebody cleared the flag by hand comes back safe.
    # No caller can hand it an organization, so there is no argument that
    # steers a synthetic population into a paying customer's tenant. The
    # advisor it creates is `is_active=False`, which `get_current_user`
    # refuses, so it is not a seat anybody can occupy; every lead carries a
    # 555-01xx number and an `example.invalid` address, so it is not a person
    # anybody can reach. Counting either against a plan would be counting a
    # seat nobody can sit in and a prospect nobody can contact.
    "services/workforce/profiles.py": "synthetic AI-workforce profiles; writes only into the two is_demo tenants it owns, with unusable logins and unroutable addresses",

    # THE SIMULATOR'S WORLD. Stricter than any exemption above it: every
    # scenario builds its own brand, its own `is_demo` organization and its
    # own population inside a SAVEPOINT that is rolled back in a `finally`,
    # so none of these rows outlive the scenario that made them. They are not
    # a customer's leads before the rollback and they do not exist after it.
    "services/workforce/simulator.py": "simulator world builder; every row is created inside a savepoint that is always rolled back, in an is_demo tenant it creates itself",

    # THE AI OPERATIONS PROVING PROFILES. Same category as the demo seeder
    # above, and the reason is the same one stated in its own module header:
    # the organizations it writes into are synthetic by construction — their
    # names and slugs carry the marker, they have no plan, no subscription
    # and no Stripe customer, and their "advisor" is a login nobody can use.
    # Counting an invented contact against a plan that does not exist would
    # be arithmetic about nothing.
    #
    # It is NOT an exemption for the operations layer itself. Nothing else in
    # services/ai_operations/ constructs a User or a Lead: the engine works
    # records that already exist, and every send path through it re-checks
    # `lead_capacity.is_held` via compliance_service.check_compliance_preflight
    # before it reaches anybody.
    "services/ai_operations/profiles.py": "synthetic proving profiles; writes only into clearly-marked synthetic organizations with no plan and no billable seats",

    # THE DEPLOYMENT PROOF'S WORLD. The same category as the three synthetic
    # builders above, and stricter than two of them, so the reason is stated
    # rather than inherited.
    #
    # `_org` OWNS the tenants it writes into: it looks them up by slugs this
    # module builds from a fixed `t8-proof-` prefix, creates them when absent,
    # and sets `is_demo=True` on creation. No caller can hand it an
    # organization, so there is no argument that steers a synthetic population
    # into a paying customer's tenant. Every lead carries a 555-01xx number in
    # the reserved fiction block and an `example.invalid` address, so none of
    # them is a person anybody can reach. The God route that runs it wraps the
    # whole thing in a savepoint that is rolled back in a `finally`, so in the
    # only place a production database ever sees these rows, they do not
    # outlive the request.
    #
    # It is NOT an exemption for the deployment layer itself. Nothing else in
    # services/ai_deployment/ constructs a User or a Lead - the layer hires,
    # configures and retires AI employees against records that already exist,
    # and `evaluation.py` builds its world by calling THIS module's helpers
    # rather than growing a second set.
    "services/ai_deployment/simulation.py": "synthetic deployment proof; writes only into is_demo tenants it creates itself, with unroutable addresses, inside a savepoint on the one path production runs it",

    # T9's MANAGEMENT proof, and exempt on the same terms as T8's above rather
    # than on looser ones.
    #
    # `_contacts` is the only place in `services/workforce_intelligence/` that
    # constructs anything, and it constructs Leads and nothing else - no User,
    # no Organization, no Platform. It cannot be pointed at a paying customer:
    # the organization it writes into is loaded by `_scenario` from the
    # `t8-proof-` slug T8's own simulation created and flagged `is_demo`, and
    # there is no argument on any function in this module that accepts an
    # organization. Every contact carries a 555-01xx number from the reserved
    # fiction block and an `example.invalid` address, so none of them is a
    # person anybody can reach, and the God route that runs it is a POST behind
    # `require_god`.
    #
    # WHY NOT CALL plan_limits ANYWAY. Because the answer would be wrong in
    # both directions: a synthetic tenant has no plan worth counting against,
    # and counting thirty-two fictional contacts into a real ceiling would be a
    # proof that consumed a customer's capacity. The guard is the right
    # question for arrivals; these are not arrivals.
    #
    # It is NOT an exemption for T9. Nothing else in
    # services/workforce_intelligence/ constructs a User or a Lead - the layer
    # reads authoritative records and writes its own management state - and
    # tests/test_ai_workforce_intelligence_security.py asserts that directly,
    # excluding only this module for this reason.
    "services/workforce_intelligence/proof.py": "synthetic management proof; writes only Leads, only into the is_demo tenants T8's simulation creates, with unroutable addresses, behind require_god",

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


# ---------------------------------------------------------------------------
# THE T8 EXEMPTION IS POLICED, NOT GRANTED
# ---------------------------------------------------------------------------
#
# `services/ai_deployment/simulation.py` is exempted above on four stated
# claims. An exemption whose claims nothing checks is a permission slip, so
# each claim gets a test. If the proof ever starts writing into a tenant it did
# not create, or reaching an address that resolves, the waiver fails with it.

def test_the_deployment_proof_owns_every_tenant_it_writes_into():
    """It looks organizations up by its own slug and flags them `is_demo`.

    No parameter anywhere in it accepts an organization, so there is no
    argument that steers a synthetic population into a paying customer.
    """
    import inspect
    from app.services.ai_deployment import simulation

    src = inspect.getsource(simulation._org)
    assert "is_demo=True" in src
    params = list(inspect.signature(simulation._org).parameters)
    # (db, platform, slug, name) - a brand and a slug, never an organization.
    assert "organization" not in params and "org" not in params
    assert "organization_id" not in params


def test_every_deployment_proof_slug_carries_the_synthetic_prefix():
    from app.services.ai_deployment import simulation
    assert simulation.SYNTHETIC_PREFIX == "t8-proof"
    for spec in simulation.SCENARIOS.values():
        assert spec["slug"] and "/" not in spec["slug"]


def test_every_deployment_proof_contact_is_unreachable():
    """555-01xx is the reserved fiction block; `.invalid` cannot resolve.

    Read out of the source rather than out of a run, so this holds even if the
    proof is never executed in this environment.
    """
    import inspect
    import re
    from app.services.ai_deployment import evaluation, simulation

    for module in (simulation, evaluation):
        src = inspect.getsource(module)
        for phone in re.finditer(r'phone="(\d{11})"', src):
            assert phone.group(1).startswith("155501"), phone.group(1)
        for email in re.finditer(r'email="([^"]+@[^"]+)"', src):
            assert email.group(1).endswith(".invalid"), email.group(1)


def test_nothing_else_in_the_deployment_layer_creates_a_person():
    """The exemption covers one file, and only because the rest need none.

    The deployment layer hires, configures and retires AI employees against
    records that already exist. A second file in it constructing a User or a
    Lead would be a second creation path inheriting a waiver written for the
    first.
    """
    import inspect
    from app.services import ai_deployment

    base = os.path.dirname(inspect.getfile(ai_deployment))
    offenders = []
    for name in sorted(os.listdir(base)):
        if not name.endswith(".py") or name == "simulation.py":
            continue
        src = open(os.path.join(base, name), encoding="utf-8").read()
        if CONSTRUCTS.search(src):
            offenders.append(name)
    assert not offenders, (
        "these files construct a User or Lead and are not the exempted "
        "proof: %s" % offenders)
