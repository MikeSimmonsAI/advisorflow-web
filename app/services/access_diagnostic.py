"""WHY CAN THIS PERSON NOT SEE THEIR WORK — answered with evidence, not guesses.

WHAT THIS IS FOR

A production report of the shape "an advisor's leads are gone" has at least six
possible causes and they are indistinguishable from a screenshot: the leads were
never assigned, the membership was never written, the membership was revoked,
the workspace resolves somewhere else, the role resolves wrong, or a count tile
is computed from a different query than the list beneath it.

Answering that used to mean a database shell. This answers it from the God
console instead, read-only, with no DATABASE_URL and no credential anywhere near
the person asking.

WHAT IT DELIBERATELY DOES NOT DO

- It does not WRITE. Not a row, not a column, not a membership. It is a
  diagnosis, and a diagnosis that edits the patient is not a diagnosis.
- It does not BYPASS lead_scope to compute the scoped number. The whole value of
  the comparison is that column B is the real authorization answer; a
  reimplementation would agree with itself and prove nothing.
- It does not return a secret. Not an environment variable, not a connection
  string, not a token, not a key. It returns COUNTS and NAMES.

HOW THE SIMULATION STAYS HONEST

`lead_scope` resolves the current workspace from the request's X-Workspace-Id
header, falling back to the ambient request when a caller does not pass one. If
this module let that happen it would be reading GOD'S headers while claiming to
describe the TARGET's session - so every simulated resolution is run against a
synthetic request carrying exactly the header being tested, built here and
thrown away. Nothing about the caller's own context is read, and nothing about
it is changed.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.models.models import Lead, Organization, User
from app.models.sales_models import (
    Membership, BrandSalesOrg, SCOPE_CUSTOMER_ORG, SCOPE_BRAND_SALES_ORG,
    SCOPE_PLATFORM,
)


class _Timer:
    """Elapsed milliseconds per phase. Not a profiler - a stopwatch."""

    def __init__(self):
        self.marks: Dict[str, float] = {}

    def time(self, label: str, fn):
        t0 = time.perf_counter()
        try:
            return fn()
        finally:
            self.marks[label] = round((time.perf_counter() - t0) * 1000, 2)


def _synthetic_request(workspace_id: Optional[str] = None) -> Request:
    """A throwaway request carrying exactly the header under test.

    THE POINT OF THIS FUNCTION. `lead_scope` falls back to the ambient request
    when it is not handed one, and the ambient request during a diagnostic is
    GOD'S. Simulating "what resolves for this user with no workspace header"
    while silently reading the operator's own header would produce a confident
    answer about the wrong session - the exact failure mode a diagnostic exists
    to remove.

    Headers are ASCII byte pairs in an ASGI scope; nothing else about the scope
    is read by the code under test.
    """
    headers: List = []
    if workspace_id:
        headers.append((b"x-workspace-id", str(workspace_id).encode("latin-1")))
    return Request({
        "type": "http", "method": "GET", "path": "/god/diagnostics/user-access",
        "headers": headers, "query_string": b"", "scheme": "https",
        "server": ("diagnostic", 443), "client": None, "root_path": "",
    })


def resolve_target(db: Session, user_id: Optional[str] = None,
                   email: Optional[str] = None) -> User:
    """Find the subject by EXACT id or EXACT email. No fuzzy matching.

    Deliberately exact: a diagnostic that guesses which "Jason" you meant will
    eventually answer confidently about the wrong person, and two users sharing
    a display name is exactly how the Greenland credential pointed at the wrong
    Mike Simmons for two days.
    """
    if user_id:
        u = db.query(User).filter(User.id == user_id).first()
        if u is None:
            raise HTTPException(status_code=404, detail="No user with that id.")
        return u
    if email:
        e = (email or "").strip().lower()
        u = db.query(User).filter(User.email == e).first()
        if u is None:
            raise HTTPException(
                status_code=404,
                detail="No user with that exact email. This diagnostic does not "
                       "match partially - use the exact address or the user id.")
        return u
    raise HTTPException(status_code=400,
                        detail="Provide user_id or email.")


def _org_name(db: Session, org_id: Optional[str]) -> Optional[str]:
    if not org_id:
        return None
    o = db.query(Organization).filter(Organization.id == org_id).first()
    return o.name if o else None


def run(db: Session, target: User) -> Dict[str, Any]:
    """The whole diagnosis. Read-only from the first line to the last."""
    from app.services import lead_scope, workspace_access

    t = _Timer()

    # ── IDENTITY ────────────────────────────────────────────────────────────
    identity = {
        "user_id": target.id,
        "full_name": target.full_name,
        "email": target.email,
        "is_active": bool(target.is_active),
        "platform_role": target.role,          # the PLATFORM role, not workspace
        "legacy_organization_id": target.organization_id,
        "legacy_organization_name": _org_name(db, target.organization_id),
        "platform_id": target.platform_id,
    }

    # ── MEMBERSHIPS (all scopes, ACTIVE AND REVOKED) ────────────────────────
    #
    # Revoked rows are INCLUDED and labelled. "There is no membership" and
    # "there is a membership and somebody switched it off" are different
    # diagnoses with different fixes, and a list of active rows only cannot
    # tell them apart.
    def _all_memberships():
        return db.query(Membership).filter(Membership.user_id == target.id).all()

    rows = t.time("memberships", _all_memberships)

    platform_memberships, brand_memberships, workspace_memberships = [], [], []
    for m in rows:
        entry = {
            "membership_id": m.id,
            "scope_type": m.scope_type,
            "scope_id": m.scope_id,
            "role": m.role,
            "is_active": bool(m.is_active),
            "state": "active" if m.is_active else "REVOKED",
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        if m.scope_type == SCOPE_BRAND_SALES_ORG:
            bso = (db.query(BrandSalesOrg)
                   .filter(BrandSalesOrg.id == m.scope_id).first())
            entry["brand_sales_org_name"] = bso.name if bso else None
            entry["resolves"] = bso is not None
            brand_memberships.append(entry)
        elif m.scope_type == SCOPE_CUSTOMER_ORG:
            org = (db.query(Organization)
                   .filter(Organization.id == m.scope_id).first())
            entry["organization_name"] = org.name if org else None
            entry["organization_slug"] = org.slug if org else None
            entry["organization_is_active"] = (
                None if org is None else bool(org.is_active))
            # A membership whose organization no longer exists is a real and
            # findable state, and it is invisible in a list of names.
            entry["resolves"] = org is not None
            workspace_memberships.append(entry)
        elif m.scope_type == SCOPE_PLATFORM:
            platform_memberships.append(entry)

    # ── AUTHORIZED CONTEXTS — the canonical answer, not a copy of it ────────
    contexts = t.time(
        "authorized_contexts",
        lambda: workspace_access.authorized_contexts(db, target))

    # ── WORKSPACE RESOLUTION + LEAD COUNTS, PER SCENARIO ────────────────────
    #
    # Scenario one is "no workspace header", which is what every advisor who
    # has never touched the switcher sends. The rest are each workspace the
    # server says this person may enter.
    scenarios: List[Dict[str, Any]] = []
    entered = [w["organization_id"] for w in contexts.get("workspace_contexts", [])]

    def _one(label: str, ws_id: Optional[str]) -> Dict[str, Any]:
        req = _synthetic_request(ws_id)
        st = _Timer()
        resolved = st.time(
            "workspace_resolution",
            lambda: lead_scope.active_workspace_org_id(target, db, req))
        role = st.time(
            "effective_role",
            lambda: lead_scope.effective_role(target, db, req))

        # A. RAW — ownership straight from the table, no authorization at all.
        def _raw():
            q = db.query(Lead).filter(Lead.assigned_to_id == target.id)
            if resolved:
                q = q.filter(Lead.organization_id == resolved)
            return q.count()
        raw_assigned = st.time("raw_assigned_count", _raw)

        # B. CANONICAL — lead_scope itself. NOT reimplemented.
        scoped: Optional[int] = None
        scoped_error: Optional[str] = None

        def _scoped():
            return lead_scope.authorized_lead_query(db, target, request=req).count()
        try:
            scoped = st.time("lead_scope_count", _scoped)
        except HTTPException as e:
            # A refusal IS the diagnosis - record it rather than letting it
            # abort the report.
            scoped_error = "%s: %s" % (e.status_code, e.detail)
            st.marks.setdefault("lead_scope_count", 0.0)

        # The organization-wide total, so "this advisor sees 0 of 4,000" and
        # "this whole organization has 0" are distinguishable at a glance.
        org_total = None
        if resolved:
            org_total = st.time(
                "org_total_count",
                lambda: db.query(Lead).filter(
                    Lead.organization_id == resolved).count())

        divergence = None
        if scoped is not None and scoped != raw_assigned:
            if scoped > raw_assigned:
                divergence = ("scoped EXCEEDS raw assigned - this person is "
                              "resolving as a manager (effective role '%s'), so "
                              "the scope is the whole organization rather than "
                              "their own book." % role)
            else:
                divergence = ("scoped is BELOW raw assigned - the scope is "
                              "narrower than ownership. Compare the resolved "
                              "workspace '%s' against the organization_id on "
                              "those leads." % resolved)

        return {
            "scenario": label,
            "workspace_header_sent": ws_id,
            "resolved_workspace_id": resolved,
            "resolved_workspace_name": _org_name(db, resolved),
            "effective_workspace_role": role,
            "A_raw_assigned": raw_assigned,
            "B_lead_scope_count": scoped,
            "B_lead_scope_error": scoped_error,
            "organization_total_leads": org_total,
            "divergence": divergence,
            "timings_ms": st.marks,
        }

    scenarios.append(t.time("scenario_no_header",
                            lambda: _one("no workspace header", None)))
    for ws in entered:
        scenarios.append(_one("workspace header = %s" % ws, ws))

    # ── C AND D: THE REAL ENDPOINT SERVICES ─────────────────────────────────
    #
    # The endpoint FUNCTIONS are called, not reimplemented, because the
    # question is what the customer's screen shows and only the real code path
    # answers that. They are plain functions over (db, current_user); calling
    # them here runs exactly what a request runs.
    api_counts: Dict[str, Any] = {}

    def _service_counts():
        from app.routers import leads_router
        from app.services import request_context
        out: Dict[str, Any] = {}
        req = _synthetic_request(None)   # the no-header case, as a browser sends

        # THE AMBIENT REQUEST IS SWAPPED, AND THIS GATE CAUGHT WHY.
        #
        # `status_funnel` takes no request argument, so lead_scope falls back to
        # the AMBIENT one - which during a diagnostic is the operator's. With a
        # workspace header on God's own session, the tile count for the SUBJECT
        # was being resolved against God's header. It failed closed here only
        # because the target held no membership in the named workspace; had God
        # been standing in a workspace the subject DOES belong to, the report
        # would have been confidently wrong about somebody else's screen.
        #
        # So the diagnostic publishes its own synthetic request for the duration
        # of these calls and restores the operator's afterwards. This sets the
        # DIAGNOSTIC's context, never the operator's session, and the finally
        # block means an exception cannot leave the swap in place.
        token = request_context.set_current_request(req)
        try:
            payload = leads_router.list_leads(
                request=req, status_filter=None, tier=None, message_track=None,
                temperature=None, import_list_name=None, page=1, page_size=1,
                db=db, current_user=target)
            out["C_leads_service_total"] = payload.get("total")
        except HTTPException as e:
            out["C_leads_service_error"] = "%s: %s" % (e.status_code, e.detail)
        except Exception as e:      # a service that raises is itself the finding
            out["C_leads_service_error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
        try:
            funnel = leads_router.status_funnel(db=db, current_user=target)
            out["D_status_funnel"] = funnel
            out["D_status_funnel_total"] = sum(
                (row.get("count") or 0) for row in funnel) if isinstance(funnel, list) else None
        except HTTPException as e:
            out["D_status_funnel_error"] = "%s: %s" % (e.status_code, e.detail)
        except Exception as e:
            out["D_status_funnel_error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
        finally:
            request_context.reset_current_request(token)
        return out

    api_counts = t.time("endpoint_services", _service_counts)

    # ── THE ANSWER, IN ONE LINE ─────────────────────────────────────────────
    primary = scenarios[0] if scenarios else {}
    findings: List[str] = []
    if not workspace_memberships:
        findings.append(
            "NO customer_org membership of any kind. This person cannot enter "
            "any customer workspace, and the switcher will show no button.")
    else:
        active_ws = [w for w in workspace_memberships if w["is_active"]]
        if not active_ws:
            findings.append(
                "Every customer_org membership is REVOKED. Access was removed "
                "deliberately at some point; the backfill will not restore it.")
        dangling = [w for w in workspace_memberships if not w["resolves"]]
        if dangling:
            findings.append(
                "%d membership(s) point at an organization that does not exist."
                % len(dangling))
    if primary.get("A_raw_assigned") == 0:
        findings.append(
            "ZERO leads are assigned to this user in the resolved workspace. "
            "The scope is not hiding anything - there is nothing owned to hide.")
    if primary.get("divergence"):
        findings.append(primary["divergence"])
    if not findings:
        findings.append(
            "Ownership, membership, workspace and role all resolve, and the "
            "counts agree.")

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "identity": identity,
        "platform_memberships": platform_memberships,
        "brand_sales_memberships": brand_memberships,
        "customer_workspace_memberships": workspace_memberships,
        "authorized_contexts": contexts,
        "workspace_scenarios": scenarios,
        "endpoint_service_counts": api_counts,
        "findings": findings,
        "timings_ms": t.marks,
        "notes": [
            "Read-only. This diagnostic performs no writes of any kind.",
            "B is computed by calling lead_scope.authorized_lead_query itself, "
            "not by reimplementing it.",
            "C and D call the real endpoint functions, so they are what the "
            "customer's screen would show.",
            "Every resolution is run against a synthetic request carrying only "
            "the header under test - the operator's own session is never read.",
        ],
    }
