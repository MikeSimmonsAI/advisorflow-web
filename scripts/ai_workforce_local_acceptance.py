"""ONE-COMMAND LOCAL ACCEPTANCE RUN - AI Workforce safe test mode (M1).

    python scripts/ai_workforce_local_acceptance.py
    python scripts/ai_workforce_local_acceptance.py --seed-only     # for a UI walkthrough

WHAT IT DOES, against a FRESH LOCAL SQLITE DATABASE ONLY:

    1. builds the schema;
    2. creates a synthetic brand, a synthetic (non-demo) customer account, an
       owner and an advisor - every address ends in .invalid;
    3. the brand offers AI Lead Qualifier, prices it as a quoted (no amount)
       catalogue item and the account holds it - the same T8/T2 rows a real
       brand admin creates;
    4. the platform allows TEST MODE (simulation) for that brand and account;
    5. the real worker's main() records one pass, so the worker is "running";
    6. unless --seed-only: hire -> answer setup -> Run test -> Start working
       -> a practice lead queued -> the background worker pass works it ->
       results read back. Prints a JSON proof and exits 0 only if every step
       held.

WHAT IT REFUSES. Any DATABASE_URL that is not a local sqlite file. It sets
AI_OPERATIONS_ENABLED for its own process only and never sets
AI_OPERATIONS_LIVE_SEND, so no live adapter can be resolved.

Prerequisites: `pip install -r requirements-dev.txt` (Python 3.11+).
"""

import argparse
import json
import os
import secrets
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Outside the repository on purpose, so a local run can never be committed.
DEFAULT_DB = "sqlite:///" + os.path.join(tempfile.gettempdir(),
                                         "ai_workforce_acceptance.db")


def _guard(url: str) -> None:
    if not url.startswith("sqlite:///"):
        sys.exit("REFUSED: this script only runs against a local sqlite file. "
                 "Got: %s" % url.split("@")[-1])


def _env(url: str) -> None:
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("JWT_SECRET", "local-acceptance-" + secrets.token_hex(16))
    if "ENCRYPTION_KEY" not in os.environ:
        from cryptography.fernet import Fernet
        os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    os.environ.setdefault("WEB_PUSH_AUTO_KEYS", "0")
    os.environ.setdefault("APP_ENV", "development")
    os.environ.pop("AI_OPERATIONS_LIVE_SEND", None)
    os.environ.pop("AI_WORKFORCE_KILL", None)
    os.environ["AI_OPERATIONS_ENABLED"] = "1"


def seed(db, *, owner_password: str):
    from app.models.models import Organization, Platform, User
    from app.services.ai_deployment import simulation as sim
    from app.services.auth_service import hash_password
    from app.services.workforce import activation as wf_activation
    from app.services.workforce import constants as WC
    from app.services.workforce import registry as wf_registry
    from app.services.workforce import service as wf_service

    wf_service.sync_templates(db)
    brand = (db.query(Platform).filter(Platform.slug == "acceptance-brand")
             .first())
    if brand is None:
        brand = Platform(name="Acceptance Brand (synthetic)",
                         slug="acceptance-brand", is_active=True)
        db.add(brand)
        db.flush()
    org = (db.query(Organization)
           .filter(Organization.slug == "sunny-day-synthetic").first())
    if org is None:
        org = Organization(name="Sunny Day Insurance (synthetic)",
                           slug="sunny-day-synthetic", plan="standard",
                           platform_id=brand.id, is_demo=False,
                           enabled_features=json.dumps(sim.FEATURES))
        db.add(org)
        db.flush()
    owner = db.query(User).filter(User.email == "owner@sunny-day.invalid").first()
    if owner is None:
        owner = User(organization_id=org.id, email="owner@sunny-day.invalid",
                     password_hash=hash_password(owner_password),
                     full_name="Pat Owner", role="org_admin",
                     must_change_password=False, is_active=True)
        db.add(owner)
    adv = db.query(User).filter(User.email == "advisor@sunny-day.invalid").first()
    if adv is None:
        adv = User(organization_id=org.id, email="advisor@sunny-day.invalid",
                   password_hash=hash_password(owner_password),
                   full_name="Alex Advisor", role="advisor",
                   must_change_password=False, is_active=True)
        db.add(adv)
    db.flush()

    wf_service.set_offering(db, platform_id=brand.id,
                            template_key="lead_qualifier", enabled=True)
    tpl = wf_registry.template("lead_qualifier")
    item = sim._catalogue_item(db, brand, key="acceptance-lead-qualifier",
                               name=tpl.name,
                               entitlement_key=tpl.entitlement_key)
    sim._set_terms(db, brand.id, "lead_qualifier", item.key)
    sim._purchase(db, org, item)
    for scope, sid in ((wf_activation.SCOPE_PLATFORM, ""),
                       (wf_activation.SCOPE_BRAND, brand.id),
                       (wf_activation.SCOPE_CUSTOMER, org.id)):
        wf_activation.set_state(db, scope, sid, WC.SIMULATION,
                                reason="local acceptance: test mode allowed")
    db.commit()
    return brand, org, owner, adv


def worker_pass() -> int:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_ai_workforce_worker",
        os.path.join(ROOT, "app", "jobs", "run_ai_workforce_worker.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main()


def journey(db, org, owner) -> dict:
    from app.models.workforce_models import AIEmployee, AIWorkItem
    from app.services.ai_deployment import lifecycle, readiness, test_mode
    from app.services.workforce import practice
    from app.services.workforce import queue as wf_queue

    steps = []

    def step(name, ok, **data):
        steps.append({"step": name, "passed": bool(ok), **data})
        return ok

    every_day = {"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00",
                 "end": "23:59"}
    dep = lifecycle.select(db, org=org, template_key="lead_qualifier",
                           actor=owner, provisioning_key="acceptance-lq")
    lifecycle.configure(db, dep, {
        "goal": "Find out which new leads are ready to talk.",
        "audience": {"statuses": ["new"]}, "channels": ["sms", "email"],
        "hours": every_day, "working_hours": every_day,
        "timezone": "America/Chicago", "good_lead": ["Wants a quote soon"],
        "handoff_to": owner.id, "handoff_user_id": owner.id}, actor=owner)
    readiness.refresh(db, dep)
    db.commit()
    step("hired_and_set_up", dep.employee_id is not None,
         deployment_id=dep.id, state=dep.state)

    report = test_mode.run_test(db, dep, actor=owner)
    db.commit()
    step("run_test_pass", report["verdict"] == "PASS",
         verdict=report["verdict"], ended=report.get("ended_state"),
         run_ids=report.get("run_ids"),
         worker_passes=report.get("worker_passes"),
         problems=report.get("problems"))

    started = test_mode.start(db, dep, actor=owner)
    db.commit()
    step("start_working_test_mode", started.get("started"),
         status=started.get("status"), blockers=started.get("blockers"))

    emp = db.get(AIEmployee, dep.employee_id)
    lead = practice.create_practice_lead(db, organization_id=org.id,
                                         assigned_to_id=owner.id)
    wf_queue.enqueue(db, emp, [lead.id], job_key=emp.job_role)
    db.commit()
    from app.services.workforce import outbound
    with outbound.use_simulated_adapters():
        code = worker_pass()
    db.expire_all()
    item = db.query(AIWorkItem).filter(AIWorkItem.subject_id == lead.id).one()
    step("background_worker_worked_the_queued_lead",
         code == 0 and item.state != "assigned",
         worker_exit=code, item_state=item.state)

    paused = test_mode.pause(db, dep, actor=owner)
    resumed = test_mode.resume(db, dep, actor=owner)
    stopped = test_mode.stop(db, dep, actor=owner)
    db.commit()
    step("pause_resume_stop", paused["status"] == test_mode.S_PAUSED
         and resumed["status"] == test_mode.S_TESTING
         and stopped["status"] == test_mode.S_STOPPED,
         paused=paused["status"], resumed=resumed["status"],
         stopped=stopped["status"])

    home = test_mode.home(db, org.id)
    step("results_from_real_rows",
         home["results"]["practice"]["qualified"] >= 1
         and home["results"]["real"]["finished"] == 0,
         practice=home["results"]["practice"], real=home["results"]["real"])
    return {"all_passed": all(s["passed"] for s in steps), "steps": steps,
            "real_outreach": 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("AIWF_ACCEPTANCE_DB",
                                                    DEFAULT_DB))
    ap.add_argument("--seed-only", action="store_true")
    ap.add_argument("--fresh", action="store_true",
                    help="delete the sqlite file first")
    args = ap.parse_args()
    _guard(args.db)
    if args.fresh:
        path = args.db[len("sqlite:///"):]
        if os.path.exists(path):
            os.remove(path)
    _env(args.db)

    from sqlalchemy.orm import Session
    import app.models.registry  # noqa: F401
    from app.deps import engine
    from app.models.models import Base
    Base.metadata.create_all(bind=engine)

    password = os.environ.get("AIWF_ACCEPTANCE_PASSWORD") or \
        ("Synthetic-" + secrets.token_hex(4))
    with Session(bind=engine) as db:
        brand, org, owner, adv = seed(db, owner_password=password)
        brand_id, org_id, owner_id = brand.id, org.id, owner.id
    code = worker_pass()
    out = {"database": args.db, "brand_id": brand_id,
           "organization_id": org_id,
           "sign_in": {"owner": "owner@sunny-day.invalid",
                       "advisor": "advisor@sunny-day.invalid",
                       "password": password},
           "worker_heartbeat_exit": code}
    if not args.seed_only:
        from app.models.models import Organization, User
        with Session(bind=engine) as db:
            out["journey"] = journey(db, db.get(Organization, org_id),
                                     db.get(User, owner_id))
    print(json.dumps(out, indent=2, default=str))
    if args.seed_only:
        return 0
    return 0 if out["journey"]["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
