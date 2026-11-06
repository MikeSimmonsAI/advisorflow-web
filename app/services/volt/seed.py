"""Idempotent load of the VOLT Marketing tenant and its three projects.

Runs in the deploy's migration step (app/migrate.py) and is safe on every
deploy: it CREATES what is missing and never changes what staff maintain.
A project's status, wording and required documents are staff-owned data
after the first load, so a redeploy never re-opens a closed project or
undoes an edit.

Nothing here invents compensation, guarantees, locations, licensing, partners
or start dates. It creates no login, no applicant and no referral.
"""
from __future__ import annotations

import json
from typing import Dict

from sqlalchemy.orm import Session

from app.models.models import Organization
from app.models.volt_models import VoltProgram, VoltProject
from app.services.volt.common import DEFAULT_SLUG, FEATURE_KEY

ORG_SLUG = "volt-marketing"
ORG_NAME = "VOLT Marketing"

_EXPERIENCE = [{"key": "experience_summary", "label": "Experience summary"}]

PROJECTS = [
    {"key": "aca", "name": "ACA (Obamacare) Health Insurance", "short_name": "ACA health insurance",
     "summary": "Health insurance outreach", "icon": "✚",
     "question": "What experience do you have with ACA health insurance?",
     "status": "open", "sort_order": 10, "required_docs": _EXPERIENCE},
    {"key": "fiber", "name": "Fiber Internet Outreach", "short_name": "Fiber internet",
     "summary": "Fiber internet outreach", "icon": "↯",
     "question": "What experience do you have with fiber internet outreach?",
     "status": "open", "sort_order": 20, "required_docs": _EXPERIENCE},
    {"key": "midterms", "name": "Midterms Flyer Distribution", "short_name": "Midterms flyers",
     "summary": "Election outreach", "icon": "▤",
     "question": "Which areas did you cover?",
     "status": "closed", "sort_order": 90, "required_docs": []},
]


def ensure(db: Session) -> Dict[str, object]:
    created = {"organization": False, "program": False, "projects": []}
    org = db.query(Organization).filter(Organization.slug == ORG_SLUG).first()
    if org is None:
        org = Organization(name=ORG_NAME, slug=ORG_SLUG, plan="enterprise", industry="recruiting",
                           is_active=True, enabled_features=json.dumps([FEATURE_KEY]))
        db.add(org)
        db.flush()
        created["organization"] = True
    else:
        try:
            keys = json.loads(org.enabled_features or "null")
        except (TypeError, ValueError):
            keys = None
        if isinstance(keys, list) and FEATURE_KEY not in keys:
            keys.append(FEATURE_KEY)
            org.enabled_features = json.dumps(keys)
        elif keys is None:
            org.enabled_features = json.dumps([FEATURE_KEY])
    prog = db.get(VoltProgram, org.id)
    if prog is None:
        db.add(VoltProgram(organization_id=org.id, slug=DEFAULT_SLUG, name=ORG_NAME,
                           profile_required_docs=json.dumps([])))
        created["program"] = True
    have = {p.key for p in db.query(VoltProject).filter(VoltProject.organization_id == org.id).all()}
    for spec in PROJECTS:
        if spec["key"] in have:
            continue
        db.add(VoltProject(organization_id=org.id, key=spec["key"], name=spec["name"],
                           short_name=spec["short_name"], summary=spec["summary"], icon=spec["icon"],
                           question=spec["question"], status=spec["status"], sort_order=spec["sort_order"],
                           required_docs=json.dumps(spec["required_docs"])))
        created["projects"].append(spec["key"])
    db.flush()
    created["organization_id"] = org.id
    return created
