# -*- coding: utf-8 -*-
"""THE PUBLICATION BOUNDARY.

One module decides what leaves this workspace, for whom, and it does it by
WHITELIST. Every payload below is built field by field from an explicit list.
Nothing is copied wholesale from a deal row and then trimmed, because the day
somebody adds a column to `wholesale_deals` is the day a trim-based approach
starts leaking it.

THE THREE AUDIENCES AND WHY THEY CANNOT SHARE A SERIALIZER
----------------------------------------------------------
    operator  sees everything, inside the product, behind a login
    investor  sees a property and its numbers AS PUBLISHED, and their own reply
    seller    sees their own transaction, and nothing about the disposition

An investor must never learn what the seller accepted, what the seller said, or
what this workspace makes on the deal. A seller must never learn who the buyers
are, what they offered, or what the assignment fee is. Those are not UI
preferences. They are the business.

WHY A FIELD IS ABSENT RATHER THAN NULL
--------------------------------------
When a deal has not published its ARV, the buyer payload has no `arv` key at
all. A `null` invites a client to render "ARV: —", which tells an investor that
an ARV exists and is being withheld. Absent means absent.

NOTHING HERE READS A REQUEST OR A USER. These functions take a deal and return
a dict; the routers own authentication. That is deliberate — it is what lets the
security tests call them directly and assert on the keys.
"""
import json
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.wholesale_models import (WholesaleComp, WholesaleDocument,
                                         WholesaleFile, WholesaleProperty)

log = logging.getLogger(__name__)


# ── Keys that must NEVER appear in an external payload ──────────────────────
#
# This is belt and braces beside the whitelists, and it exists so a security
# test has one authoritative list to assert against rather than re-deriving the
# rule from the serializer it is supposed to be checking.
FORBIDDEN_TO_BUYER = (
    "seller", "seller_profile", "seller_name", "seller_phone", "seller_email",
    "qualification_score", "qualification_band", "qualification_reasons",
    "motivation", "seller_motivation", "ai_summary", "ai_intent",
    "communications", "conversation", "replies",
    "max_allowable_offer", "mao", "proposed_offer", "contract_price",
    "assignment_fee", "estimated_spread", "buyer_price",
    "wholesale_fee_collected", "desired_wholesale_fee", "other_costs",
    "offers", "negotiation", "approvals", "approval_history",
    "events", "audit", "audit_history", "analysis_notes", "repair_notes",
    "notes", "buyer_matches", "buyer_outreach", "buyers", "matches",
    "internal_notes", "next_action", "is_test", "organization_id",
)

FORBIDDEN_TO_SELLER = (
    "buyers", "buyer_matches", "buyer_outreach", "matches", "buyer_offers",
    "assigned_buyer_id", "buyer_price", "assignment_fee", "estimated_spread",
    "max_allowable_offer", "mao", "arv", "repair_estimate",
    "wholesale_fee_collected", "desired_wholesale_fee", "other_costs",
    "qualification_score", "qualification_band", "qualification_reasons",
    "ai_summary", "ai_intent", "approvals", "approval_history",
    "offers", "negotiation", "events", "audit", "audit_history",
    "analysis_notes", "repair_notes", "notes", "internal_notes",
    "next_action", "is_test", "organization_id",
)


def _money(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except Exception:
        return None


def _num(value) -> Optional[float]:
    return _money(value)


def _date(value) -> Optional[str]:
    return value.isoformat() if value else None


def branding(db: Session, deal) -> Dict[str, Any]:
    """Whose page this is, for a stranger reading it on their phone.

    An external page with no name on it looks like a phishing attempt, and an
    external page carrying the SOFTWARE VENDOR's name is worse: it tells the
    investor who the wholesaler buys their tools from, which is nobody's
    business and is not who is selling them a house.

    So the chain is the operator's own identity first and the platform brand
    only as a fallback, and every step of it degrades gracefully:

        name    organization.brand_name -> organization.name -> None
        logo    organization.brand_logo_url -> platform logo_url -> None
        accent  organization.brand_color_primary -> platform accent -> None

    A missing value is returned as None and the page renders without it. The
    one thing this must never do is substitute a brand that does exist for one
    that does not — the same rule `brand_config.config_for_host` states about
    an unrecognised hostname.

    NOTHING HERE IS TENANT-SENSITIVE. It is the operator's own public-facing
    name and mark, which is exactly what they are trying to put in front of an
    investor; it carries no seller, no buyer and no deal economics.
    """
    from app.models.models import Organization

    org = (db.query(Organization)
           .filter(Organization.id == deal.organization_id).first())
    if org is None:
        return {"name": None, "logo_url": None, "accent": None,
                "support_email": None, "support_phone": None, "website": None}

    platform = {}
    if getattr(org, "platform_id", None):
        try:
            from app.services import brand_config
            from app.models.models import Platform
            row = (db.query(Platform)
                   .filter(Platform.id == org.platform_id).first())
            if row is not None and row.slug:
                platform = brand_config.config_for_slug(db, row.slug) or {}
        except Exception:                                    # noqa: BLE001
            log.exception("wholesale_publication: brand lookup failed for org %s",
                          org.id)
            platform = {}

    return {
        "name": (getattr(org, "brand_name", None) or org.name or None),
        "logo_url": (getattr(org, "brand_logo_url", None)
                     or platform.get("logo_url") or None),
        "accent": (getattr(org, "brand_color_primary", None)
                   or platform.get("accent_color") or None),
        # Contact of last resort, so a page is never a dead end. The seller
        # page prefers the named person the operator put on the deal; this is
        # only the fallback underneath it.
        "support_email": platform.get("support_email") or None,
        # PHASE 7.3: NO PLATFORM PHONE ON A WHOLESALE PAGE. The platform's
        # support line is the software vendor's number (for EvoSys Pro it is
        # the line another business answers), not the wholesaler's. An
        # investor or seller who calls it reaches the wrong company. There is
        # no wholesale-specific public phone configured anywhere yet, so the
        # honest value is none: the page shows no phone rather than a wrong
        # one. The seller page still shows the named contact the operator put
        # on the deal (seller_room_contact_*), which is the right number.
        "support_phone": None,
        "website": platform.get("website_url") or None,
    }


def _address(prop) -> Dict[str, Any]:
    """The physical property. Every field here is on a public listing already."""
    if prop is None:
        return {}
    line = prop.street_address or ""
    if prop.unit:
        line = "%s %s" % (line, prop.unit)
    return {
        "line1": line.strip() or None,
        "city": prop.city,
        "state": prop.state,
        "zip_code": prop.zip_code,
        "county": prop.county,
        "property_type": prop.property_type,
        "bedrooms": _num(prop.bedrooms),
        "bathrooms": _num(prop.bathrooms),
        "square_feet": prop.square_feet,
        "lot_size_sqft": prop.lot_size_sqft,
        "year_built": prop.year_built,
        # Phase 6. Two more facts an investor decides on before they read
        # anything else, and neither is private: whether they can get into it,
        # and which market it is in. Occupancy in particular changes the deal
        # completely — a tenanted house is a different purchase from a vacant
        # one — and leaving it off meant every investor had to ask.
        "occupancy_status": prop.occupancy_status,
        "market": prop.market,
    }


def _photos(db: Session, deal, field: str) -> List[Dict[str, Any]]:
    """Only the photos a person ticked for THIS audience.

    `field` is the column name, so the two audiences cannot accidentally share
    a filter: asking for seller photos reads `seller_visible` and can never
    fall back to `buyer_visible` because there is no fallback to fall to.

    Cover first, then the operator's own order. The investor page leads with
    whatever comes back first, so the order here IS the presentation.
    """
    if not deal.property_id:
        return []
    column = getattr(WholesaleFile, field)
    rows = (db.query(WholesaleFile)
            .filter(WholesaleFile.organization_id == deal.organization_id,
                    WholesaleFile.property_id == deal.property_id,
                    WholesaleFile.kind == "property_photo",
                    column.is_(True))
            .order_by(WholesaleFile.is_primary.desc(),
                      WholesaleFile.sort_order.asc(),
                      WholesaleFile.created_at.asc())
            .all())
    return [{
        "id": f.id,
        "category": f.category,
        "caption": f.caption,
        "is_cover": bool(f.is_primary),
        "content_type": f.content_type,
    } for f in rows]


def buyer_photos(db: Session, deal) -> List[Dict[str, Any]]:
    """Only the photos a person ticked for investors. Never the whole gallery."""
    return _photos(db, deal, "buyer_visible")


def seller_photos(db: Session, deal) -> List[Dict[str, Any]]:
    """Only the photos a person ticked for the owner. Never the whole gallery.

    Usually one — the front elevation — because the owner knows what their own
    house looks like. It is here so their page has a face rather than being a
    wall of status text about the most valuable thing they own.
    """
    return _photos(db, deal, "seller_visible")


def _published_documents(db: Session, deal, field: str) -> List[Dict[str, Any]]:
    column = getattr(WholesaleDocument, field)
    rows = (db.query(WholesaleDocument)
            .filter(WholesaleDocument.organization_id == deal.organization_id,
                    WholesaleDocument.deal_id == deal.id,
                    column.is_(True))
            .order_by(WholesaleDocument.created_at.asc())
            .all())
    out = []
    for d in rows:
        out.append({
            "id": d.id,
            "title": d.title or d.file_name or d.doc_type,
            "doc_type": d.doc_type,
            "status": d.status,
            "signature_status": d.signature_status,
            # A document row can exist with no file behind it. Say which, so
            # nobody is offered a download that cannot happen.
            "has_file": bool(d.file_id),
            "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
        })
    return out


def _published_comps(db: Session, deal) -> List[Dict[str, Any]]:
    rows = (db.query(WholesaleComp)
            .filter(WholesaleComp.organization_id == deal.organization_id,
                    WholesaleComp.deal_id == deal.id,
                    WholesaleComp.included.is_(True))
            .order_by(WholesaleComp.sale_date.desc())
            .all())
    out = []
    for c in rows:
        price = _money(c.sale_price)
        sqft = c.square_feet or None
        out.append({
            "address": c.street_address,
            "city": c.city,
            "state": c.state,
            "sale_date": _date(c.sale_date),
            "sale_price": price,
            "square_feet": sqft,
            "price_per_sqft": round(price / sqft, 2) if price and sqft else None,
            "bedrooms": _num(c.bedrooms),
            "bathrooms": _num(c.bathrooms),
            "distance_miles": _num(getattr(c, "distance_miles", None)),
        })
    return out


# ── The investor's room ─────────────────────────────────────────────────────

def buyer_room_payload(db: Session, deal, link=None,
                       outreach=None) -> Dict[str, Any]:
    """Everything an investor may see about this deal, and nothing else.

    `link` and `outreach` are optional so a test can build the payload for a
    deal with no link at all and still assert the boundary.
    """
    prop = (db.query(WholesaleProperty)
            .filter(WholesaleProperty.id == deal.property_id).first()
            if deal.property_id else None)

    payload: Dict[str, Any] = {
        "deal_id": deal.id,
        "published": bool(deal.buyer_room_published),
        # Whose page this is. See `branding` — the operator's own identity,
        # never the software vendor's.
        "brand": branding(db, deal),
        "property": _address(prop),
        "photos": buyer_photos(db, deal),
        "documents": _published_documents(db, deal, "buyer_visible"),
        # Written by a person, for investors. Never the internal notes.
        "summary": deal.buyer_room_summary,
        "condition_summary": deal.buyer_room_condition,
        # The number we are asking. Its own column — see the model comment.
        "asking_price": _money(deal.buyer_room_asking_price),
        "closing": {
            "closing_date": _date(deal.closing_date),
            "target_close": _date(deal.close_of_escrow_target),
            "title_company": deal.title_company,
        },
    }

    # Our workings, published only if a person switched them on.
    if deal.buyer_room_show_arv:
        payload["arv"] = _money(deal.arv)
    if deal.buyer_room_show_repairs:
        payload["estimated_repairs"] = _money(deal.repair_estimate)
    if deal.buyer_room_show_comps:
        payload["comparable_sales"] = _published_comps(db, deal)

    # THEIR OWN reply, and only their own. There is no path from here to
    # another investor's status, offer or identity.
    if outreach is not None:
        payload["you"] = {
            "status": outreach.status,
            "your_offer": _money(outreach.offer_amount),
            "responded_at": (outreach.replied_at.isoformat()
                             if outreach.replied_at else None),
            "note": outreach.response_note,
        }
    if link is not None:
        payload["recipient_name"] = link.recipient_name

    return payload


# ── The seller's page ───────────────────────────────────────────────────────

# The transaction as a seller experiences it. Deliberately five steps, not
# nineteen: the internal pipeline is an operator's tool and a seller reading
# "Disposition" learns nothing except that they are a line item.
SELLER_STEPS = (
    ("offer", "Offer"),
    ("agreement", "Agreement"),
    ("review", "Property review"),
    ("title", "Title & closing"),
    ("closed", "Closed"),
)

# Which internal stages count each seller step as reached. A stage this map
# does not know leaves the step un-reached rather than guessing forward.
_STEP_STAGES = {
    "offer": ("offer_sent", "negotiating", "offer_review"),
    "agreement": ("under_contract", "disposition", "buyer_identified",
                  "assignment_pending", "title_closing", "closed"),
    "review": ("under_contract", "disposition", "buyer_identified",
               "assignment_pending", "title_closing", "closed"),
    "title": ("title_closing", "closed"),
    "closed": ("closed",),
}


def seller_progress(deal) -> List[Dict[str, Any]]:
    """Five steps, each either done, current or not yet — read off the record.

    A step is DONE when the deal has a dated fact proving it, and CURRENT when
    the stage says we are in it. Nothing here predicts a date.
    """
    stage = deal.stage or ""
    done = {
        "offer": bool(deal.proposed_offer is not None or deal.contract_price is not None),
        "agreement": bool(deal.contract_date or deal.contract_signed_at
                          or deal.seller_signed_at),
        "review": bool(deal.inspection_deadline and deal.earnest_money_received_at),
        "title": bool(deal.title_opened_at or deal.title_commitment_received_at),
        "closed": bool(deal.closed_at),
    }
    # A LADDER HAS TO CLIMB IN ORDER.
    #
    # Each fact above is computed independently, and they genuinely can arrive
    # out of order: title opens the moment the file is sent over, which is
    # often before the earnest money has landed and therefore before `review`
    # is satisfied. Read literally that produced
    #
    #     Offer done · Agreement done · Property review CURRENT ·
    #     Title & closing DONE · Closed upcoming
    #
    # on the owner's own page — a later step finished before an earlier one,
    # which reads as a broken screen rather than as a nuance about escrow.
    # Found by opening the page, not by reading the code.
    #
    # So a step may only show DONE when every step before it is done. Nothing
    # is invented and nothing is hidden: the underlying facts are unchanged and
    # a step that is genuinely finished still shows as finished the moment the
    # one in front of it catches up.
    ordered_done = {}
    still_running = True
    for key, _ in SELLER_STEPS:
        still_running = still_running and bool(done.get(key))
        ordered_done[key] = still_running

    steps = []
    current_set = False
    for key, label in SELLER_STEPS:
        if ordered_done.get(key):
            state = "done"
        elif not current_set and stage in _STEP_STAGES.get(key, ()):
            state = "current"
            current_set = True
        else:
            state = "upcoming"
        steps.append({"key": key, "label": label, "state": state})
    if not current_set:
        for step in steps:
            if step["state"] == "upcoming":
                step["state"] = "current"
                break

    # THE LADDER HAS ONE BOUNDARY, NOT SEVERAL.
    #
    # The monotonic guard above stops a LATER step finishing before an earlier
    # one. It does not stop the mirror-image defect, which a test in
    # test_wholesale_seller_progress.py found: a deal whose recorded STAGE has
    # run ahead of its paperwork produced
    #
    #     Offer NOT STARTED · Agreement IN PROGRESS · … 
    #
    # — an agreement being worked on for an offer that never happened. Read by
    # the person whose house it is, that is not a nuance about missing data,
    # it is a broken screen.
    #
    # A progress ladder means exactly one thing: everything before where you
    # are is behind you. The stage is the operator's own recorded position, so
    # a step the stage has passed is behind them whether or not a dated fact
    # for it was ever typed in. This only fires on a record with a gap in it —
    # a real deal reaches `agreement` by way of an offer, and its evidence
    # says so — and it can only ever move a step FORWARD to `done`, never a
    # finished step back, and never past the current one.
    cursor = next((i for i, s in enumerate(steps) if s["state"] == "current"),
                  None)
    if cursor is not None:
        for step in steps[:cursor]:
            step["state"] = "done"
    return steps


def seller_room_payload(db: Session, deal, link=None) -> Dict[str, Any]:
    """Everything a property owner may see about their own transaction.

    NOTE WHAT IS NOT HERE: no buyer, no buyer price, no assignment fee, no ARV,
    no repair estimate, no MAO, no qualification, no approvals, no internal
    notes. A seller asking "what is happening with my property" is owed an
    answer; they are not owed, and must not be shown, the disposition side.
    """
    prop = (db.query(WholesaleProperty)
            .filter(WholesaleProperty.id == deal.property_id).first()
            if deal.property_id else None)

    address = _address(prop)
    return {
        "deal_id": deal.id,
        "published": bool(deal.seller_room_published),
        "brand": branding(db, deal),
        "property": {
            "line1": address.get("line1"),
            "city": address.get("city"),
            "state": address.get("state"),
            "zip_code": address.get("zip_code"),
        },
        # Their own house, when a person has published a photo of it. Note what
        # is NOT taken from the address block above: no beds, no baths, no
        # square feet, no year. The owner knows those, and a page that recited
        # them back would read like a listing rather than a status page.
        "photos": seller_photos(db, deal),
        "progress": seller_progress(deal),
        "message": deal.seller_room_message,
        "documents": _published_documents(db, deal, "seller_visible"),
        "dates": {
            # Only dates that concern the seller's own side of the deal.
            "agreement_date": _date(deal.contract_date),
            "inspection_deadline": _date(deal.inspection_deadline),
            "closing_date": _date(deal.closing_date),
            "closing_time": deal.closing_time,
            "closing_location": deal.closing_location,
            "closed_at": _date(deal.closed_at.date() if deal.closed_at else None),
        },
        "title": {
            "company": deal.title_company,
            "escrow_officer": deal.title_escrow_officer,
            "file_number": deal.title_file_number,
        },
        "contact": {
            "name": deal.seller_room_contact_name,
            "phone": deal.seller_room_contact_phone,
            "email": deal.seller_room_contact_email,
        },
        "recipient_name": link.recipient_name if link is not None else None,
    }


# ── The check the tests run, and the routers can too ────────────────────────

def leaked_keys(payload: Any, forbidden) -> List[str]:
    """Any forbidden key found anywhere in a nested payload.

    A whitelist is the defence; this is the alarm. It walks the whole structure
    because a leak is far more likely to arrive nested inside a list of
    documents than at the top level.
    """
    found = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in forbidden:
                    found.append((path + "." + k).lstrip("."))
                walk(v, path + "." + k)
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (path, i))

    walk(payload)
    return sorted(set(found))
