"""A person corrects a property's identifiers - account number (APN), county,
street, city, ZIP - from an official record they looked at.

Old purchased lists carry bad addresses ("105 VILLANOVA" with no suffix, an
Iowa ZIP on an Ennis house, a Grand Prairie house filed under the wrong
county). Until the identifiers are right, no county source can answer for the
house. This is the audited way to fix them:

  * admin only; every change is an event with before / after, the official
    record the person used (URL) and their note;
  * refused (409) if the corrected account or address already belongs to a
    DIFFERENT property in the workspace - that is a merge, not an edit;
  * then the free county lookups run for this house at once (the correction
    voids earlier no-match answers), so the OWNER comes from the county's own
    record through the normal ingest - never typed in here;
  * the property is rescored the ordinary way.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Dict, Optional

from app.models.evosense_models import EvoSenseProperty
from app.services.evosense import common as C
from app.services.evosense import identity as ID

FIELDS = ("street_address", "city", "zip_code", "county", "parcel_apn")


class IdentityConflict(Exception):
    def __init__(self, other_id: str, key: str):
        super().__init__("%s already belongs to property %s" % (key, other_id))
        self.other_id, self.key = other_id, key


def _clean(field: str, v: Any) -> Optional[str]:
    v = re.sub(r"\s+", " ", str(v or "")).strip()
    if not v:
        return None
    if field == "zip_code":
        d = re.sub(r"\D", "", v)[:5]
        if len(d) != 5:
            raise ValueError("ZIP must be 5 digits")
        return d
    if field == "county":
        return re.sub(r"\s+county$", "", v, flags=re.I).title()
    if field == "street_address":
        return v.upper()
    if field == "parcel_apn":
        return v.upper()
    return v.title()


def correct(db, org_id: str, prop: EvoSenseProperty, changes: Dict[str, Any], *, user,
            evidence_url: Optional[str], note: Optional[str], lookup: bool = True) -> Dict[str, Any]:
    from app.services.evosense import evaluate as EV
    from app.services.evosense import hunt as HU
    new = {f: _clean(f, changes[f]) for f in FIELDS if f in changes}
    if not new:
        raise ValueError("Send at least one of %s" % ", ".join(FIELDS))
    if not evidence_url or not re.match(r"^https?://", evidence_url):
        raise ValueError("Give the official record you used (its web address)")
    before = {f: getattr(prop, f) for f in FIELDS}
    after = dict(before, **new)
    after["state"] = prop.state or "TX"
    after["unit"] = prop.unit
    k = ID.keys(after)
    base = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id,
                                             EvoSenseProperty.id != prop.id)
    if k["apn_key"]:
        other = base.filter(EvoSenseProperty.apn_key == k["apn_key"]).first()
        if other is not None:
            raise IdentityConflict(other.id, "That account number")
    if k["address_key"]:
        other = base.filter(EvoSenseProperty.address_key == k["address_key"]).first()
        if other is not None:
            raise IdentityConflict(other.id, "That address")
    changed = {f: {"was": before[f], "now": after[f]} for f in new if before[f] != after[f]}
    if not changed:
        return {"changed": {}, "lookups": None}
    for f in new:
        setattr(prop, f, after[f])
    prop.address_key, prop.street_key, prop.apn_key = k["address_key"], k["street_key"], k["apn_key"]
    C.log_event(db, org_id, "identity.corrected", property_id=prop.id, user=user, actor_type=C.ACTOR_USER,
                is_test=prop.is_test,
                summary="Identifiers corrected from an official record: %s" % ", ".join(sorted(changed)),
                details={"changed": changed, "evidence_url": evidence_url, "note": (note or "")[:500]})
    db.commit()
    counts: Dict[str, Any] = {}
    if lookup:
        HU.free_lookups(db, org_id, SimpleNamespace(id=None, is_test=bool(prop.is_test)), SimpleNamespace(id=None),
                        [prop], counts, force_ids={prop.id})
        db.commit()
        db.refresh(prop)
    EV.rescore(db, prop, EV.strategy_for(db, prop))
    db.commit()
    return {"changed": changed, "lookups": counts.get("lookups")}
