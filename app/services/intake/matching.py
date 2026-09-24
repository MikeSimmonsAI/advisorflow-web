"""Duplicate detection and existing-record matching.

Two questions, answered with the same rules:

  A. Is this row a duplicate of an EARLIER ROW IN THE SAME FILE?
  B. Does this row match a record the ORGANIZATION ALREADY HAS
     (an org contact or a lead)?

Each answer is EXACT, POSSIBLE or NEW. Only EXACT is ever acted on without a
person: an exact in-file duplicate is not imported twice, and an exact
existing match updates the existing record under the batch's update policy
instead of creating a second one. POSSIBLE always goes to review. Nothing
here merges anything.

KEYS, STRONGEST FIRST
  1. source system + source record id   exact on its own
  2. email                              exact unless the names disagree
  3. mobile / phone                     exact only if the identity agrees
                                        positively (same surname, or same
                                        company on a nameless record); a
                                        number shared by two different
                                        surnames is a shared line, not a match
  4. company + street address / ZIP     possible only
  5. first + last name + company        possible only

TENANT RULE
  `load_existing` takes an organization id and every query it runs filters on
  it. There is no code path here that can see another tenant's records.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.intake_models import MatchType, OrgContact

_CHUNK = 900
_DNC_STATUSES = {"dnc", "do_not_contact", "deceased"}


def identity_conflict(a: dict, b: dict) -> bool:
    """True when two records positively disagree about who they are."""
    if a.get("last_key") and b.get("last_key") and a["last_key"] != b["last_key"]:
        return True
    if (a.get("last_key") and a.get("last_key") == b.get("last_key")
            and a.get("first_key") and b.get("first_key")
            and a["first_key"][:1] != b["first_key"][:1]):
        return True
    no_names = not a.get("last_key") and not b.get("last_key")
    if no_names and a.get("company_key") and b.get("company_key") \
            and a["company_key"] != b["company_key"]:
        return True
    return False


def identity_agrees(a: dict, b: dict) -> bool:
    """True when two records positively AGREE about who they are."""
    if identity_conflict(a, b):
        return False
    if a.get("last_key") and a.get("last_key") == b.get("last_key"):
        return True
    if (not a.get("last_key") and not b.get("last_key")
            and not a.get("first_key") and not b.get("first_key")
            and a.get("company_key") and a.get("company_key") == b.get("company_key")):
        return True
    return False


def _phones(r: dict) -> List[str]:
    return [p for p in (r.get("phone"), r.get("mobile")) if p]


def compare(row: dict, other: dict) -> Tuple[str, List[str], List[str]]:
    """Compare two identity dicts. Returns (match_type, keys_matched, notes)."""
    keys: List[str] = []
    notes: List[str] = []
    if row.get("src_id"):
        key = (row.get("src_system") or "", row["src_id"])
        other_keys = set(other.get("src_all") or ())
        if other.get("src_id"):
            other_keys.add((other.get("src_system") or "", other["src_id"]))
        if key in other_keys:
            return MatchType.EXACT, ["source_record_id"], notes
    if row.get("email") and row["email"] == other.get("email"):
        keys.append("email")
        if not identity_conflict(row, other):
            return MatchType.EXACT, keys, notes
        return MatchType.POSSIBLE, keys, ["email_shared_by_different_names"]
    shared = set(_phones(row)) & set(_phones(other))
    if shared:
        keys.append("mobile" if row.get("mobile") in shared else "phone")
        email_clash = bool(row.get("email") and other.get("email")
                           and row["email"] != other["email"])
        if identity_agrees(row, other) and not email_clash:
            return MatchType.EXACT, keys, notes
        if identity_conflict(row, other):
            # A shared line - an office switchboard, a household landline.
            return MatchType.NEW, [], ["shares_phone_with_other_record"]
        return MatchType.POSSIBLE, keys, ["phone_match_identity_unconfirmed"]
    if row.get("company_key") and row["company_key"] == other.get("company_key"):
        if identity_conflict(row, other):
            # Two different named people at the same company are colleagues,
            # not duplicates.
            return MatchType.NEW, [], notes
        clash = channel_conflict(row, other)
        nameless = not any((row.get("last_key"), other.get("last_key"),
                            row.get("first_key"), other.get("first_key")))
        if row.get("addr_key") and row["addr_key"] == other.get("addr_key"):
            # Same company, same street address, nobody named, no channel that
            # disagrees: nothing distinguishes the two records at all.
            if nameless and not clash:
                return MatchType.EXACT, ["company", "address"], notes
            return MatchType.POSSIBLE, ["company", "address"], notes
        if (row.get("last_key") and row["last_key"] == other.get("last_key")
                and row.get("first_key") and row["first_key"] == other.get("first_key")):
            if not clash:
                return MatchType.EXACT, ["company", "name"], notes
            return MatchType.POSSIBLE, ["company", "name"], notes
        if (nameless and not clash and row.get("zip5") and row.get("zip5") == other.get("zip5")
                and not (row.get("addr_key") and other.get("addr_key"))):
            return MatchType.POSSIBLE, ["company", "zip"], notes
    return MatchType.NEW, [], notes


def channel_conflict(a: dict, b: dict) -> bool:
    """Both records carry a channel of the same kind and the values differ."""
    if a.get("email") and b.get("email") and a["email"] != b["email"]:
        return True
    pa, pb = set(_phones(a)), set(_phones(b))
    if pa and pb and not (pa & pb):
        return True
    return False


class _Index:
    """Key -> list of candidate identity dicts, for one population."""

    def __init__(self):
        self.by = {"src": {}, "email": {}, "phone": {}, "company": {}}

    def add(self, r: dict):
        if r.get("src_id"):
            self.by["src"].setdefault((r.get("src_system") or "", r["src_id"]), []).append(r)
        if r.get("email"):
            self.by["email"].setdefault(r["email"], []).append(r)
        for p in _phones(r):
            self.by["phone"].setdefault(p, []).append(r)
        if r.get("company_key"):
            self.by["company"].setdefault(r["company_key"], []).append(r)

    def candidates(self, r: dict) -> List[dict]:
        seen, out = set(), []

        def take(lst):
            for c in lst or []:
                if id(c) not in seen:
                    seen.add(id(c))
                    out.append(c)
        if r.get("src_id"):
            take(self.by["src"].get((r.get("src_system") or "", r["src_id"])))
        if r.get("email"):
            take(self.by["email"].get(r["email"]))
        for p in _phones(r):
            take(self.by["phone"].get(p))
        if r.get("company_key"):
            take(self.by["company"].get(r["company_key"])[:50]
                 if self.by["company"].get(r["company_key"]) else None)
        return out


_RANK = {MatchType.EXACT: 2, MatchType.POSSIBLE: 1, MatchType.NEW: 0}


def best_match(row: dict, index: _Index) -> Tuple[str, Optional[dict], List[str], List[str]]:
    best = (MatchType.NEW, None, [], [])
    notes_all: List[str] = []
    for cand in index.candidates(row):
        if cand is row:
            continue
        mt, keys, notes = compare(row, cand)
        notes_all.extend(n for n in notes if n not in notes_all)
        if _RANK[mt] > _RANK[best[0]] or (
                _RANK[mt] == _RANK[best[0]] and mt != MatchType.NEW and best[1] is not None
                and cand.get("order", 0) < best[1].get("order", 0)):
            best = (mt, cand, keys, [])
        if mt == MatchType.EXACT and "source_record_id" in keys:
            break
    return best[0], best[1], best[2], notes_all


def within_batch(rows: List[dict]) -> None:
    """Annotate each row dict in place with dup_type / dup_of / dup_keys /
    dup_notes, comparing only against EARLIER rows (the first occurrence is
    the one that survives)."""
    idx = _Index()
    for r in rows:
        mt, other, keys, notes = best_match(r, idx)
        r["dup_type"] = mt
        r["dup_of"] = other.get("order") if (other and mt != MatchType.NEW) else None
        r["dup_keys"] = keys
        r["dup_notes"] = notes
        idx.add(r)


# ── existing organization data ───────────────────────────────────────────────

def _chunks(values: Iterable, n: int = _CHUNK):
    vals = [v for v in values if v]
    for i in range(0, len(vals), n):
        yield vals[i:i + n]


def contact_identity(c: OrgContact) -> dict:
    from app.services.intake import normalize as N
    return {
        "kind": "org_contact", "id": c.id, "lead_id": c.lead_id,
        "src_system": c.source_system or "", "src_id": c.source_record_id,
        "email": c.email, "phone": c.phone, "mobile": c.mobile_phone,
        "first_key": N.name_key(c.first_name), "last_key": N.name_key(c.last_name),
        "company_key": c.company_norm, "addr_key": N.address_key(c.street_address),
        "zip5": N.zip5(c.zip_code), "record_class": c.record_class,
        "classification": c.classification, "status": None, "order": 0,
        "archived": c.archived_at is not None,
    }


def lead_identity(l) -> dict:
    from app.services.intake import normalize as N
    return {
        "kind": "lead", "id": l.id, "lead_id": l.id,
        "org_contact_id": getattr(l, "org_contact_id", None),
        "src_system": "", "src_id": None,
        "email": (l.email or "").strip().lower() or None, "phone": l.phone, "mobile": None,
        "first_key": N.name_key(l.first_name), "last_key": N.name_key(l.last_name),
        "company_key": None, "addr_key": N.address_key(l.street_address),
        "zip5": N.zip5(l.zip_code), "record_class": "lead", "classification": None,
        "status": (l.status or "").lower(), "order": 1,
        "dnc": (l.status or "").lower() in _DNC_STATUSES,
        "allow_email": getattr(l, "allow_email", None),
        "allow_sms": getattr(l, "allow_sms", None),
        "manual_flag": getattr(l, "manual_flag", None),
        "archived": False,
    }


def load_existing(db: Session, organization_id: str, rows: List[dict]) -> _Index:
    """Load every org contact and lead IN THIS ORGANIZATION that shares any
    key with the staged rows, in bounded IN-queries. Returns an index."""
    from app.models.models import Lead
    if not organization_id:
        raise ValueError("load_existing requires an organization id")

    src_ids = {r["src_id"] for r in rows if r.get("src_id")}
    emails = {r["email"] for r in rows if r.get("email")}
    phones = set()
    for r in rows:
        phones.update(_phones(r))
    companies = {r["company_key"] for r in rows if r.get("company_key")}

    found: Dict[str, OrgContact] = {}
    base = db.query(OrgContact).filter(OrgContact.organization_id == organization_id,
                                       OrgContact.archived_at.is_(None))
    from app.models.intake_models import OrgContactSourceId
    alt_ids: Dict[str, List[Tuple[str, str]]] = {}
    for ch in _chunks(src_ids):
        for c in base.filter(OrgContact.source_record_id.in_(ch)).all():
            found[c.id] = c
        for sid in (db.query(OrgContactSourceId)
                    .filter(OrgContactSourceId.organization_id == organization_id,
                            OrgContactSourceId.source_record_id.in_(ch)).all()):
            alt_ids.setdefault(sid.org_contact_id, []).append(
                (sid.source_system or "", sid.source_record_id))
    for ch in _chunks(set(alt_ids) - set(found)):
        for c in base.filter(OrgContact.id.in_(ch)).all():
            found[c.id] = c
    for ch in _chunks(emails):
        for c in base.filter(OrgContact.email.in_(ch)).all():
            found[c.id] = c
    for ch in _chunks(phones):
        for c in base.filter(OrgContact.phone.in_(ch)).all():
            found[c.id] = c
        for c in base.filter(OrgContact.mobile_phone.in_(ch)).all():
            found[c.id] = c
    for ch in _chunks(companies):
        for c in base.filter(OrgContact.company_norm.in_(ch)).all():
            found[c.id] = c

    leads = {}
    lbase = db.query(Lead).filter(Lead.organization_id == organization_id)
    for ch in _chunks(emails):
        for l in lbase.filter(Lead.email.in_(ch)).all():
            leads[l.id] = l
    for ch in _chunks(phones):
        for l in lbase.filter(Lead.phone.in_(ch)).all():
            leads[l.id] = l

    # The leads behind matched contacts, so a DNC on the lead blocks the row
    # even when the lead itself shares no key with the file.
    missing = {c.lead_id for c in found.values() if c.lead_id and c.lead_id not in leads}
    for ch in _chunks(missing):
        for l in lbase.filter(Lead.id.in_(ch)).all():
            leads[l.id] = l

    idx = _Index()
    linked_lead_ids = set()
    for c in found.values():
        ident = contact_identity(c)
        if c.lead_id and c.lead_id in leads:
            ident["dnc"] = (leads[c.lead_id].status or "").lower() in _DNC_STATUSES
        linked_lead_ids.add(c.lead_id)
        idx.add(ident)
        # Merged-in source ids resolve to the same contact identity.
        for system, sid in alt_ids.get(c.id, []):
            if sid != c.source_record_id:
                ident.setdefault("src_all", set()).add((system, sid))
                idx.by["src"].setdefault((system, sid), []).append(ident)
    for l in leads.values():
        if l.id in linked_lead_ids:
            continue           # already represented by its org contact
        idx.add(lead_identity(l))
    return idx


def match_existing(row: dict, index: _Index) -> Tuple[str, Optional[dict], List[str], List[str]]:
    return best_match(row, index)
