"""CAMPAIGN ATTRIBUTION - which campaign an SCI event belongs to.

ONE FOUNDATION, NO SECOND STORE. A campaign is an existing CampaignFamily
row (program_campaign_families, stable `key` per organization). A family's
membership in a campaign is an existing ProgramSourceRecord row (the import
row that put them in it - never edited by this module). An import run
(program_import_runs) groups the families one file brought in, which is how
"the original campaign" (the Oct 12 SCI file, ten message families) is
selectable as one scope without inventing a name.

ATTRIBUTION, MOST RELIABLE EVIDENCE FIRST (per event):
  1. explicit   - the event row itself stores campaign_family
                  (ProgramResponse, ProgramEmailTouch);
  2. membership - the family belonged to exactly ONE campaign when the event
                  happened (its import row existed at or before the event);
  3. ambiguous  - the family belonged to more than one campaign at that time
                  and nothing on the event says which: never guessed;
  4. unattributed - no campaign membership at all (or only later ones).
Location and calendar dates are never used to guess a campaign.

Read-only. Nothing here writes, backfills or changes a record: every answer
is derived at read time from rows that already exist, so there is nothing to
roll back and no history can be lost.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

ALL = "all"
UNATTRIBUTED = "unattributed"
IMPORT_PREFIX = "import:"

EVIDENCE = ("explicit", "membership", "ambiguous", "unattributed")
EVIDENCE_WORDS = {
    "explicit": "Campaign recorded on the event",
    "membership": "Family belonged to one campaign at the time",
    "ambiguous": "Family was in more than one campaign - not guessed",
    "unattributed": "No campaign on record",
}


class CampaignNotFound(Exception):
    """A campaign scope this workspace does not have (or another tenant's):
    answered exactly like a campaign that does not exist."""


def _status_word(is_active: Optional[bool]) -> str:
    # The existing campaign state is one switch (CampaignFamily.is_active).
    return "Active" if is_active else "Off"


class Memberships:
    """Every family's campaign memberships in one workspace (one lean query)."""

    def __init__(self, db: Session, org_id: str):
        from app.models.program_models import CampaignFamily, ProgramImportRun, ProgramSourceRecord
        R = ProgramSourceRecord
        self.org = org_id
        self.by_lead: Dict[str, List[Dict]] = defaultdict(list)
        for lead_id, key, run_id, created in (db.query(R.lead_id, R.campaign_family, R.import_run_id, R.created_at)
                                              .filter(R.organization_id == org_id, R.lead_id.isnot(None))
                                              .order_by(R.created_at).all()):
            self.by_lead[lead_id].append({"key": key or None, "run": run_id, "at": created})
        self.families = {f.key: f for f in db.query(CampaignFamily)
                         .filter(CampaignFamily.organization_id == org_id).all()}
        self.runs = {r.id: r for r in db.query(ProgramImportRun)
                     .filter(ProgramImportRun.organization_id == org_id,
                             ProgramImportRun.dry_run.is_(False)).all()}

    # ── one family / one event ──────────────────────────────────────────────
    def keys_of(self, lead_id: str) -> List[str]:
        out = []
        for m in self.by_lead.get(lead_id, ()):
            if m["key"] and m["key"] not in out:
                out.append(m["key"])
        return out

    def runs_of(self, lead_id: str) -> List[str]:
        out = []
        for m in self.by_lead.get(lead_id, ()):
            if m["run"] and m["run"] not in out:
                out.append(m["run"])
        return out

    def attribute(self, lead_id: str, at: Optional[datetime],
                  explicit_key: Optional[str] = None) -> Tuple[Optional[str], Optional[str], str]:
        """(campaign key, import run id, evidence) for one event."""
        mem = self.by_lead.get(lead_id, ())
        if explicit_key and explicit_key in self.families:
            run = next((m["run"] for m in mem if m["key"] == explicit_key), None)
            return explicit_key, run, "explicit"
        # Memberships that existed when the event happened. A membership
        # created later cannot have caused it.
        live = [m for m in mem if at is None or m["at"] is None or m["at"] <= at] or []
        keys = {m["key"] for m in live if m["key"]}
        if len(keys) == 1:
            k = next(iter(keys))
            return k, next((m["run"] for m in live if m["key"] == k), None), "membership"
        if len(keys) > 1:
            return None, None, "ambiguous"
        # No campaign key, but the family arrived in one import file: the
        # campaign stays unattributed, the import group is still known.
        runs = {m["run"] for m in live if m["run"]}
        return None, (next(iter(runs)) if len(runs) == 1 else None), "unattributed"

    # ── scopes ──────────────────────────────────────────────────────────────
    def check_scope(self, scope: Optional[str]) -> str:
        s = (scope or ALL).strip() or ALL
        if s in (ALL, UNATTRIBUTED):
            return s
        if s.startswith(IMPORT_PREFIX):
            if s[len(IMPORT_PREFIX):] in self.runs:
                return s
            raise CampaignNotFound(s)
        if s in self.families:
            return s
        raise CampaignNotFound(s)

    def name_of(self, scope: str) -> str:
        if scope == ALL:
            return "All campaigns"
        if scope == UNATTRIBUTED:
            return "Unattributed"
        if scope.startswith(IMPORT_PREFIX):
            return self.group_label(self.runs.get(scope[len(IMPORT_PREFIX):]))
        f = self.families.get(scope)
        return f.name if f else scope

    @staticmethod
    def group_label(run) -> str:
        if run is None:
            return "Import"
        when = run.created_at.strftime("%b %-d, %Y") if run.created_at else ""
        return "All of %s%s" % (run.filename or "import", (" (%s)" % when) if when else "")

    def stamp(self, key: Optional[str], evidence: str) -> Dict:
        if key:
            return {"key": key, "name": self.families[key].name if key in self.families else key,
                    "evidence": evidence}
        return {"key": None, "name": "Ambiguous" if evidence == "ambiguous" else "Unattributed",
                "evidence": evidence}


def in_scope(scope: str, key: Optional[str], run: Optional[str], evidence: str) -> bool:
    """Does an attributed event (or family) fall in the selected scope?"""
    if scope == ALL:
        return True
    if scope == UNATTRIBUTED:
        return key is None
    if scope.startswith(IMPORT_PREFIX):
        # The import group never takes an ambiguous event: it is not known
        # which campaign (and so which file) it belonged to.
        return evidence != "ambiguous" and run == scope[len(IMPORT_PREFIX):]
    return key == scope


def catalog(M: Memberships, real_leads: Optional[set] = None) -> Dict:
    """The selector's options: every campaign this workspace has (real names,
    real switch state), each import file that brought in more than one
    campaign as a group, and the unattributed bucket. Family counts are real
    families only when `real_leads` is given."""
    fam_by_key: Dict[str, set] = defaultdict(set)
    fam_by_run: Dict[str, set] = defaultdict(set)
    keys_by_run: Dict[str, set] = defaultdict(set)
    unattr = set()
    for lead_id, mem in M.by_lead.items():
        if real_leads is not None and lead_id not in real_leads:
            continue
        keys = {m["key"] for m in mem if m["key"]}
        for k in keys:
            fam_by_key[k].add(lead_id)
        if not keys:
            unattr.add(lead_id)
        for m in mem:
            if m["run"]:
                fam_by_run[m["run"]].add(lead_id)
                if m["key"]:
                    keys_by_run[m["run"]].add(m["key"])
    campaigns = []
    for key, f in M.families.items():
        campaigns.append({"value": key, "key": key, "name": f.name, "status": _status_word(f.is_active),
                          "is_active": bool(f.is_active), "families": len(fam_by_key.get(key, ())),
                          "first_loaded": None})
    first_at: Dict[str, datetime] = {}
    for mem in M.by_lead.values():
        for m in mem:
            if m["key"] and m["at"] and (m["key"] not in first_at or m["at"] < first_at[m["key"]]):
                first_at[m["key"]] = m["at"]
    for c in campaigns:
        c["first_loaded"] = first_at[c["key"]].isoformat() + "Z" if c["key"] in first_at else None
    # Campaigns with families first (newest first), then empty ones by name.
    campaigns.sort(key=lambda c: (c["families"] == 0, -(first_at[c["key"]].timestamp() if c["key"] in first_at else 0),
                                  c["name"].lower()))
    groups = []
    for run_id, keys in keys_by_run.items():
        if len(keys) < 2 or run_id not in M.runs:
            continue
        r = M.runs[run_id]
        groups.append({"value": IMPORT_PREFIX + run_id, "import_run_id": run_id, "name": M.group_label(r),
                       "filename": r.filename, "imported_at": r.created_at.isoformat() + "Z" if r.created_at else None,
                       "campaign_keys": sorted(keys), "families": len(fam_by_run[run_id])})
    groups.sort(key=lambda g: g["imported_at"] or "")
    return {"campaigns": campaigns, "groups": groups, "unattributed_families": len(unattr)}
