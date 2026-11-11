"""Local QA (local backend :8011 + vite preview :5173; QA_EMAIL/QA_PASSWORD = a LOCAL principal): ChatGPT-style OAuth sign-in through the real Allow page, then MCP calls."""
import base64, hashlib, json, os, secrets, sys, urllib.parse, urllib.request
from playwright.sync_api import sync_playwright
API = "http://127.0.0.1:8011"; WEB = "http://localhost:5173"; CB = "https://chatgpt.com/connector_platform_oauth_redirect"
OUT = sys.argv[1]
def post(path, body, form=False, hdr=None):
    data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
    h = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json", **(hdr or {})}
    r = urllib.request.urlopen(urllib.request.Request(API + path, data=data, headers=h, method="POST"))
    return r.status, json.loads(r.read() or b"null")
st, client = post("/volt/dev/oauth/register", {"client_name": "ChatGPT", "redirect_uris": [CB]})
v = secrets.token_urlsafe(48); ch = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
q = urllib.parse.urlencode({"response_type": "code", "client_id": client["client_id"], "redirect_uri": CB, "state": "s1",
                            "code_challenge": ch, "code_challenge_method": "S256", "scope": "volt.dev"})
class NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
try:
    urllib.request.build_opener(NoRedir).open(API + "/volt/dev/oauth/authorize?" + q)
except urllib.error.HTTPError as e:
    consent = e.headers["Location"]
print("consent:", consent)
checks = []
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 390, "height": 844})
    landed = {}
    pg.route("https://chatgpt.com/**", lambda route: (landed.setdefault("url", route.request.url), route.fulfill(body="ok")))
    pg.goto(consent); pg.wait_for_url("**/login**", timeout=15000)
    checks.append(("unsigned -> login with next", "next=%2Fvolt-dev%2Fconnect" in pg.url))
    pg.fill("input[type=email]", os.environ["QA_EMAIL"]); pg.fill("input[type=password]", os.environ["QA_PASSWORD"])
    pg.keyboard.press("Enter")
    pg.wait_for_selector("[data-dev-connect] [data-connect-allow]", timeout=20000)
    pg.screenshot(path=OUT + "/connect-allow-phone.png", full_page=True)
    txt = pg.inner_text("[data-dev-connect]")
    checks.append(("shows who", os.environ["QA_EMAIL"] in txt))
    checks.append(("lists approve-with-commit", "exact commit" in txt))
    pg.click("[data-connect-allow]"); pg.wait_for_timeout(2500)
    url = landed.get("url", "")
    code = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("code", [None])[0]
    checks.append(("returned to chatgpt with code+state", bool(code) and "state=s1" in url))
    b.close()
st, tok = post("/volt/dev/oauth/token", {"grant_type": "authorization_code", "code": code, "client_id": client["client_id"],
                                         "redirect_uri": CB, "code_verifier": v}, form=True)
checks.append(("token issued", tok["access_token"].startswith("vdo_")))
A = {"Authorization": "Bearer " + tok["access_token"]}
st, r = post("/volt/dev/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "relay_status", "arguments": {}}}, hdr=A)
sc = r["result"]["structuredContent"]
checks.append(("mcp relay_status as signed-in person", sc["email"] == os.environ["QA_EMAIL"]))
print(json.dumps(sc)[:400])
for name, ok in checks: print(("PASS " if ok else "FAIL ") + name)
