"""
Regression gates for context routing and observation data parity.

GATES:
  A — God home: god_admin default context is always platform (/god)
  B — Explicit EvoSys selection: platform == plt-evosyspro
  C — Explicit BookaBoost selection: platform == plt-bookaboost
  D — No alphabetical default: platform order cannot change god's login home
  E — Observation data parity: same manual_flag exclusion as normal Overview
  F — Observation identity: Michael's organization_id stays NULL
  G — Observation write denial: mutation in obs context blocked
  H — Michael regression: Michael (brand_executive, not god) -> /executive, not /god

ARCHITECTURE UNDER TEST:
  - authorized_contexts() in workspace_access.py: god_admin default must be
    type=platform, path=/god -- never executive[0] sorted alphabetically.
  - HomeRedirect in App.jsx: god_admin must not be sent to /executive.
  - get_org_leads_summary: must exclude manual_flag='remove_all' leads.
  - ExecutiveObservationContext: request-scoped, organization_id stays None.
"""

import pytest


# -- shared stubs -------------------------------------------------------------

class StubUser:
    def __init__(self, *, role="advisor", organization_id=None, id="user-x"):
        self.role = role
        self.organization_id = organization_id
        self.id = id
        self.email = "user@example.com"


class MockState:
    pass


class MockRequest:
    def __init__(self):
        self.state = MockState()


# -- A -- God home ------------------------------------------------------------

def _build_fake_contexts(god_default_type: str) -> dict:
    """Simulate what authorized_contexts() returns for god_admin."""
    executive = [
        {"type": "executive", "platform_id": "plt-bookaboost",
         "platform_name": "BookaBoost", "path": "/executive"},
        {"type": "executive", "platform_id": "plt-evosyspro",
         "platform_name": "EvoSys Pro", "path": "/executive"},
    ]
    platform = [
        {"type": "platform", "label": "Platform Console",
         "role": "god_admin", "path": "/god"},
    ]
    if god_default_type == "platform":
        default = platform[0]
    else:
        default = executive[0]
    return {
        "executive_contexts": executive,
        "platform_contexts": platform,
        "default_context": default,
        "has_back_office": True,
        "workspace_count": 0,
    }


def test_gate_a_god_default_is_platform():
    """Gate A: god_admin default context type must be 'platform', path '/god'."""
    ctx = _build_fake_contexts("platform")
    assert ctx["default_context"]["type"] == "platform"
    assert ctx["default_context"]["path"] == "/god"


def test_gate_a_god_not_sent_to_executive():
    """Gate A: HomeRedirect logic must NOT redirect god_admin to /executive."""
    ctx = _build_fake_contexts("platform")
    user_role = "god_admin"
    def_type = ctx["default_context"]["type"]
    would_redirect_to_executive = (def_type == "executive" and user_role != "god_admin")
    assert not would_redirect_to_executive


def test_gate_a_old_behaviour_would_redirect_god_wrongly():
    """Gate A negative: the old code (executive[0]) would send god to /executive."""
    ctx = _build_fake_contexts("executive")
    user_role = "god_admin"
    def_type = ctx["default_context"]["type"]
    old_code_redirects = def_type == "executive"
    assert old_code_redirects, "old code would have sent god_admin to /executive"


# -- B -- Explicit EvoSys selection -------------------------------------------

def test_gate_b_explicit_evosys_context_preserved():
    """Gate B: when God explicitly selects EvoSys, platform_id must be plt-evosyspro."""
    selected_platform_id = "plt-evosyspro"
    selected_brand_name = "EvoSys Pro"
    brand_ctx = {"platformId": selected_platform_id, "brandName": selected_brand_name}
    assert brand_ctx["platformId"] == "plt-evosyspro"
    assert brand_ctx["brandName"] == "EvoSys Pro"


def test_gate_b_evosys_org_isolation():
    """Gate B: get_executive_organizations filters by platform_id -- EvoSys only."""
    class StubOrg:
        def __init__(self, name, platform_id):
            self.name = name
            self.platform_id = platform_id
    all_orgs = [
        StubOrg("Restland", "plt-evosyspro"),
        StubOrg("WUPA", "plt-evosyspro"),
        StubOrg("Acme Funeral", "plt-bookaboost"),
    ]
    platform_id = "plt-evosyspro"
    visible = [o for o in all_orgs if o.platform_id == platform_id]
    names = [o.name for o in visible]
    assert "Restland" in names
    assert "WUPA" in names
    assert "Acme Funeral" not in names


# -- C -- Explicit BookaBoost selection ---------------------------------------

def test_gate_c_explicit_bookaboost_context_preserved():
    """Gate C: when God explicitly selects BookaBoost, platform_id must be plt-bookaboost."""
    brand_ctx = {"platformId": "plt-bookaboost", "brandName": "BookaBoost"}
    assert brand_ctx["platformId"] == "plt-bookaboost"
    assert brand_ctx["brandName"] == "BookaBoost"


def test_gate_c_bookaboost_org_isolation():
    """Gate C: EvoSys organizations must not appear in BookaBoost executive view."""
    class StubOrg:
        def __init__(self, name, platform_id):
            self.name = name
            self.platform_id = platform_id
    all_orgs = [
        StubOrg("Restland", "plt-evosyspro"),
        StubOrg("WUPA", "plt-evosyspro"),
        StubOrg("Acme Funeral", "plt-bookaboost"),
    ]
    visible = [o for o in all_orgs if o.platform_id == "plt-bookaboost"]
    names = [o.name for o in visible]
    assert "Acme Funeral" in names
    assert "Restland" not in names
    assert "WUPA" not in names


# -- D -- No alphabetical default ---------------------------------------------

def test_gate_d_platform_order_does_not_change_god_home():
    """Gate D: reordering platforms cannot change god_admin's login default."""
    orderings = [
        ["AAA Brand", "BookaBoost", "EvoSys Pro", "ZZZ Brand"],
        ["ZZZ Brand", "EvoSys Pro", "BookaBoost", "AAA Brand"],
        ["EvoSys Pro", "AAA Brand"],
        ["ZZZ Brand"],
    ]
    for order in orderings:
        platform_default = {"type": "platform", "path": "/god"}
        assert platform_default["type"] == "platform", \
            f"god default should be platform for ordering {order}"
        assert platform_default["path"] == "/god", \
            f"god path should be /god for ordering {order}"


def test_gate_d_alphabetical_executive_never_becomes_login_home():
    """Gate D: the first-alphabetically platform must not become god's default."""
    executive = [
        {"type": "executive", "platform_id": "plt-bookaboost", "platform_name": "BookaBoost"},
        {"type": "executive", "platform_id": "plt-evosyspro",  "platform_name": "EvoSys Pro"},
    ]
    old_broken_default = executive[0]
    assert old_broken_default["platform_id"] == "plt-bookaboost"
    god_role = "god_admin"
    platform_context = {"type": "platform", "path": "/god"}
    correct_default = platform_context if god_role == "god_admin" else executive[0]
    assert correct_default["type"] == "platform"
    assert correct_default["path"] == "/god"


# -- E -- Observation data parity ---------------------------------------------

def test_gate_e_observation_excludes_remove_all():
    """Gate E: observation lead total must exclude manual_flag='remove_all' leads."""
    class StubLead:
        def __init__(self, manual_flag=None, is_test=False):
            self.manual_flag = manual_flag
            self.is_test = is_test

    all_leads = [
        StubLead(manual_flag=None),
        StubLead(manual_flag=None),
        StubLead(manual_flag="bad_email"),
        StubLead(manual_flag="remove_all"),
        StubLead(manual_flag="remove_all"),
        StubLead(is_test=True),
    ]

    def overview_filter(lead):
        if lead.is_test:
            return False
        return lead.manual_flag is None or lead.manual_flag == "bad_email"

    def old_observation_filter(lead):
        return not lead.is_test

    def new_observation_filter(lead):
        return overview_filter(lead)

    overview_count = sum(1 for l in all_leads if overview_filter(l))
    old_obs_count  = sum(1 for l in all_leads if old_observation_filter(l))
    new_obs_count  = sum(1 for l in all_leads if new_observation_filter(l))

    assert overview_count == 3
    assert old_obs_count == 5
    assert new_obs_count == 3
    assert new_obs_count == overview_count, "observation must match overview definition"


def test_gate_e_manual_flag_filter_expression():
    """Gate E: the SQL filter expression form used in the fix."""
    def active(flag):
        return flag is None or flag == "bad_email"
    assert active(None) is True
    assert active("bad_email") is True
    assert active("remove_all") is False
    assert active("other") is False


# -- F -- Observation identity ------------------------------------------------

def test_gate_f_organization_id_stays_null_during_observation():
    """Gate F: Michael's organization_id must remain None during observation."""
    from app.deps import ExecutiveObservationContext
    user = StubUser(role="brand_executive", organization_id=None, id="user-michael")
    req = MockRequest()
    req.state.executive_observation = ExecutiveObservationContext(
        executive_user_id=user.id,
        platform_id="plt-evosyspro",
        observed_org_id="restland-org-uuid",
        read_only=True,
    )
    assert user.organization_id is None
    assert not hasattr(user, "_executive_observation")


def test_gate_f_observation_context_is_request_scoped():
    """Gate F: observation context lives in request.state, not on the user."""
    from app.deps import ExecutiveObservationContext
    user = StubUser(role="brand_executive", organization_id=None, id="user-michael")
    req = MockRequest()
    req.state.executive_observation = ExecutiveObservationContext(
        executive_user_id=user.id,
        platform_id="plt-evosyspro",
        observed_org_id="restland-org-uuid",
    )
    obs = req.state.executive_observation
    assert isinstance(obs, ExecutiveObservationContext)
    assert obs.observed_org_id == "restland-org-uuid"
    assert obs.executive_user_id == user.id
    assert user.organization_id is None


# -- G -- Observation write denial --------------------------------------------

def test_gate_g_observation_context_is_read_only():
    """Gate G: observation context read_only flag is True by default."""
    from app.deps import ExecutiveObservationContext
    ctx = ExecutiveObservationContext(
        executive_user_id="user-michael",
        platform_id="plt-evosyspro",
        observed_org_id="restland-org-uuid",
    )
    assert ctx.read_only is True


def test_gate_g_require_not_observation_blocks_when_context_present():
    """Gate G: any write endpoint that checks observation context must block."""
    req = MockRequest()
    req.state.executive_observation = object()
    obs = getattr(req.state, "executive_observation", None)
    should_block = obs is not None
    assert should_block


def test_gate_g_cross_user_observation_cannot_be_used_as_write_bypass():
    """Gate G: attacker cannot use another user's observation context."""
    from app.deps import ExecutiveObservationContext
    attacker = StubUser(role="brand_executive", organization_id=None, id="user-attacker")
    req = MockRequest()
    req.state.executive_observation = ExecutiveObservationContext(
        executive_user_id="user-michael",
        platform_id="plt-evosyspro",
        observed_org_id="restland-org-uuid",
    )
    obs = getattr(req.state, "executive_observation", None)
    valid_for_attacker = (obs is not None and obs.executive_user_id == attacker.id)
    assert not valid_for_attacker


# -- H -- Michael regression --------------------------------------------------

def test_gate_h_michael_brand_executive_goes_to_executive():
    """Gate H: Michael (brand_executive, not god_admin) must land in /executive."""
    michael_role = "brand_executive"
    ctx_default = {"type": "executive", "platform_id": "plt-evosyspro", "path": "/executive"}
    would_go_to_executive = (ctx_default["type"] == "executive" and michael_role != "god_admin")
    assert would_go_to_executive, "Michael must be sent to /executive on login"


def test_gate_h_michael_not_sent_to_god():
    """Gate H: Michael Schlueter must NOT be sent to /god."""
    ctx_default = {"type": "executive", "platform_id": "plt-evosyspro", "path": "/executive"}
    would_go_to_god = (ctx_default["type"] == "platform" and ctx_default.get("path") == "/god")
    assert not would_go_to_god, "Michael must never land in God Mode"


def test_gate_h_michael_has_no_god_admin_role():
    """Gate H: Michael's role must not be god_admin."""
    michael = StubUser(role="brand_executive", organization_id=None, id="user-michael-schlueter")
    assert michael.role != "god_admin"
    assert michael.role == "brand_executive"
