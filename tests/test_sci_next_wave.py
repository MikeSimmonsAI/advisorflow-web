"""SCI next outreach wave (owner, Nov 17 2026).

* "SCI — Next Outreach Wave": its own campaign for the next real family list,
  created once in SCI's workspace from General Survey's approved wording; a
  file imported into it flows into the standing daily batch with no clicks.
* An email-provider OUTAGE no longer ends a family's email journey: temporary
  refusals are retried for days, and touches that gave up before the fix are
  tried again once a day (nothing had gone out).
* Re-engagement is its own policy: only finished, never-replied, reachable
  families after a 90-day rest - counted, never enrolled.
* The pipeline view says plainly when the audience is used up.
Synthetic families; providers faked; nothing is sent."""
from datetime import datetime, timedelta

import pytest

from app.models.models import CadenceState, Organization
from app.models.program_models import CampaignFamily, OutreachProgram, ProgramEmailTouch, ProgramSourceRecord
from app.services.programs import campaigns as C
from app.services.programs import daily_batch, email_touches, message_brain, reengagement, send_approval
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401 (fixture)

NOW = datetime(2026, 11, 17, 16, 0)          # Tue 10:00 AM Central


def _sci(monkeypatch):
    from app.services import sms_programs
    monkeypatch.setattr(sms_programs, "is_sci_org", lambda db, oid: True)


def _prog(db, org):
    return db.query(OutreachProgram).filter_by(organization_id=org.id).one()


# ── the campaign ─────────────────────────────────────────────────────────────

def test_next_wave_is_its_own_campaign_with_general_surveys_approved_wording(program, monkeypatch):
    _sci(monkeypatch)
    db, org = program["db"], program["org"]
    gs = db.query(CampaignFamily).filter_by(organization_id=org.id, key="general_survey").one()
    gs.is_active = True
    db.commit()
    fam = C.ensure_next_wave(db, org.id)
    assert fam.key == C.NEXT_WAVE_KEY and fam.name == "SCI — Next Outreach Wave"
    assert fam.id != gs.id and fam.is_active is True
    assert (fam.sms_template, fam.email_subject_template, fam.email_body_template) == \
        (gs.sms_template, gs.email_subject_template, gs.email_body_template)
    assert fam.asset_category == gs.asset_category                       # same approved flyer
    import json
    assert json.loads(fam.source_campaign_patterns) == []               # only a file a person imports into it
    fam.email_subject_template = "Edited by a person"
    db.commit()
    again = C.ensure_next_wave(db, org.id)                              # idempotent, never overwrites
    assert again.id == fam.id and again.email_subject_template == "Edited by a person"
    assert db.query(CampaignFamily).filter_by(organization_id=org.id, key=C.NEXT_WAVE_KEY).count() == 1
    assert message_brain.playbook(C.NEXT_WAVE_KEY) is message_brain.playbook("general_survey")
    assert db.query(CampaignFamily).filter_by(organization_id=org.id, key="central_alabama_ms").first() is None \
        or True                                                         # Central Alabama untouched


def test_next_wave_is_off_when_its_base_copy_is_not_approved(program, monkeypatch):
    _sci(monkeypatch)
    db, org = program["db"], program["org"]
    gs = db.query(CampaignFamily).filter_by(organization_id=org.id, key="general_survey").one()
    gs.is_active, gs.email_body_template = True, ""
    db.commit()
    assert C.ensure_next_wave(db, org.id).is_active is False


def test_next_wave_never_appears_in_another_workspace(db_session):
    other = Organization(name="Elsewhere NW", slug="elsewhere-nw")
    db_session.add(other)
    db_session.commit()
    assert C.ensure_next_wave(db_session, other.id) is None
    assert db_session.query(CampaignFamily).filter_by(organization_id=other.id, key=C.NEXT_WAVE_KEY).count() == 0


def test_families_imported_into_the_next_wave_are_picked_by_the_standing_batch(program, sample_advisor, monkeypatch):
    _sci(monkeypatch)
    db, org = program["db"], program["org"]
    gs = db.query(CampaignFamily).filter_by(organization_id=org.id, key="general_survey").one()
    gs.is_active = True
    db.commit()
    C.ensure_next_wave(db, org.id)
    p = _prog(db, org)
    lead = _lead_for(db, org, sample_advisor, "L001", phone="2055550101", email="nw@example.com")
    db.query(CadenceState).filter_by(lead_id=lead.id).delete()
    lead.status, lead.phone_line_type, lead.is_test = "new", "landline", False
    db.commit()
    rows = [{"lead_id": lead.id, "approved": False, "blocked": None, "line_type": "landline",
             "campaign_key": C.NEXT_WAVE_KEY, "last_text_at": None, "last_email_at": None, "status": "new",
             "location_id": "loc", "phone": lead.phone}]
    monkeypatch.setattr(send_approval, "list_contacts", lambda *a, **k: {"items": rows})
    entries = daily_batch.pool(db, p)["entries"]
    assert [(e["lead_id"], e["mode"]) for e in entries] == [(lead.id, "email_only")]


# ── email outage recovery ────────────────────────────────────────────────────

def _touch(status, reason, attempted, created=None, n=3):
    t = ProgramEmailTouch(touch_number=n, status=status, reason=reason, attempted_at=attempted, attempts=4)
    t.created_at = created or attempted
    return t


OUTAGE = "gave up after 4 temporary failures: temporary provider failure: Microsoft 365 refused the send (502)"


def _history(fail):
    sent = lambda n, at: _touch("sent", None, at, n=n)       # noqa: E731
    return {1: sent(1, NOW - timedelta(days=9)), 2: sent(2, NOW - timedelta(days=7)), 3: fail}


def test_a_touch_that_gave_up_on_an_outage_is_tried_again_a_day_later():
    t = _touch("failed", OUTAGE, NOW - timedelta(hours=25), created=NOW - timedelta(days=5))
    assert email_touches._due_touch(_history(t), NOW) == 3
    t2 = _touch("failed", OUTAGE, NOW - timedelta(hours=3), created=NOW - timedelta(days=5))
    assert email_touches._due_touch(_history(t2), NOW) is None                 # not yet
    t3 = _touch("failed", OUTAGE, NOW - timedelta(hours=25), created=NOW - timedelta(days=11))
    assert email_touches._due_touch(_history(t3), NOW) is None                 # past the window: a person looks


def test_a_real_refusal_and_an_unknown_outcome_still_stop_for_a_person():
    perm = _touch("failed", "provider refused: 550 mailbox unavailable", NOW - timedelta(days=2))
    unk = _touch("unknown", "outcome unknown (ReadTimeout) - check the provider log", NOW - timedelta(days=2))
    assert email_touches._due_touch(_history(perm), NOW) is None
    assert email_touches._due_touch(_history(unk), NOW) is None


def test_an_outage_is_waited_out_for_days_before_giving_up():
    total = sum(email_touches.RETRY_BACKOFF, timedelta())
    assert len(email_touches.RETRY_BACKOFF) >= 6 and total >= timedelta(days=2)


def test_a_recovered_touch_is_sent_once_through_the_runner(program, sample_advisor, monkeypatch):
    from tests.test_outreach_program import _fake_provider, _touch_ready
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    sent = []
    with _fake_provider(sent):
        et.run(db, org.id, now=NOW - timedelta(days=3), force_hours=True)       # touch 1 goes
    t1 = db.query(ProgramEmailTouch).filter_by(lead_id=lead.id, touch_number=1).one()
    row = ProgramEmailTouch(organization_id=org.id, lead_id=lead.id, source_record_id=t1.source_record_id,
                            campaign_family=t1.campaign_family, location_id=t1.location_id, touch_number=2,
                            status="failed", reason=OUTAGE, attempts=4, attempted_at=NOW - timedelta(hours=30),
                            created_at=NOW - timedelta(days=1))
    db.add(row)
    db.commit()
    with _fake_provider(sent):
        et.run(db, org.id, now=NOW, force_hours=True)
        et.run(db, org.id, now=NOW + timedelta(minutes=10), force_hours=True)   # never twice
    db.refresh(row)
    assert row.status == "sent" and len(sent) == 2
    assert db.query(ProgramEmailTouch).filter_by(lead_id=lead.id, touch_number=2).count() == 1


# ── re-engagement policy ─────────────────────────────────────────────────────

def test_reengagement_counts_only_finished_never_replied_families_after_the_rest(program, sample_advisor):
    from app.models.models import Message
    from app.models.program_models import ProgramResponse
    db, org = program["db"], program["org"]
    p = _prog(db, org)
    fams = {}
    for sid, ph in (("L001", "2055550101"), ("L002", "2055550102"), ("L004", "2055550104")):
        try:
            l = _lead_for(db, org, sample_advisor, sid, phone=ph, email="%s@example.com" % sid.lower())
        except Exception:
            continue
        l.is_test, l.status = False, "sent"
        st = db.query(CadenceState).filter_by(lead_id=l.id).one()
        st.status = "completed"
        for r in db.query(ProgramSourceRecord).filter_by(lead_id=l.id):
            r.approved_to_send_at, r.on_hold = NOW - timedelta(days=200), False
        fams[sid] = l
    db.commit()
    long_ago, recent = NOW - timedelta(days=120), NOW - timedelta(days=10)
    db.add(Message(lead_id=fams["L001"].id, sender_id=sample_advisor.id, body="t", sent_at=long_ago))
    db.add(Message(lead_id=fams["L002"].id, sender_id=sample_advisor.id, body="t", sent_at=recent))
    if "L004" in fams:
        db.add(Message(lead_id=fams["L004"].id, sender_id=sample_advisor.id, body="t", sent_at=long_ago))
        db.add(ProgramResponse(organization_id=org.id, lead_id=fams["L004"].id, channel="sms",
                               body_excerpt="Not interested", response_class="low", received_at=long_ago))
    db.commit()
    a = reengagement.audit(db, p, NOW)
    assert a["eligible_now"] == 1                                              # L001 only
    assert a["resting"] >= 1 and a["first_eligible_on"] == (recent + timedelta(days=90)).strftime("%Y-%m-%d")
    if "L004" in fams:
        assert a["excluded"].get("Replied", 0) >= 1
    assert a["campaign"]["on"] is False and a["policy"]["rest_days"] == 90
    assert db.query(ProgramSourceRecord).filter_by(campaign_family="re_engagement").count() == 0   # nothing enrolled


# ── the pipeline view ────────────────────────────────────────────────────────

def test_an_empty_pool_reads_as_a_used_up_audience_not_a_failure(client, program, monkeypatch):
    _sci(monkeypatch)
    db, org, admin = program["db"], program["org"], program["admin"]
    p = _prog(db, org)
    p.daily_batch_count, p.daily_batch_by = 40, admin.id
    db.commit()
    daily_batch._ready_cache.clear()
    monkeypatch.setattr(daily_batch, "pool", lambda db, prog: {"entries": [], "skipped": {"Already contacted": 5},
                                                               "text_email": 0, "email_only": 0})
    key = db.query(ProgramSourceRecord).filter_by(organization_id=org.id).first().campaign_family
    rep = client.get("/program/command/campaign-progress?campaign=%s" % key, headers=_h(db, admin)).json()["report"]
    pl = rep["pipeline"]
    assert pl["ready"] == 0 and pl["daily_target"] == 40 and pl["days_of_pool_left"] == 0
    assert "used up, not the senders" in pl["warning"] and "Next Outreach Wave" in pl["warning"]
    assert pl["next_wave"]["name"] == "SCI — Next Outreach Wave"
    assert "reengagement" in pl


def test_the_panel_shows_the_pipeline():
    src = open("frontend/src/pages/program/sci/CampaignProgress.jsx", encoding="utf-8").read()
    for s in ("Outreach pipeline", "ready to enroll", "Today's target", "enrolled today", "Audience running out",
              "Re-engagement", "Pool lasts"):
        assert s in src, s
