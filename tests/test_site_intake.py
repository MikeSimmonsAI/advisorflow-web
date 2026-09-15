"""THE MARKETING SITE'S OWN PAYLOADS, LANDING IN THE CONFIGURED WORKSPACE.

Every payload in this file is the shape the EvoSys Pro V8 site actually POSTs
from PHP - `name` in one box, `sms_consent` as the string "YES", `goals`
instead of `message`, the visitor's IP and user agent forwarded by the web
server. If this platform only accepted its own idealized schema, the site
would have to be rewritten to talk to it, and the site is the locked, approved
artefact.

What is being defended here, in order of how much it would cost to get wrong:

  1. A submission NEVER lands in a workspace nobody configured. Not the first
     organization, not one whose name matches, not the platform's own.
  2. ONE submission is ONE person. The same human through two different forms
     is one record, and a repeat of either form updates rather than duplicates.
  3. A CONSENT is filed as queryable evidence with the wording verbatim, and a
     later form never silently withdraws it.
  4. CONSENT IS NOT SALES INTENT. An opt-in does not acquire a sales tier, a
     message track, or a place in anybody's cadence.
  5. THE DEMO FORM NOTIFIES ONLY. It never starts outreach or cadence. It does
     send the brand's internal demo notice and a customer acknowledgement after
     the lead is safely persisted.
"""

import uuid

from app.models.models import Lead, Organization, Platform


SLUG = "evosyspro"


# ── fixtures ────────────────────────────────────────────────────────────────

def _brand(db, *, slug=SLUG, name="EvoSys Pro",
           website="https://evosyspro.live",
           support_email="support@evosyspro.live"):
    platform = Platform(name=name, slug=slug, website_url=website,
                        support_email=support_email)
    db.add(platform)
    db.commit()
    return platform


def _customer(db, platform, *, name="EVO Integrated Solutions LLC"):
    org = Organization(name=name, slug="cust-" + uuid.uuid4().hex[:8],
                       plan="standard", platform_id=platform.id, is_active=True)
    db.add(org)
    db.commit()
    return org


def _configured(db, **kw):
    """A brand with a verified destination - the state after a God sets one."""
    platform = _brand(db, **kw)
    org = _customer(db, platform)
    platform.public_intake_organization_id = org.id
    db.commit()
    return platform, org


# The demo form's payload, verbatim from request-demo/index.php.
DEMO = {
    "submitted_at": "2026-09-13T14:05:00+00:00",
    "name": "Dana Whitfield",
    "company": "Whitfield Roofing",
    "email": "dana@whitfieldroofing.example",
    "phone": "(214) 555-0142",
    "industry": "Roofing / Construction",
    "locations": "2–5",
    "leads": "2,500–5,000",
    "current_system": "spreadsheets",
    "goals": "Follow up faster on old quotes.",
    "package": "growth",
    "sms_consent": "NO",
    "sms_consent_text": "By checking this box, I agree to receive SMS…",
    "ip": "203.0.113.44",
    "user_agent": "Mozilla/5.0 (Macintosh)",
}

CONSENT_WORDING = (
    "By checking this box, I agree to receive SMS/text messages from EvoSys "
    "Pro including appointment confirmations, reminders, and related service "
    "follow-ups. Message frequency varies. Message & data rates may apply. "
    "Reply STOP to unsubscribe, HELP for help. Consent is not a condition of "
    "any purchase or service."
)

# The opt-in page's payload, verbatim from sms-optin/index.php.
OPTIN = {
    "submitted_at": "2026-09-13T14:09:00+00:00",
    "name": "Dana Whitfield",
    "phone": "214-555-0142",
    "email": "dana@whitfieldroofing.example",
    "consent": "YES",
    "consent_text": CONSENT_WORDING,
    "consent_version": "2026-09",
    "source_url": "https://evosyspro.live/sms-optin/",
    "ip": "203.0.113.44",
    "user_agent": "Mozilla/5.0 (Macintosh)",
}


def _post(client, kind, body, slug=SLUG):
    return client.post("/site-intake/%s/%s" % (slug, kind), json=body)


# ── 1. the destination is configured, never guessed ─────────────────────────

def test_a_demo_request_lands_in_the_configured_organization(client, db_session):
    _, org = _configured(db_session)
    r = _post(client, "demo-request", DEMO)
    assert r.status_code == 201, r.text
    lead = db_session.query(Lead).one()
    assert lead.organization_id == org.id
    assert r.json()["action"] == "created"


def test_an_unconfigured_brand_refuses_and_writes_nothing(client, db_session):
    _brand(db_session)
    r = _post(client, "demo-request", DEMO)
    assert r.status_code == 503
    assert db_session.query(Lead).count() == 0


def test_an_unknown_brand_refuses_exactly_like_an_unconfigured_one(
        client, db_session):
    """NO MAP OF THE PLATFORM FOR AN ANONYMOUS POSTER. A slug nobody has and a
    slug nobody configured must be indistinguishable from outside, or the 503
    becomes a brand directory anyone can enumerate."""
    _brand(db_session)
    unknown = _post(client, "demo-request", DEMO, slug="not-a-brand")
    unconfigured = _post(client, "demo-request", DEMO)
    assert unknown.status_code == unconfigured.status_code == 503
    assert unknown.json() == unconfigured.json()
    assert db_session.query(Lead).count() == 0


def test_a_destination_belonging_to_another_brand_refuses(client, db_session):
    """CROSS-BRAND BLEED. A misconfiguration must not quietly cross it."""
    other = _brand(db_session, slug="other", name="Other Brand",
                   website="https://other.example")
    stranger = _customer(db_session, other, name="Someone Else's Customer")
    mine = _brand(db_session)
    mine.public_intake_organization_id = stranger.id
    db_session.commit()

    assert _post(client, "demo-request", DEMO).status_code == 503
    assert db_session.query(Lead).count() == 0


def test_an_inactive_destination_refuses(client, db_session):
    platform, org = _configured(db_session)
    org.is_active = False
    db_session.commit()
    assert _post(client, "demo-request", DEMO).status_code == 503
    assert db_session.query(Lead).count() == 0


def test_naming_a_brand_grants_nothing_beyond_its_own_configuration(
        client, db_session):
    """The slug in the path is a NAME, not authority. Two brands, each with its
    own destination: naming one can only ever reach the organization an
    operator configured for that one."""
    a_platform, a_org = _configured(db_session, slug="brand-a", name="Brand A",
                                    website="https://a.example")
    b_platform, b_org = _configured(db_session, slug="brand-b", name="Brand B",
                                    website="https://b.example")

    assert _post(client, "demo-request", DEMO, slug="brand-b").status_code == 201
    lead = db_session.query(Lead).one()
    assert lead.organization_id == b_org.id
    assert lead.organization_id != a_org.id


# ── 2. one submission is one person ─────────────────────────────────────────

def test_a_repeat_demo_request_updates_rather_than_duplicating(
        client, db_session):
    _configured(db_session)
    assert _post(client, "demo-request", DEMO).status_code == 201
    again = _post(client, "demo-request", {**DEMO, "goals": "Second attempt."})
    assert again.status_code == 201
    assert again.json()["action"] == "updated"
    assert db_session.query(Lead).count() == 1
    assert "Second attempt." in db_session.query(Lead).one().notes


def test_the_same_email_in_a_different_case_is_the_same_person(
        client, db_session):
    _configured(db_session)
    _post(client, "demo-request", {**DEMO, "phone": ""})
    _post(client, "demo-request", {**DEMO, "phone": "",
                                   "email": DEMO["email"].upper()})
    assert db_session.query(Lead).count() == 1


def test_the_same_phone_written_differently_is_the_same_person(
        client, db_session):
    """"(214) 555-0142" and "214-555-0142" are one handset. Before one shared
    matching rule, the demo form compared the raw string and the opt-in page
    compared the normalized one, so these became two rows."""
    _configured(db_session)
    _post(client, "demo-request", {**DEMO, "email": ""})
    _post(client, "demo-request", {**DEMO, "email": "", "phone": "+1 214 555 0142"})
    assert db_session.query(Lead).count() == 1


def test_a_demo_request_and_an_optin_from_one_person_are_one_record(
        client, db_session):
    """THE WHOLE POINT OF A SHARED CAPTURE. Same human, two forms, one row -
    and the consent from the second attaches to the person named by the first."""
    _configured(db_session)
    assert _post(client, "demo-request", DEMO).status_code == 201
    assert _post(client, "sms-optin", OPTIN).status_code == 201
    lead = db_session.query(Lead).one()
    assert lead.sms_consent is True
    # The demo request's own answers are still on the record the opt-in
    # updated — the second form joined the first person, it did not replace them.
    assert "Whitfield Roofing" in (lead.notes or "")
    assert lead.first_name == "Dana" and lead.last_name == "Whitfield"


def test_a_repeat_submission_never_blanks_a_field_it_left_empty(
        client, db_session):
    _configured(db_session)
    _post(client, "demo-request", DEMO)
    _post(client, "demo-request", {**DEMO, "name": "", "email": ""})
    lead = db_session.query(Lead).one()
    assert lead.first_name == "Dana"
    assert lead.last_name == "Whitfield"
    assert lead.email == DEMO["email"]


# ── 3. provenance ───────────────────────────────────────────────────────────

def test_source_is_the_brand_and_source_detail_is_the_form(client, db_session):
    _configured(db_session)
    _post(client, "demo-request", DEMO)
    lead = db_session.query(Lead).one()
    assert lead.source == "EvoSys Pro Website"
    assert lead.source_detail == "Request Demo"
    assert lead.source_category == "web_form"


def test_each_form_is_distinguishable_by_source_detail(client, db_session):
    _configured(db_session)
    _post(client, "demo-request", DEMO)
    assert db_session.query(Lead).one().source_detail == "Request Demo"
    _post(client, "sms-optin", OPTIN)
    assert db_session.query(Lead).one().source_detail == "SMS Opt-In"
    _post(client, "waitlist", {"name": "Dana Whitfield", "phone": DEMO["phone"]})
    assert db_session.query(Lead).one().source_detail == "Waitlist"


def test_the_source_name_follows_the_brand_and_is_not_hard_coded(
        client, db_session):
    _configured(db_session, slug="somebrand", name="Some Other Brand",
                website="https://other.example")
    _post(client, "demo-request", DEMO, slug="somebrand")
    assert db_session.query(Lead).one().source == "Some Other Brand Website"


def test_the_form_answers_with_no_column_survive_verbatim(client, db_session):
    _configured(db_session)
    _post(client, "demo-request", DEMO)
    notes = db_session.query(Lead).one().notes or ""
    for fragment in ("Whitfield Roofing", "Roofing / Construction",
                     "spreadsheets", "growth", "Follow up faster"):
        assert fragment in notes, fragment


def test_utm_parameters_are_kept_when_the_site_collects_them(client, db_session):
    _configured(db_session)
    _post(client, "demo-request", {**DEMO, "utm_source": "google",
                                   "utm_campaign": "roofing-q3"})
    lead = db_session.query(Lead).one()
    assert "google" in (lead.notes or "")
    assert "roofing-q3" in (lead.custom_fields or "")


def test_an_unrecognised_form_field_is_kept_rather_than_rejected(
        client, db_session):
    """A copy edit on a marketing site must never become a lost prospect."""
    _configured(db_session)
    r = _post(client, "demo-request", {**DEMO, "how_did_you_hear": "a podcast"})
    assert r.status_code == 201, r.text
    assert "a podcast" in (db_session.query(Lead).one().notes or "")


# ── 4. consent as evidence ──────────────────────────────────────────────────

def test_an_optin_files_queryable_consent_evidence(client, db_session):
    """THE COLUMNS, NOT A SENTENCE IN A NOTE. These are what a send path checks
    and what a carrier or TCPA dispute is argued from."""
    _configured(db_session)
    assert _post(client, "sms-optin", OPTIN).status_code == 201
    lead = db_session.query(Lead).one()
    assert lead.sms_consent is True
    assert lead.sms_consent_timestamp is not None
    assert lead.sms_consent_ip == "203.0.113.44"
    assert lead.sms_consent_text == CONSENT_WORDING
    assert "SMS Opt-In" in (lead.sms_consent_source or "")
    assert "2026-09" in (lead.sms_consent_source or "")
    assert "Mozilla/5.0 (Macintosh)" in (lead.custom_fields or "")


def test_the_consent_wording_is_stored_verbatim_not_paraphrased(
        client, db_session):
    _configured(db_session)
    _post(client, "sms-optin", OPTIN)
    assert db_session.query(Lead).one().sms_consent_text == CONSENT_WORDING


def test_the_optin_records_the_site_visitor_not_the_web_server(
        client, db_session):
    """The request reaching this platform comes from the marketing site's web
    server. Filing ITS address as the consent IP would file the wrong evidence,
    so the visitor's address as the site saw it is what is stored."""
    _configured(db_session)
    _post(client, "sms-optin", OPTIN)
    assert db_session.query(Lead).one().sms_consent_ip == OPTIN["ip"]


def test_an_unticked_box_is_not_consent(client, db_session):
    _configured(db_session)
    r = _post(client, "sms-optin", {**OPTIN, "consent": "NO"})
    assert r.status_code == 400
    assert db_session.query(Lead).count() == 0


def test_an_unparseable_consent_value_is_not_consent(client, db_session):
    _configured(db_session)
    assert _post(client, "sms-optin",
                 {**OPTIN, "consent": "maybe"}).status_code == 400
    assert db_session.query(Lead).count() == 0


def test_the_optional_box_on_the_demo_form_is_recorded_when_ticked(
        client, db_session):
    _configured(db_session)
    _post(client, "demo-request", {**DEMO, "sms_consent": "YES",
                                   "sms_consent_text": CONSENT_WORDING})
    lead = db_session.query(Lead).one()
    assert lead.sms_consent is True
    assert lead.sms_consent_text == CONSENT_WORDING
    assert "Request Demo" in (lead.sms_consent_source or "")


def test_a_later_form_never_withdraws_a_consent_already_given(
        client, db_session):
    """A FORM IS NOT A REVOCATION CHANNEL. Somebody who opted in last month and
    fills in a demo form today without ticking the optional box has not
    withdrawn anything."""
    _configured(db_session)
    _post(client, "sms-optin", OPTIN)
    _post(client, "demo-request", {**DEMO, "sms_consent": "NO"})
    lead = db_session.query(Lead).one()
    assert lead.sms_consent is True
    assert lead.sms_consent_text == CONSENT_WORDING


def test_a_suppressed_number_cannot_opt_back_in_through_a_web_form(
        client, db_session):
    """THE DO-NOT-CONTACT LIST OUTRANKS AN ANONYMOUS FORM."""
    from app.services.compliance_service import add_suppression_entry
    from app.services.dedup_service import normalize_phone
    _, org = _configured(db_session)
    add_suppression_entry(db_session, org.id, normalize_phone(OPTIN["phone"]),
                          reason="asked to stop")
    db_session.commit()
    r = _post(client, "sms-optin", OPTIN)
    assert r.status_code == 409
    assert db_session.query(Lead).count() == 0


# ── 5. consent is not sales intent ──────────────────────────────────────────

def test_an_optin_is_not_given_a_sales_tier_or_message_track(
        client, db_session):
    """Somebody who agreed to appointment reminders has not asked to be sold
    to. A platform that reads the two as the same thing starts selling on the
    strength of a compliance checkbox."""
    _configured(db_session)
    _post(client, "sms-optin", OPTIN)
    lead = db_session.query(Lead).one()
    assert not (lead.tier or "")
    assert not (lead.message_track or "")


def test_a_demo_request_does_carry_sales_intent(client, db_session):
    _configured(db_session)
    _post(client, "demo-request", DEMO)
    lead = db_session.query(Lead).one()
    assert lead.tier == "web_lead"
    assert lead.message_track == "new_inquiry_intro"


def test_a_demo_request_sends_internal_and_customer_notifications(
        client, db_session, monkeypatch):
    platform, _ = _configured(db_session)
    sent = []

    def fake_send(to_email, subject, body_html, **kwargs):
        sent.append({
            "to": to_email,
            "subject": subject,
            "html": body_html,
            "org": kwargs.get("org"),
        })
        return {"success": True, "provider_message_id": "msg_123",
                "error": None}

    monkeypatch.setattr("app.services.email_service.send_email_via_provider",
                        fake_send)
    r = _post(client, "demo-request", DEMO)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["notifications"] == {"internal": True, "customer": True}
    assert db_session.query(Lead).count() == 1
    assert len(sent) == 2

    internal, customer = sent
    assert internal["to"] == "support@evosyspro.live"
    assert "New EvoSys Pro Demo Request" in internal["subject"]
    for text in ("Dana Whitfield", "Whitfield Roofing", DEMO["email"],
                 DEMO["phone"], "growth", "spreadsheets",
                 "Follow up faster"):
        assert text in internal["html"]
    assert customer["to"] == DEMO["email"]
    assert customer["subject"] == "EvoSys Pro — We Received Your Demo Request"
    assert "We received your demo request" in customer["html"]
    assert sent[0]["org"].from_email == platform.support_email


def test_a_repeat_demo_request_does_not_create_duplicate_leads_or_emails(
        client, db_session, monkeypatch):
    _configured(db_session)
    sent = []
    monkeypatch.setattr(
        "app.services.email_service.send_email_via_provider",
        lambda to_email, subject, body_html, **kwargs:
            sent.append((to_email, subject)) or
            {"success": True, "provider_message_id": "msg", "error": None},
    )
    assert _post(client, "demo-request", DEMO).status_code == 201
    assert _post(client, "demo-request", {**DEMO, "goals": "Second attempt."}).status_code == 201
    assert db_session.query(Lead).count() == 1
    assert len(sent) == 4
    assert sum(1 for _to, subj in sent if "New EvoSys Pro Demo Request" in subj) == 2
    assert sum(1 for _to, subj in sent if subj == "EvoSys Pro — We Received Your Demo Request") == 2


def test_demo_notification_failure_keeps_the_lead(client, db_session, monkeypatch):
    _configured(db_session)

    def fail_send(*args, **kwargs):
        return {"success": False, "provider_message_id": None,
                "error": "simulated provider failure"}

    monkeypatch.setattr("app.services.email_service.send_email_via_provider",
                        fail_send)
    r = _post(client, "demo-request", DEMO)
    assert r.status_code == 201, r.text
    assert r.json()["notifications"] == {"internal": False, "customer": False}
    lead = db_session.query(Lead).one()
    assert lead.email == DEMO["email"]
    assert lead.source_detail == "Request Demo"


def test_an_optin_never_raises_a_lead_a_seller_has_already_advanced(
        client, db_session):
    _configured(db_session)
    _post(client, "demo-request", DEMO)
    lead = db_session.query(Lead).one()
    lead.status = "booked"
    db_session.commit()
    _post(client, "sms-optin", OPTIN)
    db_session.refresh(lead)
    assert lead.status == "booked"


def test_a_waitlist_signup_is_not_a_demo_request(client, db_session):
    _configured(db_session)
    _post(client, "waitlist", {"name": "Dana Whitfield",
                               "email": DEMO["email"]})
    lead = db_session.query(Lead).one()
    assert lead.source_detail == "Waitlist"
    assert not (lead.tier or "")


# ── 6. nothing is sent ──────────────────────────────────────────────────────

def test_no_cadence_is_started_by_any_public_form(client, db_session):
    """NO AUTOMATIC OUTREACH. A website submission is a record, not a trigger."""
    from app.models.models import CadenceState
    _configured(db_session)
    _post(client, "demo-request", DEMO)
    _post(client, "sms-optin", OPTIN)
    _post(client, "waitlist", {"name": "Someone Else",
                               "phone": "(214) 555-0199"})
    assert db_session.query(CadenceState).count() == 0


def test_no_message_is_queued_by_any_public_form(client, db_session):
    from app.models.models import Message
    _configured(db_session)
    _post(client, "demo-request", DEMO)
    _post(client, "sms-optin", OPTIN)
    assert db_session.query(Message).count() == 0


# ── 7. validation fails cleanly ─────────────────────────────────────────────

def test_a_submission_nobody_can_reply_to_is_refused(client, db_session):
    _configured(db_session)
    r = _post(client, "demo-request", {"name": "Dana Whitfield"})
    assert r.status_code == 422
    assert db_session.query(Lead).count() == 0


def test_a_nameless_demo_request_is_refused(client, db_session):
    _configured(db_session)
    r = _post(client, "demo-request", {**DEMO, "name": ""})
    assert r.status_code == 422
    assert db_session.query(Lead).count() == 0


def test_an_optin_without_a_usable_phone_is_refused(client, db_session):
    _configured(db_session)
    r = _post(client, "sms-optin", {**OPTIN, "phone": "not a number"})
    assert r.status_code == 422
    assert db_session.query(Lead).count() == 0


def test_validation_runs_after_the_destination_is_known(client, db_session):
    """An unconfigured brand refuses BEFORE the payload is judged, so a
    malformed submission to a closed brand never discloses that the brand is
    merely misconfigured rather than unknown."""
    _brand(db_session)
    assert _post(client, "demo-request", {}).status_code == 503


def test_an_empty_body_is_refused_rather_than_stored(client, db_session):
    _configured(db_session)
    assert _post(client, "demo-request", {}).status_code == 422
    assert db_session.query(Lead).count() == 0


# ── 8. the credentialed path is untouched ───────────────────────────────────

def test_the_credentialed_inbound_route_is_still_credentialed(client, db_session):
    """`POST /crm/inbound/{org_id}` names an organization in its path and is
    protected by a per-organization key. Nothing in this work loosened it, and
    an anonymous caller must still be refused."""
    _, org = _configured(db_session)
    r = client.post("/crm/inbound/%s" % org.id,
                    json={"first_name": "Dana", "phone": "2145550142"})
    assert r.status_code in (401, 403), r.text


def test_the_site_intake_route_never_accepts_an_organization_id(
        client, db_session):
    """AN ORG ID IS NOT AUTHORIZATION, AND IS NOT A DESTINATION. Sending one
    must change nothing: the configured destination still decides."""
    platform, org = _configured(db_session)
    other = _customer(db_session, platform, name="Not The Destination")
    r = _post(client, "demo-request",
              {**DEMO, "organization_id": other.id, "org_id": other.id})
    assert r.status_code == 201, r.text
    assert db_session.query(Lead).one().organization_id == org.id
