"""Two contacts, one inbox: the first to email sends ONE unaddressed email
("Hi there", no name); the other never emails that inbox. Texts unaffected."""
from app.models.models import EmailMessage, Lead
from tests.test_outreach_program import _fake_provider, _touch_ready, program  # noqa: F401 (fixture)


def test_shared_inbox_gets_one_unaddressed_email(program, sample_advisor, monkeypatch):
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch, email="steve@example.com")
    other = Lead(organization_id=org.id, assigned_to_id=sample_advisor.id, first_name="Janet", last_name="Gulley",
                 phone="12145550199", email="Steve@Example.com ", status="sent")
    db.add(other); db.commit()
    sent = []
    with _fake_provider(sent):
        r = et.run(db, org.id, force_hours=True)
    assert r["sent"] == 1 and len(sent) == 1
    body = sent[0]["body_html"]
    assert "Ollie" not in body and "Janet" not in body and "there" in body
    assert "Ollie" not in sent[0]["subject"]
    # The other contact may not email that inbox now.
    assert "shares an email address" in et._skip_reason(db, other, False, None)


def test_a_private_inbox_still_gets_the_name(program, sample_advisor, monkeypatch):
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    sent = []
    with _fake_provider(sent):
        et.run(db, org.id, force_hours=True)
    assert len(sent) == 1 and "Ollie" in sent[0]["body_html"]
