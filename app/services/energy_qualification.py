"""What an Atlantis advisor knows, what is missing, and the supported next step.

Read-only over the lead's own record (custom fields written by the website
intake, the advisor or Rate Requests). It does not guess and it does not
price anything: no rate, savings figure or commission appears here. Supplier
eligibility comes from energy_suppliers.catalog() - real capability (homes /
businesses / switched on or not), nothing else.

Each field is reported as
    have      - recorded on the lead
    missing   - needed for the next step and not recorded
    unknown   - recorded as "not sure" by the customer or advisor
    na        - does not apply to this kind of request
and `required` says whether the next step needs it. Optional fields are
reported so the advisor can see them, never to block a request.

Bill analysis is manual: the advisor types what the bill says into
`bill` (total, period, kWh, charges, contract terms, actual vs estimated).
There is no OCR and nothing here pretends to read a document.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

UNKNOWN_WORDS = {"unknown", "not sure", "dont know", "don't know", "n/a?", "?"}

# Fields an advisor may record through POST /energy-ops/leads/{id}/qualification.
EDITABLE = {
    "service_address": 300, "service_area": 120, "current_supplier": 120,
    "contract_end_date": 10, "requested_start_date": 10, "move_date": 10,
    "monthly_usage": 60, "annual_usage_kwh": 20, "rate_type": 20,
    "decision_maker": 160, "meters": 60, "locations": 160,
    "contact_preference": 40, "question": 500, "company": 160,
}
BILL_FIELDS = {
    "total_amount": 20, "period_start": 10, "period_end": 10, "usage_kwh": 20,
    "energy_charge": 60, "fixed_charges": 60, "delivery_charges": 60,
    "contract_terms": 300, "values_are": 10,        # "actual" | "estimated"
}


def _cf(lead) -> Dict[str, Any]:
    try:
        d = json.loads(lead.custom_fields or "{}")
        return d if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


def segment_of(cf: Dict[str, Any]) -> str:
    kind = cf.get("request_kind")
    seg = str(cf.get("segment") or "").strip().lower()
    if kind == "partner":
        return "partner"
    if seg.startswith("commercial") or kind == "commercial":
        return "commercial"
    if seg.startswith("residential") or kind in ("rate_review", "bill_review", "move"):
        return "residential"
    return "unknown"


def _state(value) -> str:
    if value in (None, "", [], {}):
        return "missing"
    if str(value).strip().lower() in UNKNOWN_WORDS:
        return "unknown"
    return "have"


def _field(key, label, value, required, applies=True) -> Dict[str, Any]:
    if not applies:
        return {"key": key, "label": label, "state": "na", "required": False, "value": None}
    st = _state(value)
    return {"key": key, "label": label, "state": st, "required": bool(required),
            "value": value if st == "have" else None}


def _usage_known(cf) -> Any:
    bill = cf.get("bill") if isinstance(cf.get("bill"), dict) else {}
    return cf.get("annual_usage_kwh") or cf.get("monthly_usage") or bill.get("usage_kwh")


def fields_for(lead, cf: Dict[str, Any], segment: str) -> List[Dict[str, Any]]:
    kind = cf.get("request_kind")
    where = cf.get("service_address") or cf.get("service_area") or lead.zip_code
    contact = lead.email or lead.phone
    if segment == "partner":
        return [
            _field("company", "Partner company", cf.get("company"), False),
            _field("contact", "Partner email or phone", contact, True),
            _field("referred_customer", "Who they are referring (name / move date)",
                   lead.notes or cf.get("question") or cf.get("message"), True),
        ]
    commercial = segment == "commercial"
    c = cf.get("commercial") if isinstance(cf.get("commercial"), dict) else {}
    locs = [l for l in (c.get("locations") or []) if isinstance(l, dict)]
    loc_ends = sorted(l["contract_end_date"] for l in locs if l.get("contract_end_date"))
    if not where and locs:
        where = locs[0].get("address")
    return [
        _field("contact", "Email or phone", contact, True),
        _field("owner", "Atlantis advisor who owns this", lead.assigned_to_id, True),
        _field("service_location", "Service address or ZIP", where, True),
        _field("company", "Business name", cf.get("company"),
               True, applies=commercial),
        _field("decision_maker", "Who signs for electricity", cf.get("decision_maker"), True, applies=commercial),
        _field("locations", "Sites / meters",
               ("%d recorded" % len(locs)) if locs else (cf.get("locations") or cf.get("meters")),
               True, applies=commercial),
        _field("usage", "Usage or a recent bill", _usage_known(cf), True),
        _field("current_supplier", "Current supplier", cf.get("current_supplier"), False),
        _field("contract_end_date", "Current contract ends",
               cf.get("contract_end_date") or (loc_ends[0] if loc_ends else None), commercial),
        _field("move_date", "Move date", cf.get("move_date"), True, applies=(kind == "move")),
        _field("requested_start_date", "Wanted start date", cf.get("requested_start_date"), False),
        _field("consent", "Agreed to be contacted by text/phone",
               True if getattr(lead, "sms_consent", None) else None, False),
    ]


def supplier_options(segment: str, kind: Optional[str]) -> List[Dict[str, Any]]:
    from app.services import energy_suppliers
    out = []
    for s in energy_suppliers.catalog():
        if segment == "partner":
            ok, why = False, "A partner record is not the customer - hand off the referred customer."
        elif segment == "commercial":
            ok = bool(s.get("commercial"))
            why = None if ok else "Homes only."
        elif segment == "residential":
            ok = bool(s.get("residential"))
            why = None if ok else "Businesses only."
        else:
            ok, why = False, "Residential or business is not known yet."
        if ok and s["key"] == "move_concierge" and kind != "move":
            ok, why = False, "For customers who are moving."
        if ok and s["key"] == "comparepower" and not s.get("link_configured"):
            why = "Atlantis tracking link is not set up yet - use the affiliate dashboard link by hand."
        out.append({"key": s["key"], "name": s["name"], "eligible": ok, "note": why,
                    "online_enrollment": bool(s.get("online_enrollment"))})
    return out


def qualify(lead) -> Dict[str, Any]:
    cf = _cf(lead)
    segment = segment_of(cf)
    kind = cf.get("request_kind")
    fields = fields_for(lead, cf, segment)
    missing = [f for f in fields if f["required"] and f["state"] in ("missing", "unknown")]
    keys = {f["key"] for f in missing}
    if "owner" in keys:
        step = ("assign_owner", "Assign an Atlantis advisor")
    elif segment == "unknown":
        step = ("classify", "Find out if this is a home or a business")
    elif segment == "partner":
        step = (("request_info", "Get the referred customer's details from the partner")
                if missing else ("contact_referred_customer", "Contact the referred customer"))
    elif missing:
        step = ("request_info", "Get: " + ", ".join(f["label"] for f in missing if f["key"] != "owner"))
    elif segment == "commercial":
        step = ("request_supplier_quotes", "Request supplier quotes (advisor-led)")
    elif kind == "move":
        step = ("move_concierge", "Work the Move Concierge checklist and choose a supplier for the new address")
    else:
        step = ("choose_supplier", "Review options with the customer and record the hand-off")
    bill = cf.get("bill") if isinstance(cf.get("bill"), dict) else None
    return {
        "lead_id": lead.id, "segment": segment, "request_kind": kind,
        "fields": fields, "missing": [f["key"] for f in missing],
        "ready": not missing and segment in ("residential", "commercial"),
        "next_step": {"code": step[0], "label": step[1]},
        "suppliers": supplier_options(segment, kind),
        "bill": bill,
        "bill_note": ("Bill values are typed in by an advisor from the customer's bill; "
                      "nothing is read automatically and no rate or saving is calculated."),
    }


def apply_update(lead, fields: Dict[str, Any], bill: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Record what the advisor learned. Unknown keys are refused, values are
    trimmed to size; an empty string clears a field. Returns what changed."""
    cf = _cf(lead)
    changed = {}
    for k, v in (fields or {}).items():
        if k not in EDITABLE:
            raise ValueError("Unknown field: %s" % k)
        v = "" if v is None else str(v).strip()[:EDITABLE[k]]
        if v == "":
            if k in cf:
                cf.pop(k)
                changed[k] = None
        elif cf.get(k) != v:
            cf[k] = v
            changed[k] = v
    if bill is not None:
        cur = cf.get("bill") if isinstance(cf.get("bill"), dict) else {}
        for k, v in bill.items():
            if k not in BILL_FIELDS:
                raise ValueError("Unknown bill field: %s" % k)
            v = "" if v is None else str(v).strip()[:BILL_FIELDS[k]]
            if k == "values_are" and v and v not in ("actual", "estimated"):
                raise ValueError("values_are must be 'actual' or 'estimated'.")
            if v == "":
                cur.pop(k, None)
            else:
                cur[k] = v
        cf["bill"] = cur
        changed["bill"] = dict(cur)
    lead.custom_fields = json.dumps(cf)
    return changed
