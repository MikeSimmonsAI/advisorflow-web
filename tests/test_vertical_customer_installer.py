"""THE THING BETWEEN PRODUCTION AND A DUPLICATE TENANT.

`scripts/install_vertical_customer.py` creates a customer organization in a
production database. The failure it exists to prevent is not a crash — it is
the quiet one: a second organization for a customer who already had one. Both
halves look right, the leads land in whichever the operator logged into, and
nobody notices for a month.

So the duplicate check is the part under test, and it is tested for being WIDE
rather than for being correct in the easy case. A false positive costs
somebody thirty seconds of reading; a false negative costs a customer two
tenants.

The second half is the layering rule the whole vertical design rests on: this
script names no customer. It takes a name, a slug and an industry, and the
vertical's own configuration comes from `industry_templates` — so a cleaning
company created through it opens a workspace with that trade's screens,
vocabulary and board without this file knowing the vertical exists.
"""
import importlib.util
import io
import pathlib
import tokenize

import pytest

from app.models.models import Organization
from app.services import industry_templates

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install_vertical_customer.py"


def _code(path=None):
    """The script with its comments and string literals removed.

    WHY NOT JUST SEARCH THE FILE. The guards below look for words that must
    not appear in this script — a customer's name, anything that sends. The
    script's own documentation explains at length that it invites nobody and
    sends nothing, so a plain substring search fails on the sentence promising
    the thing it is checking for. Tokenising and dropping comments and strings
    leaves the code, which is what the guards are actually about.
    """
    source = (path or SCRIPT).read_text(encoding="utf-8")
    kept = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        kept.append(tok.string)
    return " ".join(kept)


def _module():
    spec = importlib.util.spec_from_file_location("install_vertical_customer",
                                                  SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


installer = _module()


def _org(db_session, name, slug, industry="cleaning"):
    org = Organization(name=name, slug=slug, plan="trial", industry=industry)
    db_session.add(org)
    db_session.commit()
    return org


# ── the duplicate check ─────────────────────────────────────────────────────

def test_the_same_slug_is_caught(db_session):
    _org(db_session, "Acme Facility Services", "acme-facility-services")
    hits = installer._looks_like_duplicate(
        db_session, "Something Else Entirely", "acme-facility-services")
    assert [o.slug for o in hits] == ["acme-facility-services"]


def test_the_same_name_with_a_different_slug_is_caught(db_session):
    """THE ONE THAT ACTUALLY HAPPENS. Somebody creates the customer, it is not
    obvious it worked, and they create it again — and `unique_slug` politely
    hands the second one a slug that does not collide."""
    _org(db_session, "Brightline Janitorial", "brightline-janitorial")
    hits = installer._looks_like_duplicate(
        db_session, "Brightline Janitorial", "brightline-janitorial-2")
    assert [o.name for o in hits] == ["Brightline Janitorial"]


def test_a_distinctive_word_in_common_is_caught(db_session):
    """"Northgate Cleaning" and "Northgate Facility Group" are one customer
    typed twice far more often than they are two customers."""
    _org(db_session, "Northgate Cleaning", "northgate-cleaning")
    hits = installer._looks_like_duplicate(
        db_session, "Northgate Facility Group", "northgate-facility-group")
    assert [o.name for o in hits] == ["Northgate Cleaning"]


def test_a_common_word_in_common_is_not_evidence_of_anything(db_session):
    """THE FALSE POSITIVE THAT WOULD MAKE THE CHECK USELESS.

    If "Commercial", "Services" or "Group" counted, the first cleaning company
    on the platform would block every one after it, the operator would learn
    to pass --force-despite-similar every time, and the check would be gone.
    """
    _org(db_session, "Commercial Cleaning Services Group",
         "commercial-cleaning-services-group")
    hits = installer._looks_like_duplicate(
        db_session, "Riverbend Commercial Services Group", "riverbend-commercial")
    assert hits == []


def test_matching_ignores_case_and_punctuation(db_session):
    _org(db_session, "Kestrel & Vaughn, Inc.", "kestrel-vaughn")
    hits = installer._looks_like_duplicate(db_session, "KESTREL FACILITIES",
                                           "kestrel-facilities")
    assert [o.slug for o in hits] == ["kestrel-vaughn"]


def test_an_unrelated_customer_is_not_a_duplicate(db_session):
    _org(db_session, "Brightline Janitorial", "brightline-janitorial")
    assert installer._looks_like_duplicate(
        db_session, "Ridgeway Dental", "ridgeway-dental") == []


def test_the_noise_list_does_not_swallow_a_whole_name(db_session):
    """A name made ENTIRELY of common words matches nothing by word, so the
    slug and exact-name clauses are what has to catch it. They do."""
    _org(db_session, "The Cleaning Company", "the-cleaning-company")
    assert [o.slug for o in installer._looks_like_duplicate(
        db_session, "The Cleaning Company", "the-cleaning-company-2")] \
        == ["the-cleaning-company"]


# ── the layering rule ───────────────────────────────────────────────────────

def test_the_installer_names_no_customer_and_no_vertical():
    """The script is code; the customer is a row and the vertical is a
    template. A name here means somebody stopped passing arguments."""
    prose = SCRIPT.read_text(encoding="utf-8").lower()
    code = _code().lower()
    for name in ("atlantis", "brightpath", "almaguer", "daniel"):
        # Neither in the code nor in the documentation: a customer named in a
        # comment is still a customer this file knows about.
        assert name not in prose, "the installer names %r" % name
    assert "commercial cleaning blueprint" not in prose
    # The vertical is not named in the code either. It arrives as an argument
    # and resolves through the template registry.
    assert "cleaning" not in code, \
        "the installer hard-codes a vertical instead of taking one"


def test_the_installer_creates_through_the_one_existing_door():
    """NOT A SECOND WAY TO MAKE A TENANT.

    `configure_customer_workspace.py` refuses to create an organization on the
    grounds that "a second door into tenant creation is how two of the same
    customer appear". This script only holds if it calls the same two
    functions POST /god/customers calls, in one transaction — so that a
    customer can never exist without the launch record that makes them
    onboardable.
    """
    code = _code()
    assert "cp . create_customer" in code
    assert "impl_svc . start_for_organization" in code
    assert "commit = False" in code, \
        "the launch record must be written in the organization's transaction"
    assert code.count("db . commit ( )") == 1, \
        "two commits means a customer can be half-created"


def test_the_installer_sends_nothing_and_invites_nobody():
    """An organization existing is not a reason for anybody to get an account,
    and creating a customer must never put a message on a wire."""
    code = _code().lower()
    for forbidden in ("send_email", "send_sms", "invite", "invitation",
                      "twilio", "sendgrid", "notification"):
        assert forbidden not in code, "the installer references %r" % forbidden


def test_nothing_is_written_without_apply():
    """The dry run is the whole safety property of running this against
    production, so the guard is asserted rather than assumed."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert 'parser.add_argument("--apply"' in body
    assert "if not args.apply:" in body
    commit_at = body.index("db.commit()")
    guard_at = body.index("if not args.apply:")
    assert guard_at < commit_at, \
        "the dry-run guard has to come before the write, not after it"


# ── what a vertical customer gets on the day it is created ──────────────────

@pytest.mark.parametrize("spelling", ["cleaning", "janitorial",
                                      "Commercial Cleaning", "custodial"])
def test_a_cleaning_company_resolves_to_the_cleaning_template(spelling):
    """However the industry is typed at creation, the stored key is canonical
    — which is what the workspace's screens, board and skin are all keyed on.
    A raw spelling stored here is a customer who silently gets the generic
    shell."""
    assert industry_templates.normalize(spelling) == "cleaning"


def test_a_cleaning_company_inherits_its_screens_with_no_configuration():
    """No file, no column, no per-customer setup: the vertical's screens come
    from its industry. This is what makes the tenth cleaning company cost the
    same as the first."""
    screens = [v["key"] for v in industry_templates.workspace_views("cleaning")]
    assert screens == ["prospects", "follow-up", "walkthroughs"]


def test_a_cleaning_company_inherits_its_own_board_not_another_trades():
    """The stages are the walkthrough-centred progression, on the existing
    free-string column. Asserted because the failure mode is silent: a
    template with no board of its own falls back to generic sales stages and
    the customer works a pipeline that does not describe their business."""
    stages = [s["key"] for s in industry_templates.crm_stage_objects("cleaning")]
    for required in ("decision_maker_found", "walkthrough_offered",
                     "walkthrough_booked", "walkthrough_confirmed",
                     "walkthrough_completed"):
        assert required in stages, "the cleaning board has no %r stage" % required
    assert "job_booked" not in stages, \
        "the cleaning board still carries the home-services vocabulary"


def test_a_new_customer_starts_with_nothing_switched_on():
    """`create_customer` writes an explicit empty allow-list rather than NULL,
    because NULL means "every feature" for the orgs that predate entitlement.
    A brand-new customer inheriting everything is the opposite of the
    intention."""
    body = (ROOT / "app" / "services" / "customer_provisioning.py").read_text(
        encoding="utf-8")
    assert "enabled_features=json.dumps([])" in body
