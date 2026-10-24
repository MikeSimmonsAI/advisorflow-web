"""Publishing an investor page the ONLY way the product allows: a deal package,
an Approval Center approval by an admin, then publish. Tests use this instead of
flipping the room on directly, which the server now refuses."""


def _ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:300])
    return r.json()


def approve_and_publish(client, headers, admin_headers, deal_id):
    base = "/wholesale/deals/%s/package" % deal_id
    st = _ok(client.get(base, headers=headers))
    if st["status"] in ("none", "withdrawn"):
        st = _ok(client.post(base, headers=headers))
    if st.get("live"):
        return st
    if st["status"] in ("draft", "changed") or (st["status"] == "approved" and st.get("changed_since_approval")):
        st = _ok(client.post(base + "/submit", headers=headers))
    if (st.get("approval") or {}).get("status") == "pending":
        _ok(client.post("/wholesale/approvals/%s/decide" % st["approval"]["id"], headers=admin_headers,
                        json={"approve": True}))
        st = _ok(client.get(base, headers=headers))
    if not st.get("live"):
        _ok(client.post("/wholesale/deals/%s/publication/state" % deal_id, headers=admin_headers,
                        json={"audience": "buyer", "published": True}))
        st = _ok(client.get(base, headers=headers))
    assert st["live"], st
    return st
