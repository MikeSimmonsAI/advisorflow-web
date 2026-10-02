"""Owner integrity report: zero on a clean database, and it finds what it says."""
from app.models.models import Lead, Notification, NotificationType, Organization, User
from app.services import data_integrity
from app.services.auth_service import create_access_token, hash_password


def _org(db, slug):
    o = Organization(name=slug, slug=slug, plan="standard", industry="insurance")
    db.add(o)
    db.commit()
    return o


def _user(db, org, email, role="advisor"):
    u = User(organization_id=org.id if org else None, email=email, password_hash=hash_password("x"),
             full_name=email, role=role, must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return u


def test_clean_database_reports_nothing(db_session, sample_lead):
    rep = data_integrity.run(db_session)
    assert rep["read_only"] is True and rep["found"] == 0, [c for c in rep["checks"] if c["status"] == "found"]
    assert all(c["status"] in ("ok", "not_applicable") for c in rep["checks"])


def test_finds_cross_tenant_assignment_and_notification_but_honours_membership(db_session):
    from app.models.sales_models import Membership
    a, b = _org(db_session, "int-a"), _org(db_session, "int-b")
    outsider = _user(db_session, b, "out@b.test")
    member = _user(db_session, b, "member@b.test")
    db_session.add(Membership(user_id=member.id, scope_type="customer_org", scope_id=a.id, role="advisor", is_active=True))
    leak = Lead(organization_id=a.id, first_name="L", assigned_to_id=outsider.id, status="new", phone="+12145550101")
    ok = Lead(organization_id=a.id, first_name="M", assigned_to_id=member.id, status="new", phone="+12145550102")
    db_session.add_all([leak, ok])
    db_session.commit()
    db_session.add(Notification(user_id=outsider.id, lead_id=leak.id, type=NotificationType.HOT_REPLY, message="x"))
    dnc = Lead(organization_id=a.id, first_name="D", status="dnc", sms_consent=True, phone="+12145550101")
    db_session.add(dnc)
    db_session.commit()
    rep = {c["key"]: c for c in data_integrity.run(db_session)["checks"]}
    assert rep["lead_assigned_across_tenants"]["count"] == 1
    assert rep["lead_assigned_across_tenants"]["examples"] == [leak.id]
    assert rep["notification_across_tenants"]["count"] == 1
    assert rep["dnc_with_sms_consent"]["count"] == 1
    assert rep["duplicate_phone_in_workspace"]["count"] == 1


def test_endpoint_is_owner_only(client, db_session, auth_headers):
    assert client.get("/god/maintenance/integrity", headers=auth_headers).status_code in (401, 403)
    god = _user(db_session, None, "owner@int.test", role="god_admin")
    r = client.get("/god/maintenance/integrity",
                   headers={"Authorization": "Bearer %s" % create_access_token(god, db_session)})
    assert r.status_code == 200 and r.json()["read_only"] is True
