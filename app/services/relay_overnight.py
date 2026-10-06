"""Control Room additions: Completed Work, Suggested Next, Overnight Package,
morning summary and the home summary. Pure functions over normalized relay
events (see relay_control.normalize_events); no network, no storage.

Hard rules encoded here:
  * A package is Mike's raw text. It is recorded as a `[RELAY:MIKE_INPUT]`
    (KIND: overnight_package) for ChatGPT Work to review; it has no
    `[RELAY:DIRECTIVE]` marker and no BRANCH, so Claude can never be woken by it.
  * ChatGPT decomposes it into ONE formal directive at a time.
  * Suggestions come only from real relay history and never run by themselves.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.services.relay_control import (
    MIKE_INPUT, PACKAGE_KIND, PKG_SEP, PKG_MAX_OBJECTIVES, PKG_MAX_OBJECTIVE_CHARS, PKG_MAX_NAME,
    _SECRET, DirectionError, _find, _short, defuse,
)

PACKAGE_STATES = ("Draft", "Ready", "Running", "Completed", "Blocked", "Needs Mike")


# ── Completed Work ───────────────────────────────────────────────────────────

def completed_work(events: List[Dict], now: Optional[datetime] = None) -> List[Dict]:
    """Finished runs, newest first. `today` / `last7` are Central Time calendar
    buckets so the UI filters without redoing the date math."""
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ct).date()
    out = []
    for e in _find(events, "claude_complete"):
        if e["status"] != "COMPLETED":
            continue
        t = e["technical"]
        at = datetime.fromisoformat(e["at"]) if e.get("at") else None
        age = (today - at.astimezone(ct).date()).days if at else None
        out.append({
            "title": _short(t.get("result") or t.get("project") or "Completed task", 90),
            "project": t.get("project") or "",
            "at": e.get("at"), "at_ct": e["at_ct"], "actor": "Claude",
            "result": e["detail"] or "Completed.",
            "relay_ref": e.get("run_id"),
            "today": age == 0,
            "last7": age is not None and 0 <= age < 7,
            "technical": {k: t.get(k) for k in ("tests", "commits", "branch", "comment_id", "url",
                                                "production_impact") if t.get(k)},
        })
    out.sort(key=lambda i: i["at"] or "", reverse=True)
    return out


# ── Suggested Next ───────────────────────────────────────────────────────────

def suggestions(events: List[Dict], state: Dict) -> List[Dict]:
    """Next steps that follow from relay history. Nothing is invented: a
    suggestion exists only because the latest run recommended it, is blocked, or
    an approval gate is open. Effort appears only when the reporting run supplied
    NEXT_ACTION_EFFORT. Nothing here ever executes; the UI offers add-to-package,
    add-to-queue or dismiss."""
    out: List[Dict] = []
    gate = state.get("needs_mike")
    for e in _find(events, "claude_complete")[-1:]:
        t = e["technical"]
        if e["status"] == "BLOCKED":
            out.append({"id": "blocked:%s" % e.get("run_id"),
                        "suggestion": "Resolve the blocker, then retry: " + (t.get("project") or "the last task"),
                        "reason": _short(t.get("blockers") or e["detail"] or "The last run reported a blocker.", 200),
                        "effort": t.get("effort"), "dependency": "The blocker must be resolved first.",
                        "source_run": e.get("run_id")})
        elif (t.get("next_action") or "").strip().lower() not in ("", "none", "n/a", "-"):
            out.append({"id": "next:%s" % e.get("run_id"), "suggestion": _short(t["next_action"], 200),
                        "reason": "Recommended by the last completed run"
                                  + (" (%s)." % t["project"] if t.get("project") else "."),
                        "effort": t.get("effort"),
                        "dependency": "Needs Mike's decision first." if gate else None,
                        "source_run": e.get("run_id")})
    if gate:
        out.append({"id": "gate:%s" % gate.get("run_id"), "suggestion": "Answer the open approval gate",
                    "reason": gate["decision"], "effort": None, "dependency": "Only Mike can answer this.",
                    "source_run": gate.get("run_id"), "needs_mike": True})
    return out


# ── Package safety ───────────────────────────────────────────────────────────

# Conservative on purpose: a false positive costs Mike one confirmation; a false
# negative is the failure to avoid.
GATE_RULES = (
    ("production", re.compile(r"\b(production|prod|go[- ]?live|launch|promot\w*|merge (?:to )?main)\b", re.I)),
    ("real customers", re.compile(r"\b(real (?:sci )?customers?|(?:send|blast) (?:sms|texts?|emails?|messages?)|message (?:real )?(?:customers?|leads?)|text blast)\b", re.I)),
    ("spending", re.compile(r"\b(spend|purchase|buy|upgrade (?:the )?plan|paid (?:plan|service)|billing|pay for)\b", re.I)),
    ("destructive data", re.compile(r"\b(delete|drop (?:table|database)|wipe|purge|truncate|destroy)\b", re.I)),
    ("credentials", re.compile(r"\b(credentials?|api key|secret|token|password|grant (?:app )?permissions?)\b", re.I)),
    ("legal/carrier", re.compile(r"\b(10dlc|attest\w*|carrier registration|legal|tcpa)\b", re.I)),
    ("irreversible infrastructure", re.compile(r"\b(dns|domain transfer|cancel (?:service|subscription))\b", re.I)),
)


def package_safety(objectives: List[str]) -> Dict:
    items = []
    for i, text in enumerate(objectives):
        hits = [name for name, rx in GATE_RULES if rx.search(text or "")]
        items.append({"position": i + 1, "text": text, "gate": bool(hits), "gate_reasons": hits})
    gates = [x for x in items if x["gate"]]
    return {
        "items": items, "objective_count": len(items), "gate_count": len(gates),
        "summary": "%d objective(s). %s Nothing runs on production, with real customers, or spends money without Mike." % (
            len(items),
            ("%d touch an approval gate; ChatGPT will stop and ask Mike before that step." % len(gates))
            if gates else "No approval gates detected; ChatGPT still stops if one appears."),
        "rules": ["ChatGPT turns this into ONE formal directive at a time",
                  "Claude never receives this text directly",
                  "Ordinary problems do not stop the package; approval gates do"],
    }


def validate_package(name: str, objectives: List[str]) -> Dict:
    name = (name or "").strip()
    objs = [(o or "").strip() for o in (objectives or [])]
    if not name:
        raise DirectionError("no_name", "Give the package a name.")
    if len(name) > PKG_MAX_NAME:
        raise DirectionError("name_too_long", "Keep the name under %d characters." % PKG_MAX_NAME)
    if not objs or any(not o for o in objs):
        raise DirectionError("no_objectives", "Every objective needs text, and the package needs at least one.")
    if len(objs) > PKG_MAX_OBJECTIVES:
        raise DirectionError("too_many", "A package holds at most %d objectives." % PKG_MAX_OBJECTIVES)
    if any(len(o) > PKG_MAX_OBJECTIVE_CHARS for o in objs):
        raise DirectionError("objective_too_long", "Keep each objective under %d characters." % PKG_MAX_OBJECTIVE_CHARS)
    if _SECRET.search(name + " " + " ".join(objs)):
        raise DirectionError("secret", "That looks like a password or key. Remove it; never paste secrets here.")

    def clean(t: str) -> str:
        return defuse(t).replace(PKG_SEP.strip(), ";").replace("\n", " ")
    return {"name": clean(name), "objectives": [clean(o) for o in objs]}


def build_package_comment(input_id: str, name: str, objectives: List[str], gates: int, who: str, ct: str) -> str:
    """Audit record and hand-off to ChatGPT Work. No [RELAY:DIRECTIVE] marker and
    no BRANCH, so the Claude workflow can never accept it."""
    return "\n".join([
        MIKE_INPUT, "input_id: " + input_id, "KIND: " + PACKAGE_KIND, "MODE: after_current",
        "FROM: " + who, "TIME CT: " + ct,
        "FOR: ChatGPT Work (review first; this is NOT a directive and does not wake Claude)",
        "PACKAGE_NAME: " + name, "OBJECTIVE_COUNT: %d" % len(objectives), "APPROVAL_GATES: %d" % gates,
        "OBJECTIVES: " + PKG_SEP.join(objectives),
        'DIRECTION: Overnight package "%s" with %d objective(s). Decompose into one formal directive at a '
        "time, continue through ordinary problems, stop at approval gates, and post a morning summary."
        % (name, len(objectives)),
    ])


# ── Overnight status, morning summary, home summary ─────────────────────────

MORNING_SUMMARY_FIELDS = ("completed_work", "changes", "tests", "blockers", "mike_decisions")


def morning_summary(events: List[Dict], package: Optional[Dict] = None) -> Dict:
    """What Mike reads in the morning, from relay history since the package
    started (all history when no package)."""
    start = 0
    if package:
        start = next((i for i, e in enumerate(events)
                      if e["technical"].get("package_id") == package["package_id"]), 0)
    window = events[start:]
    finished = _find(window, "claude_complete")
    done = [e for e in finished if e["status"] == "COMPLETED"]
    blocked = [e for e in finished if e["status"] == "BLOCKED"]
    gates = _find(window, "approval_gate")
    return {
        "completed_work": [{"what": e["detail"], "at_ct": e["at_ct"], "relay_ref": e.get("run_id")} for e in done],
        "changes": [{"commits": e["technical"]["commits"], "relay_ref": e.get("run_id")}
                    for e in done if e["technical"].get("commits")],
        "tests": [{"result": e["technical"]["tests"], "relay_ref": e.get("run_id")}
                  for e in finished if e["technical"].get("tests")],
        "blockers": [{"what": e["technical"].get("blockers") or e["detail"], "relay_ref": e.get("run_id")}
                     for e in blocked],
        "mike_decisions": [{"decision": e["detail"], "risk": e["technical"].get("risk"),
                            "relay_ref": e.get("run_id")} for e in gates],
        "empty": not (done or blocked or gates),
        "fields": list(MORNING_SUMMARY_FIELDS),
    }


def overnight_status(events: List[Dict], state: Dict) -> Dict:
    """Server-derived status of the latest started package. Draft and Ready exist
    only in the browser, before Start."""
    starts = [e for e in events if e["kind"] == "mike_direction" and e["technical"].get("package_id")]
    if not starts:
        return {"status": "None", "package": None, "morning_summary": morning_summary(events)}
    e = starts[-1]
    t = e["technical"]
    after = events[events.index(e) + 1:]
    pkg = {"package_id": t["package_id"], "name": t.get("package_name") or "Overnight package",
           "objectives": t.get("objectives") or [], "objective_count": len(t.get("objectives") or []),
           "approval_gates": t.get("gates"), "started_ct": e["at_ct"],
           "completed_runs": len([x for x in _find(after, "claude_complete") if x["status"] == "COMPLETED"])}
    verdict = next((r["technical"]["package_status"] for r in reversed(_find(after, "chatgpt_review"))
                    if r["technical"].get("package_id") == t["package_id"]
                    and r["technical"].get("package_status")), None)
    last_terminal = next((x for x in reversed(after) if x["kind"] in ("claude_complete", "approval_gate")), None)
    if state.get("needs_mike"):
        status = "Needs Mike"
    elif verdict == "completed":
        status = "Completed"
    elif verdict == "blocked" or (last_terminal and last_terminal["kind"] == "claude_complete"
                                  and last_terminal["status"] == "BLOCKED"):
        status = "Blocked"
    else:
        status = "Running"
    return {"status": status, "package": pkg, "morning_summary": morning_summary(events, pkg)}


def home_summary(state: Dict, completed: List[Dict], sugg: List[Dict], overnight: Dict) -> Dict:
    working = state["task"] if state.get("actor") in ("Claude", "ChatGPT") else None
    today = [c for c in completed if c["today"]]
    gate = state.get("needs_mike")
    return {
        "working_now": {"text": working, "actor": state.get("actor"), "status": state.get("status")},
        "completed_today": {"count": len(today), "latest": today[0]["title"] if today else None},
        "suggested_next": {"count": len(sugg), "top": sugg[0]["suggestion"] if sugg else None},
        "overnight": {"status": overnight["status"], "name": (overnight["package"] or {}).get("name")},
        "needs_mike": {"count": 1 if gate else 0, "decision": gate["decision"] if gate else None},
    }


def extras(events: List[Dict], state: Dict, now: Optional[datetime] = None) -> Dict:
    done = completed_work(events, now)
    sugg = suggestions(events, state)
    over = overnight_status(events, state)
    return {"completed_work": done, "suggested_next": sugg, "overnight": over,
            "home": home_summary(state, done, sugg, over)}
