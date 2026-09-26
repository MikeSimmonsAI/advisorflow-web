"""The universal field registry and the header mapper.

A header can be mapped to exactly one of five KINDS:

    standard   one of the platform fields below (first_name, email, ...)
    custom     an organization custom field (kept on the contact's custom_fields)
    vertical   a vertical-specific field (supplier, contract_end, VIN, policy...)
               kept on the contact's vertical_fields - never a column on Lead
    source     source metadata worth keeping for audit but not a CRM field
    ignore     deliberately discarded (the raw row still keeps the value)

There is no sixth kind called "dropped". A column nobody mapped is suggested
as `source` and listed as UNMAPPED, so it is kept AND visible - an unknown
column must never silently disappear.

Matching a header to a field is by alias, on a COMPACT form of the header
(lowercase, letters and digits only), so "Last Activity Date",
"LastActivityDate", "last_activity_date" and "Last-Activity-Date" are the
same header. Keyword rules then cover families of headers no alias list can
enumerate ("Historical <Company> Customer").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.services.intake import normalize as N

KIND_STANDARD = "standard"
KIND_CUSTOM = "custom"
KIND_VERTICAL = "vertical"
KIND_SOURCE = "source"
KIND_IGNORE = "ignore"
KINDS = (KIND_STANDARD, KIND_CUSTOM, KIND_VERTICAL, KIND_SOURCE, KIND_IGNORE)


def compact(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


def words(h: str) -> List[str]:
    return re.sub(r"[^a-z0-9]+", " ", str(h or "").lower()).split()


@dataclass
class FieldDef:
    key: str
    label: str
    group: str
    aliases: List[str] = field(default_factory=list)
    help: str = ""
    # When several columns map to the same field, the first one wins and the
    # others are demoted to `source`. `multi` fields accept several columns.
    multi: bool = False


STANDARD_FIELDS: List[FieldDef] = [
    # ── identity ──
    FieldDef("first_name", "First name", "Identity",
             ["first name", "firstname", "fname", "first", "given name", "buyer first name",
              "buyerfirstname", "contact first name"]),
    FieldDef("last_name", "Last name", "Identity",
             ["last name", "lastname", "lname", "last", "surname", "family name",
              "buyer last name", "buyerlastname", "contact last name"]),
    FieldDef("full_name", "Full name", "Identity",
             ["full name", "fullname", "name", "contact name", "lead name", "customer name",
              "client name", "display name", "primary contact"],
             "Only used to split into first/last when those are not mapped."),
    FieldDef("company", "Company / account", "Identity",
             ["company", "company name", "account", "account name", "organization",
              "organisation", "business", "business name", "employer", "firm",
              "associated company", "associated company primary", "customer company",
              "legal name", "dba"]),
    FieldDef("job_title", "Job title", "Identity",
             ["job title", "title", "position", "role", "job role"]),
    # ── channels ──
    FieldDef("email", "Email", "Contact",
             ["email", "email address", "e mail", "e-mail", "email 1", "primary email",
              "work email", "buyer email", "buyeremail", "e-mail 1 - value", "usable email"],
             "If both 'Email' and 'Usable Email' exist, map the one you trust."),
    FieldDef("phone", "Phone", "Contact",
             ["phone", "phone number", "telephone", "tel", "primary phone", "phone 1",
              "phone1", "business phone", "work phone", "office phone", "home phone",
              "main phone", "phone e164", "e164", "buyer phone", "buyerphone",
              "phone 1 - value"]),
    FieldDef("mobile_phone", "Mobile phone", "Contact",
             ["mobile phone", "mobile", "mobile number", "mobile phone number", "cell",
              "cell phone", "cellphone", "cell number", "wireless"],
             "A dedicated mobile column is the ONLY way an import can say a number is mobile."),
    FieldDef("phone_line_type", "Phone line type", "Contact",
             ["line type", "phone type", "phone line type", "number type", "carrier type"]),
    # ── address ──
    FieldDef("street_address", "Street address", "Address",
             ["street address", "address", "street", "address 1", "address1", "address line 1",
              "mailing address", "home address", "service address", "billing address"]),
    FieldDef("address_line2", "Address line 2", "Address",
             ["address 2", "address2", "address line 2", "suite", "unit", "apt"]),
    FieldDef("city", "City", "Address", ["city", "town", "municipality", "mailing city"]),
    FieldDef("state", "State", "Address",
             ["state", "st", "province", "state province", "state region", "state/region",
              "state region code", "region", "mailing state"]),
    FieldDef("zip_code", "ZIP / postal code", "Address",
             ["zip", "zip code", "zipcode", "postal code", "postcode", "zip+4", "zip 4",
              "postal", "mailing zip"]),
    FieldDef("country", "Country", "Address",
             ["country", "country region", "country/region", "nation"]),
    # ── provenance ──
    FieldDef("source_record_id", "Source record ID", "Provenance",
             ["record id", "hubspot record id", "hubspot id", "contact id", "contactid",
              "contact guid", "crm id", "external id", "external_id", "source id",
              "salesforce id", "sfdc id", "lead id", "dynamics id", "dynamics contact guid",
              "ghl id", "gohighlevel id", "vid"],
             "Preserved exactly. The strongest duplicate key there is."),
    FieldDef("source", "Lead source (channel)", "Provenance",
             ["source", "lead source", "leadsource", "original source", "source name",
              "lead origin", "origin", "referral source", "marketing source",
              "how did you hear about us", "record source"]),
    FieldDef("owner_name", "Owner (from source)", "Provenance",
             ["contact owner", "owner", "lead owner", "account owner", "original contact owner",
              "assigned to", "sales rep", "rep"]),
    FieldDef("source_created_at", "Created date (in source)", "Provenance",
             ["create date", "created date", "date created", "created at", "created on"]),
    FieldDef("last_activity_date", "Last activity date", "Activity",
             ["last activity date", "last activity", "lastactivitydate", "last logged activity",
              "last contacted", "last contact date", "last engagement date"]),
    FieldDef("notes", "Notes", "Activity", ["notes", "note", "comments", "description"]),
    # ── classification ──
    FieldDef("classification", "Record classification", "Classification",
             ["import segment", "segment", "lead type", "record type", "customer status",
              "contact type", "relationship type", "relationship", "classification",
              "customer type", "category", "lifecycle stage", "lead status", "status type"],
             "Drives ROW-LEVEL classification. Values are mapped on the Classify step."),
    FieldDef("historical_customer", "Historical customer flag", "Classification",
             ["historical customer", "past customer", "previous customer", "former customer",
              "was customer", "prior customer"],
             "Yes/No. Evidence of a past relationship - not proof of a current one."),
    FieldDef("tags", "Tags", "Classification", ["tags", "tag", "labels", "lists"], multi=True),
    # ── compliance / deliverability ──
    FieldDef("email_status", "Email verification status", "Compliance",
             ["email verification", "email status", "email validation", "email verified",
              "email deliverability", "email result"]),
    FieldDef("email_hard_bounce", "Email hard bounce", "Compliance",
             ["email hard bounce reason", "hard bounce", "hard bounced", "email bounced",
              "bounce reason", "hard bounce reason"]),
    FieldDef("email_unsubscribed", "Email unsubscribed", "Compliance",
             ["unsubscribed from all email", "unsubscribed", "email opt out", "email opted out",
              "opted out of email", "unsubscribe"]),
    FieldDef("email_invalid", "Email marked invalid", "Compliance",
             ["invalid email address", "invalid email", "email invalid"]),
    FieldDef("sms_verification", "SMS / phone verification", "Compliance",
             ["sms verification", "phone verification", "sms status", "phone status",
              "text status"]),
    FieldDef("do_not_contact", "Do not contact (all channels)", "Compliance",
             ["do not contact", "dnc", "do not solicit", "no contact"]),
    # The four permission fields accept BOTH polarities of header ("Allow
    # Texts" and "Do Not Text"). Which way a bare Yes/No is read is decided
    # per header by app/services/permission_values.COLUMN_TABLE - the
    # platform's one interpreter - never by this module.
    FieldDef("allow_email", "Email permission", "Compliance",
             ["allow emails", "allow emails?", "allow email", "allow e-mail", "email permission",
              "do not email", "do not allow emails", "email opt in", "email consent"]),
    FieldDef("allow_bulk_email", "Bulk email permission", "Compliance",
             ["do not allow bulk emails", "do not allow bulk email", "do not bulk email",
              "bulk email opt out", "allow bulk emails", "allow bulk email",
              "allow marketing emails", "do not allow marketing emails"]),
    FieldDef("allow_sms", "SMS permission / consent", "Compliance",
             ["allow text message", "allow text message?", "allow text messages", "allow sms",
              "allow texts", "sms permission", "do not text", "do not allow text messages",
              "sms opt out", "text opt out", "text consent", "sms consent", "sms opt in"]),
    FieldDef("allow_voice", "Phone call permission", "Compliance",
             ["allow phone calls", "allow phone calls?", "allow calls", "allow phone",
              "do not call", "do not phone", "do not allow phone calls", "on do not call list"]),
]

STANDARD_BY_KEY: Dict[str, FieldDef] = {f.key: f for f in STANDARD_FIELDS}

_ALIAS_INDEX: Dict[str, str] = {}
for _f in STANDARD_FIELDS:
    for _a in [_f.key, _f.label] + _f.aliases:
        _ALIAS_INDEX.setdefault(compact(_a), _f.key)

# Headers that are the SOURCE system's own opinion about a field the platform
# decides for itself. Mapping them automatically would import somebody else's
# unreliable flag as fact, so they are suggested as `source` metadata and the
# operator can promote them on purpose.
_NEVER_AUTO_MAP = {
    compact(h) for h in (
        "current customer", "number of active contracts", "is customer",
        "hubspot score", "lead score", "contact coverage", "import status",
        "number of associated deals",
    )
}

# Words that mark a column as belonging to a VERTICAL rather than to the
# universal record. Suggested as kind=vertical with a slug key; the operator
# confirms. Deliberately a hint list, not a schema - nothing below becomes a
# column anywhere.
_VERTICAL_HINTS = (
    "supplier", "commodity", "contract", "rate", "usage", "kwh", "meter", "esiid",
    "utility", "account number", "renewal", "term", "deal", "policy", "carrier",
    "coverage", "premium", "beneficiary", "vin", "vehicle", "make", "model", "trim",
    "dealership", "trade", "mileage", "mls", "parcel", "property", "listing",
    "price range", "bedrooms", "buyer", "seller", "loan", "mortgage", "lender",
    "interment", "plot", "lot", "section", "cemetery", "decedent", "plan",
    "start date", "end date", "expiration", "expiry", "historical",
)


def _keyword_rule(header: str) -> Optional[str]:
    w = words(header)
    ws = set(w)
    if "historical" in ws and ("customer" in ws or "client" in ws):
        # "Historical Atlantis Customer", "Historical Customer?" - but not
        # "Historical Deal Count", which is a number, not a flag.
        if not ({"count", "number", "date", "start", "end", "supplier"} & ws):
            return "historical_customer"
    if "record" in ws and "id" in ws and not ({"associated", "merged"} & ws):
        return "source_record_id"
    if {"mobile", "cell"} & ws and ({"phone", "number"} & ws or len(w) <= 2):
        return "mobile_phone"
    if "email" in ws and ("verification" in ws or "verified" in ws or "validation" in ws):
        return "email_status"
    if "email" in ws and "bounce" in " ".join(w):
        return "email_hard_bounce"
    return None


def suggest(header: str) -> Dict[str, Optional[str]]:
    """Best guess for one header: {kind, target, confidence}."""
    c = compact(header)
    if not c:
        return {"kind": KIND_IGNORE, "target": None, "confidence": "high"}
    if c in _NEVER_AUTO_MAP:
        return {"kind": KIND_SOURCE, "target": N.slug(header), "confidence": "low"}
    if c in _ALIAS_INDEX:
        return {"kind": KIND_STANDARD, "target": _ALIAS_INDEX[c], "confidence": "high"}
    rule = _keyword_rule(header)
    if rule:
        return {"kind": KIND_STANDARD, "target": rule, "confidence": "medium"}
    low = " ".join(words(header))
    if any(re.search(r"\b" + re.escape(h) + r"\b", low) for h in _VERTICAL_HINTS):
        return {"kind": KIND_VERTICAL, "target": N.slug(header), "confidence": "low"}
    return {"kind": KIND_SOURCE, "target": N.slug(header), "confidence": "none"}


def suggest_mapping(headers: List[str], org_custom_keys: Optional[Dict[str, str]] = None) -> Dict[str, dict]:
    """Suggest a mapping for every header, resolving collisions.

    `org_custom_keys` maps compact(label or key) -> custom key for the
    organization's existing custom fields: a header that matches one of those
    is suggested as that custom field before anything else.
    """
    org_custom_keys = org_custom_keys or {}
    out: Dict[str, dict] = {}
    taken: Dict[str, str] = {}
    # First pass: exact aliases win over keyword rules, in header order.
    ordered = sorted(enumerate(headers),
                     key=lambda it: 0 if compact(it[1]) in _ALIAS_INDEX else 1)
    for idx, h in ordered:
        c = compact(h)
        if c in org_custom_keys:
            out[h] = {"kind": KIND_CUSTOM, "target": org_custom_keys[c], "confidence": "high"}
            continue
        s = suggest(h)
        if s["kind"] == KIND_STANDARD:
            fd = STANDARD_BY_KEY[s["target"]]
            if s["target"] in taken and not fd.multi:
                s = {"kind": KIND_SOURCE, "target": N.slug(h), "confidence": "low",
                     "note": f"'{taken[s['target']]}' is already mapped to {fd.label}"}
            else:
                taken[s["target"]] = h
        out[h] = s
    return {h: out[h] for h in headers}


def validate_mapping(headers: List[str], mapping: Dict[str, dict]) -> List[str]:
    """Return a list of problems. Empty list = usable mapping."""
    problems = []
    seen: Dict[str, str] = {}
    for h in headers:
        m = mapping.get(h)
        if not m:
            continue
        kind = m.get("kind")
        if kind not in KINDS:
            problems.append(f"Column '{h}': unknown mapping kind '{kind}'.")
            continue
        if kind == KIND_STANDARD:
            t = m.get("target")
            if t not in STANDARD_BY_KEY:
                problems.append(f"Column '{h}': '{t}' is not a platform field.")
                continue
            if t in seen and not STANDARD_BY_KEY[t].multi:
                problems.append(f"Columns '{seen[t]}' and '{h}' are both mapped to "
                                f"{STANDARD_BY_KEY[t].label}. Pick one.")
            seen[t] = h
        if kind in (KIND_CUSTOM, KIND_VERTICAL, KIND_SOURCE):
            if not re.fullmatch(r"[a-z0-9_]{1,60}", str(m.get("target") or "")):
                problems.append(f"Column '{h}': key must be lowercase letters, digits "
                                f"and underscores.")
    return problems


def registry_payload() -> List[dict]:
    return [{"key": f.key, "label": f.label, "group": f.group, "help": f.help,
             "multi": f.multi} for f in STANDARD_FIELDS]
