# -*- coding: utf-8 -*-
"""THE LADDER ON THE OWNER'S PAGE.

Phase 6 shipped a `seller_progress` that read each of the five steps off its
own evidence, independently. That is correct field by field and wrong as a
sequence: title opens the moment the file goes over, which is routinely before
the earnest money has landed, so a real deal produced

    Offer done · Agreement done · Property review CURRENT ·
    Title & closing DONE · Closed upcoming

on the page belonging to the person whose house it is. Nobody found it in
review because the tests asserted the endpoint returned five steps and the
fixture happened to be well-ordered. It was found by opening the page.

So this file tests the SHAPE of the sequence, not the presence of the fields,
and it does it by constructing deals in states that are awkward on purpose:
facts arriving out of order, a closing date on a deal that has not been
agreed, a stage that has run ahead of the paperwork, and a stage that has
fallen behind it.

`seller_progress` reads plain attributes off a deal, so these are unit tests
against a stand-in rather than round trips through the API. The end-to-end
assertions — that this is what the endpoint actually publishes, and that no
internal stage name travels with it — are the last two tests, and they go
through the real route with a real token.
"""
import pytest

from app.services.wholesale_publication import (SELLER_STEPS, seller_progress)

STEP_KEYS = [k for k, _ in SELLER_STEPS]
ORDER = {k: i for i, k in enumerate(STEP_KEYS)}


class FakeDeal:
    """Every attribute `seller_progress` reads, defaulting to "nothing has
    happened yet". A test names only the facts it is about, which is what
    makes each one readable as a scenario."""

    _FIELDS = ("stage", "proposed_offer", "contract_price", "contract_date",
               "contract_signed_at", "seller_signed_at", "inspection_deadline",
               "earnest_money_received_at", "title_opened_at",
               "title_commitment_received_at", "closed_at")

    def __init__(self, **kw):
        for f in self._FIELDS:
            setattr(self, f, None)
        self.stage = kw.pop("stage", "")
        for k, v in kw.items():
            assert k in self._FIELDS, "no such field on a deal: %s" % k
            setattr(self, k, v)


def states(deal):
    return {s["key"]: s["state"] for s in seller_progress(deal)}


def as_list(deal):
    return [(s["key"], s["state"]) for s in seller_progress(deal)]


# ── The invariants. These hold for EVERY deal, in every state. ──────────────

SCENARIOS = [
    ("nothing has happened", FakeDeal()),
    ("an offer is out", FakeDeal(stage="offer_sent", proposed_offer=140000)),
    ("under contract", FakeDeal(stage="under_contract", proposed_offer=140000,
                                contract_date="2026-09-20")),
    ("title opened early", FakeDeal(stage="under_contract",
                                    proposed_offer=140000,
                                    contract_date="2026-09-20",
                                    title_opened_at="2026-09-22")),
    ("inspection set, no earnest money", FakeDeal(
        stage="under_contract", proposed_offer=140000,
        contract_date="2026-09-20", inspection_deadline="2026-10-06")),
    ("everything done", FakeDeal(
        stage="closed", proposed_offer=140000, contract_date="2026-09-20",
        inspection_deadline="2026-10-06", earnest_money_received_at="2026-09-25",
        title_opened_at="2026-09-22", closed_at="2026-11-08")),
    ("closed flag with nothing behind it", FakeDeal(stage="closed",
                                                    closed_at="2026-11-08")),
    ("a stage nobody has ever heard of", FakeDeal(stage="quantum_escrow")),
]


@pytest.mark.parametrize("name,deal", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_the_ladder_always_climbs_in_order(name, deal):
    """No step may be DONE after a step that is not.

    This is the whole bug, stated as a property. It has to hold for every
    scenario above, not for the one the fixture happens to build.
    """
    seq = as_list(deal)
    assert [k for k, _ in seq] == STEP_KEYS, "the steps must keep their order"
    seen_unfinished = None
    for key, state in seq:
        if state != "done":
            seen_unfinished = seen_unfinished or key
        elif seen_unfinished:
            pytest.fail("%s: %r is done but %r before it is not"
                        % (name, key, seen_unfinished))


@pytest.mark.parametrize("name,deal", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_there_is_exactly_one_current_step_unless_the_sale_is_finished(name, deal):
    """A person reading this asks one question: where am I? One answer."""
    seq = as_list(deal)
    current = [k for k, s in seq if s == "current"]
    if all(s == "done" for _, s in seq):
        assert current == [], "%s: a finished sale has nothing in progress" % name
    else:
        assert len(current) == 1, "%s: %d steps say 'in progress'" % (name, len(current))


@pytest.mark.parametrize("name,deal", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_nothing_is_upcoming_before_the_current_step(name, deal):
    """Done, then the one in progress, then the rest. No gaps, no islands."""
    seq = as_list(deal)
    current = [i for i, (_, s) in enumerate(seq) if s == "current"]
    if not current:
        return
    at = current[0]
    assert all(s == "done" for _, s in seq[:at]), \
        "%s: something before the current step is not done" % name
    assert all(s == "upcoming" for _, s in seq[at + 1:]), \
        "%s: something after the current step is not upcoming" % name


# ── The specific things the brief asks to be proved. ───────────────────────

def test_closing_information_alone_does_not_complete_the_earlier_steps():
    """THE ORIGINAL DEFECT, as a test.

    Title opens before the earnest money lands. Read literally that is
    "Title & closing: done" sitting under "Property review: in progress".
    """
    deal = FakeDeal(stage="under_contract", proposed_offer=140000,
                    contract_date="2026-09-20",
                    inspection_deadline="2026-10-06",
                    # Title is genuinely open …
                    title_opened_at="2026-09-22",
                    title_commitment_received_at="2026-09-23")
    # … and the earnest money has not arrived, so `review` is not satisfied.
    s = states(deal)
    assert s["review"] == "current"
    assert s["title"] == "upcoming", \
        "title showed as finished while the step before it was still running"
    assert s["closed"] == "upcoming"


def test_a_closed_at_with_no_transaction_behind_it_does_not_show_closed():
    """`closed` is the strongest claim on the page. It needs the whole ladder.

    Note what this does NOT assert. On a record this broken — a closing date
    with no offer, no contract and no title behind it — the ladder does show
    the early steps as done, because the recorded stage has passed them and a
    ladder with a hole in it reads as a bug to the owner. The claim that
    matters is the one at the end: the sale is not finished.
    """
    deal = FakeDeal(stage="closed", closed_at="2026-11-08")
    s = states(deal)
    assert s["closed"] != "done"
    assert s["title"] != "done"
    assert s["review"] != "done"


def test_closed_shows_done_only_when_the_whole_transaction_supports_it():
    deal = FakeDeal(stage="closed", proposed_offer=140000,
                    contract_date="2026-09-20",
                    inspection_deadline="2026-10-06",
                    earnest_money_received_at="2026-09-25",
                    title_opened_at="2026-09-22", closed_at="2026-11-08")
    assert states(deal)["closed"] == "done"
    assert all(s == "done" for s in states(deal).values())


def test_an_unknown_internal_stage_does_not_guess_forward():
    """A stage this module has never seen leaves the ladder at the beginning
    rather than assuming the deal is further along than it can prove."""
    deal = FakeDeal(stage="some_new_stage_added_next_quarter")
    s = states(deal)
    assert s["offer"] == "current"
    assert list(s.values()).count("done") == 0


def test_the_published_step_labels_are_seller_safe_and_fixed():
    """The five words an owner reads. Not the internal pipeline's words.

    A seller reading "Disposition" learns nothing except that they are a line
    item, so this asserts the vocabulary rather than trusting it to stay put.
    """
    labels = [label for _, label in SELLER_STEPS]
    assert labels == ["Offer", "Agreement", "Property review",
                      "Title & closing", "Closed"]
    blob = " ".join(labels).lower()
    for internal in ("disposition", "assignment", "wholesale", "mao",
                     "buyer", "spread", "under_contract", "lead"):
        assert internal not in blob


# ── Through the real route, with a real token. ─────────────────────────────
#
# A deliberately small fixture. The big `world` in test_wholesale_rooms.py
# plants every internal secret on one deal because that file is attacking the
# whole boundary; these two tests are about the LADDER, so they build the
# least deal that can have one and keep the failure message short.

def ok(response):
    assert response.status_code in (200, 201), \
        "%s %s" % (response.status_code, response.text[:400])
    return response.json()


@pytest.fixture
def seller_deal(client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers, json={
        "street_address": "1418 Cedar Springs Rd", "city": "Dallas",
        "state": "TX", "zip_code": "75201", "is_test": True,
    }))
    deal_id = prop["deal"]["id"]
    # Enough of a transaction that the ladder has somewhere to be.
    ok(client.patch("/wholesale/deals/%s/analysis" % deal_id,
                    headers=auth_headers, json={"proposed_offer": 140000}))
    ok(client.patch("/wholesale/deals/%s/contract" % deal_id,
                    headers=auth_headers,
                    json={"contract_price": 140000,
                          "contract_date": "2026-09-20"}))
    ok(client.patch("/wholesale/deals/%s/publication" % deal_id,
                    headers=auth_headers,
                    json={"seller_room_message": "We are moving along.",
                          "seller_room_contact_name": "Dana Reyes"}))
    ok(client.post("/wholesale/deals/%s/publication/state" % deal_id,
                   headers=auth_headers,
                   json={"audience": "seller", "published": True}))
    link = ok(client.post("/wholesale/deals/%s/share-links" % deal_id,
                          headers=auth_headers, json={"audience": "seller"}))
    return {"deal_id": deal_id, "token": link["token"]}


def test_the_seller_endpoint_publishes_no_internal_stage_name(
        client, auth_headers, seller_deal):
    """The stage DECIDES the ladder and must never travel with it."""
    res = client.get("/wholesale-rooms/seller/%s" % seller_deal["token"])
    assert res.status_code == 200
    text = res.text.lower()
    for internal in ("under_contract", "disposition", "assignment_pending",
                     "buyer_identified", "title_closing", "offer_sent",
                     "negotiating", "\"stage\""):
        assert internal not in text, "the seller page leaked %r" % internal


def test_a_written_update_to_the_owner_does_not_move_the_transaction(
        client, auth_headers, seller_deal):
    """Words are words.

    The operator writes a sentence for the owner. A message that could nudge
    the ladder would let a reassuring note ("we're basically closed!") become
    a claim about the transaction on the owner's own page.
    """
    token = seller_deal["token"]
    before = ok(client.get("/wholesale-rooms/seller/%s" % token))

    ok(client.patch("/wholesale/deals/%s/publication" % seller_deal["deal_id"],
                    headers=auth_headers,
                    json={"seller_room_message":
                          "All done - we have closed and the title is "
                          "recorded. Congratulations!"}))

    after = ok(client.get("/wholesale-rooms/seller/%s" % token))
    assert after["message"].startswith("All done")
    assert after["progress"] == before["progress"], \
        "a written update changed the published transaction state"


def test_the_endpoint_publishes_the_same_ladder_the_function_computes(
        client, auth_headers, seller_deal):
    """The unit tests above are only worth something if this is the same
    ladder the owner is actually served."""
    page = ok(client.get("/wholesale-rooms/seller/%s" % seller_deal["token"]))
    published = [(s["key"], s["state"]) for s in page["progress"]]
    assert [k for k, _ in published] == STEP_KEYS
    assert published.count(("offer", "done")) == 1
    # And the invariant, on the real payload rather than on a stand-in.
    seen_unfinished = None
    for key, state in published:
        if state != "done":
            seen_unfinished = seen_unfinished or key
        elif seen_unfinished:
            pytest.fail("%r is done but %r before it is not"
                        % (key, seen_unfinished))
