# -*- coding: utf-8 -*-
"""Shared fixtures for tests/test_agency_*.py (Max Life Command backend).

Until the coordinator adds `insurance_agency` to entitlements.FEATURES and the
router to app/main.py, this registers both locally (idempotent) so the suite
runs before integration and keeps running after it.
"""
import itertools
import json
import uuid
from datetime import date, datetime, timedelta

import pytest

from app.services import entitlements as _ent

import app.models.agency_models  # noqa: E402,F401  (tables on Base before create_all)
from app.models.agency_models import AgencyAgentProfile, AgencyProspectProfile  # noqa: E402
from app.models.models import Lead, Organization, Reply, User  # noqa: E402
from app.services.auth_service import create_access_token, hash_password  # noqa: E402

_SEQ = itertools.count(1)


def _import_router():
    """require_feature() validates its key when the router module is imported.
    Before integration the key is registered only for that import and removed
    again, so other suites that compare against FEATURES see the real registry."""
    added = "insurance_agency" not in _ent.FEATURES
    if added:
        _ent.FEATURES["insurance_agency"] = "Insurance agency command (Max Life Command)"
    try:
        from app.routers.agency_router import router
    finally:
        if added:
            _ent.FEATURES.pop("insurance_agency", None)
    return router


def mount():
    from app.main import app
    router = _import_router()
    if not any(getattr(r, "path", "") == "/agency/summary" for r in app.routes):
        app.include_router(router)


def org(db, name, features=("leads", "insurance_agency")):
    o = Organization(name=name, slug="ag-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                     industry="insurance", is_active=True,
                     enabled_features=json.dumps(list(features)) if features is not None else None)
    db.add(o)
    db.commit()
    return o


def user(db, o, role, label):
    u = User(organization_id=o.id, email="%s-%d@agency.test" % (label, next(_SEQ)),
             password_hash=hash_password("Pass12345!"), full_name=label.title(),
             role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


def agent_profile(db, o, u, juris=("TX",), specs=("family_protection",), available=True,
                  max_active=10, avg=None):
    p = AgencyAgentProfile(organization_id=o.id, user_id=u.id, jurisdictions=json.dumps(list(juris)),
                           specializations=json.dumps(list(specs)), available=available,
                           max_active=max_active, avg_response_minutes=avg)
    db.add(p)
    db.commit()
    return p


def lead(db, o, first="Pat", last="Prospect", state="TX", owner=None, needs=None, intent=None,
         created_ago_min=None, **kw):
    l = Lead(organization_id=o.id, first_name=first, last_name=last, state=state,
             phone="+1214555%04d" % next(_SEQ), status=kw.pop("status", "new"),
             assigned_to_id=owner.id if owner else None, source=kw.pop("source", "website"), **kw)
    if created_ago_min is not None:
        l.created_at = datetime.utcnow() - timedelta(minutes=created_ago_min)
    db.add(l)
    db.commit()
    if needs is not None or intent is not None:
        db.add(AgencyProspectProfile(organization_id=o.id, lead_id=l.id,
                                     need_categories=json.dumps(needs or []), intent_level=intent))
        db.commit()
    return l


def reply(db, l, body):
    r = Reply(lead_id=l.id, body=body, source="sms")
    db.add(r)
    db.commit()
    return r


@pytest.fixture()
def world(db_session):
    mount()
    db = db_session
    o = org(db, "Agency A")
    other = org(db, "Agency B")
    mgr = user(db, o, "org_admin", "manager")
    maya = user(db, o, "advisor", "maya")
    ben = user(db, o, "advisor", "ben")
    fmgr = user(db, other, "org_admin", "fmanager")
    fadv = user(db, other, "advisor", "fadv")
    agent_profile(db, o, maya, juris=("TX", "OK"), specs=("family_protection",), avg=12)
    agent_profile(db, o, ben, juris=("TX",), specs=("retirement",))
    agent_profile(db, other, fadv)
    return dict(db=db, org=o, other=other, mgr=mgr, maya=maya, ben=ben, fmgr=fmgr, fadv=fadv)


@pytest.fixture(autouse=True)
def agency_feature(monkeypatch):
    """Register `insurance_agency` for the duration of each agency test only
    (no-op once the coordinator adds it to entitlements.FEATURES)."""
    if "insurance_agency" not in _ent.FEATURES:
        monkeypatch.setitem(_ent.FEATURES, "insurance_agency",
                            "Insurance agency command (Max Life Command)")
    yield
