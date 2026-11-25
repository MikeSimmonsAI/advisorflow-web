"""Owner-sent agreements: fill-in blanks, signing order, CC copy, sealed PDF.

The VOLT ACA agreement is the case this was built for: the agency signs first
and types its legal company name and the signer's title into marked blanks;
VOLT's owner signs last; a CC address gets only the sealed copy.
"""
import io
import re

import pytest

from app.models.esign_models import EsignEnvelope, EsignEvent
from app.models.models import User
from app.services import email_service
from app.services.auth_service import create_access_token, hash_password

DOC = """<!doctype html><html><head><title>t</title></head><body>
<h1>Test Trial Agreement</h1>
<p>This agreement is between Example Holdings LLC and <span class="blank" data-field="agency_legal_name">________</span>,
doing business as Example Media.</p>
<p>Printed title: <span class="blank" data-field="agency_title">________</span></p>
<p>Section 1. Nothing else here is editable.</p>
</body></html>"""


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


@pytest.fixture
def god(db_session):
    u = User(organization_id=None, email="owner-esign@evosyspro.test", password_hash=hash_password("GodPass123!"),
             full_name="Owner", role="god_admin", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    return {"Authorization": "Bearer " + create_access_token(u, db_session)}


def body(**kw):
    b = {"title": "Test Trial Agreement", "document_html": DOC, "sender_name": "Example Holdings",
         "cc": ["copy@example.test"],
         "signers": [{"role": "Agency", "name": "Alex Agency", "email": "alex@example.test",
                      "fields": [{"key": "agency_legal_name", "label": "Agency legal company name"},
                                 {"key": "agency_title", "label": "Your title"}]},
                     {"role": "Owner", "name": "Olivia Owner", "email": "owner@example.test"}]}
    b.update(kw)
    return b


def token_for(mail, to):
    m = [x for x in mail if x["to"] == to and x["type"] == "esign_link"][-1]
    return re.search(r"/sign/([A-Za-z0-9_-]{20,})", m["html"]).group(1)


def verify(client, mail, tok, to):
    ok(client.post("/esign/sign/%s/code" % tok, json={}))
    code = re.search(r"(\d{6})", [x for x in mail if x["type"] == "esign_code" and x["to"] == to][-1]["subject"]).group(1)
    ok(client.post("/esign/sign/%s/verify" % tok, json={"code": code}))


def test_only_the_owner_can_send(client, auth_headers, sample_org, mail):
    r = client.post("/esign/owner/orgs/%s/envelopes" % sample_org.id, headers=auth_headers, json=body())
    assert r.status_code in (401, 403)
    assert mail == []


def test_a_blank_must_exist_in_the_document(client, god, sample_org, mail):
    b = body()
    b["signers"][0]["fields"].append({"key": "not_in_doc", "label": "Missing"})
    r = client.post("/esign/owner/orgs/%s/envelopes" % sample_org.id, headers=god, json=b)
    assert r.status_code == 400 and "not_in_doc" in r.text
    assert mail == []


def test_full_flow_fills_blanks_orders_signers_and_copies_cc(client, god, sample_org, mail, db_session):
    out = ok(client.post("/esign/owner/orgs/%s/envelopes" % sample_org.id, headers=god, json=body()))
    assert out["cc"] == ["copy@example.test"] and out["waiting_on"] == "Agency"
    # Only the first signer is emailed; the CC address gets nothing yet.
    assert {m["to"] for m in mail} == {"alex@example.test"}
    assert "Example Holdings has sent you" in mail[0]["html"]

    a = token_for(mail, "alex@example.test")
    view = ok(client.get("/esign/sign/" + a))
    assert [f["key"] for f in view["fields"]] == ["agency_legal_name", "agency_title"]
    verify(client, mail, a, "alex@example.test")
    # Blanks are required.
    r = client.post("/esign/sign/%s/sign" % a, json={"consent": True, "typed_name": "Alex Agency",
                                                      "fields": {"agency_legal_name": "Example Media LLC"}})
    assert r.status_code == 400 and "Your title" in r.text
    ok(client.post("/esign/sign/%s/sign" % a, json={
        "consent": True, "typed_name": "Alex Agency",
        "fields": {"agency_legal_name": "Example <b>Media</b> LLC", "agency_title": "Account Director"}}))

    # The owner now sees the agency's answers in the document (escaped), and fills nothing.
    o = token_for(mail, "owner@example.test")
    v2 = ok(client.get("/esign/sign/" + o))
    assert v2["fields"] == []
    assert "Example &lt;b&gt;Media&lt;/b&gt; LLC" in v2["document_html"] and "Account Director" in v2["document_html"]
    verify(client, mail, o, "owner@example.test")
    ok(client.post("/esign/sign/%s/sign" % o, json={"consent": True, "typed_name": "Olivia Owner"}))

    env = db_session.query(EsignEnvelope).one()
    assert env.status == "completed" and env.final_pdf and env.final_sha256
    # The sent document itself never changed.
    import hashlib
    assert env.document_sha256 == hashlib.sha256(DOC.encode()).hexdigest() and env.document_html == DOC
    done = [m for m in mail if m["type"] == "esign_completed"]
    assert {m["to"] for m in done} == {"alex@example.test", "owner@example.test", "copy@example.test"}
    assert all(m["attachments"] for m in done)
    # The owner sender address (the God account) is not copied: sender_name was given.
    assert "owner-esign@evosyspro.test" not in {m["to"] for m in mail}

    from pypdf import PdfReader
    text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(env.final_pdf)).pages)
    assert "Example <b>Media</b> LLC" in text or "Example Media LLC" in text.replace("<b>", "").replace("</b>", "")
    assert "Account Director" in text
    signed = [e for e in db_session.query(EsignEvent).filter_by(envelope_id=env.id, action="signed")]
    assert any("Account Director" in (e.detail or "") for e in signed)

    st = ok(client.get("/esign/owner/envelopes/%s" % env.id, headers=god))
    assert st["status"] == "completed"
    pdf = client.get("/esign/owner/envelopes/%s/signed.pdf" % env.id, headers=god)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
