"""GOD-ONLY, READ-ONLY: run the qualification engine AS a named user.

WHY THIS EXISTS RATHER THAN "just log in as them"

Validating a new engine against a real production book means answering "what
would THIS advisor see" without borrowing their password, and without the
operator's own session quietly supplying the answer. That is the same problem
`access_diagnostic` solved, and this reuses its machinery rather than inventing
a second, subtly different simulation:

  - the subject is resolved by EXACT id or EXACT email, never a name match,
    because a diagnostic that guesses which "Jason" you meant will eventually
    be confidently wrong about somebody else;
  - every call runs against a SYNTHETIC request carrying exactly the workspace
    header under test, published for the duration and restored in a finally
    block, because `lead_scope` falls back to the ambient request and the
    ambient request during a diagnostic is the OPERATOR'S;
  - the engine is CALLED, not reimplemented. A reimplementation would agree
    with itself and prove nothing about what the advisor's screen will show.

IT WRITES NOTHING AND IT SENDS NOTHING. It is a report about who WOULD be
qualified. No message leaves the building because this ran.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Lead, Organization, User
from app.services import lead_scope, qualification
from app.services.access_diagnostic import _synthetic_request, resolve_target
from app.services.request_context import set_current_request, reset_current_request


def run(db: Session, *, user_id: Optional[str] = None, email: Optional[str] = None,
        channel: str = qualification.CHANNEL_EMAIL,
        organization_id: Optional[str] = None,
        sample_size: int = 0) -> Dict[str, Any]:
    """Qualify one named user's own authorized leads, for one channel."""
    target = resolve_target(db, user_id=user_id, email=email)

    # Which workspace to stand them in. Their own authorized contexts decide -
    # this does not accept an arbitrary organization id and go looking, because
    # that would be a way to ask about a workspace the subject cannot enter.
    from app.services import workspace_access
    contexts = workspace_access.authorized_contexts(db, target)
    workspaces = contexts.get("workspace_contexts", [])
    chosen = None
    if organization_id:
        for w in workspaces:
            if w["organization_id"] == organization_id:
                chosen = organization_id
                break
        if chosen is None:
            return {
                "subject": _subject(target),
                "error": ("That user holds no active membership in the named "
                          "workspace, so there is nothing to qualify there."),
                "authorized_workspaces": [w["organization_id"] for w in workspaces],
            }
    elif len(workspaces) == 1:
        chosen = workspaces[0]["organization_id"]
    elif workspaces:
        chosen = None  # ambiguous: report every one below
    # A legacy user with only users.organization_id and no membership row still
    # resolves through lead_scope's fallback, so "no workspace context" is not
    # the same as "no leads" - the no-header run below covers exactly that.

    out: Dict[str, Any] = {
        "subject": _subject(target),
        "channel": channel,
        "authorized_workspaces": [
            {"organization_id": w["organization_id"],
             "organization_name": w.get("organization_name"),
             "workspace_role": w.get("role")}
            for w in workspaces
        ],
        "runs": [],
    }

    scenarios: List[tuple] = [("no workspace header", None)]
    for w in workspaces:
        scenarios.append(("workspace %s" % (w.get("organization_name")
                                            or w["organization_id"]),
                          w["organization_id"]))

    for label, ws_id in scenarios:
        if chosen and ws_id and ws_id != chosen:
            continue
        out["runs"].append(_one_run(db, target, channel, label, ws_id, sample_size))

    return out


def _subject(target: User) -> Dict[str, Any]:
    return {
        "user_id": target.id,
        "email": target.email,
        "full_name": target.full_name,
        "platform_role": target.role,
        "legacy_organization_id": getattr(target, "organization_id", None),
    }


def _one_run(db: Session, target: User, channel: str, label: str,
             workspace_id: Optional[str], sample_size: int) -> Dict[str, Any]:
    req = _synthetic_request(workspace_id)
    # THE AMBIENT REQUEST IS SWAPPED. Without this the engine would resolve the
    # OPERATOR'S workspace while claiming to describe the subject's - the exact
    # defect Gate 32 caught in the access diagnostic, which failed closed there
    # only by luck.
    token = set_current_request(req)
    try:
        resolved = lead_scope.active_workspace_org_id(target, db, req)
        role = lead_scope.effective_role(target, db, req)
        authorized = lead_scope.authorized_lead_query(db, target, request=req).all()
        total_authorized = len(authorized)
        # ALWAYS computed with per-lead detail. `sample_size` controls what is
        # RETURNED, not what is measured - the priority audit below needs every
        # decision, and a diagnostic that could not tell you whether a score
        # varies is the one that let an entire book sit on one number.
        result = qualification.qualify_leads(
            db, target, channel=channel, request=req, include_leads=True)
    except Exception as exc:  # a failed scenario is reported, never swallowed
        return {"scenario": label, "workspace_header": workspace_id,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:300])}
    finally:
        reset_current_request(token)

    org = (db.query(Organization).filter(Organization.id == resolved).first()
           if resolved else None)

    run: Dict[str, Any] = {
        "scenario": label,
        "workspace_header": workspace_id,
        "resolved_workspace_id": resolved,
        "resolved_workspace_name": org.name if org else None,
        "resolved_role": role,
        # THE NUMBER EVERY OTHER NUMBER IS MEASURED AGAINST. If this disagrees
        # with what the advisor sees in their leads list, the problem is
        # authorization and not qualification.
        "total_authorized": total_authorized,
        "total_selected": result["total_selected"],
        "ready": result["ready"],
        "review": result["review"],
        "excluded": result["excluded"],
        "priority": result["priority"],
        "exclusion_reasons": result["exclusion_reasons"],
        "review_reasons": result["review_reasons"],
        "priority_factors": result["priority_factors"],
    }
    if sample_size:
        # Ids and buckets only. A diagnostic about one named person's book does
        # not need to carry family names and addresses to be useful.
        run["sample"] = [
            {"lead_id": d["lead_id"], "bucket": d["bucket"],
             "priority": d["priority"], "score": d["score"],
             "reasons": [r["code"] for r in d["reasons"]]}
            for d in result.get("leads", [])[:sample_size]
        ]
    run["counts_agree"] = (total_authorized == result["total_selected"])
    run["priority_audit"] = _priority_audit(result.get("leads", []), authorized)
    return run


def _priority_audit(decisions, leads) -> Dict[str, Any]:
    """CAN THE PRIORITY ACTUALLY TELL THESE LEADS APART.

    A band that every lead reaches carries no information, and the only way to
    see that is to look at the SPREAD rather than the counts. An entire book
    landing on one score is invisible in "94 HIGH" and obvious in
    "distinct_scores: 1".

    It also reports the raw inputs the priority factors are computed FROM, so a
    factor that is true for everybody can be traced to the field that made it
    true - which is how a value stamped on a whole import file gets caught
    being read as a fact about a person.

    Counts and distributions only. No names, no addresses, no lead ids.
    """
    scored = [d["score"] for d in decisions
              if d.get("score") is not None]
    audit: Dict[str, Any] = {
        "scored_leads": len(scored),
        "distinct_scores": len(set(scored)),
        "min_score": min(scored) if scored else None,
        "max_score": max(scored) if scored else None,
        "spread": (max(scored) - min(scored)) if scored else None,
    }
    if scored:
        ordered = sorted(scored)
        audit["median_score"] = ordered[len(ordered) // 2]
        hist: Dict[str, int] = {}
        for s in scored:
            key = "%d-%d" % ((s // 10) * 10, (s // 10) * 10 + 9)
            hist[key] = hist.get(key, 0) + 1
        audit["score_histogram"] = dict(sorted(hist.items(),
                                               key=lambda kv: int(kv[0].split("-")[0])))
        # THE SENTENCE THAT MATTERS. Stated by the diagnostic rather than left
        # for somebody to infer from a histogram they may not read.
        if audit["distinct_scores"] <= 1:
            audit["verdict"] = ("Every scored lead has an IDENTICAL score. The "
                                "priority band is carrying no information about "
                                "this population - it is a label, not a ranking.")
        elif audit["spread"] is not None and audit["spread"] < 10:
            audit["verdict"] = ("Scores vary by less than 10 points. The priority "
                                "band is barely distinguishing these leads.")
        else:
            audit["verdict"] = ("Scores vary meaningfully across this population.")

    # ── the raw inputs, so a uniform factor can be traced to its field ──
    def share(pred):
        return sum(1 for l in leads if pred(l))

    rel: Dict[str, int] = {}
    years: Dict[str, int] = {}
    for l in leads:
        key = (getattr(l, "relationship_type", None) or "(none)")
        rel[key] = rel.get(key, 0) + 1
        y = getattr(l, "source_year", None)
        years[str(y) if y else "(none)"] = years.get(str(y) if y else "(none)", 0) + 1

    audit["inputs"] = {
        "total_leads": len(leads),
        # If this has ONE key covering every lead, the relationship factor
        # cannot differentiate anybody - it came from the import, not the person.
        "relationship_type_distribution": dict(
            sorted(rel.items(), key=lambda kv: -kv[1])),
        "source_year_distribution": dict(
            sorted(years.items(), key=lambda kv: kv[0])),
        "with_imported_last_contact_date": share(
            lambda l: getattr(l, "last_contact_date", None) is not None),
        "with_imported_last_action": share(
            lambda l: bool((getattr(l, "last_action_raw", None) or "").strip())),
        "with_imported_status_reason": share(
            lambda l: bool((getattr(l, "status_reason_raw", None) or "").strip())),
        "with_platform_last_messaged_at": share(
            lambda l: getattr(l, "last_messaged_at", None) is not None),
        "with_zip_code": share(lambda l: bool(getattr(l, "zip_code", None))),
        "with_street_address": share(lambda l: bool(getattr(l, "street_address", None))),
        "with_both_names": share(
            lambda l: bool(getattr(l, "first_name", None))
            and bool(getattr(l, "last_name", None))),
    }
    return audit
