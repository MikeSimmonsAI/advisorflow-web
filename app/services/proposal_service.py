"""
The sales proposal engine — Checkpoint 4.

BUILT ON THE EXISTING PORTAL, NOT BESIDE IT. `proposals`, `proposal_blocks`,
`proposal_tokens`, `proposal_views` and `proposal_files` already existed and
already worked: block types, magic-link tokens with expiry and revocation, view
analytics, file storage, branded email. All of it is reused. What Checkpoint 4
adds is the sales half — an opportunity link, structured content, versioning,
pricing authority and a lifecycle — plus the one schema change that made reuse
possible at all: `organization_id` is now nullable, because a pre-sale proposal
must not point at a customer tenant that will not exist until the deal is Won.

PREFILL IS THE POINT
--------------------
A salesperson who has to retype the company name, the package, the price and
the discovery answers into a proposal will paste last month's Word document
instead. Everything AdvisorFlow already knows is carried forward by
`create_proposal`, and the rep edits prose, not data entry.

VERSIONING NEVER DESTROYS HISTORY
---------------------------------
Editing a proposal the customer has already seen creates a NEW ROW at version
n+1 and marks the old one SUPERSEDED. The customer's original link keeps
resolving to what they were actually sent, which is the only defensible answer
to "but the price said $4,995 when I read it".
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import (
    Proposal, ProposalBlock, ProposalToken, PortalEvent,
    PROP_DRAFT, PROP_INTERNAL_REVIEW, PROP_READY, PROP_SENT, PROP_VIEWED,
    PROP_ACCEPTED, PROP_DECLINED, PROP_CHANGE_REQUESTED, PROP_EXPIRED,
    PROP_SUPERSEDED, PROPOSAL_STATUSES, PROPOSAL_OPEN_STATUSES,
    PROPOSAL_EDITABLE_STATUSES,
    PORTAL_OPENED, PORTAL_PROPOSAL_VIEWED, PORTAL_ACCEPTED, PORTAL_DECLINED,
    PORTAL_CHANGE_REQUESTED,
)
from app.models.sales_models import (
    Opportunity, OpportunityEvent, BrandSalesOrg, BrandPackage, DiscoveryRecord,
)
from app.services import package_pricing as _pp

log = logging.getLogger(__name__)

# How long a proposal stands unless someone says otherwise. Long enough not to
# pressure a real buying committee, short enough to create a reason to follow up.
DEFAULT_VALID_DAYS = 30


def _dec(value) -> Optional[Decimal]:
    """Money, as Decimal. Never float — 0.1 + 0.2 problems in a price a customer
    signs are not acceptable."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


# ── identity ────────────────────────────────────────────────────────────────

def _brand_prefix(db: Session, brand_sales_org_id: str) -> str:
    """A short brand prefix for proposal numbers: EV-1007, BB-1007.

    Derived from the brand's slug rather than hardcoded, so the second brand
    needs no code change. Falls back to 'PR' rather than to 'EV' — a BookaBoost
    proposal numbered EV-1007 is a white-label leak, and an ugly number is a far
    smaller problem than the wrong brand's initials on a customer document.
    """
    try:
        org = db.query(BrandSalesOrg).filter(BrandSalesOrg.id == brand_sales_org_id).first()
        slug = (org.slug or "") if org else ""
        letters = [p[0] for p in slug.replace("_", "-").split("-") if p]
        prefix = "".join(letters)[:3].upper()
        return prefix or "PR"
    except Exception:
        log.exception("could not derive proposal prefix for brand %s", brand_sales_org_id)
        return "PR"


def next_proposal_number(db: Session, brand_sales_org_id: str) -> str:
    """The next number for this brand. Sequential PER BRAND, starting at 1001.

    Counts DISTINCT proposal numbers, not rows: version 2 of EV-1007 is still
    EV-1007, and counting rows would skip a number every time anyone revised
    anything.
    """
    prefix = _brand_prefix(db, brand_sales_org_id)
    try:
        rows = (db.query(Proposal.proposal_number)
                .filter(Proposal.brand_sales_org_id == brand_sales_org_id,
                        Proposal.proposal_number.isnot(None))
                .distinct().all())
    except Exception:
        log.exception("could not read existing proposal numbers")
        rows = []
    highest = 1000
    for (num,) in rows:
        try:
            tail = int(str(num).rsplit("-", 1)[-1])
            highest = max(highest, tail)
        except (ValueError, IndexError):
            # A hand-edited number that does not parse must not stall the
            # sequence — it is skipped, not treated as zero.
            continue
    return "%s-%d" % (prefix, highest + 1)


# ── prefill ─────────────────────────────────────────────────────────────────

def _package_for(db: Session, opp: Opportunity) -> Optional[BrandPackage]:
    """The package this deal is actually on. `selected_package_id` is a decision;
    `package_interest_id` is a guess — prefer the decision."""
    pid = opp.selected_package_id or opp.package_interest_id
    if not pid:
        return None
    return db.query(BrandPackage).filter(BrandPackage.id == pid).first()


def prefill_from_opportunity(db: Session, opp: Opportunity) -> dict:
    """Everything AdvisorFlow already knows, shaped for a new proposal.

    Discovery answers become PROSE, not a dumped Q&A list. A proposal that reads
    like a form the customer filled in is worse than no proposal — the point is
    to show we listened, in our words.

    Nothing internal is carried across. `demo_notes` and `opportunity_notes` are
    explicitly excluded: they are the rep's private working notes, and this
    output goes into a document the customer reads.
    """
    disc = (db.query(DiscoveryRecord)
            .filter(DiscoveryRecord.opportunity_id == opp.id).first())
    pkg = _package_for(db, opp)

    def d(field):
        return (getattr(disc, field, None) or "").strip() if disc else ""

    company = opp.company_name or "your business"

    business_need = ""
    if d("bottlenecks") or d("current_process"):
        parts = []
        if d("current_process"):
            parts.append("Today, %s handles this as follows: %s"
                         % (company, d("current_process")))
        if d("bottlenecks"):
            parts.append("The constraints you described: %s" % d("bottlenecks"))
        if d("current_tools"):
            parts.append("Current systems: %s" % d("current_tools"))
        business_need = "\n\n".join(parts)

    objectives = ""
    if d("business_goals") or d("desired_outcome"):
        parts = []
        if d("business_goals"):
            parts.append(d("business_goals"))
        if d("desired_outcome"):
            parts.append("Desired outcome: %s" % d("desired_outcome"))
        objectives = "\n\n".join(parts)

    recommended = ""
    if pkg:
        recommended = "%s%s" % (pkg.name,
                                (" — " + pkg.description) if pkg.description else "")
    if d("automation_opportunities"):
        recommended = (recommended + "\n\n" if recommended else "") + \
            "Automation opportunities identified during discovery: %s" \
            % d("automation_opportunities")

    scope = ""
    if d("required_integrations"):
        scope = "Integrations in scope: %s" % d("required_integrations")

    summary = ""
    if company and (objectives or business_need):
        summary = ("Prepared for %s. This proposal sets out what we heard during "
                   "discovery, what we recommend, and what it costs." % company)

    # THIS DEAL'S one-time implementation figure, not the catalogue's. The
    # prefill is where a proposal's numbers actually come from - apply_pricing
    # only re-rates when the package CHANGES, so a fix there alone would never
    # run for a proposal created against the package already selected.
    base = _pp.implementation_fee(pkg, opp)
    return {
        "title": "%s — Proposal" % company,
        "subtitle": "Prepared for %s" % company,
        "client_name": opp.contact_name,
        "client_email": opp.email,
        "client_company": opp.company_name,
        "package_id": pkg.id if pkg else None,
        "base_amount": base,
        "final_amount": base,
        # Inherited from the deal so a proposal never quotes month-to-month for
        # a customer who agreed to a term - and vice versa.
        "billing_option": _pp.normalize_option(opp.billing_option, pkg) if pkg else None,
        "contract_term_months": (opp.contract_term_months
                                 if (pkg and _pp.normalize_option(opp.billing_option, pkg)
                                     == _pp.BILLING_TERM_AGREEMENT) else None),
        "currency": (pkg.currency if pkg and pkg.currency else "USD"),
        "executive_summary": summary,
        "business_need": business_need,
        "objectives": objectives,
        "recommended_solution": recommended,
        "scope": scope,
        "deliverables": "",
        "implementation_plan": "",
        "terms": "",
        # Carried through so the builder can offer it as a portal block with one
        # click instead of the rep hunting for the URL.
        "demo_url": opp.demo_url,
    }


# ── creation and versioning ─────────────────────────────────────────────────

def current_proposal(db: Session, opportunity_id: str) -> Optional[Proposal]:
    """The live proposal on a deal — highest version that is not superseded."""
    return (db.query(Proposal)
            .filter(Proposal.opportunity_id == opportunity_id,
                    Proposal.deleted_at.is_(None),
                    Proposal.sales_status != PROP_SUPERSEDED)
            .order_by(Proposal.version.desc(), Proposal.created_at.desc())
            .first())


def proposal_history(db: Session, opportunity_id: str):
    """Every version, newest first. Superseded rows included — that is the audit
    trail, and hiding them would defeat the point of keeping them."""
    return (db.query(Proposal)
            .filter(Proposal.opportunity_id == opportunity_id,
                    Proposal.deleted_at.is_(None))
            .order_by(Proposal.version.desc(), Proposal.created_at.desc())
            .all())


def create_proposal(db: Session, opp: Opportunity, user, now=None,
                    overrides: dict = None) -> Proposal:
    """A new version-1 proposal, prefilled from the opportunity.

    Does NOT commit — the caller owns the transaction, so a proposal and its
    timeline event land together or not at all.
    """
    now = now or datetime.utcnow()
    data = prefill_from_opportunity(db, opp)
    data.pop("demo_url", None)
    if overrides:
        data.update({k: v for k, v in overrides.items() if v is not None})

    prop = Proposal(
        # organization_id stays NULL. A pre-sale proposal belongs to the BRAND
        # and the deal, never to a customer tenant that does not exist yet.
        organization_id=None,
        brand_sales_org_id=opp.brand_sales_org_id,
        opportunity_id=opp.id,
        created_by_id=user.id,
        proposal_number=next_proposal_number(db, opp.brand_sales_org_id),
        version=1,
        sales_status=PROP_DRAFT,
        # The legacy customer-portal column. Kept at 'draft' until publish so
        # the existing portal resolver, which requires status == 'published',
        # cannot serve an unfinished sales proposal.
        status="draft",
        expires_at=now + timedelta(days=DEFAULT_VALID_DAYS),
        title=data.get("title") or "Proposal",
        subtitle=data.get("subtitle"),
        client_name=data.get("client_name"),
        client_email=data.get("client_email"),
        client_company=data.get("client_company"),
        package_id=data.get("package_id"),
        base_amount=_dec(data.get("base_amount")),
        final_amount=_dec(data.get("final_amount")),
        billing_option=data.get("billing_option"),
        contract_term_months=data.get("contract_term_months"),
        currency=data.get("currency") or "USD",
        executive_summary=data.get("executive_summary"),
        business_need=data.get("business_need"),
        objectives=data.get("objectives"),
        recommended_solution=data.get("recommended_solution"),
        scope=data.get("scope"),
        deliverables=data.get("deliverables"),
        implementation_plan=data.get("implementation_plan"),
        terms=data.get("terms"),
        created_at=now,
    )
    db.add(prop)
    db.flush()
    _event(db, opp.id, "proposal_created",
           "Proposal %s created" % prop.proposal_number,
           "Version 1", user.id, now)
    return prop


# Fields copied forward into a new version. Deliberately explicit rather than a
# loop over the model's columns: a future column should have to be considered
# here, not silently inherited into a customer-facing document.
_VERSIONED_FIELDS = (
    "title", "subtitle", "client_name", "client_email", "client_company",
    "package_id", "base_amount", "adjustment", "final_amount", "currency",
    "billing_option", "contract_term_months",
    "executive_summary", "business_need", "objectives", "recommended_solution",
    "scope", "deliverables", "implementation_plan", "terms",
    "price_override_by", "price_override_at", "price_override_reason",
)


def create_version(db: Session, prev: Proposal, user, now=None,
                   overrides: dict = None) -> Proposal:
    """Version n+1. The previous row becomes SUPERSEDED but is never touched
    otherwise, and never deleted.

    This is what "customer asked for a change" produces. The old version keeps
    its own tokens, its own view history and its own numbers, so the record of
    what the customer actually saw at the time survives intact.
    """
    now = now or datetime.utcnow()
    data = {f: getattr(prev, f, None) for f in _VERSIONED_FIELDS}
    if overrides:
        data.update({k: v for k, v in overrides.items() if k in _VERSIONED_FIELDS})

    nxt = Proposal(
        organization_id=prev.organization_id,
        brand_sales_org_id=prev.brand_sales_org_id,
        opportunity_id=prev.opportunity_id,
        created_by_id=user.id,
        # SAME number, higher version. To the customer this is still EV-1007.
        proposal_number=prev.proposal_number,
        version=(prev.version or 1) + 1,
        supersedes_id=prev.id,
        sales_status=PROP_DRAFT,
        status="draft",
        expires_at=now + timedelta(days=DEFAULT_VALID_DAYS),
        created_at=now,
        **data,
    )
    db.add(nxt)

    prev.sales_status = PROP_SUPERSEDED
    prev.superseded_at = now
    # Pulled from the portal so the customer's link stops resolving to a
    # document that is no longer the offer. The row and its history remain.
    prev.status = "archived"

    db.flush()
    _event(db, prev.opportunity_id, "proposal_versioned",
           "Proposal %s revised to v%d" % (nxt.proposal_number, nxt.version),
           "Version %d superseded" % (prev.version or 1), user.id, now)
    return nxt


# ── pricing authority ───────────────────────────────────────────────────────

def can_override_price(db: Session, user, brand_sales_org_id: str) -> bool:
    """Manager-only, exactly as Checkpoint 1 established for deal value.

    Reusing that rule rather than inventing a proposal-specific one is the whole
    point: a rep who cannot change a deal's value must not be able to change the
    same number by opening the proposal instead.
    """
    from app.services.sales_access import is_sales_manager, is_god
    return bool(is_god(user) or is_sales_manager(user, db, brand_sales_org_id))


def apply_pricing(db: Session, prop: Proposal, user, package_id=None,
                  adjustment=None, reason: str = None, now=None,
                  billing_option=None) -> dict:
    """Set the package and any adjustment, enforcing authority server-side.

    Returns {"ok": bool, "error": str|None}. The caller turns a failure into a
    403 — this function never raises, and never silently drops an unauthorized
    change, which would be worse than refusing it.
    """
    now = now or datetime.utcnow()

    if package_id is not None and package_id != prop.package_id:
        pkg = db.query(BrandPackage).filter(BrandPackage.id == package_id).first()
        if pkg is None:
            return {"ok": False, "error": "That package does not exist."}
        # Cross-brand check. A package from another brand's catalogue must never
        # reach this brand's proposal.
        org = db.query(BrandSalesOrg).filter(
            BrandSalesOrg.id == prop.brand_sales_org_id).first()
        if org is not None and pkg.platform_id != org.platform_id:
            return {"ok": False, "error": "That package belongs to another brand."}
        prop.package_id = pkg.id
        # `base_amount` keeps meaning what it always meant - the ONE-TIME
        # implementation figure - so every existing proposal, adjustment and
        # total still reads correctly.
        #
        # But it must be THIS DEAL'S implementation figure. Reading `pkg.price`
        # directly ignored a per-deal override, so a proposal could quote the
        # catalogue's $1,497 in its total while the pricing block beside it
        # showed the $1,500 actually agreed - one document, two numbers, and
        # nothing to say which one the customer owes.
        _opp = (db.query(Opportunity)
                  .filter(Opportunity.id == prop.opportunity_id).first()
                if prop.opportunity_id else None)
        prop.base_amount = _pp.implementation_fee(pkg, _opp)
        prop.billing_option = _pp.normalize_option(
            billing_option if billing_option is not None else prop.billing_option, pkg)
        prop.contract_term_months = (
            _pp.term_months_for(pkg)
            if prop.billing_option == _pp.BILLING_TERM_AGREEMENT else None)
        prop.currency = pkg.currency or prop.currency or "USD"
        # Changing the package resets any prior adjustment: a discount agreed
        # against Professional is not a discount against Starter.
        prop.adjustment = None
        prop.price_override_by = None
        prop.price_override_at = None
        prop.price_override_reason = None

    elif billing_option is not None:
        # Switching option on the package already chosen. Re-rates the proposal
        # and, like a package change, clears any adjustment: a discount agreed
        # against the month-to-month rate is not a discount against the
        # contracted one.
        pkg = (db.query(BrandPackage).filter(BrandPackage.id == prop.package_id).first()
               if prop.package_id else None)
        if pkg is None:
            return {"ok": False,
                    "error": "Choose a package before choosing a billing option."}
        if (billing_option == _pp.BILLING_TERM_AGREEMENT
                and not _pp.has_term_option(pkg)):
            return {"ok": False,
                    "error": "%s has no term-agreement rate. Only month-to-month "
                             "is available for this package." % pkg.name}
        option = _pp.normalize_option(billing_option, pkg)
        if option != prop.billing_option:
            prop.billing_option = option
            prop.contract_term_months = (
                _pp.term_months_for(pkg)
                if option == _pp.BILLING_TERM_AGREEMENT else None)
            # `base_amount` is the one-time implementation figure and does NOT
            # move with the billing option - the setup fee is identical under
            # both. Any adjustment agreed against it therefore still stands,
            # which is why nothing is cleared here.

    if adjustment is not None:
        adj = _dec(adjustment)
        if adj is None:
            return {"ok": False, "error": "That adjustment is not a valid amount."}
        existing = _dec(prop.adjustment) or Decimal("0")
        if adj != existing:
            # ANY change to the agreed price is an override — including one that
            # raises it. Restricting only discounts would leave an unaudited path
            # to change what a customer is charged.
            if adj != 0 and not can_override_price(db, user, prop.brand_sales_org_id):
                return {"ok": False,
                        "error": "Only a sales manager can change proposal pricing. "
                                 "Ask your manager to apply the adjustment."}
            if adj != 0 and not (reason or "").strip():
                return {"ok": False,
                        "error": "A reason is required for a price adjustment."}
            prop.adjustment = adj
            if adj != 0:
                prop.price_override_by = user.id
                prop.price_override_at = now
                prop.price_override_reason = (reason or "").strip()
                _event(db, prop.opportunity_id, "proposal_price_override",
                       "Proposal pricing adjusted",
                       "%s %s — %s" % (prop.currency or "USD", adj,
                                       prop.price_override_reason),
                       user.id, now)
            else:
                prop.price_override_by = None
                prop.price_override_at = None
                prop.price_override_reason = None

    # ── keep the quote in step with the deal's implementation fee ──────────
    # `apply_pricing` above only re-rates when the PACKAGE changes. But the
    # one-time fee can change on the deal alone - a customer is quoted $1,500
    # against a $1,497 catalogue - and a draft that kept the old figure would
    # show one number in its total and another in its pricing block.
    #
    # Only while the customer has NOT seen it, and only with NO manual
    # adjustment.
    #
    # The status test is PROPOSAL_EDITABLE_STATUSES, not PROP_DRAFT. A proposal
    # sitting at "ready to send" is in exactly the position a draft is - written,
    # not delivered - and the narrower test left those stuck on a stale fee with
    # no way to correct it short of a new version. Once it is SENT or VIEWED this
    # stops: a document a customer has read does not move underneath them.
    #
    # The adjustment test is the one that matters more. A figure a manager
    # deliberately agreed is never silently re-derived; that is the difference
    # between keeping a quote current and overwriting somebody's decision.
    if (prop.package_id and not _dec(prop.adjustment)
            and prop.sales_status in PROPOSAL_EDITABLE_STATUSES):
        _pkg = db.query(BrandPackage).filter(BrandPackage.id == prop.package_id).first()
        _o = (db.query(Opportunity)
                .filter(Opportunity.id == prop.opportunity_id).first()
              if prop.opportunity_id else None)
        _fee = _pp.implementation_fee(_pkg, _o)
        if _fee is not None and _dec(prop.base_amount) != _fee:
            prop.base_amount = _fee

    base = _dec(prop.base_amount) or Decimal("0")
    adj = _dec(prop.adjustment) or Decimal("0")
    total = base + adj
    if total < 0:
        return {"ok": False, "error": "That adjustment would make the total negative."}
    prop.final_amount = total
    return {"ok": True, "error": None}


# ── publishing and delivery ─────────────────────────────────────────────────

def _fmt_money(value, currency: str = "USD") -> Optional[str]:
    """A price a customer reads, not a database value.

    Whole dollars stay whole - "$1,497", not "$1,497.00" - because trailing
    zeros on a headline number read like a system printed them.
    """
    d = _dec(value)
    if d is None:
        return None
    body = "{:,.0f}".format(d) if d == d.to_integral_value() else "{:,.2f}".format(d)
    cur = (currency or "USD").upper()
    return ("$" + body) if cur == "USD" else ("%s %s" % (cur, body))


def _investment_markdown(db: Session, prop: Proposal) -> Optional[str]:
    """The commercial summary the CUSTOMER sees, built from real pricing.

    Every figure here is read from the package catalogue and this proposal's
    own negotiated amount. Nothing is typed by hand into prose, so a price can
    never be edited in one place and left stale in the document.

    Both billing options are always shown when the package has both, because
    the customer is being asked to choose between them, and the one this
    proposal is written against is named explicitly. The lower rate is never
    presented as the normal price.
    """
    if prop.final_amount is None and not prop.package_id:
        return None

    currency = prop.currency or "USD"
    pkg = None
    if prop.package_id:
        pkg = (db.query(BrandPackage)
               .filter(BrandPackage.id == prop.package_id).first())

    # What THIS proposal charges to implement, not the catalogue list price -
    # an approved override has to survive into the document.
    fee = _dec(prop.final_amount)
    if fee is None and pkg is not None:
        opp = (db.query(Opportunity)
               .filter(Opportunity.id == prop.opportunity_id).first())
        fee = _pp.implementation_fee(pkg, opp)

    name = (getattr(pkg, "name", None) or "Platform").strip()
    m2m = _pp.monthly_rate(pkg, _pp.BILLING_MONTH_TO_MONTH) if pkg else None
    term_rate = _pp.monthly_rate(pkg, _pp.BILLING_TERM_AGREEMENT) if pkg else None
    has_term = bool(pkg) and _pp.has_term_option(pkg)
    term_months = (int(prop.contract_term_months) if prop.contract_term_months
                   else (_pp.term_months_for(pkg) if pkg else None))
    chosen = _pp.normalize_option(prop.billing_option, pkg)

    rows = []
    if fee is not None:
        rows.append((
            "%s implementation & setup" % name,
            "%s one-time" % _fmt_money(fee, currency),
            "Configuration and implementation of the %s system around your "
            "existing process." % name,
        ))
    if m2m is not None:
        rows.append((
            "Standard month-to-month platform",
            "%s/month" % _fmt_money(m2m, currency),
            "Flexible month-to-month platform access with no service "
            "commitment.",
        ))
    if has_term and term_rate is not None and term_months:
        saving = _pp.savings_per_month(pkg)
        detail = ("All %d months are billed monthly. No free month and no "
                  "annual prepayment requirement." % term_months)
        if saving is not None and saving > 0:
            detail = ("Save %s every month. " % _fmt_money(saving, currency)) + detail
        rows.append((
            "%d-month service agreement" % term_months,
            "%s/month" % _fmt_money(term_rate, currency),
            detail,
        ))

    if not rows:
        return None

    out = ["## Investment", ""]
    out.append("| Item | Pricing | Details |")
    out.append("| --- | --- | --- |")
    for item, price, detail in rows:
        out.append("| %s | %s | %s |" % (item, price, detail))
    out.append("")

    # Name the option this proposal is written against, and total it only when
    # there is a real commitment to total. Month-to-month has no end date, so
    # inventing a contract value for it would be a guess dressed as a promise.
    if chosen == _pp.BILLING_TERM_AGREEMENT and term_rate is not None and term_months:
        rcv = term_rate * Decimal(term_months)
        out.append("### Selected: %d-month service agreement" % term_months)
        out.append("")
        out.append("**%d-month platform commitment: %s** — %d monthly payments of %s."
                   % (term_months, _fmt_money(rcv, currency), term_months,
                      _fmt_money(term_rate, currency)))
        if fee is not None:
            out.append("")
            out.append("**Total contract value: %s** — %s implementation plus %s platform."
                       % (_fmt_money(rcv + fee, currency),
                          _fmt_money(fee, currency),
                          _fmt_money(rcv, currency)))
    elif m2m is not None:
        out.append("### Selected: month-to-month")
        out.append("")
        line = "**Platform: %s/month**, with no term commitment." % _fmt_money(m2m, currency)
        if fee is not None:
            line = ("**Platform: %s/month**, with no term commitment, plus a "
                    "%s one-time implementation charge."
                    % (_fmt_money(m2m, currency), _fmt_money(fee, currency)))
        out.append(line)
    elif fee is not None:
        out.append("**Total: %s one-time.**" % _fmt_money(fee, currency))

    return "\n".join(out).strip()


def _sync_proposal_blocks(db: Session, prop: Proposal, now: datetime) -> None:
    """Render the structured fields into the portal's own block system.

    The blocks ARE the customer-facing document — the existing portal viewer
    renders them and needs no knowledge of proposals. Regenerated from the
    structured fields on every publish, so the two can never drift.

    Only AdvisorFlow-authored sections are replaced. Blocks the rep added by
    hand (a demo link, a slide deck, a PDF) are identified by block_type and
    left completely alone — regenerating a document must never delete the
    supporting material someone curated around it.
    """
    existing = (db.query(ProposalBlock)
                .filter(ProposalBlock.proposal_id == prop.id).all())
    # Split BEFORE deleting. Checking `db.deleted` after a flush would find an
    # empty set — the rows are gone by then — and every hand-added block would
    # be silently skipped.
    kept = [b for b in existing if (b.file_name or "") != "af-generated"]
    for b in existing:
        if (b.file_name or "") == "af-generated":
            db.delete(b)
    db.flush()

    sections = [
        ("Overview", prop.executive_summary),
        ("The situation today", prop.business_need),
        ("Objectives", prop.objectives),
        ("What we recommend", prop.recommended_solution),
        ("Scope", prop.scope),
        ("Deliverables", prop.deliverables),
        ("Implementation", prop.implementation_plan),
        ("Terms", prop.terms),
    ]
    # Keep hand-added blocks where the rep put them; generated prose goes first.
    pos = 0
    for heading, body in sections:
        if not (body or "").strip():
            continue
        db.add(ProposalBlock(
            proposal_id=prop.id, block_type="text", position=pos,
            content="## %s\n\n%s" % (heading, body.strip()),
            # The marker that makes regeneration safe. Not a user-facing value.
            file_name="af-generated",
            created_at=now))
        pos += 1

    investment = _investment_markdown(db, prop)
    if investment:
        db.add(ProposalBlock(
            proposal_id=prop.id, block_type="text", position=pos,
            content=investment,
            file_name="af-generated", created_at=now))
        pos += 1

    # Re-seat the hand-added blocks after the generated prose, in their
    # original relative order.
    for b in sorted(kept, key=lambda x: (x.position or 0)):
        b.position = pos
        pos += 1
    db.flush()


def publish_proposal(db: Session, prop: Proposal, user, now=None) -> dict:
    """Make the proposal live in the portal WITHOUT sending anything.

    Separate from `send` on purpose: a rep often wants to publish, look at it
    through the customer's eyes, then send. Publishing has no outbound side
    effect, so doing it twice costs nothing and surprises nobody.
    """
    now = now or datetime.utcnow()
    if prop.sales_status in (PROP_SUPERSEDED, PROP_ACCEPTED, PROP_DECLINED):
        return {"ok": False,
                "error": "This version is %s and cannot be republished."
                         % (prop.sales_status or "closed")}
    _sync_proposal_blocks(db, prop, now)
    # The legacy column the existing portal resolver gates on. Until this flips
    # to 'published', a token for this proposal correctly refuses to resolve.
    prop.status = "published"
    if prop.sales_status in (None, PROP_DRAFT, PROP_INTERNAL_REVIEW):
        prop.sales_status = PROP_READY
    _event(db, prop.opportunity_id, "proposal_published",
           "Proposal %s v%d published" % (prop.proposal_number, prop.version or 1),
           None, user.id, now)
    return {"ok": True, "error": None}


def issue_access(db: Session, prop: Proposal, recipient_email: str,
                 recipient_name: str = None, valid_hours: int = 720,
                 now=None) -> ProposalToken:
    """A secure portal key for one recipient.

    Reuses the EXISTING ProposalToken mechanism — expiry, revocation and
    first-redemption tracking already worked and are not reimplemented.

    The one thing upgraded: `secrets.token_urlsafe` instead of the previous
    `uuid4().hex`. A UUID is not a CSPRNG secret; it has structure and reduced
    entropy, and this token is the ONLY thing standing between a stranger and a
    customer's pricing.
    """
    import secrets
    now = now or datetime.utcnow()
    tok = ProposalToken(
        proposal_id=prop.id,
        token=secrets.token_urlsafe(32),
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        expires_at=now + timedelta(hours=valid_hours),
        created_at=now,
    )
    db.add(tok)
    db.flush()
    _event(db, prop.opportunity_id, "portal_access_issued",
           "Secure portal access issued",
           "To %s" % recipient_email, None, now)
    return tok


def revoke_access(db: Session, prop: Proposal, now=None) -> int:
    """Revoke every live key for this proposal. Returns how many."""
    now = now or datetime.utcnow()
    rows = (db.query(ProposalToken)
            .filter(ProposalToken.proposal_id == prop.id,
                    ProposalToken.revoked_at.is_(None)).all())
    for t in rows:
        t.revoked_at = now
    if rows:
        _event(db, prop.opportunity_id, "portal_access_revoked",
               "Portal access revoked", "%d link(s)" % len(rows), None, now)
    return len(rows)


def send_proposal(db: Session, prop: Proposal, user, recipient_email=None,
                  recipient_name=None, now=None, dry_run: bool = False) -> dict:
    """Publish, issue access, and email the customer their deal room.

    `dry_run` exists so tests and previews can exercise the whole path with no
    outbound side effect. No real prospect is ever emailed by a test.

    Does NOT move the opportunity to Won, or to any stage at all. Sending a
    proposal is a thing the SELLER did; the deal advances when the BUYER does
    something, and conflating the two inflates a pipeline with deals nobody has
    agreed to.
    """
    now = now or datetime.utcnow()
    to_email = (recipient_email or prop.client_email or "").strip()
    if not to_email:
        return {"ok": False, "sent": False,
                "error": "No customer email address on this proposal."}

    pub = publish_proposal(db, prop, user, now=now)
    if not pub["ok"]:
        return {"ok": False, "sent": False, "error": pub["error"]}

    tok = issue_access(db, prop, to_email,
                       recipient_name or prop.client_name, now=now)

    from app.services.appointment_invites import brand_identity_for_brand, PUBLIC_BASE_URL
    ident = brand_identity_for_brand(db, prop.brand_sales_org_id)
    portal_url = "%s/portal/access/%s" % (
        (ident.get("app_base_url") or PUBLIC_BASE_URL).rstrip("/"), tok.token)

    if dry_run:
        # Deliberately does NOT mark the proposal sent. Nobody received it, so
        # claiming otherwise would put "proposal sent" on the pipeline for a
        # customer who never got one — and `dry_run` is settable by any caller
        # of the send endpoint, not just a test. The publish and the access key
        # above are real, which is what makes the returned URL worth previewing.
        return {"ok": True, "sent": False, "dry_run": True,
                "portal_url": portal_url, "error": None}

    result = _send_portal_email(db, prop, ident, to_email,
                                recipient_name or prop.client_name, portal_url)
    if not result.get("success"):
        # The proposal IS published and the key IS valid — only the email
        # failed. Saying "sent" here would be a lie the rep acts on.
        return {"ok": False, "sent": False,
                "error": result.get("error") or "The email could not be sent.",
                "portal_url": portal_url}

    prop.sales_status = PROP_SENT
    prop.sent_at = now
    # Mirror onto the opportunity's own bookkeeping fields, which existed
    # before Checkpoint 4 and which the pipeline board already reads.
    try:
        opp = db.query(Opportunity).filter(Opportunity.id == prop.opportunity_id).first()
        if opp is not None:
            opp.proposal_status = PROP_SENT
            opp.proposal_sent_at = now
    except Exception:
        log.exception("could not mirror proposal status onto opportunity")

    _event(db, prop.opportunity_id, "proposal_sent",
           "Proposal %s v%d sent to %s" % (prop.proposal_number,
                                           prop.version or 1, to_email),
           None, user.id, now)
    return {"ok": True, "sent": True, "portal_url": portal_url, "error": None}


def _send_portal_email(db: Session, prop: Proposal, ident: dict,
                       to_email: str, to_name: str, portal_url: str) -> dict:
    """The branded customer email. Contains a link and nothing confidential.

    The price is deliberately NOT in the email body. Email is forwarded,
    screenshotted and sat in inboxes for years; the deal room is revocable and
    expiring, so that is where the numbers live.
    """
    from html import escape
    brand = escape(ident.get("name") or "AdvisorFlow")
    accent = ident.get("accent") or "#1d4ed8"
    who = escape(to_name or "there")
    company = escape(prop.client_company or "your team")

    contact = []
    if ident.get("support_phone"):
        contact.append("Call %s" % escape(ident["support_phone"]))
    if ident.get("from_email"):
        contact.append("or just reply to this email")
    footer = ("<p style='font-size:13px;color:#6b7280'>Questions? %s.</p>"
              % " ".join(contact)) if contact else ""

    body = (
        "<div style=\"font-family:Arial,Helvetica,sans-serif;font-size:15px;"
        "color:#111827;line-height:1.55;max-width:560px\">"
        "<p>Hi %s,</p>"
        "<p>Your proposal for %s is ready. Everything is in one secure place — "
        "the proposal itself, and the supporting material we put together for you.</p>"
        "<div style='margin:26px 0'>"
        "<a href=\"%s\" style=\"background:%s;color:#ffffff;text-decoration:none;"
        "padding:12px 22px;border-radius:6px;font-weight:600;display:inline-block\">"
        "Open your proposal</a></div>"
        "<p style='font-size:13px;color:#6b7280'>If the button does not work, "
        "open this link:<br><a href=\"%s\" style=\"color:%s\">%s</a></p>"
        "<p style='font-size:13px;color:#6b7280'>This is a private link just for "
        "you. Please do not forward it.</p>%s"
        "<p style='font-size:12px;color:#9ca3af;margin-top:26px'>%s</p></div>"
    ) % (who, company, escape(portal_url), accent, escape(portal_url), accent,
         escape(portal_url), footer, escape(ident.get("website") or brand))

    try:
        from app.services.email_service import send_email_via_provider
        from app.services.appointment_invites import _SendingOrg
        return send_email_via_provider(
            to_email=to_email,
            subject="Your proposal from %s" % (ident.get("name") or "us"),
            body_html=body,
            attachments=None,
            org=_SendingOrg(ident.get("from_email")),
        )
    except Exception as e:
        log.exception("proposal email send blew up for %s", prop.id)
        return {"success": False, "error": str(e)[:400]}


def _event(db: Session, opportunity_id, event_type: str, summary: str,
           detail, actor_user_id, now: datetime) -> None:
    """Append to the OPPORTUNITY timeline. Never raises — a logging failure
    must not turn a successful publish into a failed one."""
    if not opportunity_id:
        return
    try:
        db.add(OpportunityEvent(
            opportunity_id=opportunity_id, event_type=event_type,
            summary=summary, detail=detail, actor_user_id=actor_user_id,
            occurred_at=now))
    except Exception:
        log.exception("could not write proposal event for opportunity %s",
                      opportunity_id)


# ── buyer activity ──────────────────────────────────────────────────────────

# Events that should also appear on the salesperson's opportunity timeline.
# Not all of them do: a customer scrolling back to a document for the fourth
# time is real data for the activity feed, but a timeline that reports it as a
# headline event becomes noise nobody reads.
_TIMELINE_WORTHY = {
    PORTAL_OPENED, PORTAL_PROPOSAL_VIEWED, PORTAL_ACCEPTED, PORTAL_DECLINED,
    PORTAL_CHANGE_REQUESTED,
}


def record_portal_event(db: Session, prop: Proposal, event_type: str,
                        token: ProposalToken = None, label: str = None,
                        block_id: str = None, user_agent: str = None,
                        now=None) -> Optional[PortalEvent]:
    """Record one defensible act by the buyer.

    Called only from a request that arrived carrying a valid token. Nothing here
    is inferred, estimated or derived from a timer — every row corresponds to a
    real HTTP request our server actually handled.
    """
    now = now or datetime.utcnow()
    try:
        ev = PortalEvent(
            proposal_id=prop.id,
            opportunity_id=prop.opportunity_id,
            token_id=token.id if token is not None else None,
            event_type=event_type,
            block_id=block_id,
            label=label,
            proposal_version=prop.version,
            recipient_email=(token.recipient_email if token is not None else None),
            # Family only ("Chrome", "Safari"). The full UA string is a
            # fingerprinting surface we have no use for.
            user_agent_family=_ua_family(user_agent),
            occurred_at=now,
        )
        db.add(ev)
    except Exception:
        log.exception("could not record portal event for proposal %s", prop.id)
        return None

    # First view flips SENT -> VIEWED. Later views only move the timestamp: a
    # proposal that reached ACCEPTED must not fall back to VIEWED because the
    # customer opened it again to re-read the terms.
    if event_type == PORTAL_PROPOSAL_VIEWED:
        if prop.first_viewed_at is None:
            prop.first_viewed_at = now
        prop.last_viewed_at = now
        if prop.sales_status in (PROP_SENT, PROP_READY):
            prop.sales_status = PROP_VIEWED

    if event_type in _TIMELINE_WORTHY:
        from app.models.models import PORTAL_EVENT_LABELS
        _event(db, prop.opportunity_id, "portal_activity",
               "%s — %s" % (prop.client_company or "Customer",
                            PORTAL_EVENT_LABELS.get(event_type, event_type)),
               label, None, now)
    return ev


def _ua_family(ua: str) -> Optional[str]:
    if not ua:
        return None
    low = ua.lower()
    for name, needle in (("Edge", "edg/"), ("Chrome", "chrome"), ("Firefox", "firefox"),
                         ("Safari", "safari"), ("Outlook", "outlook")):
        if needle in low:
            return name
    return "Other"


# ── customer decision ───────────────────────────────────────────────────────

CUSTOMER_ACTIONS = ("accept", "decline", "request_change")


def record_decision(db: Session, prop: Proposal, action: str,
                    token: ProposalToken = None, note: str = None,
                    user_agent: str = None, now=None) -> dict:
    """The customer accepted, declined, or asked for a change.

    ACCEPTANCE DOES NOT WIN THE DEAL, and does not provision anything. It
    records what the customer said and hands the salesperson a clear next
    action. Turning acceptance into an automatic Won would skip contracting,
    payment and the human confirmation that the thing actually closed — and
    Checkpoint 6 owns provisioning, not this.
    """
    now = now or datetime.utcnow()
    action = (action or "").strip().lower()
    if action not in CUSTOMER_ACTIONS:
        return {"ok": False, "error": "Unknown action."}
    if prop.sales_status == PROP_SUPERSEDED:
        return {"ok": False,
                "error": "This version has been replaced by a newer one."}

    note = (note or "").strip() or None
    prop.customer_response_note = note
    if token is not None:
        prop.responded_by_email = token.recipient_email

    if action == "accept":
        prop.sales_status = PROP_ACCEPTED
        prop.accepted_at = now
        ev_type, summary = PORTAL_ACCEPTED, "Proposal accepted"
    elif action == "decline":
        prop.sales_status = PROP_DECLINED
        prop.declined_at = now
        ev_type, summary = PORTAL_DECLINED, "Proposal declined"
    else:
        prop.sales_status = PROP_CHANGE_REQUESTED
        prop.change_requested_at = now
        ev_type, summary = PORTAL_CHANGE_REQUESTED, "Customer requested a change"

    record_portal_event(db, prop, ev_type, token=token, label=note,
                        user_agent=user_agent, now=now)

    # Give the rep something to DO. A decision with no next action is how a
    # signed-and-ready deal sits untouched for a week.
    try:
        opp = db.query(Opportunity).filter(Opportunity.id == prop.opportunity_id).first()
        if opp is not None:
            opp.proposal_status = prop.sales_status
            if action == "accept":
                opp.next_action = "Confirm acceptance and start closing"
            elif action == "request_change":
                opp.next_action = "Revise the proposal — customer requested a change"
            else:
                opp.next_action = "Follow up on the declined proposal"
            opp.next_action_due_at = now
    except Exception:
        log.exception("could not set next action after customer decision")

    _event(db, prop.opportunity_id, "proposal_%s" % action,
           summary, note, None, now)
    return {"ok": True, "action": action, "error": None}


def expire_due_proposals(db: Session, now=None) -> int:
    """Mark open proposals past their expiry as EXPIRED. Returns how many.

    A stale proposal that still reads 'Sent' three months later is worse than
    useless — it hides in the pipeline as if it were live.
    """
    now = now or datetime.utcnow()
    rows = (db.query(Proposal)
            .filter(Proposal.opportunity_id.isnot(None),
                    Proposal.deleted_at.is_(None),
                    Proposal.expires_at.isnot(None),
                    Proposal.expires_at < now,
                    Proposal.sales_status.in_(
                        [PROP_SENT, PROP_VIEWED, PROP_READY]))
            .all())
    for p in rows:
        p.sales_status = PROP_EXPIRED
        _event(db, p.opportunity_id, "proposal_expired",
               "Proposal %s expired" % p.proposal_number, None, None, now)
    return len(rows)
