"""
SOURCE ADAPTERS - turn something tabular into reconciliation Records.

Two adapters today:
  * `rows_to_records`  - any list of {header: value} dicts (a spreadsheet, a
    CSV, an export, a staging table).
  * `leads_to_records` - operational Lead rows, including the columns an
    import parked in `custom_fields` because they had nowhere else to go.

WHY THE SECOND ONE MATTERS
--------------------------
`import_service.parse_excel_file` maps the columns it recognises onto Lead
columns and puts EVERYTHING ELSE into `custom_fields` as JSON. Recognition is
an EXACT match against a fixed alias list, so a header the list does not name
is not "fuzzy matched anyway" - it is parked. That is not a bug in the parking;
it is a bug in reading a Lead row as if the parked columns did not exist.
`leads_to_records` reads them.

Nothing here is specific to any customer, file or industry. The alias lists are
ordinary CRM column names.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Iterable

from app.services.source_reconciliation import Record

# ---------------------------------------------------------------------------
# Column aliases. Lowercased, exact match, longest list wins nothing - the
# FIRST alias present in the file is used, so order expresses preference.
# ---------------------------------------------------------------------------

ALIASES: dict[str, tuple[str, ...]] = {
    "source_key": (
        "(do not modify) contact", "(do not modify) opportunity",
        "contact id", "contactid", "crm id", "external id", "source id",
        "record id", "lead id", "leadid",
    ),
    "first_name": ("first name", "firstname", "fname", "first", "given name"),
    "last_name": ("last name", "lastname", "lname", "last", "surname", "family name"),
    "full_name": ("full name", "fullname", "name", "contact name", "lead name",
                  "customer name", "display name"),
    "street_address": ("street address", "address", "street", "address 1",
                       "address1", "mailing address", "home address"),
    "city": ("city", "town"),
    "state": ("state", "st", "province", "state/province"),
    "zip_code": ("zip code", "zip", "zipcode", "postal code", "postal", "zip+4"),
    # NOT "last logged activity" - in real CRM exports that column holds a
    # TIMESTAMP, not an action. Reading it here put "2023-09-18 14:02:31" into
    # a field whose whole purpose is to say what somebody DID, and then counted
    # that as engagement evidence. It belongs in DATE_FIELDS, where it is.
    "last_action": ("last action", "last activity type", "last call result"),
    "status_reason": ("status reason", "status", "lead status"),
    "disposition": ("disposition", "outcome", "call result", "result"),
    "sale_made": ("sale made?", "sale made", "sold", "contract sold"),
    "last_sold_date": ("last sold date", "date signed", "contract date",
                       "actual close date"),
    "owner": ("owner", "current owner", "assigned to", "sales advisor",
              "sales advisor(text)", "account owner"),
    "created_on": ("created on", "record created on", "lead date", "created at",
                   "create date"),
    "tier": ("lead type", "tier", "need type", "data tier"),
}

# Every column that can carry a contact date; ALL are consulted and the most
# recent wins. A file that has three of these is not choosing between them.
DATE_FIELDS: tuple[str, ...] = (
    "last activity date", "last activity/note", "last activity",
    "last contact date", "open activity date", "last logged activity",
    "last activity note", "last completed activity",
)

EMAIL_FIELDS: tuple[str, ...] = (
    "email", "email address", "e-mail", "primary email",
    "email address 2", "email address 3", "e-mail 1 - value", "e-mail 2 - value",
)

PHONE_FIELDS: tuple[str, ...] = (
    "phone e164", "phone (e164)", "phone_e164", "phone", "phone number",
    "mobile phone", "mobile", "cell", "cell phone", "primary phone",
    "home phone", "home phone 2", "alt phone", "telephone",
    "address 1: phone", "address 1: telephone 2", "address 1: telephone 3",
    "address 2: telephone 1", "address 2: telephone 2", "address 2: telephone 3",
    "address 3: telephone1", "address 3: telephone2", "address 3: telephone3",
    "assistant phone", "phone 1 - value", "phone 2 - value", "phone1",
)

# Permission columns. `positive` means the column grants when true; `negative`
# means the column DENIES when true (a "do not" column), which is the trap in
# most CRM exports and the reason this is data rather than an if-statement.
PERMISSION_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "allow_email": (("allow emails?", "positive"), ("allow emails", "positive"),
                    ("allow email", "positive"),
                    ("do not email", "negative"),
                    ("email opt out", "negative"), ("unsubscribed", "negative")),
    # Bulk/marketing permission is a SEPARATE fact from whether a person may be
    # emailed at all. A funeral home's advisor writing to one family is not a
    # bulk send. Folding the two together either blocks correspondence somebody
    # never objected to, or lets a campaign reach somebody who opted out of
    # campaigns. Both are wrong, so they stay apart and the send path chooses.
    "allow_bulk_email": (("do not allow bulk emails", "negative"),
                         ("allow bulk emails", "positive"),
                         ("bulk email opt out", "negative"),
                         ("do not bulk email", "negative")),
    "allow_sms": (("allow text message?", "positive"),
                  ("allow text messages", "positive"),
                  ("allow sms", "positive"), ("do not text", "negative"),
                  ("sms opt out", "negative")),
    "allow_voice": (("allow phone calls?", "positive"),
                    ("allow phone calls", "positive"),
                    ("do not call", "negative"), ("dnc", "negative"),
                    ("do not phone", "negative")),
}

from app.services import permission_values as pv  # noqa: E402

# THE VOCABULARY LIVES IN permission_values AND NOWHERE ELSE.
#
# These names are kept as thin aliases so existing readers of this module still
# resolve, but they are the SAME objects the interpreter uses - not a second
# copy that can drift. A gate asserts no module outside permission_values
# declares a permission value table of its own.
TRUE_WORDS = pv.BOOL_TRUE
FALSE_WORDS = pv.BOOL_FALSE

# Values that state a PERMISSION in their own words. A cell reading "Allow"
# means allow no matter what the column above it is called.
#
# THIS IS THE TRAP, AND IT IS NOT HYPOTHETICAL. A real export carries a column
# named "Do not allow Bulk Emails" whose cells read "Allow" and "Do Not Allow".
# Inverting on the column name turned 85,751 permissions into denials and
# reported the whole population as email-restricted. The column name describes
# what the flag is FOR; the cell says which way it points. When the cell says
# it, the cell wins - and only a bare yes/no is read through the column's
# polarity.
SELF_ALLOW = pv.SELF_ALLOW
SELF_DENY = pv.SELF_DENY


def _lower_keys(row: dict) -> dict:
    return {str(k).strip().lower(): v for k, v in row.items() if k is not None}


def _first(low: dict, names: Iterable[str]) -> Any:
    for n in names:
        if n in low:
            v = low[n]
            if v is not None and str(v).strip() != "":
                return v
    return None


def _all(low: dict, names: Iterable[str]) -> list[str]:
    out = []
    for n in names:
        v = low.get(n)
        if v is not None and str(v).strip() != "":
            out.append(str(v).strip())
    return out


def _text(v) -> str:
    return "" if v is None else str(v).strip()


def parse_bool(v) -> bool | None:
    """A bare yes/no. None means the cell said nothing - never permission."""
    if v is None:
        return None
    s = str(v).strip().lower()
    if not s:
        return None
    if s in TRUE_WORDS:
        return True
    if s in FALSE_WORDS:
        return False
    if isinstance(v, bool):
        return v
    return None


def parse_permission(v, polarity: str) -> bool | None:
    """
    Resolve one permission cell to allow (True) / deny (False) / silent (None).

    DELEGATES to the single platform interpreter. This module's `positive` /
    `negative` polarity names map onto the interpreter's `grant` / `deny`;
    nothing about what a VALUE means is decided here any more.
    """
    state, _ambiguous = pv.interpret_cell_ex(
        v, "grant" if polarity == "positive" else "deny")
    return pv.to_bool(state)


def parse_date(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
                "%d/%m/%Y", "%m-%d-%Y", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s[:len(fmt) + 8], fmt)
        except ValueError:
            continue
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def split_full_name(full: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", (full or "").strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return "", parts[0]
    return parts[0], parts[-1]


def _permission(low: dict, key: str) -> bool | None:
    """
    Resolve one channel's permission from every column that speaks to it.

    A DENIAL anywhere wins. This is the single place the negative-column trap
    is handled, and it resolves in the restrictive direction by construction.
    """
    resolved: bool | None = None
    for col, polarity in PERMISSION_FIELDS[key]:
        if col not in low:
            continue
        val = parse_permission(low[col], polarity)
        if val is None:
            continue
        if val is False:
            return False
        resolved = True
    return resolved


def row_to_record(row: dict, key: str = "") -> Record:
    low = _lower_keys(row)

    first = _text(_first(low, ALIASES["first_name"]))
    last = _text(_first(low, ALIASES["last_name"]))
    if not last:
        f2, l2 = split_full_name(_text(_first(low, ALIASES["full_name"])))
        last = l2
        first = first or f2

    dates = [d for d in (parse_date(low.get(c)) for c in DATE_FIELDS) if d]
    last_contact = max(dates) if dates else None

    return Record(
        key=key or _text(_first(low, ALIASES["source_key"])),
        source_key=_text(_first(low, ALIASES["source_key"])),
        first_name=first,
        last_name=last,
        emails=tuple(_all(low, EMAIL_FIELDS)),
        phones=tuple(_all(low, PHONE_FIELDS)),
        street_address=_text(_first(low, ALIASES["street_address"])),
        city=_text(_first(low, ALIASES["city"])),
        state=_text(_first(low, ALIASES["state"])),
        zip_code=_text(_first(low, ALIASES["zip_code"])),
        last_contact_date=last_contact,
        last_action=_text(_first(low, ALIASES["last_action"])),
        status_reason=_text(_first(low, ALIASES["status_reason"])),
        disposition=_text(_first(low, ALIASES["disposition"])),
        sale_made=_text(_first(low, ALIASES["sale_made"])),
        last_sold_date=parse_date(_first(low, ALIASES["last_sold_date"])),
        owner=_text(_first(low, ALIASES["owner"])),
        created_on=parse_date(_first(low, ALIASES["created_on"])),
        allow_email=_permission(low, "allow_email"),
        allow_bulk_email=_permission(low, "allow_bulk_email"),
        allow_sms=_permission(low, "allow_sms"),
        allow_voice=_permission(low, "allow_voice"),
        raw=dict(low),
    )


def rows_to_records(rows: Iterable[dict]) -> list[Record]:
    return [row_to_record(r) for r in rows]


def lead_to_record(lead) -> Record:
    """
    An operational Lead, INCLUDING its parked import columns.

    `custom_fields` is merged UNDER the real columns: a mapped column always
    wins over a parked one carrying the same name, so reading the parked data
    can add evidence but can never override the record proper.
    """
    parked: dict = {}
    raw = getattr(lead, "custom_fields", None)
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                parked = {str(k).strip().lower(): v for k, v in loaded.items()}
        except (ValueError, TypeError):
            parked = {}

    rec = row_to_record(parked, key=str(getattr(lead, "id", "")))

    # Real columns win.
    rec.source_key = rec.source_key or ""
    rec.first_name = getattr(lead, "first_name", "") or rec.first_name
    rec.last_name = getattr(lead, "last_name", "") or rec.last_name

    emails = [e for e in [getattr(lead, "email", None)] if e]
    emails += [e for e in rec.emails if e not in emails]
    rec.emails = tuple(emails)

    phones = [p for p in [getattr(lead, "phone", None),
                          getattr(lead, "callback_phone", None),
                          getattr(lead, "phone_raw", None)] if p]
    phones += [p for p in rec.phones if p not in phones]
    rec.phones = tuple(phones)

    for f in ("street_address", "city", "state", "zip_code"):
        v = getattr(lead, f, None)
        if v:
            setattr(rec, f, v)

    rec.last_contact_date = getattr(lead, "last_contact_date", None) or rec.last_contact_date
    rec.last_action = getattr(lead, "last_action_raw", "") or rec.last_action
    rec.status_reason = getattr(lead, "status_reason_raw", "") or rec.status_reason
    rec.created_on = getattr(lead, "created_at", None) or rec.created_on

    # Operational suppression state. Reconciliation reads it; it never writes it.
    status = (getattr(lead, "status", "") or "").strip().lower()
    flag = (getattr(lead, "manual_flag", "") or "").strip().lower()
    rec.suppressed = status == "dnc" or flag == "remove_all"
    if flag == "bad_email":
        rec.allow_email = False
    if getattr(lead, "sms_consent", False) is not True:
        # Absence of consent is not a denial on this side; the send gates own
        # that rule. Recorded as unknown so reconciliation cannot invent one.
        rec.allow_sms = rec.allow_sms
    rec.raw = dict(rec.raw)
    return rec


def leads_to_records(leads: Iterable[Any]) -> list[Record]:
    return [lead_to_record(l) for l in leads]
