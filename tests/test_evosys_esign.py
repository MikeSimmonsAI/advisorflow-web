"""EvoSys e-signature, end to end through the real routes.

Email is captured (the provider is never called). What must hold:
  * the link is private and single-purpose: hashed at rest, replaced on
    resend, dead after void / decline / expiry;
  * nobody signs without the emailed code, the consent box and their turn;
  * after the last signature the PDF is sealed (fingerprinted), emailed to
    every party with the PDF attached, and the deal's document is SIGNED;
  * every step is in the audit trail with an IP.
"""
import base64
import hashlib
import io
import re
from datetime import datetime, timedelta

import pytest

from app.models.esign_models import EsignEnvelope, EsignEvent, EsignSigner
from app.services import email_service

FULL = {"price": "95000", "earnest_money": "1000", "option_days": "10", "title_company": "Republic Title",
        "closing_date": "2026-11-20", "seller_email": "seller@example.com", "seller_name": "Elizabeth Bryant"}


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:400])
    return r.json()


@pytest.fixture
def mail(monkeypatch):
    sent = []

    def fake(to_email, subject, body_html, attachments=None, org=None, message_type=None, **kw):
        sent.append({"to": to_email, "subject": subject, "html": body_html, "attachments": attachments or [],
                     "type": message_type})
        return {"success": True, "provider_message_id": "m%d" % len(sent), "error": None}

    monkeypatch.setattr(email_service, "send_email_via_provider", fake)
    return sent


def link_token(msg):
    m = re.search(r"/sign/([A-Za-z0-9_-]{20,})", msg["html"])
    assert m, msg["html"][:300]
    return m.group(1)


def last_code(mail):
    m = re.search(r"(\d{6})", [x for x in mail if x["type"] == "esign_code"][-1]["subject"])
    return m.group(1)


def png():
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (300, 100), (255, 255, 255, 0))
    ImageDraw.Draw(im).line([(10, 80), (100, 20), (200, 80), (290, 20)], fill=(0, 0, 0, 255), width=4)
    b = io.BytesIO()
    im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


@pytest.fixture
def deal(client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "2620 Kirby St", "city": "Dallas", "state": "TX",
                                "county": "Dallas", "zip_code": "75203", "owner_name": "Elizabeth Bryant"}))
    return prop["deal"]["id"]


@pytest.fixture
def sent(client, auth_headers, deal, mail):
    r = ok(client.post("/wholesale/deals/%s/contracts/purchase_agreement/send" % deal,
                       headers=auth_headers, json={"values": FULL, "message": "Here is the contract we discussed."}))
    assert r["sent"] is True and r["provider"] == "evosys" and r["delivered"] is True
    return r


def sign_as(client, mail, token, *, drawn=False, name="Elizabeth Bryant"):
    ok(client.post("/esign/sign/%s/code" % token, json={}))
    ok(client.post("/esign/sign/%s/verify" % token, json={"code": last_code(mail)}))
    return ok(client.post("/esign/sign/%s/sign" % token, json={"consent": True, "typed_name": name,
                                                              "drawn_png": png() if drawn else None}))


def test_full_journey_seals_the_pdf_and_signs_the_deal(client, auth_headers, deal, mail, sent, db_session):
    first = mail[0]
    assert first["to"] == "seller@example.com" and first["subject"].startswith("Please sign")
    assert "Here is the contract we discussed." in first["html"]
    tok = link_token(first)

    # the token is never stored in the clear
    assert db_session.query(EsignSigner).filter(EsignSigner.token_hash == tok).count() == 0
    assert db_session.query(EsignSigner).filter(
        EsignSigner.token_hash == hashlib.sha256(tok.encode()).hexdigest()).count() == 1

    v = ok(client.get("/esign/sign/%s" % tok))
    assert v["can_sign"] and v["signer"]["role"] == "Seller" and "2620 Kirby St" in v["document_html"]
    assert v["signer"]["email_masked"].startswith("se***@")
    assert [p["role"] for p in v["parties"]] == ["Seller", "Buyer"]

    # no signing before the emailed code
    r = client.post("/esign/sign/%s/sign" % tok, json={"consent": True, "typed_name": "Elizabeth Bryant"})
    assert r.status_code == 403
    ok(client.post("/esign/sign/%s/code" % tok, json={}))
    assert client.post("/esign/sign/%s/verify" % tok, json={"code": "000000" if last_code(mail) != "000000"
                                                            else "111111"}).status_code == 400
    ok(client.post("/esign/sign/%s/verify" % tok, json={"code": last_code(mail)}))
    # no signing without consent
    assert client.post("/esign/sign/%s/sign" % tok, json={"consent": False,
                                                          "typed_name": "Elizabeth Bryant"}).status_code == 400
    out = ok(client.post("/esign/sign/%s/sign" % tok, json={"consent": True, "typed_name": "Elizabeth Bryant",
                                                            "drawn_png": png()}))
    assert out == {"signed": True, "completed": False, "next": "Buyer"}

    buyer_mail = [m for m in mail if m["type"] == "esign_link"][-1]
    assert buyer_mail["to"] != "seller@example.com"
    btok = link_token(buyer_mail)
    done = sign_as(client, mail, btok, name="Test Buyer")
    assert done["completed"] is True

    completed = [m for m in mail if m["type"] == "esign_completed"]
    assert {m["to"] for m in completed} >= {"seller@example.com", buyer_mail["to"]}
    att = completed[0]["attachments"][0]
    pdf = base64.b64decode(att["content"])
    assert pdf.startswith(b"%PDF") and att["content_type"] == "application/pdf"

    env = db_session.query(EsignEnvelope).filter(EsignEnvelope.id == sent["external_ref"]).first()
    db_session.refresh(env)
    assert env.status == "completed" and env.final_sha256 == hashlib.sha256(pdf).hexdigest()
    actions = [e.action for e in db_session.query(EsignEvent).filter(EsignEvent.envelope_id == env.id)
               .order_by(EsignEvent.at).all()]
    for a in ("sent", "link_sent", "viewed", "code_sent", "code_failed", "verified", "consented", "signed",
              "completed"):
        assert a in actions, a

    # the sender sees it signed and can download the sealed PDF
    st = ok(client.get("/wholesale/documents/%s/esign" % sent["document_id"], headers=auth_headers))
    assert st["status"] == "completed" and all(s["status"] == "signed" for s in st["signers"])
    r = client.get("/wholesale/documents/%s/signed.pdf" % sent["document_id"], headers=auth_headers)
    assert r.status_code == 200 and r.content == pdf and r.headers["X-Document-SHA256"] == env.final_sha256
    d = ok(client.get("/wholesale/deals/%s" % deal, headers=auth_headers))
    assert (d.get("deal") or d)["contract_status"] == "signed"
    docs = d.get("documents") or []
    doc = next(x for x in docs if x["id"] == sent["document_id"])
    assert (doc.get("status_key") or doc.get("status")) == "signed"

    # the signer's own link downloads their copy and can no longer sign
    assert client.get("/esign/sign/%s/signed.pdf" % tok).content == pdf
    assert client.post("/esign/sign/%s/code" % tok, json={}).status_code == 409


def test_resend_replaces_the_link(client, auth_headers, mail, sent, db_session):
    old = link_token(mail[0])
    env = db_session.query(EsignEnvelope).filter(EsignEnvelope.id == sent["external_ref"]).first()
    s = db_session.query(EsignSigner).filter(EsignSigner.envelope_id == env.id).order_by(EsignSigner.sign_order).first()
    s.link_sent_at = datetime.utcnow() - timedelta(minutes=10)
    db_session.commit()
    ok(client.post("/wholesale/documents/%s/esign/remind" % sent["document_id"], headers=auth_headers, json={}))
    new = link_token([m for m in mail if m["type"] == "esign_link"][-1])
    assert new != old
    assert client.get("/esign/sign/%s" % old).status_code == 404
    assert ok(client.get("/esign/sign/%s" % new))["can_sign"]


def test_void_kills_the_link(client, auth_headers, mail, sent):
    tok = link_token(mail[0])
    client.get("/esign/sign/%s" % tok)
    ok(client.post("/wholesale/documents/%s/esign/void" % sent["document_id"], headers=auth_headers,
                   json={"reason": "price changed"}))
    assert client.post("/esign/sign/%s/code" % tok, json={}).status_code == 410
    assert ok(client.get("/esign/sign/%s" % tok))["status"] == "voided"
    assert any(m["type"] == "esign_voided" and m["to"] == "seller@example.com" for m in mail)


def test_decline_tells_the_sender_and_declines_the_document(client, auth_headers, mail, sent, deal):
    tok = link_token(mail[0])
    ok(client.post("/esign/sign/%s/decline" % tok, json={"reason": "Need a higher price"}))
    assert any(m["type"] == "esign_declined" for m in mail)
    st = ok(client.get("/wholesale/documents/%s/esign" % sent["document_id"], headers=auth_headers))
    assert st["status"] == "declined" and st["signers"][0]["decline_reason"] == "Need a higher price"
    assert client.post("/esign/sign/%s/code" % tok, json={}).status_code == 410


def test_expired_links_stop_working(client, mail, sent, db_session):
    tok = link_token(mail[0])
    env = db_session.query(EsignEnvelope).filter(EsignEnvelope.id == sent["external_ref"]).first()
    env.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db_session.commit()
    assert ok(client.get("/esign/sign/%s" % tok))["status"] == "expired"
    assert client.post("/esign/sign/%s/code" % tok, json={}).status_code == 410


def test_codes_are_rate_limited_and_garbage_links_are_404(client, mail, sent):
    tok = link_token(mail[0])
    ok(client.post("/esign/sign/%s/code" % tok, json={}))
    assert client.post("/esign/sign/%s/code" % tok, json={}).status_code == 429
    assert client.get("/esign/sign/not-a-real-token-at-all-xxxxxxxx").status_code == 404


def test_the_second_signer_cannot_jump_the_queue(client, mail, sent, db_session):
    # signer 2 has no link until signer 1 signs
    env = db_session.query(EsignEnvelope).filter(EsignEnvelope.id == sent["external_ref"]).first()
    s2 = db_session.query(EsignSigner).filter(EsignSigner.envelope_id == env.id, EsignSigner.sign_order == 2).first()
    assert s2.token_hash is None and s2.status == "waiting"
    assert len([m for m in mail if m["type"] == "esign_link"]) == 1


def test_bad_drawing_is_refused(client, mail, sent):
    tok = link_token(mail[0])
    ok(client.post("/esign/sign/%s/code" % tok, json={}))
    ok(client.post("/esign/sign/%s/verify" % tok, json={"code": last_code(mail)}))
    r = client.post("/esign/sign/%s/sign" % tok, json={"consent": True, "typed_name": "Elizabeth Bryant",
                                                       "drawn_png": "data:image/png;base64,bm90IGEgcG5n"})
    assert r.status_code == 400


def test_change_email_of_waiting_signer_keeps_the_signature_already_given(client, auth_headers, mail, sent, db_session):
    # seller signs, buyer's link goes out
    sign_as(client, mail, link_token(mail[0]))
    old_buyer = link_token([m for m in mail if m["type"] == "esign_link"][-1])
    r = ok(client.post("/wholesale/documents/%s/esign/signer-email" % sent["document_id"], headers=auth_headers,
                       json={"role": "Buyer", "email": "info@example.com"}))
    assert r["changed"] and r["link_sent"] is True
    newest = [m for m in mail if m["type"] == "esign_link"][-1]
    assert newest["to"] == "info@example.com"
    assert client.get("/esign/sign/%s" % old_buyer).status_code == 404          # old link dead
    v = ok(client.get("/esign/sign/%s" % link_token(newest)))
    assert v["signer"]["role"] == "Buyer" and v["signer"]["verified"] is False
    assert [p["status"] for p in v["parties"]][0] == "signed"                  # seller's signature kept
    done = sign_as(client, mail, link_token(newest), name="Test Buyer")
    assert done["completed"] is True
    assert any(m["type"] == "esign_completed" and m["to"] == "info@example.com" for m in mail)


def test_cannot_change_email_of_someone_who_signed(client, auth_headers, mail, sent):
    sign_as(client, mail, link_token(mail[0]))
    r = client.post("/wholesale/documents/%s/esign/signer-email" % sent["document_id"], headers=auth_headers,
                    json={"role": "Seller", "email": "x@example.com"})
    assert r.status_code == 409
    r = client.post("/wholesale/documents/%s/esign/signer-email" % sent["document_id"], headers=auth_headers,
                    json={"role": "Buyer", "email": "not-an-email"})
    assert r.status_code == 400
