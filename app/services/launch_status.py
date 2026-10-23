"""LAUNCH BOARD - what each project has actually committed, merged and shipped.

READ-ONLY, GET-ONLY. Uses the same GitHub read credentials the relay Control
Room already uses (relay_control_room._credentials / _get), so nothing new has
to be configured and nothing can be written.

THE DELIVERY LADDER, AND WHY EACH RUNG IS SEPARATE.

    committed + pushed    a branch with commits exists on GitHub
    pr_opened             a pull request exists
    pr_merged             GitHub says it was merged
    deployed_staging      UNKNOWN - no staging deploy source is connected
    deployed_production   the merge commit is contained in the commit the
                          production backend reports it is running
                          (RENDER_GIT_COMMIT), checked with GitHub's compare
    production_verified   UNKNOWN unless recorded - a person (or a recorded
                          check) has to say it works live

Each rung is "yes", "no" or "unknown". Unknown is shown as "Unable to verify",
never guessed. There are no completion percentages anywhere.

PROJECTS are matched from the branch name and PR title (bookaboost before
wholesale, so a BookaBoost Wholesale branch counts as BookaBoost). Anything
unmatched is Platform Core.
"""

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger(__name__)

PROJECTS = [
    ("ai_workforce", "AI Workforce", ("ai-workforce", "aiwf", "workforce")),
    ("sci", "SCI", ("sci",)),
    ("bookaboost", "BookaBoost", ("bookaboost",)),
    ("wholesale", "Wholesale", ("wholesale", "evosense")),
    ("atlantis", "Atlantis", ("atlantis", "energy")),
    ("roofing", "Roofing", ("roofing", "roof")),
    ("platform", "Platform Core", ()),
]
RECENT_DAYS = 14
CACHE_SECONDS = 90
_cache: Dict[str, Any] = {"at": 0.0, "value": None}


def _project_for(branch: str, title: str) -> str:
    hay = ("%s %s" % (branch or "", title or "")).lower()
    for key, _label, words in PROJECTS:
        # Whole-word match, so "sci" does not catch "discipline".
        if any(re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(w), hay)
               for w in words):
            return key
    return "platform"


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def deployed_commit() -> Optional[str]:
    return (os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("GIT_COMMIT")
            or os.environ.get("SOURCE_VERSION") or None)


def _checks(get: Callable, sha: str) -> Dict[str, Any]:
    try:
        data = get("/commits/%s/check-runs?per_page=50" % sha) or {}
    except Exception:                                        # noqa: BLE001
        return {"state": "unknown"}
    runs = data.get("check_runs") or []
    passed = sum(1 for r in runs if r.get("conclusion") in ("success", "neutral", "skipped"))
    failed = sum(1 for r in runs if r.get("conclusion") in ("failure", "timed_out", "cancelled", "action_required"))
    pending = sum(1 for r in runs if r.get("status") != "completed")
    state = ("failing" if failed else "pending" if pending else
             "passing" if runs else "none")
    return {"state": state, "passed": passed, "failed": failed,
            "pending": pending, "total": len(runs)}


def _in_production(get: Callable, merge_sha: Optional[str],
                   deployed: Optional[str]) -> str:
    if not merge_sha or not deployed:
        return "unknown"
    if deployed.startswith(merge_sha) or merge_sha.startswith(deployed):
        return "yes"
    try:
        cmp = get("/compare/%s...%s" % (merge_sha, deployed)) or {}
    except Exception:                                        # noqa: BLE001
        return "unknown"
    status = cmp.get("status")
    if status in ("ahead", "identical"):
        return "yes"
    if status in ("behind", "diverged"):
        return "no"
    return "unknown"


def _pr_row(pr: Dict) -> Dict[str, Any]:
    head = pr.get("head") or {}
    return {"number": pr.get("number"), "title": pr.get("title"),
            "url": pr.get("html_url"), "branch": head.get("ref"),
            "head_sha": (head.get("sha") or "")[:40],
            "draft": bool(pr.get("draft")), "state": pr.get("state"),
            "merged": bool(pr.get("merged_at")),
            "merged_at": pr.get("merged_at"),
            "merge_sha": pr.get("merge_commit_sha"),
            "base": (pr.get("base") or {}).get("ref"),
            "updated_at": pr.get("updated_at"),
            "author": (pr.get("user") or {}).get("login")}


def build(get: Optional[Callable] = None, *, now: Optional[datetime] = None,
          deployed: Optional[str] = None) -> Dict[str, Any]:
    """The board. Never raises; a missing integration is an explicit state."""
    from app.services import relay_control_room as rcr
    now = now or datetime.now(timezone.utc)
    deployed = deployed if deployed is not None else deployed_commit()
    base = {"generated_at": now.isoformat(), "deployed_commit": deployed,
            "deployed_source": ("production backend (RENDER_GIT_COMMIT)"
                                if deployed else None)}
    if get is None:
        token, repo = rcr._credentials()
        if not token or not repo:
            return {**base, "available": False,
                    "reason": "GitHub read access is not configured on this "
                              "server, so commits, pull requests and checks "
                              "are unable to verify here.",
                    "projects": _empty_projects()}
        get = rcr._get
    try:
        open_prs = get("/pulls?state=open&per_page=50") or []
        closed = get("/pulls?state=closed&sort=updated&direction=desc&per_page=60") or []
    except Exception as exc:                                 # noqa: BLE001
        return {**base, "available": False,
                "reason": "GitHub could not be read just now (%s). Showing "
                          "nothing rather than old data." % type(exc).__name__,
                "projects": _empty_projects()}

    cutoff = now - timedelta(days=RECENT_DAYS)
    merged = [p for p in closed if p.get("merged_at")
              and (_parse(p["merged_at"]) or now) >= cutoff]
    by_key: Dict[str, Dict[str, List[Dict]]] = {
        k: {"open": [], "merged": []} for k, _l, _w in PROJECTS}
    for pr in open_prs:
        k = _project_for((pr.get("head") or {}).get("ref"), pr.get("title"))
        by_key[k]["open"].append(_pr_row(pr))
    for pr in merged:
        k = _project_for((pr.get("head") or {}).get("ref"), pr.get("title"))
        by_key[k]["merged"].append(_pr_row(pr))

    projects = []
    for key, label, _w in PROJECTS:
        rows = by_key[key]
        rows["merged"].sort(key=lambda r: r.get("merged_at") or "", reverse=True)
        rows["open"].sort(key=lambda r: r.get("updated_at") or "", reverse=True)
        latest_merge = rows["merged"][0] if rows["merged"] else None
        focus = rows["open"][0] if rows["open"] else latest_merge
        checks = _checks(get, focus["head_sha"]) if focus and focus.get("head_sha") else {"state": "unknown"}
        prod = _in_production(get, latest_merge.get("merge_sha"), deployed) if latest_merge else "no"
        # Newest first: once a merge into a branch is in production, every
        # older merge into that same branch is too (it is an ancestor).
        live_bases = set()
        for r in rows["merged"][:5]:
            if r is latest_merge:
                r["in_production"] = prod
            elif r.get("base") in live_bases:
                r["in_production"] = "yes"
            else:
                r["in_production"] = _in_production(get, r.get("merge_sha"), deployed)
            if r["in_production"] == "yes":
                live_bases.add(r.get("base"))
        ladder = [
            {"key": "committed", "label": "Committed", "state": "yes" if focus else "no"},
            {"key": "pushed", "label": "Pushed", "state": "yes" if focus else "no"},
            {"key": "pr_opened", "label": "PR opened", "state": "yes" if focus else "no"},
            {"key": "pr_merged", "label": "PR merged",
             "state": "yes" if (focus and focus.get("merged")) else "no"},
            {"key": "staging", "label": "On staging", "state": "unknown"},
            {"key": "production", "label": "In production",
             "state": prod if (focus and focus.get("merged")) else "no"},
            {"key": "verified", "label": "Production verified", "state": "unknown"},
        ]
        blocker, next_action, status = _assess(focus, checks, prod)
        projects.append({
            "key": key, "label": label, "status": status,
            "focus": focus, "checks": checks,
            "ladder": ladder, "blocker": blocker, "next_action": next_action,
            "open_prs": rows["open"][:8],
            "recently_merged": rows["merged"][:5],
            "last_activity": (focus or {}).get("updated_at") or (focus or {}).get("merged_at"),
        })
    summary = {
        "being_built": sum(len(by_key[k]["open"]) for k in by_key),
        "blocked": sum(1 for p in projects if p["status"] == "blocked"),
        "needs_owner": sum(1 for p in projects for r in p["open_prs"]
                           if r["draft"] and "redesign" in (r["branch"] or "")),
        "released_recently": sum(1 for p in projects for r in p["recently_merged"]
                                 if r.get("in_production") == "yes"),
    }
    return {**base, "available": True, "projects": projects, "summary": summary,
            "window_days": RECENT_DAYS}


def _assess(focus, checks, prod):
    if not focus:
        return None, "No pull requests in the last %d days." % RECENT_DAYS, "quiet"
    if not focus.get("merged"):
        if checks.get("state") == "failing":
            return ("%d check(s) failing on PR #%s." % (checks["failed"], focus["number"]),
                    "Fix the failing checks on PR #%s." % focus["number"], "blocked")
        if focus.get("draft") and "redesign" in (focus.get("branch") or ""):
            return (None, "Owner review: PR #%s is a redesign waiting for "
                          "approval." % focus["number"], "needs_owner")
        if focus.get("draft"):
            return None, "Finish draft PR #%s and mark it ready." % focus["number"], "building"
        return None, "Review and merge PR #%s." % focus["number"], "building"
    if prod == "yes":
        return None, "Merged and in production. Check it live.", "released"
    if prod == "no":
        return ("Merged but not in the running production build yet.",
                "Wait for the deploy, then check it live.", "deploying")
    return (None, "Merged. Unable to verify the production build from here.",
            "merged")


def _empty_projects():
    return [{"key": k, "label": l, "status": "unknown", "focus": None,
             "checks": {"state": "unknown"},
             "ladder": [{"key": s, "label": t, "state": "unknown"} for s, t in (
                 ("committed", "Committed"), ("pushed", "Pushed"),
                 ("pr_opened", "PR opened"), ("pr_merged", "PR merged"),
                 ("staging", "On staging"), ("production", "In production"),
                 ("verified", "Production verified"))],
             "blocker": None, "next_action": None, "open_prs": [],
             "recently_merged": [], "last_activity": None}
            for k, l, _w in PROJECTS]


def cached(*, force: bool = False) -> Dict[str, Any]:
    """GitHub is rate-limited; the board is cached for CACHE_SECONDS."""
    if not force and _cache["value"] is not None \
            and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["value"]
    value = build()
    _cache.update(at=time.time(), value=value)
    return value
