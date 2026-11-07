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

from app.models.models import Organization, Platform
from app.models.volt_models import VoltProgram, VoltProject
from app.services.volt.common import DEFAULT_SLUG, FEATURE_KEY

ORG_SLUG = "volt-marketing"
ORG_NAME = "VOLT Marketing"
# VOLT runs on the EvoSys Pro platform. Sign-in on a brand host admits only
# accounts with a workspace on that brand, and a workspace with NO platform is
# treated as BookaBoost - so a recruiter whose only workspace is VOLT could not
# sign in at app.evosyspro.live until this was set (found 2026-11-05).
PLATFORM_SLUG = "evosyspro"

_EXPERIENCE = [{"key": "experience_summary", "label": "Experience summary"}]

PROJECTS = [
    {"key": "aca", "name": "ACA Healthcare Outreach", "short_name": "ACA Healthcare Outreach",
     "summary": "Healthcare outreach and enrollment assistance", "icon": "\u271a",
     "question": "What community outreach, customer service or resource-access experience do you have?",
     "status": "open", "sort_order": 10, "required_docs": []},
    {"key": "fiber", "name": "T-Mobile D2D Fiber Sales", "short_name": "T-Mobile D2D Fiber",
     "summary": "Traveling door-to-door fiber sales", "icon": "\u21af",
     "question": "What door-to-door, telecom, fiber or field sales experience do you have?",
     "status": "open", "sort_order": 20, "required_docs": []},
    {"key": "midterms", "name": "Midterms Flyer Distribution", "short_name": "Midterms flyers",
     "summary": "Election outreach", "icon": "\u25a4",
     "question": "Which areas did you cover?",
     "status": "closed", "sort_order": 90, "required_docs": []},
]

# TARGETED, IDEMPOTENT CORRECTIONS OF RECORDS ALREADY IN THE DATABASE.
# Mike's naming (2026-11-06): "ACA Health Insurance" (no "Obamacare") and
# "T-Mobile D2D Fiber Sales". The structured application replaced the required
# "Experience summary" upload (a resume is now optional), so the original
# default requirement is dropped. Each field changes ONLY while it still holds
# the exact earlier default - a value staff edited is never overwritten - and
# the row (id, key, applications, links, reports) is untouched otherwise.
_OLD_DOCS = [{"key": "experience_summary", "label": "Experience summary"}]


class _ANY(tuple):
    """Any of these earlier defaults (for a field corrected more than once)."""
    def __new__(cls, *values):
        return super().__new__(cls, values)


CORRECTIONS = {
    # ACA Healthcare Outreach (Mike, 2026-11-06): outreach, not insurance
    # sales. A field moves from ANY earlier default (oldest first) to the new
    # text; a staff-edited value matches none of them and is left alone.
    "aca": {"name": (_ANY("ACA (Obamacare) Health Insurance", "ACA Health Insurance"), "ACA Healthcare Outreach"),
            "short_name": (_ANY("ACA health insurance", "ACA Health Insurance"), "ACA Healthcare Outreach"),
            "summary": (_ANY("Health insurance outreach"), "Healthcare outreach and enrollment assistance"),
            "question": (_ANY("What experience do you have with ACA health insurance?"),
                         "What community outreach, customer service or resource-access experience do you have?"),
            "required_docs": (_OLD_DOCS, [])},
    "fiber": {"name": ("Fiber Internet Outreach", "T-Mobile D2D Fiber Sales"),
              "short_name": ("Fiber internet", "T-Mobile D2D Fiber"),
              "summary": ("Fiber internet outreach", "Traveling door-to-door fiber sales"),
              "question": ("What experience do you have with fiber internet outreach?",
                           "What door-to-door, telecom, fiber or field sales experience do you have?"),
              "required_docs": (_OLD_DOCS, [])},
}


def _apply_corrections(db: Session, org_id: str) -> list:
    from app.models.volt_models import VoltAuditEvent
    changed = []
    for p in db.query(VoltProject).filter(VoltProject.organization_id == org_id).all():
        for field, (old, new) in CORRECTIONS.get(p.key, {}).items():
            cur = getattr(p, field)
            if field == "required_docs":
                try:
                    cur_v = json.loads(cur) if cur else []
                except (TypeError, ValueError):
                    continue
                if cur_v != old:
                    continue
                setattr(p, field, json.dumps(new))
            else:
                if (cur not in old) if isinstance(old, _ANY) else (cur != old):
                    continue
                setattr(p, field, new)
            db.add(VoltAuditEvent(organization_id=org_id, entity_type="project", entity_id=p.id,
                                  action="project_corrected", field=field,
                                  old_value=json.dumps(old) if field == "required_docs" else cur,
                                  new_value=json.dumps(new) if field == "required_docs" else new,
                                  reason="Approved naming and application update (2026-11-06)"))  # noqa: E501
            changed.append("%s.%s" % (p.key, field))
    return changed


SUPPORT_EMAIL = "support@voltmarketing.live"
PUBLIC_ORIGIN = "https://voltmarketing.live"


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
    created["platform"] = False
    if org.platform_id is None:
        plat = db.query(Platform).filter(Platform.slug == PLATFORM_SLUG).first()
        if plat is not None:
            org.platform_id = plat.id
            created["platform"] = True
    prog = db.get(VoltProgram, org.id)
    if prog is None:
        prog = VoltProgram(organization_id=org.id, slug=DEFAULT_SLUG, name=ORG_NAME,
                           profile_required_docs=json.dumps([]))
        db.add(prog)
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
    # VOLT's email identity (2026-11-06). Filled only where empty, so a value
    # staff or a later release changes is never overwritten.
    created["identity_set"] = []
    for field, value in (("support_email", SUPPORT_EMAIL), ("sender_name", ORG_NAME), ("public_origin", PUBLIC_ORIGIN)):
        if prog is not None and not getattr(prog, field, None):
            setattr(prog, field, value)
            created["identity_set"].append(field)
    created["corrected"] = _apply_corrections(db, org.id)
    # Authorized VOLT org admins (2026-11-06). Named by the deployment, never
    # in this public repository; existing logins only; never re-grants a login
    # whose VOLT access was deliberately changed.
    import os
    from app.services.volt import access as _access
    named = [e for e in (os.environ.get("VOLT_ORG_ADMIN_EMAILS") or "").split(",") if e.strip()]
    created["admins_granted"] = len(_access.ensure_admins(db, org.id, named)) if named else 0
    db.flush()
    created["organization_id"] = org.id
    return created
