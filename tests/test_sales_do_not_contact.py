"""Sales Do Not Contact (Lane F, #281) — recording, enforcement, duplicates,
isolation and the audited correction. Every outbound path is exercised with the
real code; nothing is sent (no provider is configured in tests, and each guard
is asserted to stop before the send).
"""
from datetime import datetime, timedelta

import pytest

from app.models.models import Proposal
from app.models.sales_models import Opportunity, ROLE_SALES_REP, ROLE_SALES_MANAGER
from app.models.scheduling_models import SalesAppointment, AppointmentReminder
from app.models.sales_suppression_models import SalesContactSuppression
from app.services import sales_suppression as sdnc

from tests.test_sales_workspace import (  # noqa: F401  (fixtures)
    platform, brand, brand_b, rep, manager, rep_headers, manager_headers,
    _make_sales_user, _add_membership, _token_headers, _SEQ,
)


def _opp(db, brand, owner, company, phone="(214) 555-0142", email="pat@example.test", due_days=0):
    now = datetime.utcnow()
    o = Opportunity(brand_sales_org_id=brand.id, owner_user_id=owner.id if owner else None,
                    company_name=company, contact_name="Pat " + company, phone=phone, email=email,
                    stage="contacted", status="open", next_action="Call",
                    next_action_due_at=datetime(now.year, now.month, now.day, 12) + timedelta(days=due_days))
    db.add(o); db.commit()
    return o


def _record(client, headers, opp, reason="Asked us to stop calling"):
    return client.post("/sales/opportunities/%s/do-not-contact" % opp.id, json={"reason": reason}, headers=headers)


def _appt(db, brand, opp, email="pat@example.test"):
    start = datetime.utcnow() + timedelta(days=2)
    a = SalesAppointment(brand_sales_org_id=brand.id, opportunity_id=opp.id if opp else None, title="Demo",
                         starts_at=start, ends_at=start + timedelta(minutes=30), timezone="America/Chicago",
                         status="scheduled", confirmation_status="pending", prospect_email=email,
                         prospect_name="Pat")
    db.add(a); db.commit()
    return a


# ── recording ───────────────────────────────────────────────────────────────

class TestRecording:
    def test_rep_records_request_with_reason_user_and_history(self, client, db_session, brand, rep, rep_headers):
        o = _opp(db_session, brand, rep, "Stop Co")
        r = _record(client, rep_headers, o)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["active"]["reason"] == "Asked us to stop calling"
        assert body["active"]["recorded_by"] == rep.full_name
        assert body["active"]["phone"] == "12145550142" and body["active"]["email"] == "pat@example.test"
        assert body["can_lift"] is False                       # a rep cannot correct it
        d = client.get("/sales/opportunities/" + o.id, headers=rep_headers).json()
        assert d["do_not_contact"] is True
        assert d["next_action"] is None and d["next_action_due_at"] is None
        assert any(e["event_type"] == "do_not_contact" for e in d["timeline"])

    def test_distinct_from_not_interested(self, client, db_session, brand, rep, rep_headers):
        lost = _opp(db_session, brand, rep, "Lost Co", phone="(214) 555-0101", email="lost@example.test")
        client.patch("/sales/opportunities/" + lost.id, headers=rep_headers,
                     json={"stage": "lost", "next_action": None, "next_action_due_at": None})
        assert db_session.query(SalesContactSuppression).count() == 0      # Lost suppresses nothing
        dnc = _opp(db_session, brand, rep, "Dnc Co", phone="(214) 555-0102", email="dnc@example.test")
        _record(client, rep_headers, dnc)
        d = client.get("/sales/opportunities/" + dnc.id, headers=rep_headers).json()
        assert d["stage"] == "contacted" and d["status"] == "open"          # DNC does not close the deal

    def test_nothing_to_suppress_is_refused(self, client, db_session, brand, rep, rep_headers):
        o = _opp(db_session, brand, rep, "Blank Co", phone=None, email=None)
        assert _record(client, rep_headers, o).status_code == 400

    def test_rep_cannot_record_on_another_reps_deal(self, client, db_session, brand, rep, rep_headers):
        other = _make_sales_user(db_session); _add_membership(db_session, other, brand, ROLE_SALES_REP)
        theirs = _opp(db_session, brand, other, "Theirs Co")
        assert _record(client, rep_headers, theirs).status_code in (403, 404)
        assert db_session.query(SalesContactSuppression).count() == 0


# ── enforcement ─────────────────────────────────────────────────────────────

class TestEnforcement:
    def test_follow_up_and_outreach_logging_refused_plain_notes_allowed(self, client, db_session, brand, rep, rep_headers):
        o = _opp(db_session, brand, rep, "Enforce Co")
        _record(client, rep_headers, o)
        later = (datetime.utcnow() + timedelta(days=30)).date().isoformat() + "T12:00:00"
        assert client.patch("/sales/opportunities/" + o.id, headers=rep_headers,
                            json={"next_action": "Try again", "next_action_due_at": later}).status_code == 409
        assert client.patch("/sales/opportunities/" + o.id, headers=rep_headers,
                            json={"next_action": "Sneak"}).status_code == 409
        assert client.patch("/sales/opportunities/" + o.id, headers=rep_headers,
                            json={"next_action": None, "next_action_due_at": None}).status_code == 200
        assert client.patch("/sales/opportunities/" + o.id, headers=rep_headers,
                            json={"stage": "lost"}).status_code == 200            # closing is fine
        for kind in ("call_outcome", "text_logged"):
            assert client.post("/sales/opportunities/%s/notes" % o.id, headers=rep_headers,
                               json={"event_type": kind, "summary": "x"}).status_code == 409
        assert client.post("/sales/opportunities/%s/notes" % o.id, headers=rep_headers,
                           json={"summary": "Internal note"}).status_code == 201
        # Rescheduling attempts never remove the request.
        assert sdnc.for_opportunity(db_session, o) is not None

    def test_not_in_follow_ups_and_flagged_on_lists(self, client, db_session, brand, rep, rep_headers):
        o = _opp(db_session, brand, rep, "Listed Co", due_days=-1)
        before = client.get("/sales/my-day", headers=rep_headers).json()
        assert o.id in [c["id"] for c in before["follow_ups_due"]]
        _record(client, rep_headers, o)
        after = client.get("/sales/my-day", headers=rep_headers).json()
        assert o.id not in [c["id"] for c in after["follow_ups_due"]]
        cards = client.get("/sales/opportunities", headers=rep_headers).json()
        card = [c for s in cards["stages"] for c in s["opportunities"] if c["id"] == o.id][0]
        assert card["do_not_contact"] is True

    def test_booking_refused_by_deal_and_by_typed_email(self, client, db_session, brand, rep, rep_headers):
        o = _opp(db_session, brand, rep, "Book Co")
        _record(client, rep_headers, o)
        start = (datetime.utcnow() + timedelta(days=3)).replace(microsecond=0).isoformat()
        r1 = client.post("/sales/appointments", headers=rep_headers,
                         json={"starts_at": start, "duration_minutes": 30, "opportunity_id": o.id})
        assert r1.status_code == 409 and "asked not to be contacted" in r1.json()["detail"]
        r2 = client.post("/sales/appointments", headers=rep_headers,
                         json={"starts_at": start, "duration_minutes": 30, "prospect_email": "PAT@example.test"})
        assert r2.status_code == 409
        assert db_session.query(SalesAppointment).count() == 0

    def test_every_prospect_email_path_sends_nothing(self, db_session, brand, rep, monkeypatch):
        o = _opp(db_session, brand, rep, "Mail Co")
        appt = _appt(db_session, brand, o)
        sdnc.record(db_session, o, rep, "No contact"); db_session.commit()

        sent = []
        import app.services.email_service as es
        for name in dir(es):
            if name.startswith("send"):
                monkeypatch.setattr(es, name, lambda *a, **k: sent.append(name) or {"ok": True})

        from app.services.appointment_invites import send_prospect_invitation
        for kind in ("invite", "reschedule", "cancel"):
            rep_ = send_prospect_invitation(db_session, appt, kind=kind)
            assert rep_["sent"] is False and rep_["reason"] == "do_not_contact"

        from app.services import demo_confirmation
        with pytest.raises(ValueError, match="asked not to be contacted"):
            demo_confirmation.send(db_session, appt, actor=rep)

        from app.services import sales_appointment_reminders as rem
        row = AppointmentReminder(appointment_id=appt.id, kind="reminder_24h",
                                  status="pending", target_starts_at=appt.starts_at,
                                  scheduled_for=datetime.utcnow() - timedelta(minutes=1))
        db_session.add(row); db_session.commit()
        rem.process_due(db_session, send=True)
        db_session.refresh(row)
        assert row.status == "skipped" and "Do Not Contact" in (row.detail or "")

        from app.services.proposal_service import send_proposal
        prop = Proposal(created_by_id=rep.id, brand_sales_org_id=brand.id, opportunity_id=o.id,
                        title="Proposal", client_email="pat@example.test")
        db_session.add(prop); db_session.commit()
        res = send_proposal(db_session, prop, rep)
        assert res["sent"] is False and "asked not to be contacted" in res["error"]
        assert sent == []                                                  # nothing reached a sender


# ── duplicates ──────────────────────────────────────────────────────────────

class TestDuplicates:
    def test_duplicate_records_are_covered_by_phone_or_email(self, client, db_session, brand, rep, rep_headers):
        a = _opp(db_session, brand, rep, "Orig Co", phone="214-555-0142", email="pat@example.test")
        b = _opp(db_session, brand, rep, "Dup By Phone", phone="+1 (214) 555 0142", email="other@example.test")
        c = _opp(db_session, brand, rep, "Dup By Email", phone="(469) 555-0000", email=" PAT@Example.test ")
        d = _opp(db_session, brand, rep, "Unrelated", phone="(972) 555-0199", email="else@example.test")
        _record(client, rep_headers, a)
        for o in (b, c):
            db_session.refresh(o)
            assert o.next_action is None and o.next_action_due_at is None      # follow-up cleared on duplicates
            assert client.patch("/sales/opportunities/" + o.id, headers=rep_headers,
                                json={"next_action_due_at": "2030-01-01T12:00:00"}).status_code == 409
        db_session.refresh(d)
        assert d.next_action == "Call"                                          # untouched

    def test_new_duplicate_is_created_without_a_follow_up(self, client, db_session, brand, rep, rep_headers):
        a = _opp(db_session, brand, rep, "Orig Co")
        _record(client, rep_headers, a)
        r = client.post("/sales/opportunities", headers=rep_headers, json={
            "company_name": "Same Person New Record", "contact_name": "Pat", "email": "pat@example.test",
            "next_action": "First contact", "next_action_due_at": "2030-01-01T12:00:00"})
        assert r.status_code == 201, r.text
        card = r.json()
        assert card["do_not_contact"] is True and card["next_action"] is None and card["next_action_due_at"] is None


# ── isolation and correction ───────────────────────────────────────────────

class TestIsolationAndCorrection:
    def test_other_sales_org_is_unaffected_and_cannot_see_or_lift(self, client, db_session, brand, brand_b, rep, rep_headers):
        a = _opp(db_session, brand, rep, "Brand A Co")
        row_id = _record(client, rep_headers, a).json()["active"]["id"]
        rep_b = _make_sales_user(db_session); _add_membership(db_session, rep_b, brand_b, ROLE_SALES_REP)
        mgr_b = _make_sales_user(db_session); _add_membership(db_session, mgr_b, brand_b, ROLE_SALES_MANAGER)
        hb = _token_headers(rep_b, db_session); hm = _token_headers(mgr_b, db_session)
        same_person_b = _opp(db_session, brand_b, rep_b, "Brand B Co")         # same phone + email, other brand
        assert sdnc.for_opportunity(db_session, same_person_b) is None
        assert client.patch("/sales/opportunities/" + same_person_b.id, headers=hb,
                            json={"next_action_due_at": "2030-01-01T12:00:00"}).status_code == 200
        assert client.post("/sales/do-not-contact/%s/lift" % row_id, headers=hm,
                           json={"reason": "not ours"}).status_code == 404   # cannot even probe it

    def test_only_a_manager_lifts_with_a_reason_and_history_is_kept(self, client, db_session, brand, rep, rep_headers,
                                                                     manager, manager_headers):
        o = _opp(db_session, brand, rep, "Fix Co")
        row_id = _record(client, rep_headers, o).json()["active"]["id"]
        assert client.post("/sales/do-not-contact/%s/lift" % row_id, headers=rep_headers,
                           json={"reason": "I think it was a mistake"}).status_code == 403
        assert client.post("/sales/do-not-contact/%s/lift" % row_id, headers=manager_headers,
                           json={"reason": ""}).status_code == 422
        r = client.post("/sales/do-not-contact/%s/lift" % row_id, headers=manager_headers,
                        json={"reason": "Recorded on the wrong prospect"})
        assert r.status_code == 200, r.text
        row = db_session.query(SalesContactSuppression).filter_by(id=row_id).one()
        assert row.lifted_by_user_id == manager.id and row.lift_reason == "Recorded on the wrong prospect"
        assert row.recorded_by_user_id == rep.id                                # history kept, not deleted
        st = client.get("/sales/opportunities/%s/do-not-contact" % o.id, headers=rep_headers).json()
        assert st["active"] is None and len(st["history"]) == 1 and st["history"][0]["lifted_by"]
        assert client.patch("/sales/opportunities/" + o.id, headers=rep_headers,
                            json={"next_action": "Call", "next_action_due_at": "2030-01-01T12:00:00"}).status_code == 200
