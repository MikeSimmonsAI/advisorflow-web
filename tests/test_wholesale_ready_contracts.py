"""Ready-made contracts and DocuSeal e-signing.

What must stay true:
  * the document is filled from the deal and from what the person typed;
    a required blank stays a visible blank and blocks sending;
  * nothing is stored or sent when no e-signature provider is connected;
  * SENT only when DocuSeal returned a submission id; SIGNED only when
    DocuSeal's own record (read back with our key) says complete - never from
    the webhook payload alone.
"""
import pytest

from app.services import wholesale_esign as esign


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:400])
    return r.json()


@pytest.fixture
def deal(client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "2620 Kirby St", "city": "Dallas", "state": "TX",
                                "county": "Dallas", "zip_code": "75203", "parcel_apn": "00000250831000000",
                                "property_type": "land", "owner_name": "Elizabeth Bryant", "is_test": True}))
    return prop["deal"]["id"]


FULL = {"price": "95000", "earnest_money": "1000", "option_days": "10", "title_company": "Republic Title",
        "closing_date": "2026-11-20", "seller_email": "seller@example.com"}


@pytest.fixture
def docuseal(monkeypatch):
    monkeypatch.setenv("WHOLESALE_DOCUSEAL_API_KEY", "test-key")
    calls = []
    state = {"submission": {"id": 501, "status": "pending", "submitters": [
        {"role": "Seller", "status": "sent"}, {"role": "Buyer", "status": "sent"}]}}

    def fake_call(self, method, path, body=None):
        calls.append((method, path, body))
        if method == "POST" and path == "/submissions/html":
            return {"id": 501, "submitters": []}
        if method == "GET" and path == "/submissions/501":
            return state["submission"]
        raise AssertionError("unexpected DocuSeal call %s %s" % (method, path))

    monkeypatch.setattr(esign.DocuSealProvider, "_call", fake_call)
    return {"calls": calls, "state": state}


def test_kits_list_the_three_documents_and_say_they_are_starters(client, auth_headers):
    k = ok(client.get("/wholesale/contract-kits", headers=auth_headers))
    assert [x["kind"] for x in k["kits"]] == ["purchase_agreement", "assignment_agreement", "assignee_disclosure"]
    assert "attorney" in k["notice"].lower()
    assert k["signature"]["electronic"] is False          # no key in the test environment


def test_preview_fills_from_the_deal_and_shows_blanks(client, auth_headers, deal):
    pv = ok(client.post("/wholesale/deals/%s/contracts/purchase_agreement/preview" % deal,
                        headers=auth_headers, json={}))
    assert "2620 Kirby St" in pv["html"] and "Elizabeth Bryant" in pv["html"]
    assert "Purchase price" in pv["missing"] and pv["ready"] is False
    assert 'class="blank"' in pv["html"]
    assert "<signature-field" not in pv["html"]           # preview/print copy has plain lines

    pv2 = ok(client.post("/wholesale/deals/%s/contracts/purchase_agreement/preview" % deal,
                         headers=auth_headers, json={"values": FULL}))
    assert pv2["missing"] == [] and "$95,000" in pv2["html"] and "November 20, 2026" in pv2["html"]


def test_send_without_a_provider_stores_and_sends_nothing(client, auth_headers, deal):
    r = ok(client.post("/wholesale/deals/%s/contracts/purchase_agreement/send" % deal,
                       headers=auth_headers, json={"values": FULL}))
    assert r["sent"] is False and "html" in r
    docs = client.get("/wholesale/deals/%s" % deal, headers=auth_headers).json().get("documents") or []
    assert not [d for d in docs if d.get("signature_provider") == "docuseal"]


def test_send_refuses_while_a_required_blank_is_open(client, auth_headers, deal, docuseal):
    r = client.post("/wholesale/deals/%s/contracts/purchase_agreement/send" % deal,
                    headers=auth_headers, json={"values": {"price": "95000"}})
    assert r.status_code == 409 and "Closing date" in r.json()["detail"]
    assert docuseal["calls"] == []


def test_send_then_webhook_signs_only_after_docuseal_confirms(client, auth_headers, deal, docuseal):
    r = ok(client.post("/wholesale/deals/%s/contracts/purchase_agreement/send" % deal,
                       headers=auth_headers, json={"values": FULL}))
    assert r["sent"] is True and r["external_ref"] == "501"
    method, path, body = docuseal["calls"][0]
    assert path == "/submissions/html" and body["order"] == "preserved"
    assert [s["role"] for s in body["submitters"]] == ["Seller", "Buyer"]
    assert body["documents"][0]["html"].count("<signature-field") == 2

    # The webhook says "completed" but DocuSeal's record still says pending:
    # nothing is signed.
    hook = {"event_type": "submission.completed", "data": {"id": 501}}
    out = ok(client.post("/esign/docuseal/webhook", json=hook))
    assert out["status"] in ("sent", "viewed")

    docuseal["state"]["submission"] = {"id": 501, "status": "completed",
                                       "combined_document_url": "https://docuseal.example/signed.pdf",
                                       "audit_log_url": "https://docuseal.example/audit.pdf",
                                       "submitters": [{"role": "Seller", "status": "completed"},
                                                      {"role": "Buyer", "status": "completed"}]}
    out = ok(client.post("/esign/docuseal/webhook", json=hook))
    assert out["status"] == "signed" and out["changed"] is True
    d = client.get("/wholesale/deals/%s" % deal, headers=auth_headers).json()
    assert (d.get("deal") or d).get("contract_status") == "signed"


def test_webhook_ignores_submissions_that_are_not_ours(client, docuseal):
    out = ok(client.post("/esign/docuseal/webhook", json={"event_type": "submission.completed",
                                                          "data": {"id": 999}}))
    assert out.get("ignored") == "not one of ours"
    assert docuseal["calls"] == []


def test_webhook_secret_is_enforced_when_set(client, monkeypatch):
    monkeypatch.setenv("WHOLESALE_DOCUSEAL_WEBHOOK_SECRET", "s3cret")
    r = client.post("/esign/docuseal/webhook", json={"data": {"id": 1}})
    assert r.status_code == 401
    r = client.post("/esign/docuseal/webhook", json={"data": {}}, headers={"X-Docuseal-Secret": "s3cret"})
    assert r.status_code == 200


def test_refresh_reads_the_status_back(client, auth_headers, deal, docuseal):
    sent = ok(client.post("/wholesale/deals/%s/contracts/purchase_agreement/send" % deal,
                          headers=auth_headers, json={"values": FULL}))
    docuseal["state"]["submission"]["submitters"][0]["status"] = "opened"
    r = ok(client.post("/wholesale/documents/%s/signature-refresh" % sent["document_id"],
                       headers=auth_headers, json={}))
    assert r["status"] == "viewed" and r["changed"] is True
