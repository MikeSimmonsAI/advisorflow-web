"""Destination: EvoSys BookIt (#470 Stage B).

Businesses EvoSys sells ITS OWN software to - for example cleaning companies
for the $250 Growth Pilot - land as `Opportunity` rows in one brand sales
organization, stage Prospect, owned by the person importing.

  * Who: God, or a sales MANAGER of that brand sales org. Checked on every call.
  * Purpose, not industry: an EvoSys BookIt prospect is a company EvoSys sells
    its own software to. The same business may separately be in a cleaning
    customer's pipeline (as someone who might buy cleaning); that never blocks
    the row and nothing is copied or moved - the platform owner just sees a note.
  * Match before create, inside the chosen brand sales org only: source record
    id -> the id quoted in an existing prospect's source text (the 15 FB-CLEAN
    prospects entered by hand) -> phone -> website -> company name.
  * A match is never overwritten. Optionally the file's notes and intro draft
    are ADDED to the matched prospect's timeline, once.
  * New prospects get no next action and no follow-up: a person decides.
    Do-Not-Contact matches are held, not created.
  * Notes, contact preference, email status, phone line type and the intro
    message are written to the timeline. The intro is labelled a draft; it is
    not a template, not scheduled, not sent.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.sales_models import BrandSalesOrg, Opportunity, OpportunityEvent, STAGE_PROSPECT
from app.services.intake import business_import as BI

PIPELINE = "evosys_sales"
DESTINATION = "evosys_sales"
IMPORT_MARK = "[import:%s]"
_IMPORT_EVENT_TYPES = ("created", "note")


def can_import(db: Session, user, brand_sales_org_id: str) -> bool:
    from app.services.sales_access import is_sales_manager, sales_org_ids
    return bool(brand_sales_org_id) and brand_sales_org_id in sales_org_ids(user, db) and \
        is_sales_manager(user, db, brand_sales_org_id)


def targets(db: Session, user) -> List[Dict[str, Any]]:
    from app.services.sales_access import sales_org_ids
    out = []
    for bid in sales_org_ids(user, db):
        if not can_import(db, user, bid):
            continue
        org = db.query(BrandSalesOrg).filter(BrandSalesOrg.id == bid).first()
        if org:
            out.append({"id": org.id, "name": getattr(org, "name", None) or org.id})
    return out


def _brand(db: Session, b) -> BrandSalesOrg:
    org = db.query(BrandSalesOrg).filter(BrandSalesOrg.id == BI.target_id(b)).first()
    if org is None:
        raise BI.BusinessImportError("Brand sales organization not found")
    return org


# ── "Is this business already a lead?" ─────────────────────────────────────
#
# SPEED (owner request 2026-11-23). This check runs for every row, twice (when
# the file is checked and again when it is saved). It used to make five passes
# over the team's whole lead table per row - "contains" searches the database
# cannot shortcut - each loading up to 200 full lead records to compare one
# field. Imports slowed down as the table grew.
#
# Now the team's leads are read ONCE per request (id + the five compared
# fields only) into a lookup kept on the database session. Leads this import
# creates are added to it as they are written, and it is re-read every
# _INDEX_TTL seconds so leads added meanwhile by anyone else are picked up.
# A hit is never trusted from the list alone: the lead is re-read by id and
# the same rule checked again on its current values, so a stale list can't
# wrongly block a new lead. The old 200/50/20 caps are gone (they could hide a
# real duplicate), so this finds at least what the old check found.

_INDEX_TTL = 60.0


class _DupIndex:
    def __init__(self, db: Session, bid: str):
        self.bid, self.built_at = bid, time.monotonic()
        self.sid: Dict[str, str] = {}
        self.sources: List[Tuple[str, str, str]] = []      # (id, source, source lowercased)
        self.phone: Dict[str, str] = {}
        self.domain: Dict[str, str] = {}
        self.name: Dict[str, List[Tuple[str, str]]] = {}   # company_key -> [(id, company_name)]
        for row in (db.query(Opportunity.id, Opportunity.source_record_id, Opportunity.source, Opportunity.phone,
                             Opportunity.website, Opportunity.company_name)
                    .filter(Opportunity.brand_sales_org_id == bid)
                    .order_by(Opportunity.created_at, Opportunity.id).all()):
            self.add(*row)

    def add(self, oid, sid, source, phone, website, company_name) -> None:
        if sid:
            self.sid.setdefault(sid.lower(), oid)
        if source:
            self.sources.append((oid, source, source.lower()))
        p10 = BI.digits10(phone)
        if p10:
            self.phone.setdefault(p10, oid)
        dom = BI.domain_of(website)
        if dom:
            self.domain.setdefault(dom, oid)
        ck = BI.company_key(company_name)
        if ck:
            self.name.setdefault(ck, []).append((oid, company_name or ""))


def _index(db: Session, bid: str) -> _DupIndex:
    store = db.info.setdefault("sales_import_dup_index", {})
    ix = store.get(bid)
    if ix is None or time.monotonic() - ix.built_at > _INDEX_TTL:
        ix = store[bid] = _DupIndex(db, bid)
    return ix


def _ilike_prefix(prefix: str):
    """Python twin of SQL `ILIKE prefix || '%'` (the old name check)."""
    pat = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in prefix)
    return re.compile(pat, re.IGNORECASE | re.DOTALL)


def _rules(b, n: Dict[str, Any], o) -> Optional[str]:
    """The old checks, in the old order, on ONE lead's current values."""
    sid = n.get("source_record_id")
    if sid and (o.source_record_id or "").lower() == sid.lower():
        return "same source record ID (%s)" % sid
    if sid and sid.lower() in (o.source or "").lower() and BI.id_in_text(sid, o.source):
        return "source text already names %s" % sid
    if n.get("phone10") and BI.digits10(o.phone) == n["phone10"]:
        return "same phone"
    if n.get("domain") and BI.domain_of(o.website) == n["domain"]:
        return "same website"
    ck = BI.company_key(n.get("business_name"))
    first = (n.get("business_name") or "").strip()[:3]
    if ck and _ilike_prefix(first).match(o.company_name or "") and BI.company_key(o.company_name) == ck:
        return "same company name"
    return None


def _candidates(ix: _DupIndex, n: Dict[str, Any]) -> List[str]:
    """Lead ids the list says could match, best (old check order) first."""
    out: List[str] = []
    sid = n.get("source_record_id")
    if sid:
        if sid.lower() in ix.sid:
            out.append(ix.sid[sid.lower()])
        low = sid.lower()
        out.extend(oid for oid, src, srcl in ix.sources if low in srcl and BI.id_in_text(sid, src))
    if n.get("phone10") and n["phone10"] in ix.phone:
        out.append(ix.phone[n["phone10"]])
    if n.get("domain") and n["domain"] in ix.domain:
        out.append(ix.domain[n["domain"]])
    ck = BI.company_key(n.get("business_name"))
    if ck and ck in ix.name:
        rx = _ilike_prefix((n.get("business_name") or "").strip()[:3])
        out.extend(oid for oid, nm in ix.name[ck] if rx.match(nm))
    return out


def find_existing(db: Session, b, n: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    bid = BI.target_id(b)
    ix = _index(db, bid)
    seen = set()
    for oid in _candidates(ix, n):
        if oid in seen:
            continue
        seen.add(oid)
        o = (db.query(Opportunity.id, Opportunity.source_record_id, Opportunity.source, Opportunity.phone,
                      Opportunity.website, Opportunity.company_name)
             .filter(Opportunity.id == oid, Opportunity.brand_sales_org_id == bid).first())
        if o is None:
            continue
        why = _rules(b, n, o)
        if why:
            return o.id, why
    return None


def related(db: Session, b, n: Dict[str, Any]) -> List[str]:
    """The same business elsewhere, for the platform owner's information only.
    Never blocks the row; never copies or moves anything."""
    from app.models.lead_intel_models import LeadIntelProspect
    from app.models.acquisition_models import AcquisitionOpportunity
    from app.models.models import Lead, Organization
    out = []
    if not n.get("phone10"):
        return out
    e164 = "+1" + n["phone10"]
    if db.query(LeadIntelProspect.id).filter(LeadIntelProspect.phone_e164 == e164).first():
        out.append("Also in the master pool (as a business that might buy cleaning)")
    for (name,) in (db.query(Organization.name).join(AcquisitionOpportunity, AcquisitionOpportunity.organization_id == Organization.id)
                    .join(Lead, Lead.id == AcquisitionOpportunity.lead_id)
                    .filter(Lead.phone.in_([n["phone10"], e164, n.get("phone_raw") or ""])).distinct().limit(5).all()):
        out.append("Also a prospect in %s's Acquisition pipeline" % name)
    return out


def _source_text(b, n: Dict[str, Any]) -> str:
    parts = [n.get("source") or b.source_system, n.get("source_record_id"),
             ", ".join(x for x in (n.get("city"), n.get("state")) if x) or None]
    return " | ".join(p for p in parts if p)[:500]


def _timeline_detail(b, n: Dict[str, Any]) -> str:
    lines = [IMPORT_MARK % (n.get("source_record_id") or b.batch_code), "Imported in %s" % b.batch_code]
    if n.get("notes"):
        lines.append("Source notes: " + n["notes"])
    if n.get("contact_preference_label"):
        lines.append("Contact preference: " + n["contact_preference_label"])
    lines.append("Phone line type: " + (n.get("phone_line_type") or "unknown") + " (not verified as mobile; no texting permission)")
    if n.get("email"):
        lines.append("Email: " + ("verified" if n.get("email_verified") else "not verified"))
    if n.get("tags"):
        lines.append("Tags: " + ", ".join(n["tags"]))
    if n.get("territory_note"):
        lines.append("Territory: " + n["territory_note"])
    return "\n".join(lines)


def _add_events(db: Session, opp: Opportunity, user, b, n: Dict[str, Any], created: bool) -> None:
    detail = _timeline_detail(b, n)
    if created:
        db.add(OpportunityEvent(opportunity_id=opp.id, event_type="created", actor_user_id=user.id,
                                summary="Prospect imported (%s)" % b.batch_code, detail=detail))
    else:
        db.add(OpportunityEvent(opportunity_id=opp.id, event_type="note", actor_user_id=user.id,
                                summary="Source notes added from import %s" % b.batch_code, detail=detail))
    if n.get("intro_draft"):
        db.add(OpportunityEvent(opportunity_id=opp.id, event_type="note", actor_user_id=user.id,
                                summary="Intro message DRAFT - not sent",
                                detail=(IMPORT_MARK % (n.get("source_record_id") or b.batch_code)) + "\n" + n["intro_draft"]))


def write(db: Session, b, user, n: Dict[str, Any], row) -> str:
    from app.services import sales_suppression as SDNC
    org = _brand(db, b)
    if SDNC.find(db, org.id, n.get("phone_raw"), n.get("email")):
        raise BI.BusinessImportError("Matches a Do Not Contact request - not created")
    profile = BI.destination_of(b).get("profile")
    opp = Opportunity(
        brand_sales_org_id=org.id, owner_user_id=user.id,
        company_name=n["business_name"][:300], contact_name=n.get("contact_name"),
        phone=n.get("phone_raw"), email=n.get("email"), website=n.get("website"),
        industry=n.get("industry") or (PROFILES.get(profile) or {}).get("industry"),
        timezone=getattr(org, "timezone", None), source=_source_text(b, n),
        source_system=b.source_system, source_record_id=n.get("source_record_id"), import_batch_id=b.id,
        stage=STAGE_PROSPECT, status="open", next_action=None, next_action_due_at=None,
        stage_changed_at=datetime.utcnow())
    db.add(opp)
    db.flush()
    _add_events(db, opp, user, b, n, created=True)
    _evosys_group(db, org, opp, n)
    ix = db.info.get("sales_import_dup_index", {}).get(org.id)
    if ix is not None:  # later rows of this import see it at once (a failed row is re-checked by id)
        ix.add(opp.id, opp.source_record_id, opp.source, opp.phone, opp.website, opp.company_name)
    return opp.id


def _evosys_group(db: Session, org, opp: Opportunity, n: Dict[str, Any]) -> None:
    """EvoSys BookIt: give the new lead its industry + market group (by rule)
    and flag a possible duplicate for a person to resolve. Never blocks the row."""
    try:
        from app.services.evosys_sales import grouping as G, outreach as O
        if org.id != O.brand_id(db):
            return
        dup = G.possible_duplicate(db, opp)
        G.ensure(db, O.get_settings(db, org.id), opp, city=n.get("city"), state=n.get("state"),
                 extra_text=" ".join(x for x in (n.get("industry"), n.get("notes")) if x), duplicate_of=dup)
    except Exception:  # noqa: BLE001 - grouping can be redone from the Leads screen
        import logging
        logging.getLogger(__name__).exception("EvoSys BookIt grouping failed for %s", opp.id)


def attach_to_matched(db: Session, b, user) -> Dict[str, Any]:
    """For rows that matched an existing prospect: fill a BLANK source record
    id and add the file's notes and intro draft to its timeline, once. Nothing
    on the prospect is overwritten and no history is changed."""
    from app.models.import_models import ImportDuplicateStatus, ImportStagedRow
    out = {"notes_added": 0, "source_id_filled": 0, "already_attached": 0}
    rows = (db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b.id,
                                             ImportStagedRow.duplicate_status == ImportDuplicateStatus.MATCHED_EXISTING)
            .all())
    for r in rows:
        n = (json.loads(r.extra_fields or "{}") or {}).get("business") or {}
        opp = db.query(Opportunity).filter(Opportunity.id == n.get("existing_id"),
                                           Opportunity.brand_sales_org_id == BI.target_id(b)).first()
        if opp is None:
            continue
        sid = n.get("source_record_id")
        if sid and not opp.source_record_id:
            opp.source_record_id = sid
            opp.source_system = opp.source_system or b.source_system
            out["source_id_filled"] += 1
        mark = IMPORT_MARK % (sid or b.batch_code)
        if (db.query(OpportunityEvent.id).filter(OpportunityEvent.opportunity_id == opp.id,
                                                OpportunityEvent.detail.like(mark + "%")).first()):
            out["already_attached"] += 1
            continue
        _add_events(db, opp, user, b, n, created=False)
        out["notes_added"] += 1
    db.commit()
    return out


def undo(db: Session, b, record_id: str) -> bool:
    """Remove a prospect this import created - only if nobody has worked it:
    still Prospect, still open, and its timeline holds only the import's rows."""
    opp = db.query(Opportunity).filter(Opportunity.id == record_id, Opportunity.import_batch_id == b.id).first()
    if opp is None:
        return False
    if opp.stage != STAGE_PROSPECT or opp.status != "open" or opp.contacted_at or opp.next_action:
        return False
    other = (db.query(OpportunityEvent.id).filter(OpportunityEvent.opportunity_id == opp.id,
                                                 ~OpportunityEvent.event_type.in_(_IMPORT_EVENT_TYPES)).first())
    foreign_notes = (db.query(OpportunityEvent.id)
                     .filter(OpportunityEvent.opportunity_id == opp.id,
                             or_(OpportunityEvent.detail.is_(None), ~OpportunityEvent.detail.like("[import:%"))).first())
    if other or foreign_notes:
        return False
    db.query(OpportunityEvent).filter(OpportunityEvent.opportunity_id == opp.id).delete(synchronize_session=False)
    db.delete(opp)
    return True


# Industry profiles for Sales imports (what the prospect is, not a funeral default).
PROFILES: Dict[str, Dict[str, Any]] = {
    "general": {"label": "General business prospect", "industry": None},
    "cleaning_services": {"label": "Cleaning company (Growth Pilot prospect)", "industry": "Cleaning Services"},
}

ADAPTER = BI.Adapter(pipeline=PIPELINE, destination=DESTINATION, label="EvoSys BookIt",
                     find_existing=find_existing, write=write, related=related, undo=undo)


# ── backfill: give the hand-entered prospects their source record id ──────
_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")


def _id_from_source(text: Optional[str]) -> Optional[str]:
    """'Facebook ad | FB-CLEAN-004 | Anytown' -> FB-CLEAN-004. Only a whole
    pipe-separated segment that looks like an id and contains a digit."""
    for seg in (text or "").split("|"):
        s = seg.strip()
        if _ID_RE.match(s) and re.search(r"\d", s):
            return s
    return None


def _system_from_source(text: Optional[str]) -> Optional[str]:
    first = (text or "").split("|")[0].strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", first).strip("_")[:60] or None


def backfill_preview(db: Session, brand_sales_org_id: str, prefix: Optional[str] = None) -> Dict[str, Any]:
    rows = (db.query(Opportunity).filter(Opportunity.brand_sales_org_id == brand_sales_org_id,
                                         Opportunity.source.isnot(None)).all())
    found: Dict[str, List[Opportunity]] = {}
    for o in rows:
        sid = _id_from_source(o.source)
        if sid and (not prefix or sid.startswith(prefix)):
            found.setdefault(sid, []).append(o)
    items, conflicts = [], []
    for sid, opps in sorted(found.items()):
        if len(opps) > 1:
            conflicts.append({"source_record_id": sid, "opportunity_ids": [o.id for o in opps],
                              "why": "more than one prospect names this id - left alone"})
            continue
        o = opps[0]
        state = "already_set" if o.source_record_id == sid else \
            "different_id_set" if o.source_record_id else "will_fill"
        items.append({"opportunity_id": o.id, "company": o.company_name, "source": o.source,
                      "source_record_id": sid, "source_system": o.source_system or _system_from_source(o.source),
                      "current_source_record_id": o.source_record_id, "action": state})
    return {"items": items, "conflicts": conflicts,
            "will_fill": sum(1 for i in items if i["action"] == "will_fill")}


def backfill_apply(db: Session, brand_sales_org_id: str, user, prefix: Optional[str] = None) -> Dict[str, Any]:
    """Fill ONLY blank source_record_id / source_system. Changes no other field
    and adds one timeline note per prospect so the change is visible."""
    prev = backfill_preview(db, brand_sales_org_id, prefix)
    filled = 0
    for it in prev["items"]:
        if it["action"] != "will_fill":
            continue
        o = db.query(Opportunity).filter(Opportunity.id == it["opportunity_id"]).first()
        if o is None or o.source_record_id:
            continue
        o.source_record_id = it["source_record_id"]
        if not o.source_system:
            o.source_system = it["source_system"]
        db.add(OpportunityEvent(opportunity_id=o.id, event_type="note", actor_user_id=user.id,
                                summary="Source record ID recorded: %s" % it["source_record_id"],
                                detail="Read from this prospect's own source text. Nothing else changed."))
        filled += 1
    db.commit()
    return dict(prev, filled=filled)
