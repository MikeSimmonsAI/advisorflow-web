"""EvoSys Sales campaigns: one follow-up per industry + market group.

WHAT A CAMPAIGN OWNS: its audience (industry_key / market_key; None = any),
its email steps, offer, booking link, daily limit, send days and hours, and
its own On/Off switch. The team settings keep the sender name, From, Reply-To
and mailing address, shared by every campaign.

APPROVAL IS FOR AN EXACT VERSION. content_hash() covers everything that would
change what goes out: the campaign's copy, offer, link, schedule and limit and
the team's sender, From, Reply-To and mailing address. approve() stores that
hash. A campaign sends only while sending_on is true AND its current hash
equals the approved hash, so any later change - to the campaign or to the
team settings - stops it at the next send check, before anything goes out.
Saving a change also turns the switch off and bumps the version, so the
screen says plainly that it needs approving and turning on again.

EXISTING WORK IS KEPT. The first time campaigns are used, a default campaign
("All leads") is made from the existing settings: the same steps, offer,
link, daily limit (whatever is stored), the existing send window (weekdays
8-18 Central, which was fixed in code), and the same approval and switch
state. Every existing enrolment is pointed at it with its step and due time
unchanged, so nobody restarts and nothing is queued twice.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.evosys_sales_models import EvoSysCampaign, EvoSysLead, EvoSysOutreachState

CONTENT_FIELDS = ("steps", "offer", "booking_url", "daily_limit", "send_days", "send_start_hour",
                  "send_end_hour", "timezone", "industry_key", "market_key")
TEAM_FIELDS = ("sender_name", "from_email", "reply_to_email", "mailing_address", "sms_from_number")
OFFER_PLACEHOLDER_RE = re.compile(r"^\s*\[your offer", re.I)


def real_offer(text: Optional[str]) -> Optional[str]:
    """An offer someone actually wrote. The old placeholder ("[Your offer goes
    here: ...]") is not an offer: it is never sent and never quoted."""
    t = (text or "").strip()
    return None if (not t or OFFER_PLACEHOLDER_RE.match(t)) else t


def content_hash(c: EvoSysCampaign, settings) -> str:
    body = {k: getattr(c, k) for k in CONTENT_FIELDS}
    body.update({k: getattr(settings, k, None) for k in TEAM_FIELDS})
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def is_approved(c: EvoSysCampaign, settings) -> bool:
    return bool(c.approved_hash) and c.approved_hash == content_hash(c, settings)


def can_send(c: EvoSysCampaign, settings) -> bool:
    return bool(c.sending_on) and is_approved(c, settings) and not problems(c, settings)


def ensure_default(db: Session, settings) -> EvoSysCampaign:
    bso = settings.brand_sales_org_id
    c = (db.query(EvoSysCampaign).filter(EvoSysCampaign.brand_sales_org_id == bso,
                                         EvoSysCampaign.is_default.is_(True)).first())
    if c is None:
        from app.services.evosys_sales.outreach import team_lock
        team_lock(db, bso)
        c = (db.query(EvoSysCampaign).populate_existing()
             .filter(EvoSysCampaign.brand_sales_org_id == bso, EvoSysCampaign.is_default.is_(True)).first())
    if c is None:
        c = EvoSysCampaign(brand_sales_org_id=bso, name="All leads", is_default=True,
                           industry_key=None, market_key=None,
                           steps=[dict(s) for s in (settings.steps or [])], offer=settings.offer,
                           booking_url=settings.booking_url, daily_limit=settings.daily_limit or 60,
                           send_days="0,1,2,3,4", send_start_hour=8, send_end_hour=18,
                           timezone="America/Chicago", sending_on=bool(settings.sending_on),
                           draft_origin="manual", version=1)
        db.add(c)
        db.flush()
        if settings.approved_at:
            # The existing approval carries over for exactly the content that was approved.
            c.approved_hash, c.approved_version = content_hash(c, settings), 1
            c.approved_at, c.approved_by = settings.approved_at, settings.approved_by
    # Map existing enrolments (step and due time unchanged).
    (db.query(EvoSysOutreachState)
     .filter(EvoSysOutreachState.brand_sales_org_id == bso, EvoSysOutreachState.campaign_id.is_(None))
     .update({EvoSysOutreachState.campaign_id: c.id}, synchronize_session=False))
    db.flush()
    return c


def all_for(db: Session, settings) -> List[EvoSysCampaign]:
    ensure_default(db, settings)
    return (db.query(EvoSysCampaign).filter(EvoSysCampaign.brand_sales_org_id == settings.brand_sales_org_id)
            .order_by(EvoSysCampaign.is_default.desc(), EvoSysCampaign.created_at).all())


def campaign_for(db: Session, settings, lead: Optional[EvoSysLead]) -> Optional[EvoSysCampaign]:
    """The most specific campaign whose audience fits this lead: industry and
    market > industry only / market only > the default. Ties go to the oldest."""
    best, score = None, -1
    for c in all_for(db, settings):
        if (c.kind or "group") != "group" or c.archived_at is not None:
            continue          # picked campaigns only email their own audience
        if c.industry_key and (lead is None or lead.industry_key != c.industry_key):
            continue
        if c.market_key and (lead is None or lead.market_key != c.market_key):
            continue
        s = (2 if c.industry_key else 0) + (2 if c.market_key else 0) + (0 if c.is_default else 1)
        if s > score:
            best, score = c, s
    return best


def problems(c: EvoSysCampaign, settings) -> List[str]:
    from app.services.evosys_sales import outreach as O
    out = [p for p in O.settings_problems(settings)
           if not p.startswith(("Write the offer", "Add the booking link", "Add at least one step", "Step ",
                                "Add a mailing address"))]
    steps = c.steps or []
    # The mailing address belongs in sales EMAILS; a texts-only campaign needs none.
    if any(x.get("channel") != "text" for x in steps) and not (settings.mailing_address or "").strip():
        out.append("Add a mailing address. The law requires one in every sales email.")
    for i, x in enumerate(steps, 1):
        if x.get("channel") == "text" and len(O._with_stop(x.get("body") or "")) > O.TEXT_MAX:
            out.append("Step %d text is too long (keep it under %d characters)." % (i, O.TEXT_MAX - 40))
    if not steps:
        out.append("Add at least one step.")
    uses_offer = any("{offer}" in ((x.get("subject") or "") + (x.get("body") or "")) for x in steps)
    if uses_offer and not real_offer(c.offer):
        out.append("Write the offer.")
    from app.services.evosys_sales import facts as F
    unknown = sorted({k for x in steps for k in F.placeholders_in((x.get("subject") or "") + (x.get("body") or ""))}
                     - set(F.PLACEHOLDERS))
    if unknown:
        out.append("Unknown fill-in: %s." % ", ".join("{%s}" % k for k in unknown))
    if not (c.booking_url or "").strip().lower().startswith("http"):
        out.append("Add the booking link.")
    for i, st in enumerate(steps, 1):
        if st.get("channel") == "email" and not (st.get("subject") or "").strip():
            out.append("Step %d needs a subject." % i)
        if not (st.get("body") or "").strip():
            out.append("Step %d needs a message." % i)
    if not (0 <= int(c.send_start_hour) < int(c.send_end_hour) <= 24):
        out.append("Sending hours must start before they end.")
    if not days(c):
        out.append("Pick at least one sending day.")
    return out


def days(c: EvoSysCampaign) -> List[int]:
    return sorted({int(x) for x in re.findall(r"\d", c.send_days or "") if int(x) <= 6})


def in_window(c: EvoSysCampaign, now_utc: datetime) -> bool:
    local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(c.timezone or "America/Chicago"))
    return local.weekday() in days(c) and int(c.send_start_hour) <= local.hour < int(c.send_end_hour)


def save(db: Session, c: EvoSysCampaign, data: Dict[str, Any], user_id: str, origin: Optional[str] = None) -> bool:
    """Apply edits. Any change to what would be sent stops the campaign and
    needs a new approval. Returns True when content changed."""
    from app.services.evosys_sales.outreach import _clean_step
    changed = False
    for k in ("name",) + CONTENT_FIELDS:
        if k not in data or data[k] is None and k not in ("industry_key", "market_key"):
            continue
        v = data[k]
        if k == "steps":
            v = [_clean_step(x) for x in (v or [])][:10]
        elif k == "daily_limit":
            v = max(1, min(int(v), 300))
        elif k in ("send_start_hour", "send_end_hour"):
            v = max(0, min(int(v), 24))
        elif k == "send_days":
            v = ",".join(str(d) for d in sorted({int(x) for x in re.findall(r"\d", str(v)) if int(x) <= 6}))
        elif k == "name":
            v = str(v).strip()[:200] or c.name
        elif k in ("industry_key", "market_key"):
            if c.is_default:
                continue          # the default campaign's audience is everyone else
            v = (v or None)
        if getattr(c, k) != v:
            setattr(c, k, v)
            changed = changed or k != "name"
    if changed:
        c.version = (c.version or 1) + 1
        c.sending_on = False
        if origin:
            c.draft_origin = origin
        elif c.draft_origin == "ai":
            c.draft_origin = "ai_edited"
    c.updated_at, c.updated_by = datetime.utcnow(), user_id
    db.flush()
    return changed


def approve(db: Session, c: EvoSysCampaign, settings, user_id: str) -> None:
    p = problems(c, settings)
    if p:
        raise ValueError(" ".join(p))
    c.approved_hash, c.approved_version = content_hash(c, settings), c.version
    c.approved_at, c.approved_by = datetime.utcnow(), user_id
    db.flush()


def switch(db: Session, c: EvoSysCampaign, settings, on: bool, user_id: str) -> None:
    if on and not is_approved(c, settings):
        raise ValueError("Approve this exact version before turning it on.")
    if on and problems(c, settings):
        raise ValueError(" ".join(problems(c, settings)))
    if on and c.archived_at is not None:
        raise ValueError("This campaign is archived.")
    c.sending_on = bool(on)
    c.updated_at, c.updated_by = datetime.utcnow(), user_id
    if on:
        start_queued(db, c)
    db.flush()


def start_queued(db: Session, c: EvoSysCampaign, now: Optional[datetime] = None) -> int:
    """A picked campaign turned on: its waiting audience starts at message 1.
    Contacts that already started keep their place (nothing is sent twice)."""
    now = now or datetime.utcnow()
    n = 0
    for st in db.query(EvoSysOutreachState).filter(EvoSysOutreachState.campaign_id == c.id,
                                                   EvoSysOutreachState.status == "queued").all():
        st.status, st.next_step, st.next_due_at, st.started_at = "active", 0, now, now
        n += 1
    db.flush()
    return n
