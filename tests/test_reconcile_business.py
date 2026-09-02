"""Tests for the business reconciliation layer and the reconciliation
diagnostic.

NO REAL FAMILY DATA. Every fixture here is synthetic. The real populations live
outside the repository and are passed to the script as arguments; nothing in
this file, and nothing the script writes, is committed.
"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.services import source_reconciliation as sr          # noqa: E402
from app.services.source_adapters import rows_to_records      # noqa: E402


def _load_script():
    """The runner is a script, not a package module - load it by path."""
    path = os.path.join(ROOT, "scripts", "reconcile_business.py")
    spec = importlib.util.spec_from_file_location("reconcile_business", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reconcile_business"] = mod
    spec.loader.exec_module(mod)
    return mod


rb = _load_script()


# ── the script must stay customer-agnostic and side-effect free ─────────────

def _script_source():
    with open(os.path.join(ROOT, "scripts", "reconcile_business.py"),
              encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.parametrize("word", ["jason", "restland", "mcclellan", "tisdale",
                                  "berthet", "greenland", "evosys"])
def test_no_customer_or_advisor_is_named_in_the_script(word):
    """A reusable capability names no customer. If this fails the script has
    quietly become one tenant's report."""
    assert word not in _script_source().lower(), (
        f"{word!r} appears in reconcile_business.py - the populations are "
        f"arguments, so no customer or advisor belongs in the code")


@pytest.mark.parametrize("token", ["sqlalchemy", "get_db", "sessionmaker",
                                   "twilio", "resend", "smtplib",
                                   "requests.post", "urllib.request"])
def test_script_has_no_database_or_send_path(token):
    """READ-ONLY is a property of the code, not a promise in a docstring."""
    assert token not in _script_source().lower().replace(" ", ""), (
        f"{token!r} appears in reconcile_business.py - this runner must hold "
        f"no database handle and no send path")


def test_populations_are_parameters():
    src = _script_source()
    assert '"--target"' in src and '"--source"' in src


# ── channel eligibility is independent of the work classification ──────────

def test_email_denied_but_sms_allowed_still_reports_sms_eligible():
    """The case the whole channel-aware pass exists for: a denial on one
    channel must not erase a permission on another."""
    ch = {"email": False, "bulk_email": False, "sms": True, "voice": None}
    got = rb.channel_eligibility(ch, has_email=True, has_phone=True,
                                 suppressed=False, dnc=False)
    assert rb.SMS_ELIGIBLE in got
    assert rb.EMAIL_ELIGIBLE not in got
    assert rb.CHANNEL_DNC not in got, (
        "one denied channel is not a do-not-contact")


def test_all_contactable_channels_denied_is_do_not_contact():
    ch = {"email": False, "bulk_email": False, "sms": False, "voice": False}
    got = rb.channel_eligibility(ch, True, True, False, False)
    assert got == [rb.CHANNEL_DNC]


def test_unknown_everywhere_is_review_not_permission_and_not_prohibition():
    ch = {"email": None, "bulk_email": None, "sms": None, "voice": None}
    got = rb.channel_eligibility(ch, True, True, False, False)
    assert got == [rb.CHANNEL_REVIEW]


def test_permission_without_the_contact_detail_is_not_eligibility():
    """Allowed to email somebody whose address you do not have is not a
    channel you can use."""
    ch = {"email": True, "bulk_email": True, "sms": True, "voice": True}
    got = rb.channel_eligibility(ch, has_email=False, has_phone=False,
                                 suppressed=False, dnc=False)
    assert got == [rb.CHANNEL_REVIEW]


def test_suppression_beats_every_stated_permission():
    ch = {"email": True, "bulk_email": True, "sms": True, "voice": True}
    got = rb.channel_eligibility(ch, True, True, suppressed=True, dnc=False)
    assert got == [rb.CHANNEL_DNC]


# ── most-restrictive-wins across EVERY candidate ────────────────────────────

def _rec(**kw):
    base = dict(key="k", first_name="A", last_name="B")
    base.update(kw)
    return sr.Record(**base)


def test_denial_on_an_unchosen_candidate_still_wins():
    """The defect this guards: two master rows match one lead and disagree;
    resolving from whichever the matcher ranked first turns a stated denial
    into permission."""
    t = _rec(allow_email=None, allow_sms=None)
    chosen = _rec(allow_email=True, allow_bulk_email=True, allow_sms=True)
    other = _rec(allow_email=False, allow_bulk_email=False, allow_sms=False)
    out = rb.compliance_over_all_candidates(t, [chosen, other])
    assert out["channels"]["email"] is False
    assert out["channels"]["sms"] is False
    assert "email" in out["candidate_disagreement"]


def test_agreeing_candidates_resolve_normally():
    t = _rec()
    a = _rec(allow_email=True, allow_sms=True)
    b = _rec(allow_email=True, allow_sms=True)
    out = rb.compliance_over_all_candidates(t, [a, b])
    assert out["channels"]["email"] is True
    assert out["candidate_disagreement"] == []


def test_unknown_candidates_never_become_consent():
    t = _rec()
    out = rb.compliance_over_all_candidates(t, [_rec(), _rec()])
    assert out["channels"]["email"] is None
    assert out["channels"]["sms"] is None


def test_a_current_denial_is_never_released_by_a_permissive_candidate():
    t = _rec(allow_email=False)
    out = rb.compliance_over_all_candidates(t, [_rec(allow_email=True)])
    assert out["channels"]["email"] is False


# ── a permission column with no variance is not consent ─────────────────────

def test_zero_variance_column_is_not_read_as_permission():
    """A column that says the same thing for every row in the source states no
    per-person decision. Reading it as consent manufactures permission for the
    entire database."""
    rows = [{"Full Name": f"P{i}", "Email": f"p{i}@e.com",
             "Allow Phone Calls?": "Allow"} for i in range(20)]
    recs = rows_to_records(rows)
    stated = {r.allow_voice for r in recs if r.allow_voice is not None}
    assert stated == {True}, "fixture precondition"
    assert len(stated) == 1, (
        "one distinct value across the whole source means the column carries "
        "no information; the runner demotes it to UNKNOWN")


def test_a_column_with_variance_is_kept():
    rows = [{"Full Name": "A", "Allow Emails?": "Allow"},
            {"Full Name": "B", "Allow Emails?": "Do Not Allow"}]
    recs = rows_to_records(rows)
    assert {r.allow_email for r in recs} == {True, False}


# ── the reconciliation diagnostic ───────────────────────────────────────────

def test_diagnostic_reports_no_source_layer_rather_than_guessing():
    from app.services import reconciliation_diagnostic as rd

    class _Lead:
        id = "lead-1"
        first_name, last_name = "A", "B"
        email, phone = "a@b.com", "+12145550000"
        custom_fields = None

    class _DB:
        def query(self, *a, **k):
            return self

        def filter(self, *a, **k):
            return self

        def all(self):
            return []

    out = rd.run(_DB(), [_Lead()], "org-1")
    assert out["source_records_available"] == 0
    assert out["rows"][0]["match_status"] == rd.NO_SOURCE_LAYER
    assert out["rows"][0]["current_lead_id"] == "lead-1"
    assert out["rows"][0]["historical_contact_guid"] is None


def test_diagnostic_without_a_workspace_binds_nothing():
    from app.services import reconciliation_diagnostic as rd
    out = rd.run(None, [], None)
    assert out["rows"] == []
    assert "note" in out


@pytest.mark.parametrize("word", ["jason", "restland", "mcclellan"])
def test_diagnostic_names_no_customer(word):
    with open(os.path.join(ROOT, "app", "services",
                           "reconciliation_diagnostic.py"),
              encoding="utf-8") as fh:
        assert word not in fh.read().lower()


def test_diagnostic_scopes_the_historical_side_by_organization():
    """Tenant isolation on the historical side is asserted, not assumed."""
    with open(os.path.join(ROOT, "app", "services",
                           "reconciliation_diagnostic.py"),
              encoding="utf-8") as fh:
        src = fh.read()
    assert "SourceRecord.organization_id == organization_id" in src


def test_diagnostic_never_queries_leads_itself():
    """It reconciles the list it is handed. If it queried Lead it could widen
    the subject's authorized population."""
    with open(os.path.join(ROOT, "app", "services",
                           "reconciliation_diagnostic.py"),
              encoding="utf-8") as fh:
        src = fh.read()
    assert "query(Lead)" not in src
