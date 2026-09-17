"""
Per-organization outbound email enablement.

The deployment switches added earlier are not enough on a platform that serves
several customers from one process: turning bulk AI email on for the customer
who asked for it would have turned it on for every other tenant at the same
instant. The rule is now DEPLOYMENT **AND** ORGANIZATION, and neither half
alone sends anything.

The org half deliberately does NOT reuse Organization.enabled_features. That
column's NULL means "legacy customer, keep everything" - the right failure for
a screen and exactly the wrong one for a sender. It mirrors
delegated_capabilities instead, where NULL means God never said and the safe
reading of "never said" is no.

NOTHING IN THIS FILE SENDS.
"""

import json
from unittest.mock import patch

import pytest

from app.models.models import Lead, LeadStatus, Organization, User
from app.services import outbound_email_gate as gate
from app.services import send_source
from app.services.auth_service import create_access_token, hash_password


def _lead(db_session, org, advisor, **kw):
    lead = Lead(organization_id=org.id, assigned_to_id=getattr(advisor, "id", None),
                first_name="Org", last_name="Gate", phone=None,
                email=kw.pop("email", "fam@example.com"),
                status=kw.pop("status", "new"), **kw)
    db_session.add(lead)
    db_session.commit()
    return lead


def _deployment(monkeypatch, *sources):
    for s in gate.GATED_SOURCES:
        monkeypatch.delenv(gate._ENV_BY_SOURCE[s], raising=False)
    for s in sources:
        monkeypatch.setenv(gate._ENV_BY_SOURCE[s], "true")


def _god_headers(db_session):
    god = User(organization_id=None, email="god@platform.test",
               password_hash=hash_password("GodPass123!"), full_name="God",
               role="god_admin", must_change_password=False)
    db_session.add(god)
    db_session.commit()
    return {"Authorization": f"Bearer {create_access_token(god, db_session)}"}, god


# ── the org half, in isolation ──────────────────────────────────────────────

def test_a_customer_with_nothing_configured_has_no_sources(db_session, sample_org):
    assert sample_org.outbound_email_sources is None
    assert gate.org_enabled_sources(sample_org) == []
    assert gate.org_source_enabled(sample_org, send_source.BULK_AI) is False


def test_a_missing_organization_fails_closed():
    assert gate.org_enabled_sources(None) == []
    assert gate.org_source_enabled(None, send_source.BULK_AI) is False


def test_a_corrupt_list_is_not_a_licence(db_session, sample_org):
    for junk in ("not json", '{"bulk_ai": true}', "[1, 2, 3]", "null"):
        sample_org.outbound_email_sources = junk
        assert gate.org_enabled_sources(sample_org) == [], junk


def test_an_unknown_source_in_a_stored_list_is_dropped(db_session, sample_org):
    sample_org.outbound_email_sources = json.dumps(["bulk_ai", "nonsense", "auto_send"])
    # auto_send is not gated, nonsense is not a source: neither survives.
    assert gate.org_enabled_sources(sample_org) == ["bulk_ai"]


# ── both halves together ────────────────────────────────────────────────────

def test_deployment_on_and_org_off_does_not_send(
        db_session, sample_org, sample_advisor, monkeypatch):
    _deployment(monkeypatch, send_source.BULK_AI)
    lead = _lead(db_session, sample_org, sample_advisor)
    with patch("app.services.email_service.send_email_via_provider") as provider:
        with pytest.raises(gate.EmailSendDisabled, match="not enabled for this organization"):
            gate.gate_lead_email(db_session, lead, send_source=send_source.BULK_AI)
    provider.assert_not_called()


def test_org_on_and_deployment_off_does_not_send(
        db_session, sample_org, sample_advisor, monkeypatch):
    _deployment(monkeypatch)
    sample_org.outbound_email_sources = json.dumps([send_source.BULK_AI])
    db_session.commit()
    lead = _lead(db_session, sample_org, sample_advisor)
    with patch("app.services.email_service.send_email_via_provider") as provider:
        with pytest.raises(gate.EmailSendDisabled, match="OUTBOUND_EMAIL_BULK_AI"):
            gate.gate_lead_email(db_session, lead, send_source=send_source.BULK_AI)
    provider.assert_not_called()


def test_both_on_clears_the_gate_and_the_gate_alone_sends_nothing(
        db_session, sample_org, sample_advisor, monkeypatch):
    """With both switches on the gate returns, and returning is not sending:
    `gate_lead_email` resolves no provider and contacts nobody. The send is the
    caller's separate, explicit step through `send_lead_email`."""
    _deployment(monkeypatch, send_source.BULK_AI)
    sample_org.outbound_email_sources = json.dumps([send_source.BULK_AI])
    db_session.commit()
    lead = _lead(db_session, sample_org, sample_advisor)
    with patch("app.services.email_service.send_email_via_provider") as provider:
        assert gate.gate_lead_email(
            db_session, lead, send_source=send_source.BULK_AI) is None
    provider.assert_not_called()


def test_one_customer_enabling_a_source_never_enables_it_for_another(
        db_session, sample_org, sample_advisor, monkeypatch):
    """The reason the org half exists at all."""
    _deployment(monkeypatch, send_source.BULK_AI)
    other = Organization(name="Neighbour Co", slug="neighbour", plan="trial")
    db_session.add(other)
    db_session.flush()
    neighbour_advisor = User(organization_id=other.id, email="n@neighbour.test",
                             password_hash=hash_password("NPass123!"),
                             full_name="Neighbour", role="advisor",
                             must_change_password=False)
    db_session.add(neighbour_advisor)
    sample_org.outbound_email_sources = json.dumps([send_source.BULK_AI])
    db_session.commit()

    mine = _lead(db_session, sample_org, sample_advisor)
    theirs = _lead(db_session, other, neighbour_advisor, email="n-fam@example.com")

    # Mine clears the gate.
    assert gate.gate_lead_email(db_session, mine,
                                send_source=send_source.BULK_AI) is None
    # Theirs is stopped at the organization half.
    with pytest.raises(gate.EmailSendDisabled, match="not enabled for this organization"):
        gate.gate_lead_email(db_session, theirs, send_source=send_source.BULK_AI)


def test_enabling_one_source_for_an_org_does_not_enable_its_others(
        db_session, sample_org, sample_advisor, monkeypatch):
    _deployment(monkeypatch, *gate.GATED_SOURCES)
    sample_org.outbound_email_sources = json.dumps([send_source.APPOINTMENT_FOLLOWUP])
    db_session.commit()
    lead = _lead(db_session, sample_org, sample_advisor)

    assert gate.gate_lead_email(
        db_session, lead, send_source=send_source.APPOINTMENT_FOLLOWUP) is None
    for other in (send_source.BULK_AI, send_source.PIPELINE_AUTO_REPLY,
                  send_source.VOICE_BOOKING_LINK):
        with pytest.raises(gate.EmailSendDisabled, match="not enabled for this organization"):
            gate.gate_lead_email(db_session, lead, send_source=other)


def test_compliance_still_refuses_first_with_both_switches_on(
        db_session, sample_org, sample_advisor, monkeypatch):
    """The gate order must not change: a DNC family is refused on compliance
    grounds, not told the source is off."""
    _deployment(monkeypatch, send_source.BULK_AI)
    sample_org.outbound_email_sources = json.dumps([send_source.BULK_AI])
    db_session.commit()
    lead = _lead(db_session, sample_org, sample_advisor, status="dnc")
    with pytest.raises(ValueError, match="DNC"):
        gate.gate_lead_email(db_session, lead, send_source=send_source.BULK_AI)


def test_the_gate_reads_the_leads_org_not_the_callers(
        db_session, sample_org, sample_advisor, monkeypatch):
    """A god_admin has organization_id None. Reading the caller's tenancy would
    answer the wrong question for exactly the caller most able to cause damage,
    so the lead's organization decides - the rule _demo_send_guard follows."""
    _deployment(monkeypatch, send_source.BULK_AI)
    sample_org.outbound_email_sources = json.dumps([send_source.BULK_AI])
    db_session.commit()
    lead = _lead(db_session, sample_org, sample_advisor)
    resolved = gate._org_for_lead(db_session, lead)
    assert resolved is not None and resolved.id == sample_org.id


# ── the God control surface ────────────────────────────────────────────────

def test_god_can_enable_one_source_for_one_customer(client, db_session, sample_org):
    headers, _god = _god_headers(db_session)
    response = client.put(f"/god/customers/{sample_org.id}/outbound-email-sources",
                          headers=headers, json={"sources": ["bulk_ai"]})
    assert response.status_code == 200
    assert response.json()["enabled"] == ["bulk_ai"]

    db_session.expire_all()
    org = db_session.query(Organization).filter(Organization.id == sample_org.id).one()
    assert gate.org_enabled_sources(org) == ["bulk_ai"]


def test_god_setting_sources_is_audited_with_a_real_actor(client, db_session, sample_org):
    from app.models.models import AuditLogEntry
    headers, god = _god_headers(db_session)
    client.put(f"/god/customers/{sample_org.id}/outbound-email-sources",
               headers=headers, json={"sources": ["bulk_ai"]})
    row = (db_session.query(AuditLogEntry)
           .filter(AuditLogEntry.action == "customer.outbound_email_sources_set")
           .first())
    assert row is not None
    assert row.actor_user_id == god.id


def test_an_unknown_source_is_refused_rather_than_stored(client, db_session, sample_org):
    headers, _ = _god_headers(db_session)
    response = client.put(f"/god/customers/{sample_org.id}/outbound-email-sources",
                          headers=headers, json={"sources": ["bulk_ai", "nonsense"]})
    assert response.status_code == 400
    assert "nonsense" in response.json()["detail"]
    db_session.expire_all()
    org = db_session.query(Organization).filter(Organization.id == sample_org.id).one()
    assert gate.org_enabled_sources(org) == [], "a refused request must store nothing"


def test_the_endpoint_refuses_an_unknown_field_rather_than_ignoring_it(
        client, db_session, sample_org):
    """FeaturesIn learned this the hard way: a misspelled key that is ignored
    turns a no-op request into a silent grant."""
    headers, _ = _god_headers(db_session)
    response = client.put(f"/god/customers/{sample_org.id}/outbound-email-sources",
                          headers=headers, json={"enabled": ["bulk_ai"]})
    assert response.status_code == 422


def test_the_report_shows_both_halves(client, db_session, sample_org, monkeypatch):
    _deployment(monkeypatch, send_source.BULK_AI)
    headers, _ = _god_headers(db_session)
    client.put(f"/god/customers/{sample_org.id}/outbound-email-sources",
               headers=headers, json={"sources": ["appointment_followup"]})

    response = client.get(f"/god/customers/{sample_org.id}/outbound-email-sources",
                          headers=headers)
    assert response.status_code == 200
    eff = response.json()["effective"]
    assert eff["bulk_ai"]["deployment_enabled"] is True
    assert eff["bulk_ai"]["organization_enabled"] is False
    assert eff["bulk_ai"]["effective"] is False
    assert eff["appointment_followup"]["organization_enabled"] is True
    assert eff["appointment_followup"]["deployment_enabled"] is False
    assert eff["appointment_followup"]["effective"] is False
    assert not any(v["effective"] for v in eff.values())


def test_a_tenant_admin_cannot_change_outbound_sources(
        client, admin_auth_headers, db_session, sample_org):
    response = client.put(f"/god/customers/{sample_org.id}/outbound-email-sources",
                          headers=admin_auth_headers, json={"sources": ["bulk_ai"]})
    assert response.status_code in (401, 403)
    db_session.expire_all()
    org = db_session.query(Organization).filter(Organization.id == sample_org.id).one()
    assert gate.org_enabled_sources(org) == []


def test_nothing_in_this_file_left_a_deployment_switch_on():
    assert not any(gate.enablement_report().values())
