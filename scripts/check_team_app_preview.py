"""End-to-end check of a team-app preview over its real API (synthetic people only).

    set PREVIEW_PASSWORD=...   (never commit it)
    python scripts/check_team_app_preview.py https://<api host> https://<web host>

Prints one PASS/FAIL line per check and exits non-zero on any failure. It only
signs in as @preview.test people, so it is refused by anything else.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

API, WEB = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/")
PW = os.environ.get("PREVIEW_PASSWORD") or "Preview123!"
FAILS = []


def call(tok, method, path, body=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Origin": WEB}
    if tok:
        h["Authorization"] = "Bearer " + tok
    if data is not None:
        h["Content-Type"] = ctype
    req = urllib.request.Request(API + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            b = r.read()
            try:
                return r.status, json.loads(b)
            except ValueError:
                return r.status, b
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200]


def login(who):
    d = urllib.parse.urlencode({"username": who + "@preview.test", "password": PW}).encode()
    req = urllib.request.Request(API + "/auth/login", data=d, headers={"Origin": WEB})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["access_token"]


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("  - " + str(detail)) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def main():
    t0 = time.time()
    s, _ = call(None, "GET", "/health")
    check("API answers", s == 200, s)
    who = ["owner", "platform", "partner", "rep1", "rep2", "newrep", "chatmgr", "timemgr", "outsider"]
    T = {w: login(w) for w in who}
    check("all synthetic people sign in", len(T) == len(who))
    A = {w: call(T[w], "GET", "/team-app/me")[1]["access"] for w in who}
    C = {w: call(T[w], "GET", "/auth/my-contexts")[1] for w in ("platform", "partner", "newrep")}
    ws = lambda w: sorted(x["organization_name"] for x in C[w]["workspace_contexts"])
    check("Platform sees SCI and its other workspaces",
          "Service Corporation International" in ws("platform") and len(ws("platform")) == 3, ws("platform"))
    check("Platform: SCI opens as the SCI program",
          any(x.get("program_is_sci") for x in C["platform"]["workspace_contexts"]))
    check("Partner sees only assigned workspaces", ws("partner") == ["Preview Roofing Co", "Service Corporation International"], ws("partner"))
    check("New rep has no workspace", ws("newrep") == [], ws("newrep"))
    check("Platform: Chat, Time, announce, manage rooms, Team Time",
          all(A["platform"][k] for k in ("chat", "time", "announce", "chat_manage", "time_manage")), A["platform"])
    check("Partner: Chat + Time only until granted",
          A["partner"]["chat"] and A["partner"]["time"] and not any(A["partner"][k] for k in ("announce", "chat_manage", "time_manage")), A["partner"])
    check("New rep (no workspace): Chat + Time", A["newrep"]["chat"] and A["newrep"]["time"], A["newrep"])
    check("Outsider: no Chat, no Time", not A["outsider"]["chat"] and not A["outsider"]["time"])
    check("Chat manager: rooms but no Team Time", A["chatmgr"]["chat_manage"] and not A["chatmgr"]["time_manage"])

    rooms = {r["slug"] or r["id"]: r for r in call(T["rep1"], "GET", "/team-app/chat/rooms")[1]["rooms"]}
    sales, ann = rooms["sales-team"]["id"], rooms["announcements"]["id"]
    call(T["rep2"], "GET", "/team-app/chat/rooms")
    call(T["rep2"], "POST", "/team-app/chat/rooms/%s/read" % sales, {})
    text = "Preview check %s" % uuid.uuid4().hex[:6]
    s, m = call(T["rep1"], "POST", "/team-app/chat/rooms/%s/messages" % sales, {"body": text, "client_key": uuid.uuid4().hex})
    check("rep1 posts to Sales Team", s == 200, s)
    msgs = call(T["rep2"], "GET", "/team-app/chat/rooms/%s/messages?limit=5" % sales)[1]["messages"]
    check("rep2 receives it (no refresh needed: polled read)", any(x["body"] == text for x in msgs))
    check("rep2 unread badge counts it", call(T["rep2"], "GET", "/team-app/chat/unread")[1]["unread_total"] >= 1)
    check("rep cannot post announcements", call(T["rep1"], "POST", "/team-app/chat/rooms/%s/messages" % ann, {"body": "x"})[0] == 403)
    check("platform posts announcements", call(T["platform"], "POST", "/team-app/chat/rooms/%s/messages" % ann, {"body": "Preview announcement"})[0] == 200)

    me2 = call(T["rep2"], "GET", "/team-app/me")[1]["user"]["id"]
    dm = call(T["rep1"], "POST", "/team-app/chat/dm", {"user_id": me2})[1]
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    bnd = uuid.uuid4().hex
    raw = (("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"Sales Script.pdf\"\r\n"
            "Content-Type: application/pdf\r\n\r\n" % bnd).encode() + pdf + ("\r\n--%s--\r\n" % bnd).encode())
    s, att = call(T["rep1"], "POST", "/team-app/chat/rooms/%s/attachments" % dm["id"], raw=raw,
                  ctype="multipart/form-data; boundary=" + bnd)
    check("private file upload", s == 200 and "storage_key" not in att, s)
    call(T["rep1"], "POST", "/team-app/chat/rooms/%s/messages" % dm["id"], {"body": "", "attachment_ids": [att["id"]]})
    s, body = call(T["rep2"], "GET", att["download_path"])
    check("DM partner downloads the file", s == 200 and body == pdf, s)
    for w in ("chatmgr", "platform", "owner"):
        check("%s cannot read the DM" % w, call(T[w], "GET", "/team-app/chat/rooms/%s/messages" % dm["id"])[0] == 404)
        check("%s cannot download the DM file" % w, call(T[w], "GET", att["download_path"])[0] == 404)
    check("outsider direct link to a room refused", call(T["outsider"], "GET", "/team-app/chat/rooms/%s/messages" % sales)[0] == 403)
    check("chat manager cannot manage a DM", call(T["chatmgr"], "GET", "/team-app/chat/rooms/%s/members" % dm["id"])[0] == 404)

    a = call(T["newrep"], "POST", "/team-app/time/clock-in", {})[1]
    b = call(T["newrep"], "POST", "/team-app/time/clock-in", {})[1]
    check("clock in (double tap = one shift)", a["clocked_in"] and b["already"] and a["entry"]["id"] == b["entry"]["id"])
    c = call(T["newrep"], "POST", "/team-app/time/clock-out", {})[1]
    check("clock out", not c["clocked_in"])
    wk = call(T["newrep"], "GET", "/team-app/time/mine")[1]
    check("My Time shows the shift in America/Chicago", wk["time_zone"] == "America/Chicago" and len(wk["entries"]) >= 1)
    tm = {p["name"] for p in call(T["timemgr"], "GET", "/team-app/time/team")[1]["people"]}
    check("Time manager sees only Rep One", tm == {"Rep One"}, tm)
    check("Chat manager refused Team Time", call(T["chatmgr"], "GET", "/team-app/time/team")[0] == 403)
    check("Partner refused Team Time until granted", call(T["partner"], "GET", "/team-app/time/team")[0] == 403)
    check("Only the owner can change Team access", call(T["platform"], "GET", "/team-app/admin/people")[0] == 403
          and call(T["owner"], "GET", "/team-app/admin/people")[0] == 200)

    call(T["rep2"], "POST", "/auth/logout", {})
    check("sign-out ends the session", call(T["rep2"], "GET", "/team-app/me")[0] == 401)
    print("\n%d checks failed. %.0fs" % (len(FAILS), time.time() - t0))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
