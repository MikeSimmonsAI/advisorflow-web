"""DEFECT 3 — A SUPPORT EMAIL WEARS THE TICKET'S OWN BRAND. NOTHING ELSE.

THE LEAK
--------
A ticket raised inside EvoSys Pro produced an email that presented itself as
BookaBoost. The body was already right — it reads the brand's display name out
of the ticket's own entitlement snapshot — but `support_tickets._notify`
called

    send_email_via_provider(to_email, subject, body_html)

with no `org=` at all. With nothing handed to it, that function fell through
to the module-level `FROM_EMAIL`, which was
`os.environ.get("EMAIL_FROM_ADDRESS", "noreply@bookaboost.com")`. Nobody chose
BookaBoost; a literal in a `.get()` default did, for every brand on the
deployment.

WHAT THESE TESTS DEFEND
-----------------------
  1. EvoSys ticket  -> EvoSys sender, EvoSys reply-to, EvoSys name.
  2. BookaBoost ticket -> BookaBoost's.
  3. Brand A cannot produce Brand B's support email, in either direction.
  4. The brand resolves from the TICKET/ORG/PLATFORM chain — never a default,
     never the first brand in the table, never the environment.
  5. A brand with no support address FAILS SAFE: it sends nothing rather than
     sending as somebody else.
  6. The refusal and the send are both visible, and SLA/ticket logic is
     untouched by any of it.

NOTHING HERE SENDS MAIL. `send_email_via_provider` is replaced with a recorder
in every test, and the one test that exercises the real sender stops at the
refusal before any provider is reached.
"""

import itertools

import pytest

from app.models.models import Organization, Platform, User
from app.models.support_models import (
    Severity, SupportTicket, TicketCategory, TicketStatus,
)
from app.services import support_branding, support_tickets
from app.services.auth_service import hash_password

_SEQ = itertools.count(1)


# ── two brands, configured exactly as production has them ──────────────────

@pytest.fixture()
def evosys(db_session):
    row = Platform(name="EvoSys Pro", slug="evosyspro",
                   domain="app.evosyspro.live",
                   support_email="support@evosyspro.live",
                   accent_color="#087cff", is_active=True)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture()
def bookaboost(db_session):
    row = Platform(name="BookaBoost", slug="bookaboost",
                   domain="app.bookaboost.live",
                   support_email="support@bookaboost.live",
                   accent_color="#c9973d", is_active=True)
    db_session.add(row)
    db_session.commit()
    return row


def _org(db, platform, name):
    n = next(_SEQ)
    org = Organization(name=name, slug="%s-%d" % (name.lower().replace(" ", "-"), n),
                       plan="starter", billing_plan_key="starter",
                       platform_id=(platform.id if platform else None),
                       is_active=True)
    db.add(org)
    db.commit()
    return org


def _user(db, org):
    u = User(organization_id=org.id, email="person%d@customer.com" % next(_SEQ),
             password_hash=hash_password("TestPass123!"),
             full_name="A Person", role="advisor", must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _ticket(db, org, user):
    return support_tickets.create_ticket(
        db, org=org, user=user, subject="Emails are not going out",
        body="Nothing has sent since this morning.",
        category=TicketCategory.TECHNICAL_PRODUCT_SUPPORT)


class _Recorder(object):
    """Stands in for `email_service.send_email_via_provider`.

    Records the resolved identity it was handed, which is the whole point:
    the defect was an argument that was never passed.
    """

    def __init__(self):
        self.sent = []

    def __call__(self, to_email, subject, body_html, attachments=None, org=None):
        self.sent.append({
            "to": to_email,
            "subject": subject,
            "html": body_html,
            "from_email": getattr(org, "from_email", None),
            "reply_to": getattr(org, "reply_to_email", None),
            "resolved": getattr(org, "resolved", False),
        })
        return {"success": True, "provider_message_id": "msg_%d" % len(self.sent),
                "error": None}


@pytest.fixture()
def mail(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr("app.services.email_service.send_email_via_provider",
                        recorder)
    return recorder


# ═══════════════════════════════════════════════════════════════════════════
# 1. EACH BRAND'S TICKET PRODUCES THAT BRAND'S EMAIL
# ═══════════════════════════════════════════════════════════════════════════

def test_an_evosys_ticket_sends_as_evosys(db_session, evosys, mail):
    org = _org(db_session, evosys, "Restland")
    user = _user(db_session, org)
    _ticket(db_session, org, user)

    assert mail.sent, "the acknowledgement must actually be sent"
    for msg in mail.sent:
        assert msg["from_email"] == "support@evosyspro.live"
        assert msg["reply_to"] == "support@evosyspro.live"
        assert msg["resolved"] is True
        assert "EvoSys Pro" in msg["html"]
        # THE LEAK, ASSERTED AGAINST BY NAME.
        assert "bookaboost" not in msg["html"].lower()
        assert "bookaboost" not in (msg["from_email"] or "").lower()
        assert "advisorflow" not in msg["html"].lower()


def test_a_bookaboost_ticket_sends_as_bookaboost(db_session, bookaboost, mail):
    org = _org(db_session, bookaboost, "Other Customer")
    user = _user(db_session, org)
    _ticket(db_session, org, user)

    assert mail.sent
    for msg in mail.sent:
        assert msg["from_email"] == "support@bookaboost.live"
        assert "BookaBoost" in msg["html"]
        assert "evosys" not in msg["html"].lower()
        assert "evosys" not in (msg["from_email"] or "").lower()


def test_neither_brand_can_produce_the_others_support_email(
        db_session, evosys, bookaboost, mail):
    """BOTH BRANDS LIVE IN THE SAME DEPLOYMENT, AT THE SAME TIME.

    This is the configuration the leak actually happened in: two platform
    rows, two customers, one sender. Each ticket is checked against the other
    brand's every identifying string.
    """
    org_e = _org(db_session, evosys, "Restland")
    org_b = _org(db_session, bookaboost, "Other Customer")
    _ticket(db_session, org_e, _user(db_session, org_e))
    evosys_msgs = list(mail.sent)
    mail.sent.clear()
    _ticket(db_session, org_b, _user(db_session, org_b))
    bookaboost_msgs = list(mail.sent)

    assert evosys_msgs and bookaboost_msgs
    for msg in evosys_msgs:
        blob = (msg["html"] + (msg["from_email"] or "") + (msg["to"] or "")).lower()
        assert "bookaboost" not in blob
    for msg in bookaboost_msgs:
        blob = (msg["html"] + (msg["from_email"] or "") + (msg["to"] or "")).lower()
        assert "evosyspro" not in blob


def test_the_brand_comes_from_the_ticket_not_from_whichever_row_is_first(
        db_session, bookaboost, evosys, mail):
    """BookaBoost is inserted FIRST here, deliberately.

    "The first brand in the table" is one of the named wrong answers. If
    resolution ever regresses to it, this test is the one that fails.
    """
    org = _org(db_session, evosys, "Restland")
    _ticket(db_session, org, _user(db_session, org))
    assert all(m["from_email"] == "support@evosyspro.live" for m in mail.sent)


def test_resolution_reads_the_tickets_own_platform_column(db_session, evosys,
                                                          bookaboost):
    """AUTHORITATIVE CONTEXT, NAMED. The ticket's platform_id wins over
    anything else that might be lying around."""
    org = _org(db_session, evosys, "Restland")
    ticket = SupportTicket(ticket_number="SUP-TEST-000001",
                           organization_id=org.id, platform_id=bookaboost.id,
                           subject="x", category=TicketCategory.TECHNICAL_PRODUCT_SUPPORT,
                           severity=Severity.P3, status=TicketStatus.NEW)
    db_session.add(ticket)
    db_session.commit()

    identity = support_branding.sending_identity_for_ticket(db_session, ticket)
    assert identity.from_email == "support@bookaboost.live"
    assert identity.platform_id == bookaboost.id


def test_a_ticket_with_no_platform_column_falls_back_to_its_org(db_session,
                                                                evosys):
    """A repair path, not a guess: the org's platform, never a default."""
    org = _org(db_session, evosys, "Restland")
    ticket = SupportTicket(ticket_number="SUP-TEST-000002",
                           organization_id=org.id, platform_id=None,
                           subject="x", category=TicketCategory.TECHNICAL_PRODUCT_SUPPORT,
                           severity=Severity.P3, status=TicketStatus.NEW)
    db_session.add(ticket)
    db_session.commit()

    identity = support_branding.sending_identity_for_ticket(db_session, ticket)
    assert identity.from_email == "support@evosyspro.live"


# ═══════════════════════════════════════════════════════════════════════════
# 2. MISSING BRANDING FAILS SAFE
# ═══════════════════════════════════════════════════════════════════════════

def test_an_unbranded_organization_sends_nothing_rather_than_as_a_brand(
        db_session, evosys, bookaboost, mail):
    """NO PLATFORM AT ALL. The old code sent this as BookaBoost."""
    org = _org(db_session, None, "Orphan Customer")
    user = _user(db_session, org)
    _ticket(db_session, org, user)
    assert mail.sent == [], "an unresolved brand must send nothing"


def test_a_brand_with_no_support_address_sends_nothing(db_session, mail):
    plat = Platform(name="New Brand", slug="newbrand-%d" % next(_SEQ),
                    support_email=None, is_active=True)
    db_session.add(plat)
    db_session.commit()
    org = _org(db_session, plat, "New Brand Customer")
    _ticket(db_session, org, _user(db_session, org))
    assert mail.sent == []


def test_the_ticket_is_still_created_when_the_email_is_refused(
        db_session, mail):
    """A NOTIFICATION FAILURE IS NEVER A TICKET FAILURE.

    The customer still has a number to quote, the SLA clock still started,
    and the refusal is a log line rather than a 500 in front of somebody
    whose product is broken.
    """
    org = _org(db_session, None, "Orphan Customer")
    ticket = _ticket(db_session, org, _user(db_session, org))
    assert ticket.ticket_number.startswith("SUP-")
    assert ticket.severity in Severity.ALL
    assert ticket.queue
    assert mail.sent == []


def test_the_real_sender_refuses_an_unresolved_identity(db_session, evosys,
                                                        monkeypatch):
    """THE SECOND HALF OF THE FIX, IN `email_service` ITSELF.

    Even if a caller hands over an identity that resolved to nothing, the
    provider function refuses instead of substituting a deployment-wide
    address. Asserted with EMAIL_FROM_ADDRESS deliberately set, so the test
    proves the refusal is about the RESOLUTION and not about the env being
    empty.
    """
    from app.services import email_service
    monkeypatch.setattr(email_service, "FROM_EMAIL", "noreply@bookaboost.com")
    monkeypatch.setattr(email_service, "RESEND_API_KEY", "re_fake_key")

    identity = support_branding.SupportSendingIdentity(
        from_email=None, reply_to_email=None, platform_id="p", display_name="X")
    result = email_service.send_email_via_provider(
        "customer@example.com", "Subject", "<p>Body</p>", org=identity)

    assert result["success"] is False
    assert "another brand" in result["error"] or "verified" in result["error"]


def test_no_brand_name_survives_as_a_module_default(monkeypatch):
    """THE LITERAL IS GONE. With the environment unset, the module-level
    sender is None — not somebody's brand."""
    monkeypatch.delenv("EMAIL_FROM_ADDRESS", raising=False)
    import importlib

    from app.services import email_service
    reloaded = importlib.reload(email_service)
    try:
        assert reloaded.FROM_EMAIL is None
    finally:
        importlib.reload(email_service)


# ═══════════════════════════════════════════════════════════════════════════
# 3. THE BRAND'S FACE, AND ONLY ITS OWN
# ═══════════════════════════════════════════════════════════════════════════

def test_the_email_carries_the_brands_own_links_and_colours(db_session,
                                                            evosys, mail):
    org = _org(db_session, evosys, "Restland")
    _ticket(db_session, org, _user(db_session, org))
    html = mail.sent[0]["html"]
    assert "#087cff" in html                       # the brand's accent
    assert "app.evosyspro.live" in html            # the brand's app
    assert "support@evosyspro.live" in html        # the brand's reply address


def test_a_brand_without_a_logo_renders_no_logo_rather_than_borrowing_one(
        db_session, mail):
    plat = Platform(name="Plain Brand", slug="plain-%d" % next(_SEQ),
                    support_email="help@plainbrand.com", logo_url=None,
                    is_active=True)
    db_session.add(plat)
    db_session.commit()
    org = _org(db_session, plat, "Plain Customer")
    _ticket(db_session, org, _user(db_session, org))
    html = mail.sent[0]["html"]
    assert "<img" not in html
    assert "Plain Brand" in html


# ═══════════════════════════════════════════════════════════════════════════
# 4. SUPPORT LOGIC IS UNCHANGED BY ANY OF THIS
# ═══════════════════════════════════════════════════════════════════════════

def test_sla_and_thread_behaviour_are_untouched(db_session, evosys, mail):
    org = _org(db_session, evosys, "Restland")
    user = _user(db_session, org)
    ticket = _ticket(db_session, org, user)

    assert ticket.platform_id == evosys.id
    assert ticket.organization_id == org.id
    assert ticket.first_response_at is None
    before = ticket.status

    support_tickets.notify_ticket_update(
        db_session, ticket, headline="We are looking at this",
        body="An engineer has picked it up.")

    # A notification does not stop the first-response clock, change status,
    # or move the ticket. `add_message` is the only thing that may.
    assert ticket.status == before
    assert ticket.first_response_at is None
    assert mail.sent[-1]["subject"].startswith("[%s]" % ticket.ticket_number)
    assert mail.sent[-1]["from_email"] == "support@evosyspro.live"
