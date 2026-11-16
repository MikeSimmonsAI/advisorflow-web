"""#507 (Nov 16): SCI's Central Alabama campaign never spills into another
program workspace (Restland). The shared importer used to seed it in every
workspace that previewed or loaded a file; Restland then showed SCI's survey
copy under its own sender. Synthetic data only; nothing reaches a provider."""
import json

import pytest

from app.models.models import CadenceState, EmailMessage, Lead, Message, Organization
from app.models.program_models import CampaignFamily, ProgramSourceRecord
from app.services.programs import campaigns as C
from app.services.programs import setup
from tests.test_sci_intelligent_importer import (EXISTING, NEW, _csv, _make_program, _no_line_check,  # noqa: F401
                                                 _through_review, _h)

KEY = C.CENTRAL_ALABAMA_KEY


@pytest.fixture()
def restland(client, db_session, sample_org, monkeypatch):
    """A program workspace that is NOT SCI (the shared test org is Restland)."""
    monkeypatch.delenv("SMS_PROGRAM_ORG_SCI", raising=False)
    db = db_session
    admin = _make_program(db, sample_org, "rl-admin@example.com")
    return {"db": db, "org": sample_org, "h": _h(db, admin)}


def _alabama(db, org_id):
    return db.query(CampaignFamily).filter_by(organization_id=org_id, key=KEY).first()


def _sends(db):
    return (db.query(Message).count(), db.query(EmailMessage).count(), db.query(CadenceState).count())


def _legacy_row(db, org_id):
    """The row the old importer left behind in Restland (kept for history)."""
    fam = CampaignFamily(organization_id=org_id, key=KEY, name=C.CENTRAL_ALABAMA_NAME,
                         source_campaign_patterns=json.dumps(C.CENTRAL_ALABAMA["patterns"]),
                         first_touch_email_mode="none", followup_email_mode="none", is_active=False)
    C.seed_central_alabama_copy(fam)
    db.add(fam)
    db.commit()
    return fam


def test_restland_preview_and_load_never_create_central_alabama(client, restland):
    db, org, h = restland["db"], restland["org"], restland["h"]
    assert _alabama(db, org.id) is None
    sent = _sends(db)
    bid, _pv, _rows = _through_review(client, h, _csv(NEW))
    p = client.get("/program/leads/import/batches/%s/sci-preview" % bid, headers=h)
    assert p.status_code == 200, p.text
    assert KEY not in [c["key"] for c in p.json()["campaigns"]]
    assert _alabama(db, org.id) is None                                # previewing seeds nothing
    r = client.post("/program/leads/import/batches/%s/load" % bid, headers=h, json={"mode": "load"})
    assert r.status_code == 200, r.text                                # the normal load still works
    assert db.query(ProgramSourceRecord).filter_by(organization_id=org.id).count() > 0
    assert _alabama(db, org.id) is None                                # loading seeds nothing
    assert _sends(db) == sent                                          # and sends nothing
    assert C.ensure_central_alabama(db, org.id) is None                # the seeder refuses here
    assert _alabama(db, org.id) is None


def test_a_legacy_entry_is_hidden_and_cannot_be_used(client, restland):
    db, org, h = restland["db"], restland["org"], restland["h"]
    fam = _legacy_row(db, org.id)
    sent = _sends(db)
    # Catalog and import choices.
    keys = [c["key"] for c in client.get("/program/campaigns", headers=h).json()]
    assert KEY not in keys and "veteran_planning_guide" in keys       # its own campaigns stay
    bid, _pv, _rows = _through_review(client, h, _csv(NEW))
    p = client.get("/program/leads/import/batches/%s/sci-preview" % bid, headers=h).json()
    assert KEY not in [c["key"] for c in p["campaigns"]]
    # Typed URLs: preview, edit, switch on, journey, roster, load into it.
    assert client.get("/program/campaigns/%s/preview" % fam.id, headers=h).status_code == 404
    assert client.patch("/program/campaigns/%s" % fam.id, headers=h, json={"asset_category": "x"}).status_code == 404
    on = client.post("/program/campaigns/%s/activation" % fam.id, headers=h,
                     json={"active": True, "confirm": fam.name})
    assert on.status_code == 404
    assert client.get("/program/campaigns/%s/journey" % fam.id, headers=h).status_code == 404
    assert client.get("/program/central-alabama/roster", headers=h).status_code == 404
    r = client.post("/program/leads/import/batches/%s/load" % bid, headers=h,
                    json={"mode": "stage", "campaign_family": KEY})
    assert r.status_code == 404
    # Service level: never switched on, never started, never its copy.
    assert C.activation_blocker(fam, db) == C.SCI_ONLY_REFUSAL
    from app.services.programs import journey, identity
    with pytest.raises(ValueError):
        journey.start(db, identity.program_for_org(db, org.id), fam, None)
    db.refresh(fam)
    assert fam.is_active is False and fam.name == C.CENTRAL_ALABAMA_NAME   # history kept, untouched
    # Dispatch: a record pointing at it is refused at the send gate.
    lead = Lead(organization_id=org.id, first_name="Pat", last_name="Synthetic", phone="12145550999", status="new")
    db.add(lead)
    db.flush()
    db.add(ProgramSourceRecord(organization_id=org.id, source_lead_id="LEGACY-1", raw_json="{}",
                               lead_id=lead.id, campaign_family=KEY))
    fam.is_active = True                                           # even if someone forced it on in the DB
    db.commit()
    assert identity.send_refusal(db, lead) is not None                  # automated sends refused
    assert identity.cadence_text(db, lead, 1) is None                   # SCI's survey copy never rendered
    from app.services.programs import email_touches
    due = email_touches.plan(db, identity.program_for_org(db, org.id), with_skip=False)
    assert lead.id not in {d.get("lead_id") for d in due}               # no email touch planned
    assert _sends(db) == sent


def test_sci_keeps_its_own_central_alabama(client, db_session, monkeypatch):
    db = db_session
    sci = Organization(name="Service Corporation International", slug="sci-iso-test")
    db.add(sci)
    db.commit()
    monkeypatch.setenv("SMS_PROGRAM_ORG_SCI", sci.id)
    admin = _make_program(db, sci, "sci-iso-admin@example.com")
    fam = C.ensure_central_alabama(db, sci.id)
    assert fam is not None and fam.is_active is False
    assert C.hidden_in(db, sci.id, KEY) is False
    keys = [c["key"] for c in client.get("/program/campaigns", headers=_h(db, admin)).json()]
    assert KEY in keys
    assert client.get("/program/campaigns/%s/preview" % fam.id, headers=_h(db, admin)).status_code != 404
