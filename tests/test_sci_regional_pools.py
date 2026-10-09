"""SCI POC phone architecture: six regional area-code pools, not 30 campus
numbers. Pure mapping checks plus inbound routing through the SMS webhook.
Synthetic data only; no Twilio call is made."""
from unittest.mock import patch

from app.services.programs import campuses, regional_pools as rp
from tests.test_outreach_program import _lead_for, program  # noqa: F401 - fixtures

SIX = ["205", "334", "850", "251", "706", "318"]


def _rows():
    return campuses.load_grouping()


def _pool_number(db, org, area="205", e164="+12055550177"):
    from app.models.telephony_models import PhoneNumber
    pid = rp.POOLS[area]["pool_id"]
    db.add(PhoneNumber(e164=e164, organization_id=org.id, workspace_id=None, cap_sms=True,
                       cap_voice_inbound=True, label=rp.POOL_LABEL_PREFIX + pid))
    db.commit()
    return pid


def test_exactly_six_known_poc_regional_pools():
    assert list(rp.POOLS) == SIX
    assert len({p["pool_id"] for p in rp.POOLS.values()}) == 6
    areas = {r["Area Code"] for r in _rows() if r["Area Code"]}
    assert areas == set(SIX)                       # every verified entity lands in a pool, none left over


def test_campuses_share_a_pool_without_identity_collapse():
    rows = _rows()
    members = rp.pool_members(rows)
    assert sum(len(v) for v in members.values()) == 38     # 39 entities minus unresolved Oaklawn
    p205 = members["pool-205-birmingham"]
    camp = {r["Location"]: r["Campus"] for r in rows}
    assert len({camp[n] for n in p205}) > 10               # many campuses, one number
    assert len(set(p205)) == len(p205)                     # entities stay distinct
    aliases = {r["Location"] for r in rows}
    assert len(aliases) == 39 and len({r["Campus"] for r in rows}) == 30   # internal identity unchanged


def test_pine_crest_west_is_verified_mobile_251_separate_campus():
    r = next(x for x in _rows() if x["Location"] == "Pine Crest Cemetery West")
    assert (r["City"], r["State"], r["Area Code"]) == ("Mobile", "AL", "251")
    assert r["Address Status"] != "unverified"
    assert rp.sender_pool(r["Area Code"]) == "pool-251-mobile"
    assert r["Campus"] != next(x for x in _rows() if x["Location"] == "Pine Crest Cemetery")["Campus"]


def test_oaklawn_central_is_unresolved_and_has_no_pool():
    r = next(x for x in _rows() if x["Location"] == "Oaklawn Central Care Center")
    assert r["Address Status"] == "unverified" and not r["Area Code"]
    assert rp.pool_for_area_code(r["Area Code"]) is None
    try:
        rp.sender_pool(r["Area Code"])
        raise AssertionError("an unresolved location must not get a sender")
    except LookupError:
        pass


def test_toll_free_backup_is_never_the_normal_local_sender():
    assert rp.BACKUP_TOLL_FREE == "+18449172171"
    for area in SIX:
        assert rp.sender_pool(area).startswith("pool-%s-" % area)
    assert not any(rp.BACKUP_TOLL_FREE in str(v) for v in rp.POOLS.values())


def test_route_inbound_decision():
    assert rp.route_inbound("Radney Funeral Home", "pool-251-mobile")["location"] == "Radney Funeral Home"
    q = rp.route_inbound(None, "pool-251-mobile")
    assert q["location"] is None and q["queue"] == "regional_review:pool-251-mobile"


def test_known_contact_text_to_a_pool_number_routes_to_the_contacts_exact_entity(
        program, sample_advisor, twilio_webhook):
    from app.models.program_models import ProgramResponse, ProgramUnmatchedReply
    db, org = program["db"], program["org"]
    _pool_number(db, org)
    lead = _lead_for(db, org, sample_advisor, "L001", phone="2145550101")
    with patch("app.services.sms_service.Client") as tw, \
            patch("app.services.programs.responses._deliver_staff_alert", return_value=(False, "test")):
        r = twilio_webhook("/sms/webhook/inbound", data={
            "From": "+12145550101", "To": "+12055550177", "Body": "Can I come by Tuesday to visit?",
            "MessageSid": "SMpool1"})
        assert r.status_code == 200
        tw.assert_not_called()
    resp = db.query(ProgramResponse).filter_by(lead_id=lead.id).one()
    from app.services.programs import identity
    own = identity.location_profile_for_lead(db, lead)
    assert resp.location_id == own.location_id            # the contact's own entity, not the number's
    assert "Wrote to the" not in resp.summary
    assert db.query(ProgramUnmatchedReply).count() == 0


def test_unknown_sender_to_a_pool_number_goes_to_regional_review_not_an_entity(
        program, sample_advisor, twilio_webhook):
    from app.models.program_models import ProgramResponse, ProgramUnmatchedReply
    db, org = program["db"], program["org"]
    _pool_number(db, org)
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(False, "test")):
        r = twilio_webhook("/sms/webhook/inbound", data={
            "From": "+19995550123", "To": "+12055550177", "Body": "Who is this?", "MessageSid": "SMpool2"})
    assert r.status_code == 200
    u = db.query(ProgramUnmatchedReply).one()
    assert u.location_id is None and u.status == "open"
    assert "pool-205-birmingham" in u.reason
    assert db.query(ProgramResponse).count() == 0


def test_pool_number_voice_greeting_names_no_location(program):
    from app.services.programs import program_voice
    from app.models.telephony_models import PhoneNumber
    db, org = program["db"], program["org"]
    _pool_number(db, org)
    num = db.query(PhoneNumber).filter_by(e164="+12055550177").one()
    text = program_voice.greeting_text(db, org.id, num.id)
    assert text and "planning line" in text
    for r in _rows():
        assert r["Location"] not in text
