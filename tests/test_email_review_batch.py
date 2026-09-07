"""THE EMAIL REVIEW PAIR — /email/preview-batch and /email/confirm-send-batch.

WHY THIS FILE EXISTS

Review-before-send is one feature with two halves per channel: draft it, show a
human, send what the human approved. SMS has kept both halves
(/leads/preview-messages + /leads/confirm-send-batch). The EMAIL halves were
deleted outright by the 2026-07 bulk-overwrite window and nobody noticed,
because nothing failed when they went: the queue screen simply stopped offering
review and advisors sent unreviewed mail to grieving families.

They have now been restored on current architecture — authorized_lead_query for
scope, the qualification engine for permission — and this file is the thing
that has to notice if they ever disappear again. So it tests three separate
things, and the third is the reason the other two are worth writing:

  1. THE ENDPOINTS DO THEIR JOB. Preview drafts and writes nothing; confirm
     sends the advisor's edited text and reports blocked separately from
     skipped.
  2. THE TWO ENDPOINTS APPLY DIFFERENT QUALIFICATION RULES ON PURPOSE.
     /send-batch is READY_TO_SEND only because nobody looked at those leads
     individually. /confirm-send-batch refuses EXCLUDED but lets
     REVIEW_REQUIRED through, because a human just read every one of them —
     that IS the review the bucket asks for. Test 8 below is that difference.
  3. SCOPE. The deleted implementation filtered on
     `current_user.organization_id` by hand: no workspace resolution, no
     manager visibility. The restoration goes through authorized_lead_query,
     and these tests ask for another tenant's lead and another advisor's lead
     by id to prove it.

NOTHING HERE TALKS TO AN EMAIL PROVIDER. `no_real_email_sends` below refuses
the provider call at the source, the same way conftest's `no_real_twilio_calls`
refuses a real Twilio client; a test that means to exercise sending patches
`app.services.email_service.send_email_via_provider` deliberately.
"""

import os
from unittest.mock import patch

import pytest

from app.models.models import (BookingLink, EmailMessage, Lead, LeadStatus,
                               LeadTier, MessageTrack, Organization, User)
from app.services import qualification
from app.services.auth_service import create_access_token, hash_password


PREVIEW = "/email/preview-batch"
CONFIRM = "/email/confirm-send-batch"


@pytest.fixture(autouse=True)
def no_real_email_sends(monkeypatch):
    """THIS FILE MUST NEVER REACH RESEND OR MICROSOFT GRAPH.

    The same failure mode conftest guards for Twilio applies here and is worse:
    a send path whose mock has drifted does not fail, it delivers. Both provider
    entry points are refused by name, so a test that reaches one is told which
    patch target it is missing instead of quietly mailing somebody.
    """
    def _refuse(*args, **kwargs):
        raise RuntimeError(
            "A test tried to send real email. Patch "
            "app.services.email_service.send_email_via_provider (which is what "
            "/email/confirm-send-batch actually calls) rather than sending.")

    import app.services.email_service as email_service
    monkeypatch.setattr(email_service, "send_email_via_provider", _refuse)
    try:
        import app.services.microsoft_email_service as ms
        monkeypatch.setattr(ms, "send_email_via_microsoft_graph", _refuse)
    except Exception:
        pass


def _provider_ok(message_id="em_test_provider_id"):
    return {"success": True, "provider_message_id": message_id, "error": None}


def _lead(db_session, org_id, advisor_id, **overrides):
    """A normal, sendable, email-routed lead unless a test says otherwise."""
    fields = dict(
        first_name="Jane", last_name="Doe", email="jane.doe@example.com",
        phone="12145559999", contact_channel="email_only",
        tier=LeadTier.PRE_NEED, message_track=MessageTrack.EMAIL_ONLY_NURTURE,
        status=LeadStatus.NEW,
    )
    fields.update(overrides)
    lead = Lead(organization_id=org_id, assigned_to_id=advisor_id, **fields)
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)
    return lead


def _other_org_lead(db_session, slug="other-email-review-org"):
    """A lead belonging to a DIFFERENT tenant, with its own advisor."""
    org = Organization(name="Other Memorial", slug=slug, plan="trial")
    db_session.add(org)
    db_session.commit()
    advisor = User(organization_id=org.id, email="other@othermemorial.com",
                   password_hash=hash_password("TestPass123!"),
                   full_name="Other Advisor", role="advisor",
                   must_change_password=False)
    db_session.add(advisor)
    db_session.commit()
    return _lead(db_session, org.id, advisor.id, first_name="Foreign",
                 last_name="Lead", email="foreign@example.com")


# ═════════════════════════════════════════════════════════════════════════════
# 1-4. PREVIEW: it drafts, it explains, and it writes nothing
# ═════════════════════════════════════════════════════════════════════════════

def test_preview_batch_drafts_a_real_subject_and_body(
        client, auth_headers, db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org.id, sample_advisor.id)

    response = client.post(PREVIEW, json={"lead_ids": [lead.id]},
                           headers=auth_headers)

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["lead_id"] == lead.id
    assert row["skip_reason"] is None
    assert row["draft_subject"] != ""
    assert row["draft_body_html"] != ""
    # The lead's own details are substituted in - a preview of a template with
    # the merge fields still in it tells the advisor nothing about what a
    # family will actually read.
    assert "Jane" in row["draft_subject"] or "Jane" in row["draft_body_html"]
    assert row["email"] == "jane.doe@example.com"


def test_preview_batch_persists_nothing_at_all(
        client, auth_headers, db_session, sample_org, sample_advisor):
    """THE POINT OF A PREVIEW. No message row, no booking link, no status change.

    A booking link minted at preview time would leave a dead link behind every
    time an advisor edited or dropped a draft, and an EmailMessage row would
    make the sent log lie about what left the building.
    """
    lead = _lead(db_session, sample_org.id, sample_advisor.id)

    response = client.post(PREVIEW, json={"lead_ids": [lead.id]},
                           headers=auth_headers)
    assert response.status_code == 200

    assert db_session.query(EmailMessage).filter(
        EmailMessage.lead_id == lead.id).count() == 0
    assert db_session.query(BookingLink).filter(
        BookingLink.lead_id == lead.id).count() == 0
    db_session.refresh(lead)
    assert lead.status == LeadStatus.NEW
    assert lead.last_messaged_at is None


def test_preview_batch_skips_a_lead_with_no_email_address(
        client, auth_headers, db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org.id, sample_advisor.id,
                 first_name="No", last_name="Address", email=None)

    row = client.post(PREVIEW, json={"lead_ids": [lead.id]},
                      headers=auth_headers).json()[0]

    assert row["skip_reason"] is not None
    assert "email" in row["skip_reason"].lower()
    assert row["draft_subject"] == ""
    assert row["draft_body_html"] == ""


def test_preview_batch_skips_an_excluded_lead_with_the_reason(
        client, auth_headers, db_session, sample_org, sample_advisor):
    """A DNC lead gets a REASON, not a draft. The advisor must not be handed a
    message they can edit for somebody who asked never to be contacted."""
    lead = _lead(db_session, sample_org.id, sample_advisor.id,
                 first_name="Do", last_name="NotContact",
                 email="dnc@example.com", status=LeadStatus.DNC)

    row = client.post(PREVIEW, json={"lead_ids": [lead.id]},
                      headers=auth_headers).json()[0]

    assert row["skip_reason"] is not None
    assert "do not contact" in row["skip_reason"].lower()
    assert row["draft_subject"] == ""
    assert row["draft_body_html"] == ""


def test_preview_batch_flags_a_review_lead_but_still_drafts_it(
        client, auth_headers, db_session, sample_org, sample_advisor):
    """FLAGGED IS NOT SKIPPED — the load-bearing half of REVIEW_REQUIRED.

    The bucket exists so "we are not sure" is something a person can act on. An
    advisor who is shown the reasons AND the draft can decide; one who is shown
    a skip has had the decision made for them by a machine.
    """
    lead = _lead(db_session, sample_org.id, sample_advisor.id,
                 first_name="Needs", last_name="Review",
                 email="needs.review@example.com",
                 status=LeadStatus.NEEDS_TIER_REVIEW)

    row = client.post(PREVIEW, json={"lead_ids": [lead.id]},
                      headers=auth_headers).json()[0]

    assert row["skip_reason"] is None
    assert row["draft_subject"] != ""
    assert row["draft_body_html"] != ""
    assert row["review_reasons"], "a REVIEW lead must surface its reasons"
    # The engine's own {"code","label"} shape, passed through unflattened.
    codes = {r["code"] for r in row["review_reasons"]}
    assert "needs_classification" in codes
    assert all(r.get("label") for r in row["review_reasons"])


# ═════════════════════════════════════════════════════════════════════════════
# 5-8. CONFIRM: it sends what the human approved, and says what it did not send
# ═════════════════════════════════════════════════════════════════════════════

@patch("app.services.email_service.send_email_via_provider")
def test_confirm_send_batch_sends_the_advisors_edited_text(
        mock_send, client, auth_headers, db_session, sample_org, sample_advisor):
    """THE WHOLE REASON THE FLOW EXISTS. If confirm re-rendered the template,
    every edit an advisor made in the review screen would be silently thrown
    away and the preview would be theatre."""
    mock_send.return_value = _provider_ok()
    lead = _lead(db_session, sample_org.id, sample_advisor.id)

    edited_subject = "Following up personally, Jane"
    edited_body = "<p>Jane, I hand-typed this myself after reading the draft.</p>"

    response = client.post(CONFIRM, json={"items": [
        {"lead_id": lead.id, "subject": edited_subject,
         "body_html": edited_body},
    ]}, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["sent_count"] == 1

    # What the provider was handed.
    assert mock_send.call_count == 1
    args, kwargs = mock_send.call_args
    sent_to, sent_subject, sent_body = args[0], args[1], args[2]
    assert sent_to == lead.email
    assert sent_subject == edited_subject
    assert "hand-typed this myself" in sent_body

    # What was recorded. The stored row keeps the advisor's text, not the
    # tracking-injected copy handed to the provider.
    msg = db_session.query(EmailMessage).filter(
        EmailMessage.lead_id == lead.id).one()
    assert msg.subject == edited_subject
    assert "hand-typed this myself" in msg.body_html
    assert msg.status == "sent"
    # The message is FROM the lead's advisor, whoever pressed send.
    assert msg.sender_id == sample_advisor.id


@patch("app.services.email_service.send_email_via_provider")
def test_confirm_send_batch_reports_blocked_separately_from_skipped(
        mock_send, client, auth_headers, db_session, sample_org, sample_advisor):
    """A COMPLIANCE REFUSAL IS NOT A MISSING FIELD.

    One count of "not sent" would hide a DNC refusal behind an empty address
    column, and the advisor reviewing the result would have no idea which of
    the two happened to whom.
    """
    mock_send.return_value = _provider_ok()
    sendable = _lead(db_session, sample_org.id, sample_advisor.id,
                     email="sendable@example.com")
    blocked_lead = _lead(db_session, sample_org.id, sample_advisor.id,
                         first_name="Blocked", email="blocked@example.com",
                         status=LeadStatus.DNC)
    no_address = _lead(db_session, sample_org.id, sample_advisor.id,
                       first_name="NoAddress", email=None)

    body = {"items": [
        {"lead_id": sendable.id, "subject": "S", "body_html": "<p>B</p>"},
        {"lead_id": blocked_lead.id, "subject": "S", "body_html": "<p>B</p>"},
        {"lead_id": no_address.id, "subject": "S", "body_html": "<p>B</p>"},
    ]}
    result = client.post(CONFIRM, json=body, headers=auth_headers).json()

    assert result["sent_count"] == 1
    assert result["sent_ids"] == [sendable.id]

    assert result["blocked_count"] == 1
    assert result["blocked"][0]["lead_id"] == blocked_lead.id
    assert {r["code"] for r in result["blocked"][0]["reasons"]} == {"dnc"}

    assert result["skipped_count"] == 1
    assert result["skipped"][0]["lead_id"] == no_address.id
    assert result["skipped"][0]["reason"] == "no_email_address"

    # NOT all-or-nothing: one refusal must not discard the message a human
    # already read and approved for somebody else.
    assert mock_send.call_count == 1


@patch("app.services.email_service.send_email_via_provider")
def test_confirm_send_batch_refuses_an_excluded_lead_a_human_reviewed(
        mock_send, client, auth_headers, db_session, sample_org, sample_advisor):
    """A human reviewing a message is the review REVIEW_REQUIRED asks for. It is
    NOT permission to mail somebody who asked not to be contacted."""
    mock_send.return_value = _provider_ok()
    lead = _lead(db_session, sample_org.id, sample_advisor.id,
                 email="stop@example.com", status=LeadStatus.DNC)

    result = client.post(CONFIRM, json={"items": [
        {"lead_id": lead.id, "subject": "A human typed this",
         "body_html": "<p>and it still must not go</p>"},
    ]}, headers=auth_headers).json()

    assert result["sent_count"] == 0
    assert result["blocked_count"] == 1
    assert result["blocked"][0]["lead_id"] == lead.id
    mock_send.assert_not_called()
    assert db_session.query(EmailMessage).filter(
        EmailMessage.lead_id == lead.id).count() == 0
    db_session.refresh(lead)
    assert lead.status == LeadStatus.DNC


@patch("app.services.email_service.send_email_via_provider")
def test_confirm_send_batch_allows_a_review_bucket_lead_through(
        mock_send, client, auth_headers, db_session, sample_org, sample_advisor):
    """THE LOAD-BEARING DIFFERENCE FROM /send-batch, asserted directly.

    /send-batch refuses REVIEW_REQUIRED because nobody looked at those leads
    one at a time. This endpoint is the path where somebody DID. If the bulk
    rule were applied here the review flow would be strictly less capable than
    sending one lead at a time, and advisors would go back to the unreviewed
    path - which is the outcome the whole feature exists to prevent.

    The assertion is written against the engine's own answer rather than
    against a hand-picked lead, so it keeps meaning "REVIEW_REQUIRED gets
    through" even if the reasons that produce that bucket change.
    """
    mock_send.return_value = _provider_ok()
    lead = _lead(db_session, sample_org.id, sample_advisor.id,
                 first_name="Needs", last_name="Review",
                 email="needs.review@example.com",
                 status=LeadStatus.NEEDS_TIER_REVIEW)

    ctx = qualification.QualificationContext(
        db_session, [lead], sample_org.id,
        qualification.org_rules(db_session, sample_org.id))
    decision = qualification.qualify_one(lead, qualification.CHANNEL_EMAIL, ctx)
    assert decision["bucket"] == qualification.REVIEW, (
        "this fixture is meant to be a REVIEW_REQUIRED lead; if it is not, the "
        "test below proves nothing about the review bucket")

    result = client.post(CONFIRM, json={"items": [
        {"lead_id": lead.id, "subject": "Reviewed and approved by a person",
         "body_html": "<p>Reviewed and approved by a person</p>"},
    ]}, headers=auth_headers).json()

    assert result["sent_count"] == 1, (
        "a REVIEW_REQUIRED lead must send through the confirm path - a human "
        "just read this message")
    assert result["blocked_count"] == 0
    assert mock_send.call_count == 1
    db_session.refresh(lead)
    assert lead.status == "sent"


# ═════════════════════════════════════════════════════════════════════════════
# 9. AUTH
# ═════════════════════════════════════════════════════════════════════════════

def test_both_endpoints_require_authentication(client):
    assert client.post(PREVIEW, json={"lead_ids": ["x"]}).status_code == 401
    assert client.post(CONFIRM, json={"items": [
        {"lead_id": "x", "subject": "s", "body_html": "b"}]}).status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# 10-13. SCOPE — the part the deleted implementation got wrong
#
# The old code filtered `Lead.organization_id == current_user.organization_id`
# by hand under Depends(get_current_user). That is one filter doing three jobs
# badly: no workspace resolution, no manager visibility, and no engine. These
# four tests ask for leads the caller should and should not see, by id.
# ═════════════════════════════════════════════════════════════════════════════

def test_preview_batch_silently_omits_another_organizations_lead(
        client, auth_headers, db_session):
    """Silently omitted, not reported as skipped. A skip_reason for an id the
    caller may not see would confirm that the record exists - an enumeration
    oracle over another tenant's book."""
    foreign = _other_org_lead(db_session)

    response = client.post(PREVIEW, json={"lead_ids": [foreign.id]},
                           headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == []


@patch("app.services.email_service.send_email_via_provider")
def test_confirm_send_batch_will_not_mail_another_organizations_lead(
        mock_send, client, auth_headers, db_session):
    mock_send.return_value = _provider_ok()
    foreign = _other_org_lead(db_session)

    result = client.post(CONFIRM, json={"items": [
        {"lead_id": foreign.id, "subject": "Not yours",
         "body_html": "<p>Not yours</p>"},
    ]}, headers=auth_headers).json()

    assert result["sent_count"] == 0
    assert result["skipped_count"] == 1
    assert result["skipped"][0]["reason"] == "not_found"
    mock_send.assert_not_called()
    assert db_session.query(EmailMessage).filter(
        EmailMessage.lead_id == foreign.id).count() == 0


def test_preview_batch_hides_another_advisors_lead_from_a_plain_advisor(
        client, auth_headers, db_session, sample_org, sample_advisor,
        second_advisor):
    """Owner-scoped visibility, via authorized_lead_query. Same organization,
    somebody else's family."""
    mine = _lead(db_session, sample_org.id, sample_advisor.id,
                 first_name="Mine", email="mine@example.com")
    theirs = _lead(db_session, sample_org.id, second_advisor.id,
                   first_name="Theirs", email="theirs@example.com")

    rows = client.post(PREVIEW, json={"lead_ids": [mine.id, theirs.id]},
                       headers=auth_headers).json()

    assert [r["lead_id"] for r in rows] == [mine.id]


def test_preview_batch_shows_a_manager_another_advisors_lead(
        client, admin_auth_headers, db_session, sample_org, sample_advisor):
    """THE PROOF THAT THIS IS THE CURRENT SCOPE AND NOT THE OLD RULE.

    The deleted implementation was assignee-agnostic inside one hardcoded
    organization; the current one asks authorized_lead_query, under which an
    org_admin sees the whole workspace. A manager who cannot preview their own
    team's outreach cannot supervise it, which is most of the job.
    """
    lead = _lead(db_session, sample_org.id, sample_advisor.id,
                 first_name="Teamie", email="teamie@example.com")

    rows = client.post(PREVIEW, json={"lead_ids": [lead.id]},
                       headers=admin_auth_headers).json()

    assert [r["lead_id"] for r in rows] == [lead.id]
    assert rows[0]["draft_subject"] != ""


# ═════════════════════════════════════════════════════════════════════════════
# 14-15. THE PARITY GUARD — the reason this file exists at all
# ═════════════════════════════════════════════════════════════════════════════
#
# Review-before-send is ONE feature with a half per channel. The SMS half and
# the email half are deliberately symmetric, and the symmetry is the invariant:
# there is no product decision under which an advisor may read and edit a text
# before it reaches a grieving family but must send that family email blind.
#
# It has already been broken once, silently. The email pair was deleted by a
# bulk overwrite while the SMS pair survived, and nothing failed - which is
# precisely why it took months to notice.
#
# So the assertion is STRUCTURAL. The two halves are declared once, as steps of
# one flow with a route per channel, and the test compares the channels against
# each other. Hard-coding "these four paths exist" would let the next bulk
# overwrite delete the email endpoints and the four literals naming them and
# still be green; comparing SMS to email means the surviving half is what
# accuses the missing one.

REVIEW_FLOW = (
    # (the step, the SMS route that does it, the email route that does it)
    ("draft each message and show it to a person before anything sends",
     "/leads/preview-messages", "/email/preview-batch"),
    ("send exactly what that person read, edited and approved",
     "/leads/confirm-send-batch", "/email/confirm-send-batch"),
)

_WHY = """
REVIEW-BEFORE-SEND IS A DELIBERATELY SYMMETRIC PAIR, AND THE SYMMETRY IS BROKEN.

  {broken}

This is not a missing nicety. The email half of this pair was ALREADY deleted
once, by the 2026-07 bulk-overwrite window, while the SMS half survived
untouched - and nothing failed, so nobody noticed for months. Advisors could
review and edit a text message before it reached a grieving family, and had no
way to review the email that went to the same family.

If you are removing an endpoint on purpose, remove the channel's whole review
flow and this test with it, deliberately. If you are seeing this after a merge,
a rebase or a bulk file overwrite, the endpoint was deleted by accident: restore
it rather than deleting the assertion.

Registered POST routes matching the review flow:
{observed}
"""


def _registered_post_paths():
    """Every POST path the live FastAPI app actually serves."""
    from app.main import app
    return {r.path for r in app.routes
            if "POST" in (getattr(r, "methods", None) or set())}


def test_the_email_review_pair_survives_wherever_the_sms_pair_does():
    posts = _registered_post_paths()

    sms_steps = {step for step, sms, _ in REVIEW_FLOW if sms in posts}
    email_steps = {step for step, _, email in REVIEW_FLOW if email in posts}
    every_step = {step for step, _, _ in REVIEW_FLOW}

    observed = "\n".join(
        "  %-28s SMS %-28s %s   |   EMAIL %-24s %s"
        % (step[:28], sms, "present" if sms in posts else "MISSING",
           email, "present" if email in posts else "MISSING")
        for step, sms, email in REVIEW_FLOW)

    # THE SYMMETRY, both directions. Either channel losing a step accuses the
    # other, so this cannot be satisfied by deleting one half.
    if sms_steps != email_steps:
        broken = "\n  ".join(
            "SMS can %s but email cannot." % step
            for step in sorted(sms_steps - email_steps)
        ) or ""
        broken += "\n  ".join(
            "Email can %s but SMS cannot." % step
            for step in sorted(email_steps - sms_steps))
        pytest.fail(_WHY.format(broken=broken, observed=observed))

    # And the flow exists at all - symmetric absence is still the feature gone.
    assert sms_steps == every_step and email_steps == every_step, _WHY.format(
        broken="The review flow is missing on BOTH channels: %s"
               % ", ".join(sorted(every_step - sms_steps)),
        observed=observed)


def test_the_email_queue_still_mounts_the_review_screen():
    """THE UI ENTRY POINT, asserted against the source that produces the bundle.

    The endpoints can be perfectly alive and the feature still gone: if
    EmailQueue stops importing EmailReview there is no way into the flow from
    the screen advisors actually use, and the routes become dead code nobody
    can reach - which is how a restored endpoint quietly becomes an orphan.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "frontend", "src", "pages", "EmailQueue.jsx")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()

    assert "import EmailReview from" in source, (
        "EmailQueue.jsx no longer imports EmailReview. The review endpoints "
        "may still exist, but no advisor can reach them from the email queue.")
    assert "<EmailReview" in source, (
        "EmailQueue.jsx imports EmailReview but never renders it - the review "
        "step is unreachable from the queue screen.")
