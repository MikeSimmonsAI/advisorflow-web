"""
Launch Engine delivery — the implementation team's half of a launch, and the
gate between it and Live.

===========================================================================
THE SHAPE OF THIS MODULE
===========================================================================

    seed()          create the programme's rows for one implementation,
                    from the brand's template, idempotently
    the setters     one per row type, each audited, each authority-checked
                    by the caller (routers use implementation_service)
    progress()      counts, per area, never merged into one number
    readiness()     the go-live gate: twelve computed answers
    customer_view() the same facts, filtered to what a customer may see

===========================================================================
TWO PROGRESS NUMBERS, NOT THREE, AND NEVER ONE
===========================================================================

The platform already publishes two: INTAKE (what the customer answered) and
IMPLEMENTATION (build milestones settled). Integrations, testing and training
are part of the second — they are the team's work — so they are reported as
their own counts and folded into the implementation picture, NOT as a third
headline percentage competing with the other two.

`readiness()` is not a percentage at all. It is a list of yes/no answers with
the reason attached, because "82% ready to launch" is not a thing anybody can
act on and "training is not complete" is.

===========================================================================
WHAT THE CUSTOMER SEES, AND WHY IT IS A SEPARATE FUNCTION
===========================================================================

`customer_view()` is not `staff_view()` with fields deleted at render time. It
is built from scratch out of the handful of facts a customer is entitled to:
labels and statuses, the blockers somebody deliberately marked visible, and
the things they personally have to do. Internal notes, blocker detail, owner
identities and unfiltered blocker lists are not omitted from a larger object —
they are never fetched into one. A filter you can forget is a filter that will
eventually be forgotten.

Nothing in this module names a brand or a customer.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.implementation_models import Implementation
from app.models.launch_delivery_models import (
    APPROVAL_CUSTOMER, APPROVAL_KINDS, APPROVAL_LABELS, APPROVAL_PROVIDER,
    BLOCKER_OPEN, BLOCKER_PARTIES, BLOCKER_PARTY_LABELS, BLOCKER_RESOLVED,
    CHECK_NOT_TESTED, CHECK_PASS, CHECK_STATUSES, CHECK_STATUS_LABELS,
    INTEGRATION_SETTLED, INTEGRATION_STATUSES, INTEGRATION_STATUS_LABELS,
    INT_REQUIRED, INT_VERIFIED,
    ImplementationApproval, ImplementationBlocker, ImplementationCheck,
    ImplementationIntegration, ImplementationTraining,
)
from app.routers.audit_log_router import log_action
from app.services import launch_intake, launch_template

log = logging.getLogger("launch_delivery")


# ── audit ───────────────────────────────────────────────────────────────────

def _audit(db: Session, impl: Implementation, actor_id: Optional[str], action: str,
           *, target_type: str, target_id: str,
           before: Any = None, after: Any = None,
           details: Any = None, note: Optional[str] = None) -> None:
    """Every delivery event on the SAME audit log as everything else.

    `commit=False` because each caller commits its own transaction, so an
    action and its audit row land together or not at all. A failure to audit
    must never turn a successful write into a failed one, hence the guard —
    but it must also never silently succeed unlogged, hence the warning.
    """
    try:
        log_action(
            db, impl.organization_id, actor_id or "system",
            action=action, target_type=target_type, target_id=target_id,
            platform_id=impl.platform_id,
            brand_sales_org_id=impl.brand_sales_org_id,
            before=before, after=after, details=details, note=note,
            commit=False,
        )
    except Exception:                                        # pragma: no cover
        log.warning("delivery audit failed for %s/%s", impl.id, action,
                    exc_info=True)


# ── seeding ─────────────────────────────────────────────────────────────────

def seed(db: Session, impl: Implementation,
         actor_id: Optional[str] = None) -> Dict[str, int]:
    """Create whatever the brand's programme calls for and this launch lacks.

    IDEMPOTENT BY CONSTRUCTION. Every row type carries a unique constraint on
    (implementation_id, key), and this only inserts keys that are absent. So
    seeding twice adds nothing, seeding after a template change adds only the
    new rows, and — this is the part that matters — a row somebody has already
    worked on is never touched, reset or relabelled. An implementation team
    that has verified an integration must not find it back at `required`
    because a template was edited.

    Nothing is ever deleted here. A row the template no longer mentions stays,
    because it may be a live piece of work; removing it belongs to a human.
    """
    tpl = launch_template.resolve(db, impl.platform_id)
    org_id = impl.organization_id
    added = {"integrations": 0, "checks": 0, "training": 0, "milestones": 0}

    have = {r.key for r in db.query(ImplementationIntegration)
            .filter(ImplementationIntegration.implementation_id == impl.id).all()}
    for i, row in enumerate(tpl.get("integrations") or []):
        if row["key"] in have:
            continue
        db.add(ImplementationIntegration(
            implementation_id=impl.id, organization_id=org_id,
            key=row["key"], label=row["label"],
            provider=(row.get("provider") or None),
            notes=(row.get("description") or None),
            is_required=bool(row.get("required", True)),
            status=INT_REQUIRED, position=i))
        added["integrations"] += 1

    have = {r.key for r in db.query(ImplementationCheck)
            .filter(ImplementationCheck.implementation_id == impl.id).all()}
    for i, row in enumerate(tpl.get("checks") or []):
        if row["key"] in have:
            continue
        db.add(ImplementationCheck(
            implementation_id=impl.id, organization_id=org_id,
            key=row["key"], label=row["label"],
            category=(row.get("category") or None),
            description=(row.get("description") or None),
            is_required=bool(row.get("required", True)),
            status=CHECK_NOT_TESTED, position=i))
        added["checks"] += 1

    have = {r.key for r in db.query(ImplementationTraining)
            .filter(ImplementationTraining.implementation_id == impl.id).all()}
    for i, row in enumerate(tpl.get("training") or []):
        if row["key"] in have:
            continue
        db.add(ImplementationTraining(
            implementation_id=impl.id, organization_id=org_id,
            key=row["key"], title=row["title"],
            description=(row.get("description") or None),
            is_required=bool(row.get("required", True)), position=i))
        added["training"] += 1

    # Brand-added build steps. ADDITIVE to provisioning's package template,
    # never a replacement — see launch_template's module docstring.
    extra = tpl.get("extra_milestones") or []
    if extra:
        from app.models.implementation_models import (
            ImplementationMilestone, MILESTONE_PENDING,
        )
        have = {r.key for r in db.query(ImplementationMilestone)
                .filter(ImplementationMilestone.implementation_id == impl.id).all()}
        last = (db.query(ImplementationMilestone)
                .filter(ImplementationMilestone.implementation_id == impl.id)
                .order_by(ImplementationMilestone.position.desc()).first())
        pos = (last.position + 1) if last else 0
        for row in extra:
            if row["key"] in have:
                continue
            db.add(ImplementationMilestone(
                implementation_id=impl.id, key=row["key"], label=row["label"],
                description=(row.get("description") or None),
                is_required=bool(row.get("required", True)),
                status=MILESTONE_PENDING, position=pos))
            pos += 1
            added["milestones"] += 1

    if any(added.values()):
        _audit(db, impl, actor_id, "launch_programme_seeded",
               target_type="implementation", target_id=impl.id, details=added)
    db.commit()
    return added


# ── reads ───────────────────────────────────────────────────────────────────

def _integrations(db: Session, impl: Implementation) -> List[ImplementationIntegration]:
    return (db.query(ImplementationIntegration)
            .filter(ImplementationIntegration.implementation_id == impl.id,
                    ImplementationIntegration.organization_id == impl.organization_id)
            .order_by(ImplementationIntegration.position,
                      ImplementationIntegration.created_at).all())


def _checks(db: Session, impl: Implementation) -> List[ImplementationCheck]:
    return (db.query(ImplementationCheck)
            .filter(ImplementationCheck.implementation_id == impl.id,
                    ImplementationCheck.organization_id == impl.organization_id)
            .order_by(ImplementationCheck.position,
                      ImplementationCheck.created_at).all())


def _training(db: Session, impl: Implementation) -> List[ImplementationTraining]:
    return (db.query(ImplementationTraining)
            .filter(ImplementationTraining.implementation_id == impl.id,
                    ImplementationTraining.organization_id == impl.organization_id)
            .order_by(ImplementationTraining.position,
                      ImplementationTraining.created_at).all())


def _blockers(db: Session, impl: Implementation,
              only_open: bool = False) -> List[ImplementationBlocker]:
    q = (db.query(ImplementationBlocker)
         .filter(ImplementationBlocker.implementation_id == impl.id,
                 ImplementationBlocker.organization_id == impl.organization_id))
    if only_open:
        q = q.filter(ImplementationBlocker.status == BLOCKER_OPEN)
    return q.order_by(ImplementationBlocker.opened_at.desc()).all()


def _approvals(db: Session, impl: Implementation) -> Dict[str, ImplementationApproval]:
    rows = (db.query(ImplementationApproval)
            .filter(ImplementationApproval.implementation_id == impl.id,
                    ImplementationApproval.organization_id == impl.organization_id)
            .all())
    return {r.kind: r for r in rows}


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def integration_public(r: ImplementationIntegration) -> Dict[str, Any]:
    return {
        "id": r.id, "key": r.key, "label": r.label, "provider": r.provider,
        "status": r.status,
        "status_label": INTEGRATION_STATUS_LABELS.get(r.status, r.status),
        "is_required": bool(r.is_required),
        "owner_party": r.owner_party,
        "owner_party_label": BLOCKER_PARTY_LABELS.get(r.owner_party, r.owner_party),
        "owner_user_id": r.owner_user_id,
        "notes": r.notes,
        "verified_at": _iso(r.verified_at), "verified_by": r.verified_by,
        "settled": r.status in INTEGRATION_SETTLED,
    }


def check_public(r: ImplementationCheck) -> Dict[str, Any]:
    return {
        "id": r.id, "key": r.key, "label": r.label, "category": r.category,
        "description": r.description, "status": r.status,
        "status_label": CHECK_STATUS_LABELS.get(r.status, r.status),
        "is_required": bool(r.is_required),
        "tested_at": _iso(r.tested_at), "tested_by": r.tested_by,
        "notes": r.notes,
        "customer_approved_at": _iso(r.customer_approved_at),
        "customer_approved_by": r.customer_approved_by,
    }


def training_public(r: ImplementationTraining) -> Dict[str, Any]:
    return {
        "id": r.id, "key": r.key, "title": r.title,
        "description": r.description, "is_required": bool(r.is_required),
        "owner_user_id": r.owner_user_id,
        "scheduled_at": _iso(r.scheduled_at),
        "completed_at": _iso(r.completed_at), "completed_by": r.completed_by,
        "attendees": r.attendees or [],
        "customer_acknowledged_at": _iso(r.customer_acknowledged_at),
        "notes": r.notes, "external_ref": r.external_ref,
    }


def blocker_public(r: ImplementationBlocker) -> Dict[str, Any]:
    return {
        "id": r.id, "title": r.title, "detail": r.detail,
        "party": r.party,
        "party_label": BLOCKER_PARTY_LABELS.get(r.party, r.party),
        "owner_user_id": r.owner_user_id, "status": r.status,
        "customer_visible": bool(r.customer_visible),
        "customer_action": r.customer_action,
        "opened_at": _iso(r.opened_at), "opened_by": r.opened_by,
        "resolved_at": _iso(r.resolved_at), "resolved_by": r.resolved_by,
        "resolution": r.resolution,
    }


def approval_public(r: ImplementationApproval) -> Dict[str, Any]:
    return {
        "kind": r.kind, "label": APPROVAL_LABELS.get(r.kind, r.kind),
        "given_at": _iso(r.given_at), "given_by": r.given_by,
        "given_name": r.given_name, "note": r.note,
    }


def progress(db: Session, impl: Implementation) -> Dict[str, Any]:
    """Counts per area. Deliberately not one number — see the module docstring."""
    ints = _integrations(db, impl)
    chks = _checks(db, impl)
    trns = _training(db, impl)
    open_blockers = _blockers(db, impl, only_open=True)

    def _count(rows, done_test):
        req = [r for r in rows if r.is_required]
        return {
            "total": len(rows), "done": sum(1 for r in rows if done_test(r)),
            "required_total": len(req),
            "required_done": sum(1 for r in req if done_test(r)),
        }

    return {
        "integrations": _count(ints, lambda r: r.status in INTEGRATION_SETTLED),
        "checks": _count(chks, lambda r: r.status == CHECK_PASS),
        "training": _count(trns, lambda r: r.completed_at is not None),
        "blockers_open": len(open_blockers),
        "blockers_by_party": {
            p: sum(1 for b in open_blockers if b.party == p) for p in BLOCKER_PARTIES
        },
    }


# ── the go-live gate ────────────────────────────────────────────────────────

def readiness(db: Session, impl: Implementation) -> Dict[str, Any]:
    """Twelve questions the platform can answer about itself, and one verdict.

    EVERY ITEM IS COMPUTED. Not one of them is a box somebody ticked to say a
    thing was done — each reads the rows that would exist if it had been. That
    is the difference between a go-live gate and a go-live checklist, and it
    is why `ready` can be trusted enough to be the thing that stands between a
    customer and Live.

    Which items COUNT is the brand's decision (`golive` in the template). An
    item that is not required is still computed and still reported — turning a
    requirement off hides nothing, it only stops it blocking.
    """
    from app.services import implementation_service as impl_svc

    tpl = launch_template.resolve(db, impl.platform_id)
    gate = tpl["golive"]
    org_id = impl.organization_id

    ov = launch_intake.overview(db, impl.id, org_id)
    sub = launch_intake.latest_submission(db, impl.id, org_id)
    ints = _integrations(db, impl)
    chks = _checks(db, impl)
    trns = _training(db, impl)
    open_blockers = _blockers(db, impl, only_open=True)
    apps = _approvals(db, impl)
    mile = impl_svc.completion(db, impl)

    files_step = next((s for s in ov["steps"] if s["key"] == "files"), None)

    # ACCESS. Two honest routes to the same fact, because §9's "provide it
    # through a secure process instead" is a real answer and a gate that
    # refuses it would be a gate people work around. Either the customer typed
    # every credential the form asks for, or every required connection has been
    # settled — which can only have happened if somebody got in.
    secret_fields: List[Dict[str, str]] = []
    for schema in launch_intake.STEP_SCHEMA:
        for f in schema["fields"]:
            if f["kind"] == launch_intake.KIND_SECRET:
                secret_fields.append({"step": schema["key"], "key": f["key"],
                                      "label": f["label"]})
    secrets_missing = []
    for f in secret_fields:
        got = launch_intake.read_step(db, impl.id, org_id, f["step"])
        if f["key"] not in (got.get("secrets_set") or []):
            secrets_missing.append(f["label"])
    # A ROW THAT DOES NOT EXIST YET IS OUTSTANDING WORK, NOT SATISFIED WORK.
    #
    # This is the bug that would matter most if it were left in. Counting only
    # the rows that exist means an implementation whose programme has never
    # been seeded reports "0 of 0 connections verified" — which is `True` — and
    # the same for testing and training. Three of the twelve gate items would
    # silently pass for exactly the customers nobody has set up.
    #
    # So the denominator is the BRAND'S TEMPLATE unioned with whatever rows
    # exist, and a required key with no row counts against. Seeding then makes
    # the number honest rather than making the gate meaningful.
    def _required(template_rows, rows, done_test):
        wanted = {r["key"] for r in (template_rows or [])
                  if r.get("required", True)}
        have = {r.key: r for r in rows}
        wanted |= {r.key for r in rows if r.is_required}
        done = sum(1 for k in wanted if k in have and done_test(have[k]))
        return done, len(wanted)

    int_done, int_total = _required(
        tpl.get("integrations"), ints, lambda r: r.status in INTEGRATION_SETTLED)
    chk_done, chk_total = _required(
        tpl.get("checks"), chks, lambda r: r.status == CHECK_PASS)
    chk_appr, _ = _required(
        tpl.get("checks"), chks, lambda r: r.customer_approved_at is not None)
    trn_done, trn_total = _required(
        tpl.get("training"), trns, lambda r: r.completed_at is not None)

    access_ok = (not secrets_missing) or (int_total > 0 and int_done == int_total)

    items: List[Dict[str, Any]] = []

    def add(key: str, ok: bool, detail: str) -> None:
        items.append({
            "key": key, "label": launch_template.GOLIVE_LABELS.get(key, key),
            "ok": bool(ok), "required": bool(gate.get(key, False)),
            "detail": detail,
        })

    add("intake_submitted", sub is not None,
        "Submitted %s" % _iso(sub.submitted_at) if sub is not None
        else "The customer has not submitted their intake (%d%% complete)."
             % ov["overall_pct"])
    add("intake_reviewed", bool(sub is not None and sub.reviewed_at),
        "Reviewed" if (sub is not None and sub.reviewed_at)
        else "Nobody has marked the intake reviewed.")
    add("files_received", bool(files_step and files_step["status"] == "complete"),
        "%d file%s received." % (ov["file_count"],
                                 "" if ov["file_count"] == 1 else "s"))
    add("access_received", access_ok,
        "All requested credentials stored." if not secrets_missing
        else ("Provided outside the form — every required connection is settled."
              if access_ok else "Still outstanding: " + ", ".join(secrets_missing)))
    add("milestones_complete", not mile["required_open"],
        "%d of %d build items settled." % (mile["settled"], mile["total"])
        if not mile["required_open"]
        else "Open: " + ", ".join(m["label"] for m in mile["required_open"]))
    add("integrations_verified", int_done == int_total,
        "%d of %d required connections verified." % (int_done, int_total))
    add("uat_complete", chk_done == chk_total,
        "%d of %d required checks passing." % (chk_done, chk_total))
    add("customer_uat_approval", chk_appr == chk_total,
        "%d of %d checks approved by the customer." % (chk_appr, chk_total))
    add("training_complete", trn_done == trn_total,
        "%d of %d required sessions complete." % (trn_done, trn_total))
    add("no_open_blockers", not open_blockers,
        "No open blockers." if not open_blockers
        else "%d open: %s" % (len(open_blockers),
                              ", ".join(b.title for b in open_blockers[:3])))
    add("customer_signoff", APPROVAL_CUSTOMER in apps,
        "Approved by %s" % apps[APPROVAL_CUSTOMER].given_name
        if APPROVAL_CUSTOMER in apps and apps[APPROVAL_CUSTOMER].given_name
        else ("Approved." if APPROVAL_CUSTOMER in apps
              else "The customer has not approved go-live."))
    add("provider_signoff", APPROVAL_PROVIDER in apps,
        "Approved by %s" % apps[APPROVAL_PROVIDER].given_name
        if APPROVAL_PROVIDER in apps and apps[APPROVAL_PROVIDER].given_name
        else ("Approved." if APPROVAL_PROVIDER in apps
              else "The implementation team has not approved go-live."))

    outstanding = [i for i in items if i["required"] and not i["ok"]]
    return {
        "items": items,
        "outstanding": outstanding,
        "ready": not outstanding,
        "already_live": impl.status == "live",
    }


def readiness_warnings(db: Session, impl: Implementation) -> List[str]:
    """The gate's failures as sentences, for the launch confirmation."""
    r = readiness(db, impl)
    return ["%s — %s" % (i["label"], i["detail"]) for i in r["outstanding"]]


# ── writes: integrations ────────────────────────────────────────────────────

def set_integration(db: Session, impl: Implementation, actor_id: Optional[str],
                    integration_id: str, *,
                    status: Optional[str] = None,
                    provider: Optional[str] = None,
                    notes: Optional[str] = None,
                    owner_party: Optional[str] = None,
                    owner_user_id: Optional[str] = None,
                    is_required: Optional[bool] = None) -> ImplementationIntegration:
    row = _one(db, ImplementationIntegration, impl, integration_id, "Integration")
    before = {"status": row.status, "is_required": row.is_required}
    if status is not None:
        if status not in INTEGRATION_STATUSES:
            raise ValueError("Unknown integration status '%s'." % status)
        row.status = status
        # VERIFIED IS A MOMENT, and it belongs to whoever verified it. Moving
        # off verified clears it rather than leaving a timestamp claiming a
        # proof that no longer stands.
        if status == INT_VERIFIED:
            row.verified_at = datetime.utcnow()
            row.verified_by = actor_id
        else:
            row.verified_at = None
            row.verified_by = None
    if provider is not None:
        row.provider = str(provider)[:160] or None
    if notes is not None:
        row.notes = str(notes) or None
    if owner_party is not None:
        if owner_party not in BLOCKER_PARTIES:
            raise ValueError("Unknown party '%s'." % owner_party)
        row.owner_party = owner_party
    if owner_user_id is not None:
        row.owner_user_id = owner_user_id or None
    if is_required is not None:
        row.is_required = bool(is_required)

    impl.last_activity_at = datetime.utcnow()
    _audit(db, impl, actor_id, "implementation_integration_changed",
           target_type="implementation_integration", target_id=row.id,
           before=before, after={"status": row.status, "is_required": row.is_required},
           details={"key": row.key, "label": row.label})
    db.commit()
    db.refresh(row)
    return row


def add_integration(db: Session, impl: Implementation, actor_id: Optional[str], *,
                    key: str, label: str, provider: Optional[str] = None,
                    is_required: bool = True) -> ImplementationIntegration:
    key = (key or "").strip().lower()[:64]
    label = (label or "").strip()[:160]
    if not key or not label:
        raise ValueError("A connection needs a key and a label.")
    if (db.query(ImplementationIntegration)
            .filter(ImplementationIntegration.implementation_id == impl.id,
                    ImplementationIntegration.key == key).first()) is not None:
        raise ValueError("Connection '%s' already exists on this launch." % key)
    last = (db.query(ImplementationIntegration)
            .filter(ImplementationIntegration.implementation_id == impl.id)
            .order_by(ImplementationIntegration.position.desc()).first())
    row = ImplementationIntegration(
        implementation_id=impl.id, organization_id=impl.organization_id,
        key=key, label=label, provider=(provider or None),
        is_required=bool(is_required), status=INT_REQUIRED,
        position=(last.position + 1) if last else 0)
    db.add(row)
    impl.last_activity_at = datetime.utcnow()
    _audit(db, impl, actor_id, "implementation_integration_added",
           target_type="implementation_integration", target_id=key,
           after={"key": key, "label": label, "is_required": bool(is_required)})
    db.commit()
    db.refresh(row)
    return row


# ── writes: UAT ─────────────────────────────────────────────────────────────

def set_check(db: Session, impl: Implementation, actor_id: Optional[str],
              check_id: str, *,
              status: Optional[str] = None,
              notes: Optional[str] = None,
              is_required: Optional[bool] = None) -> ImplementationCheck:
    """The INTERNAL verdict on one check. Never the customer's — see approve()."""
    row = _one(db, ImplementationCheck, impl, check_id, "Check")
    before = {"status": row.status}
    if status is not None:
        if status not in CHECK_STATUSES:
            raise ValueError("Unknown check status '%s'." % status)
        row.status = status
        if status == CHECK_NOT_TESTED:
            row.tested_at, row.tested_by = None, None
        else:
            row.tested_at = datetime.utcnow()
            row.tested_by = actor_id
        # A RE-TEST INVALIDATES THE CUSTOMER'S APPROVAL. They approved a thing
        # that worked; if it has been retested or has failed since, their
        # approval is about a different state of the system and cannot be
        # allowed to keep standing in for one they have not seen.
        if status != CHECK_PASS:
            row.customer_approved_at, row.customer_approved_by = None, None
    if notes is not None:
        row.notes = str(notes) or None
    if is_required is not None:
        row.is_required = bool(is_required)

    impl.last_activity_at = datetime.utcnow()
    _audit(db, impl, actor_id, "implementation_check_changed",
           target_type="implementation_check", target_id=row.id,
           before=before, after={"status": row.status},
           details={"key": row.key, "label": row.label})
    db.commit()
    db.refresh(row)
    return row


def customer_approve_check(db: Session, impl: Implementation, actor_id: str,
                           check_id: str) -> ImplementationCheck:
    """The CUSTOMER agreeing that a passing check really passes.

    Refuses on anything not currently passing. A customer cannot approve a
    check nobody has run — that would turn their signature into the test.
    """
    row = _one(db, ImplementationCheck, impl, check_id, "Check")
    if row.status != CHECK_PASS:
        raise ValueError("This has not passed testing yet, so there is nothing "
                         "to approve.")
    row.customer_approved_at = datetime.utcnow()
    row.customer_approved_by = actor_id
    impl.last_activity_at = datetime.utcnow()
    _audit(db, impl, actor_id, "implementation_check_customer_approved",
           target_type="implementation_check", target_id=row.id,
           details={"key": row.key, "label": row.label})
    db.commit()
    db.refresh(row)
    return row


# ── writes: training ────────────────────────────────────────────────────────

def set_training(db: Session, impl: Implementation, actor_id: Optional[str],
                 training_id: str, *,
                 scheduled_at: Optional[datetime] = None,
                 owner_user_id: Optional[str] = None,
                 attendees: Optional[List[Dict[str, Any]]] = None,
                 completed: Optional[bool] = None,
                 notes: Optional[str] = None,
                 is_required: Optional[bool] = None) -> ImplementationTraining:
    row = _one(db, ImplementationTraining, impl, training_id, "Training session")
    before = {"scheduled_at": _iso(row.scheduled_at),
              "completed_at": _iso(row.completed_at)}
    if scheduled_at is not None:
        row.scheduled_at = scheduled_at
    if owner_user_id is not None:
        row.owner_user_id = owner_user_id or None
    if attendees is not None:
        row.attendees = _clean_attendees(attendees)
    if completed is not None:
        if completed:
            row.completed_at = row.completed_at or datetime.utcnow()
            row.completed_by = actor_id
        else:
            row.completed_at, row.completed_by = None, None
            # Un-completing a session withdraws the acknowledgement of it too:
            # the customer confirmed a session that, according to the record,
            # no longer happened.
            row.customer_acknowledged_at = None
            row.customer_acknowledged_by = None
    if notes is not None:
        row.notes = str(notes) or None
    if is_required is not None:
        row.is_required = bool(is_required)

    impl.last_activity_at = datetime.utcnow()
    _audit(db, impl, actor_id, "implementation_training_changed",
           target_type="implementation_training", target_id=row.id,
           before=before,
           after={"scheduled_at": _iso(row.scheduled_at),
                  "completed_at": _iso(row.completed_at)},
           details={"key": row.key, "title": row.title})
    db.commit()
    db.refresh(row)
    return row


def acknowledge_training(db: Session, impl: Implementation, actor_id: str,
                         training_id: str) -> ImplementationTraining:
    """The customer confirming their people were trained."""
    row = _one(db, ImplementationTraining, impl, training_id, "Training session")
    if row.completed_at is None:
        raise ValueError("This session has not been recorded as delivered yet.")
    row.customer_acknowledged_at = datetime.utcnow()
    row.customer_acknowledged_by = actor_id
    impl.last_activity_at = datetime.utcnow()
    _audit(db, impl, actor_id, "implementation_training_acknowledged",
           target_type="implementation_training", target_id=row.id,
           details={"key": row.key, "title": row.title})
    db.commit()
    db.refresh(row)
    return row


def _clean_attendees(rows: Any) -> List[Dict[str, Any]]:
    out = []
    if not isinstance(rows, list):
        return out
    for r in rows[:100]:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or "").strip()[:160]
        if not name:
            continue
        out.append({"name": name,
                    "email": str(r.get("email") or "").strip()[:200] or None,
                    "role": str(r.get("role") or "").strip()[:120] or None})
    return out


# ── writes: blockers ────────────────────────────────────────────────────────

def open_blocker(db: Session, impl: Implementation, actor_id: Optional[str], *,
                 title: str, detail: Optional[str] = None,
                 party: str = "provider",
                 owner_user_id: Optional[str] = None,
                 customer_visible: bool = False,
                 customer_action: Optional[str] = None) -> ImplementationBlocker:
    title = (title or "").strip()[:200]
    if not title:
        raise ValueError("A blocker needs a title.")
    if party not in BLOCKER_PARTIES:
        raise ValueError("Unknown party '%s'." % party)
    row = ImplementationBlocker(
        implementation_id=impl.id, organization_id=impl.organization_id,
        title=title, detail=(detail or None), party=party,
        owner_user_id=(owner_user_id or None),
        customer_visible=bool(customer_visible),
        customer_action=(customer_action or None),
        status=BLOCKER_OPEN, opened_at=datetime.utcnow(), opened_by=actor_id)
    db.add(row)
    # FLUSH BEFORE AUDITING. `id` comes from a column default, which SQLAlchemy
    # applies at flush time — so auditing a freshly added row without this
    # writes target_id=NULL, and the audit table's NOT NULL turns a successful
    # action into a failed transaction.
    db.flush()
    impl.last_activity_at = datetime.utcnow()
    _audit(db, impl, actor_id, "implementation_blocker_opened",
           target_type="implementation_blocker", target_id=row.id,
           after={"title": title, "party": party,
                  "customer_visible": bool(customer_visible)})
    db.commit()
    db.refresh(row)
    return row


def resolve_blocker(db: Session, impl: Implementation, actor_id: Optional[str],
                    blocker_id: str,
                    resolution: Optional[str] = None) -> ImplementationBlocker:
    row = _one(db, ImplementationBlocker, impl, blocker_id, "Blocker")
    if row.status == BLOCKER_RESOLVED:
        return row
    row.status = BLOCKER_RESOLVED
    row.resolved_at = datetime.utcnow()
    row.resolved_by = actor_id
    row.resolution = (resolution or None)
    impl.last_activity_at = datetime.utcnow()
    _audit(db, impl, actor_id, "implementation_blocker_resolved",
           target_type="implementation_blocker", target_id=row.id,
           after={"resolution": row.resolution})
    db.commit()
    db.refresh(row)
    return row


# ── writes: go-live approvals ───────────────────────────────────────────────

def set_approval(db: Session, impl: Implementation, actor_id: Optional[str],
                 kind: str, *, given_name: Optional[str] = None,
                 note: Optional[str] = None) -> ImplementationApproval:
    if kind not in APPROVAL_KINDS:
        raise ValueError("Unknown approval '%s'." % kind)
    row = (db.query(ImplementationApproval)
           .filter(ImplementationApproval.implementation_id == impl.id,
                   ImplementationApproval.kind == kind).first())
    if row is None:
        row = ImplementationApproval(
            implementation_id=impl.id, organization_id=impl.organization_id,
            kind=kind)
        db.add(row)
    row.given_at = datetime.utcnow()
    row.given_by = actor_id
    row.given_name = (given_name or None) and str(given_name)[:160]
    row.note = (note or None)
    db.flush()                       # see open_blocker — id exists after flush
    impl.last_activity_at = datetime.utcnow()
    _audit(db, impl, actor_id, "implementation_golive_approved",
           target_type="implementation_approval", target_id=row.id,
           after={"kind": kind, "given_name": row.given_name}, note=note)
    db.commit()
    db.refresh(row)
    return row


def clear_approval(db: Session, impl: Implementation, actor_id: Optional[str],
                   kind: str) -> None:
    row = (db.query(ImplementationApproval)
           .filter(ImplementationApproval.implementation_id == impl.id,
                   ImplementationApproval.kind == kind).first())
    if row is None:
        return
    db.delete(row)
    impl.last_activity_at = datetime.utcnow()
    _audit(db, impl, actor_id, "implementation_golive_approval_withdrawn",
           target_type="implementation_approval", target_id=row.id,
           before={"kind": kind})
    db.commit()


# ── shared lookup ───────────────────────────────────────────────────────────

def _one(db: Session, model, impl: Implementation, row_id: str, what: str):
    """One row, scoped to BOTH the implementation and its organization.

    The organization filter is redundant given the implementation filter, and
    it is here anyway for the reason stated in launch_intake_models: the
    isolation belongs in the query rather than in the reasoning about the
    query. A wrong id from anywhere returns nothing rather than another
    tenant's row.
    """
    row = (db.query(model)
           .filter(model.id == row_id,
                   model.implementation_id == impl.id,
                   model.organization_id == impl.organization_id)
           .first())
    if row is None:
        raise LookupError("%s not found on this launch." % what)
    return row


# ── projections ─────────────────────────────────────────────────────────────

def staff_view(db: Session, impl: Implementation) -> Dict[str, Any]:
    """Everything the implementation team needs, in one read."""
    apps = _approvals(db, impl)
    return {
        "integrations": [integration_public(r) for r in _integrations(db, impl)],
        "checks": [check_public(r) for r in _checks(db, impl)],
        "training": [training_public(r) for r in _training(db, impl)],
        "blockers": [blocker_public(r) for r in _blockers(db, impl)],
        "approvals": {k: approval_public(v) for k, v in apps.items()},
        "progress": progress(db, impl),
        "readiness": readiness(db, impl),
        "template": launch_template.resolve(db, impl.platform_id)["golive"],
    }


# The lifecycle phase the team is working in, said in words a customer reads
# as a sentence about people rather than a status code.
_PHASE_ACTIVITY = {
    "intake":       "We are waiting on your information so the build can start.",
    "access":       "We are collecting the access and files we need to build.",
    "build":        "We are building and configuring your system.",
    "integrations": "We are connecting your system to the services you use.",
    "review":       "We are testing everything end to end.",
    "training":     "We are getting your team trained and ready.",
    "golive":       "We are making the final checks before your launch.",
}


def customer_view(db: Session, impl: Implementation) -> Dict[str, Any]:
    """What the customer is entitled to see, assembled from scratch.

    Built rather than filtered — see the module docstring. Internal notes,
    blocker detail on invisible blockers, owner identities and the readiness
    gate itself are not in this object because they were never read into it.
    """
    lifecycle = launch_intake.lifecycle_for(impl.status)
    now_phase = next((p for p in lifecycle if p["state"] == "now"), None)
    phase_key = now_phase["key"] if now_phase else ("golive" if impl.status == "live"
                                                    else "intake")

    ints = _integrations(db, impl)
    chks = _checks(db, impl)
    trns = _training(db, impl)

    visible_blockers = [
        {"id": b.id, "title": b.title,
         "party_label": BLOCKER_PARTY_LABELS.get(b.party, b.party),
         "action": b.customer_action,
         "opened_at": _iso(b.opened_at)}
        for b in _blockers(db, impl, only_open=True) if b.customer_visible
    ]

    # WHAT THE CUSTOMER HAS TO DO, in the order they would do it. Each entry is
    # actionable on this screen or answerable by them; nothing here is a task
    # of ours dressed up as theirs.
    actions: List[Dict[str, Any]] = []
    for b in visible_blockers:
        actions.append({"kind": "blocker", "id": b["id"],
                        "label": b["action"] or b["title"]})
    for r in chks:
        if r.status == CHECK_PASS and r.customer_approved_at is None and r.is_required:
            actions.append({"kind": "approve_check", "id": r.id,
                            "label": "Confirm this works: %s" % r.label})
    for r in trns:
        if r.completed_at is not None and r.customer_acknowledged_at is None:
            actions.append({"kind": "acknowledge_training", "id": r.id,
                            "label": "Confirm your team attended: %s" % r.title})

    return {
        "phase": phase_key,
        "activity": _PHASE_ACTIVITY.get(phase_key, _PHASE_ACTIVITY["build"]),
        "connections": [
            {"label": r.label, "status": r.status,
             "status_label": INTEGRATION_STATUS_LABELS.get(r.status, r.status)}
            for r in ints if r.is_required
        ],
        "checks": [
            {"id": r.id, "label": r.label, "category": r.category,
             "status": r.status,
             "status_label": CHECK_STATUS_LABELS.get(r.status, r.status),
             "approved_at": _iso(r.customer_approved_at),
             "can_approve": r.status == CHECK_PASS and r.customer_approved_at is None}
            for r in chks
        ],
        "training": [
            {"id": r.id, "title": r.title,
             "scheduled_at": _iso(r.scheduled_at),
             "completed_at": _iso(r.completed_at),
             "acknowledged_at": _iso(r.customer_acknowledged_at),
             "can_acknowledge": r.completed_at is not None
                                and r.customer_acknowledged_at is None}
            for r in trns
        ],
        "blockers": visible_blockers,
        "actions": actions,
        "counts": {
            "connections_done": sum(1 for r in ints
                                    if r.is_required and r.status in INTEGRATION_SETTLED),
            "connections_total": sum(1 for r in ints if r.is_required),
            "checks_done": sum(1 for r in chks if r.status == CHECK_PASS),
            "checks_total": len(chks),
            "training_done": sum(1 for r in trns if r.completed_at),
            "training_total": len(trns),
        },
    }
