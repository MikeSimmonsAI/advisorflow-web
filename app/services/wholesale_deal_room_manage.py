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
        "markets": v["settings"].get("markets") or [],
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


# ── owner dashboard (the approved full-page composition) ───────────────────

def _first_name(user) -> Optional[str]:
    n = (getattr(user, "full_name", None) or "").strip()
    return n.split(" ")[0] if n else None


def _listing_card(db, org_id: str, lst: DealListing) -> Dict[str, Any]:
    from app.services import wholesale_publication as P
    deal = db.query(WholesaleDeal).filter(WholesaleDeal.id == lst.deal_id).first()
    prop = db.query(WholesaleProperty).filter(WholesaleProperty.id == deal.property_id).first() if deal else None
    photos = P.buyer_photos(db, deal) if deal is not None else []
    hold = DR.active_hold(db, lst.id)
    pend = db.query(ExclusiveHold).filter(ExclusiveHold.listing_id == lst.id, ExclusiveHold.status == "requested").count()
    return {"id": lst.id, "deal_id": lst.deal_id, "state": lst.state, "audience": lst.audience,
            "min_match_score": lst.min_match_score, "published_at": _iso(lst.published_at), "expires_at": _iso(lst.expires_at),
            "address": getattr(prop, "street_address", None), "city": getattr(prop, "city", None),
            "state_code": getattr(prop, "state", None), "zip_code": getattr(prop, "zip_code", None),
            "county": getattr(prop, "county", None),
            "category": getattr(prop, "property_category", None) or getattr(prop, "property_type", None),
            "facts": {k: (float(getattr(prop, k)) if k in ("bedrooms", "bathrooms") and getattr(prop, k, None) is not None
                          else getattr(prop, k, None))
                      for k in ("bedrooms", "bathrooms", "square_feet", "unit_count")} if prop else {},
            "asking_price": float(deal.buyer_room_asking_price) if deal is not None and deal.buyer_room_asking_price is not None else None,
            "cover_photo": photos[0]["id"] if photos else None,
            "serving": DR._serving(db, org_id, deal) if deal is not None else False,
            "hold": DR.hold_json(db, hold) if hold is not None else None, "hold_requests": pend,
            "is_test": bool(lst.is_test)}


def dashboard(db, org_id: str, user, include_test: bool = False) -> Dict[str, Any]:
    """Everything the owner's Deal Room dashboard shows, from real records only."""
    from app.services import ai_gateway
    from app.services import wholesale_deal_room_setup as SET
    DR.sweep(db, org_id)
    now = datetime.utcnow()

    def t(q, model):
        return q if include_test else q.filter(model.is_test.is_(False))
    accts = t(db.query(InvestorAccount).filter(InvestorAccount.organization_id == org_id), InvestorAccount).all()
    active = [a for a in accts if a.status == "active" and a.identity_completed_at]
    lists = t(db.query(DealListing).filter(DealListing.organization_id == org_id), DealListing).all()
    live = [x for x in lists if x.state in ("published", "exclusive_review")]
    holds = t(db.query(ExclusiveHold).filter(ExclusiveHold.organization_id == org_id), ExclusiveHold)
    active_holds = holds.filter(ExclusiveHold.status == "active").all()
    expiring = [h for h in active_holds if h.ends_at and h.ends_at <= now + timedelta(hours=24)]
    pending = holds.filter(ExclusiveHold.status == "requested").count()
    # investor growth: registrations per month (cumulative), last 6 months - real rows only
    months = []
    y, m = now.year, now.month
    for _ in range(6):
        months.insert(0, (y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    series = []
    for (yy, mm) in months:
        end = datetime(yy + (mm == 12), (mm % 12) + 1, 1)
        series.append({"month": "%04d-%02d" % (yy, mm), "label": datetime(yy, mm, 1).strftime("%b"),
                       "total": sum(1 for a in accts if a.created_at and a.created_at < end),
                       "new": sum(1 for a in accts if a.created_at and a.created_at.year == yy and a.created_at.month == mm)})
    growth_ok = sum(1 for s in series if s["new"]) >= 2
    # deal room status - each line checked against the real setting
    v = SET.view(db, org_id, user)
    rd = SET.readiness(db, org_id, user)
    s = v["settings"]
    key = bool(__import__("os").environ.get("OPENAI_API_KEY"))
    ai_on = bool(s.get("ai_concierge_enabled"))
    manual = ai_gateway.manual_enabled()
    status = [
        {"key": "config", "label": "Room configuration",
         "state": "ok" if v["saved"] and not rd["blockers"] else "warn",
         "value": "Complete" if v["saved"] and not rd["blockers"] else
         ("Not saved" if not v["saved"] else "%d item(s) missing" % len(rd["blockers"]))},
        {"key": "portal", "label": "Investor portal", "state": "ok" if v["status"] == "active" else "warn",
         "value": {"active": "Live", "paused": "Paused"}.get(v["status"], "Not activated")},
        {"key": "publishing", "label": "Deal publishing", "state": "ok" if live else "off",
         "value": ("%d live" % len(live)) if live else "No deals published"},
        {"key": "ai", "label": "AI concierge",
         "state": "ok" if ai_on and manual and key else ("warn" if ai_on else "off"),
         "value": "Enabled" if ai_on and manual and key else
         ("Automatic replies (AI key not configured)" if ai_on and manual else
          ("Automatic replies (AI switched off platform-wide)" if ai_on else "Off"))},
        {"key": "esign", "label": "Document signing", "state": "ok", "value": "In-app e-sign (approved templates)"},
        {"key": "scan", "label": "Upload scanning", "state": "warn",
         "value": "Built-in checks only (no antivirus engine)"},
    ]
    issues = [i["label"] + ": " + i["detail"] for i in rd["items"] if not i["ok"] and i["blocking"]]
    hero = next((c for c in (_listing_card(db, org_id, x) for x in live) if c["cover_photo"]), None)
    return {
        "owner_first_name": _first_name(user), "room_name": s.get("name"), "status": v["status"],
        "markets": s.get("markets") or [],
        "metrics": {"total_investors": len(accts), "active_investors": len(active),
                    "new_investors_30d": sum(1 for a in accts if a.created_at and a.created_at >= now - timedelta(days=30)),
                    "active_listings": len(live), "listings_total": len(lists),
                    "exclusive_reviews": len(active_holds), "expiring": len(expiring), "pending_reviews": pending},
        "growth": {"series": series, "enough": growth_ok},
        "status_items": status, "issues": issues,
        "hero_photo": {"listing_id": hero["id"], "file_id": hero["cover_photo"]} if hero else None,
        "listings": [_listing_card(db, org_id, x) for x in sorted(live, key=lambda z: z.published_at or now, reverse=True)][:8],
    }


def feed(db, org_id: str, kind: str = "activity", include_test: bool = False, limit: int = 12) -> List[Dict[str, Any]]:
    """Recent Investor Activity rows: investor, company, activity, related deal, time, status, and what to open."""
    from app.services import wholesale_deal_room_chat as CH

    def investor_of(buyer_id):
        b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == buyer_id).first() if buyer_id else None
        a = (db.query(InvestorAccount).filter(InvestorAccount.buyer_id == buyer_id).first() if buyer_id else None)
        name = (a.preferred_name or a.legal_name) if a and (a.preferred_name or a.legal_name) else \
            (getattr(b, "contact_name", None) or getattr(b, "company_name", None))
        company = (a.company_legal_name if a else None) or getattr(b, "company_name", None)
        return name, company if company != name else None, bool(getattr(b, "is_test", False))

    def deal_of(deal_id):
        if not deal_id:
            return None
        lst = db.query(DealListing).filter(DealListing.deal_id == deal_id).first()
        d = db.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id).first()
        p = db.query(WholesaleProperty).filter(WholesaleProperty.id == d.property_id).first() if d else None
        photo = None
        if d is not None:
            from app.services import wholesale_publication as P
            ph = P.buyer_photos(db, d)
            photo = ph[0]["id"] if ph else None
        return {"deal_id": deal_id, "listing_id": getattr(lst, "id", None), "address": getattr(p, "street_address", None),
                "place": ", ".join(x for x in (getattr(p, "city", None), getattr(p, "state", None)) if x), "photo": photo}

    rows: List[Dict[str, Any]] = []
    if kind == "holds":
        for h in (db.query(ExclusiveHold).filter(ExclusiveHold.organization_id == org_id)
                  .order_by(ExclusiveHold.requested_at.desc()).limit(limit * 2).all()):
            if h.is_test and not include_test:
                continue
            n, c, _t = investor_of(h.buyer_id)
            rows.append({"investor": n, "company": c, "activity": "Requested exclusive review" + (
                (" - " + h.request_note[:60]) if h.request_note else ""), "deal": deal_of(h.deal_id),
                "at": _iso(h.requested_at), "status": {"requested": "Pending", "active": "Active", "declined": "Declined",
                                                       "ended": "Ended"}.get(h.status, h.status),
                "tone": {"requested": "warn", "active": "ok"}.get(h.status, "muted"), "open": {"view": "holds"}})
    elif kind == "signups":
        for a in (db.query(InvestorAccount).filter(InvestorAccount.organization_id == org_id)
                  .order_by(InvestorAccount.created_at.desc()).limit(limit).all()):
            if a.is_test and not include_test:
                continue
            rows.append({"investor": a.preferred_name or a.legal_name or "Investor", "company": a.company_legal_name,
                         "activity": "Joined the Deal Room" + (" - identity complete" if a.identity_completed_at else ""),
                         "deal": None, "at": _iso(a.created_at),
                         "status": "Active" if a.status == "active" else a.status.capitalize(),
                         "tone": "ok" if a.status == "active" else "muted",
                         "open": {"view": "investors", "buyer_id": a.buyer_id}})
    elif kind == "messages":
        for t in CH.inbox(db, org_id, include_test=include_test)[:limit]:
            n, c, _t = investor_of(t["buyer_id"])
            rows.append({"investor": n or t["buyer_name"], "company": c, "activity": (t["last"] or "")[:90],
                         "deal": deal_of(t["deal_id"]), "at": t["last_message_at"],
                         "status": "Asked for a person" if t["status"] == "handed_off" else
                         ("%d unread" % t["unread"] if t["unread"] else ("Concierge" if t["ai_enabled"] else "Team")),
                         "tone": "warn" if t["status"] == "handed_off" or t["unread"] else "muted",
                         "open": {"view": "messages", "thread": t["id"]}})
    else:
        evs = (db.query(WholesaleEvent).filter(WholesaleEvent.organization_id == org_id,
                                               WholesaleEvent.action.like("deal_room.%"))
               .order_by(WholesaleEvent.created_at.desc()).limit(limit * 4).all())
        status_of = {"interested": ("Interested", "ok"), "offer": ("New offer", "ok"), "pass": ("Passed", "muted"),
                     "hold_requested": ("Pending", "warn"), "hold_approve": ("Approved", "ok"),
                     "hold_expired": ("Ended", "muted"), "file_uploaded": ("To review", "warn"),
                     "invite_accepted": ("Joined", "ok"), "document_downloaded": ("Viewed", "muted"),
                     "listing_publish": ("Published", "ok")}
        for e in evs:
            try:
                det = json.loads(e.details) if e.details else {}
            except (TypeError, ValueError):
                det = {}
            act = e.action.split(".", 1)[-1]
            bid = det.get("buyer_id")
            if not bid and det.get("account_id"):
                a = db.query(InvestorAccount).filter(InvestorAccount.id == det["account_id"]).first()
                bid = getattr(a, "buyer_id", None)
            if not bid and act not in ("listing_publish", "invite_accepted"):
                continue
            n, c, is_test = investor_of(bid) if bid else (None, None, False)
            if is_test and not include_test:
                continue
            st, tone = status_of.get(act, (act.replace("_", " ").capitalize(), "muted"))
            rows.append({"investor": n or "Your team", "company": c if n else "Deal Room", "activity": e.summary, "deal": deal_of(e.deal_id),
                         "at": _iso(e.created_at), "status": st, "tone": tone,
                         "open": {"view": "holds"} if act.startswith("hold") else
                         {"view": "documents"} if act == "file_uploaded" else
                         {"view": "listings"} if e.deal_id else {"view": "investors", "buyer_id": bid}})
            if len(rows) >= limit:
                break
    return rows[:limit]
