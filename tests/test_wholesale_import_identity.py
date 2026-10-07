"""Buyer-import idempotency rules (pure; no database needed).

Executed proof of the same cases lives in scripts/wholesale_import_outreach_proof.py.
"""
from types import SimpleNamespace

from app.services import wholesale_import_identity as ident


def _b(i, email=None, phone=None):
    return SimpleNamespace(id=i, email=email, phone=phone)


def test_retry_skips_existing_by_email_or_phone():
    idx = ident.build_index([_b("b1", "a@x.com", "214-555-0100")])
    assert ident.decide("A@X.com", None, idx, {}, 2)["action"] == ident.SKIP
    assert ident.decide(None, "(214) 555-0100", idx, {}, 2)["action"] == ident.SKIP
    assert ident.decide("new@x.com", None, idx, {}, 2)["action"] == ident.CREATE


def test_ambiguous_match_fails_closed():
    idx = ident.build_index([_b("b1", "a@x.com"), _b("b2", None, "9725550111")])
    assert ident.decide("a@x.com", "972-555-0111", idx, {}, 2)["action"] == ident.REJECT
    dup = ident.build_index([_b("b1", "a@x.com"), _b("b2", "a@x.com")])
    assert ident.decide("a@x.com", None, dup, {}, 2)["action"] == ident.REJECT


def test_duplicate_rows_within_file_skipped():
    seen = {}
    assert ident.decide("n@x.com", None, {}, seen, 2)["action"] == ident.CREATE
    ident.claim("n@x.com", None, seen, 2)
    verdict = ident.decide("N@x.com", None, {}, seen, 3)
    assert verdict["action"] == ident.SKIP and "row 2" in verdict["reason"]


def test_index_is_only_what_caller_supplies_org_scope():
    assert ident.decide("a@x.com", None, ident.build_index([]), {}, 2)["action"] == ident.CREATE
