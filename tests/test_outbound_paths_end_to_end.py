"""SS10 — THE CHAIN, PROVEN LINK BY LINK, FOR EVERY GATED SOURCE.

The other four SS10 files each prove one thing well. This one is the matrix:
for every source that was dead and is now repaired, it walks the whole path in
one test and asserts each link in order.

    REQUEST OR EVENT
      -> ORG SCOPE          the gate reads the LEAD's organization, never the
                            caller's, so a cross-tenant lead is refused before
                            anything else happens
      -> SOURCE GATE        the deployment switch for THIS source, and no other
      -> ORG GATE           this customer's own list, independently
      -> COMPLIANCE         DNC, suppression, capacity, permission - and it
                            runs BEFORE either switch, so a blocked family is
                            blocked whether or not the feature is on
      -> TEMPLATE/CONTENT   a subject and a body, refused if half-formed
      -> PROVIDER ADAPTER   reached only when every gate above cleared
      -> RESULT             the provider's own words, never a generic one
      -> HISTORY            an email_messages row the timeline actually reads
      -> ACTOR              who pressed the button, or NULL for a job
      -> SEND SOURCE        from the one vocabulary, never a bare literal
      -> FAILURE/SUCCESS    a refusal raises; a success is the only thing that
                            sets a sent flag or advances a counter

AND FIVE PROPERTIES THAT MUST HOLD FOR ALL OF THEM AT ONCE:

    no swallowed exception      a failure reaches the caller
    no fake success             nothing records "sent" that was not sent
    no provider call when off   which is the shipped state of all five
    no backlog replay           enabling a source does not mail history
    no dead CTA                 a link in the body resolves to a real route

EVERY SWITCH IN THIS FILE IS TURNED ON BY MONKEYPATCH FOR THE LENGTH OF ONE
TEST, AND THE PROVIDER IS ALWAYS A MOCK. The last test asserts the file left
nothing on.
"""

import ast
import inspect
import json
from unittest.mock import MagicMock, patch

import pytest

from app.models.models import EmailMessage, Lead, Organization, User
from app.services import outbound_email_gate as gate
from app.services import send_source as src
from app.services.auth_service import hash_password

# The five that were dead. Each is (source, the env var that governs it).
GATED = [
    (src.BULK_AI, "OUTBOUND_EMAIL_BULK_AI"),
    (src.VOICE_BOOKING_LINK, "OUTBOUND_EMAIL_VOICE_BOOKING_LINK"),
    (src.PIPELINE_AUTO_REPLY, "OUTBOUND_EMAIL_PIPELINE_AUTO_REPLY"),
    (src.APPOINTMENT_FOLLOWUP, "OUTBOUND_EMAIL_APPOINTMENT_FOLLOWUP"),
    (gate.STAFF_ESCALATION, "OUTBOUND_EMAIL_STAFF_ESCALATION"),
    # The public Discovery / Demo booking paths. Three of them, because a brand
    # may want its sales team notified about website bookings without a single
    # prospect being emailed - and because confirmations and automated
    # reminders are separate decisions with different blast radii.
    (gate.PUBLIC_BOOKING_CONFIRMATION,
     "OUTBOUND_EMAIL_PUBLIC_BOOKING_CONFIRMATION"),
    (gate.PUBLIC_BOOKING_INTERNAL, "OUTBOUND_EMAIL_PUBLIC_BOOKING_INTERNAL"),
    (gate.PUBLIC_BOOKING_REMINDERS, "OUTBOUND_EMAIL_PUBLIC_BOOKING_REMINDERS"),
]

# Sources that do NOT contact a customer's lead, and so have no Lead to run the
# compliance preflight against. Staff escalation goes to one of our own people;
# the booking paths go to a brand-sales prospect or to the sales team, neither
# of which is a family in a customer's tenant.
NON_LEAD_SOURCES = (gate.STAFF_ESCALATION,) + gate.PUBLIC_BOOKING_SOURCES
LEAD_SOURCES = [s for s, _ in GATED if s not in NON_LEAD_SOURCES]


def _lead(db_session, org, advisor, **kw):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name=kw.pop("first_name", "Chain"), last_name="Lead",
                email=kw.pop("email", "chain@example.com"),
                phone=kw.pop("phone", None), status=kw.pop("status", "new"), **kw)
    db_session.add(lead)
    db_session.commit()
    return lead


def _all_off(monkeypatch):
    for _, env in GATED:
        monkeypatch.delenv(env, raising=False)


def _both_on(monkeypatch, db_session, org, source):
    _all_off(monkeypatch)
    monkeypatch.setenv(dict(GATED)[source], "true")
    if source != gate.STAFF_ESCALATION:
        org.outbound_email_sources = json.dumps([source])
        db_session.commit()


def _ok():
    return {"success": True, "provider_message_id": "em_chain", "error": None}


# ═══════════════════════════════════════════════════════════════════════════
# THE SHIPPED POSTURE. This is the state of the deployment right now.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("source,env", GATED)
def test_every_source_ships_off_and_reaches_no_provider(
        source, env, db_session, sample_org, sample_advisor, monkeypatch):
    _all_off(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch("app.services.email_service.send_email_via_provider") as provider:
        with pytest.raises(Exception) as caught:
            if source == gate.STAFF_ESCALATION:
                gate.gate_staff_email("ops@example.com", purpose="test")
            else:
                gate.send_lead_email(db_session, lead, advisor=sample_advisor,
                                     subject="s", body_html="<p>b</p>",
                                     send_source=source)
    provider.assert_not_called()
    # THE REFUSAL NAMES ITS OWN SWITCH. A refusal that does not say which
    # variable to set is a support ticket.
    assert env in str(caught.value)


def test_no_email_row_is_written_by_a_refusal(
        db_session, sample_org, sample_advisor, monkeypatch):
    """A refusal that left a history row would read as a send that failed."""
    _all_off(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor)
    before = db_session.query(EmailMessage).count()
    for source in LEAD_SOURCES:
        with pytest.raises(Exception):
            gate.send_lead_email(db_session, lead, advisor=sample_advisor,
                                 subject="s", body_html="<p>b</p>",
                                 send_source=source)
    assert db_session.query(EmailMessage).count() == before


# ═══════════════════════════════════════════════════════════════════════════
# LINK 1 — ORG SCOPE. Before any switch is even consulted.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("source", LEAD_SOURCES)
def test_another_tenants_switch_does_not_open_this_ones_gate(
        source, db_session, sample_org, sample_advisor, monkeypatch):
    """The gate reads the LEAD's organization. A customer who has enabled a
    source must not open it for a customer who has not."""
    monkeypatch.setenv(dict(GATED)[source], "true")
    other = Organization(name="Somebody Else", slug="chain-else",
                         plan="standard", industry="funeral")
    db_session.add(other)
    db_session.commit()
    other_advisor = User(organization_id=other.id, email="them@chain.test",
                         password_hash=hash_password("TestPass123!"),
                         full_name="Them", role="advisor",
                         must_change_password=False)
    db_session.add(other_advisor)
    db_session.commit()

    # THIS org turns it on. The other one does not.
    sample_org.outbound_email_sources = json.dumps([source])
    other.outbound_email_sources = None
    db_session.commit()

    foreign = _lead(db_session, other, other_advisor, email="f@chain.test")
    with patch("app.services.email_service.send_email_via_provider") as provider:
        with pytest.raises(gate.EmailSendDisabled):
            gate.send_lead_email(db_session, foreign, advisor=other_advisor,
                                 subject="s", body_html="<p>b</p>",
                                 send_source=source)
    provider.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# LINK 2/3 — THE TWO SWITCHES, INDEPENDENTLY. Both must say yes.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("source", LEAD_SOURCES)
def test_the_deployment_switch_alone_sends_nothing(
        source, db_session, sample_org, sample_advisor, monkeypatch):
    _all_off(monkeypatch)
    monkeypatch.setenv(dict(GATED)[source], "true")
    sample_org.outbound_email_sources = None      # the customer has not opted in
    db_session.commit()
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch("app.services.email_service.send_email_via_provider") as provider:
        with pytest.raises(gate.EmailSendDisabled):
            gate.send_lead_email(db_session, lead, advisor=sample_advisor,
                                 subject="s", body_html="<p>b</p>",
                                 send_source=source)
    provider.assert_not_called()


@pytest.mark.parametrize("source", LEAD_SOURCES)
def test_the_org_switch_alone_sends_nothing(
        source, db_session, sample_org, sample_advisor, monkeypatch):
    _all_off(monkeypatch)
    sample_org.outbound_email_sources = json.dumps([source])
    db_session.commit()
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch("app.services.email_service.send_email_via_provider") as provider:
        with pytest.raises(gate.EmailSendDisabled):
            gate.send_lead_email(db_session, lead, advisor=sample_advisor,
                                 subject="s", body_html="<p>b</p>",
                                 send_source=source)
    provider.assert_not_called()


@pytest.mark.parametrize("source", LEAD_SOURCES)
def test_one_source_on_does_not_open_the_others(
        source, db_session, sample_org, sample_advisor, monkeypatch):
    _both_on(monkeypatch, db_session, sample_org, source)
    lead = _lead(db_session, sample_org, sample_advisor)
    for other in LEAD_SOURCES:
        if other == source:
            continue
        with patch("app.services.email_service.send_email_via_provider") as provider:
            with pytest.raises(gate.EmailSendDisabled):
                gate.send_lead_email(db_session, lead, advisor=sample_advisor,
                                     subject="s", body_html="<p>b</p>",
                                     send_source=other)
        provider.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# LINK 4 — COMPLIANCE, AHEAD OF BOTH SWITCHES.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("source", LEAD_SOURCES)
def test_a_dnc_family_is_refused_with_every_switch_on(
        source, db_session, sample_org, sample_advisor, monkeypatch):
    _both_on(monkeypatch, db_session, sample_org, source)
    lead = _lead(db_session, sample_org, sample_advisor, status="dnc")

    with patch("app.services.email_service.send_email_via_provider") as provider:
        with pytest.raises(ValueError):
            gate.send_lead_email(db_session, lead, advisor=sample_advisor,
                                 subject="s", body_html="<p>b</p>",
                                 send_source=source)
    provider.assert_not_called()


@pytest.mark.parametrize("source", LEAD_SOURCES)
def test_compliance_answers_before_the_switch_does(
        source, db_session, sample_org, sample_advisor, monkeypatch):
    """With everything OFF and a DNC lead, the refusal must be the compliance
    one. Order matters: a family who has opted out is refused for that reason
    whether or not the feature is on, and turning the feature on later must not
    change what they are told."""
    _all_off(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, status="dnc")
    with pytest.raises(ValueError) as caught:
        gate.send_lead_email(db_session, lead, advisor=sample_advisor,
                             subject="s", body_html="<p>b</p>",
                             send_source=source)
    assert not isinstance(caught.value, gate.EmailSendDisabled)


# ═══════════════════════════════════════════════════════════════════════════
# LINKS 5-11 — CONTENT, PROVIDER, RESULT, HISTORY, ACTOR, SOURCE, STATE.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("source", LEAD_SOURCES)
def test_the_whole_chain_completes_and_records_itself(
        source, db_session, sample_org, sample_advisor, monkeypatch):
    _both_on(monkeypatch, db_session, sample_org, source)
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch("app.services.email_service.send_email_via_provider",
               return_value=_ok()) as provider:
        row = gate.send_lead_email(
            db_session, lead, advisor=sample_advisor,
            subject="Your arrangements", body_html="<p>Hello</p>",
            send_source=source, actor_user_id=sample_advisor.id)

    provider.assert_called_once()
    # HISTORY: the row the timeline, the activity feed and the sent log read.
    assert isinstance(row, EmailMessage)
    stored = db_session.query(EmailMessage).filter(
        EmailMessage.id == row.id).one()
    # SEND SOURCE, from the vocabulary rather than a literal.
    assert stored.send_source == source
    assert src.is_valid(stored.send_source)
    # ACTOR: who pressed the button.
    assert stored.sent_by_user_id == sample_advisor.id


@pytest.mark.parametrize("source", LEAD_SOURCES)
def test_a_job_with_no_human_records_null_rather_than_a_name(
        source, db_session, sample_org, sample_advisor, monkeypatch):
    """The house rule, the same one the audit log follows: an event with no
    human actor is recorded as having none. It is never forged."""
    _both_on(monkeypatch, db_session, sample_org, source)
    lead = _lead(db_session, sample_org, sample_advisor)
    with patch("app.services.email_service.send_email_via_provider",
               return_value=_ok()):
        row = gate.send_lead_email(db_session, lead, advisor=sample_advisor,
                                   subject="s", body_html="<p>b</p>",
                                   send_source=source, actor_user_id=None)
    assert row.sent_by_user_id is None


@pytest.mark.parametrize("source", LEAD_SOURCES)
def test_a_provider_refusal_raises_in_the_providers_own_words(
        source, db_session, sample_org, sample_advisor, monkeypatch):
    """NO SWALLOWED EXCEPTION, AND NO GENERIC ONE EITHER. The four paths were
    dead for months because an ImportError was being caught and reported as a
    generic failure; 'domain not verified' is the sentence that would have
    ended it in an afternoon."""
    _both_on(monkeypatch, db_session, sample_org, source)
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch("app.services.email_service.send_email_via_provider",
               return_value={"success": False, "provider_message_id": None,
                             "error": "domain not verified"}):
        with pytest.raises(Exception) as caught:
            gate.send_lead_email(db_session, lead, advisor=sample_advisor,
                                 subject="s", body_html="<p>b</p>",
                                 send_source=source)
    assert "domain not verified" in str(caught.value)


@pytest.mark.parametrize("subject,body", [
    ("", ""),                       # the bulk-AI failure mode, exactly
    ("A subject", ""),
    ("", "<p>A body</p>"),
    ("   ", "<p>A body</p>"),       # whitespace is not a subject
])
def test_an_empty_draft_is_refused_rather_than_mailed_blank(
        subject, body, db_session, sample_org, sample_advisor, monkeypatch):
    """AN EMPTY DRAFT IS NOT A DRAFT.

    The guard tested `is None`, so a present empty string passed as a
    well-formed drafted send. That is precisely what bulk AI compose produced
    when it read `result.message` and the API returned `reply` - the screen
    said "Sent" and the family would have received a blank email from their
    funeral home."""
    _both_on(monkeypatch, db_session, sample_org, src.BULK_AI)
    lead = _lead(db_session, sample_org, sample_advisor)
    with patch("app.services.email_service.send_email_via_provider") as provider:
        with pytest.raises(ValueError, match="blank|both subject and body"):
            gate.send_lead_email(db_session, lead, advisor=sample_advisor,
                                 subject=subject, body_html=body,
                                 send_source=src.BULK_AI)
    provider.assert_not_called()


def test_a_real_draft_still_sends(db_session, sample_org, sample_advisor,
                                  monkeypatch):
    """The guard above must not have made drafted sends impossible."""
    _both_on(monkeypatch, db_session, sample_org, src.BULK_AI)
    lead = _lead(db_session, sample_org, sample_advisor)
    with patch("app.services.email_service.send_email_via_provider",
               return_value=_ok()) as provider:
        row = gate.send_lead_email(db_session, lead, advisor=sample_advisor,
                                   subject="Following up",
                                   body_html="<p>Hello</p>",
                                   send_source=src.BULK_AI)
    provider.assert_called_once()
    assert row.send_source == src.BULK_AI


# ═══════════════════════════════════════════════════════════════════════════
# PROPERTIES THAT MUST HOLD FOR ALL FIVE AT ONCE
# ═══════════════════════════════════════════════════════════════════════════

def test_the_gate_module_swallows_nothing():
    """A bare `except: pass` anywhere in the gate would be the same class of
    bug as the one that killed these five paths in the first place."""
    tree = ast.parse(inspect.getsource(gate))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body = [n for n in node.body if not isinstance(n, ast.Expr)
                or not isinstance(n.value, ast.Constant)]
        silent = all(isinstance(n, ast.Pass) for n in body) if body else True
        assert not silent, ("outbound_email_gate swallows an exception at line %d"
                            % node.lineno)


def test_the_gate_functions_reach_no_provider_themselves():
    """The gate decides. It does not send. Anything that can put a message on
    the wire lives behind it, so a gate that cleared cannot also have sent."""
    for fn in (gate.gate_lead_email, gate.gate_staff_email):
        tree = ast.parse(inspect.getsource(fn))
        names = {getattr(n.func, "id", None) or getattr(n.func, "attr", None)
                 for n in ast.walk(tree) if isinstance(n, ast.Call)}
        for forbidden in ("send_email_via_provider", "_send_email_resend",
                          "send_email_to_lead", "send"):
            assert forbidden not in names, "%s calls %s" % (fn.__name__, forbidden)


def test_every_gated_source_is_in_the_one_vocabulary():
    """Every gated source is one of exactly two things, and nothing else.

    Either it is a real `send_source` - meaning an email on it writes a row of
    lead communication history against a customer's family - or it is in
    NON_LEAD_SOURCES, meaning it contacts nobody's lead and there is no history
    to write. The staff escalation alert goes to one of our own people; the
    three public-booking paths go to a brand-sales prospect or to the sales
    team, and a brand-sales prospect is not a customer's lead.

    Stated as a rule rather than as a growing list of exemptions, because the
    thing that must not happen is a source that is neither: one that contacts a
    family WITHOUT writing history, which is how outbound activity becomes
    unauditable.
    """
    for source, _ in GATED:
        assert source in NON_LEAD_SOURCES or src.is_valid(source), (
            "%r is a gated source that is neither a send_source nor declared "
            "as contacting no lead" % source)


def test_there_is_exactly_one_variable_per_source():
    envs = [env for _, env in GATED]
    assert len(set(envs)) == len(envs), "two sources share a switch"
    assert set(gate._ENV_BY_SOURCE.values()) == set(envs)


def test_this_file_left_every_switch_off(monkeypatch):
    import os
    for _, env in GATED:
        assert os.environ.get(env) in (None, ""), "%s was left on" % env
