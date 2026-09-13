"""MESSAGE TEMPLATES BELONG TO THE ORGANIZATION, NOT TO ONE CUSTOMER'S PIPELINE.

Found live. Standing inside Atlantis Light & Power — configured as energy
procurement, with its own tier model — Message Templates showed:

    Pre Need Lock Price   "...locking in today's pricing..."
    At Need (support)     "...any arrangements you need...", "...in case your
                           family needs support right now..."

Not a mislabel. A funeral home's outreach copy, presented to an energy broker
as its own, ready to send.

The cause was that `list_all_templates_with_defaults` iterated the
`MessageTrack` enum — pre_need_lock_price, at_need_support, imminent_support,
upsell_existing — which is one customer's pipeline frozen into the platform.
`TierDefinition` has carried `track_key` and `track_label` per organization the
whole time; it is what the tier filter, the tier editor and the cadence tone
lookup already read.

A second defect fell out of the first: the write path validated with
`MessageTrack(message_track)`, so a customer could READ a templates page full
of its own tracks and then not save one, because `energy_intro` is not in that
enum.
"""

import uuid

import pytest

from app.models.models import (MessageTemplate, MessageTrack, Organization,
                               TierDefinition, User)
from app.services import template_service
from app.services.auth_service import create_access_token, hash_password


def _org(db, name, industry):
    org = Organization(name=name, slug="o-" + uuid.uuid4().hex[:8], plan="standard",
                       industry=industry, is_active=True)
    db.add(org)
    db.commit()
    return org


def _tier(db, org, tier_key, tier_label, track_key, track_label,
          tone=None, order=0):
    row = TierDefinition(organization_id=org.id, tier_key=tier_key,
                         tier_label=tier_label, track_key=track_key,
                         track_label=track_label, ai_tone_context=tone,
                         is_manual_selectable=True, is_active=True,
                         sort_order=order)
    db.add(row)
    db.commit()
    return row


def _admin(db, org):
    u = User(organization_id=org.id, email="admin+%s@example.com" % uuid.uuid4().hex[:6],
             password_hash=hash_password("TestPass123!"), full_name="Admin",
             role="org_admin", is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def energy(db_session):
    org = _org(db_session, "Atlantis Light & Power", "energy")
    _tier(db_session, org, "new_inquiry", "New Inquiry", "energy_intro",
          "Energy Intro", tone="New energy inquiry. Offer a rate review.", order=0)
    _tier(db_session, org, "rate_review", "Rate Review", "energy_review",
          "Rate Review", order=1)
    return org


@pytest.fixture()
def deathcare(db_session):
    org = _org(db_session, "A Funeral Home", "funeral")
    _tier(db_session, org, "pre_need", "Pre-Need", "pre_need_lock_price",
          "Pre-Need Lock Price", order=0)
    _tier(db_session, org, "at_need", "At-Need", "at_need_support",
          "At-Need (support)", order=1)
    return org


# ── 1. the list is the organization's own ───────────────────────────────────

def test_an_energy_customer_is_not_shown_a_funeral_homes_tracks(db_session, energy):
    rows = template_service.list_all_templates_with_defaults(db_session, energy.id)
    tracks = {r["message_track"] for r in rows}
    assert tracks == {"energy_intro", "energy_review"}
    for forbidden in ("pre_need_lock_price", "at_need_support",
                      "imminent_support", "upsell_existing"):
        assert forbidden not in tracks


def test_no_funeral_copy_reaches_an_energy_customers_editor(db_session, energy):
    rows = template_service.list_all_templates_with_defaults(db_session, energy.id)
    blob = " ".join((r["body_template"] or "") + " " +
                    (r["email_subject_template"] or "") for r in rows).lower()
    for word in ("pre-need", "pre need", "at-need", "arrangements",
                 "funeral", "cemetery", "locking in today's pricing"):
        assert word not in blob, "%r reached a non-deathcare customer" % word


def test_the_organizations_own_track_label_is_returned(db_session, energy):
    rows = template_service.list_all_templates_with_defaults(db_session, energy.id)
    labels = {r["message_track"]: r["track_label"] for r in rows}
    assert labels["energy_intro"] == "Energy Intro"
    assert labels["energy_review"] == "Rate Review"


def test_every_track_has_both_channels_and_a_usable_draft(db_session, energy):
    rows = template_service.list_all_templates_with_defaults(db_session, energy.id)
    assert len(rows) == 4
    for row in rows:
        assert row["body_template"].strip(), row
        assert row["is_customized"] is False
    sms = [r for r in rows if r["channel"] == "sms"]
    assert all("Reply STOP to opt out." in r["body_template"] for r in sms), \
        "the carrier-required opt-out must be in every SMS draft"


# ── 2. deathcare keeps everything it has ────────────────────────────────────

def test_a_funeral_customer_still_gets_its_own_copy(db_session, deathcare):
    rows = template_service.list_all_templates_with_defaults(db_session, deathcare.id)
    tracks = {r["message_track"] for r in rows}
    assert tracks == {"pre_need_lock_price", "at_need_support"}
    blob = " ".join(r["body_template"] for r in rows).lower()
    assert "locking in today's pricing" in blob
    assert "arrangements" in blob


def test_an_organization_with_no_tier_definitions_sees_the_platform_tracks(
        db_session):
    """A legacy tenant that predates tier definitions loses nothing."""
    org = _org(db_session, "Legacy Tenant", None)
    rows = template_service.list_all_templates_with_defaults(db_session, org.id)
    tracks = {r["message_track"] for r in rows}
    assert tracks == {t.value for t in MessageTrack}


# ── 3. overrides still win, and can still be written ────────────────────────

def test_an_override_wins_over_the_default(db_session, energy):
    db_session.add(MessageTemplate(
        organization_id=energy.id, message_track="energy_intro", channel="sms",
        body_template="Our own words.", updated_by_user_id=None))
    db_session.commit()
    rows = template_service.list_all_templates_with_defaults(db_session, energy.id)
    row = next(r for r in rows
               if r["message_track"] == "energy_intro" and r["channel"] == "sms")
    assert row["body_template"] == "Our own words."
    assert row["is_customized"] is True


def test_the_customer_can_save_a_template_for_its_own_track(client, db_session,
                                                            energy):
    """THE SECOND DEFECT. The page listed the track and the save refused it."""
    admin = _admin(db_session, energy)
    headers = {"Authorization": "Bearer " + create_access_token(admin, db_session)}
    r = client.put("/templates/", headers=headers, json={
        "message_track": "energy_review", "channel": "sms",
        "body_template": "Hi {first_name}, shall we review your rate?"})
    assert r.status_code == 200, r.text

    rows = template_service.list_all_templates_with_defaults(db_session, energy.id)
    row = next(r for r in rows
               if r["message_track"] == "energy_review" and r["channel"] == "sms")
    assert row["is_customized"] is True


def test_a_track_belonging_to_nobody_is_still_refused(client, db_session, energy):
    """Widening the allow-list is not removing it."""
    admin = _admin(db_session, energy)
    headers = {"Authorization": "Bearer " + create_access_token(admin, db_session)}
    r = client.put("/templates/", headers=headers, json={
        "message_track": "not_a_track_at_all", "channel": "sms",
        "body_template": "..."})
    assert r.status_code == 400, r.text


def test_the_endpoint_returns_the_customers_own_tracks(client, db_session, energy):
    admin = _admin(db_session, energy)
    headers = {"Authorization": "Bearer " + create_access_token(admin, db_session)}
    rows = client.get("/templates/", headers=headers).json()
    assert {r["message_track"] for r in rows} == {"energy_intro", "energy_review"}
