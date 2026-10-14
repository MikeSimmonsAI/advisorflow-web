"""Program emails carry an open-tracking image for their own row; the saved
body does not, and loading the image marks that row opened."""
from app.models.models import EmailMessage
from tests.test_outreach_program import _fake_provider, _touch_ready, program  # noqa: F401 (fixture)


def test_program_email_carries_its_own_open_pixel(program, sample_advisor, monkeypatch, client):
    monkeypatch.setenv("PROGRAM_ASSET_BASE_URL", "https://api.example.test")
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    sent = []
    with _fake_provider(sent):
        et.run(db, org.id, force_hours=True)
    em = db.query(EmailMessage).filter_by(lead_id=lead.id).one()
    assert "/email-tracking/open/%s" % em.id in sent[0]["body_html"]
    assert "email-tracking" not in (em.body_html or "")
    assert client.get("/email-tracking/open/%s" % em.id).status_code == 200
    db.refresh(em)
    assert em.opened_at is not None
