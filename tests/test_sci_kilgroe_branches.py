"""Kilgroe is two branches (owner decision, Nov 3 2026): SCI 7008 = Pell City,
7901 = Leeds, each with its own verified address and phone. The existing
Kilgroe location becomes Pell City (it already had the Pell City phone), a
Kilgroe - Leeds location is added, the Leeds families move to it, and new
imports map by the SCI location number. Schedules, history and holds are
untouched; nothing is sent. Synthetic families only."""
from app.models.location_models import Location
from app.models.models import CadenceState, EmailMessage, Message
from app.models.program_models import LocationProfile, ProgramSourceRecord
from app.services.programs import campaigns as C, importer, setup, unsubscribe
from tests.test_outreach_program import program  # noqa: F401
from tests.test_sci_385_central_alabama import HEAD, _ca, _r, _recs


def _stage(db, org, rows):
    csv = "Lead ID," + HEAD + "".join("%s,%s" % (lid, row) for lid, row in rows)
    importer.stage(db, org, csv.encode(), dry_run=False, campaign_family=C.CENTRAL_ALABAMA_KEY)
    return {r.source_lead_id: r for r in _recs(db, org)}


def _loc(db, prof):
    return db.query(Location).filter(Location.id == prof.location_id).one()


def test_pell_city_gets_its_verified_address_and_leeds_its_own_location(program):
    db, org = program["db"], program["org"]
    _ca(db, org)
    out = C.ensure_kilgroe_branches(db, org.id)
    pell = C.ensure_kilgroe_for_sci(db, org.id)
    p = _loc(db, pell)
    assert (p.address_line1, p.city, p.state, p.postal_code) == ("2219 2nd Ave North", "Pell City", "AL", "35125-1705")
    assert pell.facility_phone == "+12053383341" and pell.official_name == "Kilgroe Funeral Home"
    leeds = db.query(LocationProfile).filter_by(organization_id=org.id,
                                                official_name="Kilgroe Funeral Home - Leeds").one()
    lq = _loc(db, leeds)
    assert (lq.address_line1, lq.city, lq.state, lq.postal_code) == ("1750 Ashville Rd", "Leeds", "AL", "35094")
    assert leeds.facility_phone == "+12056993181" and lq.phone == "+12056993181"
    assert out["pell_city_address_set"] and out["leeds_created"]
    again = C.ensure_kilgroe_branches(db, org.id)                    # idempotent
    assert again == {"pell_city_address_set": False, "leeds_created": False, "moved": 0}
    assert db.query(Location).filter_by(organization_id=org.id, slug="kilgroe-leeds").count() == 1
    # Kilgore Green is a different home and is not touched
    assert not db.query(LocationProfile).filter(LocationProfile.official_name.ilike("%kilgore%")).count()


def test_a_person_entered_address_is_never_overwritten(program):
    db, org = program["db"], program["org"]
    _ca(db, org)
    pell = C.ensure_kilgroe_for_sci(db, org.id)
    loc = _loc(db, pell)
    loc.address_line1, loc.city, loc.state = "1 Owner Entered St", "Pell City", "AL"
    db.commit()
    C.ensure_kilgroe_branches(db, org.id)
    assert _loc(db, pell).address_line1 == "1 Owner Entered St"


def test_only_the_leeds_families_move_and_nothing_else_changes(program):
    db, org = program["db"], program["org"]
    _ca(db, org)
    setup.ensure_locations(db, org, program["admin"], ["Oakwood Memorial Gardens"])
    pell = C.ensure_kilgroe_for_sci(db, org.id)
    recs = _stage(db, org, [
        ("SCI-CA-20261025-0426", _r("PAT", "PELL", "pp@example.com", "2055550201", "Kilgroe Funeral Home")),
        ("SCI-CA-20261025-0432", _r("LEE", "LEEDS", "ll@example.com", "2055550202", "Kilgroe Funeral Home")),
        ("SCI-CA-20261025-0433", _r("LYN", "LEEDS", "ly@example.com", "2055550203", "Kilgroe Funeral Home")),
        ("SCI-CA-20261025-0434", _r("MAN", "UAL", "mu@example.com", "2055550204", "Kilgroe Funeral Home")),
        ("SCI-CA-20261025-0001", _r("OAK", "WOOD", "ow@example.com", "2055550205", "Oakwood Memorial Gardens")),
    ])
    assert all(recs[i].location_id == pell.location_id for i in
               ("SCI-CA-20261025-0426", "SCI-CA-20261025-0432", "SCI-CA-20261025-0433", "SCI-CA-20261025-0434"))
    recs["SCI-CA-20261025-0434"].location_assigned_manually = True     # a person placed this one
    db.commit()
    texts, emails = db.query(Message).count(), db.query(EmailMessage).count()
    states = {s.lead_id: (s.status, s.current_touch_number, s.next_touch_due_at)
              for s in db.query(CadenceState).all()}
    out = C.ensure_kilgroe_branches(db, org.id)
    db.expire_all()
    leeds = db.query(LocationProfile).filter_by(official_name="Kilgroe Funeral Home - Leeds").one()
    recs = {r.source_lead_id: r for r in _recs(db, org)}
    assert out["moved"] == 2
    assert recs["SCI-CA-20261025-0432"].location_id == leeds.location_id
    assert recs["SCI-CA-20261025-0433"].location_id == leeds.location_id
    assert recs["SCI-CA-20261025-0426"].location_id == pell.location_id          # Pell City stays
    assert recs["SCI-CA-20261025-0434"].location_id == pell.location_id          # hand-placed stays
    assert recs["SCI-CA-20261025-0001"].location_id != leeds.location_id         # other homes untouched
    assert db.query(Message).count() == texts and db.query(EmailMessage).count() == emails
    assert {s.lead_id: (s.status, s.current_touch_number, s.next_touch_due_at)
            for s in db.query(CadenceState).all()} == states                     # schedules untouched


def test_each_branch_carries_its_own_postal_address_on_email(program):
    """The email footer address comes from the family's location: after the
    split a Pell City family's email names Pell City, a Leeds family's Leeds."""
    db, org = program["db"], program["org"]
    _ca(db, org)
    C.ensure_kilgroe_branches(db, org.id)
    pell = C.ensure_kilgroe_for_sci(db, org.id)
    leeds = db.query(LocationProfile).filter_by(official_name="Kilgroe Funeral Home - Leeds").one()
    assert unsubscribe.postal_for(pell.official_name, _loc(db, pell)) == \
        "Kilgroe Funeral Home, 2219 2nd Ave North, Pell City, AL 35125-1705"
    assert unsubscribe.postal_for(leeds.official_name, _loc(db, leeds)) == \
        "Kilgroe Funeral Home - Leeds, 1750 Ashville Rd, Leeds, AL 35094"


def test_new_imports_map_by_the_sci_branch_number(program):
    db, org = program["db"], program["org"]
    _ca(db, org)
    setup.ensure_locations(db, org, program["admin"], ["Kilgore Green Funeral Home"])
    C.ensure_kilgroe_branches(db, org.id)
    pell = C.ensure_kilgroe_for_sci(db, org.id)
    leeds = db.query(LocationProfile).filter_by(official_name="Kilgroe Funeral Home - Leeds").one()
    kg = db.query(LocationProfile).filter_by(official_name="Kilgore Green Funeral Home").one()
    recs = _stage(db, org, [
        ("SCI-CA-20261103-0001", _r("A", "PELL", "a@example.com", "2055550401", "7008 - Kilgroe Funeral Home")),
        ("SCI-CA-20261103-0002", _r("B", "LEEDS", "b@example.com", "2055550402", "7901 - Kilgroe Funeral Home")),
        ("SCI-CA-20261103-0003", _r("C", "NAME", "c@example.com", "2055550403", "Kilgroe Funeral Home")),
        ("SCI-CA-20261103-0004", _r("D", "GREEN", "d@example.com", "2055550404", "9768 - Kilgore Green Funeral Home")),
    ])
    assert recs["SCI-CA-20261103-0001"].location_id == pell.location_id
    assert recs["SCI-CA-20261103-0002"].location_id == leeds.location_id
    assert recs["SCI-CA-20261103-0003"].location_id == pell.location_id      # name only: the existing location
    assert recs["SCI-CA-20261103-0004"].location_id == kg.location_id        # Kilgore Green is not Kilgroe
