"""
Tests for app/routers/auto_send_router.py - the advisor review queue.

The single most important property across this file is unchanged from the
version that preceded it: NOTHING EVER SENDS WITHOUT AN EXPLICIT ACTION BY
THE AUTHENTICATED ADVISOR WHO OWNS THE QUEUE ROW, and when a send does
happen it goes through the same DNC/suppression-checked send_sms every
other path uses.

WHY THIS FILE WAS REWRITTEN
---------------------------
The previous version tested a Phase-1 "candidate" API that no longer
exists. The queue was rearchitected onto AutoSendItem (defined on the
router itself, table auto_send_queue) and the endpoints were renamed:

    old                                     current
    POST /auto-send/queue/{id}/confirm      POST  /auto-send/{id}/approve
    POST /auto-send/queue/{id}/edit-and-send PATCH /auto-send/{id}/edit
    POST /auto-send/queue/{id}/override     POST  /auto-send/{id}/skip
    GET  /auto-send/queue/counts            (no equivalent - see below)

Three behavioural differences the old assertions encoded, now asserted the
way the current router actually behaves:

  * Acting on an already-actioned item returns 404, not 400. The current
    endpoints put `status == "pending"` inside the lookup filter, so a
    resolved row is simply not found. 404 is also what another advisor's
    row returns, which is deliberate - it refuses to confirm the row exists.
  * A blocked send is not a 4xx. approve_item catches the ValueError that
    send_sms raises for a DNC or suppressed lead, records it in ai_reason
    and marks the item "failed". The guarantee that matters is preserved
    and asserted below: the carrier is never called and the item is not
    marked sent.
  * Editing no longer sends. PATCH /edit only rewrites the body of a
    pending item; approving is a separate, explicit second action.

ON THE MISSING COUNTS ENDPOINT
------------------------------
GET /auto-send/queue/counts has no equivalent in the current router and one
was NOT invented for this file. The current surface that answers "how many
are waiting" is GET /auto-send/queue itself - it returns the pending rows,
already scoped to the caller - so the old counts assertion is replaced by
test_queue_length_reflects_real_pending_items, which pins the same fact
(the queue reports exactly the caller's own pending rows, no more) against
an endpoint that exists.
"""

import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.models.models import Lead, Organization, SuppressionEntry
# The queue row lives on the router, not in app.models.models - it is
# declared there against the shared Base with __tablename__ auto_send_queue.
from app.routers.auto_send_router import AutoSendItem


# ── helpers ──────────────────────────────────────────────────────────────────

def _lead(db_session, org, advisor, phone="12145559800", email=None, status="new"):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name="Queue", last_name="Lead", phone=phone,
                email=email, status=status)
    db_session.add(lead)
    db_session.commit()
    return lead


def _queued(db_session, org, advisor, message="Sounds good, see you at 2pm!",
            phone="12145559800", channel="sms", subject=None, status="pending",
            lead=None, organization_id=None):
    """One pending AutoSendItem plus the lead it points at."""
    if lead is None:
        lead = _lead(db_session, org, advisor, phone=phone)
    item = AutoSendItem(
        id=str(uuid.uuid4()),
        organization_id=organization_id or org.id,
        lead_id=lead.id,
        advisor_id=advisor.id,
        message=message,
        channel=channel,
        subject=subject,
        source="ai",
        source_ref=f"reply-{uuid.uuid4()}",
        ai_reason="Simple scheduling question.",
        status=status,
        created_at=datetime.utcnow(),
    )
    db_session.add(item)
    db_session.commit()
    return lead, item


def _twilio():
    """A stand-in Twilio client whose .messages.create records its kwargs.

    error_code and error_message are pinned to real None values rather than
    left as auto-created MagicMocks: send_sms writes both straight onto the
    messages row, and a MagicMock is not a bindable SQLite parameter."""
    client = MagicMock()
    client.messages.create.return_value = MagicMock(
        sid="SM_test", status="queued", error_code=None, error_message=None,
    )
    return client


# ═════════════════════════════════════════════════════════════════════════════
# Authentication - the outermost gate. An unauthenticated caller can neither
# read a queue nor cause a send.
# ═════════════════════════════════════════════════════════════════════════════

def test_queue_requires_auth(client):
    assert client.get("/auto-send/queue").status_code == 401


def test_history_requires_auth(client):
    assert client.get("/auto-send/history").status_code == 401


def test_approve_requires_auth_and_sends_nothing(client, db_session, sample_org, sample_advisor):
    _, item = _queued(db_session, sample_org, sample_advisor)

    with patch("app.services.sms_service._resolve_twilio_creds") as creds:
        response = client.post(f"/auto-send/{item.id}/approve")
        creds.assert_not_called()

    assert response.status_code == 401
    db_session.refresh(item)
    assert item.status == "pending"


# ═════════════════════════════════════════════════════════════════════════════
# Listing and scoping
# ═════════════════════════════════════════════════════════════════════════════

def test_queue_returns_only_pending_items(client, db_session, sample_org, sample_advisor, auth_headers):
    _, pending = _queued(db_session, sample_org, sample_advisor)
    _, actioned = _queued(db_session, sample_org, sample_advisor,
                          phone="12145559801", status="sent")

    response = client.get("/auto-send/queue", headers=auth_headers)

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()]
    assert pending.id in ids
    assert actioned.id not in ids


def test_queue_scoped_to_calling_advisor_only(client, db_session, sample_org,
                                              sample_advisor, second_advisor, auth_headers):
    """A plain advisor sees their own queue and nobody else's."""
    _, mine = _queued(db_session, sample_org, sample_advisor)
    _, theirs = _queued(db_session, sample_org, second_advisor, phone="12145559802")

    ids = [row["id"] for row in client.get("/auto-send/queue", headers=auth_headers).json()]

    assert mine.id in ids
    assert theirs.id not in ids


def test_queue_scoped_to_organization(client, db_session, sample_org, sample_advisor, auth_headers):
    """Org isolation is a separate filter from advisor ownership, so it gets its
    own test: a row carrying this advisor's id but another organization's id
    must not appear."""
    other_org = Organization(name="Someone Else Memorial", slug="other-org", plan="standard")
    db_session.add(other_org)
    db_session.commit()

    _, mine = _queued(db_session, sample_org, sample_advisor)
    _, foreign = _queued(db_session, sample_org, sample_advisor, phone="12145559803",
                         organization_id=other_org.id)

    ids = [row["id"] for row in client.get("/auto-send/queue", headers=auth_headers).json()]

    assert mine.id in ids
    assert foreign.id not in ids


def test_queue_length_reflects_real_pending_items(client, db_session, sample_org,
                                                  sample_advisor, second_advisor, auth_headers):
    """Replaces the old GET /auto-send/queue/counts assertion.

    That endpoint does not exist on the current router and none was invented
    for this test. The fact the old assertion protected - that the number the
    advisor sees waiting is exactly their own pending rows - is pinned here
    against the endpoint that does exist."""
    _queued(db_session, sample_org, sample_advisor, phone="12145559804")
    _queued(db_session, sample_org, sample_advisor, phone="12145559805")
    _queued(db_session, sample_org, sample_advisor, phone="12145559806", status="skipped")
    _queued(db_session, sample_org, second_advisor, phone="12145559807")

    assert len(client.get("/auto-send/queue", headers=auth_headers).json()) == 2


# ═════════════════════════════════════════════════════════════════════════════
# Approve - the only path that sends. Was POST /queue/{id}/confirm.
# ═════════════════════════════════════════════════════════════════════════════

def test_approve_sends_the_exact_queued_message(client, db_session, sample_org,
                                                sample_advisor, auth_headers):
    _, item = _queued(db_session, sample_org, sample_advisor, message="See you Tuesday at 2pm!")
    twilio = _twilio()

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(twilio, "+12145551111", None)):
        response = client.post(f"/auto-send/{item.id}/approve", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"status": "sent", "item_id": item.id}
    twilio.messages.create.assert_called_once()
    # Sent verbatim. enforce_sms_content_policy appends the required opt-out
    # sentence (once) to every outbound body, so the queued draft is the START
    # of what goes out, not the whole of it - nothing rewrites the draft itself.
    body = twilio.messages.create.call_args.kwargs["body"]
    assert body.startswith("See you Tuesday at 2pm!")
    assert "Reply STOP" in body

    db_session.refresh(item)
    assert item.status == "sent"
    assert item.actioned_by_id == sample_advisor.id
    assert item.actioned_at is not None


def test_approve_404s_for_an_already_actioned_item(client, db_session, sample_org,
                                                   sample_advisor, auth_headers):
    """Was a 400 on the old API. The current endpoint filters on
    status == "pending" inside the lookup, so a resolved row is not found."""
    _, item = _queued(db_session, sample_org, sample_advisor, status="skipped")
    twilio = _twilio()

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(twilio, "+12145551111", None)):
        response = client.post(f"/auto-send/{item.id}/approve", headers=auth_headers)

    assert response.status_code == 404
    twilio.messages.create.assert_not_called()


def test_approve_404s_for_another_advisors_item_and_sends_nothing(
        client, db_session, sample_org, second_advisor, auth_headers):
    """One advisor must not be able to send from another advisor's queue -
    it would go out under the wrong Twilio identity to a family that is
    not theirs."""
    _, item = _queued(db_session, sample_org, second_advisor, phone="12145559808")
    twilio = _twilio()

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(twilio, "+12145551111", None)):
        response = client.post(f"/auto-send/{item.id}/approve", headers=auth_headers)

    assert response.status_code == 404
    twilio.messages.create.assert_not_called()
    db_session.refresh(item)
    assert item.status == "pending"


def test_approve_does_not_send_to_a_dnc_lead(client, db_session, sample_org,
                                             sample_advisor, auth_headers):
    """The real send path's DNC check is genuinely wired in, not bypassed by
    this queue. send_sms raises, approve_item records the reason and marks the
    item failed - the carrier is never called."""
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145559809", status="dnc")
    _, item = _queued(db_session, sample_org, sample_advisor, lead=lead)
    twilio = _twilio()

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(twilio, "+12145551111", None)):
        response = client.post(f"/auto-send/{item.id}/approve", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    twilio.messages.create.assert_not_called()

    db_session.refresh(item)
    assert item.status == "failed"
    assert "DNC" in (item.ai_reason or "")


def test_approve_respects_the_suppression_list(client, db_session, sample_org,
                                               sample_advisor, auth_headers):
    """A number can be suppressed while its lead's status was never updated to
    DNC. That independent guard must also survive this queue."""
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145559810", status="new")
    db_session.add(SuppressionEntry(organization_id=sample_org.id, phone="12145559810",
                                    reason="Manually suppressed"))
    db_session.commit()
    _, item = _queued(db_session, sample_org, sample_advisor, lead=lead)
    twilio = _twilio()

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(twilio, "+12145551111", None)):
        response = client.post(f"/auto-send/{item.id}/approve", headers=auth_headers)

    assert response.json()["status"] == "failed"
    twilio.messages.create.assert_not_called()

    db_session.refresh(item)
    assert item.status == "failed"
    assert "suppression" in (item.ai_reason or "")


@pytest.mark.xfail(
    strict=True,
    reason="PRODUCTION DEFECT, not a stale test: approve_item calls "
           "send_email(db=..., lead=..., advisor=..., subject=..., body=...) but "
           "email_service.send_email's signature is (db, org_id, to_email, "
           "to_name, subject, body). The TypeError is caught by approve_item's "
           "own except, so an approved EMAIL item is silently marked 'failed' "
           "and never sends. Remove this marker when the call is corrected.",
)
def test_approve_sends_an_email_channel_item(client, db_session, sample_org,
                                             sample_advisor, auth_headers):
    lead = _lead(db_session, sample_org, sample_advisor, phone=None,
                 email="family@example.com")
    _, item = _queued(db_session, sample_org, sample_advisor, lead=lead,
                      channel="email", subject="Following up")

    with patch("app.services.email_service.send_email_via_provider",
               return_value={"success": True, "provider_message_id": "em_1", "error": None}):
        response = client.post(f"/auto-send/{item.id}/approve", headers=auth_headers)

    assert response.json()["status"] == "sent"


# ═════════════════════════════════════════════════════════════════════════════
# Edit - was POST /queue/{id}/edit-and-send. Editing no longer sends.
# ═════════════════════════════════════════════════════════════════════════════

def test_edit_rewrites_the_body_without_sending(client, db_session, sample_org,
                                                sample_advisor, auth_headers):
    _, item = _queued(db_session, sample_org, sample_advisor, message="Original AI draft.")
    twilio = _twilio()

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(twilio, "+12145551111", None)):
        response = client.patch(f"/auto-send/{item.id}/edit",
                                json={"message": "My edited version."},
                                headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["message"] == "My edited version."
    twilio.messages.create.assert_not_called()

    db_session.refresh(item)
    assert item.message == "My edited version."
    assert item.status == "pending"


def test_edit_then_approve_sends_the_edited_body_not_the_original(
        client, db_session, sample_org, sample_advisor, auth_headers):
    _, item = _queued(db_session, sample_org, sample_advisor, message="Original AI draft.")
    twilio = _twilio()

    client.patch(f"/auto-send/{item.id}/edit",
                 json={"message": "My edited version."}, headers=auth_headers)

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(twilio, "+12145551111", None)):
        client.post(f"/auto-send/{item.id}/approve", headers=auth_headers)

    assert twilio.messages.create.call_args.kwargs["body"].startswith("My edited version.")


def test_edit_rejects_an_empty_body(client, db_session, sample_org, sample_advisor, auth_headers):
    _, item = _queued(db_session, sample_org, sample_advisor, message="Original AI draft.")

    response = client.patch(f"/auto-send/{item.id}/edit",
                            json={"message": "   "}, headers=auth_headers)

    assert response.status_code == 400
    db_session.refresh(item)
    assert item.message == "Original AI draft."


def test_edit_404s_for_another_advisors_item(client, db_session, sample_org,
                                             second_advisor, auth_headers):
    """Rewriting a colleague's pending message would let them approve and send
    text they never wrote, under their own name."""
    _, item = _queued(db_session, sample_org, second_advisor,
                      message="Their draft.", phone="12145559811")

    response = client.patch(f"/auto-send/{item.id}/edit",
                            json={"message": "Injected text."}, headers=auth_headers)

    assert response.status_code == 404
    db_session.refresh(item)
    assert item.message == "Their draft."


# ═════════════════════════════════════════════════════════════════════════════
# Skip - was POST /queue/{id}/override. Declines the draft, sends nothing.
# ═════════════════════════════════════════════════════════════════════════════

def test_skip_sends_nothing_at_all(client, db_session, sample_org, sample_advisor, auth_headers):
    _, item = _queued(db_session, sample_org, sample_advisor)

    with patch("app.services.sms_service._resolve_twilio_creds") as creds:
        response = client.post(f"/auto-send/{item.id}/skip", headers=auth_headers)
        creds.assert_not_called()

    assert response.status_code == 200
    assert response.json() == {"status": "skipped", "item_id": item.id}

    db_session.refresh(item)
    assert item.status == "skipped"
    assert item.actioned_by_id == sample_advisor.id


def test_skip_404s_for_another_advisors_item(client, db_session, sample_org,
                                             second_advisor, auth_headers):
    """Quieter than sending somebody else's message and just as wrong: the
    follow-up they were relying on never goes out and nothing tells them."""
    _, item = _queued(db_session, sample_org, second_advisor, phone="12145559812")

    response = client.post(f"/auto-send/{item.id}/skip", headers=auth_headers)

    assert response.status_code == 404
    db_session.refresh(item)
    assert item.status == "pending"


# ═════════════════════════════════════════════════════════════════════════════
# Approve-all - the bulk action, which must stay inside the caller's own queue
# ═════════════════════════════════════════════════════════════════════════════

def test_approve_all_only_touches_the_callers_own_pending_items(
        client, db_session, sample_org, sample_advisor, second_advisor, auth_headers):
    _, mine = _queued(db_session, sample_org, sample_advisor, phone="12145559813")
    _, theirs = _queued(db_session, sample_org, second_advisor, phone="12145559814")
    twilio = _twilio()

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(twilio, "+12145551111", None)):
        response = client.post("/auto-send/approve-all", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert twilio.messages.create.call_count == 1

    db_session.refresh(mine)
    db_session.refresh(theirs)
    assert mine.status == "sent"
    assert theirs.status == "pending"


# ═════════════════════════════════════════════════════════════════════════════
# History
# ═════════════════════════════════════════════════════════════════════════════

def test_history_shows_actioned_items_not_pending_ones(client, db_session, sample_org,
                                                       sample_advisor, auth_headers):
    _, pending = _queued(db_session, sample_org, sample_advisor, phone="12145559815")
    _, resolved = _queued(db_session, sample_org, sample_advisor, phone="12145559816")

    client.post(f"/auto-send/{resolved.id}/skip", headers=auth_headers)

    ids = [row["id"] for row in client.get("/auto-send/history", headers=auth_headers).json()]

    assert resolved.id in ids
    assert pending.id not in ids
