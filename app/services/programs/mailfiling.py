"""Outlook filing - EvoSys processes first, EvoSys files second.

After a reply that arrived on a location alias has been read, routed to its
workspace and location, attached to the contact's conversation, classified,
paused the sequence and raised its alerts, the original Outlook message is
moved to

    <program.mailbox_folder_path>/<location folder>
    e.g. Inbox/Customers Folder/SCI/Eastern Gate Memorial Gardens

RULES
  * Never before processing; never for an unmatched reply (it stays in the
    Inbox until a person decides) - see inbound_mailbox_service.poll_mailbox.
  * Needs Mail.ReadWrite on the mailbox. With a Mail.Read-only sign-in the
    filing is recorded "skipped - reconnect with Mail.ReadWrite" and the
    message simply stays where it is.
  * A folder that does not exist is never created on the fly (a typo would
    silently scatter mail): the filing fails with the missing path named.
  * A failed move is retried on the next polls up to MAX_ATTEMPTS, then left
    in place with its reason. Mail is never lost by a failed move.
"""
import logging
from datetime import datetime
from typing import Callable, Dict, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from app.models.program_models import LocationProfile, OutreachProgram, ProgramMailFiling

log = logging.getLogger(__name__)
GRAPH = "https://graph.microsoft.com/v1.0"
MAX_ATTEMPTS = 5

Mover = Callable[[str, str], Tuple[bool, Optional[str]]]   # (graph_id, folder_path) -> (ok, error)


def folder_for(prog: OutreachProgram, prof: LocationProfile) -> Optional[str]:
    root = (prog.mailbox_folder_path or "").strip().strip("/")
    if not root or prof is None:
        return None
    name = (prof.mailbox_folder or prof.official_name or "").strip()
    return "%s/%s" % (root, name) if name else None


class GraphMover:
    """Real Graph moves for one poll run (folder ids cached for the run)."""

    def __init__(self, token: str, can_write: bool):
        self.token, self.can_write, self._ids = token, can_write, {}

    def _h(self):
        return {"Authorization": "Bearer %s" % self.token, "Prefer": 'IdType="ImmutableId"'}

    def _child(self, parent_id: str, name: str) -> Optional[str]:
        url = "%s/me/mailFolders/%s/childFolders" % (GRAPH, parent_id)
        params = {"$select": "id,displayName", "$top": 250, "includeHiddenFolders": "true"}
        while url:
            r = httpx.get(url, headers=self._h(), params=params, timeout=20)
            if r.status_code != 200:
                raise RuntimeError("Graph folder list failed %s: %s" % (r.status_code, r.text[:200]))
            data = r.json()
            for f in data.get("value", []):
                if (f.get("displayName") or "").strip().lower() == name.strip().lower():
                    return f["id"]
            url, params = data.get("@odata.nextLink"), None
        return None

    def folder_id(self, path: str) -> Optional[str]:
        key = path.lower()
        if key in self._ids:
            return self._ids[key]
        parts = [p for p in path.split("/") if p.strip()]
        if not parts:
            return None
        first = parts[0].strip().lower()
        if first in ("inbox", "archive"):
            r = httpx.get("%s/me/mailFolders/%s" % (GRAPH, first), headers=self._h(),
                          params={"$select": "id"}, timeout=20)
            if r.status_code != 200:
                raise RuntimeError("Graph %s lookup failed %s" % (first, r.status_code))
            fid = r.json().get("id")
        else:
            fid = self._child("msgfolderroot", parts[0])
        for part in parts[1:]:
            if fid is None:
                break
            fid = self._child(fid, part)
        self._ids[key] = fid
        return fid

    def __call__(self, graph_id: str, path: str) -> Tuple[bool, Optional[str]]:
        if not self.can_write:
            return False, "skipped: the mailbox is connected read-only - reconnect it with Mail.ReadWrite"
        try:
            fid = self.folder_id(path)
            if not fid:
                return False, "Outlook folder not found: %s" % path
            r = httpx.post("%s/me/messages/%s/move" % (GRAPH, graph_id), headers=self._h(),
                           json={"destinationId": fid}, timeout=20)
            if r.status_code in (200, 201):
                return True, None
            return False, "Graph move failed %s: %s" % (r.status_code, r.text[:200])
        except Exception as exc:  # noqa: BLE001
            return False, "%s: %s" % (type(exc).__name__, str(exc)[:200])


def file_message(db: Session, mover: Optional[Mover], *, box_id: str, mailbox_message_id: str,
                 graph_id: str, prog: OutreachProgram, prof: LocationProfile) -> Optional[ProgramMailFiling]:
    """Record and attempt the post-processing move for one processed message."""
    path = folder_for(prog, prof)
    if not path:
        return None
    row = (db.query(ProgramMailFiling)
           .filter(ProgramMailFiling.mailbox_message_id == mailbox_message_id).first())
    if row is None:
        row = ProgramMailFiling(organization_id=prog.organization_id, mailbox_id=box_id,
                                mailbox_message_id=mailbox_message_id, graph_message_id=graph_id,
                                location_id=prof.location_id, folder_path=path, status="pending")
        db.add(row)
        db.flush()
    _attempt(row, mover)
    return row


def _attempt(row: ProgramMailFiling, mover: Optional[Mover]) -> None:
    if row.status == "filed":
        return
    if mover is None:
        row.status, row.last_error = "pending", "no mailbox connection available for filing"
        return
    row.attempts = (row.attempts or 0) + 1
    ok, err = mover(row.graph_message_id, row.folder_path)
    if ok:
        row.status, row.filed_at, row.last_error = "filed", datetime.utcnow(), None
    elif err and err.startswith("skipped"):
        row.status, row.last_error = "skipped", err
    else:
        row.status = "failed" if row.attempts >= MAX_ATTEMPTS else "pending"
        row.last_error = err
        log.warning("outlook filing %s attempt %d: %s", row.id, row.attempts, err)


def retry_pending(db: Session, mover: Optional[Mover], box_id: str) -> Dict:
    """Re-try filings that have not succeeded yet (and skipped ones, once the
    mailbox can write). Called once per poll."""
    out = {"retried": 0, "filed": 0}
    if mover is None:
        return out
    states = ("pending", "skipped") if getattr(mover, "can_write", True) else ("pending",)
    rows = (db.query(ProgramMailFiling)
            .filter(ProgramMailFiling.mailbox_id == box_id, ProgramMailFiling.status.in_(states))
            .limit(100).all())
    for row in rows:
        if row.status == "skipped":
            row.status = "pending"
        _attempt(row, mover)
        out["retried"] += 1
        out["filed"] += int(row.status == "filed")
    db.commit()
    return out
