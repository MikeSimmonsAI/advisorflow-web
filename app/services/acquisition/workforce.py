"""HARVEY AI WORKFORCE - the acquisition labour behind the Control Center (#452).

One central workforce serves every Acquisition client that is switched on.
It is a set of bounded workers driven by a durable job queue, run by the
backend's existing background-loop machinery (job_run_service.claim_pass +
record_job_run), writing ONLY to the existing EvoSys records:

  discovery  - per enabled client, a bounded daily pass. Paid discovery
               (Google Places, metered) runs only after the owner approves it;
               otherwise the pass reports which master-pool businesses fit
               the client and says plainly that no new source is approved.
  research   - reads a business's OWN public website (SSRF-safe client from
               Harvey), records what it says with the page URL and time.
  contacts   - an email address counts as verified only when it is published
               on the business's own website AND belongs to that website's
               domain AND the domain accepts mail (MX). Nothing is guessed.
  outreach   - drafts a category-aware first email from verified facts only,
               for a person to approve. Nothing is sent by the workforce.
  replies    - classifies inbound replies with deterministic rules first;
               opt-outs always win; uncertain/sensitive replies go to a human.
  booking    - for an Interested prospect, makes a booking link on the
               client's REAL connected calendar and drafts the scheduling
               reply. The appointment is "booked" only when the prospect picks
               a real free time and the calendar provider creates the event.

Adapted ideas from Harvey (MIT, Ethan Rogers): untrusted-content fencing and
opt-out/escalation rules (harvey_adapted.py), the public-website HTTP
boundary (public_http.py), evidence before personalisation, and an outbox a
person approves. Harvey's local runtime (a Claude Code / Codex CLI login on
the operator's own machine) is NOT used: a consumer subscription is not a
cloud service account, so model calls - when the owner allows any - go
through the existing ai_gateway with its caps and logging.

Nothing here sends email, texts or calls anyone. Real outreach still needs
ACQUISITION_OUTREACH_ENABLED=1, a person's approval and every outreach gate.
"""
import json
import logging
import re

from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.acquisition_models import (AcquisitionDraft, AcquisitionEvidence,
                                           AcquisitionOpportunity, AcquisitionProfile,
                                           AcquisitionWorkerJob, AcquisitionWorkforceClient,
                                           AcquisitionWorkforceState)
from app.models.models import Lead, Organization
from app.services.acquisition import common as C
from app.services.acquisition import harvey_adapted as H

log = logging.getLogger("acquisition.workforce")

WORKERS: List[Dict[str, str]] = [
    {"key": "discovery", "label": "Business discovery"},
    {"key": "research", "label": "Website research"},
    {"key": "contacts", "label": "Contact verification"},
    {"key": "outreach", "label": "Outreach drafting"},
    {"key": "replies", "label": "Reply reading"},
    {"key": "booking", "label": "Appointment setting"},
]
WORKER_KEYS = [w["key"] for w in WORKERS]
STATUSES = ("stopped", "running", "paused", "emergency_stopped")
PASS_SECONDS = 300
LEASE_SECONDS = 300
MAX_SITE_PAGES = 4
JOB_ACTOR = "harvey_workforce"
CONTACT_PATHS = ("/contact", "/contact-us", "/about", "/about-us")


# ── control state ───────────────────────────────────────────────────────────
def state(db: Session) -> AcquisitionWorkforceState:
    s = db.query(AcquisitionWorkforceState).filter(AcquisitionWorkforceState.id == "global").first()
    if s is None:
        s = AcquisitionWorkforceState(id="global", status="stopped", mode="practice")
        db.add(s)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            s = db.query(AcquisitionWorkforceState).filter(AcquisitionWorkforceState.id == "global").one()
    return s


def _audit(db: Session, actor, action: str, detail: Dict[str, Any]) -> None:
    """Audit-log a control change. A change with no signed-in person (the
    server's own first start) is written to the application log instead,
    because the audit table requires an actor."""
    if getattr(actor, "id", None) is None:
        log.info("acquisition workforce %s by the server: %s", action, detail)
        return
    try:
        from app.routers.audit_log_router import log_action
        log_action(db, None, actor.id, "acquisition_workforce_" + action,
                   "acquisition_workforce", "global", details=detail)
    except Exception:                                            # noqa: BLE001
        db.rollback()
        log.exception("workforce audit failed (%s)", action)


def control(db: Session, actor, action: str, reason: Optional[str] = None,
            mode: Optional[str] = None) -> AcquisitionWorkforceState:
    """start / pause / resume / stop / emergency_stop. Real state changes, audited."""
    from fastapi import HTTPException
    s = state(db)
    now = datetime.utcnow()
    before = s.status
    if action == "start":
        if mode is not None:
            if mode not in ("practice", "live"):
                raise HTTPException(422, "Mode is practice or live")
            s.mode = mode
        s.status = "running"
    elif action == "pause":
        if s.status != "running":
            raise HTTPException(409, "Only a running workforce can be paused")
        s.status = "paused"
    elif action == "resume":
        if s.status == "emergency_stopped":
            raise HTTPException(409, "After an emergency stop, use Start (a deliberate restart)")
        if s.status != "paused":
            raise HTTPException(409, "Only a paused workforce can be resumed")
        s.status = "running"
    elif action == "stop":
        s.status = "stopped"
    elif action == "emergency_stop":
        s.status = "emergency_stopped"
        n = (db.query(AcquisitionWorkerJob)
             .filter(AcquisitionWorkerJob.status.in_(("queued", "running")))
             .update({AcquisitionWorkerJob.status: "cancelled", AcquisitionWorkerJob.finished_at: now,
                      AcquisitionWorkerJob.error: "Cancelled by emergency stop"}, synchronize_session=False))
        reason = (reason or "Emergency stop") + " (%d jobs cancelled)" % n
    else:
        raise HTTPException(422, "Unknown action")
    s.reason = (reason or "")[:300] or None
    s.changed_by_id = getattr(actor, "id", None)
    s.changed_at = now
    s.next_pass_at = now if s.status == "running" else None
    db.commit()
    _audit(db, actor, action, {"from": before, "to": s.status, "mode": s.mode, "reason": s.reason})
    return s


def autostart(db: Session) -> Optional[str]:
    """ACQUISITION_WORKFORCE_AUTOSTART=practice: first start in PRACTICE mode,
    only while the controls have never been used (changed_at is NULL). Live
    mode can never be switched on from the environment."""
    import os
    if (os.getenv("ACQUISITION_WORKFORCE_AUTOSTART") or "").strip().lower() != "practice":
        return None
    s = state(db)
    if s.changed_at is not None or s.status != "stopped":
        return None
    s.status, s.mode, s.changed_at = "running", "practice", datetime.utcnow()
    s.reason = "Started in practice mode by the server setting ACQUISITION_WORKFORCE_AUTOSTART"
    s.next_pass_at = s.changed_at
    db.commit()
    _audit(db, None, "start", {"from": "stopped", "to": "running", "mode": "practice", "reason": s.reason})
    return "running"


def set_limits(db: Session, actor, data: Dict[str, Any]) -> AcquisitionWorkforceState:
    from fastapi import HTTPException
    s = state(db)
    for key, lo, hi in (("daily_job_limit", 0, 5000), ("max_jobs_per_pass", 1, 50), ("daily_ai_call_limit", 0, 1000)):
        if key in data and data[key] is not None:
            try:
                v = int(data[key])
            except (TypeError, ValueError):
                raise HTTPException(422, "%s must be a whole number" % key)
            if not lo <= v <= hi:
                raise HTTPException(422, "%s must be between %d and %d" % (key, lo, hi))
            setattr(s, key, v)
    if "paid_discovery_approved" in data:
        s.paid_discovery_approved = bool(data["paid_discovery_approved"])
    db.commit()
    _audit(db, actor, "limits", {k: data.get(k) for k in ("daily_job_limit", "max_jobs_per_pass",
                                                           "daily_ai_call_limit", "paid_discovery_approved")})
    return s


def set_client(db: Session, actor, org_id: str, enabled: bool, daily_job_limit: Optional[int] = None):
    from fastapi import HTTPException
    p = db.query(AcquisitionProfile).filter(AcquisitionProfile.organization_id == org_id).first()
    if p is None:
        raise HTTPException(404, "That company is not an Acquisition client")
    row = db.query(AcquisitionWorkforceClient).filter(AcquisitionWorkforceClient.organization_id == org_id).first()
    if row is None:
        row = AcquisitionWorkforceClient(organization_id=org_id)
        db.add(row)
    row.enabled = bool(enabled)
    if daily_job_limit is not None:
        row.daily_job_limit = max(0, min(1000, int(daily_job_limit)))
    row.changed_by_id = getattr(actor, "id", None)
    row.changed_at = datetime.utcnow()
    db.commit()
    _audit(db, actor, "client", {"organization_id": org_id, "enabled": row.enabled,
                                 "daily_job_limit": row.daily_job_limit})
    return row


def enabled_client_ids(db: Session) -> List[str]:
    return [r.organization_id for r in db.query(AcquisitionWorkforceClient)
            .filter(AcquisitionWorkforceClient.enabled.is_(True)).all()]


# ── the queue ───────────────────────────────────────────────────────────────
def enqueue(db: Session, worker: str, key: str, *, org_id: Optional[str] = None,
            target_type: Optional[str] = None, target_id: Optional[str] = None,
            is_practice: bool = False, run_after: Optional[datetime] = None) -> Optional[AcquisitionWorkerJob]:
    """Idempotent: one job per dedupe key, ever. Returns the new job or None."""
    dk = ("%s:%s" % (worker, key))[:300]
    if db.query(AcquisitionWorkerJob.id).filter(AcquisitionWorkerJob.dedupe_key == dk).first():
        return None
    job = AcquisitionWorkerJob(worker=worker, dedupe_key=dk, organization_id=org_id, target_type=target_type,
                               target_id=target_id, is_practice=is_practice, run_after=run_after)
    db.add(job)
    try:
        db.commit()
    except IntegrityError:          # another instance queued it first
        db.rollback()
        return None
    return job


def recover_leases(db: Session, now: Optional[datetime] = None) -> int:
    """Jobs left 'running' by a process that died (deploy, restart, OOM) go
    back to the queue, or to the failed queue once out of attempts."""
    now = now or datetime.utcnow()
    n = 0
    for j in (db.query(AcquisitionWorkerJob)
              .filter(AcquisitionWorkerJob.status == "running", AcquisitionWorkerJob.lease_until < now).all()):
        if j.attempts >= j.max_attempts:
            j.status, j.finished_at = "dead", now
            j.error = "The worker stopped mid-job %d times (restart or crash); moved to the failed queue" % j.attempts
        else:
            j.status, j.lease_until = "queued", None
            j.error = "Recovered after the worker stopped mid-job (restart or deploy)"
        n += 1
    if n:
        db.commit()
    return n


def _started_today(db: Session, org_id: Optional[str] = None, now: Optional[datetime] = None) -> int:
    now = now or datetime.utcnow()
    q = db.query(AcquisitionWorkerJob).filter(AcquisitionWorkerJob.started_at >= now - timedelta(hours=24))
    if org_id is not None:
        q = q.filter(AcquisitionWorkerJob.organization_id == org_id)
    return q.count()


# ── planning: what work exists right now ────────────────────────────────────
def plan(db: Session, now: Optional[datetime] = None) -> Dict[str, int]:
    """Queue the work the data shows is due. Cheap, idempotent, no network."""
    from app.models.lead_intel_models import LeadIntelProspect
    now = now or datetime.utcnow()
    s = state(db)
    out = {w: 0 for w in WORKER_KEYS}
    # Master pool research (EvoSys-owned; no contact is made by research).
    pool = (db.query(LeadIntelProspect)
            .filter(LeadIntelProspect.suppressed_reason.is_(None), LeadIntelProspect.duplicate_of_id.is_(None))
            .order_by(LeadIntelProspect.routed_org_id.is_(None), LeadIntelProspect.created_at).limit(500).all())
    for p in pool:
        if enqueue(db, "research", "pool:" + p.id, target_type="pool_prospect", target_id=p.id):
            out["research"] += 1
    clients = enabled_client_ids(db)
    day = now.strftime("%Y-%m-%d")
    for org_id in clients:
        prof = db.query(AcquisitionProfile).filter(AcquisitionProfile.organization_id == org_id).first()
        if prof is None:
            continue
        if enqueue(db, "discovery", "%s:%s" % (org_id, day), org_id=org_id, target_type="client", target_id=org_id):
            out["discovery"] += 1
        opps = (db.query(AcquisitionOpportunity).filter(AcquisitionOpportunity.organization_id == org_id).all())
        for o in opps:
            if s.mode == "practice" and not o.is_practice:
                continue
            if o.stage in (C.DISCOVERED, C.RESEARCHED, C.QUALIFIED):
                if enqueue(db, "research", "opp:" + o.id, org_id=org_id, target_type="opportunity",
                           target_id=o.id, is_practice=o.is_practice):
                    out["research"] += 1
            if o.stage in (C.CONTACT_VERIFIED, C.OUTREACH_READY) or (o.is_practice and o.stage in
                                                                     (C.DISCOVERED, C.RESEARCHED, C.QUALIFIED)):
                if enqueue(db, "outreach", "first:" + o.id, org_id=org_id, target_type="opportunity",
                           target_id=o.id, is_practice=o.is_practice):
                    out["outreach"] += 1
            if o.stage == C.INTERESTED:
                if enqueue(db, "booking", "link:%s:%s" % (o.id, (o.interested_at or o.stage_entered_at or now)
                                                          .strftime("%Y%m%d%H%M")),
                           org_id=org_id, target_type="opportunity", target_id=o.id, is_practice=o.is_practice):
                    out["booking"] += 1
        out["replies"] += _plan_replies(db, org_id, s.mode)
    return out


def _plan_replies(db: Session, org_id: str, mode: str) -> int:
    from app.models.models import Reply
    n = 0
    rows = (db.query(Reply, AcquisitionOpportunity)
            .join(AcquisitionOpportunity, AcquisitionOpportunity.lead_id == Reply.lead_id)
            .filter(AcquisitionOpportunity.organization_id == org_id, Reply.source == "email")
            .order_by(Reply.received_at.desc()).limit(200).all())
    for r, o in rows:
        if mode == "practice" and not o.is_practice:
            continue
        if enqueue(db, "replies", "reply:" + r.id, org_id=org_id, target_type="reply", target_id=r.id,
                   is_practice=o.is_practice):
            n += 1
    return n


# ── running ─────────────────────────────────────────────────────────────────
def handlers() -> Dict[str, Callable]:
    return {"discovery": run_discovery, "research": run_research, "contacts": run_contacts,
            "outreach": run_outreach, "replies": run_replies, "booking": run_booking}


def run_pass(db: Session, now: Optional[datetime] = None, *, practice_only: bool = False,
             fetch=None, mx=None, force: bool = False) -> Dict[str, Any]:
    """One bounded pass. Does nothing unless the workforce is Running (or a
    practice test was asked for and nothing is emergency-stopped)."""
    now = now or datetime.utcnow()
    s = state(db)
    if s.status != "running" and not (force and s.status != "emergency_stopped"):
        return {"ran": 0, "skipped": "workforce is %s" % s.status}
    recovered = recover_leases(db, now)
    planned = plan(db, now)
    ran, results = 0, {}
    clients = set(enabled_client_ids(db))
    limit = s.max_jobs_per_pass
    q = (db.query(AcquisitionWorkerJob).filter(AcquisitionWorkerJob.status == "queued")
         .filter((AcquisitionWorkerJob.run_after.is_(None)) | (AcquisitionWorkerJob.run_after <= now))
         .order_by(AcquisitionWorkerJob.created_at))
    for job in q.limit(limit * 5).all():
        if ran >= limit:
            break
        db.refresh(s)
        if s.status != "running" and not (force and s.status != "emergency_stopped"):
            results["stopped_mid_pass"] = s.status
            break
        if _started_today(db, now=now) >= s.daily_job_limit:
            results["daily_limit"] = "Global daily job limit (%d) reached" % s.daily_job_limit
            break
        if job.organization_id is not None:
            if job.organization_id not in clients:
                continue
            row = db.query(AcquisitionWorkforceClient).filter(
                AcquisitionWorkforceClient.organization_id == job.organization_id).first()
            if row is not None and _started_today(db, job.organization_id, now) >= row.daily_job_limit:
                continue
        if (practice_only or s.mode == "practice") and job.organization_id is not None and not job.is_practice:
            continue
        _execute(db, job, now, fetch=fetch, mx=mx)
        ran += 1
        results[job.worker] = results.get(job.worker, 0) + 1
    s = state(db)
    s.last_pass_at = now
    s.next_pass_at = now + timedelta(seconds=PASS_SECONDS) if s.status == "running" else None
    summary = {"ran": ran, "recovered": recovered, "queued_now": planned, "by_worker": results}
    s.last_pass_summary = json.dumps(summary, default=str)
    db.commit()
    return summary


def _execute(db: Session, job: AcquisitionWorkerJob, now: datetime, fetch=None, mx=None) -> None:
    job.status, job.attempts = "running", job.attempts + 1
    job.started_at, job.lease_until = now, now + timedelta(seconds=LEASE_SECONDS)
    db.commit()
    try:
        fn = handlers()[job.worker]
        status, result = fn(db, job, fetch=fetch, mx=mx)
        job.status, job.result, job.error = status, json.dumps(result, default=str)[:8000], None
        job.finished_at, job.lease_until = datetime.utcnow(), None
        db.commit()
    except Exception as exc:                                     # noqa: BLE001
        db.rollback()
        job = db.query(AcquisitionWorkerJob).filter(AcquisitionWorkerJob.id == job.id).one()
        job.error = ("%s: %s" % (type(exc).__name__, str(exc)))[:500]
        job.lease_until = None
        if job.attempts >= job.max_attempts:
            job.status, job.finished_at = "dead", datetime.utcnow()
        else:
            job.status = "queued"
            job.run_after = datetime.utcnow() + timedelta(minutes=5 * (2 ** (job.attempts - 1)))
        db.commit()
        log.warning("workforce job %s (%s) failed: %s", job.id, job.worker, job.error)


# ── worker: discovery ───────────────────────────────────────────────────────
def run_discovery(db: Session, job, **_):
    from app.models.lead_intel_models import LeadIntelProspect
    s = state(db)
    org_id = job.organization_id
    prof = db.query(AcquisitionProfile).filter(AcquisitionProfile.organization_id == org_id).first()
    segs = set(C.jload(prof.segments, []) if prof else [])
    states = set(C.jload(prof.service_states, []) if prof else []) or {"TX"}
    excl = C.jload(prof.exclusions, []) if prof else []
    fits = []
    for p in (db.query(LeadIntelProspect).filter(LeadIntelProspect.routed_org_id.is_(None),
                                                 LeadIntelProspect.suppressed_reason.is_(None),
                                                 LeadIntelProspect.duplicate_of_id.is_(None)).limit(500).all()):
        if segs and (p.industry or "") not in segs:
            continue
        if p.state and p.state.upper() not in states:
            continue
        dom = H.normalize_domain(p.website or "")
        if dom and H.do_not_contact_domain_match(dom, excl):
            continue
        fits.append({"id": p.id, "name": p.name, "segment": p.industry, "city": p.city})
    result = {"pool_matches": len(fits), "examples": fits[:10], "new_businesses_added": 0,
              "note": "Matching master-pool businesses are listed for assignment; assignment stays a "
                      "person's decision so two clients never compete for one business."}
    if not s.paid_discovery_approved:
        result["blocked"] = ("No new businesses were searched for: paid discovery (Google Places, metered) "
                             "is not approved, and no free business source is configured.")
        return "blocked", result
    from app.services.acquisition import discovery as D
    if not D._flag("ACQUISITION_DISCOVERY_ENABLED"):
        result["blocked"] = "Paid discovery is approved here but switched off on the server (ACQUISITION_DISCOVERY_ENABLED)."
        return "blocked", result
    result["blocked"] = ("Paid discovery is approved, but an automatic run needs a search plan with a priced "
                         "budget; run it from Discovery so the cost is shown before it runs.")
    return "blocked", result


# ── worker: research (and contact verification) ─────────────────────────────
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
_PHONE_RE = re.compile(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")
_BAD_EMAIL_HOSTS = ("example.com", "sentry.io", "wixpress.com", "domain.com", "email.com", "yourdomain.com")


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.desc, self._in_title, self.links, self.mailto, self.text = "", "", False, [], [], []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta" and (a.get("name") or "").lower() == "description":
            self.desc = (a.get("content") or "")[:300]
        if tag == "a" and a.get("href"):
            href = a["href"].strip()
            if href.lower().startswith("mailto:"):
                self.mailto.append(href[7:].split("?")[0].strip())
            else:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.text.append(data)


def parse_page(html: str) -> Dict[str, Any]:
    p = _Page()
    try:
        p.feed(html or "")
    except Exception:                                            # noqa: BLE001
        pass
    text = H.fence(" ".join(" ".join(p.text).split())[:20000], "website") if p.text else ""
    emails = []
    for e in p.mailto + _EMAIL_RE.findall(" ".join(p.text)):
        e = e.strip().strip(".").lower()
        if _EMAIL_RE.fullmatch(e) and not e.endswith(_BAD_EMAIL_HOSTS) and e not in emails \
                and not re.search(r"\.(png|jpg|jpeg|gif|webp|svg)$", e):
            emails.append(e)
    phones = list(dict.fromkeys(_PHONE_RE.findall(" ".join(p.text))))[:3]
    return {"title": " ".join(p.title.split())[:200], "description": " ".join(p.desc.split()),
            "emails": emails[:10], "phones": phones, "links": p.links[:200], "text_fenced": text}


def _default_fetch(url: str):
    from app.services.acquisition import public_http as PH
    with PH.client() as c:
        return PH.get_text(c, url)


def _default_mx(domain: str) -> Optional[bool]:
    """True/False when DNS answered; None when it could not be checked."""
    try:
        import dns.resolver  # type: ignore
    except ImportError:
        return None
    try:
        return len(dns.resolver.resolve(domain, "MX", lifetime=5)) > 0
    except Exception as exc:                                     # noqa: BLE001
        name = type(exc).__name__
        if name in ("NXDOMAIN", "NoAnswer"):
            return False
        return None


def _same_site(email: str, site_domain: str) -> bool:
    host = email.split("@", 1)[1].lower()
    return host == site_domain or host.endswith("." + site_domain) or site_domain.endswith("." + host)


def read_site(website: str, fetch=None) -> Dict[str, Any]:
    """The homepage plus up to three contact/about pages ON THE SAME SITE."""
    fetch = fetch or _default_fetch
    base = website.strip()
    if not base.lower().startswith(("http://", "https://")):
        base = "https://" + base
    domain = H.normalize_domain(base)
    pages, tried = [], []
    final, html = fetch(base)
    tried.append(base)
    if final is None:
        return {"domain": domain, "pages": [], "error": html, "tried": tried}
    pages.append((final, parse_page(html)))
    cands = []
    for href in pages[0][1]["links"]:
        u = urljoin(final, href)
        low = u.lower()
        if H.normalize_domain(u) == domain and any(k in low for k in ("contact", "about", "team")):
            cands.append(u.split("#")[0])
    cands += [urljoin(final, p) for p in CONTACT_PATHS]
    for u in dict.fromkeys(cands):
        if len(pages) >= MAX_SITE_PAGES or u in tried:
            continue
        tried.append(u)
        f, h = fetch(u)
        if f is None or H.normalize_domain(f) != domain:
            continue
        pages.append((f, parse_page(h)))
    return {"domain": domain, "pages": pages, "tried": tried}


def _pool_evidence(p, entry: Dict[str, Any]) -> None:
    try:
        ev = json.loads(p.evidence_json) if p.evidence_json else {}
    except ValueError:
        ev = {}
    if not isinstance(ev, dict):
        ev = {}
    ev.setdefault("research_log", []).append(entry)
    p.evidence_json = json.dumps(ev, default=str)


def verify_contacts(site: Dict[str, Any], mx=None) -> Dict[str, Any]:
    """Contact statuses, strictly from what the site published."""
    mx = mx or _default_mx
    dom = site["domain"]
    published, phones = [], []
    for url, pg in site["pages"]:
        for e in pg["emails"]:
            if e not in [x["email"] for x in published]:
                published.append({"email": e, "source_url": url})
        for ph in pg["phones"]:
            if ph not in [x["phone"] for x in phones]:
                phones.append({"phone": ph, "source_url": url})
    best = None
    for c in published:
        own = _same_site(c["email"], dom)
        c["own_domain"] = own
        c["mx"] = mx(c["email"].split("@", 1)[1]) if own else None
        if own and c["mx"] is True:
            c["status"] = "email_validated"
        elif own and c["mx"] is None:
            c["status"] = "email_published"          # couldn't check mail routing
        elif own:
            c["status"] = "email_published"          # domain does not accept mail
        else:
            c["status"] = "email_published_other_domain"
        if best is None and c["status"] == "email_validated":
            best = c
    if best:
        status = "outreach_eligible"
    elif published:
        status = "email_published"
    elif phones:
        status = "contact_identified"                # a phone is NOT email readiness
    elif site["pages"]:
        status = "business_found"
    else:
        status = "research_incomplete"
    return {"status": status, "best": best, "emails": published, "phones": phones}


def run_research(db: Session, job, fetch=None, mx=None, **_):
    now = datetime.utcnow()
    if job.target_type == "pool_prospect":
        from app.models.lead_intel_models import LeadIntelProspect
        p = db.query(LeadIntelProspect).filter(LeadIntelProspect.id == job.target_id).first()
        if p is None:
            return "blocked", {"reason": "The business is no longer in the master pool"}
        if p.suppressed_reason:
            return "blocked", {"reason": "Suppressed: %s" % p.suppressed_reason}
        if not (p.website or "").strip():
            return "blocked", {"status": "research_incomplete",
                               "reason": "No official website is on file, and no free search source is approved "
                                         "to find one. A person can add the website, then research re-runs."}
        site = read_site(p.website, fetch)
        if not site["pages"]:
            return "blocked", {"status": "research_incomplete", "reason": "Website could not be read (%s)"
                               % site.get("error")}
        v = verify_contacts(site, mx)
        home_url, home = site["pages"][0]
        entry = {"at": now.isoformat(), "by": "Harvey research worker", "source_url": home_url,
                 "title": home["title"], "description": home["description"],
                 "pages_read": [u for u, _ in site["pages"]], "contact_status": v["status"],
                 "emails_published": v["emails"], "phones_published": v["phones"]}
        _pool_evidence(p, entry)
        p.researched_at = now
        if not p.source_url:
            p.source_url = home_url
        if v["best"] and not p.email_verified:
            p.email, p.email_verified = v["best"]["email"], True
            _push_pool_contact(db, p, v["best"])
        elif v["emails"] and not p.email:
            p.email, p.email_verified = v["emails"][0]["email"], False
        db.commit()
        return "done", {"status": v["status"], "pages": len(site["pages"]),
                        "verified_email": (v["best"] or {}).get("email"), "source": home_url}
    # A client's own prospect.
    opp = db.query(AcquisitionOpportunity).filter(AcquisitionOpportunity.id == job.target_id,
                                                  AcquisitionOpportunity.organization_id == job.organization_id).first()
    if opp is None:
        return "blocked", {"reason": "Prospect not found for this client"}
    lead = db.query(Lead).filter(Lead.id == opp.lead_id, Lead.organization_id == opp.organization_id).first()
    if lead is not None and lead.status == "dnc":
        return "blocked", {"reason": "Asked not to be contacted"}
    f = C.jload(lead.custom_fields, {}) if lead else {}
    website = (f or {}).get("website") or (("https://" + opp.domain) if opp.domain else "")
    if not website:
        return "blocked", {"status": "research_incomplete", "reason": "No official website on file"}
    site = read_site(website, fetch)
    if not site["pages"]:
        return "blocked", {"status": "research_incomplete", "reason": "Website could not be read (%s)" % site.get("error")}
    v = verify_contacts(site, mx)
    home_url, home = site["pages"][0]
    _add_ev(db, opp, "website", home["title"] or site["domain"], home_url, 0.9, True)
    if home["description"]:
        _add_ev(db, opp, "note", "Site says: " + home["description"][:300], home_url, 0.7, False)
    for ph in v["phones"][:1]:
        _add_ev(db, opp, "contact_phone", ph["phone"], ph["source_url"], 0.8, True)
    if v["best"]:
        _add_ev(db, opp, "contact_email", v["best"]["email"], v["best"]["source_url"], 0.9, True)
        if lead is not None:
            lead.email = v["best"]["email"]
    from app.services.acquisition import engine as E
    if opp.stage == C.DISCOVERED:
        E._move(db, opp, C.RESEARCHED, None, "Website researched by the workforce: %s" % home_url)
    if v["best"] and opp.stage in (C.RESEARCHED, C.QUALIFIED) and not opp.is_practice:
        E._move(db, opp, C.CONTACT_VERIFIED, None,
                "Email published on the business's own website and its domain accepts mail")
    db.commit()
    return "done", {"status": v["status"], "pages": len(site["pages"]),
                    "verified_email": (v["best"] or {}).get("email"), "source": home_url}


def run_contacts(db: Session, job, fetch=None, mx=None, **kw):
    """Contact verification is done inside research (same pages, one read);
    a standalone contacts job re-runs that check."""
    return run_research(db, job, fetch=fetch, mx=mx)


def _add_ev(db, opp, kind, value, url, conf, verified):
    exists = (db.query(AcquisitionEvidence.id)
              .filter(AcquisitionEvidence.opportunity_id == opp.id, AcquisitionEvidence.kind == kind,
                      AcquisitionEvidence.value == value).first())
    if exists:
        return
    db.add(AcquisitionEvidence(organization_id=opp.organization_id, opportunity_id=opp.id, kind=kind,
                               value=str(value)[:2000], source_url=url, confidence=conf,
                               verified=bool(verified and url), recorded_by_job=JOB_ACTOR))


def _push_pool_contact(db, p, best):
    if not p.routed_org_id:
        return
    opp = (db.query(AcquisitionOpportunity)
           .filter(AcquisitionOpportunity.organization_id == p.routed_org_id,
                   AcquisitionOpportunity.source == "master_pool:" + p.id).first())
    if opp is not None:
        _add_ev(db, opp, "contact_email", best["email"], best["source_url"], 0.9, True)
        lead = db.query(Lead).filter(Lead.id == opp.lead_id).first()
        if lead is not None and lead.status != "dnc":
            lead.email = best["email"]


# ── worker: outreach drafting ───────────────────────────────────────────────
def _org_profile(db, org_id):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    prof = db.query(AcquisitionProfile).filter(AcquisitionProfile.organization_id == org_id).first()
    return org, prof


def _pending_draft(db, opp_id, kind):
    return (db.query(AcquisitionDraft).filter(AcquisitionDraft.opportunity_id == opp_id,
                                              AcquisitionDraft.kind == kind,
                                              AcquisitionDraft.status == "pending").first())


def run_outreach(db: Session, job, **_):
    from app.services.acquisition import messaging as M
    from app.services.acquisition import outreach as O
    opp = db.query(AcquisitionOpportunity).filter(AcquisitionOpportunity.id == job.target_id,
                                                  AcquisitionOpportunity.organization_id == job.organization_id).first()
    if opp is None:
        return "blocked", {"reason": "Prospect not found"}
    org, prof = _org_profile(db, opp.organization_id)
    lead = db.query(Lead).filter(Lead.id == opp.lead_id, Lead.organization_id == opp.organization_id).first()
    if lead is None or lead.status == "dnc":
        return "blocked", {"reason": "Asked not to be contacted"}
    excl = C.jload(prof.exclusions, []) if prof else []
    if (lead.email and H.do_not_contact_match(lead.email, excl)) or \
            (opp.domain and H.do_not_contact_domain_match(opp.domain, excl)):
        return "blocked", {"reason": "On the client's do-not-contact list"}
    if _pending_draft(db, opp.id, "first"):
        return "done", {"note": "A draft is already waiting for approval"}
    ev = [{"kind": e.kind, "value": e.value, "source_url": e.source_url}
          for e in db.query(AcquisitionEvidence).filter(AcquisitionEvidence.opportunity_id == opp.id,
                                                        AcquisitionEvidence.verified.is_(True)).all()]
    if not opp.is_practice and not any(e["kind"] == "contact_email" for e in ev):
        return "blocked", {"reason": "No verified, sourced contact email yet"}
    company = (C.jload(lead.custom_fields, {}) or {}).get("company") or "your business"
    who = O.client_name(org, prof)
    d = M.first_touch(org, prof, company, (lead.first_name or "").strip(),
                      (prof.owner_name if prof and prof.owner_name else who), ev)
    ai_note = ai_status(db)
    gen = "template:%s" % M.category(org)
    row = AcquisitionDraft(organization_id=opp.organization_id, opportunity_id=opp.id, kind="first",
                           subject=d["subject"], body=d["body"], generated_by=gen,
                           evidence_refs=json.dumps(ev[:10]), followups=json.dumps(M.followup_plan(org)),
                           revisions=json.dumps([{"at": datetime.utcnow().isoformat(), "by": JOB_ACTOR,
                                                  "action": "drafted"}]),
                           is_practice=opp.is_practice, job_id=job.id)
    db.add(row)
    if opp.stage == C.CONTACT_VERIFIED:
        from app.services.acquisition import engine as E
        E._move(db, opp, C.OUTREACH_READY, None, "Workforce drafted a first email - waiting for approval")
    db.commit()
    return "done", {"draft_id": row.id, "generated_by": gen, "ai": ai_note, "needs_approval": True,
                    "sent": 0}


def ai_status(db: Optional[Session] = None) -> Dict[str, Any]:
    """Whether model-written drafting could be used, in plain words. The
    workforce uses evidence-only templates unless the owner allows AI calls."""
    try:
        s_limit = state(db).daily_ai_call_limit if db is not None else 0
    except Exception:                                            # noqa: BLE001
        s_limit = 0
    if not s_limit:
        return {"available": False, "reason": "AI drafting is off: the owner has allowed 0 AI calls per day. "
                                              "Drafts use the category template and verified facts only."}
    from app.services import ai_gateway as G
    if not G.background_enabled():
        return {"available": False, "reason": "AI drafting is allowed here but background AI is switched off "
                                              "on the server (AI_BACKGROUND_AUTOMATION_ENABLED)."}
    return {"available": True, "reason": "AI drafting allowed, capped at %d calls/day" % s_limit}


# ── worker: replies ─────────────────────────────────────────────────────────
_RULES: List[Tuple[str, re.Pattern]] = [
    ("out_of_office", re.compile(r"\b(out of (the )?office|on vacation|away until|auto(matic)? ?reply|limited access to email)\b")),
    ("reschedule_request", re.compile(r"\b(reschedul\w*|move (our|the) (appointment|meeting)|different (day|time))\b")),
    ("appointment_request", re.compile(r"\b(book|schedule|set up|come (by|out)|what times?|when can you|available (on|this|next))\b")),
    ("not_interested", re.compile(r"\b(not interested|no need|we('| a)re (all )?set|already have (a|someone)|no thank)\b")),
    ("request_for_information", re.compile(r"\b(send (me|us) (more )?(info|information|details|pricing)|more info)\b")),
    ("objection", re.compile(r"\b(too expensive|budget|contract (with|until)|locked in|maybe later|not right now)\b")),
    ("question", re.compile(r"\?")),
    ("interested", re.compile(r"\b(interested|sounds good|yes,? (please|we would)|let'?s talk|tell me more|would love)\b")),
]
HUMAN_REVIEW = {"complaint_legal", "ambiguous", "reschedule_request", "objection", "question",
                "request_for_information"}


def classify(text: str) -> Dict[str, Any]:
    """Deterministic first: opt-out and legal/complaint rules always win."""
    flat = " ".join((text or "").lower().split())
    opt = H.opt_out_reason(text or "")
    if opt:
        return {"label": "unsubscribe", "confidence": 1.0, "evidence": str(opt)[:200], "rule": "opt_out"}
    esc = H.escalation_reason(text or "")
    if esc:
        return {"label": "complaint_legal", "confidence": 1.0, "evidence": esc, "rule": "escalation"}
    hits = [(lab, m.group(0)) for lab, m in ((lab, rx.search(flat)) for lab, rx in _RULES) if m]
    if not hits:
        return {"label": "ambiguous", "confidence": 0.0, "evidence": "", "rule": "none"}
    labels = [h[0] for h in hits]
    if "not_interested" in labels:
        return {"label": "not_interested", "confidence": 0.9, "evidence": dict(hits)["not_interested"], "rule": "phrase"}
    for lab in ("out_of_office", "reschedule_request", "appointment_request", "request_for_information",
                "objection", "interested", "question"):
        if lab in labels:
            conf = 0.85 if len(set(labels) - {"question"}) <= 1 else 0.6
            return {"label": lab if conf >= 0.6 else "ambiguous", "confidence": conf,
                    "evidence": dict(hits)[lab], "rule": "phrase"}
    return {"label": "ambiguous", "confidence": 0.0, "evidence": "", "rule": "none"}


NEXT_STEP = {
    "interested": "Marked Interested; the appointment setter prepares a scheduling reply",
    "appointment_request": "Marked Interested; the appointment setter prepares a scheduling reply",
    "not_interested": "Closed as not interested; no further outreach",
    "unsubscribe": "Added to do-not-contact; nothing more will be sent",
    "out_of_office": "No action; follow-up waits until they are back",
    "complaint_legal": "Needs a person now - complaint or legal language",
    "reschedule_request": "Needs a person - rescheduling is handled by a person for now",
    "question": "Needs a person to answer from the client's approved facts",
    "request_for_information": "Needs a person to send approved information",
    "objection": "Needs a person to respond",
    "ambiguous": "Needs a person to read it",
}


def run_replies(db: Session, job, **_):
    from app.models.models import Reply
    from app.services.acquisition import engine as E
    r = db.query(Reply).filter(Reply.id == job.target_id).first()
    if r is None:
        return "blocked", {"reason": "Reply not found"}
    opp = db.query(AcquisitionOpportunity).filter(AcquisitionOpportunity.lead_id == r.lead_id,
                                                  AcquisitionOpportunity.organization_id == job.organization_id).first()
    if opp is None:
        return "blocked", {"reason": "Not an acquisition prospect of this client"}
    lead = db.query(Lead).filter(Lead.id == r.lead_id).first()
    c = classify(r.body or "")
    action = NEXT_STEP[c["label"]]
    if c["label"] == "unsubscribe" or (lead is not None and lead.status == "dnc"):
        from app.services.acquisition import outreach as O
        if lead is not None and lead.status != "dnc":
            O.on_reply(db, lead, r.body or "")
        c["label"], action = "unsubscribe", NEXT_STEP["unsubscribe"]
    elif c["label"] in ("interested", "appointment_request") and c["confidence"] >= 0.85:
        if opp.stage in (C.CONTACTED, C.REPLIED, C.OUTREACH_READY):
            if opp.stage != C.REPLIED:
                E._move(db, opp, C.REPLIED, None, "Replied: " + (r.body or "")[:200])
            opp.interested_at = opp.interested_at or datetime.utcnow()
            E._move(db, opp, C.INTERESTED, None, "Workforce read the reply as %s (\"%s\")"
                    % (c["label"].replace("_", " "), c["evidence"]))
    elif c["label"] == "not_interested" and opp.stage in (C.CONTACTED, C.REPLIED):
        opp.lost_reason = "Not interested (reply)"
        if opp.stage != C.REPLIED:
            E._move(db, opp, C.REPLIED, None, "Replied: " + (r.body or "")[:200])
        E._move(db, opp, C.LOST, None, "Workforce read the reply as not interested")
    E._event(db, opp.organization_id, opp.id, "reply_classified", None,
             note=("%s (%.0f%%) - %s" % (c["label"], c["confidence"] * 100, action))[:300])
    db.commit()
    return "done", {"reply_id": r.id, "classification": c["label"], "confidence": c["confidence"],
                    "evidence": c["evidence"], "next": action, "human": c["label"] in HUMAN_REVIEW}


# ── worker: appointment setter ──────────────────────────────────────────────
class _System:
    id = None
    full_name = "Harvey workforce"
    email = None


def run_booking(db: Session, job, **_):
    from fastapi import HTTPException
    from app.services.acquisition import booking as B
    from app.services.acquisition import messaging as M
    from app.services.acquisition import outreach as O
    opp = db.query(AcquisitionOpportunity).filter(AcquisitionOpportunity.id == job.target_id,
                                                  AcquisitionOpportunity.organization_id == job.organization_id).first()
    if opp is None or opp.stage != C.INTERESTED:
        return "done", {"note": "No longer waiting to book"}
    org, prof = _org_profile(db, opp.organization_id)
    tz = (prof.timezone if prof else None) or C.DEFAULT_TIMEZONE
    if tz != "America/Chicago":
        return "blocked", {"reason": "This client's time zone is %s. Booking currently offers Central time only, "
                                     "so a person must schedule this one." % tz}
    if _pending_draft(db, opp.id, "booking"):
        return "done", {"note": "A scheduling reply is already waiting for approval"}
    try:
        link = B.make_link(db, org, opp, _System())
    except HTTPException as exc:
        return "blocked", {"reason": exc.detail}
    lead = db.query(Lead).filter(Lead.id == opp.lead_id).first()
    who = O.client_name(org, prof)
    d = M.booking_reply(org, prof, (lead.first_name or "").strip() if lead else "", link["url"],
                        (prof.owner_name if prof and prof.owner_name else who))
    row = AcquisitionDraft(organization_id=opp.organization_id, opportunity_id=opp.id, kind="booking",
                           subject=d["subject"], body=d["body"], generated_by="template:%s" % M.category(org),
                           revisions=json.dumps([{"at": datetime.utcnow().isoformat(), "by": JOB_ACTOR,
                                                  "action": "drafted"}]),
                           is_practice=opp.is_practice, job_id=job.id)
    db.add(row)
    db.commit()
    return "done", {"draft_id": row.id, "booking_url_made": True, "calendar_of": link["booker"],
                    "note": "Booked only when the prospect picks a real free time on the calendar"}


# ── drafts: approval history ────────────────────────────────────────────────
def draft_json(d: AcquisitionDraft) -> Dict[str, Any]:
    return {"id": d.id, "opportunity_id": d.opportunity_id, "kind": d.kind, "status": d.status,
            "subject": d.subject, "body": d.body, "generated_by": d.generated_by,
            "evidence": C.jload(d.evidence_refs, []), "followups": C.jload(d.followups, []),
            "revisions": C.jload(d.revisions, []), "is_practice": d.is_practice, "created_at": C.iso(d.created_at)}


def drafts_for(db: Session, org_id: str, opp_id: Optional[str] = None, status: str = "pending"):
    q = db.query(AcquisitionDraft).filter(AcquisitionDraft.organization_id == org_id)
    if opp_id:
        q = q.filter(AcquisitionDraft.opportunity_id == opp_id)
    if status:
        q = q.filter(AcquisitionDraft.status == status)
    return [draft_json(d) for d in q.order_by(AcquisitionDraft.created_at.desc()).limit(100).all()]


def decide_draft(db: Session, org_id: str, draft_id: str, actor, action: str) -> Dict[str, Any]:
    """'rejected' by a person, or 'sent' after the existing send succeeded."""
    from fastapi import HTTPException
    d = db.query(AcquisitionDraft).filter(AcquisitionDraft.id == draft_id,
                                          AcquisitionDraft.organization_id == org_id).first()
    if d is None:
        raise HTTPException(404, "Draft not found")
    if action not in ("rejected", "sent"):
        raise HTTPException(422, "Unknown decision")
    revs = C.jload(d.revisions, [])
    revs.append({"at": datetime.utcnow().isoformat(), "by": getattr(actor, "email", None) or "user", "action": action})
    d.revisions, d.status = json.dumps(revs), action
    d.decided_at, d.decided_by_id = datetime.utcnow(), getattr(actor, "id", None)
    db.commit()
    return draft_json(d)


# ── health & reporting ──────────────────────────────────────────────────────
def blockers(db: Session) -> Dict[str, List[str]]:
    """Why each worker can't do (all of) its job right now, in plain words."""
    import os
    from app.services.acquisition import outreach as O
    s = state(db)
    out: Dict[str, List[str]] = {w: [] for w in WORKER_KEYS}
    clients = enabled_client_ids(db)
    if s.status != "running":
        for w in WORKER_KEYS:
            out[w].append("The workforce is %s" % s.status.replace("_", " "))
    if not clients:
        for w in ("discovery", "outreach", "replies", "booking"):
            out[w].append("No service company is switched on for the workforce")
    if not s.paid_discovery_approved:
        out["discovery"].append("Paid discovery is not approved, so no new businesses are searched for")
    try:
        import dns.resolver  # noqa: F401  # type: ignore
    except ImportError:
        out["contacts"].append("Mail-server (MX) checks are unavailable, so no email can be marked validated")
    ai = ai_status(db)
    if not ai["available"]:
        out["outreach"].append(ai["reason"])
    if not O.enabled():
        out["outreach"].append("Sending is switched off on the server (ACQUISITION_OUTREACH_ENABLED); "
                               "drafts wait for approval and nothing is sent")
    if s.mode == "practice":
        out["outreach"].append("Practice mode: only practice prospects and the owner's test inboxes")
    if not os.getenv("ACQUISITION_TEST_RECIPIENTS", "").strip():
        out["outreach"].append("No owner test inbox is set (ACQUISITION_TEST_RECIPIENTS)")
    cal = 0
    for org_id in clients:
        p = db.query(AcquisitionProfile).filter(AcquisitionProfile.organization_id == org_id).first()
        if p and (p.booking_user_id or p.test_booking_user_id):
            cal += 1
    if clients and not cal:
        out["booking"].append("No enabled company has a booking calendar chosen")
    out["booking"].append("Rescheduling and cancellation go to a person; booking offers Central time only")
    return out


def health(db: Session) -> Dict[str, Any]:
    s = state(db)
    now = datetime.utcnow()
    bl = blockers(db)
    workers = []
    for w in WORKERS:
        q = db.query(AcquisitionWorkerJob).filter(AcquisitionWorkerJob.worker == w["key"])
        last_ok = q.filter(AcquisitionWorkerJob.status == "done").order_by(AcquisitionWorkerJob.finished_at.desc()).first()
        last_bad = (q.filter(AcquisitionWorkerJob.status.in_(("dead", "failed")))
                    .order_by(AcquisitionWorkerJob.finished_at.desc()).first())
        retrying = q.filter(AcquisitionWorkerJob.status == "queued", AcquisitionWorkerJob.error.isnot(None)).first()
        counts = {st: q.filter(AcquisitionWorkerJob.status == st).count()
                  for st in ("queued", "running", "done", "blocked", "dead", "cancelled")}
        if s.status == "emergency_stopped" or s.status == "stopped":
            st = "configured"
        elif s.status == "paused":
            st = "paused"
        elif counts["running"]:
            st = "running"
        elif retrying is not None:
            st = "recovering"
        elif last_bad is not None and (last_ok is None or (last_bad.finished_at or now) > (last_ok.finished_at or now)):
            st = "failed"
        elif counts["queued"] == 0 and counts["blocked"] and not counts["done"]:
            st = "blocked"
        elif counts["queued"] == 0 and counts["done"]:
            st = "completed"
        else:
            st = "ready"
        workers.append({"key": w["key"], "label": w["label"], "state": st, "counts": counts,
                        "last_success": C.iso(last_ok.finished_at) if last_ok else None,
                        "last_failure": C.iso(last_bad.finished_at) if last_bad else None,
                        "last_error": last_bad.error if last_bad else None, "blockers": bl[w["key"]]})
    clients = []
    for p in db.query(AcquisitionProfile).all():
        org = db.query(Organization).filter(Organization.id == p.organization_id).first()
        row = db.query(AcquisitionWorkforceClient).filter(
            AcquisitionWorkforceClient.organization_id == p.organization_id).first()
        clients.append({"organization_id": p.organization_id, "name": (p.business_name or (org.name if org else "")),
                        "category": (org.industry if org else None), "enabled": bool(row and row.enabled),
                        "daily_job_limit": row.daily_job_limit if row else 40, "client_paused": bool(p.paused)})
    pending = db.query(AcquisitionDraft).filter(AcquisitionDraft.status == "pending").count()
    return {"status": s.status, "mode": s.mode, "reason": s.reason, "changed_at": C.iso(s.changed_at),
            "last_pass_at": C.iso(s.last_pass_at), "next_pass_at": C.iso(s.next_pass_at),
            "last_pass": C.jload(s.last_pass_summary, None),
            "limits": {"daily_job_limit": s.daily_job_limit, "max_jobs_per_pass": s.max_jobs_per_pass,
                       "daily_ai_call_limit": s.daily_ai_call_limit,
                       "paid_discovery_approved": s.paid_discovery_approved},
            "jobs_last_24h": _started_today(db, now=now),
            "queue_depth": db.query(AcquisitionWorkerJob).filter(AcquisitionWorkerJob.status == "queued").count(),
            "failed_jobs": db.query(AcquisitionWorkerJob).filter(AcquisitionWorkerJob.status == "dead").count(),
            "pending_approvals": pending, "workers": workers, "clients": clients,
            "usage": {"ai_calls_today": 0, "metered_cost_usd_today": round(sum(
                j.cost_usd or 0 for j in db.query(AcquisitionWorkerJob)
                .filter(AcquisitionWorkerJob.started_at >= now - timedelta(hours=24)).all()), 2),
                "note": "No metered AI or discovery calls are made while their limits are 0 / not approved."},
            "results": report(db), "ai": ai_status(db)}


def report(db: Session) -> Dict[str, Any]:
    """Truthful counts, real and practice kept apart. Drafts are never bookings."""
    from app.models.lead_intel_models import LeadIntelProspect
    from app.models.models import EmailMessage, Reply
    out = {}
    for label, practice in (("real", False), ("practice", True)):
        opps = db.query(AcquisitionOpportunity).filter(AcquisitionOpportunity.is_practice.is_(practice))
        ids = [o.id for o in opps.all()]
        lead_ids = [o.lead_id for o in opps.all()]
        r = lambda st: opps.filter(AcquisitionOpportunity.stage.in_(st)).count()  # noqa: E731
        out[label] = {
            "verified_contacts": (db.query(AcquisitionEvidence)
                                  .filter(AcquisitionEvidence.opportunity_id.in_(ids),
                                          AcquisitionEvidence.kind == "contact_email",
                                          AcquisitionEvidence.verified.is_(True)).count() if ids else 0),
            "drafts_prepared": db.query(AcquisitionDraft).filter(AcquisitionDraft.is_practice.is_(practice)).count(),
            "messages_sent": (db.query(EmailMessage).filter(EmailMessage.lead_id.in_(lead_ids),
                                                            EmailMessage.send_source == "acquisition_outreach",
                                                            EmailMessage.status == "sent").count() if lead_ids else 0),
            "replies": (db.query(Reply).filter(Reply.lead_id.in_(lead_ids), Reply.source == "email").count()
                        if lead_ids else 0),
            "interested": opps.filter(AcquisitionOpportunity.interested_at.isnot(None)).count(),
            "appointments_confirmed": opps.filter(AcquisitionOpportunity.booked_at.isnot(None)).count(),
            "appointments_held": r((C.HELD, C.QUOTED, C.WON)),
            "estimates": opps.filter(AcquisitionOpportunity.quoted_at.isnot(None)).count(),
            "won": r((C.WON,)),
            "monthly_value": round(sum(o.monthly_value or 0 for o in opps.filter(
                AcquisitionOpportunity.stage == C.WON).all()), 2),
        }
    out["master_pool"] = {
        "businesses": db.query(LeadIntelProspect).count(),
        "researched": db.query(LeadIntelProspect).filter(LeadIntelProspect.researched_at.isnot(None)).count(),
        "verified_emails": db.query(LeadIntelProspect).filter(LeadIntelProspect.email_verified.is_(True)).count(),
    }
    return out


def recent_jobs(db: Session, limit: int = 40) -> List[Dict[str, Any]]:
    rows = db.query(AcquisitionWorkerJob).order_by(AcquisitionWorkerJob.created_at.desc()).limit(limit).all()
    return [{"id": j.id, "worker": j.worker, "status": j.status, "organization_id": j.organization_id,
             "target_type": j.target_type, "is_practice": j.is_practice, "attempts": j.attempts,
             "result": C.jload(j.result, None), "error": j.error, "created_at": C.iso(j.created_at),
             "finished_at": C.iso(j.finished_at)} for j in rows]
