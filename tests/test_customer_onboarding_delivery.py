"""SENDING A CUSTOMER THEIR OWN ONBOARDING — and everything that must not happen.

THE TWO DOORS
=============
`/launch/preview/{organization_id}` is the INTERNAL read: staff only, read
only, so somebody can see what a customer will get BEFORE contacting them. A
customer must never receive that URL — it names an organization in the path,
it is not their session, and nothing they typed on it would save.

This file tests the OTHER door: a named person is given an identity inside the
customer's own organization plus a one-time link, after which `/launch`
resolves their workspace from their own session and writes what they type.

WHAT IS BEING DEFENDED
======================
  * no duplicate identity on resend, ever
  * no control-plane authority, by any route through this endpoint
  * no cross-customer or cross-brand reach
  * no second valid link after a resend
  * no state the system cannot prove — and specifically no OPENED/DELIVERED
  * no message sent by the platform, so no accidental contact with a real
    person during internal testing
"""

import itertools

import pytest

from app.models.implementation_models import Implementation
from app.models.models import AuditLogEntry, Organization, Platform, User
from app.models.staff_models import (
    StaffActivation, STAFF_INVITE_PENDING, STAFF_INVITE_REVOKED,
)
from app.services import implementation_service as impl_svc
from app.services import launch_invitation as inv
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _platform(db, name):
    p = Platform(name=name, slug="p-%d" % next(_SEQ), short_name=name[:2],
                 tagline="t", support_email="s@example.test")
    db.add(p)
    db.commit()
    return p


def _org(db, plat, name, industry="energy"):
    o = Organization(name=name, slug="o-%d" % next(_SEQ), platform_id=plat.id,
                     plan="standard", is_active=True, industry=industry)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role, label):
    u = User(organization_id=(org.id if org else None),
             email="%s-%d@test.local" % (label, next(_SEQ)),
             password_hash=hash_password("TestPass123!"),
             full_name=label.title(), role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


@pytest.fixture()
def world(db_session):
    db = db_session
    brand_a = _platform(db, "Brand Alpha")
    brand_b = _platform(db, "Brand Beta")
    org_a = _org(db, brand_a, "Alpha Customer")
    org_b = _org(db, brand_b, "Beta Customer")
    god = _user(db, None, "god_admin", "owner")
    impl_a = impl_svc.start_for_organization(db, org_a, god)["implementation"]
    impl_b = impl_svc.start_for_organization(db, org_b, god)["implementation"]
    return {"brand_a": brand_a, "brand_b": brand_b,
            "org_a": org_a, "org_b": org_b,
            "impl_a": impl_a, "impl_b": impl_b, "god": god}


RECIPIENT = "controlled-recipient@test.local"


def _send(client, db, world, **over):
    body = {"email": RECIPIENT, "confirm_email": RECIPIENT,
            "full_name": "Controlled Recipient",
            "base_url": "https://alpha.example.test"}
    body.update(over)
    return client.post("/god/launch/%s/send-onboarding" % world["org_a"].id,
                       json=body, headers=_h(db, world["god"]))


# ════════════════════════════════════════════════════════════════════════════
# 1. CONFIRM BEFORE ANYTHING IS SENT
# ════════════════════════════════════════════════════════════════════════════

class TestRecipientConfirmation:
    def test_asking_who_would_receive_it_creates_nothing(self, client,
                                                         db_session, world):
        before_users = db_session.query(User).count()
        before_acts = db_session.query(StaffActivation).count()

        r = client.get("/god/launch/%s/onboarding-recipient" % world["org_a"].id,
                       headers=_h(db_session, world["god"]))
        assert r.status_code == 200
        body = r.json()
        assert body["will_send_message"] is False
        assert body["brand"]["name"] == "Brand Alpha"
        assert body["status"]["state"] == inv.NOT_SENT

        db_session.expire_all()
        assert db_session.query(User).count() == before_users
        assert db_session.query(StaffActivation).count() == before_acts

    def test_a_send_without_confirmation_is_refused_and_writes_nothing(
            self, client, db_session, world):
        before = db_session.query(User).count()
        r = _send(client, db_session, world, confirm_email="")
        assert r.status_code == 400
        assert "confirm" in r.json()["detail"].lower()
        db_session.expire_all()
        assert db_session.query(User).count() == before
        assert db_session.query(StaffActivation).count() == 0

    def test_a_mismatched_confirmation_is_refused(self, client, db_session,
                                                  world):
        """The failure worth preventing is not technical — it is inviting a
        real person into the wrong company's workspace."""
        r = _send(client, db_session, world,
                  confirm_email="someone-else@test.local")
        assert r.status_code == 400
        db_session.expire_all()
        assert db_session.query(StaffActivation).count() == 0

    def test_a_customer_with_no_brand_cannot_be_invited_by_nobody(
            self, client, db_session, world):
        """An invitation with no sender identity arrives from nowhere."""
        orphan = Organization(name="No Brand Co", slug="nb-%d" % next(_SEQ),
                              platform_id=None, plan="standard", is_active=True)
        db_session.add(orphan)
        db_session.commit()
        impl_svc.start_for_organization(db_session, orphan, world["god"])

        r = client.post("/god/launch/%s/send-onboarding" % orphan.id,
                        json={"email": RECIPIENT, "confirm_email": RECIPIENT},
                        headers=_h(db_session, world["god"]))
        assert r.status_code == 409
        db_session.expire_all()
        assert db_session.query(StaffActivation).count() == 0


# ════════════════════════════════════════════════════════════════════════════
# 2. THE SEND ITSELF
# ════════════════════════════════════════════════════════════════════════════

class TestSend:
    def test_it_creates_the_identity_and_a_one_time_link(self, client,
                                                          db_session, world):
        r = _send(client, db_session, world)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["identity_created"] is True
        assert body["recipient"]["email"] == RECIPIENT
        assert body["onboarding_url"].startswith("https://alpha.example.test")
        assert body["expires_at"]

        db_session.expire_all()
        u = db_session.query(User).filter(User.email == RECIPIENT).first()
        assert u is not None
        assert u.organization_id == world["org_a"].id

    def test_the_platform_does_not_send_the_message(self, client, db_session,
                                                     world):
        """Internal testing must never put a real address into a send queue."""
        body = _send(client, db_session, world).json()
        assert body["message_sent_by_platform"] is False

    def test_the_recipient_gets_customer_authority_and_nothing_more(
            self, client, db_session, world):
        _send(client, db_session, world)
        db_session.expire_all()
        u = db_session.query(User).filter(User.email == RECIPIENT).first()
        assert u.role in inv.ROLES
        assert u.role != "god_admin"
        # And they are inside the customer, not floating in the control plane.
        assert u.organization_id == world["org_a"].id

    def test_no_control_plane_role_is_expressible_through_this_endpoint(
            self, client, db_session, world):
        for role in ("god_admin", "sales_manager", "brand_executive",
                     "sales_rep"):
            r = _send(client, db_session, world, role=role)
            assert r.status_code == 400, role
        db_session.expire_all()
        assert db_session.query(User).filter(User.email == RECIPIENT).first() is None

    def test_the_link_is_never_stored_in_the_clear(self, client, db_session,
                                                    world):
        body = _send(client, db_session, world).json()
        raw = body["onboarding_url"].split("token=")[-1]
        db_session.expire_all()
        row = db_session.query(StaffActivation).first()
        assert row.token_hash != raw
        assert raw not in (row.token_hash or "")
        # And the audit note does not carry it either.
        for e in db_session.query(AuditLogEntry).all():
            assert raw not in str(e.details or "")

    def test_the_act_is_audited_against_the_customer(self, client, db_session,
                                                      world):
        _send(client, db_session, world)
        db_session.expire_all()
        rows = (db_session.query(AuditLogEntry).filter(
            AuditLogEntry.action == "customer_onboarding_invitation_issued"
        ).all())
        assert len(rows) == 1
        assert rows[0].organization_id == world["org_a"].id
        # `details` is stored as JSON text by the audit helper.
        import json
        d = rows[0].details
        d = json.loads(d) if isinstance(d, str) else (d or {})
        assert d.get("message_sent_by_platform") is False
        assert d.get("identity_created") is True


# ════════════════════════════════════════════════════════════════════════════
# 3. RESEND IS SAFE
# ════════════════════════════════════════════════════════════════════════════

class TestResend:
    def test_resending_never_creates_a_second_identity(self, client,
                                                        db_session, world):
        first = _send(client, db_session, world).json()
        second = _send(client, db_session, world).json()
        assert first["identity_created"] is True
        assert second["identity_created"] is False
        assert first["recipient"]["id"] == second["recipient"]["id"]
        db_session.expire_all()
        assert db_session.query(User).filter(User.email == RECIPIENT).count() == 1

    def test_resending_leaves_only_one_valid_link(self, client, db_session,
                                                  world):
        """A link that leaked must not be rescued by whoever leaked it."""
        _send(client, db_session, world)
        _send(client, db_session, world)
        db_session.expire_all()
        rows = db_session.query(StaffActivation).all()
        pending = [r for r in rows if r.status == STAFF_INVITE_PENDING]
        assert len(pending) == 1
        assert any(r.status == STAFF_INVITE_REVOKED for r in rows)

    def test_the_old_link_stops_working(self, client, db_session, world):
        first = _send(client, db_session, world).json()
        old = first["onboarding_url"].split("token=")[-1]
        _send(client, db_session, world)
        db_session.expire_all()
        from app.services import staff_activation
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            staff_activation.resolve(db_session, old)

    def test_an_existing_person_is_reused_not_duplicated(self, client,
                                                          db_session, world):
        existing = _user(db_session, world["org_a"], "advisor", "already-here")
        r = _send(client, db_session, world, email=existing.email,
                  confirm_email=existing.email)
        assert r.status_code == 200
        assert r.json()["identity_created"] is False
        db_session.expire_all()
        assert db_session.query(User).filter(
            User.email == existing.email).count() == 1


# ════════════════════════════════════════════════════════════════════════════
# 4. ATTACK
# ════════════════════════════════════════════════════════════════════════════

class TestAttack:
    def test_anonymous_cannot_look_or_send(self, client, world):
        org = world["org_a"].id
        assert client.get("/god/launch/%s/onboarding-recipient" % org
                          ).status_code in (401, 403)
        assert client.post("/god/launch/%s/send-onboarding" % org,
                           json={"email": RECIPIENT,
                                 "confirm_email": RECIPIENT}
                           ).status_code in (401, 403)

    def test_a_customer_cannot_invite_anybody_into_their_own_org(
            self, client, db_session, world):
        admin = _user(db_session, world["org_a"], "org_admin", "customer-admin")
        r = client.post("/god/launch/%s/send-onboarding" % world["org_a"].id,
                        json={"email": RECIPIENT, "confirm_email": RECIPIENT},
                        headers=_h(db_session, admin))
        assert r.status_code in (403, 404)
        db_session.expire_all()
        assert db_session.query(StaffActivation).count() == 0

    def test_a_customer_cannot_invite_into_another_customer(self, client,
                                                             db_session, world):
        admin = _user(db_session, world["org_a"], "org_admin", "customer-admin")
        r = client.post("/god/launch/%s/send-onboarding" % world["org_b"].id,
                        json={"email": RECIPIENT, "confirm_email": RECIPIENT},
                        headers=_h(db_session, admin))
        assert r.status_code in (403, 404)
        db_session.expire_all()
        assert db_session.query(StaffActivation).count() == 0

    def test_an_unknown_customer_is_not_confirmed_to_exist(self, client,
                                                           db_session, world):
        r = client.post("/god/launch/does-not-exist/send-onboarding",
                        json={"email": RECIPIENT, "confirm_email": RECIPIENT},
                        headers=_h(db_session, world["god"]))
        assert r.status_code == 404

    def test_a_tampered_token_is_refused_and_says_nothing(self, client,
                                                           db_session, world):
        body = _send(client, db_session, world).json()
        raw = body["onboarding_url"].split("token=")[-1]
        from app.services import staff_activation
        from fastapi import HTTPException
        for bad in (raw[:-1] + ("a" if raw[-1] != "a" else "b"),
                    raw + "x", "act_nonsense", ""):
            with pytest.raises(HTTPException) as e:
                staff_activation.resolve(db_session, bad)
            # Fails IDENTICALLY, so a token cannot be probed for
            # "exists but expired" versus "never existed".
            assert e.value.detail == staff_activation.GENERIC_REJECTION

    def test_an_expired_link_is_refused(self, client, db_session, world):
        from datetime import datetime, timedelta
        from fastapi import HTTPException
        from app.services import staff_activation

        body = _send(client, db_session, world).json()
        raw = body["onboarding_url"].split("token=")[-1]
        row = db_session.query(StaffActivation).filter(
            StaffActivation.status == STAFF_INVITE_PENDING).first()
        row.expires_at = datetime.utcnow() - timedelta(hours=1)
        db_session.commit()
        with pytest.raises(HTTPException):
            staff_activation.resolve(db_session, raw)

    def test_the_invitation_does_not_reach_across_brands(self, client,
                                                          db_session, world):
        """The recipient lands in the organization that was named, and its
        brand — never another brand's customer."""
        _send(client, db_session, world)
        db_session.expire_all()
        u = db_session.query(User).filter(User.email == RECIPIENT).first()
        assert u.organization_id == world["org_a"].id
        assert u.organization_id != world["org_b"].id
        org = db_session.query(Organization).filter(
            Organization.id == u.organization_id).first()
        assert org.platform_id == world["brand_a"].id


# ════════════════════════════════════════════════════════════════════════════
# 5. ONLY PROVABLE STATES
# ════════════════════════════════════════════════════════════════════════════

class TestStatusIsProvable:
    def test_before_anything_it_is_not_sent(self, db_session, world):
        s = inv.status(db_session, world["org_a"], world["impl_a"])
        assert s["state"] == inv.NOT_SENT
        assert s["invited_email"] is None

    def test_after_sending_it_is_invited_and_names_the_recipient(
            self, client, db_session, world):
        _send(client, db_session, world)
        db_session.expire_all()
        s = inv.status(db_session, world["org_a"], world["impl_a"])
        assert s["state"] == inv.INVITED
        assert s["invited_email"] == RECIPIENT
        assert s["invited_at"] is not None
        assert s["send_count"] >= 1

    def test_it_never_claims_opened_or_delivered(self, client, db_session,
                                                  world):
        """Nothing here observes a mailbox, so claiming either would be
        inventing evidence — and an operator who believes a customer has read
        something they have not is worse off than one who knows nothing."""
        _send(client, db_session, world)
        db_session.expire_all()
        s = inv.status(db_session, world["org_a"], world["impl_a"])
        assert s["opened"] is None
        assert s["delivery_evidence"] is None
        assert set(inv.STATE_LABELS) == {
            inv.NOT_SENT, inv.INVITED, inv.ACCOUNT_ACTIVE,
            inv.IN_PROGRESS, inv.SUBMITTED, inv.REVIEWED,
        }
        blob = " ".join(inv.STATE_LABELS.values()).lower()
        for banned in ("opened", "read", "delivered"):
            assert banned not in blob

    def test_the_launches_list_shows_who_has_and_has_not_been_sent(
            self, client, db_session, world):
        """An operator looking at a launch at 0% needs to know whether the
        customer is slow or was never sent anything. Opposite problems."""
        head = _h(db_session, world["god"])
        before = client.get("/god/launch", headers=head).json()
        row = next(r for r in before["launches"]
                   if r["organization_id"] == world["org_a"].id)
        assert row["delivery"]["state"] == inv.NOT_SENT

        _send(client, db_session, world)

        after = client.get("/god/launch", headers=head).json()
        row = next(r for r in after["launches"]
                   if r["organization_id"] == world["org_a"].id)
        assert row["delivery"]["state"] == inv.INVITED
        assert row["delivery"]["invited_email"] == RECIPIENT
        # The other customer is untouched.
        other = next(r for r in after["launches"]
                     if r["organization_id"] == world["org_b"].id)
        assert other["delivery"]["state"] == inv.NOT_SENT


# ════════════════════════════════════════════════════════════════════════════
# 6. THE PREVIEW URL IS NEVER WHAT A CUSTOMER GETS
# ════════════════════════════════════════════════════════════════════════════

class TestTheCustomerNeverGetsThePreview:
    def test_the_sent_link_is_an_activation_link_not_a_preview_url(
            self, client, db_session, world):
        body = _send(client, db_session, world).json()
        url = body["onboarding_url"]
        assert "/launch/preview/" not in url
        assert world["org_a"].id not in url, \
            "the customer's link must not carry an organization id"
        assert "token=" in url
