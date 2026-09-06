"""WHAT ONE OPPORTUNITY IS ACTUALLY WORTH, AND WHERE THAT NUMBER CAME FROM.

WHY THIS EXISTS
---------------
The pipeline projection reported $75,800 of implementation and $0 of recurring
revenue. Neither figure was a bug in the arithmetic; both were a bug in what
was being read.

`compensation.deal_economics` quoted every deal straight off the opportunity's
own package selection. Three things fall through that:

  A MONTH-TO-MONTH DEAL HAS NO CONTRACT TOTAL, so `quote()` correctly returns
  no recurring_contract_value for it - and the rollup then had nowhere to put
  its monthly rate. A deal at $597/month contributed its setup fee and nothing
  else. Since month-to-month is the DEFAULT billing option (normalize_option
  fails closed to it, deliberately), that silently erased the recurring side of
  most of the pipeline. This is the whole of the $0.

  AN OPPORTUNITY WITH NO PACKAGE quotes nothing at all: no package means no
  rate, no fee, no total. Older deals carrying only `deal_value` therefore
  contributed zero to every figure while still being counted in the deal count
  - present in the tally, absent from the money.

  A SENT PROPOSAL WAS NEVER CONSULTED. The document the customer is holding
  snapshots the package, the billing option, the term and the custom rate at
  the moment it was quoted, and the forecast ignored all four.

WHAT THIS MODULE DOES
---------------------
Resolves ONE opportunity to its commercial structure and says which source it
used, in the priority the owner specified:

  1. the current sent proposal's snapshot - what the customer was actually
     quoted, and the only one of these four that a customer has in writing,
  2. the per-deal custom rate on the opportunity,
  3. the opportunity's selected package and the catalogue,
  4. `deal_value`, as a LAST-RESORT ONE-TIME AMOUNT and nothing more.

It computes no pricing of its own. Every figure comes back through
`package_pricing.quote()`, which stays the single pricing authority; this
module only decides WHICH inputs to hand it.

WHAT IT REFUSES TO DO
---------------------
It never invents a term. A month-to-month deal reports an MRR and NO recurring
contract value, because there is no commitment to total up - twelve months of
assumed revenue is exactly the fiction the owner asked not to be told.

It never reports unknown as zero. A deal whose recurring terms cannot be proven
comes back `pricing_complete: False` with a reason, and the rollup holds it out
of the recurring totals instead of averaging it in at nothing.

A LEGACY `deal_value` NEVER BECOMES AN IMPLEMENTATION FEE. It is returned as
`legacy_one_time_value`, a separate field, precisely so that no commission rule
with a percent-of-setup basis can start paying against a number that was never
a setup fee. The pipeline may report it as one-time value; payroll may not see
it at all.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.models import Proposal
from app.models.sales_models import BrandPackage, Opportunity
from app.services import package_pricing as pp

# Where a deal's numbers came from. Reported on every row so that a figure
# somebody disagrees with can be traced without reading this file.
SOURCE_PROPOSAL = "proposal_snapshot"
SOURCE_CUSTOM   = "opportunity_custom_rate"
SOURCE_PACKAGE  = "package_catalogue"
SOURCE_LEGACY   = "legacy_deal_value"
SOURCE_NONE     = "none"

SOURCE_LABELS = {
    SOURCE_PROPOSAL: "Current proposal",
    SOURCE_CUSTOM:   "Custom deal rate",
    SOURCE_PACKAGE:  "Package catalogue",
    SOURCE_LEGACY:   "Legacy deal value",
    SOURCE_NONE:     "No pricing",
}

# The shape of the money, which is NOT the same question as where it came from.
STRUCTURE_TERM     = "fixed_term"       # committed months: has an RCV and a TCV
STRUCTURE_M2M      = "month_to_month"   # an MRR, and deliberately no total
STRUCTURE_ONE_TIME = "one_time_only"    # a fee, and no recurring rate exists
STRUCTURE_UNKNOWN  = "unknown"          # nothing could be established

STRUCTURE_LABELS = {
    STRUCTURE_TERM:     "Fixed term",
    STRUCTURE_M2M:      "Month-to-month",
    STRUCTURE_ONE_TIME: "One-time only",
    STRUCTURE_UNKNOWN:  "Unknown",
}


def _dec(v) -> Optional[Decimal]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _carries_pricing(holder) -> bool:
    """Does this row actually state a commercial structure?

    A proposal with no package and no custom rate quotes nothing, so it must not
    outrank an opportunity that does — otherwise attaching an empty draft to a
    priced deal would erase the deal's own pricing from the forecast.
    """
    if holder is None:
        return False
    if getattr(holder, "package_id", None) or getattr(holder, "selected_package_id", None):
        return True
    return _dec(getattr(holder, "custom_unit_price", None)) is not None


def current_proposal(db: Session, opp: Opportunity) -> Optional[Proposal]:
    """The proposal that represents this deal RIGHT NOW, or None.

    Deliberately narrow. A proposal counts only when it has been SENT — a draft
    is a document nobody has agreed to, and letting one govern the forecast
    would let a rep move the company's projected revenue by opening an editor.
    Superseded, soft-deleted and price-withholding versions are excluded too:
    the first two have been replaced, and the third states in as many words
    that it quotes no price.

    Newest version wins. `version` is the deal's own revision counter, so it
    orders revisions correctly even where two were sent the same second.
    """
    # `withhold_pricing` is safe to test with a plain `is_(False)`: the model
    # declares it NOT NULL, and auto_migrate adds it to existing databases as
    # "BOOLEAN DEFAULT FALSE NOT NULL", so no historical row can hold NULL and
    # be silently disqualified here.
    q = (db.query(Proposal)
         .filter(Proposal.opportunity_id == opp.id,
                 Proposal.deleted_at.is_(None),
                 Proposal.superseded_at.is_(None),
                 Proposal.sent_at.isnot(None),
                 Proposal.withhold_pricing.is_(False))
         .order_by(Proposal.version.desc(), Proposal.sent_at.desc()))
    for prop in q.all():
        if _carries_pricing(prop):
            return prop
    return None


def resolve(db: Session, opp: Opportunity) -> Dict[str, Any]:
    """This deal's commercial structure, and the evidence behind it.

    Returns Decimals (or None), never floats — this is money, and the caller
    decides how to present it. `None` throughout means NOT KNOWN and is never
    interchangeable with `Decimal("0")`, which would mean somebody decided the
    figure was nothing.
    """
    proposal = current_proposal(db, opp)
    custom_on_opp = pp.custom_rate(opp)

    if proposal is not None:
        holder = proposal
        source = SOURCE_PROPOSAL
        package_id = proposal.package_id
        custom = pp.custom_rate(proposal)
    elif custom_on_opp is not None:
        holder = opp
        source = SOURCE_CUSTOM
        package_id = opp.selected_package_id
        custom = custom_on_opp
    elif opp.selected_package_id:
        holder = opp
        source = SOURCE_PACKAGE
        package_id = opp.selected_package_id
        custom = None
    else:
        # Nothing authoritative. `deal_value` is all that is left, and it is a
        # one-time figure of unknown composition — reported, never promoted to
        # an implementation fee, and never used to imply recurring revenue.
        legacy = _dec(opp.deal_value)
        return _out(
            source=SOURCE_LEGACY if legacy is not None else SOURCE_NONE,
            structure=STRUCTURE_UNKNOWN,
            package=None, quote=None,
            implementation_fee=None, mrr=None, term_months=None,
            rcv=None, tcv=None, legacy_one_time_value=legacy,
            pricing_complete=False,
            incomplete_reason=(
                "This deal has no package, no custom rate and no sent proposal. "
                "Its recurring terms cannot be established, so they are excluded "
                "from the recurring totals rather than counted as zero."
                if legacy is not None else
                "This deal has no pricing of any kind — no package, no custom "
                "rate, no sent proposal and no deal value."),
            deal_kind_custom=False,
        )

    pkg = (db.query(BrandPackage).filter(BrandPackage.id == package_id).first()
           if package_id else None)

    # ONE pricing engine. The holder supplies the option and the term; the
    # opportunity always supplies the per-deal implementation-fee override,
    # because that override is a fact about the deal rather than about the
    # document that quoted it — which is exactly how the proposal router
    # already builds its own commercials.
    term_snapshot = getattr(holder, "contract_term_months", None) or None
    q = pp.quote(pkg, getattr(holder, "billing_option", None), opp=opp,
                 term_months=term_snapshot, custom=custom)

    setup = _dec(q.get("implementation_fee"))
    mrr = _dec(q.get("mrr"))
    term = q.get("term_months")
    rcv = _dec(q.get("recurring_contract_value"))
    tcv = _dec(q.get("total_contract_value"))

    if rcv is not None:
        structure = STRUCTURE_TERM
    elif mrr is not None:
        # A REAL month-to-month deal, not a gap in the data. It has a monthly
        # rate and no end date, so it has no contract total — and the rollup
        # reports the rate per month rather than pretending either that the
        # revenue is zero or that it runs for a year.
        structure = STRUCTURE_M2M
    elif setup is not None:
        structure = STRUCTURE_ONE_TIME
    else:
        structure = STRUCTURE_UNKNOWN

    complete = True
    reason = None
    if structure == STRUCTURE_UNKNOWN:
        complete = False
        reason = ("A package is selected but it carries neither an "
                  "implementation fee nor a monthly rate.")

    return _out(source=source, structure=structure, package=pkg, quote=q,
                implementation_fee=setup, mrr=mrr, term_months=term,
                rcv=rcv, tcv=tcv, legacy_one_time_value=None,
                pricing_complete=complete, incomplete_reason=reason,
                deal_kind_custom=bool(custom),
                proposal=proposal)


def _out(*, source, structure, package, quote, implementation_fee, mrr,
         term_months, rcv, tcv, legacy_one_time_value, pricing_complete,
         incomplete_reason, deal_kind_custom, proposal=None) -> Dict[str, Any]:
    return {
        "source": source,
        "source_label": SOURCE_LABELS.get(source, source),
        "proposal_id": getattr(proposal, "id", None),
        "proposal_number": getattr(proposal, "proposal_number", None),
        "structure": structure,
        "structure_label": STRUCTURE_LABELS.get(structure, structure),
        "package": package,
        "package_name": getattr(package, "name", None),
        "quote": quote,
        "implementation_fee": implementation_fee,
        "mrr": mrr,
        "term_months": term_months,
        "recurring_contract_value": rcv,
        "total_contract_value": tcv,
        # Deliberately its own field. See the module docstring: this must never
        # reach a percent-of-setup commission rule.
        "legacy_one_time_value": legacy_one_time_value,
        "pricing_complete": pricing_complete,
        "incomplete_reason": incomplete_reason,
        "is_custom_rate": deal_kind_custom,
    }


def one_time_value(res: Dict[str, Any]) -> Optional[Decimal]:
    """What this deal bills once, for a PIPELINE total.

    The resolved implementation fee where there is one, and otherwise the
    legacy figure — which is reported here and nowhere else, alongside the
    `pricing_complete: False` that says why it is being trusted this far and
    no further.
    """
    if res["implementation_fee"] is not None:
        return res["implementation_fee"]
    return res["legacy_one_time_value"]


def fixed_contract_value(res: Dict[str, Any]) -> Optional[Decimal]:
    """Setup plus committed recurring: the whole of what is contractually owed.

    None for a deal with nothing to total. A month-to-month deal's setup fee IS
    included — that part is committed — while its monthly rate is not, because
    no number of months has been agreed.
    """
    one_time = one_time_value(res)
    rcv = res["recurring_contract_value"]
    if one_time is None and rcv is None:
        return None
    return (one_time or Decimal("0")) + (rcv or Decimal("0"))
