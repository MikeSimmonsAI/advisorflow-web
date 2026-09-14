import json

from app.models.models import Organization, User, UserCapabilityGrant
from app.services.auth_service import create_access_token, hash_password


def _twilio_admin_headers(db, org):
    org.delegated_capabilities = json.dumps(["twilio_numbers"])
    admin = User(
        organization_id=org.id,
        email="twilio-admin@example.com",
        password_hash=hash_password("AdminPass123!"),
        full_name="Twilio Admin",
        role="org_admin",
        must_change_password=False,
    )
    db.add(admin)
    db.flush()
    db.add(UserCapabilityGrant(
        user_id=admin.id,
        organization_id=org.id,
        scope_type="customer_org",
        scope_id=org.id,
        capability="twilio_numbers",
        is_active=True,
    ))
    db.commit()
    return {"Authorization": "Bearer " + create_access_token(admin, db)}


def test_transfer_advisor_number_to_shared_org_sender_is_atomic(
    client, db_session, sample_org, second_advisor
):
    sample_org.org_twilio_phone_number = None
    sample_org.org_twilio_caller_id_name = None
    second_advisor.twilio_phone_number = "+14692241155"
    second_advisor.twilio_caller_id_name = "Jerome Simmons"
    db_session.commit()

    response = client.post(
        "/org-settings/twilio/phone/transfer-from-user",
        headers=_twilio_admin_headers(db_session, sample_org),
        json={
            "user_id": second_advisor.id,
            "org_twilio_number_type": "10dlc",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["org_twilio_phone_number"] == "+14692241155"
    assert body["org_twilio_number_type"] == "10dlc"
    assert body["org_twilio_caller_id_name"] == sample_org.name
    assert body["user_twilio_phone_number"] is None

    db_session.refresh(sample_org)
    db_session.refresh(second_advisor)
    assert sample_org.org_twilio_phone_number == "+14692241155"
    assert sample_org.org_twilio_number_type == "10dlc"
    assert second_advisor.twilio_phone_number is None


def test_transfer_refuses_number_used_outside_this_org(
    client, db_session, sample_org, second_advisor
):
    headers = _twilio_admin_headers(db_session, sample_org)
    other_org = Organization(name="Other Org", slug="other-org", plan="standard")
    db_session.add(other_org)
    db_session.flush()
    other_user = User(
        organization_id=other_org.id,
        email="other@example.com",
        password_hash=hash_password("OtherPass123!"),
        full_name="Other User",
        role="advisor",
        twilio_phone_number="+14692241155",
        must_change_password=False,
    )
    db_session.add(other_user)
    second_advisor.twilio_phone_number = "+14692241155"
    db_session.commit()

    response = client.post(
        "/org-settings/twilio/phone/transfer-from-user",
        headers=headers,
        json={"user_id": second_advisor.id},
    )

    assert response.status_code == 409
    db_session.refresh(sample_org)
    db_session.refresh(second_advisor)
    assert sample_org.org_twilio_phone_number != "+14692241155"
    assert second_advisor.twilio_phone_number == "+14692241155"
