"""NEEDS YOUR ATTENTION - the queue, and the reason it is short.

THIS IS THE DELIVERABLE, not a widget on a dashboard. Section 2: do not make
managers hunt through charts. A person opening AI Workforce Command should see
a list of things to do, ordered, each with WHAT, WHY, WHICH EMPLOYEE, WHICH
CUSTOMER, HOW OLD, HOW BAD, WHAT TO DO, and a way through to the record that
proves it.

THE HARD PART IS NOT FINDING PROBLEMS. `stalled.py` finds plenty. The hard
part is that one broken thing produces dozens of symptoms, and a queue that
lists all of them is a queue nobody reads twice. So:

    ROOTS SWALLOW SYMPTOMS. When a provider is failing for an employee, that
    employee's stalled objectives, repeated failures and retry loops are not
    separate items - they are counted INTO the provider item, which says "and
    this is why 43 objectives have stopped". One line, the true cause, and the
    scale of it.

    A SUSPENDED EMPLOYEE SWALLOWS ITS OWN QUEUE. Of course its work is
    stalled; it is switched off. Telling a manager both is telling them the
    same fact twice and burying the actionable half.

    IDENTICAL CONDITIONS COLLAPSE ON A DEDUP KEY. The key is derived from the
    ROOT - employee plus channel, employee plus reason - never from the
    individual record, so forty records with one cause produce one row with a
    count of forty.

WHAT IS STORED AND WHAT IS NOT. The CONTENT of every item is recomputed from
authoritative records on every pass; only the LIFECYCLE - first seen,
acknowledged, resolved - persists. An item whose condition has cleared is
closed by the pass, with `cleared` rather than `resolved`, so "somebody fixed
it" and "it stopped being true" stay different facts. A queue that stored its
own copy of "this objective is blocked" would keep saying so after the
objective unblocked, and would be confidently wrong.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization
from app.models.workforce_intelligence_models import AIAttentionItem
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence import cost as t9_cost
from app.services.workforce_intelligence import stalled
from app.services.workforce_intelligence.scope import Scope
from app.services.workforce_intelligence.stalled import Signal

_log = logging.getLogger(__name__)


def _priority(severity: str, oldest_at: Optional[datetime],
              now: datetime) -> int:
    """Lower sorts first. Severity decides the band; age decides within it.

    AGE MUST NOT PROMOTE ACROSS SEVERITY. A three-day-old low-severity item is
    still less urgent than a critical one raised a minute ago, and a scoring
    scheme where age eventually overtakes severity is how the most important
    item ends up second. The age term is bounded to 999, which is smaller than
    the 1000-point gap between bands, so it can only ever reorder within one.
    """
    band = C.SEVERITY_RANK.get(severity, 9) * 1000
    if oldest_at is None:
        return band + 999
    age_hours = max(0, int((now - oldest_at).total_seconds() // 3600))
    return band + max(0, 999 - min(999, age_hours))


def _cost_signals(db: Session, scope: Scope, th: Dict,
                  now: datetime) -> List[Signal]:
    """Runaway conditions, rendered in the queue's own shape.

    Cost belongs on the same list as everything else. A manager does not have
    a separate afternoon for usage problems, and an employee approaching its
    action ceiling is the same kind of fact as an employee whose channel is
    down - something is happening that a person should decide about.
    """
    out: List[Signal] = []
    for cond in t9_cost.runaway(db, scope, thresholds=th, now=now):
        org_id = cond.get("organization_id") or ""
        if not org_id:
            continue
        out.append(Signal(
            kind=C.A_COST_RUNAWAY, organization_id=str(org_id),
            employee_id=cond.get("employee_id"),
            dedup_key="cost:%s:%s" % (cond["code"],
                                      cond.get("employee_id") or "org"),
            title=C.ATTENTION_LABELS[C.A_COST_RUNAWAY],
            why=cond.get("reason") or "",
            severity=cond.get("severity") or C.SEV_NORMAL,
            source_kind="ai_ops_counters",
            recommended_action=cond.get("recommended_action"),
            evidence={"code": cond["code"], "current": cond.get("current"),
                      "limit": cond.get("limit"), "unit": cond.get("unit"),
                      "fraction_of_limit": cond.get("fraction_of_limit"),
                      "detail": cond.get("evidence") or {}},
            drilldown={"view": "costs",
                       "employee_id": cond.get("employee_id")}))
    return out


def deduplicate(signals: List[Signal]) -> List[Signal]:
    """Roll symptoms into their root, per employee. The anti-noise pass.

    Grouped by (organization, employee) because that is the granularity a root
    cause actually has: a provider failing for one employee says nothing about
    another employee on a different channel, and suppressing across employees
    would hide a second, real problem behind the first.
    """
    by_owner: Dict[Any, List[Signal]] = {}
    for sig in signals:
        by_owner.setdefault((sig.organization_id, sig.employee_id or ""),
                            []).append(sig)

    kept: List[Signal] = []
    for _owner, group in by_owner.items():
        present = {s.kind for s in group}
        suppressed_kinds = set()
        for root, symptoms in C.DEDUP_ROLLUP.items():
            if root in present:
                suppressed_kinds |= set(symptoms)
        suppressed_kinds -= set(C.DEDUP_ROLLUP)   # a root never suppresses a root

        rolled = [s for s in group if s.kind in suppressed_kinds]
        survivors = [s for s in group if s.kind not in suppressed_kinds]

        if rolled:
            extra = sum(max(1, s.count) for s in rolled)
            reasons = sorted({C.ATTENTION_LABELS.get(s.kind, s.kind)
                              for s in rolled})
            for sig in survivors:
                if sig.kind not in C.DEDUP_ROLLUP:
                    continue
                sig.count += extra
                sig.evidence = dict(sig.evidence or {})
                sig.evidence["rolled_up"] = {
                    "records": extra,
                    "conditions": reasons,
                    "why": ("These are symptoms of the condition above rather "
                            "than separate problems, so they are counted here "
                            "instead of listed separately."),
                }
                sig.why = ("%s This has also stopped %d other thing%s for this "
                           "employee." % (sig.why, extra,
                                          "" if extra == 1 else "s"))
                break
        kept.extend(survivors)
    return kept


def refresh(db: Session, scope: Scope, *, thresholds: Optional[Dict] = None,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Recompute the queue for one scope and reconcile it with what is stored.

    THREE THINGS HAPPEN, IN THIS ORDER, AND THE ORDER MATTERS:

        1. Detect. Every detector runs; a failing detector is logged and the
           pass continues, because one broken query must not empty a queue.
        2. Upsert. A condition that is still true refreshes its row, keeping
           `first_seen_at` - so "this has been waiting since Tuesday" survives
           every pass, which is the whole reason ages are trustworthy.
        3. Clear. A stored OPEN row whose condition is no longer detected is
           closed as `cleared`, not `resolved`. The distinction is the audit:
           somebody pressing a button and a problem going away on its own are
           different events and a manager can tell them apart.

    AN ACKNOWLEDGED ITEM STAYS ACKNOWLEDGED while its condition persists. A
    pass that reset acknowledgement every time would make the button useless.
    """
    now = now or datetime.utcnow()
    th = thresholds or C.thresholds()

    signals = stalled.detect(db, scope, thresholds=th, now=now)
    try:
        signals.extend(_cost_signals(db, scope, th, now))
    except Exception:                                        # noqa: BLE001
        _log.exception("t9 attention: cost signals failed for %s/%s",
                       scope.scope_type, scope.scope_id)
    signals = deduplicate(signals)

    platform_of: Dict[str, Optional[str]] = {}
    org_ids = {s.organization_id for s in signals}
    if org_ids:
        for org_id, platform_id in (db.query(Organization.id,
                                             Organization.platform_id)
                                    .filter(Organization.id.in_(list(org_ids)))
                                    .all()):
            platform_of[str(org_id)] = platform_id

    seen_keys: Dict[str, set] = {}
    written = 0
    for sig in signals:
        # THE SCOPE IS RE-ASSERTED ON THE WAY IN. A detector that produced a
        # row for an organization outside this scope would be a bug, and a
        # write is where a bug like that becomes a cross-tenant record rather
        # than a wrong number. Refused, logged, not written.
        if not scope.covers(sig.organization_id) and not scope._all_organizations:
            _log.warning("t9 attention: signal outside scope discarded "
                         "(org=%s scope=%s/%s)", sig.organization_id,
                         scope.scope_type, scope.scope_id)
            continue
        seen_keys.setdefault(sig.organization_id, set()).add(sig.dedup_key)
        _upsert(db, sig, platform_id=platform_of.get(sig.organization_id),
                now=now)
        written += 1

    cleared = _clear_absent(db, scope, seen_keys, now=now)
    db.flush()
    return {"detected": len(signals), "written": written, "cleared": cleared,
            "generated_at": now.isoformat()}


def _upsert(db: Session, sig: Signal, *, platform_id: Optional[str],
            now: datetime) -> AIAttentionItem:
    row = (db.query(AIAttentionItem)
           .filter(AIAttentionItem.organization_id == sig.organization_id,
                   AIAttentionItem.dedup_key == sig.dedup_key)
           .first())
    priority = _priority(sig.severity, sig.oldest_at, now)
    if row is None:
        row = AIAttentionItem(
            organization_id=sig.organization_id, platform_id=platform_id,
            dedup_key=sig.dedup_key, first_seen_at=(sig.oldest_at or now))
        db.add(row)
    elif row.state in (C.ATTENTION_STATE_RESOLVED,
                       C.ATTENTION_STATE_CLEARED):
        # IT CAME BACK. A resolved item whose condition is true again is
        # REOPENED rather than duplicated, and its original first_seen is
        # kept - "this is the third time this week" is the useful fact, and a
        # second row with a fresh timestamp destroys it.
        row.state = C.ATTENTION_STATE_OPEN
        row.resolved_at = None
        row.resolved_by = None
        row.cleared_at = None

    row.platform_id = platform_id
    row.employee_id = sig.employee_id
    row.deployment_id = sig.deployment_id
    row.kind = sig.kind
    row.severity = sig.severity
    row.priority = priority
    row.title = (sig.title or C.ATTENTION_LABELS.get(sig.kind, sig.kind))[:255]
    row.why = sig.why
    row.recommended_action = (sig.recommended_action or "")[:255] or None
    row.subject_type = sig.subject_type
    row.subject_id = sig.subject_id
    row.objective_ref = sig.objective_ref
    row.source_kind = sig.source_kind
    row.source_id = sig.source_id
    row.evidence = json.dumps(sig.evidence or {}, default=str)[:8000]
    row.drilldown = json.dumps(sig.drilldown or {}, default=str)[:2000]
    row.rolled_up_count = max(1, int(sig.count or 1))
    row.last_seen_at = now
    if sig.oldest_at and (row.first_seen_at is None
                          or sig.oldest_at < row.first_seen_at):
        row.first_seen_at = sig.oldest_at
    return row


def _clear_absent(db: Session, scope: Scope, seen: Dict[str, set], *,
                  now: datetime) -> int:
    """Close stored items whose condition is no longer detected.

    SCOPED LIKE EVERY OTHER QUERY. A clearing pass is a write that reads
    first, and a clearing pass with a missing tenant filter would close
    another customer's queue - the loudest possible version of the aggregate
    leak section 17 warns about.
    """
    q = scope.apply(
        db.query(AIAttentionItem),
        AIAttentionItem.organization_id).filter(
            AIAttentionItem.state.in_(list(C.ATTENTION_LIVE_STATES)))
    cleared = 0
    for row in q.limit(5000).all():
        if row.dedup_key in seen.get(str(row.organization_id), set()):
            continue
        row.state = C.ATTENTION_STATE_CLEARED
        row.cleared_at = now
        cleared += 1
    return cleared


# ---------------------------------------------------------------------------
# READING THE QUEUE
# ---------------------------------------------------------------------------


def _age_seconds(row: AIAttentionItem, now: datetime) -> int:
    first = row.first_seen_at or row.created_at or now
    return max(0, int((now - first).total_seconds()))


def _render(row: AIAttentionItem, now: datetime, *,
            employee_names: Dict[str, str],
            org_names: Dict[str, str]) -> Dict[str, Any]:
    """One item, with the eight things section 2 requires on every one."""
    try:
        evidence = json.loads(row.evidence or "{}")
    except (ValueError, TypeError):
        evidence = {}
    try:
        drilldown = json.loads(row.drilldown or "{}")
    except (ValueError, TypeError):
        drilldown = {}
    return {
        "id": row.id,
        "what": row.title,
        "why": row.why,
        "kind": row.kind,
        "kind_label": C.ATTENTION_LABELS.get(row.kind, row.kind),
        "employee_id": row.employee_id,
        "employee_name": employee_names.get(row.employee_id or ""),
        "organization_id": row.organization_id,
        "organization_name": org_names.get(str(row.organization_id)),
        "deployment_id": row.deployment_id,
        "objective": row.objective_ref,
        "subject_type": row.subject_type,
        "subject_id": row.subject_id,
        "age_seconds": _age_seconds(row, now),
        "first_seen_at": (row.first_seen_at.isoformat()
                          if row.first_seen_at else None),
        "last_seen_at": (row.last_seen_at.isoformat()
                         if row.last_seen_at else None),
        "severity": row.severity,
        "severity_label": C.SEVERITY_LABELS.get(row.severity, row.severity),
        "priority": row.priority,
        "recommended_action": row.recommended_action,
        "affects": row.rolled_up_count,
        # THE AUTHORITATIVE SOURCE, named. Section 2 requires it on every item,
        # and it is what makes an item arguable rather than merely alarming.
        "source": {"table": row.source_kind, "id": row.source_id},
        "evidence": evidence,
        "evidence_class": C.EV_FACT,
        "drilldown": drilldown,
        "state": row.state,
        "acknowledged_at": (row.acknowledged_at.isoformat()
                            if row.acknowledged_at else None),
        "acknowledged_by": row.acknowledged_by,
        "resolution_note": row.resolution_note,
    }


def queue(db: Session, scope: Scope, *,
          states: Optional[List[str]] = None,
          kinds: Optional[List[str]] = None,
          severities: Optional[List[str]] = None,
          employee_id: Optional[str] = None,
          limit: int = 200,
          now: Optional[datetime] = None) -> Dict[str, Any]:
    """The list, ordered worst-and-oldest first, with its own summary."""
    now = now or datetime.utcnow()
    q = scope.apply(db.query(AIAttentionItem),
                    AIAttentionItem.organization_id)
    q = q.filter(AIAttentionItem.state.in_(
        list(states or C.ATTENTION_LIVE_STATES)))
    if kinds:
        q = q.filter(AIAttentionItem.kind.in_(kinds))
    if severities:
        q = q.filter(AIAttentionItem.severity.in_(severities))
    if employee_id:
        q = q.filter(AIAttentionItem.employee_id == employee_id)
    rows = (q.order_by(AIAttentionItem.priority.asc(),
                       AIAttentionItem.first_seen_at.asc())
            .limit(limit).all())

    names = _employee_names(db, scope, [r.employee_id for r in rows])
    orgs = _org_names(db, [r.organization_id for r in rows])
    items = [_render(r, now, employee_names=names, org_names=orgs)
             for r in rows]

    by_severity: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}
    for item in items:
        by_severity[item["severity"]] = by_severity.get(item["severity"], 0) + 1
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1

    return {
        "generated_at": now.isoformat(),
        "items": items,
        "total": len(items),
        "by_severity": by_severity,
        "by_kind": by_kind,
        "worst_severity": (items[0]["severity"] if items else None),
        "headline": _headline(items),
        "kind_labels": dict(C.ATTENTION_LABELS),
        "severity_labels": dict(C.SEVERITY_LABELS),
    }


def _headline(items: List[Dict[str, Any]]) -> str:
    """The sentence at the top, because "what needs me" is the first question.

    Written as a count and a worst case rather than as a status word: "Nothing
    needs you right now" is a claim a manager can check against the list below
    it, and "All good" is not.
    """
    if not items:
        return "Nothing needs you right now."
    critical = sum(1 for i in items if i["severity"] == C.SEV_CRITICAL)
    if critical:
        return ("%d thing%s need%s you, and %d of them cannot wait."
                % (len(items), "" if len(items) == 1 else "s",
                   "s" if len(items) == 1 else "", critical))
    return ("%d thing%s need%s you." % (len(items),
                                        "" if len(items) == 1 else "s",
                                        "s" if len(items) == 1 else ""))


def _employee_names(db: Session, scope: Scope, ids) -> Dict[str, str]:
    wanted = sorted({i for i in ids if i})
    if not wanted:
        return {}
    from app.models.workforce_models import AIEmployee
    q = scope.apply(db.query(AIEmployee.id, AIEmployee.name),
                    AIEmployee.organization_id)
    return {row[0]: row[1] for row in q.filter(AIEmployee.id.in_(wanted)).all()}


def _org_names(db: Session, ids) -> Dict[str, str]:
    wanted = sorted({str(i) for i in ids if i})
    if not wanted:
        return {}
    return {str(row[0]): row[1]
            for row in db.query(Organization.id, Organization.name)
            .filter(Organization.id.in_(wanted)).all()}


def get(db: Session, scope: Scope, item_id: str) -> Optional[AIAttentionItem]:
    """Load INSIDE the scope. The filter is in the query, not after it."""
    return (scope.apply(db.query(AIAttentionItem),
                        AIAttentionItem.organization_id)
            .filter(AIAttentionItem.id == item_id).first())


def acknowledge(db: Session, scope: Scope, item_id: str, *, user,
                note: Optional[str] = None) -> Optional[AIAttentionItem]:
    row = get(db, scope, item_id)
    if row is None:
        return None
    row.state = C.ATTENTION_STATE_ACK
    row.acknowledged_at = datetime.utcnow()
    row.acknowledged_by = getattr(user, "id", None)
    if note:
        row.resolution_note = note[:255]
    db.flush()
    return row


def resolve(db: Session, scope: Scope, item_id: str, *, user,
            note: Optional[str] = None) -> Optional[AIAttentionItem]:
    """A person says this is dealt with.

    RESOLVING DOES NOT FIX ANYTHING, and the next pass will say so. If the
    underlying condition is still true it reopens - which is the correct
    behaviour and occasionally an annoying one. An item that could be dismissed
    permanently while still true would be a way to hide a problem from the
    next person on shift.
    """
    row = get(db, scope, item_id)
    if row is None:
        return None
    row.state = C.ATTENTION_STATE_RESOLVED
    row.resolved_at = datetime.utcnow()
    row.resolved_by = getattr(user, "id", None)
    if note:
        row.resolution_note = note[:255]
    db.flush()
    return row
