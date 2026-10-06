"""POC phone architecture: one standard local number per area-code REGIONAL
POOL, shared by every campus in that area code. (30 campuses / 39 entities are
kept for internal identity and routing only; they do not each need a number.)

A pool number carries no location: its `phone_numbers` row has
workspace_id NULL and label "pool:<pool_id>". Identity is never taken from
the number. Known senders route by contact to their own exact entity; unknown
senders and callers go to the regional review queue and a neutral greeting.
The toll-free backup (+1 844-917-2171) is overflow only, never the normal
local sender.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Dict, List, Optional

POOL_LABEL_PREFIX = "pool:"
BACKUP_TOLL_FREE = "+18449172171"

# area code -> pool id and internal (non-customer-facing) label.
POOLS: "OrderedDict[str, Dict[str, str]]" = OrderedDict([
    ("205", {"pool_id": "pool-205-birmingham", "label": "SCI Alabama Central / Birmingham-area pool"}),
    ("334", {"pool_id": "pool-334-montgomery", "label": "SCI Montgomery / central Alabama pool"}),
    ("850", {"pool_id": "pool-850-pensacola", "label": "SCI Pensacola / Florida Panhandle pool"}),
    ("251", {"pool_id": "pool-251-mobile", "label": "SCI Mobile / southwest Alabama pool"}),
    ("706", {"pool_id": "pool-706-columbus", "label": "SCI Columbus GA pool"}),
    ("318", {"pool_id": "pool-318-shreveport", "label": "SCI Shreveport LA pool"}),
])


def pool_for_area_code(area_code: Optional[str]) -> Optional[Dict[str, str]]:
    return POOLS.get((area_code or "").strip())


def sender_pool(area_code: Optional[str]) -> str:
    """Pool a campus sends from. A campus with no verified area code has no
    local sender; the toll-free backup is NOT a silent default."""
    p = pool_for_area_code(area_code)
    if p is None:
        raise LookupError("no regional pool for area code %r (location unverified)" % (area_code,))
    return p["pool_id"]


def pool_for_phone_number(rec) -> Optional[Dict[str, str]]:
    """The pool a PhoneNumber row belongs to, or None (not a pool number)."""
    label = getattr(rec, "label", None) or ""
    if not label.startswith(POOL_LABEL_PREFIX):
        return None
    pid = label[len(POOL_LABEL_PREFIX):]
    return next((p for p in POOLS.values() if p["pool_id"] == pid), None)


def pool_members(rows: List[Dict]) -> Dict[str, List[str]]:
    """pool id -> entity names, from the campus grouping rows. Entities with
    no area code (unverified) belong to no pool."""
    out: Dict[str, List[str]] = {p["pool_id"]: [] for p in POOLS.values()}
    for r in rows:
        p = pool_for_area_code(r.get("Area Code"))
        if p:
            out[p["pool_id"]].append(r["Location"])
    return out


def route_inbound(sender_location: Optional[str], pool_id: str) -> Dict[str, Optional[str]]:
    """Routing decision for an inbound text or call on a pool number: the
    known contact's own location, else the regional review queue."""
    if sender_location:
        return {"queue": None, "location": sender_location, "pool_id": pool_id}
    return {"queue": "regional_review:%s" % pool_id, "location": None, "pool_id": pool_id}
