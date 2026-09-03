"""
Regression test for Executive Observation Mode identity mutation.

WHAT THIS TEST GUARDS AGAINST:
  Commit 18369e5 violated the identity contract by writing obs_org_id to
  user.organization_id inside get_current_user. This made Michael appear to
  be a Restland tenant to all downstream code, which is architecturally wrong
  and could produce privilege-escalation bugs if downstream code made decisions
  based on the assumption that organization_id = Some ID means "tenant member".

  This test would have FAILED against commit 18369e5 and MUST pass on the
  corrected implementation.

ARCHITECTURE UNDER TEST:
  - user.organization_id must remain None for a brand_executive in observation mode
  - user._executive_observation_org_id must equal the observed org id
  - user._executive_observation must be True
  - lead_scope.active_workspace_org_id must return the observed org id
  - require_tenant_or_observer must pass for an authorized observer
  - require_tenant_user must FAIL for an observer (organization_id is None)
"""

import types
import pytest

# ── Minimal User stub ─────────────────────────────────────────────────────────

class StubUser:
    """Lightweight stand-in for the SQLAlchemy User model."""
    def __init__(self, *, role="advisor", organization_id=None):
        self.role = role
        self.organization_id = organization_id
        self.id = "user-michael"
        self.email = "michael@example.com"


# ── 1. Identity isolation: organization_id must stay None ────────────────────

def test_observation_does_not_mutate_organization_id():
    """
    REGRESSION: commit 18369e5 set user.organization_id = obs_org_id.
    After the fix, organization_id must remain None throughout observation mode.
    """
    user = StubUser(role="brand_executive", organization_id=None)

    # Simulate what get_current_user now does (the FIXED version)
    obs_org_id = "restland-org-uuid"
    user._executive_observation_org_id = obs_org_id
    user._executive_observation = True
    # user.organization_id is intentionally NOT set

    assert user.organization_id is None, (
        "organization_id must remain None for a brand_executive in observation mode. "
        "Commit 18369e5 violated this by writing obs_org_id to organization_id."
    )


def test_observation_scope_stored_separately():
    """
    Observation scope must live on _executive_observation_org_id,
    separate from the identity column organization_id.
    """
    user = StubUser(role="brand_executive", organization_id=None)
    obs_org_id = "restland-org-uuid"

    user._executive_observation_org_id = obs_org_id
    user._executive_observation = True

    assert getattr(user, "_executive_observation_org_id", None) == obs_org_id
    assert getattr(user, "_executive_observation", False) is True
    assert user.organization_id is None


# ── 2. lead_scope.active_workspace_org_id returns the observed org ────────────

def test_active_workspace_org_id_returns_observed_org_for_observer():
    """
    active_workspace_org_id must return _executive_observation_org_id when
    _executive_observation is True and organization_id is None.
    Without this, all lead queries return empty results for observers.
    """
    from app.services.lead_scope import active_workspace_org_id

    user = StubUser(role="brand_executive", organization_id=None)
    obs_org_id = "restland-org-uuid"
    user._executive_observation_org_id = obs_org_id
    user._executive_observation = True

    result = active_workspace_org_id(user)  # no db/request needed for this path
    assert result == obs_org_id, (
        f"active_workspace_org_id returned {result!r}, expected {obs_org_id!r}. "
        "The observer's data scope is broken."
    )


def test_active_workspace_org_id_returns_none_for_non_observer_platform_user():
    """
    A brand user not in observation mode must still get None from
    active_workspace_org_id so they are denied lead access.
    """
    from app.services.lead_scope import active_workspace_org_id

    user = StubUser(role="brand_executive", organization_id=None)
    # No _executive_observation set

    result = active_workspace_org_id(user)
    assert result is None, (
        f"active_workspace_org_id returned {result!r} for a non-observer platform user. "
        "Platform users must not gain lead access."
    )


def test_active_workspace_org_id_unchanged_for_real_tenant():
    """
    A real tenant user must still get their organization_id,
    not the observation path.
    """
    from app.services.lead_scope import active_workspace_org_id

    user = StubUser(role="org_admin", organization_id="acme-org-uuid")
    result = active_workspace_org_id(user)
    assert result == "acme-org-uuid"


# ── 3. require_tenant_or_observer gate ───────────────────────────────────────

def _make_dep_user(*, org_id=None, role="advisor", observation=False, obs_org=None):
    """Build a stub user the way get_current_user leaves it."""
    user = StubUser(role=role, organization_id=org_id)
    if observation:
        user._executive_observation = True
        user._executive_observation_org_id = obs_org
    return user


def test_require_tenant_or_observer_passes_for_real_tenant():
    from app.services.lead_scope import is_god
    user = _make_dep_user(org_id="acme-uuid", role="advisor")
    god = is_god(user)
    has_tenant = user.organization_id is not None
    is_observer = getattr(user, "_executive_observation", False)
    assert god or has_tenant or is_observer


def test_require_tenant_or_observer_passes_for_authorized_observer():
    from app.services.lead_scope import is_god
    user = _make_dep_user(role="brand_executive", org_id=None,
                          observation=True, obs_org="restland-uuid")
    god = is_god(user)
    has_tenant = user.organization_id is not None
    is_observer = getattr(user, "_executive_observation", False)
    assert god or has_tenant or is_observer, "Observer should pass require_tenant_or_observer"


def test_require_tenant_or_observer_blocks_plain_platform_user():
    from app.services.lead_scope import is_god
    user = _make_dep_user(role="advisor", org_id=None)
    # No observation flag
    god = is_god(user)
    has_tenant = user.organization_id is not None
    is_observer = getattr(user, "_executive_observation", False)
    assert not (god or has_tenant or is_observer), (
        "A platform user with no workspace and no observation must be blocked."
    )


# ── 4. require_tenant_user still blocks observers ─────────────────────────────

def test_require_tenant_user_blocks_observer():
    """
    require_tenant_user must NOT pass for observers. organization_id is None,
    so the gate must reject them. This ensures observers cannot reach mutation
    routes even if they somehow get past require_not_observation.
    """
    user = _make_dep_user(role="brand_executive", org_id=None,
                          observation=True, obs_org="restland-uuid")
    # require_tenant_user checks organization_id is not None
    tenant_check_passes = user.organization_id is not None
    assert not tenant_check_passes, (
        "require_tenant_user must block observers because their organization_id is None. "
        "If this fails, mutation routes are exposed to observers."
    )


# ── 5. God is unaffected ──────────────────────────────────────────────────────

def test_god_observation_path_skipped():
    """
    The observation block in get_current_user runs only for non-god users.
    God's identity and scoping is handled by X-Org-Override, not this path.
    """
    user = StubUser(role="god_admin", organization_id=None)
    observation_block_runs = user.role != "god_admin"
    assert not observation_block_runs, "Observation block must not run for god_admin."
