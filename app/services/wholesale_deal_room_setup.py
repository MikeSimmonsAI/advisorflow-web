"""DEAL ROOM SETUP - the owner's guided configuration (#415 follow-up).

One row per workspace (`deal_room_settings`). The setup screens read a
PREFILLED view: anything the workspace already knows (organization name and
brand, support email and phone, the owner's own name and email) is filled in,
so nothing is typed twice. Saving persists exactly what the owner confirmed.

ACTIVATION IS REAL. `activate` re-checks the requirements on the server, records
who activated and when, and returns the actual result - including the reasons
when it refuses. Until the room is active only PRACTICE invitations and
listings work, so the owner can try everything before an investor sees it.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.models.models import Organization, User
from app.models.wholesale_investor_models import DealRoomSettings

DESIGNS = [{"key": "evosys_investor_v1", "label": "EvoSys Pro Investor Portal",
            "detail": "Navy navigation, bright workspace, property cards, AI concierge, messages and criteria panels "
                      "(the approved combined design)."}]
CATEGORIES = [("single_family", "Single-family"), ("multifamily", "Multifamily"), ("land", "Land"),
              ("commercial", "Commercial"), ("industrial", "Industrial"), ("mixed_use", "Mixed-use"),
              ("portfolio", "Portfolios")]
DFW_MARKETS = ["Dallas", "Tarrant", "Collin", "Denton"]
NOTIFY_EVENTS = [("message", "An investor sends a message"), ("handoff", "An investor asks for a person"),
                 ("hold_request", "An exclusive review is requested"), ("upload", "An investor uploads a document"),
                 ("offer", "An investor submits interest or an offer"), ("registered", "An invited investor registers")]
ALL_PRESETS = ["24h", "48h", "72h", "7d"]
EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[A-Za-z]{2,}$")


def _j(v, default):
    try:
        out = json.loads(v) if v else default
    except (TypeError, ValueError):
        return default
    return out if isinstance(out, type(default)) else default


def _iso(v):
    return v.isoformat() + "Z" if isinstance(v, datetime) else None


def row(db, org_id: str) -> Optional[DealRoomSettings]:
    return db.query(DealRoomSettings).filter(DealRoomSettings.organization_id == org_id).first()


def is_active(db, org_id: str) -> bool:
    r = row(db, org_id)
    return r is not None and r.status == "active"


def staff_people(db, org_id: str) -> List[Dict[str, Any]]:
    """People who may run the Deal Room: this workspace's staff logins (never investors)."""
    people = (db.query(User).filter(User.organization_id == org_id, User.role != "investor")
              .order_by(User.full_name.asc()).all())
    return [{"user_id": u.id, "name": u.full_name or u.email, "email": u.email, "role": u.role}
            for u in people if getattr(u, "is_active", True)]


def _owner(db, org_id: str, user=None) -> Optional[User]:
    if user is not None and getattr(user, "organization_id", None) == org_id:
        return user
    return (db.query(User).filter(User.organization_id == org_id, User.role.in_(("org_admin", "manager")))
            .order_by(User.created_at.asc()).first())


def view(db, org_id: str, user=None) -> Dict[str, Any]:
    """Saved settings, or the prefilled defaults the workspace already implies."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    r = row(db, org_id)
    own = _owner(db, org_id, user)
    brand = getattr(org, "brand_name", None) or getattr(org, "name", None)
    team = staff_people(db, org_id)
    pre = {
        "name": "%s Deal Room" % brand if brand else "Private Deal Room",
        "brand_name": brand, "tagline": "Exclusive real estate opportunities in Dallas - Fort Worth.",
        "owner_name": getattr(own, "full_name", None), "owner_email": getattr(own, "email", None),
        "owner_phone": getattr(own, "phone", None) or getattr(org, "org_phone", None),
        "support_email": getattr(org, "support_email", None) or getattr(org, "reply_to_email", None),
        "support_phone": getattr(org, "org_phone", None),
        "team_user_ids": [p["user_id"] for p in team if p["role"] in ("org_admin", "manager")][:6],
        "welcome_title": "Welcome to your private Deal Room",
        "welcome_message": ("Off-market opportunities selected for you, with a direct line to our team. Ask the "
                            "concierge anything - answers come only from what we have published."),
        "design": DESIGNS[0]["key"], "categories": [k for k, _ in CATEGORIES[:4]], "markets": list(DFW_MARKETS),
        "ai_concierge_enabled": True, "notify": {k: True for k, _ in NOTIFY_EVENTS},
        "default_audience": "matched", "default_min_match": 60, "default_publish_days": 14,
        "hold_presets": list(ALL_PRESETS), "hold_allow_custom": True, "hold_max_per_investor": 1,
        "hold_require_note": False, "hold_require_pof": False,
    }
    saved = {}
    if r is not None:
        saved = {"name": r.name, "brand_name": r.brand_name, "tagline": r.tagline, "owner_name": r.owner_name,
                 "owner_email": r.owner_email, "owner_phone": r.owner_phone, "support_email": r.support_email,
                 "support_phone": r.support_phone, "team_user_ids": _j(r.team_user_ids, []),
                 "welcome_title": r.welcome_title, "welcome_message": r.welcome_message, "design": r.design,
                 "categories": _j(r.categories, []), "markets": _j(r.markets, []),
                 "ai_concierge_enabled": bool(r.ai_concierge_enabled), "notify": _j(r.notify, {}),
                 "default_audience": r.default_audience, "default_min_match": r.default_min_match,
                 "default_publish_days": r.default_publish_days, "hold_presets": _j(r.hold_presets, []),
                 "hold_allow_custom": bool(r.hold_allow_custom), "hold_max_per_investor": r.hold_max_per_investor,
                 "hold_require_note": bool(r.hold_require_note), "hold_require_pof": bool(r.hold_require_pof)}
        saved = {k: v for k, v in saved.items() if v not in (None, "")}
    s = {**pre, **saved}
    return {"settings": s, "saved": r is not None, "status": r.status if r is not None else "setup",
            "activated_at": _iso(getattr(r, "activated_at", None)), "updated_at": _iso(getattr(r, "updated_at", None)),
            "prefilled": sorted(k for k in pre if k not in saved),
            "options": {"designs": DESIGNS, "categories": [{"key": k, "label": v} for k, v in CATEGORIES],
                        "markets": DFW_MARKETS, "notify": [{"key": k, "label": v} for k, v in NOTIFY_EVENTS],
                        "hold_presets": ALL_PRESETS, "team": team},
            "portal_url": "/investors"}


def _may_configure(db, user) -> None:
    from app.services import wholesale_deal_room as DR
    if getattr(user, "role", None) not in DR.STAFF_ROLES and not DR._is_wholesale_manager(db, user):
        raise HTTPException(status_code=403, detail="Only an owner or manager can change the Deal Room settings.")


def save(db, org_id: str, data: Dict[str, Any], user) -> Dict[str, Any]:
    _may_configure(db, user)
    # validate everything first, so a refused save changes nothing
    for k in ("owner_email", "support_email"):
        v = (str(data.get(k) or "").strip().lower()) if k in data else ""
        if v and not EMAIL_RE.match(v):
            raise HTTPException(status_code=400, detail="%s is not a valid email address." % k.replace("_", " "))
    if "design" in data and data["design"] not in {d["key"] for d in DESIGNS}:
        raise HTTPException(status_code=400, detail="Choose an approved portal design.")
    if "default_audience" in data and data["default_audience"] not in ("matched", "selected", "all"):
        raise HTTPException(status_code=400, detail="Audience must be matched, selected or all.")
    for k in ("default_min_match", "default_publish_days", "hold_max_per_investor"):
        if data.get(k) not in (None, ""):
            try:
                int(data[k])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="%s must be a whole number." % k.replace("_", " "))
    r = row(db, org_id)
    if r is None:
        r = DealRoomSettings(organization_id=org_id, status="setup")
        db.add(r)
        db.flush()
    txt = {"name": 120, "brand_name": 120, "tagline": 200, "owner_name": 120, "owner_phone": 40,
           "support_phone": 40, "welcome_title": 160}
    for k, n in txt.items():
        if k in data:
            setattr(r, k, (str(data[k] or "").strip()[:n]) or None)
    for k in ("owner_email", "support_email"):
        if k in data:
            v = (str(data[k] or "").strip().lower()) or None
            if v and not EMAIL_RE.match(v):
                raise HTTPException(status_code=400, detail="%s is not a valid email address." % k.replace("_", " "))
            setattr(r, k, v)
    if "welcome_message" in data:
        r.welcome_message = (str(data["welcome_message"] or "").strip()[:2000]) or None
    if "team_user_ids" in data:
        allowed = {p["user_id"] for p in staff_people(db, org_id)}
        r.team_user_ids = json.dumps([u for u in (data["team_user_ids"] or []) if u in allowed])
    if "design" in data:
        if data["design"] not in {d["key"] for d in DESIGNS}:
            raise HTTPException(status_code=400, detail="Choose an approved portal design.")
        r.design = data["design"]
    if "categories" in data:
        keys = {k for k, _ in CATEGORIES}
        r.categories = json.dumps([c for c in (data["categories"] or []) if c in keys])
    if "markets" in data:
        r.markets = json.dumps([str(m).strip()[:40] for m in (data["markets"] or []) if str(m).strip()][:20])
    if "ai_concierge_enabled" in data:
        r.ai_concierge_enabled = bool(data["ai_concierge_enabled"])
    if "notify" in data and isinstance(data["notify"], dict):
        keys = {k for k, _ in NOTIFY_EVENTS}
        r.notify = json.dumps({k: bool(v) for k, v in data["notify"].items() if k in keys})
    if "default_audience" in data:
        if data["default_audience"] not in ("matched", "selected", "all"):
            raise HTTPException(status_code=400, detail="Audience must be matched, selected or all.")
        r.default_audience = data["default_audience"]
    if "default_min_match" in data and data["default_min_match"] not in (None, ""):
        r.default_min_match = max(0, min(100, int(data["default_min_match"])))
    if "default_publish_days" in data:
        v = data["default_publish_days"]
        r.default_publish_days = None if v in (None, "", 0, "0") else max(1, min(365, int(v)))
    if "hold_presets" in data:
        r.hold_presets = json.dumps([p for p in (data["hold_presets"] or []) if p in ALL_PRESETS])
    for k in ("hold_allow_custom", "hold_require_note", "hold_require_pof"):
        if k in data:
            setattr(r, k, bool(data[k]))
    if "hold_max_per_investor" in data and data["hold_max_per_investor"] not in (None, ""):
        r.hold_max_per_investor = max(1, min(10, int(data["hold_max_per_investor"])))
    r.updated_by_id = user.id
    r.updated_at = datetime.utcnow()
    db.flush()
    from app.services import wholesale_deal_room as DR
    DR.log(db, org_id, "deal_room.settings_saved", "Deal Room settings saved", user)
    return view(db, org_id, user)


def readiness(db, org_id: str, user=None) -> Dict[str, Any]:
    """What activation needs, checked against real records. `blocking` items stop
    activation; the rest are shown so nothing is a surprise."""
    from app.models.wholesale_investor_models import DealListing, InvestorAccount, InvestorInvite
    v = view(db, org_id, user)
    s = v["settings"]
    items = []

    def add(key, label, ok, detail, blocking=True):
        items.append({"key": key, "label": label, "ok": bool(ok), "detail": detail, "blocking": blocking})
    add("saved", "Settings saved", v["saved"], "Save the setup at least once." if not v["saved"] else "Saved.")
    add("name", "Deal Room name", s.get("name"), s.get("name") or "Give the Deal Room a name.")
    add("support", "Support contact for investors", s.get("support_email") or s.get("support_phone"),
        s.get("support_email") or s.get("support_phone") or "Add a support email or phone investors can reach.")
    add("team", "At least one approved team member", s.get("team_user_ids"),
        "%d team member(s) approved." % len(s.get("team_user_ids") or []) if s.get("team_user_ids")
        else "Choose who answers investors and approves exclusive reviews.")
    add("categories", "Investment categories", s.get("categories"),
        ", ".join(s.get("categories") or []) or "Pick at least one category investors can expect.")
    add("hold_rules", "Exclusive-review lengths", s.get("hold_presets") or s.get("hold_allow_custom"),
        ", ".join(s.get("hold_presets") or []) or ("Custom only" if s.get("hold_allow_custom") else "Allow at least one length."))
    try:
        from app.services import email_service
        es = email_service.describe_email_sender(db, org_id)
    except Exception:  # noqa: BLE001 - reported, never fatal
        es = {"ready": False, "reason": "The email sender could not be checked."}
    add("email", "Invitation email delivery", es.get("ready"),
        ("Invitations are emailed from %s; you can also copy each secure link." % es.get("from_email"))
        if es.get("ready") else "%s Until then, copy each secure link and send it yourself." % (es.get("reason") or ""),
        blocking=False)
    listed = db.query(DealListing).filter(DealListing.organization_id == org_id, DealListing.is_test.is_(False)).count()
    add("listing", "A real deal ready to publish", listed,
        "%d real listing(s) in the room." % listed if listed
        else "Optional now: add an approved deal package (Add deal) after activation.", blocking=False)
    invited = db.query(InvestorInvite).filter(InvestorInvite.organization_id == org_id,
                                              InvestorInvite.is_test.is_(False)).count()
    accounts = db.query(InvestorAccount).filter(InvestorAccount.organization_id == org_id,
                                                InvestorAccount.is_test.is_(False)).count()
    add("investors", "Investors invited", invited, "%d invited, %d registered." % (invited, accounts)
        if invited else "Invite your first investor after activation.", blocking=False)
    blockers = [i for i in items if i["blocking"] and not i["ok"]]
    return {"status": v["status"], "items": items, "blockers": blockers, "can_activate": not blockers,
            "portal_url": "/investors", "activated_at": v["activated_at"]}


def activate(db, org_id: str, user) -> Dict[str, Any]:
    _may_configure(db, user)
    rd = readiness(db, org_id, user)
    if rd["blockers"]:
        return {"activated": False, "status": rd["status"], "readiness": rd,
                "message": "Not activated: " + "; ".join(b["label"] for b in rd["blockers"]) + "."}
    r = row(db, org_id)
    already = r.status == "active"
    r.status = "active"
    if not already:
        r.activated_at, r.activated_by_id = datetime.utcnow(), user.id
    db.flush()
    from app.services import wholesale_deal_room as DR
    DR.log(db, org_id, "deal_room.activated", "Deal Room activated", user)
    return {"activated": True, "status": "active", "readiness": readiness(db, org_id, user),
            "message": "Deal Room is active. Real invitations and listings can now go out." if not already
            else "Deal Room was already active - settings saved."}


def pause(db, org_id: str, user) -> Dict[str, Any]:
    _may_configure(db, user)
    r = row(db, org_id)
    if r is None or r.status != "active":
        raise HTTPException(status_code=409, detail="The Deal Room is not active.")
    r.status = "paused"
    db.flush()
    from app.services import wholesale_deal_room as DR
    DR.log(db, org_id, "deal_room.paused", "Deal Room paused - no new real invitations or publications", user)
    return {"status": "paused"}


def require_active(db, org_id: str, is_test: bool, what: str) -> None:
    """Practice records always work; real ones need an active Deal Room."""
    if is_test:
        return
    if not is_active(db, org_id):
        raise HTTPException(status_code=409, detail="Activate your Deal Room before %s (Deal Room > Settings). "
                                                    "Practice records work while you set up." % what)


# ── settings other modules read ────────────────────────────────────────────

def effective(db, org_id: str) -> Dict[str, Any]:
    return view(db, org_id)["settings"]


def notify_on(db, org_id: str, event: str) -> bool:
    r = row(db, org_id)
    if r is None:
        return True
    return bool(_j(r.notify, {}).get(event, True))


def hold_rules(db, org_id: str) -> Dict[str, Any]:
    s = effective(db, org_id)
    return {"presets": s.get("hold_presets") or [], "allow_custom": bool(s.get("hold_allow_custom")),
            "max_per_investor": int(s.get("hold_max_per_investor") or 1),
            "require_note": bool(s.get("hold_require_note")), "require_pof": bool(s.get("hold_require_pof"))}


def ai_enabled(db, org_id: str) -> bool:
    r = row(db, org_id)
    return True if r is None else bool(r.ai_concierge_enabled)


def investor_view(db, org_id: str) -> Dict[str, Any]:
    """What the investor portal may show about the room (no internal settings)."""
    s = effective(db, org_id)
    return {"name": s.get("name"), "brand_name": s.get("brand_name"), "tagline": s.get("tagline"),
            "welcome_title": s.get("welcome_title"), "welcome_message": s.get("welcome_message"),
            "support_email": s.get("support_email"), "support_phone": s.get("support_phone"),
            "categories": s.get("categories") or [], "markets": s.get("markets") or [],
            "ai_concierge": bool(s.get("ai_concierge_enabled")), "design": s.get("design"),
            "hold_presets": s.get("hold_presets") or [], "hold_require_note": bool(s.get("hold_require_note"))}
