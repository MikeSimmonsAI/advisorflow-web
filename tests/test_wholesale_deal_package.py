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


def _photo(db_session, deal_id, name="front.jpg"):
    """Mark one investor-visible photo directly (the upload path is tested elsewhere)."""
    from app.models.wholesale_models import WholesaleFile
    d = db_session.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id).first()
    f = WholesaleFile(organization_id=d.organization_id, property_id=d.property_id, deal_id=d.id,
                      kind="property_photo", original_filename=name, content_type="image/jpeg",
                      storage_backend="db", byte_size=10, storage_key="test/%s" % name, buyer_visible=True,
                      seller_visible=False, is_primary=True, sort_order=0)
    db_session.add(f)
    db_session.commit()
    return f.id


def _buyer_link(client, H, deal_id, name="Package Test Buyer"):
    buyer = ok(client.post("/wholesale/buyers", headers=H, json={
        "company_name": name, "email": "%s@example.test" % name.split()[0].lower(), "is_test": True}))
    return ok(client.post("/wholesale/deals/%s/share-links" % deal_id, headers=H,
                          json={"audience": "buyer", "buyer_id": buyer["id"], "recipient_name": "Test Investor"}))["token"]


def _review(client, H, deal_id):
    return ok(client.post("/wholesale/deals/%s/package/submit" % deal_id, headers=H))


def _approve(client, A, st):
    ok(client.post("/wholesale/approvals/%s/decide" % st["approval"]["id"], headers=A, json={"approve": True}))


def test_package_lifecycle_requires_approval_of_exact_content(client, auth_headers, admin_auth_headers, db_session):
    H, A = auth_headers, admin_auth_headers
    deal_id = _deal(client, H, db_session)
    assert ok(client.get("/wholesale/deals/%s/package" % deal_id, headers=H))["status"] == "none"
    st = ok(client.post("/wholesale/deals/%s/package" % deal_id, headers=H))
    assert st["status"] == "draft" and st["version"] == 1 and not st["missing"]
    assert set(st["recommended"]) >= {"Asking price for investors", "Summary for investors"}
    assert client.post("/wholesale/deals/%s/package/publish" % deal_id, headers=A).status_code == 409  # a draft

    ok(client.patch("/wholesale/deals/%s/publication" % deal_id, headers=H, json={
        "buyer_room_summary": "Vacant 3/2 needing a roof.", "buyer_room_asking_price": 129000,
        "buyer_room_show_arv": True}))
    _photo(db_session, deal_id)
    st = _review(client, H, deal_id)
    assert st["status"] == "review" and st["approval"]["status"] == "pending"
    pending = ok(client.get("/wholesale/approvals", headers=H, params={"status": "pending"}))
    assert [a for a in pending["approvals"] if a.get("deal_id") == deal_id and a["kind"] == "package"]
    # an advisor cannot approve investor content or publish it; an admin can
    r = client.post("/wholesale/approvals/%s/decide" % st["approval"]["id"], headers=H, json={"approve": True})
    assert r.status_code == 403 and "admin" in r.json()["detail"]
    _approve(client, A, st)
    assert ok(client.get("/wholesale/deals/%s/package" % deal_id, headers=H))["status"] == "approved"
    assert client.post("/wholesale/deals/%s/package/publish" % deal_id, headers=H).status_code == 403

    # an edit after approval sends it back - the approved content is what gets published
    ok(client.patch("/wholesale/deals/%s/publication" % deal_id, headers=H, json={"buyer_room_asking_price": 139000}))
    st = ok(client.get("/wholesale/deals/%s/package" % deal_id, headers=H))
    assert st["status"] == "draft" and st["changed_since_approval"]
    assert client.post("/wholesale/deals/%s/package/publish" % deal_id, headers=A).status_code == 409
    st = _review(client, H, deal_id)
    _approve(client, A, st)
    st = ok(client.post("/wholesale/deals/%s/package/publish" % deal_id, headers=A))
    assert st["status"] == "published" and st["live"]

    token = _buyer_link(client, H, deal_id)
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

    # withdraw: page, PDF and actions stop resolving through every stale link
    st = ok(client.post("/wholesale/deals/%s/package/withdraw" % deal_id, headers=H, json={"reason": "seller paused"}))
    assert st["status"] == "withdrawn"
    for path in ("", "/package.pdf"):
        assert client.get("/wholesale-rooms/buyer/%s%s" % (token, path)).status_code == 404
    assert client.post("/wholesale-rooms/buyer/%s/action" % token, json={"action": "interested"}).status_code == 404


def test_content_changed_after_publishing_goes_dark_until_reapproved(client, auth_headers, admin_auth_headers,
                                                                     db_session):
    """No route can materially change a live investor page without a new approval:
    the edit is saved, investors see a dead link, the operator sees why."""
    from tests.wholesale_publish_helpers import approve_and_publish
    H, A = auth_headers, admin_auth_headers
    deal_id = _deal(client, H, db_session, street="80 Package Way")
    ok(client.patch("/wholesale/deals/%s/publication" % deal_id, headers=H, json={
        "buyer_room_summary": "Original text.", "buyer_room_asking_price": 120000}))
    photo = _photo(db_session, deal_id)
    ok(client.post("/wholesale/deals/%s/package" % deal_id, headers=H))
    approve_and_publish(client, H, A, deal_id)
    token = _buyer_link(client, H, deal_id, "Dark Test Buyer")
    assert ok(client.get("/wholesale-rooms/buyer/%s" % token))["summary"] == "Original text."

    # 1) text edit through the old Sharing settings
    ok(client.patch("/wholesale/deals/%s/publication" % deal_id, headers=H, json={"buyer_room_summary": "Edited!"}))
    assert client.get("/wholesale-rooms/buyer/%s" % token).status_code == 404
    st = ok(client.get("/wholesale/deals/%s/package" % deal_id, headers=H))
    assert st["status"] == "changed" and not st["live"] and "changed after it was approved" in st["notes"][0]
    approve_and_publish(client, H, A, deal_id)            # re-approval brings the SAME link back
    assert ok(client.get("/wholesale-rooms/buyer/%s" % token))["summary"] == "Edited!"

    # 2) hiding a photo is a material change too
    ok(client.patch("/wholesale/files/%s" % photo, headers=H, json={"buyer_visible": False}))
    assert client.get("/wholesale-rooms/buyer/%s" % token).status_code == 404

    # 3) a closing date is not marketing content: it never takes a live page down
    approve_and_publish(client, H, A, deal_id)
    ok(client.patch("/wholesale/deals/%s/contract" % deal_id, headers=H, json={"close_of_escrow_target": "2026-12-01"}))
    assert client.get("/wholesale-rooms/buyer/%s" % token).status_code == 200


def test_the_old_sharing_toggle_cannot_publish_unapproved_content(client, auth_headers, admin_auth_headers,
                                                                  db_session):
    H, A = auth_headers, admin_auth_headers
    deal_id = _deal(client, H, db_session, street="79 Package Way")
    url = "/wholesale/deals/%s/publication/state" % deal_id
    assert client.post(url, headers=H, json={"audience": "buyer", "published": True}).status_code == 403
    assert client.post(url, headers=A, json={"audience": "buyer", "published": True}).status_code == 409
    ok(client.post("/wholesale/deals/%s/package" % deal_id, headers=H))
    assert client.post(url, headers=A, json={"audience": "buyer", "published": True}).status_code == 409
    d = db_session.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id).first()
    db_session.refresh(d)
    assert d.buyer_room_published is False
    # even a row flipped on behind the API's back is not served
    d.buyer_room_published = True
    db_session.commit()
    token = _buyer_link(client, H, deal_id, "Bypass Test Buyer")
    assert client.get("/wholesale-rooms/buyer/%s" % token).status_code == 404
    assert ok(client.get("/wholesale/deals/%s/package" % deal_id, headers=H))["status"] == "changed"
    # taking a page down is always allowed, for anyone on the deal
    ok(client.post(url, headers=H, json={"audience": "buyer", "published": False}))
    # every refusal is in the audit trail
    from app.models.wholesale_models import WholesaleEvent
    refused = db_session.query(WholesaleEvent).filter(WholesaleEvent.deal_id == deal_id,
                                                      WholesaleEvent.action == "publication.refused").count()
    assert refused >= 3


def test_operator_pdf_is_watermarked_until_approved_and_content_is_whitelisted(client, auth_headers, db_session):
    H = auth_headers
    deal_id = _deal(client, H, db_session, street="78 Package Way")
    ok(client.post("/wholesale/deals/%s/package" % deal_id, headers=H))
    r = client.get("/wholesale/deals/%s/package.pdf" % deal_id, headers=H)
    assert r.status_code == 200 and "DRAFT - NOT APPROVED" in pdf_text(r.content)
    content = ok(client.get("/wholesale/deals/%s/package" % deal_id, headers=H))["content"]
    assert pub.leaked_keys(content, pub.FORBIDDEN_TO_BUYER) == []
    assert "Secret Seller" not in str(content)


def test_the_deal_sheet_never_shows_what_we_pay_the_seller(client, auth_headers, db_session):
    """Found in the publishing review: with no asking price set, the deal sheet
    fell back to the CONTRACT price. It now falls back to nothing, and ARV and
    repairs follow the same investor switches as the investor page."""
    H = auth_headers
    deal_id = _deal(client, H, db_session, street="81 Package Way")
    b = ok(client.post("/wholesale/buyers", headers=H, json={"company_name": "Sheet Co", "email": "sh@example.test",
                                                             "is_test": True}))
    pv = ok(client.post("/wholesale/deals/%s/disposition/preview" % deal_id, headers=H,
                        json={"buyer_ids": [b["id"]]}))
    text = str(pv)
    assert "98,765" not in text and "98765" not in text
    assert "ARV: $" not in pv["body"] and "Estimated repairs: $" not in pv["body"]
    ok(client.patch("/wholesale/deals/%s/publication" % deal_id, headers=H,
                    json={"buyer_room_asking_price": 125000, "buyer_room_show_arv": True}))
    pv = ok(client.post("/wholesale/deals/%s/disposition/preview" % deal_id, headers=H,
                        json={"buyer_ids": [b["id"]]}))
    assert "Asking: $125,000" in pv["body"] and "ARV: $210,000" in pv["body"]
