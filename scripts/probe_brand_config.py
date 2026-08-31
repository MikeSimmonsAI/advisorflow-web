"""GATE 25 - the platform row is the single source of brand presentation.

Brand data used to live in four unsynchronised places, none of which read the
database: BRAND_IDENTITY (appointment_invites), _BRAND_MAP (branding_router),
BRAND_CONFIG (frontend/src/theme.js) and a pre-bundle IIFE in index.html. They
had already drifted - EvoSys Pro's accent was #087cff in two and #1d4ed8 in the
third; BookaBoost's was #c9973d in the frontend and #2fb6ff in the branding API.

This gate answers three questions:

  1. Does the DATABASE actually drive brand presentation now?
  2. Do EvoSys Pro and BookaBoost still render EXACTLY as before? A
     consolidation that changes either brand's appearance has failed, however
     tidy the result.
  3. Can a brand that exists ONLY as a database row - no code entry anywhere -
     get its full presentation? That is the whole point of the exercise.

Nothing here touches production. Every id below is invented.
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="brandcfg_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)
os.environ.pop("PLATFORM_SLUG", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                            # noqa: E402
from app.main import app                                            # noqa: E402
from app.deps import SessionLocal, engine                           # noqa: E402
from app.models.models import Base, Platform                        # noqa: E402
from app.services import brand_config as BC                         # noqa: E402

FAILS, PASSED = [], []


def check(label, ok, detail=""):
    print("  %s %s%s" % ("ok  " if ok else "FAIL", label,
                         ("\n         -> " + str(detail)[:200]) if detail else ""))
    (PASSED if ok else FAILS).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 62 - len(t)))


# The values that were live in the four registries BEFORE consolidation.
# If any of these change, a real brand's appearance changed.
BASELINE = {
    "evosyspro": {
        "display_name": "EvoSys Pro", "short_name": "E", "logo_initial": "E",
        "accent_color": "#087cff", "accent_color_2": "#22a3ff",
        "green_color": "#19d67c", "bg_color": "#040812",
        "invite_accent_color": "#1d4ed8",
        "support_email": "support@evosyspro.live",
        "support_phone": "469-553-7417",
        "website_url": "https://evosyspro.live",
        "app_base_url": "https://app.evosyspro.live",
        "theme_slug": "evosyspro",
    },
    "bookaboost": {
        "display_name": "BookaBoost", "short_name": "BB", "logo_initial": "BB",
        "accent_color": "#c9973d", "accent_color_2": "#1ef0a8",
        "green_color": "#1ef0a8", "bg_color": "#03060f",
        "support_email": "support@bookaboost.live",
        "website_url": "https://bookaboost.live",
        "app_base_url": "https://app.bookaboost.live",
        "theme_slug": "bookaboost",
    },
}


def main():
    print("=" * 78)
    print("GATE 25 - BRAND CONFIG SOURCE OF TRUTH")
    print("=" * 78)
    Base.metadata.create_all(bind=engine)

    # ── the schema actually carries brand presentation ──────────────────────
    section("schema")
    for col in ("short_name", "logo_initial", "logo_url", "favicon_url", "tagline",
                "theme_slug", "accent_color", "accent_color_2", "green_color",
                "bg_color", "invite_accent_color", "support_phone", "website_url",
                "app_base_url"):
        check("platforms.%s exists" % col, hasattr(Platform, col))

    # ── EvoSys and BookaBoost are unchanged, from the FROZEN path ───────────
    section("no visual regression - before any row exists")
    for slug, want in BASELINE.items():
        cfg = BC.config_for_slug(None, slug)
        for field, value in want.items():
            check("%s %s == %s" % (slug, field, value),
                  cfg.get(field) == value, cfg.get(field))

    # ── and from the DATABASE path, after the backfill ──────────────────────
    section("no visual regression - driven by the platform row")
    db = SessionLocal()
    for slug, want in BASELINE.items():
        row = Platform(id="plt-" + slug, name=want["display_name"], slug=slug,
                       domain="app.%s.live" % slug)
        for field, value in want.items():
            if field == "display_name":
                continue
            if hasattr(Platform, field):
                setattr(row, field, value)
        db.add(row)
    db.commit()

    for slug, want in BASELINE.items():
        cfg = BC.config_for_slug(db, slug)
        check("%s now resolves FROM THE DATABASE" % slug,
              cfg.get("source") == "database", cfg.get("source"))
        for field, value in want.items():
            check("%s %s still %s" % (slug, field, value),
                  cfg.get(field) == value, cfg.get(field))

    # ── a brand that exists ONLY as a row ───────────────────────────────────
    section("a NEW brand, database only - no code entry anywhere")
    check("the new slug is in NO code registry",
          "northstar" not in BC.FROZEN_BRAND_DEFAULTS)
    db.add(Platform(
        id="plt-northstar", name="NorthStar Care", slug="northstar",
        domain="app.northstar.example", support_email="support@northstar.example",
        short_name="NS", logo_initial="NS", tagline="Care, planned ahead.",
        theme_slug="evosyspro", accent_color="#7c3aed", accent_color_2="#a78bfa",
        green_color="#22c55e", bg_color="#0b0714", support_phone="555-0100",
        website_url="https://northstar.example",
        app_base_url="https://app.northstar.example"))
    db.commit()

    cfg = BC.config_for_slug(db, "northstar")
    check("resolves from the database", cfg.get("source") == "database", cfg.get("source"))
    for field, value in [("display_name", "NorthStar Care"), ("short_name", "NS"),
                         ("logo_initial", "NS"), ("tagline", "Care, planned ahead."),
                         ("accent_color", "#7c3aed"), ("bg_color", "#0b0714"),
                         ("support_phone", "555-0100"),
                         ("website_url", "https://northstar.example")]:
        check("new brand %s == %s" % (field, value), cfg.get(field) == value, cfg.get(field))
    check("it borrows an existing visual theme by slug",
          cfg.get("theme_slug") == "evosyspro", cfg.get("theme_slug"))
    fav = BC.favicon_data_uri(cfg)
    check("its favicon is generated from ITS OWN colour and mark",
          "%237c3aed" in fav and "NS" in fav, fav[:120])
    check("host lookup finds it by its own domain",
          BC.config_for_host(db, "app.northstar.example").get("slug") == "northstar")
    db.close()

    # ── the public endpoint serves it ───────────────────────────────────────
    section("GET /branding is database-driven")
    with TestClient(app) as c:
        r = c.get("/branding", headers={"host": "app.evosyspro.live"})
        b = r.json() if r.status_code == 200 else {}
        check("evosyspro over its host", b.get("brand") == "evosyspro",
              "%s %s" % (r.status_code, b))
        check("   accent unchanged", b.get("accentColor") == "#087cff", b.get("accentColor"))
        check("   title is the brand name", b.get("documentTitle") == "EvoSys Pro",
              b.get("documentTitle"))
        check("   carries a favicon", bool(b.get("faviconUrl")))

        r = c.get("/branding", headers={"host": "app.bookaboost.live"})
        b = r.json() if r.status_code == 200 else {}
        check("bookaboost over its host", b.get("brand") == "bookaboost", b)
        check("   accent is the frontend gold, not the dead API blue",
              b.get("accentColor") == "#c9973d", b.get("accentColor"))

        r = c.get("/branding", headers={"host": "app.northstar.example"})
        b = r.json() if r.status_code == 200 else {}
        check("the DB-only brand is served with no code entry",
              b.get("brand") == "northstar" and b.get("accentColor") == "#7c3aed", b)

    # ── the frontend no longer treats its table as the truth ────────────────
    section("frontend reads the row")
    tj = open(os.path.join(REPO, "frontend", "src", "theme.js"), encoding="utf-8").read()
    check("theme.js hydrates from /branding", "hydrateBrand" in tj)
    check("   caches the answer for a flash-free next paint", "af_platform_brand" in tj)
    check("   prefers the cached row over hostname sniffing",
          "cached && cached.theme" in tj)
    check("   and BRAND_CONFIG is labelled BOOTSTRAP ONLY", "BOOTSTRAP ONLY" in tj)
    mj = open(os.path.join(REPO, "frontend", "src", "main.jsx"), encoding="utf-8").read()
    check("main.jsx hydrates on boot", "hydrateBrand(" in mj)

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAILS:
        print("\nFAILURES (%d):" % len(FAILS))
        for f in FAILS:
            print("  - %s" % f)
    else:
        print("\nBRAND CONFIG SOURCE OF TRUTH HOLDS - both brands unchanged.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
