"""Location outreach program API (SCI and any multi-location customer).

    /program/...          the customer's own workspace (active workspace org)
    /program-assets/{t}   hosted flyer / image links - the token IS the authority,
                          and only an ACTIVE asset is served
    /god/programs/...     owner-only configuration of a customer as a program

Nothing here sends to a customer. Campaign "preview" renders text; it never
enrols or sends. Staging an import writes staging rows, never leads.
"""
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func

from app.utils.content_disposition import content_disposition
from app.services.programs import aliases as _aliases
from sqlalchemy.orm import Session

from app.deps import get_db, require_god, require_tenant_or_observer, require_tenant_user
from app.models.location_models import Location
from app.models.models import CadenceState, Lead, Organization, User
from app.models.program_models import (
    EMAIL_MODES, CampaignFamily, ContactVerification, LocationProfile, OutreachProgram,
    ProgramAlert, ProgramAsset, ProgramResponse, ProgramSourceRecord,
)
from app.services import lead_scope
from app.services.programs import identity, importer, responses, setup

router = APIRouter(prefix="/program", tags=["program"])
public_router = APIRouter(tags=["program-public"])
god_router = APIRouter(prefix="/god/programs", tags=["program-god"])

MAX_ASSET_BYTES = 20 * 1024 * 1024
ASSET_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/svg+xml"}


# ── helpers ──────────────────────────────────────────────────────────────────

def _org_id(db: Session, user: User) -> str:
    oid = lead_scope.active_workspace_org_id(user, db) or user.organization_id
    if not oid:
        raise HTTPException(status_code=404, detail="No workspace selected.")
    return oid


def _program(db: Session, user: User, managers_only: bool = True) -> OutreachProgram:
    """The caller's program. MANAGERS ONLY by default: these screens carry every
    family's name, number, email and reply text across the whole customer,
    which is wider than an advisor's own-leads scope."""
    prog = identity.program_for_org(db, _org_id(db, user))
    if prog is None:
        raise HTTPException(status_code=404, detail="This workspace has no outreach program.")
    if managers_only and not lead_scope.is_manager_here(user, db):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return prog


def _require_manager(db: Session, user: User) -> None:
    if not lead_scope.is_manager_here(user, db):
        raise HTTPException(status_code=403, detail="Admin access required.")


def _profiles(db: Session, org_id: str) -> List[LocationProfile]:
    return (db.query(LocationProfile).filter(LocationProfile.organization_id == org_id)
            .order_by(LocationProfile.is_review_bucket, LocationProfile.official_name).all())


def _asset_url(a: Optional[ProgramAsset]) -> Optional[str]:
    return "/program-assets/%s" % a.public_token if a is not None and a.is_active else None


def _profile_json(db: Session, prog: OutreachProgram, p: LocationProfile) -> dict:
    loc = db.query(Location).filter(Location.id == p.location_id).first()
    assets = {a.id: a for a in db.query(ProgramAsset).filter(
        ProgramAsset.id.in_([x for x in (p.logo_asset_id, p.hero_asset_id) if x])).all()} \
        if (p.logo_asset_id or p.hero_asset_id) else {}
    return {
        "id": p.id, "location_id": p.location_id, "official_name": p.official_name,
        "is_review_bucket": p.is_review_bucket,
        "source_names": json.loads(p.source_names or "[]"),
        "address": {k: getattr(loc, k, None) for k in
                    ("address_line1", "address_line2", "city", "state", "postal_code")} if loc else {},
        "facility_phone": p.facility_phone or (loc.phone if loc else None),
        "website": p.website, "manager_name": p.manager_name,
        "advisor_names": json.loads(p.advisor_names or "[]"),
        "appointment_link": p.appointment_link,
        "email_display_name": identity.display_name(prog, p) if not p.is_review_bucket else None,
        "email_alias": p.email_alias, "alias_verified_at": _iso(p.alias_verified_at),
        "alias_last_seen_at": _iso(p.alias_last_seen_at),
        "mailbox_folder": p.mailbox_folder or (None if p.is_review_bucket else p.official_name),
        "alias_receiving": _aliases.receiving(prog, p),
        "alias_mode": _aliases.effective_mode(prog, p, _aliases.sending_address(db, prog.organization_id))
        if not p.is_review_bucket else None,
        "sms_signoff": identity.sms_signoff(prog, p) if not p.is_review_bucket else None,
        "logo_url": _asset_url(assets.get(p.logo_asset_id)),
        "hero_url": _asset_url(assets.get(p.hero_asset_id)),
        "brand_settings": json.loads(p.brand_settings or "{}"),
    }


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _program_json(db: Session, prog: OutreachProgram) -> dict:
    logo = db.query(ProgramAsset).filter(ProgramAsset.id == prog.logo_asset_id).first() \
        if prog.logo_asset_id else None
    return {
        "id": prog.id, "organization_id": prog.organization_id, "name": prog.name,
        "hero_title": prog.hero_title, "hero_subtitle": prog.hero_subtitle,
        "primary_contact_name": prog.primary_contact_name,
        "customer_identity_locked": identity.locked(prog),
        "primary_contact_title": prog.primary_contact_title,
        "primary_contact_user_id": prog.primary_contact_user_id,
        "alert_email": prog.alert_email, "alert_phone": prog.alert_phone,
        "management_recipients": json.loads(prog.management_recipients or "[]"),
        "hot_sla_minutes": prog.hot_sla_minutes,
        "customer_channels": identity.channels(prog),
        "reply_instructions_sms": prog.reply_instructions_sms,
        "reply_instructions_email": prog.reply_instructions_email,
        "require_location_to_send": prog.require_location_to_send,
        "staff_alerts_enabled": prog.staff_sms_alerts_enabled,
        "alias_mode": prog.alias_mode or "from",
        "mailbox_folder_path": prog.mailbox_folder_path,
        "aliases": _aliases.status(db, prog),
        "aliases_receiving_confirmed_at": _iso(prog.aliases_receiving_confirmed_at),
        "logo_url": _asset_url(logo),
    }


# ── program & settings ───────────────────────────────────────────────────────

@router.get("/status")
def program_status(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Whether this workspace runs a program - a 200 either way, for the nav."""
    prog = identity.program_for_org(db, lead_scope.active_workspace_org_id(user, db) or user.organization_id)
    manager = bool(prog) and lead_scope.is_manager_here(user, db)
    return {"active": prog is not None and manager, "name": prog.name if prog else None}


@router.get("/me")
def program_me(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    return _program_json(db, _program(db, user, managers_only=False))


class SettingsIn(BaseModel):
    primary_contact_name: Optional[str] = None
    primary_contact_title: Optional[str] = None
    alert_email: Optional[str] = None
    alert_phone: Optional[str] = None
    management_recipients: Optional[List[dict]] = None
    hot_sla_minutes: Optional[int] = None
    reply_instructions_sms: Optional[str] = None
    reply_instructions_email: Optional[str] = None
    staff_alerts_enabled: Optional[bool] = None
    alias_mode: Optional[str] = None
    mailbox_folder_path: Optional[str] = None
    customer_identity_locked: Optional[bool] = None
    placement_seed_addresses: Optional[dict] = None


_EMAIL_RE = __import__("re").compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean_phone(raw, field: str):
    """US numbers to E.164 (+1XXXXXXXXXX); anything else must already be +E.164."""
    if raw is None or not str(raw).strip():
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if str(raw).strip().startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    raise HTTPException(status_code=422, detail="%s: %r is not a valid phone number." % (field, raw))


def _clean_email(raw, field: str):
    if raw is None or not str(raw).strip():
        return None
    v = str(raw).strip().lower()
    if not _EMAIL_RE.match(v):
        raise HTTPException(status_code=422, detail="%s: %r is not a valid email address." % (field, raw))
    return v


@router.patch("/settings")
def update_settings(body: SettingsIn, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user)):
    prog = _program(db, user)
    _require_manager(db, user)
    data = body.dict(exclude_unset=True)
    if "hot_sla_minutes" in data and not (1 <= int(data["hot_sla_minutes"] or 0) <= 1440):
        raise HTTPException(status_code=422, detail="SLA must be between 1 and 1440 minutes.")
    for key in ("reply_instructions_sms", "reply_instructions_email"):
        if key in data and data[key] and "call" in data[key].lower():
            raise HTTPException(status_code=422,
                                detail="Reply instructions ask families to reply, not to call.")
    if "management_recipients" in data:
        clean = []
        for m in data.pop("management_recipients") or []:
            row = {k: (str(m.get(k)).strip() if m.get(k) else None) for k in ("name", "role")}
            row["role"] = row["role"] or "management"
            row["phone"] = _clean_phone(m.get("phone"), "Management phone")
            row["email"] = _clean_email(m.get("email"), "Management email")
            if row["phone"] or row["email"]:
                clean.append(row)
        prog.management_recipients = json.dumps(clean)
    if "alert_phone" in data:
        data["alert_phone"] = _clean_phone(data["alert_phone"], "Alert phone")
    if "alert_email" in data:
        data["alert_email"] = _clean_email(data["alert_email"], "Alert email")
    if "alias_mode" in data:
        if data["alias_mode"] not in ("from", "reply_to", "off"):
            raise HTTPException(status_code=422, detail="alias_mode must be from, reply_to or off.")
    if "staff_alerts_enabled" in data:
        prog.staff_sms_alerts_enabled = bool(data.pop("staff_alerts_enabled"))
    if "placement_seed_addresses" in data:
        from app.services.programs import placement as _pl
        raw = data.pop("placement_seed_addresses") or {}
        clean = {}
        for k, v in raw.items():
            if k not in _pl.PROVIDERS:
                raise HTTPException(status_code=422, detail="Seed providers: %s." % ", ".join(_pl.PROVIDERS))
            e = _clean_email(v, "Seed inbox (%s)" % k)
            if e:
                clean[k] = e
        prog.placement_seed_addresses = json.dumps(clean)
    # CUSTOMER-FACING IDENTITY is locked until the platform owner unlocks it:
    # a manager may write and send, but the family always sees Kerry Allan.
    from app.services.capabilities import is_god as _is_god_user
    if "customer_identity_locked" in data:
        if not _is_god_user(user):
            raise HTTPException(status_code=403, detail="Only the platform owner can unlock the "
                                "customer-facing identity.")
        prog.customer_identity_locked = bool(data.pop("customer_identity_locked"))
    if "primary_contact_name" in data and identity.locked(prog) \
            and (data["primary_contact_name"] or "").strip() != (prog.primary_contact_name or ""):
        raise HTTPException(status_code=409, detail="The customer-facing identity is locked to %s. "
                            "Only the platform owner can change it." % (prog.primary_contact_name or "the program contact"))
    before = {k: getattr(prog, k, None) for k in data}
    for k, v in data.items():
        setattr(prog, k, v.strip() if isinstance(v, str) else v)
    db.commit()
    try:
        from app.routers.audit_log_router import log_action
        log_action(db, prog.organization_id, user.id, "program.settings_changed", "program", prog.id,
                   before={k: (str(v) if v is not None else None) for k, v in before.items()},
                   after={k: (str(getattr(prog, k, None)) if getattr(prog, k, None) is not None else None)
                          for k in before})
    except Exception:                                    # noqa: BLE001
        db.rollback()
    return _program_json(db, prog)


# ── locations ────────────────────────────────────────────────────────────────

@router.get("/locations")
def list_locations(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    prog = _program(db, user)
    return [_profile_json(db, prog, p) for p in _profiles(db, prog.organization_id)]


class ProfileIn(BaseModel):
    official_name: Optional[str] = None
    website: Optional[str] = None
    facility_phone: Optional[str] = None
    manager_name: Optional[str] = None
    advisor_names: Optional[List[str]] = None
    email_display_name: Optional[str] = None
    sms_identity_name: Optional[str] = None
    email_alias: Optional[str] = None
    mailbox_folder: Optional[str] = None
    appointment_link: Optional[str] = None
    logo_asset_id: Optional[str] = None
    hero_asset_id: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None


@router.patch("/locations/{profile_id}")
def update_location(profile_id: str, body: ProfileIn, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user)):
    prog = _program(db, user)
    _require_manager(db, user)
    p = db.query(LocationProfile).filter(LocationProfile.id == profile_id,
                                         LocationProfile.organization_id == prog.organization_id).first()
    if p is None:
        raise HTTPException(status_code=404, detail="Location not found.")
    data = body.dict(exclude_unset=True)
    for aid_key in ("logo_asset_id", "hero_asset_id"):
        if data.get(aid_key) and not db.query(ProgramAsset).filter(
                ProgramAsset.id == data[aid_key],
                ProgramAsset.organization_id == prog.organization_id).first():
            raise HTTPException(status_code=422, detail="Unknown asset.")
    loc = db.query(Location).filter(Location.id == p.location_id).first()
    for k in ("address_line1", "address_line2", "city", "state", "postal_code"):
        if k in data and loc is not None:
            setattr(loc, k, (data.pop(k) or "").strip() or None)
        data.pop(k, None)
    if "advisor_names" in data:
        p.advisor_names = json.dumps([a.strip() for a in data.pop("advisor_names") or [] if a.strip()])
    for k in ("email_display_name", "sms_identity_name"):
        if k in data and not identity.override_allowed(prog, p, data[k]):
            raise HTTPException(status_code=409, detail="The customer-facing identity is locked: this must read "
                                "\"%s | %s\"." % (prog.primary_contact_name or "", p.official_name))
    if "email_alias" in data:
        raw = (data.pop("email_alias") or "").strip().lower()
        if p.is_review_bucket:
            raise HTTPException(status_code=422, detail="The review bucket has no email address.")
        if not raw:
            p.email_alias, p.alias_verified_at = None, None
        else:
            domain = _aliases.alias_domain(db, prog)
            local = raw.split("@", 1)[0]
            if "@" in raw and raw.split("@", 1)[1] != domain:
                raise HTTPException(status_code=422, detail="Location addresses must be on %s." % domain)
            why = _aliases.validate_local(local)
            if why:
                raise HTTPException(status_code=422, detail="Address: %s." % why)
            addr = "%s@%s" % (local, domain)
            if _aliases.taken(db, addr, p.id):
                raise HTTPException(status_code=409, detail="%s is already used by another location." % addr)
            if addr != p.email_alias:
                p.email_alias, p.alias_verified_at = addr, None
    for k, v in data.items():
        setattr(p, k, (v.strip() or None) if isinstance(v, str) else v)
    db.commit()
    return _profile_json(db, prog, p)


# ── dashboard ────────────────────────────────────────────────────────────────

@router.get("/dashboard")
def dashboard(location_id: Optional[str] = Query(None), db: Session = Depends(get_db),
              user: User = Depends(require_tenant_or_observer)):
    prog = _program(db, user)
    org_id = prog.organization_id
    profiles = _profiles(db, org_id)
    if location_id and location_id not in {p.location_id for p in profiles}:
        raise HTTPException(status_code=404, detail="Location not found.")

    recs_q = db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org_id)
    resp_q = db.query(ProgramResponse).filter(ProgramResponse.organization_id == org_id)
    if location_id:
        recs_q = recs_q.filter(ProgramSourceRecord.location_id == location_id)
        resp_q = resp_q.filter(ProgramResponse.location_id == location_id)
    recs = recs_q.all()
    resps = resp_q.order_by(ProgramResponse.received_at.desc()).all()
    now = datetime.utcnow()

    status = Counter((r.source_status or "").strip() or "Unknown" for r in recs)
    open_resps = [r for r in resps if r.handling_status != "closed"]
    new_resps = [r for r in resps if r.handling_status == "new"]
    hot_open = [r for r in open_resps if r.response_class == "hot"]
    unhandled_hot = [r for r in hot_open if r.handling_status == "new"
                     and r.sla_due_at and r.sla_due_at <= now]

    by_loc_recs, by_loc_resp = defaultdict(list), defaultdict(list)
    for r in recs:
        by_loc_recs[r.location_id].append(r)
    for r in resps:
        by_loc_resp[r.location_id].append(r)
    loc_perf = []
    for p in profiles:
        rs = by_loc_recs.get(p.location_id, [])
        if location_id and p.location_id != location_id:
            continue
        if not rs and p.is_review_bucket:
            continue
        rr = by_loc_resp.get(p.location_id, [])
        responders = {x.lead_id for x in rr}
        loc_perf.append({
            "location_id": p.location_id, "name": p.official_name,
            "is_review_bucket": p.is_review_bucket, "leads": len(rs),
            "qualified": sum(1 for x in rs if (x.source_status or "") == "Qualified"),
            "new_responses": sum(1 for x in rr if x.handling_status == "new"),
            "hot": sum(1 for x in rr if x.response_class == "hot" and x.handling_status != "closed"),
            "response_rate": round(100.0 * len(responders) / len(rs), 1) if rs else None,
        })
    loc_perf.sort(key=lambda x: (-x["leads"], x["name"]))

    fams = {f.key: f for f in db.query(CampaignFamily).filter(CampaignFamily.organization_id == org_id).all()}
    fam_counts = Counter(r.campaign_family or "unmapped" for r in recs)
    fam_resp = Counter(r.campaign_family or "unmapped" for r in resps)
    campaign_perf = [{"key": k, "name": fams[k].name if k in fams else "Unmapped",
                      "leads": n, "responses": fam_resp.get(k, 0),
                      "is_active": bool(fams[k].is_active) if k in fams else False,
                      "share": round(100.0 * n / len(recs), 1) if recs else 0}
                     for k, n in fam_counts.most_common()]

    lead_ids = [r.lead_id for r in recs if r.lead_id]
    cad = Counter()
    if lead_ids:
        for st in db.query(CadenceState.status).filter(CadenceState.lead_id.in_(lead_ids)).all():
            cad[str(getattr(st[0], "value", st[0]))] += 1

    leads = {}
    ids = [r.lead_id for r in resps[:8]]
    if ids:
        leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_(ids)).all()}
    pname = {p.location_id: p.official_name for p in profiles}
    recent = [{
        "id": r.id, "lead_id": r.lead_id,
        "name": ("%s %s" % (leads[r.lead_id].first_name or "", leads[r.lead_id].last_name or "")).strip().title()
        if r.lead_id in leads else None,
        "location": pname.get(r.location_id), "channel": r.channel,
        "class": r.response_class, "summary": r.summary, "status": r.handling_status,
        "received_at": r.received_at.isoformat() + "Z" if r.received_at else None,
    } for r in resps[:8]]

    sel = next((p for p in profiles if p.location_id == location_id), None) if location_id else None
    return {
        "program": _program_json(db, prog),
        "locations": [{"location_id": p.location_id, "name": p.official_name,
                       "is_review_bucket": p.is_review_bucket} for p in profiles],
        "selected_location": _profile_json(db, prog, sel) if sel else None,
        "metrics": {
            "leads": len(recs),
            "contacts": len({r.contact_master_key or r.source_lead_id for r in recs}),
            "locations": sum(1 for p in profiles if not p.is_review_bucket) if not location_id else 1,
            "qualified": status.get("Qualified", 0),
            "new_responses": len(new_resps),
            "hot_responses": len(hot_open),
        },
        "pipeline": [{"status": k, "count": v,
                      "share": round(100.0 * v / len(recs), 1) if recs else 0}
                     for k, v in status.most_common()],
        "attention": {
            "hot_responses": sum(1 for r in hot_open if r.handling_status == "new"),
            "new_sms_replies": sum(1 for r in new_resps if r.channel == "sms"),
            "new_email_replies": sum(1 for r in new_resps if r.channel == "email"),
            "follow_ups_due": sum(1 for r in open_resps if r.handling_status == "opened"),
            "unhandled_hot": len(unhandled_hot),
            "data_review": sum(1 for r in recs if r.needs_data_review),
            "location_review": sum(1 for r in recs if r.location_status == "location_review"),
            "duplicate_review": sum(1 for r in recs if r.duplicate_review_reason),
            "on_hold": sum(1 for r in recs if r.on_hold),
            "unmatched_replies": _unmatched_open(db, prog.organization_id),
        },
        "recent_conversations": recent,
        "location_performance": loc_perf,
        "campaign_performance": campaign_perf,
        "automation": {
            "active_cadences": cad.get("active", 0), "paused_cadences": cad.get("paused", 0),
            "awaiting_reply": max(0, len({r.lead_id for r in recs if r.lead_id})
                                  - len({r.lead_id for r in resps})),
            "staged_not_live": sum(1 for r in recs if not r.lead_id),
            "active_campaign_families": sum(1 for f in fams.values() if f.is_active),
            "suppressed": sum(1 for r in resps if r.response_class == "opt_out"),
            "review_needed": sum(1 for r in recs if r.needs_data_review or r.duplicate_review_reason
                                 or r.location_status == "location_review"),
        },
        "reporting": _reporting(db, recs, resps),
        "readiness": readiness(db, prog),
    }


def _reporting(db: Session, recs, resps) -> dict:
    """Delivery and handling numbers. SENT is never reported as DELIVERED."""
    from app.models.models import BookingLink, EmailMessage, Message
    lead_ids = list({r.lead_id for r in recs if r.lead_id})
    import os as _os
    events_on = bool((_os.environ.get("RESEND_WEBHOOK_SECRET") or "").strip())
    out = {"email": {"sent": 0, "delivered": 0 if events_on else None, "bounced": 0, "failed": 0,
                     "opened": 0, "replied": 0, "unsubscribed": 0, "complained": 0 if events_on else None,
                     "note": ("Delivered, bounced and complaint counts come from the provider's signed events."
                              if events_on else
                              "Provider delivery events are not switched on yet (RESEND_WEBHOOK_SECRET), so "
                              "delivered and complaint counts are unknown - only sends, failures, opens and "
                              "replies are counted.")},
           "sms": {"sent": 0, "delivered": 0, "failed": 0, "replied": 0},
           "appointments": 0}
    if lead_ids:
        for st, n in (db.query(EmailMessage.status, func.count(EmailMessage.id))
                      .filter(EmailMessage.lead_id.in_(lead_ids)).group_by(EmailMessage.status).all()):
            st = (st or "").lower()
            if st in ("sent", "delivered", "opened", "bounced", "complained"):
                out["email"]["sent"] += n
            if st == "delivered" and events_on:
                out["email"]["delivered"] += n
            if st == "complained" and events_on:
                out["email"]["complained"] += n
            if st == "bounced":
                out["email"]["bounced"] += n
            if st == "failed":
                out["email"]["failed"] += n
        out["email"]["opened"] = (db.query(func.count(EmailMessage.id))
                                  .filter(EmailMessage.lead_id.in_(lead_ids),
                                          EmailMessage.opened_at.isnot(None)).scalar() or 0)
        for st, n in (db.query(Message.delivery_status, func.count(Message.id))
                      .filter(Message.lead_id.in_(lead_ids)).group_by(Message.delivery_status).all()):
            out["sms"]["sent"] += n
            if (st or "") == "delivered":
                out["sms"]["delivered"] += n
            if (st or "") in ("failed", "undelivered"):
                out["sms"]["failed"] += n
        out["appointments"] = (db.query(func.count(BookingLink.id))
                               .filter(BookingLink.lead_id.in_(lead_ids),
                                       BookingLink.status.in_(("booked", "confirmed"))).scalar() or 0)
    out["email"]["replied"] = sum(1 for r in resps if r.channel == "email")
    out["sms"]["replied"] = sum(1 for r in resps if r.channel == "sms")
    out["email"]["unsubscribed"] = sum(1 for r in resps if r.channel == "email" and r.response_class == "opt_out")
    waits = sorted((r.responded_at - r.received_at).total_seconds() / 60.0
                   for r in resps if r.responded_at and r.received_at)
    out["response_time_minutes"] = {
        "handled": len(waits),
        "median": round(waits[len(waits) // 2], 1) if waits else None,
        "average": round(sum(waits) / len(waits), 1) if waits else None,
    }
    return out


def readiness(db: Session, prog: OutreachProgram) -> dict:
    """What would stop production outreach today. Read from configuration only."""
    org = db.query(Organization).filter(Organization.id == prog.organization_id).first()
    from app.services.public_identity import sending_identity_for_org
    ident = sending_identity_for_org(db, prog.organization_id)
    import os
    sms_number = getattr(org, "org_twilio_phone_number", None)
    items = [
        {"key": "email_sender", "ok": bool(getattr(ident, "from_email", None)),
         "detail": getattr(ident, "from_email", None) or "no verified sending address resolved"},
        _alias_item(db, prog),
        {"key": "sms_number", "ok": bool(sms_number),
         "detail": sms_number or "no organization Twilio number configured"},
        _sms_routing_item(db, prog.organization_id, sms_number),
        _mailbox_item(db, ident),
        {"key": "primary_contact_user", "ok": bool(prog.primary_contact_user_id),
         "detail": "account linked" if prog.primary_contact_user_id else "profile only - add an email to create the account"},
        _alert_item(prog),
        _hold_item(db, prog),
        _email_runner_item(),
        _postal_item(db, prog),
        _link_domain_item(ident),
        _placement_item(db, prog),
        {"key": "outbound_brake", "ok": os.environ.get("OUTBOUND_EMERGENCY_STOP", "").lower() not in ("1", "true", "yes", "on"),
         "detail": "emergency stop is ON" if os.environ.get("OUTBOUND_EMERGENCY_STOP", "").lower() in ("1", "true", "yes", "on") else "off"},
    ]
    return {"items": items, "ready": all(i["ok"] for i in items)}


def _placement_item(db: Session, prog: OutreachProgram) -> dict:
    from app.services.programs import placement as _pl
    return _pl.readiness_item(db, prog)


def _postal_item(db: Session, prog: OutreachProgram) -> dict:
    """Commercial email must carry a valid postal address. Taken from each
    location's verified address - never invented; a missing one holds that
    location's automated email (message_brain quality gate)."""
    from app.models.location_models import Location
    profs = _profiles(db, prog.organization_id)
    real = [p for p in profs if not p.is_review_bucket]
    locs = {l.id: l for l in db.query(Location).filter(Location.id.in_([p.location_id for p in real] or ["-"]))}
    have = sum(1 for p in real if locs.get(p.location_id) is not None and locs[p.location_id].address_line1
               and locs[p.location_id].city and locs[p.location_id].state)
    return {"key": "postal_addresses", "ok": bool(real) and have == len(real),
            "detail": "%d of %d locations have a verified postal address for the email footer%s"
                      % (have, len(real), "" if have == len(real) else
                         " - automated email to the others is held until one is entered")}


def _link_domain_item(ident) -> dict:
    """Links in a first touch that point at a different domain than the sender
    (e.g. a *.onrender.com host) are a classic bulk/phishing signal."""
    import os
    from urllib.parse import urlparse
    from app.services.programs import aliases as _al
    base = (os.environ.get("PROGRAM_ASSET_BASE_URL") or "").strip()
    host = (urlparse(base).hostname or "").lower() if base else ""
    dom = _al.domain_of(getattr(ident, "from_email", None)) or ""
    ok = bool(host) and bool(dom) and (host == dom or host.endswith("." + dom))
    return {"key": "link_domain", "ok": ok,
            "detail": ("hosted links and unsubscribe use %s (sender domain %s)" % (host or "the API host", dom or "?"))
                      + ("" if ok else " - set PROGRAM_ASSET_BASE_URL to a host on the sending domain")}


def _alias_item(db: Session, prog: OutreachProgram) -> dict:
    st = _aliases.status(db, prog)
    eff = st["effective"]
    if not st["assigned"]:
        return {"key": "location_email_aliases", "ok": False, "detail": "no location addresses assigned yet"}
    live = eff.get("from", 0) + eff.get("reply_to", 0)
    detail = ("%d of %d location addresses on %s; authentication %s; %d seen receiving%s; in use: %d as From, %d as Reply-To"
              % (st["assigned"], st["locations"], st["domain"],
                 "aligned (same domain as the verified sender)" if st["auth_ok"] else "NOT aligned - Reply-To only",
                 st["seen_receiving"], ", all confirmed by a person" if st["confirmed_all"] else "",
                 eff.get("from", 0), eff.get("reply_to", 0)))
    if not live:
        detail += " - not used until they receive mail (add them in Microsoft 365, then confirm)"
    return {"key": "location_email_aliases", "ok": live == st["locations"] and st["locations"] > 0,
            "detail": detail}


def _alert_item(prog: OutreachProgram) -> dict:
    mgrs = [m for m in json.loads(prog.management_recipients or "[]") if isinstance(m, dict)]
    chans = []
    if prog.alert_phone or prog.alert_email:
        chans.append("primary")
    if mgrs:
        chans.append("%d management (%s)" % (len(mgrs), ", ".join(
            "+".join(c for c, v in (("sms", m.get("phone")), ("email", m.get("email"))) if v) for m in mgrs)))
    ok = bool(chans) and bool(prog.staff_sms_alerts_enabled)
    detail = ("; ".join(chans) or "no recipients configured") + (
        "; staff alerts ON" if prog.staff_sms_alerts_enabled else "; staff alerts OFF (in-app only)")
    return {"key": "alert_recipients", "ok": ok, "detail": detail}


def _hold_item(db: Session, prog: OutreachProgram) -> dict:
    n = (db.query(func.count(ProgramSourceRecord.id))
         .filter(ProgramSourceRecord.organization_id == prog.organization_id,
                 ProgramSourceRecord.on_hold.is_(True)).scalar() or 0)
    return {"key": "held_records", "ok": True,
            "detail": "%d on hold - excluded from outreach; does not block the rest" % n}


def _unmatched_open(db: Session, org_id: str) -> int:
    from app.models.program_models import ProgramUnmatchedReply
    return (db.query(func.count(ProgramUnmatchedReply.id))
            .filter(ProgramUnmatchedReply.organization_id == org_id,
                    ProgramUnmatchedReply.status == "open").scalar() or 0)


def _email_runner_item() -> dict:
    from app.services.programs import email_touches as _et
    on = _et.sending_enabled()
    return {"key": "email_campaign_runner", "ok": on,
            "detail": ("on - campaign emails go out for campaigns that are switched on, "
                       "%d per pass, %d per day, follow-up after %d days"
                       % (_et.batch_limit(), _et.daily_cap(), _et.followup_days()))
            if on else "off (%s) - no campaign email goes out" % _et.SENDING_ENV}


def _sms_routing_item(db: Session, org_id: str, number: Optional[str]) -> dict:
    """Inbound texts are routed to a workspace BY THE NUMBER THEY WERE SENT TO.
    A number another workspace (or a user there) also uses would send this
    program's replies to the wrong place."""
    if not number:
        return {"key": "sms_reply_routing", "ok": False,
                "detail": "no number, so replies cannot be routed here"}
    from app.services.dedup_service import normalize_phone
    forms = {number, normalize_phone(number), "+" + normalize_phone(number)}
    others = (db.query(Organization.id).filter(Organization.org_twilio_phone_number.in_(forms),
                                               Organization.id != org_id).count()
              + db.query(User.id).filter(User.twilio_phone_number.in_(forms),
                                         User.organization_id != org_id).count())
    return {"key": "sms_reply_routing", "ok": others == 0,
            "detail": "number used only by this workspace" if others == 0 else
            "number is also used by %d other workspace(s)/user(s) - replies would be routed "
            "to whichever matches first" % others}


def _mailbox_item(db: Session, ident) -> dict:
    """Email replies come back through the shared sending mailbox, if connected."""
    try:
        from app.models.inbound_mailbox_models import InboundMailbox
        addrs = {a.lower() for a in (getattr(ident, "reply_to_email", None),
                                     getattr(ident, "from_email", None)) if a}
        box = (db.query(InboundMailbox).filter(InboundMailbox.address.in_(addrs),
                                               InboundMailbox.is_active.is_(True)).first()
               if addrs else None)
    except Exception:
        box = None
    if box is None:
        return {"key": "email_reply_mailbox", "ok": False,
                "detail": "the sending mailbox is not connected for reply reading"}
    return {"key": "email_reply_mailbox", "ok": (box.last_status or "ok") == "ok",
            "detail": "%s connected, last read %s (%s)" % (
                box.address, box.last_polled_at.isoformat() + "Z" if box.last_polled_at else "never",
                box.last_status or "not yet polled")}


# ── inbox placement checks ───────────────────────────────────────────────────

class PlacementSendIn(BaseModel):
    location_id: Optional[str] = None
    family: str = "veteran_planning_guide"


class PlacementResultIn(BaseModel):
    folder: str
    note: Optional[str] = None


def _check_json(c) -> dict:
    return {"id": c.id, "provider": c.provider, "seed_address": c.seed_address, "subject": c.subject,
            "sent_at": c.sent_at.isoformat() + "Z" if c.sent_at else None, "send_error": c.send_error,
            "folder": c.folder, "recorded_at": c.recorded_at.isoformat() + "Z" if c.recorded_at else None,
            "note": c.note}


@router.get("/placement-checks")
def list_placement_checks(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    from app.models.program_models import ProgramPlacementCheck
    from app.services.programs import placement as _pl
    prog = _program(db, user)
    rows = (db.query(ProgramPlacementCheck).filter(ProgramPlacementCheck.organization_id == prog.organization_id)
            .order_by(ProgramPlacementCheck.created_at.desc()).limit(40).all())
    return {"seeds": _pl.seeds(prog), "providers": list(_pl.PROVIDERS), "folders": list(_pl.FOLDERS),
            "checks": [_check_json(c) for c in rows], "readiness": _pl.readiness_item(db, prog)}


@router.post("/placement-checks/send")
def send_placement_checks(body: PlacementSendIn, db: Session = Depends(get_db),
                          user: User = Depends(require_tenant_user)):
    """Mail the real first touch to the program's own seed inboxes (never customers)."""
    from app.services.programs import placement as _pl
    prog = _program(db, user)
    _require_manager(db, user)
    try:
        rows = _pl.send_checks(db, prog, user.id, location_id=body.location_id, family=body.family)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"checks": [_check_json(c) for c in rows]}


@router.post("/placement-checks/{check_id}/result")
def record_placement_result(check_id: str, body: PlacementResultIn, db: Session = Depends(get_db),
                            user: User = Depends(require_tenant_user)):
    from app.models.program_models import ProgramPlacementCheck
    from app.services.programs import placement as _pl
    prog = _program(db, user)
    _require_manager(db, user)
    c = db.query(ProgramPlacementCheck).filter(ProgramPlacementCheck.id == check_id,
                                               ProgramPlacementCheck.organization_id == prog.organization_id).first()
    if c is None:
        raise HTTPException(status_code=404, detail="Check not found.")
    try:
        _pl.record(db, c, body.folder, user.id, body.note)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _check_json(c)


# ── responses ────────────────────────────────────────────────────────────────

@router.get("/responses")
def list_responses(status: Optional[str] = Query(None), response_class: Optional[str] = Query(None),
                   location_id: Optional[str] = Query(None), limit: int = Query(100, ge=1, le=500),
                   db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    prog = _program(db, user)
    q = db.query(ProgramResponse).filter(ProgramResponse.organization_id == prog.organization_id)
    if status == "open":
        q = q.filter(ProgramResponse.handling_status != "closed")
    elif status:
        q = q.filter(ProgramResponse.handling_status == status)
    if response_class:
        q = q.filter(ProgramResponse.response_class == response_class)
    if location_id:
        q = q.filter(ProgramResponse.location_id == location_id)
    total = q.count()
    rows = q.order_by(ProgramResponse.received_at.desc()).limit(limit).all()
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_([r.lead_id for r in rows])).all()} if rows else {}
    pname = {p.location_id: p.official_name for p in _profiles(db, prog.organization_id)}
    now = datetime.utcnow()
    return {"total": total, "items": [{
        "id": r.id, "lead_id": r.lead_id, "source_lead_id": r.source_lead_id,
        "name": ("%s %s" % (leads[r.lead_id].first_name or "", leads[r.lead_id].last_name or "")).strip().title()
        if r.lead_id in leads else None,
        "location": pname.get(r.location_id), "campaign_family": r.campaign_family,
        "channel": r.channel, "class": r.response_class, "label": responses.LABELS.get(r.response_class),
        "summary": r.summary, "recommended_action": r.recommended_action, "body": r.body_excerpt,
        "reply_to_alias": r.reply_to_alias,
        "intents": [{"key": k, "label": responses.INTENT_LABELS.get(k, k)} for k in json.loads(r.intents or "[]")],
        "urgency": r.urgency or responses.urgency(r.response_class),
        "suggested_reply": r.suggested_reply,
        "opened_at": r.opened_at.isoformat() + "Z" if r.opened_at else None,
        "responded_at": r.responded_at.isoformat() + "Z" if r.responded_at else None,
        "minutes_to_open": (int((r.opened_at - r.received_at).total_seconds() // 60)
                            if r.opened_at and r.received_at else None),
        "minutes_to_respond": (int((r.responded_at - r.received_at).total_seconds() // 60)
                               if r.responded_at and r.received_at else None),
        "status": r.handling_status, "cadence_paused": r.cadence_paused,
        "received_at": r.received_at.isoformat() + "Z" if r.received_at else None,
        "sla_due_at": r.sla_due_at.isoformat() + "Z" if r.sla_due_at else None,
        "sla_breached": bool(r.response_class == "hot" and r.handling_status == "new"
                             and r.sla_due_at and r.sla_due_at <= now),
        "sla_alert_count": r.sla_alert_count,
    } for r in rows]}


class MarkIn(BaseModel):
    state: str


@router.post("/responses/{response_id}/mark")
def mark_response(response_id: str, body: MarkIn, db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user)):
    prog = _program(db, user)
    r = db.query(ProgramResponse).filter(ProgramResponse.id == response_id,
                                         ProgramResponse.organization_id == prog.organization_id).first()
    if r is None:
        raise HTTPException(status_code=404, detail="Response not found.")
    try:
        responses.mark(db, r, body.state, user)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"id": r.id, "status": r.handling_status}


@router.post("/responses/{response_id}/viewed")
def response_viewed(response_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user)):
    """KERRY OPENED, recorded automatically - but only when the program's
    primary contact opens it. A manager looking at a HOT reply must not stop
    its SLA clock; that would hide the very response management is watching."""
    prog = _program(db, user)
    r = db.query(ProgramResponse).filter(ProgramResponse.id == response_id,
                                         ProgramResponse.organization_id == prog.organization_id).first()
    if r is None:
        raise HTTPException(status_code=404, detail="Response not found.")
    if prog.primary_contact_user_id and user.id == prog.primary_contact_user_id \
            and (r.handling_status or "new") == "new":
        responses.mark(db, r, "opened", user)
    return {"id": r.id, "status": r.handling_status}


@router.get("/voice")
def voice_report(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_or_observer)):
    """Calls, answered, missed, voicemails and transcripts per location."""
    from app.services.programs import program_voice
    prog = _program(db, user)
    since = datetime.utcnow() - timedelta(days=days)
    return {"days": days, "locations": program_voice.location_report(db, prog.organization_id, since)}


@router.get("/alerts")
def list_alerts(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
                user: User = Depends(require_tenant_or_observer)):
    prog = _program(db, user)
    _require_manager(db, user)
    rows = (db.query(ProgramAlert).filter(ProgramAlert.organization_id == prog.organization_id)
            .order_by(ProgramAlert.created_at.desc()).limit(limit).all())
    return [{"id": a.id, "kind": a.kind, "audience": a.audience, "channel": a.channel,
             "delivered": a.delivered, "reason": a.reason, "message": a.message,
             "created_at": a.created_at.isoformat() + "Z" if a.created_at else None} for a in rows]


# ── source records (review queues) ───────────────────────────────────────────

@router.get("/records")
def list_records(queue: Optional[str] = Query(None), location_id: Optional[str] = Query(None),
                 search: Optional[str] = Query(None), limit: int = Query(200, ge=1, le=1000),
                 offset: int = Query(0, ge=0),
                 db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    prog = _program(db, user)
    q = db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == prog.organization_id)
    if queue == "on_hold":
        q = q.filter(ProgramSourceRecord.on_hold.is_(True))
    elif queue == "data_review":
        q = q.filter(ProgramSourceRecord.needs_data_review.is_(True))
    elif queue == "location_review":
        q = q.filter(ProgramSourceRecord.location_status == "location_review")
    elif queue == "duplicate_review":
        q = q.filter(ProgramSourceRecord.duplicate_review_reason.isnot(None))
    elif queue == "linked":
        q = q.filter(ProgramSourceRecord.link_status.in_(("primary", "linked")))
    if location_id:
        q = q.filter(ProgramSourceRecord.location_id == location_id)
    if search:
        term = "%%%s%%" % search.strip()
        q = q.filter((ProgramSourceRecord.first_name.ilike(term)) | (ProgramSourceRecord.last_name.ilike(term))
                     | (ProgramSourceRecord.email.ilike(term)) | (ProgramSourceRecord.source_lead_id.ilike(term)))
    total = q.count()
    rows = q.order_by(ProgramSourceRecord.row_number).offset(offset).limit(limit).all()
    pname = {p.location_id: p.official_name for p in _profiles(db, prog.organization_id)}
    return {"total": total, "items": [{
        "id": r.id, "source_lead_id": r.source_lead_id, "first_name": r.first_name,
        "last_name": r.last_name, "email": r.email, "phone": r.phone,
        "source_status": r.source_status, "source_campaign": r.source_campaign,
        "source_location_name": r.source_location_name, "location": pname.get(r.location_id),
        "location_status": r.location_status, "campaign_family": r.campaign_family,
        "contact_master_key": r.contact_master_key, "link_status": r.link_status,
        "link_reason": r.link_reason, "duplicate_review_reason": r.duplicate_review_reason,
        "data_note_flags": json.loads(r.data_note_flags or "[]"), "lead_id": r.lead_id,
        "on_hold": bool(r.on_hold), "hold_reason": r.hold_reason, "held_at": _iso(r.held_at),
    } for r in rows]}


class AssignLocationIn(BaseModel):
    location_id: str


@router.post("/records/{record_id}/location")
def assign_location(record_id: str, body: AssignLocationIn, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user)):
    """Resolve Location Review by hand. The source location name is kept as supplied."""
    prog = _program(db, user)
    _require_manager(db, user)
    rec = db.query(ProgramSourceRecord).filter(ProgramSourceRecord.id == record_id,
                                               ProgramSourceRecord.organization_id == prog.organization_id).first()
    prof = db.query(LocationProfile).filter(LocationProfile.organization_id == prog.organization_id,
                                            LocationProfile.location_id == body.location_id).first()
    if rec is None or prof is None or prof.is_review_bucket:
        raise HTTPException(status_code=404, detail="Record or location not found.")
    rec.location_id, rec.location_status = prof.location_id, "mapped"
    rec.location_assigned_manually = True
    db.commit()
    return {"id": rec.id, "location": prof.official_name, "location_status": rec.location_status}


class ClearReviewIn(BaseModel):
    review: str          # data_review | duplicate_review
    note: Optional[str] = None


@router.post("/records/{record_id}/review")
def clear_review(record_id: str, body: ClearReviewIn, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user)):
    """A person has looked. Clears the hold; the flags and reasons stay on record
    (data_note_flags is kept, the duplicate reason moves to the link note)."""
    prog = _program(db, user)
    _require_manager(db, user)
    rec = db.query(ProgramSourceRecord).filter(ProgramSourceRecord.id == record_id,
                                               ProgramSourceRecord.organization_id == prog.organization_id).first()
    if rec is None:
        raise HTTPException(status_code=404, detail="Record not found.")
    if body.review == "data_review":
        rec.needs_data_review = False
        rec.data_review_cleared_at = datetime.utcnow()
    elif body.review == "duplicate_review":
        if rec.duplicate_review_reason:
            rec.link_reason = ("%s | reviewed by %s: %s" % (
                rec.link_reason or "", user.full_name or user.email,
                rec.duplicate_review_reason)).strip(" |")
        rec.duplicate_review_reason = None
        rec.duplicate_review_cleared_at = datetime.utcnow()
    else:
        raise HTTPException(status_code=422, detail="review must be data_review or duplicate_review.")
    from app.routers.audit_log_router import log_action
    log_action(db, prog.organization_id, user.id, action="program.review_cleared",
               target_type="program_source_record", target_id=rec.id,
               details={"review": body.review, "source_lead_id": rec.source_lead_id,
                        "note": (body.note or "")[:300]})
    db.commit()
    return {"id": rec.id, "needs_data_review": rec.needs_data_review,
            "duplicate_review_reason": rec.duplicate_review_reason}


# ── operations health ────────────────────────────────────────────────────────

@router.get("/health")
def program_health(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Is the SCI system working? Read-only; managers only."""
    from app.services.programs import health
    prog = _program(db, user)
    return health.snapshot(db, prog)


# ── holds ────────────────────────────────────────────────────────────────────

class HoldIn(BaseModel):
    on_hold: bool
    reason: Optional[str] = None


@router.post("/records/{record_id}/hold")
def hold_record(record_id: str, body: HoldIn, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user)):
    """Put one record on hold, or release it. Audited; nothing else changes."""
    from app.services.programs import holds
    prog = _program(db, user)
    _require_manager(db, user)
    rec = db.query(ProgramSourceRecord).filter(ProgramSourceRecord.id == record_id,
                                               ProgramSourceRecord.organization_id == prog.organization_id).first()
    if rec is None:
        raise HTTPException(status_code=404, detail="Record not found.")
    holds.set_hold(db, rec, body.on_hold, user.id, body.reason)
    from app.routers.audit_log_router import log_action
    log_action(db, prog.organization_id, user.id, action="program.record_hold" if body.on_hold else "program.record_release",
               target_type="program_source_record", target_id=rec.id,
               details={"source_lead_id": rec.source_lead_id, "reason": (body.reason or "")[:300]})
    db.commit()
    return {"id": rec.id, "on_hold": rec.on_hold, "hold_reason": rec.hold_reason}


@router.post("/records/hold-open-reviews")
def hold_open_reviews(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Hold every record still in Location / Duplicate / Data Review (or a
    location conflict). Deletes nothing, fixes nothing."""
    from app.services.programs import holds
    prog = _program(db, user)
    _require_manager(db, user)
    res = holds.hold_open_reviews(db, prog.organization_id, user.id)
    db.commit()
    return res


# ── location email aliases ───────────────────────────────────────────────────

@router.get("/aliases")
def list_aliases(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    prog = _program(db, user)
    frm = _aliases.sending_address(db, prog.organization_id)
    rows = [{"location": p.official_name, "location_id": p.location_id, "alias": p.email_alias,
             "seen_receiving_at": _iso(p.alias_verified_at), "receiving": _aliases.receiving(prog, p),
             "mode": _aliases.effective_mode(prog, p, frm)}
            for p in _profiles(db, prog.organization_id) if not p.is_review_bucket]
    return {"status": _aliases.status(db, prog), "items": rows}


@router.get("/aliases.csv")
def export_aliases(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Alias,Location - the input for scripts/m365_location_aliases.ps1."""
    import csv as _csv
    import io as _io
    from fastapi.responses import Response
    prog = _program(db, user)
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["Alias", "Location"])
    for p in _profiles(db, prog.organization_id):
        if p.email_alias and not p.is_review_bucket:
            w.writerow([p.email_alias, p.official_name])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": content_disposition("location_aliases.csv")})


@router.post("/aliases/assign")
def assign_aliases(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Give every location without one its address (existing ones are kept)."""
    prog = _program(db, user)
    _require_manager(db, user)
    try:
        out = _aliases.assign(db, prog)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return {"assigned": out, "status": _aliases.status(db, prog)}


class ConfirmAliasesIn(BaseModel):
    confirm: Optional[str] = None
    receiving: bool = True


@router.post("/aliases/confirm-receiving")
def confirm_aliases(body: ConfirmAliasesIn, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user)):
    """A person confirms the aliases were added to the central mailbox. Until
    then an alias is used only once mail to it has been SEEN arriving."""
    prog = _program(db, user)
    _require_manager(db, user)
    if body.receiving:
        if (body.confirm or "").strip().upper() != "ALIASES RECEIVE MAIL":
            raise HTTPException(status_code=422, detail='Type "ALIASES RECEIVE MAIL" to confirm.')
        prog.aliases_receiving_confirmed_at, prog.aliases_receiving_confirmed_by = datetime.utcnow(), user.id
    else:
        prog.aliases_receiving_confirmed_at = prog.aliases_receiving_confirmed_by = None
    from app.routers.audit_log_router import log_action
    log_action(db, prog.organization_id, user.id, action="program.aliases_receiving",
               target_type="outreach_program", target_id=prog.id, details={"receiving": body.receiving})
    db.commit()
    return _aliases.status(db, prog)


# ── replies to a location address that match no contact ─────────────────────

@router.get("/unmatched-replies")
def unmatched_replies(status: str = Query("open"), db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_or_observer)):
    from app.models.program_models import ProgramUnmatchedReply
    prog = _program(db, user)
    q = db.query(ProgramUnmatchedReply).filter(ProgramUnmatchedReply.organization_id == prog.organization_id)
    if status in ("open", "handled"):
        q = q.filter(ProgramUnmatchedReply.status == status)
    pname = {p.location_id: p.official_name for p in _profiles(db, prog.organization_id)}
    return [{"id": u.id, "location": pname.get(u.location_id), "alias": u.alias, "from": u.from_address,
             "subject": u.subject, "body": u.body_excerpt, "received_at": _iso(u.received_at),
             "reason": u.reason, "status": u.status, "handled_at": _iso(u.handled_at)}
            for u in q.order_by(ProgramUnmatchedReply.received_at.desc()).limit(200).all()]


@router.post("/unmatched-replies/{reply_id}/handled")
def unmatched_handled(reply_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    from app.models.program_models import ProgramUnmatchedReply
    prog = _program(db, user)
    u = db.query(ProgramUnmatchedReply).filter(ProgramUnmatchedReply.id == reply_id,
                                               ProgramUnmatchedReply.organization_id == prog.organization_id).first()
    if u is None:
        raise HTTPException(status_code=404, detail="Reply not found.")
    u.status, u.handled_at, u.handled_by = "handled", datetime.utcnow(), user.id
    db.commit()
    return {"id": u.id, "status": u.status}


# ── verification / enrichment (stored beside the originals) ─────────────────

class VerificationIn(BaseModel):
    verified_phone: Optional[str] = None
    phone_line_type: Optional[str] = None
    phone_ownership_confidence: Optional[int] = None
    alternate_phone: Optional[str] = None
    verified_email: Optional[str] = None
    email_confidence: Optional[int] = None
    verified_address: Optional[str] = None
    current_address: Optional[str] = None
    identity_confidence: Optional[int] = None
    provider: Optional[str] = None
    notes: Optional[str] = None


@router.get("/records/{record_id}/verification")
def get_verification(record_id: str, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_or_observer)):
    prog = _program(db, user)
    rec = db.query(ProgramSourceRecord).filter(ProgramSourceRecord.id == record_id,
                                               ProgramSourceRecord.organization_id == prog.organization_id).first()
    if rec is None:
        raise HTTPException(status_code=404, detail="Record not found.")
    v = (db.query(ContactVerification).filter(ContactVerification.source_record_id == rec.id)
         .order_by(ContactVerification.created_at.desc()).first())
    verified = None
    if v is not None:
        verified = {c: getattr(v, c) for c in (
            "verified_phone", "phone_line_type", "phone_ownership_confidence", "alternate_phone",
            "verified_email", "email_confidence", "verified_address", "current_address",
            "identity_confidence", "provider", "notes", "outreach_eligible")}
        verified["verified_at"] = v.verified_at.isoformat() + "Z" if v.verified_at else None
    return {
        "original": {"phone": rec.phone, "email": rec.email, "source_lead_id": rec.source_lead_id},
        "verified": verified,
    }


@router.post("/records/{record_id}/verification")
def add_verification(record_id: str, body: VerificationIn, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user)):
    """Record a verification result. Never edits the source record; never grants outreach."""
    prog = _program(db, user)
    _require_manager(db, user)
    rec = db.query(ProgramSourceRecord).filter(ProgramSourceRecord.id == record_id,
                                               ProgramSourceRecord.organization_id == prog.organization_id).first()
    if rec is None:
        raise HTTPException(status_code=404, detail="Record not found.")
    v = ContactVerification(organization_id=prog.organization_id, source_record_id=rec.id,
                            verified_at=datetime.utcnow(), outreach_eligible=False,
                            **body.dict(exclude_unset=True))
    db.add(v)
    db.commit()
    return {"id": v.id, "outreach_eligible": False}


# ── campaign families ────────────────────────────────────────────────────────

def _family_json(f: CampaignFamily) -> dict:
    return {"id": f.id, "key": f.key, "name": f.name, "language": f.language,
            "asset_category": f.asset_category, "is_active": f.is_active,
            "first_touch_email_mode": f.first_touch_email_mode,
            "followup_email_mode": f.followup_email_mode,
            "source_campaign_patterns": json.loads(f.source_campaign_patterns or "[]"),
            "sms_template": f.sms_template, "email_subject_template": f.email_subject_template,
            "email_body_template": f.email_body_template}


@router.get("/campaigns")
def list_campaigns(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    prog = _program(db, user)
    return [_family_json(f) for f in db.query(CampaignFamily)
            .filter(CampaignFamily.organization_id == prog.organization_id)
            .order_by(CampaignFamily.name).all()]


class FamilyIn(BaseModel):
    first_touch_email_mode: Optional[str] = None
    followup_email_mode: Optional[str] = None
    sms_template: Optional[str] = None
    email_subject_template: Optional[str] = None
    email_body_template: Optional[str] = None
    asset_category: Optional[str] = None


@router.patch("/campaigns/{family_id}")
def update_campaign(family_id: str, body: FamilyIn, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user)):
    """Edit a family's modes and copy. Activation is not done here - see readiness."""
    prog = _program(db, user)
    _require_manager(db, user)
    f = db.query(CampaignFamily).filter(CampaignFamily.id == family_id,
                                        CampaignFamily.organization_id == prog.organization_id).first()
    if f is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    data = body.dict(exclude_unset=True)
    for k in ("first_touch_email_mode", "followup_email_mode"):
        if k in data and data[k] not in EMAIL_MODES:
            raise HTTPException(status_code=422, detail="Email mode must be one of %s." % ", ".join(EMAIL_MODES))
    for k in ("sms_template", "email_body_template"):
        if k in data and data[k] and "call us" in data[k].lower():
            raise HTTPException(status_code=422, detail="Families are asked to reply, not to call.")
    for k, v in data.items():
        setattr(f, k, v)
    db.commit()
    return _family_json(f)


class ActivationIn(BaseModel):
    active: bool
    confirm: Optional[str] = None


@router.post("/campaigns/{family_id}/activation")
def set_campaign_active(family_id: str, body: ActivationIn, db: Session = Depends(get_db),
                        user: User = Depends(require_tenant_user)):
    """Switch a family on or off. ON lets AUTOMATED sends go to contacts already
    enrolled in it; it enrols no one by itself. Switching on requires typing the
    family's name, and is audit-logged."""
    prog = _program(db, user)
    _require_manager(db, user)
    f = db.query(CampaignFamily).filter(CampaignFamily.id == family_id,
                                        CampaignFamily.organization_id == prog.organization_id).first()
    if f is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    if body.active and (body.confirm or "").strip().lower() != f.name.strip().lower():
        raise HTTPException(status_code=422, detail="Type the campaign name to confirm switching it on.")
    f.is_active = bool(body.active)
    from app.routers.audit_log_router import log_action
    log_action(db, prog.organization_id, user.id, action="program.campaign_%s" % ("on" if f.is_active else "off"),
               target_type="program_campaign_family", target_id=f.id, details={"key": f.key, "name": f.name})
    db.commit()
    return _family_json(f)


@router.get("/campaigns/{family_id}/preview")
def preview_campaign(family_id: str, location_id: Optional[str] = Query(None),
                     record_id: Optional[str] = Query(None), touch: str = Query("first"),
                     db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Render one family for one location (and optionally one contact). Sends nothing."""
    prog = _program(db, user)
    f = db.query(CampaignFamily).filter(CampaignFamily.id == family_id,
                                        CampaignFamily.organization_id == prog.organization_id).first()
    if f is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    rec = None
    if record_id:
        rec = db.query(ProgramSourceRecord).filter(ProgramSourceRecord.id == record_id,
                                                   ProgramSourceRecord.organization_id == prog.organization_id).first()
        location_id = location_id or (rec.location_id if rec else None)
    prof = db.query(LocationProfile).filter(LocationProfile.organization_id == prog.organization_id,
                                            LocationProfile.location_id == location_id).first() if location_id else None
    if prof is None or prof.is_review_bucket:
        return {"ok": False, "reason": identity.LOCATION_REVIEW_REFUSAL}
    from app.services.programs import email_touches as _et
    r = _et.render_touch(db, prog, f, prof, rec.first_name if rec else "Pat",
                         "first" if touch == "first" else "followup")
    fields = r["fields"]
    sms = identity.render(f.sms_template, dict(fields, reply_instructions=prog.reply_instructions_sms or ""))
    sms = "%s %s" % (sms, identity.sms_signoff(prog, prof))
    return {
        "ok": True, "family": f.key, "location": prof.official_name, "email_mode": r["email_mode"],
        "from_display_name": identity.display_name(prog, prof),
        "sms": sms + " Reply STOP to opt out.",
        "email_subject": r["subject"],
        "email_body": r["body"],
        "attachment": r["attachment"], "flyer_available": r["flyer_available"],
    }


@router.get("/email-touches")
def email_touches(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """What the campaign email runner WOULD send now (dry run) and what it has sent. Sends nothing."""
    from app.services.programs import email_touches as _et
    from app.models.program_models import ProgramEmailTouch
    prog = _program(db, user)
    report = _et.run(db, prog.organization_id, dry_run=True)
    recent = (db.query(ProgramEmailTouch).filter(ProgramEmailTouch.organization_id == prog.organization_id)
              .order_by(ProgramEmailTouch.created_at.desc()).limit(50).all())
    counts = {}
    for st, n in (db.query(ProgramEmailTouch.status, func.count(ProgramEmailTouch.id))
                  .filter(ProgramEmailTouch.organization_id == prog.organization_id)
                  .group_by(ProgramEmailTouch.status).all()):
        counts[st] = n
    return {
        "enabled": report["enabled"], "followup_days": _et.followup_days(), "batch": _et.batch_limit(),
        "daily_cap": _et.daily_cap(), "used_today": _et.used_today(db, prog.organization_id),
        "due": report["due"], "skipped": report["skipped"], "held_no_flyer": report.get("held_no_flyer", 0), "would_send": report["would_send"][:200],
        "would_send_total": len(report["would_send"]), "counts": counts,
        "recent": [{"id": t.id, "lead_id": t.lead_id, "family": t.campaign_family, "touch": t.touch_number,
                    "email_mode": t.email_mode, "status": t.status, "reason": t.reason,
                    "attempted_at": t.attempted_at.isoformat() + "Z" if t.attempted_at else None}
                   for t in recent],
    }


# Shared with the email touch runner - one renderer for preview and send.
from app.services.programs.email_touches import public_asset_url, flyer_for  # noqa: E402,F401


# ── assets ───────────────────────────────────────────────────────────────────

def _asset_json(a: ProgramAsset) -> dict:
    return {"id": a.id, "kind": a.kind, "category": a.category, "campaign_family": a.campaign_family,
            "location_id": a.location_id, "title": a.title, "version": a.version,
            "is_active": a.is_active, "filename": a.filename, "content_type": a.content_type,
            "size_bytes": a.size_bytes, "hosted_url": _asset_url(a),
            "preview_url": "/program/assets/%s/preview" % a.id,
            "dynamic_fields": json.loads(a.dynamic_fields or "[]"),
            "created_at": a.created_at.isoformat() + "Z" if a.created_at else None}


@router.get("/assets")
def list_assets(kind: Optional[str] = Query(None), db: Session = Depends(get_db),
                user: User = Depends(require_tenant_or_observer)):
    prog = _program(db, user)
    q = db.query(ProgramAsset).filter(ProgramAsset.organization_id == prog.organization_id)
    if kind:
        q = q.filter(ProgramAsset.kind == kind)
    return {"categories": setup.FLYER_CATEGORIES,
            "items": [_asset_json(a) for a in q.order_by(ProgramAsset.kind, ProgramAsset.title,
                                                          ProgramAsset.version.desc()).all()]}


@router.post("/assets")
async def upload_asset(file: UploadFile = File(...), kind: str = Form(...), title: str = Form(...),
                       category: Optional[str] = Form(None), campaign_family: Optional[str] = Form(None),
                       location_id: Optional[str] = Form(None), activate: bool = Form(False),
                       db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    prog = _program(db, user)
    _require_manager(db, user)
    if kind not in ("logo", "facility_image", "flyer"):
        raise HTTPException(status_code=422, detail="kind must be logo, facility_image or flyer.")
    if category and category not in setup.FLYER_CATEGORIES:
        raise HTTPException(status_code=422, detail="Unknown flyer category.")
    if location_id and not db.query(LocationProfile).filter(
            LocationProfile.organization_id == prog.organization_id,
            LocationProfile.location_id == location_id).first():
        raise HTTPException(status_code=404, detail="Location not found.")
    data = await file.read(MAX_ASSET_BYTES + 1)
    if len(data) > MAX_ASSET_BYTES:
        raise HTTPException(status_code=413, detail="Files up to 20 MB.")
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in ASSET_TYPES:
        raise HTTPException(status_code=415, detail="PDF, PNG, JPEG, WebP or SVG only.")
    org = db.query(Organization).filter(Organization.id == prog.organization_id).first()
    a = setup.store_asset(db, org, kind=kind, title=title.strip(), data=data, content_type=ctype,
                          filename=file.filename, category=category or None,
                          campaign_family=campaign_family or None, location_id=location_id or None,
                          activate=activate, uploaded_by=user.id)
    db.commit()
    return _asset_json(a)


class ActiveIn(BaseModel):
    active: bool


@router.post("/assets/{asset_id}/active")
def set_asset_active(asset_id: str, body: ActiveIn, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user)):
    prog = _program(db, user)
    _require_manager(db, user)
    a = db.query(ProgramAsset).filter(ProgramAsset.id == asset_id,
                                      ProgramAsset.organization_id == prog.organization_id).first()
    if a is None:
        raise HTTPException(status_code=404, detail="Asset not found.")
    setup.set_active(db, a, body.active)
    db.commit()
    return _asset_json(a)


@router.get("/assets/{asset_id}/preview")
def preview_asset(asset_id: str, db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_or_observer)):
    prog = _program(db, user)
    a = db.query(ProgramAsset).filter(ProgramAsset.id == asset_id,
                                      ProgramAsset.organization_id == prog.organization_id).first()
    if a is None:
        raise HTTPException(status_code=404, detail="Asset not found.")
    headers = {"Content-Disposition": content_disposition(a.filename or "asset", "inline"),
               "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=60",
               "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:"}
    return Response(content=a.data, media_type=a.content_type, headers=headers)


@public_router.get("/program-assets/{token}")
def hosted_asset(token: str, db: Session = Depends(get_db)):
    """The hosted link a family opens. Only an ACTIVE version is served."""
    a = db.query(ProgramAsset).filter(ProgramAsset.public_token == token,
                                      ProgramAsset.is_active.is_(True)).first()
    if a is None:
        raise HTTPException(status_code=404, detail="Not found.")
    headers = {"Content-Disposition": content_disposition(a.filename or "file", "inline"),
               "X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=300"}
    if a.content_type == "image/svg+xml":
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return Response(content=a.data, media_type=a.content_type, headers=headers)


# ── import (staging only) ────────────────────────────────────────────────────

@router.post("/import")
async def import_source(file: UploadFile = File(...), dry_run: bool = Form(True),
                        db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Dry-run (default) or STAGE a source file. Never creates leads or sends."""
    prog = _program(db, user)
    _require_manager(db, user)
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Files up to 10 MB.")
    org = db.query(Organization).filter(Organization.id == prog.organization_id).first()
    return importer.stage(db, org, content, filename=file.filename, dry_run=dry_run, actor_id=user.id)


# ── owner: configure a customer as a program ─────────────────────────────────

class GodSetupIn(BaseModel):
    organization_id: str
    name: str
    primary_contact_name: Optional[str] = None
    primary_contact_title: Optional[str] = None
    hero_title: Optional[str] = None
    hero_subtitle: Optional[str] = None
    location_names: List[str] = []


@god_router.post("/setup")
def god_setup(body: GodSetupIn, db: Session = Depends(get_db), god: User = Depends(require_god)):
    """Idempotent: program row, one location + profile per name, review bucket,
    inactive campaign families. Creates no users and sends nothing."""
    org = db.query(Organization).filter(Organization.id == body.organization_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")
    prog = setup.ensure_program(db, org, name=body.name,
                                primary_contact_name=body.primary_contact_name,
                                primary_contact_title=body.primary_contact_title,
                                hero_title=body.hero_title, hero_subtitle=body.hero_subtitle)
    profiles = setup.ensure_locations(db, org, god, body.location_names)
    fams = setup.ensure_campaign_families(db, org)
    try:
        _aliases.assign(db, prog)            # addresses only; unused until they receive mail
    except ValueError:
        pass
    db.commit()
    return {"program": _program_json(db, prog),
            "locations": sum(1 for p in profiles.values() if not p.is_review_bucket),
            "review_bucket": True, "campaign_families": len(fams)}
