"""Lane F (#281) — My Day personal scope and the full-day "worked today" count.

1. scope=mine narrows a MANAGER to their own deals and meetings; the default
   (no scope) is unchanged and still covers the whole team.
2. scope=mine never widens anything: a rep's numbers are identical either way.
3. worked_today counts every deal the caller called or texted since the
   brand's midnight — not just what fits in the 15-row activity feed — and
   ignores other people's activity, notes, and yesterday's work.
4. A bad scope value is refused.
"""
from datetime import datetime, timedelta

from app.models.sales_models import Opportunity, OpportunityEvent, ROLE_SALES_MANAGER, ROLE_SALES_REP
from app.models.scheduling_models import SalesAppointment, AppointmentParticipant

from tests.test_sales_workspace import (  # noqa: F401  (fixtures)
    platform, brand, rep, manager, rep_headers, manager_headers,
    _make_sales_user, _add_membership, _token_headers, _SEQ,
)


def _opp(db, brand, owner, company, due_days=0):
    now = datetime.utcnow()
    o = Opportunity(brand_sales_org_id=brand.id, owner_user_id=owner.id if owner else None,
                    company_name=company, contact_name="Test " + company, stage="contacted",
                    status="open", next_action="Call",
                    next_action_due_at=datetime(now.year, now.month, now.day, 12) + timedelta(days=due_days))
    db.add(o); db.commit()
    return o


def _event(db, opp, actor, kind, when=None):
    e = OpportunityEvent(opportunity_id=opp.id, event_type=kind, summary="x",
                         actor_user_id=actor.id, occurred_at=when or datetime.utcnow())
    db.add(e); db.commit()
    return e


def _appt(db, brand, opp, who, hours=2):
    start = datetime.utcnow() + timedelta(hours=hours)
    a = SalesAppointment(brand_sales_org_id=brand.id, opportunity_id=opp.id, title="Demo",
                         starts_at=start, ends_at=start + timedelta(minutes=30),
                         timezone="America/Chicago", status="scheduled",
                         confirmation_status="pending")
    db.add(a); db.commit()
    db.add(AppointmentParticipant(appointment_id=a.id, user_id=who.id,
                                  busy_start_at=a.starts_at, busy_end_at=a.ends_at)); db.commit()
    return a


class TestManagerPersonalScope:
    def test_default_is_team_and_mine_is_personal(self, client, db_session, brand,
                                                  manager, manager_headers, rep):
        _add_membership(db_session, rep, brand, ROLE_SALES_REP)
        mine = _opp(db_session, brand, manager, "Mgr Own Co", due_days=-1)
        theirs = _opp(db_session, brand, rep, "Rep Owned Co", due_days=-1)
        _appt(db_session, brand, theirs, rep)
        _appt(db_session, brand, mine, manager, hours=3)

        team = client.get("/sales/my-day", headers=manager_headers).json()
        assert team["scope"] == "team" and team["is_manager"] is True
        assert team["metrics"]["active_opportunities"] == 2
        assert team["metrics"]["follow_ups_due"] == 2
        assert len(team["upcoming_appointments"]) == 2

        me = client.get("/sales/my-day?scope=mine", headers=manager_headers).json()
        assert me["scope"] == "mine"
        assert me["metrics"]["active_opportunities"] == 1
        assert me["metrics"]["follow_ups_due"] == 1
        assert [o["company_name"] for o in me["follow_ups_due"]] == ["Mgr Own Co"]
        assert len(me["upcoming_appointments"]) == 1
        assert me["upcoming_appointments"][0]["opportunity_id"] == mine.id

    def test_rep_numbers_identical_either_way(self, client, db_session, brand, rep,
                                              rep_headers, manager):
        _add_membership(db_session, manager, brand, ROLE_SALES_MANAGER)
        _opp(db_session, brand, rep, "Rep Co", due_days=-1)
        _opp(db_session, brand, None, "Unowned Co", due_days=0)
        _opp(db_session, brand, manager, "Mgr Co", due_days=0)
        a = client.get("/sales/my-day", headers=rep_headers).json()
        b = client.get("/sales/my-day?scope=mine", headers=rep_headers).json()
        assert a["scope"] == b["scope"] == "mine"
        assert a["metrics"] == b["metrics"]
        assert a["metrics"]["active_opportunities"] == 2      # own + unowned, never the manager's

    def test_bad_scope_refused(self, client, rep_headers):
        assert client.get("/sales/my-day?scope=everyone", headers=rep_headers).status_code == 422


class TestWorkedToday:
    def test_counts_every_deal_worked_today_beyond_feed_size(self, client, db_session,
                                                              brand, rep, rep_headers):
        opps = [_opp(db_session, brand, rep, "Busy %d" % i) for i in range(20)]
        for o in opps:
            _event(db_session, o, rep, "call_outcome")
            _event(db_session, o, rep, "note")           # noise that fills the feed
        _event(db_session, opps[0], rep, "text_logged")    # same deal twice → counted once
        d = client.get("/sales/my-day", headers=rep_headers).json()
        assert len(d["recent_activity"]) == 15             # the feed is still capped
        assert d["worked_today"]["count"] == 20
        assert set(d["worked_today"]["opportunity_ids"]) == {o.id for o in opps}

    def test_ignores_other_people_notes_and_yesterday(self, client, db_session, brand,
                                                      rep, rep_headers, manager):
        _add_membership(db_session, manager, brand, ROLE_SALES_MANAGER)
        a = _opp(db_session, brand, rep, "A")
        b = _opp(db_session, brand, rep, "B")
        c = _opp(db_session, brand, rep, "C")
        _event(db_session, a, manager, "call_outcome")                       # someone else
        _event(db_session, b, rep, "note")                                   # not a call/text
        _event(db_session, c, rep, "call_outcome", datetime.utcnow() - timedelta(days=2))
        d = client.get("/sales/my-day", headers=rep_headers).json()
        assert d["worked_today"] == {"count": 0, "opportunity_ids": []}

    def test_never_reports_deals_outside_the_callers_view(self, client, db_session, brand,
                                                          rep, rep_headers):
        other = _make_sales_user(db_session)
        _add_membership(db_session, other, brand, ROLE_SALES_REP)
        hidden = _opp(db_session, brand, other, "Other rep's deal")
        _event(db_session, hidden, rep, "call_outcome")  # even if somehow logged by this rep
        d = client.get("/sales/my-day", headers=rep_headers).json()
        assert hidden.id not in d["worked_today"]["opportunity_ids"]
