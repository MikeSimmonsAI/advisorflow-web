"""A STRANGER ASKS FOR HELP, AND IT BECOMES A REAL TICKET.

The V8 marketing site has had a working support form the whole time. It had
nowhere to post: every one of the twenty routes under `/support` required
`require_tenant_user`, so the only thing the form could do was write a CSV on
the web host and try to send an email. A person asking for help was therefore
outside the support product entirely — no ticket number, no SLA clock, no
queue, no record an operator could be measured against.

What this file defends:

  1. It REUSES the support system. `support_tickets.create_ticket` is still
     the only function that builds a ticket, so the number, the entitlement
     snapshot, the SLA target and the queue are the ones the product already
     computes — not a second, parallel inbox.
  2. A SUPPORT REQUEST IS NOT A LEAD. It creates no lead, and nothing about
     it enters a sales workspace.
  3. THE IDENTITY IS THE BRAND'S. EvoSys Pro answers as EvoSys Pro and
     BookaBoost as BookaBoost, from the same code, with neither named in it.
  4. AN ANONYMOUS REQUEST IS STILL ANSWERABLE. Before, every notification
     path derived the customer's address from the submitting USER, so a
     ticket with no user silently acknowledged nobody.
  5. AN OPERATOR IS TOLD NOBODY SIGNED IN.
"""

import uuid

import pytest

from app.models.models import Lead, Organization, Platform
from app.models.support_models import SupportTicket, TicketSource


def _brand(db, *, slug, name, support_email, website=None):
    p = Platform(name=name, slug=slug, support_email=support_email,
                 website_url=website)
    db.add(p)
    db.commit()
    return p


def _configured(db, *, slug="evosyspro", name="EvoSys Pro",
                support_email="support@evosyspro.live",
                website="https://evosyspro.live"):
    platform = _brand(db, slug=slug, name=name, support_email=support_email,
                      website=website)
    org = Organization(name="EVO Integrated Solutions LLC",
                       slug="evo-" + uuid.uuid4().hex[:8], plan="standard",
                       platform_id=platform.id, is_active=True)
    db.add(org)
    db.commit()
    platform.public_intake_organization_id = org.id
    db.commit()
    return platform, org


# The payload the V8 support form actually POSTs.
SUPPORT = {
    "submitted_at": "2026-09-13T18:00:00+00:00",
    "name": "Synthetic Tester",
    "company": "Synthetic Co",
    "email": "synthetic.tester@example.com",
    "topic": "Billing question",
    "message": "I cannot find last month's invoice.",
    "ip": "203.0.113.77",
    "user_agent": "Mozilla/5.0 (probe)",
}


def _post(client, body, slug="evosyspro"):
    return client.post("/site-intake/%s/support" % slug, json=body)


# ── 1. it reuses the ticket system ──────────────────────────────────────────

def test_a_public_support_request_creates_a_real_ticket(client, db_session):
    _, org = _configured(db_session)
    r = _post(client, SUPPORT)
    assert r.status_code == 201, r.text

    ticket = db_session.query(SupportTicket).one()
    assert ticket.organization_id == org.id
    assert ticket.ticket_number
    # The reference the site can quote back is the product's own number.
    assert r.json()["reference"] == ticket.ticket_number


def test_the_ticket_carries_the_platform_and_its_sla_clock(client, db_session):
    platform, _ = _configured(db_session)
    _post(client, SUPPORT)
    ticket = db_session.query(SupportTicket).one()
    assert ticket.platform_id == platform.id
    assert ticket.queue
    assert ticket.severity
    assert ticket.entitlement_json, "the entitlement snapshot was not taken"


def test_the_message_becomes_the_first_thread_entry(client, db_session):
    from app.models.support_models import SupportTicketMessage
    _configured(db_session)
    _post(client, SUPPORT)
    msgs = db_session.query(SupportTicketMessage).all()
    assert msgs, "the request body was not recorded as a message"
    assert "cannot find last month's invoice" in msgs[0].body.lower()


# ── 2. a support request is not a lead ──────────────────────────────────────

def test_it_creates_no_lead(client, db_session):
    """SOMEBODY WITH A PROBLEM IS NOT A PROSPECT. Filing them in the sales
    workspace would put a person asking for help into a seller's queue."""
    _configured(db_session)
    _post(client, SUPPORT)
    assert db_session.query(Lead).count() == 0


# ── 3. the sender is recorded, and marked unverified ────────────────────────

def test_the_reporter_is_recorded_because_no_account_is(client, db_session):
    _configured(db_session)
    _post(client, SUPPORT)
    ticket = db_session.query(SupportTicket).one()
    assert ticket.submitted_by is None
    assert ticket.reporter_email == "synthetic.tester@example.com"
    assert ticket.reporter_name == "Synthetic Tester"
    assert ticket.source == TicketSource.PUBLIC_WEBSITE


def test_an_operator_is_told_the_sender_is_not_signed_in(client, db_session):
    from app.models.support_models import SupportTicketMessage
    _configured(db_session)
    _post(client, SUPPORT)
    body = db_session.query(SupportTicketMessage).first().body.lower()
    assert "not signed in" in body
    assert "synthetic.tester@example.com" in body


def test_the_forms_own_fields_survive(client, db_session):
    from app.models.support_models import SupportTicketMessage
    _configured(db_session)
    _post(client, SUPPORT)
    body = db_session.query(SupportTicketMessage).first().body
    assert "Synthetic Co" in body


# ── 4. brand identity, resolved and isolated ────────────────────────────────

def test_the_reply_identity_is_the_brands_own(client, db_session):
    """EvoSys Pro answers as support@evosyspro.live — from the platform row,
    not from a literal anywhere in the request path."""
    from app.services import support_branding
    _configured(db_session)
    _post(client, SUPPORT)
    ticket = db_session.query(SupportTicket).one()
    identity = support_branding.sending_identity_for_ticket(db_session, ticket)
    assert identity.from_email == "support@evosyspro.live"
    assert identity.reply_to_email == "support@evosyspro.live"


def test_a_different_brand_answers_as_itself(client, db_session):
    """CROSS-BRAND ISOLATION. The same code, a different platform row, a
    different address — and EvoSys is not named in it."""
    from app.services import support_branding
    _configured(db_session, slug="bookaboost", name="BookaBoost",
                support_email="support@bookaboost.live",
                website="https://bookaboost.live")
    _post(client, SUPPORT, slug="bookaboost")
    ticket = db_session.query(SupportTicket).one()
    identity = support_branding.sending_identity_for_ticket(db_session, ticket)
    assert identity.from_email == "support@bookaboost.live"
    assert "evosys" not in (identity.from_email or "").lower()


def test_a_brand_with_no_support_address_refuses_rather_than_borrowing_one(
        client, db_session):
    """NO BRAND SENDS UNDER ANOTHER BRAND'S ADDRESS. An unconfigured sender
    must fail visibly, not fall back to whoever the deployment defaults to."""
    from app.services import support_branding
    _configured(db_session, slug="nameless", name="Nameless Brand",
                support_email=None, website="https://nameless.example")
    _post(client, SUPPORT, slug="nameless")
    ticket = db_session.query(SupportTicket).one()
    identity = support_branding.sending_identity_for_ticket(db_session, ticket)
    assert not identity.from_email
    assert identity.resolved is True


# ── 5. destination resolution, same rules as every public route ─────────────

def test_an_unconfigured_brand_refuses_and_files_nothing(client, db_session):
    _brand(db_session, slug="evosyspro", name="EvoSys Pro",
           support_email="support@evosyspro.live")
    r = _post(client, SUPPORT)
    assert r.status_code == 503
    assert db_session.query(SupportTicket).count() == 0


def test_an_invented_brand_is_indistinguishable_from_an_unconfigured_one(
        client, db_session):
    _brand(db_session, slug="evosyspro", name="EvoSys Pro",
           support_email="support@evosyspro.live")
    unknown = _post(client, SUPPORT, slug="not-a-brand")
    unconfigured = _post(client, SUPPORT)
    assert unknown.status_code == unconfigured.status_code == 503
    assert unknown.json() == unconfigured.json()


def test_a_spoofed_organization_id_changes_nothing(client, db_session):
    platform, org = _configured(db_session)
    other = Organization(name="Not The Destination", slug="nope",
                         plan="standard", platform_id=platform.id, is_active=True)
    db_session.add(other)
    db_session.commit()
    r = _post(client, {**SUPPORT, "organization_id": other.id, "org_id": other.id})
    assert r.status_code == 201
    assert db_session.query(SupportTicket).one().organization_id == org.id


# ── 6. validation fails cleanly, and discloses nothing ──────────────────────

def test_a_request_with_no_reply_address_is_refused(client, db_session):
    _configured(db_session)
    r = _post(client, {**SUPPORT, "email": ""})
    assert r.status_code == 422
    assert db_session.query(SupportTicket).count() == 0


def test_an_empty_message_is_refused(client, db_session):
    _configured(db_session)
    r = _post(client, {**SUPPORT, "message": "", "goals": ""})
    assert r.status_code == 422
    assert db_session.query(SupportTicket).count() == 0


def test_the_public_response_discloses_nothing_but_the_reference(client, db_session):
    """An anonymous poster learns the reference and nothing else — not the
    queue, the severity, the SLA target or the organization's name."""
    _configured(db_session)
    body = _post(client, SUPPORT).json()
    assert set(body) == {"success", "reference"}


def test_the_privileged_support_api_is_still_privileged(client, db_session):
    """The public route is additive. Nothing under /support or /god/support
    became reachable without a credential."""
    _configured(db_session)
    assert client.get("/support/tickets").status_code in (401, 403)
    assert client.post("/support/tickets",
                       json={"subject": "x", "body": "y"}).status_code in (401, 403)
    assert client.get("/god/support/overview").status_code in (401, 403)


# ── 6. the operator console can see where it came from ─────────────────────
#
# Retaining source metadata on the row and never showing it to the person
# answering the ticket is the same as not retaining it. These assert the
# operator's view carries it and the CUSTOMER'S view still does not — the
# reporter's address and the channel are operational facts, not something to
# echo back onto a customer's own screen.

def test_agent_view_shows_the_public_website_as_the_source(client, db_session):
    from app.services import support_tickets
    _configured(db_session)
    ref = _post(client, SUPPORT).json()["reference"]
    ticket = (db_session.query(SupportTicket)
              .filter(SupportTicket.ticket_number == ref).first())

    view = support_tickets.agent_view(db_session, ticket)
    assert view["source"] == TicketSource.PUBLIC_WEBSITE
    assert view["source_label"] == TicketSource.LABELS[TicketSource.PUBLIC_WEBSITE]
    assert view["reporter_email"] == SUPPORT["email"]
    assert view["reporter_name"] == SUPPORT["name"]
    assert view["submitted_by"] is None, "nobody was signed in"


def test_the_customer_view_does_not_gain_the_reporter_address(client, db_session):
    from app.services import support_tickets
    _configured(db_session)
    ref = _post(client, SUPPORT).json()["reference"]
    ticket = (db_session.query(SupportTicket)
              .filter(SupportTicket.ticket_number == ref).first())

    view = support_tickets.customer_view(db_session, ticket)
    for key in ("reporter_email", "reporter_name", "source"):
        assert key not in view, "%s leaked into the customer view" % key


def test_an_in_app_ticket_still_reports_its_own_source(db_session):
    """The addition must not relabel everything that already existed."""
    from app.models.models import User
    from app.services import support_tickets
    from app.services.auth_service import hash_password

    platform, org = _configured(db_session)
    user = User(organization_id=org.id, email="staff@example.com",
                password_hash=hash_password("TestPass123!"),
                full_name="Staff", role="org_admin", is_active=True,
                must_change_password=False)
    db_session.add(user)
    db_session.commit()

    ticket = support_tickets.create_ticket(
        db_session, org=org, user=user, subject="In-app", body="From the app.")
    db_session.commit()

    view = support_tickets.agent_view(db_session, ticket)
    assert view["source"] == TicketSource.IN_APP
    assert view["submitted_by"] == user.id
    # The reply address is recorded either way — from the signed-in user here,
    # from the form there — so one field answers "who do I write back to"
    # without the caller having to know which kind of ticket it is.
    assert view["reporter_email"] == "staff@example.com"
