"""Lane F (#281) — the call-outcome flows the Sales Workspace sends, end to end
through the real API. The requests below are exactly what
frontend/src/pages/sales/simpleSales.js buildOutcomeRequests produces.

1. Not Interested → Close as Lost: deal closed, next action and date cleared,
   gone from follow-ups, history kept, counted as worked today.
2. Not Interested → Follow up later: rep-chosen action + date saved, real stage
   kept, appears in follow-ups when due.
3. Recording on one deal changes no other deal.
4. A rep cannot record an outcome on another rep's deal (server-side).
5. The outcome survives a reload.
"""
from datetime import datetime, timedelta

from app.models.sales_models import Opportunity, ROLE_SALES_REP

from tests.test_sales_workspace import (  # noqa: F401  (fixtures)
    platform, brand, rep, manager, rep_headers, manager_headers,
    _make_sales_user, _add_membership, _token_headers, _SEQ,
)


def _opp(db, brand, owner, company, stage="contacted", due_days=-1):
    now = datetime.utcnow()
    o = Opportunity(brand_sales_org_id=brand.id, owner_user_id=owner.id, company_name=company,
                    contact_name="Test " + company, phone="(214) 555-0100", stage=stage, status="open",
                    next_action="Call", next_action_due_at=datetime(now.year, now.month, now.day, 12) + timedelta(days=due_days))
    db.add(o); db.commit()
    return o


def _snapshot(client, headers, ids):
    out = {}
    for i in ids:
        d = client.get("/sales/opportunities/" + i, headers=headers).json()
        out[i] = (d["stage"], d["status"], d["next_action"], d["next_action_due_at"], len(d["timeline"]))
    return out


LOST_NOTE = {"event_type": "call_outcome", "summary": "Call: Not interested — closed as Lost", "detail": None}
LOST_PATCH = {"stage": "lost", "loss_reason": "Not interested (call)", "next_action": None, "next_action_due_at": None}


class TestNotInterested:
    def test_close_as_lost(self, client, db_session, brand, rep, rep_headers):
        target = _opp(db_session, brand, rep, "Lost Co")
        others = [_opp(db_session, brand, rep, "Other %d" % i) for i in range(3)]
        before = _snapshot(client, rep_headers, [o.id for o in others])

        assert client.post("/sales/opportunities/%s/notes" % target.id, json=LOST_NOTE,
                           headers=rep_headers).status_code == 201
        r = client.patch("/sales/opportunities/" + target.id, json=LOST_PATCH, headers=rep_headers)
        assert r.status_code == 200, r.text

        d = client.get("/sales/opportunities/" + target.id, headers=rep_headers).json()   # reload
        assert d["stage"] == "lost" and d["status"] == "lost"
        assert d["next_action"] is None and d["next_action_due_at"] is None   # no future follow-up
        assert d["loss_reason"] == "Not interested (call)"
        kinds = [e["event_type"] for e in d["timeline"]]
        assert "call_outcome" in kinds and "stage_changed" in kinds          # history kept

        day = client.get("/sales/my-day", headers=rep_headers).json()
        assert target.id not in [o["id"] for o in day["follow_ups_due"]]
        assert target.id in day["worked_today"]["opportunity_ids"]
        assert _snapshot(client, rep_headers, [o.id for o in others]) == before   # nothing else moved

    def test_follow_up_later_keeps_stage_and_uses_chosen_date(self, client, db_session, brand, rep, rep_headers):
        target = _opp(db_session, brand, rep, "Later Co", stage="discovery")
        later = (datetime.utcnow() + timedelta(days=45)).date().isoformat()
        client.post("/sales/opportunities/%s/notes" % target.id, headers=rep_headers,
                    json={"event_type": "call_outcome", "summary": "Call: Not interested now — follow up later"})
        r = client.patch("/sales/opportunities/" + target.id, headers=rep_headers,
                         json={"next_action": "Check budget after Q1", "next_action_due_at": later + "T12:00:00"})
        assert r.status_code == 200, r.text
        d = client.get("/sales/opportunities/" + target.id, headers=rep_headers).json()
        assert d["stage"] == "discovery" and d["status"] == "open"
        assert d["next_action"] == "Check budget after Q1"
        assert str(d["next_action_due_at"]).startswith(later)
        day = client.get("/sales/my-day", headers=rep_headers).json()
        assert target.id not in [o["id"] for o in day["follow_ups_due"]]      # not due yet


class TestOtherOutcomes:
    def test_no_answer_retry_date_persists(self, client, db_session, brand, rep, rep_headers):
        target = _opp(db_session, brand, rep, "Retry Co", stage="prospect")
        tomorrow = (datetime.utcnow() + timedelta(days=1)).date().isoformat()
        client.post("/sales/opportunities/%s/notes" % target.id, headers=rep_headers,
                    json={"event_type": "call_outcome", "summary": "Call: No answer"})
        client.patch("/sales/opportunities/" + target.id, headers=rep_headers,
                     json={"next_action": "Try calling again", "next_action_due_at": tomorrow + "T12:00:00"})
        d = client.get("/sales/opportunities/" + target.id, headers=rep_headers).json()
        assert d["stage"] == "prospect"                    # no conversation → no stage move
        assert d["next_action"] == "Try calling again"
        assert str(d["next_action_due_at"]).startswith(tomorrow)

    def test_book_a_meeting_outcome_creates_no_appointment(self, client, db_session, brand, rep, rep_headers):
        target = _opp(db_session, brand, rep, "Agreed Co", stage="prospect")
        client.post("/sales/opportunities/%s/notes" % target.id, headers=rep_headers,
                    json={"event_type": "call_outcome", "summary": "Call: Agreed to a meeting (not booked yet)"})
        client.patch("/sales/opportunities/" + target.id, headers=rep_headers, json={"stage": "contacted"})
        d = client.get("/sales/opportunities/" + target.id, headers=rep_headers).json()
        assert d["appointments"] == []
        assert not any(e["event_type"] == "appointment_booked" for e in d["timeline"])


class TestPermissions:
    def test_rep_cannot_record_on_another_reps_deal(self, client, db_session, brand, rep, rep_headers):
        other = _make_sales_user(db_session)
        _add_membership(db_session, other, brand, ROLE_SALES_REP)
        theirs = _opp(db_session, brand, other, "Not Yours Co")
        r1 = client.post("/sales/opportunities/%s/notes" % theirs.id, json=LOST_NOTE, headers=rep_headers)
        r2 = client.patch("/sales/opportunities/" + theirs.id, json=LOST_PATCH, headers=rep_headers)
        assert r1.status_code in (403, 404) and r2.status_code in (403, 404)
        db_session.refresh(theirs)
        assert theirs.stage == "contacted" and theirs.status == "open" and theirs.next_action == "Call"
