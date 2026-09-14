"""A MODULE SOLD WITHOUT THE MODULE IT OPERATES ON.

WHAT WAS FOUND. Robert Okafor, an advisor in WUPA, opened his dashboard and got
a red banner over six repeated refusals, each one naming an internal feature key
and telling him an operator could fix it in a console he cannot open. WUPA's
allow-list held `campaigns`, `lead_cleanup`, `tier_config` and `crm` — and not
`leads`. The organization had 4,000 leads in the database and 778 assigned to
him. Fiber Cartel was in the same state with 237.

THE CAUSE WAS NOT A MISCLICK. The plan presets lived in `OrgManager.jsx`, in the
browser, and NONE of the four tiers contained `leads`. Every organization ever
configured from a preset was missing the platform's core module. It did no
visible harm while `leads` was ungated; the moment entitlements were enforced,
those customers lost the module the other four were operating on.

So the presets moved to the server beside the registry they draw from, every
tier gained `leads`, and an import-time assertion means a preset that ships a
dependency gap stops the process instead of configuring another customer into
that state quietly.
"""

import pytest

from app.services import entitlements as ent


# ── 1. the presets themselves ───────────────────────────────────────────────

@pytest.mark.parametrize("plan", sorted(ent.PLAN_FEATURES))
def test_every_plan_includes_the_core_module(plan):
    keys = ent.PLAN_FEATURES[plan]
    if keys is None:          # enterprise = everything
        return
    assert "leads" in keys, (
        "plan %r sells a lead platform without the lead module" % plan)


@pytest.mark.parametrize("plan", sorted(ent.PLAN_FEATURES))
def test_every_plan_names_only_registered_features(plan):
    keys = ent.PLAN_FEATURES[plan]
    if keys is None:
        return
    unknown = [k for k in keys if k not in ent.FEATURES]
    assert unknown == [], unknown


@pytest.mark.parametrize("plan", sorted(ent.PLAN_FEATURES))
def test_no_plan_ships_a_dependency_gap(plan):
    assert ent.dependency_gaps(ent.PLAN_FEATURES[plan]) == []


def test_an_unknown_plan_falls_back_rather_than_returning_nothing():
    """`None` means EVERYTHING here. An unknown plan must not mean that."""
    assert ent.plan_features("no-such-plan") == ent.PLAN_FEATURES["trial"]
    assert ent.plan_features(None) == ent.PLAN_FEATURES["trial"]
    assert ent.plan_features("enterprise") is None


def test_the_import_time_assertion_actually_rejects_an_incoherent_preset():
    """The guard is only worth having if it fails when it should."""
    with pytest.raises(RuntimeError) as exc:
        original = dict(ent.PLAN_FEATURES)
        try:
            ent.PLAN_FEATURES["bad"] = ["campaigns", "users"]   # campaigns, no leads
            ent._assert_presets_are_coherent()
        finally:
            ent.PLAN_FEATURES.clear()
            ent.PLAN_FEATURES.update(original)
    assert "incoherent" in str(exc.value)


# ── 2. the dependency model ─────────────────────────────────────────────────

def test_wupas_exact_configuration_is_reported_as_incoherent():
    """The allow-list found in production, verbatim."""
    wupa = ["master_dashboard", "reports", "users", "availability", "campaigns",
            "crm", "lead_cleanup", "tier_config", "branding_settings",
            "compliance", "audit_log"]
    gaps = ent.dependency_gaps(wupa)
    assert {g["feature"] for g in gaps} == {"campaigns", "lead_cleanup", "tier_config"}
    assert all(g["requires"] == "leads" for g in gaps)
    assert all("cannot function without it" in g["detail"] for g in gaps)


def test_a_coherent_allow_list_reports_nothing():
    assert ent.dependency_gaps(["leads", "campaigns", "lead_cleanup"]) == []


def test_a_legacy_open_organization_cannot_have_a_gap():
    """NULL is entitled to everything, so nothing can be missing."""
    assert ent.dependency_gaps(None) == []


def test_an_empty_allow_list_has_no_gaps_either():
    """Nothing enabled is coherent — it is switched off, not broken."""
    assert ent.dependency_gaps([]) == []


def test_the_map_does_not_grant_anything():
    """A dependency that silently widened an allow-list would make the stored
    configuration a lie, and an operator would never see the problem."""
    before = ["campaigns"]
    ent.dependency_gaps(before)
    assert before == ["campaigns"]

    class _Org:
        enabled_features = '["campaigns"]'
    assert ent.org_has_feature(_Org(), "leads") is False, \
        "a prerequisite must never be implied into the allow-list"


# ── 3. what the operator console is told ────────────────────────────────────

def test_feature_report_surfaces_the_gap_and_the_plan(db_session):
    from app.models.models import Organization
    import json
    import uuid

    org = Organization(name="Gap Co", slug="gap-" + uuid.uuid4().hex[:8],
                       plan="growth", is_active=True,
                       enabled_features=json.dumps(
                           ["campaigns", "lead_cleanup", "users"]))
    db_session.add(org)
    db_session.commit()

    report = ent.feature_report(org)
    assert report["plan"] == "growth"
    assert "leads" in report["plan_preset"]
    assert "leads" in report["below_plan"]
    assert {g["feature"] for g in report["dependency_gaps"]} == {"campaigns", "lead_cleanup"}
    # And the customer-facing shape is unchanged.
    assert report["mode"] == "allow_list"
    assert report["enabled"] == ["campaigns", "lead_cleanup", "users"]


def test_feature_report_is_quiet_for_a_coherent_organization(db_session):
    from app.models.models import Organization
    import json
    import uuid

    org = Organization(name="Fine Co", slug="fine-" + uuid.uuid4().hex[:8],
                       plan="trial", is_active=True,
                       enabled_features=json.dumps(ent.PLAN_FEATURES["trial"]))
    db_session.add(org)
    db_session.commit()

    report = ent.feature_report(org)
    assert report["dependency_gaps"] == []
    assert report["below_plan"] == []
