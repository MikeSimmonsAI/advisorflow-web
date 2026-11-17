"""Render Program Access V2 from the built app with test data and save screenshots.

Usage (from the repo root, after building the frontend with
VITE_API_BASE_URL=http://api.test and serving dist on :4173):

    python3 docs/redesign/program-access-v2/qa/shots.py OUT_DIR

Every API call goes to the fake host api.test and is answered from
fixtures.json, so no real account, token or customer data is involved.
"""
import base64
import json
import pathlib
import sys
import time

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
FIX = json.loads((HERE / "fixtures.json").read_text())
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/pa2-shots")
OUT.mkdir(parents=True, exist_ok=True)
APP = "http://127.0.0.1:4173"


def fake_jwt(user_id):
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return b64({"alg": "HS256", "typ": "JWT"}) + "." + b64({"sub": user_id, "exp": int(time.time()) + 86400}) + ".sig"


def run(page, who, state):
    f = FIX[who]
    pins = {"keys": list(state.get("pins", f.get("pins", [])))}
    log = []

    def handle(route):
        url = route.request.url
        path = url.split("api.test", 1)[1].split("?", 1)[0]
        log.append((route.request.method, path))
        if path == "/auth/my-contexts":
            body = f["contexts"]
        elif path == "/launcher/pins":
            if route.request.method == "PUT":
                pins["keys"] = json.loads(route.request.post_data or "{}").get("keys", [])
            body = pins
        elif path == "/launcher/attention":
            body = {"items": f.get("attention", [])}
        elif path == "/health/ready":
            body = state.get("health", {"status": "ok"})
            if body is None:
                return route.fulfill(status=503, body="{}", content_type="application/json")
        elif path == "/settings/profile":
            body = f["user"]
        else:
            body = {}
        route.fulfill(status=200, body=json.dumps(body), content_type="application/json",
                      headers={"access-control-allow-origin": "*", "access-control-allow-headers": "*",
                               "access-control-allow-methods": "*"})

    page.route("**://api.test/**", handle)
    # Nothing else leaves the machine (web fonts, analytics): local render only.
    page.route(lambda u: not (u.startswith(APP) or "api.test" in u), lambda r: r.abort())
    page.goto(APP + "/login")
    page.evaluate("""([t, u]) => { localStorage.clear(); localStorage.setItem('af_token', t);
                      localStorage.setItem('af_user', JSON.stringify(u)); }""",
                  [fake_jwt(f["user"]["id"]), f["user"]])
    page.goto(APP + "/workspaces")
    page.wait_for_selector(".pa2", timeout=15000)
    page.wait_for_timeout(800)
    # Load lazy photos: walk down the page, then wait (bounded) for every image.
    page.evaluate("""async () => { for (let y = 0; y < document.body.scrollHeight; y += 600) {
        window.scrollTo(0, y); await new Promise(r => setTimeout(r, 60)); } window.scrollTo(0, 0); }""")
    page.evaluate("""() => Promise.race([new Promise(r => setTimeout(r, 4000)),
        Promise.all([...document.images].map(i => i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r })))])""")
    return log


def main():
    ccb = {"pins": ["workspace:org-ccb:"]}
    shots = [
        # same size as the approved mockup (1672 x 941), Commercial Cleaning pinned as in the mockup
        ("owner", "mockup-size-1672x941", {"width": 1672, "height": 941}, dict(ccb, viewport_only=True)),
        ("owner", "desktop-1536-viewport", {"width": 1536, "height": 864}, dict(ccb, viewport_only=True)),
        ("owner", "desktop-1536", {"width": 1536, "height": 1100}, {}),
        ("owner", "desktop-1920", {"width": 1920, "height": 1200}, {}),
        ("owner", "laptop-1280", {"width": 1280, "height": 860}, {}),
        ("owner", "tablet-834", {"width": 834, "height": 1112}, {}),
        ("owner", "phone-390", {"width": 390, "height": 844}, {}),
        ("salesperson", "desktop-1536", {"width": 1536, "height": 1000}, {}),
        ("operator", "desktop-1536", {"width": 1536, "height": 1000}, {"health": None}),
    ]
    print("starting", flush=True)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox", "--disable-gpu", "--no-first-run", "--disable-sync", "--disable-background-networking"])
        print("launched", flush=True)
        only = sys.argv[2] if len(sys.argv) > 2 else ""
        for who, name, vp, state in shots:
            if only and only not in who + "-" + name:
                continue
            ctx = b.new_context(viewport=vp, device_scale_factor=1 if vp["width"] > 900 else 2)
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            print("render", who, name, flush=True)
            run(page, who, state)
            page.screenshot(path=str(OUT / f"{who}-{name}.png"), full_page=not state.get("viewport_only"))
            if errors:
                print(who, name, "PAGE ERRORS:", errors)
            ctx.close()
        b.close()
    print("saved to", OUT)


if __name__ == "__main__":
    main()
