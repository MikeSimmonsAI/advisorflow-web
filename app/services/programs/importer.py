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
import re
from collections import Counter, defaultdict
from datetime import datetime
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

# A normal export, as the customer's system writes it (#385): the location
# column goes by several names, and SCI's own export puts a 4-digit location
# number in front of the name ("4394 - Southern Heritage Funeral Home").
COLUMN_ALIASES = {
    "lead_id": ("Lead ID", "LeadID", "Lead Id", "Source Lead ID"),
    "location": ("Location Friendly Name", "Nearest/Preferred Location", "Preferred Location",
                 "Nearest Location", "Location Name", "Location"),
    "campaign": ("Primary Campaign", "Campaign", "Campaign Name"),
}
IMPORT_FLAG_COL = "SCI Import Flag"          # set by the Intelligent Importer bridge
GENERATED_PREFIX = "GEN-"                     # never mistaken for a customer's CRM Lead ID
_LOC_NUMBER = re.compile(r"^\s*\d{3,5}\s*[-–:]\s*")


def strip_location_number(name: Optional[str]) -> str:
    """'4394 - Southern Heritage Funeral Home' -> 'Southern Heritage Funeral Home'.
    Only a leading location number is removed; the name is never guessed at
    (Kilgroe stays Kilgroe - it is not Kilgore Green)."""
    return _LOC_NUMBER.sub("", name or "").strip()


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
    return [dict(r) for r in csv.DictReader(io.StringIO(text))
            if any((v or "").strip() for v in r.values() if isinstance(v, str))]


def _val(r: Dict[str, str], key: str) -> str:
    """The row's value for one SCI column, under any of the names it goes by."""
    for h in COLUMN_ALIASES.get(key, (COL[key],)):
        v = r.get(h)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _fingerprint(first, last, phone, email, location) -> str:
    return "|".join([person_key(first, last), phone_key(phone) or "", email_key(email) or "",
                     location_key(strip_location_number(location))])


def assign_lead_ids(rows: List[Dict[str, str]], scope: str = "") -> List[Dict]:
    """[{lead_id, generated, repeat_of}] for every row (#385).

    A row with its own Lead ID keeps it. A row with none gets a DETERMINISTIC
    generated identifier (GEN- + a hash of who the row is and where it sits
    among identical rows), so re-importing the same file gives the same IDs -
    and it is never presented as a CRM Lead ID. A Lead ID the file repeats
    stays on its first row; a later row gets '<id>-R2', '-R3'... and is flagged
    for review - nothing is dropped and nothing refuses the whole file.

    scope: the campaign the file is loaded into. With none chosen, the row's own
    campaign text. It is part of the hash, so the same person on two campaigns'
    files is two source rows - never one overwritten by the other."""
    out, seen_ids, seen_fp = [], Counter(), Counter()
    first_row = {}
    for i, r in enumerate(rows):
        lid = _val(r, "lead_id")
        if lid:
            seen_ids[lid] += 1
            if seen_ids[lid] == 1:
                first_row[lid] = i + 2
                out.append({"lead_id": lid, "generated": False, "repeat_of": None})
            else:
                out.append({"lead_id": "%s-R%d" % (lid, seen_ids[lid]), "generated": True,
                            "repeat_of": "repeats Lead ID %s (row %d)" % (lid, first_row[lid])})
            continue
        fp = _fingerprint(_val(r, "first"), _val(r, "last"), _val(r, "phone"), _val(r, "email"),
                          _val(r, "location")) + "|" + (_val(r, "created") or "") \
            + "|" + (scope or _val(r, "campaign") or "").strip().lower()
        seen_fp[fp] += 1
        h = hashlib.sha256(("%s#%d" % (fp, seen_fp[fp])).encode("utf-8")).hexdigest()[:16].upper()
        out.append({"lead_id": GENERATED_PREFIX + h, "generated": True, "repeat_of": None})
    return out


def rows_fingerprint(r: Dict[str, str]) -> str:
    return _fingerprint(_val(r, "first"), _val(r, "last"), _val(r, "phone"), _val(r, "email"),
                        _val(r, "location"))


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
            families: List[CampaignFamily], *, campaign_family: Optional[str] = None) -> Dict:
    """Pure decision pass. Returns per-row decisions plus a summary.

    #385: no row refuses the file. A missing Lead ID is generated (and marked
    as generated), a repeated one is kept and flagged, the location is read
    from any of its column names with the location number stripped, and the
    campaign is the one the person chose for this file (campaign_family) or
    the file's own campaign column - never a guessed fallback."""
    errors = []
    ids_info = assign_lead_ids(rows, campaign_family or "")
    ids = [x["lead_id"] for x in ids_info]
    seen = Counter(ids)

    # name -> profile, by every source spelling the profile carries
    by_name = {}
    review = None
    for prof in profiles.values():
        if prof.is_review_bucket:
            review = prof
            continue
        for n in json.loads(prof.source_names or "[]") + [prof.official_name]:
            by_name[location_key(n)] = prof
            by_name[location_key(strip_location_number(n))] = prof

    fam_keys = {f.key for f in families}
    decisions = []
    for i, r in enumerate(rows):
        first, last = _val(r, "first"), _val(r, "last")
        loc_raw = _val(r, "location")
        prof = by_name.get(location_key(loc_raw)) or by_name.get(location_key(strip_location_number(loc_raw)))
        notes = data_notes(first, last)
        family = campaign_family if campaign_family in fam_keys else _family_for(_val(r, "campaign"), families)
        flag = (r.get(IMPORT_FLAG_COL) or "").strip()
        decisions.append({
            "row": i + 2, "lead_id": ids[i], "id_generated": ids_info[i]["generated"],
            "repeat_of": ids_info[i]["repeat_of"],
            "person": person_key(first, last),
            "phone": phone_key(_val(r, "phone")),
            "email": email_key(_val(r, "email")),
            "location_name": strip_location_number(loc_raw) or None,
            "location_profile": prof, "review_profile": review,
            "location_status": "mapped" if prof else "location_review",
            "family": family,
            "notes": notes,
            "import_flag": flag or None,
            "fingerprint": rows_fingerprint(r),
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
    for d in decisions:
        if d["repeat_of"]:
            review_reason[d["row"]].append(d["repeat_of"])

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

    # Phone numbers that can never be texted or called, caught BEFORE loading
    # (a short number, an area code that doesn't exist). The person can still
    # be emailed; the list goes back to the customer to correct.
    from app.services.programs.send_approval import _real_us_number
    bad_phones, no_phone = [], 0
    for r, d in zip(rows, decisions):
        raw = _val(r, "phone")
        if not raw:
            no_phone += 1
        elif not _real_us_number(raw):
            bad_phones.append({"lead_id": d["lead_id"], "row": d["row"], "phone": raw,
                               "name": " ".join(x for x in (_val(r, "first"), _val(r, "last")) if x),
                               "has_email": bool(d["email"])})
    bad_rows = {b["row"] for b in bad_phones}
    email_only = sum(1 for d in decisions if d["email"] and (not d["phone"] or d["row"] in bad_rows))

    status = Counter(_val(r, "status") or "(blank)" for r in rows)
    summary = {
        "bad_phone_rows": len(bad_phones),
        "bad_phones": bad_phones[:100],
        "no_phone_rows": no_phone,
        "email_only_candidates": email_only,
        "no_way_to_reach": sum(1 for d in decisions if not d["email"] and (not d["phone"] or d["row"] in bad_rows)),
        "source_rows": len(rows),
        "distinct_lead_ids": len([k for k in seen if k]),
        "generated_lead_ids": sum(1 for d in decisions if d["id_generated"]),
        "repeated_lead_ids": sum(1 for d in decisions if d["repeat_of"]),
        "errors": errors,
        "with_email": sum(1 for d in decisions if d["email"]),
        "with_phone": sum(1 for d in decisions if d["phone"]),
        "location_mapped": sum(1 for d in decisions if d["location_status"] == "mapped"),
        "location_review": sum(1 for d in decisions if d["location_status"] == "location_review"),
        "location_review_names": dict(Counter(d["location_name"] or "(blank)" for d in decisions
                                              if d["location_status"] == "location_review")),
        "locations_used": len({d["location_profile"].id for d in decisions if d["location_profile"]}),
        "contact_masters": len(groups),
        "auto_linked_rows": sum(1 for d in decisions if d["link_status"] == "linked"),
        "duplicate_review_rows": sum(1 for d in decisions if d.get("dup_review")),
        "data_note_rows": sum(1 for d in decisions if d["notes"]),
        "data_note_label": DATA_NOTE_LABEL,
        "status_counts": dict(status),
        "family_counts": dict(Counter(d["family"] or "(no campaign)" for d in decisions)),
        "import_flags": dict(Counter(d["import_flag"] for d in decisions if d["import_flag"])),
        "review_bucket": REVIEW_BUCKET_NAME,
    }
    return {"decisions": decisions, "summary": summary}


# Rows the Intelligent Importer passed through only to be KEPT, never worked
# until a person looks (#385: every source record is preserved).
FLAG_HOLDS = {
    "do_not_contact_match": "matches a do-not-contact family - kept, never contacted",
    "invalid": "nothing usable on the row - kept for review",
}


def _existing_people(db: Session, org_id: str, exclude_ids: set) -> Dict:
    """Families already in SCI (other files, other campaigns): person+phone and
    person+email -> their contact master; phone/email -> masters (for review)."""
    strong, by_phone, by_email = {}, defaultdict(set), defaultdict(set)
    for rec in (db.query(ProgramSourceRecord)
                .filter(ProgramSourceRecord.organization_id == org_id).all()):
        if rec.source_lead_id in exclude_ids:
            continue
        p, ph, em = person_key(rec.first_name, rec.last_name), phone_key(rec.phone), email_key(rec.email)
        master = rec.contact_master_key or rec.source_lead_id
        if p and ph:
            strong.setdefault((p, ph), master)
        if p and em:
            strong.setdefault((p, em), master)
        if ph:
            by_phone[ph].add((p, master))
        if em:
            by_email[em].add((p, master))
    return {"strong": strong, "phone": by_phone, "email": by_email}


def stage(db: Session, org: Organization, content: bytes, *, filename: Optional[str] = None,
          dry_run: bool = True, actor_id: Optional[str] = None,
          campaign_family: Optional[str] = None) -> Dict:
    rows = parse_csv(content)
    profiles = {p.id: p for p in db.query(LocationProfile)
                .filter(LocationProfile.organization_id == org.id).all()}
    families = db.query(CampaignFamily).filter(CampaignFamily.organization_id == org.id).all()
    if campaign_family and campaign_family not in {f.key for f in families}:
        raise ValueError("unknown campaign %r" % campaign_family)
    result = analyze(rows, profiles, families, campaign_family=campaign_family)
    summary = result["summary"]
    decisions = result["decisions"]
    run = ProgramImportRun(organization_id=org.id, filename=filename,
                           sha256=hashlib.sha256(content).hexdigest(), dry_run=dry_run,
                           created_by=actor_id)
    db.add(run)
    db.flush()

    existing = {s.source_lead_id: s for s in db.query(ProgramSourceRecord)
                .filter(ProgramSourceRecord.organization_id == org.id).all()}
    # SAME SOURCE ROW, ANY ID (#385): a row with no Lead ID of its own is the
    # record staged before from the same file (e.g. under IDs a person typed
    # into a repaired copy) when who it is and where it belongs match exactly.
    # That record is updated - never a second record for one source row.
    by_fp = defaultdict(list)
    for rec in existing.values():
        by_fp[_fingerprint(rec.first_name, rec.last_name, rec.phone, rec.email,
                           rec.source_location_name)].append(rec)
    claimed = set()
    for d in decisions:
        if d["lead_id"] in existing:
            claimed.add(d["lead_id"])
    reused = 0
    for d in decisions:
        if not d["id_generated"] or d["lead_id"] in existing:
            continue
        for rec in by_fp.get(d["fingerprint"], []):
            # Only a record with no campaign yet, or already in this one. A
            # record another campaign owns is a different source row (linked
            # below as the same person) - never moved between campaigns.
            if rec.campaign_family not in (None, d["family"]):
                continue
            if rec.source_lead_id not in claimed:
                old = d["lead_id"]
                d["lead_id"] = rec.source_lead_id
                d["reused_from_generated"] = old
                claimed.add(rec.source_lead_id)
                reused += 1
                break
    # masters pointed at generated ids that were swapped above follow them
    swap = {d["reused_from_generated"]: d["lead_id"] for d in decisions if d.get("reused_from_generated")}
    for d in decisions:
        d["master_key"] = swap.get(d["master_key"], d["master_key"])

    # ALREADY IN SCI (#385): the same person (name + phone or email) under
    # another file or campaign is linked to that family - one family, never a
    # second one texted by two campaigns. Same phone/email, different name:
    # review, never merged.
    people = _existing_people(db, org.id, {d["lead_id"] for d in decisions})
    groups = defaultdict(list)
    for d in decisions:
        groups[d["master_key"]].append(d)
    linked_existing = 0
    for mkey, members in groups.items():
        target = None
        for d in members:
            for k in ((d["person"], d["phone"]), (d["person"], d["email"])):
                if k[0] and k[1] and k in people["strong"]:
                    target = people["strong"][k]
                    break
            if target:
                break
        if target:
            for d in members:
                d["existing_master"] = target
                d["master_key"] = target
                d["link_status"] = "linked"
                d["link_reason"] = "same person as a family already in SCI (%s)" % target
                linked_existing += 1
            continue
        for d in members:
            others = set()
            for kind in ("phone", "email"):
                if d[kind]:
                    others |= {m for (p, m) in people[kind].get(d[kind], set()) if p != d["person"]}
            if others:
                reason = "same phone/email as a different person already in SCI (%s)" % ", ".join(sorted(others)[:3])
                d["dup_review"] = "; ".join(x for x in (d.get("dup_review"), reason) if x)
    summary["linked_to_existing_families"] = linked_existing
    summary["reused_earlier_records"] = reused
    summary["duplicate_review_rows"] = sum(1 for d in decisions if d.get("dup_review"))

    staged = created = kept_other = 0
    if not dry_run:
        for r, d in zip(rows, decisions):
            rec = existing.get(d["lead_id"])
            if rec is None:
                # ORIGINAL VALUES: written once, here, and never again.
                rec = ProgramSourceRecord(
                    organization_id=org.id, source_lead_id=d["lead_id"], row_number=d["row"],
                    raw_json=json.dumps(r, ensure_ascii=False),
                    first_name=_val(r, "first"), last_name=_val(r, "last"),
                    email=_val(r, "email"), phone=_val(r, "phone"),
                    source_status=_val(r, "status"), source_campaign=_val(r, "campaign") or None,
                    source_channel=_val(r, "channel") or None,
                    source_location_name=d["location_name"],
                    source_owner=_val(r, "owner") or None, source_manager=_val(r, "manager") or None,
                    source_id_generated=bool(d["id_generated"]),
                )
                db.add(rec)
                existing[d["lead_id"]] = rec
                created += 1
            prof = d["location_profile"] or d["review_profile"]
            rec.import_run_id = run.id
            # Explicit Central Alabama owner decision: Joyce Strong's two
            # different Southern Heritage source sites refer to ONE person,
            # with the funeral home selected as her campaign location.
            joyce_owner_override = (
                d["family"] == "central_alabama_ms"
                and rec.source_lead_id in ("SCI-CA-20261025-0052",
                                           "SCI-CA-20261025-0416"))
            if joyce_owner_override:
                chosen = next((p for p in profiles.values()
                               if p.official_name == "Southern Heritage Funeral Home"
                               and not p.is_review_bucket), None)
                if chosen is None:
                    raise ValueError("owner-selected Southern Heritage Funeral Home profile missing")
                rec.location_id = chosen.location_id
                rec.location_status = "mapped"
                rec.location_assigned_manually = True
            elif not rec.location_assigned_manually:
                rec.location_id = prof.location_id if prof else None
                rec.location_status = d["location_status"]
            if rec.campaign_family and d["family"] and rec.campaign_family != d["family"] \
                    and campaign_family:
                # #385: the campaign picked for THIS file never takes a record
                # another campaign already owns (a real Lead ID loaded before).
                kept_other += 1
            elif d["family"] or not rec.campaign_family:
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
            why = FLAG_HOLDS.get(d["import_flag"] or "")
            # A Central Alabama source row belonging to a person already in SCI
            # is excluded from THIS NEW campaign, never from the old campaign.
            # Keep its own lead_id: identity.send_refusal checks every row on a
            # lead, so attaching a held new row to the existing lead would
            # silently stop that person's original active follow-ups.
            if d.get("existing_master") and d["family"] == "central_alabama_ms":
                rec.on_hold = True
                rec.hold_reason = ("EXCLUDED FROM CENTRAL ALABAMA: already in SCI "
                                   "(original campaign continues)")
                rec.held_at, rec.held_by = rec.held_at or datetime.utcnow(), actor_id
                rec.approved_to_send_at, rec.approved_to_send_by = None, None
            elif why and not rec.on_hold:
                rec.on_hold, rec.hold_reason = True, why
                rec.held_at, rec.held_by = datetime.utcnow(), actor_id
            elif d["family"] == "central_alabama_ms" and rec.on_hold and not why:
                # Owner explicitly cleared historic Disqualified/Not Interested
                # labels, selected Joyce's funeral home, and selected the
                # Kilgroe brand-only location. Release ONLY those old import
                # holds once an actual mapped location is assigned. Never
                # release DNC, data, duplicate or unmatched facility reviews.
                reason = (rec.hold_reason or "").strip()
                historical_only = (reason in ("SOURCE STATUS: Disqualified",
                                              "SOURCE STATUS: Not Interested"))
                kilgroe_resolved = (
                    rec.source_location_name == "Kilgroe Funeral Home"
                    and rec.location_status == "mapped"
                    and reason in ("LOCATION REVIEW",
                                   "LOCATION REVIEW; SOURCE STATUS: Disqualified"))
                joyce_resolved = (
                    joyce_owner_override and rec.location_status == "mapped"
                    and reason == "LOCATION CONFLICT; SOURCE STATUS: Disqualified")
                if historical_only or kilgroe_resolved or joyce_resolved:
                    from app.services.programs.holds import DO_NOT_WORK_STATUSES
                    if (rec.source_status or "").strip().lower() not in DO_NOT_WORK_STATUSES:
                        rec.on_hold = False
                        rec.hold_reason = "RELEASED: owner Central Alabama source correction"
                        rec.held_at, rec.held_by = None, None
            staged += 1
    # ONE FAMILY, ONE LEAD (#385): a record linked to a family already in SCI
    # uses that family's live lead. A second lead an earlier load made for the
    # same person - never texted, emailed or enrolled - is marked a duplicate
    # of it (kept, not deleted), so no campaign can reach that person twice.
    superseded = 0
    if not dry_run:
        from app.models.models import (BookingLink, CadenceState, EmailMessage, Lead, Message,
                                       PipelineConversation, Reply, VoiceCall)
        from app.models.program_models import ProgramEmailTouch
        # The family's lead is the one on the family's OWN master record first,
        # then the oldest lead any of its records carries - never whichever
        # row a query happens to return first.
        lead_rows = {}
        for rec in existing.values():
            if rec.lead_id:
                lead_rows.setdefault(rec.contact_master_key or rec.source_lead_id, []).append(rec)
        master_lead = {}
        for key, recs_ in lead_rows.items():
            own = next((r for r in recs_ if r.source_lead_id == key), None)
            if own is not None:
                master_lead[key] = own.lead_id
                continue
            ls = (db.query(Lead).filter(Lead.id.in_({r.lead_id for r in recs_}),
                                        Lead.organization_id == org.id)
                  .order_by(Lead.created_at.asc(), Lead.id.asc()).all())
            if ls:
                master_lead[key] = ls[0].id

        def _ever_worked(lead_id: str, recs_) -> bool:
            """Anything at all happened with this lead: it is never marked a
            duplicate automatically - a person decides."""
            for model in (Message, EmailMessage, CadenceState, Reply, BookingLink, ProgramEmailTouch,
                          VoiceCall, PipelineConversation):
                if db.query(model.id).filter(model.lead_id == lead_id).first() is not None:
                    return True
            return any(r.approved_to_send_at or r.scheduled_start_at for r in recs_)

        for d in decisions:
            target = d.get("existing_master")
            rec = existing.get(d["lead_id"])
            keep = master_lead.get(target) if target else None
            if not (rec is not None and keep and rec.lead_id and rec.lead_id != keep):
                continue
            dup = db.query(Lead).filter(Lead.id == rec.lead_id, Lead.organization_id == org.id).first()
            if dup is None or _ever_worked(dup.id, [r for r in existing.values() if r.lead_id == dup.id]) \
                    or (dup.status or "new").lower() not in ("new", "duplicate"):
                d["dup_review"] = "; ".join(x for x in (d.get("dup_review"),
                                                       "two live leads for one person - a person decides") if x)
                rec.duplicate_review_reason = d["dup_review"]
                continue
            # Keep the *new* source record attached to its own duplicate lead.
            # Repointing it to keep would make its campaign-specific hold block
            # the ORIGINAL SCI lead and all of its legitimate old cadences.
            dup.duplicate_of_lead_id = keep
            dup.status = "duplicate"
            superseded += 1
        db.commit()
    summary["superseded_duplicate_leads"] = superseded
    summary["staged"] = staged
    summary["new_records"] = created
    summary["kept_in_other_campaign"] = kept_other
    run.summary_json = json.dumps(summary)
    db.commit()
    return {"run_id": run.id, "dry_run": dry_run, "summary": summary,
            "lead_ids": [d["lead_id"] for d in decisions]}
