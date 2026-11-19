"""Client Growth: Program Access card, staff/API authority, God Mode owner-only
controls, public token scope, validation, persistence, duplicate submits,
proposal tasks, review, and safe failure of AI summary and notifications."""
from datetime import datetime, timedelta

import pytest

from app.models.client_growth_models import (ClientGrowthInvitation, ClientGrowthSubmission, ClientGrowthTask)
from app.models.models import Notification, Organization, Platform, User, UserCapabilityGrant
from app.models.sales_models import (ROLE_SALES_MANAGER, SCOPE_BRAND_SALES_ORG, BrandSalesOrg, Membership,
                                     Opportunity, OpportunityEvent)
from app.services.auth_service import create_access_token, hash_password
from app.services.client_growth import service as S
from app.services.client_growth import templates as T

_n = [0]


def _user(db, role="advisor", email=None, org_id=None):
    _n[0] += 1
    u = User(organization_id=org_id, email=email or "u%d@cg.test" % _n[0], password_hash=hash_password("x"),
             full_name="Person %d" % _n[0], role=role, is_active=True, must_change_password=False)
    db.add(u); db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


@pytest.fixture()
def bso(db_session):
    p = Platform(name="EvoSys Pro", slug="evosyspro")
    db_session.add(p); db_session.commit()
    b = BrandSalesOrg(platform_id=p.id, name="EvoSys Pro Sales", slug="evosyspro-sales")
    db_session.add(b); db_session.commit()
    return b


@pytest.fixture()
def god(db_session):
    return _user(db_session, "god_admin", "owner@cg.test")


@pytest.fixture()
def owner(db_session, bso):
    u = _user(db_session, "advisor", "partner@cg.test")
    db_session.add(UserCapabilityGrant(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=bso.id,
                                       organization_id=None, capability="sales_program_owner", is_active=True))
    db_session.commit()
    return u


@pytest.fixture()
def rep(db_session, bso):
    u = _user(db_session, "advisor", "rep@cg.test")
    db_session.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=bso.id,
                              role=ROLE_SALES_MANAGER, is_active=True))
    db_session.commit()
    return u


def _prospect(client, db, who, **kw):
    body = {"company": "Vincent Greene Agency", "contact": "Vincent Greene", "email": "vincent.prospect@example.test",
            "industry": "insurance"}
    body.update(kw)
    r = client.post("/client-growth/prospects", json=body, headers=_h(db, who))
    assert r.status_code == 200, r.text
    return r.json()["prospect"]


def _invite(client, db, who, pid):
    r = client.post("/client-growth/invitations", json={
        "prospect_id": pid, "prepared_for": "Vincent Greene", "contact_name": "Vincent Greene",
        "business_name": "Vincent Greene Agency", "email": "Vincent.Prospect@example.test"}, headers=_h(db, who))
    assert r.status_code == 200, r.text
    return r.json()


def _token(url):
    return url.rsplit("/", 1)[1]


GOOD = {"new_customers_per_month": 12, "preferred_launch": "30_days", "insurance_products": ["home", "auto"],
        "service_areas": "Mesquite", "monthly_ad_budget": "1000_3000", "monthly_va_budget": "not_sure",
        "va_status": "yes", "va_count": "2", "va_hours": "9-5 Central", "support_needed": ["lead_follow_up"],
        "handoff_preference": "both", "licensed_agents": 3, "contact_name": "Vincent Greene",
        "business_name": "Vincent Greene Agency", "email": "Vincent.Prospect@example.test", "notes": "Hi"}


# ── access ───────────────────────────────────────────────────────────────────

class TestAccess:
    def test_card_only_for_god_owner_and_grantee(self, client, db_session, bso, god, owner, rep):
        org = Organization(name="Some Customer", slug="cust-cg")
        db_session.add(org); db_session.commit()
        customer = _user(db_session, "org_admin", "cust@cg.test", org_id=org.id)
        granted = _user(db_session, "advisor", "granted@cg.test")

        def card(u):
            r = client.get("/auth/my-contexts", headers=_h(db_session, u))
            assert r.status_code == 200, r.text
            return r.json().get("client_growth_contexts")

        assert card(god)[0]["path"] == "/client-growth" and card(god)[0]["access"] == "owner"
        assert card(owner)[0]["access"] == "staff"
        assert card(rep) == [] and card(customer) == [] and card(granted) == []
        r = client.post("/god/client-growth/access", json={"email": "granted@cg.test", "grant": True},
                        headers=_h(db_session, god))
        assert r.status_code == 200, r.text
        assert card(granted)[0]["access"] == "staff"
        client.post("/god/client-growth/access", json={"email": "granted@cg.test", "grant": False},
                    headers=_h(db_session, god))
        assert card(granted) == []
        assert client.get("/client-growth/overview", headers=_h(db_session, granted)).status_code == 403

    def test_api_refused_for_unauthorized(self, client, db_session, bso, rep):
        stranger = _user(db_session)
        for u in (rep, stranger):
            for path in ("/client-growth/overview", "/client-growth/prospects", "/client-growth/submissions",
                         "/client-growth/invitations", "/client-growth/tasks"):
                assert client.get(path, headers=_h(db_session, u)).status_code == 403, path
        assert client.get("/client-growth/overview").status_code == 401

    def test_god_mode_controls_are_owner_only(self, client, db_session, bso, god, owner):
        assert client.get("/client-growth/overview", headers=_h(db_session, owner)).status_code == 200
        for method, path, body in (("get", "/god/client-growth/overview", None),
                                   ("post", "/god/client-growth/access", {"email": "x@cg.test"}),
                                   ("post", "/god/client-growth/test-records/cleanup", {"confirm": True}),
                                   ("post", "/god/client-growth/test-prospect", {})):
            r = getattr(client, method)(path, json=body, headers=_h(db_session, owner)) if body is not None \
                else getattr(client, method)(path, headers=_h(db_session, owner))
            assert r.status_code == 403, path
        assert client.get("/client-growth/me", headers=_h(db_session, owner)).json()["god_mode_path"] is None
        g = client.get("/god/client-growth/overview", headers=_h(db_session, god)).json()
        vias = {p["email"]: p["via"] for p in g["access"]}
        assert vias["owner@cg.test"] == "God Mode" and vias["partner@cg.test"] == "Sales program owner"

    def test_grant_keeps_other_brand_grants(self, client, db_session, bso, god, owner):
        client.post("/god/client-growth/access", json={"email": "partner@cg.test"}, headers=_h(db_session, god))
        client.post("/god/client-growth/access", json={"email": "partner@cg.test", "grant": False},
                    headers=_h(db_session, god))
        from app.services import capabilities as caps
        assert caps.brand_grants_for(db_session, owner.id, bso.id) == ["sales_program_owner"]


# ── prospects and invitations ────────────────────────────────────────────────

class TestProspectsAndInvitations:
    def test_dedup_and_discovery_stage(self, client, db_session, bso, god):
        p = _prospect(client, db_session, god)
        o = db_session.get(Opportunity, p["id"])
        assert o.stage == "discovery" and o.source_system == "client_growth"
        from app.services.evosys_sales import outreach as O
        assert o.stage not in O.WORKING_STAGES          # never picked up by automated outreach
        r = client.post("/client-growth/prospects", json={"company": "vincent greene agency"},
                        headers=_h(db_session, god))
        assert r.status_code == 409 and r.json()["detail"]["duplicates"][0]["id"] == p["id"]
        r = client.post("/client-growth/prospects", json={"company": "Other", "email": "VINCENT.PROSPECT@example.test"},
                        headers=_h(db_session, god))
        assert r.status_code == 409

    def test_link_created_not_sent_and_existing_email_kept(self, client, db_session, bso, god):
        o = Opportunity(brand_sales_org_id=bso.id, company_name="Existing Agency", contact_name="Vince G",
                        email="old@agency.test", stage="contacted")
        db_session.add(o); db_session.commit()
        out = _invite(client, db_session, god, o.id)
        assert out["sent"] is False and out["invitation"]["state"] == "active"
        assert out["invitation"]["url"].startswith("https://app.evosyspro.live/growth-plan/")
        tok = _token(out["invitation"]["url"])
        assert len(tok) >= 40 and "vincent" not in tok.lower() and o.id not in out["invitation"]["url"]
        i = db_session.query(ClientGrowthInvitation).one()
        assert tok not in (i.token_hash or "") and i.token_hash == S._hash(tok)
        db_session.refresh(o)
        assert o.email == "old@agency.test"            # not overwritten
        ev = [e.summary for e in db_session.query(OpportunityEvent).filter_by(opportunity_id=o.id)]
        assert any("Newly supplied preferred proposal email: Vincent.Prospect@example.test" in s for s in ev)
        assert any("not sent" in s for s in ev)

    def test_copy_mark_sent_revoke_reissue(self, client, db_session, bso, god):
        p = _prospect(client, db_session, god)
        inv = _invite(client, db_session, god, p["id"])["invitation"]
        h = _h(db_session, god)
        assert client.post("/client-growth/invitations/%s/copy" % inv["id"], headers=h).json()["url"] == inv["url"]
        r = client.post("/client-growth/invitations/%s/mark-sent" % inv["id"], json={"note": "Gmail"}, headers=h)
        assert r.json()["invitation"]["state"] == "sent"
        assert client.post("/client-growth/invitations/%s/revoke" % inv["id"], json={"reason": "x"},
                           headers=h).status_code == 400          # must confirm
        r = client.post("/client-growth/invitations/%s/revoke" % inv["id"],
                        json={"reason": "Wrong email", "confirm": True}, headers=h)
        assert r.json()["invitation"]["state"] == "revoked"
        assert client.get("/client-growth-public/invitations/" + _token(inv["url"])).json()["state"] == "revoked"
        new = client.post("/client-growth/invitations/%s/reissue" % inv["id"], json={}, headers=h).json()
        assert new["invitation"]["replaces_id"] == inv["id"] and new["invitation"]["url"] != inv["url"]
        from app.models.models import AuditLogEntry
        acts = {a.action for a in db_session.query(AuditLogEntry).all()}
        assert {"client_growth.invitation_created", "client_growth.invitation_revoked",
                "client_growth.invitation_marked_sent"} <= acts


# ── public form ──────────────────────────────────────────────────────────────

class TestPublic:
    def test_view_is_scoped_and_private(self, client, db_session, bso, god):
        p = _prospect(client, db_session, god)
        url = _invite(client, db_session, god, p["id"])["invitation"]["url"]
        r = client.get("/client-growth-public/invitations/" + _token(url))
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store" and "noindex" in r.headers["x-robots-tag"]
        body = r.json()
        assert body["state"] == "ready" and body["public"]["eyebrow"] == "Prepared for Vincent Greene"
        assert body["prefill"]["email"] == "Vincent.Prospect@example.test"
        text = str(body)
        for leak in (p["id"], "owner@cg.test", "intro_note", "Discovery stage"):
            assert leak not in text
        assert client.get("/client-growth-public/invitations/" + "x" * 43).status_code == 404
        assert client.get("/client-growth-public/invitations/short").status_code == 404
        # no product is preselected
        assert "insurance_products" not in body["prefill"]

    def test_validation(self, client, db_session, bso, god):
        p = _prospect(client, db_session, god)
        tok = _token(_invite(client, db_session, god, p["id"])["invitation"]["url"])
        bad = dict(GOOD, new_customers_per_month=0, insurance_products=[], email="nope", monthly_ad_budget="lots")
        r = client.post("/client-growth-public/invitations/%s/submit" % tok, json={"answers": bad})
        assert r.status_code == 422
        errs = r.json()["detail"]["errors"]
        assert {"new_customers_per_month", "insurance_products", "email", "monthly_ad_budget"} <= set(errs)
        assert db_session.query(ClientGrowthSubmission).count() == 0

    def test_submit_persists_creates_task_notifies_and_hides_answers(self, client, db_session, bso, god, owner):
        p = _prospect(client, db_session, god)
        tok = _token(_invite(client, db_session, god, p["id"])["invitation"]["url"])
        r = client.post("/client-growth-public/invitations/%s/submit" % tok,
                        json={"answers": dict(GOOD, password="hunter2", va_count="2")})
        assert r.status_code == 200, r.text
        assert r.json()["message"].startswith("Thanks, Vincent. We've received your growth goals.")
        s = db_session.query(ClientGrowthSubmission).one()
        assert s.answers["va_count"] == 2 and "password" not in s.answers
        assert s.template_key == T.INSURANCE_KEY and s.template_version == 1
        k = db_session.query(ClientGrowthTask).one()
        assert k.title == "Prepare Vincent Greene agency growth proposal" and k.opportunity_id == p["id"]
        notes = db_session.query(Notification).all()
        assert {n.user_id for n in notes} == {god.id, owner.id}
        assert any(e.event_type == "questionnaire_submitted"
                   for e in db_session.query(OpportunityEvent).filter_by(opportunity_id=p["id"]))
        # double click: same answer, nothing duplicated
        r2 = client.post("/client-growth-public/invitations/%s/submit" % tok, json={"answers": GOOD})
        assert r2.status_code == 200 and r2.json()["already"] is True
        assert db_session.query(ClientGrowthSubmission).count() == 1
        assert db_session.query(ClientGrowthTask).count() == 1 and db_session.query(Notification).count() == 2
        # holding the link never shows the answers again
        v = client.get("/client-growth-public/invitations/" + tok).json()
        assert v["state"] == "submitted" and "Vincent.Prospect" not in str(v) and "prefill" not in v

    def test_hidden_conditional_answers_dropped(self, client, db_session, bso, god):
        p = _prospect(client, db_session, god)
        tok = _token(_invite(client, db_session, god, p["id"])["invitation"]["url"])
        client.post("/client-growth-public/invitations/%s/submit" % tok,
                    json={"answers": dict(GOOD, va_status="no", va_count=4)})
        s = db_session.query(ClientGrowthSubmission).one()
        assert "va_count" not in s.answers and s.answers["va_status"] == "no"

    def test_expired(self, client, db_session, bso, god):
        p = _prospect(client, db_session, god)
        tok = _token(_invite(client, db_session, god, p["id"])["invitation"]["url"])
        i = db_session.query(ClientGrowthInvitation).one()
        i.expires_at = datetime.utcnow() - timedelta(minutes=1); db_session.commit()
        assert client.get("/client-growth-public/invitations/" + tok).json()["state"] == "expired"
        r = client.post("/client-growth-public/invitations/%s/submit" % tok, json={"answers": GOOD})
        assert r.status_code == 410

    def test_one_token_cannot_reach_another_prospect(self, client, db_session, bso, god):
        a = _prospect(client, db_session, god)
        b = _prospect(client, db_session, god, company="Other Agency", contact="Ann Other", email="ann@other.test")
        ta = _token(_invite(client, db_session, god, a["id"])["invitation"]["url"])
        _invite(client, db_session, god, b["id"])
        client.post("/client-growth-public/invitations/%s/submit" % ta, json={"answers": GOOD})
        s = db_session.query(ClientGrowthSubmission).one()
        assert s.opportunity_id == a["id"]
        assert db_session.query(ClientGrowthInvitation).filter_by(opportunity_id=b["id"]).one().status == "active"

    def test_notification_failure_does_not_lose_submission(self, client, db_session, bso, god, monkeypatch):
        p = _prospect(client, db_session, god)
        tok = _token(_invite(client, db_session, god, p["id"])["invitation"]["url"])

        def boom(db):
            raise RuntimeError("down")
        monkeypatch.setattr(S, "_recipients", boom)
        r = client.post("/client-growth-public/invitations/%s/submit" % tok, json={"answers": GOOD})
        assert r.status_code == 200
        assert db_session.query(ClientGrowthSubmission).count() == 1
        assert db_session.query(ClientGrowthTask).count() == 1


# ── staff review ─────────────────────────────────────────────────────────────

class TestReview:
    def _submitted(self, client, db, god):
        p = _prospect(client, db, god)
        tok = _token(_invite(client, db, god, p["id"])["invitation"]["url"])
        client.post("/client-growth-public/invitations/%s/submit" % tok, json={"answers": GOOD})
        return db.query(ClientGrowthSubmission).one()

    def test_full_answers_review_and_proposal_prepared(self, client, db_session, bso, god, owner):
        s = self._submitted(client, db_session, god)
        h = _h(db_session, owner)
        d = client.get("/client-growth/submissions/" + s.id, headers=h).json()["submission"]
        assert d["answers"] == s.answers and d["status_label"] == "Awaiting review"
        labels = {r["key"]: r["display"] for r in d["readable"]}
        assert labels["monthly_ad_budget"] == "$1,000 to under $3,000" and labels["monthly_va_budget"] == "Not sure yet"
        c = client.get("/client-growth/overview", headers=h).json()["counts"]
        assert c["submitted"] == 1 and c["awaiting_review"] == 1 and c["pending_invitations"] == 0
        d = client.post("/client-growth/submissions/%s/review" % s.id, headers=h).json()["submission"]
        assert d["status"] == "under_review"
        d = client.post("/client-growth/submissions/%s/proposal-prepared" % s.id, headers=h).json()["submission"]
        assert d["status"] == "proposal_prepared" and d["task"]["status"] == "done"
        c = client.get("/client-growth/overview", headers=h).json()["counts"]
        assert c["proposals_prepared"] == 1 and c["awaiting_review"] == 0
        assert client.get("/client-growth/tasks", headers=h).json()["tasks"][0]["company"] == "Vincent Greene Agency"

    def test_ai_summary_failure_is_safe(self, client, db_session, bso, god, monkeypatch):
        s = self._submitted(client, db_session, god)
        monkeypatch.setenv("AI_MANUAL_ACTIONS_ENABLED", "false")
        d = client.post("/client-growth/submissions/%s/ai-summary" % s.id,
                        headers=_h(db_session, god)).json()["submission"]
        assert d["ai_summary"]["status"] == "failed" and d["answers"] == s.answers
        assert d["status"] == "submitted"

    def test_ai_summary_is_labelled_internal(self, client, db_session, bso, god, monkeypatch):
        s = self._submitted(client, db_session, god)

        class _Msg:
            content = '{"goals":"12 new customers a month","budget":"$1k-3k ads","va_readiness":"2 VAs",' \
                      '"missing":["VA budget"]}'

        class _Resp:
            choices = [type("C", (), {"message": _Msg})]

        class _Client:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kw):
                        return _Resp()
        monkeypatch.setattr(S, "_get_client", lambda: _Client)
        d = client.post("/client-growth/submissions/%s/ai-summary" % s.id,
                        headers=_h(db_session, god)).json()["submission"]
        assert d["ai_summary"]["status"] == "done" and d["ai_summary"]["label"].startswith("AI summary - internal")
        assert d["ai_summary"]["content"]["missing"] == ["VA budget"]


# ── synthetic test records ───────────────────────────────────────────────────

def test_test_records_excluded_from_counts_and_cleaned_up_only(client, db_session, bso, god):
    real = _prospect(client, db_session, god)
    _invite(client, db_session, god, real["id"])
    h = _h(db_session, god)
    assert client.post("/god/client-growth/test-prospect", json={"email": "x@gmail.com"},
                       headers=h).status_code == 422
    tp = client.post("/god/client-growth/test-prospect", json={}, headers=h).json()["prospect"]
    tok = _token(_invite(client, db_session, god, tp["id"])["invitation"]["url"])
    client.post("/client-growth-public/invitations/%s/submit" % tok, json={"answers": GOOD})
    c = client.get("/client-growth/overview", headers=h).json()
    assert c["counts"]["submitted"] == 0 and c["counts"]["pending_invitations"] == 1
    assert c["test_counts"]["submitted"] == 1
    out = client.post("/god/client-growth/test-records/cleanup", json={"confirm": True}, headers=h).json()["removed"]
    assert out == {"submissions": 1, "tasks": 1, "invitations": 1, "notifications": 1, "prospects": 1}
    assert db_session.get(Opportunity, real["id"]) is not None
    assert db_session.query(ClientGrowthInvitation).count() == 1


def test_template_validator_rejects_unknown_types():
    clean, errs = T.validate(T.insurance_v1(), "nope")
    assert errs and clean == {}
    clean, errs = T.validate(T.insurance_v1(), dict(GOOD, new_customers_per_month="help"))
    assert not errs and clean["new_customers_per_month"] == "help"
