"""RESTLAND RESET - CLEAR (Issue #507, Nov 15), against a real PostgreSQL.

The clear step uses PostgreSQL (row_to_json, gen_random_uuid), so this test
needs RESTLAND_PG_URL (a throwaway database). It is skipped without it.
Synthetic data only."""
import json
import os
import uuid
from datetime import datetime

import pytest

PG = os.environ.get("RESTLAND_PG_URL")
pytestmark = pytest.mark.skipif(not PG, reason="needs RESTLAND_PG_URL (throwaway PostgreSQL)")


@pytest.fixture()
def pg():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from app.models import registry  # noqa: F401 - every model on the metadata
    from app.models.models import Base
    from app.services import contact_suppression as CS
    CS.register()
    eng = create_engine(PG)
    with eng.begin() as c:
        c.execute(text("drop schema public cascade; create schema public"))
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()
    eng.dispose()


def _org(db, oid, name):
    from app.models.models import Organization
    o = Organization(id=oid, name=name, slug=name.lower().replace(" ", "-") + oid[:6])
    db.add(o)
    db.flush()
    return o


def _seed(db):
    from app.models.models import (BookingLink, CadenceState, EmailMessage, Lead, Message, Reply,
                                   SuppressionEntry, User)
    from app.models.program_models import CampaignFamily, OutreachProgram, ProgramSourceRecord
    from app.services.programs import restland_reset as RR
    rl = _org(db, RR.ORG_REQUIRED, "Restland")
    other = _org(db, str(uuid.uuid4()), "Other Tenant")
    staff = User(id=str(uuid.uuid4()), email="staff-%s@example.invalid" % uuid.uuid4().hex[:6], full_name="Staff",
                 password_hash="x", organization_id=rl.id, is_active=True)
    db.add(staff)
    db.add(OutreachProgram(organization_id=rl.id, name="Restland", is_active=True))
    db.add(CampaignFamily(organization_id=rl.id, key="restland_family_file_review", name="FFR", is_active=True))
    db.flush()
    leads = []
    for i in range(6):
        l = Lead(id=str(uuid.uuid4()), organization_id=rl.id, first_name="F%d" % i, last_name="L",
                 phone="21455502%02d" % i, email="f%d@example.com" % i, status="new")
        db.add(l)
        leads.append(l)
    leads[0].status = "dnc"                               # a real source do-not-contact
    keep = Lead(id=str(uuid.uuid4()), organization_id=other.id, first_name="Other", phone="2145550200",
                email="f0@example.com", status="new")
    db.add(keep)
    db.flush()
    for l in leads + [keep]:
        db.add(Message(lead_id=l.id, sender_id=staff.id, body="hi", sent_at=datetime.utcnow()))
        db.add(EmailMessage(lead_id=l.id, sender_id=staff.id, subject="s", body_html="<p>b</p>"))
        db.add(CadenceState(lead_id=l.id, status="paused"))
        db.add(BookingLink(lead_id=l.id, user_id=staff.id, token=uuid.uuid4().hex, status="pending"))
    db.add(Reply(lead_id=leads[1].id, body="thanks"))
    db.add(ProgramSourceRecord(organization_id=rl.id, lead_id=leads[1].id, source_lead_id="src-1", raw_json="{}"))
    db.add(SuppressionEntry(organization_id=rl.id, phone="12145550299", reason="manual"))
    db.commit()
    return rl, other, leads, keep


def test_clear_preserves_dnc_then_empties_restland_and_nothing_else(pg):
    from sqlalchemy import text
    from app.models.models import ContactSuppression, Lead, Message, SuppressionEntry
    from app.models.program_models import CampaignFamily
    from app.models.reset_models import WorkspaceResetItem
    from app.services.programs import restland_clear as RC
    rl, other, leads, keep = _seed(pg)
    ids, keep_id, rl_id, other_id = [l.id for l in leads], keep.id, rl.id, other.id
    out = RC.clear(pg, rl, "clear-1")
    assert out["status"] == "applied", out["summary"].get("failed_at")
    assert pg.query(Lead).filter_by(organization_id=rl.id).count() == 0
    assert pg.query(Message).filter(Message.lead_id.in_(ids)).count() == 0
    # other tenant untouched
    assert pg.query(Lead).filter_by(id=keep_id).count() == 1
    assert pg.query(Message).filter_by(lead_id=keep_id).count() == 1
    # do-not-contact kept on the destinations
    assert pg.query(ContactSuppression).filter_by(organization_id=rl.id, kind="phone", value="12145550200").count() == 1
    assert pg.query(SuppressionEntry).filter_by(organization_id=rl.id, phone="12145550299").count() == 1
    assert out["summary"]["dnc_proof_after"]["ok"] is True
    # templates kept, all off
    fams = pg.query(CampaignFamily).filter_by(organization_id=rl.id).all()
    assert fams and not any(f.is_active for f in fams)
    # restore point: every deleted lead is copied whole
    items = pg.query(WorkspaceResetItem).filter_by(run_id=out["run_id"], entity="leads").all()
    assert len(items) == 6 and {json.loads(i.before_json)["id"] for i in items} == set(ids)
    # re-run is a no-op; a reimport of the DNC family arrives blocked
    assert RC.clear(pg, rl, "clear-1")["already"] is True
    again = Lead(id=str(uuid.uuid4()), organization_id=rl.id, first_name="Back", phone="+1 (214) 555-0200",
                 status="new")
    pg.add(again)
    pg.commit()
    assert again.status == "dnc"
    other_same = Lead(id=str(uuid.uuid4()), organization_id=other_id, first_name="X", phone="2145550200",
                      status="new")
    pg.add(other_same)
    pg.commit()
    assert other_same.status == "new"


def test_clear_refuses_another_organization(pg):
    from app.services.programs import restland_clear as RC
    from app.services.programs.restland_reset import ResetRefused
    rl, other, leads, keep = _seed(pg)
    with pytest.raises(ResetRefused):
        RC.clear(pg, other, "clear-x")
