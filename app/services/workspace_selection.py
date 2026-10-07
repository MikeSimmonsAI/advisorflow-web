"""THE VERDICT ON AN X-Workspace-Id HEADER — pure, imports nothing.

`workspace_access.selected_workspace_id` discards an unauthorized selection and
lets the request fall back to the caller's home tenant. For a person whose
membership was removed (or who asserts an id they never held) that meant a
request STILL CARRYING the target's id was answered with the home workspace's
data: no cross-tenant leak, but a screen that labels one tenant's rows as
another's, and a failed switch that looks like a successful one.

The verdict is separated from the database so it executes under plain Python:

    NONE     no header: legacy / single-workspace behaviour, unchanged
    GRANTED  header names a workspace the caller holds (membership, or the
             documented legacy users.organization_id column)
    DENIED   header names anything else: the request fails closed

DENIED is deliberately indistinguishable between "does not exist" and "exists
but is not yours" - the caller only ever sees the same refusal text.
"""
from typing import Iterable, Optional

NONE = "none"
GRANTED = "granted"
DENIED = "denied"

DENIED_DETAIL = "That workspace is not available to this account."


def selection_verdict(requested: Optional[str], held_org_ids: Iterable[str],
                      legacy_org_id: Optional[str] = None) -> str:
    requested = (requested or "").strip()
    if not requested:
        return NONE
    held = set(held_org_ids or ())
    if legacy_org_id:
        held.add(legacy_org_id)
    return GRANTED if requested in held else DENIED
