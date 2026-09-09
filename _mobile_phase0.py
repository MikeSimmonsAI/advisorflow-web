# -*- coding: utf-8 -*-
"""Mobile Phase 0 inspection. READ-ONLY. Writes one report file and nothing else.

Answers only the questions a NATIVE client raises that a browser client does not:
  1. Notification infrastructure - is there any push, or only in-app rows?
  2. File/photo upload - where do the bytes actually go?
  3. PWA / manifest / service-worker artifacts already in the repo
  4. Anything that assumes a browser (cookies, Origin, redirect-based OAuth)
  5. Pagination - can a phone ask for a page, or does every list return everything?
"""
import ast
import io
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
FE = os.path.join(ROOT, "frontend")
OUT = []


def read(p):
    return io.open(p, "r", encoding="utf-8", errors="replace").read()


def walk(base, exts):
    out = []
    for dp, dn, fn in os.walk(base):
        dn[:] = [d for d in dn if d not in ("node_modules", "dist", "__pycache__", ".git")]
        for f in fn:
            if f.endswith(exts):
                out.append(os.path.join(dp, f))
    return out


PY = [(p, read(p)) for p in walk(APP, (".py",))]


def section(title):
    OUT.append("")
    OUT.append("=" * 76)
    OUT.append(title)
    OUT.append("=" * 76)


def grep(pattern, files=None, label=None, limit=40):
    pat = re.compile(pattern, re.I)
    n = 0
    for p, t in (files or PY):
        rel = os.path.relpath(p, ROOT)
        for i, line in enumerate(t.split("\n"), 1):
            if pat.search(line):
                OUT.append("   %s:%d  %s" % (rel, i, line.strip()[:120]))
                n += 1
                if n >= limit:
                    OUT.append("   ... (truncated)")
                    return n
    if n == 0:
        OUT.append("   NONE FOUND")
    return n


section("1. PUSH NOTIFICATION INFRASTRUCTURE")
OUT.append("-- any push provider, device token or APNs/FCM reference?")
grep(r"\b(fcm|apns|firebase|expo[- ]?push|onesignal|push_token|device_token|"
     r"web ?push|vapid|pushsubscription)\b")
OUT.append("")
OUT.append("-- the Notification model as it exists")
nm = os.path.join(APP, "models", "models.py")
src = read(nm)
m = re.search(r"class Notification\(Base\):.*?(?=\nclass |\Z)", src, re.S)
OUT.append(m.group(0)[:1800] if m else "   class Notification not found in models.py")

section("2. FILE / PHOTO UPLOAD — WHERE DO THE BYTES GO?")
OUT.append("-- UploadFile endpoints")
grep(r"UploadFile")
OUT.append("")
OUT.append("-- storage destination: s3 / blob / local disk / base64 in a column")
grep(r"\b(boto3|s3_client|put_object|cloudinary|azure\.storage|gcs|"
     r"shutil\.copyfileobj|tempfile\.|\.write\(await |static/uploads|UPLOAD_DIR)\b")

section("3. EXISTING PWA / MOBILE ARTIFACTS IN THE FRONTEND")
for name in ("manifest.json", "manifest.webmanifest", "sw.js", "service-worker.js",
             "workbox-config.js", "capacitor.config.ts", "capacitor.config.json"):
    hits = []
    for dp, dn, fn in os.walk(FE):
        dn[:] = [d for d in dn if d not in ("node_modules",)]
        if name in fn:
            hits.append(os.path.relpath(os.path.join(dp, name), ROOT))
    OUT.append("   %-28s %s" % (name, hits or "absent"))
idx = os.path.join(FE, "index.html")
if os.path.exists(idx):
    t = read(idx)
    OUT.append("")
    OUT.append("-- index.html mobile-relevant tags")
    for line in t.split("\n"):
        if re.search(r"viewport|apple-mobile|theme-color|manifest|serviceWorker", line, re.I):
            OUT.append("   %s" % line.strip()[:140])

section("4. BROWSER ASSUMPTIONS A NATIVE CLIENT WOULD BREAK")
OUT.append("-- cookie-based state (a native client has no cookie jar by default)")
grep(r"set_cookie|response\.cookies|SessionMiddleware")
OUT.append("")
OUT.append("-- OAuth redirect flows (need a native redirect / app-link strategy)")
grep(r"RedirectResponse\(.*oauth|redirect_uri|OAUTH_REDIRECT")
OUT.append("")
OUT.append("-- FRONTEND_URL / hardcoded web origins used to build links back")
grep(r"FRONTEND_URL|APP_BASE_URL|frontend_url")

section("5. PAGINATION — CAN A PHONE ASK FOR A PAGE?")
OUT.append("-- list endpoints exposing limit/offset/cursor/page")
grep(r"(limit:\s*int|offset:\s*int|page:\s*int|cursor)\s*=\s*Query", limit=40)

section("6. LEAD LIST SHAPE (the screen a phone opens most)")
lr = os.path.join(APP, "routers", "leads_router.py")
t = read(lr)
m = re.search(r"@router\.get\(\"/\"\).*?\ndef list_leads\((.*?)\):", t, re.S)
OUT.append(m.group(0)[:1500] if m else "   list_leads signature not matched")

with io.open(os.path.join(ROOT, "_mobile_phase0.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(OUT))
print("wrote _mobile_phase0.txt  lines=%d" % len(OUT))
