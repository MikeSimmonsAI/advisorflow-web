"""Booking links start with the family's own location: https://<location>.<domain>/<code>."""
from app.models.models import BookingLink, User
from app.models.program_models import OutreachProgram
from app.services.programs import identity
from app.services.programs.location_links import location_key, parse_host
from tests.test_outreach_program import _lead_for, program  # noqa: F401


def test_location_keys():
    assert location_key("Eastern Gate Memorial Gardens") == "easterngate"
    assert location_key("Eastern Gate Memorial Funeral Home") == "easterngate"
    assert location_key("Elmwood Cemetery & Mausoleum") == "elmwood"
    assert location_key("Johns-Ridout's Funeral Parlors") == "johnsridouts"
    assert location_key("Rockco Funeral Home (Montevallo)") == "rockcomontevallo"
    assert location_key("Alabama Heritage Funeral Home") == "alabamaheritage"
    assert location_key("Anything", override="EGate") == "egate"


def test_parse_host(monkeypatch):
    monkeypatch.setenv("LOCATION_LINK_DOMAIN", "evosyspro.live")
    assert parse_host("easterngate.evosyspro.live") == "easterngate"
    assert parse_host("app.evosyspro.live") is None and parse_host("links.evosyspro.live") is None
    assert parse_host("a.b.evosyspro.live") is None and parse_host("evil.com") is None


def test_off_until_domain_is_set(program):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="Rosa", email="rd@example.com")
    assert identity._with_booking(db, lead, "{booking_line}", {})["booking_line"] == ""


def test_short_link_opens_only_under_its_own_location(client, program, monkeypatch):
    db, org, admin = program["db"], program["org"], program["admin"]
    monkeypatch.setenv("LOCATION_LINK_DOMAIN", "evosyspro.live")
    from app.services import public_identity
    monkeypatch.setattr(public_identity, "booking_url", lambda db, o, t: "https://app.example.test/book/" + t)
    u = User(organization_id=org.id, email="booker3@example.com", password_hash="x", full_name="B",
             role="org_admin", is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    db.query(OutreachProgram).filter_by(organization_id=org.id).one().booking_user_id = u.id
    db.commit()
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="Rosa", email="rd@example.com")
    line = identity._with_booking(db, lead, "{booking_line}", {})["booking_line"]
    again = identity._with_booking(db, lead, "{booking_line}", {})["booking_line"]
    link = db.query(BookingLink).filter_by(lead_id=lead.id).one()
    assert len(link.short_code) == 5 and line == again
    assert line == "Pick a time that works for you: https://alabamaheritage.evosyspro.live/" + link.short_code
    ok = client.get("/" + link.short_code, headers={"host": "alabamaheritage.evosyspro.live"},
                    follow_redirects=False)
    assert ok.status_code == 302 and ok.headers["location"] == "https://app.example.test/book/" + link.token
    assert client.get("/" + link.short_code, headers={"host": "easterngate.evosyspro.live"},
                      follow_redirects=False).status_code == 404
    assert client.get("/" + link.short_code, follow_redirects=False).status_code == 404
