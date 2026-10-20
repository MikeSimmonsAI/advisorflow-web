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
from app.services import test_records as _test_records
from app.services.programs import identity, importer, readiness_check, responses, setup

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
    if managers_only and not _sees_program(db, user, prog.organization_id):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return prog


def _sees_program(db: Session, user: User, org_id: Optional[str] = None) -> bool:
    """Managers work the program; a SPECTATOR of this workspace reads it too.
    A spectator's writes never get this far - deps.get_current_user refuses
    them for every route (services/spectator.py)."""
    if lead_scope.is_manager_here(user, db):
        return True
    from app.services import spectator as _spec
    return _spec.holds_spectator_seat(db, user, org_id or _org_id(db, user)) and _spec.is_spectator(db, user)


def _require_manager(db: Session, user: User) -> None:
    if not _sees_program(db, user):
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
        "website": p.website or _reference_website(p), "manager_name": p.manager_name,
        "website_is_reference": not p.website and bool(_reference_website(p)),
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
        "verification_held": _held_location(p),
    }


def _reference_website(p) -> Optional[str]:
    """The location's official public page from scripts/sci_campuses.csv
    (Website column), used only when no website has been entered on the
    profile. Read-only reference: nothing is written to the profile."""
    if p.is_review_bucket:
        return None
    try:
        from app.services.programs import campuses
        row = next((r for r in campuses.load_grouping() if r.get("Location") == p.official_name), None)
        return ((row or {}).get("Website") or "").strip() or None
    except Exception:                                   # noqa: BLE001
        return None


def _held_location(p) -> bool:
    """True for a location whose identity is unverified (today: Oaklawn).
    Nothing is sent for it; the screen shows it as held."""
    if p.is_review_bucket:
        return False
    try:
        from app.services import sms_programs as _sp
        return _sp.location_name_unverified(p.official_name)
    except Exception:                                   # noqa: BLE001
        return True


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _et_hold(prog):
    from app.services.programs.email_touches import held_providers
    return held_providers(prog)


def _program_json(db: Session, prog: OutreachProgram) -> dict:
    logo = db.query(ProgramAsset).filter(ProgramAsset.id == prog.logo_asset_id).first() \
        if prog.logo_asset_id else None
    return {
        "id": prog.id, "organization_id": prog.organization_id, "name": prog.name,
        "hero_title": prog.hero_title, "hero_subtitle": prog.hero_subtitle,
        "primary_contact_name": prog.primary_contact_name,
        "customer_identity_locked": identity.locked(prog),
        "send_via_mailbox": getattr(prog, "send_via_mailbox", None),
        "email_hold_providers": sorted(_et_hold(prog)),
        "booking_user_id": getattr(prog, "booking_user_id", None),
        "primary_contact_title": prog.primary_contact_title,
        "primary_contact_user_id": prog.primary_contact_user_id,
        "alert_email": prog.alert_email, "alert_phone": prog.alert_phone,
        "alert_title": getattr(prog, "alert_title", None),
        "ai_setter_mode": getattr(prog, "ai_setter_mode", None) or "off",
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
    manager = bool(prog) and _sees_program(db, user, prog.organization_id)
    return {"active": prog is not None and manager, "name": prog.name if prog else None}


@router.get("/me")
def program_me(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    return _program_json(db, _program(db, user, managers_only=False))


class SettingsIn(BaseModel):
    primary_contact_name: Optional[str] = None
    primary_contact_title: Optional[str] = None
    alert_email: Optional[str] = None
    alert_phone: Optional[str] = None
    alert_title: Optional[str] = None
    management_recipients: Optional[List[dict]] = None
    hot_sla_minutes: Optional[int] = None
    reply_instructions_sms: Optional[str] = None
    reply_instructions_email: Optional[str] = None
    staff_alerts_enabled: Optional[bool] = None
    alias_mode: Optional[str] = None
    mailbox_folder_path: Optional[str] = None
    customer_identity_locked: Optional[bool] = None
    placement_seed_addresses: Optional[dict] = None
    send_via_mailbox: Optional[str] = None
    email_hold_providers: Optional[List[str]] = None
    booking_user_id: Optional[str] = None
    ai_setter_mode: Optional[str] = None


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
    if "ai_setter_mode" in data:
        m = (data.get("ai_setter_mode") or "off").strip().lower()
        if m not in ("off", "draft", "auto"):
            raise HTTPException(status_code=422, detail="ai_setter_mode must be off, draft or auto.")
        if m != "off" and getattr(user, "role", None) != "god_admin":
            # Letting AI answer families is the owner's decision alone.
            raise HTTPException(status_code=403, detail="Only the owner can switch the appointment setter on.")
        data["ai_setter_mode"] = m
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
    if "booking_user_id" in data:
        uid = (data.pop("booking_user_id") or "").strip() or None
        if uid and db.query(User).filter(User.id == uid, User.is_active.is_(True)).first() is None:
            raise HTTPException(status_code=422, detail="Booking user not found or not active.")
        prog.booking_user_id = uid
    if "email_hold_providers" in data:
        from app.services.programs.email_touches import PROVIDER_LABELS
        held = sorted({str(x).strip().lower() for x in (data.pop("email_hold_providers") or []) if str(x).strip()})
        bad = [h for h in held if h not in PROVIDER_LABELS]
        if bad:
            raise HTTPException(status_code=422, detail="Email hold providers: %s." % ", ".join(PROVIDER_LABELS))
        prog.email_hold_providers = json.dumps(held) if held else None
    if "send_via_mailbox" in data:
        addr = (data.pop("send_via_mailbox") or "").strip().lower()
        if addr:
            from app.services.inbound_mailbox_service import sending_mailbox
            if sending_mailbox(db, addr) is None:
                raise HTTPException(status_code=422, detail="%s is not a connected Microsoft 365 mailbox. "
                                    "Connect it first in God Mode > Email Diagnostics." % addr)
        prog.send_via_mailbox = addr or None
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
    # #234 (Oct 19): what this location really offers a family who can't or
    # won't come in. Stored in brand_settings; read by the appointment setter.
    home_visits: Optional[bool] = None
    offer_phone_alternative: Optional[bool] = None


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
    offers = {k: data.pop(k) for k in ("home_visits", "offer_phone_alternative") if k in data}
    if offers:
        bs = identity.brand_settings(p)
        bs.update({k: bool(v) for k, v in offers.items() if v is not None})
        p.brand_settings = json.dumps(bs)
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

def _survey_alerts(db, org_id, location_id):
    try:
        from app.services import survey_results
        return survey_results.alert_count(db, org_id, location_id)
    except Exception:
        return 0


LEAD_FILE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/heic", "text/plain",
                   "application/msword",
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
MAX_LEAD_FILE_BYTES = 15 * 1024 * 1024


def _program_lead(db: Session, prog, lead_id: str) -> Lead:
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == prog.organization_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return lead


def _file_json(f) -> dict:
    return {"id": f.id, "filename": f.filename, "content_type": f.content_type, "size_bytes": f.size_bytes,
            "note": f.note, "created_at": f.created_at.isoformat() + "Z" if f.created_at else None}


@router.get("/leads/{lead_id}/files")
def program_lead_files(lead_id: str, db: Session = Depends(get_db),
                       user: User = Depends(require_tenant_or_observer)):
    from app.models.program_models import ProgramLeadFile
    prog = _program(db, user)
    _program_lead(db, prog, lead_id)
    from sqlalchemy.orm import defer
    rows = (db.query(ProgramLeadFile).options(defer(ProgramLeadFile.data))
            .filter(ProgramLeadFile.organization_id == prog.organization_id, ProgramLeadFile.lead_id == lead_id)
            .order_by(ProgramLeadFile.created_at.desc()).all())
    return {"items": [_file_json(f) for f in rows]}


@router.post("/leads/{lead_id}/files")
async def program_lead_file_upload(lead_id: str, file: UploadFile = File(...), note: Optional[str] = Form(None),
                                   db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    from app.models.program_models import ProgramLeadFile
    prog = _program(db, user)
    _program_lead(db, prog, lead_id)
    data = await file.read(MAX_LEAD_FILE_BYTES + 1)
    if len(data) > MAX_LEAD_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Files up to 15 MB.")
    if not data:
        raise HTTPException(status_code=422, detail="That file is empty.")
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in LEAD_FILE_TYPES:
        raise HTTPException(status_code=415, detail="PDF, Word, text or picture files only.")
    f = ProgramLeadFile(organization_id=prog.organization_id, lead_id=lead_id,
                        filename=(file.filename or "file")[:200], content_type=ctype, size_bytes=len(data),
                        data=data, note=(note or "").strip()[:500] or None, uploaded_by=user.id)
    db.add(f)
    db.commit()
    return _file_json(f)


@router.get("/leads/{lead_id}/files/{file_id}")
def program_lead_file_download(lead_id: str, file_id: str, db: Session = Depends(get_db),
                               user: User = Depends(require_tenant_or_observer)):
    from app.models.program_models import ProgramLeadFile
    prog = _program(db, user)
    f = (db.query(ProgramLeadFile).filter(ProgramLeadFile.id == file_id, ProgramLeadFile.lead_id == lead_id,
                                          ProgramLeadFile.organization_id == prog.organization_id).first())
    if f is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return Response(content=f.data, media_type=f.content_type,
                    headers={"Content-Disposition": content_disposition(f.filename, "attachment"),
                             "X-Content-Type-Options": "nosniff"})


@router.get("/appointments")
def program_appointments(when: str = Query("upcoming"), location_id: Optional[str] = Query(None),
                         lead_id: Optional[str] = Query(None),
                         db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Booked visits for the program's families: upcoming (default), past or all."""
    from app.models.models import BookingLink
    prog = _program(db, user)
    now = datetime.utcnow()
    q = (db.query(BookingLink, Lead).join(Lead, Lead.id == BookingLink.lead_id)
         .filter(Lead.organization_id == prog.organization_id, BookingLink.booked_time.isnot(None),
                 Lead.is_test.isnot(True),
                 BookingLink.status.in_(("booked", "confirmed", "cancelled", "completed"))))
    if lead_id:
        q = q.filter(Lead.id == lead_id)
    if when == "upcoming":
        q = q.filter(BookingLink.booked_time >= now - timedelta(hours=2)).order_by(BookingLink.booked_time.asc())
    elif when == "past":
        q = q.filter(BookingLink.booked_time < now - timedelta(hours=2)).order_by(BookingLink.booked_time.desc())
    else:
        q = q.order_by(BookingLink.booked_time.desc())
    rows = q.limit(300).all()
    lead_ids = list({l.id for _b, l in rows})
    loc_of = {}
    if lead_ids:
        for r in (db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == prog.organization_id,
                                                        ProgramSourceRecord.lead_id.in_(lead_ids)).all()):
            loc_of.setdefault(r.lead_id, r.location_id)
    names = {p.location_id: p.official_name for p in db.query(LocationProfile)
             .filter(LocationProfile.organization_id == prog.organization_id).all()}
    items = []
    for b, l in rows:
        loc = loc_of.get(l.id)
        if location_id and loc != location_id:
            continue
        items.append({"id": b.id, "lead_id": l.id,
                      "name": " ".join(x for x in (l.first_name, l.last_name) if x) or "(no name)",
                      "phone": l.phone, "email": l.email, "location_id": loc, "location": names.get(loc),
                      "when": b.booked_time.isoformat() + "Z", "status": b.status,
                      "label": getattr(b, "appt_label", None), "minutes": getattr(b, "appt_duration", None)})
    return {"items": items, "when": when}


@router.get("/reach/{lead_id}")
def program_reach(lead_id: str, db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_or_observer)):
    """'Reaches by' and 'Last sent' for one person's record."""
    prog = _program(db, user)
    from app.services.programs import send_approval
    out = send_approval.reach_for_lead(db, prog, lead_id)
    if out is None:
        raise HTTPException(status_code=404, detail="Not on the program.")
    return out


@router.post("/line-check/run")
def program_line_check(limit: int = Query(100, ge=1, le=200), db: Session = Depends(get_db),
                       user: User = Depends(require_tenant_user)):
    """Mobile/landline lookup for not-yet-checked contacts (Twilio, ~$0.008 each)."""
    prog = _program(db, user)
    _require_manager(db, user)
    from app.services.programs import line_check
    return line_check.run_for_org(db, prog.organization_id, limit=limit)


@router.get("/surveys")
def program_surveys(location_id: Optional[str] = Query(None), db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_or_observer)):
    """Survey totals, the latest answers, and 1-3 star alerts not yet handled."""
    prog = _program(db, user)
    from app.services import survey_results
    return survey_results.for_org(db, prog.organization_id, location_id)


@router.post("/surveys/{response_id}/handled")
def program_survey_handled(response_id: str, db: Session = Depends(get_db),
                           user: User = Depends(require_tenant_user)):
    prog = _program(db, user)
    from app.services import survey_results
    out = survey_results.mark_handled(db, prog.organization_id, response_id, user.id)
    if out is None:
        raise HTTPException(status_code=404, detail="Survey answer not found.")
    return out


@router.get("/dashboard")
def dashboard(location_id: Optional[str] = Query(None), db: Session = Depends(get_db),
              user: User = Depends(require_tenant_or_observer)):
    prog = _program(db, user)
    org_id = prog.organization_id
    profiles = _profiles(db, org_id)
    if location_id and location_id not in {p.location_id for p in profiles}:
        raise HTTPException(status_code=404, detail="Location not found.")

    recs_q = _test_records.exclude_test_program_records(
        db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org_id))
    resp_q = _test_records.exclude_test_responses(
        db.query(ProgramResponse).filter(ProgramResponse.organization_id == org_id))
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
            "survey_alerts": _survey_alerts(db, org_id, location_id),
        },
        "recent_conversations": recent,
        "location_performance": loc_perf,
        "campaign_performance": campaign_perf,
        "automation": {
            "active_cadences": cad.get("active", 0), "paused_cadences": cad.get("paused", 0),
            # Everyone whose texts stopped because they answered (paused, or
            # finished after a reply) - not only the ones still "paused".
            "stopped_by_reply": _stopped_by_reply(db, resps),
            "awaiting_reply": max(0, len({r.lead_id for r in recs if r.lead_id})
                                  - len({r.lead_id for r in resps})),
            "staged_not_live": sum(1 for r in recs if not r.lead_id),
            "active_campaign_families": sum(1 for f in fams.values() if f.is_active),
            "suppressed": sum(1 for r in resps if r.response_class == "opt_out"),
            "review_needed": sum(1 for r in recs if r.needs_data_review or r.duplicate_review_reason
                                 or r.location_status == "location_review"),
        },
        "reporting": _reporting(db, recs, resps, prog),
        "readiness": readiness(db, prog),
        "readiness_test": _readiness_summary(org_id, db),
        "viewer_is_manager": _sees_program(db, user),
    }


def _stopped_by_reply(db: Session, resps) -> int:
    ids = list({r.lead_id for r in resps if r.lead_id and r.response_class != "opt_out"})
    if not ids:
        return 0
    return sum(1 for (st,) in db.query(CadenceState.status).filter(CadenceState.lead_id.in_(ids)).all()
               if str(getattr(st, "value", st)) != "active")


def _reporting(db: Session, recs, resps, prog=None) -> dict:
    """Delivery and handling numbers. SENT is never reported as DELIVERED.

    Email sent from a Microsoft 365 mailbox (send_via_mailbox) has NO delivery
    or open events at all, so those two read "Not tracked" (None) - a 0 there
    read as "nothing was delivered" (Oct 14). What IS known is shown instead:
    sent, bounced (from the bounce notices), and reached = sent - bounced."""
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
        # Owner test contacts (booking preview) never count as appointments.
        from app.models.models import Lead as _L
        test_ids = {i for (i,) in db.query(_L.id).filter(_L.id.in_(lead_ids), _L.is_test.is_(True)).all()}
        real_ids = [i for i in lead_ids if i not in test_ids]
        out["appointments"] = (db.query(func.count(BookingLink.id))
                               .filter(BookingLink.lead_id.in_(real_ids or ["-"]),
                                       BookingLink.status.in_(("booked", "confirmed"))).scalar() or 0)
    if prog is not None and getattr(prog, "send_via_mailbox", None):
        out["email"]["delivered"] = None
        out["email"]["complained"] = None
        out["email"]["reached"] = max(0, out["email"]["sent"] - out["email"]["bounced"])
        out["email"]["note"] = ("Email goes out from the %s mailbox. Microsoft does not report delivery, so "
                                "'Not bounced' is every email sent minus the bounce notices that came back. "
                                "Opens are counted from Oct 14 on; Outlook hides images until the reader "
                                "allows them, so real opens are higher." % prog.send_via_mailbox)
    # A car's "I'm driving" auto-text is not a reply from a person.
    out["email"]["replied"] = sum(1 for r in resps if r.channel == "email" and r.response_class != "auto_reply")
    out["sms"]["replied"] = sum(1 for r in resps if r.channel == "sms" and r.response_class != "auto_reply")
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
        *_sms_items(db, prog.organization_id, sms_number),
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


def _sms_items(db: Session, org_id: str, org_number: Optional[str]) -> list:
    """The program's texting number and where replies to it land. A program
    that texts from the shared toll-free line (SCI) has no organization number
    by design: its number is the toll-free line on the platform account."""
    import os
    from app.services import sms_campaigns as _sc, sms_programs as _sp
    if not _sp.is_sci_org(db, org_id):
        return [{"key": "sms_number", "ok": bool(org_number),
                 "detail": org_number or "no organization Twilio number configured"},
                _sms_routing_item(db, org_id, org_number)]
    from app.models.telephony_models import PhoneNumber
    from app.services.programs import regional_pools as rp
    tf = _sp.sci_sender_number()
    entry = _sp.sci_sender_entry()
    creds = bool((os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
                 and (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip())
    approved = _sc.is_approved(entry)
    gaps = [g for g, bad in (("platform Twilio account not configured on this server", not creds),
                             ("not approved for sending", not approved)) if bad]
    scope = ", ".join((entry or {}).get("approved_scope") or []) or "unknown"
    num = {"key": "sms_number", "ok": not gaps,
           "detail": "%s toll-free line on the platform account (approved scope: %s)%s"
                     % (tf, scope, (" - " + "; ".join(gaps)) if gaps else "")}
    row = db.query(PhoneNumber).filter(PhoneNumber.e164 == tf).first()
    routed = (row is not None and row.is_active and row.organization_id == org_id
              and rp.pool_for_phone_number(row) == rp.TOLL_FREE_POOL)
    route = {"key": "sms_reply_routing", "ok": routed,
             "detail": ("replies to %s route to each contact's own location; unknown senders go to "
                        "Location Review" % tf) if routed else
                       ("%s is not registered to this workspace as its toll-free line, so replies "
                        "cannot be routed here" % tf)}
    return [num, route]


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
                   include_test: bool = Query(False),
                   db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    prog = _program(db, user)
    q = db.query(ProgramResponse).filter(ProgramResponse.organization_id == prog.organization_id)
    if not include_test:
        q = _test_records.exclude_test_responses(q)
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


@router.get("/campuses")
def campus_plan(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """One local number per PHYSICAL CAMPUS: entities served, contacts, number."""
    from app.services.programs import campuses
    prog = _program(db, user)
    rows = campuses.plan(db, prog.organization_id)
    return {"campuses": len(rows), "locations": sum(len(r["entities"]) for r in rows), "items": rows}


@router.get("/voice")
def voice_report(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_or_observer)):
    """Calls, answered, missed, voicemails and transcripts per location."""
    from app.services.programs import program_voice
    prog = _program(db, user)
    since = datetime.utcnow() - timedelta(days=days)
    return {"days": days, "locations": program_voice.location_report(db, prog.organization_id, since)}


@router.get("/alerts")
def list_alerts(limit: int = Query(50, ge=1, le=200), include_test: bool = Query(False),
                db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    prog = _program(db, user)
    _require_manager(db, user)
    q = db.query(ProgramAlert).filter(ProgramAlert.organization_id == prog.organization_id)
    if not include_test:
        q = _test_records.exclude_test_alerts(q)
    rows = (q.order_by(ProgramAlert.created_at.desc()).limit(limit).all())
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
    q = _test_records.exclude_test_program_records(
        db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == prog.organization_id))
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
        "hide_location": bool(getattr(r, "hide_location", False)),
        "contact_master_key": r.contact_master_key, "link_status": r.link_status,
        "link_reason": r.link_reason, "duplicate_review_reason": r.duplicate_review_reason,
        "data_note_flags": json.loads(r.data_note_flags or "[]"), "lead_id": r.lead_id,
        "on_hold": bool(r.on_hold), "hold_reason": r.hold_reason, "held_at": _iso(r.held_at),
    } for r in rows]}


class AssignLocationIn(BaseModel):
    location_id: str
    hide_location: Optional[bool] = None    # True: contact them without naming the location


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
    if body.hide_location is not None:
        rec.hide_location = bool(body.hide_location)
    db.commit()
    return {"id": rec.id, "location": prof.official_name, "location_status": rec.location_status,
            "hide_location": bool(rec.hide_location)}


class HideLocationIn(BaseModel):
    on: bool


@router.post("/records/{record_id}/hide-location")
def set_hide_location(record_id: str, body: HideLocationIn, db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user)):
    """Contact this person without naming any location: texts, emails, the
    sender name, booking page and survey all say "Family Services" instead.
    The real location stays on the record for routing and reports."""
    prog = _program(db, user)
    _require_manager(db, user)
    rec = db.query(ProgramSourceRecord).filter(ProgramSourceRecord.id == record_id,
                                               ProgramSourceRecord.organization_id == prog.organization_id).first()
    if rec is None:
        raise HTTPException(status_code=404, detail="Record not found.")
    rec.hide_location = bool(body.on)
    db.commit()
    return {"id": rec.id, "hide_location": bool(rec.hide_location)}


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


# ── controlled test console (synthetic only, nothing is sent) ────────────────

_LAST_READINESS_RUN: dict = {}      # org id -> latest run; in memory, reset on restart


def _readiness_summary(org_id: str, db: Session = None) -> dict:
    last = _LAST_READINESS_RUN.get(org_id)
    v = readiness_check.verdict(last, readiness_check.live_gates(db, org_id))
    return {**v, "pass_count": last["pass_count"] if last else None,
            "fail_count": last["fail_count"] if last else None, "ran_at": last["ran_at"] if last else None}


@router.get("/readiness-test")
def readiness_test_console(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    prog = _program(db, user)
    return readiness_check.console(_LAST_READINESS_RUN.get(prog.organization_id), db, prog.organization_id)


@router.post("/readiness-test/run")
def run_readiness_test(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Run every synthetic check. No database rows read, no message, call or
    provider request. Managers only."""
    prog = _program(db, user)
    _LAST_READINESS_RUN[prog.organization_id] = readiness_check.run_all()
    return readiness_check.console(_LAST_READINESS_RUN[prog.organization_id], db, prog.organization_id)


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
    lead_id = rec.lead_id
    if not body.on_hold and not rec.lead_id:
        lead_id = _promote_released(db, prog, user, rec)
    return {"id": rec.id, "on_hold": rec.on_hold, "hold_reason": rec.hold_reason, "lead_id": lead_id}


def _promote_released(db: Session, prog, user: User, rec) -> Optional[str]:
    """A record released from hold that was never made a live contact (it was
    held when its file was loaded) becomes one now, exactly as the load would
    have done: a new lead, no consent, no campaign, nothing sent. Without this
    a released person never shows up in Choose & Send. Its number gets the
    usual mobile/landline check so a landline is never texted."""
    from app.services.programs import line_check, promote
    org = db.query(Organization).filter(Organization.id == prog.organization_id).first()
    try:
        promote.promote(db, org, user, apply=True, only_keys={rec.contact_master_key or rec.source_lead_id})
    except HTTPException:
        db.rollback()
        return None
    db.refresh(rec)
    if rec.lead_id:
        try:
            line_check.run_for_org(db, prog.organization_id, limit=5)
        except Exception:                                # noqa: BLE001
            db.rollback()
    return rec.lead_id


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


# ── Choose & Send: a manager picks exactly who gets outreach ───────────────

class PickIn(BaseModel):
    lead_ids: List[str]
    confirm: Optional[str] = None
    mode: Optional[str] = None
    start_at: Optional[str] = None          # ISO time; slow drip starts then
    spacing_minutes: Optional[int] = None   # minutes between each contact's start


@router.get("/send/contacts")
def send_contacts(search: Optional[str] = Query(None), location_id: Optional[str] = Query(None),
                  family: Optional[str] = Query(None), show: str = Query("all"),
                  limit: int = Query(100, ge=1, le=300), offset: int = Query(0, ge=0),
                  db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Every loaded contact, one row per person, approved or not. Reads only."""
    from app.services.programs import send_approval
    prog = _program(db, user)
    return send_approval.list_contacts(db, prog, search=search or "", location_id=location_id or "",
                                       family=family or "", show=show, limit=limit, offset=offset)


@router.post("/send/preview")
def send_preview(body: PickIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Exactly what the first text says for each ticked contact. Sends nothing."""
    from app.services.programs import send_approval
    prog = _program(db, user)
    if len(body.lead_ids or []) > 300:
        raise HTTPException(status_code=422, detail="Preview at most 300 at a time.")
    return send_approval.preview(db, prog, body.lead_ids)


@router.post("/send/approve")
def send_approve(body: PickIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """EXPLICIT HUMAN ACTION: approve the ticked contacts and start their
    outreach. confirm must read "SEND <n>" for the n contacts ticked."""
    from app.services.programs import send_approval
    prog = _program(db, user)
    _require_manager(db, user)
    ids = list(dict.fromkeys(i for i in (body.lead_ids or []) if i))
    if not ids:
        raise HTTPException(status_code=422, detail="Tick at least one contact.")
    if len(ids) > send_approval.MAX_PER_APPROVAL:
        raise HTTPException(status_code=422, detail="Approve at most %d at a time." % send_approval.MAX_PER_APPROVAL)
    if (body.confirm or "").strip().upper() != "SEND %d" % len(ids):
        raise HTTPException(status_code=422, detail="Confirm with SEND %d." % len(ids))
    if body.mode and body.mode not in send_approval.MODES:
        raise HTTPException(status_code=422, detail="Mode must be text_email, email_only or call.")
    start = None
    if body.start_at:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        try:
            start = _dt.fromisoformat(body.start_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail="Start time is not a valid date/time.")
        if start.tzinfo is not None:
            start = start.astimezone(_tz.utc).replace(tzinfo=None)
        now = _dt.utcnow()
        if start <= now:
            start = None                         # in the past: start right away
        elif start > now + _td(days=14):
            raise HTTPException(status_code=422, detail="Start within the next 14 days.")
    spacing = max(0, min(240, int(body.spacing_minutes or 0)))
    return send_approval.approve(db, prog, user, ids, mode=body.mode or "text_email",
                                 start_at=start, spacing_minutes=spacing)


class DailyBatchIn(BaseModel):
    count: int
    spacing_minutes: Optional[int] = None
    start: Optional[str] = None
    days: Optional[str] = None
    confirm: Optional[str] = None


@router.get("/daily-batch")
def get_daily_batch(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    from app.services.programs import daily_batch
    prog = _program(db, user)
    out = daily_batch.status(prog)
    if prog.daily_batch_by:
        u = db.query(User).filter(User.id == prog.daily_batch_by).first()
        out["set_by_name"] = u.full_name if u else None
    return out


@router.post("/daily-batch")
def set_daily_batch(body: DailyBatchIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """EXPLICIT HUMAN ACTION: a standing approval. Every sending day the
    program approves the next `count` people in this person's name. Turning it
    on (or raising it) needs confirm "DAILY <count>"; count 0 switches it off."""
    import re as _re
    from app.services.programs import daily_batch
    prog = _program(db, user)
    _require_manager(db, user)
    n = int(body.count or 0)
    if n < 0 or n > daily_batch.MAX_PER_DAY:
        raise HTTPException(status_code=422, detail="Between 0 and %d people a day." % daily_batch.MAX_PER_DAY)
    if n > 0 and (body.confirm or "").strip().upper() != "DAILY %d" % n:
        raise HTTPException(status_code=422, detail="Confirm with DAILY %d." % n)
    if body.start is not None and not _re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", body.start.strip()):
        raise HTTPException(status_code=422, detail="Start time must look like 09:00.")
    if body.days is not None and not _re.fullmatch(r"[1-7]{1,7}", body.days.strip()):
        raise HTTPException(status_code=422, detail="Days must be digits 1 (Mon) to 7 (Sun).")
    prog.daily_batch_count = n
    if body.spacing_minutes is not None:
        prog.daily_batch_spacing = max(1, min(240, int(body.spacing_minutes)))
    if body.start is not None:
        prog.daily_batch_start = body.start.strip()
    if body.days is not None:
        prog.daily_batch_days = "".join(sorted(set(body.days.strip())))
    prog.daily_batch_by = user.id if n > 0 else prog.daily_batch_by
    from app.routers.audit_log_router import log_action
    log_action(db, prog.organization_id, user.id, action="program.daily_batch_set",
               target_type="outreach_program", target_id=prog.id, details=daily_batch.status(prog))
    db.commit()
    return daily_batch.status(prog)


class RepaceIn(BaseModel):
    approved_since: str
    confirm: Optional[str] = None


@router.post("/daily-batch/repace")
def repace_daily_batch(body: RepaceIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Spread a batch that was approved after hours across the next sending
    day (confirm "REPACE"). Changes start times only."""
    from app.services.programs import daily_batch
    prog = _program(db, user)
    _require_manager(db, user)
    if (body.confirm or "").strip().upper() != "REPACE":
        raise HTTPException(status_code=422, detail="Confirm with REPACE.")
    try:
        since = datetime.fromisoformat(body.approved_since.replace("Z", "")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(status_code=422, detail="approved_since must be an ISO time (UTC).")
    return daily_batch.repace(db, prog, user, since)


@router.post("/send/unapprove")
def send_unapprove(body: PickIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Take approval back: no more automated messages to these contacts."""
    from app.services.programs import send_approval
    prog = _program(db, user)
    _require_manager(db, user)
    return send_approval.unapprove(db, prog, user, body.lead_ids)


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
    for k in ("email_body_template",):
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
    sms = identity.render(f.sms_template, dict(fields, reply_instructions=prog.reply_instructions_sms or "",
                                                   call_line=identity._call_line(prof.facility_phone)))
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


@public_router.get("/planning-guide")
def planning_guide_page():
    """The public pre-planning guide SCI texts link to. No auth, no script, no
    tracking; served by the platform so the link works on every deployment."""
    from app.services.programs.planning_guide_page import HTML
    return Response(content=HTML, media_type="text/html; charset=utf-8", headers={
        "Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"})


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


# ── owner: load a source file into production (the server-side version of
#    scripts/program_setup.py --stage --hold-reviews --promote) ──────────────

def _excel_to_csv(content: bytes, filename: Optional[str]) -> bytes:
    """Kerry's lists come as Excel as often as CSV. Read the first sheet and
    hand the importer the same CSV it has always read."""
    name = (filename or "").lower()
    if not (name.endswith(".xlsx") or name.endswith(".xls")):
        return content
    import io as _io
    import pandas as _pd
    try:
        frame = _pd.read_excel(_io.BytesIO(content), dtype=str).fillna("")
    except Exception:                                    # noqa: BLE001
        raise HTTPException(status_code=422, detail="That Excel file could not be read. Save it as .xlsx or .csv and try again.")
    return frame.to_csv(index=False).encode("utf-8")


def _load_source(db: Session, org, content: bytes, filename: Optional[str], apply: bool, actor: User) -> dict:
    """Check (apply=False) or load (apply=True) a lead file: stage every row,
    HOLD every record still in a review state, promote the clean contacts to
    live leads, then check each new number mobile vs landline. Never records
    consent, never enrols anyone in a campaign, never sends anything."""
    from app.services.programs import holds, promote
    content = _excel_to_csv(content, filename)
    staged = importer.stage(db, org, content, filename=filename, dry_run=not apply, actor_id=actor.id)
    out = {"apply": apply, "stage": staged["summary"], "run_id": staged["run_id"]}
    if not apply or staged["summary"].get("refused"):
        out["promotion_plan"] = {k: v for k, v in promote.plan(db, org).items() if k != "groups"}
        return out
    held = holds.hold_open_reviews(db, org.id, actor.id)
    db.commit()
    out["held"] = {k: v for k, v in held.items() if k != "source_lead_ids"}
    try:
        out["promotion"] = promote.promote(db, org, actor, apply=True)
    except HTTPException:
        db.rollback()
        raise
    # Mobile or landline for every new number, right away, so a landline is
    # never texted (Twilio Lookup, about $0.008 a number; approved by Mike).
    try:
        from app.services.programs import line_check
        out["line_check"] = line_check.run_for_org(db, org.id, limit=2000)
    except Exception as exc:                             # noqa: BLE001
        db.rollback()
        out["line_check"] = {"error": str(exc)[:200]}
    try:
        from app.routers.audit_log_router import log_action
        log_action(db, org.id, actor.id, "program.source_loaded", "organization", org.id,
                   details={"file": filename, "staged": staged["summary"].get("staged"),
                            "held": held.get("held"), "created": out["promotion"].get("created")})
    except Exception:                                    # noqa: BLE001
        db.rollback()
    return out


@router.post("/leads/import")
async def import_leads(file: UploadFile = File(...), apply: bool = Form(False),
                       db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """IMPORT LEADS INSIDE THE WORKSPACE (managers). apply=False checks the file
    and changes nothing; apply=True loads it live. Loading never sends."""
    prog = _program(db, user)
    _require_manager(db, user)
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Files up to 10 MB.")
    org = db.query(Organization).filter(Organization.id == prog.organization_id).first()
    return _load_source(db, org, content, file.filename, apply, user)


@god_router.post("/{organization_id}/load-source")
async def god_load_source(organization_id: str, file: UploadFile = File(...),
                          apply: bool = Form(False),
                          db: Session = Depends(get_db), god: User = Depends(require_god)):
    """Platform owner only - same as /program/leads/import for any workspace."""
    from app.models.program_models import OutreachProgram
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == organization_id).first() \
        if org else None
    if org is None or prog is None:
        raise HTTPException(status_code=404, detail="No outreach program for that organization.")
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Files up to 10 MB.")
    return _load_source(db, org, content, file.filename, apply, god)


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


# ── EACH WORKSPACE'S OWN TEXT SWITCH ────────────────────────────────────────
class TextSwitchIn(BaseModel):
    on: bool


@router.get("/text-switch")
def get_text_switch(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Is this workspace's automated texting on? Only the owner account can change it."""
    from app.services import cadence_service as _cs
    prog = _program(db, user)
    return {"on": _cs.org_sending_enabled(db, prog.organization_id),
            "can_change": getattr(user, "role", None) == "god_admin"}


@router.post("/text-switch")
def set_text_switch(body: TextSwitchIn, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user)):
    from app.services import cadence_service as _cs
    prog = _program(db, user)
    if getattr(user, "role", None) != "god_admin":
        raise HTTPException(status_code=403, detail="Only the owner can switch automated texts on or off.")
    on = _cs.set_org_sending(db, prog.organization_id, body.on, by=user.id)
    return {"on": on, "can_change": True}


@router.get("/booking-activity")
def booking_activity(days: int = 30, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user)):
    """Who opened their booking page, looked at times and booked - and when."""
    from app.services.programs import booking_events
    prog = _program(db, user)
    return booking_events.summary(db, prog.organization_id, days=max(1, min(int(days), 365)))


# ── A TEST BOOKING LINK, so the owner can see exactly what a family sees ────
class TestLinkIn(BaseModel):
    location_id: Optional[str] = None


@router.post("/booking-test-link")
def booking_test_link(body: TestLinkIn, db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user)):
    """A real booking link for a TEST contact (is_test, on hold): it opens the
    same page a family gets, books onto the same calendar, and that contact is
    never texted or emailed by anything. Owner only."""
    import json as _json
    from app.models.models import Lead
    from app.models.program_models import ProgramSourceRecord
    prog = _program(db, user)
    if getattr(user, "role", None) != "god_admin":
        raise HTTPException(status_code=403, detail="Owner only.")
    org_id = prog.organization_id
    homes = [p for p in _profiles(db, org_id) if not p.is_review_bucket]
    prof = next((p for p in homes if p.location_id == body.location_id), None) or (homes[0] if homes else None)
    if prof is None:
        raise HTTPException(status_code=422, detail="This workspace has no locations.")
    sid = "TEST-BOOKING-%s" % prof.location_id[:8]
    rec = (db.query(ProgramSourceRecord)
           .filter(ProgramSourceRecord.organization_id == org_id, ProgramSourceRecord.source_lead_id == sid).first())
    lead = db.query(Lead).filter(Lead.id == rec.lead_id).first() if rec and rec.lead_id else None
    if lead is None:
        lead = Lead(organization_id=org_id, assigned_to_id=getattr(prog, "booking_user_id", None) or user.id,
                    first_name=(user.full_name or "Test").split(" ")[0], last_name="(TEST)",
                    email=None, phone=None, status="new", is_test=True)
        db.add(lead)
        db.flush()
    if rec is None:
        rec = ProgramSourceRecord(organization_id=org_id, source_lead_id=sid,
                                  raw_json=_json.dumps({"note": "test booking contact"}))
        db.add(rec)
    rec.lead_id = lead.id
    rec.location_id = prof.location_id
    rec.location_status = "mapped"
    rec.on_hold = True
    rec.hold_reason = "TEST contact for previewing the booking page - never contacted"
    db.commit()
    url = identity.program_booking_url(db, lead)
    db.commit()          # the short code is only flushed when it is minted
    if not url:
        raise HTTPException(status_code=422, detail="No booking calendar is set for this workspace.")
    return {"url": url, "location": prof.official_name}


# ── SELF-CHECK: is the program doing what the plan says? ───────────────────

class AlertPreviewIn(BaseModel):
    response_id: str


@router.post("/alerts/preview")
def alert_preview(body: AlertPreviewIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Send ONE alert for this reply, in the current format, to the program
    OWNER only (alert phone + alert email) so it can be approved. Nobody else."""
    from app.models.program_models import ProgramResponse
    from app.services.programs import responses as R
    prog = _program(db, user)
    _require_manager(db, user)
    resp = db.query(ProgramResponse).filter(ProgramResponse.id == body.response_id,
                                            ProgramResponse.organization_id == prog.organization_id).first()
    if resp is None:
        raise HTTPException(status_code=404, detail="Reply not found.")
    kind = "hot" if resp.response_class == "hot" else resp.response_class
    content = R.alert_content(db, prog, resp, kind)
    sent = {}
    for ch, to in (("sms", prog.alert_phone), ("email", prog.alert_email)):
        if to:
            ok, why = R._deliver_staff_alert(db, prog, ch, to, resp.summary or "", kind=kind, response_id=resp.id)
            sent[ch] = {"to": to, "ok": ok, "error": why}
    return {"sent": sent, "preview": content}


def _mask(v: Optional[str], keep: int = 4) -> str:
    v = str(v or "")
    return ("*" * max(0, len(v) - keep)) + v[-keep:] if v else ""


@router.get("/alerts/sms-check")
def alert_sms_check(hours: int = Query(6, ge=1, le=72), db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user)):
    """PROOF, from Twilio itself: every text the SCI line sent to a staff
    alert number in the last N hours, with Twilio's own status and error.
    Also refreshes stored alert records from those statuses."""
    from datetime import timedelta as _td
    from app.models.program_models import ProgramAlert
    from app.services import sms_programs as _sci
    from app.services.programs import responses as R
    prog = _program(db, user)
    _require_manager(db, user)
    staff = {prog.alert_phone} | {m.get("phone") for m in R._managers(prog)}
    staff = {p for p in staff if p}
    client, from_ = _sci.sci_sender()
    since = datetime.utcnow() - _td(hours=hours)
    rows = []
    for to in sorted(staff):
        for m in client.messages.list(to=to, date_sent_after=since, limit=50):
            rows.append({"to": _mask(to), "from": _mask(getattr(m, "from_", None)), "sid": _mask(m.sid, 6),
                         "status": m.status, "error_code": m.error_code, "error": m.error_message,
                         "sent_at": m.date_sent.isoformat() if m.date_sent else None,
                         "body_start": (m.body or "")[:40]})
            if m.status:
                R.apply_sms_receipt(db, m.sid, m.status, str(m.error_code) if m.error_code else None,
                                    m.error_message)
    db.commit()
    return {"sender": _mask(from_), "recipients": [_mask(p) for p in sorted(staff)], "messages": rows}


class AlertTestSmsIn(BaseModel):
    message: str
    target: str = "owner"          # owner | team (each team member's phone, once)


@router.post("/alerts/test-sms")
def alert_test_sms(body: AlertTestSmsIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """ONE plain test text, exactly as given, to the program OWNER's alert
    phone only (never the team, never a family), from the same SCI line staff
    alerts use. Recorded as a program alert with Twilio's SID; its delivery
    report updates the record. Read the result with GET /alerts/sms-check."""
    from app.models.program_models import ProgramAlert
    from app.services.programs import responses as R
    prog = _program(db, user)
    _require_manager(db, user)
    text = (body.message or "").strip()
    if not text or len(text) > 300:
        raise HTTPException(status_code=422, detail="Message must be 1-300 characters.")
    if body.target == "team":
        targets = [("team", m.get("name"), m.get("phone")) for m in R._managers(prog)
                   if (m.get("role") or "").lower() == "team" and m.get("phone")]
    else:
        targets = [("primary", "owner", prog.alert_phone)] if prog.alert_phone else []
    if not targets:
        raise HTTPException(status_code=422, detail="No phone is set for that target.")
    # Sent EXACTLY as given, ONCE per phone, never retried automatically.
    from app.services import sms_programs as _sci
    from app.services.twilio_callbacks import apply_status_callback
    out = []
    for aud, who, to in targets:
        rc, ok, why = {}, False, None
        try:
            client, from_ = _sci.sci_sender()
            sent = client.messages.create(**apply_status_callback(dict(body=text, from_=from_, to=to)))
            rc = {"sid": getattr(sent, "sid", None), "status": getattr(sent, "status", None)}
            ok = True
        except Exception as exc:                         # noqa: BLE001 - shown, not hidden
            why = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        a = R.sms_alert_record(ProgramAlert(organization_id=prog.organization_id, response_id=None, kind="test",
                                            audience=aud, channel="sms", recipient=to,
                                            delivered=False, reason=why, message=text), rc)
        db.add(a)
        db.commit()
        out.append({"who": who, "accepted": ok, "error": why, "alert_id": a.id, "sid": rc.get("sid"),
                    "status": rc.get("status"), "to": _mask(to)})
    return out[0] if body.target != "team" else {"items": out}


@router.post("/alerts/test-system")
def alert_test_system(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """ONE test of the OWNER-ONLY system alert path (the one self-checks and
    mailbox problems use): owner's alert email + alert phone, nobody else."""
    from app.services.programs import responses as R
    prog = _program(db, user)
    _require_manager(db, user)
    title = R.alert_title(prog)
    res = R.owner_system_alert(
        db, prog, "system",
        "TEST ONLY - owner system alert check. Internal system warnings now go to you only, "
        "never to the team.",
        sms_text="TEST - %s: Owner-only system alert check. Please check your email." % title)
    db.commit()
    return {"email": {"to": _mask(prog.alert_email, 10), "ok": res["email"][0] if res["email"] else None},
            "sms": {"to": _mask(prog.alert_phone), "ok": res["sms"][0] if res["sms"] else None}}


@router.get("/audit/spacing")
def audit_spacing(days: int = Query(7, ge=1, le=60), gap_hours: int = Query(20, ge=1, le=96),
                  db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """READ-ONLY spacing audit. Every real (non-test) contact who got two
    outbound texts less than `gap_hours` apart in the last `days`, with every
    outbound text (time, why it was sent, Twilio SID, delivery), every cadence
    touch attempt, the cadence state, and any reply from the family. Sends
    nothing, changes nothing."""
    from datetime import timedelta as _td
    from app.models.models import CadenceState, CadenceTouchLog, Message
    from app.models.program_models import ProgramResponse
    prog = _program(db, user)
    _require_manager(db, user)
    org = prog.organization_id
    since = datetime.utcnow() - _td(days=days)
    gap = _td(hours=gap_hours)
    rows = (db.query(Message).join(Lead, Lead.id == Message.lead_id)
            .filter(Lead.organization_id == org, Lead.is_test.isnot(True), Message.sent_at >= since - gap)
            .order_by(Message.sent_at).all())
    by: dict = {}
    for m in rows:
        by.setdefault(m.lead_id, []).append(m)
    out = []
    for lid, ms in by.items():
        pairs = [(a, b) for a, b in zip(ms, ms[1:]) if b.sent_at >= since and b.sent_at - a.sent_at < gap]
        if not pairs:
            continue
        lead = db.query(Lead).filter(Lead.id == lid).first()
        st = db.query(CadenceState).filter(CadenceState.lead_id == lid).first()
        logs = (db.query(CadenceTouchLog).filter(CadenceTouchLog.lead_id == lid)
                .order_by(CadenceTouchLog.created_at).all())
        replies = (db.query(ProgramResponse).filter(ProgramResponse.lead_id == lid)
                   .order_by(ProgramResponse.received_at).all())
        iso = lambda d: d.isoformat() if d else None
        out.append({
            "lead_id": lid,
            "name": ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip() if lead else None,
            "phone_last4": (lead.phone or "")[-4:] if lead else None,
            "status": getattr(lead, "status", None),
            "close_pairs": [{"first": iso(a.sent_at), "second": iso(b.sent_at),
                             "hours_apart": round((b.sent_at - a.sent_at).total_seconds() / 3600, 2)}
                            for a, b in pairs],
            "texts": [{"id": m.id, "sent_at": iso(m.sent_at), "source": getattr(m, "send_source", None),
                       "by_user": getattr(m, "sent_by_user_id", None), "sid": m.twilio_sid,
                       "twilio_status": m.twilio_status, "delivery": m.delivery_status,
                       "send_state": getattr(m, "send_state", None), "error": getattr(m, "error_code", None),
                       "body": (m.body or "")[:70]} for m in ms],
            "cadence": ({"status": st.status, "touch": st.current_touch_number, "started": iso(st.cadence_started_at),
                         "next_due": iso(st.next_touch_due_at), "last_sent": iso(st.last_touch_sent_at)}
                        if st else None),
            "touch_log": [{"touch": l.touch_number, "attempt": l.attempt_seq, "channel": l.channel,
                           "outcome": l.outcome, "reason": l.reason, "scheduled": iso(l.scheduled_for),
                           "attempted": iso(l.attempted_at), "message_id": l.message_id,
                           "sid": l.provider_message_id, "err": l.provider_error_code} for l in logs],
            "replies": [{"at": iso(r.received_at), "channel": r.channel,
                         "class": r.response_class} for r in replies],
        })
    # FORWARD CHECK: every active cadence in this workspace, when its next
    # touch is due, and whether that is inside the gap after the last text the
    # family actually got (the runner holds those; this shows how many).
    from app.services.programs import spacing as _sp
    upcoming = {"active": 0, "due_inside_gap": 0, "examples": []}
    for st in (db.query(CadenceState).join(Lead, Lead.id == CadenceState.lead_id)
               .filter(Lead.organization_id == org, Lead.is_test.isnot(True), CadenceState.status == "active")
               .all()):
        upcoming["active"] += 1
        last = _sp.last_text_at(db, st.lead_id)
        if last is not None and st.next_touch_due_at is not None and st.next_touch_due_at < last + _sp.MIN_GAP:
            upcoming["due_inside_gap"] += 1
            if len(upcoming["examples"]) < 10:
                upcoming["examples"].append({"lead_id": st.lead_id, "last_text": last.isoformat(),
                                             "next_due": st.next_touch_due_at.isoformat(),
                                             "touch": st.current_touch_number + 1})
    return {"window_days": days, "gap_hours": gap_hours, "contacts": len(out), "items": out,
            "upcoming": upcoming}


@router.get("/audit/failed-texts")
def audit_failed_texts(days: int = Query(30, ge=1, le=120), db: Session = Depends(get_db),
                       user: User = Depends(require_tenant_user)):
    """OWNER ONLY, READ-ONLY. Every SCI text that failed or came back
    undelivered, with the full Twilio SID, error code, send time, source and
    the exact body - the evidence for a carrier-filtering (30007) support
    case. Test contacts are included and marked. Sends nothing."""
    from app.models.models import Message
    if getattr(user, "role", None) != "god_admin":
        raise HTTPException(status_code=403, detail="Owner only.")
    prog = _program(db, user)
    since = datetime.utcnow() - timedelta(days=days)
    rows = (db.query(Message, Lead).join(Lead, Lead.id == Message.lead_id)
            .filter(Lead.organization_id == prog.organization_id, Message.sent_at >= since,
                    (Message.delivery_status.in_(("failed", "undelivered")))
                    | (Message.twilio_status.in_(("failed", "undelivered")))
                    | Message.error_code.isnot(None))
            .order_by(Message.sent_at).all())
    items = [{"sid": m.twilio_sid, "sent_at_utc": m.sent_at.isoformat() if m.sent_at else None,
              "to_last4": (l.phone or "")[-4:], "is_test": bool(l.is_test),
              "status": m.delivery_status or m.twilio_status, "error_code": m.error_code,
              "error_message": getattr(m, "error_message", None), "source": m.send_source,
              "has_link": "http" in (m.body or "").lower(), "body": m.body} for m, l in rows]
    from collections import Counter
    return {"days": days, "count": len(items),
            "by_error": dict(Counter(i["error_code"] or "none" for i in items)),
            "with_link": sum(1 for i in items if i["has_link"]), "items": items}


@router.get("/audit/inbound-window")
def audit_inbound_window(start: str, end: str, db: Session = Depends(get_db),
                         user: User = Depends(require_tenant_user)):
    """OWNER ONLY, READ-ONLY. Asks Twilio for every inbound text and call to
    the SCI line between start and end (ISO, UTC) and says whether each one
    reached the app; also lists Twilio's own webhook-failure alerts in that
    window. Used to check for replies missed during an outage. Sends nothing,
    changes nothing."""
    from app.models.models import Reply
    from app.models.program_models import ProgramUnmatchedReply
    from app.services import sms_programs as _sci
    if getattr(user, "role", None) != "god_admin":
        raise HTTPException(status_code=403, detail="Owner only.")
    try:
        t0 = datetime.fromisoformat(start.replace("Z", "")).replace(tzinfo=None)
        t1 = datetime.fromisoformat(end.replace("Z", "")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(status_code=422, detail="start / end must be ISO times (UTC).")
    if t1 <= t0 or (t1 - t0) > timedelta(hours=6):
        raise HTTPException(status_code=422, detail="A window of up to 6 hours.")
    _program(db, user)
    client, line = _sci.sci_sender()
    from datetime import timezone as _tz
    a, b = t0.replace(tzinfo=_tz.utc), t1.replace(tzinfo=_tz.utc)
    texts = []
    for m in client.messages.list(to=line, date_sent_after=a, date_sent_before=b, limit=200):
        if not (m.direction or "").startswith("inbound"):
            continue
        in_app = (db.query(Reply.id).filter(Reply.twilio_sid == m.sid).first() is not None
                  or db.query(ProgramUnmatchedReply.id)
                  .filter(ProgramUnmatchedReply.mailbox_message_id == "sms:%s" % m.sid).first() is not None)
        texts.append({"sid": m.sid, "from_last4": (m.from_ or "")[-4:],
                      "at_utc": m.date_sent.isoformat() if m.date_sent else None,
                      "body": m.body, "in_app": in_app})
    calls = []
    try:
        for c in client.calls.list(to=line, start_time_after=a, start_time_before=b, limit=100):
            calls.append({"sid": c.sid, "from_last4": (c.from_ or "")[-4:], "status": c.status,
                          "at_utc": c.start_time.isoformat() if c.start_time else None,
                          "duration": c.duration})
    except Exception as exc:                              # noqa: BLE001
        calls = [{"error": str(exc)[:200]}]
    alerts = []
    try:
        for al in client.monitor.v1.alerts.list(start_date=a, end_date=b, limit=200):
            alerts.append({"code": al.error_code, "at_utc": al.date_created.isoformat() if al.date_created else None,
                           "url": (al.request_url or "")[:200], "resource": al.resource_sid,
                           "text": (al.alert_text or "")[:300]})
    except Exception as exc:                              # noqa: BLE001
        alerts = [{"error": str(exc)[:200]}]
    return {"line_last4": line[-4:], "start": t0.isoformat(), "end": t1.isoformat(),
            "inbound_texts": texts, "missing_from_app": [t for t in texts if not t["in_app"]],
            "calls": calls, "twilio_alerts": alerts}


@router.get("/audit/lead/{lead_id}")
def audit_lead(lead_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """READ-ONLY send history for one family: cadence state, every touch
    attempt with its outcome and reason, and every outbound text. Used to
    explain a self-check flag such as "texts never started". Sends nothing."""
    from app.models.models import CadenceState, CadenceTouchLog, Message
    prog = _program(db, user)
    _require_manager(db, user)
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == prog.organization_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Not found")
    iso = lambda d: d.isoformat() if d else None
    st = db.query(CadenceState).filter(CadenceState.lead_id == lead_id).first()
    logs = (db.query(CadenceTouchLog).filter(CadenceTouchLog.lead_id == lead_id)
            .order_by(CadenceTouchLog.created_at).all())
    msgs = db.query(Message).filter(Message.lead_id == lead_id).order_by(Message.sent_at).all()
    return {
        "lead_id": lead_id, "status": getattr(lead, "status", None), "is_test": bool(lead.is_test),
        "cadence": ({"status": st.status, "touch": st.current_touch_number, "started": iso(st.cadence_started_at),
                     "next_due": iso(st.next_touch_due_at), "last_sent": iso(st.last_touch_sent_at)}
                    if st else None),
        "touch_log": [{"touch": l.touch_number, "attempt": l.attempt_seq, "channel": l.channel,
                       "outcome": l.outcome, "reason": l.reason, "scheduled": iso(l.scheduled_for),
                       "attempted": iso(l.attempted_at), "message_id": l.message_id,
                       "err": l.provider_error_code} for l in logs],
        "texts": [{"sent_at": iso(m.sent_at), "source": getattr(m, "send_source", None),
                   "delivery": m.delivery_status, "error": getattr(m, "error_code", None)} for m in msgs],
    }


# ── appointment setter: conversations and the synthetic simulator ──────────

def _conv_json(db, conv, lead=None, turns: bool = False) -> dict:
    from app.models.program_models import ProgramConversationTurn
    from app.services.programs import appointments as _A
    out = {"id": conv.id, "lead_id": conv.lead_id, "state": conv.state, "is_test": bool(conv.is_test),
           "appointment_kind": conv.appointment_kind, "service_type": conv.service_type,
           "offered": [s.isoformat() for s in _A.offered(conv)],
           "offered_text": [_A.describe(s) for s in _A.offered(conv)],
           "callback_window": conv.callback_window,
           "booked_time": conv.booked_time.isoformat() if conv.booked_time else None,
           "handoff_reason": conv.handoff_reason, "ai_turns": conv.ai_turns,
           "last_family_at": conv.last_family_at.isoformat() if conv.last_family_at else None,
           "last_ai_at": conv.last_ai_at.isoformat() if conv.last_ai_at else None,
           "next_followup_at": conv.next_followup_at.isoformat() if conv.next_followup_at else None}
    if lead is not None:
        out["name"] = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title()
    if turns:
        out["turns"] = [{"role": t.role, "text": t.text, "action": t.action, "delivery": t.delivery,
                         "reason": t.reason, "at": t.created_at.isoformat() if t.created_at else None}
                        for t in db.query(ProgramConversationTurn)
                        .filter(ProgramConversationTurn.conversation_id == conv.id)
                        .order_by(ProgramConversationTurn.created_at).all()]
    return out


@router.get("/conversations")
def list_conversations(state: Optional[str] = None, include_test: bool = False,
                       db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    from app.models.program_models import ProgramConversation
    prog = _program(db, user, managers_only=False)
    q = db.query(ProgramConversation).filter(ProgramConversation.organization_id == prog.organization_id)
    if not include_test:
        q = q.filter(ProgramConversation.is_test.isnot(True))
    if state:
        q = q.filter(ProgramConversation.state.in_(state.split(",")))
    rows = q.order_by(ProgramConversation.updated_at.desc()).limit(200).all()
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_([c.lead_id for c in rows])).all()} if rows else {}
    return {"items": [_conv_json(db, c, leads.get(c.lead_id)) for c in rows]}


@router.get("/conversations/{lead_id}")
def get_conversation(lead_id: str, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_or_observer)):
    from app.models.program_models import ProgramConversation
    prog = _program(db, user, managers_only=False)
    conv = (db.query(ProgramConversation).filter(ProgramConversation.organization_id == prog.organization_id,
                                                 ProgramConversation.lead_id == lead_id)
            .order_by(ProgramConversation.created_at.desc()).first())
    if conv is None:
        return {"conversation": None}
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    return {"conversation": _conv_json(db, conv, lead, turns=True)}


class ConversationHandIn(BaseModel):
    state: str                          # human | ai_active


@router.post("/conversations/{lead_id}/hand")
def hand_conversation(lead_id: str, body: ConversationHandIn, db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user)):
    """A person takes the conversation (state human) or gives it back (ai_active)."""
    from app.models.program_models import ProgramConversation
    prog = _program(db, user)
    if body.state not in ("human", "ai_active"):
        raise HTTPException(status_code=422, detail="state must be human or ai_active")
    conv = (db.query(ProgramConversation).filter(ProgramConversation.organization_id == prog.organization_id,
                                                 ProgramConversation.lead_id == lead_id)
            .order_by(ProgramConversation.created_at.desc()).first())
    if conv is None:
        raise HTTPException(status_code=404, detail="No conversation for this contact.")
    conv.state = body.state
    conv.handoff_reason = ("taken by %s" % (user.email or user.id)) if body.state == "human" else None
    db.commit()
    return _conv_json(db, conv)


class SetterSimIn(BaseModel):
    persona: str                        # short key, e.g. "oscar"; one synthetic contact each
    first_name: str
    text: str
    location_id: Optional[str] = None
    channel: str = "sms"
    reset: bool = False


@router.post("/setter/simulate")
def setter_simulate(body: SetterSimIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """ONE family message from a SYNTHETIC contact (is_test, no phone, no
    email - it can never be messaged) through the real reply path and the
    appointment setter (as "draft" in production unless it is switched on).
    Booking for a synthetic contact is simulated in production (the time is
    checked on the live calendar, nothing is written) and real in staging.
    Returns the setter's decision and the full conversation."""
    from app.models.program_models import ProgramConversation, ProgramSourceRecord
    from app.services.programs import ai_setter
    from app.services.programs import responses as R
    prog = _program(db, user)
    _require_manager(db, user)
    key = "SIM-SETTER-%s" % "".join(ch for ch in body.persona.lower() if ch.isalnum())[:24]
    lead = db.query(Lead).filter(Lead.organization_id == prog.organization_id, Lead.is_test.is_(True),
                                 Lead.last_name == key).first()
    if lead is None:
        lead = Lead(organization_id=prog.organization_id, first_name=body.first_name[:40], last_name=key,
                    phone=None, email=None, status="sent", is_test=True,
                    assigned_to_id=getattr(prog, "booking_user_id", None) or user.id)
        db.add(lead)
        db.flush()
        if body.location_id:
            db.add(ProgramSourceRecord(organization_id=prog.organization_id, lead_id=lead.id,
                                       source_lead_id=key, location_id=body.location_id,
                                       raw_json="{}", campaign_family=None))
        db.commit()
    if body.reset:
        for c in db.query(ProgramConversation).filter(ProgramConversation.lead_id == lead.id).all():
            c.state = "stopped"
        db.commit()
        db.add(ProgramConversation(organization_id=lead.organization_id, lead_id=lead.id,
                                   state="ai_active", is_test=True))
        db.commit()
    # The simulator is a person's action: the reply is recorded without the
    # setter, then the setter runs as THIS person's manual AI action - in any
    # program mode, for a synthetic contact that can never be messaged.
    resp = R.on_inbound(db, lead, body.text[:1000], body.channel, staff_alerts=False, run_setter=False)
    result = None
    if resp is not None:
        result = ai_setter.on_family_reply(db, prog, lead, resp, body.text, force_mode="draft", actor=user.id)
    conv = ai_setter.conversation_for(db, lead, create=False)
    return {"lead_id": lead.id, "class": resp.response_class if resp else None, "result": result,
            "conversation": _conv_json(db, conv, lead, turns=True) if conv else None}


class SetterEvalIn(BaseModel):
    scenario_id: str
    run_tag: str = "r1"
    model: str = "gpt-4o"            # gpt-4o | gpt-4o-mini (benchmark)
    location_id: Optional[str] = None


@router.get("/setter/eval/scenarios")
def setter_eval_scenarios(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    from app.services.programs.setter_eval_scenarios import SCENARIOS
    _program(db, user)
    return {"count": len(SCENARIOS), "items": [{"id": s["id"], "category": s["category"],
                                                  "turns": len(s["turns"])} for s in SCENARIOS]}


@router.post("/setter/eval")
def setter_eval(body: SetterEvalIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """OWNER ONLY. Run one evaluation conversation through the real setter as
    the owner's own manual AI action, with a fresh SYNTHETIC contact that can
    never be messaged (bookings are simulated against the live calendar).
    Returns every turn and what failed."""
    from app.services.programs import setter_eval as E
    if getattr(user, "role", None) != "god_admin":
        raise HTTPException(status_code=403, detail="Only the owner runs setter evaluations.")
    prog = _program(db, user)
    sc = E.scenario(body.scenario_id)
    if sc is None:
        raise HTTPException(status_code=404, detail="No such scenario.")
    cap = {"gpt-4o": "sci_appointment_setter", "gpt-4o-mini": "sci_appointment_setter_mini"}.get(body.model)
    if cap is None:
        raise HTTPException(status_code=422, detail="model must be gpt-4o or gpt-4o-mini")
    loc = body.location_id
    if not loc:
        p = (db.query(LocationProfile).filter(LocationProfile.organization_id == prog.organization_id,
                                              LocationProfile.is_review_bucket.isnot(True),
                                              LocationProfile.location_id.isnot(None))
             .order_by(LocationProfile.official_name).first())
        loc = p.location_id if p else None
    tag = "".join(ch for ch in body.run_tag if ch.isalnum())[:16] or "r1"
    return E.run_scenario(db, prog, user, sc, run_tag="%s%s" % (tag, "m" if cap.endswith("mini") else ""),
                          location_id=loc, capability=cap)


class LiveTestIn(BaseModel):
    text: str
    reset: bool = False


@router.post("/setter/live-test")
def setter_live_test(body: LiveTestIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """OWNER ONLY. The real path, end to end, to the owner's OWN phone: a
    reply from the owner's live test contact (is_test, phone = the program's
    verified alert number) is recorded, the setter answers in AUTO, and the
    answer goes out on the SCI line through every production gate (consent,
    sender, location identity, contact hours, spacing). No family, no team
    alert. Returns the setter turn and the message record."""
    from app.models.models import Message
    from app.models.program_models import ProgramConversation, ProgramSourceRecord
    from app.services import test_records as TR
    from app.services.programs import ai_setter
    from app.services.programs import responses as R
    if getattr(user, "role", None) != "god_admin":
        raise HTTPException(status_code=403, detail="Only the owner runs the live test.")
    prog = _program(db, user)
    if len(TR._digits(prog.alert_phone)) != 10:
        raise HTTPException(status_code=409, detail="The program has no verified owner alert phone.")
    lead = (db.query(Lead).filter(Lead.organization_id == prog.organization_id, Lead.is_test.is_(True),
                                  Lead.last_name == TR.OWNER_TEST_LAST_NAME).first())
    if lead is None:
        lead = Lead(organization_id=prog.organization_id, first_name="Owner", last_name=TR.OWNER_TEST_LAST_NAME,
                    phone="+1" + TR._digits(prog.alert_phone), email=None, status="sent", is_test=True,
                    assigned_to_id=getattr(prog, "booking_user_id", None) or user.id)
        db.add(lead)
        db.flush()
        p = (db.query(LocationProfile).filter(LocationProfile.organization_id == prog.organization_id,
                                              LocationProfile.is_review_bucket.isnot(True),
                                              LocationProfile.location_id.isnot(None))
             .order_by(LocationProfile.official_name).first())
        if p is not None:
            db.add(ProgramSourceRecord(organization_id=prog.organization_id, lead_id=lead.id,
                                       source_lead_id=TR.OWNER_TEST_LAST_NAME, location_id=p.location_id,
                                       location_status="mapped", raw_json="{}"))
        db.commit()
    # The owner approves his own test contact for sending by running this
    # (Choose & Send approval) - every other gate, consent included, still applies.
    # It rides on a campaign that is already switched on for real families,
    # so it meets exactly the gates a replying family meets.
    from app.models.program_models import CampaignFamily
    live_family = (db.query(CampaignFamily).filter(CampaignFamily.organization_id == prog.organization_id,
                                                   CampaignFamily.is_active.is_(True))
                   .order_by(CampaignFamily.key).first())
    for rec in db.query(ProgramSourceRecord).filter(ProgramSourceRecord.lead_id == lead.id).all():
        if rec.approved_to_send_at is None:
            rec.approved_to_send_at = datetime.utcnow()
            rec.approved_to_send_by = user.id
        if live_family is not None:
            fam = (db.query(CampaignFamily).filter(CampaignFamily.organization_id == prog.organization_id,
                                                   CampaignFamily.key == (rec.campaign_family or "")).first())
            if fam is None or not fam.is_active:
                rec.campaign_family = live_family.key
    db.commit()
    if body.reset:
        for c in db.query(ProgramConversation).filter(ProgramConversation.lead_id == lead.id).all():
            c.state = "stopped"
        db.add(ProgramConversation(organization_id=lead.organization_id, lead_id=lead.id, state="ai_active",
                                   is_test=True))
        db.commit()
    resp = R.on_inbound(db, lead, body.text[:500], "sms", staff_alerts=False, run_setter=False)
    res = (ai_setter.on_family_reply(db, prog, lead, resp, body.text, force_mode="auto", actor=user.id)
           if resp is not None else None)
    msg = None
    if res and res.get("delivery") == "sent":
        m = db.query(Message).filter(Message.lead_id == lead.id).order_by(Message.sent_at.desc()).first()
        if m is not None:
            msg = {"id": m.id, "sid": m.twilio_sid, "status": m.twilio_status, "delivery": m.delivery_status,
                   "error": m.error_code, "sent_at": m.sent_at.isoformat() if m.sent_at else None}
    conv = ai_setter.conversation_for(db, lead, create=False)
    turn = None
    if conv is not None:
        from app.models.program_models import ProgramConversationTurn as T
        t = db.query(T).filter(T.conversation_id == conv.id, T.role == "ai").order_by(T.created_at.desc()).first()
        if t is not None:
            turn = {"delivery": t.delivery, "reason": t.reason, "text": t.text}
    return {"lead_id": lead.id, "class": resp.response_class if resp else None, "result": res,
            "turn": turn, "message": msg}


@router.post("/setter/live-test/email-start")
def setter_live_test_email_start(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """OWNER ONLY. Start the live EMAIL test: the owner's test contact gets the
    program's owner alert address, and ONE short email goes to it from the
    program's own sending mailbox. The owner replies from his own inbox; that
    reply arrives through the real inbound mailbox path into SCI Live. The
    conversation starts with a PERSON handling it, so the owner's first answer
    is his own; he then hands it to the AI. Nobody else is emailed."""
    from app.models.models import EmailMessage
    from app.models.program_models import ProgramConversation
    from app.services import send_source as ss, test_records as TR
    from app.services.programs import ai_setter, mailbox_send
    from app.services.programs import appointments as A
    if getattr(user, "role", None) != "god_admin":
        raise HTTPException(status_code=403, detail="Only the owner runs the live test.")
    prog = _program(db, user)
    owner_email = (prog.alert_email or "").strip().lower()
    if "@" not in owner_email:
        raise HTTPException(status_code=409, detail="The program has no owner alert email.")
    lead = (db.query(Lead).filter(Lead.organization_id == prog.organization_id, Lead.is_test.is_(True),
                                  Lead.last_name == TR.OWNER_TEST_LAST_NAME).first())
    if lead is None:
        raise HTTPException(status_code=409, detail="Run the text live test once first (it creates the test contact).")
    lead.email = owner_email
    for c in db.query(ProgramConversation).filter(ProgramConversation.lead_id == lead.id).all():
        c.state = "stopped"
        c.next_followup_at = None
    db.add(ProgramConversation(organization_id=lead.organization_id, lead_id=lead.id, state="human", is_test=True,
                               handoff_reason="owner live email test - the owner answers first",
                               owner_changed_at=datetime.utcnow(), owner_changed_by=user.id,
                               owner_note="Human Handling on (live email test)"))
    db.commit()
    subject = "SCI Live email test %s" % datetime.utcnow().strftime("%b %d %H:%M")
    html = ("<p>This is the EvoSys Pro SCI Live email test.</p>"
            "<p>Please reply to this email with any short question about planning ahead. "
            "Your reply should appear in SCI Live, where it can be answered in this same thread.</p>")
    res = mailbox_send.send(db, prog, to_email=owner_email, subject=subject, body_html=html)
    if res is None:
        raise HTTPException(status_code=409, detail="The program has no sending mailbox.")
    advisor = A.program_advisor(db, prog)
    em = EmailMessage(lead_id=lead.id, sender_id=(advisor.id if advisor else user.id), subject=subject,
                      body_html=html, status="sent" if res.get("success") else "failed",
                      send_source=ss.MANUAL, sent_by_user_id=user.id)
    em.to_email, em.from_email, em.error = owner_email, res.get("from_email"), res.get("error")
    db.add(em)
    db.commit()
    return {"ok": bool(res.get("success")), "to": owner_email, "from": res.get("from_email"),
            "subject": subject, "error": res.get("error"), "lead_id": lead.id, "email_id": em.id}


@router.post("/setter/live-test/booking-link")
def setter_live_test_booking_link(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """OWNER ONLY. Text the owner's live test contact (the program's own owner
    alert number) a real booking link: a booking token for the PROGRAM'S
    booking calendar (the central EvoSys Appointment Booking calendar), on the
    branded /book page. Sent through send_sms with every gate - SCI sender,
    consent, contact hours, and the toll-free registration's content check
    (links are registered on TF-8449172171 for informational messages). No
    family is involved; nothing is bypassed."""
    from app.models.models import Message
    from app.services import send_source as ss, test_records as TR
    from app.services.programs import appointments as A
    from app.services.public_identity import booking_url
    from app.services.sms_service import get_or_create_booking_link, send_sms
    if getattr(user, "role", None) != "god_admin":
        raise HTTPException(status_code=403, detail="Only the owner runs the live test.")
    prog = _program(db, user)
    lead = (db.query(Lead).filter(Lead.organization_id == prog.organization_id, Lead.is_test.is_(True),
                                  Lead.last_name == TR.OWNER_TEST_LAST_NAME).first())
    if lead is None or not TR.owner_live_test_send_allowed(db, lead, ss.SCI_SETTER):
        raise HTTPException(status_code=409, detail="The owner live test contact is not set up for this program.")
    advisor = A.program_advisor(db, prog)
    if advisor is None:
        raise HTTPException(status_code=409, detail="The program has no booking calendar.")
    link = get_or_create_booking_link(db, lead, advisor)
    if (link.status or "") in ("booked", "confirmed"):
        raise HTTPException(status_code=409, detail="The test contact already has a booking - clean it up first.")
    url = booking_url(db, lead.organization_id, link.token)
    if not url:
        raise HTTPException(status_code=409, detail="No branded booking host is configured.")
    db.commit()
    body = "Here is your link to choose a time for your planning visit: %s" % url
    try:
        # The owner pressing this is a person sending by hand (MANUAL): the
        # 20-hour spacing backstop is for automated texts to families.
        msg = send_sms(db, advisor, lead, body, include_booking_link=False, send_source=ss.MANUAL,
                       sent_by_user_id=user.id)
    except Exception as exc:                             # noqa: BLE001 - report the exact refusal
        raise HTTPException(status_code=409, detail="Not sent: %s" % str(exc)[:400])
    m = db.query(Message).filter(Message.id == msg.id).first()
    return {"ok": True, "booking_url": url, "token": link.token, "calendar_user_id": advisor.id,
            "message": {"id": m.id, "sid": m.twilio_sid, "status": m.twilio_status, "body": m.body,
                        "link_in_body": url in (m.body or "")}}


@router.post("/setter/live-test/cleanup")
def setter_live_test_cleanup(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """OWNER ONLY. Remove the owner live test's booking from the calendar
    (Google event deleted, booking marked cancelled) without texting anyone,
    and close its conversation. Touches nothing but the owner test contact."""
    from app.models.models import BookingLink
    from app.models.program_models import ProgramConversation
    from app.services import test_records as TR
    from app.services.calendar_service import cancel_calendar_event
    if getattr(user, "role", None) != "god_admin":
        raise HTTPException(status_code=403, detail="Owner only.")
    prog = _program(db, user)
    lead = (db.query(Lead).filter(Lead.organization_id == prog.organization_id, Lead.is_test.is_(True),
                                  Lead.last_name == TR.OWNER_TEST_LAST_NAME).first())
    if lead is None:
        return {"ok": True, "bookings": []}
    out = []
    for b in db.query(BookingLink).filter(BookingLink.lead_id == lead.id,
                                          BookingLink.status.in_(("booked", "confirmed"))).all():
        had_event = b.calendar_event_id
        res = cancel_calendar_event(db, b)
        out.append({"booking_id": b.id, "booked_time": b.booked_time.isoformat() if b.booked_time else None,
                    "calendar_event_id": had_event, "result": res.get("note"), "status": b.status})
    for c in db.query(ProgramConversation).filter(ProgramConversation.lead_id == lead.id).all():
        c.state = "stopped"
        c.next_followup_at = None
    db.commit()
    return {"ok": True, "bookings": out}


@router.get("/setter/live-test/message/{message_id}")
def setter_live_test_message(message_id: str, db: Session = Depends(get_db),
                             user: User = Depends(require_tenant_user)):
    from app.models.models import Message
    from app.services import test_records as TR
    if getattr(user, "role", None) != "god_admin":
        raise HTTPException(status_code=403, detail="Owner only.")
    prog = _program(db, user)
    m = (db.query(Message).join(Lead, Lead.id == Message.lead_id)
         .filter(Message.id == message_id, Lead.organization_id == prog.organization_id,
                 Lead.last_name == TR.OWNER_TEST_LAST_NAME).first())
    if m is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return {"id": m.id, "sid": m.twilio_sid, "status": m.twilio_status, "delivery": m.delivery_status,
            "error": m.error_code, "error_message": m.error_message}


@router.get("/setter/stats")
def setter_stats(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_or_observer)):
    """The conversation numbers, each counted separately and never mixed with
    test contacts: AI replies sent / drafted, conversations people are
    handling, appointment offers, callback requests, bookings, confirmed."""
    from app.models.models import BookingLink, Message
    from app.services import ai_gateway as _gw
    from app.services.programs import ai_setter as _setter
    from app.models.program_models import ProgramConversation, ProgramConversationTurn
    from app.services import send_source as _ss
    prog = _program(db, user, managers_only=False)
    org = prog.organization_id
    since = datetime.utcnow() - timedelta(days=days)
    test_ids = db.query(Lead.id).filter(Lead.is_test.is_(True))
    turns = (db.query(ProgramConversationTurn.delivery)
             .filter(ProgramConversationTurn.organization_id == org, ProgramConversationTurn.role == "ai",
                     ProgramConversationTurn.created_at >= since, ~ProgramConversationTurn.lead_id.in_(test_ids))
             .all())
    convs = (db.query(ProgramConversation).filter(ProgramConversation.organization_id == org,
                                                  ProgramConversation.is_test.isnot(True),
                                                  ProgramConversation.updated_at >= since).all())
    replied = _test_records.exclude_test_responses(
        db.query(ProgramResponse.lead_id).filter(ProgramResponse.organization_id == org,
                                                 ProgramResponse.received_at >= since,
                                                 ProgramResponse.response_class.notin_(("auto_reply", "opt_out"))))
    human = (db.query(Message.lead_id).filter(Message.send_source == _ss.MANUAL, Message.sent_at >= since,
                                              Message.lead_id.in_(replied)).distinct().count())
    org_leads = db.query(Lead.id).filter(Lead.organization_id == org, Lead.is_test.isnot(True))
    bookings = (db.query(BookingLink).filter(BookingLink.lead_id.in_(org_leads),
                                             BookingLink.status.in_(("booked", "confirmed", "completed")),
                                             BookingLink.created_at >= since).all())
    return {
        "days": days,
        "ai_replies_sent": sum(1 for (d,) in turns if d == "sent"),
        "ai_replies_drafted": sum(1 for (d,) in turns if d == "draft"),
        "ai_replies_held_or_blocked": sum(1 for (d,) in turns if d in ("held", "blocked")),
        "families_replied": replied.distinct().count(),
        "human_conversations": human,
        "appointment_offers": sum(1 for c in convs if c.offered_at),
        "callbacks_requested": sum(1 for c in convs if c.state == "callback_requested"),
        "handed_to_person": sum(1 for c in convs if c.state == "human"),
        "bookings": len(bookings),
        "booked_by_ai": sum(1 for c in convs if c.state == "booked"),
        "confirmed_visits": sum(1 for b in bookings if (b.status or "") in ("confirmed", "completed")),
        "outcomes": _appointment_outcomes(db, org, since, convs, bookings),
        "setter_mode": getattr(prog, "ai_setter_mode", None) or "off",
        "effective_mode": _setter.mode_of(prog),
        "emergency_off": _setter.killed(),
        "platform_background_ai": _gw.background_enabled(),
        "runs_without_platform_switch": True,
    }


def _appointment_outcomes(db, org, since, convs, bookings) -> dict:
    """THE APPOINTMENT FUNNEL, real families only, each step counted on its
    own: a guide request, a delivered text or a mailing task is never an
    appointment. Offered = real open times were put to the family; accepted
    = they chose one and it was booked; confirmed on Google = the booking
    holds a calendar event on the central EvoSys Appointment Booking
    calendar; completed = marked as having taken place."""
    import json as _json
    real = [b for b in bookings if (b.status or "") != "cancelled"]
    fulfilled = 0
    for c in convs:
        try:
            mr = (_json.loads(c.memory or "{}") or {}).get("mail_request")
        except (TypeError, ValueError):
            mr = None
        if isinstance(mr, dict) and mr.get("done"):
            fulfilled += 1
    opt_outs = (_test_records.exclude_test_responses(
        db.query(ProgramResponse.lead_id).filter(ProgramResponse.organization_id == org,
                                                 ProgramResponse.received_at >= since,
                                                 ProgramResponse.response_class == "opt_out"))
        .distinct().count())
    return {
        "appointments_offered": sum(1 for c in convs if c.offered_at),
        "appointments_accepted": len({b.lead_id for b in real}),
        "confirmed_on_google_calendar": len({b.lead_id for b in real if getattr(b, "calendar_event_id", None)}),
        "visits_completed": len({b.lead_id for b in real if (b.status or "") == "completed"}),
        "information_only_fulfilled": fulfilled,
        "declines": sum(1 for c in convs if c.state == "closed"),
        "opt_outs": opt_outs,
    }


@router.post("/setter/off")
def setter_emergency_off(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """EMERGENCY OFF. Any manager may stop the appointment setter at once
    (turning it ON stays the owner's decision). Held replies are dropped by
    the next sweep; nothing further is answered or sent."""
    prog = _program(db, user, managers_only=True)
    before = getattr(prog, "ai_setter_mode", None) or "off"
    prog.ai_setter_mode = "off"
    db.commit()
    import logging
    logging.getLogger(__name__).warning("SCI setter EMERGENCY OFF by %s (was %s)", user.id, before)
    return {"ok": True, "was": before, "mode": "off"}


class SimulateReplyIn(BaseModel):
    text: str
    channel: str = "sms"           # sms | email


@router.post("/alerts/simulate-reply")
def alert_simulate_reply(body: SimulateReplyIn, db: Session = Depends(get_db),
                         user: User = Depends(require_tenant_user)):
    """END-TO-END ALERT TEST without a real family: a reply from the program's
    own SYNTHETIC test contact (is_test, no phone, no email - it can never be
    messaged) goes through the real reply handling: classification, response
    record, cadence pause, and the SAME alert dispatch a real reply gets.
    Returns the response and every alert record it produced."""
    from app.models.program_models import ProgramAlert, ProgramResponse
    from app.services.programs import responses as R
    prog = _program(db, user)
    _require_manager(db, user)
    if body.channel not in ("sms", "email"):
        raise HTTPException(status_code=422, detail="channel must be sms or email")
    lead = (db.query(Lead).filter(Lead.organization_id == prog.organization_id, Lead.is_test.is_(True),
                                  Lead.first_name == "SCI Alert", Lead.last_name == "Test (synthetic)").first())
    if lead is None:
        lead = Lead(organization_id=prog.organization_id, first_name="SCI Alert", last_name="Test (synthetic)",
                    phone=None, email=None, status="new", is_test=True,
                    assigned_to_id=getattr(prog, "booking_user_id", None) or user.id)
        db.add(lead)
        db.commit()
    resp = R.on_inbound(db, lead, (body.text or "")[:500], body.channel)
    alerts = (db.query(ProgramAlert).filter(ProgramAlert.response_id == resp.id).all()) if resp else []
    return {"response_id": resp.id if resp else None, "class": resp.response_class if resp else None,
            "alerts": [{"channel": a.channel, "audience": a.audience, "to": _mask(a.recipient),
                        "accepted": bool(a.provider_id) or (a.channel != "sms" and a.delivered),
                        "sid": a.provider_id, "status": a.provider_status, "delivered": a.delivered,
                        "reason": a.reason} for a in alerts]}


class AlertResendIn(BaseModel):
    response_ids: List[str]


@router.post("/alerts/resend")
def alert_resend(body: AlertResendIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Re-send the alert for replies that were missed, to EVERYONE who gets
    reply alerts now (owner + team). Each send is recorded."""
    from app.models.program_models import ProgramResponse, ProgramAlert
    from app.services.programs import responses as R
    prog = _program(db, user)
    _require_manager(db, user)
    out = []
    for rid in body.response_ids[:20]:
        resp = db.query(ProgramResponse).filter(ProgramResponse.id == rid,
                                                ProgramResponse.organization_id == prog.organization_id).first()
        if resp is None:
            out.append({"id": rid, "error": "not found"})
            continue
        kind = "hot" if resp.response_class == "hot" else resp.response_class
        targets = [("primary", "sms", prog.alert_phone), ("primary", "email", prog.alert_email)]
        for m in R._managers(prog):
            if (m.get("role") or "").lower() == "team":
                targets += [("team", "sms", m.get("phone")), ("team", "email", m.get("email"))]
        res = []
        for aud, ch, to in targets:
            if not to:
                continue
            rc = {}
            ok, why = R._deliver_staff_alert(db, prog, ch, to, resp.summary or "", kind=kind, response_id=resp.id,
                                             receipt=rc)
            db.add(R.sms_alert_record(ProgramAlert(
                organization_id=prog.organization_id, response_id=resp.id, kind=kind, audience=aud,
                channel=ch, recipient=to, delivered=ok, reason=why, message="(resent) " + (resp.summary or "")), rc))
            res.append({"to": to, "channel": ch, "accepted": ok, "sid": rc.get("sid"), "status": rc.get("status"),
                        "error": why})
        db.commit()
        out.append({"id": rid, "sent": res})
    return {"items": out}


@router.get("/self-check")
def self_check(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    from app.services.programs import self_check as _sc
    prog = _program(db, user)
    return _sc.run(db, prog.organization_id)


class AckIn(BaseModel):
    key: str
    signature: str


@router.post("/self-check/ack")
def self_check_ack(body: AckIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """'Got it' on a self-check problem: hidden until it changes."""
    from app.services.programs import self_check as _sc
    prog = _program(db, user)
    _sc.acknowledge(db, prog.organization_id, body.key[:64], body.signature[:32])
    return _sc.run(db, prog.organization_id)


# ── ENGAGEMENT: of the people reached, how many did something back ────────

@router.get("/engagement")
def engagement(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Engagement rate since the program started and for people contacted
    today (Central). See services/programs/engagement.py for what counts."""
    from datetime import datetime as _dt, time as _time
    from zoneinfo import ZoneInfo
    from app.services.programs import engagement as _eng
    prog = _program(db, user)
    tz, utc = ZoneInfo("America/Chicago"), ZoneInfo("UTC")
    today = _dt.now(utc).astimezone(tz).date()
    start = _dt.combine(today, _time(0, 0), tz).astimezone(utc).replace(tzinfo=None)
    return {"all": _eng.summary(db, prog.organization_id),
            "today": _eng.summary(db, prog.organization_id, since=start)}


# ── OUTBOX: what went out (text or email) and what is going out next ────────

@router.get("/outbox")
def outbox(day: Optional[str] = Query(None, description="YYYY-MM-DD, Central; default today"),
           db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Every text and email sent to the program's families on one day, newest
    first, with what it said and whether it was delivered; and every family
    still scheduled later that day, by time and by channel. Reads only."""
    from datetime import date as _date, datetime as _dt, time as _time, timedelta as _td
    from zoneinfo import ZoneInfo
    from app.models.models import CadenceState, EmailMessage, Lead, Message
    from app.services.programs import send_approval as _sa
    prog = _program(db, user)
    org_id = prog.organization_id
    tz = ZoneInfo("America/Chicago")
    utc = ZoneInfo("UTC")
    try:
        d = _date.fromisoformat(day) if day else _dt.now(utc).astimezone(tz).date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Day must be YYYY-MM-DD.")
    start = _dt.combine(d, _time(0, 0), tz).astimezone(utc).replace(tzinfo=None)
    end = start + _td(days=1)
    now = _dt.utcnow()
    today_c = _dt.now(utc).astimezone(tz).date()

    def iso(x):
        return x.strftime("%Y-%m-%dT%H:%M:%SZ") if x else None

    profiles = {p.location_id: p.official_name for p in _profiles(db, org_id)}
    recs = {}
    for r in (_test_records.exclude_test_program_records(
              db.query(ProgramSourceRecord)
              .filter(ProgramSourceRecord.organization_id == org_id, ProgramSourceRecord.lead_id.isnot(None)))
              .all()):
        recs.setdefault(r.lead_id, []).append(r)

    def where(lead_id):
        rows = recs.get(lead_id) or []
        m = _sa._master(rows) if rows else None
        return (profiles.get(m.location_id) if m else None), (m.campaign_family if m else None), m

    sent = []
    texts = (db.query(Message, Lead).join(Lead, Lead.id == Message.lead_id)
             .filter(Lead.organization_id == org_id, Lead.is_test.isnot(True),   # test contacts never in the counts
                     Message.sent_at >= start, Message.sent_at < end).all())
    for m, lead in texts:
        loc, fam, _ = where(lead.id)
        st = (m.delivery_status or m.twilio_status or "sent").lower()
        sent.append({"at": iso(m.sent_at), "lead_id": lead.id,
                     "name": " ".join(x for x in (lead.first_name, lead.last_name) if x) or "Family",
                     "location": loc, "channel": "text", "to": lead.phone, "subject": None,
                     "preview": (m.body or "")[:400], "status": st})
    emails = (db.query(EmailMessage, Lead).join(Lead, Lead.id == EmailMessage.lead_id)
              .filter(Lead.organization_id == org_id, Lead.is_test.isnot(True), EmailMessage.sent_at >= start,
                      EmailMessage.sent_at < end).all())
    import re as _re
    for e, lead in emails:
        loc, fam, _ = where(lead.id)
        text = _re.sub(r"<[^>]+>", " ", e.body_html or "")
        import html as _html
        text = _re.sub(r"\s+", " ", _html.unescape(text)).strip()
        st = (e.status or "sent").lower()
        if getattr(e, "opened_at", None):
            st = "opened"
        sent.append({"at": iso(e.sent_at), "lead_id": lead.id,
                     "name": " ".join(x for x in (lead.first_name, lead.last_name) if x) or "Family",
                     "location": loc, "channel": "email", "to": lead.email, "subject": e.subject,
                     "preview": text[:400], "status": st})
    sent.sort(key=lambda x: x["at"] or "", reverse=True)

    # Coming up: slow-drip starts later today, and follow-up texts due today.
    coming = []
    seen = set()
    for lead_id, rows in recs.items():
        m = _sa._master(rows)
        sa = getattr(m, "scheduled_start_at", None)
        if not (m.approved_to_send_at and sa and now < sa < end and sa >= start):
            continue
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if lead is None:
            continue
        mode = getattr(m, "send_mode", None) or "text_email"
        reach = _sa.reaches(lead, mode)
        coming.append({"at": iso(sa), "lead_id": lead_id,
                       "name": " ".join(x for x in (lead.first_name, lead.last_name) if x) or "Family",
                       "location": profiles.get(m.location_id), "what": "First message",
                       "channels": reach, "campaign": m.campaign_family})
        seen.add(lead_id)
    # A follow-up text only goes out inside the sending window (8:30 AM -
    # 7:45 PM Eastern, and daytime where the person lives). One that is due
    # after the window closes waits for the next morning, so it is listed at
    # the time it will ACTUALLY go - and not as "today" when that is tomorrow.
    from app.services import contact_hours as _ch
    later = 0
    for state, lead in (db.query(CadenceState, Lead).join(Lead, Lead.id == CadenceState.lead_id)
                        .filter(Lead.organization_id == org_id, CadenceState.status == "active",
                                CadenceState.next_touch_due_at.isnot(None),
                                CadenceState.next_touch_due_at < end).all()):
        if lead.id in seen:
            continue
        due = state.next_touch_due_at
        if due < start and due < now - timedelta(days=2):
            continue
        if d < today_c:
            break
        go = _ch.next_permitted_utc(lead, max(due, now)) if d == today_c else due
        if go is None or go >= end or go < start:
            later += 1
            continue
        loc, fam, _ = where(lead.id)
        coming.append({"at": iso(go), "lead_id": lead.id,
                       "name": " ".join(x for x in (lead.first_name, lead.last_name) if x) or "Family",
                       "location": loc, "what": "Text #%d" % ((state.current_touch_number or 0) + 1),
                       "channels": "Text", "campaign": fam})
    coming.sort(key=lambda x: x["at"] or "")

    texts_n = sum(1 for s in sent if s["channel"] == "text")
    emails_n = sum(1 for s in sent if s["channel"] == "email")
    failed_n = sum(1 for s in sent if s["status"] in ("failed", "undelivered", "bounced"))
    return {"day": d.isoformat(), "texts_sent": texts_n, "emails_sent": emails_n, "failed": failed_n,
            "coming_up": len(coming), "next_day": later, "sent": sent, "upcoming": coming,
            "note": "Texts go out within about 5 minutes of their time; emails within about 10 minutes."}
