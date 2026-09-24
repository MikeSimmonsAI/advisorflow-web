"""Row-level record classification.

A classification answers "what IS this record to the organization?" - not
"is it ready to import" (intake status) and not "may we text it" (outreach
status). Every classification belongs to a coarse RECORD CLASS and says
whether it represents an actual sales opportunity (`creates_lead`).

  creates_lead = False  ->  the record becomes an org contact only. It never
                            appears in a lead count, pipeline, forecast, SMS-
                            ready figure or assignment queue.
  creates_lead = True   ->  the record ALSO becomes a lead, but only if it has
                            a usable direct channel and is not blocked.

The platform defaults below are vertical-neutral. An organization may add its
own (IntakeClassification rows); they are resolved exactly like defaults.

HOW A ROW GETS ITS CLASSIFICATION
  1. The mapped classification column's value, through the batch's value map
     (which the operator reviews on the Classify step).
  2. A blank cell -> the batch's FALLBACK classification.
  3. A non-blank value nobody mapped -> `needs_classification`, and the row
     goes to review. An unrecognized value is not quietly made a lead.
  4. The fallback defaults to `contact`. Never `new_inquiry`: an outside
     database is not a list of people who just asked to hear from you.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from app.models.intake_models import RecordClass

NEEDS_CLASSIFICATION = "needs_classification"
DEFAULT_FALLBACK = "contact"


@dataclass
class ClassDef:
    key: str
    label: str
    record_class: str
    creates_lead: bool
    aliases: List[str] = field(default_factory=list)
    # The existing Lead.relationship_type vocabulary this maps to when a lead
    # is created, so AI tone and existing reports keep working.
    relationship_type: Optional[str] = None
    builtin: bool = True


DEFAULT_CLASSIFICATIONS: List[ClassDef] = [
    ClassDef("contact", "Contact", RecordClass.CONTACT, False,
             ["contact", "contacts", "general contact", "subscriber", "other contact"]),
    ClassDef("new_inquiry", "New Inquiry", RecordClass.LEAD, True,
             ["new inquiry", "inquiry", "new lead", "inbound", "web lead", "form fill",
              "website lead"], "warm_lead"),
    ClassDef("cold_prospect", "Cold Prospect", RecordClass.LEAD, True,
             ["cold", "cold lead", "cold prospect", "prospect", "lead", "open lead"],
             "cold_lead"),
    ClassDef("warm_prospect", "Warm Prospect", RecordClass.LEAD, True,
             ["warm", "warm lead", "warm prospect", "mql", "sql", "marketing qualified lead",
              "sales qualified lead", "opportunity", "in progress", "engaged"], "warm_lead"),
    ClassDef("existing_customer", "Existing Customer", RecordClass.CUSTOMER, False,
             ["customer", "current customer", "existing customer", "active customer",
              "client", "active client", "member"], "existing_customer"),
    ClassDef("previous_customer", "Previous Customer", RecordClass.PREVIOUS_CUSTOMER, False,
             ["previous customer", "past customer", "former customer", "historical customer",
              "churned", "lost customer", "inactive customer", "former client"],
             "past_customer"),
    ClassDef("win_back", "Win-Back", RecordClass.PREVIOUS_CUSTOMER, True,
             ["win back", "winback", "previous customer win back", "past customer win back",
              "re engagement", "reengagement", "reactivation"], "past_customer"),
    ClassDef("renewal", "Renewal", RecordClass.RENEWAL, True,
             ["renewal", "renewals", "renewal due", "up for renewal", "renewal opportunity"],
             "existing_customer"),
    ClassDef("referral", "Referral", RecordClass.LEAD, True,
             ["referral", "referred", "referral lead"], "warm_lead"),
    ClassDef("purchased_lead", "Purchased Lead", RecordClass.LEAD, True,
             ["purchased", "purchased lead", "purchased list", "bought list", "list purchase"],
             "cold_lead"),
    ClassDef("imported_database", "Imported Database", RecordClass.CONTACT, False,
             ["imported database", "database", "general contact database", "crm database",
              "general database", "import"]),
    ClassDef("partner", "Partner", RecordClass.PARTNER, False,
             ["partner", "evangelist", "affiliate", "channel partner", "broker"]),
    ClassDef("vendor", "Vendor", RecordClass.VENDOR, False,
             ["vendor", "supplier", "contractor"]),
    ClassDef("employee", "Employee", RecordClass.EMPLOYEE, False,
             ["employee", "staff", "internal", "team member"]),
    ClassDef("other", "Other", RecordClass.OTHER, False, []),
    ClassDef(NEEDS_CLASSIFICATION, "Needs Classification", RecordClass.CONTACT, False, []),
]


def value_key(v) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(v or "").lower()).strip()


def catalog(org_rows=None) -> Dict[str, ClassDef]:
    """Platform defaults plus the organization's own classifications."""
    out = {c.key: ClassDef(**asdict(c)) for c in DEFAULT_CLASSIFICATIONS}
    for r in org_rows or []:
        if not getattr(r, "is_active", True):
            continue
        try:
            aliases = json.loads(r.aliases) if r.aliases else []
        except Exception:  # noqa: BLE001
            aliases = []
        rc = r.record_class if r.record_class in RecordClass.ALL else RecordClass.CONTACT
        out[r.key] = ClassDef(r.key, r.label, rc, bool(r.creates_lead), aliases, None, False)
    return out


def suggest_value(raw, cat: Dict[str, ClassDef]) -> Optional[str]:
    """Best classification for one source value, or None if unrecognized."""
    k = value_key(raw)
    if not k:
        return None
    exact = {}
    for c in cat.values():
        for a in [c.key.replace("_", " "), c.label] + c.aliases:
            exact.setdefault(value_key(a), c.key)
    if k in exact:
        return exact[k]
    # Contained alias: the LONGEST alias that appears as whole words wins, so
    # "Previous Customer - Win-Back" is win_back, not previous_customer.
    best, best_len = None, 0
    padded = f" {k} "
    for a, key in exact.items():
        if a and f" {a} " in padded and len(a) > best_len:
            best, best_len = key, len(a)
    return best


def suggest_value_map(values: Dict[str, int], cat: Dict[str, ClassDef]) -> Dict[str, Optional[str]]:
    return {v: suggest_value(v, cat) for v in values}


def resolve(raw, value_map: Dict[str, str], fallback: str,
            cat: Dict[str, ClassDef]) -> tuple:
    """Return (classification_key, source) for one row.

    source: 'row' (mapped from the cell), 'fallback' (blank cell),
            'unmapped' (non-blank value with no mapping -> needs review).
    """
    k = value_key(raw)
    if not k:
        fb = fallback if fallback in cat else DEFAULT_FALLBACK
        return fb, "fallback"
    for src_value, cls in (value_map or {}).items():
        if value_key(src_value) == k and cls in cat:
            return cls, "row"
    return NEEDS_CLASSIFICATION, "unmapped"


def payload(cat: Dict[str, ClassDef]) -> List[dict]:
    return [{"key": c.key, "label": c.label, "record_class": c.record_class,
             "creates_lead": c.creates_lead, "builtin": c.builtin,
             "aliases": c.aliases} for c in cat.values()]
