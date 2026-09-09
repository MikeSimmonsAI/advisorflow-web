import urllib.request
import urllib.error
import re
import sys

BASE = "https://app.evosyspro.live"
API  = "https://advisorflow-backend.onrender.com"

results = []

def check(label, url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "smoke-test/1.0"})
        resp = urllib.request.urlopen(req, timeout=20)
        status = resp.getcode()
        ct = resp.headers.get("content-type", "")
        body = resp.read()
        ok = status == 200
        print(f"{'OK  ' if ok else 'FAIL'} {label}: HTTP {status}  ct={ct[:50]}  size={len(body)}")
        results.append(ok)
        return body
    except Exception as e:
        print(f"FAIL {label}: {e}")
        results.append(False)
        return b""

# 1. HTML
html_bytes = check("HTML app.evosyspro.live", BASE)
html = html_bytes.decode("utf-8", errors="replace")

# Extract asset refs from index.html
js_refs  = re.findall(r'src="(/assets/[^"]+\.js)"', html)
css_refs = re.findall(r'href="(/assets/[^"]+\.css)"', html)
print(f"     JS refs  : {js_refs}")
print(f"     CSS refs : {css_refs}")

# 2. JS assets
for ref in js_refs:
    check(f"JS  {ref}", BASE + ref)

# 3. CSS assets
for ref in css_refs:
    check(f"CSS {ref}", BASE + ref)

# 4. API health
check("API /health", API + "/health")

# Summary
passed = sum(results)
total  = len(results)
print()
print(f"SMOKE: {passed}/{total} checks passed")
if passed < total:
    sys.exit(1)
