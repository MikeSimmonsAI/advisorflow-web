"""Spend a one-time staff setup link and set a password for the QA identity.

    python scripts/m2_prod_activate.py <base_url> <token_file>

The token is read from a file and the file is deleted afterwards, so a one-time
credential never sits in a shell history or a process list. The password comes
from M2_VERIFY_PASSWORD in the environment and is never printed.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

base = (sys.argv[1] if len(sys.argv) > 1 else "").rstrip("/")
token_file = sys.argv[2] if len(sys.argv) > 2 else ""
password = os.environ.get("M2_VERIFY_PASSWORD")

if not (base and token_file and password):
    print("usage: m2_prod_activate.py <base_url> <token_file>  "
          "(with M2_VERIFY_PASSWORD set)")
    sys.exit(2)

with open(token_file, "r", encoding="utf-8") as fh:
    token = fh.read().strip()


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


code, preview = call("GET", "/auth/staff-activation?token=" +
                     urllib.parse.quote(token))
print("preview: %s %s" % (code, preview))
if code != 200:
    sys.exit(1)

code, body = call("POST", "/auth/staff-activation/accept",
                  {"token": token, "new_password": password})
print("accept: %s %s" % (code, body))

try:
    os.remove(token_file)
    print("one-time token file removed")
except Exception as e:                                       # noqa: BLE001
    print("could not remove token file: %s" % e)

sys.exit(0 if code == 200 else 1)
