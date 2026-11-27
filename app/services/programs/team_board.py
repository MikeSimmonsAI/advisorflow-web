"""The team board (Restland, owner Nov 25): the handoff sheet's work, inside
EvoSys.

"Let's build a dashboard within EvoSys to collect the same data that we were
doing for the Google spreadsheet. It needs to have all the same capabilities
and more. Everyone has their own login ... and only can see their data, not
everyone else's data."

How it fits with the sheet while both are in use
------------------------------------------------
* Reading: the same rows the sheet gets (appointment_rows / follow_up_rows),
  plus what the team has entered - the sheet's team columns as last read
  (HandoffSheetRow.staff_json) with any board change not yet put through laid
  on top, so a save shows at once.
* Writing: a board change is stored as a HandoffPendingEdit. The next sheet
  pass (within about two minutes, sooner after a save) puts it through the
  same road a sheet edit takes - the sheet cell, then the app (booking,
  outcome, notes, ...). One road, so the two can never disagree.

Who sees what
-------------
* The program's managers (the owner's logins) see everyone and can filter by
  person, reassign work and manage the team.
* A team member sees only the work assigned to their own name, and only once
  the owner switches the board on for the team (`team_board_live`); until
  then the board is the owner's alone (standing order, Nov 21).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.programs import restland_sheet as RS

log = logging.getLogger(__name__)

OUTCOMES = ("Showed", "No-Show", "Rescheduled", "Cancelled")
APPT_EDIT = ("outcome", "sold", "sold_what", "notes", "link")
FU_EDIT = ("done", "notes", "appt_date", "appt_time", "appt_how", "appt_address", "fu_date", "fu_result")
OWNER_EDIT = ("assigned",)
LABELS = {"assigned": "Assigned To", "outcome": "Outcome", "sold": "Sold?", "sold_what": "What Was Sold",
          "notes": "Team Notes", "link": "Call Link / Details", "done": "Done?",
          "appt_date": "Appointment Date", "appt_time": "Appointment Time", "appt_how": "How",
          "appt_address": "Home Address", "fu_date": "Followed Up On", "fu_result": "What Happened"}


class BoardError(Exception):
    """A refusal in plain words (the router turns it into a 4xx)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ── who is asking ───────────────────────────────────────────────────────────

def cfg(prog) -> Dict[str, Any]:
    return RS._cfg(prog)


def is_live(prog) -> bool:
    """Is the board switched on for the team (not just the owner)?"""
    return bool(cfg(prog).get("team_board_live"))


def member_for(db: Session, org_id: str, user_id: str):
    from app.models.program_models import ProgramTeamMember as M
    return db.query(M).filter(M.organization_id == org_id, M.user_id == user_id).first()


def viewer(db: Session, prog, user, manager: bool) -> Dict[str, Any]:
    """{"manager": bool, "name": the person's own name or None}. Refuses
    anyone who is neither a manager nor a linked member of a live board."""
    if manager:
        m = member_for(db, prog.organization_id, user.id)
        return {"manager": True, "name": m.name if m else None}
    m = member_for(db, prog.organization_id, user.id)
    if m is None or not is_live(prog):
        raise BoardError("The team board isn't open for this login yet.", 403)
    return {"manager": False, "name": m.name}


# ── team ────────────────────────────────────────────────────────────────────

def sync_members(db: Session, org_id: str, team: List[Dict[str, Any]], now: Optional[datetime] = None) -> int:
    """The sheet's Team tab -> team members (by name). New names are added;
    phone, email and Active? follow the sheet. A login already linked is kept.
    Nobody is deleted - a name taken off the sheet is marked not active."""
    from app.models.program_models import ProgramTeamMember as M
    now = now or datetime.utcnow()
    have = {m.name: m for m in db.query(M).filter(M.organization_id == org_id).all()}
    seen, changed = set(), 0
    for p in team:
        if p.get("placeholder") or not p.get("name"):
            continue
        name = p["name"]
        seen.add(name)
        m = have.get(name)
        if m is None:
            m = M(organization_id=org_id, name=name, source="sheet", created_at=now)
            db.add(m)
            have[name] = m
            changed += 1
        for attr, val in (("email", (p.get("email") or "").strip() or None),
                          ("phone", (p.get("phone") or "").strip() or None),
                          ("active", bool(p.get("active")))):
            if getattr(m, attr) != val and not (attr != "active" and val is None):
                setattr(m, attr, val)
                changed += 1
    for name, m in have.items():
        if name not in seen and m.source == "sheet" and m.active:
            m.active = False
            changed += 1
    db.flush()
    return changed


def team(db: Session, prog) -> List[Dict[str, Any]]:
    from app.models.program_models import ProgramTeamMember as M
    c = cfg(prog)
    paused = c.get("paused") or {}
    members = (db.query(M).filter(M.organization_id == prog.organization_id)
               .order_by(M.active.desc(), M.created_at).all())
    out = []
    for m in members:
        status = ("Paused" if m.name in paused else "In the rotation") if m.active else "Not in the rotation"
        out.append({"id": m.id, "name": m.name, "email": m.email, "phone": m.phone, "role": m.role,
                    "active": m.active, "status": status, "has_login": bool(m.user_id),
                    "in_rotation_since": c.get("rr_since") if m.name in (c.get("rr_members") or []) else None})
    return out


# ── reading the work ────────────────────────────────────────────────────────

def _day(serial) -> Optional[str]:
    if not isinstance(serial, (int, float)):
        return None
    return (date(1899, 12, 30) + timedelta(days=int(serial))).isoformat()


def _clock(fraction) -> Optional[str]:
    if not isinstance(fraction, (int, float)):
        return None
    mins = int(round((fraction % 1) * 1440)) % 1440
    return "%02d:%02d" % (mins // 60, mins % 60)


def _stamp(serial) -> Optional[str]:
    """A Central-time serial -> 'YYYY-MM-DDTHH:MM' (Central)."""
    if not isinstance(serial, (int, float)):
        return None
    return (datetime(1899, 12, 30) + timedelta(days=float(serial))).strftime("%Y-%m-%dT%H:%M")


def _ct(at: Optional[datetime]) -> Optional[str]:
    return RS._central(at).strftime("%Y-%m-%dT%H:%M") if at else None


def _pending(db: Session, org_id: str) -> Dict[tuple, Dict[str, Any]]:
    """{(tab, row id): {key: value}} - board changes not put through yet."""
    from app.models.program_models import HandoffPendingEdit as E
    out: Dict[tuple, Dict[str, Any]] = {}
    for e in (db.query(E).filter(E.organization_id == org_id, E.applied_at.is_(None))
              .order_by(E.created_at).all()):
        out.setdefault((e.tab, e.row_id), {})[e.key] = e.value or ""
    return out


VISIT_KINDS = ("onsite", "call", "offsite", "unknown")


def visit_kind(meeting_type: Optional[str]) -> str:
    """The canonical visit type of a booking: onsite / call / offsite / unknown."""
    mt = (meeting_type or "").strip().lower()
    if mt in ("office", "restland", "in_person", "onsite"):
        return "onsite"
    if mt in ("phone", "video", "remote", "call", "zoom"):
        return "call"
    if mt in ("home", "offsite", "other_location"):
        return "offsite"
    return "unknown"


ACTIVITY_KINDS = {"family_at": "Customer replied", "human_at": "Advisor replied", "note_at": "Note saved",
                  "status_at": "Status changed"}


def _activity(db: Session, fus: List[Dict[str, Any]], recs) -> None:
    """Each follow-up's real last activity (owner, Nov 26 SS1/SS4): the newest
    of the family's own message, a reply a person sent, a note a person saved
    and a status change made by the team - with its Central time and kind.
    Never the time this page was loaded or the board's sync time. Also
    `new_reply`: the family's newest message came after any reply a person sent."""
    from app.models.program_models import ProgramConversation
    ids = [f["id"][3:] for f in fus if str(f["id"]).startswith("FU-")]
    convs = db.query(ProgramConversation).filter(ProgramConversation.id.in_(ids or ["-"])).all() if ids else []
    try:
        ev = RS.followup_events(db, convs)
    except Exception:                                    # noqa: BLE001 - rows still show, without activity
        log.exception("follow-up activity lookup failed")
        ev = {}
    for f in fus:
        e = dict(ev.get(str(f["id"])[3:]) or {})
        rec = recs.get(f["id"])
        e["status_at"] = rec.staff_changed_at if rec is not None else None
        best = None
        for k in ("family_at", "human_at", "note_at", "status_at"):
            at = e.get(k)
            if at is not None and (best is None or at > best[1]):
                best = (k, at)
        if best is None:
            f["activity"] = None
        else:
            kind = ACTIVITY_KINDS[best[0]]
            if best[0] == "human_at" and e.get("human_channel"):
                kind += " by " + e["human_channel"]
            f["activity"] = {"at": _ct(best[1]), "kind": kind}
        fam, human = e.get("family_at"), e.get("human_at")
        f["new_reply"] = bool(f["open"] and fam is not None and (human is None or fam > human))
        f["replied_at"] = _ct(human) if human else None
        f["replied_by"] = e.get("human_channel")
        f["family_at"] = _ct(fam) if fam else None


def work(db: Session, prog, person: Optional[str] = None, today: Optional[date] = None) -> Dict[str, Any]:
    """Appointments and follow-ups, with the team's fields. `person` limits
    it to one person's work (always set for a team member)."""
    from app.models.program_models import HandoffSheetRow as R
    from app.services.volt.sheets_export import central_serial
    org = prog.organization_id
    recs = {r.row_id: r for r in db.query(R).filter(R.organization_id == org).all()}
    pend = _pending(db, org)
    today = today or RS._central(datetime.utcnow()).date()
    today_serial = int(central_serial(datetime.utcnow()))

    def staff(tab, rid):
        rec = recs.get(rid)
        vals = json.loads(rec.staff_json or "{}") if rec is not None else {}
        vals.update(pend.get((tab, rid), {}))
        if not vals.get("assigned") and rec is not None and rec.assigned_to:
            vals["assigned"] = rec.assigned_to
        return vals, rec, bool(pend.get((tab, rid)))

    appts = []
    for r in RS.appointment_rows(db, org):
        rid = str(r[0])
        vals, rec, waiting = staff(RS.APPOINTMENTS, rid)
        who = vals.get("assigned") or ""
        if person is not None and who != person:
            continue
        appts.append({
            "id": rid, "lead_id": rec.lead_id if rec else None, "booked_on": _stamp(r[1]),
            "date": _day(r[2]), "time": _clock(r[3]), "day": r[4], "family": r[5], "phone": r[6],
            "email": r[7], "how": r[8], "home_address": r[9], "status": r[10], "said": r[11],
            "assigned": who, "outcome": vals.get("outcome", ""), "sold": vals.get("sold", ""),
            "sold_what": vals.get("sold_what", ""), "notes": vals.get("notes", ""), "link": vals.get("link", ""),
            "last_update": _ct(rec.staff_changed_at) if rec else None, "saving": waiting,
            "upcoming": RS._eligible(RS.APPOINTMENTS, r, today_serial),
        })
    # Visit type from the booking itself (owner, Nov 26): Onsite (Restland
    # office), Call In (phone/video), Offsite (home/other) or Unspecified - a
    # missing or unknown value is never guessed as an office visit.
    if appts:
        from app.models.models import BookingLink as _BL
        raw = {i: (m, a) for i, m, a in db.query(_BL.id, _BL.meeting_type, _BL.visit_address)
               .filter(_BL.id.in_([a["id"] for a in appts])).all()}
        for a in appts:
            mt, addr = raw.get(a["id"], (None, None))
            a["visit"] = visit_kind(mt)
            a["visit_detail"] = {"phone": "Phone call", "video": "Video call", "remote": "Phone or video call",
                                 "home": "Home visit"}.get(mt or "", "")
            if mt == "home" and addr and not a.get("home_address"):
                a["home_address"] = addr
    appts.sort(key=lambda a: ((a["date"] or "9999"), a["time"] or ""))
    known = [rid for rid, rec in recs.items() if rec.tab == RS.FOLLOW_UPS]
    fus = []
    for r in RS.follow_up_rows(db, org, known):
        rid = str(r[0])
        if r[2] is None:                                 # a marker row ("booked or handled")
            continue
        vals, rec, waiting = staff(RS.FOLLOW_UPS, rid)
        who = vals.get("assigned") or ""
        if person is not None and who != person:
            continue
        due = _day(r[6]) if r[6] != "" else None
        status = r[8]
        is_open = status == "Open" and str(vals.get("done", "")).upper() != "TRUE"
        fus.append({
            "id": rid, "lead_id": rec.lead_id if rec else None, "added": _stamp(r[1]), "family": r[2],
            "phone": r[3], "email": r[4], "why": r[5], "follow_up_on": due, "said": r[7], "status": status,
            "assigned": who, "done": str(vals.get("done", "")).upper() == "TRUE", "notes": vals.get("notes", ""),
            "appt_date": vals.get("appt_date", ""), "appt_time": vals.get("appt_time", ""),
            "appt_how": vals.get("appt_how", ""), "appt_address": vals.get("appt_address", ""),
            "fu_date": vals.get("fu_date", ""), "fu_result": vals.get("fu_result", ""),
            "last_update": _ct(rec.staff_changed_at or rec.assigned_at) if rec else None,
            "saving": waiting, "open": is_open,
            "due_now": is_open and (due is None or due <= today.isoformat()),
        })
    _activity(db, fus, recs)
    # due now first, then the newest update
    fus.sort(key=lambda f: (not f["due_now"], not f["open"], f["follow_up_on"] or "",
                            "" if f["last_update"] is None else f["last_update"]), reverse=False)
    return {"appointments": appts, "follow_ups": fus}


def summary(db: Session, prog, data: Optional[Dict[str, Any]] = None, today: Optional[date] = None) -> Dict[str, Any]:
    """The numbers: totals and by person - what the sheet's Dashboard tab shows, and more."""
    today = today or RS._central(datetime.utcnow()).date()
    data = data or work(db, prog, today=today)
    t = today.isoformat()
    week_ago = (today - timedelta(days=7)).isoformat()

    def blank():
        return {"appointments_upcoming": 0, "appointments_today": 0, "showed": 0, "no_show": 0, "sold": 0,
                "follow_ups_open": 0, "follow_ups_due_now": 0, "followed_up_7d": 0,
                "booked_from_follow_ups": 0, "last_update": None}
    people: Dict[str, Dict[str, Any]] = {}
    total = blank()

    def bump(who, key, n=1):
        for d in (total, people.setdefault(who or "Not assigned", blank())):
            d[key] += n

    def seen(who, at):
        for d in (total, people.setdefault(who or "Not assigned", blank())):
            if at and (d["last_update"] is None or at > d["last_update"]):
                d["last_update"] = at
    for a in data["appointments"]:
        who = a["assigned"]
        if a["upcoming"]:
            bump(who, "appointments_upcoming")
        if a["date"] == t and not str(a["status"]).startswith("Cancel"):
            bump(who, "appointments_today")
        out = (a["outcome"] or a["status"] or "")
        if out == "Showed":
            bump(who, "showed")
        elif out == "No-Show":
            bump(who, "no_show")
        if a["sold"] == "Yes":
            bump(who, "sold")
        seen(who, a["last_update"])
    for f in data["follow_ups"]:
        who = f["assigned"]
        if f["open"]:
            bump(who, "follow_ups_open")
        if f["due_now"]:
            bump(who, "follow_ups_due_now")
        d = _date_text(f["fu_date"])
        if d and d >= week_ago:
            bump(who, "followed_up_7d")
        if (f["fu_result"] or "").startswith("Talked - booked"):
            bump(who, "booked_from_follow_ups")
        seen(who, f["last_update"])
    return {"total": total, "by_person": [dict(v, name=k) for k, v in sorted(people.items())]}


def _date_text(v: str) -> Optional[str]:
    """'11/25/2026' or '11/25/2026 4:38 PM' or '2026-11-25' -> '2026-11-25'."""
    v = (v or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        return v[:10]
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", v)
    if m:
        return "%s-%02d-%02d" % (m.group(3), int(m.group(1)), int(m.group(2)))
    return None


# ── saving ──────────────────────────────────────────────────────────────────

def _sheet_date(v: str, label: str) -> str:
    """'2026-12-03' or '12/3/2026' -> '12/3/2026' (what the sheet reads as a date)."""
    v = (v or "").strip()
    if not v:
        return ""
    d = _date_text(v)
    if d is None:
        raise BoardError("%s must be a date." % label)
    y, mth, day = d.split("-")
    return "%d/%d/%s" % (int(mth), int(day), y)


def clean(tab: str, changes: Dict[str, Any], manager: bool, team_names: List[str]) -> Dict[str, str]:
    """The fields being saved, checked and in the sheet's own spelling."""
    allowed = set(APPT_EDIT if tab == RS.APPOINTMENTS else FU_EDIT) | (set(OWNER_EDIT) if manager else set())
    out: Dict[str, str] = {}
    for k, v in (changes or {}).items():
        if k not in allowed:
            raise BoardError("%s can't be changed here." % LABELS.get(k, k), 403 if k in OWNER_EDIT else 400)
        v = "" if v is None else v
        if k == "done":
            out[k] = "TRUE" if v is True or str(v).upper() == "TRUE" else ""
            continue
        v = str(v).strip()
        if k == "outcome" and v and v not in OUTCOMES:
            raise BoardError("Outcome must be one of: " + ", ".join(OUTCOMES))
        if k == "sold" and v and v not in RS.SOLD_CHOICES:
            raise BoardError("Sold? must be Yes or No.")
        if k == "fu_result" and v and v not in RS.WHAT_HAPPENED:
            raise BoardError("What Happened must be one of: " + ", ".join(RS.WHAT_HAPPENED))
        if k == "appt_time" and v and v not in RS.TIME_CHOICES:
            raise BoardError("Appointment Time must be one of the times on the list (10:00 AM - 6:00 PM).")
        if k == "appt_how" and v and v not in RS.HOW_PICK:
            raise BoardError("How must be one of: " + ", ".join(RS.HOW_PICK))
        if k in ("appt_date", "fu_date"):
            v = _sheet_date(v, LABELS[k])
        if k == "assigned" and v and v not in team_names:
            raise BoardError("%s isn't on the team." % v)
        out[k] = v[:2000]
    return out


def save(db: Session, prog, who: Dict[str, Any], user_id: str, tab: str, row_id: str,
         changes: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Store a board change for the next pass. A team member may only change
    their own rows."""
    from app.models.program_models import HandoffPendingEdit as E
    from app.models.program_models import ProgramTeamMember as M
    now = now or datetime.utcnow()
    if tab not in (RS.APPOINTMENTS, RS.FOLLOW_UPS):
        raise BoardError("Unknown list.")
    org = prog.organization_id
    data = work(db, prog)
    rows = {r["id"]: r for r in (data["appointments"] if tab == RS.APPOINTMENTS else data["follow_ups"])}
    row = rows.get(row_id)
    if row is None:
        raise BoardError("That one is no longer on the board.", 404)
    if not who["manager"] and row["assigned"] != who["name"]:
        raise BoardError("That one isn't assigned to you.", 403)
    names = [m.name for m in db.query(M).filter(M.organization_id == org).all()]
    vals = clean(tab, changes, who["manager"], names)
    # a booking needs a date and a time; a home visit needs the address (same rules as the sheet)
    if tab == RS.FOLLOW_UPS:
        merged = {k: row.get(k, "") for k in ("appt_date", "appt_time", "appt_how", "appt_address")}
        merged.update({k: v for k, v in vals.items() if k in merged})
        if bool(merged["appt_date"]) != bool(merged["appt_time"]) and ("appt_date" in vals or "appt_time" in vals):
            raise BoardError("To book, fill in both the Appointment Date and the Appointment Time.")
        if merged["appt_how"] == "At Their Home" and merged["appt_date"] and not merged["appt_address"]:
            raise BoardError("A visit at their home needs the Home Address.")
    changed = {}
    for k, v in vals.items():
        have = row.get(k)
        have = ("TRUE" if have else "") if k == "done" else (have or "")
        if have != v:
            db.add(E(organization_id=org, row_id=row_id, tab=tab, key=k, value=v, by_user_id=user_id,
                     by_name=who.get("name"), created_at=now))
            changed[k] = v
    db.commit()
    if changed:
        RS._last_run.pop(prog.id, None)                  # put it through on the next tick
    return {"saved": changed, "waiting": bool(changed)}
