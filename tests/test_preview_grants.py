# -*- coding: utf-8 -*-
"""God-granted demo previews (deal_preview_grants).

Pins:
  * God grants one person one deal's existing demo; the card shows in that
    person's Program Access with only grant id / title / label / path;
  * opening it re-checks the grant, is audited, returns the page exactly as
    stored, and does not count a prospect view;
  * expired / revoked / another user / another brand / closed deal / dead
    demo -> no card and the open route refuses;
  * a non-god cannot grant, list or revoke; a god/super admin cannot be a
    grantee; a grantee must belong to the brand named on the grant;
  * the demo's token and public address never reach the grantee;
  * a grant creates no membership and opens none of the deal brand's sales
    screens.
All data is synthetic.
"""
import json
import uuid
from datetime import datetime, timedelta

import pytest

from app.models.demo_site_models import DealPreviewGrant, DemoSite
from app.models.models import AuditLogEntry, Organization, Platform, User
from app.models.sales_models import BrandSalesOrg, Membership, Opportunity
from app.services.auth_service import create_access_token, hash_password

DEMO_HTML = ("<!doctype html><html><head><style>body{background:#1f3b2a;color:#d8b25a}</style></head>"
             "<body><h1>Land demo</h1><button onclick=\"alert(1)\">Book</button>"
             "<footer>Built by EvoSys Pro</footer></body></html>")


def _sfx():
    return uuid.uuid4().hex[:6]


def _u(db, email, org=None, role="advisor"):
    u = User(organization_id=org.id if org else None, email=email, full_name=email.split("@")[0], role=role,
             password_hash=hash_password("Pass12345!"), is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _m(db, u, st, sid, role):
    db.add(Membership(user_id=u.id, scope_type=st, scope_id=sid, role=role, is_active=True))
    db.commit()


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


@pytest.fixture()
def w(db_session):
    db = db_session
    evo = Platform(name="EvoSys Pro", slug="evo-%s" % _sfx())
    bab = Platform(name="BookaBoost", slug="bab-%s" % _sfx())
    oth = Platform(name="Other Brand", slug="oth-%s" % _sfx())
    db.add_all([evo, bab, oth])
    db.commit()
    evo_sales = BrandSalesOrg(platform_id=evo.id, name="EvoSys Pro Sales", slug="es-%s" % _sfx())
    db.add(evo_sales)
    db.commit()
    bab_org = Organization(name="BookaBoost Wholesale", slug="bw-%s" % _sfx(), plan="trial",
                           is_active=True, platform_id=bab.id)
    oth_org = Organization(name="Other Org", slug="oo-%s" % _sfx(), plan="trial",
                           is_active=True, platform_id=oth.id)
    db.add_all([bab_org, oth_org])
    db.commit()

    god = _u(db, "god-%s@example.com" % _sfx(), role="god_admin")
    evo_mgr = _u(db, "evo-mgr-%s@example.com" % _sfx())
    _m(db, evo_mgr, "brand_sales_org", evo_sales.id, "sales_manager")
    bab_admin = _u(db, "bab-admin-%s@example.com" % _sfx(), org=bab_org, role="org_admin")
    _m(db, bab_admin, "customer_org", bab_org.id, "org_admin")
    bab_other = _u(db, "bab-other-%s@example.com" % _sfx(), org=bab_org, role="org_admin")
    _m(db, bab_other, "customer_org", bab_org.id, "org_admin")
    oth_admin = _u(db, "oth-admin-%s@example.com" % _sfx(), org=oth_org, role="org_admin")
    _m(db, oth_admin, "customer_org", oth_org.id, "org_admin")

    opp = Opportunity(brand_sales_org_id=evo_sales.id, owner_user_id=evo_mgr.id,
                      company_name="Countryside Example Land", contact_name="Pat Example",
                      stage="demo_proposal", status="open")
    db.add(opp)
    db.commit()
    demo = DemoSite(opportunity_id=opp.id, brand_sales_org_id=evo_sales.id, slot="platform",
                    title="Countryside Example Land - platform demo", html=DEMO_HTML,
                    token="tok" + uuid.uuid4().hex + uuid.uuid4().hex, slug="countryside-ex-%s" % _sfx(),
                    is_active=True, expires_at=datetime.utcnow() + timedelta(days=30))
    db.add(demo)
    db.commit()
    return dict(db=db, evo=evo, bab=bab, oth=oth, evo_sales=evo_sales, bab_org=bab_org, god=god,
                evo_mgr=evo_mgr, bab_admin=bab_admin, bab_other=bab_other, oth_admin=oth_admin,
                opp=opp, demo=demo)


def _grant(client, w, who="bab_admin", platform="bab", **extra):
    body = {"opportunity_id": w["opp"].id, "grantee_user_id": w[who].id,
            "platform_id": w[platform].id, "reason": "Owner asked: review the land demo"}
    body.update(extra)
    return client.post("/god/preview-grants", json=body, headers=_h(w["db"], w["god"]))


def _ctx(client, w, who):
    r = client.get("/auth/my-contexts", headers=_h(w["db"], w[who]))
    assert r.status_code == 200, r.text
    return r.json()


def _granted(ctx):
    return [p for p in ctx["preview_contexts"] if p.get("grant_id")]


def _views(w):
    db = w["db"]
    db.expire_all()
    return db.query(DemoSite).filter(DemoSite.id == w["demo"].id).first().view_count


def _no_secret(w, text):
    assert w["demo"].token not in text
    assert w["demo"].slug not in text
    assert "/demo/" not in text


def test_grant_card_and_open(client, w):
    db = w["db"]
    assert _granted(_ctx(client, w, "bab_admin")) == []
    r = _grant(client, w, expires_in_days=14)
    assert r.status_code == 200, r.text
    gid = r.json()["grant"]["id"]
    _no_secret(w, r.text)

    ctx = _ctx(client, w, "bab_admin")
    cards = _granted(ctx)
    assert cards == [{"type": "preview", "grant_id": gid, "title": "Countryside Example Land",
                      "label": "Authorized preview", "path": "/previews/%s" % gid}]
    # never a context / workspace / product, and nothing else changed
    assert all(c.get("type") != "preview" for c in ctx["contexts"])
    assert all(c.get("organization_id") != w["evo_sales"].id for c in ctx["workspace_contexts"])
    assert ctx["has_back_office"] is False and ctx["platform_contexts"] == []
    _no_secret(w, json.dumps(ctx))

    before = _views(w)
    o = client.get("/program-access/previews/%s" % gid, headers=_h(db, w["bab_admin"]))
    assert o.status_code == 200, o.text
    body = o.json()
    assert body == {"title": w["demo"].title, "html": DEMO_HTML, "label": "Authorized preview"}
    _no_secret(w, o.text)
    assert _views(w) == before, "a granted preview open is not a prospect view"

    opens = db.query(AuditLogEntry).filter(AuditLogEntry.action == "preview.opened").all()
    assert len(opens) == 1 and opens[0].actor_user_id == w["bab_admin"].id
    assert opens[0].target_id == w["opp"].id and opens[0].organization_id is None
    assert db.query(AuditLogEntry).filter(AuditLogEntry.action == "preview.grant").count() == 1


def test_grant_creates_no_membership_or_sales_access(client, w):
    db = w["db"]
    before = db.query(Membership).count()
    gid = _grant(client, w).json()["grant"]["id"]
    assert db.query(Membership).count() == before
    h = _h(db, w["bab_admin"])
    opp_id = w["opp"].id
    for path in ("/sales/me", "/sales/my-day", "/sales/opportunities",
                 "/sales/opportunities/%s" % opp_id, "/sales/opportunities/%s/demo-sites" % opp_id,
                 "/sales/team", "/sales/demo-queue"):
        r = client.get(path, headers=h)
        assert r.status_code in (403, 404), (path, r.status_code, r.text[:200])
        _no_secret(w, r.text)
    # the grant is not a staff flag on the public route either: still counted
    v = _views(w)
    assert client.get("/public/demo/%s?staff=1" % w["demo"].token, headers=h).status_code == 200
    assert _views(w) == v + 1
    assert client.get("/program-access/previews/%s" % gid, headers=h).status_code == 200


def test_expired_grant_gives_no_card_and_404(client, w):
    db = w["db"]
    gid = _grant(client, w).json()["grant"]["id"]
    g = db.query(DealPreviewGrant).filter(DealPreviewGrant.id == gid).first()
    g.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    assert _granted(_ctx(client, w, "bab_admin")) == []
    assert client.get("/program-access/previews/%s" % gid, headers=_h(db, w["bab_admin"])).status_code == 404


def test_revoked_grant_gives_no_card_and_404(client, w):
    db = w["db"]
    gid = _grant(client, w).json()["grant"]["id"]
    r = client.post("/god/preview-grants/%s/revoke" % gid, json={"note": "done"}, headers=_h(db, w["god"]))
    assert r.status_code == 200 and r.json()["grant"]["active"] is False
    assert r.json()["grant"]["revoked_by"] == w["god"].id
    assert db.query(AuditLogEntry).filter(AuditLogEntry.action == "preview.revoke").count() == 1
    assert _granted(_ctx(client, w, "bab_admin")) == []
    assert client.get("/program-access/previews/%s" % gid, headers=_h(db, w["bab_admin"])).status_code == 404


def test_other_user_cannot_use_someone_elses_grant(client, w):
    db = w["db"]
    gid = _grant(client, w).json()["grant"]["id"]
    for who in ("bab_other", "oth_admin", "evo_mgr"):
        assert _granted(_ctx(client, w, who)) == []
        r = client.get("/program-access/previews/%s" % gid, headers=_h(db, w[who]))
        assert r.status_code == 404, who
    assert client.get("/program-access/previews/%s" % gid).status_code in (401, 403)
    assert client.get("/program-access/previews/nope", headers=_h(db, w["bab_admin"])).status_code == 404


def test_grant_for_a_brand_the_person_left_shows_nothing(client, w):
    db = w["db"]
    gid = _grant(client, w).json()["grant"]["id"]
    # the person stops belonging to BookaBoost
    m = db.query(Membership).filter(Membership.user_id == w["bab_admin"].id).first()
    m.is_active = False
    u = db.query(User).filter(User.id == w["bab_admin"].id).first()
    u.organization_id = None
    db.commit()
    assert _granted(_ctx(client, w, "bab_admin")) == []
    assert client.get("/program-access/previews/%s" % gid, headers=_h(db, w["bab_admin"])).status_code == 404


def test_grant_naming_another_brand_is_refused(client, w):
    r = _grant(client, w, platform="oth")
    assert r.status_code == 422
    assert w["db"].query(DealPreviewGrant).count() == 0


@pytest.mark.parametrize("change", ["won", "lost", "linked", "demo_revoked", "demo_expired"])
def test_closed_deal_or_dead_demo_hides_the_preview(client, w, change):
    db = w["db"]
    gid = _grant(client, w).json()["grant"]["id"]
    opp = db.query(Opportunity).filter(Opportunity.id == w["opp"].id).first()
    demo = db.query(DemoSite).filter(DemoSite.id == w["demo"].id).first()
    if change == "won":
        opp.stage = "won"
    elif change == "lost":
        opp.stage, opp.status = "lost", "lost"
    elif change == "linked":
        opp.customer_organization_id = w["bab_org"].id
    elif change == "demo_revoked":
        demo.revoked_at, demo.is_active = datetime.utcnow(), False
    else:
        demo.expires_at = datetime.utcnow() - timedelta(days=1)
    db.commit()
    assert _granted(_ctx(client, w, "bab_admin")) == []
    assert client.get("/program-access/previews/%s" % gid, headers=_h(db, w["bab_admin"])).status_code == 404


@pytest.mark.parametrize("who", ["bab_admin", "evo_mgr", "oth_admin"])
def test_non_god_cannot_grant_list_or_revoke(client, w, who):
    db = w["db"]
    h = _h(db, w[who])
    body = {"opportunity_id": w["opp"].id, "grantee_user_id": w["bab_admin"].id,
            "platform_id": w["bab"].id, "reason": "x"}
    assert client.post("/god/preview-grants", json=body, headers=h).status_code == 403
    assert client.get("/god/preview-grants", headers=h).status_code == 403
    gid = _grant(client, w).json()["grant"]["id"]
    assert client.post("/god/preview-grants/%s/revoke" % gid, headers=h).status_code == 403
    assert db.query(DealPreviewGrant).filter(DealPreviewGrant.revoked_at.isnot(None)).count() == 0


def test_god_or_super_admin_is_never_a_grantee(client, w):
    db = w["db"]
    sa = _u(db, "sa-%s@example.com" % _sfx(), org=w["bab_org"], role="super_admin")
    for uid in (w["god"].id, sa.id):
        r = client.post("/god/preview-grants", json={
            "opportunity_id": w["opp"].id, "grantee_user_id": uid,
            "platform_id": w["bab"].id, "reason": "x"}, headers=_h(db, w["god"]))
        assert r.status_code == 422, r.text
    assert db.query(DealPreviewGrant).count() == 0


def test_validation_reason_duplicate_closed_deal(client, w):
    db = w["db"]
    assert _grant(client, w, reason="   ").status_code == 422
    assert _grant(client, w, expires_at=(datetime.utcnow() - timedelta(days=1)).isoformat()).status_code == 422
    assert _grant(client, w).status_code == 200
    assert _grant(client, w).status_code == 409
    assert _grant(client, w, opportunity_id="missing").status_code == 404
    assert db.query(DealPreviewGrant).count() == 1


def test_god_list_has_no_secret(client, w):
    _grant(client, w)
    r = client.get("/god/preview-grants?opportunity_id=%s" % w["opp"].id, headers=_h(w["db"], w["god"]))
    assert r.status_code == 200 and len(r.json()["grants"]) == 1
    _no_secret(w, r.text)


def test_brand_manager_launcher_unchanged_by_a_grant(client, w):
    """The deal brand's own manager keeps the #397 preview exactly as before."""
    before = _ctx(client, w, "evo_mgr")["preview_contexts"]
    _grant(client, w)
    after = _ctx(client, w, "evo_mgr")["preview_contexts"]
    assert before == after and len(after) == 1 and "url" in after[0]


def test_wholesale_locked_admin_can_still_open_the_preview(client, w):
    """A BookaBoost Wholesale admin is locked to Wholesale screens; the granted
    preview route is reachable (the grant is the authorization) while other
    platform screens stay refused."""
    db = w["db"]
    org = db.query(Organization).filter(Organization.id == w["bab_org"].id).first()
    org.enabled_features = json.dumps(["wholesale_workspace", "wholesale_real_estate"])
    db.commit()
    gid = _grant(client, w).json()["grant"]["id"]
    h = dict(_h(db, w["bab_admin"]), **{"X-Workspace-Id": org.id})
    locked = client.get("/campaigns/", headers=h)
    assert locked.status_code == 403 and "Wholesale" in locked.json()["detail"]
    r = client.get("/program-access/previews/%s" % gid, headers=h)
    assert r.status_code == 200, r.text


def test_dry_run_writes_nothing_and_email_slug_lookup_works(client, w):
    db = w["db"]
    body = {"opportunity_id": w["opp"].id, "grantee_email": w["bab_admin"].email.upper(),
            "platform_slug": w["bab"].slug, "reason": "check first", "expires_in_days": 7,
            "dry_run": True}
    r = client.post("/god/preview-grants", json=body, headers=_h(db, w["god"]))
    assert r.status_code == 200, r.text
    assert r.json()["dry_run"] is True and r.json()["would_create"]["grantee_user_id"] == w["bab_admin"].id
    _no_secret(w, r.text)
    assert db.query(DealPreviewGrant).count() == 0
    assert db.query(AuditLogEntry).filter(AuditLogEntry.action == "preview.grant").count() == 0
    body["dry_run"] = False
    assert client.post("/god/preview-grants", json=body, headers=_h(db, w["god"])).status_code == 200
    assert db.query(DealPreviewGrant).count() == 1
    body["grantee_email"] = "nobody@example.com"
    assert client.post("/god/preview-grants", json=body, headers=_h(db, w["god"])).status_code == 422
