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

def test_the_home_services_template_covers_a_commercial_cleaning_customer(db_session):
    """"cleaning" is an alias of home_services, so it needs no code of its own."""
    org = _org(db_session, "CleanCo", "cleanco", industry="cleaning")
    assert [v["key"] for v in wv.for_organization(org)] == [
        "prospects", "follow-up", "walkthroughs"]


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
