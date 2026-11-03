# -*- coding: utf-8 -*-
"""Program-scoped team provisioning (#452, 2026-11-03), shown with an Atlantis-
shaped energy workspace: its OWN admin adds a worker, gets a one-time setup
link (no email, no password), can re-issue it (only one live link), can
deactivate/reactivate - all through the existing user model. The worker sees
only that workspace and never the Acquisition master or God Mode."""
import json

from app.models.staff_models import StaffActivation
from tests.test_acquisition_pilot import _h, _org, _user, pilot  # noqa: F401


def _atlantis(p):
    db = p["db"]
    org = _org(db, "Atlantis-like Energy (synthetic)", industry="energy", features=("leads", "users"))
    admin = _user(db, org, "org_admin", "org_admin")
    return org, admin


def test_program_admin_adds_and_manages_a_worker(pilot):
    p = pilot
    db, c = p["db"], p["c"]
    org, admin = _atlantis(p)
    h = _h(db, admin, org.id)
    r = c.post("/admin/users", json={"email": "worker.synthetic@atlantis.example", "full_name": "Synthetic Worker",
                                     "role": "advisor"}, headers=h)
    assert r.status_code in (200, 201), r.text
    wid = r.json()["id"]
    assert r.json().get("setup_url")                                  # one-time link, shown once
    # re-issue: a new link, the old one revoked - never two live links
    a = c.post("/admin/users/%s/setup-link" % wid, json={}, headers=h)
    assert a.status_code == 200 and "/activate?token=" in a.json()["setup_url"], a.text
    b = c.post("/admin/users/%s/setup-link" % wid, json={}, headers=h).json()
    assert b["setup_url"] != a.json()["setup_url"]
    live = [x for x in db.query(StaffActivation).filter(StaffActivation.user_id == wid).all() if x.is_usable()]
    assert len(live) == 1
    # deactivate -> no link; reactivate -> link again
    assert c.patch("/admin/users/%s/deactivate" % wid, headers=h).status_code == 200
    assert c.post("/admin/users/%s/setup-link" % wid, json={}, headers=h).status_code == 409
    assert c.patch("/admin/users/%s/reactivate" % wid, headers=h).status_code == 200
    assert c.post("/admin/users/%s/setup-link" % wid, json={}, headers=h).status_code == 200


def test_worker_is_confined_and_other_admins_cannot_reach_it(pilot):
    p = pilot
    db, c = p["db"], p["c"]
    org, admin = _atlantis(p)
    worker = _user(db, org, "advisor", "advisor")
    wh = _h(db, worker, org.id)
    for path in ("/god/acquisition/hub", "/acquisition-ops/clients", "/god/stats"):
        assert c.get(path, headers=wh).status_code == 403, path
    ctx = c.get("/auth/my-contexts", headers=wh).json()
    ids = {w["organization_id"] for w in ctx.get("workspace_contexts") or []}
    assert ids <= {org.id} and not (ctx.get("acquisition_ops") or [])
    # a worker can't issue links; another workspace's admin can't reach this person
    assert c.post("/admin/users/%s/setup-link" % worker.id, json={}, headers=wh).status_code == 403
    assert c.post("/admin/users/%s/setup-link" % worker.id, json={},
                  headers=_h(db, p["owner"], p["shine"].id)).status_code in (403, 404)


def test_setup_link_never_resets_someone_who_already_signs_in(pilot):
    p = pilot
    db, c = p["db"], p["c"]
    org, admin = _atlantis(p)
    worker = _user(db, org, "advisor", "advisor")
    from datetime import datetime
    worker.last_login_at, worker.must_change_password = datetime.utcnow(), False
    db.commit()
    r = c.post("/admin/users/%s/setup-link" % worker.id, json={}, headers=_h(db, admin, org.id))
    assert r.status_code == 409 and "already signs in" in r.text
