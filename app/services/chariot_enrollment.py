"""CHARIOT ONLINE ENROLLMENT - the "Enroll online with Chariot" option, beside
"Advisor enrolls manually" (the existing hand-off + Enroll action).

What Chariot told Atlantis (Tim Richmond, Oct 6 2026), and what each fact
forces here:

* RESIDENTIAL ONLY. Commercial and partner records are refused before any
  payload is assembled (`energy_enrollment.build_enrollment_payload`).
* UP TO 120 SECONDS. The adapter waits 125 s; a timeout comes back pending
  with "outcome unknown - do not resubmit".
* NO STATUS ENDPOINT, NO WEBHOOK, NO REFERENCE LOOKUP after SubmitEnrollment,
  and the final documents go to the customer's own email. So no answer from
  the API is ever treated as a finished enrollment: EVERY submission lands in
  the confirmation queue, and only a person marks it enrolled - which runs the
  same Enroll action a manual enrollment uses.

ONE SUBMISSION PER PERSON / METER. The row is written BEFORE the call, so two
clicks, two tabs or a crash mid-call still leave exactly one attempt on record,
reading "outcome unknown". A retry is allowed only after a person records, on
the earlier row, that Chariot did NOT receive it.

SENSITIVE DATA (date of birth, SSN or driver licence) lives in the request
only: assembled in memory, sent, dropped. It is never written to the lead, the
audit log, a note, a task or the transaction row.

SWITCHED OFF. Nothing reaches Chariot unless CHARIOT_ALLOW_TRANSACTIONS is set
on the server; until then a submission is refused before any row is written.
Plan, address and start-date lookups are read-only and need only the key.
"""

import json
import re
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.provider_transaction_models import ProviderTransaction
from app.services import energy_enrollment as ee
from app.services import provider_transactions as ptx
from app.services.providers import base, chariot

PROVIDER = chariot.PROVIDER
REASON_SUBMITTING = "submitting"

OUTCOME_ENROLLED = "enrolled"          # confirmed with the customer / Chariot's report
OUTCOME_NOT_ENROLLED = "not_enrolled"  # Chariot declined, or the customer cancelled
OUTCOME_NOT_RECEIVED = "not_received"  # Chariot never got it: a retry is allowed
OUTCOMES = (OUTCOME_ENROLLED, OUTCOME_NOT_ENROLLED, OUTCOME_NOT_RECEIVED)

# The only option values this integration sends are the ones in Chariot's own
# Broker API Guide sample (tests/test_chariot_provider_adapter.py). Anything
# else - a deposit, a waiver, autopay, paper bills - is handled by a person.
FIXED_OPTIONS = {
    "BillDeliverMethodID": "Email", "languagePreference": "English",
    "IsAccountHolder": "Yes", "BillingAddressTypeID": "No",
    "AutoPay": "No", "PayDeposit": "No", "IsPromoInterested": "No",
}
SERVICE_TYPES = {"switching": "switching", "moving": "moving"}


class SubmissionRefused(ValueError):
    """The submission is not allowed right now. The message says why."""


class NotConnected(RuntimeError):
    """No Chariot key on this server - lookups cannot run."""


# ── status and classification ────────────────────────────────────────────────
def status() -> Dict[str, Any]:
    rep = chariot.configuration_report()
    return {"connected": bool(rep["key_present"]), "environment": rep["environment"],
            "promo_code_set": bool(rep["promo_code_present"]),
            "submissions_enabled": chariot.transactions_armed(),
            "fixed_options": FIXED_OPTIONS,
            "note": ("Online enrollment is switched OFF: you can prepare an enrollment, nothing is sent "
                     "to Chariot." if not chariot.transactions_armed() else
                     "Online enrollment is ON: submitting sends a real enrollment to Chariot.")}


def classification(lead) -> str:
    """This vertical's lead -> energy_enrollment classification, from what the
    record says (segment / request kind / tier) - never guessed."""
    try:
        cf = json.loads(lead.custom_fields or "{}")
    except (ValueError, TypeError):
        cf = {}
    cf = cf if isinstance(cf, dict) else {}
    from app.services.energy_qualification import segment_of
    seg = segment_of(cf)
    kind = str(cf.get("request_kind") or "").lower()
    if seg == "partner":
        return ee.CLASS_PARTNER_REFERRAL
    if seg == "commercial":
        return ee.CLASS_COMMERCIAL_ENERGY
    if seg != "residential":
        return ee.CLASS_GENERAL_INQUIRY          # not known to be a home: not eligible
    if kind == "move":
        return ee.CLASS_MOVE_CONCIERGE
    if kind == "bill_review":
        return ee.CLASS_BILL_ANALYSIS
    if (lead.tier or "") in ("contract_signed", "renewal_due"):
        return ee.CLASS_RENEWAL
    return ee.CLASS_RESIDENTIAL_RATE_REVIEW


# ── read-only lookups (need the key, never the switch) ───────────────────────
def _need_key():
    if not chariot.is_configured():
        raise NotConnected("Chariot is not connected on this server yet (no API key).")


def _read_out(res: base.ProviderResult) -> Dict[str, Any]:
    if not res.ok:
        if res.normalized_status == base.INTEGRATION_ERROR:
            return {"ok": False, "error": "Chariot could not be reached or did not accept our key. Try again later."}
        return {"ok": False, "error": res.raw_message or res.reason or res.normalized_status}
    return {"ok": True, "data": res.data}


def plans(zip_code: str) -> Dict[str, Any]:
    _need_key()
    if not re.fullmatch(r"\d{5}", (zip_code or "").strip()):
        raise ValueError("Enter the 5-digit service ZIP.")
    return _read_out(chariot.list_plans(zip_code.strip()))


def addresses(zip_code: str, address1: str) -> Dict[str, Any]:
    _need_key()
    if not re.fullmatch(r"\d{5}", (zip_code or "").strip()) or not (address1 or "").strip():
        raise ValueError("Enter the street address and 5-digit ZIP.")
    return _read_out(chariot.search_address(zip_code.strip(), address1.strip()[:120]))


def start_dates(esiid: str, service_type: str, start: str) -> Dict[str, Any]:
    _need_key()
    st = SERVICE_TYPES.get((service_type or "").lower())
    if not st or not (esiid or "").strip():
        raise ValueError("Choose the meter and whether the customer is switching or moving.")
    return _read_out(chariot.service_type_date(esiid.strip(), st.title(), (start or "Standard Switch").strip()))


# ── submission ───────────────────────────────────────────────────────────────
def _blocking_row(db: Session, org_id: str, lead_id: Optional[str], esiid: Optional[str],
                  operation: str) -> Optional[ProviderTransaction]:
    """An earlier attempt that forbids another one: anything unresolved, or
    resolved as anything other than "Chariot never received it"."""
    from sqlalchemy import or_
    keys = []
    if lead_id:
        keys.append(ProviderTransaction.lead_id == lead_id)
    if esiid:
        keys.append(ProviderTransaction.esiid == str(esiid))
    if not keys:
        return None
    q = (db.query(ProviderTransaction)
         .filter(ProviderTransaction.organization_id == org_id, ProviderTransaction.provider == PROVIDER,
                 ProviderTransaction.operation == operation, or_(*keys))
         .order_by(ProviderTransaction.submitted_at.desc()))
    for row in q.all():
        if row.resolved_at is None:
            return row
        if (row.resolution_note or "").startswith("[%s]" % OUTCOME_NOT_RECEIVED):
            continue
        if row.normalized_status == base.FAILED_VALIDATION:
            continue        # our request was malformed; nothing was enrolled
        return row
    return None


def _digits(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


_ID_LIMITS = {"dob": 10, "ssn": 11, "drivers_licence": 30, "drivers_licence_state": 2}


def build_fields(lead, form: Dict[str, Any]) -> Dict[str, Any]:
    """The Chariot field set from the lead + what the advisor typed. Identity
    values are passed through only; nothing here stores them, and a bad one is
    refused by NAME, never by repeating it."""
    for k, n in _ID_LIMITS.items():
        if len(str(form.get(k) or "").strip()) > n:
            raise ee.EnrollmentNotReady("The %s looks too long - check it and try again."
                                        % k.replace("_", " ").replace("dob", "date of birth"))
    if str(form.get("ssn") or "").strip() and len(_digits(form.get("ssn"))) != 9:
        raise ee.EnrollmentNotReady("The SSN must have 9 digits.")
    if str(form.get("esiid") or "").strip() and not re.fullmatch(r"\d{10,22}", str(form.get("esiid")).strip()):
        raise ee.EnrollmentNotReady("Choose the meter from the address lookup (ESI ID).")
    st = SERVICE_TYPES.get(str(form.get("service_type") or "").lower())
    fields = {
        "Zip": str(form.get("zip") or lead.zip_code or "").strip(),
        "ProductID": str(form.get("product_id") or "").strip(),
        "FirstName": str(form.get("first_name") or lead.first_name or "").strip(),
        "LastName": str(form.get("last_name") or lead.last_name or "").strip(),
        "EmailAddress": str(form.get("email") or lead.email or "").strip(),
        "PhoneNumber": _digits(form.get("phone") or lead.phone)[-10:],
        "ESIID": str(form.get("esiid") or "").strip(),
        "ServiceType": st or "",
        "ServiceStartDate": str(form.get("service_start_date") or "").strip(),
        "DOB": str(form.get("dob") or "").strip(),
    }
    if str(form.get("ssn") or "").strip():
        fields["SSN"] = _digits(form.get("ssn"))
    if str(form.get("drivers_licence") or "").strip():
        fields["DriversLicence"] = str(form.get("drivers_licence")).strip()
        fields["DriversLicenceState"] = str(form.get("drivers_licence_state") or "TX").strip()[:2].upper()
    if chariot.promo_code():
        fields["PromoCode"] = chariot.promo_code()
    fields.update(FIXED_OPTIONS)
    return fields


def submit_enrollment(db: Session, *, organization_id: str, lead, actor_user_id: str,
                      form: Dict[str, Any], plan_snapshot: Optional[Dict[str, Any]] = None) -> ProviderTransaction:
    """Submit one residential enrollment. Order is the design: refuse what is
    not allowed; write the row; then call. Identity data lives only in this
    function's locals."""
    if not chariot.transactions_armed():
        raise SubmissionRefused("Online enrollment with Chariot is switched off on this server. "
                                "Nothing was sent. Use \"Advisor enrolls manually\" for now.")
    if not form.get("customer_authorized"):
        raise SubmissionRefused("Confirm the customer authorized this enrollment and saw the plan documents.")
    payload, audit = ee.build_enrollment_payload(classification=classification(lead),
                                                 fields=build_fields(lead, form))
    esiid = audit.get("ESIID")
    # Serialize: two tabs / two advisors / two leads for one meter must not
    # both pass the check below. Lead row lock (all databases) + a meter lock
    # on Postgres, both held until the attempt row is committed.
    from app.services.energy_suppliers import _lock
    lead = _lock(db, lead)
    if db.get_bind().dialect.name == "postgresql" and esiid:
        from sqlalchemy import text
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                   {"k": "chariot-esiid:%s:%s" % (organization_id, esiid)})
    earlier = _blocking_row(db, organization_id, lead.id, esiid, base.OP_SUBMIT_ENROLLMENT)
    if earlier is not None:
        raise SubmissionRefused(
            "An enrollment for this customer or meter was already submitted on %s and has not been "
            "cleared. Confirm it with the customer or Chariot first. Nothing was sent."
            % earlier.submitted_at.strftime("%Y-%m-%d %H:%M UTC"))
    if plan_snapshot is not None:
        base.assert_no_sensitive(plan_snapshot, "plan_snapshot")
    claim = base.ProviderResult(operation=base.OP_SUBMIT_ENROLLMENT,
                                normalized_status=base.PENDING_PROVIDER, reason=REASON_SUBMITTING)
    row = ptx.record(db, organization_id=organization_id, result=claim, provider=PROVIDER,
                     environment=chariot.environment(), lead_id=lead.id, actor_user_id=actor_user_id,
                     product_id=audit.get("ProductID"), esiid=esiid, service_type=audit.get("ServiceType"),
                     service_start_date=audit.get("ServiceStartDate"), plan_snapshot=plan_snapshot,
                     commit=False)
    row.needs_human = True
    db.commit()                        # the attempt is on record BEFORE anything is sent
    try:
        result = chariot.submit_enrollment(payload)
    except chariot.ChariotTransactionsDisarmed:
        payload = None
        ptx.resolve(db, row, user_id=actor_user_id,
                    note="[%s] Switched off before sending - nothing reached Chariot." % OUTCOME_NOT_RECEIVED,
                    commit=True)
        raise SubmissionRefused("Online enrollment was switched off before sending. Nothing was sent.")
    except Exception as exc:           # noqa: BLE001 - the row stays "outcome unknown"
        result = base.ProviderResult(operation=base.OP_SUBMIT_ENROLLMENT,
                                     normalized_status=base.PENDING_PROVIDER,
                                     reason=chariot.REASON_OUTCOME_UNKNOWN, raw_message=str(exc)[:300])
    finally:
        payload = None                 # noqa: F841 - identity data goes no further
    ptx.apply_result(db, row, result, commit=False)
    row.attempt_count = 1
    row.needs_human = True             # no API answer closes an enrollment: a person confirms every one
    db.commit()
    return row


def link_handoff(db: Session, *, lead, actor_user_id: str, row: ProviderTransaction) -> Optional[str]:
    """The online submission is ALSO a supplier hand-off to Chariot, so it shows
    with every other hand-off (Suppliers -> waiting for an outcome) and gets the
    same follow-up task. Re-uses an open Chariot hand-off if there is one."""
    from app.services import energy_suppliers
    out = energy_suppliers.record_handoff(
        db, lead=lead, actor_user_id=actor_user_id, supplier="chariot",
        note="Submitted online to Chariot (EvoSys submission %s). Confirm the outcome before marking enrolled."
             % row.id[:8])
    return (out.get("handoff") or {}).get("id")


# ── a person confirms what happened ──────────────────────────────────────────
def reconcile(db: Session, row: ProviderTransaction, *, user_id: str, outcome: str, note: str) -> ProviderTransaction:
    """`normalized_status` keeps what Chariot said; the outcome a person
    verified is recorded beside it (prefixed on the note, no schema change)."""
    if outcome not in OUTCOMES:
        raise ValueError("Choose what happened: enrolled, not enrolled, or Chariot never received it.")
    if not (note or "").strip():
        raise ValueError("Say how it was verified (call with the customer, Chariot's report, ...).")
    if row.resolved_at is not None:
        raise ValueError("This submission was already closed.")
    ptx.resolve(db, row, user_id=user_id, note="[%s] %s" % (outcome, note.strip()), commit=False)
    return row


def close_handoff(db: Session, *, lead, actor_user_id: str, outcome: str, note: str) -> Optional[str]:
    """Mirror a verified outcome onto the open Chariot hand-off, if any."""
    from app.services import energy_suppliers
    open_h = next((h for h in reversed(energy_suppliers.handoffs(lead))
                   if h.get("supplier") == "chariot" and energy_suppliers.is_open(h)), None)
    if open_h is None or outcome == OUTCOME_NOT_RECEIVED:
        return None
    energy_suppliers.record_outcome(
        db, lead=lead, actor_user_id=actor_user_id, handoff_id=open_h["id"],
        outcome="confirmed" if outcome == OUTCOME_ENROLLED else "not_completed",
        source="staff_report",
        note="Online submission confirmed: %s" % note.strip()[:300])
    return open_h["id"]


def queue(db: Session, organization_id: str, *, include_closed: bool = False, limit: int = 100,
          lead_ids=None):
    q = db.query(ProviderTransaction).filter(
        ProviderTransaction.organization_id == organization_id, ProviderTransaction.provider == PROVIDER,
        ProviderTransaction.operation.in_(base.TRANSACTIONAL_OPERATIONS))
    if lead_ids is not None:
        q = q.filter(ProviderTransaction.lead_id.in_(lead_ids))
    if not include_closed:
        q = q.filter(ProviderTransaction.resolved_at.is_(None))
    return q.order_by(ProviderTransaction.submitted_at.asc()).limit(limit).all()


SAID = {
    base.PROVIDER_CONFIRMED: "Chariot says completed - confirm with the customer, then mark enrolled",
    base.PENDING_PROVIDER: "Pending at Chariot - no API will report the result; check with Chariot",
    base.ACTION_REQUIRED: "Chariot needs something from the customer (deposit or payment details)",
    base.PROVIDER_FAILED: "Chariot declined it",
    base.FAILED_VALIDATION: "Chariot rejected our request as incomplete - fix and resubmit",
    base.INTEGRATION_ERROR: "Connection or key problem on our side - not the customer",
}


def row_out(row: ProviderTransaction, lead_names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    note = row.resolution_note or ""
    outcome = note[1:note.find("]")] if note.startswith("[") and "]" in note else None
    plan = None
    try:
        plan = json.loads(row.plan_snapshot) if row.plan_snapshot else None
    except (ValueError, TypeError):
        plan = None
    unknown = row.reason_code == chariot.REASON_OUTCOME_UNKNOWN
    return {
        "id": row.id, "lead_id": row.lead_id, "name": (lead_names or {}).get(row.lead_id),
        "operation": row.operation, "environment": row.environment,
        "product_id": row.product_id, "plan_title": (plan or {}).get("title"),
        "esiid": row.esiid, "service_type": row.service_type, "service_start_date": row.service_start_date,
        "provider_reference": row.provider_reference,
        "provider_said": row.raw_status, "provider_message": row.raw_message,
        "status": row.normalized_status, "reason": row.reason_code, "amount_quoted": row.amount_quoted,
        "meaning": ("Outcome unknown - the request may have reached Chariot. Do NOT resubmit; check with Chariot."
                    if unknown else SAID.get(row.normalized_status, row.normalized_status)),
        "submitted_at": row.submitted_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.submitted_at else None,
        "resolved_at": row.resolved_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.resolved_at else None,
        "verified_outcome": outcome,
        "waiting_hours": (round((datetime.utcnow() - row.submitted_at).total_seconds() / 3600, 1)
                          if row.submitted_at and not row.resolved_at else None),
    }
