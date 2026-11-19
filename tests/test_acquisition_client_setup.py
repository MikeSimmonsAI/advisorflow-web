# -*- coding: utf-8 -*-
"""Client-filled setup (2026-11-18): the client saves her own details step by
step, may only ADD to the never-contact list, agrees to the offer without
changing it, and finishing shows "ready for review" to EvoSys. A no-show
keeps its reason."""
from tests.test_acquisition_outcomes_followups import _delivered
from tests.test_acquisition_pilot import _h, pilot  # noqa: F401


def test_client_setup_steps_offer_and_ready_for_review(pilot):
    p = pilot
    h = _h(p["db"], p["owner"], p["shine"].id)
    c = p["c"]
    r = c.put("/acquisition/profile", json={"business_name": "Shine Co", "contact_phone": "901-555-0100", "setup_step": "business"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["setup"]["steps"] == ["business"]
    r = c.put("/acquisition/profile", json={"never_contact": ["acmedental.com", "boss@acme.com"], "setup_step": "never"}, headers=h).json()
    assert r["setup"]["never_contact_count"] == 2 and r["setup"]["steps"] == ["business", "never"]
    # adding again never duplicates and never removes
    r = c.put("/acquisition/profile", json={"never_contact": ["acmedental.com"]}, headers=h).json()
    assert r["setup"]["never_contact_count"] == 2
    # can't finish before agreeing to the offer (EvoSys sets it first)
    assert c.post("/acquisition/setup/complete", headers=h).status_code == 422
    assert c.put("/acquisition-ops/%s/profile" % p["shine"].id, json={"offer_type": "free_pilot"},
                 headers=_h(p["db"], p["god"])).status_code == 200
    r = c.get("/acquisition/profile", headers=h).json()
    before = r["offer_type"]
    a = c.post("/acquisition/setup/accept-offer", headers=h)
    assert a.status_code == 200 and a.json()["setup"]["offer_accepted_at"] and a.json()["offer_type"] == before
    # the client can't change the offer type
    c.put("/acquisition/profile", json={"offer_type": "growth_pilot_250"}, headers=h)
    assert c.get("/acquisition/profile", headers=h).json()["offer_type"] == before
    done = c.post("/acquisition/setup/complete", headers=h)
    assert done.status_code == 200 and done.json()["setup"]["completed_at"]
    g = _h(p["db"], p["god"])
    items = c.get("/god/acquisition/directory", headers=g).json()["items"]
    me = next(i for i in items if i["organization_id"] == p["shine"].id)
    assert me["client_setup"] == "done" and me["offer_accepted_at"]
    # setup never starts outreach
    ov = c.get("/acquisition-ops/%s/overview" % p["shine"].id, headers=g).json()
    assert ov["profile"]["paused"] is True


def test_outsider_cannot_finish_another_clients_setup(pilot):
    p = pilot
    h = _h(p["db"], p["outsider"], p["shine"].id)
    assert p["c"].post("/acquisition/setup/accept-offer", headers=h).status_code in (402, 403, 404)


def test_no_show_keeps_its_reason(pilot):
    p = pilot
    opp = _delivered(p)
    h = _h(p["db"], p["owner"], p["shine"].id)
    r = p["c"].post("/acquisition/prospects/%s/outcome" % opp, json={"outcome": "no_show", "reason": "They cancelled"}, headers=h)
    assert r.status_code == 200, r.text
    assert "No-show reason: They cancelled" in (r.json().get("outcome_note") or "")


# ── P0 2026-11-18: the invited client admin lands in the portal ─────────────
from tests.test_acquisition_pilot import _user  # noqa: E402


def _contexts(p, u):
    r = p["c"].get("/auth/my-contexts", headers=_h(p["db"], u))
    assert r.status_code == 200, r.text
    return r.json()


def test_home_org_client_login_gets_its_portal_as_its_workspace(pilot):
    p = pilot
    eric = _user(p["db"], p["shine"], "org_admin")          # how invite() creates it: no membership
    d = _contexts(p, eric)
    ws = d["workspace_contexts"]
    assert [w["organization_id"] for w in ws] == [p["shine"].id]
    assert ws[0]["program_route"] == "/program/acquisition" and ws[0]["experience"] == "acquisition"
    assert d["default_context"]["organization_id"] == p["shine"].id
    # it is a REAL membership now, so the workspace header is honoured too
    from app.models.sales_models import Membership
    assert p["db"].query(Membership).filter(Membership.user_id == eric.id, Membership.scope_id == p["shine"].id,
                                            Membership.is_active.is_(True)).count() == 1
    assert p["c"].get("/acquisition/status", headers=_h(p["db"], eric, p["shine"].id)).status_code == 200
    _contexts(p, eric)                                       # idempotent: still exactly one
    assert p["db"].query(Membership).filter(Membership.user_id == eric.id).count() == 1
    # internal screens stay refused by the server
    h = _h(p["db"], eric)
    assert p["c"].get("/acquisition/status", headers=h).status_code == 200
    assert p["c"].get("/acquisition-ops/%s/overview" % p["shine"].id, headers=h).status_code in (401, 403)


def test_ordinary_tenant_without_acquisition_is_unchanged(pilot):
    p = pilot
    u = _user(p["db"], p["other"], "org_admin")             # cleaning org WITHOUT the program
    d = _contexts(p, u)
    assert d["workspace_contexts"] == []
    assert d["default_context"]["type"] == "legacy_tenant"


def test_offer_versioning_and_pending(pilot, db_session):
    from app.services.acquisition import engine as E
    p = pilot
    h = _h(p["db"], p["owner"], p["shine"].id)
    prof = E.get_profile(db_session, p["shine"].id)
    prof.offer_type, prof.offer_terms = None, None
    db_session.commit()
    assert p["c"].get("/acquisition/profile", headers=h).json()["setup"]["offer_configured"] is False
    assert p["c"].post("/acquisition/setup/accept-offer", headers=h).status_code == 409
    g = _h(p["db"], p["god"])
    assert p["c"].put("/acquisition-ops/%s/profile" % p["shine"].id, json={"offer_type": "free_pilot"}, headers=g).status_code == 200
    a = p["c"].post("/acquisition/setup/accept-offer", headers=h).json()["setup"]
    assert a["offer_accepted_at"] and a["offer_version"]
    db_session.expire_all()
    prof = E.get_profile(db_session, p["shine"].id)
    assert prof.offer_accepted_version == a["offer_version"] and prof.offer_accepted_by == p["owner"].id
    # a material change needs a new "I agree"
    assert p["c"].put("/acquisition-ops/%s/profile" % p["shine"].id, json={"offer_type": "growth_pilot_250"}, headers=g).status_code == 200
    s = p["c"].get("/acquisition/profile", headers=h).json()["setup"]
    assert s["offer_accepted_at"] is None and s["offer_previously_accepted_at"]
    assert p["c"].post("/acquisition/setup/complete", headers=h).status_code == 422


def test_estimate_keeps_the_visit_and_one_time_win_adds_no_monthly_value(pilot):
    p = pilot
    h = _h(p["db"], p["owner"], p["shine"].id)
    a, b = _delivered(p), _delivered(p)
    post = lambda o, body: p["c"].post("/acquisition/prospects/%s/outcome" % o, json=body, headers=h)  # noqa: E731
    r = post(a, {"outcome": "quote_issued", "quote_amount": 1800})
    assert r.status_code == 200, r.text
    labels = [e["label"] for e in r.json()["history"] if e["kind"] == "stage"]
    assert labels[-2:] == ["Appointment held", "Quote issued"]
    assert post(a, {"outcome": "won", "contract_type": "recurring", "monthly_value": 2200, "contract_start": "2026-12-01"}).status_code == 200
    assert post(b, {"outcome": "appointment_held"}).status_code == 200
    r = post(b, {"outcome": "won", "contract_type": "one_time", "contract_value": 5000, "monthly_value": 999, "contract_start": "2026-12-05"})
    assert r.status_code == 200 and r.json()["monthly_value"] is None
    # saving the same result twice can't count it twice
    assert post(b, {"outcome": "won", "contract_type": "one_time", "contract_value": 5000}).status_code == 409
    rep = p["c"].get("/acquisition/report?period=all", headers=h).json()
    assert rep["contracts_won"] == 2 and rep["reported_monthly_value"] == 2200
    assert rep["visits_held"] == 2 and rep["estimates_sent"] == 1


def test_logo_upload_is_company_scoped(pilot):
    import base64, io
    from PIL import Image
    p = pilot
    buf = io.BytesIO(); Image.new("RGB", (120, 60), (15, 143, 122)).save(buf, "PNG")
    data = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    h = _h(p["db"], p["owner"], p["shine"].id)
    r = p["c"].put("/acquisition/logo", json={"image_base64": data}, headers=h)
    assert r.status_code == 200, r.text
    assert p["c"].get("/acquisition/status", headers=h).json()["logo_url"].startswith("data:image/png;base64,")
    assert p["c"].put("/acquisition/logo", json={"image_base64": "data:image/png;base64,bm90IGFuIGltYWdl"}, headers=h).status_code == 400
    # another company's login can't touch it
    o = _h(p["db"], p["outsider"], p["shine"].id)
    assert p["c"].put("/acquisition/logo", json={"image_base64": data}, headers=o).status_code in (402, 403, 404)


def test_two_clients_cannot_see_each_others_appointments(pilot, db_session):
    import json as _j
    from app.services.acquisition import engine as E
    p = pilot
    opp = _delivered(p)
    p["other"].enabled_features = _j.dumps(list(E.CLIENT_FEATURES))
    db_session.commit()
    g = _h(p["db"], p["god"])
    assert p["c"].post("/god/acquisition/%s/enable" % p["other"].id, headers=g).status_code == 200
    o = _h(p["db"], p["outsider"], p["other"].id)
    assert p["c"].get("/acquisition/appointments", headers=o).json()["all"] == []
    assert p["c"].get("/acquisition/prospects/%s" % opp, headers=o).status_code == 404
    assert p["c"].post("/acquisition/prospects/%s/outcome" % opp, json={"outcome": "appointment_held"}, headers=o).status_code == 404
    # and the other company's header can't be borrowed
    x = _h(p["db"], p["outsider"], p["shine"].id)
    r = p["c"].get("/acquisition/appointments", headers=x)
    assert r.status_code in (402, 403, 404) or r.json()["all"] == []


def test_revoked_client_membership_is_never_resurrected(pilot):
    from app.models.sales_models import Membership
    p = pilot
    u = _user(p["db"], p["shine"], "org_admin")
    p["db"].add(Membership(user_id=u.id, scope_type="customer_org", scope_id=p["shine"].id, role="org_admin", is_active=False))
    p["db"].commit()
    assert _contexts(p, u)["workspace_contexts"] == []
    assert p["db"].query(Membership).filter(Membership.user_id == u.id, Membership.is_active.is_(True)).count() == 0


def test_invite_creates_the_portal_membership(pilot):
    from app.models.models import User
    from app.models.sales_models import Membership
    p = pilot
    g = _h(p["db"], p["god"])
    r = p["c"].post("/god/acquisition/clients/%s/invite" % p["shine"].id, json={"email": "newclient@example.com", "name": "New Client"}, headers=g)
    assert r.status_code == 200, r.text
    u = p["db"].query(User).filter(User.email == "newclient@example.com").first()
    assert p["db"].query(Membership).filter(Membership.user_id == u.id, Membership.scope_id == p["shine"].id,
                                            Membership.is_active.is_(True)).count() == 1
