"""Pure caller-identity selection and replay guard for the HUMAN dialer.

No database, no provider, no network. The service layer loads rows and calls
these helpers; tests execute them directly.

  eligible_identities   the tenant's own active, voice-capable, well-formed numbers
  choose_sender         one number, or a refusal with an actionable reason
  find_inflight         a live call this user already has to this lead (double-click)

Rules: a number from another organization, an inactive or non-voice number, a
malformed caller ID, or a brand/platform pool number is never offered. Zero
numbers refuses. More than one in the preferred tier refuses until the user
selects one explicitly; a selection outside the eligible set refuses (it is
never "corrected" to another number).
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Iterable, List, Optional

LIVE_STATUSES = ("initiating", "ringing_user", "in_progress")
INFLIGHT_WINDOW = timedelta(minutes=10)
_E164 = re.compile(r"^\+1[2-9]\d{2}[2-9]\d{6}$")

NO_IDENTITY = ("No approved voice number is assigned to this organization. A platform "
               "administrator assigns one in Organization Control Center > Operations > "
               "Phone numbers.")
NEEDS_SELECTION = "This organization has more than one approved voice number. Choose the one this call should come from."
BAD_SELECTION = "That caller ID is not an approved voice number for this organization. Choose one from the list."


def valid_caller_id(e164) -> bool:
    return isinstance(e164, str) and bool(_E164.match(e164))


def _g(row, name, default=None):
    return row.get(name, default) if isinstance(row, dict) else getattr(row, name, default)


def eligible_identities(rows: Iterable, org_id: Optional[str],
                        workspace_id: Optional[str] = None) -> List[dict]:
    """Options the authenticated tenant may call from, oldest first."""
    out = []
    if not org_id:
        return out
    for r in rows or []:
        if _g(r, "organization_id") != org_id:
            continue
        if not _g(r, "is_active", False) or not _g(r, "cap_voice_outbound", False):
            continue
        if not valid_caller_id(_g(r, "e164")):
            continue
        ws = _g(r, "workspace_id")
        if ws and ws != workspace_id:
            continue
        out.append({"id": _g(r, "id"), "e164": _g(r, "e164"), "label": _g(r, "label") or None,
                    "workspace_id": ws or None, "_order": str(_g(r, "created_at") or "")})
    out.sort(key=lambda o: (o["_order"], str(o["id"])))
    for o in out:
        o.pop("_order")
    return out


def choose_sender(options: List[dict], requested_id: Optional[str] = None,
                  workspace_id: Optional[str] = None) -> dict:
    """{"ok", "number_id", "e164", "label", "reason", "needs_selection"}."""
    def refuse(reason, needs=False):
        return {"ok": False, "number_id": None, "e164": None, "label": None,
                "reason": reason, "needs_selection": needs}

    def take(o):
        return {"ok": True, "number_id": o["id"], "e164": o["e164"], "label": o["label"],
                "reason": None, "needs_selection": False}

    if not options:
        return refuse(NO_IDENTITY)
    if requested_id:
        hit = next((o for o in options if o["id"] == requested_id), None)
        return take(hit) if hit else refuse(BAD_SELECTION, needs=len(options) > 1)
    tier = [o for o in options if workspace_id and o["workspace_id"] == workspace_id] \
        or [o for o in options if not o["workspace_id"]]
    if len(tier) == 1:
        return take(tier[0])
    return refuse(NEEDS_SELECTION, needs=True)


def find_inflight(calls: Iterable, lead_id: str, user_id: str, now) -> Optional[object]:
    """A live human call by this user to this lead started within the window."""
    for c in calls or []:
        if _g(c, "lead_id") != lead_id or _g(c, "advisor_id") != user_id:
            continue
        if _g(c, "status") not in LIVE_STATUSES:
            continue
        made = _g(c, "created_at")
        if made is not None and now - made > INFLIGHT_WINDOW:
            continue
        return c
    return None
