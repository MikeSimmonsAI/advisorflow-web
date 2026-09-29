"""AI drafts write for the LEAD's business, with real context (owner, 2026-09-29).

An Atlantis Light & Power lead got: "I'm EvoSys Wholesale from EVO Integrated
Solutions LLC ... any service business needs you might have" - the sender's home
org, an account label as a person, and no idea the business does energy rate
reviews. Pinned here with a capturing fake model (no real AI call).
"""
import json
import uuid
from types import SimpleNamespace

import pytest

from app.models.models import Lead, Organization, User
from app.services import draft_context, draft_reply_service
from app.services.auth_service import hash_password


class _Capture:
    def __init__(self, content):
        self.prompts = []
        outer = self

        class _C:
            def create(self, **kw):
                outer.prompts.append("\n".join(m["content"] for m in kw["messages"]))
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
        self.chat = SimpleNamespace(completions=_C())


@pytest.fixture()
def world(db_session):
    home = Organization(name="EVO Home Solutions LLC", slug="h-%s" % uuid.uuid4().hex[:6], industry="real_estate")
    work = Organization(name="Atlantis Test Power", slug="w-%s" % uuid.uuid4().hex[:6], industry="energy")
    db_session.add_all([home, work])
    db_session.commit()
    user = User(organization_id=home.id, email="%s@x.test" % uuid.uuid4().hex[:8],
                password_hash=hash_password("Pass12345!"), full_name="EvoSys Wholesale", role="org_admin")
    lead = Lead(organization_id=work.id, first_name="Guillermo", last_name="Perez",
                email="g@example.com", tier="new_inquiry",
                custom_fields=json.dumps({"current_supplier": "TXU", "segment": "Residential"}))
    db_session.add_all([user, lead])
    db_session.commit()
    return SimpleNamespace(home=home, work=work, user=user, lead=lead)


def test_context_is_the_leads_business_and_not_a_fake_person(db_session, world):
    ctx = draft_context.build(db_session, world.lead, world.user, booking_type="Energy Rate Review")
    assert ctx["org_name"] == "Atlantis Test Power"
    assert ctx["is_person"] is False and ctx["signature"] == "The Atlantis Test Power Team"
    assert "EVO Home" not in ctx["business_block"]
    assert "Energy Rate Review" in ctx["business_block"]
    assert "New Inquiry" in ctx["business_block"]
    assert "Energy" in ctx["business_block"]
    assert "TXU" in ctx["lead_facts"] and "Residential" in ctx["lead_facts"]
    assert "utility" in ctx["voice"].lower() or "energy" in ctx["voice"].lower()


def test_a_real_person_keeps_their_name():
    assert draft_context.looks_like_person("Mike Simmons", ["Atlantis Test Power"])
    for label in ("EvoSys Wholesale", "Atlantis Test Power", "Org Admin", "Sales Team", ""):
        assert not draft_context.looks_like_person(label, ["Atlantis Test Power"]), label


def test_email_prompt_carries_the_business_and_never_the_home_org(db_session, world, monkeypatch):
    out = json.dumps({"talking_points": ["a"], "options": [
        {"label": "x", "subject": "s", "body": "Hi Guillermo,\n\nbody\n\n[Your Name]"}]})
    cap = _Capture(out)
    monkeypatch.setattr(draft_reply_service, "_get_client", lambda: cap)
    res = draft_reply_service.draft_email_options(db_session, world.lead, world.user,
                                                  booking_type="Energy Rate Review", actor=world.user.id)
    p = cap.prompts[0]
    assert "Atlantis Test Power" in p and "EVO Home" not in p
    assert "Energy Rate Review" in p and "TXU" in p
    assert "funeral" not in p.lower()
    assert res["options"][0]["body"].endswith("The Atlantis Test Power Team")


def test_sms_prompt_and_fallback_are_industry_aware(db_session, world, monkeypatch):
    cap = _Capture(json.dumps({"suggested_reply": "Hi Guillermo"}))
    monkeypatch.setattr(draft_reply_service, "_get_client", lambda: cap)
    draft_reply_service.draft_reply(db_session, world.lead, world.user, tone="cold",
                                    booking_type="Energy Rate Review", actor=world.user.id)
    p = cap.prompts[0]
    assert "Atlantis Test Power" in p and "EVO Home" not in p and "funeral" not in p.lower()
    ctx = draft_context.build(db_session, world.lead, world.user, booking_type="Energy Rate Review")
    fb = draft_reply_service._fallback_reply(world.lead, world.user, "", "cold", ctx["org_name"], ctx)
    assert "Energy Rate Review" in fb and "cemetery" not in fb and "EvoSys Wholesale" not in fb


def test_router_passes_the_booking_type(client, db_session, sample_lead, auth_headers, monkeypatch):
    seen = {}
    real = draft_context.build

    def spy(db, lead, advisor, booking_type=None):
        seen["bt"] = booking_type
        return real(db, lead, advisor, booking_type=booking_type)
    monkeypatch.setattr(draft_context, "build", spy)
    r = client.post("/email/draft/%s" % sample_lead.id, headers=auth_headers,
                    json={"tone": "warm", "booking_type": "Energy Rate Review"})
    assert r.status_code == 200 and seen["bt"] == "Energy Rate Review"
