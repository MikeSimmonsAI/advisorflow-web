"""THE FORECLOSURE WORK-PRIORITY POLICY - one rule, used by every screen.

Owner requirement (2026-10-26, docs/wholesale/FORECLOSURE_PRIORITY_POLICY.md):
every verified imminent foreclosure auction is worked first, whatever its
investment evidence score, and however incomplete its money, owner or contact
data. Auction urgency is not profitability; unknown value is not a bad deal.

Four separate answers, never folded into one another:

    AUCTION URGENCY      how soon a sale is scheduled, and how sure we are
        CRITICAL            verified sale within 14 calendar days (Central Time)
        UPCOMING            verified sale more than 14 days away
        URGENT_VERIFY       a sale within 14 days that is claimed, conflicting or
                            not found on the official record - a person verifies
        VERIFY              the same, further out
        POSTPONED / CANCELED  only as recorded with evidence (or posted by the
                            official source)
        OUTCOME_UNVERIFIED  the date passed and nobody verified what happened
        COMPLETED           sold / reverted, recorded with evidence
        NONE                no sale on record
    DEAL POTENTIAL       opportunity.deal_potential - supported numbers only
    DATA CONFIDENCE      opportunity.data_confidence_measure
    WORK PRIORITY        the order a person works things in (below)

WORK PRIORITY
    CRITICAL - ACT NOW    CRITICAL auction urgency (unless closed / do-not-contact)
    REVIEW - URGENT       URGENT_VERIFY: an imminent notice a person must check
    HIGH - WORK NEXT      a live seller conversation, an UPCOMING verified sale,
                          or supported HIGH / MEDIUM deal potential
    NORMAL                everything else that is open
    REVIEW REQUIRED       a notice to verify later, an outcome nobody checked
    CLOSED / INACTIVE     canceled or completed sale with nothing else open,
                          owner said no / do not contact, deal closed

The investment evidence score NEVER lowers a work priority. It only breaks
ties inside the same priority and the same sale date.

Two kinds of auction are kept apart: MORTGAGE (trustee / HOA / judicial
notices - Texas first-Tuesday trustee sales) and TAX (a delinquent-tax sale
posted by the county's tax attorney). Each says which it is.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

CRITICAL_DAYS = 14
TZ = "America/Chicago"

# auction urgency levels
A_CRITICAL, A_UPCOMING, A_URGENT_VERIFY, A_VERIFY = "CRITICAL", "UPCOMING", "URGENT_VERIFY", "VERIFY"
A_POSTPONED, A_CANCELED, A_OUTCOME, A_COMPLETED, A_NONE = (
    "POSTPONED", "CANCELED", "OUTCOME_UNVERIFIED", "COMPLETED", "NONE")
URGENCY_ORDER = {A_CRITICAL: 0, A_URGENT_VERIFY: 1, A_UPCOMING: 2, A_VERIFY: 3, A_OUTCOME: 4, A_POSTPONED: 5,
                 A_NONE: 6, A_CANCELED: 7, A_COMPLETED: 7}

# work priority levels
W_CRITICAL, W_REVIEW_URGENT, W_HIGH, W_NORMAL, W_REVIEW, W_CLOSED = (
    "CRITICAL", "REVIEW_URGENT", "HIGH", "NORMAL", "REVIEW", "CLOSED")
WORK_LABELS = {W_CRITICAL: "CRITICAL — ACT NOW", W_REVIEW_URGENT: "REVIEW REQUIRED — URGENT",
               W_HIGH: "HIGH — WORK NEXT", W_NORMAL: "NORMAL", W_REVIEW: "REVIEW REQUIRED",
               W_CLOSED: "CLOSED / INACTIVE"}
WORK_ORDER = {W_CRITICAL: 0, W_REVIEW_URGENT: 1, W_HIGH: 2, W_NORMAL: 3, W_REVIEW: 4, W_CLOSED: 5}
KIND_LABELS = {"mortgage": "Foreclosure auction", "tax": "Tax sale"}

ACTIVE_STATES = ("RESPONDED", "HUMAN_HANDOFF", "FOLLOW_UP", "APPOINTMENT", "ANALYSIS", "OFFER")
CLOSED_STATES = ("NOT_INTERESTED", "DO_NOT_CONTACT", "CLOSED")


def today_ct() -> date:
    """Today in Texas (Central Time) - the calendar the sales run on. The
    servers run on UTC; after 7 PM Central that is already tomorrow there."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(TZ)).date()


def _day(v) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


# notice statuses from the Top 100 view and auction-event statuses -> one vocabulary
_FROM_NOTICE = {"VERIFIED NOTICE": "notice_verified", "CLAIMED": "claimed", "NO NOTICE FOUND": "not_found",
                "CONFLICT": "conflict", "POSTPONED": "postponed", "CANCELED": "canceled",
                "RESOLVED": "reported_resolved", "SALE PASSED": "outcome_unverified"}


def auction_urgency(status: Optional[str], sale_date=None, *, kind: str = "mortgage",
                    today: Optional[date] = None) -> Dict[str, Any]:
    """`status`: an auction-event status (foreclosure_cycles.STATUS keys), a
    Top-100 notice status ("VERIFIED NOTICE" ...) or "tax_sale_posted" (a tax
    sale on the tax attorney's official sale list)."""
    today = today or today_ct()
    st = _FROM_NOTICE.get(status or "", status or "")
    d = _day(sale_date)
    days = (d - today).days if d else None
    when = ("%s %d, %d" % (d.strftime("%b"), d.day, d.year)).upper() if d else "DATE NOT READ"
    kind_label = KIND_LABELS.get(kind, kind)

    def out(level, label, reason):
        return {"level": level, "label": label, "reason": reason, "days": days,
                "sale_date": d.isoformat() if d else None, "kind": kind, "kind_label": kind_label,
                "status": st or None, "verified": st in ("notice_verified", "tax_sale_posted"),
                "urgent": level in (A_CRITICAL, A_URGENT_VERIFY)}
    if st in ("notice_verified", "tax_sale_posted"):
        src = "verified clerk notice" if st == "notice_verified" else "the county tax attorney's posted sale list"
        if days is None:
            return out(A_UPCOMING, "UPCOMING — DATE NOT READ", "%s on %s; the date could not be read" % (kind_label, src))
        if days < 0:
            return out(A_OUTCOME, "OUTCOME UNVERIFIED",
                       "The %s %s date has passed; sold, canceled or postponed is not verified" % (when, kind_label.lower()))
        if days <= CRITICAL_DAYS:
            return out(A_CRITICAL, "CRITICAL — %d DAY%s" % (days, "" if days == 1 else "S"),
                       "%s %s, %d day%s away (%s)" % (kind_label, when, days, "" if days == 1 else "s", src))
        return out(A_UPCOMING, "UPCOMING — %d DAYS" % days, "%s %s (%s)" % (kind_label, when, src))
    if st in ("conflict", "claimed", "not_found", "reported_resolved", "awaiting_notice", "no_longer_on_record"):
        why = {"conflict": "the official notice and the list disagree",
               "claimed": "only an imported list gives this date; no official notice confirms it yet",
               "not_found": "the list gives this date but the official record read shows no notice for it",
               "reported_resolved": "reported resolved; not verified",
               "awaiting_notice": "carried forward; no new notice yet",
               "no_longer_on_record": "the notice is no longer on the official record"}[st]
        soon = days is not None and 0 <= days <= CRITICAL_DAYS
        if days is not None and days < 0:
            return out(A_OUTCOME, "OUTCOME UNVERIFIED", "The claimed %s date has passed; %s" % (when, why))
        return out(A_URGENT_VERIFY if soon else A_VERIFY,
                   ("URGENT — VERIFY NOTICE (%d DAYS)" % days) if soon else "VERIFY NOTICE",
                   "%s %s: %s" % (kind_label, when, why))
    if st == "postponed":
        return out(A_POSTPONED, "POSTPONED", "Recorded as postponed with evidence; no new date verified yet")
    if st == "canceled":
        return out(A_CANCELED, "CANCELED", "Recorded as canceled with evidence")
    if st in ("sold_at_auction", "reverted_to_lender"):
        return out(A_COMPLETED, "AUCTION HELD — %s" % st.replace("_", " ").upper(), "Outcome recorded with evidence")
    if st == "outcome_unverified":
        return out(A_OUTCOME, "OUTCOME UNVERIFIED", "The %s date has passed; what happened is not verified" % when)
    return out(A_NONE, "NO SALE ON RECORD", "No foreclosure or tax sale on record")


def controlling(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Of a property's auctions (mortgage, tax), the one that decides its
    urgency: the most urgent level, then the soonest date."""
    if not candidates:
        return auction_urgency(None)
    return sorted(candidates, key=lambda a: (URGENCY_ORDER.get(a["level"], 9),
                                             a["days"] if a.get("days") is not None and a["days"] >= 0 else 99999))[0]


def property_auctions(fc: Optional[Dict[str, Any]], tax: Optional[Dict[str, Any]], event: Optional[Dict[str, Any]],
                      *, today: Optional[date] = None) -> Dict[str, Any]:
    """The controlling auction for one property from what the engine already
    reads: the mortgage notice status (cohort.foreclosure_status), this month's
    auction event if it is on a Foreclosure Command Center month (a person's
    recorded outcome wins), and a posted tax sale (cohort tax status)."""
    today = today or today_ct()
    cands = []
    if event and (event.get("status") or event.get("sale_date")):
        cands.append(auction_urgency(event.get("status"), event.get("sale_date"), kind="mortgage", today=today))
    elif fc and fc.get("status") not in (None, "NO EVIDENCE"):
        cands.append(auction_urgency(fc.get("status"), fc.get("sale_date") or fc.get("claimed_sale_date"),
                                     kind="mortgage", today=today))
    if tax and tax.get("status") == "TAX SALE" and tax.get("sale_date"):
        cands.append(auction_urgency("tax_sale_posted", tax["sale_date"], kind="tax", today=today))
    return controlling(cands)


def work_priority(au: Dict[str, Any], *, workflow_state: Optional[str], contact_ready: bool,
                  owner_review: bool, deal_level: Optional[str], score: Optional[int] = None,
                  prop_id: str = "") -> Dict[str, Any]:
    """The operational answer. `score` (investment evidence) only orders ties."""
    lvl = au.get("level")
    wf = workflow_state or ""
    engaged = wf in ACTIVE_STATES
    if wf in CLOSED_STATES:
        w, why = W_CLOSED, {"NOT_INTERESTED": "The owner said no / not the owner / already sold",
                            "DO_NOT_CONTACT": "The owner asked not to be contacted",
                            "CLOSED": "Deal closed"}[wf]
    elif lvl == A_CRITICAL:
        w, why = W_CRITICAL, au["reason"]
    elif lvl == A_URGENT_VERIFY:
        w, why = W_REVIEW_URGENT, au["reason"]
    elif engaged:
        w, why = W_HIGH, "A seller conversation is under way"
    elif lvl == A_UPCOMING:
        w, why = W_HIGH, au["reason"]
    elif deal_level in ("HIGH", "MEDIUM"):
        w, why = W_HIGH, "Supported numbers show %s deal potential" % deal_level.lower()
    elif lvl in (A_VERIFY, A_OUTCOME):
        w, why = W_REVIEW, au["reason"]
    elif lvl in (A_CANCELED, A_COMPLETED):
        w, why = W_CLOSED, au["reason"]
    else:
        w, why = W_NORMAL, "No deadline on record"
    if w in (W_CRITICAL, W_REVIEW_URGENT, W_HIGH):
        if lvl == A_URGENT_VERIFY:
            action = "Verify the notice"
        elif engaged:
            action = "Work the seller conversation"
        elif owner_review:
            action = "Verify owner"
        elif contact_ready:
            action = "Call the owner (approved calling only)"
        else:
            action = "Find contact"
    elif w == W_REVIEW:
        action = "Verify the notice / record the outcome"
    elif w == W_CLOSED:
        action = "Nothing — closed"
    else:
        action = "Research when time allows"
    # deterministic order: priority, then days to the sale, then who is ready,
    # then the investment evidence score, then id
    sub = 0 if engaged else 1 if contact_ready else 2
    days = au.get("days") if au.get("days") is not None and au["days"] >= 0 else 9999
    rank = WORK_ORDER[w] * 100000 + min(days, 9999) * 10 + sub
    return {"level": w, "label": WORK_LABELS[w], "reason": why, "next_action": action, "rank": rank,
            "sort_key": [rank, -(score or 0), prop_id]}


def summary(au: Dict[str, Any], wp: Dict[str, Any]) -> Dict[str, Any]:
    """The row a list shows: AUCTION / URGENCY / WORK PRIORITY / NEXT ACTION."""
    return {"auction_date": au.get("sale_date"), "auction_kind": au.get("kind_label") if au.get("level") != A_NONE
            else None, "auction_verified": au.get("verified"), "urgency": au.get("level"),
            "urgency_label": au.get("label"), "work_priority": wp["level"], "work_priority_label": wp["label"],
            "work_rank": wp["rank"], "next_action": wp["next_action"], "why": wp["reason"]}
