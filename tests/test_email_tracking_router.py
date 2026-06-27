"""
Tests for app/routers/email_tracking_router.py - the actual
unauthenticated open-pixel and click-redirect endpoints. These are hit
directly by a recipient's email client/browser, which has no
AdvisorFlow login at all, so every test here deliberately calls the
endpoint with NO auth headers - that's the real, correct usage.
"""

from app.models.models import EmailMessage


def _email_message(db_session, sample_org, sample_advisor, sample_lead):
    msg = EmailMessage(
        lead_id=sample_lead.id, sender_id=sample_advisor.id,
        subject="Test subject", body_html="<p>Test body</p>", status="sent",
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)
    return msg


# ---------------------------------------------------------------------------
# Open tracking pixel
# ---------------------------------------------------------------------------

def test_open_pixel_requires_no_auth_and_returns_an_image(client, db_session, sample_org, sample_advisor, sample_lead):
    msg = _email_message(db_session, sample_org, sample_advisor, sample_lead)

    response = client.get(f"/email-tracking/open/{msg.id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/gif"


def test_open_pixel_sets_opened_at_on_first_load(client, db_session, sample_org, sample_advisor, sample_lead):
    msg = _email_message(db_session, sample_org, sample_advisor, sample_lead)
    assert msg.opened_at is None

    client.get(f"/email-tracking/open/{msg.id}")

    db_session.refresh(msg)
    assert msg.opened_at is not None


def test_open_pixel_does_not_overwrite_opened_at_on_repeat_loads(client, db_session, sample_org, sample_advisor, sample_lead):
    """opened_at should reflect the FIRST open, not the most recent - an email client may re-fetch images on every view."""
    msg = _email_message(db_session, sample_org, sample_advisor, sample_lead)

    client.get(f"/email-tracking/open/{msg.id}")
    db_session.refresh(msg)
    first_opened_at = msg.opened_at

    client.get(f"/email-tracking/open/{msg.id}")
    db_session.refresh(msg)

    assert msg.opened_at == first_opened_at


def test_open_pixel_with_unknown_id_still_returns_a_valid_image_not_an_error(client, db_session):
    """A missing/invalid email_message_id must never surface as a broken image or error to whoever is viewing the email."""
    response = client.get("/email-tracking/open/not-a-real-id")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/gif"


# ---------------------------------------------------------------------------
# Click tracking redirect
# ---------------------------------------------------------------------------

def test_click_redirect_requires_no_auth_and_redirects_to_real_url(client, db_session, sample_org, sample_advisor, sample_lead):
    msg = _email_message(db_session, sample_org, sample_advisor, sample_lead)

    response = client.get(f"/email-tracking/click/{msg.id}?url=https://example.com/real-page", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/real-page"


def test_click_redirect_increments_click_count(client, db_session, sample_org, sample_advisor, sample_lead):
    msg = _email_message(db_session, sample_org, sample_advisor, sample_lead)
    assert msg.click_count in (0, None)

    client.get(f"/email-tracking/click/{msg.id}?url=https://example.com/page", follow_redirects=False)

    db_session.refresh(msg)
    assert msg.click_count == 1


def test_click_redirect_counts_repeat_clicks_unlike_opens(client, db_session, sample_org, sample_advisor, sample_lead):
    """Unlike opens, repeat clicks ARE meaningful engagement and should all count."""
    msg = _email_message(db_session, sample_org, sample_advisor, sample_lead)

    client.get(f"/email-tracking/click/{msg.id}?url=https://example.com/a", follow_redirects=False)
    client.get(f"/email-tracking/click/{msg.id}?url=https://example.com/b", follow_redirects=False)
    client.get(f"/email-tracking/click/{msg.id}?url=https://example.com/c", follow_redirects=False)

    db_session.refresh(msg)
    assert msg.click_count == 3


def test_click_redirect_updates_last_clicked_at(client, db_session, sample_org, sample_advisor, sample_lead):
    msg = _email_message(db_session, sample_org, sample_advisor, sample_lead)
    assert msg.last_clicked_at is None

    client.get(f"/email-tracking/click/{msg.id}?url=https://example.com/page", follow_redirects=False)

    db_session.refresh(msg)
    assert msg.last_clicked_at is not None


def test_click_redirect_with_unknown_id_still_redirects_to_original_url(client, db_session):
    """A tracking-data issue on our side must never block the recipient from reaching the page they clicked toward."""
    response = client.get("/email-tracking/click/not-a-real-id?url=https://example.com/still-works", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/still-works"


def test_click_redirect_requires_url_query_param(client, db_session, sample_org, sample_advisor, sample_lead):
    msg = _email_message(db_session, sample_org, sample_advisor, sample_lead)

    response = client.get(f"/email-tracking/click/{msg.id}", follow_redirects=False)

    assert response.status_code == 422
