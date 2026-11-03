# -*- coding: utf-8 -*-
"""#452 Phase 5: delegated workers (VAs) get ONLY what the owner granted, for
ONLY the clients they're assigned, and every action is audited."""
import json

from app.models.acquisition_models import AcquisitionOperator
from tests.test_acquisition_master import _assign
from tests.test_acquisition_pilot import _add, _h, _org, _pool_business, _user, pilot  # noqa: F401


def _worker(p, perms=None):
    va_home = _org(p["db"], "VA Home Org", industry="other")
    va = _user(p["db"], va_home, "org_admin", "org_admin")
    body = {"email": va.email}
    if perms is not None:
        body["permissions"] = perms
    r = p["c"].post("/god/acquisition/%s/operators" % p["shine"].id, json=body, headers=_h(p["db"], p["god"]))
    assert r.status_code == 200, r.text
    return va


def test_new_worker_starts_with_view_only_and_owner_actions_stay_owner_only(pilot):
    p = pilot
    va = _worker(p)                                                   # no permissions given
    h = _h(p["db"], va)
    base = "/acquisition-ops/%s" % p["shine"].id
    assert p["c"].get(base + "/prospects", headers=h).status_code == 200
    opp = _add(p)
    assert p["c"].post(base + "/prospects/%s/stage" % opp, json={"stage": "researched"}, headers=h).status_code == 403
    assert p["c"].post(base + "/prospects/%s/email/send" % opp, json={}, headers=h).status_code == 403
    assert p["c"].get(base + "/report", headers=h).status_code == 403
    assert p["c"].put(base + "/calendar", json={}, headers=h).status_code == 403
    assert p["c"].post(base + "/prospects/%s/confirm-won" % opp, headers=h).status_code == 403
    for path in ("/god/acquisition/hub", "/god/acquisition/master/prospects", "/god/acquisition/directory",
                 "/god/acquisition/followups"):
        assert p["c"].get(path, headers=h).status_code == 403
    assert p["c"].post("/god/acquisition/clients", json={"name": "Nope Co"}, headers=h).status_code == 403


def test_researcher_can_research_but_not_send_or_book(pilot):
    p = pilot
    va = _worker(p, ["research"])
    h = _h(p["db"], va)
    base = "/acquisition-ops/%s" % p["shine"].id
    opp = _add(p)
    assert p["c"].post(base + "/prospects/%s/stage" % opp, json={"stage": "researched"}, headers=h).status_code == 200
    assert p["c"].post(base + "/prospects/%s/stage" % opp, json={"stage": "interested", "note": "x"},
                       headers=h).status_code == 403                                  # needs review_replies
    assert p["c"].post(base + "/prospects/%s/email/preview" % opp, json={}, headers=h).status_code == 403
    assert p["c"].get(base + "/prospects/%s/slots?day=2026-11-10" % opp, headers=h).status_code == 403
    from app.models.models import AuditLogEntry as AL
    assert p["db"].query(AL).filter(AL.action == "acquisition_operator_action", AL.actor_user_id == va.id).count() >= 1


def test_worker_sees_only_authorized_clients_and_never_the_master_pool(pilot, db_session):
    p = pilot
    from app.services.acquisition import engine as E
    p["other"].enabled_features = json.dumps(list(E.CLIENT_FEATURES))
    db_session.commit()
    item = _pool_business(p["db"], industry="office", name="Pool Only Office")
    va = _worker(p, ["research", "review_replies", "schedule"])
    h = _h(p["db"], va)
    assert p["c"].get("/acquisition-ops/%s/prospects" % p["other"].id, headers=h).status_code == 403
    q = p["c"].get("/acquisition-ops/my-queue", headers=h).json()
    assert [c["organization_id"] for c in q["clients"]] == [p["shine"].id]
    assert q["clients"][0]["followups"] is None and q["clients"][0]["outreach_ready"] is None
    assert "Pool Only Office" not in json.dumps(q)
    _assign(p, p["shine"], [item.id])                                      # allocated to the client...
    q = p["c"].get("/acquisition-ops/my-queue", headers=h).json()
    assert "Pool Only Office" in json.dumps(q)                             # ...now in that client's queue


def test_owner_sets_permissions_and_legacy_grants_keep_full_access(pilot):
    p = pilot
    va = _worker(p, ["research"])
    g = _h(p["db"], p["god"])
    url = "/god/acquisition/%s/operators/%s" % (p["shine"].id, va.id)
    assert p["c"].put(url, json={"permissions": ["bogus"]}, headers=g).status_code == 422
    r = p["c"].put(url, json={"permissions": ["schedule"]}, headers=g).json()
    assert r["permissions"] == ["schedule", "view_queue"]                  # view_queue always included
    # a grant recorded before permissions existed = full operator, as before
    row = p["db"].query(AcquisitionOperator).filter(AcquisitionOperator.user_id == va.id).one()
    row.permissions = None
    p["db"].commit()
    ops = p["c"].get("/god/acquisition/%s/operators" % p["shine"].id, headers=g).json()["items"]
    assert ops[0]["legacy_full_access"] is True and "send_outreach" in ops[0]["permissions"]
    # client logins and the owner can't be made operators
    assert p["c"].post("/god/acquisition/%s/operators" % p["shine"].id, json={"email": p["owner"].email},
                       headers=g).status_code == 409
    assert p["c"].post("/god/acquisition/%s/operators" % p["shine"].id, json={"email": p["god"].email},
                       headers=g).status_code == 409
