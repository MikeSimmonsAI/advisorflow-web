"""DEAL ROOM MANAGEMENT - the owner's operating views (#415 follow-up).

Read models over the records the Deal Room already keeps - nothing here is a
new store:

    overview     real counts for the management dashboard
    investors    registered investors with their login, buyer record, entities,
                 representatives, Buyer Intelligence assessment and activity
    candidates   Wholesale deals and whether each can be added (the exact
                 package step still missing when it cannot)
    preview      the investor card exactly as the whitelist builds it
    documents    investor files and e-sign envelopes in one place
    activity     the Deal Room's audit trail (wholesale_events, deal_room.*)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.models.models import User
from app.models.wholesale_buyer_engine_models import WholesaleBuyerConversation, WholesaleBuyerEntity
from app.models.wholesale_investor_models import (DealListing, ExclusiveHold, InvestorAccount, InvestorEntityRole,
                                                  InvestorInvite)
from app.models.wholesale_models import (WholesaleBuyer, WholesaleBuyerOutreach, WholesaleDeal, WholesaleEvent,
                                         WholesaleFile, WholesaleProperty)
from app.services import wholesale_deal_room as DR

PORTAL_KINDS = ("concierge", "relationship", "deal")


def _iso(v):
    return v.isoformat() + "Z" if isinstance(v, datetime) else None


def overview(db, org_id: str, include_test: bool = False) -> Dict[str, Any]:
    from app.services import wholesale_deal_room_setup as SET
    DR.sweep(db, org_id)

    def t(q, model):
        return q if include_test else q.filter(model.is_test.is_(False))
    now = datetime.utcnow()
    accounts = t(db.query(InvestorAccount).filter(InvestorAccount.organization_id == org_id), InvestorAccount)
    invites = t(db.query(InvestorInvite).filter(InvestorInvite.organization_id == org_id,
                                                InvestorInvite.status == "pending"), InvestorInvite)
    listings = t(db.query(DealListing).filter(DealListing.organization_id == org_id), DealListing)
    holds = t(db.query(ExclusiveHold).filter(ExclusiveHold.organization_id == org_id), ExclusiveHold)
    convs = t(db.query(WholesaleBuyerConversation).filter(WholesaleBuyerConversation.organization_id == org_id,
                                                          WholesaleBuyerConversation.kind.in_(PORTAL_KINDS)),
              WholesaleBuyerConversation)
    files = (db.query(WholesaleFile).filter(WholesaleFile.organization_id == org_id,
                                            WholesaleFile.uploaded_by_investor_id.isnot(None),
                                            WholesaleFile.review_status == "pending"))
    active_ids = [lst.deal_id for lst in listings.filter(DealListing.state.in_(("published", "exclusive_review"))).all()]
    offers = 0
    if active_ids:
        offers = (db.query(WholesaleBuyerOutreach).filter(WholesaleBuyerOutreach.organization_id == org_id,
                                                          WholesaleBuyerOutreach.deal_id.in_(active_ids),
                                                          WholesaleBuyerOutreach.status.in_(("interested", "offer_submitted")))
                  .count())
    v = SET.view(db, org_id)
    return {
        "status": v["status"], "name": v["settings"].get("name"), "saved": v["saved"],
        "activated_at": v["activated_at"],
        "counts": {
            "investors": accounts.count(),
            "invites_pending": invites.count(),
            "listings_live": len(active_ids),
            "listings_total": listings.count(),
            "holds_pending": holds.filter(ExclusiveHold.status == "requested").count(),
            "holds_active": holds.filter(ExclusiveHold.status == "active").count(),
            "holds_expiring": holds.filter(ExclusiveHold.status == "active",
                                           ExclusiveHold.ends_at <= now + timedelta(hours=24)).count(),
            "messages_unread": sum(c.staff_unread or 0 for c in convs.all()),
            "handoffs": convs.filter(WholesaleBuyerConversation.status == "handed_off").count(),
            "files_to_review": files.count() if include_test else files.filter(WholesaleFile.buyer_id.in_(
                db.query(WholesaleBuyer.id).filter(WholesaleBuyer.organization_id == org_id,
                                                   WholesaleBuyer.is_test.is_(False)))).count(),
            "interest": offers,
        },
        "portal_url": "/investors",
    }


def investors(db, org_id: str, include_test: bool = False, q: Optional[str] = None) -> List[Dict[str, Any]]:
    from app.services import wholesale_buyer_engine as ENG
    rows = (db.query(InvestorAccount, User, WholesaleBuyer)
            .join(User, User.id == InvestorAccount.user_id)
            .join(WholesaleBuyer, WholesaleBuyer.id == InvestorAccount.buyer_id)
            .filter(InvestorAccount.organization_id == org_id).order_by(InvestorAccount.created_at.desc()).all())
    out = []
    needle = (q or "").strip().lower()
    for a, u, b in rows:
        if a.is_test and not include_test:
            continue
        name = a.preferred_name or a.legal_name or u.full_name
        if needle and needle not in " ".join(x for x in (name, a.legal_name, a.company_legal_name, u.email,
                                                           b.company_name) if x).lower():
            continue
        ents = (db.query(InvestorEntityRole, WholesaleBuyerEntity)
                .join(WholesaleBuyerEntity, WholesaleBuyerEntity.id == InvestorEntityRole.entity_id)
                .filter(InvestorEntityRole.account_id == a.id).all())
        reps = [x for x in DR.accounts_for_buyer(db, org_id, b.id) if x["id"] != a.id]
        try:
            d = ENG.detail(db, org_id, b.id)
            asmt = d["assessment"]
            intel = {"headline": asmt.get("headline_label") or asmt.get("headline"),
                     "readiness": asmt["readiness"]["label"], "enrichment": asmt["enrichment"]["percent"],
                     "buy_box": asmt["buy_box"]["label"], "contact": asmt["contact"]["label"],
                     "funding_documented": bool(asmt["readiness"]["funding"]["documented"])}
        except Exception:  # noqa: BLE001 - shown as unavailable, never fatal
            intel = None
        convs = db.query(WholesaleBuyerConversation).filter(WholesaleBuyerConversation.account_id == a.id).all()
        last = max([c.last_message_at for c in convs if c.last_message_at] or [None], key=lambda x: x or datetime.min)
        interest = (db.query(WholesaleBuyerOutreach).filter(WholesaleBuyerOutreach.organization_id == org_id,
                                                            WholesaleBuyerOutreach.buyer_id == b.id,
                                                            WholesaleBuyerOutreach.channel == "deal_room").count())
        out.append({
            "account_id": a.id, "buyer_id": b.id, "name": name, "legal_name": a.legal_name, "email": u.email,
            "phone": a.phone, "kind": a.kind, "company": a.company_legal_name, "entity_type": a.entity_type,
            "position": a.position, "status": a.status, "identity_complete": bool(a.identity_completed_at),
            "email_verified": bool(a.email_verified_at), "joined_at": _iso(a.created_at),
            "entities": [{"name": e.legal_name, "status": e.status, "position": r.position, "authority": r.authority}
                         for r, e in ents],
            "representatives": [{"name": x["name"], "email": x["email"], "status": x["status"]} for x in reps],
            "intel": intel, "unread": sum(c.staff_unread or 0 for c in convs), "threads": len(convs),
            "last_message_at": _iso(last), "responses": interest,
            "assigned_user_id": a.assigned_user_id, "is_test": bool(a.is_test),
        })
    return out


def _readiness(st: Dict[str, Any], lst: Optional[DealListing]) -> Dict[str, Any]:
    """Package status -> what the owner can do next (the exact missing step)."""
    key = st["status"]
    if lst is not None:
        return {"ready": False, "already_listed": True, "label": "Already in the Deal Room (%s)" % lst.state,
                "steps": []}
    if st.get("live"):
        return {"ready": True, "already_listed": False, "label": "Ready to publish", "steps": []}
    steps = []
    if key == "none":
        steps.append("Create the investor package on the deal (Disposition > Investor package)")
    if st.get("missing"):
        steps.append("Fill in: %s" % ", ".join(st["missing"]))
    if key in ("none", "draft", "changed"):
        steps.append("Send the investor package for approval")
    if key == "review":
        steps.append("Waiting for an owner or manager to approve the investor package")
    if key == "approved":
        steps.append("Publish the approved investor package (owner or manager)")
    if key == "withdrawn":
        steps.append("The package was withdrawn - publish it again after review")
    if key == "closed":
        steps.append("This deal is closed")
    steps += ["Recommended: %s" % r for r in st.get("recommended") or []]
    return {"ready": False, "already_listed": False, "label": st.get("label") or key, "steps": steps}


def candidates(db, org_id: str, q: Optional[str] = None, include_test: bool = False,
               limit: int = 60) -> List[Dict[str, Any]]:
    from app.services import wholesale_deal_package as PKG
    query = (db.query(WholesaleDeal, WholesaleProperty)
             .join(WholesaleProperty, WholesaleProperty.id == WholesaleDeal.property_id)
             .filter(WholesaleDeal.organization_id == org_id, WholesaleDeal.closed_at.is_(None),
                     WholesaleDeal.stage.notin_(("dead", "closed"))))
    if not include_test:
        query = query.filter(WholesaleDeal.is_test.is_(False))
    needle = (q or "").strip()
    if needle:
        like = "%%%s%%" % needle.lower()
        from sqlalchemy import func, or_
        query = query.filter(or_(func.lower(WholesaleProperty.street_address).like(like),
                                 func.lower(WholesaleProperty.city).like(like),
                                 WholesaleProperty.zip_code.like("%%%s%%" % needle)))
    rows = query.order_by(WholesaleDeal.buyer_room_published.desc(), WholesaleDeal.updated_at.desc()).limit(limit).all()
    listed = {x.deal_id: x for x in db.query(DealListing).filter(DealListing.organization_id == org_id).all()}
    out = []
    for d, p in rows:
        try:
            st = PKG.status(db, org_id, d)
        except Exception:  # noqa: BLE001
            st = {"status": "none", "label": "Package status unavailable", "missing": [], "recommended": []}
        r = _readiness(st, listed.get(d.id))
        out.append({"deal_id": d.id, "address": ", ".join(x for x in (p.street_address, p.city, p.zip_code) if x),
                    "county": p.county, "property_type": p.property_category or p.property_type,
                    "units": p.unit_count, "stage": d.stage, "package": st.get("status"),
                    "package_label": st.get("label"),
                    "asking_price": float(d.buyer_room_asking_price) if d.buyer_room_asking_price is not None else None,
                    "is_test": bool(d.is_test), **r,
                    "listing_id": getattr(listed.get(d.id), "id", None)})
    out.sort(key=lambda x: (not x["ready"], x["already_listed"]))
    return out


def preview(db, org_id: str, lst: DealListing) -> Dict[str, Any]:
    """The investor's card and detail as the publication whitelist builds them
    (no match score - that is per investor)."""
    from app.services import wholesale_publication as P
    deal = db.query(WholesaleDeal).filter(WholesaleDeal.id == lst.deal_id).first()
    pay = P.buyer_room_payload(db, deal)
    pay.pop("deal_id", None)
    hidden = set(DR._j(lst.hidden_document_ids, []))
    docs = pay.get("documents") or []
    pay["documents"] = [d for d in docs if d.get("id") not in hidden]
    if P.leaked_keys(pay, P.FORBIDDEN_TO_BUYER):
        raise HTTPException(status_code=500, detail="Withheld: publication boundary check failed.")
    prop = db.query(WholesaleProperty).filter(WholesaleProperty.id == deal.property_id).first()
    return {"listing_id": lst.id, "state": lst.state, "room": pay, "serving": DR._serving(db, org_id, deal),
            "all_documents": [{"id": d.get("id"), "title": d.get("title"), "visible": d.get("id") not in hidden}
                              for d in docs],
            "category": getattr(prop, "property_category", None) or getattr(prop, "property_type", None),
            "facts": {k: getattr(prop, k, None) for k in ("bedrooms", "bathrooms", "square_feet", "year_built",
                                                          "unit_count")} if prop else {},
            "notice": "This is what an investor sees. Seller details, contract price and internal notes are never "
                      "included."}


def set_hidden_documents(db, org_id: str, lst: DealListing, visible_ids: List[str]) -> DealListing:
    pv = preview(db, org_id, lst)
    published = [d["id"] for d in pv["all_documents"]]
    keep = set(visible_ids or [])
    lst.hidden_document_ids = json.dumps([i for i in published if i not in keep])
    db.flush()
    return lst


def documents(db, org_id: str, include_test: bool = False) -> Dict[str, Any]:
    from app.models.esign_models import EsignEnvelope, EsignSigner
    files = (db.query(WholesaleFile, WholesaleBuyer).join(WholesaleBuyer, WholesaleBuyer.id == WholesaleFile.buyer_id)
             .filter(WholesaleFile.organization_id == org_id, WholesaleFile.buyer_id.isnot(None))
             .order_by(WholesaleFile.created_at.desc()).limit(300).all())
    out_files = []
    for f, b in files:
        if b.is_test and not include_test:
            continue
        out_files.append({**DR.file_json(f), "buyer_id": b.id, "buyer_name": b.company_name or b.contact_name})
    accts = db.query(InvestorAccount, User).join(User, User.id == InvestorAccount.user_id).filter(
        InvestorAccount.organization_id == org_id).all()
    emails = {u.email.lower(): a for a, u in accts if u.email and (include_test or not a.is_test)}
    sigs = []
    if emails:
        for s, e in (db.query(EsignSigner, EsignEnvelope).join(EsignEnvelope, EsignEnvelope.id == EsignSigner.envelope_id)
                     .filter(EsignSigner.organization_id == org_id, EsignSigner.email.in_(list(emails))).all()):
            signers = db.query(EsignSigner).filter(EsignSigner.envelope_id == e.id).order_by(EsignSigner.sign_order).all()
            sigs.append({"envelope_id": e.id, "title": e.title, "status": e.status, "deal_id": e.deal_id,
                         "investor": emails[s.email.lower()].legal_name or s.email,
                         "parties": [{"role": x.role, "status": x.status, "signed_at": _iso(x.signed_at)} for x in signers],
                         "completed": e.status == "completed", "sent_at": _iso(e.created_at),
                         "completed_at": _iso(getattr(e, "completed_at", None))})
    return {"files": out_files, "signatures": sigs}


def activity(db, org_id: str, limit: int = 80) -> List[Dict[str, Any]]:
    rows = (db.query(WholesaleEvent).filter(WholesaleEvent.organization_id == org_id,
                                            WholesaleEvent.action.like("deal_room.%"))
            .order_by(WholesaleEvent.created_at.desc()).limit(limit).all())
    names = {}
    out = []
    for e in rows:
        who = e.actor_label
        if e.actor_user_id:
            if e.actor_user_id not in names:
                u = db.query(User).filter(User.id == e.actor_user_id).first()
                names[e.actor_user_id] = getattr(u, "full_name", None) or getattr(u, "email", None)
            who = names[e.actor_user_id]
        out.append({"at": _iso(e.created_at), "action": e.action.split(".", 1)[-1], "summary": e.summary,
                    "by": who or "Deal Room", "deal_id": e.deal_id})
    return out


def assign_thread(db, org_id: str, conv_id: str, user_id: Optional[str], by) -> WholesaleBuyerConversation:
    from app.services import wholesale_deal_room_chat as CH
    from app.services import wholesale_deal_room_setup as SET
    c = CH.staff_thread(db, org_id, conv_id)
    if user_id:
        if user_id not in {p["user_id"] for p in SET.staff_people(db, org_id)}:
            raise HTTPException(status_code=400, detail="Assign to someone on this team.")
    c.assigned_user_id = user_id or None
    db.flush()
    u = db.query(User).filter(User.id == user_id).first() if user_id else None
    DR.log(db, org_id, "deal_room.thread_assigned", "Conversation assigned to %s"
           % (getattr(u, "full_name", None) or "nobody"), by)
    if user_id:
        DR.notify_staff(db, org_id, "A Deal Room conversation was assigned to you",
                        "/wholesale/deal-room?view=messages&thread=%s" % c.id, assignee_id=user_id)
    return c
