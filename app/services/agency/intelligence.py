"""AGENCY INTELLIGENCE — attention feed, summary counts, Ask EvoAI.

Every attention item and every Ask answer is built from the queries module's
list builders, so each one links to a real record, and every summary count is
`len(list)` of the exact filter its link names.
Ask EvoAI (app.services.agency.ask) is a scored intent parser over SUPPORTED questions only. It never
generates free text about records it did not read; an unsupported question gets
supported:false and the list of questions it can answer.
"""
from typing import Any, Dict, List

from app.services.agency import queries as Q

PATHS = {"prospect": "/agency/prospects/%s", "application": "/agency/applications/%s",
         "appointment": "/agency/appointments/%s", "agent": "/agency/agents/%s",
         "recruit": "/agency/recruits/%s", "policy": "/agency/policies/%s",
         "task": "/agency/tasks/%s", "assignment": "/agency/prospects/%s"}


def link(kind, id_, path_id=None):
    return {"type": kind, "id": id_, "path": PATHS[kind] % (path_id or id_)}


def attention(ctx: Q.Ctx) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for p in Q.list_prospects(ctx, intent="high", past_response_target=True):
        items.append({"kind": "high_intent_past_response_target", "severity": "high",
                      "title": "High-intent prospect not yet contacted: %s" % p["name"],
                      "detail": "Past the %d-minute response target." % ctx.cfg["response_target_minutes"],
                      "link": link("prospect", p["id"]), "is_demo": p["is_demo"]})
    for st in ("timed_out", "escalated"):
        for p in Q.list_prospects(ctx, assignment=st):
            items.append({"kind": "offer_%s" % st, "severity": "high",
                          "title": "Assignment %s: %s" % (st.replace("_", " "), p["name"]),
                          "detail": "Needs a manager decision.",
                          "link": link("prospect", p["id"]), "is_demo": p["is_demo"]})
    if ctx.manager:
        for p in Q.list_prospects(ctx, unassigned=True):
            items.append({"kind": "unassigned_prospect", "severity": "medium",
                          "title": "Unassigned prospect: %s" % p["name"], "detail": "No agent assigned.",
                          "link": link("prospect", p["id"]), "is_demo": p["is_demo"]})
    for a in Q.list_applications(ctx, stalled=True):
        items.append({"kind": "stalled_application", "severity": "high",
                      "title": "Stalled application: %s" % (a["prospect"]["name"] or a["id"]),
                      "detail": "%s for %d days (threshold %d)." % ((a["status"] or "").replace("_", " ").capitalize(),
                                                                   a["days_in_status"],
                                                                   ctx.cfg["stalled_days"]),
                      "link": link("application", a["id"]), "is_demo": a["is_demo"]})
    for ap in Q.list_appointments(ctx, needs_confirmation=True):
        items.append({"kind": "appointment_needs_confirmation", "severity": "medium",
                      "title": "Appointment needs confirmation: %s" % (ap["prospect"]["name"] or ""),
                      "detail": "Starts %s." % ap["starts_at"],
                      "link": link("appointment", ap["id"]), "is_demo": ap["is_demo"]})
    if ctx.manager:
        for ag in Q.list_agents(ctx, over_workload=True):
            items.append({"kind": "agent_over_workload", "severity": "medium",
                          "title": "Agent above workload threshold: %s" % ag["name"],
                          "detail": "%d%% (%d of %d)." % (ag["workload_pct"], ag["active_count"], ag["max_active"]),
                          "link": link("agent", ag["user_id"]), "is_demo": ag["is_demo"]})
    for t in Q.overdue_tasks(ctx):
        if t.lead_id:
            lk = link("prospect", t.lead_id)
        elif t.application_id:
            lk = link("application", t.application_id)
        elif t.policy_id:
            lk = link("policy", t.policy_id)
        elif t.recruit_id:
            lk = link("recruit", t.recruit_id)
        else:
            lk = link("task", t.id)
        items.append({"kind": "overdue_task", "severity": "medium", "title": "Overdue: %s" % t.title,
                      "detail": "Due %s." % (t.due_at.date().isoformat() if t.due_at else ""),
                      "link": lk, "is_demo": bool(t.is_demo)})
    for r in Q.list_recruits(ctx, milestone_overdue=True):
        items.append({"kind": "licensing_milestone_overdue", "severity": "medium",
                      "title": "Licensing milestone overdue: %s" % r["name"],
                      "detail": "%d overdue milestone(s)." % r["milestones_overdue"],
                      "link": link("recruit", r["id"]), "is_demo": r["is_demo"]})
    for p in Q.list_policies(ctx, review_due=True):
        items.append({"kind": "annual_review_due", "severity": "low",
                      "title": "Annual review due: %s" % (p["client"]["name"] or ""),
                      "detail": "Review date %s." % p["annual_review_date"],
                      "link": link("policy", p["id"]), "is_demo": p["is_demo"]})
    return items


SUMMARY_SPECS = [
    # key, label, list function, kwargs, API link
    ("prospects", "Prospects", "list_prospects", {}, "/agency/prospects"),
    ("unassigned", "Unassigned prospects", "list_prospects", {"unassigned": True},
     "/agency/prospects?unassigned=true"),
    ("high_intent", "High-intent prospects", "list_prospects", {"intent": "high"},
     "/agency/prospects?intent=high"),
    ("offers_pending", "Offers awaiting acceptance", "list_prospects", {"assignment": "offered"},
     "/agency/prospects?assignment=offered"),
    ("escalated", "Escalated assignments", "list_prospects", {"assignment": "escalated"},
     "/agency/prospects?assignment=escalated"),
    ("applications_open", "Open applications", "list_applications", {"open_only": True},
     "/agency/applications?open=true"),
    ("applications_stalled", "Stalled applications", "list_applications", {"stalled": True},
     "/agency/applications?stalled=true"),
    ("appointments_today", "Appointments today", "list_appointments", {"range_": "today"},
     "/agency/appointments?range=today"),
    ("appointments_need_confirmation", "Appointments needing confirmation", "list_appointments",
     {"needs_confirmation": True}, "/agency/appointments?needs_confirmation=true"),
    ("policies_review_due", "Annual reviews due", "list_policies", {"review_due": True},
     "/agency/policies?review_due=true"),
    ("recruits", "Recruits", "list_recruits", {}, "/agency/recruits"),
    ("agents", "Agents", "list_agents", {}, "/agency/agents"),
]


def summary(ctx: Q.Ctx) -> Dict[str, Any]:
    counts = {}
    for key, label, fn, kw, lk in SUMMARY_SPECS:
        n = Q.count_prospects(ctx, **kw) if fn == "list_prospects" else len(getattr(Q, fn)(ctx, **kw))
        counts[key] = {"label": label, "count": n, "link": lk}
    att = attention(ctx)
    counts["attention"] = {"label": "Needs attention", "count": len(att), "link": "/agency/attention"}
    return {"counts": counts}


# ── Ask EvoAI ───────────────────────────────────────────────────────────────
# The parser and every supported intent live in app.services.agency.ask; this
# wrapper keeps the original import surface (intel.ask / intel.parse_intent).

def parse_intent(question: str):
    from app.services.agency import ask as _ask
    return _ask.parse_intent(question)


def ask(ctx: Q.Ctx, question: str) -> Dict[str, Any]:
    from app.services.agency import ask as _ask
    return _ask.ask(ctx, question)
