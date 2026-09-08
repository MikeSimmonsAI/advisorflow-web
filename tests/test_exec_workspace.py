"""
Executive Workspace boundary tests.

ACTOR MATRIX
────────────
  Exec A  → Org A only
  Exec B  → Org B only
  Exec C  → Org A + Org B          (cross-org executive)
  Exec D  → no orgs               (brand grant, zero assignments)
  Owner   → god_admin, all orgs
  Outsider→ no brand grant at all

INVARIANTS UNDER TEST
─────────────────────
1. An item in Org A is invisible to Exec B and Outsider.
2. An item in Org B is invisible to Exec A.
3. Exec C can see both org A and org B items.
4. Exec D (empty portfolio) sees zero items.
5. Owner sees everything.
6. A file served from Org A requires the caller to own Org A.
7. A PATCH that changes any tracked field writes exactly one snapshot.
8. A PATCH that changes nothing writes no snapshot.
9. PUT /files/{id} marks old as not-current and produces replaces_file_id.
10. Cross-org create is blocked (404).
"""

import io
import itertools
from datetime import datetime, timedelta

import pytest

from app.models.models import Organization, Platform, User
from app.models.sales_models import (
    ROLE_BRAND_EXECUTIVE, SCOPE_CUSTOMER_ORG, SCOPE_PLATFORM, Membership,
)
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(9000)


# ─────────────────────────────────────────────────────────────────────────────
# Fixture helpers
# ─────────────────────────────────────────────────────────────────────────────

def _platform(db, name):
    p = Platform(name=name, slug="wsp-%d" % next(_SEQ))
    db.add(p); db.commit()
    return p


def _org(db, platform, name):
    o = Organization(
        name=name, slug="wso-%d" % next(_SEQ),
        platform_id=platform.id, plan="starter", is_active=True,
        created_at=datetime.utcnow() - timedelta(days=60),
    )
    db.add(o); db.commit()
    return o


def _exec(db, platform, name, orgs):
    u = User(
        organization_id=None,
        email="wsex%d@x.test" % next(_SEQ),
        password_hash=hash_password("x"),
        full_name=name, role="advisor", must_change_password=False,
    )
    db.add(u); db.commit()
    db.add(Membership(user_id=u.id, scope_type=SCOPE_PLATFORM,
                      scope_id=platform.id, role=ROLE_BRAND_EXECUTIVE,
                      is_active=True))
    db.commit()
    for o in orgs:
        db.add(Membership(user_id=u.id, scope_type=SCOPE_CUSTOMER_ORG,
                          scope_id=o.id, role=ROLE_BRAND_EXECUTIVE,
                          is_active=True))
    db.commit()
    return u


def _god(db):
    u = User(
        organization_id=None,
        email="wsgod%d@x.test" % next(_SEQ),
        password_hash=hash_password("x"),
        full_name="Owner", role="god_admin", must_change_password=False,
    )
    db.add(u); db.commit()
    return u


def _plain(db):
    """A user with no executive grant at all."""
    u = User(
        organization_id=None,
        email="wspout%d@x.test" % next(_SEQ),
        password_hash=hash_password("x"),
        full_name="Outsider", role="advisor", must_change_password=False,
    )
    db.add(u); db.commit()
    return u


def _h(db, u, platform_id=None):
    h = {"Authorization": "Bearer " + create_access_token(u, db)}
    if platform_id:
        h["X-Brand-Override"] = platform_id
    return h


# ─────────────────────────────────────────────────────────────────────────────
# Main fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def ws(db_session, client):
    p = _platform(db_session, "WsBrand")
    oa = _org(db_session, p, "Org Alpha")
    ob = _org(db_session, p, "Org Beta")
    exec_a = _exec(db_session, p, "Exec A", [oa])
    exec_b = _exec(db_session, p, "Exec B", [ob])
    exec_c = _exec(db_session, p, "Exec C", [oa, ob])
    exec_d = _exec(db_session, p, "Exec D", [])     # empty portfolio
    owner  = _god(db_session)
    outsider = _plain(db_session)
    return {
        "p": p, "oa": oa, "ob": ob,
        "a": exec_a, "b": exec_b, "c": exec_c, "d": exec_d,
        "owner": owner, "outsider": outsider,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers that call the API
# ─────────────────────────────────────────────────────────────────────────────

def _create_item(client, db, user, org_id, title="Deal X", status="Draft"):
    r = client.post(
        "/executive/workspace/",
        json={"organization_id": org_id, "title": title, "status": status},
        headers=_h(db, user),
    )
    return r


def _upload(client, db, user, item_id, content=b"hello", filename="doc.txt"):
    r = client.post(
        f"/executive/workspace/{item_id}/files",
        files={"file": (filename, io.BytesIO(content), "text/plain")},
        headers=_h(db, user),
    )
    return r


# ─────────────────────────────────────────────────────────────────────────────
# 1. Item visibility: each exec sees only their orgs
# ─────────────────────────────────────────────────────────────────────────────

def test_exec_a_cannot_see_org_b_items(client, db_session, ws):
    # Exec C creates an item in Org B
    r = _create_item(client, db_session, ws["c"], ws["ob"].id, "Beta Deal")
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]

    # Exec A tries to fetch it — must 404
    r2 = client.get(f"/executive/workspace/{item_id}", headers=_h(db_session, ws["a"]))
    assert r2.status_code == 404


def test_exec_b_cannot_see_org_a_items(client, db_session, ws):
    r = _create_item(client, db_session, ws["a"], ws["oa"].id, "Alpha Deal")
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]

    r2 = client.get(f"/executive/workspace/{item_id}", headers=_h(db_session, ws["b"]))
    assert r2.status_code == 404


def test_exec_c_sees_both_orgs(client, db_session, ws):
    r1 = _create_item(client, db_session, ws["a"], ws["oa"].id, "Alpha Deal C")
    r2 = _create_item(client, db_session, ws["c"], ws["ob"].id, "Beta Deal C")
    assert r1.status_code == 201
    assert r2.status_code == 201

    # List — Exec C should see both
    r3 = client.get("/executive/workspace/", headers=_h(db_session, ws["c"]))
    assert r3.status_code == 200
    titles = {i["title"] for i in r3.json()["items"]}
    assert "Alpha Deal C" in titles
    assert "Beta Deal C" in titles


def test_exec_d_empty_portfolio_sees_nothing(client, db_session, ws):
    _create_item(client, db_session, ws["a"], ws["oa"].id, "Alpha Deal D")
    r = client.get("/executive/workspace/", headers=_h(db_session, ws["d"]))
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_owner_sees_all_orgs(client, db_session, ws):
    r1 = _create_item(client, db_session, ws["a"], ws["oa"].id, "Alpha Owner")
    r2 = _create_item(client, db_session, ws["b"], ws["ob"].id, "Beta Owner")
    assert r1.status_code == 201
    assert r2.status_code == 201

    r = client.get("/executive/workspace/", headers=_h(db_session, ws["owner"], ws["p"].id))
    assert r.status_code == 200
    titles = {i["title"] for i in r.json()["items"]}
    assert "Alpha Owner" in titles
    assert "Beta Owner" in titles


def test_outsider_no_brand_grant_401_or_403(client, db_session, ws):
    r = client.get("/executive/workspace/", headers=_h(db_session, ws["outsider"]))
    assert r.status_code in (401, 403)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Cross-org create is blocked
# ─────────────────────────────────────────────────────────────────────────────

def test_cross_org_create_blocked(client, db_session, ws):
    # Exec A tries to create an item in Org B which they cannot see
    r = _create_item(client, db_session, ws["a"], ws["ob"].id, "Forbidden")
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# 3. File serve is org-gated
# ─────────────────────────────────────────────────────────────────────────────

def test_file_serve_blocked_for_wrong_org(client, db_session, ws):
    # Exec C creates item in Org B and uploads a file
    r = _create_item(client, db_session, ws["c"], ws["ob"].id, "Beta File")
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]

    ru = _upload(client, db_session, ws["c"], item_id, b"secret", "secret.txt")
    assert ru.status_code == 201, ru.text
    file_id = ru.json()["id"]

    # Exec A (Org A only) tries to serve the file — must 404
    rs = client.get(f"/executive/workspace/{item_id}/files/{file_id}/serve",
                    headers=_h(db_session, ws["a"]))
    assert rs.status_code == 404


def test_file_serve_works_for_correct_org(client, db_session, ws):
    r = _create_item(client, db_session, ws["a"], ws["oa"].id, "Alpha File")
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]

    ru = _upload(client, db_session, ws["a"], item_id, b"content", "a.txt")
    assert ru.status_code == 201, ru.text
    file_id = ru.json()["id"]

    rs = client.get(f"/executive/workspace/{item_id}/files/{file_id}/serve",
                    headers=_h(db_session, ws["a"]))
    assert rs.status_code == 200
    assert rs.content == b"content"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Snapshot semantics
# ─────────────────────────────────────────────────────────────────────────────

def _version_count(client, db, user, item_id):
    r = client.get(f"/executive/workspace/{item_id}/versions",
                   headers=_h(db, user))
    assert r.status_code == 200, r.text
    return len(r.json()["versions"])


def test_patch_with_changed_title_writes_one_snapshot(client, db_session, ws):
    r = _create_item(client, db_session, ws["a"], ws["oa"].id, "Original Title")
    assert r.status_code == 201
    item_id = r.json()["id"]
    assert _version_count(client, db_session, ws["a"], item_id) == 0

    client.patch(f"/executive/workspace/{item_id}",
                 json={"title": "Updated Title"},
                 headers=_h(db_session, ws["a"]))
    assert _version_count(client, db_session, ws["a"], item_id) == 1


def test_patch_multiple_fields_writes_exactly_one_snapshot(client, db_session, ws):
    r = _create_item(client, db_session, ws["a"], ws["oa"].id, "Multi Fields")
    assert r.status_code == 201
    item_id = r.json()["id"]

    client.patch(f"/executive/workspace/{item_id}",
                 json={"title": "New Title", "status": "Partner Review",
                       "working_notes": "notes changed"},
                 headers=_h(db_session, ws["a"]))
    # Three fields changed — still exactly ONE snapshot.
    assert _version_count(client, db_session, ws["a"], item_id) == 1


def test_patch_unchanged_fields_writes_no_snapshot(client, db_session, ws):
    r = _create_item(client, db_session, ws["a"], ws["oa"].id, "No Change")
    assert r.status_code == 201
    item_id = r.json()["id"]

    # PATCH with identical values
    client.patch(f"/executive/workspace/{item_id}",
                 json={"title": "No Change"},
                 headers=_h(db_session, ws["a"]))
    assert _version_count(client, db_session, ws["a"], item_id) == 0


def test_snapshot_captures_pre_change_values(client, db_session, ws):
    r = _create_item(client, db_session, ws["a"], ws["oa"].id, "Before Title")
    assert r.status_code == 201
    item_id = r.json()["id"]

    client.patch(f"/executive/workspace/{item_id}",
                 json={"title": "After Title", "status": "Partner Review"},
                 headers=_h(db_session, ws["a"]))

    rv = client.get(f"/executive/workspace/{item_id}/versions",
                    headers=_h(db_session, ws["a"]))
    assert rv.status_code == 200
    v = rv.json()["versions"][0]
    # Snapshot must capture the BEFORE state
    assert v["snapshot_title"] == "Before Title"
    assert v["snapshot_status"] == "Draft"


# ─────────────────────────────────────────────────────────────────────────────
# 5. File replacement (PUT)
# ─────────────────────────────────────────────────────────────────────────────

def test_file_replace_marks_old_as_not_current(client, db_session, ws):
    r = _create_item(client, db_session, ws["a"], ws["oa"].id, "Replace Test")
    assert r.status_code == 201
    item_id = r.json()["id"]

    ru = _upload(client, db_session, ws["a"], item_id, b"v1", "v1.txt")
    assert ru.status_code == 201
    file_id = ru.json()["id"]

    rp = client.put(
        f"/executive/workspace/{item_id}/files/{file_id}",
        files={"file": ("v2.txt", io.BytesIO(b"v2"), "text/plain")},
        headers=_h(db_session, ws["a"]),
    )
    assert rp.status_code == 201, rp.text
    new_file = rp.json()
    assert new_file["replaces_file_id"] == file_id
    assert new_file["is_current"] is True

    # Listing current files should show only the new one
    rl = client.get(f"/executive/workspace/{item_id}/files",
                    headers=_h(db_session, ws["a"]))
    assert rl.status_code == 200
    current_ids = [f["id"] for f in rl.json()["files"]]
    assert new_file["id"] in current_ids
    assert file_id not in current_ids


def test_file_history_includes_both_versions(client, db_session, ws):
    r = _create_item(client, db_session, ws["a"], ws["oa"].id, "History Test")
    assert r.status_code == 201
    item_id = r.json()["id"]

    ru = _upload(client, db_session, ws["a"], item_id, b"v1", "v1.txt")
    file_id = ru.json()["id"]

    rp = client.put(
        f"/executive/workspace/{item_id}/files/{file_id}",
        files={"file": ("v2.txt", io.BytesIO(b"v2"), "text/plain")},
        headers=_h(db_session, ws["a"]),
    )
    new_id = rp.json()["id"]

    rh = client.get(f"/executive/workspace/{item_id}/files/{new_id}/history",
                    headers=_h(db_session, ws["a"]))
    assert rh.status_code == 200
    ids_in_history = {f["id"] for f in rh.json()["history"]}
    assert file_id in ids_in_history
    assert new_id in ids_in_history


# ─────────────────────────────────────────────────────────────────────────────
# 6. Status validation
# ─────────────────────────────────────────────────────────────────────────────

def test_invalid_status_on_create_returns_422(client, db_session, ws):
    r = _create_item(client, db_session, ws["a"], ws["oa"].id,
                     title="Bad Status", status="Nonsense")
    assert r.status_code == 422


def test_invalid_status_on_patch_returns_422(client, db_session, ws):
    r = _create_item(client, db_session, ws["a"], ws["oa"].id, "Status Patch")
    assert r.status_code == 201
    item_id = r.json()["id"]

    rp = client.patch(f"/executive/workspace/{item_id}",
                      json={"status": "Garbage"},
                      headers=_h(db_session, ws["a"]))
    assert rp.status_code == 422


def test_valid_statuses_all_accepted(client, db_session, ws):
    for s in ("Draft", "Partner Review", "Approved", "Final"):
        r = _create_item(client, db_session, ws["a"], ws["oa"].id,
                         title=f"Status {s}", status=s)
        assert r.status_code == 201, f"status={s} rejected: {r.text}"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Version detail endpoint
# ─────────────────────────────────────────────────────────────────────────────

def test_version_detail_accessible_only_by_authorized_exec(client, db_session, ws):
    r = _create_item(client, db_session, ws["a"], ws["oa"].id, "Version Detail")
    assert r.status_code == 201
    item_id = r.json()["id"]

    client.patch(f"/executive/workspace/{item_id}",
                 json={"title": "Changed"},
                 headers=_h(db_session, ws["a"]))

    rv = client.get(f"/executive/workspace/{item_id}/versions",
                    headers=_h(db_session, ws["a"]))
    version_id = rv.json()["versions"][0]["id"]

    # Exec B cannot read this version
    rb = client.get(f"/executive/workspace/{item_id}/versions/{version_id}",
                    headers=_h(db_session, ws["b"]))
    assert rb.status_code == 404

    # Exec A can
    ra = client.get(f"/executive/workspace/{item_id}/versions/{version_id}",
                    headers=_h(db_session, ws["a"]))
    assert ra.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 8. org_id filter on list
# ─────────────────────────────────────────────────────────────────────────────

def test_list_filtered_by_org_id(client, db_session, ws):
    _create_item(client, db_session, ws["c"], ws["oa"].id, "Alpha Filter")
    _create_item(client, db_session, ws["c"], ws["ob"].id, "Beta Filter")

    r = client.get(f"/executive/workspace/?organization_id={ws['oa'].id}",
                   headers=_h(db_session, ws["c"]))
    assert r.status_code == 200
    titles = {i["title"] for i in r.json()["items"]}
    assert "Alpha Filter" in titles
    assert "Beta Filter" not in titles


def test_list_filter_by_unauthorized_org_returns_404(client, db_session, ws):
    # Exec A tries to filter by Org B
    r = client.get(f"/executive/workspace/?organization_id={ws['ob'].id}",
                   headers=_h(db_session, ws["a"]))
    assert r.status_code == 404
