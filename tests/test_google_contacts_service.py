"""
Tests for app/services/google_contacts_service.py - the CURRENT, manual
Google Contacts integration.

WHAT THIS FILE USED TO TEST, AND WHY IT NO LONGER DOES
------------------------------------------------------
The previous version tested an automatic-sync layer that has since been
removed from the codebase. It is deliberately NOT recreated here. The
mapping from the old assertions to current reality:

  old                                        current
  ----------------------------------------   -------------------------------
  sync_lead_to_google_contacts(db, lead)     push_lead_to_google_contacts(
    returning {"success", "skipped_reason",    db, user, lead) - the advisor is
    "error"} and never raising                passed in explicitly, and failure
                                              RAISES ValueError rather than
                                              being reported in a result dict.
                                              google_contacts_router turns that
                                              ValueError into an HTTP 400.
  sync_leads_to_google_contacts_batch        no equivalent. There is no batch
    with succeeded/skipped/failed counts     entry point; bulk import does not
                                             push to Google at all.
  patching _get_people_service               no equivalent. The service talks
    (a googleapiclient discovery client)     to the People API over plain
                                             `requests`, so these tests patch
                                             `requests` in that module instead.
  "Already synced." idempotency via          no equivalent. The column is not
    Lead.google_contact_resource_name        on the Lead model and alembic
                                             02907fcdb80c DROPS it from `leads`.
                                             Every push creates a new contact.
  skip when the lead has no assigned         no equivalent. The caller supplies
    advisor                                  the user, so there is no lead ->
                                             advisor lookup to fail.
  skip when the lead has no phone or         NOT skipped any more, and that is
    email                                    asserted below instead: the current
                                             service posts a names-only contact.

What survives from the old file is its intent - exercise the integration
without touching real Google services - and the guarantees still worth
holding: an unconnected advisor causes no HTTP call at all, the payload
carries exactly the fields the lead actually has, and every non-OK Google
response becomes a clear ValueError rather than a silent success.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.models.models import Lead, LeadStatus, LeadTier
from app.services.google_contacts_service import (
    PEOPLE_API_BASE, pull_google_contacts, push_lead_to_google_contacts,
)
from app.utils.crypto import encrypt_value


# ── helpers ──────────────────────────────────────────────────────────────────

def _connected(db_session, advisor):
    advisor.google_oauth_refresh_token_encrypted = encrypt_value("fake-refresh-token")
    advisor.google_calendar_connected = True
    db_session.commit()
    return advisor


def _lead(db_session, org, advisor, idx=1, **kwargs):
    lead = Lead(
        organization_id=org.id, assigned_to_id=advisor.id,
        first_name=kwargs.pop("first_name", "Contact%d" % idx),
        last_name=kwargs.pop("last_name", "Sync"),
        phone=kwargs.pop("phone", "1214555%04d" % idx),
        status=LeadStatus.NEW, **kwargs,
    )
    db_session.add(lead)
    db_session.commit()
    return lead


def _ok(payload):
    """A successful `requests` response double."""
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


def _err(status_code, text="boom"):
    resp = MagicMock()
    resp.ok = False
    resp.status_code = status_code
    resp.text = text
    return resp


def _token_ok():
    return _ok({"access_token": "fake-access-token"})


# ═════════════════════════════════════════════════════════════════════════════
# Connection gate - an advisor who has not connected Google causes NO HTTP
# call whatsoever. This is the old "skips when advisor not connected" test,
# expressed against the raising contract the service actually has.
# ═════════════════════════════════════════════════════════════════════════════

def test_push_refuses_when_advisor_has_not_connected_google(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch("app.services.google_contacts_service.requests") as req:
        with pytest.raises(ValueError, match="not connected"):
            push_lead_to_google_contacts(db_session, sample_advisor, lead)
        req.post.assert_not_called()


def test_push_refuses_when_the_refresh_token_is_missing(db_session, sample_org, sample_advisor):
    """The connected flag alone is not enough - without a stored refresh token
    there is nothing to exchange, and the service must say so rather than
    calling Google with an empty credential."""
    sample_advisor.google_calendar_connected = True
    sample_advisor.google_oauth_refresh_token_encrypted = None
    db_session.commit()
    lead = _lead(db_session, sample_org, sample_advisor, idx=2)

    with patch("app.services.google_contacts_service.requests") as req:
        with pytest.raises(ValueError, match="not connected"):
            push_lead_to_google_contacts(db_session, sample_advisor, lead)
        req.post.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
# Token exchange failures surface as clear errors, never as a silent success
# ═════════════════════════════════════════════════════════════════════════════

def test_push_raises_when_google_rejects_the_refresh_token(db_session, sample_org, sample_advisor):
    _connected(db_session, sample_advisor)
    lead = _lead(db_session, sample_org, sample_advisor, idx=3)

    with patch("app.services.google_contacts_service.requests") as req:
        req.post.return_value = _err(400, "invalid_grant")
        with pytest.raises(ValueError, match="Failed to refresh Google token"):
            push_lead_to_google_contacts(db_session, sample_advisor, lead)


def test_push_raises_when_the_token_response_has_no_access_token(db_session, sample_org, sample_advisor):
    _connected(db_session, sample_advisor)
    lead = _lead(db_session, sample_org, sample_advisor, idx=4)

    with patch("app.services.google_contacts_service.requests") as req:
        req.post.return_value = _ok({"scope": "contacts"})
        with pytest.raises(ValueError, match="No access token"):
            push_lead_to_google_contacts(db_session, sample_advisor, lead)


# ═════════════════════════════════════════════════════════════════════════════
# The successful push - endpoint, auth header and payload
# ═════════════════════════════════════════════════════════════════════════════

def test_push_creates_the_contact_and_returns_googles_payload(db_session, sample_org, sample_advisor):
    _connected(db_session, sample_advisor)
    lead = _lead(db_session, sample_org, sample_advisor, idx=5,
                 email="casey@example.com", tier=LeadTier.PRE_NEED)

    with patch("app.services.google_contacts_service.requests") as req:
        req.post.side_effect = [_token_ok(), _ok({"resourceName": "people/c123456"})]

        result = push_lead_to_google_contacts(db_session, sample_advisor, lead)

    assert result == {"resourceName": "people/c123456"}

    create_call = req.post.call_args_list[1]
    assert create_call.args[0] == f"{PEOPLE_API_BASE}/people:createContact"
    assert create_call.kwargs["headers"]["Authorization"] == "Bearer fake-access-token"

    body = create_call.kwargs["json"]
    assert body["names"] == [{"givenName": "Contact5", "familyName": "Sync"}]
    assert body["phoneNumbers"] == [{"value": lead.phone, "type": "mobile"}]
    assert body["emailAddresses"] == [{"value": "casey@example.com", "type": "home"}]
    assert "Tier: " in body["biographies"][0]["value"]


def test_push_omits_phone_and_email_keys_when_the_lead_has_neither(db_session, sample_org, sample_advisor):
    """Replaces the old "skips when lead has no phone or email" assertion.

    The current service does NOT skip such a lead - it posts a names-only
    contact. Pinning that here so the difference is visible rather than
    assumed: the keys are absent, not present-and-empty."""
    _connected(db_session, sample_advisor)
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="NoContact", last_name="Info", phone=None, email=None,
                status=LeadStatus.NEW)
    db_session.add(lead)
    db_session.commit()

    with patch("app.services.google_contacts_service.requests") as req:
        req.post.side_effect = [_token_ok(), _ok({"resourceName": "people/cnames"})]

        push_lead_to_google_contacts(db_session, sample_advisor, lead)

    body = req.post.call_args_list[1].kwargs["json"]
    assert body["names"] == [{"givenName": "NoContact", "familyName": "Info"}]
    assert "phoneNumbers" not in body
    assert "emailAddresses" not in body


# ═════════════════════════════════════════════════════════════════════════════
# People API failures
# ═════════════════════════════════════════════════════════════════════════════

def test_push_explains_a_missing_contacts_scope_rather_than_a_bare_403(db_session, sample_org, sample_advisor):
    """A 403 here almost always means the advisor connected Google before the
    contacts scope was requested. The message has to tell them to reconnect,
    because nothing else in the product will."""
    _connected(db_session, sample_advisor)
    lead = _lead(db_session, sample_org, sample_advisor, idx=6)

    with patch("app.services.google_contacts_service.requests") as req:
        req.post.side_effect = [_token_ok(), _err(403, "insufficient scope")]

        with pytest.raises(ValueError, match="reconnect Google"):
            push_lead_to_google_contacts(db_session, sample_advisor, lead)


def test_push_raises_on_any_other_google_failure(db_session, sample_org, sample_advisor):
    _connected(db_session, sample_advisor)
    lead = _lead(db_session, sample_org, sample_advisor, idx=7)

    with patch("app.services.google_contacts_service.requests") as req:
        req.post.side_effect = [_token_ok(), _err(429, "Rate limit exceeded")]

        with pytest.raises(ValueError, match="Failed to create Google Contact"):
            push_lead_to_google_contacts(db_session, sample_advisor, lead)


def test_push_does_not_write_anything_to_the_lead_row(db_session, sample_org, sample_advisor):
    """The idempotency column the old suite asserted on
    (Lead.google_contact_resource_name) was dropped by alembic 02907fcdb80c.
    Nothing about the push is recorded against the lead any more, so a second
    push creates a second contact - stated here so the absence is a documented
    property rather than an oversight."""
    _connected(db_session, sample_advisor)
    lead = _lead(db_session, sample_org, sample_advisor, idx=8)

    assert not hasattr(lead, "google_contact_resource_name")

    with patch("app.services.google_contacts_service.requests") as req:
        req.post.side_effect = [_token_ok(), _ok({"resourceName": "people/cfirst"}),
                                _token_ok(), _ok({"resourceName": "people/csecond"})]

        first = push_lead_to_google_contacts(db_session, sample_advisor, lead)
        second = push_lead_to_google_contacts(db_session, sample_advisor, lead)

    assert first["resourceName"] == "people/cfirst"
    assert second["resourceName"] == "people/csecond"


# ═════════════════════════════════════════════════════════════════════════════
# The import direction - pull_google_contacts, the other half of the current
# integration and the one that feeds import_leads_from_rows.
# ═════════════════════════════════════════════════════════════════════════════

def test_pull_maps_contacts_into_import_rows(db_session, sample_advisor):
    _connected(db_session, sample_advisor)

    with patch("app.services.google_contacts_service.requests") as req:
        req.post.return_value = _token_ok()
        req.get.return_value = _ok({"connections": [{
            "names": [{"givenName": "Robin", "familyName": "Fields"}],
            "phoneNumbers": [{"value": "(214) 555-0301"}],
            "emailAddresses": [{"value": "robin@example.com"}],
        }]})

        rows = pull_google_contacts(sample_advisor)

    assert len(rows) == 1
    assert rows[0]["first_name"] == "Robin"
    assert rows[0]["last_name"] == "Fields"
    # Punctuation stripped so the value matches the app's phone handling.
    assert rows[0]["phone"] == "2145550301"
    assert rows[0]["email"] == "robin@example.com"
    assert rows[0]["source_raw"] == "google_contacts"


def test_pull_drops_contacts_with_nothing_usable(db_session, sample_advisor):
    """A Google contact with only a given name is not a lead - importing it
    would create a row nobody can ever be contacted through."""
    _connected(db_session, sample_advisor)

    with patch("app.services.google_contacts_service.requests") as req:
        req.post.return_value = _token_ok()
        req.get.return_value = _ok({"connections": [
            {"names": [{"givenName": "OnlyFirstName"}]},
            {"names": [{"givenName": "Real", "familyName": "Person"}],
             "phoneNumbers": [{"value": "2145550302"}]},
        ]})

        rows = pull_google_contacts(sample_advisor)

    assert [r["last_name"] for r in rows] == ["Person"]


def test_pull_explains_a_missing_contacts_scope(db_session, sample_advisor):
    _connected(db_session, sample_advisor)

    with patch("app.services.google_contacts_service.requests") as req:
        req.post.return_value = _token_ok()
        req.get.return_value = _err(403, "insufficient scope")

        with pytest.raises(ValueError, match="reconnect Google"):
            pull_google_contacts(sample_advisor)
