"""GATE 30 - PLATFORM / WORKSPACE ACCESS, CONTEXT SWITCHER, ONBOARDING ENTRY.

Membership answers CAN THIS PERSON ENTER THIS WORKSPACE.
lead_scope answers WHICH DATA THEY SEE ONCE INSIDE.
This gate proves both, and proves they stay separate.

THE FIXTURE IS MIKE'S TEST MATRIX, NAMED AS HE NAMED IT:

  A  Sal        BookaBoost salesperson, NO customer membership
  B  Wanda      We Epic Game only, NO platform membership
  C  D'Angelo   BookaBoost sales manager AND We Epic Game org_admin
  D  Dana       BookaBoost sales manager AND We Epic Game AND ABC Roofing
  E  Ex         a REVOKED We Epic Game membership
  -  Jason      We Epic Game advisor, 3 leads   } P0 must still hold
  -  Michael    We Epic Game advisor, 3 leads   } INSIDE one workspace
  -  Legacy     users.organization_id only, no membership row - the backfill

Every REFUSED check is paired with an ALLOWED one. A build that refuses
everybody passes an access probe perfectly and ships a product nobody can log
into, which is the exact failure being fixed here.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="wsctx_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                              # noqa: E402
from app.main import app                                              # noqa: E402
from app.deps import SessionLocal, engine                             # noqa: E402
from app.models.models import Base, Platform, Organization, User, Lead  # noqa: E402
from app.models.sales_models import (                                 # noqa: E402
    Membership, BrandSalesOrg, SCOPE_CUSTOMER_ORG, SCOPE_BRAND_SALES_ORG,
    ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.services.auth_service import hash_password                   # noqa: E402

PW = "ProbeTest!2026"
FAILED, BROKEN, PASSED = [], [], []

WEG = "org-weepic"        # We Epic Game
ABC = "org-abcroof"       # ABC Roofing
REST = "org-restland"     # Restland - nobody in this gate belongs to it


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


def _walk_numbers(obj):
    """Every integer anywhere in a response, so a count tile cannot hide.

    Booleans are excluded deliberately - in Python `True` IS an int, and a
    payload full of flags would otherwise read as a wall of 1s and 0s and make
    the assertion meaningless.
    """
    if isinstance(obj, bool):
        return
    if isinstance(obj, int):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            for n in _walk_numbers(v):
                yield n
    elif isinstance(obj, list):
        for v in obj:
            for n in _walk_numbers(v):
                yield n


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add(Platform(id="plt-bb", name="BookaBoost", slug="bookaboost"))
    db.flush()
    db.add(BrandSalesOrg(id="bso-bb", platform_id="plt-bb",
                         name="BookaBoost Sales", slug="bookaboost-sales"))
    db.add_all([
        Organization(id=WEG, name="We Epic Game", slug="we-epic-game",
                     platform_id="plt-bb"),
        Organization(id=ABC, name="ABC Roofing", slug="abc-roofing",
                     platform_id="plt-bb"),
        Organization(id=REST, name="Restland", slug="restland",
                     platform_id="plt-bb"),
    ])
    db.flush()

    def mk(uid, email, name, role, org=None):
        db.add(User(id=uid, organization_id=org, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role,
                    platform_id="plt-bb", must_change_password=False,
                    is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(days=1)))

    # organization_id is left NULL on everyone who gets a membership, so the
    # switcher is proved to read MEMBERSHIP and not the legacy column. Legacy is
    # the one exception and exists to prove the backfill.
    mk("u-sal", "sal@bookaboost.test", "Sal Salesperson", "advisor")
    mk("u-wanda", "wanda@weepic.test", "Wanda Workspace", "advisor")
    mk("u-dangelo", "dangelo@bookaboost.test", "DAngelo", "advisor")
    mk("u-dana", "dana@bookaboost.test", "Dana Multi", "advisor")
    mk("u-ex", "ex@weepic.test", "Ex Employee", "advisor")
    mk("u-jason", "jason@weepic.test", "Jason Advisor", "advisor")
    mk("u-michael", "michael@weepic.test", "Michael Advisor", "advisor")
    mk("u-legacy", "legacy@weepic.test", "Legacy User", "org_admin", WEG)
    mk("u-god", "god@probe.test", "Owner", "god_admin")

    # THE PRODUCTION-SHAPED ADVISOR.
    #
    # Every advisor in the field today looks like this and NOT like u-jason
    # above: `users.organization_id` is SET because that column was tenancy for
    # the product's whole life, the backfill gives them a membership, and their
    # browser sends NO X-Workspace-Id because they never touched a switcher.
    # u-jason deliberately has a NULL column to prove membership is the
    # authority - which is the right thing to prove and the WRONG shape to stop
    # at, because it is not the shape of anybody real. An advisor who cannot see
    # their own book is the failure that matters most, so the realistic shape
    # gets its own case.
    mk("u-field", "field@weepic.test", "Field Advisor", "advisor", WEG)
    db.flush()

    def plat(uid, role=ROLE_SALES_MANAGER):
        db.add(Membership(user_id=uid, scope_type=SCOPE_BRAND_SALES_ORG,
                          scope_id="bso-bb", role=role, is_active=True))

    def ws(uid, org, role="advisor", active=True):
        db.add(Membership(user_id=uid, scope_type=SCOPE_CUSTOMER_ORG,
                          scope_id=org, role=role, is_active=active))

    plat("u-sal", ROLE_SALES_REP)
    plat("u-dangelo", ROLE_SALES_MANAGER)
    plat("u-dana", ROLE_SALES_MANAGER)

    ws("u-wanda", WEG, "advisor")
    # D'Angelo is a PLATFORM sales_manager and a WORKSPACE org_admin. The two
    # roles are independent and this fixture is the proof.
    ws("u-dangelo", WEG, "org_admin")
    ws("u-dana", WEG, "advisor")
    ws("u-dana", ABC, "org_admin")
    ws("u-ex", WEG, "advisor", active=False)   # revoked
    ws("u-jason", WEG, "advisor")
    ws("u-michael", WEG, "advisor")
    db.flush()

    # P0 fixture INSIDE one workspace: two advisors, three leads each.
    for n in range(3):
        db.add(Lead(id="ld-j%d" % n, organization_id=WEG,
                    assigned_to_id="u-jason", first_name="Lead",
                    last_name="JASONOWNED%d" % n, phone="+1214555%04d" % n,
                    email="ld-j%d@example.test" % n, status="new",
                    tier="pre_need"))
        db.add(Lead(id="ld-m%d" % n, organization_id=WEG,
                    assigned_to_id="u-michael", first_name="Lead",
                    last_name="MICHAELONLY%d" % n, phone="+1214556%04d" % n,
                    email="ld-m%d@example.test" % n, status="new",
                    tier="pre_need"))
        db.add(Lead(id="ld-r%d" % n, organization_id=REST,
                    assigned_to_id=None, first_name="Lead",
                    last_name="RESTLANDONLY%d" % n, phone="+1214557%04d" % n,
                    email="ld-r%d@example.test" % n, status="new",
                    tier="pre_need"))
    # The field advisor's own book - four leads, distinctively named.
    for n in range(4):
        db.add(Lead(id="ld-f%d" % n, organization_id=WEG,
                    assigned_to_id="u-field", first_name="Lead",
                    last_name="FIELDOWNED%d" % n, phone="+1214559%04d" % n,
                    email="ld-f%d@example.test" % n, status="new",
                    tier="pre_need"))
    db.commit()
    db.close()


def token(c, email, password=None):
    r = c.post("/auth/login", data={"username": email,
                                    "password": password or PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s" % (email, r.text[:300]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def text(r):
    try:
        return r.text
    except Exception:
        return ""


def ctx(c, hdr):
    r = c.get("/auth/my-contexts", headers=hdr)
    return r, (r.json() if r.status_code == 200 else {})


def ws_names(j):
    return sorted(w["organization_name"] for w in j.get("workspace_contexts", []))


def main():
    print("=" * 78)
    print("GATE 30 - PLATFORM / WORKSPACE ACCESS + CONTEXT SWITCHER")
    print("=" * 78)
    build()

    with TestClient(app) as c:
        sal = token(c, "sal@bookaboost.test")
        wanda = token(c, "wanda@weepic.test")
        dangelo = token(c, "dangelo@bookaboost.test")
        dana = token(c, "dana@bookaboost.test")
        ex = token(c, "ex@weepic.test")
        jason = token(c, "jason@weepic.test")
        michael = token(c, "michael@weepic.test")
        legacy = token(c, "legacy@weepic.test")

        # ── USER A ──────────────────────────────────────────────────────────
        section("USER A - Sal, BookaBoost salesperson, no customer membership")
        r, j = ctx(c, sal)
        allowed("her context list loads", r.status_code == 200, r.status_code)
        allowed("   and she HAS back office access",
                j.get("has_back_office") is True, j.get("has_back_office"))
        refused("   with ZERO workspace contexts - so NO Workspace button",
                j.get("workspace_count") == 0, j.get("workspace_contexts"))
        allowed("   and login lands her in the back office",
                j.get("default_context", {}).get("path") == "/sales",
                j.get("default_context"))
        r = c.get("/auth/workspace/%s" % WEG, headers=sal)
        refused("   entering We Epic Game by direct URL is refused",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:120]))
        r = c.get("/auth/workspace/%s" % REST, headers=sal)
        refused("   and so is Restland", r.status_code == 403, "%s" % r.status_code)

        # ── USER B ──────────────────────────────────────────────────────────
        section("USER B - Wanda, We Epic Game only, no platform membership")
        r, j = ctx(c, wanda)
        allowed("her context list loads", r.status_code == 200, r.status_code)
        refused("   she has NO back office access - so NO Back Office button",
                j.get("has_back_office") is False, j.get("has_back_office"))
        allowed("   and exactly one workspace: We Epic Game",
                ws_names(j) == ["We Epic Game"], ws_names(j))
        allowed("   login routes her STRAIGHT INTO it, not to an empty page",
                j.get("default_context", {}).get("path") == "/workspace/" + WEG,
                j.get("default_context"))
        r = c.get("/auth/workspace/%s" % WEG, headers=wanda)
        allowed("   and she can enter it", r.status_code == 200,
                "%s %s" % (r.status_code, text(r)[:140]))
        allowed("   with the server naming her workspace role",
                r.json().get("workspace_role") == "advisor", text(r)[:140])
        refused("   and the server tells the UI she has no back office",
                r.json().get("has_back_office") is False, text(r)[:140])
        r = c.get("/auth/workspace/%s" % ABC, headers=wanda)
        refused("   ABC Roofing by direct URL is refused",
                r.status_code == 403, "%s" % r.status_code)
        r = c.get("/sales/me", headers=wanda)
        refused("   and the sales back office refuses her",
                r.status_code in (401, 403), "%s %s" % (r.status_code, text(r)[:120]))

        # ── USER C - D'ANGELO ───────────────────────────────────────────────
        section("USER C - D'Angelo, BookaBoost sales manager + We Epic Game admin")
        r, j = ctx(c, dangelo)
        allowed("his context list loads", r.status_code == 200, r.status_code)
        allowed("   he HAS back office access", j.get("has_back_office") is True, j)
        allowed("   and exactly one workspace: We Epic Game",
                ws_names(j) == ["We Epic Game"], ws_names(j))
        allowed("   login lands him in the BACK OFFICE by default",
                j.get("default_context", {}).get("path") == "/sales",
                j.get("default_context"))
        allowed("   so the header shows ONE [ WORKSPACE ] button",
                j.get("workspace_count") == 1, j.get("workspace_count"))
        r = c.get("/auth/workspace/%s" % WEG, headers=dangelo)
        allowed("BACK OFFICE -> WORKSPACE: he enters We Epic Game",
                r.status_code == 200, "%s %s" % (r.status_code, text(r)[:140]))
        allowed("WORKSPACE -> BACK OFFICE: the server says he may go back",
                r.json().get("has_back_office") is True, text(r)[:160])
        # THE ROLES ARE INDEPENDENT. This is the check that proves it.
        allowed("   his WORKSPACE role is org_admin",
                r.json().get("workspace_role") == "org_admin", text(r)[:160])
        plat_roles = [p.get("role") for p in j.get("platform_contexts", [])]
        allowed("   while his PLATFORM role is sales_manager",
                "sales_manager" in plat_roles, plat_roles)
        r = c.get("/sales/me", headers=dangelo)
        allowed("   and the back office still admits him",
                r.status_code == 200, "%s %s" % (r.status_code, text(r)[:120]))
        r = c.get("/auth/workspace/%s" % REST, headers=dangelo)
        refused("   Restland is REFUSED - he has no membership there",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:120]))
        refused("   and Restland never appears in his context list",
                "Restland" not in str(j), str(j)[:200])

        # ── USER D - MULTI WORKSPACE ────────────────────────────────────────
        section("USER D - Dana, back office + TWO workspaces")
        r, j = ctx(c, dana)
        allowed("her context list loads", r.status_code == 200, r.status_code)
        allowed("   two workspaces exactly, and the right two",
                ws_names(j) == ["ABC Roofing", "We Epic Game"], ws_names(j))
        allowed("   so the header shows [ WORKSPACES v ]",
                j.get("workspace_count") == 2, j.get("workspace_count"))
        refused("   the menu contains NOTHING else - no Restland",
                "Restland" not in str(j), str(j)[:200])
        allowed("   she still defaults to the back office",
                j.get("default_context", {}).get("path") == "/sales",
                j.get("default_context"))
        for org, name in ((WEG, "We Epic Game"), (ABC, "ABC Roofing")):
            r = c.get("/auth/workspace/%s" % org, headers=dana)
            allowed("   she can enter %s" % name, r.status_code == 200,
                    "%s %s" % (r.status_code, text(r)[:120]))
        # Her role differs between her two workspaces. Neither is her platform role.
        ra = c.get("/auth/workspace/%s" % ABC, headers=dana).json()
        rw = c.get("/auth/workspace/%s" % WEG, headers=dana).json()
        allowed("   org_admin in ABC Roofing, advisor in We Epic Game",
                ra.get("workspace_role") == "org_admin"
                and rw.get("workspace_role") == "advisor",
                "%s / %s" % (ra.get("workspace_role"), rw.get("workspace_role")))
        r = c.get("/auth/workspace/%s" % REST, headers=dana)
        refused("   Restland is still refused", r.status_code == 403, "%s" % r.status_code)

        # ── USER E - REVOKED ────────────────────────────────────────────────
        section("USER E - Ex, membership revoked")
        r, j = ctx(c, ex)
        refused("an INACTIVE membership produces NO context",
                j.get("workspace_count") == 0, j.get("workspace_contexts"))
        refused("   so no button is rendered for it",
                "We Epic Game" not in str(j), str(j)[:200])
        r = c.get("/auth/workspace/%s" % WEG, headers=ex)
        refused("   and the route is refused as well, not merely hidden",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:120]))

        section("revoking a LIVE membership takes effect on the next check")
        db = SessionLocal()
        from app.services import workspace_access
        workspace_access.revoke_workspace_membership(db, "u-wanda", WEG)
        db.close()
        r = c.get("/auth/workspace/%s" % WEG, headers=wanda)
        refused("Wanda's workspace is refused the moment it is revoked",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:120]))
        r, j = ctx(c, wanda)
        refused("   and it disappears from her contexts on re-fetch",
                j.get("workspace_count") == 0, j.get("workspace_contexts"))
        db = SessionLocal()
        workspace_access.grant_workspace_membership(db, "u-wanda", WEG, "advisor")
        db.close()
        r = c.get("/auth/workspace/%s" % WEG, headers=wanda)
        allowed("   and granting it back restores access",
                r.status_code == 200, "%s" % r.status_code)

        # ── THE LEGACY COLUMN ───────────────────────────────────────────────
        section("the backfill: a legacy organization_id user is not stranded")
        db = SessionLocal()
        rows = (db.query(Membership)
                .filter(Membership.user_id == "u-legacy",
                        Membership.scope_type == SCOPE_CUSTOMER_ORG).all())
        db.close()
        allowed("logging in materialised their column into a real membership",
                len(rows) == 1 and rows[0].scope_id == WEG,
                [(m.scope_id, m.role, m.is_active) for m in rows])
        allowed("   with the workspace role their user row implied",
                rows and rows[0].role == "org_admin",
                rows[0].role if rows else None)
        r, j = ctx(c, legacy)
        allowed("   so their workspace appears in the switcher",
                ws_names(j) == ["We Epic Game"], ws_names(j))
        # Idempotence: log in again, still exactly one row.
        token(c, "legacy@weepic.test")
        db = SessionLocal()
        again = (db.query(Membership)
                 .filter(Membership.user_id == "u-legacy",
                         Membership.scope_type == SCOPE_CUSTOMER_ORG).count())
        db.close()
        refused("   and a second login does NOT duplicate it",
                again == 1, again)

        # ── ATTACKS ─────────────────────────────────────────────────────────
        section("the browser cannot select a workspace by asserting one")
        # X-Workspace-Id is a REQUEST. Sal holds no membership anywhere, so
        # naming a workspace must not put her in one.
        r = c.get("/leads/", headers={**sal, "X-Workspace-Id": WEG})
        refused("Sal naming We Epic Game in a header reaches no leads",
                r.status_code in (401, 403), "%s %s" % (r.status_code, text(r)[:120]))
        refused("   and no lead surname reaches her",
                "JASONOWNED" not in text(r) and "MICHAELONLY" not in text(r),
                text(r)[:200])
        # D'Angelo holds We Epic Game but NOT Restland.
        r = c.get("/leads/", headers={**dangelo, "X-Workspace-Id": REST})
        refused("D'Angelo naming Restland does not put him in Restland",
                "RESTLANDONLY" not in text(r),
                "%s %s" % (r.status_code, text(r)[:160]))
        r = c.get("/leads/", headers={**dangelo, "X-Workspace-Id": WEG})
        allowed("   while naming HIS OWN workspace works",
                r.status_code == 200, "%s %s" % (r.status_code, text(r)[:120]))
        allowed("   and as its org_admin he sees the team's book",
                "JASONOWNED" in text(r) and "MICHAELONLY" in text(r), text(r)[:200])
        refused("   but never another tenant's",
                "RESTLANDONLY" not in text(r), text(r)[:200])

        # ── P0 STILL HOLDS INSIDE A WORKSPACE ───────────────────────────────
        section("membership lets you IN; P0 still decides what you see")
        r = c.get("/leads/", headers={**jason, "X-Workspace-Id": WEG})
        b = text(r)
        allowed("Jason enters We Epic Game and sees his own book",
                r.status_code == 200 and "JASONOWNED" in b,
                "%s %s" % (r.status_code, b[:160]))
        refused("   and NOT Michael's, though both hold the same membership",
                "MICHAELONLY" not in b, b[:200])
        allowed("   exactly his 3 leads", r.json().get("total") == 3,
                r.json().get("total"))
        r = c.get("/leads/ld-m0", headers={**michael, "X-Workspace-Id": WEG})
        allowed("Michael reaches his own lead by id", r.status_code == 200,
                "%s" % r.status_code)
        r = c.get("/leads/ld-m0", headers={**jason, "X-Workspace-Id": WEG})
        refused("   and Jason is still 404 on it", r.status_code == 404,
                "%s" % r.status_code)

        # ═══════════════════════════════════════════════════════════════════
        # THE PRODUCTION-SHAPED ADVISOR - AND THE FOUR COUNTS MUST AGREE
        #
        # A real advisor has users.organization_id SET, a backfilled membership,
        # and sends NO X-Workspace-Id because they have never touched a
        # switcher. If any of the membership work broke a working advisor, THIS
        # is where it shows, and it shows as an empty screen rather than an
        # error - which is why the four counts are compared rather than just
        # eyeballing the list:
        #
        #   A. raw assigned    - the database, no authorization at all
        #   B. lead_scope      - the canonical authorization answer
        #   C. /leads API      - what the endpoint returns
        #   D. dashboard tiles - what the advisor actually reads on screen
        #
        # A disagreement between any adjacent pair names the exact layer that
        # is wrong. All four equal means the advisor sees their book.
        # ═══════════════════════════════════════════════════════════════════
        section("production-shaped advisor: legacy column, no workspace header")
        field = token(c, "field@weepic.test")

        db = SessionLocal()
        field_row = db.query(User).filter(User.id == "u-field").first()
        # A. RAW - what the database holds, before any authorization runs.
        # Named raw_count, not raw: the activation section below binds `raw` to
        # a one-time token, and a count silently becoming a token string is the
        # kind of collision that makes a gate assert nothing while still passing.
        raw_count = db.query(Lead).filter(Lead.assigned_to_id == "u-field").count()
        allowed("A. raw assigned leads exist in the database",
                raw_count == 4, raw_count)
        allowed("   their legacy organization_id is still set",
                field_row.organization_id == WEG, field_row.organization_id)
        ms = [m for m in db.query(Membership).filter(
            Membership.user_id == "u-field",
            Membership.scope_type == SCOPE_CUSTOMER_ORG).all()]
        allowed("   the backfill gave them ONE active customer_org membership",
                [(m.scope_id, m.role, m.is_active) for m in ms]
                == [(WEG, "advisor", True)],
                [(m.scope_id, m.role, m.is_active) for m in ms])
        # B. CANONICAL - lead_scope's own answer, called directly with no
        # request, exactly as a background job would see it.
        from app.services import lead_scope as _ls
        scoped = _ls.authorized_lead_query(db, field_row).count()
        allowed("B. lead_scope agrees with the raw count", scoped == raw_count,
                "raw=%s scoped=%s" % (raw_count, scoped))
        allowed("   the resolved workspace is their organization",
                _ls.active_workspace_org_id(field_row) == WEG,
                _ls.active_workspace_org_id(field_row))
        allowed("   and the resolved role is advisor, not a manager",
                _ls.effective_role(field_row, db) == "advisor",
                _ls.effective_role(field_row, db))
        db.close()

        # C. THE API - no X-Workspace-Id header, exactly as a real browser that
        # has never used the switcher would call it.
        r = c.get("/leads/", headers=field)
        b = text(r)
        allowed("C. the /leads API returns 200", r.status_code == 200,
                "%s %s" % (r.status_code, b[:140]))
        api_total = r.json().get("total") if r.status_code == 200 else None
        allowed("   and its total equals the raw count", api_total == raw_count,
                "raw=%s api=%s" % (raw_count, api_total))
        allowed("   with their own leads actually in the body",
                "FIELDOWNED0" in b, b[:200])
        refused("   and NOT another advisor's",
                "JASONOWNED" not in b and "MICHAELONLY" not in b, b[:200])
        refused("   and NOT another organization's",
                "RESTLANDONLY" not in b, b[:200])

        # D. THE DASHBOARD - the numbers on screen. Every integer anywhere in
        # the payload is walked, because a tile computed from a wider query
        # than the list is the failure the P0 named and it hides in one field.
        r = c.get("/leads/status-funnel", headers=field)
        if r.status_code == 200:
            nums = [n for n in _walk_numbers(r.json())]
            refused("D. no dashboard tile exceeds their own book",
                    all(n <= raw_count for n in nums), (raw_count, sorted(set(nums))[-5:]))
        r = c.get("/leads/engagement-breakdown", headers=field)
        if r.status_code == 200:
            nums = [n for n in _walk_numbers(r.json())]
            refused("   nor does any engagement tile",
                    all(n <= raw_count for n in nums), (raw_count, sorted(set(nums))[-5:]))

        # The same advisor, by direct id, both directions.
        r = c.get("/leads/ld-f0", headers=field)
        allowed("they can open their own lead by id", r.status_code == 200,
                "%s" % r.status_code)
        r = c.get("/leads/ld-m0", headers=field)
        refused("   and are 404 on a colleague's", r.status_code == 404,
                "%s" % r.status_code)
        r = c.get("/leads/ld-r0", headers=field)
        refused("   and 404 on another organization's",
                r.status_code == 404, "%s" % r.status_code)

        # And the header changes nothing for them: naming their OWN workspace
        # must give the identical answer, and naming another must not move them.
        r = c.get("/leads/", headers={**field, "X-Workspace-Id": WEG})
        allowed("naming their own workspace gives the identical count",
                r.json().get("total") == raw_count, r.json().get("total"))
        r = c.get("/leads/", headers={**field, "X-Workspace-Id": REST})
        refused("naming another organization does NOT move them into it",
                "RESTLANDONLY" not in text(r), text(r)[:200])
        allowed("   and leaves their own book intact",
                r.json().get("total") == raw_count, r.json().get("total"))

        # ── ONBOARDING: THE TEST THAT PROVES A CUSTOMER CAN USE WHAT THEY BOUGHT
        section("new customer activation writes a real workspace membership")
        db = SessionLocal()
        from app.services import customer_activation
        god_row = db.query(User).filter(User.id == "u-god").first()
        new_org = Organization(id="org-fresh", name="Fresh Customer",
                               slug="fresh-customer", platform_id="plt-bb")
        db.add(new_org)
        db.commit()
        user, activation, raw = customer_activation.create_customer_admin(
            db, new_org, god_row, full_name="Fresh Admin",
            email="admin@fresh.test", role="org_admin")
        new_user_id = user.id
        made = (db.query(Membership)
                .filter(Membership.user_id == new_user_id,
                        Membership.scope_type == SCOPE_CUSTOMER_ORG).all())
        allowed("creating the workspace admin writes the membership immediately",
                len(made) == 1 and made[0].scope_id == "org-fresh",
                [(m.scope_id, m.role, m.is_active) for m in made])
        db.close()

        # THE MEMBERSHIP IS DELETED BEFORE ACCEPTANCE, DELIBERATELY.
        #
        # Creation writes it and acceptance writes it, which is correct - but it
        # made acceptance untestable: removing the write from `accept` changed
        # nothing the gate could see, because creation had already put the row
        # there. The revert proof caught that and this is the answer.
        #
        # Deleting it first also reproduces the REAL case: every customer admin
        # created before this deploy exists with a password and no membership.
        # Acceptance is the path that has to rescue them.
        db = SessionLocal()
        db.query(Membership).filter(
            Membership.user_id == new_user_id,
            Membership.scope_type == SCOPE_CUSTOMER_ORG).delete()
        db.commit()
        gone = (db.query(Membership)
                .filter(Membership.user_id == new_user_id,
                        Membership.scope_type == SCOPE_CUSTOMER_ORG).count())
        db.close()
        allowed("   (fixture: the membership is removed before acceptance)",
                gone == 0, gone)

        # Accept the invitation - the step that used to set a password and
        # nothing else.
        # Schema-valid payload: a 422 would mean the request never reached the
        # code under test, which proves nothing about activation.
        r = c.post("/auth/activation/accept",
                   json={"token": raw, "new_password": "FreshPassword!2026"})
        allowed("the activation link is accepted", r.status_code == 200,
                "%s %s" % (r.status_code, text(r)[:160]))
        db = SessionLocal()
        rescued = (db.query(Membership)
                   .filter(Membership.user_id == new_user_id,
                           Membership.scope_type == SCOPE_CUSTOMER_ORG).all())
        db.close()
        allowed("   and ACCEPTANCE ITSELF created the workspace membership",
                len(rescued) == 1 and rescued[0].scope_id == "org-fresh"
                and rescued[0].is_active is True,
                [(m.scope_id, m.role, m.is_active) for m in rescued])
        db = SessionLocal()
        after = (db.query(Membership)
                 .filter(Membership.user_id == new_user_id,
                         Membership.scope_type == SCOPE_CUSTOMER_ORG).all())
        db.close()
        allowed("   and the membership is active afterwards",
                len(after) == 1 and after[0].is_active is True,
                [(m.scope_id, m.role, m.is_active) for m in after])
        refused("   accepting did NOT create a duplicate membership",
                len(after) == 1, len(after))

        # Sign in as the brand-new customer, exactly as they would - with the
        # password THEY chose at activation, not a fixture one.
        fresh = token(c, "admin@fresh.test", "FreshPassword!2026")
        r, j = ctx(c, fresh)
        allowed("the new customer signs in and their workspace is discovered",
                ws_names(j) == ["Fresh Customer"], ws_names(j))
        allowed("   and login routes them straight into it",
                j.get("default_context", {}).get("path") == "/workspace/org-fresh",
                j.get("default_context"))
        refused("   they get NO back office", j.get("has_back_office") is False, j)
        r = c.get("/auth/workspace/org-fresh", headers=fresh)
        allowed("   the workspace is reachable", r.status_code == 200,
                "%s %s" % (r.status_code, text(r)[:120]))
        r = c.get("/auth/workspace/%s" % WEG, headers=fresh)
        refused("   and another customer's workspace is refused",
                r.status_code == 403, "%s" % r.status_code)
        r = c.get("/leads/", headers={**fresh, "X-Workspace-Id": WEG})
        refused("   including by header, with no lead reaching them",
                "JASONOWNED" not in text(r) and "MICHAELONLY" not in text(r),
                "%s %s" % (r.status_code, text(r)[:160]))

        # ── IDEMPOTENT INVITE ACCEPTANCE, THE SECOND OPENING ────────────────
        section("opening a used activation link again changes nothing")
        r = c.post("/auth/activation/accept",
                   json={"token": raw, "new_password": "AnotherPassword!2026"})
        refused("a spent activation token is refused",
                r.status_code in (400, 409), "%s %s" % (r.status_code, text(r)[:120]))
        db = SessionLocal()
        final = (db.query(Membership)
                 .filter(Membership.user_id == new_user_id,
                         Membership.scope_type == SCOPE_CUSTOMER_ORG).count())
        db.close()
        refused("   and the membership count is still exactly one",
                final == 1, final)

        # ── ADDITIVE MEMBERSHIP - THE D'ANGELO CASE, END TO END ─────────────
        section("a platform person can be ADDED to a workspace, not refused")
        db = SessionLocal()
        god_row = db.query(User).filter(User.id == "u-god").first()
        weg = db.query(Organization).filter(Organization.id == WEG).first()
        before_ids = set(workspace_access.workspace_org_ids(
            db.query(User).filter(User.id == "u-sal").first(), db))
        customer_activation.add_existing_user(
            db, weg, god_row, user_id="u-sal", role="advisor")
        sal_row = db.query(User).filter(User.id == "u-sal").first()
        workspace_access.invalidate_workspace_memberships(sal_row)
        after_ids = set(workspace_access.workspace_org_ids(sal_row, db))
        db.close()
        allowed("adding a brand salesperson to a workspace SUCCEEDS (was a 409)",
                after_ids == before_ids | {WEG}, (sorted(before_ids), sorted(after_ids)))
        sal = token(c, "sal@bookaboost.test")
        r, j = ctx(c, sal)
        allowed("   she now holds BOTH contexts",
                j.get("has_back_office") is True and j.get("workspace_count") == 1,
                (j.get("has_back_office"), j.get("workspace_count")))
        r = c.get("/auth/workspace/%s" % WEG, headers=sal)
        allowed("   and can enter the workspace she was added to",
                r.status_code == 200, "%s" % r.status_code)
        r = c.get("/auth/workspace/%s" % REST, headers=sal)
        refused("   Restland is still refused", r.status_code == 403, "%s" % r.status_code)

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAILED:
        print("\nACCESS FAILURES (%d):" % len(FAILED))
        for f in FAILED:
            print("  - %s" % f)
    if BROKEN:
        print("\nLEGITIMATE ACCESS BROKEN (%d):" % len(BROKEN))
        for f in BROKEN:
            print("  - %s" % f)
    if not FAILED and not BROKEN:
        print("\nMEMBERSHIP DECIDES WHO ENTERS - and P0 still decides what they see.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if (FAILED or BROKEN) else 0)


if __name__ == "__main__":
    main()
