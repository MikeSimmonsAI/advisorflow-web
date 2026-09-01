"""GATE 35 - QUALIFICATION NARROWS AUTHORIZATION. IT NEVER WIDENS IT.

    AUTHORIZED SCOPE  ->  QUALIFICATION      yes
    QUALIFICATION     ->  AUTHORIZED SCOPE   never

The whole risk of a qualification engine is that it becomes a second way to
select leads, and a second selector is a second place tenancy can be decided.
So the first half of this gate is not about qualification at all: it asks an
advisor to qualify a colleague's lead and another tenant's lead, by id, through
every entry point, and requires the same refusal lead_scope already gives.

The second half is the control: EXCLUDED and REVIEW_REQUIRED must not reach the
email queue, at any batch size, and the compliance rules that were already
proven - DNC, suppression, unusable address, internal record, duplicate - must
still block after being routed through a new service.

THE FIXTURE IS THE SHAPE OF A REAL BOOK, not a set of happy rows:

  Ada     advisor, Northwind      - the caller. 12 leads, one of each condition
  Ben     advisor, Northwind      - a colleague. His leads must stay his
  Cara    org_admin, Northwind    - sees the workspace, so scope != qualification
  Otto    advisor, Other Tenant   - cross-tenant, must be invisible

Every REFUSED check is paired with an ALLOWED one. A build that excludes
everybody passes a compliance probe perfectly and ships a product that can
never send anything.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="qual_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                               # noqa: E402
from app.main import app                                                # noqa: E402
from app.deps import SessionLocal, engine                               # noqa: E402
from app.models.models import (                                         # noqa: E402
    Base, Platform, Organization, User, Lead, SuppressionEntry, Reply,
)
from app.models.qualification_models import QualificationRule           # noqa: E402
from app.services import qualification                                  # noqa: E402
from app.services.auth_service import hash_password                     # noqa: E402

PW = "ProbeTest!2026"
FAILED, BROKEN, PASSED = [], [], []

NW = "org-northwind"
OT = "org-othertenant"


def refused(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "OPEN ", label,
                         ("\n          -> " + str(detail)[:200]) if detail else ""))
    (PASSED if ok else FAILED).append(label)


def allowed(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "BROKE", label,
                         ("\n          -> " + str(detail)[:200]) if detail else ""))
    (PASSED if ok else BROKEN).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 66 - len(t)))


def _code_only(path):
    """A python file with its comments and docstrings removed, lowercased.

    Uses the tokenizer rather than a regex, so it cannot be fooled by a '#'
    inside a string or by a docstring that contains code-looking text.
    """
    import io
    import tokenize
    src = open(path, encoding="utf-8", errors="replace").read()
    out = []
    prev_type = tokenize.INDENT
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            # A STRING that is the whole of its logical line is a docstring.
            if tok.type == tokenize.STRING and prev_type in (
                    tokenize.INDENT, tokenize.NEWLINE, tokenize.NL,
                    tokenize.DEDENT, tokenize.ENCODING):
                prev_type = tok.type
                continue
            if tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                                tokenize.DEDENT, tokenize.ENCODING):
                out.append(tok.string)
            prev_type = tok.type
    except Exception:
        # A file that will not tokenize is a real problem, but not this gate's
        # problem to hide - fall back to the raw text so the check still runs.
        return src.lower()
    return " ".join(out).lower()


def token(c, email, password=None):
    r = c.post("/auth/login", data={"username": email, "password": password or PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s" % (email, r.text[:300]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


# ── the fixture ─────────────────────────────────────────────────────────────
#
# ONE LEAD PER CONDITION, each one a shape that has actually turned up in a
# production book. The ids are readable on purpose: a failing assertion names
# the condition rather than a uuid.
LEADS = [
    # id            expected bucket   why
    ("ld-clean",    "READY",    "valid address, nothing against it"),
    ("ld-warm",     "READY",    "existing customer, replied before - HIGH"),
    ("ld-dnc",      "EXCLUDED", "status dnc"),
    ("ld-removeall","EXCLUDED", "manual_flag remove_all"),
    ("ld-bademail", "EXCLUDED", "manual_flag bad_email"),
    ("ld-noemail",  "EXCLUDED", "no email address"),
    ("ld-badaddr",  "EXCLUDED", "email is not a deliverable address"),
    ("ld-placeholder", "EXCLUDED", "unknown@unknown placeholder from import"),
    ("ld-test",     "EXCLUDED", "internal test record"),
    ("ld-dupe",     "EXCLUDED", "unresolved duplicate"),
    ("ld-roleaddr", "REVIEW",   "info@ shared mailbox - a person should decide"),
    ("ld-untiered", "REVIEW",   "needs_tier_review - not yet classified"),
]


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add(Platform(id="plt", name="Probe Platform", slug="probe"))
    db.flush()
    db.add_all([
        Organization(id=NW, name="Northwind Memorial", slug="northwind", platform_id="plt"),
        Organization(id=OT, name="Other Tenant", slug="other-tenant", platform_id="plt"),
    ])
    db.flush()

    def mk(uid, email, name, role, org):
        db.add(User(id=uid, organization_id=org, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role, platform_id="plt",
                    must_change_password=False, is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(days=1)))

    mk("u-ada", "ada@northwind.test", "Ada Advisor", "advisor", NW)
    mk("u-ben", "ben@northwind.test", "Ben Advisor", "advisor", NW)
    mk("u-cara", "cara@northwind.test", "Cara Admin", "org_admin", NW)
    mk("u-otto", "otto@other.test", "Otto Outsider", "advisor", OT)
    db.flush()

    old = datetime.utcnow() - timedelta(days=400)

    def lead(lid, **kw):
        base = dict(id=lid, organization_id=NW, assigned_to_id="u-ada",
                    first_name="Fam", last_name=lid.replace("ld-", "").upper(),
                    email="%s@example.test" % lid, phone="+12145550100",
                    status="new", tier="pre_need", contact_channel="email_only",
                    zip_code="75001", relationship_type="cold_lead")
        base.update(kw)
        db.add(Lead(**base))

    lead("ld-clean")
    lead("ld-warm", relationship_type="existing_customer", last_messaged_at=old)
    lead("ld-dnc", status="dnc")
    lead("ld-removeall", manual_flag="remove_all", manual_flag_reason="asked us to stop")
    lead("ld-bademail", manual_flag="bad_email", manual_flag_reason="hard bounce")
    lead("ld-noemail", email=None)
    lead("ld-badaddr", email="not-an-address")
    lead("ld-placeholder", email="unknown@unknown")
    lead("ld-test", is_test=True, test_note="QA fixture")
    lead("ld-dupe", is_duplicate=True, duplicate_reason="registry_exact")
    lead("ld-roleaddr", email="info@example.test")
    lead("ld-untiered", status="needs_tier_review")

    # Ben's book. Same organization, different advisor - the P0 boundary that
    # qualification must not become a way around.
    for n in range(3):
        db.add(Lead(id="ld-ben%d" % n, organization_id=NW, assigned_to_id="u-ben",
                    first_name="Ben", last_name="LEAD%d" % n,
                    email="ben%d@example.test" % n, phone="+12145550%03d" % n,
                    status="new", contact_channel="email_only"))

    # Another tenant entirely.
    for n in range(2):
        db.add(Lead(id="ld-otto%d" % n, organization_id=OT, assigned_to_id="u-otto",
                    first_name="Otto", last_name="LEAD%d" % n,
                    email="otto%d@example.test" % n, phone="+13125550%03d" % n,
                    status="new", contact_channel="email_only"))

    db.flush()
    # A prior reply for the warm lead, so "has replied to us before" is a real
    # signal read from the table rather than a flag somebody set by hand.
    db.add(Reply(id="rp-1", lead_id="ld-warm", body="yes please call me",
                 received_at=datetime.utcnow() - timedelta(days=300)))
    # A suppression entry, so the SMS channel has something real to refuse.
    db.add(SuppressionEntry(id="sup-1", organization_id=NW, phone="+12145550100",
                            reason="replied STOP"))
    db.commit()
    db.close()


# ── proven by reverts ───────────────────────────────────────────────────────
#
# A GATE THAT PASSES AGAINST THE DEFECT IT WAS WRITTEN FOR IS DECORATION. Each
# entry below is a different way of putting the failure back into the real
# source; the gate is re-run against each one in a child process and must FAIL.
# The source is restored in a finally block whatever happens.
ENGINE = os.path.join(REPO, "app", "services", "qualification.py")
ROUTER = os.path.join(REPO, "app", "routers", "qualification_router.py")

REVERTS = [
    (ENGINE, "REVIEW_REQUIRED is allowed into the send queue",
     "        (ready if d[\"bucket\"] == READY else blocked).append(d)",
     "        (ready if d[\"bucket\"] in (READY, REVIEW) else blocked).append(d)"),

    (ENGINE, "a batch with an unauthorized id is narrowed instead of refused",
     "        assert_leads_in_scope(db, current_user, list(lead_ids), request=request)\n",
     ""),

    (ENGINE, "the population comes from the whole table, not the authorized query",
     "    query = authorized_lead_query(db, current_user, request=request)",
     "    query = db.query(Lead)"),

    (ENGINE, "the DNC check is dropped from the decision",
     '    if (getattr(lead, "status", None) or "").lower() == "dnc":\n'
     '        return _decision(lead, EXCLUDED, [reason("dnc")], channel)',
     '    if False:\n'
     '        return _decision(lead, EXCLUDED, [reason("dnc")], channel)'),

    (ENGINE, "an unusable email address is allowed through",
     '        if validity == "invalid":\n'
     '            return _decision(lead, EXCLUDED,\n'
     '                             [reason("invalid_email", (lead.email or "").strip())], channel)',
     '        if False:\n'
     '            return _decision(lead, EXCLUDED,\n'
     '                             [reason("invalid_email", (lead.email or "").strip())], channel)'),

    (ROUTER, "a rule may be pointed at any field it likes",
     "    if not qualification.rule_field_is_allowed(payload.field):",
     "    if False:"),
]


def prove_by_revert():
    import subprocess
    section("PROVEN BY REVERTS - each defect put back, each one caught")
    env = dict(os.environ, QUAL_GATE_CHILD="1", PYTHONIOENCODING="utf-8")
    for path, label, old, new in REVERTS:
        original = open(path, encoding="utf-8").read()
        if old not in original:
            allowed("REVERT anchor still matches: %s" % label, False,
                    "the gate's own patch no longer matches the source")
            continue
        try:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(original.replace(old, new, 1))
            p = subprocess.run([sys.executable, os.path.abspath(__file__)],
                               cwd=REPO, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=600)
            allowed("REVERT caught: %s" % label, p.returncode != 0,
                    "the gate PASSED against the reintroduced defect"
                    if p.returncode == 0 else "child exited %d" % p.returncode)
        finally:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(original)

    p = subprocess.run([sys.executable, os.path.abspath(__file__)], cwd=REPO,
                       env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=600)
    allowed("the source was restored and the gate is green again",
            p.returncode == 0, p.stdout.decode("utf-8", "replace")[-400:])


def main():
    build()
    c = TestClient(app)
    ada = token(c, "ada@northwind.test")
    ben = token(c, "ben@northwind.test")
    cara = token(c, "cara@northwind.test")
    otto = token(c, "otto@other.test")

    print("=" * 78)
    print("GATE 35 - QUALIFICATION NARROWS AUTHORIZATION, NEVER WIDENS IT")
    print("=" * 78)

    # ─────────────────────────────────────────────────────────────────────────
    section("the buckets are what the conditions say they are")
    r = c.post("/qualification/preview",
               json={"channel": "email", "include_leads": True}, headers=ada)
    allowed("an advisor can qualify their own book", r.status_code == 200, r.text[:160])
    body = r.json()
    by_id = {d["lead_id"]: d for d in body.get("leads", [])}

    for lid, expected, why in LEADS:
        got = by_id.get(lid, {}).get("bucket")
        want = {"READY": qualification.READY, "REVIEW": qualification.REVIEW,
                "EXCLUDED": qualification.EXCLUDED}[expected]
        allowed("%-16s -> %-16s (%s)" % (lid, expected, why), got == want,
                "got %s" % got)

    allowed("every decision carries at least one reason or factor",
            all((d["reasons"] or d["factors"]) for d in by_id.values()))
    allowed("an excluded lead is given no priority band",
            all(d["priority"] is None for d in by_id.values()
                if d["bucket"] == qualification.EXCLUDED))

    section("the counts add up and the reasons are named")
    allowed("total selected is Ada's book and only Ada's book",
            body["total_selected"] == len(LEADS),
            "%s selected, expected %s" % (body["total_selected"], len(LEADS)))
    allowed("ready + review + excluded == total selected",
            body["ready"] + body["review"] + body["excluded"] == body["total_selected"])
    codes = {r["code"] for r in body["exclusion_reasons"]}
    for expect in ("dnc", "missing_email", "invalid_email", "duplicate",
                   "internal_record", "flagged_bad_email", "flagged_remove_all"):
        allowed("exclusion reason reported: %s" % expect, expect in codes,
                sorted(codes))
    allowed("review reasons are named too",
            {"role_address", "needs_classification"} <= {r["code"] for r in body["review_reasons"]},
            [r["code"] for r in body["review_reasons"]])
    allowed("priority factors are explainable, not a bare score",
            any(f["code"] == "has_valid_email" for f in body["priority_factors"]))

    section("priority is earned and can be checked by adding it up")
    warm = by_id["ld-warm"]
    allowed("the existing customer who replied is HIGH", warm["priority"] == "HIGH",
            "%s score=%s" % (warm["priority"], warm["score"]))
    allowed("...and its score is exactly the sum of its named factors",
            warm["score"] == sum(f["points"] for f in warm["factors"]),
            "%s vs %s" % (warm["score"], sum(f["points"] for f in warm["factors"])))
    allowed("...and the reasons include the relationship and the prior reply",
            {"existing_relationship", "prior_response"} <=
            {f["code"] for f in warm["factors"]},
            [f["code"] for f in warm["factors"]])
    clean = by_id["ld-clean"]
    allowed("a cold lead with nothing behind it is not HIGH",
            clean["priority"] in ("MEDIUM", "LOW"), clean["priority"])

    # ─────────────────────────────────────────────────────────────────────────
    section("QUALIFICATION CANNOT WIDEN AUTHORIZATION")
    r = c.post("/qualification/preview",
               json={"channel": "email", "lead_ids": ["ld-ben0"]}, headers=ada)
    refused("naming a COLLEAGUE'S lead by id is refused, not qualified",
            r.status_code in (403, 404), "%s %s" % (r.status_code, r.text[:120]))

    r = c.post("/qualification/preview",
               json={"channel": "email", "lead_ids": ["ld-otto0"]}, headers=ada)
    refused("naming ANOTHER TENANT'S lead by id is refused",
            r.status_code in (403, 404), "%s %s" % (r.status_code, r.text[:120]))

    r = c.post("/qualification/preview",
               json={"channel": "email", "lead_ids": ["ld-clean", "ld-ben0"]}, headers=ada)
    refused("a batch mixing one of mine with one of Ben's refuses THE WHOLE BATCH",
            r.status_code in (403, 404), "%s %s" % (r.status_code, r.text[:120]))

    r = c.get("/qualification/lead/ld-ben0", headers=ada)
    refused("asking why a colleague's lead is excluded is a 404",
            r.status_code == 404, r.status_code)
    r = c.get("/qualification/lead/ld-otto0", headers=ada)
    refused("...and another tenant's is a 404 too", r.status_code == 404, r.status_code)

    r = c.post("/qualification/preview",
               json={"channel": "email", "filters": {"advisor_id": "u-ben"}}, headers=ada)
    refused("filtering BY ANOTHER ADVISOR narrows to nothing, it does not widen",
            r.status_code == 200 and r.json()["total_selected"] == 0,
            r.json().get("total_selected") if r.status_code == 200 else r.text[:120])

    r = c.post("/qualification/preview", json={"channel": "email"}, headers=otto)
    allowed("the other tenant qualifies their own book normally",
            r.status_code == 200 and r.json()["total_selected"] == 2,
            r.json().get("total_selected") if r.status_code == 200 else r.text[:120])

    r = c.post("/qualification/preview", json={"channel": "email"}, headers=cara)
    allowed("an org_admin sees the whole workspace, so scope still decides scope",
            r.status_code == 200 and r.json()["total_selected"] == len(LEADS) + 3,
            r.json().get("total_selected") if r.status_code == 200 else r.text[:120])

    allowed("the advisor's own qualification is unchanged by the admin's",
            c.post("/qualification/preview", json={"channel": "email"},
                   headers=ada).json()["total_selected"] == len(LEADS))


    # ─────────────────────────────────────────────────────────────────────────
    section("NOTHING BUT READY_TO_SEND ENTERS THE EMAIL QUEUE")

    def send(ids, headers=ada):
        return c.post("/email/send-batch", json={"lead_ids": ids}, headers=headers)

    for lid, expected, why in LEADS:
        if expected == "READY":
            continue
        r = send([lid])
        refused("%-16s cannot enter the email queue (%s)" % (lid, expected),
                r.status_code == 400, "%s %s" % (r.status_code, r.text[:110]))

    r = send(["ld-dnc"])
    allowed("...and the refusal NAMES the reason rather than shrugging",
            r.status_code == 400 and
            any(x["code"] == "dnc" for x in r.json()["detail"]["reasons"]),
            r.text[:160])
    allowed("...and says plainly that nothing was sent",
            "Nothing was sent" in r.json()["detail"]["message"], r.text[:160])

    section("bulk and select-all cannot bypass it")
    everything = [lid for lid, _, _ in LEADS]
    r = send(everything)
    refused("SELECT ALL with one bad lead refuses the WHOLE batch",
            r.status_code == 400, "%s %s" % (r.status_code, r.text[:110]))
    allowed("...and reports how many were blocked",
            r.status_code == 400 and r.json()["detail"]["blocked_count"] == 10,
            r.json()["detail"].get("blocked_count") if r.status_code == 400 else "")

    r = send(["ld-clean", "ld-warm", "ld-roleaddr"])
    refused("a REVIEW lead does not become sendable by being in a bigger batch",
            r.status_code == 400, "%s %s" % (r.status_code, r.text[:110]))

    r = send(["ld-clean", "ld-ben0"])
    refused("bulk cannot reach a colleague's lead either",
            r.status_code in (403, 404), "%s %s" % (r.status_code, r.text[:110]))

    section("...and a qualified send still works")
    r = send(["ld-clean", "ld-warm"])
    allowed("the two READY leads are accepted", r.status_code == 200, r.text[:200])
    allowed("...and the response says how many were qualified",
            r.status_code == 200 and r.json().get("qualified") == 2,
            r.json() if r.status_code == 200 else "")

    section("the single-send path refuses EXCLUDED and admits REVIEW")
    r = c.post("/email/send/ld-dnc", json={"body": "hello"}, headers=ada)
    refused("a human cannot single-send to a DNC lead", r.status_code == 400,
            "%s %s" % (r.status_code, r.text[:110]))
    r = c.post("/email/send/ld-placeholder", json={"body": "hello"}, headers=ada)
    refused("...nor to an address that cannot receive mail", r.status_code == 400,
            "%s %s" % (r.status_code, r.text[:110]))
    r = c.post("/email/send/ld-ben0", json={"body": "hello"}, headers=ada)
    refused("...nor to a colleague's lead", r.status_code == 404, r.status_code)

    # ─────────────────────────────────────────────────────────────────────────
    section("CHANNEL IS PART OF THE QUESTION")
    r = c.get("/qualification/lead/ld-noemail?channel=sms", headers=ada)
    allowed("a lead with no email is NOT excluded from SMS for that reason",
            r.status_code == 200 and
            not any(x["code"] == "missing_email" for x in r.json()["reasons"]),
            r.text[:160])
    r = c.get("/qualification/lead/ld-clean?channel=sms", headers=ada)
    allowed("a suppressed phone IS excluded from SMS",
            r.status_code == 200 and r.json()["bucket"] == qualification.EXCLUDED,
            r.text[:160])
    allowed("...for the suppression reason, or for missing consent",
            r.status_code == 200 and
            {x["code"] for x in r.json()["reasons"]} & {"suppressed", "no_sms_consent"},
            r.text[:160])
    r = c.get("/qualification/lead/ld-clean?channel=email", headers=ada)
    allowed("...while the same lead is READY for email",
            r.status_code == 200 and r.json()["bucket"] == qualification.READY,
            r.text[:160])

    section("the engine reports which channel it is authoritative for")
    r = c.post("/qualification/preview", json={"channel": "email"}, headers=ada)
    allowed("email is authoritative", r.json()["authoritative"] is True)
    r = c.post("/qualification/preview", json={"channel": "sms"}, headers=ada)
    allowed("sms is NOT yet authoritative, and says so",
            r.json()["authoritative"] is False)
    r = c.post("/qualification/preview", json={"channel": "carrier-pigeon"}, headers=ada)
    refused("an unknown channel is refused rather than defaulted",
            r.status_code == 400, r.status_code)


    # ─────────────────────────────────────────────────────────────────────────
    section("ORGANIZATION RULES - and no industry written into the platform")
    # THE ASSERTION IS ABOUT CODE, NOT PROSE. An earlier gate in this repo
    # failed because a file's own comment contained the word it was asserting
    # was absent, and a checker that cries wolf on correct code gets ignored
    # along with the nine real failures after it. Both these files DO name
    # Restland - in a docstring, explaining why no customer may be named in
    # the logic. That is the rule being documented, not broken.
    src = _code_only(os.path.join(REPO, "app", "services", "qualification.py"))
    for word in ("restland", "jason", "greenland", "nsmg", "evosys", "bookaboost"):
        allowed("no '%s' in the engine's CODE" % word, word not in src)
    model_src = _code_only(os.path.join(REPO, "app", "models",
                                        "qualification_models.py"))
    for word in ("restland", "jason", "nsmg"):
        allowed("no '%s' in the rule model's CODE" % word, word not in model_src)
    allowed("no industry classification is hardcoded in the engine",
            not any(w in src for w in ("pre_need", "storm", "seller lead",
                                       "memorial", "roofing")),
            [w for w in ("pre_need", "storm", "seller lead", "memorial", "roofing")
             if w in src])
    # And the prose is still held to something: a customer may be named as an
    # example in a comment, but the engine must not import their config.
    allowed("the engine imports no customer-specific module",
            "restland" not in src and "import app.services.restland" not in src)

    r = c.post("/qualification/rules", headers=ada, json={
        "name": "x", "effect": "exclude", "field": "tier", "operator": "equals",
        "value": "pre_need", "reason_label": "nope"})
    refused("a plain advisor cannot author organization rules",
            r.status_code == 403, r.status_code)

    r = c.post("/qualification/rules", headers=cara, json={
        "name": "No property owners with a memorial", "effect": "exclude",
        "field": "custom_fields.has_memorial", "operator": "is_true",
        "reason_label": "Already has a memorial on file"})
    allowed("an org admin can author a rule over a column THEY imported",
            r.status_code == 200, r.text[:200])
    rule_id = r.json()["id"] if r.status_code == 200 else None

    r = c.post("/qualification/rules", headers=cara, json={
        "name": "peek", "effect": "exclude", "field": "password_hash",
        "operator": "is_not_empty", "reason_label": "nope"})
    refused("a rule cannot be pointed at a field it may not read",
            r.status_code == 400, "%s %s" % (r.status_code, r.text[:120]))

    r = c.post("/qualification/rules", headers=cara, json={
        "name": "nameless", "effect": "exclude", "field": "tier",
        "operator": "equals", "value": "pre_need", "reason_label": "  "})
    refused("a rule with no human reason is refused - explainable is the point",
            r.status_code == 400, r.status_code)

    r = c.post("/qualification/rules", headers=cara, json={
        "name": "admit everybody", "effect": "include", "field": "tier",
        "operator": "equals", "value": "pre_need", "reason_label": "let them in"})
    refused("there is NO rule effect that admits a lead",
            r.status_code == 400, "%s %s" % (r.status_code, r.text[:120]))

    # The rule is authored by Northwind. It must not touch the other tenant.
    db = SessionLocal()
    lead = db.query(Lead).filter(Lead.id == "ld-clean").first()
    lead.custom_fields = '{"has_memorial": true}'
    other = db.query(Lead).filter(Lead.id == "ld-otto0").first()
    other.custom_fields = '{"has_memorial": true}'
    db.commit()
    db.close()

    r = c.get("/qualification/lead/ld-clean", headers=ada)
    allowed("the organization's own rule excludes its own lead",
            r.status_code == 200 and r.json()["bucket"] == qualification.EXCLUDED,
            r.text[:200])
    allowed("...and the reason quotes the sentence the organization wrote",
            "Already has a memorial on file" in r.text, r.text[:200])
    r = c.get("/qualification/lead/ld-otto0", headers=otto)
    allowed("...and the OTHER TENANT is untouched by it",
            r.status_code == 200 and r.json()["bucket"] == qualification.READY,
            r.text[:200])

    r = c.post("/qualification/rules", headers=cara, json={
        "name": "Old pre-need is worth calling", "effect": "boost", "points": 25,
        "field": "source_year", "operator": "less_than", "value": "2015",
        "reason_label": "Pre-need lead older than 2015"})
    allowed("an organization can boost priority with its own reason",
            r.status_code == 200, r.text[:160])

    r = c.get("/qualification/rules", headers=ada)
    allowed("an advisor can READ the rules that judged their leads",
            r.status_code == 200 and len(r.json()) >= 2, r.status_code)

    if rule_id:
        r = c.delete("/qualification/rules/%s" % rule_id, headers=cara)
        allowed("deactivating a rule works", r.status_code == 200, r.text[:120])
        r = c.get("/qualification/lead/ld-clean", headers=ada)
        allowed("...and the lead qualifies again with no other change",
                r.status_code == 200 and r.json()["bucket"] != qualification.EXCLUDED,
                r.text[:160])
        r = c.delete("/qualification/rules/%s" % rule_id, headers=otto)
        refused("another tenant cannot touch this organization's rule",
                r.status_code in (403, 404), r.status_code)

    section("the vocabulary is served by the server, not kept by the client")
    r = c.get("/qualification/vocabulary")
    allowed("the reason codes and rule primitives are published",
            r.status_code == 200 and
            {"buckets", "priorities", "reasons", "rule_fields"} <= set(r.json()),
            r.status_code)
    allowed("...and password_hash is not among the fields a rule may read",
            "password_hash" not in r.json()["rule_fields"])


    # ─────────────────────────────────────────────────────────────────────────
    section("the campaign preview shows the qualified population")
    r = c.post("/campaigns/preview", headers=cara,
               json={"filter_criteria": {"channel": "email"}})
    allowed("the preview answers with a qualification block",
            r.status_code == 200 and "qualification" in r.json(), r.text[:200])
    if r.status_code == 200:
        q = r.json()["qualification"]
        allowed("...with READY, REVIEW and EXCLUDED counts",
                {"ready", "review", "excluded"} <= set(q))
        allowed("...with the HIGH / MEDIUM / LOW split",
                set(q["priority"]) == {"HIGH", "MEDIUM", "LOW"})
        allowed("...and the exclusion reasons, so the number is actionable",
                len(q["exclusion_reasons"]) > 0)
        allowed("...and it did not leak beyond the caller's workspace",
                q["total_selected"] <= len(LEADS) + 3, q["total_selected"])

    section("qualification is READ-ONLY - it decides nothing by writing")
    db = SessionLocal()
    before = {(l.id, l.status, l.manual_flag, l.assigned_to_id, l.organization_id)
              for l in db.query(Lead).all()}
    db.close()
    c.post("/qualification/preview", json={"channel": "email", "include_leads": True},
           headers=ada)
    c.post("/qualification/preview", json={"channel": "sms"}, headers=cara)
    c.get("/qualification/lead/ld-clean", headers=ada)
    db = SessionLocal()
    after = {(l.id, l.status, l.manual_flag, l.assigned_to_id, l.organization_id)
             for l in db.query(Lead).all()}
    db.close()
    allowed("no lead changed status, flag, owner or tenant during qualification",
            before == after)

    section("a lead that is fixed qualifies again")
    db = SessionLocal()
    fixed = db.query(Lead).filter(Lead.id == "ld-badaddr").first()
    fixed.email = "real.address@example.test"
    db.commit()
    db.close()
    r = c.get("/qualification/lead/ld-badaddr", headers=ada)
    allowed("correcting the address moves it out of EXCLUDED",
            r.status_code == 200 and r.json()["bucket"] == qualification.READY,
            r.text[:200])
    r = send(["ld-badaddr"])
    allowed("...and it can now be sent", r.status_code == 200, r.text[:160])

    db = SessionLocal()
    dupe = db.query(Lead).filter(Lead.id == "ld-dupe").first()
    dupe.duplicate_resolved_at = datetime.utcnow()
    dupe.duplicate_resolved_by = "u-cara"
    db.commit()
    db.close()
    r = c.get("/qualification/lead/ld-dupe", headers=ada)
    allowed("a human resolving a duplicate un-excludes it",
            r.status_code == 200 and r.json()["bucket"] != qualification.EXCLUDED,
            r.text[:200])

    # ─────────────────────────────────────────────────────────────────────────
    if not os.environ.get("QUAL_GATE_CHILD"):
        prove_by_revert()

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAILED:
        print("\nAUTHORIZATION OR COMPLIANCE OPEN (%d):" % len(FAILED))
        for f in FAILED:
            print("  - %s" % f)
    if BROKEN:
        print("\nLEGITIMATE BEHAVIOUR BROKEN (%d):" % len(BROKEN))
        for f in BROKEN:
            print("  - %s" % f)
    if not FAILED and not BROKEN:
        print("\nQUALIFICATION NARROWS AUTHORIZATION AND NEVER WIDENS IT -")
        print("and every legitimate send still works.")
    print("=" * 78)
    return 1 if (FAILED or BROKEN) else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
