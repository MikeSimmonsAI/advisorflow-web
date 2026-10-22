"""ONE REPLY, ONE ALERT (Oct 19) - the single gate every SCI staff text and
staff email goes through.

WHY. Production records Oct 16-19 showed a family's ONE interaction paging
each person several times. Twilio retries were already harmless (unique
replies.twilio_sid); the extra pages came from four separate senders, each
deciding on its own:

  1. every text in a back-and-forth paged again   (Tony Ellis: 4 texts in 3 min -> 4 pages)
  2. the AI hand-off paged a second time, seconds after the reply alert
     (Teresita Roque, Bill Rodebush, Dale Burgener)
  3. the "not handled yet" reminder re-paged every 15 min, 3 times, even
     while the AI was talking to the family                (Emily Kaelin)
  4. the generic platform hot-reply email (notification_service) for the
     same reply

THE RULE, enforced in the database so it holds across instances, restarts,
retries and concurrent workers:

  * Every page has a stable dedupe key (reply:<reply id>, booked:<lead>:<slot>,
    sla:<response id>, ...). A claim row in program_alerts with that key,
    channel and recipient is inserted BEFORE anything is sent; a unique index
    lets exactly one claim win. A duplicate webhook, a repeated job or a
    second worker finds the claim and sends nothing.
  * One conversation, one page: while a person has already been paged about
    this family in the last COALESCE window, a further reply of the same or
    lower importance is recorded (send_state "merged") but does not page
    again. A MORE important event (reply -> needs a person -> booked) still
    pages, once. On Postgres the decision is taken under a per-family
    advisory lock, so two messages processed at the same instant cannot both
    decide to page.
  * Every decision is a row: sent, failed (with the provider's reason),
    merged, skipped - with the provider SID and the carrier's delivery report.
  * A failed page is retried by updating ITS OWN row (attempts + 1), claimed
    with a conditional UPDATE, so a retry can never become a second page.

Genuine replies are never discarded: every message is still its own Reply,
ProgramResponse, in-app notice and conversation turn. Only the staff
text/email is consolidated.
"""
import contextvars
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text as _sql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# How important an event is. A page is suppressed only by an earlier page of
# the SAME OR HIGHER tier for the same family inside the window.
TIER = {"low": 1, "active": 1, "reply": 1, "hot": 2, "handoff": 2, "sla_breach": 2, "booked": 3, "home_visit": 3,
        "opt_out_request": 4}                       # never merged away: staff must not text them back
DEFAULT_COALESCE_MINUTES = 30
MAX_ATTEMPTS = 3
# While a family's reply is being handled, a hand-off or a booking during that
# turn is collected here instead of paging on its own (responses.on_inbound).
DEFER: "contextvars.ContextVar" = contextvars.ContextVar("sci_alert_defer", default=None)


def defer(event: Dict) -> bool:
    """Collect this event for the reply being handled. False when no reply
    is being handled - the caller then pages on its own."""
    lst = DEFER.get()
    if lst is None:
        return False
    lst.append(event)
    return True


def coalesce_minutes() -> int:
    try:
        return max(0, int(os.environ.get("PROGRAM_ALERT_COALESCE_MINUTES") or DEFAULT_COALESCE_MINUTES))
    except ValueError:
        return DEFAULT_COALESCE_MINUTES


def _norm(to: Optional[str]) -> str:
    v = (to or "").strip().lower()
    return v if "@" in v else "".join(c for c in v if c.isdigit())[-10:]


def staff_targets(prog, *, management: bool = False) -> List[Tuple[str, str, str]]:
    """(audience, channel, recipient) for a family-response page: the owner's
    alert phone and email, every "team" member, and - when management is
    asked for - everyone else on the list. Each phone or email ONCE, even if
    it appears under two names."""
    from app.services.programs.responses import _managers
    out, seen = [], set()

    def add(aud, ch, to):
        if not to:
            return
        k = (ch, _norm(to))
        if k in seen:
            return
        seen.add(k)
        out.append((aud, ch, to))
    add("primary", "sms", prog.alert_phone)
    add("primary", "email", prog.alert_email)
    for m in _managers(prog):
        role = (m.get("role") or "").lower()
        if management or role == "team":
            aud = "team" if role == "team" else "management"
            add(aud, "sms", m.get("phone"))
            add(aud, "email", m.get("email"))
    return out


def _lock_family(db: Session, lead_id: Optional[str]) -> None:
    """Serialize page decisions for one family across workers (Postgres)."""
    if not lead_id:
        return
    try:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(_sql("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": "sci-alert:" + lead_id})
    except Exception:                                    # noqa: BLE001 - the unique index still holds
        log.exception("alert lock failed for lead %s", lead_id)


def recent_page(db: Session, org_id: str, lead_id: Optional[str], now: datetime):
    """The highest-tier page sent (or being sent) about this family within the
    window, or None."""
    from app.models.program_models import ProgramAlert
    win = coalesce_minutes()
    if not lead_id or win <= 0:
        return None
    rows = (db.query(ProgramAlert)
            .filter(ProgramAlert.organization_id == org_id, ProgramAlert.lead_id == lead_id,
                    ProgramAlert.channel.in_(("sms", "email")),
                    ProgramAlert.send_state.in_(("sending", "sent")),
                    ProgramAlert.created_at >= now - timedelta(minutes=win))
            .all())
    if not rows:
        return None
    return max(rows, key=lambda a: (TIER.get(a.kind, 1), a.created_at or now))


def page(db: Session, prog, *, lead_id: Optional[str], key: str, kind: str,
         sms_text: str, subject: str, html: str, response_id: Optional[str] = None,
         management: bool = False, coalesce: bool = True, test: bool = False,
         now: Optional[datetime] = None, audit_message: Optional[str] = None) -> List:
    """Raise ONE logical staff alert: at most one text and one email per
    recipient for this key, ever. Returns the ProgramAlert rows it wrote."""
    from app.models.program_models import ProgramAlert
    from app.services.programs.reply_rules import staff_alert_gate
    now = now or datetime.utcnow()
    _lock_family(db, lead_id)
    targets = [] if test else staff_targets(prog, management=management)
    merged_into = None
    if coalesce and targets:
        prev = recent_page(db, prog.organization_id, lead_id, now)
        if prev is not None and TIER.get(prev.kind, 1) >= TIER.get(kind, 1):
            merged_into = prev
    msg = (audit_message or sms_text or "")[:2000]
    out = []
    for aud, ch, to in targets:
        base = dict(organization_id=prog.organization_id, response_id=response_id, lead_id=lead_id,
                    kind=kind, audience=aud, channel=ch, recipient=to, message=msg, dedupe_key=key)
        ok, why = staff_alert_gate(to, ch, aud, bool(prog.staff_sms_alerts_enabled))
        if merged_into is not None:
            row = ProgramAlert(delivered=False, send_state="merged", attempts=0,
                               reason="same conversation - already alerted %s" % (
                                   merged_into.created_at.strftime("%H:%M UTC") if merged_into.created_at else ""),
                               **base)
        elif not ok:
            row = ProgramAlert(delivered=False, send_state="skipped", attempts=0, reason=why, **base)
        else:
            row = ProgramAlert(delivered=False, send_state="sending", attempts=1, reason="sending",
                               retry_payload="%s\n\x1e%s\n\x1e%s" % (subject, sms_text, html), **base)
        sp = db.begin_nested()
        try:
            db.add(row)
            db.flush()
            sp.commit()
        except IntegrityError:
            sp.rollback()
            continue                                     # already claimed by another delivery: no page
        if row.send_state == "sending":
            _send_row(db, prog, row, sms_text=sms_text, subject=subject, html=html)
        out.append(row)
    return out


def _send_row(db: Session, prog, row, *, sms_text: str, subject: str, html: str) -> None:
    from app.services.programs import responses as R
    rc: Dict = {}
    content = {"sms": sms_text, "subject": subject, "html": html}
    ok, why = R._deliver_staff_alert(db, prog, row.channel, row.recipient, row.message or "",
                                     kind=row.kind, response_id=None, receipt=rc, content=content)
    row.send_state = "sent" if ok else "failed"
    row.reason = why
    row.delivered = bool(ok) and row.channel == "email"
    if row.channel == "sms":
        R.sms_alert_record(row, rc)
        if not ok:
            row.delivered = False
            row.reason = why
    row.last_attempt_at = datetime.utcnow()
    if ok and row.channel == "sms":
        row.message = sms_text[:2000]
    elif ok:
        row.message = subject[:2000]
    db.flush()


def _retry_blocked(db: Session, prog, r) -> Optional[str]:
    if r.channel in ("sms", "email") and not getattr(prog, "staff_sms_alerts_enabled", False):
        return "staff alerts are switched off for this program"
    if r.lead_id:
        from app.models.models import Lead
        lead = db.query(Lead).filter(Lead.id == r.lead_id).first()
        if lead is not None:
            from app.services.sci_staging_demo import is_demo_lead
            from app.services.test_records import is_owner_test_lead
            if is_demo_lead(lead) or is_owner_test_lead(lead):
                return "test or demo family - not paged"
    return None


def retry_failed(db: Session, *, now: Optional[datetime] = None, min_age_seconds: int = 90,
                 max_age_minutes: int = 120) -> int:
    """Retry pages that failed to send, by re-using their own row. A row is
    claimed with a conditional UPDATE, so two workers can never retry the
    same page. Stops after MAX_ATTEMPTS. Returns how many were re-sent."""
    from app.models.program_models import OutreachProgram, ProgramAlert
    now = now or datetime.utcnow()
    rows = (db.query(ProgramAlert)
            .filter(ProgramAlert.send_state == "failed", ProgramAlert.dedupe_key.isnot(None),
                    ProgramAlert.attempts < MAX_ATTEMPTS,
                    ProgramAlert.created_at >= now - timedelta(minutes=max_age_minutes),
                    ProgramAlert.last_attempt_at <= now - timedelta(seconds=min_age_seconds))
            .limit(50).all())
    sent = 0
    for r in rows:
        n = (db.query(ProgramAlert)
             .filter(ProgramAlert.id == r.id, ProgramAlert.send_state == "failed",
                     ProgramAlert.attempts == r.attempts)
             .update({"send_state": "sending", "attempts": r.attempts + 1}, synchronize_session=False))
        db.commit()
        if n != 1:
            continue                                     # another worker took it
        db.refresh(r)
        prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == r.organization_id).first()
        if prog is None:
            r.send_state, r.reason = "failed", "program not found"
            db.commit()
            continue
        # Re-check what the first attempt checked: alerts switched off since,
        # or a test / demo family, never pages on a retry.
        why_not = _retry_blocked(db, prog, r)
        if why_not:
            r.send_state, r.reason = "skipped", why_not
            db.commit()
            continue
        stored = r.retry_payload or ""
        subj, _, rest = stored.partition("\n\x1e")
        sms_text, _, html = rest.partition("\n\x1e")
        if not (sms_text or subj):
            sms_text, subj, html = r.message or "", r.message or "", "<p>%s</p>" % (r.message or "")
        _send_row(db, prog, r, sms_text=sms_text, subject=subj, html=html)
        db.commit()
        sent += 1
    return sent


# ── what the text says ───────────────────────────────────────────────────────

_URL = re.compile(r"(https?://\S+|www\.\S+)", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
_LONGNUM = re.compile(r"\b\d{5,}\b")
_ASCII = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-",
                        "—": "-", "…": "...", " ": " "})
SEGMENT = 160


def preview(body: Optional[str], limit: int) -> str:
    """The family's own words, shortened - never paraphrased. Links, phone
    numbers, emails and long numbers are masked (privacy, and carriers
    filter them), and characters that would force a non-GSM text are
    replaced so the alert stays one segment."""
    s = (body or "").translate(_ASCII)
    s = _URL.sub("[link]", s)
    s = _EMAIL.sub("[email]", s)
    s = _PHONE.sub("[number]", s)
    s = _LONGNUM.sub("[number]", s)
    s = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > limit:
        cut = s[:max(0, limit - 3)]
        if " " in cut[len(cut) // 2:]:
            cut = cut[:cut.rindex(" ")]                  # never end on half a word
        s = cut.rstrip(" ,.;:-") + "..."
    return s


def first_last(lead) -> str:
    return ("%s %s" % (getattr(lead, "first_name", "") or "", getattr(lead, "last_name", "") or "")).strip().title() \
        or "A family"


def sms_line(kind: str, name: str, *, words: Optional[str] = None, location: Optional[str] = None,
             detail: Optional[str] = None, link: Optional[str] = None, test: bool = False) -> str:
    """A short, specific staff text. The label says WHAT happened; the
    preview is the family's real words (or the system's real reason) - never
    a made-up summary. Fits one segment without a link."""
    label = {"hot": "SCI Appointment Request", "handoff": "SCI Needs Attention", "booked": "SCI Booked",
             "sla_breach": "SCI Still Waiting", "home_visit": "SCI Home Visit Request",
             "opt_out_request": "SCI Opted Out But Asked - Call/Email, No Texts"}.get(kind, "SCI Reply")
    if test:
        label = "TEST " + label
    tail = " Open EvoSys Pro to respond." if kind in ("low", "active", "reply") else " Review in EvoSys Pro."
    loc = " Location: %s." % location if location else ""
    head = "%s | %s" % (label, name)
    fixed = len(head) + len(loc) + len(tail) + (len(link) + 1 if link else 0)
    room = (SEGMENT if not link else 2 * 153) - fixed - 4
    if detail:
        body = ": " + preview(detail, max(20, room)) + "."
    elif words:
        body = ': "%s"' % preview(words, max(20, room - 2))
    else:
        body = "."
    out = head + body + loc + tail
    if link:
        out += " " + link
    return out


def family_link(lead_id: Optional[str]) -> Optional[str]:
    """Direct link to the family's conversation - only while links in SCI
    texts are switched on (the same switch the family texts use)."""
    base = (os.environ.get("APP_BASE_URL") or "").strip().rstrip("/")
    if not (base and lead_id):
        return None
    try:
        from app.services.sms_programs import sms_links_enabled
        if not sms_links_enabled():
            return None
    except Exception:                                    # noqa: BLE001
        return None
    return "%s/program/live?f=%s" % (base, lead_id)
