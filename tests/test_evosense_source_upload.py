"""TAD file upload (platform admin) and the token-only nightly feed.

TAD refuses the cloud server but serves its free file to the operator's PC.
The copy uploaded here is what the TAD adapter reads instead of calling TAD."""
import io
import zipfile

import pytest

from app.services.evosense import providers as PV
from app.services.evosense.sources import base as SB
from app.services.evosense.sources.tarrant import TAD_REQUIRED, TadReader

TOKEN = "t" * 40


def _tad_zip(rows=(("191264", "BOWLES SHAWN W", "PO BOX 1", "TONTITOWN, AR", "72770",
                    "210 SAN ANGELO ST", "026", "98500", "1949", "912", "11/30/2009"),),
             header=TAD_REQUIRED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        lines = ["|".join(header)] + ["|".join(r) for r in rows]
        z.writestr("PropertyData(Delimited)_R.txt", "\r\n".join(lines) + "\r\n")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _isolated_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOSENSE_SOURCE_UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.delenv("EVOSENSE_SRC_TAD_PATH", raising=False)
    monkeypatch.delenv("EVOSENSE_SOURCE_UPLOAD_TOKEN", raising=False)
    yield


@pytest.fixture()
def god_headers(db_session, sample_org, sample_advisor):
    from app.services.auth_service import create_access_token
    sample_advisor.role = "god_admin"
    db_session.commit()
    return {"Authorization": "Bearer %s" % create_access_token(sample_advisor, db_session),
            "X-Org-Override": sample_org.id}


def _blocked(db):
    PV.block_platform(db, "tad", "AUTH_FAILED", "AUTH_FAILED: www.tad.org refused the request (403)")
    db.commit()
    assert PV.platform_blocked(db, "tad")


def test_status_says_where_to_download(client, auth_headers):
    r = client.get("/wholesale/evosense/sources/tad/upload", headers=auth_headers)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["uploadable"] and j["download_page"] == "https://www.tad.org/resources/data-downloads"
    assert j["current"] is None and j["can_upload"] is False


def test_only_a_platform_admin_can_upload(client, auth_headers):
    r = client.post("/wholesale/evosense/sources/tad/upload", headers=auth_headers,
                    files={"file": ("tad.zip", _tad_zip(), "application/zip")})
    assert r.status_code == 403
    assert not SB.uploaded_info("tad")


def test_upload_validates_stores_and_lifts_the_block(client, db_session, god_headers):
    _blocked(db_session)
    bad = client.post("/wholesale/evosense/sources/tad/upload", headers=god_headers,
                      files={"file": ("x.zip", b"not a zip", "application/zip")})
    assert bad.status_code == 422 and "zip" in bad.json()["detail"].lower()
    wrong = client.post("/wholesale/evosense/sources/tad/upload", headers=god_headers,
                        files={"file": ("x.zip", _tad_zip(header=("Account_Num", "Owner_Name")), "application/zip")})
    assert wrong.status_code == 422 and "missing columns" in wrong.json()["detail"]
    assert PV.platform_blocked(db_session, "tad") and not SB.uploaded_info("tad")
    ok = client.post("/wholesale/evosense/sources/tad/upload", headers=god_headers,
                     files={"file": ("PropertyData(Delimited)_R.ZIP", _tad_zip(), "application/zip")})
    assert ok.status_code == 200, ok.text
    assert ok.json()["ok"] and ok.json()["columns"] == len(TAD_REQUIRED)
    db_session.expire_all()
    assert not PV.platform_blocked(db_session, "tad")
    assert SB.local_override("tad") == SB.uploaded_path("tad")


def test_adapter_reads_the_uploaded_copy_without_calling_tad(db_session, monkeypatch, tmp_path):
    def no_network(*a, **k):
        raise AssertionError("TAD must not be called when a copy is on file")
    monkeypatch.setattr(SB, "_request", no_network)
    tmp = tmp_path / "in.zip"
    tmp.write_bytes(_tad_zip())
    PV.accept_upload(db_session, "tad", str(tmp), source_date="Sat, 10 Oct 2026 07:52:11 GMT", by="t")
    res = TadReader().lookup_many(["191264"])
    rec = res["records"]["191264"]
    assert rec["square_feet"] == 912 and rec["year_built"] == 1949
    assert rec["owner"]["name"] == "BOWLES SHAWN W" and rec["deed_transfer_date"] == "2009-11-30"


def test_feed_refuses_without_the_setting_or_with_a_wrong_token(client, monkeypatch):
    r = client.post("/source-feeds/tad", content=_tad_zip(), headers={"X-Source-Token": TOKEN})
    assert r.status_code == 503
    monkeypatch.setenv("EVOSENSE_SOURCE_UPLOAD_TOKEN", TOKEN)
    r = client.post("/source-feeds/tad", content=_tad_zip(), headers={"X-Source-Token": "wrong"})
    assert r.status_code == 401
    r = client.post("/source-feeds/dcad", content=_tad_zip(), headers={"X-Source-Token": TOKEN})
    assert r.status_code == 404
    assert not SB.uploaded_info("tad")


def test_feed_with_the_token_replaces_the_copy(client, db_session, monkeypatch):
    monkeypatch.setenv("EVOSENSE_SOURCE_UPLOAD_TOKEN", TOKEN)
    _blocked(db_session)
    r = client.post("/source-feeds/tad", content=_tad_zip(),
                    headers={"X-Source-Token": TOKEN, "X-Source-Date": "Sat, 10 Oct 2026 07:52:11 GMT",
                             "Content-Type": "application/zip"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] and j["source_date"] == "Sat, 10 Oct 2026 07:52:11 GMT"
    assert SB.uploaded_info("tad")["via"] == "feed"
    db_session.expire_all()
    assert not PV.platform_blocked(db_session, "tad")
    bad = client.post("/source-feeds/tad", content=b"junk", headers={"X-Source-Token": TOKEN})
    assert bad.status_code == 422
    assert SB.uploaded_info("tad")["via"] == "feed"          # the good copy is untouched


def test_uploader_email_is_hidden_from_tenants_but_not_platform_admins(
        client, db_session, sample_org, god_headers):
    # god_headers promotes sample_advisor, so the tenant caller is a second user.
    from app.models.models import User
    from app.services.auth_service import create_access_token, hash_password
    tenant_user = User(organization_id=sample_org.id, email="tenant-admin@example.com",
                       password_hash=hash_password("TestPass123!"), full_name="Tenant Admin",
                       role="org_admin", must_change_password=False)
    db_session.add(tenant_user)
    db_session.commit()
    auth_headers = {"Authorization": "Bearer %s" % create_access_token(tenant_user, db_session)}
    up =client.post("/wholesale/evosense/sources/tad/upload", headers=god_headers,
                     files={"file": ("PropertyData(Delimited)_R.ZIP", _tad_zip(), "application/zip")})
    assert up.status_code == 200, up.text
    tenant = client.get("/wholesale/evosense/sources/tad/upload", headers=auth_headers).json()
    assert tenant["current"] and "by" not in tenant["current"]
    assert "@" not in str(tenant)
    god = client.get("/wholesale/evosense/sources/tad/upload", headers=god_headers).json()
    assert "@" in (god["current"].get("by") or "")
