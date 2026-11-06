"""VOLT - Get Future Opportunities (the Talent Pool).

A signup saves INTEREST only: no login, no rep ID, no application, nothing that
counts toward applications, approvals, starts or the leaderboard. Full
application and account creation happen when the person chooses to apply.

ONE ROW PER EMAIL PER TENANT. A repeat signup updates that row: the names and
interests are refreshed (interests are merged), the email preference becomes
the person's latest choice, and submit_count goes up. The public answer is the
same whether the email is new, repeated or belongs to an existing applicant,
so a signup can never reveal an account or touch a profile.

REFERRALS follow the click-to-confirm rule: credit is the recruiter id the
person confirmed (an invite they accepted, or a directory result they clicked),
re-validated here. A typed name is never accepted.

Email preference is stored, never acted on: nothing here sends mail. A
recruiting email to real people needs its own instruction.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.volt_models import VoltProgram, VoltProject, VoltRep, VoltTalentSignup
from app.services.volt import common as C
from app.services.volt.core import EMAIL_RE, field_error, resolve_referrer

ANY = "any"


def _interests(db: Session, org_id: str, raw) -> List[str]:
    keys = {p.key for p in db.query(VoltProject).filter(VoltProject.organization_id == org_id).all()}
    out = []
    for k in raw or []:
        k = str(k or "").strip().lower()
        if (k in keys or k == ANY) and k not in out:
            out.append(k)
    return out


def signup(db: Session, prog: VoltProgram, body: Dict[str, Any]) -> VoltTalentSignup:
    first = str(body.get("first_name") or "").strip()[:70]
    last = str(body.get("last_name") or "").strip()[:70]
    email = str(body.get("email") or "").strip().lower()
    errs = {}
    if not first:
        errs["first_name"] = "Enter your first name."
    if not last:
        errs["last_name"] = "Enter your last name."
    if not email:
        errs["email"] = "Enter your email address."
    elif not EMAIL_RE.match(email) or len(email) > 254:
        errs["email"] = "Enter a valid email address, like name@example.com."
    if errs:
        field_error(errs)
    referrer = resolve_referrer(db, prog, body.get("referrer_rep_id"))
    interests = _interests(db, prog.organization_id, body.get("interests"))
    notify = body.get("notify") is True
    now = C.now()
    row = (db.query(VoltTalentSignup).filter(VoltTalentSignup.organization_id == prog.organization_id,
                                             VoltTalentSignup.email == email).first())
    if row is None:
        row = VoltTalentSignup(organization_id=prog.organization_id, email=email, first_name=first, last_name=last,
                               interests=json.dumps(interests), notify_opt_in=notify,
                               notify_opt_in_at=now if notify else None,
                               source=str(body.get("source") or "portal")[:40],
                               referrer_serial=referrer.serial if referrer else None,
                               referral_source=(body.get("referral_source") if body.get("referral_source") in ("invite", "search") else "search") if referrer else None,
                               submit_count=1, is_test=C.is_test_email(email), created_at=now, updated_at=now)
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
            return row
        except IntegrityError:
            # A simultaneous signup for the same email won: update that one.
            row = (db.query(VoltTalentSignup).filter(VoltTalentSignup.organization_id == prog.organization_id,
                                                     VoltTalentSignup.email == email).first())
    row.first_name, row.last_name = first, last
    merged = json.loads(row.interests or "[]")
    for k in interests:
        if k not in merged:
            merged.append(k)
    row.interests = json.dumps(merged)
    if notify and not row.notify_opt_in:
        row.notify_opt_in_at = now
    row.notify_opt_in = notify
    if notify:
        row.unsubscribed_at = None
    if referrer is not None and row.referrer_serial is None:
        # The first confirmed referral stands; a later one never overwrites it.
        row.referrer_serial = referrer.serial
        row.referral_source = body.get("referral_source") if body.get("referral_source") in ("invite", "search") else "search"
    row.submit_count = (row.submit_count or 1) + 1
    row.updated_at = now
    return row


def _d(v) -> Optional[date]:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def query(db: Session, org_id: str, params: Dict[str, Any]):
    q = db.query(VoltTalentSignup).filter(VoltTalentSignup.organization_id == org_id)
    s = str(params.get("q") or "").strip().lower()
    if s:
        like = "%" + s.replace("%", "").replace("_", "") + "%"
        q = q.filter(func.lower(VoltTalentSignup.first_name + " " + VoltTalentSignup.last_name).like(like)
                     | VoltTalentSignup.email.like(like))
    d1, d2 = _d(params.get("date_from")), _d(params.get("date_to"))
    if d1:
        q = q.filter(VoltTalentSignup.created_at >= datetime.combine(d1, datetime.min.time()))
    if d2:
        q = q.filter(VoltTalentSignup.created_at < datetime.combine(d2 + timedelta(days=1), datetime.min.time()))
    if str(params.get("notify") or "") in ("1", "true", "yes"):
        q = q.filter(VoltTalentSignup.notify_opt_in.is_(True))
    if str(params.get("include_test") or "").lower() not in ("1", "true", "yes"):
        q = q.filter(VoltTalentSignup.is_test.is_(False))
    rows = q.order_by(VoltTalentSignup.created_at.desc()).limit(2000).all()
    interest = str(params.get("interest") or "").strip().lower()
    if interest:
        rows = [r for r in rows if interest in json.loads(r.interests or "[]")]
    return rows


def row_dict(db: Session, r: VoltTalentSignup, names: Dict[str, str]) -> Dict[str, Any]:
    ref = db.get(VoltRep, r.referrer_serial) if r.referrer_serial else None
    ints = json.loads(r.interests or "[]")
    return {"id": r.id, "first_name": r.first_name, "last_name": r.last_name, "email": r.email,
            "interests": ints, "interest_labels": [names.get(k, "Any future opportunity" if k == ANY else k) for k in ints],
            "notify_opt_in": r.notify_opt_in, "notify_opt_in_at": C.iso(r.notify_opt_in_at),
            "source": r.source, "referrer": C.public_rep(ref), "referral_source": r.referral_source,
            "submit_count": r.submit_count, "is_test": r.is_test,
            "created_at": C.iso(r.created_at), "updated_at": C.iso(r.updated_at)}


def project_names(db: Session, org_id: str) -> Dict[str, str]:
    return {p.key: (p.short_name or p.name) for p in db.query(VoltProject).filter(VoltProject.organization_id == org_id).all()}


def _cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def csv_text(db: Session, org_id: str, params: Dict[str, Any]) -> str:
    names = project_names(db, org_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["First name", "Last name", "Email", "Interests", "Email me about future opportunities",
                "Opted in (UTC)", "Referred by", "Referrer ID", "Source", "Signups", "First signup (UTC)", "Last signup (UTC)"])
    for r in query(db, org_id, params):
        d = row_dict(db, r, names)
        ref = d["referrer"] or {}
        w.writerow([_cell(x) for x in (d["first_name"], d["last_name"], d["email"], "; ".join(d["interest_labels"]),
                                        "Yes" if d["notify_opt_in"] else "No", d["notify_opt_in_at"],
                                        ref.get("display_name"), ref.get("rep_code"), d["source"], d["submit_count"],
                                        d["created_at"], d["updated_at"])])
    return buf.getvalue()
