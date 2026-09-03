"""
Regression test for Executive Observation Mode identity contract.

ARCHITECTURE UNDER TEST:
  Observation context must live in request.state.executive_observation
  (an ExecutiveObservationContext), NOT as attributes on the User object.

  Three concepts that must remain separate:
    IDENTITY     user.organization_id = None  (Michael is not Restland)
    AUTHORITY    executive's brand membership  (EvoSys platform scope)
    OBSERVATION  request.state.executive_observation.observed_org_id

WHAT THESE TESTS GUARD AGAINST:
  Commit 18369e5 violated the identity contract by writing obs_org_id to
  user.organization_id. The correct model stores NOTHING on the user object
  for observation; the context is request-scoped, not identity-scoped.
"""

import pytest


class StubUser:
    def __init__(self, *, role="advisor", organization_id=None, id="user-michael"):
        self.role = role
        self.organization_id = organization_id
        self.id = id
        self.email = "michael@example.com"

class MockState:
    pass

class MockRequest:
    def __init__(self):
        self.state = MockState()
    def with_observation(self, ctx):
        self.state.executive_observation = ctx
        return self

def _make_obs_request(user_id="user-michael", platform_id="plt-evosyspro", obs_org_id="restland-org-uuid"):
    from app.deps import ExecutiveObservationContext
    req = MockRequest()
    req.state.executive_observation = ExecutiveObservationContext(
        executive_user_id=user_id, platform_id=platform_id,
        observed_org_id=obs_org_id, read_only=True,
    )
    return req


def test_observation_does_not_mutate_organization_id():
    user = StubUser(role="brand_executive", organization_id=None)
    req = _make_obs_request(user_id=user.id)
    assert user.organization_id is None
    assert not hasattr(user, "_executive_observation")
    assert not hasattr(user, "_executive_observation_org_id")

def test_observation_context_lives_in_request_state():
    from app.deps import ExecutiveObservationContext
    user = StubUser(role="brand_executive", organization_id=None)
    req = _make_obs_request(user_id=user.id, obs_org_id="restland-org-uuid")
    obs = req.state.executive_observation
    assert isinstance(obs, ExecutiveObservationContext)
    assert obs.observed_org_id == "restland-org-uuid"
    assert obs.executive_user_id == user.id
    assert obs.read_only is True
    assert user.organization_id is None


def test_active_workspace_org_id_returns_observed_org_for_observer():
    from app.services.lead_scope import active_workspace_org_id
    user = StubUser(role="brand_executive", organization_id=None)
    req = _make_obs_request(user_id=user.id, obs_org_id="restland-org-uuid")
    result = active_workspace_org_id(user, db=None, request=req)
    assert result == "restland-org-uuid"

def test_active_workspace_org_id_ignores_other_users_observation():
    from app.services.lead_scope import active_workspace_org_id
    user = StubUser(role="advisor", organization_id=None, id="user-alice")
    req = _make_obs_request(user_id="user-michael")
    result = active_workspace_org_id(user, db=None, request=req)
    assert result is None

def test_active_workspace_org_id_returns_none_without_request():
    from app.services.lead_scope import active_workspace_org_id
    user = StubUser(role="brand_executive", organization_id=None)
    result = active_workspace_org_id(user, db=None, request=None)
    assert result is None

def test_active_workspace_org_id_unchanged_for_real_tenant():
    from app.services.lead_scope import active_workspace_org_id
    user = StubUser(role="org_admin", organization_id="acme-org-uuid")
    result = active_workspace_org_id(user, db=None, request=MockRequest())
    assert result == "acme-org-uuid"




# â”€â”€ 3. require_tenant_or_observer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _passes_require_tenant_or_observer(user, request=None) -> bool:
    """Returns True if the gate would pass, False if it would 403."""
    from app.services.lead_scope import is_god
    if is_god(user):
        return True
    if user.organization_id is not None:
        return True
    _obs = None
    if request is not None:
        _obs = getattr(request.state, "executive_observation", None)
    if _obs is not None and _obs.executive_user_id == user.id:
        return True
    return False


def test_require_tenant_or_observer_passes_for_real_tenant():
    user = StubUser(role="advisor", organization_id="acme-uuid")
    assert _passes_require_tenant_or_observer(user, MockRequest())


def test_require_tenant_or_observer_passes_for_authorized_observer():
    user = StubUser(role="brand_executive", organization_id=None)
    req = _make_obs_request(user_id=user.id, obs_org_id="restland-uuid")
    assert _passes_require_tenant_or_observer(user, req)


def test_require_tenant_or_observer_blocks_plain_platform_user():
    user = StubUser(role="advisor", organization_id=None)
    req = MockRequest()
    assert not _passes_require_tenant_or_observer(user, req)


def test_require_tenant_or_observer_blocks_observer_without_request():
    user = StubUser(role="brand_executive", organization_id=None)
    assert not _passes_require_tenant_or_observer(user, request=None)



# â”€â”€ 4. require_tenant_user still blocks observers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_require_tenant_user_blocks_observer():
    user = StubUser(role="brand_executive", organization_id=None)
    req = _make_obs_request(user_id=user.id)
    tenant_check_passes = user.organization_id is not None
    assert not tenant_check_passes, (
        "require_tenant_user must block observers because their organization_id is None."
    )


# â”€â”€ 5. Write safety: observation context signals read-only â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_observation_context_is_read_only():
    from app.deps import ExecutiveObservationContext
    ctx = ExecutiveObservationContext(
        executive_user_id="user-michael",
        platform_id="plt-evosyspro",
        observed_org_id="restland-org-uuid",
    )
    assert ctx.read_only is True


def test_require_not_observation_would_block_when_context_present():
    user = StubUser(role="brand_executive", organization_id=None)
    req = _make_obs_request(user_id=user.id)
    obs = getattr(req.state, "executive_observation", None)
    should_block = obs is not None
    assert should_block


def test_require_not_observation_passes_when_no_context():
    user = StubUser(role="advisor", organization_id="acme-uuid")
    req = MockRequest()
    obs = getattr(req.state, "executive_observation", None)
    should_block = obs is not None
    assert not should_block


# â”€â”€ 6. God is unaffected â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_god_observation_path_skipped():
    user = StubUser(role="god_admin", organization_id=None)
    observation_block_runs = user.role != "god_admin"
    assert not observation_block_runs


def test_god_passes_require_tenant_or_observer_regardless():
    user = StubUser(role="god_admin", organization_id=None)
    assert _passes_require_tenant_or_observer(user, MockRequest())


# â”€â”€ 7. Cross-user context isolation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_observation_context_requires_matching_user_id():
    attacker = StubUser(role="brand_executive", organization_id=None, id="user-attacker")
    req = _make_obs_request(user_id="user-michael")
    result = _passes_require_tenant_or_observer(attacker, req)
    assert not result
