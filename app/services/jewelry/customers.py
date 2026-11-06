"""Customer Vault - customers are Leads in THIS workspace, read through lead scope.

Every list, count and export starts from lead_scope.authorized_lead_query and
is then narrowed to the acting organization, so a staff member sees their own
book and an owner/manager sees the business. Nothing here sends a message.
"""
import csv
import io
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from app.models.jewelry_models import JewelryAppointmentDetail, JewelryTransaction
from app.models.models import BookingLink, Lead, SuppressionEntry, SuppressionSource, User
from app.models.work_models import LeadNote, LeadTask
from app.services import lead_scope
from app.services.jewelry import common as C

MAX_IMPORT_ROWS = 5000


def to_e164(raw: Optional[str]) -> Optional[str]:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


def clean_email(raw: Optional[str]) -> Optional[str]:
    e = (raw or "").strip().lower()
    return e if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e) else None


# ── reading ────────────────────────────────────────────────────────────────
def scoped_query(ctx: C.Ctx):
    return (lead_scope.authorized_lead_query(ctx.db, ctx.user, request=ctx.request)
            .filter(Lead.organization_id == ctx.org_id))


def load(ctx: C.Ctx, lead_id: str) -> Lead:
    lead = lead_scope.load_lead_in_scope(ctx.db, ctx.user, lead_id, ctx.request)
    if lead.organization_id != ctx.org_id:
        raise HTTPException(404, "Customer not found")
    return lead


def activity_index(ctx: C.Ctx, lead_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Per customer: recorded transactions and completed visits (real rows only)."""
    out: Dict[str, Dict[str, Any]] = {i: {"txn_count": 0, "txn_total": 0.0, "last_txn": None,
                                          "visits": 0, "last_visit": None} for i in lead_ids}
    if not lead_ids:
        return out
    for t in (ctx.db.query(JewelryTransaction)
              .filter(JewelryTransaction.organization_id == ctx.org_id,
                      JewelryTransaction.lead_id.in_(lead_ids)).all()):
        a = out[t.lead_id]
        a["txn_count"] += 1
        a["txn_total"] += float(t.amount or 0)
        if a["last_txn"] is None or t.occurred_on > a["last_txn"]:
            a["last_txn"] = t.occurred_on
    rows = (ctx.db.query(BookingLink, JewelryAppointmentDetail)
            .join(JewelryAppointmentDetail, JewelryAppointmentDetail.booking_link_id == BookingLink.id)
            .filter(JewelryAppointmentDetail.organization_id == ctx.org_id,
                    JewelryAppointmentDetail.status == "completed",
                    BookingLink.lead_id.in_(lead_ids)).all())
    for b, _d in rows:
        a = out[b.lead_id]
        a["visits"] += 1
        if b.booked_time and (a["last_visit"] is None or b.booked_time > a["last_visit"]):
            a["last_visit"] = b.booked_time
    return out


def last_seen(lead: Lead, act: Dict[str, Any]) -> Optional[datetime]:
    cands = [act.get("last_txn"), act.get("last_visit")]
    lv = C.custom(lead).get(C.CF_LAST_VISIT)
    if lv:
        try:
            cands.append(datetime.fromisoformat(str(lv)[:10]))
        except ValueError:
            pass
    cands = [c for c in cands if c]
    return max(cands) if cands else None


def is_inactive(lead: Lead, act: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=C.INACTIVE_DAYS)
    seen = last_seen(lead, act)
    if seen is not None:
        return seen < cutoff
    return bool(lead.created_at and lead.created_at < cutoff)


def is_returning(act: Dict[str, Any]) -> bool:
    return act.get("txn_count", 0) >= 2 or act.get("visits", 0) >= 2


def opted_out(lead: Lead) -> bool:
    return lead.status == "dnc" or C.custom(lead).get(C.CF_FOLLOW_UP) == "do_not_contact"


def segments_of(lead: Lead, act: Dict[str, Any]) -> List[str]:
    segs = [s for s in C.custom(lead).get(C.CF_SEGMENTS) or [] if s in C.ASSIGNABLE_SEGMENTS]
    if is_inactive(lead, act):
        segs.append("inactive")
    return segs or ["general"]


def customer_json(lead: Lead, act: Dict[str, Any], names: Dict[str, str]) -> Dict[str, Any]:
    cf = C.custom(lead)
    seen = last_seen(lead, act)
    return {
        "id": lead.id,
        "first_name": lead.first_name, "last_name": lead.last_name, "name": C.lead_name(lead),
        "phone": lead.phone_raw or lead.phone, "email": lead.email,
        "street_address": lead.street_address, "city": lead.city, "state": lead.state, "zip_code": lead.zip_code,
        "segments": segments_of(lead, act),
        "assigned_segments": [s for s in cf.get(C.CF_SEGMENTS) or [] if s in C.ASSIGNABLE_SEGMENTS],
        "interests": cf.get(C.CF_INTERESTS) or [],
        "follow_up": cf.get(C.CF_FOLLOW_UP) or "new",
        "follow_up_at": cf.get(C.CF_FOLLOW_UP_AT),
        "assigned_to_id": lead.assigned_to_id, "assigned_to_name": names.get(lead.assigned_to_id or ""),
        "allow_sms": bool(lead.allow_sms) and not opted_out(lead),
        "allow_email": bool(lead.allow_email) and not opted_out(lead),
        "sms_consent": bool(lead.sms_consent),
        "permission_source": lead.permission_source,
        "opted_out": opted_out(lead),
        "notes": lead.notes,
        "source": lead.source,
        "last_seen": C.iso(seen),
        "inactive": is_inactive(lead, act), "returning": is_returning(act),
        "transactions": act.get("txn_count", 0), "recorded_total": round(act.get("txn_total", 0.0), 2),
        "completed_visits": act.get("visits", 0),
        "created_at": C.iso(lead.created_at),
        "is_test": bool(lead.is_test),
    }


def team_names(ctx: C.Ctx) -> Dict[str, str]:
    from app.services.jewelry.team import members
    return {m["id"]: m["name"] for m in members(ctx.db, ctx.org_id)}


def list_customers(ctx: C.Ctx, q: Optional[str], segment: Optional[str], follow_up: Optional[str],
                   interest: Optional[str], limit: int = 200, offset: int = 0) -> Dict[str, Any]:
    leads = scoped_query(ctx).order_by(Lead.created_at.desc()).all()
    act = activity_index(ctx, [l.id for l in leads])
    names = team_names(ctx)
    rows = [customer_json(l, act[l.id], names) for l in leads]
    counts = {k: 0 for k in C.SEGMENT_KEYS}
    for r in rows:
        for s in r["segments"]:
            counts[s] = counts.get(s, 0) + 1
    if segment and segment != "all":
        rows = [r for r in rows if segment in r["segments"]]
    if follow_up and follow_up != "all":
        rows = [r for r in rows if r["follow_up"] == follow_up]
    if interest:
        il = interest.strip().lower()
        rows = [r for r in rows if any(il in (i or "").lower() for i in r["interests"])]
    if q:
        ql = q.strip().lower()
        digits = re.sub(r"\D", "", ql)

        def hit(r):
            hay = " ".join(str(r.get(k) or "") for k in ("name", "email", "city", "notes")).lower()
            hay += " " + " ".join(r["interests"]).lower()
            if ql in hay:
                return True
            return bool(digits) and len(digits) >= 3 and digits in re.sub(r"\D", "", r.get("phone") or "")
        rows = [r for r in rows if hit(r)]
    total = len(rows)
    return {"total": total, "all_total": len(leads), "segment_counts": counts,
            "customers": rows[offset:offset + max(1, min(limit, 500))]}


# ── writing ────────────────────────────────────────────────────────────────
def _assignable(ctx: C.Ctx, user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    from app.services.jewelry.team import member_ids
    if user_id not in member_ids(ctx.db, ctx.org_id):
        raise HTTPException(404, "That staff member is not on this workspace's team")
    return user_id


def find_duplicate(ctx: C.Ctx, phone: Optional[str], email: Optional[str],
                   exclude_id: Optional[str] = None) -> Optional[Lead]:
    """Duplicates are checked across the WHOLE business (not only the caller's
    book), but only an id is revealed - never another person's record."""
    q = ctx.db.query(Lead).filter(Lead.organization_id == ctx.org_id)
    if exclude_id:
        q = q.filter(Lead.id != exclude_id)
    if phone:
        hit = q.filter(Lead.phone == phone).first()
        if hit:
            return hit
    if email:
        return q.filter(Lead.email == email).first()
    return None


def apply_fields(ctx: C.Ctx, lead: Lead, data: Dict[str, Any], creating: bool) -> List[str]:
    changed = []
    for k in ("first_name", "last_name", "street_address", "city", "state", "zip_code", "notes"):
        if k in data:
            v = (data[k] or "").strip() or None
            if k == "state" and v:
                v = v[:2].upper()
            setattr(lead, k, v)
            changed.append(k)
    if "phone" in data:
        raw = (data["phone"] or "").strip()
        e164 = to_e164(raw) if raw else None
        if raw and not e164:
            raise HTTPException(400, "Phone number must be a 10-digit US number")
        lead.phone, lead.phone_raw = e164, (raw or None)
        changed.append("phone")
    if "email" in data:
        raw = (data["email"] or "").strip()
        e = clean_email(raw) if raw else None
        if raw and not e:
            raise HTTPException(400, "That email address doesn't look right")
        lead.email = e
        changed.append("email")
    cf_updates = {}
    if "segments" in data:
        cf_updates[C.CF_SEGMENTS] = [s for s in (data["segments"] or []) if s in C.ASSIGNABLE_SEGMENTS]
    if "interests" in data:
        cf_updates[C.CF_INTERESTS] = C.clean_list(data["interests"])
    if "follow_up" in data and data["follow_up"]:
        if data["follow_up"] not in C.FOLLOW_UP_STATUSES:
            raise HTTPException(400, "Unknown follow-up status")
        if data["follow_up"] == "do_not_contact":
            opt_out(ctx, lead, "Marked do not contact by staff")
        cf_updates[C.CF_FOLLOW_UP] = data["follow_up"]
    if "last_visit" in data:
        lv = (data["last_visit"] or "").strip()[:10] or None
        if lv:
            try:
                datetime.fromisoformat(lv)
            except ValueError:
                raise HTTPException(400, "last_visit must be YYYY-MM-DD")
        cf_updates[C.CF_LAST_VISIT] = lv
    if cf_updates:
        C.set_custom(lead, **cf_updates)
        changed += sorted(cf_updates)
    if "assigned_to_id" in data:
        if not ctx.manager and data["assigned_to_id"] not in (None, ctx.user.id):
            raise HTTPException(403, "Only an owner or manager can assign customers to someone else")
        lead.assigned_to_id = _assignable(ctx, data["assigned_to_id"])
        changed.append("assigned_to_id")
    # Contact permission. Turning a channel ON requires saying where the
    # permission came from; an opted-out customer can never be turned back on here.
    for chan in ("allow_sms", "allow_email"):
        if chan in data and data[chan] is not None:
            want = bool(data[chan])
            if want and opted_out(lead):
                raise HTTPException(409, "This customer opted out. Contact permission can't be turned back on here.")
            if want and not (data.get("permission_source") or lead.permission_source):
                raise HTTPException(400, "Say where the customer's permission came from before allowing marketing.")
            setattr(lead, chan, want)
            changed.append(chan)
            if chan == "allow_sms":
                lead.sms_consent = want
                lead.sms_consent_timestamp = datetime.utcnow() if want else lead.sms_consent_timestamp
                lead.sms_consent_source = (data.get("permission_source") or lead.permission_source) if want else lead.sms_consent_source
    if data.get("permission_source"):
        lead.permission_source = str(data["permission_source"]).strip()[:200]
        changed.append("permission_source")
    return changed


def create(ctx: C.Ctx, data: Dict[str, Any]) -> Lead:
    if not ((data.get("first_name") or "").strip() or (data.get("last_name") or "").strip()):
        raise HTTPException(400, "A customer needs at least a first or last name")
    lead = Lead(organization_id=ctx.org_id, status="new", source=(data.get("source") or "manual")[:60],
                source_detail="Gold & Jewelry Customer Vault", contact_channel="sms",
                allow_sms=False, allow_email=False, sms_consent=False,
                assigned_to_id=None if ctx.manager else ctx.user.id,
                relationship_type="customer")
    apply_fields(ctx, lead, data, creating=True)
    dup = find_duplicate(ctx, lead.phone, lead.email)
    if dup and not data.get("allow_duplicate"):
        raise HTTPException(409, {"message": "A customer with this phone or email already exists.",
                                  "duplicate_id": dup.id})
    if not C.custom(lead).get(C.CF_FOLLOW_UP):
        C.set_custom(lead, **{C.CF_FOLLOW_UP: "new"})
    ctx.db.add(lead)
    ctx.db.flush()
    return lead


def opt_out(ctx: C.Ctx, lead: Lead, reason: str) -> None:
    """One-way here: status dnc, every channel off, phone on the suppression list."""
    lead.status = "dnc"
    lead.allow_sms = False
    lead.allow_email = False
    lead.allow_bulk_email = False
    lead.allow_voice = False
    C.set_custom(lead, **{C.CF_FOLLOW_UP: "do_not_contact"})
    if lead.phone:
        exists = (ctx.db.query(SuppressionEntry)
                  .filter(SuppressionEntry.organization_id == ctx.org_id,
                          SuppressionEntry.phone == lead.phone).first())
        if not exists:
            ctx.db.add(SuppressionEntry(organization_id=ctx.org_id, phone=lead.phone,
                                        reason=reason[:300], source=SuppressionSource.MANUAL))


def add_note(ctx: C.Ctx, lead: Lead, body: str) -> LeadNote:
    body = (body or "").strip()
    if not body:
        raise HTTPException(400, "Write the note first")
    n = LeadNote(organization_id=ctx.org_id, lead_id=lead.id, author_user_id=ctx.user.id,
                 body=body[:4000], kind="note")
    ctx.db.add(n)
    ctx.db.flush()
    return n


def add_follow_up(ctx: C.Ctx, lead: Lead, title: str, due_at: Optional[datetime],
                  assigned_to_id: Optional[str]) -> LeadTask:
    title = (title or "").strip()
    if not title:
        raise HTTPException(400, "Say what the follow-up is")
    assignee = _assignable(ctx, assigned_to_id) if assigned_to_id else (lead.assigned_to_id or ctx.user.id)
    if not ctx.manager and assignee != ctx.user.id:
        raise HTTPException(403, "Only an owner or manager can assign follow-ups to someone else")
    t = LeadTask(organization_id=ctx.org_id, lead_id=lead.id, title=title[:200], due_at=due_at,
                 status="open", assigned_to_id=assignee, created_by_id=ctx.user.id, source="manual")
    ctx.db.add(t)
    C.set_custom(lead, **{C.CF_FOLLOW_UP: "follow_up_due",
                          C.CF_FOLLOW_UP_AT: C.iso(due_at) if due_at else None})
    ctx.db.flush()
    return t


def history(ctx: C.Ctx, lead: Lead) -> Dict[str, Any]:
    from app.services.jewelry.team import members
    names = {m["id"]: m["name"] for m in members(ctx.db, ctx.org_id)}
    author_ids = set()
    notes = (ctx.db.query(LeadNote).filter(LeadNote.organization_id == ctx.org_id, LeadNote.lead_id == lead.id)
             .order_by(LeadNote.created_at.desc()).limit(200).all())
    tasks = (ctx.db.query(LeadTask).filter(LeadTask.organization_id == ctx.org_id, LeadTask.lead_id == lead.id)
             .order_by(LeadTask.created_at.desc()).limit(200).all())
    txns = (ctx.db.query(JewelryTransaction).filter(JewelryTransaction.organization_id == ctx.org_id,
                                                    JewelryTransaction.lead_id == lead.id)
            .order_by(JewelryTransaction.occurred_on.desc()).limit(200).all())
    for x in notes:
        author_ids.add(x.author_user_id)
    if author_ids - set(names):
        for u in ctx.db.query(User).filter(User.id.in_([a for a in author_ids if a])).all():
            names.setdefault(u.id, u.full_name or u.email)
    from app.services.jewelry.appointments import list_for_lead
    comms = []
    try:
        from app.models.models import EmailMessage, Message, Reply
        for m in (ctx.db.query(Reply).filter(Reply.lead_id == lead.id)
                  .order_by(Reply.received_at.desc()).limit(50).all()):
            comms.append({"channel": m.source or "sms", "direction": "inbound", "body": (m.body or "")[:600],
                          "status": "received", "at": C.iso(m.received_at)})
        for m in (ctx.db.query(Message).filter(Message.lead_id == lead.id)
                  .order_by(Message.sent_at.desc()).limit(50).all()):
            comms.append({"channel": "sms", "direction": "outbound", "body": (m.body or "")[:600],
                          "status": m.delivery_status or m.twilio_status, "at": C.iso(m.sent_at)})
        for m in (ctx.db.query(EmailMessage).filter(EmailMessage.lead_id == lead.id)
                  .order_by(EmailMessage.sent_at.desc()).limit(50).all()):
            comms.append({"channel": "email", "direction": "outbound", "body": (m.subject or "")[:300],
                          "status": m.status, "at": C.iso(m.sent_at)})
    except Exception:  # noqa: BLE001 - history must render even if a message table differs
        comms = []
    comms.sort(key=lambda x: x.get("at") or "", reverse=True)
    return {
        "notes": [{"id": n.id, "body": n.body, "author": names.get(n.author_user_id or "", "Staff"),
                   "at": C.iso(n.created_at)} for n in notes],
        "follow_ups": [{"id": t.id, "title": t.title, "due_at": C.iso(t.due_at), "status": t.status,
                        "assigned_to_name": names.get(t.assigned_to_id or "", None),
                        "completed_at": C.iso(t.completed_at)} for t in tasks],
        "transactions": [txn_json(t) for t in txns],
        "appointments": list_for_lead(ctx, lead.id),
        "communications": comms,
    }


def txn_json(t: JewelryTransaction) -> Dict[str, Any]:
    return {"id": t.id, "lead_id": t.lead_id, "kind": t.kind, "occurred_on": C.iso(t.occurred_on),
            "description": t.description, "amount": round(float(t.amount or 0), 2),
            "campaign_id": t.campaign_id, "item_id": t.item_id, "source": t.source}


def record_transaction(ctx: C.Ctx, lead: Lead, kind: str, occurred_on: Optional[str], amount: float,
                       description: Optional[str], campaign_id: Optional[str], item_id: Optional[str]) -> JewelryTransaction:
    if kind not in ("sale", "purchase", "trade"):
        raise HTTPException(400, "Transaction type must be sale, purchase or trade")
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        raise HTTPException(400, "Amount must be a number")
    if amt < 0 or amt > 10_000_000:
        raise HTTPException(400, "Amount must be between 0 and 10,000,000")
    when = datetime.utcnow()
    if occurred_on:
        try:
            when = datetime.fromisoformat(occurred_on[:10])
        except ValueError:
            raise HTTPException(400, "Date must be YYYY-MM-DD")
    if when > datetime.utcnow() + timedelta(days=1):
        raise HTTPException(400, "A transaction can't be in the future")
    if campaign_id:
        from app.models.jewelry_models import JewelryCampaign
        if not ctx.db.query(JewelryCampaign.id).filter(JewelryCampaign.id == campaign_id,
                                                       JewelryCampaign.organization_id == ctx.org_id).first():
            raise HTTPException(404, "Campaign not found")
    if item_id:
        from app.models.jewelry_models import JewelryItem
        if not ctx.db.query(JewelryItem.id).filter(JewelryItem.id == item_id,
                                                   JewelryItem.organization_id == ctx.org_id).first():
            raise HTTPException(404, "Item not found")
    t = JewelryTransaction(organization_id=ctx.org_id, lead_id=lead.id, kind=kind, occurred_on=when,
                           amount=round(amt, 2), description=(description or "").strip()[:300] or None,
                           campaign_id=campaign_id, item_id=item_id, recorded_by=ctx.user.id, source="manual")
    ctx.db.add(t)
    if "previous" not in (C.custom(lead).get(C.CF_SEGMENTS) or []):
        C.set_custom(lead, **{C.CF_SEGMENTS: (C.custom(lead).get(C.CF_SEGMENTS) or []) + ["previous"]})
    ctx.db.flush()
    return t


# ── export ─────────────────────────────────────────────────────────────────
EXPORT_COLUMNS = ["first_name", "last_name", "phone", "email", "city", "state", "segments", "interests",
                  "follow_up", "allow_sms", "allow_email", "opted_out", "last_seen", "transactions",
                  "recorded_total", "created_at"]


def export_csv(ctx: C.Ctx) -> str:
    C.require_manager(ctx)
    data = list_customers(ctx, None, None, None, None, limit=500000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLUMNS)
    for r in data["customers"]:
        w.writerow([";".join(r[k]) if isinstance(r[k], list) else ("" if r[k] is None else r[k])
                    for k in EXPORT_COLUMNS])
    return buf.getvalue()


# ── import (preview, then confirm - stateless: the file is sent both times) ─
HEADER_MAP = {
    "first_name": ["first name", "first", "firstname", "given name", "fname"],
    "last_name": ["last name", "last", "lastname", "surname", "family name", "lname"],
    "full_name": ["name", "full name", "customer", "customer name", "contact"],
    "phone": ["phone", "phone number", "mobile", "cell", "cell phone", "telephone", "mobile phone"],
    "email": ["email", "e-mail", "email address"],
    "street_address": ["address", "street", "street address", "address 1"],
    "city": ["city", "town"],
    "state": ["state", "st", "province"],
    "zip_code": ["zip", "zip code", "postal code", "zipcode"],
    "segments": ["segment", "segments", "customer type", "type", "category"],
    "interests": ["interest", "interests", "notes on interest", "collects", "likes"],
    "notes": ["notes", "note", "comments", "comment"],
    "last_visit": ["last visit", "last purchase", "last transaction", "last seen", "last visit date"],
    "sms_permission": ["sms permission", "sms opt in", "text opt in", "sms consent", "texts ok", "allow sms"],
    "email_permission": ["email permission", "email opt in", "email consent", "allow email", "emails ok"],
}
SEGMENT_WORDS = {
    "gold_seller": ["gold seller", "gold sellers", "seller", "sold gold", "gold"],
    "jewelry_buyer": ["jewelry buyer", "jewelry buyers", "jewelry", "buyer"],
    "coin_collector": ["coin collector", "coin collectors", "coins", "coin", "collector"],
    "previous": ["previous", "previous customer", "past customer"],
    "vip": ["vip", "v.i.p."],
    "general": ["general", "other"],
}
YES = {"yes", "y", "true", "1", "opted in", "opt in", "ok", "x"}


def parse_file(filename: str, data: bytes) -> Tuple[List[str], List[List[str]]]:
    name = (filename or "").lower()
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        try:
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            ws = wb.worksheets[0]
            rows = [["" if v is None else str(v).strip() for v in r] for r in ws.iter_rows(values_only=True)]
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "That spreadsheet couldn't be read. Save it as .xlsx or .csv and try again.")
    elif name.endswith(".csv") or name.endswith(".txt"):
        text = None
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text or ""))]
    else:
        raise HTTPException(400, "Upload a .csv or .xlsx file")
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        raise HTTPException(400, "That file has no rows")
    header, body = rows[0], rows[1:]
    if len(body) > MAX_IMPORT_ROWS:
        raise HTTPException(400, "That file has %d rows. Import at most %d at a time." % (len(body), MAX_IMPORT_ROWS))
    return header, body


def auto_map(header: List[str]) -> Dict[str, Optional[int]]:
    norm = [re.sub(r"[^a-z0-9 ]", " ", (h or "").lower()).strip() for h in header]
    norm = [re.sub(r"\s+", " ", h) for h in norm]
    mapping: Dict[str, Optional[int]] = {}
    for field, names in HEADER_MAP.items():
        idx = next((i for i, h in enumerate(norm) if h in names or h == field.replace("_", " ")), None)
        mapping[field] = idx
    return mapping


def _segments_from(text: str) -> List[str]:
    out = []
    for part in re.split(r"[;,/|]", (text or "").lower()):
        p = part.strip()
        if not p:
            continue
        for key, words in SEGMENT_WORDS.items():
            if p in words and key not in out:
                out.append(key)
                break
    return out


def analyse(ctx: C.Ctx, header: List[str], body: List[List[str]],
            mapping: Optional[Dict[str, Optional[int]]] = None) -> Dict[str, Any]:
    m = auto_map(header) if mapping is None else mapping
    for k, v in list(m.items()):
        if v is not None and (not isinstance(v, int) or v < 0 or v >= len(header)):
            raise HTTPException(400, "Column mapping for %s points at a column that isn't in the file" % k)

    def cell(row, field):
        i = m.get(field)
        return row[i].strip() if i is not None and i < len(row) else ""

    existing_phone = {p for (p,) in ctx.db.query(Lead.phone).filter(Lead.organization_id == ctx.org_id,
                                                                     Lead.phone.isnot(None)).all()}
    existing_email = {e for (e,) in ctx.db.query(Lead.email).filter(Lead.organization_id == ctx.org_id,
                                                                     Lead.email.isnot(None)).all()}
    suppressed = {p for (p,) in ctx.db.query(SuppressionEntry.phone)
                  .filter(SuppressionEntry.organization_id == ctx.org_id).all()}
    seen_phone, seen_email = set(), set()
    rows = []
    for n, r in enumerate(body, start=2):
        first, last = cell(r, "first_name"), cell(r, "last_name")
        if not (first or last) and cell(r, "full_name"):
            parts = cell(r, "full_name").split()
            first, last = (parts[0], " ".join(parts[1:])) if parts else ("", "")
        raw_phone, raw_email = cell(r, "phone"), cell(r, "email")
        phone, email = to_e164(raw_phone), clean_email(raw_email)
        issues, status = [], "ready"
        if not (first or last):
            issues.append("No name")
        if raw_phone and not phone:
            issues.append("Phone isn't a valid US number")
        if raw_email and not email:
            issues.append("Email isn't valid")
        if not (first or last) and not phone and not email:
            status = "skip"
        dup_of = None
        if phone and phone in existing_phone or email and email in existing_email:
            status, dup_of = "duplicate", "existing customer"
        elif phone and phone in seen_phone or email and email in seen_email:
            status, dup_of = "duplicate", "earlier row in this file"
        if status == "ready" and issues and not (first or last):
            status = "skip"
        if phone:
            seen_phone.add(phone)
        if email:
            seen_email.add(email)
        sms_ok = cell(r, "sms_permission").lower() in YES
        email_ok = cell(r, "email_permission").lower() in YES
        rows.append({
            "row": n, "status": status, "issues": issues, "duplicate_of": dup_of,
            "first_name": first, "last_name": last, "phone": raw_phone, "phone_e164": phone,
            "email": email or raw_email, "street_address": cell(r, "street_address"), "city": cell(r, "city"),
            "state": cell(r, "state")[:2].upper() if cell(r, "state") else "", "zip_code": cell(r, "zip_code"),
            "segments": _segments_from(cell(r, "segments")),
            "interests": C.clean_list(re.split(r"[;,|]", cell(r, "interests"))) if cell(r, "interests") else [],
            "notes": cell(r, "notes"), "last_visit": cell(r, "last_visit")[:10],
            "sms_permission": sms_ok and not (phone and phone in suppressed),
            "email_permission": email_ok, "suppressed": bool(phone and phone in suppressed),
        })
    summary = {k: sum(1 for x in rows if x["status"] == k) for k in ("ready", "duplicate", "skip")}
    summary["with_issues"] = sum(1 for x in rows if x["issues"])
    summary["sms_permission"] = sum(1 for x in rows if x["sms_permission"] and x["status"] == "ready")
    summary["email_permission"] = sum(1 for x in rows if x["email_permission"] and x["status"] == "ready")
    missing = [f for f in ("phone", "email") if m.get(f) is None]
    return {"header": header, "mapping": m, "fields": list(HEADER_MAP), "rows": rows,
            "total": len(rows), "summary": summary,
            "warnings": (["No phone or email column was found."] if len(missing) == 2 else []) +
                        (["No name column was found."] if m.get("first_name") is None and m.get("last_name") is None
                         and m.get("full_name") is None else [])}


def commit_import(ctx: C.Ctx, analysis: Dict[str, Any], filename: str) -> Dict[str, Any]:
    import uuid
    batch = "jw-" + uuid.uuid4().hex[:12]
    created = 0
    for r in analysis["rows"]:
        if r["status"] != "ready":
            continue
        lead = Lead(organization_id=ctx.org_id, status="new", source="import",
                    source_detail="Gold & Jewelry import", source_file=(filename or "")[:200],
                    import_batch_id=batch, imported_by_name=ctx.user.full_name or ctx.user.email,
                    first_name=r["first_name"] or None, last_name=r["last_name"] or None,
                    phone=r["phone_e164"], phone_raw=r["phone"] or None,
                    email=clean_email(r["email"]), street_address=r["street_address"] or None,
                    city=r["city"] or None, state=r["state"] or None, zip_code=r["zip_code"] or None,
                    notes=r["notes"] or None, contact_channel="sms", relationship_type="customer",
                    assigned_to_id=None if ctx.manager else ctx.user.id,
                    # Permission only where the FILE says so, and recorded as such.
                    allow_sms=bool(r["sms_permission"]), allow_email=bool(r["email_permission"]),
                    sms_consent=bool(r["sms_permission"]),
                    sms_consent_source=("import:%s" % filename)[:200] if r["sms_permission"] else None,
                    sms_consent_timestamp=datetime.utcnow() if r["sms_permission"] else None,
                    permission_source=("import:%s" % filename)[:200] if (r["sms_permission"] or r["email_permission"]) else None,
                    permission_review=bool(r["sms_permission"] or r["email_permission"]))
        cf = {C.CF_FOLLOW_UP: "new", C.CF_SEGMENTS: r["segments"], C.CF_INTERESTS: r["interests"]}
        if r["last_visit"]:
            try:
                datetime.fromisoformat(r["last_visit"])
                cf[C.CF_LAST_VISIT] = r["last_visit"]
            except ValueError:
                pass
        lead.custom_fields = C.jdump(cf)
        ctx.db.add(lead)
        created += 1
    ctx.db.flush()
    return {"batch_id": batch, "created": created, "skipped_duplicates": analysis["summary"]["duplicate"],
            "skipped_invalid": analysis["summary"]["skip"], "messages_sent": 0}
