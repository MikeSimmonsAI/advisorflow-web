"""Customer Overview: "Total leads" and "Lead flow" use the same test-record rule.

Total leads comes from GET /leads/?page_size=1&exclude_test=true; Lead flow from
GET /leads/status-funnel. Both must exclude internal test records (Lead.is_test),
so the funnel stages always reconcile against the total. The Leads LIST still
shows test records by default (testers need them).
"""
from app.models.models import Lead, LeadStatus


def _lead(db, org, advisor, n, status, is_test=False):
    db.add(Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name="R%d" % n, last_name="Count", phone="1214555%04d" % n,
                status=status, is_test=is_test))


def test_total_and_funnel_reconcile(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    real = [LeadStatus.NEW, LeadStatus.NEW, LeadStatus.SENT, LeadStatus.REPLIED, LeadStatus.BOOKED]
    for i, st in enumerate(real):
        _lead(db_session, sample_org, sample_advisor, i, st)
    for i, st in enumerate([LeadStatus.NEW, LeadStatus.BOOKED, LeadStatus.HOT]):
        _lead(db_session, sample_org, sample_advisor, 100 + i, st, is_test=True)
    db_session.commit()

    total = client.get("/leads/?page=1&page_size=1&exclude_test=true", headers=admin_auth_headers)
    assert total.status_code == 200, total.text
    funnel = client.get("/leads/status-funnel", headers=admin_auth_headers)
    assert funnel.status_code == 200, funnel.text

    stage_sum = sum(s["count"] for s in funnel.json())
    assert total.json()["total"] == len(real)
    # Every real lead here is in a funnel stage, so the two numbers must agree.
    assert stage_sum == total.json()["total"]
    by = {s["status"]: s["count"] for s in funnel.json()}
    assert by["hot"] == 0 and by["booked"] == 1 and by["new"] == 2

    # The list itself is unchanged: test records still appear without the flag.
    listed = client.get("/leads/?page=1&page_size=1", headers=admin_auth_headers)
    assert listed.json()["total"] == len(real) + 3


def test_dnc_count_excludes_test_records(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    _lead(db_session, sample_org, sample_advisor, 1, LeadStatus.DNC)
    _lead(db_session, sample_org, sample_advisor, 2, LeadStatus.DNC, is_test=True)
    db_session.commit()
    r = client.get("/leads/?status=dnc&page=1&page_size=1&exclude_test=true", headers=admin_auth_headers)
    assert r.status_code == 200
    assert r.json()["total"] == 1
