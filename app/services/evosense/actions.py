"""EvoSense NEXT ACTIONS — what a person can DO with a finding, computed from
real state (never a hard-coded menu).

Every property screen answers six questions from this module:
    what was found · why it matters · what is missing ·
    what I can do next · if I can't, why · what happens after

`actions(db, org_id, prop)` returns the full list for the property page;
`compact(db, org_id, prop)` returns the single most useful next step for an
inbox row. Both are read-only: nothing here writes, calls a provider, spends,
or touches consent. Each action names the real endpoint that performs it.

Truth rules:
  * a provider is "available" only if the routing engine would call it now;
    otherwise the action is disabled and says so (NOT CONFIGURED / paused)
  * a found phone number is never described as permission to contact
  * promotion requirements are the ones `promotion.promote` enforces, plus the
    recommendations it does not enforce, labelled as such
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseFeedback, EvoSenseHandoff,
                                        EvoSenseIdentityReview, EvoSensePerson)
from app.services.evosense import common as C
from app.services.evosense import contacts as CT

BASE = "/wholesale/evosense"
DISMISS_KINDS = ("IGNORE", "BAD_FIT", "WRONG_OWNER", "NOT_ACTUALLY_DISTRESSED")


def _a(key, label, *, enabled=True, reason=None, effect=None, endpoint=None, method="POST",
       primary=False, payload=None, kind="action") -> Dict[str, Any]:
    return {"key": key, "label": label, "enabled": bool(enabled),
            "reason_if_disabled": None if enabled else reason,
            "effect_description": effect, "endpoint": endpoint, "method": method,
            "primary": bool(primary), "payload": payload, "kind": kind}


def dismissal(db, org_id: str, prop) -> Optional[Dict[str, Any]]:
    """The latest person-recorded dismissal, if the property is dismissed."""
    f = (db.query(EvoSenseFeedback)
         .filter(EvoSenseFeedback.organization_id == org_id,
                 EvoSenseFeedback.property_id == prop.id)
         .order_by(EvoSenseFeedback.created_at.desc()).first())
    if f is None or f.kind not in DISMISS_KINDS:
        return None
    return {"kind": f.kind, "reason": f.reason,
            "at": f.created_at.isoformat() + "Z" if f.created_at else None}


def open_identity_review(db, org_id: str, prop):
    return (db.query(EvoSenseIdentityReview)
            .filter(EvoSenseIdentityReview.organization_id == org_id,
                    EvoSenseIdentityReview.status == "open",
                    EvoSenseIdentityReview.candidate_property_ids.like('%%"%s"%%' % prop.id))
            .first())


def _active_contacts(db, org_id: str, owner) -> List[EvoSenseContactPoint]:
    if owner is None:
        return []
    return (db.query(EvoSenseContactPoint)
            .filter(EvoSenseContactPoint.organization_id == org_id,
                    EvoSenseContactPoint.owner_id == owner.id,
                    EvoSenseContactPoint.status == "active").all())


def lookup_availability(db, org_id: str, prop) -> Dict[str, Any]:
    """Would the engine call a contact-lookup provider for this property now?

    Read-only: routing may lazily create provider-config rows, so it runs in a
    savepoint that is always rolled back."""
    from app.services.evosense import providers as PV
    ctl = C.controls(db, org_id)
    if ctl.paused_all:
        return {"available": False, "reason": "EvoSense is paused for this workspace."}
    if ctl.paused_paid_data:
        return {"available": False, "reason": "Paid data is paused for this workspace."}
    routes = []
    sp = db.begin_nested()
    try:
        routes = PV.route(db, org_id, C.CONTACT_ENRICHMENT, sandbox_allowed=bool(prop.is_test))
        routes = [(p.key, p.label, p.connector_kind, int(cost or 0)) for p, _cfg, cost in routes]
    except Exception:                                       # noqa: BLE001
        routes = []
    finally:
        sp.rollback()
    if not routes:
        return {"available": False,
                "reason": "No contact-lookup provider is configured and enabled for this workspace "
                          "(NOT CONFIGURED). Enter the owner's contact by hand instead."}
    key, label, kind, cost = routes[0]
    return {"available": True, "provider": key, "provider_label": label, "connector_kind": kind,
            "estimated_cost_cents": cost, "sandbox": kind == C.SANDBOX}


def requirements(db, org_id: str, prop, *, owner=None, contacts=None, review=None) -> Dict[str, Any]:
    """Promotion readiness. `required` items block promotion (the same rules
    promotion.promote enforces); `recommended` items do not block but are
    shown so the deal team knows what it inherits."""
    owner = owner if owner is not None else CT.primary_owner(db, prop)
    contacts = contacts if contacts is not None else _active_contacts(db, org_id, owner)
    review = review if review is not None else open_identity_review(db, org_id, prop)
    identity_ok = prop.identity_status != "review" and review is None
    required = [{"key": "identity", "label": "Property identity confirmed", "met": identity_ok,
                 "detail": None if identity_ok else
                 "Another record may be this same property. Merge it or keep it separate first."}]
    recommended = [
        {"key": "owner", "label": "Owner of record known", "met": owner is not None,
         "detail": None if owner is not None else "The deal will start without an owner name."},
        {"key": "contact", "label": "Owner contact on file", "met": bool(contacts),
         "detail": None if contacts else
         "No phone or email yet. The deal will start with no seller attached."},
    ]
    return {"ready": all(r["met"] for r in required), "required": required, "recommended": recommended}


def _seller_person(db, org_id: str, owner, contacts) -> Optional[EvoSensePerson]:
    ids = [c.person_id for c in contacts if c.person_id]
    if not ids:
        return None
    return (db.query(EvoSensePerson)
            .filter(EvoSensePerson.organization_id == org_id, EvoSensePerson.id.in_(ids))
            .order_by(EvoSensePerson.created_at.asc()).first())


def actions(db, org_id: str, prop) -> Dict[str, Any]:
    pid = prop.id
    owner = CT.primary_owner(db, prop)
    contacts = _active_contacts(db, org_id, owner)
    review = open_identity_review(db, org_id, prop)
    dismissed = dismissal(db, org_id, prop)
    handoff = (db.query(EvoSenseHandoff)
               .filter(EvoSenseHandoff.organization_id == org_id, EvoSenseHandoff.property_id == pid,
                       EvoSenseHandoff.status == "open").first())
    req = requirements(db, org_id, prop, owner=owner, contacts=contacts, review=review)
    promoted = bool(prop.promoted_deal_id)
    out: List[Dict[str, Any]] = []

    if promoted:
        out.append(_a("open_deal", "Open the deal in Deal Operations", primary=True, method="LINK",
                      endpoint="/wholesale/deals/%s" % prop.promoted_deal_id,
                      effect="This property is already a Wholesale deal. The deal team works it there; "
                             "EvoSense keeps the discovery history attached."))

    out.append(_a("review_evidence", "Review why it was found", method="ANCHOR", endpoint="#signals",
                  kind="review",
                  effect="Shows each distress signal with its source record, date and freshness."))

    if review is not None:
        out.append(_a("resolve_identity", "Resolve the identity review", primary=not promoted,
                      endpoint="%s/identity-reviews/%s" % (BASE, review.id), kind="exception",
                      payload={"options": ["merge", "new"]},
                      effect="Merge the incoming record into this property, or keep it separate. "
                             "Promotion is blocked until this is decided."))

    if handoff is not None and not promoted:
        out.append(_a("acknowledge_handoff", "Acknowledge — I'm on it",
                      endpoint="%s/handoffs/%s" % (BASE, handoff.id), payload={"status": "acknowledged"},
                      kind="assign",
                      effect="Marks the hand-off as taken by you. Nothing is sent to the seller."))

    # ── Owner / contact path ────────────────────────────────────────────────
    closed_reason = ("Already in Deal Operations — continue on the deal." if promoted else None)
    if owner is None:
        out.append(_a("add_owner", "Add the owner of record", enabled=not promoted, reason=closed_reason,
                      endpoint="%s/properties/%s/owner" % (BASE, pid), kind="contact",
                      primary=not promoted and review is None,
                      effect="Records the owner's name as YOU entered it (source: manual). "
                             "Nothing is looked up and nothing is sent."))
    lk = lookup_availability(db, org_id, prop) if (owner is not None and not promoted) else None
    if owner is None:
        lk_reason = "No owner of record is known, so there is nobody to look up."
    elif promoted:
        lk_reason = closed_reason
    elif lk and not lk["available"]:
        lk_reason = lk["reason"]
    else:
        lk_reason = None
    if lk and lk.get("available"):
        cost = lk.get("estimated_cost_cents") or 0
        effect = ("Asks %s for the owner's phone/email%s. The EvoSense approval and budget gates "
                  "still apply. A number found is NOT permission to contact."
                  % (lk["provider_label"], " (sandbox — fictional data, no charge)" if lk["sandbox"]
                     else (" (estimated $%.2f)" % (cost / 100.0) if cost else "")))
        approve = prop.status == "needs_enrichment"
    else:
        effect = "Would ask a configured contact-lookup provider for the owner's phone/email."
        approve = False
    out.append(_a("run_enrichment", "Approve contact lookup" if approve else "Look up owner contact",
                  enabled=lk_reason is None, reason=lk_reason,
                  endpoint="%s/properties/%s/enrich" % (BASE, pid), payload={"approved": approve},
                  kind="contact", effect=effect))
    out.append(_a("add_contact_manual", "Enter owner phone/email by hand",
                  enabled=owner is not None and not promoted,
                  reason=closed_reason or ("Add the owner of record first." if owner is None else None),
                  endpoint="%s/properties/%s/contacts" % (BASE, pid), kind="contact",
                  primary=(owner is not None and not contacts and not promoted and review is None),
                  effect="Saves the number/email you have (source: manual, unverified). It records no "
                         "SMS consent and starts no outreach."))
    person = _seller_person(db, org_id, owner, contacts)
    if contacts and not promoted:
        out.append(_a("seller_contact", "Seller contact for the deal", kind="info", method="NONE",
                      enabled=True,
                      effect=("On promotion, %s becomes the deal's seller: an existing contact in this "
                              "workspace with the same phone/email is reused; otherwise one contact "
                              "record is created (it uses a lead seat). No consent is recorded."
                              % (person.full_name if person else "this owner contact"))))

    # ── Promote ─────────────────────────────────────────────────────────────
    if not promoted:
        unmet = [r["label"] for r in req["required"] if not r["met"]]
        out.append(_a("promote", "Promote to Deal Operations", enabled=req["ready"],
                      reason=("Not yet: " + "; ".join(unmet)) if unmet else None,
                      endpoint="%s/properties/%s/promote" % (BASE, pid), kind="promote",
                      primary=req["ready"] and bool(contacts) and review is None,
                      effect="Creates ONE Wholesale property + deal through Deal Operations' own service, "
                             "attaches the seller (if a contact is on file), and carries signals, evidence "
                             "and spend. No message is sent and no offer is made."))

    # ── Dismiss ─────────────────────────────────────────────────────────────
    out.append(_a("dismiss", "Dismiss with a reason", enabled=not promoted and dismissed is None,
                  reason=closed_reason or ("Already dismissed: %s" % (dismissed["reason"] or dismissed["kind"])
                                           if dismissed else None),
                  endpoint="%s/properties/%s/dismiss" % (BASE, pid), kind="dismiss",
                  payload={"kinds": list(DISMISS_KINDS)},
                  effect="Records who dismissed it and why. The record and its evidence are kept."))

    nxt = next((a for a in out if a["primary"] and a["enabled"]), None) \
        or next((a for a in out if a["enabled"] and a["kind"] not in ("review", "info")), None)
    return {"actions": out, "requirements": req, "dismissed": dismissed,
            "next": {"key": nxt["key"], "label": nxt["label"]} if nxt else None,
            "lookup": lk}


def summary(db, org_id: str, prop, detail: Dict[str, Any], acts: Dict[str, Any]) -> Dict[str, Any]:
    """The six questions, answered in plain language from the payload."""
    sigs = [s for s in (detail.get("signals") or []) if s.get("freshness") != "stale"] \
        or (detail.get("signals") or [])
    found = ", ".join(s.get("label") or s.get("signal_type") for s in sigs[:4]) or "No distress signal on file"
    why = []
    if prop.opportunity_score is not None:
        why.append("Opportunity score %s/100" % prop.opportunity_score)
    if len(sigs) > 1:
        why.append("%d signals stack on this property" % len(sigs))
    if prop.equity_pct is not None:
        why.append("equity about %s%%" % prop.equity_pct)
    missing = []
    for r in acts["requirements"]["required"] + acts["requirements"]["recommended"]:
        if not r["met"]:
            missing.append(r["label"].replace(" known", "").replace(" on file", "").replace(" confirmed", "")
                           + " — missing")
    arv = (detail.get("facts") or {}).get("arv") or {}
    if not (isinstance(arv, dict) and arv.get("value") is not None):
        missing.append("ARV — no verified comparable sales")
    if prop.mortgage_balance is None:
        missing.append("Mortgage / liens — unknown")
    nxt = acts.get("next")
    a = next((x for x in acts["actions"] if nxt and x["key"] == nxt["key"]), None)
    blocked = [{"label": x["label"], "reason": x["reason_if_disabled"]}
               for x in acts["actions"] if not x["enabled"] and x["reason_if_disabled"]]
    return {"found": found, "why_it_matters": "; ".join(why) or "Not scored yet",
            "missing": missing, "next": nxt["label"] if nxt else "Nothing to do — see why below",
            "after": a["effect_description"] if a else None, "blocked": blocked}


def compact(db, org_id: str, prop) -> Dict[str, Any]:
    """One next step for an inbox row. Cheap: no provider routing."""
    if prop.promoted_deal_id:
        return {"key": "open_deal", "label": "Open deal", "href": "/wholesale/deals/%s" % prop.promoted_deal_id}
    if prop.identity_status == "review" or open_identity_review(db, org_id, prop) is not None:
        return {"key": "resolve_identity", "label": "Resolve identity", "tone": "danger"}
    d = dismissal(db, org_id, prop)
    if d is not None:
        return {"key": "dismissed", "label": "Dismissed", "reason": d["reason"] or d["kind"], "tone": "quiet"}
    owner = CT.primary_owner(db, prop)
    if owner is None:
        return {"key": "add_owner", "label": "Add owner"}
    if not _active_contacts(db, org_id, owner):
        blocked = (db.query(EvoSenseContactPoint.id)
                   .filter(EvoSenseContactPoint.organization_id == org_id,
                           EvoSenseContactPoint.owner_id == owner.id).first())
        if blocked is not None:
            return {"key": "add_contact", "label": "Contacts blocked — review", "tone": "quiet",
                    "reason": "Every contact on file is suppressed or marked wrong party."}
        return {"key": "add_contact", "label": "Add contact"}
    return {"key": "promote", "label": "Review & promote", "tone": "good"}
