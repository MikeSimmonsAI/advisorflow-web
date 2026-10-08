"""Local invoice draft rules (pure functions over a draft dict; no I/O, no provider).

States: draft -> approval_ready -> (draft | void); draft -> void. `void` is terminal.
The slice stops at approval_ready: nothing here sends, finalizes, charges or marks paid.

Money is integer cents only. Server-authoritative totals:
    line_total = quantity * unit_price_cents
    subtotal   = sum(line_total)
    total      = subtotal - discount_cents + tax_cents
`tax_cents` is an operator-entered flat amount. It is NOT calculated and NOT attested.
"""
import copy
import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

STATES = ("draft", "approval_ready", "void")
TRANSITIONS = {"draft": ("approval_ready", "void"), "approval_ready": ("draft", "void"), "void": ()}
CURRENCY = "usd"
PROVIDER_STATUS = "not_started"

MAX_UNIT_CENTS = 1_000_000_000
MAX_QTY = 1_000_000
MAX_LINES = 200
MAX_SUBTOTAL_CENTS = 10_000_000_000_000     # keeps every sum well inside signed 64-bit
MAX_ADJUST_CENTS = MAX_SUBTOTAL_CENTS


class InvoiceError(Exception):
    """A rule refusal; the message is the user-facing reason."""


class NotFoundError(InvoiceError):
    """Unknown id, or an id that belongs to another tenant (deliberately indistinguishable)."""


class StaleError(InvoiceError):
    """The draft changed under this writer, or an idempotency key was reused for a different request."""


class StorageError(InvoiceError):
    """The store is unavailable or not migrated. Fail closed."""


# ── validation ──────────────────────────────────────────────────────────────────

def _int(v, name: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise InvoiceError("%s must be a whole number (integer cents / count)" % name)
    if v < lo or v > hi:
        raise InvoiceError("%s must be between %d and %d" % (name, lo, hi))
    return v


def _text(v, name: str, maxlen: int, required: bool) -> str:
    if not isinstance(v, str):
        raise InvoiceError("%s must be text" % name)
    v = v.strip()
    if required and not v:
        raise InvoiceError("%s is required" % name)
    if len(v) > maxlen:
        raise InvoiceError("%s is too long (max %d)" % (name, maxlen))
    return v


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _event(d: Dict, actor: str, action: str, detail: Dict) -> None:
    d["events"].append({"at": _now(), "actor": actor, "action": action,
                        "detail": json.dumps(detail, sort_keys=True)})


# ── money ───────────────────────────────────────────────────────────────────────

def totals(d: Dict) -> Dict[str, int]:
    subtotal = sum(l["quantity"] * l["unit_price_cents"] for l in d["lines"])
    return {"subtotal_cents": subtotal, "discount_cents": d["discount_cents"], "tax_cents": d["tax_cents"],
            "total_cents": subtotal - d["discount_cents"] + d["tax_cents"]}


def _check_money(d: Dict) -> None:
    t = totals(d)
    if t["subtotal_cents"] > MAX_SUBTOTAL_CENTS:
        raise InvoiceError("subtotal exceeds the supported maximum")
    if d["discount_cents"] > t["subtotal_cents"]:
        raise InvoiceError("discount cannot exceed the subtotal")
    if t["total_cents"] < 0:
        raise InvoiceError("total cannot be below zero")


def _require_editable(d: Dict) -> None:
    if d["state"] != "draft":
        raise InvoiceError("invoice is %s: money and lines are locked; return it to draft first" % d["state"])


# ── operations (each mutates the draft dict it is given and records one event) ──

def create(org_id: str, customer_name, memo, actor: str) -> Dict:
    d = {"id": uuid.uuid4().hex, "organization_id": org_id, "state": "draft", "version": 1, "currency": CURRENCY,
         "customer_name": _text(customer_name, "customer name", 200, True), "memo": _text(memo or "", "memo", 2000, False),
         "discount_cents": 0, "tax_cents": 0, "next_line_no": 1, "created_by": actor, "lines": [], "events": []}
    _event(d, actor, "created", {"customer_name": d["customer_name"]})
    return d


def add_line(d: Dict, description, quantity, unit_price_cents, actor: str) -> int:
    _require_editable(d)
    line = {"description": _text(description, "description", 200, True),
            "quantity": _int(quantity, "quantity", 1, MAX_QTY),
            "unit_price_cents": _int(unit_price_cents, "unit price", 0, MAX_UNIT_CENTS)}
    if len(d["lines"]) >= MAX_LINES:
        raise InvoiceError("too many lines (max %d)" % MAX_LINES)
    line["line_no"] = d["next_line_no"]
    d["next_line_no"] += 1
    d["lines"].append(line)
    try:
        _check_money(d)
    except InvoiceError:
        d["lines"].pop()          # a refused call leaves the draft exactly as it was
        d["next_line_no"] -= 1
        raise
    _event(d, actor, "line_added", {"line_no": line["line_no"], "description": line["description"],
                                    "quantity": line["quantity"], "unit_price_cents": line["unit_price_cents"]})
    return line["line_no"]


def remove_line(d: Dict, line_no, actor: str) -> None:
    _require_editable(d)
    keep = [l for l in d["lines"] if l["line_no"] != line_no]
    if len(keep) == len(d["lines"]):
        raise InvoiceError("line %s does not exist on this draft" % line_no)
    old, d["lines"] = d["lines"], keep
    try:
        _check_money(d)   # removing a line can leave the discount larger than the subtotal
    except InvoiceError:
        d["lines"] = old
        raise InvoiceError("removing this line would leave the discount above the subtotal; lower the discount first")
    _event(d, actor, "line_removed", {"line_no": line_no})


def set_adjustments(d: Dict, discount_cents, tax_cents, actor: str) -> None:
    _require_editable(d)
    prev = (d["discount_cents"], d["tax_cents"])
    d["discount_cents"] = _int(discount_cents, "discount", 0, MAX_ADJUST_CENTS)
    d["tax_cents"] = _int(tax_cents, "tax amount", 0, MAX_ADJUST_CENTS)
    try:
        _check_money(d)
    except InvoiceError:
        d["discount_cents"], d["tax_cents"] = prev
        raise
    _event(d, actor, "adjustments_set", {"discount_cents": d["discount_cents"], "tax_cents": d["tax_cents"]})


def transition(d: Dict, to, reason, actor: str) -> None:
    if to not in STATES:
        raise InvoiceError("unknown state")
    if to not in TRANSITIONS[d["state"]]:
        raise InvoiceError("cannot move an invoice from %s to %s" % (d["state"], to))
    reason = _text(reason or "", "reason", 500, to == "void")
    if to == "approval_ready":
        blocker = approval_blocker(d)
        if blocker:
            raise InvoiceError(blocker)
    prev = d["state"]
    d["state"] = to
    _event(d, actor, "state_changed", {"from": prev, "to": to, "reason": reason})


def approval_blocker(d: Dict) -> Optional[str]:
    """Why this draft cannot become approval_ready right now (None when it can)."""
    if d["state"] != "draft":
        return "invoice is %s" % d["state"]
    if not d["lines"]:
        return "add at least one line first"
    try:
        _check_money(d)
    except InvoiceError as e:
        return str(e)
    if totals(d)["total_cents"] <= 0:
        return "total must be greater than zero"
    return None


# ── truthful API view ───────────────────────────────────────────────────────────

def view(d: Dict, with_events: bool = True) -> Dict:
    out = {"id": d["id"], "state": d["state"], "version": d["version"], "currency": d["currency"],
           "customer_name": d["customer_name"], "memo": d["memo"],
           "lines": [{"line_no": l["line_no"], "description": l["description"], "quantity": l["quantity"],
                      "unit_price_cents": l["unit_price_cents"],
                      "line_total_cents": l["quantity"] * l["unit_price_cents"]}
                     for l in sorted(d["lines"], key=lambda x: x["line_no"])],
           "editable": d["state"] == "draft",
           "allowed_transitions": list(TRANSITIONS[d["state"]]),
           "refusal_reason": (approval_blocker(d) if d["state"] == "draft" else
                              "locked while %s" % d["state"] if d["state"] == "approval_ready" else "invoice is void"),
           "provider_status": PROVIDER_STATUS,
           "tax_note": "Tax is an operator-entered amount; it is not calculated or attested.",
           "created_by": d["created_by"]}
    out.update(totals(d))
    if with_events:
        out["events"] = copy.deepcopy(d["events"])
    return out
