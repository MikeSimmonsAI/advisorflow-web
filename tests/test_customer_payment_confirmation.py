"""The customer's own receipt screen must say what they actually bought.

═══════════════════════════════════════════════════════════════════════════
THE FALSE CLAIM THIS GUARDS, FOUND BY THE FIRST REAL TEST PAYMENT
═══════════════════════════════════════════════════════════════════════════
A customer paid a $1,500 ONE-TIME implementation fee. Stripe returned them
to `/billing?success=1&part=setup`, and the page said:

    "Subscription activated! Your plan is now live."

No subscription existed. None had been created. None was going to be —
`mode="payment"` cannot create one. The banner keyed off `success=1` alone
and assumed every checkout was a subscription checkout.

This is the same defect as the $3,500 combined charge, one layer up: the
billing engine kept the two obligations apart and the SCREEN merged them
back together. The place a customer is most entitled to an accurate answer
about what they just bought is the page confirming it.

The return URL has carried `part` since the split shipped. These tests pin
that the page reads it.
"""

import pathlib
import re

import pytest

BILLING_PAGE = pathlib.Path("frontend/src/pages/Billing.jsx")


def _source() -> str:
    return BILLING_PAGE.read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """Source with // and /* */ comments stripped.

    The comment above the fix quotes the wrong sentence verbatim in order to
    explain it. A naive substring search would match that explanation and
    report the bug as still present.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    return src


class TestTheConfirmationMatchesWhatWasPaid:
    def test_a_setup_payment_is_never_called_a_subscription(self):
        """The exact sentence that was wrong, forbidden from the code path.

        Not forbidden from the file — the comment explaining the fix quotes
        it — but from anything that can render.
        """
        code = _code_only(_source())
        assert "Subscription activated! Your plan is now live." not in code, (
            "this sentence rendered after a one-time setup payment; it must "
            "not be reachable unconditionally again")

    def test_the_page_reads_the_part_parameter(self):
        code = _code_only(_source())
        assert "searchParams.get('part')" in code, (
            "the return URL carries `part`; the confirmation must read it "
            "rather than assuming what was bought")

    def test_setup_wording_says_one_time_and_denies_a_subscription(self):
        """Saying "setup fee paid" is not enough on its own.

        A customer who has just been charged $1,500 needs to know two things:
        it is done, and it is NOT the start of a recurring charge. The second
        is the one that stops a support ticket.
        """
        code = _code_only(_source())
        setup_branch = code.split("part === 'setup'", 1)
        assert len(setup_branch) == 2, "no setup branch in the confirmation"
        branch = setup_branch[1].split("part === 'subscription'", 1)[0].lower()
        assert "one-time" in branch or "one time" in branch
        assert "does not start a subscription" in branch, (
            "the customer must be told this did NOT start recurring billing")

    def test_a_subscription_payment_still_says_so(self):
        """The fix must not make every message vague."""
        code = _code_only(_source())
        assert "part === 'subscription'" in code
        sub_branch = code.split("part === 'subscription'", 1)[1][:220].lower()
        assert "subscription" in sub_branch and "live" in sub_branch

    def test_an_unknown_part_claims_nothing_specific(self):
        """A confirmation that cannot tell should be vague, not confident.

        An older link, a hand-edited URL, or a future third obligation all
        land here. "Payment received" is true in every one of those cases;
        naming a product would be a guess printed as a receipt.
        """
        code = _code_only(_source())
        # Bound the search to the successMessage expression itself, and bound
        # it on a BLANK LINE rather than on punctuation. Two earlier versions
        # of this test failed on the boundary, not on the code under test:
        # splitting on ":" walked out of the ternary into the data-loading
        # callback below it, and splitting on ";" stopped early because the
        # setup wording contains one ("a one-time charge; it does not...").
        # Any delimiter that can appear inside a string literal is the wrong
        # delimiter for reading a string literal.
        expr = code.split("const successMessage", 1)[1].split("\n\n", 1)[0]
        arms = [ln.strip() for ln in expr.splitlines() if ln.strip()]
        fallback = arms[-1].lower()   # the last arm is the fallback

        assert "payment received" in fallback
        assert "subscription" not in fallback, (
            "the fallback must not assert a subscription it cannot verify")
        assert "setup" not in fallback
