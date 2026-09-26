"""EvoSense multi-tenant acceptance — the approval's section 9.

EvoSys Wholesale / EvoSense is a platform product; EVO Integrated Solutions
is its first tenant, not its owner. These tests stand up two independent
wholesalers on the same public sources and prove:

    1. a workspace without the Wholesale entitlement reaches no EvoSense API
    2. tenant A and tenant B see none of each other's properties, owners,
       scores, strategies (rule sets), controls / budgets, scoring options,
       events or re-derivation runs  (every id-bearing route is attacked in
       test_wholesale_cross_tenant.py; this file checks the LISTS and the
       configuration, which that attack cannot reach)
    3. A's scoring override changes A's score version only
    4. a new tenant starts with the platform defaults and zero data
    5. A's re-derivation, apply and rollback leave B untouched
    (the platform-wide source block reaching every tenant without leaking who
    tripped it is test_evosense_property_truth.py::
    test_the_block_reaches_every_tenant_without_leaking_who_tripped_it)
"""
import json

import pytest

from app.main import app
from app.models.evosense_models import EvoSenseProperty, EvoSenseScore, EvoSenseSignal
from app.services.evosense import scoring as SC

from test_evosense_dfw_sources import (ALL_DFW, _admin, _enable, _pilot, dfw_files, fake_apis,  # noqa: F401
                                       no_network, ok)
from test_evosense_property_truth import _tenant
from test_evosense_reprocess import _make_legacy, _state

BASE = "/wholesale/evosense"


def _param_free_gets():
    out = []
    for r in app.routes:
        path = getattr(r, "path", "")
        if path.startswith(BASE) and "{" not in path and "GET" in (getattr(r, "methods", None) or ()):
            out.append(path)
    return sorted(set(out))


def _world(client, h):
    return {
        "inbox": ok(client.get(BASE + "/inbox", headers=h)),
        "strategies": ok(client.get(BASE + "/strategies", headers=h, params={"include_archived": True})),
        "controls": ok(client.get(BASE + "/controls", headers=h)),
        "events": ok(client.get(BASE + "/events", headers=h)),
        "reprocess": ok(client.get(BASE + "/reprocess", headers=h)),
        "reviews": ok(client.get(BASE + "/identity-reviews", headers=h)),
    }


@pytest.fixture()
def two_tenants(client, db_session, sample_org, dfw_files, fake_apis):
    _, ha = _admin(db_session, sample_org)
    _enable(client, ha, *ALL_DFW)
    sa = _pilot(client, ha)
    ok(client.post(BASE + "/strategies/%s/hunt" % sa["id"], headers=ha))
    org_b, hb = _tenant(db_session, "Second Wholesaler", "second-wholesaler-mt", "admin@second-mt.test")
    return {"a": sample_org, "ha": ha, "sa": sa, "b": org_b, "hb": hb}


def test_a_workspace_without_wholesale_reaches_no_evosense_api(client, db_session, sample_org, auth_headers):
    sample_org.enabled_features = json.dumps(["leads"])
    db_session.commit()
    paths = _param_free_gets()
    assert BASE + "/inbox" in paths and BASE + "/reprocess" in paths
    for path in paths:
        r = client.get(path, headers=auth_headers)
        assert r.status_code == 402, (path, r.status_code)
    for method, path, body in (("post", BASE + "/strategies", {"name": "x"}),
                               ("post", BASE + "/reprocess/dry-run", {}),
                               ("patch", BASE + "/controls", {"paused_all": True}),
                               ("patch", BASE + "/providers", {"key": "dcad", "enabled": True})):
        assert getattr(client, method)(path, headers=auth_headers, json=body).status_code == 402, path


def test_a_new_tenant_gets_platform_defaults_and_zero_data(client, two_tenants):
    hb = two_tenants["hb"]
    w = _world(client, hb)
    assert w["inbox"]["total"] == 0 and w["strategies"]["items"] == []
    assert w["events"]["items"] == [] and w["reprocess"]["items"] == []
    ctl = w["controls"]
    assert ctl["score_weights"] is None
    assert ctl["scoring_options"] == SC.DEFAULT_OPTIONS and ctl["score_version"] == SC.PO_VERSION
    reg = ok(client.get(BASE + "/sources", headers=hb))["sources"]
    on = sorted(r["key"] for r in reg if r["enabled"])
    # only operator-entry channels are on; every network / paid source starts off
    assert on == ["csv_import", "dallas_foreclosure_manual", "dallas_tax_manual", "manual"], on
    prov = ok(client.get(BASE + "/providers", headers=hb))
    assert prov["real_connectors"] == []


def test_lists_and_configuration_never_cross(client, db_session, two_tenants):
    a, b, ha, hb = two_tenants["a"], two_tenants["b"], two_tenants["ha"], two_tenants["hb"]
    ok(client.patch(BASE + "/controls", headers=ha, json={
        "score_weights": {"TAX_DELINQUENT": 25}, "scoring_options": {"exclude_institutional": False},
        "org_daily_budget_cents": 1234}))
    ok(client.post(BASE + "/reprocess/dry-run", headers=ha, json={}))
    wa, wb = _world(client, ha), _world(client, hb)
    assert wa["inbox"]["total"] > 0 and wb["inbox"]["total"] == 0
    a_ids = {p.id for p in db_session.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == a.id)}
    assert not any(pid in json.dumps(wb) for pid in a_ids)
    assert a.name not in json.dumps(wb)
    assert wb["reprocess"]["items"] == [] and wb["events"]["items"] == []
    cb = wb["controls"]
    assert cb["score_weights"] is None and cb["scoring_options"] == SC.DEFAULT_OPTIONS
    assert cb["score_version"] == SC.PO_VERSION
    assert cb.get("org_daily_budget_cents") != 1234
    assert wa["controls"]["org_daily_budget_cents"] == 1234
    ca = wa["controls"]
    assert ca["score_version"] != SC.PO_VERSION and ca["score_version"].startswith(SC.PO_VERSION + "+w")
    # B's source switches are B's: A enabled everything, B enabled nothing
    reg_b = {r["key"]: r for r in ok(client.get(BASE + "/sources", headers=hb))["sources"]}
    assert not reg_b["dcad"]["enabled"] and not reg_b["tarrant_tax_roll"]["enabled"]


def test_a_scoring_override_changes_only_that_tenants_scores(client, db_session, two_tenants):
    a, b, ha, hb = two_tenants["a"], two_tenants["b"], two_tenants["ha"], two_tenants["hb"]
    _enable(client, hb, *ALL_DFW)
    sb = _pilot(client, hb)
    ok(client.post(BASE + "/strategies/%s/hunt" % sb["id"], headers=hb))

    def versions(org_id):
        return {s.version for s in db_session.query(EvoSenseScore).filter(
            EvoSenseScore.organization_id == org_id, EvoSenseScore.score_type == "property_opportunity",
            EvoSenseScore.is_current.is_(True))}

    b_before = _state(db_session, b.id)
    ok(client.patch(BASE + "/controls", headers=ha, json={"scoring_options": {"exclude_institutional": False},
                                                          "score_weights": {"TAX_DELINQUENT": 25}}))
    for p in db_session.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == a.id).all():
        ok(client.post(BASE + "/properties/%s/rescore" % p.id, headers=ha))
    db_session.expire_all()
    assert all(v.startswith(SC.PO_VERSION + "+w") and "+o" in v for v in versions(a.id))
    assert versions(b.id) == {SC.PO_VERSION}
    assert _state(db_session, b.id) == b_before


def test_reprocessing_and_rollback_touch_only_their_own_tenant(client, db_session, two_tenants):
    a, b, ha, hb = two_tenants["a"], two_tenants["b"], two_tenants["ha"], two_tenants["hb"]
    _enable(client, hb, *ALL_DFW)
    sb = _pilot(client, hb)
    ok(client.post(BASE + "/strategies/%s/hunt" % sb["id"], headers=hb))
    _make_legacy(db_session, a.id)
    _make_legacy(db_session, b.id)                       # B has the same legacy shape ...
    b_state = _state(db_session, b.id)
    b_signals = db_session.query(EvoSenseSignal).filter(EvoSenseSignal.organization_id == b.id).count()
    run = ok(client.post(BASE + "/reprocess/dry-run", headers=ha, json={}))
    assert all(d["property_id"] not in b_state for d in run["diff"])
    ok(client.post(BASE + "/reprocess/%s/apply" % run["id"], headers=ha, json={"confirm": "APPLY %s" % run["id"]}))
    db_session.expire_all()
    assert _state(db_session, b.id) == b_state, "... and A's apply did not correct B's records"
    ok(client.post(BASE + "/reprocess/%s/rollback" % run["id"], headers=ha,
                   json={"confirm": "ROLLBACK %s" % run["id"]}))
    db_session.expire_all()
    assert _state(db_session, b.id) == b_state
    assert db_session.query(EvoSenseSignal).filter(EvoSenseSignal.organization_id == b.id).count() == b_signals
