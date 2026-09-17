"""/outcomes/summary counts in SQL now. The numbers must not have moved.

THE DEFECT. This endpoint sits on the Overview page - one of the ~22 requests a
dashboard load fires - and it used to answer by loading every LeadOutcome row
in the organization as a fully-hydrated ORM object, then producing two integers
from them with `len()` and a `sum()` over a boolean. A customer with three
years of appointments materialised three years of appointments to render two
tiles, on a 512 MB instance that was restarting out of memory.

THE RISK IN FIXING IT. Moving an aggregation from Python to SQL is exactly the
kind of change that silently shifts a number - NULL handling, a `== True` that
becomes `IS NOT NULL`, a join that starts multiplying rows. So this file pins
the arithmetic against cases built by hand, including the ones where SQL and
Python disagree if you are careless:

  * resulted_in_sale left NULL, which is neither True nor False
  * sale_items NULL, empty, and whitespace-only
  * the same free-text string recorded many times, which the rewrite now groups
    in SQL and weights by its count instead of iterating row by row
  * a second organization's outcomes, which must not appear in either number

The grouping is the subtle one. `top_sale_items` cannot be done entirely in SQL
because the column is comma-separated free text, so the split stays in Python -
but the rows are grouped first, so the loop runs over distinct strings weighted
by occurrence rather than over every outcome. Same answer, unless the weighting
is wrong, which is what test_repeated_items_are_weighted_by_occurrence checks.
"""

from datetime import datetime, timezone

import pytest

from app.models.models import LeadOutcome


def _outcome(db, lead, advisor, sale=False, items=None):
    row = LeadOutcome(
        lead_id=lead.id,
        recorded_by_id=advisor.id,
        appointment_date=datetime.now(timezone.utc).replace(tzinfo=None),
        resulted_in_sale=sale,
        sale_items=items,
    )
    db.add(row)
    db.commit()
    return row


def _summary(client, auth_headers):
    r = client.get("/outcomes/summary", headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()


# ── the counts ──────────────────────────────────────────────────────────────

def test_empty_org_reports_zeroes_not_a_division_error(client, auth_headers):
    body = _summary(client, auth_headers)
    assert body["total_appointments"] == 0
    assert body["sales_count"] == 0
    assert body["conversion_rate"] == 0
    assert body["top_sale_items"] == []


def test_total_and_sales_counts(client, db_session, sample_lead, sample_advisor, auth_headers):
    for sale in (True, True, False, False, False):
        _outcome(db_session, sample_lead, sample_advisor, sale=sale)
    body = _summary(client, auth_headers)
    assert body["total_appointments"] == 5
    assert body["sales_count"] == 2
    assert body["conversion_rate"] == 40


def test_a_null_resulted_in_sale_is_not_a_sale(
        client, db_session, sample_lead, sample_advisor, auth_headers):
    """The column is nullable. `sum(1 for o if o.resulted_in_sale)` skipped NULL
    and so must `== True`; a `!= False` would have counted it."""
    _outcome(db_session, sample_lead, sample_advisor, sale=True)
    _outcome(db_session, sample_lead, sample_advisor, sale=None)
    body = _summary(client, auth_headers)
    assert body["total_appointments"] == 2
    assert body["sales_count"] == 1


def test_conversion_rate_rounds_the_way_it_always_did(
        client, db_session, sample_lead, sample_advisor, auth_headers):
    for sale in (True, False, False):
        _outcome(db_session, sample_lead, sample_advisor, sale=sale)
    assert _summary(client, auth_headers)["conversion_rate"] == 33


# ── the free-text top items ─────────────────────────────────────────────────

def test_top_items_splits_and_lowercases(
        client, db_session, sample_lead, sample_advisor, auth_headers):
    _outcome(db_session, sample_lead, sample_advisor, sale=True, items="Marker, Vault")
    _outcome(db_session, sample_lead, sample_advisor, sale=True, items="marker")
    items = {i["item"]: i["count"] for i in _summary(client, auth_headers)["top_sale_items"]}
    assert items == {"marker": 2, "vault": 1}


def test_repeated_items_are_weighted_by_occurrence(
        client, db_session, sample_lead, sample_advisor, auth_headers):
    """The heart of the rewrite.

    Ten rows carrying the same string collapse to ONE grouped row with a count
    of ten. If the weighting were dropped, this would report 1 instead of 10 -
    which is the only way the optimisation could be silently wrong.
    """
    for _ in range(10):
        _outcome(db_session, sample_lead, sample_advisor, sale=True, items="urn")
    _outcome(db_session, sample_lead, sample_advisor, sale=True, items="urn, plot")
    items = {i["item"]: i["count"] for i in _summary(client, auth_headers)["top_sale_items"]}
    assert items["urn"] == 11
    assert items["plot"] == 1


def test_null_and_blank_sale_items_are_ignored(
        client, db_session, sample_lead, sample_advisor, auth_headers):
    _outcome(db_session, sample_lead, sample_advisor, sale=True, items=None)
    _outcome(db_session, sample_lead, sample_advisor, sale=True, items="")
    _outcome(db_session, sample_lead, sample_advisor, sale=True, items="   ")
    _outcome(db_session, sample_lead, sample_advisor, sale=True, items=" , ,")
    _outcome(db_session, sample_lead, sample_advisor, sale=True, items="casket")
    body = _summary(client, auth_headers)
    assert body["total_appointments"] == 5
    assert body["top_sale_items"] == [{"item": "casket", "count": 1}]


def test_only_the_top_three_come_back(
        client, db_session, sample_lead, sample_advisor, auth_headers):
    _outcome(db_session, sample_lead, sample_advisor, items="a,b,c,d,e")
    for _ in range(3):
        _outcome(db_session, sample_lead, sample_advisor, items="a")
    for _ in range(2):
        _outcome(db_session, sample_lead, sample_advisor, items="b")
    top = _summary(client, auth_headers)["top_sale_items"]
    assert len(top) == 3
    assert top[0] == {"item": "a", "count": 4}
    assert top[1] == {"item": "b", "count": 3}


# ── scope, which an aggregation rewrite must not widen ──────────────────────

def test_another_organizations_outcomes_are_not_counted(
        client, db_session, sample_lead, sample_advisor, auth_headers,
        sample_org):
    """The comment in the router is explicit that a count tile built from a
    wider query than the list above it tells an advisor the size of everybody
    else's book. Moving to SQL must not quietly widen the scope."""
    from app.models.models import Lead, Organization, User
    from app.services.auth_service import hash_password

    other_org = Organization(name="Some Other Company", slug="other-co-outcomes")
    db_session.add(other_org)
    db_session.commit()
    other_user = User(
        email="other-outcomes@example.com",
        password_hash=hash_password("TestPass123!"),
        full_name="Other Advisor",
        role="advisor",
        organization_id=other_org.id,
        must_change_password=False,
    )
    db_session.add(other_user)
    db_session.commit()
    other_lead = Lead(
        first_name="Not", last_name="Yours", phone="+15550001111",
        organization_id=other_org.id, assigned_to_id=other_user.id,
    )
    db_session.add(other_lead)
    db_session.commit()

    _outcome(db_session, sample_lead, sample_advisor, sale=True, items="mine")
    for _ in range(9):
        _outcome(db_session, other_lead, other_user, sale=True, items="theirs")

    body = _summary(client, auth_headers)
    assert body["total_appointments"] == 1
    assert body["sales_count"] == 1
    assert [i["item"] for i in body["top_sale_items"]] == ["mine"]


def test_the_endpoint_does_not_materialise_outcome_rows():
    """The regression itself: a `db.query(LeadOutcome)` with no projection.

    Every assertion above stays green if somebody restores the `.all()` and
    counts in Python, because the numbers would be identical. Only the shape of
    the query tells you whether the memory fix survived.
    """
    import ast
    import inspect
    import textwrap

    from app.routers import outcomes_router

    src = textwrap.dedent(inspect.getsource(outcomes_router.get_outcomes_summary))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "query":
            continue
        args = [ast.dump(a) for a in node.args]
        bare_model = any(a == ast.dump(ast.Name(id="LeadOutcome", ctx=ast.Load()))
                         for a in args)
        assert not bare_model, (
            "get_outcomes_summary queries LeadOutcome as a whole mapped entity "
            "again. It renders two counts and a top-three list; loading every "
            "row to do that is the defect this was fixed for.")
