"""Stage a customer source file into a program. Dry-run by default.

WHAT A RUN DOES
    1. Parses the file. Every source Lead ID is kept; a repeated or blank ID is
       an error the run reports (it never silently drops a row).
    2. Resolves each row's location from its source location name. A row
       whose name is blank or unknown goes to LOCATION REVIEW - nothing is
       guessed.
    3. Links duplicates under a contact master. AUTO-LINK only when the
       NORMALISED PERSON NAME matches AND the phone or the email matches
       exactly. Shared household phones/emails are real in this data, so the
       same phone or email under a DIFFERENT name is DUPLICATE REVIEW, never a
       merge. Nothing is deleted; every Lead ID keeps its own record.
    4. Flags operational notes typed into name fields ("DISQUALIFIED",
       "NOT INTERESTED", "FULLY PREPLANNED") as SOURCE DATA NOTE DETECTED -
       REVIEW. The source status is NOT changed because of them.
    5. Maps each row's source campaign to a campaign family.

dry_run=True (the default) computes and returns the summary and writes ONLY
an audit row for the run. dry_run=False writes the ProgramSourceRecord
staging rows (idempotent on the Lead ID: a re-run updates DECISIONS, never
the original values). Neither mode creates a lead or a contact, enrols
anyone, or sends anything.
"""
import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization
from app.models.program_models import (
    CampaignFamily, LocationProfile, ProgramImportRun, ProgramSourceRecord,
)
from app.services.programs.normalize import (
    DATA_NOTE_LABEL, data_notes, email_key, location_key, person_key, phone_key,
)
from app.services.programs.setup import REVIEW_BUCKET_NAME

COL = {
    "lead_id": "Lead ID", "manager": "Lead Owner: Manager", "owner": "Lead Owner",
    "first": "First Name", "last": "Last Name", "email": "Email",
    "status": "Lead Status", "last_activity": "Last Activity",
    "last_activity_date": "Last Activity Date", "phone": "Phone",
    "campaign": "Primary Campaign", "channel": "Campaign Channel",
    "created": "Create Date", "location": "Location Friendly Name",
}


def parse_csv(content: bytes) -> List[Dict[str, str]]:
    text = content.decode("utf-8-sig", errors="replace")
    return [dict(r) for r in csv.DictReader(io.StringIO(text))]


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def _family_for(campaign: str, families: List[CampaignFamily]) -> Optional[str]:
    c = (campaign or "").lower()
    for fam in families:
        for pat in json.loads(fam.source_campaign_patterns or "[]"):
            if pat.lower() in c:
                return fam.key
    return None


def analyze(rows: List[Dict[str, str]], profiles: Dict[str, LocationProfile],
            families: List[CampaignFamily]) -> Dict:
    """Pure decision pass. Returns per-row decisions plus a summary."""
    errors = []
    ids = [((r.get(COL["lead_id"]) or "").strip()) for r in rows]
    seen = Counter(ids)
    for i, lid in enumerate(ids):
        if not lid:
            errors.append({"row": i + 2, "error": "blank Lead ID"})
    for lid, n in seen.items():
        if lid and n > 1:
            errors.append({"lead_id": lid, "error": "Lead ID appears %d times" % n})

    # name -> profile, by every source spelling the profile carries
    by_name = {}
    review = None
    for prof in profiles.values():
        if prof.is_review_bucket:
            review = prof
            continue
        for n in json.loads(prof.source_names or "[]") + [prof.official_name]:
            by_name[location_key(n)] = prof

    decisions = []
    for i, r in enumerate(rows):
        first, last = r.get(COL["first"]), r.get(COL["last"])
        prof = by_name.get(location_key(r.get(COL["location"])))
        notes = data_notes(first, last)
        decisions.append({
            "row": i + 2, "lead_id": ids[i],
            "person": person_key(first, last),
            "phone": phone_key(r.get(COL["phone"])),
            "email": email_key(r.get(COL["email"])),
            "location_profile": prof, "review_profile": review,
            "location_status": "mapped" if prof else "location_review",
            "family": _family_for(r.get(COL["campaign"]), families),
            "notes": notes,
        })

    # STRONG links: same person AND (same phone OR same email).
    uf = _UF()
    by_np, by_ne = defaultdict(list), defaultdict(list)
    for d in decisions:
        uf.find(d["row"])
        if d["person"] and d["phone"]:
            by_np[(d["person"], d["phone"])].append(d["row"])
        if d["person"] and d["email"]:
            by_ne[(d["person"], d["email"])].append(d["row"])
    for group in list(by_np.values()) + list(by_ne.values()):
        for other in group[1:]:
            uf.union(group[0], other)

    # REVIEW: shared phone/email across DIFFERENT people (households, typos).
    by_phone, by_email = defaultdict(set), defaultdict(set)
    for d in decisions:
        if d["phone"]:
            by_phone[d["phone"]].add(d["row"])
        if d["email"]:
            by_email[d["email"]].add(d["row"])
    review_reason = defaultdict(list)
    row_of = {d["row"]: d for d in decisions}
    for kind, idx in (("phone", by_phone), ("email", by_email)):
        for key, members in idx.items():
            masters = {uf.find(m) for m in members}
            if len(masters) > 1:
                for m in members:
                    others = sorted(row_of[o]["lead_id"] for o in members if uf.find(o) != uf.find(m))
                    if others:
                        review_reason[m].append("same %s as %s under a different name"
                                                % (kind, ", ".join(others[:5])))

    groups = defaultdict(list)
    for d in decisions:
        groups[uf.find(d["row"])].append(d)
    for root, members in groups.items():
        master_id = row_of[root]["lead_id"]
        for d in members:
            d["master_key"] = master_id
            if len(members) == 1:
                d["link_status"], d["link_reason"] = "unique", None
            elif d["row"] == root:
                d["link_status"], d["link_reason"] = "primary", "contact master for %d source rows" % len(members)
            else:
                d["link_status"] = "linked"
                d["link_reason"] = "same name and same phone/email as %s" % master_id
            if review_reason.get(d["row"]):
                d["link_status"] = "duplicate_review" if d["link_status"] in ("unique", "primary") and len(members) == 1 else d["link_status"]
                d["dup_review"] = "; ".join(review_reason[d["row"]])
            else:
                d["dup_review"] = None

    status = Counter((r.get(COL["status"]) or "").strip() or "(blank)" for r in rows)
    summary = {
        "source_rows": len(rows),
        "distinct_lead_ids": len([k for k in seen if k]),
        "errors": errors,
        "with_email": sum(1 for d in decisions if d["email"]),
        "with_phone": sum(1 for d in decisions if d["phone"]),
        "location_mapped": sum(1 for d in decisions if d["location_status"] == "mapped"),
        "location_review": sum(1 for d in decisions if d["location_status"] == "location_review"),
        "locations_used": len({d["location_profile"].id for d in decisions if d["location_profile"]}),
        "contact_masters": len(groups),
        "auto_linked_rows": sum(1 for d in decisions if d["link_status"] == "linked"),
        "duplicate_review_rows": sum(1 for d in decisions if d.get("dup_review")),
        "data_note_rows": sum(1 for d in decisions if d["notes"]),
        "data_note_label": DATA_NOTE_LABEL,
        "status_counts": dict(status),
        "family_counts": dict(Counter(d["family"] or "(unmapped)" for d in decisions)),
        "review_bucket": REVIEW_BUCKET_NAME,
    }
    return {"decisions": decisions, "summary": summary}


def stage(db: Session, org: Organization, content: bytes, *, filename: Optional[str] = None,
          dry_run: bool = True, actor_id: Optional[str] = None) -> Dict:
    rows = parse_csv(content)
    profiles = {p.id: p for p in db.query(LocationProfile)
                .filter(LocationProfile.organization_id == org.id).all()}
    families = db.query(CampaignFamily).filter(CampaignFamily.organization_id == org.id).all()
    result = analyze(rows, profiles, families)
    summary = result["summary"]
    run = ProgramImportRun(organization_id=org.id, filename=filename,
                           sha256=hashlib.sha256(content).hexdigest(), dry_run=dry_run,
                           created_by=actor_id)
    db.add(run)
    db.flush()
    if summary["errors"] and not dry_run:
        summary["staged"] = 0
        summary["refused"] = "the file has blank or repeated Lead IDs; nothing was staged"
        run.summary_json = json.dumps(summary)
        db.commit()
        return {"run_id": run.id, "dry_run": dry_run, "summary": summary}

    staged = 0
    if not dry_run:
        existing = {s.source_lead_id: s for s in db.query(ProgramSourceRecord)
                    .filter(ProgramSourceRecord.organization_id == org.id).all()}
        for r, d in zip(rows, result["decisions"]):
            rec = existing.get(d["lead_id"])
            if rec is None:
                # ORIGINAL VALUES: written once, here, and never again.
                rec = ProgramSourceRecord(
                    organization_id=org.id, source_lead_id=d["lead_id"], row_number=d["row"],
                    raw_json=json.dumps(r, ensure_ascii=False),
                    first_name=r.get(COL["first"]), last_name=r.get(COL["last"]),
                    email=r.get(COL["email"]), phone=r.get(COL["phone"]),
                    source_status=r.get(COL["status"]), source_campaign=r.get(COL["campaign"]),
                    source_channel=r.get(COL["channel"]),
                    source_location_name=r.get(COL["location"]),
                    source_owner=r.get(COL["owner"]), source_manager=r.get(COL["manager"]),
                )
                db.add(rec)
            prof = d["location_profile"] or d["review_profile"]
            rec.import_run_id = run.id
            if not rec.location_assigned_manually:
                rec.location_id = prof.location_id if prof else None
                rec.location_status = d["location_status"]
            rec.campaign_family = d["family"]
            rec.contact_master_key = d["master_key"]
            rec.link_status = d["link_status"]
            rec.link_reason = d["link_reason"] if not rec.duplicate_review_cleared_at else rec.link_reason
            if not rec.duplicate_review_cleared_at:
                rec.duplicate_review_reason = d.get("dup_review")
            # Flags are MERGED, never replaced: a flag raised later (a reply
            # saying "wrong person") is not erased by re-staging the file.
            flags = json.loads(rec.data_note_flags or "[]")
            for f in d["notes"]:
                if f not in flags:
                    flags.append(f)
            rec.data_note_flags = json.dumps(flags) if flags else None
            new_notes = [f for f in d["notes"]] if not rec.data_review_cleared_at else []
            rec.needs_data_review = bool(rec.needs_data_review or new_notes)
            staged += 1
    summary["staged"] = staged
    run.summary_json = json.dumps(summary)
    db.commit()
    return {"run_id": run.id, "dry_run": dry_run, "summary": summary}
