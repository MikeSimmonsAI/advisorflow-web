"""Private BookaBoost / AdvisorFlow Opportunity Radar contract — synthetic data only."""
from datetime import datetime, timedelta
import json
import uuid

import pytest

from app.models.models import Organization, User
from app.models.evosense_models import EvoSenseProperty, EvoSenseSignal
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password
from app.services.evosense import overlooked as RADAR


def org(db, name):
    o = Organization(name=name, slug="radar-" + uuid.uuid4().hex[:12],
                     plan="standard", industry="real_estate", is_active=True,
                     enabled_features=json.dumps(["wholesale_real_estate", "wholesale_workspace",
                                                  "leads", "users", "reports"]))
    db.add(o)
    db.flush()
    return o


def property_record(db, o, address, category, *, state="TX", score=68,
                    test=False, archived=False):
    p = EvoSenseProperty(organization_id=o.id, street_address=address,
                         state=state, county="Tarrant", city="Arlington",
                         property_category=category,
                         property_class_source="county_record",
                         opportunity_score=score, data_confidence="medium",
                         is_test=test,
                         archived_at=datetime.utcnow() if archived else None)
    db.add(p)
    db.flush()
    return p


def evidence(db, o, p, kind, *, source="county_record", days_ago=5):
    s = EvoSenseSignal(organization_id=o.id, property_id=p.id,
                       signal_type=kind, source=source, active=True,
                       confidence=80,
                       observed_at=datetime.utcnow() - timedelta(days=days_ago),
                       effective_at=datetime.utcnow() - timedelta(days=days_ago))
    db.add(s)
    db.flush()
    return s


def test_radar_tenant_isolation_and_source_evidence(db_session):
    db = db_session
    a = org(db, "BookaBoost Private")
    b = org(db, "EvoSys Private")
    a_p = property_record(db, a, "A Only St", "residential", state="TX")
    b_p = property_record(db, b, "B Only St", "residential", state="OK")
    evidence(db, a, a_p, "PRE_FORECLOSURE", source="county_notice")
    evidence(db, b, b_p, "TAX_DELINQUENT", source="other_county")
    db.commit()

    found = RADAR.search(db, a.id, market="residential")
    assert found["total"] == 1
    assert [v["address"] for v in found["items"]] == ["A Only St"]
    assert found["items"][0]["why_review"][0]["source"] == "county_notice"
    assert found["items"][0]["why_review"][0]["type"] == "PRE_FORECLOSURE"
    assert found["items"][0]["existing_opportunity_score"] == 68
    assert RADAR.search(db, a.id, state="OK")["total"] == 0
    assert RADAR.search(db, b.id, state="OK")["total"] == 1


def test_farmland_never_presents_vacancy_or_absentee_as_opportunity_evidence(db_session):
    db = db_session
    a = org(db, "Land Specialist")
    p = property_record(db, a, "Ranch Road 1", "agricultural", state="KS")
    evidence(db, a, p, "VACANT")
    evidence(db, a, p, "ABSENTEE_OWNER")
    db.commit()
    result = RADAR.search(db, a.id, market="land")
    assert result["total"] == 1
    item = result["items"][0]
    assert item["why_review"] == [] and item["evidence_status"] == "needs_research"
    assert RADAR.search(db, a.id, market="residential")["total"] == 0


def test_no_fabricated_score_and_unknown_property_class_requires_review(db_session):
    db = db_session
    a = org(db, "Unclassified")
    p = property_record(db, a, "Unknown 5", None, score=None, state="CA")
    evidence(db, a, p, "CODE_HISTORY")
    db.commit()
    item = RADAR.search(db, a.id, market="all")["items"][0]
    assert item["existing_opportunity_score"] is None
    assert item["why_review"] == [] and item["classification_review_required"]
    assert RADAR.search(db, a.id, market="residential")["total"] == 0
    assert RADAR.search(db, a.id, market="wholesale")["total"] == 0


def test_test_and_archived_records_hidden_without_mutations(db_session):
    db = db_session
    a = org(db, "Safety")
    property_record(db, a, "Real Ave", "commercial")
    property_record(db, a, "Test Ave", "commercial", test=True)
    property_record(db, a, "Archived Ave", "commercial", archived=True)
    db.commit()
    assert RADAR.search(db, a.id, market="commercial")["total"] == 1
    assert RADAR.search(db, a.id, market="commercial", include_test=True)["total"] == 2
    assert db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == a.id).count() == 3


def test_filters_pagination_and_invalid_scope(db_session):
    db = db_session
    a = org(db, "Nationwide")
    property_record(db, a, "Arkansas 1", "land", state="AR", score=40)
    property_record(db, a, "Arkansas 2", "land", state="AR", score=80)
    property_record(db, a, "Georgia 1", "commercial", state="GA", score=90)
    db.commit()
    first = RADAR.search(db, a.id, market="land", state="ar", min_score=50, limit=1)
    assert first["total"] == 1 and first["items"][0]["address"] == "Arkansas 2"
    second = RADAR.search(db, a.id, market="all", limit=1, offset=1)
    assert second["total"] == 3 and len(second["items"]) == 1
    for kwargs in ({"market": "bogus"}, {"limit": 10000}, {"min_score": 500},
                   {"state": "USA"}):
        with pytest.raises(ValueError):
            RADAR.search(db, a.id, **kwargs)
    with pytest.raises(ValueError):
        RADAR.search(db, "")


def test_private_radar_endpoint_rejects_anonymous(client):
    response = client.get("/wholesale/evosense/opportunity-radar")
    assert response.status_code in (401, 403), response.text[:200]


def test_private_radar_endpoint_workspace_scoped(client, db_session):
    db = db_session
    a = org(db, "BookaBoost Radar")
    b = org(db, "Other Radar")
    property_record(db, a, "Our Only St", "land")
    property_record(db, b, "Secret Other St", "land")
    user = User(organization_id=a.id, email="radar-" + uuid.uuid4().hex[:8] + "@example.com",
                full_name="Test Radar Admin", role="org_admin",
                password_hash=hash_password("Pass12345!"), is_active=True,
                must_change_password=False)
    db.add(user)
    db.flush()
    db.add(Membership(user_id=user.id, scope_type="customer_org", scope_id=a.id,
                      role="org_admin", is_active=True))
    db.commit()
    headers = {"Authorization": "Bearer " + create_access_token(user, db),
               "X-Workspace-Id": a.id}
    r = client.get("/wholesale/evosense/opportunity-radar", headers=headers)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["total"] == 1
    assert "Secret Other St" not in r.text
    attack = client.get("/wholesale/evosense/opportunity-radar", headers={
        **headers, "X-Workspace-Id": b.id})
    assert attack.status_code in (403, 404), attack.text[:300]
