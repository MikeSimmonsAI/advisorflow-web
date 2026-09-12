"""THE WHOLE JOURNEY, WITH A CONTROLLED RECIPIENT.

God Customer Launches → Send Onboarding → confirm the recipient → one-time
link → set a password → land in the REAL writable onboarding → save an answer
→ hard reload → the answer is still there → progress moved → Customer Launches
reflects it.

WHY THIS IS ONE TEST AND NOT SIX
================================
Every step above passes on its own in the other files. What this proves is the
JOIN: that the identity the invitation created is the identity that logs in,
that the workspace their session resolves to is the customer they were invited
to and not another, and that what they type survives the round trip. A chain of
individually-correct links can still fail to be a chain.

"Hard reload" is modelled the way it actually happens: a brand-new request
carrying only the token, resolving the workspace from the session — never from
an id the test helpfully passes in.
"""

import itertools

import pytest

from app.models.implementation_models import Implementation
from app.models.launch_intake_models import LaunchIntakeStep
from app.models.models import Organization, Platform, User
from app.services import implementation_service as impl_svc
from app.services import launch_invitation as inv
from app.services import staff_activation
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)

RECIPIENT = "controlled-e2e@test.local"
CHOSEN_PASSWORD = "TheyChoseThisOne123"


def _h(token):
    return {"Authorization": "Bearer " + token}


@pytest.fixture()
def world(db_session):
    db = db_session
    plat = Platform(name="EvoSys Pro", slug="p-%d" % next(_SEQ), short_name="EP",
                    tagline="t", support_email="launch@example.test")
    db.add(plat)
    db.commit()
    org = Organization(name="Controlled Energy Co", slug="o-%d" % next(_SEQ),
                       platform_id=plat.id, plan="standard", is_active=True,
                       industry="Energy / Energy Procurement")
    other = Organization(name="Somebody Else", slug="o-%d" % next(_SEQ),
                         platform_id=plat.id, plan="standard", is_active=True)
    db.add_all([org, other])
    db.commit()
    god = User(organization_id=None, email="owner-%d@test.local" % next(_SEQ),
               password_hash=hash_password("TestPass123!"), full_name="Owner",
               role="god_admin", must_change_password=False)
    db.add(god)
    db.commit()
    impl = impl_svc.start_for_organization(db, org, god)["implementation"]
    impl_svc.start_for_organization(db, other, god)
    return {"plat": plat, "org": org, "other": other, "impl": impl, "god": god}


def test_the_whole_journey(client, db_session, world):
    god_head = _h(create_access_token(world["god"], db_session))

    # ── 1. the operator confirms who it is going to ──
    pre = client.get("/god/launch/%s/onboarding-recipient" % world["org"].id,
                     headers=god_head)
    assert pre.status_code == 200
    assert pre.json()["status"]["state"] == inv.NOT_SENT
    assert pre.json()["brand"]["name"] == "EvoSys Pro"

    # ── 2. send ──
    sent = client.post("/god/launch/%s/send-onboarding" % world["org"].id,
                       json={"email": RECIPIENT, "confirm_email": RECIPIENT,
                             "full_name": "Controlled Recipient",
                             "base_url": "https://brand.example.test"},
                       headers=god_head)
    assert sent.status_code == 200, sent.text
    body = sent.json()
    raw_token = body["onboarding_url"].split("token=")[-1]

    # THE CUSTOMER'S LINK IS NOT THE INTERNAL PREVIEW.
    assert "/launch/preview/" not in body["onboarding_url"]
    assert world["org"].id not in body["onboarding_url"]

    db_session.expire_all()
    assert inv.status(db_session, world["org"],
                      world["impl"])["state"] == inv.INVITED

    # ── 3. they set their own password ──
    who = staff_activation.preview(db_session, raw_token)
    assert who["email"] == RECIPIENT
    assert who["full_name"] == "Controlled Recipient"

    staff_activation.accept(db_session, raw_token, CHOSEN_PASSWORD)
    db_session.expire_all()

    person = db_session.query(User).filter(User.email == RECIPIENT).first()
    assert person.must_change_password is False
    # Customer authority only — never control plane.
    assert person.role in inv.ROLES
    assert person.organization_id == world["org"].id

    # ── 4. they land in the REAL onboarding, resolved from their session ──
    theirs = _h(create_access_token(person, db_session))
    me = client.get("/launch-experience/me", headers=theirs)
    assert me.status_code == 200, me.text
    payload = me.json()
    assert payload["customer"]["name"] == "Controlled Energy Co"
    # Their own page, not a preview.
    assert payload["experience"]["preview"] is False
    assert payload["customer"]["user"]["name"] == "Controlled Recipient"
    assert payload["overview"]["overall_pct"] == 0

    # ── 5. they answer something, and it saves ──
    schema = client.get("/launch/config", headers=theirs).json()
    company = next(s for s in schema["steps"] if s["key"] == "company")
    answers = {}
    for f in company["fields"]:
        if not f.get("required"):
            continue
        kind = f.get("kind") or "text"
        if kind == "email":
            answers[f["key"]] = "ops@example.test"
        elif kind == "phone":
            answers[f["key"]] = "+15555550100"
        elif kind == "url":
            answers[f["key"]] = "https://example.test"
        elif kind == "date":
            answers[f["key"]] = "2030-01-01"
        elif kind == "checkbox":
            answers[f["key"]] = True
        elif kind == "select":
            opts = [o for o in (f.get("options") or []) if o]
            first = opts[0] if opts else "yes"
            answers[f["key"]] = first.get("value") if isinstance(first, dict) else first
        else:
            answers[f["key"]] = "Typed by the customer"
    assert answers

    saved = client.put("/launch/me/steps/company", json={"answers": answers},
                       headers=theirs)
    assert saved.status_code == 200, saved.text

    # ── 6. HARD RELOAD: a brand-new request, session only ──
    fresh = _h(create_access_token(person, db_session))
    again = client.get("/launch/me/steps/company", headers=fresh)
    assert again.status_code == 200
    for key, value in answers.items():
        if isinstance(value, str) and value == "Typed by the customer":
            assert again.json()["answers"].get(key) == value

    # ── 7. progress moved, and it is real ──
    after = client.get("/launch-experience/me", headers=fresh).json()
    assert after["overview"]["overall_pct"] > 0

    # ── 8. Customer Launches reflects it ──
    listed = client.get("/god/launch", headers=god_head).json()
    row = next(r for r in listed["launches"]
               if r["organization_id"] == world["org"].id)
    assert row["intake_pct"] > 0
    assert row["delivery"]["state"] == inv.IN_PROGRESS
    assert row["delivery"]["invited_email"] == RECIPIENT

    # ── 9. and none of it reached the other customer ──
    other_row = next(r for r in listed["launches"]
                     if r["organization_id"] == world["other"].id)
    assert other_row["intake_pct"] == 0
    assert other_row["delivery"]["state"] == inv.NOT_SENT


def test_the_invited_customer_cannot_reach_another_customers_workspace(
        client, db_session, world):
    """Their session resolves THEIR workspace. An id they supply changes
    nothing, because no customer route accepts one."""
    god_head = _h(create_access_token(world["god"], db_session))
    body = client.post("/god/launch/%s/send-onboarding" % world["org"].id,
                       json={"email": RECIPIENT, "confirm_email": RECIPIENT,
                             "full_name": "Controlled Recipient"},
                       headers=god_head).json()
    raw = body["onboarding_url"].split("token=")[-1]
    staff_activation.accept(db_session, raw, CHOSEN_PASSWORD)
    db_session.expire_all()

    person = db_session.query(User).filter(User.email == RECIPIENT).first()
    theirs = _h(create_access_token(person, db_session))

    # Their own page is their own customer.
    assert client.get("/launch-experience/me",
                      headers=theirs).json()["customer"]["name"] \
        == "Controlled Energy Co"

    # And the internal preview of anybody — including themselves — is refused.
    for org_id in (world["other"].id, world["org"].id):
        r = client.get("/launch-experience/preview/" + org_id, headers=theirs)
        assert r.status_code == 404, org_id


def test_a_replayed_link_cannot_be_used_twice(client, db_session, world):
    """One-shot. Accepting again must not hand out a second session."""
    from fastapi import HTTPException

    god_head = _h(create_access_token(world["god"], db_session))
    body = client.post("/god/launch/%s/send-onboarding" % world["org"].id,
                       json={"email": RECIPIENT, "confirm_email": RECIPIENT,
                             "full_name": "Controlled Recipient"},
                       headers=god_head).json()
    raw = body["onboarding_url"].split("token=")[-1]

    staff_activation.accept(db_session, raw, CHOSEN_PASSWORD)
    db_session.expire_all()
    with pytest.raises(HTTPException):
        staff_activation.accept(db_session, raw, "ADifferentPassword123")
