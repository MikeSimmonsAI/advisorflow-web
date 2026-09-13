"""NO VERTICAL'S VOCABULARY LEAKS INTO ANOTHER TENANT'S PRODUCT.

THE DEFECT. An energy customer, correctly configured as Energy & Procurement
with energy tiers already applied, opened the customer application and found a
funeral home's language: ARRANGEMENTS and ARRANGEMENT RATE on the overview,
Pre-Need and At-Need in the lead filters, Pre-Need / At-Need / Arrangements /
Services Complete / Aftercare Follow-up in the CRM, and a placeholder asking
whether the prospect still needed pre-need planning.

None of it was their configuration. It was another vertical's defaults, in
several separate maps, each of which fell back to FUNERAL when it did not
recognise the organization - and one of which was reached on every request
because the value it keyed off was never populated at all.

WHAT IS BEING DEFENDED, and the order matters:

  1. An organization gets ITS OWN business's words. Energy gets energy.
  2. A DEATHCARE ORGANIZATION STILL GETS DEATHCARE WORDS. Pre-Need and At-Need
     are legitimate configuration for a funeral home; the defect was treating
     them as the platform's defaults, not their existence.
  3. An organization the platform cannot place gets NEUTRAL words - never
     whichever vertical happens to be first in a dictionary.
  4. One organization's configuration never changes another's.
  5. Changing what a label SAYS never rewrites what a record STORES.
"""

import itertools
import json

import pytest

from app.models.models import Organization, User
from app.services import industry_templates as templates
from app.services.auth_service import hash_password


# "imminent" is deliberately NOT here. It is a tier VALUE a funeral home
# configures, and the composer matches on tier words to pick an appointment
# type — matching on a key renders nothing. The words below are ones that,
# appearing in a customer surface, are being SHOWN to somebody.
DEATHCARE_WORDS = ("pre-need", "pre_need", "at-need", "at_need", "arrangement",
                   "aftercare", "memorial", "marker")


def _has_deathcare(blob) -> bool:
    text = json.dumps(blob, default=str).lower()
    return any(word in text for word in DEATHCARE_WORDS)


# ── the registry itself ─────────────────────────────────────────────────────

def test_every_template_names_what_it_calls_an_appointment():
    """The overview's seven KPI labels are derived from this one noun. A
    template without it would silently fall back to neutral wording on a page
    the customer reads every morning."""
    for key, tpl in templates.TEMPLATES.items():
        assert tpl["vocabulary"].get("appointments"), key


def test_an_unknown_business_type_resolves_to_neutral_not_to_a_vertical():
    for value in (None, "", "something nobody has heard of", "widgets"):
        resolved = templates.resolve(value)
        assert resolved["key"] == templates.GENERIC_KEY, value
        assert not _has_deathcare(resolved), value


def test_the_generic_template_carries_no_vertical_vocabulary():
    assert not _has_deathcare(templates.TEMPLATES[templates.GENERIC_KEY])


def test_energy_resolves_to_energy_however_it_is_spelled():
    for spelling in ("energy", "Energy & Procurement", "energy_procurement",
                     "Energy / energy procurement", "utilities", "power"):
        assert templates.normalize(spelling) == "energy", spelling


def test_the_funeral_template_keeps_its_own_words():
    """DEATHCARE IS NOT BEING REMOVED. A funeral home configured as one is
    entitled to Pre-Need, At-Need and Arrangements."""
    tpl = templates.resolve("funeral")
    assert tpl["key"] == "funeral"
    assert _has_deathcare(tpl)


# ── CRM stages ──────────────────────────────────────────────────────────────

def test_crm_stages_resolve_per_industry_and_never_default_to_funeral():
    energy = templates.crm_stage_objects("energy")
    assert energy and not _has_deathcare(energy)
    generic = templates.crm_stage_objects("something unrecognised")
    assert generic and not _has_deathcare(generic)
    assert templates.crm_stage_objects(None) == generic


def test_a_funeral_organization_still_gets_the_funeral_pipeline():
    stages = templates.crm_stage_objects("funeral")
    keys = [s["key"] for s in stages]
    assert "pre_need" in keys and "at_need" in keys and "arrangements" in keys


def test_existing_stage_keys_are_preserved_exactly():
    """LIVE DATA IS NOT ORPHANED. A contact already sitting in `at_need` must
    still match a stage after this change, or its card loses its column."""
    for industry, expected in (
            ("funeral", "pre_need"),
            ("fiber", "pending_install"),
            ("roofing", "inspection_scheduled"),
            ("real_estate", "under_contract"),
            ("insurance", "underwriting"),
    ):
        keys = [s["key"] for s in templates.crm_stage_objects(industry)]
        assert expected in keys, industry


def test_every_stage_object_is_renderable():
    for industry in list(templates.TEMPLATES) + ["solar", "auto_repair", "medicare"]:
        for stage in templates.crm_stage_objects(industry):
            assert stage.get("key") and stage.get("label") and stage.get("color")


# ── what the customer application is served ─────────────────────────────────

def _org(db, *, name, slug, industry):
    org = Organization(name=name, slug=slug, plan="standard", industry=industry)
    db.add(org)
    db.commit()
    return org


def _user(db, org, *, email, role="advisor"):
    user = User(organization_id=org.id, email=email,
                password_hash=hash_password("TestPass123!"),
                full_name="Test Person", role=role, must_change_password=False)
    db.add(user)
    db.commit()
    return user


def _headers(db, user):
    from app.services.auth_service import create_access_token
    return {"Authorization": "Bearer " + create_access_token(user, db)}


_SEQ = itertools.count()


def _settings(client, db, org, *, role="advisor"):
    # A fresh person each call: some of these read the same organization twice
    # and `users.email` is unique across the platform.
    user = _user(db, org, role=role,
                 email="person%d@%s.example" % (next(_SEQ), org.slug))
    r = client.get("/org-settings/", headers=_headers(db, user))
    assert r.status_code == 200, r.text
    return r.json()


def test_an_energy_customer_is_served_energy_words(client, db_session):
    org = _org(db_session, name="Atlantis-like Energy Co", slug="energy-co",
               industry="energy")
    body = _settings(client, db_session, org)

    assert body["industry"] == "energy"
    assert not _has_deathcare(body), "deathcare vocabulary reached an energy tenant"

    tier_labels = [t["label"] for t in body["tier_config"]]
    assert "New Inquiry" in tier_labels
    assert "Rate Review" in tier_labels
    assert "Proposal Sent" in tier_labels
    assert "Contract Signed" in tier_labels
    assert "Renewal Due" in tier_labels


def test_an_energy_customer_gets_energy_crm_stages(client, db_session):
    org = _org(db_session, name="Energy Co", slug="energy-stages",
               industry="energy")
    body = _settings(client, db_session, org)
    assert body["crm_stages"], "no stages were served"
    assert not _has_deathcare(body["crm_stages"])


def test_an_energy_customer_gets_no_deathcare_kpi_noun(client, db_session):
    """The overview derives all seven KPI labels from this one word."""
    org = _org(db_session, name="Energy Co", slug="energy-kpi", industry="energy")
    body = _settings(client, db_session, org)
    noun = body["vocabulary"]["appointments"]
    assert noun
    assert "arrangement" not in noun.lower()


def test_a_deathcare_customer_still_gets_deathcare_words(client, db_session):
    """THE REGRESSION THAT MATTERS MOST. Removing the leak must not remove the
    vertical."""
    org = _org(db_session, name="A Funeral Home", slug="a-funeral-home",
               industry="funeral")
    body = _settings(client, db_session, org)
    assert body["industry"] == "funeral"
    assert _has_deathcare(body["tier_config"])
    assert _has_deathcare(body["crm_stages"])
    assert "arrangement" in body["vocabulary"]["appointments"].lower()


def test_a_generic_customer_gets_neutral_words(client, db_session):
    org = _org(db_session, name="Some Service Business", slug="generic-co",
               industry=None)
    body = _settings(client, db_session, org)
    assert body["industry"] == templates.GENERIC_KEY
    assert not _has_deathcare(body)


def test_an_unrecognised_business_type_is_neutral_and_says_so(client, db_session):
    org = _org(db_session, name="Widget Co", slug="widget-co",
               industry="artisanal widget restoration")
    body = _settings(client, db_session, org)
    assert body["industry_matched"] is False
    assert not _has_deathcare(body)


# ── tenant isolation ────────────────────────────────────────────────────────

def test_one_organizations_vocabulary_never_reaches_another(client, db_session):
    funeral = _org(db_session, name="A Funeral Home", slug="iso-funeral",
                   industry="funeral")
    energy = _org(db_session, name="An Energy Co", slug="iso-energy",
                  industry="energy")

    funeral_body = _settings(client, db_session, funeral)
    energy_body = _settings(client, db_session, energy)

    assert _has_deathcare(funeral_body)
    assert not _has_deathcare(energy_body)
    assert funeral_body["tier_config"] != energy_body["tier_config"]
    assert funeral_body["crm_stages"] != energy_body["crm_stages"]


def test_changing_one_organizations_business_type_leaves_another_alone(
        client, db_session):
    a = _org(db_session, name="Org A", slug="iso-a", industry="funeral")
    b = _org(db_session, name="Org B", slug="iso-b", industry="funeral")

    before_b = _settings(client, db_session, b)
    a.industry = "energy"
    db_session.commit()
    after_b = _settings(client, db_session, b)

    assert before_b["tier_config"] == after_b["tier_config"]
    assert before_b["crm_stages"] == after_b["crm_stages"]
    assert _has_deathcare(after_b)


# ── customized configuration outranks a template ────────────────────────────

def test_an_organizations_own_tier_configuration_wins(client, db_session):
    """A business that renamed its own pipeline keeps the names it chose. The
    template is a starting point, not an override."""
    org = _org(db_session, name="Custom Co", slug="custom-co", industry="energy")
    org.tier_config = json.dumps([
        {"value": "walk_in", "label": "Walk-In", "color": "blue"},
        {"value": "quoted", "label": "Quoted", "color": "amber"},
    ])
    db_session.commit()
    body = _settings(client, db_session, org)
    assert [t["label"] for t in body["tier_config"]] == ["Walk-In", "Quoted"]


def test_an_organizations_own_crm_stages_win(client, db_session):
    org = _org(db_session, name="Custom Stages Co", slug="custom-stages",
               industry="energy")
    org.crm_stages = json.dumps([
        {"key": "intake", "label": "Intake", "color": "#64748b"},
        {"key": "done", "label": "Done", "color": "#10b981"},
    ])
    db_session.commit()
    body = _settings(client, db_session, org)
    assert [s["label"] for s in body["crm_stages"]] == ["Intake", "Done"]


# ── nothing stored is rewritten ─────────────────────────────────────────────

def test_reading_terminology_writes_nothing(client, db_session):
    """DISPLAY LABELS ARE NOT DATA MIGRATIONS. Serving a business its own
    vocabulary must not touch a single stored value."""
    org = _org(db_session, name="Read Only Co", slug="read-only",
               industry="energy")
    before = (org.industry, org.tier_config, org.crm_stages)
    _settings(client, db_session, org)
    db_session.refresh(org)
    assert (org.industry, org.tier_config, org.crm_stages) == before


# ── the customer-facing bundle carries no other vertical's words ────────────

CUSTOMER_SURFACES = [
    "frontend/src/pages/Overview.jsx",
    "frontend/src/pages/Leads.jsx",
    "frontend/src/pages/CRM.jsx",
    "frontend/src/pages/EmailQueue.jsx",
    "frontend/src/pages/Pipeline.jsx",
    "frontend/src/pages/LeadDetail.jsx",
    "frontend/src/pages/Templates.jsx",
    "frontend/src/pages/TierDefinitions.jsx",
    "frontend/src/pages/OrgSettings.jsx",
    "frontend/src/pages/ImportBatches.jsx",
    "frontend/src/pages/CampaignBuilder.jsx",
    "frontend/src/terminology.js",
]

# One real customer, whose name was typed into the composer's subject-line
# generator and therefore into every other tenant's outgoing email.
CUSTOMER_NAMES = ("Restland",)

# Words that may legitimately appear in these files as CODE or as a comment
# explaining the defect, rather than as something a customer is shown.
_ALLOWED_CONTEXT = ("//", "/*", "*", "isDeathcare", "terminology.industry")


def _rendered_lines(path):
    """Lines that could put text on a customer's screen.

    Comments are excluded deliberately: the files explain the defect they fix,
    and naming it is how the next person understands why the literals are
    gone. A comment renders nothing.
    """
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full = os.path.join(root, path.replace("/", os.sep))
    if not os.path.exists(full):
        pytest.skip("%s is not present in this checkout" % path)
    out = []
    for raw in open(full, encoding="utf-8", errors="replace").read().splitlines():
        line = raw.strip()
        if not line or line.startswith(("//", "/*", "*")):
            continue
        out.append(line)
    return out


@pytest.mark.parametrize("path", CUSTOMER_SURFACES)
def test_no_customer_surface_hard_codes_another_vertical(path):
    """THE REGRESSION GUARD. Each of these files carried a vertical's words in
    a literal; a new one added later is the same defect returning."""
    offenders = []
    for line in _rendered_lines(path):
        lowered = line.lower()
        for word in DEATHCARE_WORDS:
            if word in lowered and not any(ok in line for ok in _ALLOWED_CONTEXT):
                offenders.append(line)
                break
    assert not offenders, "%s still renders deathcare wording:\n%s" % (
        path, "\n".join(offenders[:8]))


@pytest.mark.parametrize("path", CUSTOMER_SURFACES)
def test_no_customer_surface_names_another_customer(path):
    """A REAL CUSTOMER'S NAME IN A SHARED COMPOSER IS NOT A LABEL DEFECT.

    `smartSubject` in LeadDetail.jsx returned "Your family file at <a cemetery
    customer's name>" and it is the DEFAULT SUBJECT of the email that page
    sends, so an advisor who did not retype the field mailed their own prospect
    under somebody else's business name.
    """
    offenders = [line for line in _rendered_lines(path)
                 if any(name in line for name in CUSTOMER_NAMES)]
    assert not offenders, "%s names another customer:\n%s" % (
        path, "\n".join(offenders[:8]))
