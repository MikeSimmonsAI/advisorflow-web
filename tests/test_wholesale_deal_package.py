"""Investor deal package: nothing is published without an approval of the exact content,
the PDF carries only the investor whitelist, and the existing room/links keep working."""
import base64
import re
import zlib

from app.services import wholesale_publication as pub
from app.models.wholesale_models import WholesaleDeal

ES = "/wholesale/evosense"


def ok(r, what=""):
    assert r.status_code in (200, 201), "%s -> %s %s" % (what or r.url, r.status_code, r.text[:400])
    return r.json()


def pdf_text(data: bytes) -> str:
    assert data.startswith(b"%PDF")
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        raw = m.group(1).strip()
        if raw.endswith(b"~>"):                     # ReportLab: ASCII85 then Flate
            try:
                raw = base64.a85decode(raw[:-2])
            except ValueError:
                continue
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        out.append(raw.decode("latin-1", "ignore"))
    return "\n".join(out)


def _deal(client, H, db_session, street="77 Package Way"):
    pid = ok(client.post(ES + "/properties", headers=H, json={
        "street_address": street, "city": "Dallas", "state": "TX", "zip_code": "75216", "county": "Dallas",
        "property_type": "single_family", "square_feet": 1400, "is_test": True}))["property_id"]
    ok(client.post(ES + "/properties/%s/owner" % pid, headers=H, json={"name": "Secret Seller Name"}))
    deal_id = ok(client.post(ES + "/properties/%s/promote" % pid, headers=H, json={}))["deal_id"]
    ok(client.patch("/wholesale/deals/%s/analysis" % deal_id, headers=H, json={"arv": 210000, "repair_estimate": 30000}))
    ok(client.patch("/wholesale/deals/%s/contract" % deal_id, headers=H, json={"contract_price": 98765}))
    return deal_id


def _photo(db_session, deal_id):
    """Mark one investor-visible photo directly (the upload path is tested elsewhere)."""
    from app.models.wholesale_models import WholesaleFile
    d = db_session.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id).first()
    f = WholesaleFile(organization_id=d.organization_id, property_id=d.property_id, deal_id=d.id,
                      kind="property_photo", original_filename="front.jpg", content_type="image/jpeg",
                      storage_backend="db", byte_size=10, storage_key="test/front.jpg", buyer_visible=True,
                      seller_visible=False, is_primary=True, sort_order=0)
    db_session.add(f)
    db_session.commit()


def test_package_lifecycle_requires_approval_of_exact_content(client, auth_headers, db_session):
    H = auth_headers
    deal_id = _deal(client, H, db_session)
    st = ok(client.get("/wholesale/deals/%s/package" % deal_id, headers=H))
    assert st["status"] == "none"
    st = ok(client.post("/wholesale/deals/%s/package" % deal_id, headers=H))
    assert st["status"] == "draft" and st["version"] == 1 and st["missing"]
    # cannot review an incomplete package, cannot publish a draft
    assert client.post("/wholesale/deals/%s/package/submit" % deal_id, headers=H).status_code == 409
    assert client.post("/wholesale/deals/%s/package/publish" % deal_id, headers=H).status_code == 409

    ok(client.patch("/wholesale/deals/%s/publication" % deal_id, headers=H, json={
        "buyer_room_summary": "Vacant 3/2 needing a roof.", "buyer_room_asking_price": 129000,
        "buyer_room_show_arv": True}))
    _photo(db_session, deal_id)
    st = ok(client.post("/wholesale/deals/%s/package/submit" % deal_id, headers=H))
    assert st["status"] == "review" and st["approval"]["status"] == "pending"
    assert client.post("/wholesale/deals/%s/package/publish" % deal_id, headers=H).status_code == 409

    # the request is an ordinary Approval Center item
    pending = ok(client.get("/wholesale/approvals", headers=H, params={"status": "pending"}))
    item = [a for a in pending["approvals"] if a.get("deal_id") == deal_id and a["kind"] == "package"]
    assert item
    ok(client.post("/wholesale/approvals/%s/decide" % st["approval"]["id"], headers=H, json={"approve": True}))
    assert ok(client.get("/wholesale/deals/%s/package" % deal_id, headers=H))["status"] == "approved"

    # an edit after approval sends it back - the approved content is what gets published
    ok(client.patch("/wholesale/deals/%s/publication" % deal_id, headers=H, json={"buyer_room_asking_price": 139000}))
    st = ok(client.get("/wholesale/deals/%s/package" % deal_id, headers=H))
    assert st["status"] == "draft" and st["changed_since_approval"]
    assert client.post("/wholesale/deals/%s/package/publish" % deal_id, headers=H).status_code == 409
    st = ok(client.post("/wholesale/deals/%s/package/submit" % deal_id, headers=H))
    ok(client.post("/wholesale/approvals/%s/decide" % st["approval"]["id"], headers=H, json={"approve": True}))
    st = ok(client.post("/wholesale/deals/%s/package/publish" % deal_id, headers=H))
    assert st["status"] == "published"

    # the EXISTING share link + investor page serve it; the PDF too, and the download is recorded
    buyer = ok(client.post("/wholesale/buyers", headers=H, json={
        "company_name": "Package Test Buyer", "email": "pkg@example.test", "is_test": True}))
    link = ok(client.post("/wholesale/deals/%s/share-links" % deal_id, headers=H,
                          json={"audience": "buyer", "buyer_id": buyer["id"], "recipient_name": "Test Investor"}))
    token = link["token"]
    room = ok(client.get("/wholesale-rooms/buyer/%s" % token))
    assert room["asking_price"] == 139000
    r = client.get("/wholesale-rooms/buyer/%s/package.pdf" % token)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    text = pdf_text(r.content)
    assert "77 Package Way" in text and "139,000" in text
    for secret in ("Secret Seller", "98,765", "98765"):
        assert secret not in text, secret
    acts = ok(client.get("/wholesale/deals/%s/share-activity" % deal_id, headers=H))["activity"]
    assert any(a["action"] == "pdf_downloaded" for a in acts)

    # withdraw: the page and the PDF stop resolving, like an unpublished room always has
    st = ok(client.post("/wholesale/deals/%s/package/withdraw" % deal_id, headers=H, json={"reason": "seller paused"}))
    assert st["status"] == "withdrawn"
    assert client.get("/wholesale-rooms/buyer/%s" % token).status_code == 404
    assert client.get("/wholesale-rooms/buyer/%s/package.pdf" % token).status_code == 404


def test_operator_pdf_is_watermarked_until_approved_and_content_is_whitelisted(client, auth_headers, db_session):
    H = auth_headers
    deal_id = _deal(client, H, db_session, street="78 Package Way")
    ok(client.post("/wholesale/deals/%s/package" % deal_id, headers=H))
    r = client.get("/wholesale/deals/%s/package.pdf" % deal_id, headers=H)
    assert r.status_code == 200 and "DRAFT - NOT APPROVED" in pdf_text(r.content)
    content = ok(client.get("/wholesale/deals/%s/package" % deal_id, headers=H))["content"]
    assert pub.leaked_keys(content, pub.FORBIDDEN_TO_BUYER) == []
    assert "Secret Seller" not in str(content)


def test_legacy_publish_toggle_still_works_and_is_reported_honestly(client, auth_headers, db_session):
    H = auth_headers
    deal_id = _deal(client, H, db_session, street="79 Package Way")
    ok(client.post("/wholesale/deals/%s/package" % deal_id, headers=H))
    ok(client.post("/wholesale/deals/%s/publication/state" % deal_id, headers=H,
                   json={"audience": "buyer", "published": True}))
    st = ok(client.get("/wholesale/deals/%s/package" % deal_id, headers=H))
    assert st["status"] == "published" and any("directly" in n for n in st["notes"])
