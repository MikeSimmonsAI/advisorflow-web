"""TRAINING AND READINESS. What this file defends.

  1. ONLY GOD ASSIGNS. Training assignment is a statement about what somebody
     is expected to be able to do on this platform, and there is one root
     authority for those.

  2. PROGRESS PERSISTS, PER STEP. Somebody returning after a week is put back
     where they were, and "who is ready" is answerable without asking them.

  3. A PRACTICE STEP CANNOT BE READ. The Demo Presenter path is not complete
     until the person has actually driven the demonstration, because the whole
     point is to stop the platform owner attending sales meetings — and that is
     not achieved by anybody's reading comprehension.

  4. THE LEARNER ROUTES CANNOT BE POINTED AT SOMEBODY ELSE. They take no user
     id at all, so there is no id to get wrong.
"""

import itertools

import pytest

from app.models.models import Platform, User
from app.models.training_models import (TRAINING_COMPLETE, TRAINING_IN_PROGRESS,
                                        TRAINING_NOT_STARTED,
                                        TrainingAssignment)
from app.services import capabilities, demo_content
from app.services import demo_environment as denv
from app.services import training_catalog as catalog
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _user(db, role="advisor", name="Person"):
    u = User(organization_id=None, email="t%d@example.com" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def world(db_session):
    plat = Platform(name="EvoSys Pro", slug="evo-t-%d" % next(_SEQ))
    db_session.add(plat)
    db_session.commit()
    god = _user(db_session, role="god_admin", name="Owner")
    learner = _user(db_session, name="Christina Torres")
    return dict(plat=plat, god=god, learner=learner)


def _assign(client, db, world, path_key, learner=None, **extra):
    return client.post("/god/training/assign", json={
        "user_id": (learner or world["learner"]).id, "path_key": path_key,
        **extra}, headers=_h(db, world["god"]))


# ═════════════════════════════════════════════════════════════════════════════
# 1. Only god assigns
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("role", ["advisor", "org_admin", "super_admin"])
def test_nobody_below_god_may_assign_training(client, db_session, world, role):
    actor = _user(db_session, role=role)
    r = client.post("/god/training/assign",
                    json={"user_id": world["learner"].id,
                          "path_key": "running_a_demo"},
                    headers=_h(db_session, actor))
    assert r.status_code == 403


@pytest.mark.parametrize("path", ["/god/training/readiness", "/god/training/paths"])
def test_the_readiness_report_is_god_only(client, db_session, world, path):
    actor = _user(db_session, role="super_admin")
    assert client.get(path, headers=_h(db_session, actor)).status_code == 403
    assert client.get(path, headers=_h(db_session, world["god"])).status_code == 200


def test_holding_demo_access_does_not_let_you_assign_training(client,
                                                              db_session,
                                                              world):
    p = _user(db_session)
    capabilities.set_platform_grants(db_session, p, world["plat"].id,
                                     "EvoSys Pro", world["god"],
                                     ["demo_suite", "demo_admin"], commit=True)
    r = client.post("/god/training/assign",
                    json={"user_id": world["learner"].id,
                          "path_key": "running_a_demo"},
                    headers=_h(db_session, p))
    assert r.status_code == 403


# ═════════════════════════════════════════════════════════════════════════════
# 2. Assignment
# ═════════════════════════════════════════════════════════════════════════════

def test_assignment_appears_for_the_learner(client, db_session, world):
    assert _assign(client, db_session, world, "salesperson_basics").status_code == 200
    body = client.get("/training/me",
                      headers=_h(db_session, world["learner"])).json()
    assert [a["key"] for a in body["assigned"]] == ["salesperson_basics"]
    assert body["assigned"][0]["assignment"]["status"] == TRAINING_NOT_STARTED
    # The unassigned list is shown deliberately: "not for you" and "not built"
    # must be distinguishable.
    assert body["available"]


def test_assigning_twice_does_not_create_two_records(client, db_session, world):
    _assign(client, db_session, world, "salesperson_basics")
    _assign(client, db_session, world, "salesperson_basics")
    assert (db_session.query(TrainingAssignment)
            .filter(TrainingAssignment.user_id == world["learner"].id,
                    TrainingAssignment.path_key == "salesperson_basics")
            .count()) == 1


def test_unknown_path_is_refused(client, db_session, world):
    assert _assign(client, db_session, world, "nope").status_code == 404


def test_revoking_keeps_the_completion_record(client, db_session, world):
    _assign(client, db_session, world, "salesperson_basics")
    path = catalog.get_path("salesperson_basics")
    for step in path["steps"]:
        client.post("/training/paths/salesperson_basics/complete",
                    json={"step": step["key"]},
                    headers=_h(db_session, world["learner"]))
    r = client.post("/god/training/revoke",
                    json={"user_id": world["learner"].id,
                          "path_key": "salesperson_basics"},
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 200
    row = (db_session.query(TrainingAssignment)
           .filter(TrainingAssignment.user_id == world["learner"].id).first())
    # DEACTIVATED, NEVER DELETED. "Was this person ever signed off?" is exactly
    # the question a revocation makes somebody ask.
    assert row is not None
    assert row.is_active is False
    assert row.status == TRAINING_COMPLETE


# ═════════════════════════════════════════════════════════════════════════════
# 3. Progress persists, per step
# ═════════════════════════════════════════════════════════════════════════════

def test_progress_persists_and_is_resumable(client, db_session, world):
    _assign(client, db_session, world, "salesperson_basics")
    steps = catalog.get_path("salesperson_basics")["steps"]
    r = client.post("/training/paths/salesperson_basics/complete",
                    json={"step": steps[0]["key"]},
                    headers=_h(db_session, world["learner"]))
    assert r.status_code == 200
    assert r.json()["assignment"]["status"] == TRAINING_IN_PROGRESS
    assert r.json()["assignment"]["completed_steps"] == 1

    # A fresh request — the position survives, which is the whole point.
    again = client.get("/training/paths/salesperson_basics",
                       headers=_h(db_session, world["learner"])).json()
    assert again["steps"][0]["done"] is True
    assert again["steps"][1]["done"] is False


def test_completing_every_step_completes_the_path(client, db_session, world):
    _assign(client, db_session, world, "salesperson_basics")
    for step in catalog.get_path("salesperson_basics")["steps"]:
        r = client.post("/training/paths/salesperson_basics/complete",
                        json={"step": step["key"]},
                        headers=_h(db_session, world["learner"]))
    assert r.json()["assignment"]["status"] == TRAINING_COMPLETE
    assert r.json()["assignment"]["completed_at"]


def test_a_step_can_be_un_ticked(client, db_session, world):
    _assign(client, db_session, world, "salesperson_basics")
    step = catalog.get_path("salesperson_basics")["steps"][0]["key"]
    client.post("/training/paths/salesperson_basics/complete",
                json={"step": step}, headers=_h(db_session, world["learner"]))
    r = client.post("/training/paths/salesperson_basics/uncomplete",
                    json={"step": step},
                    headers=_h(db_session, world["learner"]))
    assert r.json()["assignment"]["completed_steps"] == 0
    assert r.json()["assignment"]["status"] == TRAINING_NOT_STARTED


def test_an_unassigned_path_cannot_be_worked_through(client, db_session, world):
    r = client.get("/training/paths/salesperson_basics",
                   headers=_h(db_session, world["learner"]))
    assert r.status_code == 404
    r2 = client.post("/training/paths/salesperson_basics/complete",
                     json={"step": "one_record"},
                     headers=_h(db_session, world["learner"]))
    assert r2.status_code == 404


def test_one_learner_cannot_affect_another(client, db_session, world):
    other = _user(db_session, name="Someone Else")
    _assign(client, db_session, world, "salesperson_basics")
    _assign(client, db_session, world, "salesperson_basics", learner=other)
    step = catalog.get_path("salesperson_basics")["steps"][0]["key"]
    client.post("/training/paths/salesperson_basics/complete",
                json={"step": step}, headers=_h(db_session, world["learner"]))
    body = client.get("/training/paths/salesperson_basics",
                      headers=_h(db_session, other)).json()
    assert body["assignment"]["completed_steps"] == 0


# ═════════════════════════════════════════════════════════════════════════════
# 4. The Demo Presenter path is practised, not read
# ═════════════════════════════════════════════════════════════════════════════

def _build_and_grant(db, world):
    denv.build(db, world["plat"].id, actor=world["god"])
    capabilities.set_platform_grants(db, world["learner"], world["plat"].id,
                                     "EvoSys Pro", world["god"],
                                     ["demo_suite"], commit=True)
    return denv.get_environment(db, world["plat"].id)


def test_a_practice_step_cannot_be_completed_by_reading(client, db_session,
                                                        world):
    _build_and_grant(db_session, world)
    _assign(client, db_session, world, "running_a_demo")
    r = client.post("/training/paths/running_a_demo/complete",
                    json={"step": "practise_core"},
                    headers=_h(db_session, world["learner"]))
    assert r.status_code == 409
    # The refusal names the way out rather than just saying no.
    assert "Demo Suite" in r.json()["detail"]
    assert "lead to appointment" in r.json()["detail"]


def test_practising_in_the_demo_suite_unlocks_the_step(client, db_session,
                                                       world):
    _build_and_grant(db_session, world)
    _assign(client, db_session, world, "running_a_demo")
    learner = world["learner"]

    # The step is visibly blocked before the practice happens.
    body = client.get("/training/paths/running_a_demo",
                      headers=_h(db_session, learner)).json()
    core = [s for s in body["steps"] if s["key"] == "practise_core"][0]
    assert core["requires_practice"] is True
    assert core["practice_available"] is False

    for step in demo_content.get_scenario("lead_to_appointment")["steps"]:
        client.post("/demo-suite/%s/scenarios/lead_to_appointment/step"
                    % world["plat"].id, json={"step": step["key"]},
                    headers=_h(db_session, learner))

    body2 = client.get("/training/paths/running_a_demo",
                       headers=_h(db_session, learner)).json()
    core2 = [s for s in body2["steps"] if s["key"] == "practise_core"][0]
    assert core2["practice_available"] is True

    r = client.post("/training/paths/running_a_demo/complete",
                    json={"step": "practise_core"},
                    headers=_h(db_session, learner))
    assert r.status_code == 200


def test_the_whole_presenter_path_can_be_completed(client, db_session, world):
    _build_and_grant(db_session, world)
    _assign(client, db_session, world, "running_a_demo")
    learner = world["learner"]
    path = catalog.get_path("running_a_demo")

    for step in path["steps"]:
        scenario = step["practice_scenario"]
        if scenario:
            for s in demo_content.get_scenario(scenario)["steps"]:
                client.post("/demo-suite/%s/scenarios/%s/step"
                            % (world["plat"].id, scenario),
                            json={"step": s["key"]}, headers=_h(db_session, learner))
        r = client.post("/training/paths/running_a_demo/complete",
                        json={"step": step["key"]},
                        headers=_h(db_session, learner))
        assert r.status_code == 200, (step["key"], r.text)
    assert r.json()["assignment"]["status"] == TRAINING_COMPLETE


# ═════════════════════════════════════════════════════════════════════════════
# 5. Readiness, and what the owner sees
# ═════════════════════════════════════════════════════════════════════════════

def test_readiness_names_the_step_people_are_stuck_on(client, db_session,
                                                      world):
    a, b = _user(db_session, name="A"), _user(db_session, name="B")
    for u in (a, b):
        _assign(client, db_session, world, "salesperson_basics", learner=u)
        client.post("/training/paths/salesperson_basics/complete",
                    json={"step": "one_record"}, headers=_h(db_session, u))
    body = client.get("/god/training/readiness",
                      headers=_h(db_session, world["god"])).json()
    assert body["assigned"] == 2
    assert body["complete"] == 0
    # A finding, not an average.
    assert body["stuck_on"]
    assert body["stuck_on"][0]["people"] == 2
    assert all(p["next_step"] for p in body["people"])


def test_overdue_is_derived_not_stored(client, db_session, world):
    from datetime import datetime, timedelta
    yesterday = (datetime.utcnow() - timedelta(days=1)).isoformat()
    _assign(client, db_session, world, "salesperson_basics", due_at=yesterday)
    body = client.get("/training/me",
                      headers=_h(db_session, world["learner"])).json()
    assert body["assigned"][0]["assignment"]["overdue"] is True


def test_the_catalogue_only_contains_grounded_paths(client, db_session, world):
    """A path that practises a scenario must name one that exists."""
    for path in catalog.PATHS:
        for step in path["steps"]:
            key = step["practice_scenario"]
            if key:
                assert demo_content.get_scenario(key) is not None, \
                    (path["key"], step["key"], key)
                assert path["requires_demo"] is True
            if step.get("help_key"):
                assert demo_content.help_topic(step["help_key"]) is not None, \
                    (path["key"], step["key"], step["help_key"])


def test_training_reaches_the_context_payload_web_and_mobile_share(
        client, db_session, world):
    """`/auth/my-contexts` is the ONE authority truth both clients read."""
    _assign(client, db_session, world, "running_a_demo")
    capabilities.set_platform_grants(db_session, world["learner"],
                                     world["plat"].id, "EvoSys Pro",
                                     world["god"], ["demo_suite"], commit=True)
    r = client.get("/auth/my-contexts",
                   headers=_h(db_session, world["learner"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_demo_access"] is True
    assert [d["platform_name"] for d in body["demo_contexts"]] == ["EvoSys Pro"]
    assert body["training"]["assigned"] == 1
    assert body["training"]["paths"][0]["path_key"] == "running_a_demo"
    # Demo entitlement is NOT a context: it does not put anybody anywhere.
    assert all(c.get("type") != "demo" for c in body["contexts"])
