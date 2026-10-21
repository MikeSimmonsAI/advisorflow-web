"""Where each homeowner is in the roofing journey, and how they move.

TWO WAYS A STAGE CHANGES, and both leave a RoofingStageEvent:

  * ACTIVITY (actor_user_id NULL). `sync` reads what has actually happened in
    the shared engines - a text or email that went out, a reply that came in,
    an inspection on the books - and moves a homeowner FORWARD to match. It
    never moves anyone backward, and never into or out of a stage only a
    person can decide (inspected, estimate sent, won, lost).
  * A PERSON, through `set_stage`, for any stage, with a reason.

Nothing here invents an event. A homeowner shows "Contacted" because a
Message or EmailMessage row exists for them, not because a screen said so.
"""
from collections import defaultdict
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import BookingLink, EmailMessage, Lead, Message, Reply, ReplyClassification
from app.models.roofing_models import RoofingOpportunity, RoofingStageEvent
from app.services.roofing import common as C

# Outbound rows that never reached anyone are not "contact".
_DEAD_SMS = ("blocked", "failed")
_DEAD_EMAIL = ("failed",)
OPT_OUT_CLASSES = (ReplyClassification.DNC,)
NOT_ENGAGED_CLASSES = (ReplyClassification.DNC, ReplyClassification.WRONG_NUMBER)


def _chunks(seq: List[str], n: int = 800):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


class Signals:
    """Everything that happened to a set of leads, read once, in bulk."""

    def __init__(self, db: Session, lead_ids: Iterable[str]):
        ids = list(lead_ids)
        self.outbound: Dict[str, List[datetime]] = defaultdict(list)   # lead -> send times
        self.replies: Dict[str, List[Reply]] = defaultdict(list)
        self.bookings: Dict[str, List[BookingLink]] = defaultdict(list)
        self.offer_msgs: Set[str] = set()
        for chunk in _chunks(ids):
            for lid, at, state, blid in (db.query(Message.lead_id, Message.sent_at, Message.send_state,
                                                  Message.booking_link_id)
                                         .filter(Message.lead_id.in_(chunk)).all()):
                if (state or "") in _DEAD_SMS:
                    continue
                self.outbound[lid].append(at or datetime.utcnow())
                if blid:
                    self.offer_msgs.add(lid)
            for lid, at, status in (db.query(EmailMessage.lead_id, EmailMessage.sent_at, EmailMessage.status)
                                    .filter(EmailMessage.lead_id.in_(chunk)).all()):
                if (status or "") in _DEAD_EMAIL:
                    continue
                self.outbound[lid].append(at or datetime.utcnow())
            for r in db.query(Reply).filter(Reply.lead_id.in_(chunk)).all():
                self.replies[r.lead_id].append(r)
            for b in db.query(BookingLink).filter(BookingLink.lead_id.in_(chunk)).all():
                self.bookings[b.lead_id].append(b)
        for v in self.outbound.values():
            v.sort()
        for v in self.replies.values():
            v.sort(key=lambda r: r.received_at or datetime.min)

    def contacted(self, lid: str) -> bool:
        return bool(self.outbound.get(lid))

    def engaged(self, lid: str) -> bool:
        return any(r.classification not in NOT_ENGAGED_CLASSES for r in self.replies.get(lid, ()))

    def opted_out(self, lid: str) -> bool:
        return any(r.classification in OPT_OUT_CLASSES for r in self.replies.get(lid, ()))

    def booked(self, lid: str) -> Optional[BookingLink]:
        live = [b for b in self.bookings.get(lid, ())
                if b.status in ("booked", "confirmed") and b.booked_time is not None]
        return max(live, key=lambda b: b.booked_time) if live else None

    def offered(self, lid: str) -> bool:
        return lid in self.offer_msgs or any(b.status == "pending" for b in self.bookings.get(lid, ()))


def _record(db: Session, opp: RoofingOpportunity, to_stage: str, actor: Optional[str],
            reason: Optional[str], now: datetime):
    db.add(RoofingStageEvent(organization_id=opp.organization_id, opportunity_id=opp.id,
                             lead_id=opp.lead_id, from_stage=opp.stage, to_stage=to_stage,
                             actor_user_id=actor, reason=(reason or "")[:300] or None, at=now))
    opp.stage = to_stage
    opp.stage_entered_at = now


def ensure(db: Session, org_id: str, leads: List[Lead], now: Optional[datetime] = None
           ) -> Dict[str, RoofingOpportunity]:
    """Every lead gets exactly one opportunity row (created as Imported)."""
    now = now or datetime.utcnow()
    ids = [l.id for l in leads]
    have: Dict[str, RoofingOpportunity] = {}
    for chunk in _chunks(ids):
        for o in (db.query(RoofingOpportunity)
                  .filter(RoofingOpportunity.organization_id == org_id,
                          RoofingOpportunity.lead_id.in_(chunk)).all()):
            have[o.lead_id] = o
    for l in leads:
        if l.id in have:
            continue
        o = RoofingOpportunity(organization_id=org_id, lead_id=l.id, stage=C.IMPORTED,
                               stage_entered_at=l.created_at or now)
        db.add(o)
        db.flush()
        db.add(RoofingStageEvent(organization_id=org_id, opportunity_id=o.id, lead_id=l.id,
                                 from_stage=None, to_stage=C.IMPORTED, actor_user_id=None,
                                 reason="Imported", at=l.created_at or now))
        have[l.id] = o
    return have


def derived_stage(lid: str, sig: Signals, qualified: Set[str]) -> str:
    if sig.booked(lid):
        return C.BOOKED
    if sig.offered(lid):
        return C.OFFERED
    if sig.engaged(lid):
        return C.ENGAGED
    if sig.contacted(lid):
        return C.CONTACTED
    if lid in qualified:
        return C.QUALIFIED
    return C.IMPORTED


def qualified_ids(db: Session, org_id: str, leads: List[Lead]) -> Set[str]:
    """Leads the platform's own qualification engine calls READY on SMS or email."""
    from app.services import qualification as Q
    if not leads:
        return set()
    out: Set[str] = set()
    rules = Q.org_rules(db, org_id)
    for channel in (Q.CHANNEL_SMS, Q.CHANNEL_EMAIL):
        ctx = Q.QualificationContext(db, leads, org_id, rules)
        if channel == Q.CHANNEL_SMS:
            ctx.suppressed_phones = Q.load_suppressed_phones(db, org_id)
        for l in leads:
            if l.id in out:
                continue
            if Q.qualify_one(l, channel, ctx)["bucket"] == Q.READY:
                out.add(l.id)
    return out


def sync(db: Session, org_id: str, leads: List[Lead], now: Optional[datetime] = None,
         sig: Optional[Signals] = None) -> Dict[str, RoofingOpportunity]:
    """Bring every opportunity forward to what activity proves. Commits."""
    now = now or datetime.utcnow()
    opps = ensure(db, org_id, leads, now)
    sig = sig or Signals(db, [l.id for l in leads])
    # Qualification only matters for homeowners nothing has happened to yet.
    untouched = [l for l in leads
                 if opps[l.id].stage == C.IMPORTED and not sig.contacted(l.id)]
    qual = qualified_ids(db, org_id, untouched)
    for l in leads:
        o = opps[l.id]
        if o.stage in C.MANUAL_STAGES:
            continue
        target = derived_stage(l.id, sig, qual)
        b = sig.booked(l.id)
        if b and o.booking_link_id != b.id:
            o.booking_link_id = b.id
        if C.RANK[target] > C.RANK.get(o.stage, 0):
            _record(db, o, target, None, "Activity: %s" % C.STAGE_LABEL[target], now)
    db.commit()
    return opps


def set_stage(db: Session, opp: RoofingOpportunity, stage: str, user_id: str,
              reason: Optional[str] = None, now: Optional[datetime] = None) -> RoofingOpportunity:
    if stage not in C.STAGE_KEYS:
        raise HTTPException(400, "Unknown stage: %s" % stage)
    if stage == C.LOST and not (reason or "").strip():
        raise HTTPException(400, "Say why the homeowner was lost.")
    if stage == opp.stage:
        return opp
    now = now or datetime.utcnow()
    if stage == C.LOST:
        opp.lost_reason = reason.strip()[:300]
    elif opp.stage == C.LOST:
        opp.lost_reason = None
    _record(db, opp, stage, user_id, reason or "Moved to %s" % C.STAGE_LABEL[stage], now)
    db.commit()
    return opp


def history(db: Session, org_id: str, lead_id: str) -> List[dict]:
    rows = (db.query(RoofingStageEvent)
            .filter(RoofingStageEvent.organization_id == org_id,
                    RoofingStageEvent.lead_id == lead_id)
            .order_by(RoofingStageEvent.at.asc()).all())
    return [{"from": r.from_stage, "to": r.to_stage, "to_label": C.STAGE_LABEL.get(r.to_stage, r.to_stage),
             "by": "person" if r.actor_user_id else "activity", "actor_user_id": r.actor_user_id,
             "reason": r.reason, "at": C.iso(r.at)} for r in rows]


def reached(db: Session, org_id: str, opp_ids: List[str], current: Dict[str, str]) -> Dict[str, int]:
    """Furthest rank each opportunity ever reached (LOST excluded) - the funnel
    counts a homeowner who reached Engaged and was then lost as Engaged."""
    best: Dict[str, int] = {oid: C.RANK.get(st, -1) for oid, st in current.items()}
    for chunk in _chunks(opp_ids):
        for oid, to in (db.query(RoofingStageEvent.opportunity_id, RoofingStageEvent.to_stage)
                        .filter(RoofingStageEvent.organization_id == org_id,
                                RoofingStageEvent.opportunity_id.in_(chunk)).all()):
            r = C.RANK.get(to)
            if r is not None and r > best.get(oid, -1):
                best[oid] = r
    return best
