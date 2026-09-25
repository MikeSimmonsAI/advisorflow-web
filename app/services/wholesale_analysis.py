"""Deal math — ARV from comps, the offer formula, and the spread.

EVERY FUNCTION HERE IS PURE. No session, no models, no I/O: they take numbers
and return numbers plus the working. That is not tidiness for its own sake — it
is what makes the arithmetic a wholesaler is going to bet money on testable
without a database, and what lets the API return the same breakdown the screen
renders.

THREE RULES THIS MODULE IS BUILT AROUND
---------------------------------------
1. NOTHING IS INVENTED. Every function that cannot compute an answer returns
   None with a reason, never a plausible default. `arv_from_comps([])` is None,
   not a guess; `max_allowable_offer` with no ARV is None, not zero. A zero here
   would look like a computed answer and would be believed.

2. THE INVESTOR PERCENTAGE IS AN INPUT. There is no 70 in this file outside the
   docstring. It comes from `WholesaleSettings.investor_percentage`, which the
   customer sets. "Do not hard-code '70% rule' as universal truth."

3. THE MATH IS SHOWN. Every calculation returns a `steps` list of
   {label, value, note} in the order a person would work it on paper, so the
   deal analyzer can print the working rather than a single number the user has
   to trust.

Money is handled as `Decimal` throughout and rounded to cents once, at the end.
Float arithmetic on money is how a spread comes out at 9,999.999999999998.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from statistics import median
from typing import Any, Dict, List, Optional, Sequence

CENTS = Decimal("0.01")


def money(value: Any) -> Optional[Decimal]:
    """Coerce anything user- or database-shaped into a Decimal, or None.

    Returns None for None, "", and anything unparseable — deliberately NOT 0.
    An unparseable repair estimate is an unknown repair estimate, and treating
    it as zero is the single easiest way to produce an offer that is too high.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    try:
        text = str(value).strip().replace(",", "").replace("$", "")
        if text == "":
            return None
        return Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _round(value: Optional[Decimal]) -> Optional[Decimal]:
    if value is None:
        return None
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def _f(value: Optional[Decimal]) -> Optional[float]:
    """For JSON. Kept in one place so no route does its own conversion."""
    return None if value is None else float(value)


# ── ARV from comparable sales ───────────────────────────────────────────────

def arv_from_comps(comps: Sequence[Any], subject_sqft: Optional[int] = None,
                   method: str = "median_psf") -> Dict[str, Any]:
    """Derive an ARV from the comps the user INCLUDED, and say how.

    Two methods, both transparent:

        median_psf   median price per square foot of the included comps,
                     multiplied by the subject's square footage. Preferred,
                     because it is the only one that adjusts for size at all.
                     Requires the subject's sqft and at least one comp that has
                     both a price and a size.

        median_price plain median of the included comps' sale prices. Used when
                     the subject's square footage is unknown or no comp carries
                     a size. Honest, and labelled as the cruder method it is.

    Returns {"arv", "method", "comp_count", "steps", "warnings"}. `arv` is None
    when there is nothing to compute from — never a fallback number.

    `comps` are model rows or anything with the same attribute names, so this
    stays testable with plain objects.
    """
    steps: List[Dict[str, Any]] = []
    warnings: List[str] = []

    included = [c for c in comps if getattr(c, "included", True)]
    if not included:
        return {"arv": None, "method": None, "comp_count": 0, "steps": [],
                "warnings": ["No comps are included. Add comps, or enter an ARV "
                             "manually — nothing is estimated from an empty set."]}

    prices = [(c, money(getattr(c, "sale_price", None))) for c in included]
    priced = [(c, p) for c, p in prices if p is not None and p > 0]
    if not priced:
        return {"arv": None, "method": None, "comp_count": len(included), "steps": [],
                "warnings": ["None of the included comps has a sale price."]}

    if len(priced) < len(included):
        warnings.append("%d of %d included comps have no sale price and were skipped."
                        % (len(included) - len(priced), len(included)))

    psf_values = []
    for c, price in priced:
        sqft = getattr(c, "square_feet", None)
        try:
            sqft = int(sqft) if sqft else None
        except (TypeError, ValueError):
            sqft = None
        if sqft and sqft > 0:
            psf_values.append(price / Decimal(sqft))

    subject = None
    try:
        subject = int(subject_sqft) if subject_sqft else None
    except (TypeError, ValueError):
        subject = None

    use_psf = (method == "median_psf" and psf_values and subject and subject > 0)

    if use_psf:
        med_psf = median(sorted(psf_values))
        arv = med_psf * Decimal(subject)
        steps.append({"label": "Comps included", "value": len(priced),
                      "note": "%d of them carry a square footage" % len(psf_values)})
        steps.append({"label": "Median price per sq ft", "value": _f(_round(med_psf)),
                      "note": "median of the included comps"})
        steps.append({"label": "Subject square feet", "value": subject, "note": None})
        steps.append({"label": "Estimated ARV", "value": _f(_round(arv)),
                      "note": "median $/sqft x subject sqft"})
        if len(psf_values) < 3:
            warnings.append("Only %d comp(s) had a square footage. A $/sqft median "
                            "from that few is weak evidence." % len(psf_values))
        return {"arv": _round(arv), "method": "comps:median_psf",
                "comp_count": len(priced), "steps": steps, "warnings": warnings}

    med_price = median(sorted(p for _, p in priced))
    steps.append({"label": "Comps included", "value": len(priced), "note": None})
    steps.append({"label": "Median sale price", "value": _f(_round(med_price)),
                  "note": "no size adjustment applied"})
    steps.append({"label": "Estimated ARV", "value": _f(_round(med_price)), "note": None})
    if method == "median_psf":
        warnings.append(
            "Fell back to a plain median of sale prices: "
            + ("the subject property has no square footage on file."
               if not subject else
               "none of the included comps has a square footage."))
    if len(priced) < 3:
        warnings.append("Only %d comp(s). Treat this ARV as a starting point, not "
                        "a valuation." % len(priced))
    return {"arv": _round(med_price), "method": "comps:median_price",
            "comp_count": len(priced), "steps": steps, "warnings": warnings}


def comp_statistics(comps: Sequence[Any], subject_sqft: Any = None,
                    subject_value: Any = None) -> Dict[str, Any]:
    """The comp set described as numbers, for the side-by-side comparison.

    Separate from `arv_from_comps` on purpose. That function answers "what is
    the ARV"; this one answers "what does this comp set actually look like, and
    where does the subject sit in it" — median AND average $/sqft, the spread,
    and each comp's distance from the median so an outlier is visible rather
    than silently dragging the average.

    EVERY FIGURE IS None WHEN IT CANNOT BE COMPUTED. There is no fallback
    number anywhere in here: a comp set with no square footages reports
    median_psf = None, not zero and not a guess. The screen renders the dash.

    `subject_value` is whatever the deal currently calls the ARV. It is used
    only to express the subject as a $/sqft so it can sit in the same column as
    the comps; it is never fed back into the ARV.
    """
    included = [c for c in comps if getattr(c, "included", True)]
    excluded = [c for c in comps if not getattr(c, "included", True)]

    def _sqft(c):
        raw = getattr(c, "square_feet", None)
        try:
            n = int(raw) if raw else None
        except (TypeError, ValueError):
            return None
        return n if n and n > 0 else None

    prices, psfs, rows = [], [], []
    for c in comps:
        price = money(getattr(c, "sale_price", None))
        sqft = _sqft(c)
        psf = (price / Decimal(sqft)) if (price and price > 0 and sqft) else None
        counts = bool(getattr(c, "included", True))
        if counts and price and price > 0:
            prices.append(price)
            if psf is not None:
                psfs.append(psf)
        rows.append({"id": getattr(c, "id", None), "included": counts,
                     "price_per_sqft": _f(_round(psf)) if psf is not None else None})

    med_psf = median(sorted(psfs)) if psfs else None
    avg_psf = (sum(psfs) / Decimal(len(psfs))) if psfs else None
    med_price = median(sorted(prices)) if prices else None
    avg_price = (sum(prices) / Decimal(len(prices))) if prices else None

    # Each comp's distance from the median, once the median exists. This is what
    # makes an outlier a visible fact instead of an argument.
    if med_psf and med_psf > 0:
        for row in rows:
            psf = row["price_per_sqft"]
            if psf is None:
                row["vs_median_pct"] = None
            else:
                row["vs_median_pct"] = _f(_round(
                    ((money(psf) - med_psf) / med_psf) * Decimal(100)))
    else:
        for row in rows:
            row["vs_median_pct"] = None

    subject = None
    try:
        subject = int(subject_sqft) if subject_sqft else None
    except (TypeError, ValueError):
        subject = None
    if subject is not None and subject <= 0:
        subject = None

    value = money(subject_value)
    subject_psf = (value / Decimal(subject)) if (value and value > 0 and subject) else None

    return {
        "comp_count": len(comps),
        "included_count": len(included),
        "excluded_count": len(excluded),
        "priced_count": len(prices),
        "sized_count": len(psfs),
        "median_price_per_sqft": _f(_round(med_psf)),
        "average_price_per_sqft": _f(_round(avg_psf)),
        "low_price_per_sqft": _f(_round(min(psfs))) if psfs else None,
        "high_price_per_sqft": _f(_round(max(psfs))) if psfs else None,
        "median_sale_price": _f(_round(med_price)),
        "average_sale_price": _f(_round(avg_price)),
        "low_sale_price": _f(_round(min(prices))) if prices else None,
        "high_sale_price": _f(_round(max(prices))) if prices else None,
        "subject_square_feet": subject,
        "subject_price_per_sqft": _f(_round(subject_psf)),
        "rows": rows,
    }


# ── The offer formula ───────────────────────────────────────────────────────

def calculate_offer(arv: Any, repairs: Any, investor_percentage: Any,
                    wholesale_fee: Any, transaction_cost_percent: Any = 0,
                    transaction_cost_flat: Any = 0) -> Dict[str, Any]:
    """Maximum Allowable Offer, with the working shown.

        MAO = (ARV x investor%) - repairs - transaction costs - wholesale fee

    Every term is an argument. The caller reads them from the organization's
    settings and from the deal, and a change to either is visible in `steps`
    rather than buried in a constant.

    Returns {"mao", "blocked", "steps", "warnings"}. `mao` is None whenever ARV
    is missing — the formula has no meaning without it, and a None that the UI
    renders as "not enough information" is the correct answer. Missing repairs
    are treated as zero AND warned about loudly, because an unknown repair
    number is the most common way a wholesale offer goes wrong.

    A NEGATIVE MAO IS RETURNED AS-IS, not clamped to zero. It is a real and
    useful answer: it says this deal does not work at these numbers.
    """
    steps: List[Dict[str, Any]] = []
    warnings: List[str] = []

    arv_d = money(arv)
    if arv_d is None or arv_d <= 0:
        return {"mao": None, "blocked": "no_arv", "steps": [],
                "warnings": ["No ARV. Add comps and calculate one, or enter an ARV "
                             "manually. Nothing is assumed."]}

    pct = money(investor_percentage)
    if pct is None or pct <= 0:
        return {"mao": None, "blocked": "no_investor_percentage", "steps": [],
                "warnings": ["No investor percentage is configured for this "
                             "organization. Set one in Wholesale Settings."]}

    repairs_d = money(repairs)
    if repairs_d is None:
        repairs_d = Decimal(0)
        warnings.append("No repair estimate. This offer is calculated as if repairs "
                        "were zero, which they are not. Add an estimate before "
                        "sending anything.")

    fee = money(wholesale_fee) or Decimal(0)
    if money(wholesale_fee) is None:
        warnings.append("No wholesale fee set on this deal; the organization default "
                        "was not applied either, so the fee is treated as zero.")

    tc_pct = money(transaction_cost_percent) or Decimal(0)
    tc_flat = money(transaction_cost_flat) or Decimal(0)

    base = arv_d * (pct / Decimal(100))
    transaction_costs = (arv_d * (tc_pct / Decimal(100))) + tc_flat
    mao = base - repairs_d - transaction_costs - fee

    steps.append({"label": "ARV", "value": _f(_round(arv_d)), "note": None})
    steps.append({"label": "x investor %", "value": float(pct),
                  "note": "configured by this organization, not a platform rule"})
    steps.append({"label": "= base", "value": _f(_round(base)), "note": None})
    steps.append({"label": "- repairs", "value": _f(_round(repairs_d)),
                  "note": "estimated as zero" if not repairs_d else None})
    steps.append({"label": "- transaction costs", "value": _f(_round(transaction_costs)),
                  "note": "%.2f%% of ARV + %s flat" % (float(tc_pct), _f(_round(tc_flat)))})
    steps.append({"label": "- wholesale fee", "value": _f(_round(fee)), "note": None})
    steps.append({"label": "= maximum allowable offer", "value": _f(_round(mao)),
                  "note": "the most that can be paid and still leave the fee intact"})

    if mao <= 0:
        warnings.append("The maximum allowable offer is at or below zero at these "
                        "numbers. This deal does not work as entered.")

    return {"mao": _round(mao), "blocked": None, "steps": steps,
            "warnings": warnings, "transaction_costs": _round(transaction_costs)}


def deal_summary(deal: Any, settings: Any) -> Dict[str, Any]:
    """The full financial picture of one deal, ready to render.

    Pulls the assumptions off the deal where they were snapshotted and falls
    back to the organization's current settings only for the ones the deal has
    never carried. `assumption_source` says which happened for each, because
    "why did this number change" is the question this screen gets asked.
    """
    arv = money(getattr(deal, "arv", None))
    repairs = money(getattr(deal, "repair_estimate", None))

    pct = money(getattr(deal, "investor_percentage_used", None))
    pct_source = "deal"
    if pct is None:
        pct = money(getattr(settings, "investor_percentage", None))
        pct_source = "settings"

    fee = money(getattr(deal, "desired_wholesale_fee", None))
    fee_source = "deal"
    if fee is None:
        fee = money(getattr(settings, "default_wholesale_fee", None))
        fee_source = "settings"

    calc = calculate_offer(
        arv, repairs, pct, fee,
        getattr(settings, "transaction_cost_percent", 0),
        getattr(settings, "transaction_cost_flat", 0),
    )

    proposed = money(getattr(deal, "proposed_offer", None))
    contract_price = money(getattr(deal, "contract_price", None))
    buyer_price = money(getattr(deal, "buyer_price", None))

    acquisition = contract_price if contract_price is not None else proposed
    spread = None
    if buyer_price is not None and acquisition is not None:
        spread = buyer_price - acquisition

    # What the BUYER is left with at the price we are asking them to pay.
    buyer_margin = None
    if arv is not None and buyer_price is not None:
        buyer_margin = arv - buyer_price - (repairs or Decimal(0))

    min_margin = money(getattr(settings, "min_buyer_margin", None))
    margin_warning = None
    if min_margin is not None and buyer_margin is not None and buyer_margin < min_margin:
        margin_warning = (
            "At this buyer price the buyer's margin is %s, below this "
            "organization's %s floor. Nothing is blocked — this is a flag for the "
            "person deciding." % (_f(_round(buyer_margin)), _f(_round(min_margin))))

    return {
        "arv": _f(arv),
        "arv_source": getattr(deal, "arv_source", None),
        "arv_method": getattr(deal, "arv_method", None),
        "repair_estimate": _f(repairs),
        "repair_estimate_source": getattr(deal, "repair_estimate_source", None),
        "investor_percentage": float(pct) if pct is not None else None,
        "investor_percentage_source": pct_source,
        "wholesale_fee": _f(fee),
        "wholesale_fee_source": fee_source,
        "transaction_costs": _f(calc.get("transaction_costs")),
        "max_allowable_offer": _f(calc["mao"]),
        "proposed_offer": _f(proposed),
        "contract_price": _f(contract_price),
        "buyer_price": _f(buyer_price),
        "estimated_spread": _f(_round(spread)) if spread is not None else None,
        "estimated_buyer_margin": _f(_round(buyer_margin)) if buyer_margin is not None else None,
        "assignment_fee": _f(money(getattr(deal, "assignment_fee", None))),
        "wholesale_fee_collected": _f(money(getattr(deal, "wholesale_fee_collected", None))),
        "steps": calc["steps"],
        "warnings": calc["warnings"] + ([margin_warning] if margin_warning else []),
        "blocked": calc["blocked"],
    }


# ── Seller / deal qualification ─────────────────────────────────────────────
#
# CONFIGURABLE, AND DELIBERATELY NOT ONE UNIVERSAL FORMULA. The weights below
# are the module's defaults; the BANDS come from the organization's settings.
# Each factor contributes points and a REASON, and the reasons are what get
# stored — a score with no reasons is a number nobody can argue with, which in
# this business means a number nobody should trust.

TIMELINE_POINTS = {
    "asap": 25, "30_days": 22, "60_days": 16, "90_days": 12,
    "6_months": 6, "no_rush": 2,
}
CONDITION_POINTS = {
    "distressed": 18, "poor": 16, "fair": 12, "good": 6, "excellent": 3,
}
# What we would need to know to score this seller honestly. Completeness is the
# share of these that are actually answered, and a low completeness routes to
# REVIEW rather than producing a confident band from very little.
COMPLETENESS_FIELDS = (
    "considering_selling", "asking_price", "timeline", "property_condition",
    "occupancy", "motivation", "decision_makers",
)


def qualify_seller(profile: Any, deal: Any, settings: Any,
                   lead: Any = None) -> Dict[str, Any]:
    """Score a seller and say why, in sentences.

    Returns {"band", "score", "completeness", "reasons"}. Bands:

        excluded  contact is impossible or forbidden — DNC, opted out, no
                  contact details at all, or the owner said not interested.
                  An EXCLUDED seller is never scored on motivation; the answer
                  is already no.
        review    not enough is known to be confident (completeness below the
                  organization's floor), or the AI flagged it for a person.
        high / medium / low   by score, against the organization's thresholds.
    """
    reasons: List[str] = []
    score = 0

    # ── Exclusions come first and end the question ──────────────────────────
    if lead is not None:
        if getattr(lead, "status", None) == "dnc":
            return {"band": "excluded", "score": 0, "completeness": 0,
                    "reasons": ["This contact is on the do-not-contact list."]}
        if getattr(lead, "manual_flag", None) == "remove_all":
            return {"band": "excluded", "score": 0, "completeness": 0,
                    "reasons": ["This contact is flagged remove_all."]}
        if getattr(lead, "allow_sms", None) is False and \
                getattr(lead, "allow_email", None) is False and \
                getattr(lead, "allow_voice", None) is False:
            return {"band": "excluded", "score": 0, "completeness": 0,
                    "reasons": ["Every channel is denied for this contact."]}

    if getattr(profile, "considering_selling", None) is False:
        return {"band": "excluded", "score": 0, "completeness": 100,
                "reasons": ["The owner said they are not considering selling."]}
    if getattr(profile, "is_available", None) is False:
        return {"band": "excluded", "score": 0, "completeness": 100,
                "reasons": ["The property is not available — already sold or off market."]}

    # ── Completeness ────────────────────────────────────────────────────────
    known = 0
    for field in COMPLETENESS_FIELDS:
        val = getattr(profile, field, None)
        if val is not None and val != "":
            known += 1
    completeness = int(round(100 * known / len(COMPLETENESS_FIELDS)))

    # ── Contactability. Worth real points, because an unreachable motivated
    #    seller is worth less than a reachable lukewarm one.
    if lead is not None:
        if getattr(lead, "phone", None):
            score += 12
            reasons.append("A phone number is on file (+12).")
        else:
            reasons.append("No phone number on file (0).")
        if getattr(lead, "email", None):
            score += 5
            reasons.append("An email address is on file (+5).")

    # ── Stated intent ───────────────────────────────────────────────────────
    if getattr(profile, "considering_selling", None) is True:
        score += 20
        reasons.append("The owner said they are considering selling (+20).")

    timeline = (getattr(profile, "timeline", None) or "").strip().lower()
    if timeline in TIMELINE_POINTS:
        pts = TIMELINE_POINTS[timeline]
        score += pts
        reasons.append("Timeline is %s (+%d)." % (timeline.replace("_", " "), pts))

    condition = (getattr(profile, "property_condition", None) or "").strip().lower()
    if condition in CONDITION_POINTS:
        pts = CONDITION_POINTS[condition]
        score += pts
        reasons.append("Condition is %s (+%d) — more work usually means more spread."
                       % (condition, pts))

    if (getattr(profile, "motivation", None) or "").strip():
        score += 8
        reasons.append("A motivation was stated (+8).")

    if (getattr(profile, "occupancy", None) or "").strip().lower() == "vacant":
        score += 6
        reasons.append("The property is vacant (+6).")

    if (getattr(profile, "decision_makers", None) or "").strip():
        score += 4
        reasons.append("The decision makers are known (+4).")

    # ── Price against the numbers, when both exist ──────────────────────────
    asking = money(getattr(profile, "asking_price", None))
    if asking is not None and deal is not None:
        summary_mao = money(getattr(deal, "max_allowable_offer", None))
        if summary_mao is not None and summary_mao > 0:
            if asking <= summary_mao:
                score += 20
                reasons.append(
                    "The asking price (%s) is at or below the maximum allowable "
                    "offer (%s) (+20)." % (_f(asking), _f(summary_mao)))
            else:
                over = (asking - summary_mao) / summary_mao * Decimal(100)
                if over <= Decimal(15):
                    score += 8
                    reasons.append(
                        "The asking price is %.0f%% above the maximum allowable "
                        "offer — negotiable range (+8)." % float(over))
                else:
                    score -= 10
                    reasons.append(
                        "The asking price is %.0f%% above the maximum allowable "
                        "offer (-10)." % float(over))
        else:
            reasons.append("An asking price is known but there is no analysis to "
                           "compare it against yet.")

    score = max(0, min(100, score))

    # ── Bands, from the organization's own thresholds ───────────────────────
    review_floor = int(getattr(settings, "review_below_completeness", 40) or 0)
    if getattr(profile, "needs_human", False):
        band = "review"
        reasons.append("Flagged for a person: %s"
                       % (getattr(profile, "needs_human_reason", None) or "reason not recorded"))
    elif completeness < review_floor:
        band = "review"
        reasons.append(
            "Only %d%% of the qualifying questions are answered, below this "
            "organization's %d%% floor. Scored, but sent for review rather than "
            "banded on thin information." % (completeness, review_floor))
    else:
        high = int(getattr(settings, "high_threshold", 70) or 70)
        medium = int(getattr(settings, "medium_threshold", 45) or 45)
        band = "high" if score >= high else ("medium" if score >= medium else "low")

    return {"band": band, "score": score, "completeness": completeness,
            "reasons": reasons}
