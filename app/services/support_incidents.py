"""CORRELATION — five tickets are one problem, and only the platform can see it.

THE FACT THIS EXISTS TO PRODUCE
-------------------------------
Three BookaBoost customers and two EvoSys Pro customers hit the same calendar
failure in the same hour. Every one of them sees a broken calendar. Every
brand's support queue sees an isolated ticket. Nobody sees the outage — except
AdvisorFlow, which is the only thing standing above all of them.

So correlation lives here, at the platform layer, and its output is a
PLATFORM INCIDENT with a named likely cause and a recommended fix, rather than
five investigations of the same thing.

THE SIGNATURE IS THE UNIT
-------------------------
`signature_for()` turns a problem into a stable, LOW-CARDINALITY fingerprint —
"calendar.provider_refusing_connection", not "org 8f3a's calendar broke at
14:02". High cardinality would make every occurrence unique and the count
would always be one, which is the failure mode that makes most "error
grouping" useless. Signatures come from the diagnostic checks' own `signals`,
so the thing that detects a problem is the thing that names it.

CROSS-TENANT BY CONSTRUCTION, GOD-ONLY BY CONSEQUENCE
-----------------------------------------------------
Everything in `SupportIncident` is an aggregate over customers. None of it
reaches a customer. `customer_statement()` is the only function that returns
anything to a tenant, and it returns either a sentence a human wrote or one
generated from the SERVICE NAME ALONE — never a count, never another brand,
never another organization, and never the word "customers".

WHAT MAKES THE RECOMMENDATION "STRONG"
---------------------------------------
"19 errors occurred" is a metric. What God needs is: likely root cause,
impact, confidence, evidence, recommended remediation, suggested validation,
and risk. `recommend()` produces that shape from the evidence, and where the
evidence does not support a field it leaves it NULL rather than filling it
with something plausible — a confident wrong root cause costs more than a
blank one.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization
from app.models.support_models import (
    Cause, IncidentStatus, SupportFixRun, SupportIncident, SupportIncidentLink,
    SupportIssueSignature, SupportTicket, TicketStatus,
)

log = logging.getLogger(__name__)

# How far back correlation looks for "the same thing, at the same time". Six
# hours is long enough to gather a provider incident that ramps over a morning
# and short enough that two unrelated occurrences a week apart do not become
# one incident.
CORRELATION_WINDOW_HOURS = 6

# How many DISTINCT organizations make a shared problem a platform incident.
# Two, not five: the point of correlation is to notice early, and waiting for
# five means four customers were told it was just them.
MULTI_ORG_THRESHOLD = 2

# Occurrences in a week past which a signature stops being an incident and
# starts being a design problem worth an engineering ticket.
RECURRENCE_ENGINEERING_THRESHOLD = 20


def signature_for(service: Optional[str], signals: Optional[List[str]],
                  cause: Optional[str] = None) -> Optional[str]:
    """A stable fingerprint for a KIND of problem, or None if we cannot name one.

    RETURNS NONE RATHER THAN GUESSING. A signature invented from free text —
    a ticket subject, a customer's wording — would be unique per customer and
    would make every count one, which is worse than no grouping at all
    because it looks like grouping.

    The first signal wins when several are present: `run_checks` already sorts
    its results worst-first, so the leading signal is the most consequential
    thing observed rather than the alphabetically first.
    """
    for signal in (signals or []):
        if isinstance(signal, str) and signal.strip():
            return signal.strip().lower()[:120]
    if service and cause and cause != Cause.UNKNOWN:
        return ("%s.%s" % (service, cause)).lower()[:120]
    return None


def title_for(signature: str) -> str:
    """A readable headline from a signature, without inventing detail."""
    pretty = signature.replace("_", " ").replace(".", " — ")
    return pretty[:1].upper() + pretty[1:]


def observe(db: Session, *, signature: Optional[str], service: Optional[str],
            cause: Optional[str] = None, org: Optional[Organization] = None,
            platform_id: Optional[str] = None, title: Optional[str] = None,
            now: Optional[datetime] = None) -> Optional[SupportIssueSignature]:
    """Record that this KIND of problem was seen once more.

    Idempotent per call, not per occurrence: every call increments. Callers
    call once per genuine observation — a ticket being raised, a diagnostic
    finding a fault — never once per render.

    BEST EFFORT, ALWAYS. Intelligence about a problem must never be able to
    become a second problem, so every failure here is logged and swallowed.
    The caller's ticket still gets created.
    """
    if not signature:
        return None
    now = now or datetime.utcnow()
    try:
        row = (db.query(SupportIssueSignature)
               .filter(SupportIssueSignature.signature == signature).first())
        if row is None:
            row = SupportIssueSignature(
                signature=signature, title=title or title_for(signature),
                service=service, suspected_cause=cause,
                first_seen_at=now, last_seen_at=now,
                occurrence_count=0, organizations_affected=0,
                platforms_affected=0)
            db.add(row)
            db.flush()

        row.occurrence_count = int(row.occurrence_count or 0) + 1
        row.last_seen_at = now
        if service and not row.service:
            row.service = service
        if cause and cause != Cause.UNKNOWN:
            row.suspected_cause = cause

        # Counted from the evidence rather than incremented, so a customer who
        # hits the same fault six times counts once and the number keeps
        # meaning "how many customers".
        row.organizations_affected = _distinct_orgs(db, signature)
        row.platforms_affected = _distinct_platforms(db, signature)
        if (row.occurrence_count >= RECURRENCE_ENGINEERING_THRESHOLD
                and row.auto_fixed_count and not row.engineering_candidate):
            # A fix that keeps working on a problem that keeps coming back is
            # treating a symptom. That is exactly when it stops being a win.
            row.symptom_only_remediation = True
            row.engineering_candidate = True
        db.flush()
        return row
    except Exception:                                          # noqa: BLE001
        log.exception("support_incidents: could not record observation for %s",
                      signature)
        return None


def _distinct_orgs(db: Session, signature: str) -> int:
    tickets = {t[0] for t in db.query(SupportTicket.organization_id)
               .filter(SupportTicket.issue_signature == signature).all() if t[0]}
    fixes = {f[0] for f in db.query(SupportFixRun.organization_id)
             .filter(SupportFixRun.issue_signature == signature).all() if f[0]}
    return len(tickets | fixes)


def _distinct_platforms(db: Session, signature: str) -> int:
    tickets = {t[0] for t in db.query(SupportTicket.platform_id)
               .filter(SupportTicket.issue_signature == signature).all() if t[0]}
    fixes = {f[0] for f in db.query(SupportFixRun.platform_id)
             .filter(SupportFixRun.issue_signature == signature).all() if f[0]}
    return len(tickets | fixes)


def record_fix_outcome(db: Session, run: SupportFixRun) -> None:
    """Fold one remediation's result into its signature's counters.

    Counts VERIFIED fixes as fixes. A run whose status is FIXED but whose
    verification never happened is counted as a failure here, deliberately:
    the number an operator reads as "we handle this automatically" must mean
    "and we checked".
    """
    from app.models.support_models import FixStatus
    if not run.issue_signature:
        return
    row = (db.query(SupportIssueSignature)
           .filter(SupportIssueSignature.signature == run.issue_signature).first())
    if row is None:
        row = SupportIssueSignature(
            signature=run.issue_signature, title=title_for(run.issue_signature),
            occurrence_count=0, organizations_affected=0, platforms_affected=0)
        db.add(row)
        db.flush()

    if run.verified:
        row.auto_fixed_count = int(row.auto_fixed_count or 0) + 1
        row.known_remediation_key = run.action_key
    elif run.status in (FixStatus.FAILED, FixStatus.ROLLED_BACK):
        row.auto_fix_failed_count = int(row.auto_fix_failed_count or 0) + 1
    row.last_seen_at = datetime.utcnow()
    db.flush()


# ══════════════════════════════════════════════════════════════════════════
# CORRELATION
# ══════════════════════════════════════════════════════════════════════════

def _incident_number(db: Session, now: datetime) -> str:
    """INC-YYYYMMDD-NN, counted within the day.

    Readable, sortable and safe to say on a call. The suffix is the count of
    incidents opened today plus one; a collision would need two incidents
    opened in the same transaction, and the unique constraint catches that
    rather than letting two rows share a number.
    """
    day = now.strftime("%Y%m%d")
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    n = db.query(SupportIncident).filter(SupportIncident.created_at >= start).count()
    return "INC-%s-%02d" % (day, n + 1)


def correlate(db: Session, *, now: Optional[datetime] = None,
              window_hours: int = CORRELATION_WINDOW_HOURS) -> Dict[str, Any]:
    """Look across every brand for the same problem at the same time.

    Runs over TICKETS and FIX RUNS rather than over raw logs, because both
    already carry a signature that a check produced — correlating over log
    text would mean re-deriving the grouping the detectors already did, in a
    second place, differently.

    Returns what it did rather than writing quietly, so the God brief and a
    manual run report the same thing.
    """
    now = now or datetime.utcnow()
    since = now - timedelta(hours=window_hours)

    buckets: Dict[str, Dict[str, Any]] = {}

    def _bucket(sig: str) -> Dict[str, Any]:
        return buckets.setdefault(sig, {
            "orgs": set(), "platforms": set(), "tickets": [], "fix_runs": [],
            "services": set(), "causes": [], "first": None, "last": None,
        })

    for ticket in (db.query(SupportTicket)
                   .filter(SupportTicket.issue_signature.isnot(None),
                           SupportTicket.created_at >= since).all()):
        b = _bucket(ticket.issue_signature)
        if ticket.organization_id:
            b["orgs"].add(ticket.organization_id)
        if ticket.platform_id:
            b["platforms"].add(ticket.platform_id)
        b["tickets"].append(ticket)
        if ticket.ai_suspected_cause:
            b["causes"].append(ticket.ai_suspected_cause)
        b["first"] = min(b["first"] or ticket.created_at, ticket.created_at)
        b["last"] = max(b["last"] or ticket.created_at, ticket.created_at)

    for run in (db.query(SupportFixRun)
                .filter(SupportFixRun.issue_signature.isnot(None),
                        SupportFixRun.created_at >= since).all()):
        b = _bucket(run.issue_signature)
        if run.organization_id:
            b["orgs"].add(run.organization_id)
        if run.platform_id:
            b["platforms"].add(run.platform_id)
        b["fix_runs"].append(run)
        b["first"] = min(b["first"] or run.created_at, run.created_at)
        b["last"] = max(b["last"] or run.created_at, run.created_at)

    opened, updated = [], []
    for signature, bucket in buckets.items():
        if len(bucket["orgs"]) < MULTI_ORG_THRESHOLD:
            continue
        incident, is_new = _upsert_incident(db, signature, bucket, now)
        (opened if is_new else updated).append(incident.incident_number)

    db.flush()
    return {
        "window_hours": window_hours,
        "signatures_examined": len(buckets),
        "incidents_opened": opened,
        "incidents_updated": updated,
    }


def _upsert_incident(db: Session, signature: str, bucket: Dict[str, Any],
                     now: datetime):
    """One incident per signature per open episode.

    An OPEN incident for this signature is updated rather than duplicated; a
    resolved one is left alone and a new incident opens, because a problem
    that comes back is a new episode and folding it into the closed one would
    lose the fact that it recurred.
    """
    incident = (db.query(SupportIncident)
                .filter(SupportIncident.signature == signature,
                        SupportIncident.status.in_(list(IncidentStatus.OPEN)))
                .order_by(SupportIncident.created_at.desc()).first())
    is_new = incident is None

    sig_row = (db.query(SupportIssueSignature)
               .filter(SupportIssueSignature.signature == signature).first())
    service = (sig_row.service if sig_row is not None else None) or _service_from(signature)

    if is_new:
        incident = SupportIncident(
            incident_number=_incident_number(db, now),
            title=(sig_row.title if sig_row is not None else title_for(signature)),
            signature=signature, service=service,
            status=IncidentStatus.SUSPECTED,
            first_observed_at=bucket["first"] or now)
        db.add(incident)
        db.flush()

    incident.last_observed_at = bucket["last"] or now
    incident.occurrence_count = len(bucket["tickets"]) + len(bucket["fix_runs"])
    incident.organizations_affected = len(bucket["orgs"])
    incident.platforms_affected = len(bucket["platforms"])
    incident.service = incident.service or service

    recommendation = recommend(db, signature=signature, bucket=bucket,
                               sig_row=sig_row, service=service)
    incident.classification = recommendation["classification"]
    incident.confidence = recommendation["confidence"]
    incident.likely_shared_dependency = recommendation["likely_shared_dependency"]
    incident.likely_root_cause = recommendation["likely_root_cause"]
    incident.impact_summary = recommendation["impact"]
    incident.recommended_remediation = recommendation["recommended_remediation"]
    incident.suggested_validation = recommendation["suggested_validation"]
    incident.risk_assessment = recommendation["risk"]
    incident.evidence_json = json.dumps(recommendation["evidence"], default=str)
    incident.provider_health_note = recommendation["provider_note"]

    _link(db, incident, "organization", bucket["orgs"])
    _link(db, incident, "ticket", {t.id for t in bucket["tickets"]},
          org_of={t.id: t.organization_id for t in bucket["tickets"]})
    _link(db, incident, "fix_run", {f.id for f in bucket["fix_runs"]},
          org_of={f.id: f.organization_id for f in bucket["fix_runs"]})

    # Tie the tickets to the incident so a support engineer opening any one of
    # them sees "this is part of INC-…" instead of investigating it alone.
    for ticket in bucket["tickets"]:
        if ticket.incident_id != incident.id:
            ticket.incident_id = incident.id

    db.flush()
    return incident, is_new


def _service_from(signature: str) -> Optional[str]:
    return signature.split(".", 1)[0] if "." in signature else None


def _link(db: Session, incident: SupportIncident, link_type: str, ids,
          org_of: Optional[Dict[str, str]] = None) -> None:
    existing = {row.link_id for row in
                db.query(SupportIncidentLink)
                .filter(SupportIncidentLink.incident_id == incident.id,
                        SupportIncidentLink.link_type == link_type).all()}
    for link_id in ids:
        if not link_id or link_id in existing:
            continue
        db.add(SupportIncidentLink(
            incident_id=incident.id, link_type=link_type, link_id=link_id,
            organization_id=(org_of or {}).get(link_id,
                                               link_id if link_type == "organization" else None)))


def recommend(db: Session, *, signature: str, bucket: Dict[str, Any],
              sig_row: Optional[SupportIssueSignature],
              service: Optional[str]) -> Dict[str, Any]:
    """The strong recommendation. Every field earns its place or stays NULL.

    THIS IS THE DIFFERENCE BETWEEN A DASHBOARD AND AN OPERATIONS TOOL. A
    number tells an operator that something happened. This tells them what is
    probably wrong, who it is hurting, how sure we are, what to do, how to
    check the fix, and what the fix might break.

    Where the evidence does not support a claim the field is None and the
    screen says "not established". Filling it with a plausible sentence is how
    a recommendation engine becomes something people learn to ignore.
    """
    org_count = len(bucket["orgs"])
    platform_count = len(bucket["platforms"])
    tickets = bucket["tickets"]
    fix_runs = bucket["fix_runs"]

    causes = [c for c in bucket["causes"] if c]
    classification = None
    if causes:
        # Same priority order `support_diagnostics._dominant_cause` uses:
        # consequence, not frequency.
        for candidate in (Cause.PLATFORM_DEFECT, Cause.THIRD_PARTY_PROVIDER,
                          Cause.USAGE_CAPACITY, Cause.CUSTOMER_CONFIGURATION):
            if candidate in causes:
                classification = candidate
                break
    if classification is None and platform_count > 1:
        # THE SAME FAULT IN TWO BRANDS IS NOT A CUSTOMER'S CONFIGURATION.
        # Two independent companies do not misconfigure the same thing in the
        # same hour; a shared dependency does.
        classification = Cause.PLATFORM_DEFECT
    if classification is None:
        classification = Cause.UNKNOWN

    if platform_count > 1 and org_count >= 4:
        confidence = "high"
    elif org_count >= MULTI_ORG_THRESHOLD:
        confidence = "medium"
    else:
        confidence = "low"

    verified_fixes = sum(1 for f in fix_runs if f.verified)
    failed_fixes = sum(1 for f in fix_runs if not f.verified)

    impact = ("%d organization(s) across %d brand(s) are affected in this window."
              % (org_count, platform_count))

    root_cause = None
    remediation = None
    validation = None
    risk = None
    provider_note = None

    if classification == Cause.THIRD_PARTY_PROVIDER:
        root_cause = ("A shared third-party provider behind %s is failing for "
                      "multiple unrelated organizations." % (service or "this service"))
        provider_note = ("Failures cluster across independent customers, which "
                         "points at the provider rather than at any one "
                         "customer's configuration.")
        remediation = ("Confirm the provider's own status, stop futile retries "
                       "so customers see an actionable state instead of "
                       "silence, and re-run the affected work once the "
                       "provider recovers.")
        validation = ("Re-run the affected diagnostic for two affected "
                      "organizations and confirm the failure signature stops "
                      "appearing.")
        risk = "Low — the recommended actions change no customer data."
    elif classification == Cause.PLATFORM_DEFECT:
        root_cause = ("The same failure signature appears for organizations "
                      "with no shared configuration, which points at platform "
                      "code on the %s path." % (service or "affected"))
        remediation = ("Trace the shared code path behind this signature, fix "
                       "the defect, and add a regression test that reproduces "
                       "the signature.")
        validation = ("A test that reproduces the signature and fails before "
                      "the change; a tenant-isolation test alongside it if the "
                      "path reads organization scope.")
        risk = ("Medium — a change on a shared path affects every brand, so it "
                "needs the full regression suite before it ships.")
    elif classification == Cause.CUSTOMER_CONFIGURATION:
        root_cause = ("Several organizations have the same configuration gap. "
                      "That is usually a product problem wearing a "
                      "configuration costume: the setup step is missable.")
        remediation = ("Fix the affected organizations, then make the step "
                       "hard to miss — a required field, a setup check, or a "
                       "default — so the next customer cannot land here.")
        validation = ("Confirm the diagnostic clears for the affected "
                      "organizations and that a fresh workspace cannot reach "
                      "the same state.")
        risk = "Low to medium, depending on whether a default changes."

    if sig_row is not None and sig_row.symptom_only_remediation:
        remediation = ((remediation or "") +
                       " NOTE: the registered automatic repair resolves the "
                       "symptom and the condition keeps returning, so this is "
                       "a candidate for a permanent engineering fix rather "
                       "than a standing repair.").strip()

    return {
        "classification": classification,
        "confidence": confidence,
        "likely_shared_dependency": service,
        "likely_root_cause": root_cause,
        "impact": impact,
        "recommended_remediation": remediation,
        "suggested_validation": validation,
        "risk": risk,
        "provider_note": provider_note,
        "evidence": {
            "signature": signature,
            "organizations_affected": org_count,
            "brands_affected": platform_count,
            "tickets": [t.ticket_number for t in tickets][:25],
            "fix_runs_verified": verified_fixes,
            "fix_runs_failed": failed_fixes,
            "occurrences_all_time": (sig_row.occurrence_count
                                     if sig_row is not None else None),
        },
    }


# ══════════════════════════════════════════════════════════════════════════
# WHAT A CUSTOMER MAY BE TOLD
# ══════════════════════════════════════════════════════════════════════════

_GENERIC_BY_SERVICE = {
    "calendar": "We're aware of a problem affecting calendar connections and "
                "are working on it.",
    "sms": "We're aware of a problem affecting text message delivery and are "
           "working on it.",
    "email": "We're aware of a problem affecting email delivery and are "
             "working on it.",
    "ai": "We're aware of a problem affecting AI features and are working on it.",
    "billing": "We're aware of a problem affecting billing and are working on it.",
    "platform": "We're aware of a problem affecting part of the platform and "
                "are working on it.",
}


def customer_statement(db: Session, *, org: Optional[Organization],
                       services: Optional[List[str]] = None) -> Optional[str]:
    """The ONLY thing this module ever says to a customer.

    Returns either a sentence a human wrote on the incident, or one generated
    FROM THE SERVICE NAME ALONE. It never returns a count, another
    organization, another brand, a signature, or the fact that other customers
    exist. A SUSPECTED incident says nothing at all — telling a customer we
    might have a problem, before anyone has confirmed it, is worse than
    silence and it is not reversible.
    """
    if db is None:
        return None
    wanted = {s for s in (services or []) if s}
    try:
        incidents = (db.query(SupportIncident)
                     .filter(SupportIncident.status.in_(
                         [IncidentStatus.ACKNOWLEDGED, IncidentStatus.INVESTIGATING,
                          IncidentStatus.MITIGATED]))
                     .order_by(SupportIncident.last_observed_at.desc())
                     .limit(20).all())
    except Exception:                                          # noqa: BLE001
        log.exception("support_incidents: customer statement lookup failed")
        return None

    for incident in incidents:
        if wanted and incident.service and incident.service not in wanted:
            continue
        if incident.customer_facing_statement:
            return incident.customer_facing_statement
        generic = _GENERIC_BY_SERVICE.get(incident.service or "")
        if generic:
            return generic
    return None


def matching_open_incident(db: Session, signature: Optional[str]
                           ) -> Optional[SupportIncident]:
    """Is this problem already a known incident? Used to avoid a duplicate
    ticket becoming a duplicate investigation."""
    if not signature:
        return None
    return (db.query(SupportIncident)
            .filter(SupportIncident.signature == signature,
                    SupportIncident.status.in_(list(IncidentStatus.OPEN)))
            .order_by(SupportIncident.created_at.desc()).first())


# ══════════════════════════════════════════════════════════════════════════
# READ SURFACES — God only
# ══════════════════════════════════════════════════════════════════════════

def incident_view(db: Session, incident: SupportIncident) -> Dict[str, Any]:
    links = (db.query(SupportIncidentLink)
             .filter(SupportIncidentLink.incident_id == incident.id).all())
    try:
        evidence = json.loads(incident.evidence_json) if incident.evidence_json else {}
    except (TypeError, ValueError):
        evidence = {"unparseable": True}

    return {
        "id": incident.id,
        "incident_number": incident.incident_number,
        "title": incident.title,
        "signature": incident.signature,
        "service": incident.service,
        "status": incident.status,
        "classification": incident.classification,
        "classification_label": Cause.LABELS.get(incident.classification or "",
                                                 incident.classification),
        "confidence": incident.confidence,
        "first_observed_at": incident.first_observed_at,
        "last_observed_at": incident.last_observed_at,
        "occurrence_count": incident.occurrence_count,
        "organizations_affected": incident.organizations_affected,
        "platforms_affected": incident.platforms_affected,
        "likely_shared_dependency": incident.likely_shared_dependency,
        "likely_root_cause": incident.likely_root_cause,
        "impact_summary": incident.impact_summary,
        "recommended_remediation": incident.recommended_remediation,
        "suggested_validation": incident.suggested_validation,
        "risk_assessment": incident.risk_assessment,
        "provider_health_note": incident.provider_health_note,
        "customer_facing_statement": incident.customer_facing_statement,
        "evidence": evidence,
        "acknowledged_by": incident.acknowledged_by,
        "acknowledged_at": incident.acknowledged_at,
        "resolution_note": incident.resolution_note,
        "linked": {
            "tickets": [l.link_id for l in links if l.link_type == "ticket"],
            "fix_runs": [l.link_id for l in links if l.link_type == "fix_run"],
            "organizations": [l.link_id for l in links
                              if l.link_type == "organization"],
        },
    }


def recurring_report(db: Session, *, days: int = 7, limit: int = 25,
                     now: Optional[datetime] = None) -> Dict[str, Any]:
    """What keeps happening, and what that means.

    The narrative lines are the product here. "This occurred 43 times this
    week across 12 organizations, the automatic repair works every time, and
    it keeps coming back" is a decision; the four numbers on their own are
    homework.
    """
    now = now or datetime.utcnow()
    since = now - timedelta(days=days)
    rows = (db.query(SupportIssueSignature)
            .filter(SupportIssueSignature.last_seen_at >= since)
            .order_by(SupportIssueSignature.occurrence_count.desc())
            .limit(limit).all())

    out = []
    for row in rows:
        notes = []
        if row.occurrence_count:
            notes.append("Seen %d time(s) in total." % row.occurrence_count)
        if row.organizations_affected > 1:
            notes.append("Affects %d organizations." % row.organizations_affected)
        if row.platforms_affected > 1:
            notes.append("Seen on %d brands." % row.platforms_affected)
        if row.auto_fixed_count:
            notes.append("Repaired automatically %d time(s)." % row.auto_fixed_count)
        if row.auto_fix_failed_count:
            notes.append("The automatic repair failed %d time(s)."
                         % row.auto_fix_failed_count)
        if row.symptom_only_remediation:
            notes.append("The repair resolves the symptom, not the cause.")
        if row.engineering_candidate:
            notes.append("Candidate for a permanent engineering fix.")

        out.append({
            "signature": row.signature,
            "title": row.title,
            "service": row.service,
            "suspected_cause": row.suspected_cause,
            "first_seen_at": row.first_seen_at,
            "last_seen_at": row.last_seen_at,
            "occurrence_count": row.occurrence_count,
            "organizations_affected": row.organizations_affected,
            "platforms_affected": row.platforms_affected,
            "auto_fixed_count": row.auto_fixed_count,
            "auto_fix_failed_count": row.auto_fix_failed_count,
            "known_remediation_key": row.known_remediation_key,
            "engineering_candidate": bool(row.engineering_candidate),
            "narrative": " ".join(notes) or "No pattern established yet.",
        })

    return {"days": days, "signatures": out,
            "engineering_candidates": sum(1 for s in out
                                          if s["engineering_candidate"])}


def acknowledge(db: Session, incident: SupportIncident, *, user,
                statement: Optional[str] = None) -> SupportIncident:
    """A person takes ownership, and may write the sentence customers see.

    The statement is optional and stays NULL unless somebody writes one:
    generating customer-facing prose about an incident nobody has understood
    yet is how a status page starts lying.
    """
    incident.status = IncidentStatus.ACKNOWLEDGED
    incident.acknowledged_by = getattr(user, "id", None)
    incident.acknowledged_at = datetime.utcnow()
    if statement is not None:
        incident.customer_facing_statement = statement.strip() or None
    db.flush()
    return incident


def set_status(db: Session, incident: SupportIncident, status: str, *,
               note: Optional[str] = None) -> SupportIncident:
    if status not in IncidentStatus.ALL:
        raise ValueError("Unknown incident status %r" % status)
    incident.status = status
    if note is not None:
        incident.resolution_note = note
    if status in (IncidentStatus.RESOLVED, IncidentStatus.DISMISSED):
        incident.resolved_at = datetime.utcnow()
    db.flush()
    return incident


def open_incident_count(db: Session) -> int:
    return (db.query(SupportIncident)
            .filter(SupportIncident.status.in_(list(IncidentStatus.OPEN))).count())


def tickets_for_incident(db: Session, incident_id: str) -> List[SupportTicket]:
    return (db.query(SupportTicket)
            .filter(SupportTicket.incident_id == incident_id)
            .order_by(SupportTicket.created_at.desc()).all())


def open_ticket_signature_counts(db: Session) -> Dict[str, int]:
    """Which signatures currently have open tickets, for the God queue board."""
    counts: Dict[str, int] = {}
    for sig, in (db.query(SupportTicket.issue_signature)
                 .filter(SupportTicket.issue_signature.isnot(None),
                         SupportTicket.status.in_(list(TicketStatus.OPEN)))
                 .all()):
        if sig:
            counts[sig] = counts.get(sig, 0) + 1
    return counts
