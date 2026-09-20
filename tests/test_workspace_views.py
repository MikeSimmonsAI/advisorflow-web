"""Configured workflow screens: what they show, and what they refuse to show.

The reason this file exists is the second half. A view is CONFIGURATION — a
row on an organization, or a default on an industry — and configuration that
can widen who sees what is not configuration, it is an authorization bug with
a JSON syntax. Every test below that starts "a view cannot" is guarding that
property, and none of them should ever be relaxed to make a screen render.
"""

import json
from datetime import datetime, timedelta

from app.models.models import BookingLink, Lead, Organization, User
from app.services import workspace_views as wv
from app.services.auth_service import create_access_token, hash_password


def _headers(db_session, user):
    return {"Authorization": f"Bearer {create_access_token(user, db_session)}"}


def _org(db_session, name, slug, industry="energy", views=None):
    org = Organization(name=name, slug=slug, plan="standard", industry=industry)
    if views is not None:
        org.workspace_views = views
    db_session.add(org)
    db_session.commit()
    return org


def _admin(db_session, org, email):
    user = User(organization_id=org.id, email=email,
                password_hash=hash_password("TestPass123!"),
                full_name="Org Admin", role="org_admin",
                must_change_password=False)
    db_session.add(user)
    db_session.commit()
    return user


def _lead(db_session, org, *, tier="rate_review", status="new", owner=None,
          first="Rate", last="Payer", phone="12145550101"):
    lead = Lead(organization_id=org.id, first_name=first, last_name=last,
                phone=phone, phone_raw=phone, tier=tier, status=status,
                assigned_to_id=(owner.id if owner else None),
                created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db_session.add(lead)
    db_session.commit()
    return lead


# ── RESOLUTION: WHERE A VIEW COMES FROM ─────────────────────────────────────

def test_null_inherits_the_industry_and_empty_list_is_a_decision(db_session):
    """NULL is "nothing said yet". [] is "we turned them off"."""
    inherits = _org(db_session, "Inherits", "inherits", industry="energy")
    assert [v["key"] for v in wv.for_organization(inherits)] == [
        "rate-requests", "renewals", "consultations"]

    silenced = _org(db_session, "Silenced", "silenced", industry="energy",
                    views="[]")
    assert wv.for_organization(silenced) == []


def test_a_customers_own_row_replaces_the_industry_list(db_session):
    """A screen only one customer wants costs a row, not a page."""
    org = _org(db_session, "Bespoke", "bespoke", industry="energy",
               views=json.dumps([{
                   "key": "move-concierge", "label": "Move Concierge",
                   "source": "leads", "filter": {"tier": ["new_inquiry"]},
                   "columns": ["name", "contact"],
               }]))
    keys = [v["key"] for v in wv.for_organization(org)]
    assert keys == ["move-concierge"]
    assert "rate-requests" not in keys


def test_an_industry_with_no_views_gets_none_rather_than_a_placeholder(db_session):
    org = _org(db_session, "Generic", "generic-co", industry="generic")
    assert wv.for_organization(org) == []


def test_a_malformed_view_is_dropped_not_repaired(db_session):
    """A screen rendered from a half-understood spec shows the wrong records."""
    org = _org(db_session, "Messy", "messy", industry="energy", views=json.dumps([
        {"label": "No Key At All", "source": "leads"},
        {"key": "bad source", "label": "Spaces In Key", "source": "leads"},
        {"key": "unknown-source", "label": "Nope", "source": "invoices"},
        {"key": "good", "label": "Good", "source": "leads"},
    ]))
    assert [v["key"] for v in wv.for_organization(org)] == ["good"]


def test_unknown_columns_are_dropped_rather_than_rendered_blank(db_session):
    org = _org(db_session, "Cols", "cols", industry="energy", views=json.dumps([
        {"key": "v", "label": "V", "source": "leads",
         "columns": ["name", "social_security_number", "tier"]},
    ]))
    view = wv.for_organization(org)[0]
    assert view["columns"] == ["name", "tier"]


def test_a_filter_key_that_is_not_in_the_vocabulary_is_ignored(db_session, client):
    """Ignored, not honoured. A filter naming another org matches nothing here
    because the tenancy filter is not expressible in a view at all."""
    org = _org(db_session, "FilterCo", "filterco", industry="energy",
               views=json.dumps([{"key": "v", "label": "V", "source": "leads",
                                  "filter": {"organization_id": "some-other-org"}}]))
    admin = _admin(db_session, org, "filter@example.com")
    _lead(db_session, org)
    body = client.get("/workspace-views/v",
                      headers=_headers(db_session, admin)).json()
    assert body["total"] == 1


# ── THE ROUTES ──────────────────────────────────────────────────────────────

def test_listing_views_needs_authentication(client):
    assert client.get("/workspace-views").status_code == 401


def test_listing_returns_what_the_navigation_needs(db_session, client):
    org = _org(db_session, "EnergyCo", "energyco", industry="energy")
    admin = _admin(db_session, org, "nav@example.com")
    body = client.get("/workspace-views",
                      headers=_headers(db_session, admin)).json()
    keys = [v["key"] for v in body["views"]]
    assert keys == ["rate-requests", "renewals", "consultations"]
    assert body["views"][0]["label"] == "Rate Requests"
    # The navigation gets no rows, no counts and no filter - nothing that
    # would cost a query on every page render.
    assert set(body["views"][0]) == {"key", "label", "group", "icon", "subtitle"}


def test_an_unknown_key_is_a_404_and_says_nothing_about_other_customers(
        db_session, client):
    org = _org(db_session, "EnergyCo2", "energyco2", industry="energy")
    admin = _admin(db_session, org, "unknown@example.com")
    # "prospects" is a real view — for a home-services customer, not this one.
    res = client.get("/workspace-views/prospects",
                     headers=_headers(db_session, admin))
    assert res.status_code == 404


def test_rows_counters_and_columns_come_back_together(db_session, client):
    org = _org(db_session, "EnergyCo3", "energyco3", industry="energy")
    admin = _admin(db_session, org, "rows@example.com")
    _lead(db_session, org, tier="new_inquiry", phone="12145550111")
    _lead(db_session, org, tier="rate_review", phone="12145550112")
    _lead(db_session, org, tier="proposal_sent", phone="12145550113")
    # Out of the filter: a dead lead is not an open rate request.
    _lead(db_session, org, tier="rate_review", status="dead", phone="12145550114")

    body = client.get("/workspace-views/rate-requests",
                      headers=_headers(db_session, admin)).json()
    assert body["total"] == 3
    assert {s["label"]: s["value"] for s in body["stats"]}["New"] == 1
    assert {s["label"]: s["value"] for s in body["stats"]}["Unassigned"] == 3
    assert body["view"]["column_labels"]["updated_at"] == "Last Activity"
    assert len(body["items"]) == 3
    assert all("lead_id" in item for item in body["items"])


# ── WHAT A VIEW CANNOT DO ───────────────────────────────────────────────────

def test_a_view_cannot_show_another_organizations_records(db_session, client):
    mine = _org(db_session, "Mine", "mine", industry="energy")
    theirs = _org(db_session, "Theirs", "theirs", industry="energy")
    admin = _admin(db_session, mine, "mine@example.com")
    _lead(db_session, mine, phone="12145550201", first="Ours")
    _lead(db_session, theirs, phone="12145550202", first="Theirs")

    body = client.get("/workspace-views/rate-requests",
                      headers=_headers(db_session, admin)).json()
    assert body["total"] == 1
    names = [i["values"]["name"] for i in body["items"]]
    assert names == ["Ours Payer"]


def test_a_view_is_owner_scoped_for_an_advisor_exactly_like_the_leads_page(
        db_session, client):
    org = _org(db_session, "Scoped", "scoped", industry="energy")
    mine = User(organization_id=org.id, email="a1@scoped.example",
                password_hash=hash_password("TestPass123!"),
                full_name="Advisor One", role="advisor",
                must_change_password=False)
    theirs = User(organization_id=org.id, email="a2@scoped.example",
                  password_hash=hash_password("TestPass123!"),
                  full_name="Advisor Two", role="advisor",
                  must_change_password=False)
    db_session.add_all([mine, theirs])
    db_session.commit()

    _lead(db_session, org, owner=mine, phone="12145550301", first="Mine")
    _lead(db_session, org, owner=theirs, phone="12145550302", first="Theirs")
    _lead(db_session, org, owner=None, phone="12145550303", first="Nobodys")

    body = client.get("/workspace-views/rate-requests",
                      headers=_headers(db_session, mine)).json()
    assert [i["values"]["name"] for i in body["items"]] == ["Mine Payer"]
    # The counter agrees with the list. A stat computed org-wide over a list
    # that is owner-scoped is how a screen lies to the person reading it.
    assert body["total"] == 1


def test_a_view_has_no_write_path(db_session, client):
    org = _org(db_session, "ReadOnly", "readonly", industry="energy")
    admin = _admin(db_session, org, "ro@example.com")
    headers = _headers(db_session, admin)
    assert client.post("/workspace-views/rate-requests",
                       headers=headers, json={}).status_code in (404, 405)
    assert client.delete("/workspace-views/rate-requests",
                         headers=headers).status_code in (404, 405)


# ── APPOINTMENTS ────────────────────────────────────────────────────────────

def test_an_appointment_view_reads_bookings_in_scope_only(db_session, client):
    mine = _org(db_session, "ApptMine", "apptmine", industry="energy")
    theirs = _org(db_session, "ApptTheirs", "apptTheirs".lower(), industry="energy")
    admin = _admin(db_session, mine, "appt@example.com")
    their_admin = _admin(db_session, theirs, "appt2@example.com")

    ours = _lead(db_session, mine, phone="12145550401", first="Booked")
    not_ours = _lead(db_session, theirs, phone="12145550402", first="Hidden")
    soon = datetime.utcnow() + timedelta(days=2)
    db_session.add_all([
        BookingLink(lead_id=ours.id, user_id=admin.id, status="booked",
                    booked_time=soon, appt_label="Consultation"),
        BookingLink(lead_id=not_ours.id, user_id=their_admin.id, status="booked",
                    booked_time=soon, appt_label="Consultation"),
    ])
    db_session.commit()

    body = client.get("/workspace-views/consultations",
                      headers=_headers(db_session, admin)).json()
    assert body["total"] == 1
    assert body["items"][0]["values"]["name"] == "Booked Payer"
    assert {s["label"]: s["value"] for s in body["stats"]}["Upcoming"] == 1


def test_a_cancelled_appointment_is_out_of_the_calendar_view(db_session, client):
    org = _org(db_session, "ApptCancel", "apptcancel", industry="energy")
    admin = _admin(db_session, org, "cancel@example.com")
    lead = _lead(db_session, org, phone="12145550501")
    db_session.add(BookingLink(lead_id=lead.id, user_id=admin.id,
                               status="cancelled",
                               booked_time=datetime.utcnow() + timedelta(days=1)))
    db_session.commit()
    body = client.get("/workspace-views/consultations",
                      headers=_headers(db_session, admin)).json()
    assert body["total"] == 0


# ── THE VERTICALS THIS WAS BUILT FOR, AS CONFIGURATION ──────────────────────

def test_the_cleaning_vertical_is_keyed_on_the_industry_not_a_customer(db_session):
    """Commercial cleaning has its own template, and it is still CONFIGURATION.

    It was an alias of home_services and the three screens were already right;
    what it gained is its own vocabulary, not its own code. The thing this
    asserts is the layering: the key is `cleaning`, an INDUSTRY, so every
    cleaning company gets it and none of them is named anywhere.
    """
    org = _org(db_session, "CleanCo", "cleanco", industry="cleaning")
    assert [v["key"] for v in wv.for_organization(org)] == [
        "prospects", "follow-up", "walkthroughs"]

    # A second cleaning company, with no configuration of its own, gets the
    # identical screens. That is what makes this a vertical rather than a
    # customer.
    other = _org(db_session, "Second Janitorial", "secondjan", industry="janitorial")
    assert ([v["key"] for v in wv.for_organization(other)]
            == [v["key"] for v in wv.for_organization(org)])


# ── WHAT A WALKTHROUGH IS ───────────────────────────────────────────────────
#
# The screen these guard had a filter of `not_status: ["cancelled", "expired"]`,
# which is "everything except those two" — and the status it therefore let in
# was `pending`: a booking link that was SENT and that nobody has acted on. A
# cleaning company that had sent forty links and booked nothing opened a page
# titled Walkthroughs and read forty.
#
# None of the tests below should ever be relaxed to make a number larger.

def _cleaning(db_session, name, slug):
    return _org(db_session, name, slug, industry="cleaning")


def _booking(db_session, lead, user, status, when=None, label="Walkthrough"):
    link = BookingLink(lead_id=lead.id, user_id=user.id, status=status,
                       booked_time=when, appt_label=label)
    db_session.add(link)
    db_session.commit()
    return link


def _walkthroughs(client, db_session, admin):
    return client.get("/workspace-views/walkthroughs",
                      headers=_headers(db_session, admin)).json()


def _stats(body):
    return {s["label"]: s["value"] for s in body["stats"]}


def test_a_sent_booking_link_is_not_a_booked_walkthrough(db_session, client):
    """THE DEFECT, stated as an assertion. A link is an invitation."""
    org = _cleaning(db_session, "PendingCo", "pendingco")
    admin = _admin(db_session, org, "pending@example.com")
    lead = _lead(db_session, org, phone="12145550601", first="Northgate")
    _booking(db_session, lead, admin, "pending")

    body = _walkthroughs(client, db_session, admin)
    assert body["total"] == 0
    assert body["items"] == []
    assert _stats(body)["Upcoming"] == 0
    assert _stats(body)["Awaiting Confirmation"] == 0


def test_an_expired_link_is_not_a_booked_walkthrough_either(db_session, client):
    org = _cleaning(db_session, "ExpiredCo", "expiredco")
    admin = _admin(db_session, org, "expired@example.com")
    lead = _lead(db_session, org, phone="12145550602", first="Lapsed")
    _booking(db_session, lead, admin, "expired")

    assert _walkthroughs(client, db_session, admin)["total"] == 0


def test_a_booked_or_confirmed_appointment_is_a_walkthrough(db_session, client):
    """A time on the calendar is the thing that makes one exist."""
    org = _cleaning(db_session, "BookedCo", "bookedco")
    admin = _admin(db_session, org, "booked@example.com")
    soon = datetime.utcnow() + timedelta(days=2)
    booked = _lead(db_session, org, phone="12145550603", first="Booked")
    confirmed = _lead(db_session, org, phone="12145550604", first="Confirmed")
    _booking(db_session, booked, admin, "booked", soon)
    _booking(db_session, confirmed, admin, "confirmed", soon)
    # Sent but unanswered, sitting in the same workspace the whole time.
    _booking(db_session, _lead(db_session, org, phone="12145550605"),
             admin, "pending")

    body = _walkthroughs(client, db_session, admin)
    assert body["total"] == 2
    stats = _stats(body)
    assert stats["Confirmed"] == 1
    assert stats["Awaiting Confirmation"] == 1
    assert stats["Upcoming"] == 2


def test_a_cancelled_walkthrough_stays_visible_and_stays_named(db_session, client):
    """It happened as a booking. Deleting it from the history is its own lie."""
    org = _cleaning(db_session, "CancelCo", "cancelco")
    admin = _admin(db_session, org, "cancelled@example.com")
    lead = _lead(db_session, org, phone="12145550606", first="Called")
    _booking(db_session, lead, admin, "cancelled",
             datetime.utcnow() + timedelta(days=1))

    body = _walkthroughs(client, db_session, admin)
    assert body["total"] == 1
    assert _stats(body)["Cancelled"] == 1
    # And it does not inflate the counters that mean a visit is coming.
    assert _stats(body)["Upcoming"] == 0
    assert _stats(body)["Confirmed"] == 0
    assert body["items"][0]["values"]["outcome"] == "Cancelled"


def test_a_walkthrough_is_completed_only_when_the_pipeline_says_so(db_session, client):
    """A booked time that has passed is a question, not an attendance.

    `booking_links` has no attended flag, no no-show and no completion
    timestamp. Promoting a past booking to "completed" would hand a business a
    perfect show rate on visits nobody turned up to.
    """
    from app.models.models import PipelineConversation

    org = _cleaning(db_session, "KeptCo", "keptco")
    admin = _admin(db_session, org, "kept@example.com")
    lead = _lead(db_session, org, phone="12145550607", first="Yesterday")
    _booking(db_session, lead, admin, "confirmed",
             datetime.utcnow() - timedelta(days=1))

    body = _walkthroughs(client, db_session, admin)
    assert _stats(body)["Completed"] == 0
    assert _stats(body)["Awaiting Outcome"] == 1
    assert body["items"][0]["values"]["outcome"] == "Awaiting outcome"

    # Now somebody records what happened. THAT is the evidence.
    db_session.add(PipelineConversation(
        organization_id=org.id, lead_id=lead.id, advisor_id=admin.id,
        stage="walkthrough_completed",
        appointment_kept_at=datetime.utcnow()))
    db_session.commit()

    body = _walkthroughs(client, db_session, admin)
    assert _stats(body)["Completed"] == 1
    assert _stats(body)["Awaiting Outcome"] == 0
    assert body["items"][0]["values"]["outcome"] == "Completed"


def test_kept_evidence_from_another_organization_counts_for_nobody(db_session, client):
    """The conversation must belong to the same tenant as the lead.

    Not redundant with the tenancy scope: that decides which LEADS are
    readable. This decides that the EVIDENCE about them came from the same
    customer, so a mis-set row contributes to nobody's numbers rather than to
    the wrong one's.
    """
    from app.models.models import PipelineConversation

    org = _cleaning(db_session, "EvidenceCo", "evidenceco")
    other = _cleaning(db_session, "OtherCleanCo", "othercleanco")
    admin = _admin(db_session, org, "evidence@example.com")
    lead = _lead(db_session, org, phone="12145550608", first="Crossed")
    _booking(db_session, lead, admin, "confirmed",
             datetime.utcnow() - timedelta(days=1))
    db_session.add(PipelineConversation(
        organization_id=other.id, lead_id=lead.id, advisor_id=admin.id,
        stage="kept", appointment_kept_at=datetime.utcnow()))
    db_session.commit()

    body = _walkthroughs(client, db_session, admin)
    assert _stats(body)["Completed"] == 0
    assert _stats(body)["Awaiting Outcome"] == 1


def test_the_walkthrough_screen_cannot_show_another_customers_visits(db_session, client):
    """The corrected filter did not buy itself an exemption from tenancy."""
    mine = _cleaning(db_session, "MineClean", "mineclean")
    theirs = _cleaning(db_session, "TheirsClean", "theirsclean")
    admin = _admin(db_session, mine, "mineclean@example.com")
    their_admin = _admin(db_session, theirs, "theirsclean@example.com")
    soon = datetime.utcnow() + timedelta(days=2)
    _booking(db_session, _lead(db_session, mine, phone="12145550609",
                               first="Ours"), admin, "booked", soon)
    _booking(db_session, _lead(db_session, theirs, phone="12145550610",
                               first="Hidden"), their_admin, "booked", soon)

    body = _walkthroughs(client, db_session, admin)
    assert body["total"] == 1
    assert body["items"][0]["values"]["name"] == "Ours Payer"


# The one appointment screen still carrying the original defect, named here
# rather than skipped silently. `energy/consultations` has the same
# `not_status` filter that `home_services` and `cleaning` were corrected out
# of, so it still counts pending booking links as consultations. It was left
# unchanged because the only production organization on the `energy` template
# is a live customer this pass was instructed not to touch, and correcting it
# changes what that customer's screen reports. It is a one-line change of the
# same shape as the two above, and it needs an authorization, not a decision.
#
# THIS LIST ONLY SHRINKS. An entry added to it is a screen that lies.
KNOWN_UNCORRECTED_APPOINTMENT_VIEWS = {("energy", "consultations")}


def test_no_configured_appointment_screen_counts_a_pending_link(db_session):
    """Whatever a template calls it, an appointment screen names its statuses.

    A guard against the defect coming back through a NEW vertical: the shape
    that caused it was `not_status`, which admits every status nobody thought
    to exclude — including the one that means "sent, unanswered". Naming the
    statuses that mean a time exists fails closed instead.
    """
    from app.services import industry_templates

    offenders = []
    for key, template in industry_templates.TEMPLATES.items():
        for view in (template.get("workspace_views") or []):
            if view.get("source") != "appointments":
                continue
            statuses = (view.get("filter") or {}).get("status") or []
            if statuses and "pending" not in statuses:
                continue
            offenders.append((key, view["key"]))

    assert set(offenders) <= KNOWN_UNCORRECTED_APPOINTMENT_VIEWS, (
        "an appointment screen counts sent booking links as appointments: %s"
        % sorted(set(offenders) - KNOWN_UNCORRECTED_APPOINTMENT_VIEWS))


def test_the_uncorrected_list_describes_screens_that_still_exist(db_session):
    """An exemption for a screen nobody ships is a comment pretending to be a
    guard. When `energy/consultations` is corrected, this fails until the entry
    is removed — which is the only direction that list is allowed to move."""
    from app.services import industry_templates

    for industry, view_key in KNOWN_UNCORRECTED_APPOINTMENT_VIEWS:
        views = industry_templates.TEMPLATES[industry].get("workspace_views") or []
        view = next((v for v in views if v["key"] == view_key), None)
        assert view is not None, "%s/%s no longer exists" % (industry, view_key)
        statuses = (view.get("filter") or {}).get("status") or []
        assert not statuses or "pending" in statuses, (
            "%s/%s has been corrected — remove it from "
            "KNOWN_UNCORRECTED_APPOINTMENT_VIEWS" % (industry, view_key))


def test_no_source_file_in_this_feature_names_a_customer(db_session):
    """The shell is code; the customer is a row. If a name ever appears in one
    of these modules, the layering has been broken and this test says so."""
    import pathlib
    forbidden = ("atlantis", "commercial cleaning blueprint", "brightpath",
                 "almaguer")
    root = pathlib.Path(__file__).resolve().parent.parent
    for relative in ("app/services/workspace_views.py",
                     "app/routers/workspace_views_router.py",
                     "app/services/customer_sites.py",
                     "app/routers/customer_site_router.py",
                     "app/models/customer_site_models.py"):
        text = (root / relative).read_text(encoding="utf-8").lower()
        for name in forbidden:
            assert name not in text, "%s names %r" % (relative, name)
