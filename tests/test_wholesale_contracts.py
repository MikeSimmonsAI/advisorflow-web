"""Contract templates, the document lifecycle and the signature path.

The brief for this work said three things in capital letters, and this file
exists to make each of them a failing test if it ever stops being true:

    DO NOT INVENT LEGAL CONTRACT LANGUAGE
    Do not manufacture status events
    DO NOT FAKE SIGNATURES

So the assertions here are mostly about what does NOT happen. A test that only
checks the happy path would pass just as cheerfully against a module that
generated a Texas purchase agreement out of a language model, which is exactly
the outcome worth preventing.
"""

import pytest

from app.services import wholesale_esign as esign


def ok(response):
    assert response.status_code in (200, 201), \
        "%s %s" % (response.status_code, response.text[:400])
    return response.json()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.setenv("WHOLESALE_LOCAL_MEDIA_ROOT", str(tmp_path / "media"))
    return tmp_path


@pytest.fixture
def deal(client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "88 Template Way",
                                "unit": "B", "city": "Fort Worth", "state": "TX",
                                "county": "Tarrant", "zip_code": "76102",
                                "parcel_apn": "APN-88-B",
                                "property_type": "single_family",
                                "owner_name": "Dolores Abernathy",
                                "is_test": True}))
    return {"property_id": prop["id"], "deal_id": prop["deal"]["id"]}


@pytest.fixture
def document(client, auth_headers, deal):
    return ok(client.post("/wholesale/deals/%s/documents" % deal["deal_id"],
                          headers=auth_headers,
                          json={"doc_type": "purchase_contract",
                                "title": "Purchase agreement"}))


# ── The fill sheet is facts, not prose ──────────────────────────────────────

def test_the_fill_sheet_returns_only_facts_somebody_recorded(
        client, auth_headers, deal):
    sheet = ok(client.get("/wholesale/deals/%s/fill-sheet" % deal["deal_id"],
                          headers=auth_headers))

    flat = {f["label"]: f for g in sheet["groups"] for f in g["fields"]}

    # What a person typed comes back, exactly as typed.
    assert flat["Street address"]["value"] == "88 Template Way B"
    assert flat["County"]["value"] == "Tarrant"
    assert flat["Parcel / APN"]["value"] == "APN-88-B"
    assert flat["Seller of record"]["value"] == "Dolores Abernathy"

    # What nobody recorded comes back EMPTY, with a note saying where it
    # actually comes from. This is the assertion that matters: a legal
    # description quietly filled in from an address is how a wrong parcel
    # reaches a closing table.
    assert flat["Legal description"]["value"] is None
    assert "title commitment" in flat["Legal description"]["note"]
    assert flat["Purchase price (we pay the seller)"]["value"] is None
    assert flat["Closing date"]["value"] is None

    assert "Legal description" in sheet["missing"]


def test_the_fill_sheet_contains_no_contract_language(
        client, auth_headers, deal):
    """No clause, no recital, no covenant. Anywhere in the payload.

    Checked against the vocabulary that only appears in a drafted agreement.
    If somebody later adds a "suggested wording" field to this endpoint, this
    test is what tells them the product decided not to do that.
    """
    body = client.get("/wholesale/deals/%s/fill-sheet" % deal["deal_id"],
                      headers=auth_headers).text.lower()
    for phrase in ("whereas", "hereby", "herein", "the parties agree",
                   "in consideration of", "shall be deemed", "witnesseth",
                   "covenants and agrees", "assigns and transfers"):
        assert phrase not in body, "fill sheet contains contract prose: %r" % phrase


def test_the_fill_sheet_says_what_it_is_not(client, auth_headers, deal):
    sheet = ok(client.get("/wholesale/deals/%s/fill-sheet" % deal["deal_id"],
                          headers=auth_headers))
    assert "does not produce a contract" in sheet["notice"]


# ── Templates are the customer's own forms ──────────────────────────────────

def test_a_template_is_stored_with_its_provenance(
        client, auth_headers, storage):
    created = ok(client.post(
        "/wholesale/contract-templates", headers=auth_headers,
        files={"file": ("trec.pdf", b"%PDF-1.4\n" + b"\x00" * 64,
                        "application/pdf")},
        data={"name": "TREC 20-18 plus our rider",
              "doc_type": "purchase_contract", "jurisdiction": "Texas",
              "source_note": "Our attorney, March 2026",
              "guidance": "Use the rider on every assignment."}))

    assert created["name"] == "TREC 20-18 plus our rider"
    # Jurisdiction is free text and is NOT normalised to a state code: a code
    # would imply this module had checked the form is good there.
    assert created["jurisdiction"] == "Texas"
    assert created["source_note"] == "Our attorney, March 2026"
    assert created["file_url"].startswith("/wholesale/files/")

    listing = ok(client.get("/wholesale/contract-templates", headers=auth_headers))
    assert [t["id"] for t in listing["templates"]] == [created["id"]]
    assert "Your attorney does that" in listing["notice"]


def test_a_template_is_archived_and_never_deleted(
        client, auth_headers, storage):
    created = ok(client.post(
        "/wholesale/contract-templates", headers=auth_headers,
        files={"file": ("old.pdf", b"%PDF-1.4\n" + b"\x00" * 32, "application/pdf")},
        data={"name": "Last year's form"}))

    archived = ok(client.post("/wholesale/contract-templates/%s/archive"
                              % created["id"], headers=auth_headers))
    assert archived["is_active"] is False
    assert archived["archived_at"]

    # Gone from the picker...
    assert ok(client.get("/wholesale/contract-templates",
                         headers=auth_headers))["templates"] == []
    # ...but still there, because a deal papered with it must still be able to
    # say which form that was.
    kept = ok(client.get("/wholesale/contract-templates", headers=auth_headers,
                         params={"include_archived": True}))
    assert [t["id"] for t in kept["templates"]] == [created["id"]]

    restored = ok(client.post("/wholesale/contract-templates/%s/archive?restore=true"
                              % created["id"], headers=auth_headers))
    assert restored["is_active"] is True
    assert restored["archived_at"] is None


def test_the_template_list_is_this_organizations_own(client, auth_headers):
    """An empty list is the correct starting state.

    There is no seeded library of forms, because a form this platform shipped
    would be a form nobody's lawyer wrote.
    """
    listing = ok(client.get("/wholesale/contract-templates", headers=auth_headers))
    assert listing["templates"] == []


# ── The lifecycle ───────────────────────────────────────────────────────────

def test_the_lifecycle_is_published_with_who_causes_each_state(
        client, auth_headers):
    report = ok(client.get("/wholesale/documents/lifecycle", headers=auth_headers))
    by_key = {s["key"]: s for s in report["statuses"]}

    assert by_key["viewed"]["set_by"] == "recipient"
    assert by_key["sent"]["set_by"] == "person"
    assert by_key["signed"]["set_by"] == "person or signature provider"
    # Terminal means terminal: nothing moves out of these on its own or
    # otherwise.
    assert by_key["voided"]["next"] == []
    assert by_key["superseded"]["next"] == []


def test_a_legal_transition_is_recorded_and_an_illegal_one_is_refused(
        client, auth_headers, document):
    moved = ok(client.post("/wholesale/documents/%s/status" % document["id"],
                           headers=auth_headers, json={"status": "ready_for_review"}))
    assert moved["status"] == "ready_for_review"

    # draft -> sent skips approval, so it is refused, and the refusal says
    # what IS possible rather than "invalid transition".
    refused = client.post("/wholesale/documents/%s/status" % document["id"],
                          headers=auth_headers, json={"status": "viewed"})
    assert refused.status_code == 409
    assert "can only become" in refused.json()["detail"]


def test_signed_is_refused_when_there_is_nothing_signed_to_point_at(
        client, auth_headers, document):
    ok(client.post("/wholesale/documents/%s/status" % document["id"],
                   headers=auth_headers, json={"status": "approved"}))
    refused = client.post("/wholesale/documents/%s/status" % document["id"],
                          headers=auth_headers, json={"status": "signed"})
    assert refused.status_code == 409
    assert "no signed copy" in refused.json()["detail"]


def test_superseding_a_document_means_naming_the_one_that_replaces_it(
        client, auth_headers, deal, document):
    missing = client.post("/wholesale/documents/%s/status" % document["id"],
                          headers=auth_headers, json={"status": "superseded"})
    assert missing.status_code == 400
    assert "superseded_by_id" in missing.json()["detail"]

    replacement = ok(client.post("/wholesale/deals/%s/documents" % deal["deal_id"],
                                 headers=auth_headers,
                                 json={"doc_type": "purchase_contract",
                                       "title": "Purchase agreement v2"}))
    done = ok(client.post("/wholesale/documents/%s/status" % document["id"],
                          headers=auth_headers,
                          json={"status": "superseded",
                                "superseded_by_id": replacement["id"]}))
    assert done["status"] == "superseded"
    assert done["superseded_by_id"] == replacement["id"]


def test_the_old_status_vocabulary_is_read_not_rewritten():
    """Rows written before the lifecycle existed still mean something.

    They are mapped on read. Nothing migrates them in place, because a
    document row is evidence about a legal document and quietly editing its
    recorded state to fit a newer vocabulary is a bad habit to start.
    """
    assert esign.normalise("needed") == esign.STATUS_DRAFT
    assert esign.normalise("executed") == esign.STATUS_SIGNED
    assert esign.normalise("void") == esign.STATUS_VOIDED
    assert esign.normalise(None) == esign.STATUS_DRAFT
    # An unrecognised value falls back rather than raising: a status nobody can
    # read must not make the documents tab throw.
    assert esign.normalise("something_from_the_future") == esign.STATUS_DRAFT


# ── Signatures ──────────────────────────────────────────────────────────────

def test_no_provider_means_no_signature_and_the_screen_is_told_why(
        client, auth_headers, document):
    result = ok(client.post("/wholesale/documents/%s/signature-request"
                            % document["id"], headers=auth_headers,
                            json={"parties": [{"name": "Dolores",
                                               "email": "d@example.com"}]}))

    assert result["sent"] is False
    assert result["status"] == esign.SEND_MANUAL
    assert result["external_ref"] is None
    assert "No e-signature provider is connected" in result["message"]

    # THE POINT OF THIS TEST. The document did not move. A product that set
    # `sent` here would be telling its own audit trail that a contract left the
    # building when nothing did.
    assert result["document_status"] == esign.STATUS_DRAFT
    fresh = ok(client.get("/wholesale/documents/lifecycle", headers=auth_headers))
    assert fresh["signature"]["electronic_signature"] is False


def test_the_manual_provider_mints_nothing(client, auth_headers):
    """It is a record of an offline process, not a simulated signature."""
    provider = esign.get_provider("manual")
    result = provider.send(esign.SignatureRequest(
        document_id="d1", title="Purchase agreement",
        parties=[{"name": "A", "email": "a@example.com"}]))

    assert result.left_the_building is False
    assert result.external_ref is None
    assert result.sent_at is None
    assert provider.electronic is False


def test_an_unknown_provider_falls_back_instead_of_breaking_the_screen():
    assert esign.get_provider("some-vendor-we-removed").key == "manual"


def test_capability_refuses_to_claim_an_electronic_signature():
    cap = esign.capability()
    assert cap["electronic_signature"] is False
    assert "signed outside this system" in cap["reason"].lower()
    # Every listed provider is honest about whether it can carry a signature.
    assert all(p["electronic"] is False for p in cap["providers"])
