"""Give one person a God-granted preview of one deal's existing demo.

DRY RUN BY DEFAULT. Without --apply nothing is written: the server checks
everything (the person, their brand, the deal is open, the demo is live, no
duplicate) and shows what it would create.

Needs two environment variables (nothing is stored in this file):
  PREVIEW_API_BASE   the backend address, e.g. https://<your-backend-host>
  GOD_TOKEN          a God Mode sign-in token for that backend

Examples:
  python scripts/preview_grant.py grant --deal <opportunity id> \
      --email person@example.com --brand bookaboost --days 30 \
      --reason "Owner approved: BookaBoost admin reviews the land demo"
  python scripts/preview_grant.py grant ... --apply
  python scripts/preview_grant.py list --deal <opportunity id>
  python scripts/preview_grant.py revoke <grant id> --apply

The deal id is the last part of the deal's Back Office address
(/sales/leads/<opportunity id>). Grants never include the demo link.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def _call(method, path, body=None):
    base = os.environ.get("PREVIEW_API_BASE", "").rstrip("/")
    token = os.environ.get("GOD_TOKEN", "")
    if not base or not token:
        sys.exit("Set PREVIEW_API_BASE and GOD_TOKEN first.")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("grant")
    g.add_argument("--deal", required=True)
    g.add_argument("--email", required=True)
    g.add_argument("--brand", required=True, help="brand slug, e.g. bookaboost")
    g.add_argument("--reason", required=True)
    g.add_argument("--days", type=int, default=None, help="ends after this many days (optional)")
    g.add_argument("--apply", action="store_true")
    ls = sub.add_parser("list")
    ls.add_argument("--deal", default=None)
    rv = sub.add_parser("revoke")
    rv.add_argument("grant_id")
    rv.add_argument("--note", default=None)
    rv.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "grant":
        body = {"opportunity_id": a.deal, "grantee_email": a.email, "platform_slug": a.brand,
                "reason": a.reason, "dry_run": not a.apply}
        if a.days:
            body["expires_in_days"] = a.days
        status, out = _call("POST", "/god/preview-grants", body)
    elif a.cmd == "list":
        q = ("?opportunity_id=" + urllib.parse.quote(a.deal)) if a.deal else ""
        status, out = _call("GET", "/god/preview-grants" + q)
    else:
        if not a.apply:
            status, out = _call("GET", "/god/preview-grants")
            hit = [x for x in (out or {}).get("grants", []) if x.get("id") == a.grant_id]
            print("DRY RUN - would revoke:" if hit else "No such grant.")
            print(json.dumps(hit, indent=2))
            return 0 if hit else 1
        status, out = _call("POST", "/god/preview-grants/%s/revoke" % urllib.parse.quote(a.grant_id),
                            {"note": a.note})
    if a.cmd == "grant" and not a.apply:
        print("DRY RUN - nothing written. Add --apply to create it.")
    print(status)
    print(json.dumps(out, indent=2))
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
