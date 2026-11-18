"""SCI AND RESTLAND SHARE NOTHING (owner, Nov 17).

A permanent guard. A non-SCI program - one that even holds leftover SCI-only
campaign rows, as Restland's workspace did - reads EVERY program data feed the
app serves, and none may carry SCI's name, people or campaigns. SCI's feeds may
not carry the other program's. The feed list is read from the app's own
routes, so a screen added later is covered without editing this file.
"""
import re

import pytest
from fastapi.routing import APIRoute

from app.models.models import Lead, User
from app.models.program_models import CampaignFamily
from app.services.auth_service import hash_password
from app.services.programs import campaigns, setup
from tests.test_outreach_program import _h

SKIP = ("export", "download", "run", "send", "fire", "trigger", "test-", "csv", "backup", "self-check",
        "readiness-test",
        "sms-check")   # asks the phone company live; its line is checked in test_staff_alerts_use_own_line
SCI_WORDS = re.compile(r"\bSCI\b|Service Corporation|Kerry Allan|Central Alabama|Next Outreach Wave")
OTHER_WORDS = re.compile(r"Restland|Family File Review|Greenville Ave")


def _feeds():
    import app.main
    out = set()
    for r in app.main.app.routes:
        if isinstance(r, APIRoute) and "GET" in r.methods and r.path.startswith("/program") and "{" not in r.path \
                and not any(p.required for p in r.dependant.query_params) and not any(s in r.path for s in SKIP):
            out.add(r.path)
    assert len(out) > 30, "the program feed list looks wrong"
    return sorted(out)


def _manager(db, org, email):
    u = User(organization_id=org.id, email=email, password_hash=hash_password("Pass12345!"), full_name="Pat Manager",
             role="org_admin", is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _scan(client, db, user, pattern):
    leaks = {}
    for path in _feeds():
        r = client.get(path, headers=_h(db, user))
        if r.status_code >= 500:
            leaks[path] = "server error %s" % r.status_code
            continue
        hits = sorted(set(m.group(0) for m in pattern.finditer(r.text)))
        if hits:
            leaks[path] = hits
    return leaks


def test_a_non_sci_program_never_shows_sci(client, db_session, sample_org, monkeypatch):
    monkeypatch.delenv("SMS_PROGRAM_ORG_SCI", raising=False)
    db, org = db_session, sample_org
    org.name = "Greenlawn Memorial Park"                 # not SCI, not Restland
    db.commit()
    mgr = _manager(db, org, "mgr@example.com")
    setup.ensure_program(db, org, name="Greenlawn Memorial Park", primary_contact_name="Pat Manager",
                         primary_contact_title="Advisor")
    # Leftovers like the ones found in Restland on Nov 17: SCI-only rows
    # that slipped in before the guards existed. They must stay invisible.
    db.add_all([CampaignFamily(organization_id=org.id, key=campaigns.CENTRAL_ALABAMA_KEY,
                               name="Central Alabama 2025–2026 MS", is_active=False),
                CampaignFamily(organization_id=org.id, key=campaigns.NEXT_WAVE_KEY,
                               name=campaigns.NEXT_WAVE_NAME, is_active=False)])
    db.add(Lead(organization_id=org.id, first_name="Jo", last_name="Sample", phone="2145550111", status="new"))
    # An upload stored before Nov 16 under the old "SCI program file" label
    # (Restland's own family file was one).
    from app.models.import_models import ImportBatch
    from app.services.intake import engine as ENG
    db.add(ImportBatch(organization_id=org.id, batch_code="ZZ-OLD-1", status="committed", source_type="csv",
                       source_filename="families.csv", source_label="SCI program file",
                       source_system=ENG.SCI_SOURCE_SYSTEM, pipeline=ENG.PIPELINE))
    db.commit()
    assert _scan(client, db, mgr, SCI_WORDS) == {}


def test_sci_never_shows_another_program(client, db_session, sample_org, monkeypatch):
    db, org = db_session, sample_org
    org.name = "Service Corporation International"
    db.commit()
    monkeypatch.setenv("SMS_PROGRAM_ORG_SCI", org.id)
    mgr = _manager(db, org, "sci-mgr@example.com")
    setup.ensure_program(db, org, name="Service Corporation International", primary_contact_name="Kerry Allan",
                         primary_contact_title="Head of Sales")
    db.add(Lead(organization_id=org.id, first_name="Jo", last_name="Sample", phone="2145550112", status="new"))
    db.commit()
    assert _scan(client, db, mgr, OTHER_WORDS) == {}


def test_staff_alert_label_never_borrows_sci(db_session, sample_org, monkeypatch):
    from app.services.programs import alert_ledger as L
    monkeypatch.delenv("SMS_PROGRAM_ORG_SCI", raising=False)
    sample_org.name = "Greenlawn Memorial Park"
    db_session.commit()
    prog = setup.ensure_program(db_session, sample_org, name="Greenlawn Memorial Park",
                                primary_contact_name="Pat Manager", primary_contact_title="Advisor")
    assert "SCI" not in L.program_label(db_session, prog)
    assert L.program_label(db_session, None) != "SCI"


def test_staff_alerts_use_the_programs_own_line(db_session, sample_org, monkeypatch):
    """The alert check and the test alert text from THIS program's number,
    never SCI's toll-free line (it did until Nov 17)."""
    from app.routers import program_router as PR
    from app.services import sms_programs as SP
    monkeypatch.delenv("SMS_PROGRAM_ORG_SCI", raising=False)
    sample_org.name = "Greenlawn Memorial Park"
    db_session.commit()
    prog = setup.ensure_program(db_session, sample_org, name="Greenlawn Memorial Park",
                                primary_contact_name="Pat Manager", primary_contact_title="Advisor")
    monkeypatch.setattr(SP, "sci_sender", lambda: pytest.fail("used SCI's line for another program"))
    import app.services.sms_service as SS
    monkeypatch.setattr(SS, "Client", lambda sid, token: ("own-client", sid))
    client, line = PR._staff_line(db_session, prog)
    assert line == sample_org.org_twilio_phone_number
