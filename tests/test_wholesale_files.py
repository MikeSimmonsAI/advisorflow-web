"""The file layer: what it accepts, what it refuses, and who may read it back.

The refusals matter more than the acceptances. A document drawer that accepts a
renamed executable, serves a purchase contract to anyone with the link, or says
"uploaded" on a deployment with nowhere to put the bytes is worse than no
drawer at all.
"""

import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128
PDF = b"%PDF-1.4\n" + b"\x00" * 128


def ok(response):
    assert response.status_code in (200, 201), \
        "%s %s" % (response.status_code, response.text[:300])
    return response.json()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.setenv("WHOLESALE_LOCAL_MEDIA_ROOT", str(tmp_path / "media"))
    return tmp_path


@pytest.fixture
def prop(client, auth_headers):
    return ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "1 Photo Way", "state": "TX",
                                "is_test": True}))


def upload(client, headers, path, data=PNG, name="shot.png", ctype="image/png",
           form=None):
    return client.post(path, headers=headers,
                       files={"file": (name, data, ctype)}, data=form or {})


# ── What the deployment can do, said out loud ───────────────────────────────

def test_with_no_storage_configured_the_product_says_so_and_refuses(
        client, auth_headers, prop, monkeypatch):
    """A 200 that evaporates is the failure this whole design exists to avoid."""
    monkeypatch.delenv("MEDIA_STORAGE_BACKEND", raising=False)

    cap = ok(client.get("/wholesale/files/capability", headers=auth_headers))
    assert cap["uploads_enabled"] is False
    assert "MEDIA_STORAGE_BACKEND" in cap["reason"]
    assert cap["env"] == "MEDIA_STORAGE_BACKEND"

    response = upload(client, auth_headers,
                      "/wholesale/properties/%s/photos" % prop["id"])
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_local_storage_is_offered_and_says_exactly_what_it_is(
        client, auth_headers, storage):
    """Phase 4 corrected this claim rather than softening it.

    The earlier version reported the local backend as `durable: false`, which
    was not true — a file written to the server's own disk survives a restart.
    What it is NOT is replicated, and that is the thing a production deployment
    has to care about. The capability now reports the two separately and names
    where the bytes went, so the screen can say so in one clause instead of
    telling somebody their uploads will evaporate when they will not.
    """
    cap = ok(client.get("/wholesale/files/capability", headers=auth_headers))
    assert cap["uploads_enabled"] is True
    assert cap["durable"] is True            # it survives a restart
    assert cap["replicated"] is False        # the honest part
    assert cap["where"] == "this server's disk"
    assert "not replicated" in cap["reason"]
    # The reason still names the switch to set, so the fix is actionable.
    assert "MEDIA_STORAGE_BACKEND=s3" in cap["reason"]


# ── What the bytes are, not what the upload claimed ─────────────────────────

def test_a_renamed_executable_is_refused_however_it_is_labelled(
        client, auth_headers, prop, storage):
    """The classic. Content-Type and filename are both attacker-controlled."""
    evil = b"MZ\x90\x00" + b"\x00" * 200
    response = upload(client, auth_headers,
                      "/wholesale/properties/%s/photos" % prop["id"],
                      data=evil, name="innocent.png", ctype="image/png")
    assert response.status_code == 400
    assert "not accepted" in response.json()["detail"] \
        or "must be a JPG" in response.json()["detail"]


def test_a_pdf_is_not_a_property_photo(client, auth_headers, prop, storage):
    response = upload(client, auth_headers,
                      "/wholesale/properties/%s/photos" % prop["id"],
                      data=PDF, name="contract.pdf", ctype="application/pdf")
    assert response.status_code == 400
    assert "photo must be" in response.json()["detail"]


def test_a_pdf_is_a_perfectly_good_document(client, auth_headers, prop, storage):
    deal_id = prop["deal"]["id"]
    result = ok(upload(client, auth_headers,
                       "/wholesale/deals/%s/documents/upload" % deal_id,
                       data=PDF, name="contract.pdf", ctype="application/pdf",
                       form={"doc_type": "purchase_contract",
                             "title": "Purchase contract"}))
    assert result["file"]["content_type"] == "application/pdf"
    assert result["document_id"]


def test_an_empty_file_is_refused(client, auth_headers, prop, storage):
    response = upload(client, auth_headers,
                      "/wholesale/properties/%s/photos" % prop["id"], data=b"")
    assert response.status_code == 400


def test_the_stored_key_is_never_built_from_the_uploaded_name(
        client, auth_headers, prop, storage):
    """A filename is a string somebody typed. It is kept for display and is
    not allowed anywhere near the path the bytes are written to."""
    from app.services import wholesale_files

    result = ok(upload(client, auth_headers,
                       "/wholesale/properties/%s/photos" % prop["id"],
                       name="../../../../etc/passwd.png"))
    assert "passwd" in (result["original_filename"] or "")   # kept for display
    assert ".." not in result["original_filename"]           # but stripped of path

    # And nothing was written outside the media root.
    root = wholesale_files.local_root()
    import os
    for here, _dirs, names in os.walk(root):
        for name in names:
            assert os.path.abspath(os.path.join(here, name)).startswith(
                os.path.abspath(root))


# ── Who may read it back ────────────────────────────────────────────────────

def test_a_file_is_served_to_its_owner_and_never_by_public_url(
        client, auth_headers, prop, storage):
    photo = ok(upload(client, auth_headers,
                      "/wholesale/properties/%s/photos" % prop["id"]))

    # The payload carries an app route, not a bucket URL.
    assert photo["url"] == "/wholesale/files/%s" % photo["id"]
    assert "http" not in photo["url"]
    assert "storage_key" not in photo and "storage_backend" not in photo

    served = client.get(photo["url"], headers=auth_headers)
    assert served.status_code == 200
    assert served.content == PNG
    assert served.headers["content-type"].startswith("image/png")
    # A deal document is never a shared cache entry.
    assert "private" in served.headers.get("cache-control", "")
    assert served.headers.get("x-content-type-options") == "nosniff"


def test_an_anonymous_request_gets_nothing(client, auth_headers, prop, storage):
    """Knowing the id is not authorization."""
    photo = ok(upload(client, auth_headers,
                      "/wholesale/properties/%s/photos" % prop["id"]))
    assert client.get(photo["url"]).status_code in (401, 403)


# ── The gallery behaves like a gallery ──────────────────────────────────────

def test_the_first_photo_becomes_the_cover_without_being_asked(
        client, auth_headers, prop, storage):
    first = ok(upload(client, auth_headers,
                      "/wholesale/properties/%s/photos" % prop["id"]))
    second = ok(upload(client, auth_headers,
                       "/wholesale/properties/%s/photos" % prop["id"]))
    assert first["is_primary"] is True
    assert second["is_primary"] is False


def test_setting_a_new_cover_demotes_the_old_one(client, auth_headers, prop,
                                                 storage):
    first = ok(upload(client, auth_headers,
                      "/wholesale/properties/%s/photos" % prop["id"]))
    second = ok(upload(client, auth_headers,
                       "/wholesale/properties/%s/photos" % prop["id"]))
    ok(client.patch("/wholesale/files/%s" % second["id"], headers=auth_headers,
                    json={"is_primary": True}))
    photos = ok(client.get("/wholesale/properties/%s/photos" % prop["id"],
                           headers=auth_headers))["photos"]
    by_id = {p["id"]: p for p in photos}
    assert by_id[second["id"]]["is_primary"] is True
    assert by_id[first["id"]]["is_primary"] is False


def test_deleting_the_cover_promotes_the_next_one(client, auth_headers, prop,
                                                  storage):
    """A gallery with photos and no cover looks broken."""
    first = ok(upload(client, auth_headers,
                      "/wholesale/properties/%s/photos" % prop["id"]))
    second = ok(upload(client, auth_headers,
                       "/wholesale/properties/%s/photos" % prop["id"]))
    ok(client.delete("/wholesale/files/%s" % first["id"], headers=auth_headers))
    photos = ok(client.get("/wholesale/properties/%s/photos" % prop["id"],
                           headers=auth_headers))["photos"]
    assert len(photos) == 1
    assert photos[0]["id"] == second["id"]
    assert photos[0]["is_primary"] is True


def test_deleting_a_file_leaves_the_document_record_standing(
        client, auth_headers, prop, storage):
    """A person deleting a bad scan has not said the contract never existed."""
    deal_id = prop["deal"]["id"]
    result = ok(upload(client, auth_headers,
                       "/wholesale/deals/%s/documents/upload" % deal_id,
                       data=PDF, name="c.pdf", ctype="application/pdf",
                       form={"doc_type": "purchase_contract"}))
    ok(client.delete("/wholesale/files/%s" % result["file"]["id"],
                     headers=auth_headers))

    room = ok(client.get("/wholesale/deals/%s" % deal_id, headers=auth_headers))
    docs = room["documents"]
    assert len(docs) == 1
    assert docs[0]["doc_type"] == "purchase_contract"


def test_the_bytes_go_after_the_row_not_before(client, auth_headers, prop,
                                               storage):
    """An orphaned object is a cleanup job. A row pointing at nothing is a
    broken screen. So the row goes first and the object second."""
    import os
    from app.services import wholesale_files

    photo = ok(upload(client, auth_headers,
                      "/wholesale/properties/%s/photos" % prop["id"]))
    count_before = sum(len(f) for _, _, f in os.walk(wholesale_files.local_root()))
    ok(client.delete("/wholesale/files/%s" % photo["id"], headers=auth_headers))
    count_after = sum(len(f) for _, _, f in os.walk(wholesale_files.local_root()))

    assert count_after == count_before - 1
    assert client.get(photo["url"], headers=auth_headers).status_code == 404


def test_a_photo_upload_is_audited(client, auth_headers, prop, storage):
    ok(upload(client, auth_headers,
              "/wholesale/properties/%s/photos" % prop["id"]))
    events = ok(client.get("/wholesale/events", headers=auth_headers,
                           params={"property_id": prop["id"]}))["events"]
    assert any(e["action"] == "property.photo_uploaded" for e in events)


def test_the_deal_room_carries_the_photos_and_the_storage_state(
        client, auth_headers, prop, storage):
    """The header renders a photo without a second request, and knows whether
    an upload button can work before it draws one."""
    ok(upload(client, auth_headers,
              "/wholesale/properties/%s/photos" % prop["id"]))
    room = ok(client.get("/wholesale/deals/%s" % prop["deal"]["id"],
                         headers=auth_headers))
    assert len(room["photos"]) == 1
    assert room["photos"][0]["is_primary"] is True
    assert room["file_storage"]["uploads_enabled"] is True
