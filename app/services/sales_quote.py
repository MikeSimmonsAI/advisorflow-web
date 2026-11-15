"""The Salesperson Deal Builder, inside a customer's deal.

ONE QUOTE PER DEAL, ONE SET OF NUMBERS
--------------------------------------
The builder's controls (package, add-ons, Custom SaaS setup, monthly override
and restore) are the same page the standalone Salesperson Deal Builder uses.
In a deal it saves what was built here, on the deal, so it can be reloaded,
reopened and revised.

Saving does three things and nothing else:
  1. stores the quote (customer prices only - no commission is ever stored);
  2. sets the deal's per-deal one-time figure (`Opportunity.implementation_fee`),
     which the existing proposal pricing already follows while a proposal is
     still editable;
  3. if the deal's current proposal is still editable (draft / internal review
     / ready), records that this proposal quotes this revision. A proposal that
     was sent, viewed, accepted or declined is never touched: a new version
     picks up the latest quote.

The proposal's customer-facing Investment section is rendered from the quote
its version recorded, so the builder and the proposal show the same figures.
Commission is shown only by `/deal-builder/commission`, which decides the view
from the caller's seat; customer output carries no compensation at all.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import PROPOSAL_EDITABLE_STATUSES, Proposal
from app.models.sales_quote_models import SalesQuoteDraft, SalesQuoteSnapshot
from app.services import deal_builder_commission as dbc

CENT = Decimal("0.01")
MAX_MONEY = Decimal("10000000")
MAX_LINES = 80
MAX_STATE_BYTES = 60_000


class QuoteError(ValueError):
    pass


def _money(v, field: str) -> Decimal:
    try:
        d = Decimal(str(v if v not in (None, "") else 0))
    except (InvalidOperation, ValueError):
        raise QuoteError("%s must be a number." % field)
    if not d.is_finite() or d < 0:
        raise QuoteError("%s cannot be negative." % field)
    if d > MAX_MONEY:
        raise QuoteError("%s is too large." % field)
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def _text(v, n: int) -> str:
    return str(v or "").strip()[:n]


def clean(body: Dict[str, Any]) -> Dict[str, Any]:
    """Validate what the builder sent. Customer prices only."""
    plan_key = _text(body.get("plan_key"), 40)
    if plan_key not in dbc.PLANS:
        raise QuoteError("Choose a package.")
    setup = _money(body.get("setup_total"), "One-time total")
    monthly = _money(body.get("monthly_total"), "Monthly total")
    addon = _money(body.get("addon_setup"), "Add-on setup")
    if addon > setup:
        raise QuoteError("Add-on setup cannot be more than the one-time total.")
    lines_in = body.get("lines") or []
    if not isinstance(lines_in, list) or len(lines_in) > MAX_LINES:
        raise QuoteError("Too many quote lines.")
    lines = []
    for ln in lines_in:
        if not isinstance(ln, dict):
            continue
        name = _text(ln.get("name"), 120)
        if not name:
            continue
        lines.append({"name": name,
                      "setup": float(_money(ln.get("setup"), "Line setup")),
                      "monthly": float(_money(ln.get("monthly"), "Line monthly")),
                      "usage": float(_money(ln.get("usage"), "Line usage")),
                      "note": _text(ln.get("note"), 80) or None})
    state = body.get("state") or {}
    if not isinstance(state, dict):
        raise QuoteError("The builder state is not readable.")
    state_json = json.dumps(state, separators=(",", ":"))
    if len(state_json) > MAX_STATE_BYTES:
        raise QuoteError("The quote is too large to save.")
    plan_line = body.get("plan_line") or {}
    pending = [_text(p, 120) for p in (body.get("pending") or [])][:30]
    # Never keep anything that looks like compensation, whatever was sent.
    for k in list(state.keys()):
        if "commission" in k.lower() or k.lower() in ("comm", "earnings"):
            state.pop(k, None)
    return {
        "plan_key": plan_key, "plan_name": dbc.PLANS[plan_key]["name"],
        "setup_total": setup, "monthly_total": monthly, "addon_setup": addon,
        "monthly_override": bool(body.get("monthly_override")),
        "plan_line": {"setup": float(_money(plan_line.get("setup"), "Package setup")),
                      "monthly": float(_money(plan_line.get("monthly"), "Package monthly"))},
        "lines": lines, "pending": [p for p in pending if p], "state": state,
    }


def draft_for(db: Session, opp_id: str) -> Optional[SalesQuoteDraft]:
    return db.query(SalesQuoteDraft).filter(SalesQuoteDraft.opportunity_id == opp_id).first()


def draft_out(d: Optional[SalesQuoteDraft]) -> Optional[Dict[str, Any]]:
    if d is None:
        return None
    data = json.loads(d.data_json or "{}")
    return {"revision": d.revision, "plan_key": d.plan_key, "plan_name": data.get("plan_name"),
            "setup_total": float(d.setup_total), "monthly_total": float(d.monthly_total),
            "addon_setup": float(d.addon_setup or 0),
            "monthly_override": d.monthly_override == "yes",
            "plan_line": data.get("plan_line"), "lines": data.get("lines", []),
            "pending": data.get("pending", []), "state": data.get("state", {}),
            "rules_version": d.rules_version,
            "updated_at": d.updated_at.isoformat() + "Z" if d.updated_at else None,
            "updated_by": d.updated_by}


def save(db: Session, opp, user, body: Dict[str, Any]) -> Dict[str, Any]:
    c = clean(body)
    now = datetime.utcnow()
    expected = body.get("expected_revision")
    d = draft_for(db, opp.id)
    if d is not None and expected is not None and int(expected) != d.revision:
        raise QuoteError("conflict")
    data = {k: c[k] for k in ("plan_name", "plan_line", "lines", "pending", "state")}
    if d is None:
        d = SalesQuoteDraft(opportunity_id=opp.id, revision=1, created_at=now)
        db.add(d)
    else:
        d.revision = (d.revision or 0) + 1
    d.plan_key = c["plan_key"]
    d.setup_total = c["setup_total"]
    d.monthly_total = c["monthly_total"]
    d.addon_setup = c["addon_setup"]
    d.monthly_override = "yes" if c["monthly_override"] else None
    d.data_json = json.dumps(data, separators=(",", ":"))
    d.rules_version = dbc.RULES_VERSION
    d.updated_by = user.id
    d.updated_at = now
    # The deal's own one-time figure: the proposal already follows it while
    # it is editable.
    opp.implementation_fee = c["setup_total"]
    db.flush()

    from app.services import proposal_service as ps
    prop = ps.current_proposal(db, opp.id)
    applied = None
    held = None
    if prop is not None:
        if prop.sales_status in PROPOSAL_EDITABLE_STATUSES:
            snapshot(db, prop, d)
            ps.apply_pricing(db, prop, user)
            if not prop.package_id and not ps._dec(prop.adjustment):
                # No catalogue package on this proposal: its one-time figure
                # is the quote's, so totals elsewhere read the same number.
                prop.base_amount = c["setup_total"]
                prop.final_amount = c["setup_total"]
            applied = {"proposal_number": prop.proposal_number, "version": prop.version}
        else:
            held = {"proposal_number": prop.proposal_number, "version": prop.version,
                    "status": prop.sales_status}
    return {"draft": draft_out(d), "applied_to_proposal": applied, "proposal_not_changed": held}


def snapshot(db: Session, prop: Proposal, d: Optional[SalesQuoteDraft] = None) -> Optional[SalesQuoteSnapshot]:
    """Record which quote this proposal version quotes. Editable proposals only."""
    if prop is None or not prop.opportunity_id:
        return None
    if prop.sales_status not in PROPOSAL_EDITABLE_STATUSES and prop.sales_status is not None:
        return None
    d = d or draft_for(db, prop.opportunity_id)
    if d is None:
        return None
    s = db.query(SalesQuoteSnapshot).filter(SalesQuoteSnapshot.proposal_id == prop.id).first()
    payload = json.dumps({**json.loads(d.data_json or "{}"),
                          "plan_key": d.plan_key, "setup_total": float(d.setup_total),
                          "monthly_total": float(d.monthly_total)}, separators=(",", ":"))
    if s is None:
        s = SalesQuoteSnapshot(proposal_id=prop.id, opportunity_id=prop.opportunity_id,
                               draft_revision=d.revision, data_json=payload)
        db.add(s)
    else:
        s.draft_revision = d.revision
        s.data_json = payload
        s.updated_at = datetime.utcnow()
    db.flush()
    return s


def snapshot_for(db: Session, prop: Proposal) -> Optional[Dict[str, Any]]:
    s = db.query(SalesQuoteSnapshot).filter(SalesQuoteSnapshot.proposal_id == prop.id).first()
    if s is None:
        return None
    data = json.loads(s.data_json or "{}")
    data["revision"] = s.draft_revision
    return data


def _fmt(v: float) -> str:
    d = Decimal(str(v))
    return "$" + ("{:,.0f}".format(d) if d == d.to_integral_value() else "{:,.2f}".format(d))


def investment_markdown(data: Dict[str, Any]) -> str:
    """The customer's Investment section for a builder quote. Prices only."""
    plan = data.get("plan_name") or "Platform"
    pl = data.get("plan_line") or {}
    rows = []
    if pl.get("setup") or pl.get("monthly"):
        rows.append((plan, _fmt(pl.get("setup", 0)) + " one-time",
                     (_fmt(pl.get("monthly", 0)) + "/month") if pl.get("monthly") else "—"))
    for ln in data.get("lines") or []:
        rows.append((ln["name"],
                     (_fmt(ln.get("setup", 0)) + " one-time") if ln.get("setup") else (ln.get("note") or "—"),
                     (_fmt(ln.get("monthly", 0)) + "/month") if ln.get("monthly") else "—"))
    out = ["## Investment", "", "| Item | One-time | Monthly |", "| --- | --- | --- |"]
    for a, b, c in rows:
        out.append("| %s | %s | %s |" % (a.replace("|", "/"), b, c))
    out += ["", "**First payment — one-time setup and services: %s.**" % _fmt(data.get("setup_total", 0)),
            "",
            "Monthly service: **%s/month**, billed after setup. Usage, renewals and pass-through "
            "costs are separate." % _fmt(data.get("monthly_total", 0))]
    pending = data.get("pending") or []
    if pending:
        out += ["", "Still to be quoted: %s." % ", ".join(pending)]
    return "\n".join(out)
