"""Pure buyer-selection transition rules (no DB, no framework imports).

One buyer is CURRENT-SELECTED per deal. The invariant lives in two columns that
must agree: `outreach.is_selected` and `outreach.status == "selected"`. The old
select path cleared only the flag, so a replaced buyer kept status "selected" and
the board / counters (`responded_statuses`, "interested" tallies) called them
chosen. Nothing new is invented here: a replaced buyer returns to the EXISTING
status their own evidence supports (`offer_submitted` if they priced the deal,
else `interested`, which is the least a buyer must have said to be selectable).
The audit trail is the `buyer.selection_replaced` event carrying before/after.

Every refusal is (code, label); the router renders "label [code]".
"""

REPLACED_WITH_OFFER = "offer_submitted"
REPLACED_NO_OFFER = "interested"

# Statuses that may never be written over a currently selected row by a generic
# response/update call: they would silently un-choose the buyer or fake a send.
_DELIVERY_STATUSES = ("not_contacted", "queued", "sent", "delivered", "opened",
                      "failed", "blocked", "no_response")
# A selected buyer can still back out; that releases the selection explicitly.
_RELEASING = ("passed", "rejected")

REFUSALS = {
    "select_via_select_buyer":
        "Choose a buyer with Select this buyer, not by editing a response.",
    "selected_row_is_locked":
        "This buyer is the current selection. Select a different buyer, or "
        "record that they passed, before changing their status.",
    "buyer_opted_out":
        "That buyer has opted out, so they cannot be selected.",
    "buyer_inactive":
        "That buyer is marked inactive, so they cannot be selected.",
    "buyer_passed":
        "That buyer passed on this deal. Record a new response from them first.",
    "buyer_not_on_deal": "That buyer is not on this deal.",
}


def refusal(code):
    return code, REFUSALS[code]


def replaced_status(offer_amount):
    """Status a previously selected buyer returns to when someone else is chosen."""
    return REPLACED_WITH_OFFER if offer_amount not in (None, 0, "0", "0.00") \
        else REPLACED_NO_OFFER


def check_select(status, is_selected, do_not_contact, is_active):
    """-> (decision, code, label). decision: 'select' | 'noop' | 'refuse'.

    Order: opt-out and inactivity first (never reach contact decisions), then an
    already-current buyer is an idempotent no-op (retry must not add a second
    event or reset buyer_selected_at), then passed/rejected.
    """
    if do_not_contact:
        return ("refuse",) + refusal("buyer_opted_out")
    if not is_active:
        return ("refuse",) + refusal("buyer_inactive")
    if is_selected and status == "selected":
        return "noop", None, None
    if status in ("passed", "rejected"):
        return ("refuse",) + refusal("buyer_passed")
    return "select", None, None


def check_status_write(current_status, is_selected, new_status):
    """Gate for generic response/update status writes.

    -> (decision, code, label). decision: 'allow' | 'release' | 'refuse' | 'noop'
    | 'keep' (accepted, but the selected status is left as is).
    'release' = the selected buyer backed out: the caller must clear is_selected
    and the deal's assigned buyer, and log it.
    """
    if new_status is None or new_status == current_status:
        return "noop", None, None
    if new_status == "selected":
        return ("refuse",) + refusal("select_via_select_buyer")
    if is_selected or current_status == "selected":
        if new_status in _RELEASING:
            return "release", None, None
        if new_status in ("offer_submitted", "accepted"):
            # An offer on the selected buyer is fine; the status stays
            # "selected" so flag and status never disagree.
            return "keep", None, None
        if new_status in _DELIVERY_STATUSES or new_status in (
                "replied", "needs_info", "requested_info", "interested"):
            return ("refuse",) + refusal("selected_row_is_locked")
    return "allow", None, None


def offer_may_set_status(current_status, is_selected):
    """An offer amount alone must not demote a selected buyer to offer_submitted."""
    return not (is_selected or current_status == "selected")
