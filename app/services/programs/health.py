"""Operations health for a location outreach program - "is it working?"

One read-only snapshot a manager can open without asking a developer:
email system, sender domain, mailbox connection, aliases, SMS, campaigns,
holds, failures, bounces, complaints, unmatched replies, HOT handling, Kerry's
response time, automation errors, and the last successful inbound sync /
email send / webhook event - plus a feed of recent automated decisions.

Every block is {"status": "ok" | "warn" | "fail" | "off", "detail": ..., ...}.
Nothing here sends, changes or retries anything.
"""
import json
import os
import statistics
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import EmailMessage, Lead, Organization
from app.models.program_models import (
    CampaignFamily, EmailProviderEvent, OutreachProgram, ProgramAlert, ProgramEmailTouch,
    ProgramMailFiling, ProgramResponse, ProgramSourceRecord, ProgramUnmatchedReply,
)
from app.services.programs import aliases as _aliases

_DNS_CACHE: Dict[str, tuple] = {}
DNS_TTL = 6 * 3600


def _txt(name: str) -> Optional[List[str]]:
    """TXT records via DNS-over-HTTPS (no extra dependency). None = lookup failed."""
    r = httpx.get("https://dns.google/resolve", params={"name": name, "type": "TXT"}, timeout=6)
    if r.status_code != 200:
        return None
    return [a.get("data", "").replace('" "', "").strip('"') for a in r.json().get("Answer", []) or []]


def sender_domain(domain: Optional[str], now: Optional[float] = None) -> Dict:
    """SPF (return-path subdomain), DKIM (Resend selector) and DMARC for the
    sending domain. Cached for six hours; a failed lookup is 'unknown', never
    reported as healthy."""
    if not domain:
        return {"status": "fail", "detail": "no sending domain resolved"}
    now = now or time.time()
    hit = _DNS_CACHE.get(domain)
    if hit and now - hit[0] < DNS_TTL:
        return hit[1]
    try:
        spf = _txt("send.%s" % domain)
        dkim = _txt("resend._domainkey.%s" % domain)
        dmarc = _txt("_dmarc.%s" % domain)
    except Exception as exc:  # noqa: BLE001
        return {"status": "warn", "detail": "DNS lookup failed: %s" % type(exc).__name__}
    if spf is None or dkim is None or dmarc is None:
        res = {"status": "warn", "detail": "DNS lookup unavailable - status unknown"}
    else:
        checks = {
            "spf": any(t.startswith("v=spf1") for t in spf),
            "dkim": any("p=" in t for t in dkim),
            "dmarc": any(t.upper().startswith("V=DMARC1") for t in dmarc),
        }
        policy = next((t for t in dmarc if t.upper().startswith("V=DMARC1")), "")
        res = {"status": "ok" if all(checks.values()) else "fail", "checks": checks,
               "dmarc_policy": policy[:120],
               "detail": ", ".join("%s %s" % (k.upper(), "ok" if v else "MISSING") for k, v in checks.items())}
    _DNS_CACHE[domain] = (now, res)
    return res


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def snapshot(db: Session, prog: OutreachProgram, now: Optional[datetime] = None,
             check_dns: bool = True) -> Dict:
    now = now or datetime.utcnow()
    org_id = prog.organization_id
    org = db.query(Organization).filter(Organization.id == org_id).first()
    lead_ids = db.query(Lead.id).filter(Lead.organization_id == org_id)
    day, week, month = now - timedelta(days=1), now - timedelta(days=7), now - timedelta(days=30)
    out: Dict = {"generated_at": _iso(now)}

    # ── email system ──
    frm = _aliases.sending_address(db, org_id)
    resend_ok = bool((os.environ.get("RESEND_API_KEY") or "").strip()) or bool(getattr(org, "resend_api_key", None))
    last_sent = (db.query(func.max(EmailMessage.sent_at))
                 .filter(EmailMessage.lead_id.in_(lead_ids), EmailMessage.status.in_(("sent", "delivered"))).scalar())
    failed_7d = (db.query(func.count(EmailMessage.id))
                 .filter(EmailMessage.lead_id.in_(lead_ids), EmailMessage.status == "failed",
                         EmailMessage.sent_at >= week).scalar() or 0)
    out["email_system"] = {
        "status": "ok" if (frm and resend_ok) else "fail",
        "detail": "%s via Resend%s" % (frm or "no sender", "" if resend_ok else " - RESEND_API_KEY missing"),
        "verified_from": frm, "last_successful_send": _iso(last_sent), "failed_sends_7d": failed_7d,
    }
    domain = _aliases.domain_of(frm)
    out["sender_domain"] = sender_domain(domain) if check_dns else {"status": "warn", "detail": "not checked"}
    out["sender_domain"]["domain"] = domain

    # ── webhook ──
    secret = bool((os.environ.get("RESEND_WEBHOOK_SECRET") or "").strip())
    last_evt = db.query(func.max(EmailProviderEvent.created_at)).scalar()
    evts_24h = (db.query(func.count(EmailProviderEvent.id))
                .filter(EmailProviderEvent.created_at >= day).scalar() or 0)
    out["webhook"] = {
        "status": "off" if not secret else ("ok" if last_evt else "warn"),
        "detail": ("RESEND_WEBHOOK_SECRET not set - delivered/bounce/complaint events are not received"
                   if not secret else ("last event %s" % _iso(last_evt) if last_evt
                                       else "configured, no event received yet")),
        "last_successful_event": _iso(last_evt), "events_24h": evts_24h,
    }

    # ── mailbox ──
    from app.models.inbound_mailbox_models import InboundMailbox, InboundMailboxMessage
    box = (db.query(InboundMailbox).filter(func.lower(InboundMailbox.address) == (frm or "").lower()).first()
           if frm else None)
    if box is None:
        out["mailbox"] = {"status": "fail", "detail": "the reply mailbox %s is not connected" % (frm or "-")}
    else:
        last_ok = box.last_polled_at if box.last_status == "ok" else (
            db.query(func.max(InboundMailboxMessage.created_at))
            .filter(InboundMailboxMessage.mailbox_id == box.id,
                    InboundMailboxMessage.outcome != "error").scalar())
        stale = box.last_polled_at is None or now - box.last_polled_at > timedelta(minutes=20)
        skipped = (db.query(func.count(ProgramMailFiling.id))
                   .filter(ProgramMailFiling.mailbox_id == box.id, ProgramMailFiling.status == "skipped").scalar() or 0)
        if box.last_status == "auth_error":
            st, detail = "fail", "RECONNECT NEEDED - Microsoft refused the stored sign-in"
        elif box.last_status == "error":
            st, detail = "warn", "last read had errors: %s" % (box.last_error or "")[:160]
        elif stale:
            st, detail = "warn", "not read in the last 20 minutes (the poller runs every 5)"
        else:
            st, detail = "ok", "read %s" % _iso(box.last_polled_at)
        if skipped:
            detail += "; %d message(s) not filed - reconnect with Mail.ReadWrite" % skipped
            st = "warn" if st == "ok" else st
        out["mailbox"] = {"status": st, "detail": detail, "address": box.address,
                          "last_successful_sync": _iso(last_ok), "last_poll": _iso(box.last_polled_at),
                          "last_status": box.last_status}

    # ── aliases ──
    ast = _aliases.status(db, prog)
    from app.models.program_models import LocationProfile
    quiet = (db.query(func.count(LocationProfile.id))
             .filter(LocationProfile.organization_id == org_id, LocationProfile.is_review_bucket.is_(False),
                     LocationProfile.email_alias.isnot(None), LocationProfile.alias_verified_at.is_(None)).scalar() or 0)
    in_use = ast["effective"].get("from", 0) + ast["effective"].get("reply_to", 0)
    out["aliases"] = {
        "status": "ok" if ast["assigned"] and in_use == ast["locations"] else ("warn" if ast["assigned"] else "fail"),
        "detail": "%d of %d in use (%d as From); %d not yet seen receiving%s" % (
            in_use, ast["locations"], ast["effective"].get("from", 0), quiet,
            "; all confirmed by a person" if ast["confirmed_all"] else ""),
        **ast,
    }

    # ── SMS ──
    number = getattr(org, "org_twilio_phone_number", None)
    out["sms"] = {"status": "fail" if not number else "warn",
                  "detail": ("BLOCKED - no SCI sending number (TFV / dedicated number pending)" if not number
                             else "number %s configured - confirm toll-free verification before sending" % number)}

    # ── campaigns ──
    fams = db.query(CampaignFamily).filter(CampaignFamily.organization_id == org_id).all()
    from app.services.programs import email_touches as _et
    from app.services import outbound_brake
    out["campaigns"] = {
        "status": "off" if not any(f.is_active for f in fams) else "ok",
        "active": [f.name for f in fams if f.is_active], "paused": [f.name for f in fams if not f.is_active],
        "email_runner": "on" if _et.sending_enabled() else "off",
        "daily_cap": _et.daily_cap(), "per_pass": _et.batch_limit(),
        "sent_today": _et.used_today(db, org_id, now),
        "emergency_stop": outbound_brake.engaged(),
        "detail": "%d active, %d off; email runner %s; emergency stop %s" % (
            sum(1 for f in fams if f.is_active), sum(1 for f in fams if not f.is_active),
            "ON" if _et.sending_enabled() else "OFF", "ENGAGED" if outbound_brake.engaged() else "off"),
    }

    # ── people ──
    held = (db.query(func.count(ProgramSourceRecord.id))
            .filter(ProgramSourceRecord.organization_id == org_id, ProgramSourceRecord.on_hold.is_(True)).scalar() or 0)
    out["held_contacts"] = {"status": "ok", "count": held, "detail": "%d records on hold - excluded" % held}
    bounces = (db.query(func.count(EmailMessage.id))
               .filter(EmailMessage.lead_id.in_(lead_ids), EmailMessage.status == "bounced").scalar() or 0)
    complaints = (db.query(func.count(EmailMessage.id))
                  .filter(EmailMessage.lead_id.in_(lead_ids), EmailMessage.status == "complained").scalar() or 0)
    out["bounces"] = {"status": "ok" if not bounces else "warn", "count": bounces}
    out["complaints"] = {"status": "ok" if not complaints else "fail", "count": complaints}
    touches = dict(db.query(ProgramEmailTouch.status, func.count(ProgramEmailTouch.id))
                   .filter(ProgramEmailTouch.organization_id == org_id)
                   .group_by(ProgramEmailTouch.status).all())
    bad = touches.get("failed", 0) + touches.get("unknown", 0)
    out["failed_sends"] = {"status": "ok" if not (bad or failed_7d) else "warn",
                           "campaign_touches": touches, "provider_failed_7d": failed_7d,
                           "detail": "%d campaign email(s) failed or unknown, %d retrying" % (bad, touches.get("retry", 0))}
    unmatched = (db.query(func.count(ProgramUnmatchedReply.id))
                 .filter(ProgramUnmatchedReply.organization_id == org_id,
                         ProgramUnmatchedReply.status == "open").scalar() or 0)
    out["unmatched_replies"] = {"status": "ok" if not unmatched else "warn", "count": unmatched}

    hot = (db.query(ProgramResponse).filter(ProgramResponse.organization_id == org_id,
                                            ProgramResponse.response_class == "hot",
                                            ProgramResponse.handling_status != "closed").all())
    unhandled = [r for r in hot if r.handling_status == "new" and r.sla_due_at and r.sla_due_at < now]
    out["hot_responses"] = {"status": "ok" if not hot else "warn", "open": len(hot)}
    out["unhandled_hot"] = {"status": "ok" if not unhandled else "fail", "count": len(unhandled),
                            "sla_minutes": prog.hot_sla_minutes}
    recent = (db.query(ProgramResponse).filter(ProgramResponse.organization_id == org_id,
                                               ProgramResponse.received_at >= month).all())
    mins = []
    for r in recent:
        first = min([t for t in (r.opened_at, r.responded_at) if t] or [None]) if (r.opened_at or r.responded_at) else None
        if first and r.received_at:
            mins.append((first - r.received_at).total_seconds() / 60.0)
    hot_mins = []
    for r in recent:
        if r.response_class == "hot" and r.received_at and (r.opened_at or r.responded_at):
            first = min(t for t in (r.opened_at, r.responded_at) if t)
            hot_mins.append((first - r.received_at).total_seconds() / 60.0)
    out["response_time"] = {
        "status": "ok" if not hot_mins or statistics.median(hot_mins) <= (prog.hot_sla_minutes or 15) else "warn",
        "median_minutes_all": round(statistics.median(mins), 1) if mins else None,
        "median_minutes_hot": round(statistics.median(hot_mins), 1) if hot_mins else None,
        "measured": len(mins), "window_days": 30,
        "detail": "first handled after a median of %s min (HOT %s min) over %d replies, last 30 days" % (
            round(statistics.median(mins), 1) if mins else "-", round(statistics.median(hot_mins), 1)
            if hot_mins else "-", len(mins)),
    }

    # ── automation errors ──
    from app.models.job_models import JobRun
    job_err = (db.query(func.count(JobRun.id))
               .filter(JobRun.job_name == "program_sla_loop", JobRun.started_at >= day,
                       JobRun.status.in_(("error", "failed"))).scalar() or 0)
    last_job = (db.query(func.max(JobRun.finished_at))
                .filter(JobRun.job_name == "program_sla_loop", JobRun.status.in_(("ok", "success", "succeeded"))).scalar())
    inbound_err = (db.query(func.count(InboundMailboxMessage.id))
                   .filter(InboundMailboxMessage.outcome == "error", InboundMailboxMessage.created_at >= day).scalar() or 0)
    filing_failed = (db.query(func.count(ProgramMailFiling.id))
                     .filter(ProgramMailFiling.organization_id == org_id,
                             ProgramMailFiling.status == "failed").scalar() or 0)
    errs = job_err + inbound_err + filing_failed + bad
    out["automation_errors"] = {
        "status": "ok" if not errs else "warn", "count": errs,
        "detail": "SLA loop errors 24h: %d; inbound read errors 24h: %d; Outlook filings failed: %d; "
                  "campaign emails failed/unknown: %d" % (job_err, inbound_err, filing_failed, bad),
        "last_loop_success": _iso(last_job),
    }
    filed = (db.query(func.count(ProgramMailFiling.id))
             .filter(ProgramMailFiling.organization_id == org_id, ProgramMailFiling.status == "filed").scalar() or 0)
    out["outlook_filing"] = {"status": "ok" if not filing_failed else "warn", "filed": filed,
                             "failed": filing_failed, "folder_root": prog.mailbox_folder_path,
                             "detail": ("filed %d, failed %d under %s" % (filed, filing_failed, prog.mailbox_folder_path))
                             if prog.mailbox_folder_path else "off - no folder path configured"}
    out["last_successful"] = {"inbound_sync": out.get("mailbox", {}).get("last_successful_sync"),
                              "email_send": _iso(last_sent), "webhook_event": _iso(last_evt)}
    out["decisions"] = decisions(db, org_id)
    blocks = [v for v in out.values() if isinstance(v, dict) and "status" in v]
    out["overall"] = "fail" if any(b["status"] == "fail" for b in blocks) else (
        "warn" if any(b["status"] == "warn" for b in blocks) else "ok")
    return out


def decisions(db: Session, org_id: str, limit: int = 40) -> List[Dict]:
    """The latest automated decisions, newest first - alerts, campaign touches,
    Outlook filings, unmatched replies."""
    rows = []
    for a in (db.query(ProgramAlert).filter(ProgramAlert.organization_id == org_id)
              .order_by(ProgramAlert.created_at.desc()).limit(limit)):
        rows.append({"at": a.created_at, "kind": "alert",
                     "text": "%s alert to %s by %s: %s" % (a.kind, a.audience, a.channel,
                                                           "delivered" if a.delivered else (a.reason or "not sent"))})
    for t in (db.query(ProgramEmailTouch).filter(ProgramEmailTouch.organization_id == org_id)
              .order_by(ProgramEmailTouch.created_at.desc()).limit(limit)):
        rows.append({"at": t.attempted_at or t.created_at, "kind": "campaign_email",
                     "text": "touch %d (%s) %s%s" % (t.touch_number, t.campaign_family, t.status,
                                                    (": " + t.reason[:120]) if t.reason else "")})
    for f in (db.query(ProgramMailFiling).filter(ProgramMailFiling.organization_id == org_id)
              .order_by(ProgramMailFiling.updated_at.desc()).limit(limit)):
        rows.append({"at": f.filed_at or f.updated_at or f.created_at, "kind": "outlook_filing",
                     "text": "%s -> %s%s" % (f.status, f.folder_path, (": " + f.last_error[:120]) if f.last_error else "")})
    for u in (db.query(ProgramUnmatchedReply).filter(ProgramUnmatchedReply.organization_id == org_id)
              .order_by(ProgramUnmatchedReply.created_at.desc()).limit(limit)):
        rows.append({"at": u.created_at, "kind": "unmatched_reply",
                     "text": "reply to %s from %s kept for review (%s)" % (u.alias, u.from_address, u.status)})
    rows.sort(key=lambda r: r["at"] or datetime.min, reverse=True)
    return [{"at": _iso(r["at"]), "kind": r["kind"], "text": r["text"]} for r in rows[:limit]]
